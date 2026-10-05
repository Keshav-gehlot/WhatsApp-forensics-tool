"""MediaAnalysisTab — media analysis tab (split out of main.py)."""

from ui.common import (
    Image,
    TESSERACT_DOWNLOAD_URL,
    _load_ocr_config,
    _save_ocr_config,
    ctk,
    fd,
    imga,
    mb,
    os,
    theme,
    threading,
    tk,
    webbrowser,
)


class MediaAnalysisTabMixin:
    def _build_media_analysis_tab(self, tab):
        outer = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(outer, text="◆ MEDIA ANALYSIS ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(0, 4), anchor="w")

        # --- OCR Setup panel ---
        ocr_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                border_width=1, corner_radius=8)
        ocr_box.pack(fill="x", pady=6)
        ctk.CTkLabel(ocr_box, text="🔍  OCR Setup (Tesseract)", font=theme.FONT_LABEL).pack(
            anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(ocr_box,
                     text=("Tesseract OCR enables accurate document text extraction from images. "
                           "Without it, document detection falls back to a weak edge-density heuristic. "
                           "Install the free Tesseract binary (Windows: UB-Mannheim installer) and point "
                           "the app to it below."),
                     font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                     wraplength=850, justify="left").pack(anchor="w", padx=12)

        ocr_status_row = ctk.CTkFrame(ocr_box, fg_color="transparent")
        ocr_status_row.pack(fill="x", padx=12, pady=(8, 2))
        ocr_available = imga.is_ocr_available()
        status_color = theme.ACCENT_GREEN if ocr_available else theme.ACCENT_RED
        status_icon = "✅" if ocr_available else "❌"
        ver_info = imga.get_tesseract_info() or "not found"
        self._ocr_status_label = ctk.CTkLabel(
            ocr_status_row,
            text=f"{status_icon}  Tesseract: {ver_info}",
            font=theme.FONT_MONO_SMALL, text_color=status_color)
        self._ocr_status_label.pack(side="left")

        ocr_path_row = ctk.CTkFrame(ocr_box, fg_color="transparent")
        ocr_path_row.pack(fill="x", padx=12, pady=(6, 4))
        self._ocr_path_entry = ctk.CTkEntry(ocr_path_row, width=420,
                                             placeholder_text="Path to tesseract.exe (auto-filled if found)")
        cfg = _load_ocr_config()
        saved_path = cfg.get("tesseract_path", "")
        if saved_path:
            self._ocr_path_entry.insert(0, saved_path)
        self._ocr_path_entry.pack(side="left")
        ctk.CTkButton(ocr_path_row, text="Browse", width=70,
                      command=self._browse_tesseract_exe).pack(side="left", padx=8)
        ctk.CTkButton(ocr_path_row, text="Apply Path",
                      fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=self._apply_tesseract_path).pack(side="left", padx=4)
        ctk.CTkButton(ocr_path_row, text="Test OCR",
                      command=self._test_ocr).pack(side="left", padx=8)
        ctk.CTkButton(ocr_path_row, text="⬇ Download Tesseract (Windows)",
                      command=lambda: webbrowser.open(TESSERACT_DOWNLOAD_URL)).pack(side="left", padx=8)

        # --- Find a specific person ---
        person_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                   border_width=1, corner_radius=8)
        person_box.pack(fill="x", pady=6)
        ctk.CTkLabel(person_box, text="Find a specific person", font=theme.FONT_LABEL).pack(
            anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(person_box, text=("Uses classical (non-deep-learning) face recognition, trained "
                                        "on-the-fly from reference photo(s) you provide. Treat results "
                                        "as leads to review, not a confirmed identification."),
                     font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                     wraplength=850, justify="left").pack(anchor="w", padx=12)
        ref_row = ctk.CTkFrame(person_box, fg_color="transparent")
        ref_row.pack(fill="x", padx=12, pady=6)
        self.person_ref_entry = ctk.CTkEntry(ref_row, width=420,
                                              placeholder_text="Reference photo(s), comma-separated")
        self.person_ref_entry.pack(side="left")
        ctk.CTkButton(ref_row, text="Browse", width=70,
                      command=self._pick_person_ref_files).pack(side="left", padx=8)
        ctk.CTkButton(ref_row, text="Find This Person", fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=self._run_person_search).pack(side="left", padx=8)
        self.person_status_label = ctk.CTkLabel(person_box, text="", text_color=theme.TEXT_MUTED,
                                                  font=theme.FONT_MONO_SMALL)
        self.person_status_label.pack(anchor="w", padx=12, pady=(0, 4))
        self.person_results_frame = ctk.CTkScrollableFrame(person_box, height=180, fg_color=theme.BG_PANEL_ALT)
        self.person_results_frame.pack(fill="x", padx=12, pady=(0, 12))

        # --- Similar / duplicate images ---
        similar_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                    border_width=1, corner_radius=8)
        similar_box.pack(fill="x", pady=6)
        ctk.CTkLabel(similar_box, text="Similar / duplicate images", font=theme.FONT_LABEL).pack(
            anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(similar_box, text=("Groups near-identical images (same photo resent/recompressed/"
                                         "resized). This finds repeated image FILES, not the same object "
                                         "photographed from a different angle or scene."),
                     font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                     wraplength=850, justify="left").pack(anchor="w", padx=12)
        ctk.CTkButton(similar_box, text="Group Similar Images", fg_color=theme.ACCENT_GREEN,
                      text_color="black", command=self._run_similar_image_scan).pack(
            anchor="w", padx=12, pady=6)
        self.similar_status_label = ctk.CTkLabel(similar_box, text="", text_color=theme.TEXT_MUTED,
                                                   font=theme.FONT_MONO_SMALL)
        self.similar_status_label.pack(anchor="w", padx=12, pady=(0, 4))
        self.similar_results_frame = ctk.CTkScrollableFrame(similar_box, height=180, fg_color=theme.BG_PANEL_ALT)
        self.similar_results_frame.pack(fill="x", padx=12, pady=(0, 12))

        # --- Documents ---
        doc_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                border_width=1, corner_radius=8)
        doc_box.pack(fill="x", pady=6)
        ctk.CTkLabel(doc_box, text="Detect documents", font=theme.FONT_LABEL).pack(
            anchor="w", padx=12, pady=(10, 2))
        ctk.CTkButton(doc_box, text="Scan for Documents", fg_color=theme.ACCENT_GREEN,
                      text_color="black", command=self._run_document_scan).pack(
            anchor="w", padx=12, pady=6)
        self.doc_status_label = ctk.CTkLabel(doc_box, text="", text_color=theme.TEXT_MUTED,
                                               font=theme.FONT_MONO_SMALL)
        self.doc_status_label.pack(anchor="w", padx=12, pady=(0, 4))
        self.doc_results_frame = ctk.CTkScrollableFrame(doc_box, height=180, fg_color=theme.BG_PANEL_ALT)
        self.doc_results_frame.pack(fill="x", padx=12, pady=(0, 12))

        self._media_analysis_image_refs = []  # keep CTkImage references alive

    def _browse_tesseract_exe(self):
        path = fd.askopenfilename(
            title="Select tesseract.exe",
            filetypes=[("Tesseract executable", "tesseract.exe"), ("All executables", "*.exe")],
        )
        if path:
            self._ocr_path_entry.delete(0, tk.END)
            self._ocr_path_entry.insert(0, path)
            self._apply_tesseract_path()

    def _apply_tesseract_path(self):
        path = self._ocr_path_entry.get().strip()
        if not path:
            mb.showwarning("No path", "Enter or browse to the tesseract.exe path first.")
            return
        if not os.path.isfile(path):
            mb.showerror("File not found", f"Could not find:\n{path}")
            return
        imga.set_tesseract_cmd(path)
        _save_ocr_config({"tesseract_path": path})
        ver = imga.get_tesseract_info()
        if ver:
            self._ocr_status_label.configure(
                text=f"✅  Tesseract: {ver}", text_color=theme.ACCENT_GREEN)
        else:
            self._ocr_status_label.configure(
                text="❌  Tesseract: binary found but could not run — check the path",
                text_color=theme.ACCENT_RED)

    def _test_ocr(self):
        """Run a quick smoke-test: write a tiny test image and OCR it."""
        if not imga.is_ocr_available():
            mb.showerror("OCR not available",
                         "Tesseract is not configured. Enter the path to tesseract.exe and click 'Apply Path'.\n\n"
                         f"Download from: {TESSERACT_DOWNLOAD_URL}")
            return

        import tempfile
        try:
            import pytesseract
            from PIL import Image, ImageDraw, ImageFont
            # Create a tiny white image with black text
            img = Image.new("RGB", (300, 60), "white")
            draw = ImageDraw.Draw(img)
            draw.text((10, 15), "OCR test 1234", fill="black")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                img.save(tmp_path)
            result = pytesseract.image_to_string(Image.open(tmp_path)).strip()
            os.unlink(tmp_path)
            if result:
                mb.showinfo("OCR Test Passed ✅",
                            f"Tesseract successfully extracted text:\n\n\"{result}\"\n\n"
                            "OCR is working correctly.")
            else:
                mb.showwarning("OCR Test Warning",
                               "Tesseract ran but returned empty output on the test image.\n"
                               "It may still work on real document images.")
        except Exception as e:
            mb.showerror("OCR Test Failed", f"Error during OCR test:\n{e}")

    def _pick_person_ref_files(self):

        paths = fd.askopenfilenames(title="Select reference photo(s) of the person",
                                     filetypes=[("Images", "*.jpg *.jpeg *.png *.webp")])
        if paths:
            self.person_ref_entry.delete(0, tk.END)
            self.person_ref_entry.insert(0, ", ".join(paths))

    def _current_image_pool(self, extra_media_dir: str = "") -> list:
        """The set of local image files available to analyze — extracted
        Media folder, extra folder, and anything pulled via the scanner."""
        idx = self._build_media_index(extra_media_dir)
        return idx.image_paths()

    def _run_person_search(self):
        ref_text = self.person_ref_entry.get().strip()
        if not ref_text:
            mb.showwarning("No reference photo", "Browse to or type one or more reference photo paths.")
            return
        ref_paths = [p.strip() for p in ref_text.split(",") if p.strip()]
        extra_media_dir = self.export_media_entry.get().strip()
        self.person_status_label.configure(text="Training on reference photo(s)...")

        def worker():
            try:
                matcher, engine = imga.make_person_matcher()
                n_trained = matcher.train(ref_paths)
                pool = self._current_image_pool(extra_media_dir)
                self._run_on_main(lambda: self.person_status_label.configure(
                    text=f"[{engine.upper()}] Trained on {n_trained} reference face(s). Scanning {len(pool)} image(s)..."))
                matches = matcher.match(pool, progress=lambda m: self._run_on_main(
                    lambda m=m: self.person_status_label.configure(text=m)))

                def apply():
                    self.person_status_label.configure(
                        text=f"{len(matches)} plausible match(es) found (best first). {imga.MATCH_DISCLAIMER}")
                    self._populate_person_results(matches)
                self._run_on_main(apply)
            except ValueError as e:
                self._run_on_main(lambda err=str(e): (mb.showerror("Reference photo error", err),
                                            self.person_status_label.configure(text="")))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_person_results(self, matches):
        for w in self.person_results_frame.winfo_children():
            w.destroy()
        if not matches:
            ctk.CTkLabel(self.person_results_frame, text="No matches found.",
                         text_color=theme.TEXT_MUTED).pack(pady=8)
            return
        for m in matches[:100]:  # cap displayed results for responsiveness
            row = ctk.CTkFrame(self.person_results_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            try:
                pil_img = Image.open(m.image_path)
                pil_img.thumbnail((80, 80))
                ctk_img = ctk.CTkImage(light_image=pil_img, size=pil_img.size)
                self._media_analysis_image_refs.append(ctk_img)
                ctk.CTkLabel(row, text="", image=ctk_img).pack(side="left", padx=6)
            except Exception:
                ctk.CTkLabel(row, text="[unreadable]", width=80).pack(side="left", padx=6)
            ctk.CTkLabel(row, text=f"{os.path.basename(m.image_path)}  ({m.describe_score()})",
                         font=theme.FONT_MONO_SMALL, anchor="w").pack(side="left", padx=6)

    def _run_similar_image_scan(self):
        extra_media_dir = self.export_media_entry.get().strip()
        self.similar_status_label.configure(text="Scanning...")

        def worker():
            pool = self._current_image_pool(extra_media_dir)
            groups = imga.group_similar_images(pool, progress=lambda m: self._run_on_main(
                lambda m=m: self.similar_status_label.configure(text=m)))

            def apply():
                self.similar_status_label.configure(
                    text=f"{len(groups)} group(s) of similar/duplicate images found among {len(pool)} image(s).")
                self._populate_similar_results(groups)
            self._run_on_main(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _populate_similar_results(self, groups):
        for w in self.similar_results_frame.winfo_children():
            w.destroy()
        if not groups:
            ctk.CTkLabel(self.similar_results_frame, text="No similar/duplicate groups found.",
                         text_color=theme.TEXT_MUTED).pack(pady=8)
            return
        for i, group in enumerate(groups):
            group_frame = ctk.CTkFrame(self.similar_results_frame, fg_color=theme.BG_PANEL,
                                        border_color=theme.BORDER_GREEN, border_width=1)
            group_frame.pack(fill="x", pady=4, padx=2)
            ctk.CTkLabel(group_frame, text=f"Group {i+1} ({len(group)} files)",
                         font=theme.FONT_MONO_SMALL, text_color=theme.ACCENT_GREEN).pack(anchor="w", padx=8, pady=(4, 0))
            names_row = ctk.CTkFrame(group_frame, fg_color="transparent")
            names_row.pack(fill="x", padx=8, pady=(0, 6))
            for path in group:
                ctk.CTkLabel(names_row, text=os.path.basename(path),
                             font=theme.FONT_MONO_SMALL).pack(anchor="w")

    def _run_document_scan(self):
        extra_media_dir = self.export_media_entry.get().strip()
        self.doc_status_label.configure(text="Scanning...")

        def worker():
            pool = self._current_image_pool(extra_media_dir)
            findings = imga.scan_for_documents(pool, progress=lambda m: self._run_on_main(
                lambda m=m: self.doc_status_label.configure(text=m)))

            def apply():
                self.doc_status_label.configure(
                    text=f"{len(findings)} likely document(s) found among {len(pool)} image(s).")
                self._populate_document_results(findings)
            self._run_on_main(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _populate_document_results(self, findings):
        for w in self.doc_results_frame.winfo_children():
            w.destroy()
        if not findings:
            ctk.CTkLabel(self.doc_results_frame, text="No documents found.",
                         text_color=theme.TEXT_MUTED).pack(pady=8)
            return
        for f in findings:
            row = ctk.CTkFrame(self.doc_results_frame, fg_color="transparent")
            row.pack(fill="x", pady=3, anchor="w")
            ctk.CTkLabel(row, text=os.path.basename(f.image_path), font=theme.FONT_LABEL,
                         anchor="w").pack(anchor="w")
            preview = (f.extracted_text or "")[:120].replace("\n", " ")
            ctk.CTkLabel(row, text=f"{f.reason}" + (f' — "{preview}..."' if preview else ""),
                         font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                         wraplength=850, justify="left").pack(anchor="w")
