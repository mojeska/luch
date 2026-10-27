"""Client side of the logger socket. Same interface as Radio, so the screen doesn't care which it has."""
from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any, Callable

from .radio import RadioState, state_from_dict


class RemoteRadio:
    def __init__(self, sock_path: Path, notify: Callable[[str, Any], None]):
        self.sock_path = sock_path
        self.notify = notify
        self.state = RadioState()
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._stop = False

    async def run(self) -> None:
        while not self._stop:
            try:
                reader, self._writer = await asyncio.open_unix_connection(str(self.sock_path))
            except OSError:
                self.notify("status", "LOGGER NOT RUNNING · RETRY")
                await asyncio.sleep(3)
                continue
            self.notify("status", "CONNECTED TO LOGGER")
            try:
                while line := await reader.readline():
                    self._handle(json.loads(line))
            except (ConnectionResetError, json.JSONDecodeError):
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

    async def stop(self) -> None:
        self._stop = True
        if self._writer:
            self._writer.close()
