import wave

from luch import sound
from luch.app import LuchApp
from luch.mascot import Mascot


def test_anthem_score_lines_up():
    assert sum(d for _, d in sound.MELODY) == 4 * len(sound.HARMONY)
    assert all(h in sound.CH for h in sound.HARMONY)


def test_effect_renders(tmp_path):
    sound.render_effect("rx", tmp_path / "rx.wav")
    with wave.open(str(tmp_path / "rx.wav")) as w:
        assert w.getframerate() == sound.SR and 0.1 < w.getnframes() / sound.SR < 0.3


def test_muted_effects_do_nothing(tmp_path):
    s = sound.Sound(tmp_path, muted=True)
    s.player = ["false"]
    s.effect("rx")
    assert not (tmp_path / "sounds").exists()  # nothing rendered or played


class FakeSound(sound.Sound):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.player, self.playing = ["true"], False

    def prepare(self):
        pass

    def anthem_playing(self):
        return self.playing

    def play_anthem(self):
        self.playing = True
        return True

    def stop_anthem(self):
        self.playing = False


async def test_mute_and_launch(tmp_path):
    app = LuchApp("x", tmp_path / "t.db", offline=True)
    app.sound = FakeSound(tmp_path)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("ctrl+s")
        assert app.sound.muted and app.settings["muted"] is True
        app.countdown_seconds = 2
        await pilot.click("#launch")
        await pilot.pause(0.2)
        assert app._count == 2 and app.query_one("#countdown").display
        assert app.query_one(Mascot).reaction_say == "standby"
        await pilot.pause(2.3)
        assert not app.query_one("#countdown").display
        assert app.sound.playing
        assert app.query_one(Mascot).reaction_say == "anthem"
        assert "СТОП" in str(app.query_one("#launch").label)
        await pilot.press("escape")
        assert not app.sound.playing
        assert "ПУСК" in str(app.query_one("#launch").label)


async def test_countdown_can_be_aborted(tmp_path):
    app = LuchApp("x", tmp_path / "t.db", offline=True)
    app.sound = FakeSound(tmp_path)
    async with app.run_test(size=(140, 44)) as pilot:
        app.countdown_seconds = 3
        await pilot.press("ctrl+l")
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(3.2)
        assert not app.sound.playing and not app.query_one("#countdown").display


def test_missing_player_is_silent(tmp_path):
    s = sound.Sound(tmp_path)
    s.player = ["no-such-player-luch"]
    s.tick()  # would raise FileNotFoundError before
    assert s.play_anthem() is False
