# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Knowledge Graph module for the Arbiter platform.
Provides graph-based knowledge representation and querying capabilities.
"""

from typing import Any, Dict, List, Optional, Tuple

try:
    from .core import KnowledgeGraph
except (ImportError, Exception):
    # core.py has heavy optional dependencies (langchain, etc.).
    # Fall back to the lightweight in-memory implementation defined below.

    class KnowledgeGraph:  # type: ignore[no-redef]
        """In-memory KnowledgeGraph used when optional backend deps are unavailable."""

        def __init__(self, config: Optional[Dict[str, Any]] = None):
            self.config = config or {}
            self._nodes: Dict[str, Dict[str, Any]] = {}
            self._edges: List[Dict[str, Any]] = []

        async def connect(self) -> bool:
            return True

        async def disconnect(self) -> bool:
            return True

        async def add_node(self, node_id: str, properties: Dict[str, Any]) -> str:
            self._nodes[node_id] = {"id": node_id, "properties": properties}
            return node_id

        async def add_edge(
            self,
            from_node: str,
            to_node: str,
            relationship: str,
            properties: Optional[Dict[str, Any]] = None,
        ) -> bool:
            self._edges.append({
                "from": from_node, "to": to_node,
                "relationship": relationship,
                "properties": properties or {},
            })
            return True

        async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
            return self._nodes.get(node_id)

        async def query(self, query_string: str) -> List[Dict[str, Any]]:
            return [{"result": "mock_result", "query": query_string}]

        async def get_neighbors(
            self, node_id: str, relationship_type: Optional[str] = None
        ) -> List[Tuple[str, str]]:
            return [
                (e["to"], e["relationship"])
                for e in self._edges
                if e["from"] == node_id
                and (relationship_type is None or e["relationship"] == relationship_type)
            ]

        async def find_path(
            self, start_node: str, end_node: str, max_depth: int = 5
        ) -> Optional[List[str]]:
            if start_node == end_node:
                return [start_node]
            visited: set = set()
            queue = [(start_node, [start_node])]
            while queue:
                current, path = queue.pop(0)
                if len(path) > max_depth or current in visited:
                    continue
                visited.add(current)
                for neighbor, _ in await self.get_neighbors(current):
                    if neighbor == end_node:
                        return path + [neighbor]
                    if neighbor not in visited:
                        queue.append((neighbor, path + [neighbor]))
            return None

        async def add_fact(
            self,
            domain: str,
            key: str,
            data: Dict[str, Any],
            **kwargs: Any,
        ) -> Dict[str, Any]:
            safe_domain = domain.replace(":", "_")
            safe_key = key.replace(":", "_")
            fact_id = f"{safe_domain}:{safe_key}"
            await self.add_node(fact_id, {
                "domain": safe_domain, "key": safe_key,
                "data": data, **kwargs,
            })
            return {"status": "success", "fact_id": fact_id}

        async def clear(self) -> None:
            self._nodes = {}
            self._edges = []

        async def get_stats(self) -> Dict[str, int]:
            return {"node_count": len(self._nodes), "edge_count": len(self._edges)}

# Export the main class
__all__ = ["KnowledgeGraph"]
