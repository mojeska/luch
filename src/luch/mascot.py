"""МИША: a cosmonaut bear who reacts to the radio, after the diagnostic-screen mascot in Red Planet (2000).

Each frame is drawn on a 24x12 grid with a parallel colour mask:
R helmet, F face, Y suit, B gloves and belt, space = blank.
"""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

W = 24
BODY_CENTRE = 9  # column of the helmet's middle; stays put when an arm goes up
COLOURS = {"R": "#e0532f", "F": "#f2e6c9", "Y": "#c6d92e", "B": "#4aa3f0"}

FACES = {  # eyes, mouth
    "idle": ("o o", "w"),
    "blink": ("- -", "w"),
    "wave": ("^ ^", "w"),
    "point": ("- o", "v"),
    "distress": ("> <", "O"),
}


def helmet(face: str) -> list[tuple[str, str]]:
    eyes, mouth = FACES[face]
    return [
        ("     .-'''''-.", "     RRRRRRRRR"),
        ("    / (_) (_) \\", "    R FFF FFF R"),
        (f"   |  ( {eyes} )  |", "   R  FFFFFFF  R"),
        (f"   |   ( {mouth} )   |", "   R   FFFFF   R"),
        ("    \\  '---'  /", "    R  FFFFF  R"),
        ("     '-.___.-'", "     RRRRRRRRR"),
    ]


LEGS = [
    ("     |   |   |", "     Y   Y   Y"),
    ("     |   |   |", "     Y   Y   Y"),
    ("    (___/ \\___)", "    BBBBY YBBBB"),
]

BODIES = {
    "idle": [  # arms down, gloves at the sides
        ("   .-/=====\\-.", "   YYYBBBBBYYY"),
        ("  / /|  [o]  |\\ \\", "  Y YY  BBB  YY Y"),
        (" (_) |_______| (_)", " BBB YBBBBBBBY BBB"),
    ],
    "raised_right": [  # right arm (screen right) goes up
        ("   .-/=====\\-.//", "   YYYBBBBBYYYYY"),
        ("  / /|  [o]  |", "  Y YY  BBB  Y"),
        (" (_) |_______|", " BBB YBBBBBBBY"),
    ],
    "raised_left": [  # left arm (screen left) goes up to the helmet
        ("   \\-/=====\\-.", "   YYYBBBBBYYY"),
        ("     |  [o]  |\\ \\", "     Y  BBB  YY Y"),
        ("     |_______| (_)", "     YBBBBBBBY BBB"),
    ],
}

# (row, col, text, mask) drawn over the helmet rows
ARMS = {
    "wave": [
        (0, 19, "\\|//", "BBBB"),
        (1, 19, "(__)", "BBBB"),
        (2, 19, "//", "YY"),
        (3, 18, "//", "YY"),
        (4, 17, "//", "YY"),
        (5, 16, "//", "YY"),
    ],
    "wave2": [
        (0, 18, "\\\\|/", "BBBB"),
        (1, 19, "(__)", "BBBB"),
        (2, 19, "//", "YY"),
        (3, 18, "//", "YY"),
        (4, 17, "//", "YY"),
        (5, 16, "//", "YY"),
    ],
    "point": [
        (0, 20, "|", "B"),
        (1, 19, "(=)", "BBB"),
        (2, 19, "//", "YY"),
        (3, 18, "//", "YY"),
        (4, 17, "//", "YY"),
        (5, 16, "//", "YY"),
    ],
    "distress": [
        (0, 1, "\\|/", "BBB"),
        (1, 1, "(_)", "BBB"),
        (2, 1, "|", "Y"),
        (3, 1, "|", "Y"),
        (4, 1, "|", "Y"),
        (5, 2, "\\", "Y"),
    ],
}

POSES = {  # pose -> (face, body, arm overlay)
    "idle": ("idle", "idle", None),
    "blink": ("blink", "idle", None),
    "wave": ("wave", "raised_right", "wave"),
    "wave2": ("wave", "raised_right", "wave2"),
    "point": ("point", "raised_right", "point"),
    "distress": ("distress", "raised_left", "distress"),
}

SAYS = {
    "idle": "ВСЁ В НОРМЕ",
    "wave": "ПРИВЕТ! НОВОЕ СООБЩЕНИЕ",
    "point": "ДОСТАВЛЕНО!",
    "sent": "ОТПРАВЛЕНО!",
    "distress": "НЕТ СВЯЗИ",
    "battery": "СЛАБОЕ ЗАЖИГАНИЕ",
    "anthem": "СЛАВЬСЯ, ОТЕЧЕСТВО!",
}


def frame(pose: str) -> list[tuple[str, str]]:
    """Rows of (text, mask), each padded to W."""
    face, body, arm = POSES[pose]
    rows = helmet(face) + BODIES[body] + LEGS
    grid = [[(" ", " ")] * W for _ in rows]
    for r, (text, mask) in enumerate(rows):
        for c, (ch, m) in enumerate(zip(text, mask)):
            grid[r][c] = (ch, m)
    for r, c, text, mask in ARMS.get(arm, []):
        for i, (ch, m) in enumerate(zip(text, mask)):
            grid[r][c + i] = (ch, m)
    return ["".join(ch for ch, _ in row) for row in grid], ["".join(m for _, m in row) for row in grid]


def render(pose: str, width: int = W) -> Text:
    """The pose with the bear's body centred in `width` columns."""
    lines, masks = frame(pose)
    pad = max(0, width // 2 - BODY_CENTRE)
    t = Text()
    for line, mask in zip(lines, masks):
        t.append(" " * pad)
        for ch, m in list(zip(line, mask))[: max(0, width - pad)]:
            t.append(ch, style=COLOURS.get(m, ""))
        t.append("\n")
    return t


class Mascot(Static):
    """Shows a base mood (idle or distress) and short reactions (wave, point) on top of it."""

    def on_mount(self) -> None:
        self.base = "idle"
        self.say_key = "idle"
        self.reaction: str | None = None
        self.reaction_say = "idle"
        self._ticks = 0
        self._reaction_left = 0
        self.set_interval(0.25, self._tick)
        self.draw()

    def set_base(self, pose: str, say: str) -> None:
        self.base, self.say_key = pose, say
        self.draw()

    def react(self, pose: str, seconds: float, say: str | None = None) -> None:
        self.reaction, self.reaction_say = pose, say or pose
        self._reaction_left = int(seconds * 4)
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def _tick(self) -> None:
        self._ticks += 1
        if self._reaction_left:
            self._reaction_left -= 1
            if not self._reaction_left:
                self.reaction = None
        self.draw()

    def draw(self) -> None:
        if self.reaction:
            pose, say = self.reaction, self.reaction_say
            if pose == "wave" and self._ticks % 4 < 2:  # flap the hand
                pose = "wave2"
        else:
            pose, say = self.base, self.say_key
            if pose == "idle" and self._ticks % 20 == 0:
                pose = "blink"
        width = max(W, self.content_size.width or W)
        t = render(pose, width)
        t.append(self.app.t(SAYS[say]).center(width).rstrip(), style="bold #7fdc6f")
        self.update(t)
