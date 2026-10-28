"""Client side of the logger socket. Same interface as Radio, so the screen doesn't care which it has."""
from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any, Callable

from .daemon import open_logger
from .radio import RadioState, state_from_dict
from .store import Store

LINE_LIMIT = 16 * 1024 * 1024  # a state line with many contacts can pass asyncio's 64 KiB default


async def logger_host(sock_path: Path) -> str | None:
    """Hostname the logger behind this socket runs on (None for loggers too old to say)."""
    try:
        reader, w = await asyncio.wait_for(open_logger(sock_path, limit=LINE_LIMIT), 3)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        msg = json.loads(await asyncio.wait_for(reader.readline(), 3))
        return msg["data"]["host"] if msg.get("kind") == "hello" else None
    except (asyncio.TimeoutError, ValueError, KeyError, TypeError):
        return None
    finally:
        w.close()


class RemoteRadio:
    def __init__(self, sock_path: Path, notify: Callable[[str, Any], None], mirror: Store | None = None):
        """`mirror`: a local copy of the logger's log, for a logger on another machine."""
        self.sock_path = sock_path
        self.notify = notify
        self.mirror = mirror
        self.state = RadioState()
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._stop = False

    async def run(self) -> None:
        while not self._stop:
            try:
                reader, self._writer = await open_logger(self.sock_path, limit=LINE_LIMIT)
            except OSError:
                self.notify("status", "LOGGER NOT RUNNING · RETRY")
                await asyncio.sleep(3)
                continue
            self.notify("status", "CONNECTED TO LOGGER")
            try:
                while line := await reader.readline():
                    self._handle(json.loads(line))
            except (ConnectionResetError, ValueError):
                pass
            self._writer = None
            self.state.connected = False
            self.notify("state", self.state)
            self.notify("status", "LOGGER LINK LOST")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("LOGGER LINK LOST"))
            self._pending.clear()
            if not self._stop:
                await asyncio.sleep(2)

    def _handle(self, msg: dict) -> None:
        kind = msg.get("kind")
        if self.mirror is not None:
            if kind == "hello":
                self._writer.write((json.dumps({"op": "sync", "since": self.mirror.max_id()}) + "\n").encode())
                return
            if kind == "row":
                self.mirror.put(msg["data"])
                return
            if kind == "synced":
                for conv, title in msg.get("data") or []:
                    self.mirror.set_title(conv, title)
                self.notify("synced", None)
                return
            if msg.get("msg"):
                self.mirror.put(msg["msg"])
        if kind in ("hello", "row", "synced"):
            return
        if kind == "sent":
            fut = self._pending.pop(msg.get("req"), None)
            if fut and not fut.done():
                if msg.get("error"):
                    fut.set_exception(RuntimeError(msg["error"]))
                else:
                    fut.set_result(msg.get("id"))
            return
        data = msg.get("data")
        if kind == "state":
            self.state = state_from_dict(data)
            data = self.state
        self.notify(kind, data)

    async def _request(self, body: dict) -> Any:
        if not self._writer:
            raise RuntimeError("NO LINK TO LOGGER")
        req = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[req] = fut
        self._writer.write((json.dumps({**body, "req": req}) + "\n").encode())
        await self._writer.drain()
        return await asyncio.wait_for(fut, 30)

    async def send(self, conv: str, text: str) -> int | None:
        return await self._request({"op": "send", "conv": conv, "text": text})

    async def join_channel(self, name: str, secret: str | None = None) -> int:
        return await self._request({"op": "call", "method": "join_channel", "args": [name, secret]})

    async def leave_channel(self, idx: int) -> None:
        await self._request({"op": "call", "method": "leave_channel", "args": [idx]})

    async def set_favourite(self, public_key: str, on: bool) -> None:
        await self._request({"op": "call", "method": "set_favourite", "args": [public_key, on]})

    async def set_gps(self, on: bool, interval: int | None = None) -> None:
        await self._request({"op": "call", "method": "set_gps", "args": [on, interval]})

    async def cli(self, command: str) -> str:
        return await self._request({"op": "call", "method": "cli", "args": [command]})

    async def set_name(self, name: str) -> None:
        await self._request({"op": "call", "method": "set_name", "args": [name]})

    async def reboot(self) -> None:
        await self._request({"op": "call", "method": "reboot", "args": []})

    async def stop(self) -> None:
        self._stop = True
        if self._writer:
            self._writer.close()
