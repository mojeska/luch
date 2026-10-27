"""The red launch banner: a 10-second countdown in big block digits over the comms log."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

DIGITS = {
    "0": [" ████ ", "██  ██", "██  ██", "██  ██", " ████ "],
    "1": ["  ██  ", " ███  ", "  ██  ", "  ██  ", " ████ "],
    "2": [" ████ ", "    ██", " ████ ", "██    ", "██████"],
    "3": ["█████ ", "    ██", " ████ ", "    ██", "█████ "],
    "4": ["██  ██", "██  ██", "██████", "    ██", "    ██"],
    "5": ["██████", "██    ", "█████ ", "    ██", "█████ "],
    "6": [" ████ ", "██    ", "█████ ", "██  ██", " ████ "],
    "7": ["██████", "    ██", "   ██ ", "  ██  ", "  ██  "],
    "8": [" ████ ", "██  ██", " ████ ", "██  ██", " ████ "],
    "9": [" ████ ", "██  ██", " █████", "    ██", " ████ "],
}
W, H = 44, 13  # banner size including its border


def big(number: int) -> list[str]:
    rows = [""] * 5
    for ch in str(number):
        for i, line in enumerate(DIGITS[ch]):
            rows[i] += line + "  "
    return [r.rstrip() for r in rows]


class Countdown(Static):
    def show_count(self, n: int, title: str, hint: str) -> None:
        inner = W - 4
        t = Text(justify="center")
        t.append(f"★ {title} ★".center(inner) + "\n\n", style="bold #ffe9a8")
        for row in big(n):
            t.append(row.center(inner) + "\n", style="bold #ffe9a8")
        t.append("\n" + hint.center(inner), style="#ffd0c4")
        self.update(t)

    def place(self) -> None:
        """Centre over the comms log (the banner sits on the overlay layer of the centre column)."""
        log = self.app.query_one("#log")
        self.styles.offset = (max(0, (log.size.width - W) // 2), max(0, (log.size.height - H) // 2))
