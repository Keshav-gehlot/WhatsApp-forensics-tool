"""
DB Parser
---------
Reads the decrypted msgstore.db (messages, chats, calls) and wa.db
(contacts) SQLite files and exposes clean Python structures for the UI
and export layers.

WhatsApp's schema has shifted across versions (message -> message_view,
jid table introduced, etc). This module probes for the schema variant
present and adapts, rather than assuming one fixed layout.
"""

import sqlite3
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Message:
    chat_jid: str
    from_me: bool
    text: str
    timestamp: datetime
    media_type: Optional[str] = None
    media_relative_path: Optional[str] = None   # e.g. "Media/WhatsApp Images/IMG-...WA0003.jpg"
    media_mime_type: Optional[str] = None
    thumbnail_blob: Optional[bytes] = None      # low-res JPEG stored inline in msgstore.db,
                                                 # independent of whether the full file survives
    is_deleted: bool = False


@dataclass
class Chat:
    jid: str
    display_name: str
    messages: list = field(default_factory=list)
    unread_count: int = 0


@dataclass
class CallRecord:
    contact_jid: str
    phone_number: str
    call_type: str       # IN / OUT / MISSED
    is_video: bool
    duration_seconds: int
    timestamp: datetime


class WaDatabase:
    def __init__(self, msgstore_path: str, wa_db_path: Optional[str] = None):
        if not os.path.exists(msgstore_path):
            raise FileNotFoundError(msgstore_path)
        self.msgstore_path = msgstore_path
        self.wa_db_path = wa_db_path
        self._conn = sqlite3.connect(f"file:{msgstore_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        # This connection only ever reads — msgstore.db can be tens of
        # thousands of rows on an active account. A larger page cache and
        # in-memory temp store measurably speed up the ORDER BY/JOIN
        # queries below versus sqlite's small defaults, and query_only
        # is a hard guarantee against ever accidentally writing to
        # evidence (belt-and-suspenders alongside the mode=ro URI, which
        # already prevents writes at the OS/file level).
        self._conn.execute("PRAGMA query_only = ON")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._conn.execute("PRAGMA cache_size = -16000")  # ~16 MB page cache
        self._contact_names = self._load_contact_names() if wa_db_path else {}
        self._schema = self._detect_schema()

    # -- schema detection -------------------------------------------------

    def _table_exists(self, name: str) -> bool:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return cur.fetchone() is not None

    def _table_columns(self, table: str) -> set:
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}

    def _detect_schema(self) -> str:
        if self._table_exists("message") and self._table_exists("jid"):
            return "modern"       # WhatsApp ~2.21+ (message/jid/chat tables)
        elif self._table_exists("messages"):
            return "legacy"       # older single `messages` table
        else:
            raise RuntimeError(
                "Unrecognized msgstore schema — no `message`/`jid` or "
                "`messages` tables found. This build may target a newer "
                "or older WhatsApp version than this parser supports."
            )

    def _load_contact_names(self) -> dict:
        names = {}
        try:
            conn = sqlite3.connect(f"file:{self.wa_db_path}?mode=ro", uri=True)
            cur = conn.execute("SELECT jid, display_name FROM wa_contacts")
            for row in cur.fetchall():
                if row[1]:
                    names[row[0]] = row[1]
            conn.close()
        except sqlite3.OperationalError:
            pass
        return names

    def contact_name(self, jid: str) -> str:
        return self._contact_names.get(jid, jid)

    # -- messages -----------------------------------------------------------

    def get_chats(self, limit: int = 500) -> list[Chat]:
        chats = {}
        row_id_to_message = {}

        if self._schema == "modern":
            query = """
                SELECT m._id AS row_id, j.raw_string AS jid,
                       m.from_me, m.text_data, m.timestamp,
                       m.message_type
                FROM message m
                JOIN chat c ON m.chat_row_id = c._id
                JOIN jid j ON c.jid_row_id = j._id
                ORDER BY m.timestamp ASC
                LIMIT ?
            """
        else:
            legacy_cols = self._table_columns("messages")
            media_col = next((c for c in ("media_name", "media_url", "_data") if c in legacy_cols), None)
            media_select = f", {media_col} AS media_path" if media_col else ", NULL AS media_path"
            thumb_col = next((c for c in ("thumb_image", "thumbnail") if c in legacy_cols), None)
            thumb_select = f", {thumb_col} AS thumb_blob" if thumb_col else ", NULL AS thumb_blob"
            query = f"""
                SELECT key_remote_jid AS jid, key_from_me AS from_me,
                       data AS text_data, timestamp, media_wa_type AS message_type
                       {media_select}{thumb_select}
                FROM messages
                ORDER BY timestamp ASC
                LIMIT ?
            """

        cur = self._conn.execute(query, (limit,))
        for row in cur.fetchall():
            jid = row["jid"]
            if jid not in chats:
                chats[jid] = Chat(jid=jid, display_name=self.contact_name(jid))
            ts = datetime.fromtimestamp(row["timestamp"] / 1000.0) if row["timestamp"] else None
            msg = Message(
                chat_jid=jid,
                from_me=bool(row["from_me"]),
                text=row["text_data"] or "",
                timestamp=ts,
                media_type=str(row["message_type"]) if row["message_type"] else None,
            )
            if self._schema == "legacy":
                keys = row.keys()
                if "media_path" in keys and row["media_path"]:
                    msg.media_relative_path = row["media_path"]
                if "thumb_blob" in keys and row["thumb_blob"]:
                    msg.thumbnail_blob = row["thumb_blob"]
            chats[jid].messages.append(msg)
            if self._schema == "modern":
                row_id_to_message[row["row_id"]] = msg

        # Modern schema stores media file references (and, on some
        # versions, an inline thumbnail BLOB) in a separate table keyed by
        # message row id — resolve those onto the Message objects we just
        # built. The thumbnail blob is independent of media_relative_path:
        # it can be present and readable even when the full-resolution
        # file it once pointed to no longer exists anywhere on disk.
        if self._schema == "modern" and self._table_exists("message_media"):
            media_cols = self._table_columns("message_media")
            path_col = next((c for c in ("file_path", "media_name") if c in media_cols), None)
            mime_col = "mime_type" if "mime_type" in media_cols else None
            thumb_col = next((c for c in ("thumbnail", "thumb_image") if c in media_cols), None)
            if path_col or thumb_col:
                select_parts = ["message_row_id"]
                select_parts.append(path_col if path_col else "NULL")
                select_parts.append(mime_col if mime_col else "NULL")
                select_parts.append(thumb_col if thumb_col else "NULL")
                cur2 = self._conn.execute(f"SELECT {', '.join(select_parts)} FROM message_media")
                for r in cur2.fetchall():
                    msg = row_id_to_message.get(r[0])
                    if msg is not None:
                        if r[1]:
                            msg.media_relative_path = r[1]
                        if r[2]:
                            msg.media_mime_type = r[2]
                        if r[3]:
                            msg.thumbnail_blob = r[3]

        return list(chats.values())

    # -- calls ---------------------------------------------------------------

    def get_calls(self) -> list[CallRecord]:
        if not self._table_exists("call_log"):
            return []
        if self._schema == "modern":
            cur = self._conn.execute("""
                SELECT j.raw_string AS jid, cl.from_me, cl.video_call,
                       cl.duration, cl.timestamp
                FROM call_log cl
                JOIN jid j ON cl.jid_row_id = j._id
                ORDER BY cl.timestamp DESC
            """)
        else:
            cur = self._conn.execute("""
                SELECT key_remote_jid AS jid, from_me, video_call,
                       duration, timestamp
                FROM call_log_legacy
            """)

        results = []
        for row in cur.fetchall():
            try:
                jid = row["jid"]
                from_me = bool(row["from_me"])
                duration = row["duration"] or 0
                call_type = "MISSED" if duration == 0 and not from_me else (
                    "OUT" if from_me else "IN"
                )
                ts = datetime.fromtimestamp(row["timestamp"] / 1000.0) if row["timestamp"] else None
                results.append(CallRecord(
                    contact_jid=jid,
                    phone_number=jid.split("@")[0] if jid else "unknown",
                    call_type=call_type,
                    is_video=bool(row["video_call"]),
                    duration_seconds=duration,
                    timestamp=ts,
                ))
            except (IndexError, KeyError):
                continue
        return results

    def close(self):
        self._conn.close()
