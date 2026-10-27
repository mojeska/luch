# ЛУЧ (LUCH)

Terminal client for a MeshCore Companion radio over Bluetooth, styled like a Soviet comms console. Every message sent or received is logged to SQLite.

## Install

Creates the venv and installs `luch` into it.

```sh
python3.14 -m venv .venv && .venv/bin/pip install -e .
```

## Run

Connects to the radio by BLE name (any part of it works).

```sh
.venv/bin/luch -d MeshCore-Mojeska
```

Browse the log without connecting.

```sh
.venv/bin/luch --offline
```

Debug log goes to `~/.local/share/luch/luch.log`.

```sh
.venv/bin/luch -d MeshCore-Mojeska --debug
```

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

- Log: `~/.local/share/luch/luch.db` (`--db` to change). Table `messages`, one row per message, duplicates ignored.
- The radio takes one Bluetooth connection at a time. Disconnect the phone app first.
- Messages that arrive while LUCH is closed wait in the radio's small queue and are pulled in on the next connect. Overflow is lost.
- The sidebar lists channels, room servers and contacts with logged traffic. Repeaters and sensors are left out.
- DMs show ДОСТАВЛЕНО (delivered) when the radio gets an ACK, НЕТ ПОДТВ. (no ack) after the timeout.

## Tests

Runs the store tests and an offline UI test.

```sh
.venv/bin/python -m pytest -q
```
