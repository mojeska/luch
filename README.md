# ЛУЧ (LUCH)

Terminal client for a [MeshCore](https://github.com/meshcore-dev/MeshCore) Companion radio, styled like a Soviet comms console. Connects over Bluetooth or USB, and logs every message to SQLite. An optional background logger keeps the radio connected and logging while the screen is closed.

![LUCH screenshot](docs/screenshot.png)

Built on [meshcore_py](https://github.com/meshcore-dev/meshcore_py) and [Textual](https://github.com/Textualize/textual). Tested on macOS with a Heltec V3.

## Install

Python 3.11 or later.

```sh
git clone https://github.com/mojeska/luch.git && cd luch
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Run

Bluetooth, by BLE name (any part of it works).

```sh
.venv/bin/luch -d MeshCore-MyNode
```

USB, finding the port automatically (or give it, e.g. `--port /dev/cu.usbserial-0001`).

```sh
.venv/bin/luch --port auto
```

Browse the log without connecting.

```sh
.venv/bin/luch --offline
```

## Background logger

Runs as a launchd agent: starts at login, restarts if it stops, and owns the radio. `luch` then connects to the logger instead of the radio, so the log has no gaps. Status shows РЕЖИМ ЧЕРЕЗ ЛОГГЕР (via logger).

```sh
.venv/bin/luch service install -d MeshCore-MyNode   # or: --port auto
.venv/bin/luch service status
.venv/bin/luch service stop        # frees the radio for the phone app
.venv/bin/luch service start
.venv/bin/luch service uninstall
```

Logger output goes to `~/.local/share/luch/logger.log`. On Linux, run `luch logger -d ...` under systemd instead.

## Keys and commands

| | |
| --- | --- |
| Enter | Transmit on the selected channel or contact |
| `/dm <name>` | Open a direct line to a contact |
| `/search <words>` | Search every logged message |
| `/help` | Command list |
| Tab / Shift+Tab | Move between panels |
| Ctrl+N / Ctrl+P | Next / previous conversation |
| Ctrl+Q | Quit |

## Notes

- **One link at a time.** The radio takes one Bluetooth connection. Stop the logger (`luch service stop`) or close `luch` before using the phone app.
- **USB needs USB firmware.** MeshCore Companion builds have one interface: a BLE build won't answer over USB. Flash the USB Companion build to use `--port`.
- **Log:** `~/.local/share/luch/luch.db` (`--db` to change). Table `messages`, one row per message, duplicates ignored.
- **Queue:** with nothing connected, the radio keeps a small queue of messages, pulled in on the next connect. Overflow is lost.
- **Sidebar:** channels, room servers, and contacts with logged traffic. Repeaters and sensors are left out. `/dm` reaches anyone else.
- **Delivery:** DMs show ДОСТАВЛЕНО (delivered) on ACK, НЕТ ПОДТВ. (no ack) after the timeout.
- `--direct` talks to the radio even when the logger runs. `--debug` writes `luch.log` next to the database.

## Tests

```sh
.venv/bin/pip install pytest pytest-asyncio && .venv/bin/python -m pytest -q
```

## License

MIT. See [LICENSE](LICENSE).
