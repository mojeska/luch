"""Background logger: owns the radio, logs everything, and serves LUCH screens over a Unix socket.

Protocol: one JSON object per line.
  daemon -> client  {"kind": "state" | "status" | "message" | "updated", "data": ...}
  client -> daemon  {"op": "send", "req": n, "conv": "...", "text": "..."}
  daemon -> client  {"kind": "sent", "req": n, "id": <message id> | null, "error": "..." | null, "msg": {...}}
Every other connected screen also gets that sent message as a "message", like one received.
On the same machine, screens read messages from the shared SQLite log by id. A screen on another
machine (socket forwarded over ssh) can't see that file, so "message", "updated" and "sent" also
carry the row as "msg", the first line is {"kind": "hello", "data": {"host": ...}}, and
  client -> daemon  {"op": "sync", "since": n}
answers with every row after id n as {"kind": "row", "data": {...}}, then
{"kind": "synced", "data": [[conv, title], ...]}.

Transport: a Unix socket at <radio>.sock (mode 0600) on macOS and Linux. Python's asyncio has no
Unix sockets on Windows, so there the logger listens on 127.0.0.1 at a random port and <radio>.sock
is a small file holding {"port": n, "key": "..."}; a client's first line is {"op": "auth", "key": ...}.
The file sits in the user's own %LOCALAPPDATA%, so only they can read the key.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import signal
import socket
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .radio import Radio, state_to_dict
from .store import Store

log = logging.getLogger("luch.daemon")
UNIX_SOCKETS = sys.platform != "win32"  # see Transport above

CALLABLE = {"join_channel", "leave_channel", "set_favourite", "set_gps", "cli", "reboot", "set_name"}  # radio methods screens may call


def default_socket_path(db_path: Path) -> Path:
    return db_path.with_suffix(".sock")


class Daemon:
    def __init__(self, device: str, db_path: Path, port: str | None = None, tcp: str | None = None,
                 pin: str | None = None):
        self.store = Store(db_path)
        self.sock_path = default_socket_path(db_path)
        self.radio = Radio(device, self.store, self.notify, port=port, tcp=tcp, pin=pin)
        self.clients: set[asyncio.StreamWriter] = set()
        self.last_status = "STARTING"
        self.key = ""  # Windows: what a client must send first
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def notify(self, kind: str, data: Any, skip: asyncio.StreamWriter | None = None) -> None:
        extra = {}
        if kind == "state":
            data = state_to_dict(data)
        elif kind == "status":
            self.last_status = data
            log.info("%s", data)
        elif kind == "message":
            m = self.store.get(data)
            if m:
                log.info("%s %s %s: %s", m.conv, m.direction, m.sender, m.text)
                extra["msg"] = asdict(m)
        elif kind == "updated":
            m = self.store.get(data)
            if m:
                extra["msg"] = asdict(m)
        self.broadcast({"kind": kind, "data": data, **extra}, skip)

    def broadcast(self, obj: dict, skip: asyncio.StreamWriter | None = None) -> None:
        line = (json.dumps(obj) + "\n").encode()
        for w in list(self.clients - {skip}):
            try:
                w.write(line)
            except Exception:
                self.clients.discard(w)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.key:  # localhost TCP: anyone on this PC can connect, so ask for the key first
            try:
                first = json.loads(await asyncio.wait_for(reader.readline(), 5))
                ok = hmac.compare_digest(str(first.get("key", "")), self.key)
            except (asyncio.TimeoutError, ValueError, AttributeError, ConnectionError):
                ok = False
            if not ok:
                writer.close()
                return
        self.clients.add(writer)
        writer.write((json.dumps({"kind": "hello", "data": {"host": socket.gethostname()}}) + "\n").encode())
        writer.write((json.dumps({"kind": "state", "data": state_to_dict(self.radio.state)}) + "\n").encode())
        writer.write((json.dumps({"kind": "status", "data": self.last_status}) + "\n").encode())
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if req.get("op") == "send":
                    reply = {"kind": "sent", "req": req.get("req"), "id": None, "error": None}
                    try:
                        reply["id"] = await self.radio.send(req["conv"], req["text"])
                        if reply["id"] and (m := self.store.get(reply["id"])):
                            reply["msg"] = asdict(m)
                    except Exception as e:
                        reply["error"] = str(e)
                    writer.write((json.dumps(reply) + "\n").encode())
                    if reply["id"]:  # other screens see it too; the sender already has it from the reply
                        self.notify("message", reply["id"], skip=writer)
                elif req.get("op") == "sync":
                    for i, m in enumerate(self.store.since(int(req.get("since") or 0))):
                        writer.write((json.dumps({"kind": "row", "data": asdict(m)}) + "\n").encode())
                        if i % 200 == 199:
                            await writer.drain()
                    writer.write((json.dumps({"kind": "synced", "data": self.store.titles()}) + "\n").encode())
                elif req.get("op") == "call" and req.get("method") in CALLABLE:
                    reply = {"kind": "sent", "req": req.get("req"), "id": None, "error": None}
                    try:
                        reply["id"] = await getattr(self.radio, req["method"])(*req.get("args", []))
                    except Exception as e:
                        reply["error"] = str(e)
                    writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    async def run(self) -> None:
        if self.sock_path.exists():
            if await socket_alive(self.sock_path):
                raise SystemExit(f"another LUCH logger is already running ({self.sock_path})")
            self.sock_path.unlink()
        if UNIX_SOCKETS:
            server = await asyncio.start_unix_server(self.handle, path=str(self.sock_path))
            os.chmod(self.sock_path, 0o600)
        else:
            self.key = secrets.token_hex(16)
            server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            tmp = self.sock_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"port": port, "key": self.key, "pid": os.getpid()}))
            tmp.replace(self.sock_path)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, AttributeError, ValueError):
                pass  # Windows: Ctrl+C cancels the run instead, and the finally below still cleans up
        radio_task = asyncio.create_task(self.radio.run())
        log.info("logger up, socket %s", self.sock_path)
        try:
            await self._stop.wait()
        finally:
            log.info("logger stopping")
            await self.radio.stop()
            radio_task.cancel()
            server.close()
            self.sock_path.unlink(missing_ok=True)
            self.store.close()


async def open_logger(path: Path, limit: int = 2 ** 16):
    """Connect to the logger behind `path` (see Transport above). Raises OSError if none answers."""
    if UNIX_SOCKETS:
        return await asyncio.open_unix_connection(str(path), limit=limit)
    try:
        info = json.loads(path.read_text())
        port, key = int(info["port"]), str(info["key"])
    except (ValueError, KeyError, TypeError) as e:
        raise ConnectionRefusedError(f"unreadable logger file {path}") from e
    reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=limit)
    writer.write((json.dumps({"op": "auth", "key": key}) + "\n").encode())
    return reader, writer


async def socket_alive(path: Path) -> bool:
    try:
        _, w = await asyncio.wait_for(open_logger(path), 1)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


def run_daemon(device: str, db_path: Path, port: str | None, tcp: str | None = None,
               pin: str | None = None) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    if sys.stderr is None:  # pythonw.exe from Task Scheduler: no console, so log to a file
        logging.basicConfig(filename=str(db_path.parent / "logger.log"), level=logging.INFO, format=fmt)
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)
    logging.getLogger("meshcore").setLevel(logging.ERROR)
    asyncio.run(Daemon(device, db_path, port, tcp, pin).run())
