import asyncio
import shutil
import tempfile
from pathlib import Path

from luch.daemon import Daemon
from luch.radio import Channel, RadioState
from luch.remote import RemoteRadio


async def test_logger_serves_state_messages_and_sends():
    d = Path(tempfile.mkdtemp(prefix="luch", dir="/tmp"))  # unix socket paths must be short
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
        import os, signal
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, 3)
        assert not daemon.sock_path.exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_screen_on_another_machine_mirrors_the_log():
    """Over an ssh-forwarded socket the screen can't read the logger's SQLite file, so it keeps a copy."""
    from luch.store import Store
    d = Path(tempfile.mkdtemp(prefix="luch", dir="/tmp"))
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
