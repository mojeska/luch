from luch.app import LuchApp
from luch.radio import Channel, Contact, RadioState


async def test_offline_ui_renders_and_receives(tmp_path):
    app = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(140, 40)) as pilot:
        state = RadioState(connected=True, name="Mojeska", freq=927.875, bw=62.5, sf=7, tx_power=22,
                           battery_mv=4012, channels=[Channel(0, "Public"), Channel(1, "#test")],
                           contacts=[Contact("ab" * 32, "Ivan", 1, 0, 2), Contact("cd" * 32, "Hilltop", 2, 0, 1)])
        app.on_radio("state", state)
        mid = app.store.add("chan:0", "in", "Ivan", "приём, как слышно?", 1_700_000_000, snr=6.5, path_len=2)
        app.on_radio("message", mid)
        mid2 = app.store.add("chan:1", "in", "Olga", "test one two", 1_700_000_100, snr=-3.0, path_len=-1)
        app.on_radio("message", mid2)
        app.on_radio("message", app.store.add("dm:" + "ab" * 6, "in", "Ivan", "private", 1_700_000_200))
        await pilot.pause()
        assert app.current == "chan:0"
        assert app.store.unread("chan:0") == 0  # shown, so read
        assert app.store.unread("chan:1") == 1
        labels = [label for _, label in app.convs]
        assert labels[:2] == ["К-00 PUBLIC", "К-01 #TEST"]
        assert any("IVAN" in label for label in labels)
        assert not any("HILLTOP" in label for label in labels)  # repeaters stay out of the list
        assert not any("OLGA" in label for label in labels)  # no contact entry without traffic
        await pilot.press(*"/dm hill", "enter")  # repeaters can't be opened
        assert app.current == "chan:0"
        await pilot.press("ctrl+t")
        assert app.lang == "en"
        assert [label for _, label in app.convs][:2] == ["C-00 PUBLIC", "C-01 #TEST"]
        await pilot.press("ctrl+t")
        assert app.lang == "ru"
        app.save_screenshot(str(tmp_path / "shot.svg"))


async def test_mascot_moods(tmp_path):
    from luch.mascot import Mascot
    app = LuchApp("x", tmp_path / "t.db", offline=False)
    app.radio.run = lambda: __import__("asyncio").sleep(0)  # no radio
    async with app.run_test(size=(140, 44)) as pilot:
        m = app.query_one(Mascot)
        app.on_radio("state", RadioState(connected=False))
        assert m.base == "distress"
        app.on_radio("state", RadioState(connected=True, battery_mv=3400))
        assert m.say_key == "battery"
        app.on_radio("state", RadioState(connected=True, battery_mv=4100, channels=[Channel(0, "Public")]))
        assert m.base == "idle"
        app.on_radio("message", app.store.add("chan:0", "in", "Ivan", "hi", 1))
        assert m.reaction == "wave"
        await pilot.press("ctrl+b")
        assert not m.display


def test_farewell_line():
    from luch.app import farewell
    assert "ДО СВИДАНИЯ" in farewell("ru", "Mojeska", 3) and "записано: 3" in farewell("ru", "Mojeska", 3)
    assert farewell("en", "", 0).startswith("★ LUCH-M · SESSION CLOSED · NO NODE")


async def test_ticker_scrolls_and_toggles(tmp_path):
    from luch.ticker import Ticker
    app = LuchApp("x", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(140, 44)) as pilot:
        tk = app.query_one(Ticker)
        await pilot.pause(0.3)
        first = tk._offset
        await pilot.pause(0.5)
        assert tk._offset > first  # moving
        await pilot.press("ctrl+k")
        assert not tk.display and app.settings["ticker"] is False
        await pilot.press("ctrl+k")
        assert tk.display


def test_ticker_includes_ukrainian_slogans():
    from luch.ticker import UA_SLOGANS
    assert ("СЛАВА ЗСУ!", "Glory to the Armed Forces of Ukraine!") in UA_SLOGANS and len(UA_SLOGANS) == 8
