"""Noticing a dead or stolen link: the radio's Wi-Fi serves one client, and the newest wins."""
import asyncio
from types import SimpleNamespace

from meshcore import EventType

from luch import radio as radio_mod
from luch.radio import Radio
from luch.store import Store


def ev(kind, **payload):
    return SimpleNamespace(type=kind, payload=payload)


class FakeMC:
    """A radio that answers setup, then answers `beats` battery reads before going quiet."""

    def __init__(self, beats=0, contacts_time_out=False):
        self.self_info = {"name": "Mojeska"}
        self.contacts = {}
        self.beats = beats
        self.disconnected = False
        mc = self

        class Commands:
            async def send_device_query(self):
                return ev(EventType.DEVICE_INFO, ver="v1.18.0", max_channels=1)

            async def get_custom_vars(self):
                return ev(EventType.CUSTOM_VARS)

            async def get_channel(self, idx):
                return ev(EventType.CHANNEL_INFO, channel_name="Public")

            async def get_contacts(self, timeout=5):
                mc.contacts_timeout = timeout
                if contacts_time_out:
                    return ev(EventType.ERROR, reason="timeout waiting for contacts")
                return ev(EventType.CONTACTS)

            async def get_msg(self):
                return ev(EventType.NO_MORE_MSGS)

            async def get_bat(self):
                if mc.beats > 0:
                    mc.beats -= 1
                    return ev(EventType.BATTERY, level=4100)
                return ev(EventType.ERROR, reason="timeout")

        self.commands = Commands()

    def subscribe(self, *a):
        return object()

    def unsubscribe(self, sub):
        pass

    async def start_auto_message_fetching(self):
        pass

    async def stop_auto_message_fetching(self):
        pass

    async def disconnect(self):
        self.disconnected = True


def fast(monkeypatch, radios):
    monkeypatch.setattr(radio_mod, "HEARTBEAT_SECS", 0.01)
    monkeypatch.setattr(radio_mod, "RETRY_SECS", 0.01)
    made = []

    async def create_tcp(host, port, **kw):
        mc = radios.pop(0) if radios else FakeMC()
        made.append(mc)
        return mc
    monkeypatch.setattr(radio_mod.MeshCore, "create_tcp", staticmethod(create_tcp))
    return made


async def wait_for(cond, secs=3):
    for _ in range(int(secs / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def test_a_silent_link_counts_as_lost(monkeypatch):
    fast(monkeypatch, [FakeMC(beats=3)])
    monkeypatch.setattr(radio_mod, "TAKEOVER_SECS", 0)  # not a takeover here, just silence
    statuses = []
    r = Radio("x", Store(":memory:"), lambda k, d: statuses.append(d) if k == "status" else None, tcp="radio")
    task = asyncio.create_task(r.run())
    await wait_for(lambda: "NO REPLY FROM RADIO" in statuses)
    assert "LINK LOST" in statuses[statuses.index("NO REPLY FROM RADIO"):]
    await r.stop()
    task.cancel()


async def test_contacts_get_time_for_a_slow_bluetooth_list(monkeypatch):
    # MeshCore's timeout covers the whole list; 350 contacts take about 20 s over Bluetooth.
    fake = FakeMC()
    fast(monkeypatch, [fake])
    statuses = []
    r = Radio("x", Store(":memory:"), lambda k, d: statuses.append(d) if k == "status" else None, tcp="radio")
    task = asyncio.create_task(r.run())
    await wait_for(lambda: "LINK ESTABLISHED" in statuses)
    assert fake.contacts_timeout >= 30
    await r.stop()
    task.cancel()


async def test_contacts_that_never_arrive_drop_the_link(monkeypatch):
    fast(monkeypatch, [FakeMC(contacts_time_out=True)])
    statuses = []
    r = Radio("x", Store(":memory:"), lambda k, d: statuses.append(d) if k == "status" else None, tcp="radio")
    task = asyncio.create_task(r.run())
    await wait_for(lambda: "NO REPLY FROM RADIO (CONTACTS)" in statuses)
    assert "LINK ESTABLISHED" not in statuses
    await r.stop()
    task.cancel()


async def test_a_screen_gives_the_radio_up_until_reconnect(monkeypatch):
    made = fast(monkeypatch, [])
    statuses = []
    r = Radio("x", Store(":memory:"), lambda k, d: statuses.append(d) if k == "status" else None, tcp="radio")
    r.yield_on_takeover = True
    task = asyncio.create_task(r.run())
    await wait_for(lambda: r.taken)
    assert any("RADIO TAKEN" in s and "/reconnect" in s for s in statuses)
    tries = len(made)
    await asyncio.sleep(0.2)
    assert len(made) == tries  # stopped trying
    r.reconnect()
    await wait_for(lambda: len(made) > tries)
    await r.stop()
    task.cancel()


async def test_the_logger_backs_off_instead(monkeypatch):
    fast(monkeypatch, [])
    statuses = []
    r = Radio("x", Store(":memory:"), lambda k, d: statuses.append(d) if k == "status" else None, tcp="radio")
    task = asyncio.create_task(r.run())
    await wait_for(lambda: any("RETRY IN 30s" in s for s in statuses))
    assert not r.taken
    await r.stop()
    await asyncio.wait_for(task, 1)  # stop() also ends the back-off pause


class SilentSerialMC:
    """A radio on USB that never answers; records when each port open happens."""
    opens = []

    def __init__(self, cx, **kw):
        self.cx = cx

    async def connect(self):
        SilentSerialMC.opens.append((self.cx.dtr, asyncio.get_running_loop().time()))
        return None

    async def disconnect(self):
        pass


async def test_usb_tries_both_dtr_settings_with_a_pause(monkeypatch):
    # Windows refuses to reopen a COM port the moment it's closed ("Access is denied").
    SilentSerialMC.opens = []
    monkeypatch.setattr(radio_mod, "MeshCore", SilentSerialMC)
    monkeypatch.setattr(radio_mod, "SERIAL_REOPEN_SECS", 0.05)
    try:
        await radio_mod.open_serial("COM6")
    except RuntimeError as e:
        assert "NO REPLY" in str(e)
    else:
        raise AssertionError("a silent radio should raise")
    (dtr1, t1), (dtr2, t2) = SilentSerialMC.opens
    assert (dtr1, dtr2) == (True, False)
    assert t2 - t1 >= 0.05
