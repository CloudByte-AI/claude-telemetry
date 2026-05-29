import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db import manager as db_manager_module
from src.db.manager import DatabaseManager
from src.db.schema import create_indexes, create_tables
from src.mcp import memory
from src.workers import memory_indexer


class RuntimeMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cloudbyte.db"
        self.ensure_dirs = patch("src.db.manager.ensure_directories", lambda: None)
        self.ensure_dirs.start()
        test_db_manager = DatabaseManager(self.db_path)
        test_db_manager._schema_checked = True
        db_manager_module._db_manager = test_db_manager
        conn = db_manager_module.get_db_connection()
        create_tables(conn)
        create_indexes(conn)
        self._seed(conn)

    def tearDown(self):
        db_manager_module.close_db()
        db_manager_module._db_manager = None
        self.ensure_dirs.stop()
        self.tmp.cleanup()

    def _seed(self, conn: sqlite3.Connection):
        conn.execute(
            "INSERT INTO PROJECT (project_id, name, path, created_at) VALUES (?, ?, ?, ?)",
            ("proj-1", "Project One", "D:/repo/one", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO PROJECT (project_id, name, path, created_at) VALUES (?, ?, ?, ?)",
            ("proj-2", "Project Two", "D:/repo/two", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO SESSION (session_id, project_id, cwd, created_at) VALUES (?, ?, ?, ?)",
            ("sess-1", "proj-1", "D:/repo/one", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO SESSION (session_id, project_id, cwd, created_at) VALUES (?, ?, ?, ?)",
            ("sess-2", "proj-2", "D:/repo/two", "2026-01-01T00:00:00+00:00"),
        )
        conn.executemany(
            "INSERT INTO USER_PROMPT (prompt_id, session_id, prompt, timestamp) VALUES (?, ?, ?, ?)",
            [
                ("prompt-1", "sess-1", "Fix auth", "2026-01-03T00:00:00+00:00"),
                ("prompt-2", "sess-1", "Add memory", "2026-01-04T00:00:00+00:00"),
                ("prompt-3", "sess-2", "Other fix", "2026-01-05T00:00:00+00:00"),
            ],
        )
        observations = [
            (
                "obs-1",
                "sess-1",
                "prompt-1",
                "bugfix",
                "Fixed auth timeout",
                "OAuth refresh now retries expired tokens.",
                "Updated the auth middleware retry flow.",
                json.dumps(["Changed retry policy"]),
                json.dumps(["oauth2", "token-refresh"]),
                json.dumps(["src/auth.py"]),
                json.dumps(["src/auth.py"]),
                "hash-1",
                "2026-01-03T00:00:00+00:00",
            ),
            (
                "obs-2",
                "sess-1",
                "prompt-2",
                "decision",
                "Chose ChromaDB memory index",
                "Runtime memory uses a background vector index.",
                "Stored observations in ChromaDB for semantic retrieval.",
                json.dumps(["Selected ChromaDB"]),
                json.dumps(["runtime-memory", "chromadb"]),
                json.dumps(["src/mcp/server.py"]),
                json.dumps(["src/mcp/memory.py"]),
                "hash-2",
                "2026-01-04T00:00:00+00:00",
            ),
            (
                "obs-3",
                "sess-2",
                "prompt-3",
                "bugfix",
                "Fixed unrelated project bug",
                "Other project observation.",
                "This should not appear in active project searches.",
                json.dumps(["Other project"]),
                json.dumps(["other"]),
                json.dumps(["src/other.py"]),
                json.dumps(["src/other.py"]),
                "hash-3",
                "2026-01-05T00:00:00+00:00",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO HOOK_OBSERVATION (
                id, session_id, prompt_id, type, title, subtitle, narrative,
                facts, concepts, files_read, files_modified, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            observations,
        )
        conn.commit()

    def test_metadata_search_filters_by_type_and_active_project(self):
        with patch("src.mcp.memory._active_project", return_value={"project_id": "proj-1", "project_path": "D:/repo/one"}):
            result = memory.search_memory({"types": ["bugfix"], "limit": 10})

        self.assertEqual(result["mode"], "metadata")
        self.assertEqual([row["id"] for row in result["results"]], ["obs-1"])

    def test_metadata_search_filters_by_file_and_concept(self):
        with patch("src.mcp.memory._active_project", return_value={"project_id": "proj-1", "project_path": "D:/repo/one"}):
            result = memory.search_memory({
                "files_modified": ["src/mcp/memory.py"],
                "concepts": ["chromadb"],
                "limit": 10,
            })

        self.assertEqual([row["id"] for row in result["results"]], ["obs-2"])

    def test_recent_memory_returns_newest_first(self):
        with patch("src.mcp.memory._active_project", return_value={"project_id": "proj-1", "project_path": "D:/repo/one"}):
            result = memory.recent_memory({"limit": 2})

        self.assertEqual([row["id"] for row in result["results"]], ["obs-2", "obs-1"])

    def test_index_document_excludes_metadata_fields(self):
        obs = memory_indexer.get_observation_for_index("obs-1")

        document = memory_indexer._document_text(obs)
        metadata = memory_indexer._metadata(obs)

        self.assertIn("Fixed auth timeout", document)
        self.assertIn("OAuth refresh now retries expired tokens.", document)
        self.assertNotIn("Changed retry policy", document)
        self.assertNotIn("oauth2", document)
        self.assertEqual(json.loads(metadata["concepts_json"]), ["oauth2", "token-refresh"])
        self.assertEqual(json.loads(metadata["files_modified_json"]), ["src/auth.py"])

    def test_semantic_search_uses_chroma_ids_and_metadata_post_filters(self):
        class FakeCollection:
            def count(self):
                return 2

            def query(self, **_kwargs):
                return {
                    "ids": [["obs-2", "obs-1"]],
                    "metadatas": [[
                        {"concepts_json": json.dumps(["chromadb"]), "files_modified_json": json.dumps(["src/mcp/memory.py"])},
                        {"concepts_json": json.dumps(["oauth2"]), "files_modified_json": json.dumps(["src/auth.py"])},
                    ]],
                    "distances": [[0.1, 0.2]],
                }

        with patch("src.mcp.memory._active_project", return_value={"project_id": "proj-1", "project_path": "D:/repo/one"}), \
             patch("src.mcp.memory._get_collection", return_value=FakeCollection()):
            result = memory.search_memory({
                "query": "vector memory",
                "concepts": ["chromadb"],
                "limit": 10,
            })

        self.assertEqual(result["mode"], "semantic")
        self.assertEqual([row["id"] for row in result["results"]], ["obs-2"])
        self.assertAlmostEqual(result["results"][0]["score"], 0.9)

    def test_worker_memory_index_task_delegates_observation_id(self):
        with patch("src.workers.memory_indexer.upsert_observation", return_value={"status": "indexed", "observation_id": "obs-1"}) as upsert:
            result = memory_indexer.process_memory_index_task({"observation_id": "obs-1"}, session_id="sess-1")

        upsert.assert_called_once_with("obs-1")
        self.assertEqual(result["status"], "indexed")

    def test_worker_backfill_mode_takes_precedence_over_session_id(self):
        with patch("src.workers.memory_indexer.backfill", return_value={"status": "completed"}) as backfill, \
             patch("src.workers.memory_indexer.index_session") as index_session:
            result = memory_indexer.process_memory_index_task(
                {"mode": "backfill", "limit": 1000},
                session_id="sess-1",
            )

        backfill.assert_called_once_with(limit=1000)
        index_session.assert_not_called()
        self.assertEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
