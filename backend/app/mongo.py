import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import Settings
from .schema import ensure_schema


@dataclass
class MongoStatus:
    state: str = "unavailable"
    detail: str = "MongoDB has not been checked"


class MongoAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        self.database = None
        self.status = MongoStatus()
        self._probe_lock = asyncio.Lock()

    async def connect(self) -> MongoStatus:
        try:
            from pymongo import MongoClient
            from pymongo.uri_parser import parse_uri

            if self.settings.mongodb_db != "story_tool_web_dev":
                raise ValueError("MONGODB_DB must be story_tool_web_dev")
            parsed = parse_uri(self.settings.mongodb_uri)
            if parsed.get("username") or parsed.get("password"):
                raise ValueError("development MongoDB URI must not contain credentials")
            hosts = [host for host, _ in parsed["nodelist"]]
            if not hosts or any(host.lower() not in {"127.0.0.1", "localhost", "::1"} for host in hosts):
                raise ValueError("development MongoDB must bind to loopback")
            if parsed.get("options", {}).get("replicaSet") != "storytool-dev":
                raise ValueError("MongoDB replica set must be storytool-dev")

            self.client = await asyncio.to_thread(
                MongoClient,
                self.settings.mongodb_uri,
                serverSelectionTimeoutMS=self.settings.mongodb_timeout_ms,
                connectTimeoutMS=self.settings.mongodb_timeout_ms,
                socketTimeoutMS=self.settings.mongodb_timeout_ms,
            )
            hello = await asyncio.wait_for(asyncio.to_thread(self.client.admin.command, "hello"), timeout=2.0)
            if hello.get("setName") != "storytool-dev":
                raise ValueError("MongoDB server is not the storytool-dev replica set")
            if not hello.get("isWritablePrimary"):
                raise ValueError("storytool-dev has no writable PRIMARY")
            self.database = self.client[self.settings.mongodb_db]
            await asyncio.wait_for(asyncio.to_thread(ensure_schema, self.database), timeout=5.0)
            self.status = MongoStatus("connected", f"database={self.settings.mongodb_db}; replicaSet=storytool-dev; state=PRIMARY")
        except Exception as exc:  # startup diagnostics must preserve a safe, bounded reason
            safe_reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            self.status = MongoStatus("unavailable", f"MongoDB unavailable: {safe_reason[:160]}")
            if self.client is not None:
                self.client.close()
            self.client = None
            self.database = None
        return self.status

    async def close(self) -> None:
        if self.client is not None:
            await asyncio.to_thread(self.client.close)
            self.client = None
            self.database = None

    async def check_readiness(self) -> MongoStatus:
        """Probe the actual dev replica set and reconnect after an outage."""
        async with self._probe_lock:
            if self.client is None:
                return await self.connect()
            try:
                hello = await asyncio.wait_for(
                    asyncio.to_thread(self.client.admin.command, "hello"),
                    timeout=max(2.0, self.settings.mongodb_timeout_ms / 1000 * 2),
                )
                if hello.get("setName") != "storytool-dev":
                    raise ValueError("MongoDB server is not the storytool-dev replica set")
                if not hello.get("isWritablePrimary"):
                    raise ValueError("storytool-dev has no writable PRIMARY")
                self.status = MongoStatus("connected", f"database={self.settings.mongodb_db}; replicaSet=storytool-dev; state=PRIMARY")
            except Exception as exc:
                safe_reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                self.status = MongoStatus("unavailable", f"MongoDB unavailable: {safe_reason[:160]}")
                if self.client is not None:
                    self.client.close()
                self.client = None
                self.database = None
            return self.status

    async def check_liveness(self) -> MongoStatus:
        """Refresh reported database state without making process liveness depend on Mongo."""
        if self.client is None:
            return self.status
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.client.admin.command, "ping"),
                timeout=max(2.0, self.settings.mongodb_timeout_ms / 1000 * 2),
            )
        except Exception:
            self.status = MongoStatus("unavailable", "MongoDB unavailable: health probe failed")
            self.client.close()
            self.client = None
            self.database = None
        return self.status

    @property
    def is_ready(self) -> bool:
        return self.status.state == "connected"
