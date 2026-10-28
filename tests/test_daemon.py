import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from luch import daemon as daemon_mod
from luch.daemon import UNIX_SOCKETS, Daemon
from luch.radio import Channel, RadioState
from luch.remote import RemoteRadio



@pytest.fixture(autouse=True, params=["unix", "localhost"])
def transport(request, monkeypatch):
    """Every test runs over the Unix socket (macOS, Linux) and the localhost port + key (Windows)."""
    if request.param == "unix" and not UNIX_SOCKETS:
        pytest.skip("no Unix sockets on Windows")
    monkeypatch.setattr(daemon_mod, "UNIX_SOCKETS", request.param == "unix")
    return request.param


def tmpdir():
    return Path(tempfile.mkdtemp(prefix="luch", dir=None if os.name == "nt" else "/tmp"))  # short socket paths


async def test_logger_serves_state_messages_and_sends():
    d = tmpdir()
    try:
        daemon = Daemon("fake", d / "luch.db")

        async def fake_run():
            daemon.radio.state = RadioState(connected=True, name="Mojeska", link="BLE",
                                            channels=[Channel(0, "Public")])
            daemon.notify("state", daemon.radio.state)
            await asyncio.Event().wait()

        async def fake_send(conv, text):
            return daemon.store.add(conv, "out", "Mojeska", text, 1, status="sent")

        daemon.radio.run = fake_run
        daemon.radio.send = fake_send
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0.2)

        seen = []
        client = RemoteRadio(daemon.sock_path, lambda k, v: seen.append((k, v)))
        ctask = asyncio.create_task(client.run())
        await asyncio.sleep(0.2)
        assert client.state.name == "Mojeska" and client.state.channels[0].name == "Public"

        mid = daemon.store.add("chan:0", "in", "Ivan", "приём", 2)
        daemon.notify("message", mid)
        await asyncio.sleep(0.1)
        assert ("message", mid) in seen

        sent = await client.send("chan:0", "hello")
        assert daemon.store.get(sent).text == "hello"

        await client.stop()
        ctask.cancel()
        daemon.radio.stop = lambda: asyncio.sleep(0)
        if daemon_mod.UNIX_SOCKETS:
            import signal
            os.kill(os.getpid(), signal.SIGTERM)  # what launchd and systemd send
        else:
            daemon.request_stop()
        await asyncio.wait_for(task, 3)
        assert not daemon.sock_path.exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_screen_on_another_machine_mirrors_the_log():
    """Over an ssh-forwarded socket the screen can't read the logger's SQLite file, so it keeps a copy."""
    from luch.store import Store
    d = tmpdir()
    try:
        daemon = Daemon("fake", d / "luch.db")
        old = daemon.store.add("chan:0", "in", "Ivan", "старое", 1)
        daemon.store.set_title("chan:0", "Public")

        async def fake_run():
            daemon.radio.state = RadioState(connected=True, name="Mojeska", link="WiFi")
            daemon.notify("state", daemon.radio.state)
            await asyncio.Event().wait()

        async def fake_send(conv, text):
            return daemon.store.add(conv, "out", "Mojeska", text, 5, status="sent")

        daemon.radio.run = fake_run
        daemon.radio.send = fake_send
        daemon.radio.stop = lambda: asyncio.sleep(0)
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0.2)

        mirror = Store(":memory:")
        seen = []
        client = RemoteRadio(daemon.sock_path, lambda k, v: seen.append((k, v)), mirror=mirror)
        ctask = asyncio.create_task(client.run())
        await asyncio.sleep(0.3)
        assert ("synced", None) in seen
        assert mirror.get(old).text == "старое"
        assert ("chan:0", "Public") in mirror.titles()

        mid = daemon.store.add("chan:0", "in", "Olga", "новое", 2)
        daemon.notify("message", mid)
        await asyncio.sleep(0.1)
        assert ("message", mid) in seen and mirror.get(mid).text == "новое"

        sent = await client.send("chan:0", "hello")
        assert mirror.get(sent).text == "hello"

        daemon.store.set_status(sent, "delivered")
        daemon.notify("updated", sent)
        await asyncio.sleep(0.1)
        assert mirror.get(sent).status == "delivered"

        await client.stop()
        ctask.cancel()
        task.cancel()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_a_send_shows_on_the_other_screens():
    """A message sent from one screen reaches every other screen on the logger, not just the sender."""
    from luch.store import Store
    d = tmpdir()
    try:
        daemon = Daemon("fake", d / "luch.db")

        async def fake_run():
            daemon.radio.state = RadioState(connected=True, name="Mojeska", link="WiFi")
            daemon.notify("state", daemon.radio.state)
            await asyncio.Event().wait()

        async def fake_send(conv, text):
            return daemon.store.add(conv, "out", "Mojeska", text, 5, status="sent")

        daemon.radio.run = fake_run
        daemon.radio.send = fake_send
        daemon.radio.stop = lambda: asyncio.sleep(0)
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0.2)

        sender_seen, local_seen, remote_seen = [], [], []
        mirror = Store(":memory:")
        clients = [
            RemoteRadio(daemon.sock_path, lambda k, v: sender_seen.append((k, v))),
            RemoteRadio(daemon.sock_path, lambda k, v: local_seen.append((k, v))),  # same machine
            RemoteRadio(daemon.sock_path, lambda k, v: remote_seen.append((k, v)), mirror=mirror),
        ]
        tasks = [asyncio.create_task(c.run()) for c in clients]
        await asyncio.sleep(0.3)

        mid = await clients[0].send("chan:0", "привет")
        await asyncio.sleep(0.1)
        assert ("message", mid) in local_seen
        assert ("message", mid) in remote_seen and mirror.get(mid).text == "привет"
        assert ("message", mid) not in sender_seen  # it shows its own send from the reply

        for c in clients:
            await c.stop()
        for t in tasks + [task]:
            t.cancel()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_localhost_logger_wants_the_key(monkeypatch, transport):
    if transport != "localhost":
        pytest.skip("only the localhost transport has a key")
    d = tmpdir()
    try:
        daemon = Daemon("fake", d / "luch.db")
        daemon.radio.run = lambda: asyncio.Event().wait()
        task = asyncio.create_task(daemon.run())
        await asyncio.sleep(0.2)
        import json
        info = json.loads(daemon.sock_path.read_text())
        reader, writer = await asyncio.open_connection("127.0.0.1", info["port"])
        writer.write(b'{"op": "auth", "key": "wrong"}\n')
        assert await asyncio.wait_for(reader.readline(), 2) == b""  # dropped, no hello
        assert await daemon_mod.socket_alive(daemon.sock_path)      # the right key works
        daemon.request_stop()
        await asyncio.wait_for(task, 3)
        assert not daemon.sock_path.exists()
        assert not await daemon_mod.socket_alive(daemon.sock_path)
    finally:
        shutil.rmtree(d, ignore_errors=True)
