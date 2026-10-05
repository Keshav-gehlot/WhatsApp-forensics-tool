"""ScanTab — scan tab (split out of main.py)."""

from ui.common import (
    ctk,
    custody,
    mb,
    os,
    scanner,
    theme,
    threading,
    tk,
)


class ScanTabMixin:
    def _build_scan_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ FILESYSTEM SCAN ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 4))
        ctk.CTkLabel(
            tab,
            text=("Scans external storage (no root needed) for WhatsApp backups, "
                  "databases, and media in WhatsApp's own folders — plus files "
                  "that were shared or downloaded from WhatsApp and later moved "
                  "or saved elsewhere (Downloads, Pictures, Documents, etc.), "
                  "detected via WhatsApp's own filename convention "
                  "(IMG-/VID-/AUD-/PTT-/DOC-...-WA####). Also checks whether "
                  "app-private storage (/data/data/com.whatsapp) is reachable — "
                  "only true on an already-rooted device — and reports that "
                  "plainly rather than guessing."),
            font=theme.FONT_SUBHEADER, text_color=theme.TEXT_MUTED,
            wraplength=900, justify="left",
        ).pack(padx=20, pady=(0, 10), anchor="w")

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=6)
        ctk.CTkButton(top, text="Scan Connected Device", command=self._run_scan).pack(side="left")
        self.deep_scan_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(top, text="Deep scan (entire storage — slower, catches "
                                    "files saved anywhere)",
                        variable=self.deep_scan_var).pack(side="left", padx=16)
        self.scan_status_label = ctk.CTkLabel(top, text="No scan run yet.",
                                               text_color=theme.TEXT_MUTED)
        self.scan_status_label.pack(side="left", padx=12)

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 6))

        self.scan_results_frame = ctk.CTkScrollableFrame(body, fg_color=theme.BG_PANEL_ALT)
        self.scan_results_frame.pack(fill="both", expand=True)
        self._scan_hit_vars = []  # list of (tk.BooleanVar, ScanHit)

        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(bottom, text="Pull selected to:", anchor="w").pack(side="left")
        self.scan_output_entry = ctk.CTkEntry(bottom, width=380)
        self.scan_output_entry.pack(side="left", padx=8)
        ctk.CTkButton(bottom, text="Select", width=70,
                      command=lambda: self._pick_dir(self.scan_output_entry)).pack(side="left")
        ctk.CTkButton(bottom, text="Pull Selected Files", fg_color=theme.ACCENT_GREEN,
                      text_color="black", command=self._pull_selected_scan_hits).pack(side="left", padx=10)

        self.scan_log = ctk.CTkTextbox(tab, height=140, font=theme.FONT_MONO,
                                        fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.scan_log.pack(fill="both", padx=20, pady=(0, 16))

    def _run_scan(self):
        serial = self.device_dropdown.get()
        if not serial:
            mb.showwarning("No device", "Select a device on the ADB Extractor tab first.")
            return
        self.scan_status_label.configure(text="Scanning...")
        deep = self.deep_scan_var.get()  # read the tk.Variable on the main thread, not the worker

        def worker():
            try:
                report = scanner.scan_device(serial, progress=lambda m: self._log(self.scan_log, m),
                                              deep=deep)
                root_note = ("root-accessible paths included" if report.root_available
                              else "device not rooted — external storage only")
                status_text = f"{len(report.hits)} file(s) found ({root_note})"
                self._run_on_main(lambda: (self._populate_scan_results(report),
                                            self.scan_status_label.configure(text=status_text)))
            except Exception as e:
                self._log(self.scan_log, f"ERROR: {e}")
                self._run_on_main(lambda: self.scan_status_label.configure(text="Scan failed — see log."))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_scan_results(self, report):
        for widget in self.scan_results_frame.winfo_children():
            widget.destroy()
        self._scan_hit_vars = []

        if not report.hits:
            ctk.CTkLabel(self.scan_results_frame, text="No matching files found.",
                         text_color=theme.TEXT_MUTED).pack(pady=10)
            return

        for hit in report.hits:
            row = ctk.CTkFrame(self.scan_results_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            var = tk.BooleanVar(value=False)
            self._scan_hit_vars.append((var, hit))
            if hit.tier == "root_only":
                tier_tag = "[root]"
            elif hit.tier == "shared_elsewhere":
                tier_tag = "[shared]"
            else:
                tier_tag = "[ext]"
            ctk.CTkCheckBox(row, text=f"{tier_tag} {hit.category} — {hit.path}",
                            variable=var, font=theme.FONT_MONO_SMALL).pack(anchor="w")

    def _pull_selected_scan_hits(self):
        serial = self.device_dropdown.get()
        out_dir = self.scan_output_entry.get().strip()
        selected = [hit for var, hit in self._scan_hit_vars if var.get()]
        if not (serial and out_dir and selected):
            mb.showwarning("Missing info", "Select a device, output folder, and at least one file.")
            return

        def worker():
            pulled = scanner.pull_hits(serial, selected, out_dir,
                                        progress=lambda m: self._log(self.scan_log, m))
            self._log(self.scan_log, f"Pulled {len(pulled)}/{len(selected)} file(s) to {out_dir}")
            self._protect_if_case(out_dir)
            for _hit, _lp in pulled:
                try:
                    custody.record(out_dir, "adb-pull-scan-hit", _lp, serial=serial, note=_hit.path)
                except Exception as e:
                    self._log(self.scan_log, f"WARNING: custody logging failed: {e}")
            for hit, local_path in pulled:
                if hit.tier == "thumbnail":
                    self.session.setdefault("pulled_thumbnails", []).append(local_path)
                else:
                    self.session.setdefault("pulled_files", []).append(local_path)
                if local_path.lower().endswith((".crypt12", ".crypt14", ".crypt15")):
                    lp = local_path
                    self._run_on_main(lambda lp=lp: (self.encrypted_db_entry.delete(0, tk.END),
                                                      self.encrypted_db_entry.insert(0, lp)))
                if os.path.basename(local_path) == "key":
                    lp = local_path
                    self._run_on_main(lambda lp=lp: (self.key_entry.delete(0, tk.END),
                                                      self.key_entry.insert(0, lp)))

        threading.Thread(target=worker, daemon=True).start()
