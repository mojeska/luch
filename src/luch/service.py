"""Install the background logger as a macOS launchd agent (starts at login, restarts if it dies)."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "io.github.mojeska.luch"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def install(device: str, db_path: Path, port: str | None) -> None:
    if sys.platform != "darwin":
        raise SystemExit("service install is macOS only; run `luch logger` under systemd instead")
    args = [sys.executable, "-m", "luch", "logger", "--db", str(db_path)]
    args += ["--port", port] if port else ["-d", device]
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
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    PLIST.unlink(missing_ok=True)
    print(f"removed {LABEL}")


def start() -> None:
    if not PLIST.exists():
        raise SystemExit("not installed; run `luch service install` first")
    res = _launchctl("bootstrap", _domain(), str(PLIST))
    print("started" if res.returncode == 0 else res.stderr.strip() or "already running")


def stop() -> None:
    res = _launchctl("bootout", f"{_domain()}/{LABEL}")
    print("stopped (the radio is free for the phone app)" if res.returncode == 0 else "not running")


def status() -> None:
    res = _launchctl("print", f"{_domain()}/{LABEL}")
    if res.returncode:
        print("not running" + ("" if PLIST.exists() else " (not installed)"))
        return
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith(("state =", "pid =", "last exit code =")):
            print(line)
