"""FalkorDB access layer.

One FalkorDB graph per player (tenant): ``town:{player_id}``. Every read goes
through ``ro_query`` (GRAPH.RO_QUERY), every write through ``query``; write
statistics are accumulated per tenant so the UI can show writes/sec.
"""
from __future__ import annotations

import asyncio
import re
import socket
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from falkordb.asyncio import FalkorDB
from redis.asyncio import BlockingConnectionPool, SSLConnection
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError

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


POOL_SIZE = 24  # bounded and pre-warmed: opening a connection to a remote DB costs several round trips


def _keepalive_options() -> dict[int, int]:
    """Keep pooled TCP connections alive through cloud load balancers that drop idle flows."""
    opts = {}
    for name, value in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 20), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, name):
            opts[getattr(socket, name)] = value
    return opts


class Store:
    def __init__(self) -> None:
        self._db: FalkorDB | None = None  # connect lazily: an unreachable DB must not crash the app at import
        self._known: set[str] = set()  # tenants known to exist (saves a GRAPH.LIST round trip per request)
        self.stats: dict[str, TenantStats] = defaultdict(TenantStats)

    @property
    def db(self) -> FalkorDB:
        if self._db is None:
            kwargs: dict[str, Any] = dict(
                host=settings.falkordb_host, port=settings.falkordb_port, decode_responses=True,
                max_connections=POOL_SIZE, timeout=30, socket_connect_timeout=15, socket_timeout=10,
                socket_keepalive=True, socket_keepalive_options=_keepalive_options(),
                health_check_interval=120, retry_on_error=[ConnectionError, TimeoutError])
            if settings.falkordb_username:
                kwargs["username"] = settings.falkordb_username
            if settings.falkordb_password:
                kwargs["password"] = settings.falkordb_password
            if settings.falkordb_tls:
                kwargs["connection_class"] = SSLConnection
            pool = BlockingConnectionPool(**kwargs)
            self._db = FalkorDB(connection_pool=pool)
            # set after construction: FalkorDB probes the server with a *sync* client built from these kwargs
            pool.connection_kwargs["retry"] = Retry(ExponentialBackoff(cap=1.0, base=0.05), 3)
        return self._db

    async def warmup(self, n: int = POOL_SIZE) -> None:
        """Open the whole pool up front so ticks never pay for connection setup."""
        conn = self.db.connection
        await asyncio.gather(*(conn.execute_command("PING") for _ in range(n)), return_exceptions=True)

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
        if player_id in self._known:
            return True
        names = await self.db.list_graphs()
        found = graph_name(player_id) in names
        if found:
            self._known.add(player_id)
        return found

    async def list_tenants(self) -> list[str]:
        prefix = settings.graph_prefix + ":"
        return [n[len(prefix):] for n in await self.db.list_graphs() if n.startswith(prefix)]

    async def drop(self, player_id: str) -> None:
        self._known.discard(player_id)
        if await self.exists(player_id):
            await self.graph(player_id).delete()
        self._known.discard(player_id)
        self.stats.pop(player_id, None)

    async def ping(self) -> bool:
        try:
            await self.db.list_graphs()
            return True
        except Exception:
            return False

    async def heartbeat(self) -> bool:
        """A real graph write. FalkorDB Cloud's free tier stops instances it sees as idle, and
        listing graphs alone does not count as activity. Uses its own graph, outside the town: prefix."""
        try:
            await self.db.select_graph("keepalive").query(
                "MERGE (h:Heartbeat {id: 1}) SET h.at = timestamp() RETURN h.at")
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
