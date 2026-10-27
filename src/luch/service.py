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


def _args(device: str, db_path: Path, port: str | None, tcp: str | None = None,
          pin: str | None = None) -> list[str]:
    args = [sys.executable, "-m", "luch", "logger", "--db", str(db_path)]
    if tcp:
        return args + ["--tcp", tcp]
    if port:
        return args + ["--port", port]
    return args + ["-d", device] + (["--pin", pin] if pin else [])


def _install_linux(device: str, db_path: Path, port: str | None, tcp: str | None,
                   pin: str | None = None) -> None:
    import shlex
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(f"""[Unit]
Description=LUCH MeshCore logger
After=bluetooth.target

[Service]
ExecStart={shlex.join(_args(device, db_path, port, tcp, pin))}
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


def _bootout_and_wait() -> None:
    """bootout returns before launchd has finished unloading; bootstrapping too soon fails with error 5."""
    import time
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    for _ in range(50):
        if _launchctl("print", f"{_domain()}/{LABEL}").returncode:
            return
        time.sleep(0.2)


def _bootstrap() -> subprocess.CompletedProcess:
    import time
    res = _launchctl("bootstrap", _domain(), str(PLIST))
    if res.returncode:  # one retry for a slow unload
        time.sleep(2)
        res = _launchctl("bootstrap", _domain(), str(PLIST))
    return res


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def install(device: str, db_path: Path, port: str | None, tcp: str | None = None,
            pin: str | None = None) -> None:
    if LINUX:
        return _install_linux(device, db_path, port, tcp, pin)
    if sys.platform != "darwin":
        raise SystemExit("service install supports macOS and Linux; run `luch logger` yourself elsewhere")
    if os.geteuid() == 0:
        raise SystemExit("run this without sudo: the logger is a per-user agent in your login session")
    args = _args(device, db_path, port, tcp)
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
    _bootout_and_wait()
    res = _bootstrap()
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
    if _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0:
        print("already running")
        return
    res = _bootstrap()
    print("started" if res.returncode == 0 else res.stderr.strip())


def stop() -> None:
    if LINUX:
        res = _systemctl("stop", "luch.service")
        print("stopped (the radio is free for the phone app)" if res.returncode == 0 else res.stderr.strip())
        return
    running = _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0
    _bootout_and_wait()
    print("stopped (the radio is free for the phone app)" if running else "not running")


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
