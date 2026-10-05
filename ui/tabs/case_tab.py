"""CaseTabMixin — case folder, integrity verification and PDF report."""

import os

from ui.common import (
    ctk,
    custody,
    fd,
    mb,
    theme,
    threading,
    tk,
)
from modules.case import Case
from modules import report_pdf


class CaseTabMixin:
    case = None   # active modules.case.Case, or None

    def _build_case_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ CASE FOLDER & EVIDENCE INTEGRITY ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 4))
        ctk.CTkLabel(
            tab,
            text=("A case keeps evidence/ (read-only originals), working/ (decrypted copies), exports/ and one "
                  "hash-chained custody log together. Read-only flags are a safety net, not a write blocker — "
                  "use write-protected media for real casework."),
            font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED, wraplength=900, justify="left",
        ).pack(padx=20, anchor="w")

        row = ctk.CTkFrame(tab, fg_color="transparent"); row.pack(fill="x", padx=20, pady=8)
        ctk.CTkLabel(row, text="Case folder", width=100, anchor="w").pack(side="left")
        self.case_dir_entry = ctk.CTkEntry(row, width=460); self.case_dir_entry.pack(side="left", padx=8)
        ctk.CTkButton(row, text="Select", width=70,
                      command=lambda: self._pick_dir(self.case_dir_entry)).pack(side="left")

        btns = ctk.CTkFrame(tab, fg_color="transparent"); btns.pack(fill="x", padx=20, pady=4)
        for text, cmd in [("Create / open case", self._open_or_create_case),
                          ("Verify integrity", self._verify_case),
                          ("Protect evidence (read-only)", self._protect_case_evidence),
                          ("Generate PDF report", self._make_pdf_report)]:
            ctk.CTkButton(btns, text=text, command=cmd).pack(side="left", padx=4)

        self.case_status = ctk.CTkLabel(tab, text="No case open — outputs go wherever you choose.",
                                         font=theme.FONT_MONO_SMALL, text_color=theme.ACCENT_YELLOW)
        self.case_status.pack(padx=20, anchor="w", pady=(6, 0))
        self.case_log = ctk.CTkTextbox(tab, height=260, font=theme.FONT_MONO,
                                        fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.case_log.pack(fill="both", expand=True, padx=20, pady=(6, 16))

    # ------------------------------------------------------------ actions
    def _open_or_create_case(self):
        root = self.case_dir_entry.get().strip()
        if not root:
            mb.showwarning("Missing info", "Choose a case folder first.")
            return
        examiner, case_id = self.examiner_entry.get().strip(), self.case_entry.get().strip()
        try:
            if os.path.exists(os.path.join(root, "case.json")):
                self.case = Case.open(root)
                if examiner: custody.set_examiner(examiner)
                if case_id: custody.set_case_id(case_id)
                verb = "Opened"
            else:
                self.case = Case.create(root, case_id=case_id, examiner=examiner)
                verb = "Created"
        except Exception as e:
            mb.showerror("Case error", str(e))
            return
        self._apply_case_defaults()
        self.case_status.configure(text=f"Case active: {self.case.root}", text_color=theme.ACCENT_GREEN)
        self._log(self.case_log, f"{verb} case at {self.case.root}")

    def _apply_case_defaults(self):
        c = self.case
        for entry, folder in [(self.adb_output_entry, c.evidence_dir),
                              (self.cloud_restore_out_entry, c.evidence_dir),
                              (self.scan_output_entry, c.evidence_dir),
                              (self.decrypt_out_entry, c.working_dir),
                              (self.export_out_entry, c.exports_dir)]:
            entry.delete(0, tk.END); entry.insert(0, folder)

    def _protect_if_case(self, path):
        """Called after pulls: make freshly pulled evidence read-only."""
        if self.case and path and os.path.exists(path):
            try:
                self.case.protect(path)
            except Exception as e:
                self._log(self.case_log, f"WARNING: could not protect {path}: {e}")

    def _protect_case_evidence(self):
        if not self.case:
            mb.showwarning("No case", "Open or create a case first."); return
        n = self.case.protect(self.case.evidence_dir)
        self._log(self.case_log, f"Set {n} evidence file(s) read-only.")

    def _verify_case(self):
        if not self.case:
            mb.showwarning("No case", "Open or create a case first."); return
        ok, msg = self.case.verify()
        self._log(self.case_log, ("INTEGRITY OK\n" if ok else "INTEGRITY PROBLEM\n") + msg)
        self.case_status.configure(
            text=("Integrity verified ✔" if ok else "INTEGRITY PROBLEM — see log"),
            text_color=theme.ACCENT_GREEN if ok else theme.ACCENT_RED)

    def _make_pdf_report(self):
        if not self.case:
            mb.showwarning("No case", "Open or create a case first."); return
        out = os.path.join(self.case.exports_dir, "case_report.pdf")
        chats, calls = self.session.get("chats", []), self.session.get("calls", [])

        def worker():
            try:
                _ok, note = self.case.verify()
                report_pdf.write_pdf_report(out, self.case.custody_dir, chats, calls, verify_note=note)
                self._log(self.case_log, f"PDF report written: {out}")
            except Exception as e:
                self._log(self.case_log, f"ERROR: {e}")
        threading.Thread(target=worker, daemon=True).start()
