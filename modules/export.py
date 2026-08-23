"""
Export
------
Renders parsed chats/calls to court-referenceable formats:
  - TXT   (plain chat-format transcript)
  - XLSX  (tabular, sortable — good for indexing/searching)
  - HTML  (WhatsApp-style bubble rendering, for readability in a report)
  - PDF   (optional; requires the pdf skill / a PDF engine if wkhtmltopdf
           or a headless browser isn't available on the examiner's machine
           — this module produces the HTML and leaves final PDF rendering
           to whatever engine you wire up, since that's environment-specific)
"""

import os
import html
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from .db_parser import Chat, CallRecord

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".3gp", ".mov", ".mkv"}
AUDIO_EXTS = {".opus", ".mp3", ".m4a", ".aac", ".wav"}


def export_chat_txt(chat: Chat, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"Chat with: {chat.display_name} ({chat.jid})\n")
        f.write(f"Exported: {datetime.now().isoformat()}\n")
        f.write("=" * 60 + "\n\n")
        for m in chat.messages:
            who = "Me" if m.from_me else chat.display_name
            ts = m.timestamp.strftime("%Y-%m-%d %H:%M:%S") if m.timestamp else "unknown time"
            f.write(f"[{ts}] {who}: {m.text}\n")


def export_chats_xlsx(chats: list[Chat], output_path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Messages"
    ws.append(["Chat", "From", "Message", "Timestamp", "Media Type"])
    for chat in chats:
        for m in chat.messages:
            ws.append([
                chat.display_name,
                "Me" if m.from_me else chat.display_name,
                m.text,
                m.timestamp.isoformat() if m.timestamp else "",
                m.media_type or "",
            ])
    wb.save(output_path)


def export_calls_xlsx(calls: list[CallRecord], output_path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Call Log"
    ws.append(["Contact/Number", "Type", "Video", "Duration (s)", "Timestamp"])
    for c in calls:
        ws.append([
            c.phone_number,
            c.call_type,
            "Yes" if c.is_video else "No",
            c.duration_seconds,
            c.timestamp.isoformat() if c.timestamp else "",
        ])
    wb.save(output_path)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Chat Export — {chat_name}</title>
<style>
  body {{ background:#e5ddd5; font-family: -apple-system, Segoe UI, sans-serif; margin:0; padding:20px; }}
  .header {{ background:#075e54; color:white; padding:14px 20px; border-radius:8px 8px 0 0; }}
  .chat-container {{ max-width:700px; margin:0 auto; background:white; border-radius:0 0 8px 8px; padding:16px; }}
  .bubble {{ max-width:70%; padding:8px 12px; border-radius:8px; margin:6px 0; font-size:14px; line-height:1.4; clear:both; }}
  .me {{ background:#dcf8c6; float:right; }}
  .them {{ background:#ffffff; border:1px solid #eee; float:left; }}
  .meta {{ font-size:11px; color:#888; margin-top:4px; }}
  .clearfix {{ clear:both; }}
  .media-missing {{ font-size:12px; color:#a55; background:#fff3f3; border:1px dashed #e0b4b4;
                     border-radius:6px; padding:6px 8px; margin-top:6px; }}
  .call-marker {{ clear:both; text-align:center; margin:14px auto; }}
  .call-marker span {{ display:inline-block; background:#e3f2fd; color:#2a6f97; font-size:12px;
                        padding:5px 14px; border-radius:14px; }}
  img, video {{ max-width:280px; border-radius:8px; display:block; margin-top:6px; }}
  audio {{ display:block; margin-top:6px; max-width:280px; }}
</style></head>
<body>
<div class="header"><strong>{chat_name}</strong><br><span style="font-size:12px;">{jid}</span></div>
<div class="chat-container">
{messages}
<div class="clearfix"></div>
</div>
</body></html>
"""

_BUBBLE_TEMPLATE = """<div class="bubble {cls}">{text}<div class="meta">{ts}</div></div><div class="clearfix"></div>"""
_CALL_MARKER_TEMPLATE = """<div class="call-marker"><span>{icon} {label}{duration}</span><div class="meta">{ts}</div></div>"""


def _media_snippet(local_path, relative_path) -> str:
    """Embed the actual file if we resolved a local copy of it; otherwise
    say plainly that the reference exists but wasn't recovered locally."""
    if not relative_path:
        return ""
    if not local_path:
        return (f'<div class="media-missing">📎 media referenced '
                 f'({html.escape(os.path.basename(relative_path))}) — not recovered locally</div>')
    uri = Path(local_path).resolve().as_uri()
    ext = os.path.splitext(local_path)[1].lower()
    if ext in IMAGE_EXTS:
        return f'<img src="{uri}" alt="media">'
    elif ext in VIDEO_EXTS:
        return f'<video controls><source src="{uri}"></video>'
    elif ext in AUDIO_EXTS:
        return f'<audio controls><source src="{uri}"></audio>'
    else:
        return f'<a href="{uri}">📎 {html.escape(os.path.basename(local_path))}</a>'


def _call_marker(call: CallRecord) -> str:
    icon = "📹" if call.is_video else "📞"
    label = {"IN": "Incoming call", "OUT": "Outgoing call", "MISSED": "Missed call"}.get(
        call.call_type, call.call_type
    )
    duration = ""
    if call.duration_seconds:
        duration = f" · {call.duration_seconds // 60}:{call.duration_seconds % 60:02d}"
    ts = call.timestamp.strftime("%Y-%m-%d %H:%M") if call.timestamp else ""
    return _CALL_MARKER_TEMPLATE.format(icon=icon, label=label, duration=duration, ts=ts)


def export_chat_html(chat: Chat, output_path: str, media_index=None):
    rows = []
    for m in chat.messages:
        cls = "me" if m.from_me else "them"
        ts = m.timestamp.strftime("%Y-%m-%d %H:%M") if m.timestamp else ""
        text_html = html.escape(m.text).replace("\n", "<br>") if m.text else ""
        local_path = media_index.resolve(m.media_relative_path) if media_index else None
        media_html = _media_snippet(local_path, m.media_relative_path)
        rows.append(_BUBBLE_TEMPLATE.format(cls=cls, text=text_html + media_html, ts=ts))
    page = _HTML_TEMPLATE.format(
        chat_name=html.escape(chat.display_name),
        jid=html.escape(chat.jid),
        messages="\n".join(rows),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)


def export_reconstructed_chat(chat: Chat, calls: list[CallRecord], output_path: str, media_index=None):
    """
    'Recreate the whole chat': merges every message AND every call with
    this contact into one chronological timeline, with media embedded
    inline wherever a local copy was resolved (extracted Media folder or
    a file recovered via the filesystem scanner). This is the full
    activity reconstruction, not just the text transcript.
    """
    calls_for_chat = [c for c in calls if c.contact_jid == chat.jid]

    events = []
    for m in chat.messages:
        events.append((m.timestamp or datetime.min, "message", m))
    for c in calls_for_chat:
        events.append((c.timestamp or datetime.min, "call", c))
    events.sort(key=lambda e: e[0])

    rows = []
    for ts, kind, payload in events:
        if kind == "message":
            m = payload
            cls = "me" if m.from_me else "them"
            ts_str = m.timestamp.strftime("%Y-%m-%d %H:%M") if m.timestamp else ""
            text_html = html.escape(m.text).replace("\n", "<br>") if m.text else ""
            local_path = media_index.resolve(m.media_relative_path) if media_index else None
            media_html = _media_snippet(local_path, m.media_relative_path)
            rows.append(_BUBBLE_TEMPLATE.format(cls=cls, text=text_html + media_html, ts=ts_str))
        else:
            rows.append(_call_marker(payload))

    page = _HTML_TEMPLATE.format(
        chat_name=html.escape(chat.display_name) + " — full reconstruction",
        jid=html.escape(chat.jid),
        messages="\n".join(rows),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)


def export_all(chats: list[Chat], calls: list[CallRecord], output_dir: str,
               formats: list[str], media_index=None):
    """
    formats: any combination of "txt", "xlsx", "html", "calls_xlsx", "reconstruct"
    media_index: an optional media_resolver.MediaIndex used to embed real
                 media inline for "html" and "reconstruct" formats.
    Returns a dict of format -> list of written file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    written = {fmt: [] for fmt in formats}

    for chat in chats:
        safe_name = "".join(c for c in chat.display_name if c.isalnum() or c in " _-")[:50] or "chat"
        if "txt" in formats:
            p = os.path.join(output_dir, f"{safe_name}.txt")
            export_chat_txt(chat, p)
            written["txt"].append(p)
        if "html" in formats:
            p = os.path.join(output_dir, f"{safe_name}.html")
            export_chat_html(chat, p, media_index=media_index)
            written["html"].append(p)
        if "reconstruct" in formats:
            p = os.path.join(output_dir, f"{safe_name}.reconstructed.html")
            export_reconstructed_chat(chat, calls, p, media_index=media_index)
            written["reconstruct"].append(p)

    if "xlsx" in formats:
        p = os.path.join(output_dir, "all_messages.xlsx")
        export_chats_xlsx(chats, p)
        written["xlsx"].append(p)

    if "calls_xlsx" in formats and calls:
        p = os.path.join(output_dir, "call_log.xlsx")
        export_calls_xlsx(calls, p)
        written["calls_xlsx"].append(p)

    return written
