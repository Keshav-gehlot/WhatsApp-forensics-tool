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
from datetime import datetime, timezone
from typing import Optional


UTC = timezone.utc
MIN_TS = datetime.min.replace(tzinfo=UTC)   # sort key for rows with no timestamp

# message_type value WhatsApp uses for "message deleted for everyone"
# (revoked). Heuristic from observed schemas — verify on your samples.
REVOKED_MESSAGE_TYPE = 15


def fmt_ts(dt, seconds=False, empty="unknown time"):
    """Format an aware-UTC datetime for display/export, always labelled."""
    if not dt:
        return empty
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC" if seconds else "%Y-%m-%d %H:%M UTC")


def ms_to_utc(ms):
    """Epoch milliseconds -> timezone-aware UTC datetime (None if empty).
    All timestamps in this tool are UTC so exports are reproducible
    regardless of the examiner's machine timezone."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


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
    is_deleted: bool = False                     # deleted-for-everyone marker (see REVOKED_MESSAGE_TYPE)


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
    result_code: Optional[int] = None   # raw call_result from the DB when present (meaning varies by version)


@dataclass
class Reaction:
    chat_jid: str
    target_text: str          # text of the message that was reacted to (may be empty for media)
    reactor_jid: str          # "me" when from_me
    emoji: str
    timestamp: Optional[datetime]


@dataclass
class EditRecord:
    chat_jid: str
    current_text: str
    edited_at: Optional[datetime]
    original_sent_at: Optional[datetime]


@dataclass
class Participant:
    group_jid: str
    group_name: str
    member_jid: str
    role: str                 # "member" | "admin" | "superadmin" | "unknown"


STATUS_JID = "status@broadcast"


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

    def get_chats(self, limit: Optional[int] = None) -> list[Chat]:
        """Returns ALL chats and messages by default. `limit` (if given)
        caps the total number of message rows read, oldest first — only
        pass it for quick previews, never for evidence review/export."""
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
                {limit_clause}
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
                {{limit_clause}}
            """

        limit_clause = "LIMIT ?" if limit else ""
        # (the legacy query is an f-string already; both carry the literal
        # {limit_clause} placeholder, filled here)
        query = query.replace("{limit_clause}", limit_clause)
        cur = self._conn.execute(query, (limit,) if limit else ())
        for row in cur.fetchall():
            jid = row["jid"]
            if jid not in chats:
                chats[jid] = Chat(jid=jid, display_name=self.contact_name(jid))
            ts = ms_to_utc(row["timestamp"])
            msg = Message(
                chat_jid=jid,
                from_me=bool(row["from_me"]),
                text=row["text_data"] or "",
                timestamp=ts,
                media_type=str(row["message_type"]) if row["message_type"] else None,
                is_deleted=(row["message_type"] == REVOKED_MESSAGE_TYPE),
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

        # Thumbnails live in their own `message_thumbnails` table on modern
        # schemas (keyed by message_row_id) — pick those up too, without
        # overwriting a blob already found in message_media.
        if self._schema == "modern" and self._table_exists("message_thumbnails"):
            tcols = self._table_columns("message_thumbnails")
            if "thumbnail" in tcols and "message_row_id" in tcols:
                for r in self._conn.execute(
                        "SELECT message_row_id, thumbnail FROM message_thumbnails"):
                    msg = row_id_to_message.get(r[0])
                    if msg is not None and r[1] and not msg.thumbnail_blob:
                        msg.thumbnail_blob = r[1]

        return list(chats.values())

    # -- calls ---------------------------------------------------------------

    def get_calls(self) -> list[CallRecord]:
        if self._schema == "modern":
            if not self._table_exists("call_log"):
                return []
            cols = self._table_columns("call_log")
            result_sel = "cl.call_result" if "call_result" in cols else "NULL"
            cur = self._conn.execute(f"""
                SELECT j.raw_string AS jid, cl.from_me, cl.video_call,
                       cl.duration, cl.timestamp, {result_sel} AS call_result
                FROM call_log cl
                JOIN jid j ON cl.jid_row_id = j._id
                ORDER BY cl.timestamp DESC
            """)
        else:
            # Legacy layouts used `calls` (older) or a key_remote_jid-style
            # `call_log`. Use whichever exists with the columns we need.
            table = None
            for cand in ("calls", "call_log", "call_log_legacy"):
                if self._table_exists(cand) and "key_remote_jid" in self._table_columns(cand):
                    table = cand
                    break
            if table is None:
                return []
            cols = self._table_columns(table)
            from_me = "from_me" if "from_me" in cols else ("key_from_me" if "key_from_me" in cols else "0")
            result_sel = "call_result" if "call_result" in cols else "NULL"
            cur = self._conn.execute(f"""
                SELECT key_remote_jid AS jid, {from_me} AS from_me, video_call,
                       duration, timestamp, {result_sel} AS call_result
                FROM {table}
                ORDER BY timestamp DESC
            """)

        results = []
        for row in cur.fetchall():
            try:
                jid = row["jid"]
                from_me = bool(row["from_me"])
                duration = row["duration"] or 0
                # Only claim what the data supports: an incoming call with
                # no talk time is "MISSED" (missed OR rejected — the DB's
                # raw result_code is kept so an examiner can tell them
                # apart); an outgoing call with no talk time is
                # "UNANSWERED".
                if from_me:
                    call_type = "OUT" if duration else "UNANSWERED"
                else:
                    call_type = "IN" if duration else "MISSED"
                results.append(CallRecord(
                    contact_jid=jid,
                    phone_number=jid.split("@")[0] if jid else "unknown",
                    call_type=call_type,
                    is_video=bool(row["video_call"]),
                    duration_seconds=duration,
                    timestamp=ms_to_utc(row["timestamp"]),
                    result_code=row["call_result"],
                ))
            except (IndexError, KeyError):
                continue
        return results

    # -- extras: reactions, edits, participants, starred, status ---------
    # These tables vary a lot between WhatsApp releases. Each method probes
    # for the table/columns it needs and returns [] if they are absent, so a
    # missing table is "nothing found", never a crash. Table/column names
    # are from observed schemas — validate against your own samples.

    def _jid_map(self) -> dict:
        if not self._table_exists("jid"):
            return {}
        return {r[0]: r[1] for r in self._conn.execute("SELECT _id, raw_string FROM jid")}

    def _message_index(self) -> dict:
        """message _id -> (chat_jid, text, timestamp_ms) for the modern schema."""
        if self._schema != "modern":
            return {}
        out = {}
        for r in self._conn.execute(
                "SELECT m._id, j.raw_string, m.text_data, m.timestamp FROM message m "
                "JOIN chat c ON m.chat_row_id = c._id JOIN jid j ON c.jid_row_id = j._id"):
            out[r[0]] = (r[1], r[2] or "", r[3])
        return out

    def get_reactions(self) -> list[Reaction]:
        if not (self._table_exists("message_add_on") and self._table_exists("message_add_on_reaction")):
            return []
        ao = self._table_columns("message_add_on")
        parent = next((c for c in ("parent_message_row_id", "message_row_id") if c in ao), None)
        if not parent or "message_add_on_row_id" not in self._table_columns("message_add_on_reaction"):
            return []
        jids, msgs = self._jid_map(), self._message_index()
        sender = "a.sender_jid_row_id" if "sender_jid_row_id" in ao else "NULL"
        from_me = "a.from_me" if "from_me" in ao else "0"
        ts = "a.timestamp" if "timestamp" in ao else "NULL"
        rows = self._conn.execute(
            f"SELECT a.{parent}, {sender}, {from_me}, {ts}, r.reaction "
            "FROM message_add_on a JOIN message_add_on_reaction r ON r.message_add_on_row_id = a._id "
            "ORDER BY 4").fetchall()
        out = []
        for parent_id, sender_id, fm, t, emoji in rows:
            chat, text, _ = msgs.get(parent_id, ("", "", None))
            out.append(Reaction(chat_jid=chat, target_text=text,
                                reactor_jid="me" if fm else jids.get(sender_id, ""),
                                emoji=emoji or "", timestamp=ms_to_utc(t)))
        return out

    def get_edits(self) -> list[EditRecord]:
        if not self._table_exists("message_edit_info"):
            return []
        cols = self._table_columns("message_edit_info")
        if "message_row_id" not in cols:
            return []
        msgs = self._message_index()
        edited = "edited_timestamp" if "edited_timestamp" in cols else "NULL"
        sent = "sender_timestamp" if "sender_timestamp" in cols else "NULL"
        out = []
        for mid, e, snd in self._conn.execute(
                f"SELECT message_row_id, {edited}, {sent} FROM message_edit_info ORDER BY 2"):
            chat, text, _ = msgs.get(mid, ("", "", None))
            out.append(EditRecord(chat_jid=chat, current_text=text,
                                  edited_at=ms_to_utc(e), original_sent_at=ms_to_utc(snd)))
        return out

    def get_group_participants(self) -> list[Participant]:
        roles = {0: "member", 1: "admin", 2: "superadmin"}
        jids = self._jid_map()
        out = []
        if self._table_exists("group_participant_user"):
            cols = self._table_columns("group_participant_user")
            if {"group_jid_row_id", "user_jid_row_id"} <= cols:
                rank = "rank" if "rank" in cols else "NULL"
                for g, u, rk in self._conn.execute(
                        f"SELECT group_jid_row_id, user_jid_row_id, {rank} FROM group_participant_user"):
                    gj = jids.get(g, "")
                    out.append(Participant(gj, self.contact_name(gj), jids.get(u, ""),
                                           roles.get(rk, "unknown") if rk is not None else "unknown"))
        elif self._table_exists("group_participants"):          # legacy
            cols = self._table_columns("group_participants")
            if {"gjid", "jid"} <= cols:
                adm = "admin" if "admin" in cols else "NULL"
                for g, u, a in self._conn.execute(f"SELECT gjid, jid, {adm} FROM group_participants"):
                    out.append(Participant(g, self.contact_name(g), u or "",
                                           {0: "member", 1: "admin", 2: "superadmin"}.get(a, "unknown")))
        return out

    def get_starred(self) -> list[Message]:
        """Starred messages (modern: a starred-message table or a `starred`
        column on `message`; legacy: `messages.starred`)."""
        if self._schema == "modern":
            ids = set()
            for t in ("starred_message", "message_starred"):
                if self._table_exists(t) and "message_row_id" in self._table_columns(t):
                    ids |= {r[0] for r in self._conn.execute(f"SELECT message_row_id FROM {t}")}
            if "starred" in self._table_columns("message"):
                ids |= {r[0] for r in self._conn.execute("SELECT _id FROM message WHERE starred = 1")}
            idx = self._message_index()
            return [Message(chat_jid=idx[i][0], from_me=False, text=idx[i][1],
                            timestamp=ms_to_utc(idx[i][2])) for i in sorted(ids) if i in idx]
        if "starred" in self._table_columns("messages"):
            return [Message(chat_jid=r[0], from_me=bool(r[1]), text=r[2] or "", timestamp=ms_to_utc(r[3]))
                    for r in self._conn.execute(
                        "SELECT key_remote_jid, key_from_me, data, timestamp FROM messages WHERE starred = 1")]
        return []

    def get_status_updates(self) -> list[Message]:
        """Status (story) posts and views — stored as messages in the
        `status@broadcast` chat."""
        for chat in self.get_chats():
            if chat.jid == STATUS_JID:
                return chat.messages
        return []

    def close(self):
        self._conn.close()
