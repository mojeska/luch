from luch.radio import split_channel_text
from luch.store import Store


def test_add_dedup_and_history():
    s = Store(":memory:")
    a = s.add("chan:0", "in", "Bob", "hi", 100, snr=5.0, path_len=2)
    assert a and s.add("chan:0", "in", "Bob", "hi", 100) is None  # same message seen twice
    s.add("chan:0", "out", "Me", "yo", 101, status="sent")
    assert [m.text for m in s.history("chan:0")] == ["hi", "yo"]


def test_unread_and_mark_read():
    s = Store(":memory:")
    s.add("chan:0", "in", "Bob", "one", 1)
    s.add("chan:0", "in", "Bob", "two", 2)
    s.add("chan:0", "out", "Me", "mine", 3)
    assert s.unread("chan:0") == 2
    s.mark_read("chan:0")
    assert s.unread("chan:0") == 0
    s.add("chan:0", "in", "Bob", "three", 4)
    assert s.unread("chan:0") == 1


def test_ack_updates_status():
    s = Store(":memory:")
    mid = s.add("dm:abc", "out", "Me", "ping", 5, status="sending")
    s.set_status(mid, "sent", "deadbeef")
    assert s.set_status_by_ack("deadbeef", "delivered") == mid
    assert s.get(mid).status == "delivered"


def test_search():
    s = Store(":memory:")
    s.add("chan:0", "in", "Bob", "weather is fine", 1)
    s.add("chan:1", "in", "Ann", "printer jammed", 2)
    assert [m.sender for m in s.search("printer")] == ["Ann"]


def test_split_channel_text():
    assert split_channel_text("Bob: hello: world") == ("Bob", "hello: world")
    assert split_channel_text("no sender here") == ("?", "no sender here")


def test_backup_copies_everything(tmp_path):
    s = Store(tmp_path / "live.db")
    s.add("chan:0", "in", "Bob", "hi", 1)
    s.add_tag("dm:abc", "family")
    s.backup(tmp_path / "copy.db")
    copy = Store(tmp_path / "copy.db")
    assert [m.text for m in copy.history("chan:0")] == ["hi"]
    assert copy.tags_for("dm:abc") == ["family"]


def test_reset_clears_but_keeps_numbering(tmp_path):
    s = Store(tmp_path / "live.db")
    for i in range(3):
        s.add("dm:abc", "in", "Bob", f"m{i}", i)
    s.set_title("dm:abc", "Bob")
    s.add_tag("dm:abc", "family")
    other = Store(tmp_path / "live.db")  # e.g. the logger, still holding the file open
    assert s.reset() == 3
    assert s.count() == 0 and s.known_dms() == [] and s.all_tags() == {}
    assert other.add("dm:abc", "in", "Bob", "after", 9) == 4  # screens fetching ids > 3 still see it
    assert [m.text for m in s.since(3)] == ["after"]


def test_reset_upgrades_an_old_log(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "old.db")
    old.executescript(SCHEMA_V1)
    old.execute("INSERT INTO messages (conv, direction, sender, text, sender_ts, rx_ts) "
                "VALUES ('chan:0', 'in', 'Bob', 'x', 1, 1)")
    old.commit()
    old.close()
    s = Store(tmp_path / "old.db")
    s.reset()
    assert s.add("chan:0", "in", "Bob", "y", 2) == 2


SCHEMA_V1 = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, conv TEXT NOT NULL, direction TEXT NOT NULL, sender TEXT NOT NULL,
    text TEXT NOT NULL, sender_ts INTEGER NOT NULL, rx_ts INTEGER NOT NULL, snr REAL, rssi INTEGER,
    path_len INTEGER, status TEXT NOT NULL DEFAULT '', ack TEXT, UNIQUE (conv, sender, sender_ts, text));
"""
