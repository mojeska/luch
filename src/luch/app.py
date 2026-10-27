"""ЛУЧ: a MeshCore terminal in the style of a Soviet communications console."""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .radio import CONTACT_REPEATER, CONTACT_ROOM, CONTACT_SENSOR, Radio, RadioState
from .store import Message, Store, default_db_path

DEFAULT_DEVICE = "MeshCore"

STATUS_RU = {
    "sending": "ПЕРЕДАЁТСЯ",
    "sent": "ОТПРАВЛЕНО",
    "delivered": "ДОСТАВЛЕНО",
    "no ack": "НЕТ ПОДТВ.",
    "failed": "СБОЙ",
}

HELP = [
    "КОМАНДЫ // COMMANDS",
    "  text + Enter        transmit on the selected channel or contact",
    "  /dm <name>          open a direct line to a contact (part of the name is enough)",
    "  /search <words>     search every logged message",
    "  /help               this list",
    "  Tab / Shift+Tab     move between panels",
    "  Ctrl+N / Ctrl+P     next / previous conversation",
    "  Ctrl+Q              quit",
]


def hhmmss(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class Header(Static):
    def on_mount(self) -> None:
        self.set_interval(1, self.refresh_clock)
        self.refresh_clock()

    def refresh_clock(self) -> None:
        now = datetime.now()
        utc = datetime.now(UTC)
        t = Text()
        t.append(" ★ ЛУЧ-М ", style="bold #0b0d0a on #c8261e")
        t.append("  АППАРАТУРА ЗАСЕКРЕЧЕННОЙ СВЯЗИ", style="bold #ffb000")
        t.append("  //  MESH RADIO TERMINAL", style="#8a7a4a")
        t.append(f"    МЕСТН {now:%H:%M:%S}   UTC {utc:%H:%M:%S}  ", style="#ffb000")
        self.update(t)


class LuchApp(App):
    CSS_PATH = "luch.tcss"
    TITLE = "ЛУЧ"
    BINDINGS = [
        Binding("ctrl+q", "quit", "ВЫХОД"),
        Binding("ctrl+n", "cycle(1)", "СЛЕД", show=False),
        Binding("ctrl+p", "cycle(-1)", "ПРЕД", show=False),
    ]

    def __init__(self, device: str, db_path: Path, offline: bool = False):
        super().__init__()
        self.store = Store(db_path)
        self.radio = Radio(device, self.store, self.on_radio)
        self.offline = offline
        self.state = RadioState()
        self.current: str | None = None
        self.convs: list[tuple[str, str]] = []  # (conv, label) in list order
        self.searching = False
        self.pinned: set[str] = set()

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header(id="header")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield OptionList(id="convs")
            with Vertical(id="center"):
                yield RichLog(id="log", wrap=True, markup=False, highlight=False)
                yield Input(placeholder="ПЕРЕДАЧА ▸  type and press Enter  ·  /help", id="tx")
            with Vertical(id="right"):
                yield Static(id="status")
                yield Static(id="events")
        yield Static(id="footer")

    def on_mount(self) -> None:
        self.query_one("#convs").border_title = "КАНАЛЫ И АБОНЕНТЫ"
        self.query_one("#convs").border_subtitle = "CHANNELS · CONTACTS"
        self.query_one("#log").border_title = "ЖУРНАЛ СВЯЗИ"
        self.query_one("#status").border_title = "СОСТОЯНИЕ"
        self.query_one("#status").border_subtitle = "STATUS"
        self.query_one("#events").border_title = "СОБЫТИЯ"
        self.query_one("#events").border_subtitle = "EVENTS"
        self._events: list[str] = []
        self.query_one("#footer", Static).update(Text.assemble(
            (" ^Q ", "bold #0b0d0a on #ffb000"), (" ВЫХОД  ", "#ffb000"),
            (" ^N/^P ", "bold #0b0d0a on #ffb000"), (" КАНАЛ  ", "#ffb000"),
            (" TAB ", "bold #0b0d0a on #ffb000"), (" ПАНЕЛЬ  ", "#ffb000"),
            (" /help ", "bold #0b0d0a on #ffb000"), (" СПРАВКА ", "#ffb000"),
        ))
        self.rebuild_convs()
        self.render_status()
        self.event("ПИТАНИЕ ВКЛ · POWER ON")
        if not self.offline:
            self.run_worker(self.radio.run(), exclusive=True, name="radio")
        self.query_one("#tx", Input).focus()

    # ---------------------------------------------------------------- radio events
    def on_radio(self, kind: str, data: Any) -> None:
        if kind == "status":
            self.event(str(data))
        elif kind == "state":
            self.state = data
            self.rebuild_convs()
            self.render_status()
        elif kind == "message":
            msg = self.store.get(data)
            if not msg:
                return
            if msg.conv == self.current and not self.searching:
                self.write_msg(msg)
                self.store.mark_read(msg.conv)
            else:
                if msg.conv.startswith("dm:"):
                    self.bell()
                self.event(f"ПРИЁМ ◄ {self.label_of(msg.conv)} · {msg.sender}")
            self.rebuild_convs()
            self.render_status()
        elif kind == "updated":
            msg = self.store.get(data)
            if msg and msg.conv == self.current and not self.searching:
                self.show_conv(self.current)

    def event(self, line: str) -> None:
        self._events.append(f"{datetime.now():%H:%M:%S} {line}")
        self._events = self._events[-14:]
        self.query_one("#events", Static).update(Text("\n".join(self._events), style="#c9a64a"))

    # ---------------------------------------------------------------- conversation list
    def label_of(self, conv: str) -> str:
        return next((label for c, label in self.convs if c == conv), conv)

    def rebuild_convs(self) -> None:
        convs: list[tuple[str, str, str]] = []  # conv, label, group
        for ch in self.state.channels:
            convs.append((ch.conv, f"К-{ch.idx:02d} {ch.name.upper()}", "chan"))
        # Hundreds of contacts get auto-added from adverts; list only rooms, contacts with
        # logged traffic, and ones opened with /dm.
        talked = self.store.dm_convs_with_traffic()
        seen = set()
        for c in self.state.contacts:
            if c.type in (CONTACT_REPEATER, CONTACT_SENSOR):
                continue
            if c.type != CONTACT_ROOM and c.conv not in talked and c.conv not in self.pinned:
                continue
            tag = "КОМ" if c.type == CONTACT_ROOM else "АБН"
            convs.append((c.conv, f"{tag} {c.name.upper()}", "dm"))
            seen.add(c.conv)
        for conv, title in self.store.known_dms():
            if conv not in seen and conv in talked:
                convs.append((conv, f"АРХ {title.upper()}", "dm"))
        if not self.state.channels:  # offline or not connected yet: show logged channels
            rows = self.store.db.execute(
                "SELECT conv, title FROM conversations WHERE conv LIKE 'chan:%' ORDER BY conv").fetchall()
            for r in rows:
                idx = int(r["conv"].split(":")[1])
                convs.insert(0, (r["conv"], f"К-{idx:02d} {r['title'].upper()}", "chan"))
        self.convs = [(c, label) for c, label, _ in convs]

        ol = self.query_one("#convs", OptionList)
        highlighted = self.current
        ol.clear_options()
        last_group = None
        for conv, label, group in convs:
            if group != last_group:
                head = "── КАНАЛЫ ──────────" if group == "chan" else "── АБОНЕНТЫ ────────"
                ol.add_option(Option(Text(head, style="#c8261e"), disabled=True))
                last_group = group
            n = self.store.unread(conv)
            t = Text(label[:20].ljust(20), style="#ffb000")
            if n:
                t.append(f" {n:>3}", style="bold #0b0d0a on #ffb000")
            ol.add_option(Option(t, id=conv))
        if highlighted is None and self.convs:
            highlighted = self.convs[0][0]
        if highlighted:
            try:
                ol.highlighted = ol.get_option_index(highlighted)
            except Exception:
                pass
            if self.current is None:
                self.show_conv(highlighted)

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option.id and ev.option.id != self.current:
            self.show_conv(ev.option.id)

    def action_cycle(self, step: int) -> None:
        if not self.convs:
            return
        ids = [c for c, _ in self.convs]
        i = ids.index(self.current) if self.current in ids else 0
        self.show_conv(ids[(i + step) % len(ids)])
        self.rebuild_convs()

    # ---------------------------------------------------------------- log pane
    def show_conv(self, conv: str) -> None:
        self.current = conv
        self.searching = False
        log = self.query_one("#log", RichLog)
        log.clear()
        log.border_title = f"ЖУРНАЛ СВЯЗИ · {self.label_of(conv)}"
        log.border_subtitle = "COMMS LOG"
        history = self.store.history(conv)
        self._log_empty = not history
        if not history:
            log.write(Text("── НЕТ ЗАПИСЕЙ · NO TRAFFIC LOGGED ──", style="#5f5532"))
        for m in history:
            self.write_msg(m)
        self.store.mark_read(conv)

    def write_msg(self, m: Message, show_conv: bool = False) -> None:
        log = self.query_one("#log", RichLog)
        if getattr(self, "_log_empty", False):
            log.clear()
            self._log_empty = False
        t = Text()
        t.append(hhmmss(m.rx_ts), style="#8a7a4a")
        if m.direction == "in":
            t.append("  ◄ ПРМ  ", style="bold #7fdc6f")
            t.append(f"{m.sender[:14]:<14} ", style="bold #7fdc6f")
            t.append(m.text, style="#e8e2c8")
            meta = []
            if m.snr is not None:
                meta.append(f"SNR {m.snr:+.1f}")
            if m.rssi is not None:
                meta.append(f"RSSI {m.rssi}")
            if m.path_len is not None:
                meta.append("FLOOD" if m.path_len < 0 else f"{m.path_len} HOP" + ("" if m.path_len == 1 else "S"))
            if meta:
                t.append(f"  [{' · '.join(meta)}]", style="#5f5532")
        else:
            t.append("  ► ПРД  ", style="bold #ffb000")
            t.append(f"{m.sender[:14]:<14} ", style="bold #ffb000")
            t.append(m.text, style="#e8e2c8")
            if m.status:
                colour = {"delivered": "#7fdc6f", "failed": "#ff4a3d", "no ack": "#ff4a3d"}.get(m.status, "#5f5532")
                t.append(f"  [{STATUS_RU.get(m.status, m.status.upper())}]", style=colour)
        if show_conv:
            t = Text.assemble((f"{self.label_of(m.conv)[:10]:<10} ", "#c8261e"), t)
        log.write(t)

    # ---------------------------------------------------------------- status pane
    def render_status(self) -> None:
        s = self.state
        t = Text()

        def row(k: str, v: str, style: str = "#ffb000") -> None:
            t.append(f"{k:<9}", style="#8a7a4a")
            t.append(f"{v}\n", style=style)

        if self.offline:
            row("СВЯЗЬ", "АВТОНОМНО", "bold #c9a64a")
        elif s.connected:
            row("СВЯЗЬ", "■ УСТАНОВЛЕНА", "bold #7fdc6f")
        else:
            row("СВЯЗЬ", "□ НЕТ", "bold #ff4a3d")
        row("УЗЕЛ", s.name or "—")
        if s.freq:
            row("ЧАСТОТА", f"{s.freq:.3f} МГц")
            row("ПОЛОСА", f"{s.bw:g} кГц  SF{s.sf}")
            row("МОЩН.", f"{s.tx_power} дБм")
        if s.battery_mv:
            pct = max(0, min(100, round((s.battery_mv - 3300) / 9)))
            bars = round(pct / 10)
            row("АККУМ.", f"{'▮' * bars}{'▯' * (10 - bars)} {s.battery_mv / 1000:.2f}В")
        reps = sum(1 for c in s.contacts if c.type == CONTACT_REPEATER)
        row("АБОНЕНТЫ", str(sum(1 for c in s.contacts if c.type not in (CONTACT_REPEATER, CONTACT_SENSOR))))
        row("РЕТРАНСЛ.", str(reps))
        row("ЗАПИСЕЙ", str(self.store.count()))
        self.query_one("#status", Static).update(t)

    # ---------------------------------------------------------------- input
    async def on_input_submitted(self, ev: Input.Submitted) -> None:
        text = ev.value.strip()
        ev.input.value = ""
        if not text:
            return
        if text in ("/help", "/?"):
            log = self.query_one("#log", RichLog)
            for line in HELP:
                log.write(Text(line, style="#c9a64a"))
            return
        if text.startswith("/dm "):
            self.open_dm(text[4:].strip())
            return
        if text.startswith("/search "):
            self.do_search(text[8:].strip())
            return
        if self.searching and self.current:
            self.show_conv(self.current)
        if not self.current:
            self.event("НЕТ АДРЕСАТА · NO TARGET")
            return
        try:
            mid = await self.radio.send(self.current, text)
        except Exception as e:
            self.event(f"СБОЙ ПЕРЕДАЧИ · {e}")
            return
        msg = self.store.get(mid) if mid else None
        if msg:
            self.write_msg(msg)
        self.render_status()

    def open_dm(self, name: str) -> None:
        people = [c for c in self.state.contacts if c.type not in (CONTACT_REPEATER, CONTACT_SENSOR)]
        exact = [c for c in people if c.name.lower() == name.lower()]
        hits = exact or [c for c in people if name.lower() in c.name.lower()]
        if len(hits) != 1:
            log = self.query_one("#log", RichLog)
            if not hits:
                log.write(Text(f"── АБОНЕНТ НЕ НАЙДЕН · no contact matches '{name}' ──", style="#ff4a3d"))
            else:
                log.write(Text(f"── {len(hits)} СОВПАДЕНИЙ · be more specific: ──", style="#c9a64a"))
                for c in hits[:15]:
                    log.write(Text(f"   {c.name}", style="#c9a64a"))
            return
        self.pinned.add(hits[0].conv)
        self.rebuild_convs()
        self.show_conv(hits[0].conv)
        self.rebuild_convs()

    def do_search(self, needle: str) -> None:
        self.searching = True
        log = self.query_one("#log", RichLog)
        log.clear()
        log.border_title = f"ПОИСК · {needle}"
        log.border_subtitle = "SEARCH · pick a conversation to leave"
        hits = self.store.search(needle)
        if not hits:
            log.write(Text("── НЕ НАЙДЕНО · NOT FOUND ──", style="#5f5532"))
        for m in hits:
            self.write_msg(m, show_conv=True)

    async def on_unmount(self) -> None:
        await self.radio.stop()
        self.store.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="luch", description="ЛУЧ: MeshCore terminal over Bluetooth")
    ap.add_argument("-d", "--device", default=DEFAULT_DEVICE,
                    help="BLE name (or part of it) or address of the radio, e.g. MeshCore-Mojeska")
    ap.add_argument("--db", type=Path, default=default_db_path(), help="SQLite log (default %(default)s)")
    ap.add_argument("--offline", action="store_true", help="browse the log without connecting")
    ap.add_argument("--debug", action="store_true", help="write a debug log to ~/.local/share/luch/luch.log")
    args = ap.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    if args.debug:
        logging.basicConfig(filename=str(args.db.parent / "luch.log"), level=logging.DEBUG)
    else:
        logging.getLogger().addHandler(logging.NullHandler())  # keep library warnings off the screen
    LuchApp(args.device, args.db, offline=args.offline).run()


if __name__ == "__main__":
    main()
