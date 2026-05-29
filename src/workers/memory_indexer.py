"""Background ChromaDB indexing for hook observations."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from src.common.logging import get_logger
from src.common.paths import get_chroma_dir
from src.db.manager import get_db_connection


logger = get_logger(__name__)

COLLECTION_NAME = "hook_observations"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _get_collection():
    import chromadb
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    chroma_dir = get_chroma_dir()
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL,
        device="cpu",
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
    )


def _row_to_dict(row: tuple, columns: Iterable[str]) -> Dict[str, Any]:
    return dict(zip(columns, row))


def get_observation_for_index(observation_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT o.id, o.session_id, o.prompt_id, o.title, o.subtitle,
               o.narrative, o.type, o.facts, o.concepts, o.files_read,
               o.files_modified, o.content_hash, o.created_at,
               p.project_id, p.path AS project_path
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE o.id = ?
        """,
        (observation_id,),
    )
    row = cursor.fetchone()
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    cursor.close()
    if not row:
        return None
    return _row_to_dict(row, columns)


def get_observations_for_session(session_id: str) -> list[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT o.id, o.session_id, o.prompt_id, o.title, o.subtitle,
               o.narrative, o.type, o.facts, o.concepts, o.files_read,
               o.files_modified, o.content_hash, o.created_at,
               p.project_id, p.path AS project_path
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE o.session_id = ?
        ORDER BY o.created_at ASC
        """,
        (session_id,),
    )
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    cursor.close()
    return [_row_to_dict(row, columns) for row in rows]


def get_all_observations_for_index(limit: int = 1000) -> list[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT o.id, o.session_id, o.prompt_id, o.title, o.subtitle,
               o.narrative, o.type, o.facts, o.concepts, o.files_read,
               o.files_modified, o.content_hash, o.created_at,
               p.project_id, p.path AS project_path
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        ORDER BY o.created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    cursor.close()
    return [_row_to_dict(row, columns) for row in rows]


def _document_text(obs: Dict[str, Any]) -> str:
    parts = [
        obs.get("title") or "",
        obs.get("subtitle") or "",
        obs.get("narrative") or "",
    ]
    return "\n\n".join(part for part in parts if part.strip())


def _metadata(obs: Dict[str, Any]) -> Dict[str, Any]:
    facts = _json_list(obs.get("facts"))
    concepts = _json_list(obs.get("concepts"))
    files_read = _json_list(obs.get("files_read"))
    files_modified = _json_list(obs.get("files_modified"))

    return {
        "observation_id": obs.get("id") or "",
        "session_id": obs.get("session_id") or "",
        "prompt_id": obs.get("prompt_id") or "",
        "project_id": obs.get("project_id") or "",
        "project_path": obs.get("project_path") or "",
        "type": obs.get("type") or "",
        "created_at": obs.get("created_at") or "",
        "content_hash": obs.get("content_hash") or "",
        "facts_json": json.dumps(facts),
        "concepts_json": json.dumps(concepts),
        "files_read_json": json.dumps(files_read),
        "files_modified_json": json.dumps(files_modified),
    }


def upsert_observation(observation_id: str) -> Dict[str, Any]:
    obs = get_observation_for_index(observation_id)
    if not obs:
        return {"status": "missing", "observation_id": observation_id}

    collection = _get_collection()
    existing = collection.get(ids=[observation_id], include=["metadatas"])
    existing_metadatas = existing.get("metadatas") or []
    if existing_metadatas:
        existing_hash = existing_metadatas[0].get("content_hash")
        if existing_hash and existing_hash == (obs.get("content_hash") or ""):
            return {"status": "skipped", "observation_id": observation_id}

    document = _document_text(obs)
    if not document.strip():
        return {"status": "empty", "observation_id": observation_id}

    collection.upsert(
        ids=[observation_id],
        documents=[document],
        metadatas=[_metadata(obs)],
    )
    return {"status": "indexed", "observation_id": observation_id}


def index_session(session_id: str) -> Dict[str, Any]:
    return index_observations(get_observations_for_session(session_id))


def backfill(limit: int = 1000) -> Dict[str, Any]:
    return index_observations(get_all_observations_for_index(limit=limit))


def index_observations(observations: list[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"indexed": 0, "skipped": 0, "missing": 0, "empty": 0, "failed": 0}
    for obs in observations:
        observation_id = obs.get("id")
        if not observation_id:
            counts["missing"] += 1
            continue
        try:
            result = upsert_observation(observation_id)
            status = result.get("status", "failed")
            counts[status] = counts.get(status, 0) + 1
        except Exception as exc:
            logger.warning(f"Failed to index observation {observation_id}: {exc}", exc_info=True)
            counts["failed"] += 1
    return {"status": "completed", "counts": counts}


def process_memory_index_task(payload: Dict[str, Any], session_id: str | None = None) -> Dict[str, Any]:
    if payload.get("observation_id"):
        return upsert_observation(payload["observation_id"])
    if payload.get("mode") == "backfill":
        return backfill(limit=int(payload.get("limit", 1000)))
    if payload.get("session_id") or session_id:
        return index_session(payload.get("session_id") or session_id or "")
    return {"status": "skipped", "reason": "no observation_id, session_id, or backfill mode"}
