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
