"""
Case folders
------------
A case folder keeps evidence, working copies and outputs apart:

    <case>/
      case.json            case id, examiner, creation time
      custody/             ONE hash-chained custody log for the whole case
      evidence/            files exactly as pulled from the device (read-only)
      working/             decrypted databases and anything derived from evidence
      exports/             reports, spreadsheets, HTML

Originals in evidence/ are set read-only the moment they are ingested, and
every later step (decrypt, parse, export) reads from them or from copies in
working/ — never modifies them. Read-only flags are a safety net against
accidents, NOT a forensic write blocker: a determined user or another
program can clear them. For real casework also use write-protected media
and compare against the SHA-256 recorded in the custody log.
"""

import json
import os
import shutil
import stat
from dataclasses import dataclass
from typing import Optional

from . import custody

SUBDIRS = ("custody", "evidence", "working", "exports")


@dataclass
class Case:
    root: str
    case_id: str = ""
    examiner: str = ""

    # ------------------------------------------------------------ paths
    @property
    def evidence_dir(self): return os.path.join(self.root, "evidence")
    @property
    def working_dir(self): return os.path.join(self.root, "working")
    @property
    def exports_dir(self): return os.path.join(self.root, "exports")
    @property
    def custody_dir(self): return os.path.join(self.root, "custody")

    # --------------------------------------------------------- lifecycle
    @classmethod
    def create(cls, root: str, case_id: str = "", examiner: str = "") -> "Case":
        for sub in SUBDIRS:
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        case = cls(root=os.path.abspath(root), case_id=case_id, examiner=examiner)
        meta = os.path.join(root, "case.json")
        if not os.path.exists(meta):
            with open(meta, "w", encoding="utf-8") as f:
                json.dump({"case_id": case_id, "examiner": examiner,
                           "created_utc": custody.utc_now()}, f, indent=2)
        case.activate()
        custody.record(case.custody_dir, "case-opened", None, note=f"case folder {case.root}")
        return case

    @classmethod
    def open(cls, root: str) -> "Case":
        meta = os.path.join(root, "case.json")
        if not os.path.exists(meta):
            raise FileNotFoundError(f"{root} is not a case folder (no case.json)")
        with open(meta, encoding="utf-8") as f:
            info = json.load(f)
        case = cls(root=os.path.abspath(root), case_id=info.get("case_id", ""),
                   examiner=info.get("examiner", ""))
        for sub in SUBDIRS:
            os.makedirs(os.path.join(root, sub), exist_ok=True)
        case.activate()
        custody.record(case.custody_dir, "case-opened", None, note=f"case folder {case.root}")
        return case

    def activate(self) -> None:
        """Route every custody record in this process into the case's single log."""
        custody.set_log_folder(self.custody_dir)
        if self.examiner:
            custody.set_examiner(self.examiner)
        if self.case_id:
            custody.set_case_id(self.case_id)

    # --------------------------------------------------------- protection
    def protect(self, path: str) -> int:
        """Make a file (or every file under a folder) read-only. Returns count."""
        n = 0
        paths = []
        if os.path.isdir(path):
            for r, _d, files in os.walk(path):
                paths += [os.path.join(r, f) for f in files]
        elif os.path.isfile(path):
            paths = [path]
        for p in paths:
            try:
                os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                n += 1
            except OSError:
                pass
        if n:
            custody.record(self.custody_dir, "set-read-only", None, note=f"{n} file(s) under {path}")
        return n

    def ingest(self, src: str) -> str:
        """Copy an outside file into evidence/ (hash before and after, make
        it read-only). Returns the evidence path."""
        dest = os.path.join(self.evidence_dir, os.path.basename(src))
        if os.path.exists(dest):
            base, ext = os.path.splitext(dest)
            i = 2
            while os.path.exists(f"{base}_{i}{ext}"):
                i += 1
            dest = f"{base}_{i}{ext}"
        before = custody.sha256_file(src)
        shutil.copy2(src, dest)
        after = custody.sha256_file(dest)
        if before != after:
            os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
            os.remove(dest)
            raise IOError(f"copy of {src} did not match the original (hash mismatch)")
        custody.record(self.custody_dir, "ingest-evidence", dest, note=f"copied from {src}")
        self.protect(dest)
        return dest

    def working_copy(self, evidence_path: str) -> str:
        """A writable copy of an evidence file in working/ (hash-verified)."""
        dest = os.path.join(self.working_dir, os.path.basename(evidence_path))
        if os.path.exists(dest):
            os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
        shutil.copy2(evidence_path, dest)
        os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
        if custody.sha256_file(dest) != custody.sha256_file(evidence_path):
            raise IOError("working copy hash mismatch")
        custody.record(self.custody_dir, "working-copy", dest, note=f"from {evidence_path}")
        return dest

    def verify(self) -> tuple[bool, str]:
        """Check the custody chain AND re-hash every evidence file that has a
        recorded SHA-256. Returns (ok, multi-line message)."""
        ok, msg = custody.verify_log(self.custody_dir)
        lines = [f"Custody chain: {msg}"]
        all_ok = ok
        recorded = {}
        log = os.path.join(self.custody_dir, custody.LOG_NAME)
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                for raw in f:
                    if raw.strip():
                        r = json.loads(raw)
                        if r.get("sha256") and r.get("file", "").startswith(self.evidence_dir):
                            recorded.setdefault(r["file"], r["sha256"])
        for path, digest in recorded.items():
            if not os.path.exists(path):
                lines.append(f"MISSING  {path}"); all_ok = False
            elif custody.sha256_file(path) != digest:
                lines.append(f"CHANGED  {path}"); all_ok = False
            else:
                lines.append(f"ok       {path}")
        if not recorded:
            lines.append("(no hashed evidence files recorded yet)")
        return all_ok, "\n".join(lines)
