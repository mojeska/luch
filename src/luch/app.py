"""ЛУЧ: a MeshCore terminal in the style of a Soviet communications console."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.cells import cell_len, set_cell_size
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape as escape_markup
from textual.widgets import Button, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .daemon import default_socket_path, run_daemon, socket_alive
from .radio import CONTACT_REPEATER, CONTACT_ROOM, CONTACT_SENSOR, Radio, RadioState
from .remote import RemoteRadio, logger_host
from . import firmware, service
from .blepair import valid_pin
from .i18n import subtitle, tr
from .countdown import Countdown
from .mascot import Mascot
from .ticker import Ticker
from .sound import Sound
from .weather import WeatherCache
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
    "  /firmware           radio firmware vs the newest MeshCore Companion release",
    "  text + Enter        transmit on the selected channel or contact",
    "  /dm <name>          open a direct line to a contact (part of the name is enough)",
    "  Ctrl+R              reply to the last message here (on a channel: starts with @[sender])",
    "  click a sender      on a channel, start a reply to them with @[name]",
    "  /search <words>     search every logged message",
    "  /join #name         join a hashtag channel (key comes from the name)",
    "  /join <name> <key>  join a private channel (32 hex chars or base64)",
    "  /leave [#name]      leave a channel (default: the one on screen)",
    "  /fav [name]         favourite a contact, on the radio (default: the one on screen)",
    "  /unfav [name]       remove the favourite",
    "  /tag <name> <tag>   tag a contact (LUCH only; tag is one word)",
    "  /untag <name> <tag> remove a tag",
    "  /tags [tag]         list tags, or the contacts with one tag",
    "  /home [<lat> <lon>] show or set the home position for weather (/home clear)",
    "  /gps on|off [secs]  turn the radio's GPS on or off, optional update interval",
    "  /weather            weather details for the current position",
    "  /units metric|imperial",
    "  /cli <command>      run a radio CLI command, e.g. /cli get wifi.ip",
    "  /name <new name>    rename this node and announce it (Bluetooth name changes after /reboot)",
    "  /reboot             restart the radio (LUCH reconnects)",
    "  /reconnect          connect to the radio again now (e.g. after another client took it)",
    "  /help               this list",
    "  Tab / Shift+Tab     move between panels",
    "  Ctrl+N / Ctrl+P     next / previous conversation",
    "  Ctrl+T              Russian / English screen text",
    "  Ctrl+O              collapse / restore the channels and contacts list (more room for the log)",
    "  Ctrl+G              collapse / restore the status, Misha and events column",
    "  [−] [+] on a panel  click: fold or collapse it / maximize it; Esc or [−] restores",
    "  Ctrl+B              show / hide Misha",
    "  Ctrl+S  or /mute    sound effects on / off",
    "  Ctrl+K  or /ticker  slogan ticker on / off",
    "  Ctrl+L  or ПУСК     play the anthem; Esc, /stop or СТОП ends it",
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
        tt = self.app.t
        t = Text()
        t.append(f" ★ {tt('ЛУЧ-М')} ", style="bold #0b0d0a on #c8261e")
        t.append(f"  {tt('АППАРАТУРА ЗАСЕКРЕЧЕННОЙ СВЯЗИ')}", style="bold #ffb000")
        t.append("  //  MESH RADIO TERMINAL", style="#8a7a4a")
        t.append(f"    {tt('МЕСТН')} {now:%H:%M:%S}   UTC {utc:%H:%M:%S}  ", style="#ffb000")
        self.update(t)


# Panels with [−] / [+] in the top-right corner, and what "−" does to each:
# "strip" shrinks its whole column to a narrow bar, "fold" leaves just the title line, None has no "−".
FRAMES = {"convs": "strip", "log": None, "status": "fold", "mascot": "fold", "events": "fold"}
BORDER, BORDER_FOCUS = "#6b5a22", "#ffb000"


class Pane(Vertical):
    """The comms log plus the command line: maximized together, so you can still type."""
    ALLOW_MAXIMIZE = True


class SideStrip(Static):
    """A collapsed side column. Click anywhere on it to bring the column back."""

    def __init__(self, column: str, **kw):
        super().__init__(**kw)
        self.column = column

    def on_click(self) -> None:
        self.app.set_column(self.column, True)


class LuchApp(App):
    CSS_PATH = "luch.tcss"
    TITLE = "ЛУЧ"
    BINDINGS = [
        Binding("ctrl+q", "quit", "ВЫХОД"),
        Binding("ctrl+n", "cycle(1)", "СЛЕД", show=False),
        Binding("ctrl+p", "cycle(-1)", "ПРЕД", show=False),
        Binding("ctrl+t", "toggle_lang", "ПЕРЕВОД", show=False),
        Binding("ctrl+b", "toggle_mascot", "МИША", show=False),
        Binding("ctrl+s", "toggle_mute", "ЗВУК", show=False, priority=True),
        Binding("ctrl+k", "toggle_ticker", "ЛОЗУНГИ", show=False, priority=True),
        Binding("ctrl+l", "launch", "ПУСК", show=False, priority=True),
        Binding("ctrl+r", "reply", "ОТВЕТ", show=False, priority=True),
        Binding("ctrl+o", "toggle_convs", "СПИСОК", show=False, priority=True),
        Binding("ctrl+g", "toggle_gauges", "ПРИБОРЫ", show=False, priority=True),
        Binding("escape", "stop_anthem", "СТОП", show=False),
    ]

    def __init__(self, device: str, db_path: Path, offline: bool = False,
                 port: str | None = None, via_logger: bool = False, tcp: str | None = None,
                 pin: str | None = None, mirror_path: Path | None = None):
        """`mirror_path`: the logger runs on another machine; keep a copy of its log here."""
        super().__init__()
        self.store = Store(mirror_path or db_path)
        self.via_logger = via_logger
        if via_logger:
            self.radio = RemoteRadio(default_socket_path(db_path), self.on_radio,
                                     mirror=self.store if mirror_path else None)
        else:
            self.radio = Radio(device, self.store, self.on_radio, port=port, tcp=tcp, pin=pin)
            self.radio.yield_on_takeover = True  # don't fight a logger or phone for the Wi-Fi link
        self.offline = offline
        self.state = RadioState()
        self.current: str | None = None
        self.convs: list[tuple[str, str]] = []  # (conv, label) in list order
        self.searching = False
        self.pinned: set[str] = set()
        self.settings_file = db_path.parent / "settings.json"
        self.settings = self._load_settings()
        self.lang = self.settings.get("lang", "ru")
        self.fw_cache = db_path.parent / "firmware.json"
        self.fw_latest: firmware.Release | None = None
        self._fw_for: str | None = None
        self._events: list[tuple[str, str, str | None, str]] = []  # time, fixed text, conv, free text
        self.sound = Sound(db_path.parent, muted=self.settings.get("muted", False))
        self.countdown_seconds = 10
        self._start_count = self.store.count()
        self.farewell = ""
        self._count = 0
        self._count_timer = None
        self._was_connected: bool | None = None
        self._reply_targets: list[tuple[str, str]] = []  # (conv, sender) behind each clickable name
        self._titles: dict[str, str] = {}  # panel id -> its title, before the corner buttons go on
        self._folded: set[str] = set()
        self.weather_cache = WeatherCache()
        self.weather = None
        self._weather_err = ""

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_file.read_text())
        except (OSError, ValueError):
            return {}

    @property
    def imperial(self) -> bool:
        return self.settings.get("units", "imperial") == "imperial"

    def position(self) -> tuple[float, float, str] | None:
        """GPS fix if the radio has one, else the position stored on the radio, else home."""
        s = self.state
        if s.lat is not None and s.lon is not None:
            return s.lat, s.lon, s.pos_src
        home = self.settings.get("home")
        return (home[0], home[1], "ДОМ") if home else None

    async def refresh_weather(self) -> None:
        pos = self.position()
        if not pos:
            return
        try:
            self.weather = await self.weather_cache.get(pos[0], pos[1], self.imperial)
            self._weather_err = ""
        except Exception as e:
            self._weather_err = str(e)[:30]
        self.render_status()

    def t(self, text: str) -> str:
        return tr(text, self.lang)

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Header(id="header")
        with Horizontal(id="body"):
            yield SideStrip("left", id="left-strip", classes="strip")
            with Vertical(id="left"):
                yield OptionList(id="convs")
            with Pane(id="center"):
                yield RichLog(id="log", wrap=True, markup=False, highlight=False)
                yield Input(id="tx")
                yield Countdown(id="countdown")
            with Vertical(id="right"):
                yield Static(id="status")
                yield Mascot(id="mascot")
                yield Static(id="events")
            yield SideStrip("right", id="right-strip", classes="strip")
        yield Ticker(id="ticker")
        with Horizontal(id="footerbar"):
            yield Static(id="footer")
            yield Button("▶ ПУСК", id="launch")

    def apply_labels(self) -> None:
        t, sub = self.t, lambda s: subtitle(s, self.lang)
        for wid, title, subt in (("#convs", "КАНАЛЫ И АБОНЕНТЫ", "CHANNELS · CONTACTS"),
                                 ("#status", "СОСТОЯНИЕ", "STATUS"), ("#events", "СОБЫТИЯ", "EVENTS"),
                                 ("#mascot", "МИША", "MASCOT")):
            self.set_title(wid[1:], t(title))
            self.query_one(wid).border_subtitle = sub(subt)
        for strip, label in (("#left-strip", "СПИСОК"), ("#right-strip", "ПРИБОРЫ")):
            self.query_one(strip, Static).update(Text("+\n\n" + "\n".join(t(label)), style="bold #ffb000"))
        self.query_one("#tx", Input).placeholder = t("ПЕРЕДАЧА ▸") + "  type and press Enter  ·  /help"
        key, lab = "bold #0b0d0a on #ffb000", "#ffb000"
        self.query_one("#footer", Static).update(Text.assemble(
            (" ^Q ", key), (f" {t('ВЫХОД')}  ", lab), (" ^N/^P ", key), (f" {t('КАНАЛ')}  ", lab),
            (" ^O ", key), (f" {t('СПИСОК')}  ", lab), (" ^G ", key), (f" {t('ПРИБОРЫ')}  ", lab), (" TAB ", key), (f" {t('ПАНЕЛЬ')}  ", lab), (" ^T ", key), (f" {t('ПЕРЕВОД')}  ", lab),
            (" ^S ", key), (f" {t('ЗВУК')} {t('ВЫКЛ') if self.sound.muted else t('ВКЛ')}  ", lab),
            (" ^K ", key), (f" {t('ЛОЗУНГИ')}  ", lab),
            (" /help ", key), (f" {t('СПРАВКА')} ", lab),
        ))
        busy = self.sound.anthem_playing() or self._count > 0
        self.query_one("#launch", Button).label = t("■ СТОП" if busy else "▶ ПУСК")
        self.query_one(Header).refresh_clock()

    def on_mount(self) -> None:
        if self.settings.get("mascot") is False:
            self.query_one(Mascot).display = False
        if self.settings.get("ticker") is False:
            self.query_one(Ticker).display = False
        for wid in ("#status", "#mascot", "#events"):  # not focusable, so Textual wouldn't maximize them
            self.query_one(wid).ALLOW_MAXIMIZE = True
        for key in self.settings.get("folded", []):
            if FRAMES.get(key) == "fold":
                self._folded.add(key)
                self.query_one("#" + key).add_class("-folded")
        self.set_column("left", self.settings.get("convs") is not False, save=False)
        self.set_column("right", self.settings.get("gauges") is not False, save=False)
        self.watch(self.screen, "maximized", lambda _: self.call_after_refresh(self.render_titles), init=False)
        self.watch(self.screen, "focused", lambda _: self.render_titles(), init=False)
        self.apply_labels()
        self.rebuild_convs()
        self.render_status()
        self.update_mascot()
        self.event("ПИТАНИЕ ВКЛ · POWER ON")
        self.run_worker(self.sound.prepare, thread=True, group="sound")  # render the WAVs once
        self.set_interval(0.5, self._watch_anthem)
        self.set_interval(300, lambda: self.run_worker(self.refresh_weather, group="weather", exclusive=True))
        self.run_worker(self.refresh_weather, group="weather", exclusive=True)
        if not self.offline:
            self.run_worker(self.radio.run(), exclusive=True, name="radio")
        self.query_one("#tx", Input).focus()

    # ---------------------------------------------------------------- radio events
    def on_radio(self, kind: str, data: Any) -> None:
        if kind == "status":
            self.event(str(data))
        elif kind == "state":
            if self._was_connected is not None and data.connected != self._was_connected:
                self.sound.effect("link_up" if data.connected else "link_down")
            self._was_connected = data.connected
            self.state = data
            self.rebuild_convs()
            self.render_status()
            self.update_mascot()
            self.run_worker(self.refresh_weather, group="weather", exclusive=True)
            if data.firmware and data.firmware != self._fw_for:
                self._fw_for = data.firmware
                self.run_worker(self.check_firmware(), name="firmware")
        elif kind == "message":
            msg = self.store.get(data)
            if not msg:
                return
            if msg.direction == "in":
                self.sound.effect("dm" if msg.conv.startswith("dm:") else "rx")
            self.query_one(Mascot).react("wave", 6)
            if msg.conv == self.current and not self.searching:
                self.write_msg(msg)
                self.store.mark_read(msg.conv)
            elif msg.direction == "in":  # "out" here was sent from another screen on the same logger
                if msg.conv.startswith("dm:"):
                    self.bell()
                self.event("ПРИЁМ ◄ ", msg.conv, f" · {msg.sender}")
            self.rebuild_convs()
            self.render_status()
        elif kind == "synced":  # a remote logger's backlog has landed in the mirror
            self.rebuild_convs()
            self.render_status()
            if self.current and not self.searching:
                self.show_conv(self.current)
        elif kind == "updated":
            msg = self.store.get(data)
            if msg and msg.status == "delivered":
                self.sound.effect("delivered")
                self.query_one(Mascot).react("point", 4)
            if msg and msg.conv == self.current and not self.searching:
                self.show_conv(self.current)

    def event(self, fixed: str, conv: str | None = None, free: str = "") -> None:
        """`fixed` is screen text (translated); `conv` and `free` carry names and stay as they are."""
        self._events.append((f"{datetime.now():%H:%M:%S}", fixed, conv, free))
        self._events = self._events[-14:]
        self.render_events()

    def render_events(self) -> None:
        lines = [f"{ts} {self.t(fixed)}{self.label_of(conv) if conv else ''}{free}"
                 for ts, fixed, conv, free in self._events]
        self.query_one("#events", Static).update(Text("\n".join(lines), style="#c9a64a"))

    def update_mascot(self) -> None:
        s, m = self.state, self.query_one(Mascot)
        if self.offline:
            m.set_base("idle", "idle")
        elif not s.connected:
            m.set_base("distress", "distress")
        elif s.battery_mv and s.battery_mv < 3500:
            m.set_base("distress", "battery")
        else:
            m.set_base("idle", "idle")

    # ---------------------------------------------------------------- sound
    def action_toggle_mute(self) -> None:
        self.sound.muted = not self.sound.muted
        self.settings["muted"] = self.sound.muted
        self._save_settings()
        self.event("ЗВУК ", free="OFF" if self.sound.muted else "ON")
        self.apply_labels()

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id == "launch":
            busy = self.sound.anthem_playing() or self._count > 0
            self.action_stop_anthem() if busy else self.action_launch()

    def action_launch(self) -> None:
        if self._count > 0 or self.sound.anthem_playing():
            return
        if not self.sound.available:
            self.event("НЕТ ЗВУКА ", free="(no afplay / paplay / aplay / winsound)")
            return
        self.event("ПУСК ", free=f"T-{self.countdown_seconds}")
        self._count = self.countdown_seconds
        banner = self.query_one(Countdown)
        banner.display = True
        banner.place()
        self._draw_count()
        self.query_one(Mascot).react("point", 3600, say="standby")
        self._count_timer = self.set_interval(1, self._count_tick)
        self.apply_labels()

    def _draw_count(self) -> None:
        self.query_one(Countdown).show_count(self._count, self.t("ПУСК ЧЕРЕЗ"), self.t("ESC — ОТМЕНА"))
        self.sound.tick()

    def _count_tick(self) -> None:
        self._count -= 1
        if self._count > 0:
            self._draw_count()
            return
        self._end_countdown()
        self.run_worker(self._start_anthem, thread=True, group="sound", exclusive=True)

    def _end_countdown(self) -> None:
        if self._count_timer:
            self._count_timer.stop()
            self._count_timer = None
        self._count = 0
        self.query_one(Countdown).display = False

    def on_resize(self) -> None:
        if self._count > 0:
            self.query_one(Countdown).place()
        self.call_after_refresh(self.render_titles)

    def _start_anthem(self) -> None:  # worker thread: the first run renders the WAV
        if self.sound.play_anthem():
            self.call_from_thread(self._anthem_started)

    def _anthem_started(self) -> None:
        self.query_one(Mascot).react("point", 3600, say="anthem")
        self.apply_labels()

    def action_stop_anthem(self) -> None:
        if self.screen.maximized is not None:  # Esc first restores a maximized panel
            self.screen.minimize()
        elif self._count > 0:
            self._end_countdown()
            self.event("ОТМЕНА")
            self._anthem_done()
        elif self.sound.anthem_playing():
            self.sound.stop_anthem()
            self._anthem_done()

    def _anthem_done(self) -> None:
        m = self.query_one(Mascot)
        if m.reaction_say in ("anthem", "standby"):
            m.react("point", 0.25, say="idle")
        self.apply_labels()

    def _watch_anthem(self) -> None:
        playing = self.sound.anthem_playing()
        if getattr(self, "_anthem_was", False) and not playing:
            self._anthem_done()
        self._anthem_was = playing

    def action_toggle_ticker(self) -> None:
        tk = self.query_one(Ticker)
        tk.display = not tk.display
        self.settings["ticker"] = bool(tk.display)
        self._save_settings()
        if tk.display:
            tk.rebuild()

    # ---------------------------------------------------------------- panels: collapse, fold, maximize
    def action_toggle_convs(self) -> None:
        """Collapse the channels and contacts list to a strip so the log gets its width. Ctrl+N/P still switch."""
        self.set_column("left", not self.query_one("#left").display)

    def action_toggle_gauges(self) -> None:
        """Collapse the status / Misha / events column to a strip so the log gets its width."""
        self.set_column("right", not self.query_one("#right").display)

    def set_column(self, column: str, shown: bool, save: bool = True) -> None:
        panel = self.query_one("#" + column)
        if self.screen.maximized is not None and not shown:
            self.screen.minimize()
        panel.display = shown
        self.query_one(f"#{column}-strip").display = not shown
        if save:
            self.settings["convs" if column == "left" else "gauges"] = shown
            self._save_settings()
        if not shown and self.focused and panel in self.focused.ancestors_with_self:
            self.query_one("#tx", Input).focus()  # don't leave the cursor on a hidden panel
        self.call_after_refresh(self.render_titles)

    def action_frame(self, key: str, op: str) -> None:
        """A click on a panel's [−] or [+]."""
        if op == "max":
            target = self.query_one("#center") if key == "log" else self.query_one("#" + key)
            if self.screen.maximized is not None:
                self.screen.minimize()
            self.screen.maximize(target, container=False)
            if key == "log":
                self.query_one("#tx", Input).focus()
            elif key == "convs":
                self.query_one("#convs").focus()
        elif op == "restore":
            self.screen.minimize()
        elif op == "min" and FRAMES.get(key) == "strip":
            self.set_column("left", False)
        elif op in ("min", "unfold") and FRAMES.get(key) == "fold":
            folding = op == "min"
            (self._folded.add if folding else self._folded.discard)(key)
            self.query_one("#" + key).set_class(folding, "-folded")
            self.settings["folded"] = sorted(self._folded)
            self._save_settings()
        self.call_after_refresh(self.render_titles)

    def set_title(self, key: str, title: str) -> None:
        self._titles[key] = title
        self.render_titles(key)

    def render_titles(self, only: str | None = None) -> None:
        """Each panel's title with its buttons pushed to the top-right corner by border characters."""
        maxed = self.screen.maximized
        for key, minimize in FRAMES.items():
            if only and key != only:
                continue
            w = self.query_one("#" + key)
            target = self.query_one("#center") if key == "log" else w
            if maxed is not None and maxed is target:
                buttons = [("−", "restore")]
            elif key in self._folded:
                buttons = [("+", "unfold")]
            else:
                buttons = ([("−", "min")] if minimize else []) + [("+", "max")]
            title = self._titles.get(key, "")
            room = w.outer_size.width - 6  # Textual draws "╔═ " + title + " ═╗"
            fill = room - cell_len(title) - 1 - 3 * len(buttons)
            if fill < 0 and room > 3 * len(buttons) + 1:
                title = set_cell_size(title, room - 3 * len(buttons) - 2) + "…"
                fill = 0
            colour = BORDER_FOCUS if w.has_focus else BORDER
            btns = "".join(f"[@click=app.frame('{key}','{op}')]\\[{sym}][/]" for sym, op in buttons)
            w.border_title = (f"{escape_markup(title)} [{colour}]{'═' * max(fill, 0)}[/]{btns}" if room > 0
                              else escape_markup(title))

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        if self.screen.maximized is self.query_one("#convs"):  # picked one from the full-screen list
            self.screen.minimize()

    def action_toggle_mascot(self) -> None:
        m = self.query_one(Mascot)
        m.display = not m.display
        self.settings["mascot"] = bool(m.display)
        self._save_settings()

    def _save_settings(self) -> None:
        try:
            self.settings_file.write_text(json.dumps(self.settings))
        except OSError:
            pass

    def action_toggle_lang(self) -> None:
        self.lang = "en" if self.lang == "ru" else "ru"
        self.settings["lang"] = self.lang
        self._save_settings()
        self.apply_labels()
        self.query_one(Ticker).rebuild()
        self.rebuild_convs()
        self.render_status()
        self.render_events()
        if self.current and not self.searching:
            self.show_conv(self.current)

    async def check_firmware(self) -> None:
        try:
            self.fw_latest = await firmware.latest_release(self.fw_cache)
        except Exception as e:
            self.event("ПРОВЕРКА ПРОШИВКИ: ", free=f"{e}"[:40])
            return
        have = firmware.parse_version(self.state.firmware)
        if self.fw_latest and have and self.fw_latest.version > have:
            self.event("ДОСТУПНА ПРОШИВКА ", free=f"{firmware.fmt(self.fw_latest.version)} · /firmware")
        self.render_status()

    def show_firmware(self) -> None:
        log = self.query_one("#log", RichLog)
        have = firmware.parse_version(self.state.firmware)
        lines = [f"{self.t('ПРОШИВКА')}: {self.state.firmware or '?'}"]
        if self.fw_latest:
            lines.append(f"NEWEST COMPANION RELEASE: {firmware.fmt(self.fw_latest.version)}  {self.fw_latest.url}")
            if have and self.fw_latest.version > have:
                lines.append(f"UPDATE WITH THE WEB FLASHER: {firmware.FLASHER}")
                lines.append("  pick your board and the Companion build for your link (BLE or USB); settings are kept")
            elif have:
                lines.append(self.t("ПРОШИВКА АКТУАЛЬНА"))
        else:
            lines.append("NEWEST RELEASE NOT KNOWN YET (needs internet and a connected radio)")
        for line in lines:
            log.write(Text(line, style="#c9a64a"))

    # ---------------------------------------------------------------- conversation list
    def label_of(self, conv: str) -> str:
        label = next((label for c, label in self.convs if c == conv), None)
        if label:
            return label
        row = self.store.db.execute("SELECT title FROM conversations WHERE conv = ?", (conv,)).fetchone()
        return row["title"].upper() if row else conv

    def rebuild_convs(self) -> None:
        convs: list[tuple[str, str, str]] = []  # conv, label, group
        for ch in self.state.channels:
            convs.append((ch.conv, f"{self.t('К-')}{ch.idx:02d} {ch.name.upper()}", "chan"))
        # Hundreds of contacts get auto-added from adverts; list only rooms, contacts with
        # logged traffic, and ones opened with /dm.
        talked = self.store.dm_convs_with_traffic()
        tagged = self.store.tagged_convs()
        seen = set()
        for c in self.state.contacts:  # favourites first, whether or not there's traffic
            if c.favourite and c.type not in (CONTACT_REPEATER, CONTACT_SENSOR):
                convs.append((c.conv, f"★ {c.name.upper()}", "fav"))
                seen.add(c.conv)
        for c in self.state.contacts:
            if c.conv in seen or c.type in (CONTACT_REPEATER, CONTACT_SENSOR):
                continue
            if c.type != CONTACT_ROOM and not ({c.conv} & (talked | tagged | self.pinned)):
                continue
            tag = self.t("КОМ" if c.type == CONTACT_ROOM else "АБН")
            convs.append((c.conv, f"{tag} {c.name.upper()}", "dm"))
            seen.add(c.conv)
        for conv, title in self.store.known_dms():
            if conv not in seen and conv in talked:
                convs.append((conv, f"{self.t('АРХ')} {title.upper()}", "dm"))
        if not self.state.channels:  # offline or not connected yet: show logged channels
            rows = self.store.db.execute(
                "SELECT conv, title FROM conversations WHERE conv LIKE 'chan:%' ORDER BY conv").fetchall()
            for r in rows:
                idx = int(r["conv"].split(":")[1])
                convs.insert(0, (r["conv"], f"{self.t('К-')}{idx:02d} {r['title'].upper()}", "chan"))
        self.convs = [(c, label) for c, label, _ in convs]

        ol = self.query_one("#convs", OptionList)
        highlighted = self.current
        ol.clear_options()
        last_group = None
        for conv, label, group in convs:
            if group != last_group:
                name = {"chan": "КАНАЛЫ", "fav": "ИЗБРАННЫЕ", "dm": "АБОНЕНТЫ"}[group]
                head = f"── {self.t(name)} ".ljust(20, "─")
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
        self.set_title("log", f"{self.t('ЖУРНАЛ СВЯЗИ')} · {self.label_of(conv)}")
        tags = self.store.tags_for(conv)
        log.border_subtitle = escape_markup("#" + " #".join(tags)) if tags else subtitle("COMMS LOG", self.lang)
        history = self.store.history(conv)
        self._log_empty = not history
        if not history:
            log.write(Text(self.t("── НЕТ ЗАПИСЕЙ · NO TRAFFIC LOGGED ──"), style="#5f5532"))
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
            t.append(f"  ◄ {self.t('ПРМ')}  ", style="bold #7fdc6f")
            name_style = Style(bold=True, color="#7fdc6f")
            if m.conv.startswith("chan:"):  # click the name to start a reply mentioning them
                self._reply_targets.append((m.conv, m.sender))
                name_style += Style(meta={"@click": f"app.mention({len(self._reply_targets) - 1})"})
            t.append(f"{m.sender[:14]:<14}", style=name_style)
            t.append(" ")
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
            t.append(f"  ► {self.t('ПРД')}  ", style="bold #ffb000")
            t.append(f"{m.sender[:14]:<14} ", style="bold #ffb000")
            t.append(m.text, style="#e8e2c8")
            if m.status:
                colour = {"delivered": "#7fdc6f", "failed": "#ff4a3d", "no ack": "#ff4a3d"}.get(m.status, "#5f5532")
                t.append(f"  [{self.t(STATUS_RU.get(m.status, m.status.upper()))}]", style=colour)
        if show_conv:
            t = Text.assemble((f"{self.label_of(m.conv)[:10]:<10} ", "#c8261e"), t)
        log.write(t)

    # ---------------------------------------------------------------- status pane
    def render_status(self) -> None:
        s = self.state
        t = Text()

        def row(k: str, v: str, style: str = "#ffb000", raw: str = "") -> None:
            t.append(f"{self.t(k):<10}", style="#8a7a4a")
            t.append(f"{self.t(v)}{raw}\n", style=style)

        if self.offline:
            row("СВЯЗЬ", "АВТОНОМНО", "bold #c9a64a")
        elif s.connected:
            row("СВЯЗЬ", f"■ УСТАНОВЛЕНА {s.link}", "bold #7fdc6f")
        else:
            row("СВЯЗЬ", "□ НЕТ", "bold #ff4a3d")
        if self.via_logger:
            row("РЕЖИМ", "ЧЕРЕЗ ЛОГГЕР", "#c9a64a")
        row("УЗЕЛ", "", raw=s.name or "—")
        if s.freq:
            row("ЧАСТОТА", f"{s.freq:.3f} МГц")
            row("ПОЛОСА", f"{s.bw:g} кГц  SF{s.sf}")
            row("МОЩН.", f"{s.tx_power} дБм")
        if s.battery_mv:
            pct = max(0, min(100, round((s.battery_mv - 3300) / 9)))
            bars = round(pct / 10)
            row("АККУМ.", f"{'▮' * bars}{'▯' * (10 - bars)} {s.battery_mv / 1000:.2f}В")
        have = firmware.parse_version(s.firmware)
        if have:
            row("ПРОШИВКА", firmware.fmt(have))
            if self.fw_latest and self.fw_latest.version > have:
                row("", f"▲ НОВАЯ {firmware.fmt(self.fw_latest.version)}", "bold #0b0d0a on #ffb000")
        pos = self.position()
        if s.gps is not None:
            row("GPS", "ВКЛ" if s.gps else "ВЫКЛ", raw=f"  {s.gps_interval}s" if s.gps and s.gps_interval else "")
        if pos:
            row("КООРД.", "", raw=f"{pos[0]:.4f},{pos[1]:.4f}")
            row("", f"  {pos[2]}" + (f"  {s.alt:.0f}m" if pos[2] == "GPS" and s.alt else ""), "#8a7a4a")
        w = self.weather
        if w:
            row("ПОГОДА", f"{w.temp:.0f}{w.temp_unit} {w.summary_ru}")
            row("ВЕТЕР", "", raw=f"{w.wind:.0f} {w.wind_unit} {w.compass}")
            row("ВЛАЖН.", "", raw=f"{w.humidity}%")
        elif self._weather_err:
            row("ПОГОДА", "—", "#ff4a3d", raw=f" {self._weather_err}")
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
        if text in ("/mute", "/unmute"):
            if (text == "/mute") != self.sound.muted:
                self.action_toggle_mute()
            return
        if text == "/ticker":
            self.action_toggle_ticker()
            return
        if text == "/stop":
            self.action_stop_anthem()
            return
        if text in ("/help", "/?"):
            log = self.query_one("#log", RichLog)
            for line in HELP:
                log.write(Text(self.t(line), style="#c9a64a"))
            return
        if text == "/firmware":
            self.show_firmware()
            return
        if text.startswith("/dm "):
            self.open_dm(text[4:].strip())
            return
        cmd, _, rest = text.partition(" ")
        handler = {"/home": self.cmd_home, "/gps": self.cmd_gps, "/weather": self.cmd_weather,
                   "/units": self.cmd_units, "/cli": self.cmd_cli,
                   "/name": self.cmd_name, "/reboot": self.cmd_reboot,
                   "/reconnect": self.cmd_reconnect,
                   "/join": self.cmd_join, "/leave": self.cmd_leave, "/fav": self.cmd_fav,
                   "/unfav": self.cmd_unfav, "/tag": self.cmd_tag, "/untag": self.cmd_untag,
                   "/tags": self.cmd_tags}.get(cmd)
        if handler:
            try:
                await handler(rest.strip())
            except Exception as e:
                self.note(f"── {self.t('СБОЙ')} · {e} ──", "#ff4a3d")
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
            self.event("СБОЙ ПЕРЕДАЧИ · ", free=str(e))
            return
        msg = self.store.get(mid) if mid else None
        if msg:
            self.write_msg(msg)
            if msg.status == "sent":
                self.sound.effect("sent")
                self.query_one(Mascot).react("point", 3, say="sent")
        self.render_status()

    def note(self, line: str, style: str = "#c9a64a") -> None:
        self.query_one("#log", RichLog).write(Text(line, style=style))

    def find_contact(self, name: str, quiet: bool = False):
        """One person/room contact by full or partial name; explains in the log when it can't."""
        people = [c for c in self.state.contacts if c.type not in (CONTACT_REPEATER, CONTACT_SENSOR)]
        exact = [c for c in people if c.name.lower() == name.lower()]
        hits = exact or [c for c in people if name.lower() in c.name.lower()]
        if len(hits) == 1:
            return hits[0]
        if not quiet:
            if not hits:
                self.note(f"── {self.t('АБОНЕНТ НЕ НАЙДЕН')} · no contact matches '{name}' ──", "#ff4a3d")
            else:
                self.note(f"── {len(hits)} {self.t('СОВПАДЕНИЙ')} · be more specific: ──")
                for c in hits[:15]:
                    self.note(f"   {c.name}")
        return None

    def contact_arg(self, name: str):
        """The named contact, or the one on screen when no name is given."""
        if name:
            return self.find_contact(name)
        c = next((c for c in self.state.contacts if c.conv == self.current), None)
        if c is None:
            self.note("── open a contact first, or give a name ──", "#ff4a3d")
        return c

    # ---------------------------------------------------------------- replies
    def start_reply(self, sender: str) -> None:
        """Put @[sender] at the start of the command line (replacing any earlier mention) and focus it."""
        inp = self.query_one("#tx", Input)
        rest = re.sub(r"^@\[[^\]]*\]\s*", "", inp.value)
        inp.value = f"@[{sender}] {rest}"
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_mention(self, i: int) -> None:
        if not 0 <= i < len(self._reply_targets):
            return
        conv, sender = self._reply_targets[i]
        if conv != self.current or self.searching:  # clicked in search results: go to that channel
            self.show_conv(conv)
            self.rebuild_convs()
        self.start_reply(sender)

    def action_reply(self) -> None:
        if not self.current:
            self.event("НЕТ АДРЕСАТА · NO TARGET")
            return
        if self.searching:
            self.show_conv(self.current)
        if self.current.startswith("dm:"):  # everything typed here already goes to them
            self.query_one("#tx", Input).focus()
            return
        last = next((m for m in reversed(self.store.history(self.current, 200)) if m.direction == "in"), None)
        if not last:
            self.event("НЕКОМУ ОТВЕЧАТЬ · NOTHING TO REPLY TO")
            return
        self.start_reply(last.sender)

    def open_dm(self, name: str) -> None:
        c = self.find_contact(name)
        if not c:
            return
        self.pinned.add(c.conv)
        self.rebuild_convs()
        self.show_conv(c.conv)
        self.rebuild_convs()

    async def cmd_home(self, rest: str) -> None:
        if not rest:
            home = self.settings.get("home")
            pos = self.position()
            if home:
                self.note(f"{self.t('ДОМ')}: {home[0]:.4f},{home[1]:.4f}")
            else:
                self.note(f"{self.t('ДОМ')}: {self.t('НЕ ЗАДАН')} · /home <lat> <lon>")
            if pos and pos[2] != "ДОМ":
                self.note(f"  weather now uses {self.t(pos[2])} {pos[0]:.4f},{pos[1]:.4f}; home is the fallback")
            return
        if rest == "clear":
            self.settings.pop("home", None)
        else:
            try:
                lat, lon = (float(x) for x in rest.replace(",", " ").split())
            except ValueError:
                raise ValueError("/home <lat> <lon>, e.g. /home 33.64 -117.69") from None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("LAT -90..90, LON -180..180")
            self.settings["home"] = [lat, lon]
        self._save_settings()
        self.weather = None
        await self.refresh_weather()
        self.render_status()

    async def cmd_gps(self, rest: str) -> None:
        parts = rest.split()
        if not parts or parts[0] not in ("on", "off"):
            raise ValueError("/gps on|off [interval seconds]")
        interval = int(parts[1]) if len(parts) > 1 else None
        await self.radio.set_gps(parts[0] == "on", interval)
        self.event("GPS ", free=parts[0].upper())

    async def cmd_units(self, rest: str) -> None:
        if rest not in ("metric", "imperial"):
            raise ValueError("/units metric|imperial")
        self.settings["units"] = rest
        self._save_settings()
        await self.refresh_weather()

    async def cmd_weather(self, rest: str) -> None:
        pos = self.position()
        if not pos:
            raise ValueError("NO POSITION: turn on GPS (/gps on) or set /home <lat> <lon>")
        await self.refresh_weather()
        w = self.weather
        if not w:
            raise RuntimeError(f"WEATHER UNAVAILABLE {self._weather_err}")
        self.note(f"{self.t('ПОГОДА')} @ {pos[0]:.4f},{pos[1]:.4f} ({self.t(pos[2])}) · Open-Meteo")
        self.note(f"  {w.temp:.0f}{w.temp_unit} {self.t(w.summary_ru)}, {self.t('ОЩУЩ.')} {w.feels:.0f}{w.temp_unit}")
        self.note(f"  {self.t('ВЕТЕР')} {w.wind:.0f} {w.wind_unit} {w.compass}, {self.t('ВЛАЖН.')} {w.humidity}%")

    async def cmd_name(self, rest: str) -> None:
        if not rest:
            raise ValueError("/name <new name>")
        old = self.state.name
        await self.radio.set_name(rest)
        self.event("УЗЕЛ ПЕРЕИМЕНОВАН ", free=f"{old} → {rest}")
        self.note(f"── {old} → {rest} · announced to the mesh; Bluetooth name MeshCore-{rest} after /reboot ──")

    async def cmd_reconnect(self, rest: str) -> None:
        if self.via_logger:
            self.event("ЧЕРЕЗ ЛОГГЕР ", free="(the logger keeps the link; nothing to reconnect)")
            return
        self.event("ПЕРЕПОДКЛЮЧЕНИЕ")
        self.radio.reconnect()

    async def cmd_reboot(self, rest: str) -> None:
        self.event("ПЕРЕЗАГРУЗКА")
        await self.radio.reboot()

    async def cmd_cli(self, rest: str) -> None:
        if not rest:
            raise ValueError("/cli <command>, e.g. /cli get wifi.ip")
        reply = await self.radio.cli(rest)
        shown = "set wifi.pwd ***" if rest.startswith("set wifi.pwd") else rest
        self.note(f"CLI> {shown}")
        self.note(f"     {reply}")

    async def cmd_join(self, rest: str) -> None:
        name, _, key = rest.partition(" ")
        if not name:
            raise ValueError("/join #name  or  /join <name> <key>")
        idx = await self.radio.join_channel(name, key.strip() or None)
        self.event("КАНАЛ ДОБАВЛЕН ", free=f"{self.t('К-')}{idx:02d} {name}")
        self.show_conv(f"chan:{idx}")
        self.rebuild_convs()

    async def cmd_leave(self, rest: str) -> None:
        if rest:
            ch = next((c for c in self.state.channels if c.name.lower() == rest.lower()), None)
        else:
            ch = next((c for c in self.state.channels if c.conv == self.current), None)
        if ch is None:
            raise ValueError("NO SUCH CHANNEL (name it, or open it first)")
        await self.radio.leave_channel(ch.idx)
        self.event("КАНАЛ УДАЛЁН ", free=ch.name)
        self.show_conv("chan:0")
        self.rebuild_convs()

    async def _set_fav(self, rest: str, on: bool) -> None:
        c = self.contact_arg(rest)
        if not c:
            return
        await self.radio.set_favourite(c.public_key, on)
        self.event("В ИЗБРАННОМ " if on else "УБРАН ИЗ ИЗБРАННОГО ", free=c.name)
        self.rebuild_convs()

    async def cmd_fav(self, rest: str) -> None:
        await self._set_fav(rest, True)

    async def cmd_unfav(self, rest: str) -> None:
        await self._set_fav(rest, False)

    def _tag_args(self, rest: str):
        """'<name> <tag>' where the tag is the last word; name may be omitted for the contact on screen."""
        parts = rest.split()
        if not parts:
            raise ValueError("/tag <name> <tag>")
        tag = parts[-1].lstrip("#").lower()
        return self.contact_arg(" ".join(parts[:-1])), tag

    async def cmd_tag(self, rest: str) -> None:
        c, tag = self._tag_args(rest)
        if c:
            self.store.add_tag(c.conv, tag)
            self.store.set_title(c.conv, c.name)
            self.event("МЕТКА ", free=f"#{tag} · {c.name}")
            self.rebuild_convs()
            if self.current == c.conv:
                self.show_conv(c.conv)

    async def cmd_untag(self, rest: str) -> None:
        c, tag = self._tag_args(rest)
        if c and self.store.remove_tag(c.conv, tag):
            self.event("МЕТКА СНЯТА ", free=f"#{tag} · {c.name}")
            self.rebuild_convs()
            if self.current == c.conv:
                self.show_conv(c.conv)

    async def cmd_tags(self, rest: str) -> None:
        tags = self.store.all_tags()
        want = rest.lstrip("#").lower()
        if want:
            tags = {want: tags.get(want, [])}
        if not any(tags.values()):
            self.note(f"── {self.t('НЕТ МЕТОК')} ──", "#5f5532")
            return
        for tag, convs in tags.items():
            self.note(f"#{tag}: " + ", ".join(self.label_of(c) for c in convs))

    def do_search(self, needle: str) -> None:
        self.searching = True
        log = self.query_one("#log", RichLog)
        log.clear()
        self.set_title("log", f"{self.t('ПОИСК')} · {needle}")
        log.border_subtitle = "SEARCH · pick a conversation to leave"
        hits = self.store.search(needle)
        if not hits:
            log.write(Text(self.t("── НЕ НАЙДЕНО · NOT FOUND ──"), style="#5f5532"))
        for m in hits:
            self.write_msg(m, show_conv=True)

    async def on_unmount(self) -> None:
        self.sound.stop_anthem()
        self.farewell = farewell(self.lang, self.state.name, self.store.count() - self._start_count)
        await self.radio.stop()
        self.store.close()


def farewell(lang: str, node: str, logged: int) -> str:
    """The line printed after LUCH closes."""
    if lang == "en":
        return f"★ LUCH-M · SESSION CLOSED · {node or 'NO NODE'} · {logged} logged · DO SVIDANIYA, COMRADE ★"
    return f"★ ЛУЧ-М · СЕАНС СВЯЗИ ЗАВЕРШЁН · {node or 'НЕТ УЗЛА'} · записано: {logged} · ДО СВИДАНИЯ, ТОВАРИЩ ★"


def radio_slug(name: str) -> str:
    """'MeshCore-Mojeska' -> 'mojeska': the per-radio log and socket name."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"^meshcore-", "", slug) or "luch"


def mirror_path_for(db: Path) -> Path | None:
    """Where to keep a copy of the logger's log when the logger runs on another machine
    (its socket forwarded over ssh), since its SQLite file isn't on this disk."""
    host = asyncio.run(logger_host(default_socket_path(db)))
    if not host or host.split(".")[0].lower() == socket.gethostname().split(".")[0].lower():
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", host.split(".")[0].lower()).strip("-") or "remote"
    return db.with_name(f"{db.stem}@{slug}.db")


def find_logger_db(data_dir: Path) -> Path | None:
    """The database of the one running logger, so plain `luch` connects to it."""
    alive = [p for p in sorted(data_dir.glob("*.sock")) if asyncio.run(socket_alive(p))]
    return alive[0].with_suffix(".db") if len(alive) == 1 else None


async def wifi_setup(args) -> None:
    """Put a radio on Wi-Fi. Prompts for the password so it never lands in shell history."""
    import getpass
    ssid = input("Wi-Fi network (SSID, 2.4 GHz): ").strip()
    pwd = getpass.getpass("Wi-Fi password: ")
    db = args.db
    if await socket_alive(default_socket_path(db)):
        radio = RemoteRadio(default_socket_path(db), lambda k, d: None)
    else:
        radio = Radio(args.device or DEFAULT_DEVICE, Store(db), lambda k, d: None, port=args.port, tcp=args.tcp,
                      pin=args.pin)
    task = asyncio.create_task(radio.run())
    for _ in range(60):
        if radio.state.connected:
            break
        await asyncio.sleep(0.5)
    else:
        task.cancel()
        raise SystemExit("could not reach the radio")
    for cmd in (f"set wifi.ssid {ssid}", f"set wifi.pwd {pwd}", "set wifi.enabled 1"):
        reply = await radio.cli(cmd)
        print(reply.replace(pwd, "***") if pwd else reply)
    print("rebooting the radio to join Wi-Fi...")
    try:
        await radio.reboot()
    except Exception:
        pass
    await radio.stop()
    task.cancel()
    print("when it's back, `luch /cli get wifi.ip` or check your router; then connect with --tcp <ip>")


def existing_log(db: Path) -> Path:
    """The log to back up or reset; stops with the logs that do exist if this one doesn't."""
    if db.exists():
        return db
    logs = sorted(db.parent.glob("*.db"))
    hint = "".join(f"\n  --db {p}" for p in logs) or "\n  (none yet)"
    raise SystemExit(f"no log at {db}. Pick one with --radio NAME or:{hint}")


def backup_path(db: Path, dest: Path | None) -> Path:
    """Where `luch backup` writes: the given file, or a dated file in the given folder
    (by default ~/.local/share/luch/backups)."""
    dest = (dest or db.parent / "backups").expanduser()
    if dest.is_dir() or not dest.suffix:
        dest.mkdir(parents=True, exist_ok=True)
        base = f"{db.stem}-{time.strftime('%Y-%m-%d-%H%M%S')}"
        dest, n = dest / f"{base}.db", 2
        while dest.exists():
            dest, n = dest.with_name(f"{base}-{n}.db"), n + 1
    elif dest.exists():
        raise SystemExit(f"{dest} already exists; pick another name")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def backup_log(db: Path, dest: Path | None) -> Path:
    db, out = existing_log(db), backup_path(db, dest)
    store = Store(db)
    try:
        store.backup(out)
        print(f"backed up {store.count()} messages\n  from {db}\n  to   {out}")
    finally:
        store.close()
    return out


def reset_log(db: Path, yes: bool = False) -> None:
    db = existing_log(db)
    store = Store(db)
    try:
        n = store.count()
        print(f"This deletes all {n} messages, tags and remembered contacts in\n  {db}\n"
              "Contacts stored on the radio stay; they come back on the next connect.")
        if not yes and input("Type yes to reset: ").strip().lower() != "yes":
            raise SystemExit("cancelled, nothing changed")
        out = backup_path(db, None)
        store.backup(out)
        print(f"saved a backup first: {out}")
        store.reset()
        print(f"reset: {n} messages deleted. Restart any open luch screens to clear them too.")
    finally:
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="luch", description="ЛУЧ: MeshCore terminal over Bluetooth, USB or Wi-Fi")
    link = argparse.ArgumentParser(add_help=False)
    link.add_argument("-d", "--device", help="BLE name (or part of it) or address, e.g. MeshCore-Mojeska")
    link.add_argument("--port", help="USB serial port, or 'auto' to find it")
    link.add_argument("--tcp", metavar="HOST[:PORT]", help="radio on Wi-Fi (port 5000 by default)")
    link.add_argument("--pin", type=valid_pin, help="Bluetooth pairing PIN shown on the radio's screen "
                      "(Linux; only needed the first time, macOS asks in a dialog)")
    link.add_argument("--radio", help="name for this radio's log (default: from -d, e.g. MeshCore-Mojeska -> mojeska)")
    link.add_argument("--db", type=Path, help="SQLite log (default ~/.local/share/luch/<radio>.db)")
    ap.add_argument("--offline", action="store_true", help="browse the log without connecting")
    ap.add_argument("--direct", action="store_true", help="talk to the radio directly even if the logger runs")
    ap.add_argument("--debug", action="store_true", help="write a debug log next to the database")
    ap.add_argument("--english", action="store_true", help="start with English screen text (Ctrl+T switches)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("logger", parents=[link], help="run the background logger in the foreground")
    sub.add_parser("wifi", parents=[link], help="put the radio on Wi-Fi (asks for SSID and password)")
    pick = argparse.ArgumentParser(add_help=False)
    pick.add_argument("-d", "--device", help="radio whose log to use, e.g. MeshCore-Mojeska")
    pick.add_argument("--radio", help="name of the radio's log (default: the running logger's)")
    pick.add_argument("--db", type=Path, help="path to the SQLite log")
    bk = sub.add_parser("backup", parents=[pick], help="copy the message log to a backup file")
    bk.add_argument("dest", nargs="?", type=Path,
                    help="file or folder to write to (default ~/.local/share/luch/backups/)")
    rs = sub.add_parser("reset", parents=[pick], help="delete every logged message (saves a backup first)")
    rs.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    svc = sub.add_parser("service", help="manage the background logger (launchd / systemd --user / Task Scheduler)")
    svc_sub = svc.add_subparsers(dest="action", required=True)
    svc_sub.add_parser("install", parents=[link], help="install and start at login")
    for name in ("uninstall", "start", "stop", "status"):
        svc_sub.add_parser(name)
    for a in link._actions:  # link options work on the top level too
        if a.dest != "help":
            ap._add_action(a)
    args = ap.parse_args()

    data_dir = default_db_path().parent
    data_dir.mkdir(parents=True, exist_ok=True)
    if args.db:
        args.db = args.db.expanduser()
    else:
        name = getattr(args, "radio", None) or (args.device and radio_slug(args.device))
        if name:
            args.db = data_dir / f"{radio_slug(name)}.db"
        elif args.cmd in (None, "backup", "reset") and not args.offline and (found := find_logger_db(data_dir)):
            args.db = found
        else:
            args.db = default_db_path()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    device = args.device or DEFAULT_DEVICE

    if args.cmd == "logger":
        run_daemon(device, args.db, args.port, args.tcp, args.pin)
        return
    if args.cmd == "backup":
        backup_log(args.db, args.dest)
        return
    if args.cmd == "reset":
        reset_log(args.db, args.yes)
        return
    if args.cmd == "wifi":
        asyncio.run(wifi_setup(args))
        return
    if args.cmd == "service":
        if args.action == "install":
            service.install(device, args.db, args.port, args.tcp, args.pin)
        else:
            getattr(service, args.action)()
        return

    if args.debug:
        logging.basicConfig(filename=str(args.db.with_suffix(".log")), level=logging.DEBUG)
    else:
        logging.getLogger().addHandler(logging.NullHandler())  # keep library warnings off the screen
    via_logger = (not args.offline and not args.direct
                  and asyncio.run(socket_alive(default_socket_path(args.db))))
    app = LuchApp(device, args.db, offline=args.offline, port=args.port, via_logger=via_logger, tcp=args.tcp,
                  pin=args.pin, mirror_path=mirror_path_for(args.db) if via_logger else None)
    if args.english:
        app.lang = "en"
    app.run()
    if app.farewell:
        print(f"\033[1;38;2;255;233;168;48;2;200;38;30m {app.farewell} \033[0m"
              f"\033[1;38;2;255;215;0;48;2;0;87;183m FREE UKRAINE! \033[0m")


if __name__ == "__main__":
    main()
