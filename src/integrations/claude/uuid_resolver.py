"""
UUID Resolver Module

Handles UUID resolution and parent-child relationship traversal in Claude Code sessions.
Events are linked via parentUuid → uuid chains, and sometimes we need to search
through the JSONL file to find parent events.

Example chain:
    User Prompt (uuid=A, parentUuid=null)
        ↓
    MCP Servers (uuid=B, parentUuid=A)
        ↓
    Skill Listing (uuid=C, parentUuid=B)
        ↓
    Thinking (uuid=D, parentUuid=C)
        ↓
    Response (uuid=E, parentUuid=D)
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from collections import defaultdict

from src.common.logging import get_logger
from src.integrations.claude.reader import read_jsonl_file, get_project_jsonl_path, normalize_project_name


logger = get_logger(__name__)


class UUIDResolver:
    """
    Resolves UUID relationships and builds event chains from JSONL sessions.
    """

    def __init__(self, project_name: str, session_id: str, claude_dir: Optional[Path] = None):
        """
        Initialize the UUID resolver for a session.

        Args:
            project_name: Normalized project name
            session_id: Session UUID
            claude_dir: Optional path to .claude directory
        """
        self.project_name = project_name
        self.session_id = session_id
        self.claude_dir = claude_dir

        # Build UUID index
        self._uuid_index: Dict[str, Dict[str, Any]] = {}
        self._children_index: Dict[str, List[str]] = defaultdict(list)
        self._root_uuids: Set[str] = set()

        # Load and index events
        self._build_index()

    def _build_index(self) -> None:
        """
        Build UUID and children indexes from the JSONL file.

        Creates:
        - _uuid_index: Maps UUID → event data
        - _children_index: Maps UUID → list of child UUIDs
        - _root_uuids: Set of UUIDs with no parent (parentUuid=null)
        """
        jsonl_path = get_project_jsonl_path(self.project_name, self.session_id, self.claude_dir)

        if not jsonl_path.exists():
            logger.warning(f"JSONL file not found: {jsonl_path}")
            return

        logger.info(f"Building UUID index for {self.session_id}")

        try:
            for event in read_jsonl_file(jsonl_path):
                uuid = event.get("uuid")
                parent_uuid = event.get("parentUuid")

                if not uuid:
                    continue

                # Index by UUID
                self._uuid_index[uuid] = event

                # Track parent-child relationships
                if parent_uuid:
                    self._children_index[parent_uuid].append(uuid)
                else:
                    # This is a root event
                    self._root_uuids.add(uuid)

            logger.info(f"Indexed {len(self._uuid_index)} events, {len(self._root_uuids)} roots")

        except Exception as e:
            logger.error(f"Error building UUID index: {e}")

    def get_event_by_uuid(self, uuid: str) -> Optional[Dict[str, Any]]:
        """
        Get an event by its UUID.

        Args:
            uuid: Event UUID to look up

        Returns:
            Optional[Dict]: Event data or None if not found
        """
        return self._uuid_index.get(uuid)

    def get_parent_event(self, uuid: str, level: int = 1) -> Optional[Dict[str, Any]]:
        """
        Get the parent event of a given UUID.

        Args:
            uuid: Child event UUID
            level: How many levels up to go (default=1)

        Returns:
            Optional[Dict]: Parent event data or None if not found
        """
        event = self.get_event_by_uuid(uuid)
        if not event:
            return None

        parent_uuid = event.get("parentUuid")
        if not parent_uuid:
            return None

        if level == 1:
            return self.get_event_by_uuid(parent_uuid)

        # Recurse up the chain
        return self.get_parent_event(parent_uuid, level - 1)

    def get_children_events(self, uuid: str) -> List[Dict[str, Any]]:
        """
        Get all child events of a given UUID.

        Args:
            uuid: Parent event UUID

        Returns:
            List[Dict]: List of child events
        """
        child_uuids = self._children_index.get(uuid, [])
        return [self.get_event_by_uuid(child_uuid) for child_uuid in child_uuids]

    def get_event_chain(self, uuid: str) -> List[Dict[str, Any]]:
        """
        Get the full chain of events from root to the given UUID.

        Args:
            uuid: Event UUID to get chain for

        Returns:
            List[Dict]: Chain of events from root to target (inclusive)
        """
        chain = []
        current_uuid = uuid

        # Walk up to root
        while current_uuid:
            event = self.get_event_by_uuid(current_uuid)
            if not event:
                break

            chain.insert(0, event)  # Insert at beginning to maintain order

            parent_uuid = event.get("parentUuid")
            if not parent_uuid:
                break

            current_uuid = parent_uuid

        return chain

    def get_event_tree(self, root_uuid: Optional[str] = None) -> Dict[str, Any]:
        """
        Get the full event tree starting from a root UUID.

        Args:
            root_uuid: Root UUID to start from (uses first root if None)

        Returns:
            Dict: Tree structure with event and children
        """
        if root_uuid is None:
            if not self._root_uuids:
                return {}
            root_uuid = next(iter(self._root_uuids))

        def build_tree(uuid: str) -> Dict[str, Any]:
            """Recursively build tree from UUID."""
            event = self.get_event_by_uuid(uuid)
            if not event:
                return {}

            children = []
            for child_uuid in self._children_index.get(uuid, []):
                child_tree = build_tree(child_uuid)
                if child_tree:
                    children.append(child_tree)

            return {
                "event": event,
                "uuid": uuid,
                "type": event.get("type"),
                "children": children,
            }

        return build_tree(root_uuid)

    def find_root_uuid(self, uuid: str) -> Optional[str]:
        """
        Find the root UUID for a given event by walking up the chain.

        Args:
            uuid: Event UUID to find root for

        Returns:
            Optional[str]: Root UUID or None
        """
        current_uuid = uuid
        visited = set()

        while current_uuid and current_uuid not in visited:
            visited.add(current_uuid)
            event = self.get_event_by_uuid(current_uuid)

            if not event:
                break

            parent_uuid = event.get("parentUuid")
            if not parent_uuid:
                return current_uuid  # This is the root

            current_uuid = parent_uuid

        return None

    def get_all_roots(self) -> List[str]:
        """
        Get all root UUIDs (events with parentUuid=null).

        Returns:
            List[str]: List of root UUIDs
        """
        return list(self._root_uuids)

    def resolve_prompt_id(self, event_uuid: str) -> Optional[str]:
        """
        Resolve the promptId for an event by walking up the chain.

        Many events don't have a direct promptId, but their parent does.
        This walks up the chain to find the first event with a promptId.

        Args:
            event_uuid: Event UUID to resolve

        Returns:
            Optional[str]: promptId if found
        """
        current_uuid = event_uuid
        visited = set()

        while current_uuid and current_uuid not in visited:
            visited.add(current_uuid)
            event = self.get_event_by_uuid(current_uuid)

            if not event:
                break

            # Check if this event has a promptId
            prompt_id = event.get("promptId")
            if prompt_id:
                return prompt_id

            # Check if it's a user event (which always has a promptId)
            if event.get("type") == "user":
                return event.get("uuid")

            # Move to parent
            current_uuid = event.get("parentUuid")

        return None

    def get_conversation_thread(self, root_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get a linear conversation thread (user prompts and responses).

        Args:
            root_uuid: Starting root UUID (uses first if None)

        Returns:
            List[Dict]: Thread of conversation events in order
        """
        thread = []

        def collect_in_order(uuid: str):
            """Recursively collect events in depth-first order."""
            event = self.get_event_by_uuid(uuid)
            if not event:
                return

            event_type = event.get("type")

            # Add relevant events to thread
            if event_type in ["user", "assistant", "text"]:
                thread.append(event)

            # Recurse through children
            for child_uuid in self._children_index.get(uuid, []):
                collect_in_order(child_uuid)

        start_uuid = root_uuid or (next(iter(self._root_uuids)) if self._root_uuids else None)
        if start_uuid:
            collect_in_order(start_uuid)

        return thread

    def search_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """
        Search for all events of a specific type.

        Args:
            event_type: Type to search for (e.g., "user", "tool", "assistant")

        Returns:
            List[Dict]: List of matching events
        """
        return [
            event for event in self._uuid_index.values()
            if event.get("type") == event_type
        ]

    def search_by_content(self, keyword: str, case_sensitive: bool = False) -> List[Dict[str, Any]]:
        """
        Search for events containing a keyword in their content.

        Args:
            keyword: Keyword to search for
            case_sensitive: Whether search is case sensitive

        Returns:
            List[Dict]: List of matching events
        """
        results = []
        search_keyword = keyword if case_sensitive else keyword.lower()

        for event in self._uuid_index.values():
            # Search in various content fields
            content_to_search = ""

            # Check message content
            message = event.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", "")
                if isinstance(content, str):
                    content_to_search += content
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            content_to_search += str(item.get("text", ""))
                            content_to_search += str(item.get("thinking", ""))

            # Check attachment content
            attachment = event.get("attachment", {})
            if isinstance(attachment, dict):
                content_to_search += str(attachment.get("content", ""))

            # Perform search
            search_content = content_to_search if case_sensitive else content_to_search.lower()
            if search_keyword in search_content:
                results.append(event)

        return results

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the indexed events.

        Returns:
            Dict with statistics
        """
        event_types = defaultdict(int)
        root_count = len(self._root_uuids)

        for event in self._uuid_index.values():
            event_type = event.get("type", "unknown")
            event_types[event_type] += 1

        return {
            "total_events": len(self._uuid_index),
            "root_events": root_count,
            "event_types": dict(event_types),
            "max_depth": self._calculate_max_depth(),
        }

    def _calculate_max_depth(self) -> int:
        """Calculate the maximum depth of the event tree."""
        max_depth = 0

        for root_uuid in self._root_uuids:
            depth = self._calculate_depth(root_uuid)
            max_depth = max(max_depth, depth)

        return max_depth

    def _calculate_depth(self, uuid: str, current_depth: int = 1) -> int:
        """Recursively calculate depth from a UUID."""
        children = self._children_index.get(uuid, [])

        if not children:
            return current_depth

        return max(
            self._calculate_depth(child_uuid, current_depth + 1)
            for child_uuid in children
        )


# Convenience functions

def resolve_uuid_chain(
    project_name: str,
    session_id: str,
    uuid: str,
    claude_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """
    Resolve the full chain of events for a UUID.

    Args:
        project_name: Normalized project name
        session_id: Session UUID
        uuid: Event UUID to resolve
        claude_dir: Optional path to .claude directory

    Returns:
        List[Dict]: Chain of events from root to target
    """
    resolver = UUIDResolver(project_name, session_id, claude_dir)
    return resolver.get_event_chain(uuid)


def find_prompt_id(
    project_name: str,
    session_id: str,
    event_uuid: str,
    claude_dir: Optional[Path] = None,
) -> Optional[str]:
    """
    Find the promptId for an event by walking up the chain.

    Args:
        project_name: Normalized project name
        session_id: Session UUID
        event_uuid: Event UUID to find prompt for
        claude_dir: Optional path to .claude directory

    Returns:
        Optional[str]: promptId if found
    """
    resolver = UUIDResolver(project_name, session_id, claude_dir)
    return resolver.resolve_prompt_id(event_uuid)


def get_conversation_tree(
    project_name: str,
    session_id: str,
    claude_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Get the full conversation tree for a session.

    Args:
        project_name: Normalized project name
        session_id: Session UUID
        claude_dir: Optional path to .claude directory

    Returns:
        Dict: Tree structure of all events
    """
    resolver = UUIDResolver(project_name, session_id, claude_dir)
    return resolver.get_event_tree()
