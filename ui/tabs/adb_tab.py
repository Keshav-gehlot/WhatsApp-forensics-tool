"""AdbTab — adb tab (split out of main.py)."""

from ui.common import (
    adb,
    ctk,
    mb,
    theme,
    threading,
    tk,
)


class AdbTabMixin:
    def _build_adb_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ ADB CONNECTION ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 6))

        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=6)
        ctk.CTkButton(row, text="Refresh Devices", command=self._refresh_devices).pack(side="left")
        self.device_dropdown = ctk.CTkComboBox(row, values=[], width=260)
        self.device_dropdown.pack(side="left", padx=10)

        tcp_row = ctk.CTkFrame(tab, fg_color="transparent")
        tcp_row.pack(fill="x", padx=20, pady=6)
        self.tcp_ip_entry = ctk.CTkEntry(tcp_row, placeholder_text="Device IP (Wi-Fi debugging)")
        self.tcp_ip_entry.pack(side="left")
        ctk.CTkButton(tcp_row, text="Connect TCP", command=self._connect_tcp).pack(side="left", padx=10)

        out_row = ctk.CTkFrame(tab, fg_color="transparent")
        out_row.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(out_row, text="Output folder:").pack(side="left")
        self.adb_output_entry = ctk.CTkEntry(out_row, width=400)
        self.adb_output_entry.pack(side="left", padx=10)
        ctk.CTkButton(out_row, text="Browse", command=lambda: self._pick_dir(self.adb_output_entry)).pack(side="left")

        method_row = ctk.CTkFrame(tab, fg_color="transparent")
        method_row.pack(fill="x", padx=20, pady=16)
        ctk.CTkButton(method_row, text="Extract (Root Method)",
                      fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=lambda: self._run_extract(root_method=True)).pack(side="left", padx=(0, 10))
        ctk.CTkButton(method_row, text="Extract (Non-Root, Android 13+)",
                      command=lambda: self._run_extract(root_method=False)).pack(side="left")

        self.adb_log = ctk.CTkTextbox(tab, height=220, font=theme.FONT_MONO,
                                       fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.adb_log.pack(fill="both", expand=True, padx=20, pady=(6, 16))
        self._log(self.adb_log, "Ready. Connect a device with USB debugging authorized, then Refresh Devices.")

    def _refresh_devices(self):
        try:
            devices = adb.list_devices()
            self.device_dropdown.configure(values=devices)
            if devices:
                self.device_dropdown.set(devices[0])
            self._log(self.adb_log, f"Found {len(devices)} authorized device(s): {devices}")
        except adb.AdbError as e:
            mb.showerror("ADB Error", str(e))

    def _connect_tcp(self):
        ip = self.tcp_ip_entry.get().strip()
        if not ip:
            return
        try:
            serial = adb.connect_tcp(ip)
            self._log(self.adb_log, f"Connected: {serial}")
            self._refresh_devices()
        except adb.AdbError as e:
            mb.showerror("ADB Error", str(e))

    def _run_extract(self, root_method: bool):
        serial = self.device_dropdown.get()
        out_dir = self.adb_output_entry.get().strip()
        if not serial or not out_dir:
            mb.showwarning("Missing info", "Select a device and an output folder first.")
            return

        def worker():
            try:
                fn = adb.extract_root if root_method else adb.extract_nonroot
                results = fn(serial, out_dir, progress=lambda m: self._log(self.adb_log, m))
                self._protect_if_case(out_dir)
                self._log(self.adb_log, f"Done: {results}")
                if results.get("msgstore"):
                    self.session["msgstore_path"] = results["msgstore"]
                if results.get("wa_db"):
                    self.session["wa_db_path"] = results["wa_db"]
                if results.get("media"):
                    self.session["media_dir"] = results["media"]
                if results.get("key"):
                    self.session["key_path"] = results["key"]
                    key_path = results["key"]
                    self._run_on_main(lambda: (self.key_entry.delete(0, tk.END),
                                                self.key_entry.insert(0, key_path)))
                if results.get("msgstore"):
                    msgstore_path = results["msgstore"]
                    self._run_on_main(lambda: (self.encrypted_db_entry.delete(0, tk.END),
                                                self.encrypted_db_entry.insert(0, msgstore_path)))
            except adb.AdbError as e:
                self._log(self.adb_log, f"ERROR: {e}")

        threading.Thread(target=worker, daemon=True).start()
