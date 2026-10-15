"""FalkorDB access layer.

One FalkorDB graph per player (tenant): ``town:{player_id}``. Every read goes
through ``ro_query`` (GRAPH.RO_QUERY), every write through ``query``; write
statistics are accumulated per tenant so the UI can show writes/sec.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from falkordb.asyncio import FalkorDB

from .config import settings

_PID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def normalize_player_id(raw: str) -> str:
    pid = re.sub(r"[^a-z0-9_-]", "-", raw.strip().lower())[:32].strip("-")
    if not pid or not _PID_RE.match(pid):
        raise ValueError(f"invalid player id: {raw!r}")
    return pid


def graph_name(player_id: str) -> str:
    return f"{settings.graph_prefix}:{player_id}"


@dataclass
class TenantStats:
    queries: int = 0
    reads: int = 0
    writes: int = 0  # individual graph mutations (nodes/edges created+deleted, properties set)
    last_tick_ms: float = 0.0
    last_tick_writes: int = 0
    last_tick_queries: int = 0
    tick_history: list[dict[str, Any]] = field(default_factory=list)


class Store:
    def __init__(self) -> None:
        kwargs: dict[str, Any] = dict(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            max_connections=256,
        )
        if settings.falkordb_username:
            kwargs["username"] = settings.falkordb_username
        if settings.falkordb_password:
            kwargs["password"] = settings.falkordb_password
        if settings.falkordb_tls:
            kwargs["ssl"] = True
        self.db = FalkorDB(**kwargs)
        self.stats: dict[str, TenantStats] = defaultdict(TenantStats)

    def graph(self, player_id: str):
        return self.db.select_graph(graph_name(player_id))

    @staticmethod
    def _rows(res) -> list[dict[str, Any]]:
        header = [h[1] for h in res.header] if res.header else []
        return [dict(zip(header, row)) for row in res.result_set]

    async def ro(self, player_id: str, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        res = await self.graph(player_id).ro_query(cypher, params or {})
        st = self.stats[player_id]
        st.queries += 1
        st.reads += 1
        return self._rows(res)

    async def rw(self, player_id: str, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        res = await self.graph(player_id).query(cypher, params or {})
        st = self.stats[player_id]
        st.queries += 1
        st.writes += int(
            res.nodes_created
            + res.nodes_deleted
            + res.relationships_created
            + res.relationships_deleted
            + res.properties_set
        )
        return self._rows(res)

    async def exists(self, player_id: str) -> bool:
        names = await self.db.list_graphs()
        return graph_name(player_id) in names

    async def list_tenants(self) -> list[str]:
        prefix = settings.graph_prefix + ":"
        return [n[len(prefix):] for n in await self.db.list_graphs() if n.startswith(prefix)]

    async def drop(self, player_id: str) -> None:
        if await self.exists(player_id):
            await self.graph(player_id).delete()
        self.stats.pop(player_id, None)

    async def ping(self) -> bool:
        try:
            await self.db.list_graphs()
            return True
        except Exception:
            return False


class TickTimer:
    """Measures latency + write volume for one simulation tick of one tenant."""

    def __init__(self, store: Store, player_id: str):
        self.store, self.pid = store, player_id

    def __enter__(self):
        st = self.store.stats[self.pid]
        self.w0, self.q0, self.t0 = st.writes, st.queries, time.perf_counter()
        return self

    def __exit__(self, *exc):
        st = self.store.stats[self.pid]
        self.ms = (time.perf_counter() - self.t0) * 1000
        self.writes = st.writes - self.w0
        self.queries = st.queries - self.q0
        st.last_tick_ms, st.last_tick_writes, st.last_tick_queries = self.ms, self.writes, self.queries
        st.tick_history = (st.tick_history + [{"ms": round(self.ms, 1), "writes": self.writes}])[-30:]
        return False


store = Store()
