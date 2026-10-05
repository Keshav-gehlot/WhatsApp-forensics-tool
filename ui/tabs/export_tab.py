"""ExportTab — export tab (split out of main.py)."""

from ui.common import (
    os,
    ctk,
    exp,
    mb,
    media_resolver,
    theme,
    threading,
    tk,
)


class ExportTabMixin:
    def _build_export_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ EXPORT OPTIONS ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 6))

        out_row = ctk.CTkFrame(tab, fg_color="transparent")
        out_row.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(out_row, text="Output folder", width=140, anchor="w").pack(side="left")
        self.export_out_entry = ctk.CTkEntry(out_row, width=420)
        self.export_out_entry.pack(side="left", padx=8)
        ctk.CTkButton(out_row, text="Select", width=70,
                      command=lambda: self._pick_dir(self.export_out_entry)).pack(side="left")

        media_row = ctk.CTkFrame(tab, fg_color="transparent")
        media_row.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(media_row, text="Extra media folder (optional)", width=140, anchor="w").pack(side="left")
        self.export_media_entry = ctk.CTkEntry(media_row, width=420)
        self.export_media_entry.pack(side="left", padx=8)
        ctk.CTkButton(media_row, text="Select", width=70,
                      command=lambda: self._pick_dir(self.export_media_entry)).pack(side="left")
        ctk.CTkLabel(tab, text=("Media already pulled via ADB Extractor or the Filesystem Scan tab "
                                 "is included automatically — use this only to point at an additional "
                                 "folder of recovered media."),
                     font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                     wraplength=850, justify="left").pack(padx=20, anchor="w")

        self.export_vars = {}
        opts_row = ctk.CTkFrame(tab, fg_color="transparent")
        opts_row.pack(fill="x", padx=20, pady=10)
        for fmt, label in [("txt", "Chats -> TXT"), ("xlsx", "Chats -> Excel"),
                           ("html", "Chats -> HTML (WA look, media embedded)"),
                           ("reconstruct", "Full reconstruction (messages + calls, one timeline)"),
                           ("calls_xlsx", "Call log -> Excel"),
                           ("extras", "Extras -> Excel (reactions, edits, groups, starred, status)"),
                           ("timeline", "Timeline -> CSV")]:
            var = tk.BooleanVar(value=(fmt != "reconstruct"))
            self.export_vars[fmt] = var
            ctk.CTkCheckBox(opts_row, text=label, variable=var).pack(anchor="w", pady=2)

        ctk.CTkButton(tab, text="Export", fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=self._run_export).pack(pady=16)

        self.export_log = ctk.CTkTextbox(tab, height=200, font=theme.FONT_MONO,
                                          fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.export_log.pack(fill="both", expand=True, padx=20, pady=(6, 16))

    def _build_media_index(self, extra_media_dir: str = "") -> media_resolver.MediaIndex:
        idx = media_resolver.MediaIndex()
        idx.add_directory(self.session.get("media_dir"))
        idx.add_paths(self.session.get("pulled_files", []))
        if extra_media_dir:
            idx.add_directory(extra_media_dir)
        return idx

    def _build_thumbnail_index(self, extra_media_dir: str = "") -> media_resolver.ThumbnailIndex:
        idx = media_resolver.ThumbnailIndex()
        # media_dir was pulled as a whole folder via ADB, so any .Thumbs
        # subfolder inside it is still structurally intact — safe to
        # detect by path here.
        idx.add_directory(self.session.get("media_dir"))
        if extra_media_dir:
            idx.add_directory(extra_media_dir)
        # pulled_thumbnails were tagged at pull time (tier == "thumbnail"),
        # since their local folder layout no longer preserves the original
        # .Thumbs path segment — add them directly rather than re-detecting.
        idx.add_paths(self.session.get("pulled_thumbnails", []))
        return idx

    def _run_export(self):
        out_dir = self.export_out_entry.get().strip()
        chats = self.session.get("chats", [])
        calls = self.session.get("calls", [])
        if not out_dir or not chats:
            mb.showwarning("Missing info", "Load a database first, then select an output folder.")
            return
        formats = [fmt for fmt, var in self.export_vars.items() if var.get()]
        want_extras, want_timeline = "extras" in formats, "timeline" in formats
        formats = [f for f in formats if f not in ("extras", "timeline")]
        db = self.session.get("wa_database")
        extra_media_dir = self.export_media_entry.get().strip()  # read on main thread

        def worker():
            try:
                media_index = self._build_media_index(extra_media_dir)
                if len(media_index):
                    self._log(self.export_log, f"Media index: {len(media_index)} local file(s) available to embed.")
                written = exp.export_all(chats, calls, out_dir, formats, media_index=media_index)
                from modules import custody as _c, timeline as _tl
                if want_extras and db is not None:
                    p = os.path.join(out_dir, "extras.xlsx")
                    n = exp.export_extras_xlsx(db, p); _c.record(out_dir, "export-extras", p)
                    written["extras"] = [p]; self._log(self.export_log, f"extras: {n} row(s)")
                if want_timeline:
                    p = os.path.join(out_dir, "timeline.csv")
                    _tl.export_timeline_csv(self._timeline_events(), p); _c.record(out_dir, "export-timeline", p)
                    written["timeline"] = [p]
                for fmt, paths in written.items():
                    self._log(self.export_log, f"{fmt}: {len(paths)} file(s) written")
                self._run_on_main(lambda: mb.showinfo("Export complete", f"Files written to:\n{out_dir}"))
            except Exception as e:
                self._log(self.export_log, f"ERROR: {e}")
                self._run_on_main(lambda err=str(e): mb.showerror("Export Error", err))

        threading.Thread(target=worker, daemon=True).start()
