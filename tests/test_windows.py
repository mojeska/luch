"""Windows code paths, exercised on any OS by pretending to be Windows."""
import sys
from types import SimpleNamespace

from luch import daemon, radio, sound, store


def test_data_folder_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert store.default_db_path() == tmp_path / "luch" / "luch.db"


def test_usb_auto_finds_the_radio_com_port(monkeypatch):
    from serial.tools import list_ports
    monkeypatch.setattr(sys, "platform", "win32")
    fake = [SimpleNamespace(device="COM1", vid=None),            # built-in serial port
            SimpleNamespace(device="COM7", vid=0x05AC),          # some other USB gadget
            SimpleNamespace(device="COM4", vid=0x10C4)]          # CP2102 on a Heltec V3
    monkeypatch.setattr(list_ports, "comports", lambda: fake)
    assert radio.find_usb_port() == "COM4"
    monkeypatch.setattr(list_ports, "comports", lambda: fake[:2])
    assert radio.find_usb_port() == "COM7"                       # any USB serial beats none
    monkeypatch.setattr(list_ports, "comports", lambda: fake[:1])
    assert radio.find_usb_port() is None


async def test_no_logger_check_without_unix_sockets(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "UNIX_SOCKETS", False)
    assert await daemon.socket_alive(tmp_path / "x.sock") is False


def test_windows_uses_winsound(monkeypatch):
    monkeypatch.delenv("LUCH_SOUND", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert sound._player() == ["winsound"]


def test_effects_wait_for_the_anthem_on_windows(tmp_path):
    s = sound.Sound(tmp_path)
    s.player = ["winsound"]
    s._anthem = SimpleNamespace(poll=lambda: None)  # anthem still playing
    s.effect("rx")
    assert not (tmp_path / "sounds").exists()       # a chirp would have cut it off


def test_task_scheduler_task():
    import xml.etree.ElementTree as ET
    from luch import service
    xml = service.task_xml(r"AzureAD\Someone", r"C:\luch\.venv\Scripts\pythonw.exe",
                           ["-m", "luch", "logger", "--db", r"C:\Users\Some One\AppData\Local\luch\mynode.db",
                            "--tcp", "192.168.1.50"])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(xml.encode("utf-16"))
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", ns).text == r"AzureAD\Someone"
    assert root.find("t:Settings/t:ExecutionTimeLimit", ns).text == "PT0S"  # never killed for running long
    assert root.find("t:Settings/t:StopIfGoingOnBatteries", ns).text == "false"
    exe = root.find("t:Actions/t:Exec", ns)
    assert exe.find("t:Command", ns).text.endswith("pythonw.exe")
    assert '"C:\\Users\\Some One\\AppData\\Local\\luch\\mynode.db"' in exe.find("t:Arguments", ns).text
