"""DecryptTab — decrypt tab (split out of main.py)."""

from ui.common import (
    os,
    ctk,
    custody,
    dec,
    mb,
    theme,
)


class DecryptTabMixin:
    def _build_decrypt_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ DECRYPT DATABASE ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 6))

        for label, attr in [("Encrypted database (.crypt12/14/15)", "encrypted_db_entry"),
                             ("Key file", "key_entry")]:
            row = ctk.CTkFrame(tab, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=4)
            ctk.CTkLabel(row, text=label, width=260, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=420)
            entry.pack(side="left", padx=8)
            setattr(self, attr, entry)
            ctk.CTkButton(row, text="Select", width=70,
                          command=lambda e=entry: self._pick_file(e)).pack(side="left")

        out_row = ctk.CTkFrame(tab, fg_color="transparent")
        out_row.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(out_row, text="Output folder", width=260, anchor="w").pack(side="left")
        self.decrypt_out_entry = ctk.CTkEntry(out_row, width=420)
        self.decrypt_out_entry.pack(side="left", padx=8)
        ctk.CTkButton(out_row, text="Select", width=70,
                      command=lambda: self._pick_dir(self.decrypt_out_entry)).pack(side="left")

        ctk.CTkButton(tab, text="Decrypt", fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=self._run_decrypt).pack(pady=16)

        self.decrypt_log = ctk.CTkTextbox(tab, height=200, font=theme.FONT_MONO,
                                           fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.decrypt_log.pack(fill="both", expand=True, padx=20, pady=(6, 16))

    def _run_decrypt(self):
        enc = self.encrypted_db_entry.get().strip()
        key = self.key_entry.get().strip()
        out = self.decrypt_out_entry.get().strip()
        if not (enc and key and out):
            mb.showwarning("Missing info", "Select the encrypted DB, key file, and output folder.")
            return
        custody.set_examiner(self.examiner_entry.get())
        custody.set_case_id(self.case_entry.get())
        try:
            if self.case:   # originals go to evidence/ (read-only); output to working/
                for name in ("enc", "key"):
                    src = enc if name == "enc" else key
                    if not os.path.abspath(src).startswith(self.case.evidence_dir):
                        dest = self.case.ingest(src)
                        self._log(self.decrypt_log, f"Ingested {src} -> {dest}")
                        enc, key = (dest, key) if name == "enc" else (enc, dest)
                out = self.case.working_dir
            result = dec.decrypt_backup(key, enc, out)
            self.session["msgstore_path"] = result.output_path
            self._log(self.decrypt_log, f"Decrypted ({result.backup_format}) -> {result.output_path}")
            if result.custody_warning:
                self._log(self.decrypt_log, f"WARNING: {result.custody_warning}")
            mb.showinfo("Success", f"Decrypted to:\n{result.output_path}")
        except dec.DecryptError as e:
            self._log(self.decrypt_log, f"ERROR: {e}")
            mb.showerror("Decrypt Error", str(e))
