"""DeletedMediaTab — deleted media tab (split out of main.py)."""

from ui.common import (
    Image,
    ctk,
    find_deleted_media,
    io,
    mb,
    os,
    shutil,
    theme,
    threading,
)


class DeletedMediaTabMixin:
    def _build_deleted_media_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ DELETED MEDIA CHECK ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 4))
        ctk.CTkLabel(
            tab,
            text=("For every message referencing a photo/video/document whose full-resolution "
                  "file can't be found anywhere (Media folder, extra folders, or files pulled via "
                  "the Filesystem Scan tab), checks whether a thumbnail still exists — either as "
                  "a blob embedded directly inside msgstore.db, or as a leftover file in a hidden "
                  "\".Thumbs\" cache folder. Either can survive after the original was deleted."),
            font=theme.FONT_SUBHEADER, text_color=theme.TEXT_MUTED,
            wraplength=900, justify="left",
        ).pack(padx=20, pady=(0, 10), anchor="w")

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=6)
        ctk.CTkButton(top, text="Run Deleted Media Check", command=self._run_deleted_media_check).pack(side="left")
        self.deleted_media_status = ctk.CTkLabel(top, text="Load a database first (Chat Viewer tab).",
                                                  text_color=theme.TEXT_MUTED)
        self.deleted_media_status.pack(side="left", padx=12)

        save_row = ctk.CTkFrame(tab, fg_color="transparent")
        save_row.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(save_row, text="Save recovered thumbnails to:", anchor="w").pack(side="left")
        self.deleted_media_out_entry = ctk.CTkEntry(save_row, width=380)
        self.deleted_media_out_entry.pack(side="left", padx=8)
        ctk.CTkButton(save_row, text="Select", width=70,
                      command=lambda: self._pick_dir(self.deleted_media_out_entry)).pack(side="left")
        ctk.CTkButton(save_row, text="Save All Recovered Thumbnails",
                      fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=self._save_all_recovered_thumbnails).pack(side="left", padx=10)

        self.deleted_media_results_frame = ctk.CTkScrollableFrame(tab, fg_color=theme.BG_PANEL_ALT)
        self.deleted_media_results_frame.pack(fill="both", expand=True, padx=20, pady=(6, 16))
        self._deleted_media_findings = []
        self._thumbnail_image_refs = []  # keep references so CTkImage isn't garbage-collected

    def _run_deleted_media_check(self):
        chats = self.session.get("chats", [])
        if not chats:
            mb.showwarning("No database loaded", "Load a database on the Chat Viewer tab first.")
            return

        extra_media_dir = self.export_media_entry.get().strip()  # read on main thread
        self.deleted_media_status.configure(text="Checking...")

        def worker():
            media_index = self._build_media_index(extra_media_dir)
            thumbnail_index = self._build_thumbnail_index(extra_media_dir)
            findings = find_deleted_media(chats, media_index, thumbnail_index)
            self._deleted_media_findings = findings

            recoverable = sum(1 for f in findings if f.has_thumbnail)
            status_text = (f"{len(findings)} deleted media reference(s) found — "
                            f"{recoverable} with a recoverable thumbnail, "
                            f"{len(findings) - recoverable} with none.")

            def apply():
                self.deleted_media_status.configure(text=status_text)
                self._populate_deleted_media_results(findings)
            self._run_on_main(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _load_thumbnail_ctkimage(self, finding):
        try:
            if finding.thumbnail_source == "db_blob":
                img = Image.open(io.BytesIO(finding.message.thumbnail_blob))
            elif finding.thumbnail_source == "thumbnail_cache_file":
                img = Image.open(finding.thumbnail_local_path)
            else:
                return None
            img.thumbnail((120, 120))
            ctk_img = ctk.CTkImage(light_image=img, size=img.size)
            self._thumbnail_image_refs.append(ctk_img)
            return ctk_img
        except Exception:
            return None  # blob/file exists but isn't a decodable image — still report the finding

    def _populate_deleted_media_results(self, findings):
        for widget in self.deleted_media_results_frame.winfo_children():
            widget.destroy()
        self._thumbnail_image_refs = []

        if not findings:
            ctk.CTkLabel(self.deleted_media_results_frame,
                         text="No deleted media references found.",
                         text_color=theme.TEXT_MUTED).pack(pady=10)
            return

        for finding in findings:
            row = ctk.CTkFrame(self.deleted_media_results_frame, fg_color=theme.BG_PANEL,
                                border_color=theme.BORDER_GREEN, border_width=1, corner_radius=6)
            row.pack(fill="x", pady=4, padx=2)

            ctk_img = self._load_thumbnail_ctkimage(finding) if finding.has_thumbnail else None
            if ctk_img:
                ctk.CTkLabel(row, text="", image=ctk_img).pack(side="left", padx=10, pady=8)
            else:
                placeholder = "⚠ no thumbnail" if not finding.has_thumbnail else "⚠ unreadable"
                ctk.CTkLabel(row, text=placeholder, width=100, text_color=theme.ACCENT_RED,
                             font=theme.FONT_MONO_SMALL).pack(side="left", padx=10, pady=8)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True, pady=8)
            ctk.CTkLabel(info, text=f"{finding.original_filename}", font=theme.FONT_LABEL,
                         anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=f"Chat: {finding.chat_name}", font=theme.FONT_MONO_SMALL,
                         text_color=theme.TEXT_MUTED, anchor="w").pack(anchor="w")
            source_label = {
                "db_blob": "Thumbnail recovered from msgstore.db (embedded blob)",
                "thumbnail_cache_file": "Thumbnail recovered from device .Thumbs cache",
                None: "No thumbnail recoverable — original and cache both missing",
            }[finding.thumbnail_source]
            ctk.CTkLabel(info, text=source_label, font=theme.FONT_MONO_SMALL,
                         text_color=theme.ACCENT_GREEN if finding.has_thumbnail else theme.ACCENT_RED,
                         anchor="w").pack(anchor="w")

    def _save_all_recovered_thumbnails(self):
        out_dir = self.deleted_media_out_entry.get().strip()
        if not out_dir:
            mb.showwarning("Missing folder", "Select a folder to save recovered thumbnails to.")
            return
        if not self._deleted_media_findings:
            mb.showwarning("Nothing to save", "Run the Deleted Media Check first.")
            return

        os.makedirs(out_dir, exist_ok=True)
        saved = 0
        for finding in self._deleted_media_findings:
            if not finding.has_thumbnail:
                continue
            safe_name = "".join(c for c in finding.original_filename if c.isalnum() or c in "._-") or "thumbnail"
            dest = os.path.join(out_dir, f"THUMB_{safe_name}")
            try:
                if finding.thumbnail_source == "db_blob":
                    with open(dest, "wb") as f:
                        f.write(finding.message.thumbnail_blob)
                    saved += 1
                elif finding.thumbnail_source == "thumbnail_cache_file":
                    import shutil
                    shutil.copy2(finding.thumbnail_local_path, dest)
                    saved += 1
            except OSError:
                continue

        mb.showinfo("Saved", f"Saved {saved} recovered thumbnail(s) to:\n{out_dir}")
