"""Talks to a MeshCore Companion radio over Bluetooth and logs every message to the Store."""
from __future__ import annotations

import asyncio
import glob
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from meshcore import EventType, MeshCore

from .store import Store

log = logging.getLogger("luch")

CONTACT_CHAT, CONTACT_REPEATER, CONTACT_ROOM, CONTACT_SENSOR = 1, 2, 3, 4


@dataclass
class Channel:
    idx: int
    name: str

    @property
    def conv(self) -> str:
        return f"chan:{self.idx}"


@dataclass
class Contact:
    public_key: str
    name: str
    type: int
    last_advert: int
    path_len: int

    @property
    def conv(self) -> str:
        return f"dm:{self.public_key[:12]}"


@dataclass
class RadioState:
    connected: bool = False
    name: str = ""
    freq: float = 0.0
    bw: float = 0.0
    sf: int = 0
    tx_power: int = 0
    battery_mv: int = 0
    firmware: str = ""
    link: str = ""
    channels: list[Channel] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)


def state_to_dict(s: RadioState) -> dict:
    return asdict(s)


def state_from_dict(d: dict) -> RadioState:
    d = dict(d)
    d["channels"] = [Channel(**c) for c in d.get("channels", [])]
    d["contacts"] = [Contact(**c) for c in d.get("contacts", [])]
    return RadioState(**d)


USB_PATTERNS = ["/dev/cu.usbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*",
                "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*"]


def find_usb_port() -> str | None:
    """First USB serial port that looks like a radio (Heltec V3 uses a CP2102)."""
    for pat in USB_PATTERNS:
        ports = sorted(glob.glob(pat))
        if ports:
            return ports[0]
    return None


def split_channel_text(text: str) -> tuple[str, str]:
    """Channel messages arrive as 'Sender: text'."""
    name, sep, body = text.partition(": ")
    if sep and 0 < len(name) <= 32:
        return name, body
    return "?", text


class Radio:
    """Owns the MeshCore connection. `notify(kind, data)` is called for every change."""

    def __init__(self, device: str, store: Store, notify: Callable[[str, Any], None], port: str | None = None):
        self.device = device
        self.port = port  # USB serial port; "auto" to find one. None = Bluetooth
        self.store = store
        self.notify = notify
        self.state = RadioState()
        self.mc: MeshCore | None = None
        self._lost = asyncio.Event()
        self._stop = False

    # ---------------------------------------------------------------- connection loop
    async def run(self) -> None:
        delay = 2
        while not self._stop:
            try:
                if self.port:
                    port = find_usb_port() if self.port == "auto" else self.port
                    if not port:
                        raise RuntimeError("NO USB RADIO FOUND")
                    self.notify("status", f"USB {port}")
                    self.mc = await MeshCore.create_serial(port, 115200, default_timeout=10, only_error=True)
                else:
                    self.notify("status", f"SCANNING FOR {self.device}")
                    self.mc = await MeshCore.create_ble(address=self.device, default_timeout=10, only_error=True)
            except Exception as e:  # bleak and serial raise a variety of errors
                log.warning("connect failed: %s", e)
                self.notify("status", str(e).upper()[:60])
                self.mc = None
            if self.mc is None:
                self.notify("status", f"NO CONTACT · RETRY IN {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            delay = 2
            self._lost.clear()
            try:
                await self._session()
            except Exception as e:
                log.exception("session error")
                self.notify("status", f"FAULT: {e}")
            self.state.connected = False
            self.notify("state", self.state)
            if self.mc:
                try:
                    await self.mc.disconnect()
                except Exception:
                    pass
            self.mc = None
            if not self._stop:
                await asyncio.sleep(2)

    async def stop(self) -> None:
        self._stop = True
        self._lost.set()
        if self.mc:
            try:
                await self.mc.disconnect()
            except Exception:
                pass

    async def _session(self) -> None:
        mc = self.mc
        assert mc is not None
        info = mc.self_info
        dev = await mc.commands.send_device_query()
        dev_info = dev.payload if dev.type == EventType.DEVICE_INFO else {}
        s = self.state
        s.connected = True
        s.link = "USB" if self.port else "BLE"
        s.name = info.get("name", "?")
        s.freq = info.get("radio_freq", 0.0)
        s.bw = info.get("radio_bw", 0.0)
        s.sf = info.get("radio_sf", 0)
        s.tx_power = info.get("tx_power", 0)
        s.firmware = dev_info.get("ver", "") or dev_info.get("fw_build", "")
        s.channels = await self._load_channels(dev_info.get("max_channels", 8))
        for ch in s.channels:
            self.store.set_title(ch.conv, ch.name)
        await self._load_contacts()

        subs = [
            mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_msg),
            mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_contact_msg),
            mc.subscribe(EventType.ACK, self._on_ack),
            mc.subscribe(EventType.ADVERTISEMENT, self._on_contacts_changed),
            mc.subscribe(EventType.NEW_CONTACT, self._on_contacts_changed),
            mc.subscribe(EventType.PATH_UPDATE, self._on_contacts_changed),
            mc.subscribe(EventType.DISCONNECTED, self._on_disconnected),
        ]
        self.notify("state", s)
        self.notify("status", "LINK ESTABLISHED")

        await self._drain()
        await mc.start_auto_message_fetching()

        try:
            while not self._lost.is_set():
                await self._poll_battery()
                try:
                    await asyncio.wait_for(self._lost.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
        finally:
            for sub in subs:
                mc.unsubscribe(sub)
            try:
                await mc.stop_auto_message_fetching()
            except Exception:
                pass
        self.notify("status", "LINK LOST")

    async def _drain(self) -> None:
        """Pull every message the radio queued while nobody was connected."""
        assert self.mc
        for _ in range(500):
            ev = await self.mc.commands.get_msg()
            if ev.type in (EventType.NO_MORE_MSGS, EventType.ERROR):
                break

    async def _load_channels(self, max_channels: int) -> list[Channel]:
        assert self.mc
        out: list[Channel] = []
        for idx in range(max_channels or 8):
            ev = await self.mc.commands.get_channel(idx)
            if ev.type != EventType.CHANNEL_INFO:
                break
            name = ev.payload.get("channel_name", "").strip()
            if name:
                out.append(Channel(idx, name))
        return out

    async def _load_contacts(self) -> None:
        assert self.mc
        await self.mc.commands.get_contacts()
        contacts = []
        for c in self.mc.contacts.values():
            contacts.append(Contact(
                c["public_key"], c.get("adv_name") or c["public_key"][:12], c.get("type", 0),
                c.get("last_advert", 0), c.get("out_path_len", -1),
            ))
            self.store.set_title(f"dm:{c['public_key'][:12]}", c.get("adv_name") or c["public_key"][:12])
        contacts.sort(key=lambda c: c.name.lower())
        self.state.contacts = contacts

    async def _poll_battery(self) -> None:
        assert self.mc
        ev = await self.mc.commands.get_bat()
        if ev.type == EventType.BATTERY:
            self.state.battery_mv = ev.payload.get("level", 0)
            self.notify("state", self.state)

    # ---------------------------------------------------------------- incoming
    async def _on_channel_msg(self, ev) -> None:
        p = ev.payload
        idx = p.get("channel_idx", 0)
        sender, body = split_channel_text(p.get("text", ""))
        conv = f"chan:{idx}"
        mid = self.store.add(
            conv, "in", sender, body, p.get("sender_timestamp"),
            snr=p.get("SNR"), rssi=p.get("RSSI"), path_len=p.get("path_len"),
        )
        if mid:
            self.notify("message", mid)

    async def _on_contact_msg(self, ev) -> None:
        p = ev.payload
        if p.get("txt_type", 0) == 1:  # CLI reply from a repeater, not chat
            return
        prefix = p.get("pubkey_prefix", "")
        contact = self.mc.get_contact_by_key_prefix(prefix) if self.mc else None
        name = (contact or {}).get("adv_name") or prefix
        conv = f"dm:{(contact or {}).get('public_key', prefix)[:12]}"
        self.store.set_title(conv, name)
        mid = self.store.add(
            conv, "in", name, p.get("text", ""), p.get("sender_timestamp"),
            snr=p.get("SNR"), path_len=p.get("path_len"),
        )
        if mid:
            self.notify("message", mid)

    async def _on_ack(self, ev) -> None:
        code = ev.payload.get("code")
        if code:
            mid = self.store.set_status_by_ack(code, "delivered")
            if mid:
                self.notify("updated", mid)

    async def _on_contacts_changed(self, ev) -> None:
        await self._load_contacts()
        self.notify("state", self.state)

    async def _on_disconnected(self, ev) -> None:
        self._lost.set()

    # ---------------------------------------------------------------- outgoing
    async def send(self, conv: str, text: str) -> int | None:
        if not self.mc or not self.state.connected:
            raise RuntimeError("NO LINK")
        ts = int(time.time())
        me = self.state.name
        if conv.startswith("chan:"):
            idx = int(conv.split(":")[1])
            mid = self.store.add(conv, "out", me, text, ts, status="sending")
            ev = await self.mc.commands.send_chan_msg(idx, text, ts)
            if mid:
                self.store.set_status(mid, "sent" if ev.type == EventType.OK else "failed")
            return mid
        contact = next((c for c in self.state.contacts if c.conv == conv), None)
        if contact is None:
            raise RuntimeError("CONTACT NOT ON RADIO")
        mid = self.store.add(conv, "out", me, text, ts, status="sending")
        ev = await self.mc.commands.send_msg(contact.public_key, text, ts)
        if ev.type != EventType.MSG_SENT:
            if mid:
                self.store.set_status(mid, "failed")
            return mid
        ack = ev.payload["expected_ack"]
        ack = ack.hex() if isinstance(ack, (bytes, bytearray)) else str(ack)
        if mid:
            self.store.set_status(mid, "sent", ack)
            timeout = max(ev.payload.get("suggested_timeout", 10000) / 1000 * 1.5, 10)
            asyncio.get_running_loop().call_later(timeout, self._check_unacked, mid)
        return mid

    def _check_unacked(self, mid: int) -> None:
        msg = self.store.get(mid)
        if msg and msg.status == "sent":
            self.store.set_status(mid, "no ack")
            self.notify("updated", mid)
