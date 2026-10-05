"""CloudTab — cloud tab (split out of main.py)."""

from ui.common import (
    cra,
    ctk,
    custody,
    mb,
    os,
    theme,
    threading,
    tk,
)


class CloudTabMixin:
    def _build_cloud_restore_tab(self, tab):
        outer = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(outer, text="◆ CLOUD RESTORE ASSISTANT ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            outer,
            text=("There is no supported way to pull WhatsApp's Google Drive/iCloud backup "
                  "directly — it lives in an app-restricted area only WhatsApp's own client "
                  "can read, enforced by Google/Apple regardless of credentials or consent. "
                  "The working path is to let WhatsApp itself restore it, then extract the "
                  "resulting local file with the same pipeline used everywhere else in this "
                  "tool. This tab checks prerequisites and watches for that restore to "
                  "finish — it does not perform the restore itself."),
            font=theme.FONT_SUBHEADER, text_color=theme.TEXT_MUTED,
            wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # --- Pre-flight checks ---
        preflight_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                      border_width=1, corner_radius=8)
        preflight_box.pack(fill="x", pady=6)
        ctk.CTkLabel(preflight_box, text="Step 1 — Pre-flight checks", font=theme.FONT_LABEL).pack(
            anchor="w", padx=12, pady=(10, 2))
        ctk.CTkButton(preflight_box, text="Run Pre-Flight Checks",
                      command=self._run_cloud_preflight_checks).pack(anchor="w", padx=12, pady=6)
        self.preflight_results_frame = ctk.CTkFrame(preflight_box, fg_color="transparent")
        self.preflight_results_frame.pack(fill="x", padx=12, pady=(0, 12))

        # --- Manual restore guidance ---
        guide_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                  border_width=1, corner_radius=8)
        guide_box.pack(fill="x", pady=6)
        ctk.CTkLabel(guide_box, text="Step 2 — Trigger the restore on-device (manual)",
                     font=theme.FONT_LABEL).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            guide_box,
            text=("On the device itself: open WhatsApp → if prompted on first launch, tap "
                  "\"Restore\" when it detects the Drive backup, or use Settings → Chats → "
                  "Chat backup to confirm one exists, then reinstall the app (uninstall + "
                  "reinstall from Play Store, or use WhatsApp's \"Transfer or restore\" flow "
                  "during setup) to trigger the actual restore prompt. Once you tap Restore "
                  "and it starts, come back here and click \"Capture Baseline & Start "
                  "Monitoring\" below BEFORE it finishes, so a real baseline is captured."),
            font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
            wraplength=900, justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 12))

        # --- Monitoring ---
        monitor_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                    border_width=1, corner_radius=8)
        monitor_box.pack(fill="x", pady=6)
        ctk.CTkLabel(monitor_box, text="Step 3 — Watch for restore completion",
                     font=theme.FONT_LABEL).pack(anchor="w", padx=12, pady=(10, 2))
        btn_row = ctk.CTkFrame(monitor_box, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=6)
        self.monitor_start_button = ctk.CTkButton(btn_row, text="Capture Baseline & Start Monitoring",
                                                    fg_color=theme.ACCENT_GREEN, text_color="black",
                                                    command=self._start_restore_monitoring)
        self.monitor_start_button.pack(side="left")
        self.monitor_stop_button = ctk.CTkButton(btn_row, text="Stop Monitoring", state="disabled",
                                                   command=self._stop_restore_monitoring)
        self.monitor_stop_button.pack(side="left", padx=10)
        self.monitor_status_label = ctk.CTkLabel(monitor_box, text="Not monitoring.",
                                                   text_color=theme.TEXT_MUTED)
        self.monitor_status_label.pack(anchor="w", padx=12)
        self.monitor_log = ctk.CTkTextbox(monitor_box, height=140, font=theme.FONT_MONO,
                                           fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.monitor_log.pack(fill="x", padx=12, pady=(6, 12))

        # --- Handoff ---
        handoff_box = ctk.CTkFrame(outer, fg_color=theme.BG_PANEL, border_color=theme.BORDER_GREEN,
                                    border_width=1, corner_radius=8)
        handoff_box.pack(fill="x", pady=6)
        ctk.CTkLabel(handoff_box, text="Step 4 — Extract the restored backup",
                     font=theme.FONT_LABEL).pack(anchor="w", padx=12, pady=(10, 2))
        out_row = ctk.CTkFrame(handoff_box, fg_color="transparent")
        out_row.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(out_row, text="Output folder", width=110, anchor="w").pack(side="left")
        self.cloud_restore_out_entry = ctk.CTkEntry(out_row, width=380)
        self.cloud_restore_out_entry.pack(side="left", padx=8)
        ctk.CTkButton(out_row, text="Select", width=70,
                      command=lambda: self._pick_dir(self.cloud_restore_out_entry)).pack(side="left")
        self.extract_now_button = ctk.CTkButton(handoff_box, text="Extract Now",
                                                  fg_color=theme.ACCENT_GREEN, text_color="black",
                                                  state="disabled", command=self._extract_restored_backup)
        self.extract_now_button.pack(anchor="w", padx=12, pady=(0, 12))

        self._restore_monitor_stop_flag = False
        self._restore_monitor_result = None

    def _run_cloud_preflight_checks(self):
        serial = self.device_dropdown.get()
        if not serial:
            mb.showwarning("No device", "Select a device on the ADB Extractor tab first.")
            return

        def worker():
            report = cra.run_preflight_checks(serial)
            self._run_on_main(lambda: self._populate_preflight_results(report))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_preflight_results(self, report):
        for w in self.preflight_results_frame.winfo_children():
            w.destroy()
        for check in report.checks:
            color = theme.ACCENT_GREEN if check.passed else theme.ACCENT_RED
            mark = "✓" if check.passed else "✗"
            row = ctk.CTkLabel(self.preflight_results_frame,
                                text=f"{mark} {check.name}: {check.detail}",
                                text_color=color, font=theme.FONT_MONO_SMALL,
                                wraplength=850, justify="left", anchor="w")
            row.pack(anchor="w", pady=2)

    def _start_restore_monitoring(self):
        serial = self.device_dropdown.get()
        if not serial:
            mb.showwarning("No device", "Select a device on the ADB Extractor tab first.")
            return

        self._restore_monitor_stop_flag = False
        self._restore_monitor_result = None
        self.monitor_start_button.configure(state="disabled")
        self.monitor_stop_button.configure(state="normal")
        self.extract_now_button.configure(state="disabled")
        self.monitor_status_label.configure(text="Capturing baseline...")

        def worker():
            baseline = cra.get_backup_file_status(serial)
            self._log(self.monitor_log,
                      f"Baseline: {'file exists, ' + str(baseline.size_bytes) + ' bytes' if baseline.exists else 'no backup file present yet'}")

            result = cra.poll_for_restore_completion(
                serial, baseline,
                progress=lambda m: self._log(self.monitor_log, m),
                should_stop=lambda: self._restore_monitor_stop_flag,
            )
            self._restore_monitor_result = result

            def apply():
                self.monitor_start_button.configure(state="normal")
                self.monitor_stop_button.configure(state="disabled")
                if result:
                    self.monitor_status_label.configure(
                        text=f"Likely complete — {result.path} ({result.size_bytes} bytes). "
                             f"Confirm on the device screen, then extract.",
                        text_color=theme.ACCENT_GREEN)
                    self.extract_now_button.configure(state="normal")
                else:
                    self.monitor_status_label.configure(
                        text="Stopped or timed out without detecting a stable completion.",
                        text_color=theme.ACCENT_YELLOW)
            self._run_on_main(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_restore_monitoring(self):
        self._restore_monitor_stop_flag = True
        self.monitor_stop_button.configure(state="disabled")

    def _extract_restored_backup(self):
        serial = self.device_dropdown.get()
        out_dir = self.cloud_restore_out_entry.get().strip()
        result = self._restore_monitor_result
        if not (serial and out_dir and result and result.path):
            mb.showwarning("Missing info", "Select a device, an output folder, and detect a "
                                            "completed restore first.")
            return

        self.extract_now_button.configure(state="disabled")

        def worker():
            from modules.adb_extractor import _adb_pull
            local_path = os.path.join(out_dir, os.path.basename(result.path))
            self._log(self.monitor_log, f"Pulling {result.path} ...")
            ok = _adb_pull(serial, result.path, local_path)
            if ok:
                self.session["msgstore_path"] = local_path
                self._log(self.monitor_log, f"Pulled to {local_path}")
                try:
                    custody.record(out_dir, "adb-pull-restored-backup", local_path,
                                   serial=serial, note=result.path)
                except Exception as e:
                    self._log(self.monitor_log, f"WARNING: custody logging failed: {e}")

                def apply():
                    self.encrypted_db_entry.delete(0, tk.END)
                    self.encrypted_db_entry.insert(0, local_path)
                    self.extract_now_button.configure(state="normal")
                    self.tabs.set("Decryptor")
                    mb.showinfo("Handed off to Decryptor",
                                f"Pulled the restored backup to:\n{local_path}\n\n"
                                f"Fields pre-filled on the Decryptor tab — just add the key file "
                                f"and decrypt.")
                self._run_on_main(apply)
            else:
                self._log(self.monitor_log, "Pull failed — the file may have moved or the "
                                             "restore may not actually be finished yet.")
                self._run_on_main(lambda: self.extract_now_button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()
