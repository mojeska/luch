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
