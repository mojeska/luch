"""Background logger: owns the radio, logs everything, and serves LUCH screens over a Unix socket.

Protocol: one JSON object per line.
  daemon -> client  {"kind": "state" | "status" | "message" | "updated", "data": ...}
  client -> daemon  {"op": "send", "req": n, "conv": "...", "text": "..."}
  daemon -> client  {"kind": "sent", "req": n, "id": <message id> | null, "error": "..." | null}
Messages themselves are read from the shared SQLite log by id.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

from .radio import Radio, state_to_dict
from .store import Store

log = logging.getLogger("luch.daemon")

CALLABLE = {"join_channel", "leave_channel", "set_favourite", "set_gps", "cli", "reboot", "set_name"}  # radio methods screens may call


def default_socket_path(db_path: Path) -> Path:
    return db_path.with_suffix(".sock")


class Daemon:
    def __init__(self, device: str, db_path: Path, port: str | None = None, tcp: str | None = None):
        self.store = Store(db_path)
        self.sock_path = default_socket_path(db_path)
        self.radio = Radio(device, self.store, self.notify, port=port, tcp=tcp)
        self.clients: set[asyncio.StreamWriter] = set()
        self.last_status = "STARTING"

    def notify(self, kind: str, data: Any) -> None:
        if kind == "state":
            data = state_to_dict(data)
        elif kind == "status":
            self.last_status = data
            log.info("%s", data)
        elif kind == "message":
            m = self.store.get(data)
            if m:
                log.info("%s %s %s: %s", m.conv, m.direction, m.sender, m.text)
        self.broadcast({"kind": kind, "data": data})

    def broadcast(self, obj: dict) -> None:
        line = (json.dumps(obj) + "\n").encode()
        for w in list(self.clients):
            try:
                w.write(line)
            except Exception:
                self.clients.discard(w)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
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
                    except Exception as e:
                        reply["error"] = str(e)
                    writer.write((json.dumps(reply) + "\n").encode())
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
        server = await asyncio.start_unix_server(self.handle, path=str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        radio_task = asyncio.create_task(self.radio.run())
        log.info("logger up, socket %s", self.sock_path)
        await stop.wait()
        log.info("logger stopping")
        await self.radio.stop()
        radio_task.cancel()
        server.close()
        self.sock_path.unlink(missing_ok=True)
        self.store.close()


async def socket_alive(path: Path) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 1)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


def run_daemon(device: str, db_path: Path, port: str | None, tcp: str | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("meshcore").setLevel(logging.ERROR)
    asyncio.run(Daemon(device, db_path, port, tcp).run())
