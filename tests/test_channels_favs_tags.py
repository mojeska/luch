import hashlib
from types import SimpleNamespace

import pytest
from meshcore import EventType

from luch.app import LuchApp
from luch.radio import Channel, Contact, Radio, RadioState, parse_secret
from luch.store import Store


class FakeCommands:
    def __init__(self, slots):
        self.slots = slots  # idx -> (name, secret)
        self.flag_calls = []

    async def set_channel(self, idx, name, secret=None):
        if name.startswith("#") or secret is None:
            secret = hashlib.sha256(name.encode()).digest()[:16]
        self.slots[idx] = (name, secret)
        return SimpleNamespace(type=EventType.OK, payload={})

    async def get_channel(self, idx):
        name, _ = self.slots.get(idx, ("", b""))
        return SimpleNamespace(type=EventType.CHANNEL_INFO, payload={"channel_name": name})

    async def change_contact_flags(self, contact, flags):
        self.flag_calls.append(flags)
        contact["flags"] = flags
        return SimpleNamespace(type=EventType.OK, payload={})

    async def get_contacts(self):
        return SimpleNamespace(type=EventType.CONTACTS, payload={})


def make_radio():
    events = []
    radio = Radio("fake", Store(":memory:"), lambda k, d: events.append(k))
    cmds = FakeCommands({0: ("Public", b"x"), 1: ("#test", b"y"), 3: ("#later", b"z")})
    contact = {"public_key": "ab" * 32, "adv_name": "Ivan", "type": 1, "flags": 0b110}
    radio.mc = SimpleNamespace(commands=cmds, contacts={contact["public_key"]: contact})
    radio.state = RadioState(connected=True, channels=[Channel(0, "Public"), Channel(1, "#test"), Channel(3, "#later")])
    return radio, cmds, contact


async def test_join_uses_first_free_slot_and_hash_key():
    radio, cmds, _ = make_radio()
    assert await radio.join_channel("#hiking") == 2
    assert cmds.slots[2][1] == hashlib.sha256(b"#hiking").digest()[:16]
    assert [c.name for c in radio.state.channels] == ["Public", "#test", "#hiking", "#later"]


async def test_join_private_needs_valid_key():
    radio, cmds, _ = make_radio()
    with pytest.raises(ValueError):
        await radio.join_channel("club")
    idx = await radio.join_channel("club", "00112233445566778899aabbccddeeff")
    assert cmds.slots[idx][1] == bytes.fromhex("00112233445566778899aabbccddeeff")
    with pytest.raises(ValueError):
        await radio.join_channel("#test")  # already there


async def test_leave_clears_slot_but_keeps_public():
    radio, cmds, _ = make_radio()
    await radio.leave_channel(1)
    assert cmds.slots[1] == ("", bytes(16))
    assert [c.idx for c in radio.state.channels] == [0, 3]
    with pytest.raises(ValueError):
        await radio.leave_channel(0)


async def test_favourite_keeps_permission_bits():
    radio, cmds, contact = make_radio()
    await radio.set_favourite(contact["public_key"], True)
    assert cmds.flag_calls[-1] == 0b111
    await radio.set_favourite(contact["public_key"], False)
    assert cmds.flag_calls[-1] == 0b110


def test_parse_secret():
    assert parse_secret("AAECAwQFBgcICQoLDA0ODw==") == bytes(range(16))
    with pytest.raises(ValueError):
        parse_secret("abcd")


def test_store_tags():
    s = Store(":memory:")
    s.add_tag("dm:ab", "Hiking")
    s.add_tag("dm:cd", "hiking")
    s.add_tag("dm:ab", "ham")
    assert s.tags_for("dm:ab") == ["ham", "hiking"]
    assert s.all_tags()["hiking"] == ["dm:ab", "dm:cd"]
    assert s.remove_tag("dm:ab", "ham") and s.tags_for("dm:ab") == ["hiking"]


async def test_screen_commands(tmp_path):
    app = LuchApp("x", tmp_path / "t.db", offline=True)
    favs = []

    async def fake_fav(key, on):
        favs.append((key, on))
        for c in app.state.contacts:
            if c.public_key == key:
                c.flags = 1 if on else 0

    app.radio.set_favourite = fake_fav
    async with app.run_test(size=(140, 44)) as pilot:
        app.on_radio("state", RadioState(connected=True, channels=[Channel(0, "Public")],
                                         contacts=[Contact("ab" * 32, "Ivan", 1, 0, 2), Contact("cd" * 32, "Olga", 1, 0, 1)]))
        assert not any("OLGA" in label for _, label in app.convs)
        await pilot.press(*"/tag olga hiking", "enter")
        await pilot.pause()
        assert app.store.tags_for("dm:" + "cd" * 6) == ["hiking"]
        assert any("OLGA" in label for _, label in app.convs)  # tagged contacts are listed
        await pilot.press(*"/fav ivan", "enter")
        await pilot.pause()
        assert favs == [("ab" * 32, True)]
        assert ("dm:" + "ab" * 6, "★ IVAN") in app.convs


async def test_set_name_announces():
    radio, cmds, _ = make_radio()
    calls = []

    async def set_name(n):
        calls.append(("name", n))
        return SimpleNamespace(type=EventType.OK, payload={})

    async def send_advert(flood=False):
        calls.append(("advert", flood))
        return SimpleNamespace(type=EventType.OK, payload={})

    cmds.set_name, cmds.send_advert = set_name, send_advert
    await radio.set_name("Mojeska-T")
    assert calls == [("name", "Mojeska-T"), ("advert", True)] and radio.state.name == "Mojeska-T"
    with pytest.raises(ValueError):
        await radio.set_name("x" * 40)
