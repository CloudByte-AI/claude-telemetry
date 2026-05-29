"""Runtime memory retrieval for the CloudByte MCP server."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from src.common.logging import get_logger
from src.common.paths import get_chroma_dir, get_cloudbyte_dir
from src.db.manager import get_db_connection
from src.workers.memory_indexer import COLLECTION_NAME, EMBEDDING_MODEL


logger = get_logger(__name__)

DEFAULT_LIMIT = 10
MAX_LIMIT = 50


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value), MAX_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item is not None]
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _row_to_dict(row: tuple, columns: Iterable[str]) -> Dict[str, Any]:
    return dict(zip(columns, row))


def _current_session_id() -> Optional[str]:
    import os

    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if session_id:
        return session_id

    session_file = get_cloudbyte_dir() / "current_session_id.txt"
    try:
        if session_file.exists():
            value = session_file.read_text(encoding="utf-8").strip()
            return value or None
    except Exception as exc:
        logger.debug(f"Could not read current session id: {exc}")
    return None


def _active_project() -> Optional[Dict[str, str]]:
    session_id = _current_session_id()
    if not session_id:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT p.project_id, p.path
        FROM SESSION s
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE s.session_id = ?
        """,
        (session_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row or not row[0]:
        return None
    return {"project_id": row[0], "project_path": row[1] or ""}


def _base_observation_query(where_sql: str, params: list[Any], order_sql: str, limit: int) -> list[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT o.id, o.session_id, o.prompt_id, o.type, o.title, o.subtitle,
               o.narrative, o.text, o.facts, o.concepts, o.files_read,
               o.files_modified, o.content_hash, o.created_at,
               p.project_id, p.path AS project_path, p.name AS project_name
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE {where_sql}
        {order_sql}
        LIMIT ?
        """,
        tuple(params + [limit]),
    )
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    cursor.close()
    return [_normalize_observation(_row_to_dict(row, columns)) for row in rows]


def _normalize_observation(obs: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": obs.get("id"),
        "session_id": obs.get("session_id"),
        "prompt_id": obs.get("prompt_id"),
        "project_id": obs.get("project_id"),
        "project_path": obs.get("project_path"),
        "project_name": obs.get("project_name"),
        "type": obs.get("type"),
        "title": obs.get("title"),
        "subtitle": obs.get("subtitle"),
        "narrative": obs.get("narrative"),
        "facts": _json_list(obs.get("facts")),
        "concepts": _json_list(obs.get("concepts")),
        "files_read": _json_list(obs.get("files_read")),
        "files_modified": _json_list(obs.get("files_modified")),
        "content_hash": obs.get("content_hash"),
        "created_at": obs.get("created_at"),
    }


def _scope_filter(args: Dict[str, Any]) -> tuple[list[str], list[Any], Dict[str, Any]]:
    filters = ["1=1"]
    params: list[Any] = []
    active = _active_project()

    project_path = args.get("project_path")
    include_global = bool(args.get("include_global_fallback", False))
    session_id = args.get("session_id")

    if session_id:
        filters.append("o.session_id = ?")
        params.append(session_id)
    elif project_path:
        filters.append("(p.path = ? OR s.cwd = ?)")
        params.extend([project_path, project_path])
    elif active and active.get("project_id"):
        filters.append("p.project_id = ?")
        params.append(active["project_id"])
    elif not include_global:
        filters.append("0=1")

    return filters, params, {"active_project": active, "scope": "global" if include_global and not project_path and not session_id else "project"}


def _add_common_filters(filters: list[str], params: list[Any], args: Dict[str, Any]) -> None:
    types = _as_list(args.get("types"))
    if types:
        filters.append(f"o.type IN ({','.join(['?'] * len(types))})")
        params.extend(types)

    days = args.get("days")
    if days:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
            filters.append("o.created_at >= ?")
            params.append(cutoff.isoformat())
        except (TypeError, ValueError):
            pass

    for column, arg_name in (
        ("o.files_read", "files_read"),
        ("o.files_modified", "files_modified"),
        ("o.concepts", "concepts"),
        ("o.facts", "facts"),
    ):
        values = _as_list(args.get(arg_name))
        if values:
            sub_filters = []
            for value in values:
                sub_filters.append(f"{column} LIKE ?")
                params.append(f'%"{value}"%')
            filters.append("(" + " OR ".join(sub_filters) + ")")


def metadata_search(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = _limit(args.get("limit"))
    filters, params, scope = _scope_filter(args)
    _add_common_filters(filters, params, args)

    rows = _base_observation_query(
        " AND ".join(filters),
        params,
        "ORDER BY o.created_at DESC",
        limit,
    )
    return {
        "mode": "metadata",
        "index_status": "not_required",
        **scope,
        "count": len(rows),
        "results": rows,
    }


def recent_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(args)
    args.setdefault("limit", DEFAULT_LIMIT)
    return metadata_search(args) | {"mode": "recent"}


def _get_collection(with_embedding: bool = True):
    import chromadb

    chroma_dir = get_chroma_dir()
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    if not with_embedding:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
        )

    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL,
        device="cpu",
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
    )


def _where_for_chroma(args: Dict[str, Any], active: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    clauses: list[Dict[str, Any]] = []
    types = _as_list(args.get("types"))
    if len(types) == 1:
        clauses.append({"type": types[0]})
    elif len(types) > 1:
        clauses.append({"type": {"$in": types}})

    project_path = args.get("project_path")
    include_global = bool(args.get("include_global_fallback", False))
    session_id = args.get("session_id")

    if session_id:
        clauses.append({"session_id": session_id})
    elif project_path:
        clauses.append({"project_path": project_path})
    elif active and active.get("project_id"):
        clauses.append({"project_id": active["project_id"]})
    elif not include_global:
        clauses.append({"project_id": "__no_active_project__"})

    json_filter_ids = _prefilter_observation_ids(args)
    if json_filter_ids is not None:
        if json_filter_ids:
            clauses.append({"observation_id": {"$in": json_filter_ids}})
        else:
            clauses.append({"observation_id": "__no_matching_metadata_filters__"})

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _prefilter_observation_ids(args: Dict[str, Any]) -> Optional[list[str]]:
    if not any(_as_list(args.get(key)) for key in ("files_read", "files_modified", "concepts", "facts")):
        return None

    filters, params, _scope = _scope_filter(args)
    _add_common_filters(filters, params, args)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT o.id
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE {' AND '.join(filters)}
        ORDER BY o.created_at DESC
        LIMIT 1000
        """,
        tuple(params),
    )
    rows = cursor.fetchall()
    cursor.close()
    return [row[0] for row in rows]


def _metadata_matches(metadata: Dict[str, Any], args: Dict[str, Any]) -> bool:
    for key, meta_key in (
        ("files_read", "files_read_json"),
        ("files_modified", "files_modified_json"),
        ("concepts", "concepts_json"),
        ("facts", "facts_json"),
    ):
        wanted = set(_as_list(args.get(key)))
        if not wanted:
            continue
        actual = set(_json_list(metadata.get(meta_key)))
        if actual.isdisjoint(wanted):
            return False
    return True


def _ids_to_observations(ids: list[str], scores: dict[str, float]) -> list[Dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT o.id, o.session_id, o.prompt_id, o.type, o.title, o.subtitle,
               o.narrative, o.text, o.facts, o.concepts, o.files_read,
               o.files_modified, o.content_hash, o.created_at,
               p.project_id, p.path AS project_path, p.name AS project_name
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE o.id IN ({placeholders})
        """,
        tuple(ids),
    )
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description] if cursor.description else []
    cursor.close()
    by_id = {}
    for row in rows:
        obs = _normalize_observation(_row_to_dict(row, columns))
        obs["score"] = scores.get(obs["id"])
        by_id[obs["id"]] = obs
    return [by_id[item] for item in ids if item in by_id]


def _sqlite_candidate_count(args: Dict[str, Any]) -> int:
    filters, params, _scope = _scope_filter(args)
    _add_common_filters(filters, params, args)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM HOOK_OBSERVATION o
        LEFT JOIN SESSION s ON s.session_id = o.session_id
        LEFT JOIN PROJECT p ON p.project_id = s.project_id
        WHERE {' AND '.join(filters)}
        """,
        tuple(params),
    )
    row = cursor.fetchone()
    cursor.close()
    return int(row[0] or 0) if row else 0


def _queue_backfill(args: Dict[str, Any]) -> str:
    try:
        from src.workers.llm_client import queue_memory_index_task

        session_id = args.get("session_id") or _current_session_id()
        result = queue_memory_index_task(
            session_id=session_id or "memory-backfill",
            payload={"mode": "backfill", "limit": 1000},
            priority=-10,
        )
        return "queued" if result.get("status") == "queued" else "partial"
    except Exception as exc:
        logger.debug(f"Could not queue memory backfill: {exc}")
        return "partial"


def semantic_search(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return metadata_search(args)

    limit = _limit(args.get("limit"))
    active = _active_project()
    scope = {"active_project": active, "scope": "project"}
    if args.get("include_global_fallback") and not args.get("project_path") and not args.get("session_id"):
        scope["scope"] = "global"

    try:
        count_collection = _get_collection(with_embedding=False)
        candidate_count = _sqlite_candidate_count(args)
        index_count = count_collection.count()
        index_status = "ready" if candidate_count == 0 or index_count >= candidate_count else "partial"
        if index_count == 0 and candidate_count > 0:
            index_status = _queue_backfill(args)
            fallback = metadata_search(args)
            fallback.update({
                "mode": "semantic",
                "index_status": index_status,
            })
            return fallback

        collection = _get_collection(with_embedding=True)
        where = _where_for_chroma(args, active)
        query_result = collection.query(
            query_texts=[query],
            n_results=min(max(limit * 5, limit), MAX_LIMIT),
            where=where,
            include=["metadatas", "distances"],
        )
    except Exception as exc:
        logger.warning(f"Chroma memory search unavailable: {exc}")
        fallback = metadata_search(args)
        fallback.update({
            "mode": "semantic",
            "index_status": "unavailable",
            "index_error": str(exc),
        })
        return fallback

    ids = (query_result.get("ids") or [[]])[0]
    metadatas = (query_result.get("metadatas") or [[]])[0]
    distances = (query_result.get("distances") or [[]])[0]

    selected: list[str] = []
    scores: dict[str, float] = {}
    min_score = args.get("min_score")
    for item_id, metadata, distance in zip(ids, metadatas, distances):
        if not _metadata_matches(metadata or {}, args):
            continue
        try:
            score = 1.0 - float(distance)
        except (TypeError, ValueError):
            score = None
        if min_score is not None and score is not None:
            try:
                if score < float(min_score):
                    continue
            except (TypeError, ValueError):
                pass
        selected.append(item_id)
        if score is not None:
            scores[item_id] = score
        if len(selected) >= limit:
            break

    rows = _ids_to_observations(selected, scores)
    return {
        "mode": "semantic",
        "index_status": index_status,
        **scope,
        "count": len(rows),
        "results": rows,
    }


def search_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    if str(args.get("query") or "").strip():
        return semantic_search(args)
    return metadata_search(args)
