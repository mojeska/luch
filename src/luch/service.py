"""Install the background logger as a user service: launchd on macOS, systemd --user on Linux.
Either way it starts at login and restarts if it dies."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "io.github.mojeska.luch"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
UNIT = Path.home() / ".config" / "systemd" / "user" / "luch.service"
LINUX = sys.platform.startswith("linux")


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _args(device: str, db_path: Path, port: str | None) -> list[str]:
    args = [sys.executable, "-m", "luch", "logger", "--db", str(db_path)]
    return args + (["--port", port] if port else ["-d", device])


def _install_linux(device: str, db_path: Path, port: str | None) -> None:
    import shlex
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(f"""[Unit]
Description=LUCH MeshCore logger
After=bluetooth.target

[Service]
ExecStart={shlex.join(_args(device, db_path, port))}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
""")
    _systemctl("daemon-reload")
    res = _systemctl("enable", "--now", "luch.service")
    if res.returncode:
        raise SystemExit(f"systemctl failed: {res.stderr.strip()}")
    print(f"installed and started luch.service\n  unit {UNIT}\n  log  journalctl --user -u luch -f")
    print("  to keep it running while logged out: loginctl enable-linger $USER")


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def install(device: str, db_path: Path, port: str | None) -> None:
    if LINUX:
        return _install_linux(device, db_path, port)
    if sys.platform != "darwin":
        raise SystemExit("service install supports macOS and Linux; run `luch logger` yourself elsewhere")
    args = _args(device, db_path, port)
    log_path = db_path.parent / "logger.log"
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": args,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
            "ProcessType": "Background",
        }, f)
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    res = _launchctl("bootstrap", _domain(), str(PLIST))
    if res.returncode:
        raise SystemExit(f"launchctl bootstrap failed: {res.stderr.strip()}")
    print(f"installed and started {LABEL}\n  plist {PLIST}\n  log   {log_path}")


def uninstall() -> None:
    if LINUX:
        _systemctl("disable", "--now", "luch.service")
        UNIT.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        print("removed luch.service")
        return
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    PLIST.unlink(missing_ok=True)
    print(f"removed {LABEL}")


def start() -> None:
    if LINUX:
        res = _systemctl("start", "luch.service")
        print("started" if res.returncode == 0 else res.stderr.strip())
        return
    if not PLIST.exists():
        raise SystemExit("not installed; run `luch service install` first")
    res = _launchctl("bootstrap", _domain(), str(PLIST))
    print("started" if res.returncode == 0 else res.stderr.strip() or "already running")


def stop() -> None:
    if LINUX:
        res = _systemctl("stop", "luch.service")
        print("stopped (the radio is free for the phone app)" if res.returncode == 0 else res.stderr.strip())
        return
    res = _launchctl("bootout", f"{_domain()}/{LABEL}")
    print("stopped (the radio is free for the phone app)" if res.returncode == 0 else "not running")


def status() -> None:
    if LINUX:
        res = _systemctl("is-active", "luch.service")
        print(res.stdout.strip() or "not installed")
        return
    res = _launchctl("print", f"{_domain()}/{LABEL}")
    if res.returncode:
        print("not running" + ("" if PLIST.exists() else " (not installed)"))
        return
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith(("state =", "pid =", "last exit code =")):
            print(line)
