"""
Timeline
--------
Merges messages, calls, reactions and edits from every chat into one
chronological (UTC) list, optionally filtered by chat, text or date range.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .db_parser import MIN_TS, fmt_ts


@dataclass
class TimelineEvent:
    timestamp: Optional[datetime]
    kind: str          # message | call | reaction | edit
    chat_jid: str
    chat_name: str
    who: str
    summary: str


def build_timeline(chats, calls=(), reactions=(), edits=(), names=None) -> list[TimelineEvent]:
    names = names or {c.jid: c.display_name for c in chats}
    ev = []
    for chat in chats:
        for m in chat.messages:
            body = m.text or (f"[media {m.media_type}]" if m.media_type and m.media_relative_path else "")
            if m.is_deleted:
                body = (body + " " if body else "") + "[deleted for everyone]"
            ev.append(TimelineEvent(m.timestamp, "message", chat.jid, chat.display_name,
                                    "Me" if m.from_me else chat.display_name, body))
    for c in calls:
        label = {"IN": "Incoming call", "OUT": "Outgoing call", "MISSED": "Missed/rejected call",
                 "UNANSWERED": "Unanswered outgoing call"}.get(c.call_type, c.call_type)
        dur = f" ({c.duration_seconds // 60}:{c.duration_seconds % 60:02d})" if c.duration_seconds else ""
        ev.append(TimelineEvent(c.timestamp, "call", c.contact_jid, names.get(c.contact_jid, c.contact_jid),
                                c.phone_number, ("Video " if c.is_video else "") + label + dur))
    for r in reactions:
        ev.append(TimelineEvent(r.timestamp, "reaction", r.chat_jid, names.get(r.chat_jid, r.chat_jid),
                                r.reactor_jid, f"{r.emoji} on “{r.target_text[:60]}”"))
    for e in edits:
        ev.append(TimelineEvent(e.edited_at, "edit", e.chat_jid, names.get(e.chat_jid, e.chat_jid),
                                "", f"message edited (now: “{e.current_text[:80]}”)"))
    ev.sort(key=lambda x: x.timestamp or MIN_TS)
    return ev


def filter_timeline(events, text: str = "", chat: str = "", kinds=None,
                    start: Optional[datetime] = None, end: Optional[datetime] = None):
    text, chat = text.lower().strip(), chat.lower().strip()
    out = []
    for e in events:
        if kinds and e.kind not in kinds:
            continue
        if chat and chat not in e.chat_name.lower() and chat not in e.chat_jid.lower():
            continue
        if text and text not in e.summary.lower() and text not in e.who.lower():
            continue
        if start and (not e.timestamp or e.timestamp < start):
            continue
        if end and (not e.timestamp or e.timestamp > end):
            continue
        out.append(e)
    return out


def _csv_safe(v: str) -> str:
    return "'" + v if v and v[0] in "=+-@\t\r" else v


def export_timeline_csv(events, path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Time (UTC)", "Type", "Chat", "Who", "Summary"])
        for e in events:
            w.writerow([fmt_ts(e.timestamp, seconds=True, empty=""), e.kind,
                        _csv_safe(e.chat_name), _csv_safe(e.who), _csv_safe(e.summary)])
