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


async def test_reply_with_ctrl_r_and_clicking_a_sender(tmp_path):
    from textual.widgets import Input, RichLog
    app = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(140, 40)) as pilot:
        app.on_radio("state", RadioState(connected=True, name="Mojeska", channels=[Channel(0, "Public")],
                                         contacts=[Contact("ab" * 32, "Ivan", 1, 0, 2)]))
        app.on_radio("message", app.store.add("chan:0", "in", "Olga", "anyone on?", 1_700_000_000))
        app.on_radio("message", app.store.add("chan:0", "in", "Ivan", "приём", 1_700_000_100))
        await pilot.pause()
        tx = app.query_one("#tx", Input)

        await pilot.press("ctrl+r")  # newest sender
        assert tx.value == "@[Ivan] " and tx.has_focus
        await pilot.press(*"da")
        await pilot.press("ctrl+r")  # a second Ctrl+R keeps what was typed
        assert tx.value == "@[Ivan] da"

        # Click Olga's name in the log (the first message line).
        log = app.query_one("#log", RichLog)
        x = 0
        for seg in log.lines[0]._segments:
            if seg.style and seg.style.meta.get("@click"):
                break
            x += len(seg.text)
        left = log.content_region.x - log.region.x  # border + padding
        top = log.content_region.y - log.region.y
        await pilot.click("#log", offset=(left + x + 1, top))
        await pilot.pause()
        assert tx.value == "@[Olga] da"

        tx.value = ""
        app.open_dm("Ivan")  # in a DM, Ctrl+R just focuses the command line
        await pilot.press("ctrl+r")
        assert tx.value == "" and tx.has_focus


async def test_ctrl_o_collapses_the_conversation_list(tmp_path):
    from textual.widgets import Input, RichLog
    app = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(100, 40)) as pilot:
        app.on_radio("state", RadioState(connected=True, name="Mojeska",
                                         channels=[Channel(0, "Public"), Channel(1, "#test")]))
        await pilot.pause()
        log = app.query_one("#log", RichLog)
        wide_before = log.region.width
        app.query_one("#convs").focus()
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert not app.query_one("#left").display
        assert log.region.width >= wide_before + 27  # the list's width (less a 3-column strip) goes to the log
        assert app.query_one("#left-strip").display
        assert app.query_one("#tx", Input).has_focus  # not stuck on a hidden list
        await pilot.press("ctrl+n")  # still switch conversations without it
        assert app.current == "chan:1"

    again = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with again.run_test(size=(100, 40)) as pilot:
        assert not again.query_one("#left").display  # remembered
        await pilot.press("ctrl+o")
        assert again.query_one("#left").display and again.settings["convs"] is True


async def test_ctrl_g_collapses_the_gauges_panel(tmp_path):
    from textual.widgets import RichLog
    app = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        log = app.query_one("#log", RichLog)
        wide_before = log.region.width
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert not app.query_one("#right").display
        assert log.region.width >= wide_before + 29
        await pilot.press("ctrl+o")  # both collapsed: the log has all but the two strips
        await pilot.pause()
        assert log.region.width == app.size.width - 6

    again = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with again.run_test(size=(100, 40)):
        assert not again.query_one("#right").display and not again.query_one("#left").display



def _click_spot(app, frame_id, button):
    """Screen position of a panel's [−] or [+] in its top border."""
    w = app.query_one(frame_id)
    row = app.screen._compositor.render_strips()[w.region.y].text
    x = row.index(button, w.region.x, w.region.right)
    return x + 1, w.region.y


async def test_corner_buttons_fold_collapse_and_maximize(tmp_path):
    from textual.widgets import Input
    app = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(120, 40)) as pilot:
        app.on_radio("state", RadioState(connected=True, name="Mojeska",
                                         channels=[Channel(0, "Public"), Channel(1, "#test")]))
        await pilot.pause()
        await pilot.pause()

        # [+] on the log: log and command line fill the screen; Esc puts it back.
        await pilot.click(offset=_click_spot(app, "#log", "[+]"))
        await pilot.pause()
        assert app.screen.maximized is app.query_one("#center")
        assert app.query_one("#tx", Input).has_focus
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.maximized is None

        # [−] on status folds it to its title line; [+] opens it again.
        await pilot.click(offset=_click_spot(app, "#status", "[−]"))
        await pilot.pause()
        status = app.query_one("#status")
        assert status.has_class("-folded") and status.region.height == 1
        assert app.settings["folded"] == ["status"]
        await pilot.click(offset=_click_spot(app, "#status", "[+]"))
        await pilot.pause()
        assert not status.has_class("-folded") and status.region.height > 1

        # [−] on the list collapses its column to a strip; clicking the strip brings it back.
        await pilot.click(offset=_click_spot(app, "#convs", "[−]"))
        await pilot.pause()
        assert not app.query_one("#left").display and app.query_one("#left-strip").display
        await pilot.click("#left-strip")
        await pilot.pause()
        assert app.query_one("#left").display and not app.query_one("#left-strip").display

        # A maximized list goes back to normal once a conversation is picked.
        await pilot.click(offset=_click_spot(app, "#convs", "[+]"))
        await pilot.pause()
        assert app.screen.maximized is app.query_one("#convs")
        convs = app.query_one("#convs")
        convs.highlighted = convs.get_option_index("chan:1")
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.maximized is None and app.current == "chan:1"

        app.action_frame("events", "min")
    again = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with again.run_test(size=(120, 40)):
        assert again.query_one("#events").has_class("-folded")  # remembered


async def test_titles_keep_their_buttons_when_narrow(tmp_path):
    app = LuchApp("MeshCore-Test", tmp_path / "t.db", offline=True)
    async with app.run_test(size=(80, 30)) as pilot:
        app.on_radio("state", RadioState(connected=True, name="Mojeska",
                                         channels=[Channel(0, "A very long channel name [x] indeed")]))
        await pilot.pause()
        await pilot.pause()
        rows = app.screen._compositor.render_strips()
        for wid in ("#convs", "#log", "#status"):
            w = app.query_one(wid)
            assert "[+]" in rows[w.region.y].text[w.region.x:w.region.right], wid


def test_backup_and_reset_commands(tmp_path, monkeypatch, capsys):
    import pytest
    from luch.app import backup_log, reset_log
    from luch.store import Store
    db = tmp_path / "mynode.db"
    s = Store(db)
    s.add("chan:0", "in", "Bob", "hi", 1)
    s.close()
    out = backup_log(db, None)
    assert out.parent == tmp_path / "backups" and Store(out).count() == 1
    monkeypatch.setattr("builtins.input", lambda _: "no")
    with pytest.raises(SystemExit):
        reset_log(db)
    assert Store(db).count() == 1
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    reset_log(db)
    assert Store(db).count() == 0
    assert len(list((tmp_path / "backups").glob("mynode-*.db"))) >= 1
    with pytest.raises(SystemExit, match="--db"):
        backup_log(tmp_path / "nope.db", None)
