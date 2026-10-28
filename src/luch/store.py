"""SQLite log of every message LUCH sends or receives."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT, -- never reused, so a reset can't confuse screens
    conv        TEXT    NOT NULL,           -- 'chan:<idx>' or 'dm:<pubkey prefix>'
    direction   TEXT    NOT NULL,           -- 'in' or 'out'
    sender      TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    sender_ts   INTEGER NOT NULL,           -- sender's clock (unix seconds)
    rx_ts       INTEGER NOT NULL,           -- our clock when logged
    snr         REAL,
    rssi        INTEGER,
    path_len    INTEGER,                    -- hops; -1 = flood
    status      TEXT    NOT NULL DEFAULT '',-- out: sent / delivered / failed
    ack         TEXT,
    UNIQUE (conv, sender, sender_ts, text)
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages (conv, id);
CREATE INDEX IF NOT EXISTS messages_ack ON messages (ack);

CREATE TABLE IF NOT EXISTS tags (
    conv TEXT NOT NULL,                     -- 'dm:<pubkey prefix>'
    tag  TEXT NOT NULL,
    UNIQUE (conv, tag)
);

CREATE TABLE IF NOT EXISTS conversations (
    conv         TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    last_read_id INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Message:
    id: int
    conv: str
    direction: str
    sender: str
    text: str
    sender_ts: int
    rx_ts: int
    snr: float | None
    rssi: int | None
    path_len: int | None
    status: str


def default_db_path() -> Path:
    return Path.home() / ".local" / "share" / "luch" / "luch.db"


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def backup(self, dest: Path | str) -> None:
        """Write a complete copy to one file. Safe while the logger is writing."""
        out = sqlite3.connect(str(dest))
        try:
            self.db.backup(out)
        finally:
            out.close()

    def reset(self) -> int:
        """Forget every message, tag and conversation. Returns how many messages went.
        New messages keep numbering on from the old ones, since screens and remote copies
        fetch by id and would skip anything below the last id they saw."""
        gone, top = self.count(), self.max_id()
        self.db.executescript(f"""
            BEGIN IMMEDIATE;
            DROP TABLE messages;
            DELETE FROM tags;
            DELETE FROM conversations;
            {SCHEMA}
            DELETE FROM sqlite_sequence WHERE name = 'messages';
            INSERT INTO sqlite_sequence (name, seq) VALUES ('messages', {int(top)});
            COMMIT;
        """)
        try:  # drop the deleted text from the file too; skipped if another screen is mid-read
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        return gone

    def set_title(self, conv: str, title: str) -> None:
        self.db.execute(
            "INSERT INTO conversations (conv, title) VALUES (?, ?) "
            "ON CONFLICT(conv) DO UPDATE SET title = excluded.title",
            (conv, title),
        )
        self.db.commit()

    def add(
        self,
        conv: str,
        direction: str,
        sender: str,
        text: str,
        sender_ts: int | None = None,
        snr: float | None = None,
        rssi: int | None = None,
        path_len: int | None = None,
        status: str = "",
        ack: str | None = None,
    ) -> int | None:
        """Log a message. Returns its id, or None if it was a duplicate."""
        now = int(time.time())
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages "
            "(conv, direction, sender, text, sender_ts, rx_ts, snr, rssi, path_len, status, ack) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conv, direction, sender, text, sender_ts or now, now, snr, rssi, path_len, status, ack),
        )
        self.db.commit()
        return cur.lastrowid if cur.rowcount else None

    def set_status_by_ack(self, ack: str, status: str) -> int | None:
        row = self.db.execute("SELECT id FROM messages WHERE ack = ?", (ack,)).fetchone()
        if not row:
            return None
        self.db.execute("UPDATE messages SET status = ? WHERE id = ?", (status, row["id"]))
        self.db.commit()
        return row["id"]

    def set_status(self, msg_id: int, status: str, ack: str | None = None) -> None:
        self.db.execute(
            "UPDATE messages SET status = ?, ack = COALESCE(?, ack) WHERE id = ?",
            (status, ack, msg_id),
        )
        self.db.commit()

    def history(self, conv: str, limit: int = 500) -> list[Message]:
        rows = self.db.execute(
            "SELECT * FROM (SELECT * FROM messages WHERE conv = ? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id",
            (conv, limit),
        ).fetchall()
        return [self._msg(r) for r in rows]

    def get(self, msg_id: int) -> Message | None:
        row = self.db.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return self._msg(row) if row else None

    def search(self, needle: str, limit: int = 200) -> list[Message]:
        rows = self.db.execute(
            "SELECT * FROM messages WHERE text LIKE ? OR sender LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{needle}%", f"%{needle}%", limit),
        ).fetchall()
        return [self._msg(r) for r in reversed(rows)]

    def unread(self, conv: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM messages m "
            "LEFT JOIN conversations c ON c.conv = m.conv "
            "WHERE m.conv = ? AND m.direction = 'in' AND m.id > COALESCE(c.last_read_id, 0)",
            (conv,),
        ).fetchone()
        return row[0]

    def mark_read(self, conv: str) -> None:
        top = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE conv = ?", (conv,)).fetchone()[0]
        self.db.execute(
            "INSERT INTO conversations (conv, title, last_read_id) VALUES (?, ?, ?) "
            "ON CONFLICT(conv) DO UPDATE SET last_read_id = excluded.last_read_id",
            (conv, conv, top),
        )
        self.db.commit()

    def known_dms(self) -> list[tuple[str, str]]:
        """DM conversations seen before, as (conv, title), even if the radio forgot the contact."""
        rows = self.db.execute(
            "SELECT conv, title FROM conversations WHERE conv LIKE 'dm:%' ORDER BY title"
        ).fetchall()
        return [(r["conv"], r["title"]) for r in rows]

    def dm_convs_with_traffic(self) -> set[str]:
        rows = self.db.execute("SELECT DISTINCT conv FROM messages WHERE conv LIKE 'dm:%'").fetchall()
        return {r[0] for r in rows}

    def add_tag(self, conv: str, tag: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO tags (conv, tag) VALUES (?, ?)", (conv, tag.lower()))
        self.db.commit()

    def remove_tag(self, conv: str, tag: str) -> bool:
        cur = self.db.execute("DELETE FROM tags WHERE conv = ? AND tag = ?", (conv, tag.lower()))
        self.db.commit()
        return cur.rowcount > 0

    def tags_for(self, conv: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT tag FROM tags WHERE conv = ? ORDER BY tag", (conv,))]

    def all_tags(self) -> dict[str, list[str]]:
        """tag -> conversations carrying it."""
        out: dict[str, list[str]] = {}
        for conv, tag in self.db.execute("SELECT conv, tag FROM tags ORDER BY tag, conv"):
            out.setdefault(tag, []).append(conv)
        return out

    def tagged_convs(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT conv FROM tags")}

    def put(self, m: dict) -> None:
        """Copy a message row from another machine's log, keeping its id."""
        self.db.execute(
            "INSERT OR REPLACE INTO messages "
            "(id, conv, direction, sender, text, sender_ts, rx_ts, snr, rssi, path_len, status) "
            "VALUES (:id, :conv, :direction, :sender, :text, :sender_ts, :rx_ts, :snr, :rssi, :path_len, :status)",
            m,
        )
        self.db.commit()

    def since(self, msg_id: int) -> list[Message]:
        rows = self.db.execute("SELECT * FROM messages WHERE id > ? ORDER BY id", (msg_id,)).fetchall()
        return [self._msg(r) for r in rows]

    def titles(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self.db.execute("SELECT conv, title FROM conversations")]

    def max_id(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    @staticmethod
    def _msg(r: sqlite3.Row) -> Message:
        return Message(
            r["id"], r["conv"], r["direction"], r["sender"], r["text"], r["sender_ts"],
            r["rx_ts"], r["snr"], r["rssi"], r["path_len"], r["status"],
        )
