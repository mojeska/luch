"""8-bit sound for LUCH: short effects for radio events, and the anthem for the ПУСК (Launch) button.

Everything is synthesised here (pulse, triangle and noise channels, like an NES), written once to WAV
files in the data directory, and played with the system player (afplay on macOS, paplay/pw-play/aplay
on Linux). No audio libraries needed.
"""
from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import sys
import time
import wave
from array import array
from pathlib import Path

SR = 22050
A4 = 440.0


def hz(midi: float) -> float:
    return A4 * 2 ** ((midi - 69) / 12)


class Mix:
    def __init__(self, seconds: float):
        self.buf = array("f", bytes(4 * int(SR * seconds + SR)))

    def add(self, start: float, sig: list[float]) -> None:
        i = int(start * SR)
        b = self.buf
        for k, v in enumerate(sig[: max(0, len(b) - i)]):
            b[i + k] += v

    def wav(self, path: Path) -> None:
        peak = max(1e-9, max(abs(v) for v in self.buf))
        scale = 0.85 * 32767 / peak
        out = array("h", (int(v * scale) for v in self.buf))
        while out and out[-1] == 0:  # trim trailing silence
            out.pop()
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(out.tobytes())


def pulse(f: float, dur: float, duty: float = 0.5, vol: float = 0.3, decay: float = 1.5, slide: float = 0.0) -> list[float]:
    """Square-ish pulse with an NES-style volume decay; `slide` bends the pitch (semitones over the note)."""
    n = int(SR * dur)
    out, phase = [0.0] * n, 0.0
    for i in range(n):
        x = i / max(1, n)
        ff = f * 2 ** (slide * x / 12) if slide else f
        phase = (phase + ff / SR) % 1
        env = max(0.0, 1 - x * decay * 0.6) if decay else 1.0
        if i > n - 80:  # avoid clicks
            env *= (n - i) / 80
        out[i] = vol * env * (1.0 if phase < duty else -1.0)
    return out


def tri(f: float, dur: float, vol: float = 0.35) -> list[float]:
    n = int(SR * dur)
    out = [0.0] * n
    for i in range(n):
        p = (i * f / SR) % 1
        env = 1.0 if i < n - 120 else (n - i) / 120
        out[i] = vol * env * (4 * abs(p - 0.5) - 1)
    return out


def noise(dur: float, vol: float = 0.2, decay: float = 25.0, seed: int = 1) -> list[float]:
    rnd = random.Random(seed)
    n = int(SR * dur)
    return [vol * rnd.uniform(-1, 1) * math.exp(-decay * i / SR) for i in range(n)]


# ------------------------------------------------------------------ effects
EFFECTS = {
    # name: list of (start s, midi note, length s, duty, volume, pitch slide)
    "rx": [(0.00, 81, 0.06, 0.25, 0.25, 0), (0.07, 88, 0.09, 0.25, 0.25, 0)],
    "dm": [(0.00, 76, 0.06, 0.25, 0.25, 0), (0.07, 81, 0.06, 0.25, 0.25, 0), (0.14, 88, 0.12, 0.25, 0.25, 0)],
    "sent": [(0.00, 84, 0.05, 0.125, 0.2, 0)],
    "delivered": [(0.00, 84, 0.06, 0.5, 0.2, 0), (0.07, 91, 0.10, 0.5, 0.2, 0)],
    "link_up": [(0.00, 72, 0.07, 0.25, 0.2, 0), (0.08, 76, 0.07, 0.25, 0.2, 0), (0.16, 79, 0.07, 0.25, 0.2, 0),
                (0.24, 84, 0.16, 0.25, 0.2, 0)],
    "link_down": [(0.00, 79, 0.35, 0.5, 0.22, -12)],
    "tick": [(0.00, 69, 0.07, 0.5, 0.22, 0)],
}


def render_effect(name: str, path: Path) -> None:
    notes = EFFECTS[name]
    m = Mix(max(s + d for s, _, d, *_ in notes) + 0.05)
    for start, note, length, duty, vol, slide in notes:
        m.add(start, pulse(hz(note), length, duty, vol, decay=1.2, slide=slide))
    m.wav(path)


# ------------------------------------------------------------------ the anthem
# Music by A. V. Alexandrov (1939), public domain; this chiptune arrangement is LUCH's own.
# Melody as (midi note, length in eighths); 0 = rest. Key of C, starts with a one-eighth pickup.
C4, D4, E4, F4, G4, A4_, B4 = 60, 62, 64, 65, 67, 69, 71
C5, D5, E5, F5 = 72, 74, 76, 77
MELODY = [
    (0, 7), (G4, 1),
    # verse
    (C5, 2), (G4, 1.5), (A4_, .5), (B4, 2), (E4, 1), (E4, 1),
    (A4_, 2), (G4, 1.5), (F4, .5), (G4, 2), (C4, 1), (C4, 1),
    (D4, 2), (D4, 1), (E4, 1), (F4, 2), (F4, 1), (G4, 1),
    (A4_, 2), (B4, 1), (C5, 1), (D5, 3), (G4, 1),
    (E5, 2), (D5, 1.5), (C5, .5), (D5, 2), (B4, 1), (G4, 1),
    (C5, 2), (B4, 1.5), (A4_, .5), (B4, 2), (E4, 1), (E4, 1),
    (A4_, 2), (G4, 1), (F4, 1), (G4, 2), (C4, 1), (C4, 1),
    (C5, 2), (B4, 1.5), (A4_, .5), (G4, 1), (B4, 1), (C5, 1), (D5, 1),
    # chorus
    (E5, 4), (D5, 1), (C5, 1), (B4, 1), (C5, 1),
    (D5, 3), (G4, 1), (G4, 1), (B4, 1), (C5, 1), (D5, 1),
    (C5, 4), (B4, 1), (A4_, 1), (G4, 1), (A4_, 1),
    (B4, 3), (E4, 1), (E4, 1), (G4, 1), (A4_, 1), (B4, 1),
    (C5, 2), (A4_, 1.5), (B4, .5), (C5, 2), (A4_, 1.5), (B4, .5),
    (C5, 2), (A4_, 1), (A4_, 1), (F5, 4),
    (F5, 2), (C5, 2), (E5, 1), (D5, 1), (C5, 1), (D5, 1),
    (E5, 3), (C5, 1), (C5, 4),
    (D5, 4), (C5, 1), (B4, 1), (A4_, 1), (B4, 1),
    (C5, 3), (A4_, 1), (A4_, 4),
    (C5, 2), (B4, 1), (A4_, 1), (G4, 2), (C4, 1.5), (C4, .5),
    (C5, 2), (B4, 1.5), (A4_, .5), (G4, 4),
    (C5, 8),
]
# Harmony, one chord per half bar (4 eighths): root for the bass, tones for the arpeggio.
CH = {
    "C": (48, [60, 64, 67]), "C/E": (52, [60, 64, 67]), "Em": (52, [59, 64, 67]), "F": (53, [57, 60, 65]),
    "F/C": (48, [57, 60, 65]), "Dm": (50, [57, 62, 65]), "G": (43, [59, 62, 67]), "G7": (43, [59, 62, 65]),
    "G7/D": (50, [59, 62, 65]), "Am": (45, [57, 60, 64]), "Am/C": (48, [57, 60, 64]), "Am/G": (43, [57, 60, 64]),
    "D7": (50, [57, 60, 66]), "F/G": (43, [57, 60, 65]), "Dm7": (50, [57, 60, 65]), "Bm7": (47, [57, 62, 66]), "E": (52, [56, 59, 64]),
}
HARMONY = (
    ["C", "C"]
    + ["C", "Em", "F", "C/E", "Dm", "F/C", "F/C", "G7/D",
       "C", "G", "Am", "Em", "Dm", "C/E", "D7", "G"]
    + ["C", "C/E", "G", "Em", "Am", "Am/C", "Em", "Am",
       "F", "F/G", "F/C", "F", "Dm7", "G7", "C", "Am",
       "Bm7", "E", "Am", "Am/G", "Dm7", "C/E", "D7", "G7", "C", "C"]
)
TEMPO = 84  # quarter notes per minute


def render_anthem(path: Path) -> None:
    eighth = 60 / TEMPO / 2
    total = sum(d for _, d in MELODY)
    m = Mix(total * eighth + 1)
    t = 0.0
    for note, d in MELODY:  # lead: 25% pulse with a little sustain, the "trumpet"
        if note:
            m.add(t * eighth, pulse(hz(note), d * eighth * 0.95, duty=0.25, vol=0.26, decay=0.5))
        t += d
    for k, name in enumerate(HARMONY):  # bass and arpeggio, half a bar each
        root, tones = CH[name]
        start = k * 4 * eighth
        for b in range(2):  # march bass: root, then the fifth
            m.add(start + b * 2 * eighth, tri(hz(root + (7 if b else 0)), 2 * eighth * 0.9))
        steps = 8  # sixteenth-note arpeggio, the chiptune "chord"
        for s in range(steps):
            m.add(start + s * eighth / 2, pulse(hz(tones[s % 3]), eighth / 2, duty=0.125, vol=0.06, decay=1.0))
        drum = noise(eighth * 0.6, 0.16 if k % 2 else 0.22, 40 if k % 2 else 25, seed=k)
        m.add(start, drum)  # march snare on each half bar
        m.add(start + 2 * eighth, noise(eighth * 0.3, 0.08, 60, seed=k + 99))
    m.wav(path)


# ------------------------------------------------------------------ playback
def _player() -> list[str] | None:
    if os.environ.get("LUCH_SOUND", "").lower() in ("0", "off", "no"):
        return None
    if sys.platform == "win32":
        return ["winsound"]  # built into Python on Windows; played in-process, see _WinSound
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay"]
    for cmd in (["paplay"], ["pw-play"], ["aplay", "-q"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


class _WinSound:
    """A sound playing through winsound, with the poll()/terminate() of a player process.
    winsound plays one sound at a time, so a new one cuts off the last."""

    def __init__(self, path: Path):
        import wave
        import winsound
        with wave.open(str(path)) as w:
            self.ends = time.monotonic() + w.getnframes() / w.getframerate()
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def poll(self) -> int | None:
        return None if time.monotonic() < self.ends else 0

    def terminate(self) -> None:
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)
        self.ends = 0


class Sound:
    """Effects (mutable) and the anthem (explicit, so it plays even when effects are muted)."""

    def __init__(self, cache_dir: Path, muted: bool = False):
        self.dir = cache_dir / "sounds"
        self.muted = muted
        self.player = _player()
        self._anthem: subprocess.Popen | _WinSound | None = None
        self._last = 0.0

    @property
    def available(self) -> bool:
        return self.player is not None

    def _file(self, name: str) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{name}-v1.wav"
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            render_anthem(tmp) if name == "anthem" else render_effect(name, tmp)
            tmp.replace(path)
        return path

    def _play(self, path: Path) -> subprocess.Popen | _WinSound | None:
        if not self.player:
            return None
        try:  # a missing or broken player means no sound, never a crash
            if self.player == ["winsound"]:
                return _WinSound(path)
            return subprocess.Popen(self.player + [str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, RuntimeError):
            return None

    def effect(self, name: str) -> None:
        """Play an effect unless muted; at most one every 0.4 s so a burst of messages doesn't chatter."""
        now = time.monotonic()
        if self.muted or not self.player or now - self._last < 0.4:
            return
        if self.player == ["winsound"] and self.anthem_playing():
            return
        self._last = now
        self._play(self._file(name))

    def tick(self) -> None:
        """Countdown tick: part of the launch the user asked for, so it ignores mute."""
        if self.player:
            self._play(self._file("tick"))

    def prepare(self) -> None:
        """Render every sound ahead of time (slow-ish in pure Python, so done in a worker)."""
        for name in [*EFFECTS, "anthem"]:
            self._file(name)

    def anthem_playing(self) -> bool:
        return self._anthem is not None and self._anthem.poll() is None

    def play_anthem(self) -> bool:
        self.stop_anthem()
        self._anthem = self._play(self._file("anthem"))
        return self._anthem is not None

    def stop_anthem(self) -> None:
        if self.anthem_playing():
            self._anthem.terminate()
        self._anthem = None
