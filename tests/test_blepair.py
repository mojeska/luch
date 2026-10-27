import argparse

import pytest

from luch import blepair, service
from luch.radio import Radio


def test_valid_pin():
    assert blepair.valid_pin("123456") == "123456"
    for bad in ("12345", "1234567", "12a456", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            blepair.valid_pin(bad)


def test_agent_answers_with_pin():
    pytest.importorskip("dbus_fast")
    from dbus_fast import DBusError
    from dbus_fast.service import ServiceInterface

    agent = blepair.make_agent("012345")
    methods = {m.name: m for m in ServiceInterface._get_methods(agent)}
    assert methods["RequestPasskey"].in_signature == "o"
    assert methods["RequestPasskey"].out_signature == "u"
    assert methods["RequestPasskey"].fn(agent, "/org/bluez/hci0/dev_X") == 12345
    assert methods["RequestPinCode"].fn(agent, "/org/bluez/hci0/dev_X") == "012345"
    methods["RequestConfirmation"].fn(agent, "/org/bluez/hci0/dev_X", 12345)  # match: accepted
    with pytest.raises(DBusError):
        methods["RequestConfirmation"].fn(agent, "/org/bluez/hci0/dev_X", 999999)
    assert agent.asked == 4


async def test_pin_agent_is_a_no_op_without_pin():
    async with blepair.pin_agent(None) as agent:
        assert agent is None


async def test_pin_agent_is_a_no_op_off_linux(monkeypatch):
    monkeypatch.setattr(blepair, "LINUX", False)
    async with blepair.pin_agent("123456") as agent:
        assert agent is None


def test_service_passes_pin_only_for_bluetooth(tmp_path):
    db = tmp_path / "r.db"
    assert service._args("MeshCore-X", db, None, None, "123456")[-4:] == ["-d", "MeshCore-X", "--pin", "123456"]
    assert "--pin" not in service._args("MeshCore-X", db, None, "10.0.0.5", "123456")
    assert "--pin" not in service._args("MeshCore-X", db, "auto", None, "123456")
    assert "--pin" not in service._args("MeshCore-X", db, None, None, None)


def test_radio_keeps_pin(tmp_path):
    from luch.store import Store
    r = Radio("MeshCore-X", Store(tmp_path / "r.db"), lambda k, d: None, pin="123456")
    assert r.pin == "123456"
