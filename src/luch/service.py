"""Install the background logger as a user service: launchd on macOS, systemd --user on Linux,
a Task Scheduler task on Windows. Each starts it at login and restarts it if it dies."""
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
WINDOWS = sys.platform == "win32"
TASK = "LUCH logger"


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


# ------------------------------------------------------------------ Windows: Task Scheduler
def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def _pythonw() -> str:
    """pythonw.exe runs without a console window, so nothing pops up at login."""
    w = Path(sys.executable).with_name("pythonw.exe")
    return str(w) if w.exists() else sys.executable


def task_xml(user: str, exe: str, args: list[str]) -> str:
    """At logon of `user`, run the logger with no time limit, on battery too, restarting every minute
    if it fails; one copy at a time."""
    from xml.sax.saxutils import escape
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>LUCH MeshCore logger</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType>
    <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(exe)}</Command>
    <Arguments>{escape(subprocess.list2cmdline(args))}</Arguments></Exec></Actions>
</Task>
"""


def _install_windows(device: str, db_path: Path, port: str | None, tcp: str | None) -> None:
    import tempfile
    user = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    if not user:
        raise SystemExit("could not tell who you are (whoami)")
    args = _args(device, db_path, port, tcp)[1:]  # drop python.exe; the task runs pythonw.exe
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "luch-task.xml"
        xml.write_text(task_xml(user, _pythonw(), args), encoding="utf-16")
        res = _schtasks("/Create", "/TN", TASK, "/XML", str(xml), "/F")
    if res.returncode:
        raise SystemExit(f"schtasks failed: {(res.stderr or res.stdout).strip()}")
    _schtasks("/End", "/TN", TASK)
    res = _schtasks("/Run", "/TN", TASK)
    if res.returncode:
        raise SystemExit(f"installed, but it didn't start: {(res.stderr or res.stdout).strip()}")
    print(f"installed and started the \"{TASK}\" task (runs at login for {user})\n"
          f"  log  {db_path.parent / 'logger.log'}")


def _windows_state() -> str | None:
    """'Running', 'Ready' (stopped), 'Disabled', or None if not installed."""
    res = _schtasks("/Query", "/TN", TASK, "/FO", "LIST")
    if res.returncode:
        return None
    for line in res.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return "?"


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
    if WINDOWS:
        return _install_windows(device, db_path, port, tcp)
    if sys.platform != "darwin":
        raise SystemExit("service install supports macOS, Linux and Windows; run `luch logger` yourself elsewhere")
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
    if WINDOWS:
        _schtasks("/End", "/TN", TASK)
        res = _schtasks("/Delete", "/TN", TASK, "/F")
        print(f"removed the \"{TASK}\" task" if res.returncode == 0 else "not installed")
        return
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
    if WINDOWS:
        state = _windows_state()
        if state is None:
            raise SystemExit("not installed; run `luch service install` first")
        if state == "Running":
            print("already running")
            return
        res = _schtasks("/Run", "/TN", TASK)
        print("started" if res.returncode == 0 else (res.stderr or res.stdout).strip())
        return
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
    if WINDOWS:
        running = _windows_state() == "Running"
        _schtasks("/End", "/TN", TASK)
        print("stopped (the radio is free for the phone app)" if running else "not running")
        return
    if LINUX:
        res = _systemctl("stop", "luch.service")
        print("stopped (the radio is free for the phone app)" if res.returncode == 0 else res.stderr.strip())
        return
    running = _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0
    _bootout_and_wait()
    print("stopped (the radio is free for the phone app)" if running else "not running")


def status() -> None:
    if WINDOWS:
        state = _windows_state()
        print("not installed" if state is None else "running" if state == "Running" else f"not running ({state})")
        return
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
