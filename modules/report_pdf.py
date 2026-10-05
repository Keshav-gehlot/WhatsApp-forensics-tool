"""PDF case report: summary counts, integrity status and the custody table."""

import json
import os
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import custody


def _read_log(folder):
    path = os.path.join(folder, custody.LOG_NAME)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def write_pdf_report(output_path: str, custody_folder: str, chats=(), calls=(),
                     title: str = "WhatsApp Forensicator — Case Report", verify_note: str = "") -> str:
    ok, msg = custody.verify_log(custody_folder)
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small"); small.fontSize = 7; small.leading = 8.5
    doc = SimpleDocTemplate(output_path, pagesize=landscape(A4), leftMargin=12 * mm,
                            rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=title, author=custody._examiner)
    msgs = sum(len(c.messages) for c in chats)
    story = [Paragraph(title, styles["Title"]),
             Paragraph(f"Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}<br/>"
                       f"Examiner: {custody._examiner} &nbsp; Case ID: {custody._case_id or '-'}", styles["BodyText"]),
             Spacer(1, 4 * mm),
             Paragraph("Summary", styles["Heading2"]),
             Paragraph(f"Chats: {len(chats)} &nbsp; Messages: {msgs} &nbsp; Calls: {len(calls)}", styles["BodyText"]),
             Paragraph("Custody log integrity: <b>%s</b> — %s" % ("INTACT" if ok else "PROBLEM", msg), styles["BodyText"])]
    if verify_note:
        story.append(Paragraph(verify_note.replace("\n", "<br/>"), small))
    story += [Spacer(1, 4 * mm), Paragraph("Chain of custody", styles["Heading2"])]
    rows = [["Time (UTC)", "Action", "File", "SHA-256", "Device", "Note"]]
    for r in _read_log(custody_folder):
        rows.append([Paragraph(r.get("time_utc", ""), small), Paragraph(r.get("action", ""), small),
                     Paragraph(os.path.basename(r.get("file", "")) or "-", small),
                     Paragraph(r.get("sha256", "-"), small),
                     Paragraph(r.get("device_serial", "") or "-", small),
                     Paragraph((r.get("note", "") or "")[:120], small)])
    t = Table(rows, repeatRows=1, colWidths=[32 * mm, 30 * mm, 45 * mm, 85 * mm, 22 * mm, 55 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6f43")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("FONTSIZE", (0, 0), (-1, 0), 8)]))
    story.append(t)
    doc.build(story)
    custody.record(custody_folder, "pdf-report", output_path)
    return output_path
