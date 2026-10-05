"""
Chain of custody
----------------
Append-only audit log (JSON Lines) plus SHA-256 hashing of every evidence
file the tool pulls, decrypts or exports. One log file per output folder:
`custody_log.jsonl`. Each entry carries a UTC timestamp, the action, the
file's SHA-256 and size, the device serial (if any) and the examiner name.

Entries are chained: each record includes the SHA-256 of the previous
record's line, so deleting or editing an earlier line is detectable with
verify_log(). This is tamper-EVIDENT, not tamper-proof — keep the folder
on write-protected media for real casework.
"""

import hashlib
import json
import os
import platform
import threading
from datetime import datetime, timezone
from typing import Optional

LOG_NAME = "custody_log.jsonl"
_lock = threading.Lock()

# Set once from the UI; included in every record.
_log_folder_override: Optional[str] = None   # set by a Case: one log for everything
_examiner = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
_case_id = ""


def set_examiner(name: str) -> None:
    global _examiner
    if name and name.strip():
        _examiner = name.strip()


def set_log_folder(folder: Optional[str]) -> None:
    """Send every record to `folder` instead of the folder passed to record().
    Used by case folders so a whole case has a single custody log."""
    global _log_folder_override
    _log_folder_override = folder


def set_case_id(case_id: str) -> None:
    global _case_id
    _case_id = (case_id or "").strip()


def sha256_file(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _last_line_hash(log_path: str) -> str:
    if not os.path.exists(log_path):
        return "0" * 64
    last = b""
    with open(log_path, "rb") as f:
        for line in f:
            if line.strip():
                last = line.rstrip(b"\r\n")
    return hashlib.sha256(last).hexdigest() if last else "0" * 64


def record(folder: str, action: str, path: Optional[str] = None,
           serial: Optional[str] = None, note: str = "") -> dict:
    """Append one record to <folder>/custody_log.jsonl. If `path` is a file
    that exists, its SHA-256 and size are recorded. Never raises on a
    hashing problem — the failure itself is logged — but does raise if
    the log cannot be written, since silent loss of custody data is worse
    than a visible error."""
    folder = _log_folder_override or folder
    os.makedirs(folder, exist_ok=True)
    entry = {
        "time_utc": utc_now(),
        "action": action,
        "examiner": _examiner,
        "case_id": _case_id,
        "host": platform.node(),
        "device_serial": serial or "",
        "file": os.path.abspath(path) if path else "",
        "note": note,
    }
    if path:
        try:
            if os.path.isfile(path):
                entry["sha256"] = sha256_file(path)
                entry["size_bytes"] = os.path.getsize(path)
            else:
                entry["hash_error"] = "path is not a file"
        except OSError as e:
            entry["hash_error"] = str(e)

    log_path = os.path.join(folder, LOG_NAME)
    with _lock:
        entry["prev_record_sha256"] = _last_line_hash(log_path)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def record_tree(folder: str, action: str, tree: str, serial: Optional[str] = None) -> dict:
    """Hash every file under `tree` into <folder>/<action>_manifest.sha256
    (sha256sum-compatible, relative paths) and log ONE custody record for
    the manifest itself, so a whole pulled Media folder is covered by a
    single verifiable entry."""
    os.makedirs(folder, exist_ok=True)
    manifest = os.path.join(folder, f"{action}_manifest.sha256")
    count = 0
    with open(manifest, "w", encoding="utf-8", newline="\n") as out:
        for root, _dirs, files in os.walk(tree):
            for name in sorted(files):
                full = os.path.join(root, name)
                try:
                    digest = sha256_file(full)
                except OSError as e:
                    out.write(f"# unreadable: {os.path.relpath(full, tree)} ({e})\n")
                    continue
                out.write(f"{digest}  {os.path.relpath(full, tree).replace(os.sep, '/')}\n")
                count += 1
    return record(folder, action, manifest, serial=serial,
                  note=f"{count} files hashed under {os.path.abspath(tree)}")


def verify_log(folder: str) -> tuple[bool, str]:
    """Re-walk the chain. Returns (ok, message)."""
    log_path = os.path.join(folder, LOG_NAME)
    if not os.path.exists(log_path):
        return False, "no custody log found"
    prev = "0" * 64
    with open(log_path, "rb") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip(b"\r\n")
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                return False, f"record {n} is not valid JSON"
            if rec.get("prev_record_sha256") != prev:
                return False, f"chain broken at record {n}"
            prev = hashlib.sha256(line).hexdigest()
    return True, "chain intact"


def write_case_report(folder: str, title: str = "WhatsApp Forensicator — case report") -> str:
    """Human-readable summary of the custody log, written next to it."""
    folder_for_log = _log_folder_override or folder
    log_path = os.path.join(folder_for_log, LOG_NAME)
    ok, msg = verify_log(folder_for_log)
    lines = [title, "=" * len(title), f"Generated (UTC): {utc_now()}",
             f"Examiner: {_examiner}", f"Case ID: {_case_id or '-'}",
             f"Log integrity: {'OK' if ok else 'PROBLEM'} — {msg}", ""]
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for raw in f:
                if not raw.strip():
                    continue
                r = json.loads(raw)
                lines.append(f"[{r['time_utc']}] {r['action']}")
                if r.get("file"):
                    lines.append(f"    file:   {r['file']}")
                if r.get("sha256"):
                    lines.append(f"    sha256: {r['sha256']}  ({r.get('size_bytes', '?')} bytes)")
                if r.get("device_serial"):
                    lines.append(f"    device: {r['device_serial']}")
                if r.get("note"):
                    lines.append(f"    note:   {r['note']}")
    out = os.path.join(folder, "case_report.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out
