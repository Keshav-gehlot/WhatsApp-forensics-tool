"""
WhatsApp Forensicator (offline edition)
----------------------------------------
Desktop forensics tool for extracting, decrypting, and reviewing WhatsApp
data from a device you have lawful, physical/authorized access to.

Scope, deliberately: this build does NOT include live network
interception, VoIP call sniffing, or real-time geolocation of a remote
party during a call. Everything here operates on data already extracted
from a device already in the examiner's possession/authorization.

Run:
    pip install -r requirements.txt
    python main.py
"""

import os
import io
import json
import queue
import shutil
import subprocess
import threading
import webbrowser
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb

import customtkinter as ctk
from PIL import Image

from ui import theme
from modules import adb_extractor as adb
from modules import decryptor as dec
from modules import fs_scanner as scanner
from modules import media_resolver
from modules.media_resolver import MediaIndex, ThumbnailIndex, find_deleted_media
from modules.db_parser import WaDatabase
from modules import export as exp
from modules import image_analysis as imga
from modules.message_search import search_messages
from modules import cloud_restore_assistant as cra

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

APP_TITLE = "WHATSAPP FORENSICATOR — by Cyber Octopus (offline edition)"

# ---------------------------------------------------------------------------
# Tesseract OCR configuration helpers
# ---------------------------------------------------------------------------

_OCR_CONFIG_PATH = os.path.join(os.path.dirname(__file__), ".ocr_config.json")

# Project-bundled binary (takes priority over system installs)
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_LOCAL_TESSERACT = os.path.join(_PROJECT_ROOT, "bin", "tesseract", "tesseract.exe")
_LOCAL_TESSDATA  = os.path.join(_PROJECT_ROOT, "bin", "tesseract", "tessdata")

# Fallback: well-known Windows install paths (UB-Mannheim installer)
_TESSERACT_SEARCH_PATHS = [
    _LOCAL_TESSERACT,                                                                    # bundled in bin/tesseract/
    os.path.join(_PROJECT_ROOT, "tesseract.exe"),                                       # legacy root bundle fallback
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",                                    # system 64-bit
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",                              # system 32-bit
    os.path.join(os.path.expanduser("~"), "AppData", "Local",
                 "Programs", "Tesseract-OCR", "tesseract.exe"),                          # user install
]

TESSERACT_DOWNLOAD_URL = "https://github.com/UB-Mannheim/tesseract/wiki"


def _load_ocr_config() -> dict:
    try:
        with open(_OCR_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_ocr_config(cfg: dict) -> None:
    try:
        with open(_OCR_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


def _autodetect_tesseract() -> str | None:
    """Returns the first usable tesseract.exe found, checking:
    1. The project-bundled binary (e:\\wa_forensicator\\tesseract.exe)
    2. System PATH
    3. Well-known Windows install locations
    """
    for p in _TESSERACT_SEARCH_PATHS:
        if os.path.isfile(p):
            return p
    # PATH fallback
    return shutil.which("tesseract")


def _apply_ocr_config(exe_path: str | None, tessdata_path: str | None = None) -> None:
    """Configure pytesseract to use the given binary and tessdata directory.
    Sets TESSDATA_PREFIX so Tesseract can locate its language models even
    when they're not in the system default location."""
    if not exe_path or not os.path.isfile(exe_path):
        return
    imga.set_tesseract_cmd(exe_path)
    # Resolve tessdata: use the explicit override, the local bundled folder,
    # or the directory next to the exe — whichever exists first.
    tdata = (
        tessdata_path
        or (_LOCAL_TESSDATA if os.path.isdir(_LOCAL_TESSDATA) else None)
        or os.path.join(os.path.dirname(exe_path), "tessdata")
    )
    if tdata and os.path.isdir(tdata):
        os.environ["TESSDATA_PREFIX"] = tdata

class StatCard(ctk.CTkFrame):
    def __init__(self, master, icon, value, label, accent_color=theme.ACCENT_GREEN, **kwargs):
        super().__init__(master, fg_color=theme.BG_PANEL_ALT,
                          border_color=theme.BORDER_GREEN, border_width=1,
                          corner_radius=10, **kwargs)
        self.accent_color = accent_color
        
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(expand=True, fill="both", padx=10, pady=8)

        top_row = ctk.CTkFrame(container, fg_color="transparent")
        top_row.pack(fill="x")
        ctk.CTkLabel(top_row, text=icon, font=("Segoe UI Emoji", 16)).pack(side="left", padx=(2, 6))
        ctk.CTkLabel(top_row, text=label, font=theme.FONT_STAT_LABEL,
                     text_color=theme.TEXT_MUTED).pack(side="left")

        self.value_label = ctk.CTkLabel(container, text=str(value), font=theme.FONT_STAT_VALUE,
                                         text_color=self.accent_color)
        self.value_label.pack(anchor="w", padx=4, pady=(2, 2))

    def set_value(self, value):
        self.value_label.configure(text=str(value))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1220x800")
        self.configure(fg_color=theme.BG_PRIMARY)

        # Force window to foreground and gain focus on launch
        self.lift()
        self.attributes("-topmost", True)
        self.after(500, lambda: self.attributes("-topmost", False))
        self.focus_force()

        # Shared session state
        self.session = {
            "msgstore_path": None,
            "wa_db_path": None,
            "key_path": None,
            "wa_database": None,   # WaDatabase instance once loaded
            "chats": [],
            "calls": [],
            "media_dir": None,      # extracted Media folder (from ADB pull)
            "pulled_files": [],     # individual full-media files pulled via the scanner tab
            "pulled_thumbnails": [],  # individual thumbnail-cache files pulled via the scanner tab
        }

        # Thread-safe UI updates from background threads.
        self._ui_queue = queue.Queue()

        # Apply saved/auto-detected tesseract path before building the UI
        cfg = _load_ocr_config()
        saved_path = cfg.get("tesseract_path")
        saved_tessdata = cfg.get("tessdata_path")
        if not saved_path or not os.path.isfile(saved_path):
            saved_path = _autodetect_tesseract()
            if saved_path:
                _save_ocr_config({
                    "tesseract_path": saved_path,
                    "tessdata_path": _LOCAL_TESSDATA if os.path.isdir(_LOCAL_TESSDATA) else saved_tessdata,
                })
                saved_tessdata = _LOCAL_TESSDATA if os.path.isdir(_LOCAL_TESSDATA) else saved_tessdata
        _apply_ocr_config(saved_path, saved_tessdata)

        self._build_header()
        self._build_stats_bar()
        self._build_tabs()

        self.after(100, self._poll_ui_queue)

    def _poll_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "log":
                    widget, message = payload
                    widget.insert(tk.END, f"{message}\n")
                    widget.see(tk.END)
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.after(100, self._poll_ui_queue)

    def _run_on_main(self, fn):
        """Schedule a zero-arg callable to run on the main thread."""
        self._ui_queue.put(("call", fn))

    # ---------------------------------------------------------------- UI

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=theme.BG_HEADER, corner_radius=12,
                              border_color=theme.BORDER_GREEN, border_width=1)
        header.pack(fill="x", padx=20, pady=(14, 8))

        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", padx=16, pady=10)

        ctk.CTkLabel(left, text="🛡️  WHATSAPP FORENSICATOR",
                     font=theme.FONT_HEADER, text_color=theme.TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(left, text="v2.0 · Offline Cyber Forensic Suite",
                     font=theme.FONT_SUBHEADER, text_color=theme.ACCENT_CYAN).pack(side="left", padx=12)

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=16, pady=10)

        # Security Badge
        ctk.CTkLabel(right, text="🔒 AIR-GAPPED / OFFLINE", font=theme.FONT_MONO_SMALL,
                     text_color=theme.ACCENT_GREEN, fg_color=theme.BG_PANEL_ALT,
                     corner_radius=6, padx=10, pady=4).pack(side="right", padx=4)

        # OCR Badge
        ocr_status = "⚡ OCR ENGINE READY" if imga.is_ocr_available() else "⚠️ OCR HEURISTIC ONLY"
        ocr_color = theme.ACCENT_CYAN if imga.is_ocr_available() else theme.ACCENT_YELLOW
        self.header_ocr_badge = ctk.CTkLabel(right, text=ocr_status, font=theme.FONT_MONO_SMALL,
                                             text_color=ocr_color, fg_color=theme.BG_PANEL_ALT,
                                             corner_radius=6, padx=10, pady=4)
        self.header_ocr_badge.pack(side="right", padx=4)

        # Database Loaded Badge
        self.header_db_badge = ctk.CTkLabel(right, text="📂 DB: NOT LOADED", font=theme.FONT_MONO_SMALL,
                                            text_color=theme.TEXT_MUTED, fg_color=theme.BG_PANEL_ALT,
                                            corner_radius=6, padx=10, pady=4)
        self.header_db_badge.pack(side="right", padx=4)

    def _build_stats_bar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(0, 10))
        self.stat_chats = StatCard(bar, "💬", 0, "Chats", accent_color=theme.ACCENT_GREEN)
        self.stat_msgs = StatCard(bar, "✉️", 0, "Messages", accent_color=theme.ACCENT_CYAN)
        self.stat_deleted = StatCard(bar, "🗑️", 0, "Deleted Found", accent_color=theme.ACCENT_RED)
        self.stat_media = StatCard(bar, "🖼️", 0, "Media Files", accent_color=theme.ACCENT_BLUE)
        self.stat_calls = StatCard(bar, "📞", 0, "Call Logs", accent_color=theme.ACCENT_PURPLE)
        self.stat_contacts = StatCard(bar, "👤", 0, "Contacts", accent_color=theme.ACCENT_YELLOW)
        for card in (self.stat_chats, self.stat_msgs, self.stat_deleted,
                     self.stat_media, self.stat_calls, self.stat_contacts):
            card.pack(side="left", expand=True, fill="both", padx=3)

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=theme.BG_PANEL,
            segmented_button_selected_color=theme.BTN_PRIMARY_BG,
            segmented_button_selected_hover_color=theme.BTN_PRIMARY_HOVER,
            segmented_button_unselected_color=theme.BG_PANEL_ALT,
            segmented_button_unselected_hover_color=theme.BTN_SECONDARY_HOVER,
            text_color=theme.TEXT_PRIMARY,
            corner_radius=12
        )
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        for name in [
            "📱 ADB Extractor",
            "☁️ Cloud Restore",
            "🔍 Filesystem Scan",
            "🔐 Decryptor",
            "💬 Chat Viewer",
            "📞 Call Analysis",
            "🗑️ Deleted Media",
            "🖼️ Media Analysis",
            "🔎 Search",
            "📦 Export"
        ]:
            self.tabs.add(name)

        self._build_adb_tab(self.tabs.tab("📱 ADB Extractor"))
        self._build_cloud_restore_tab(self.tabs.tab("☁️ Cloud Restore"))
        self._build_scan_tab(self.tabs.tab("🔍 Filesystem Scan"))
        self._build_decrypt_tab(self.tabs.tab("🔐 Decryptor"))
        self._build_chat_tab(self.tabs.tab("💬 Chat Viewer"))
        self._build_calls_tab(self.tabs.tab("📞 Call Analysis"))
        self._build_deleted_media_tab(self.tabs.tab("🗑️ Deleted Media"))
        self._build_media_analysis_tab(self.tabs.tab("🖼️ Media Analysis"))
        self._build_search_tab(self.tabs.tab("🔎 Search"))
        self._build_export_tab(self.tabs.tab("📦 Export"))

    # ----------------------------------------------------------- ADB tab

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

    # ------------------------------------------------ Cloud Restore tab

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

    # ----------------------------------------------------------- Scan tab

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

    # ------------------------------------------------------- Decrypt tab

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
        try:
            result = dec.decrypt_backup(key, enc, out)
            self.session["msgstore_path"] = result.output_path
            self._log(self.decrypt_log, f"Decrypted ({result.backup_format}) -> {result.output_path}")
            mb.showinfo("Success", f"Decrypted to:\n{result.output_path}")
        except dec.DecryptError as e:
            self._log(self.decrypt_log, f"ERROR: {e}")
            mb.showerror("Decrypt Error", str(e))

    # ---------------------------------------------------------- Chat tab

    def _build_chat_tab(self, tab):
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=10)
        self.load_db_button = ctk.CTkButton(top, text="📂 Load Database", fg_color=theme.BTN_PRIMARY_BG,
                                             hover_color=theme.BTN_PRIMARY_HOVER, command=self._load_database)
        self.load_db_button.pack(side="left")
        self.chat_search_entry = ctk.CTkEntry(top, placeholder_text="Filter by contact name or number...",
                                             fg_color=theme.BG_INPUT, border_color=theme.BORDER_GREEN)
        self.chat_search_entry.pack(side="left", padx=10, fill="x", expand=True)
        ctk.CTkButton(top, text="🔍 Search", fg_color=theme.BTN_SECONDARY_BG,
                      hover_color=theme.BTN_SECONDARY_HOVER, command=self._filter_chats).pack(side="left")

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self.chat_list = ctk.CTkScrollableFrame(body, width=280, fg_color=theme.BG_PANEL_ALT,
                                                corner_radius=10, border_color=theme.BORDER_GREEN, border_width=1)
        self.chat_list.pack(side="left", fill="y", padx=(0, 10))

        self.chat_view = ctk.CTkTextbox(body, font=theme.FONT_MONO,
                                         fg_color=theme.BG_INPUT, text_color=theme.TEXT_PRIMARY,
                                         border_color=theme.BORDER_GREEN, border_width=1, corner_radius=10)
        self.chat_view.pack(side="left", fill="both", expand=True)

    def _load_database(self):
        msgstore = self.session.get("msgstore_path")
        if not msgstore:
            msgstore = fd.askopenfilename(title="Select decrypted msgstore.db",
                                           filetypes=[("SQLite DB", "*.db")])
        if not msgstore:
            return
        wa_db = self.session.get("wa_db_path")

        self._run_on_main(lambda: self._set_load_button_state(False, "Loading..."))

        def worker():
            try:
                db = WaDatabase(msgstore, wa_db)
                chats = db.get_chats()
                calls = db.get_calls()
                self.session["wa_database"] = db
                self.session["chats"] = chats
                self.session["calls"] = calls

                def apply():
                    self._refresh_stats()
                    self._populate_chat_list(chats)
                    self._set_load_button_state(True, "📂 Load Database")
                self._run_on_main(apply)
            except Exception as e:
                self._run_on_main(lambda: (mb.showerror("Load Error", str(e)),
                                            self._set_load_button_state(True, "📂 Load Database")))

        threading.Thread(target=worker, daemon=True).start()

    def _set_load_button_state(self, enabled: bool, text: str):
        self.load_db_button.configure(state=("normal" if enabled else "disabled"), text=text)

    def _populate_chat_list(self, chats):
        for widget in self.chat_list.winfo_children():
            widget.destroy()
        for chat in chats:
            btn = ctk.CTkButton(self.chat_list, text=f"{chat.display_name}\n{len(chat.messages)} msgs",
                                 anchor="w", fg_color="transparent",
                                 command=lambda c=chat: self._show_chat(c))
            btn.pack(fill="x", pady=2, padx=4)

    def _show_chat(self, chat):
        self.chat_view.delete("1.0", tk.END)
        self.chat_view.insert(tk.END, f"Chat with {chat.display_name} ({chat.jid})\n")
        self.chat_view.insert(tk.END, "=" * 60 + "\n\n")
        for m in chat.messages:
            who = "Me" if m.from_me else chat.display_name
            ts = m.timestamp.strftime("%Y-%m-%d %H:%M") if m.timestamp else "?"
            self.chat_view.insert(tk.END, f"[{ts}] {who}: {m.text}\n")

    def _filter_chats(self):
        query = self.chat_search_entry.get().strip().lower()
        chats = self.session.get("chats", [])
        if not query:
            self._populate_chat_list(chats)
            return
        filtered = [c for c in chats if query in c.display_name.lower() or query in c.jid.lower()]
        self._populate_chat_list(filtered)

    # --------------------------------------------------------- Calls tab

    def _build_calls_tab(self, tab):
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=10)
        ctk.CTkButton(top, text="Refresh Calls", command=self._refresh_calls).pack(side="left")

        self.calls_view = ctk.CTkTextbox(tab, font=theme.FONT_MONO_SMALL,
                                          fg_color=theme.BG_INPUT, text_color=theme.ACCENT_GREEN)
        self.calls_view.pack(fill="both", expand=True, padx=20, pady=(0, 16))

    def _refresh_calls(self):
        calls = self.session.get("calls", [])
        self.calls_view.delete("1.0", tk.END)
        header = f"{'TYPE':<8}{'NUMBER':<20}{'VIDEO':<8}{'DURATION':<10}{'TIMESTAMP':<20}\n"
        self.calls_view.insert(tk.END, header)
        self.calls_view.insert(tk.END, "-" * 66 + "\n")
        for c in calls:
            ts = c.timestamp.strftime("%Y-%m-%d %H:%M:%S") if c.timestamp else "?"
            dur = f"{c.duration_seconds // 60}:{c.duration_seconds % 60:02d}"
            self.calls_view.insert(
                tk.END,
                f"{c.call_type:<8}{c.phone_number:<20}{'Y' if c.is_video else 'N':<8}{dur:<10}{ts:<20}\n"
            )

    # ------------------------------------------------- Deleted Media tab

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

    # -------------------------------------------------- Media Analysis tab

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

    # -------------------------------------------------- OCR Setup handlers

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
                matcher = imga.PersonMatcher()
                n_trained = matcher.train(ref_paths)
                pool = self._current_image_pool(extra_media_dir)
                self._run_on_main(lambda: self.person_status_label.configure(
                    text=f"Trained on {n_trained} reference face(s). Scanning {len(pool)} image(s)..."))
                matches = matcher.match(pool, progress=lambda m: self._run_on_main(
                    lambda m=m: self.person_status_label.configure(text=m)))

                def apply():
                    self.person_status_label.configure(
                        text=f"{len(matches)} plausible match(es) found (best first).")
                    self._populate_person_results(matches)
                self._run_on_main(apply)
            except ValueError as e:
                self._run_on_main(lambda: (mb.showerror("Reference photo error", str(e)),
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
            ctk.CTkLabel(row, text=f"{os.path.basename(m.image_path)}  (distance: {m.confidence:.1f})",
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

    # ---------------------------------------------------- Search Messages tab

    def _build_search_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ SEARCH MESSAGES ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 6))

        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=6)
        self.search_keyword_entry = ctk.CTkEntry(top, width=360, placeholder_text="word or phrase to search for")
        self.search_keyword_entry.pack(side="left")
        self.search_case_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(top, text="Case-sensitive", variable=self.search_case_var).pack(side="left", padx=10)
        self.search_whole_word_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(top, text="Whole word only", variable=self.search_whole_word_var).pack(side="left", padx=10)
        ctk.CTkButton(top, text="Search", fg_color=theme.ACCENT_GREEN, text_color="black",
                      command=self._run_message_search).pack(side="left", padx=10)

        self.search_status_label = ctk.CTkLabel(tab, text="", text_color=theme.TEXT_MUTED)
        self.search_status_label.pack(anchor="w", padx=20)

        self.search_results_frame = ctk.CTkScrollableFrame(tab, fg_color=theme.BG_PANEL_ALT)
        self.search_results_frame.pack(fill="both", expand=True, padx=20, pady=(6, 16))

    def _run_message_search(self):
        chats = self.session.get("chats", [])
        if not chats:
            mb.showwarning("No database loaded", "Load a database on the Chat Viewer tab first.")
            return
        keyword = self.search_keyword_entry.get().strip()
        case_sensitive = self.search_case_var.get()
        whole_word = self.search_whole_word_var.get()

        hits = search_messages(chats, keyword, case_sensitive=case_sensitive, whole_word=whole_word)
        self.search_status_label.configure(text=f"{len(hits)} message(s) matched \"{keyword}\".")
        self._populate_search_results(hits)

    def _populate_search_results(self, hits):
        for w in self.search_results_frame.winfo_children():
            w.destroy()
        if not hits:
            ctk.CTkLabel(self.search_results_frame, text="No matches.",
                         text_color=theme.TEXT_MUTED).pack(pady=10)
            return
        for h in hits[:300]:  # cap for responsiveness on very broad searches
            row = ctk.CTkFrame(self.search_results_frame, fg_color=theme.BG_PANEL,
                                border_color=theme.BORDER_GREEN, border_width=1, corner_radius=6)
            row.pack(fill="x", pady=3, padx=2)
            ts = h.message.timestamp.strftime("%Y-%m-%d %H:%M") if h.message.timestamp else "unknown time"
            who = "Me" if h.message.from_me else h.chat_name
            ctk.CTkLabel(row, text=f"{h.chat_name}  ·  {who}  ·  {ts}", font=theme.FONT_LABEL,
                         anchor="w").pack(anchor="w", padx=8, pady=(6, 0))
            ctk.CTkLabel(row, text=h.snippet, font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                         wraplength=850, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

    # -------------------------------------------------------- Export tab

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
                           ("calls_xlsx", "Call log -> Excel")]:
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
        extra_media_dir = self.export_media_entry.get().strip()  # read on main thread

        def worker():
            try:
                media_index = self._build_media_index(extra_media_dir)
                if len(media_index):
                    self._log(self.export_log, f"Media index: {len(media_index)} local file(s) available to embed.")
                written = exp.export_all(chats, calls, out_dir, formats, media_index=media_index)
                for fmt, paths in written.items():
                    self._log(self.export_log, f"{fmt}: {len(paths)} file(s) written")
                self._run_on_main(lambda: mb.showinfo("Export complete", f"Files written to:\n{out_dir}"))
            except Exception as e:
                self._log(self.export_log, f"ERROR: {e}")
                self._run_on_main(lambda: mb.showerror("Export Error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ helpers

    def _refresh_stats(self):
        chats = self.session.get("chats", [])
        calls = self.session.get("calls", [])
        total_msgs = sum(len(c.messages) for c in chats)
        media_msgs = sum(1 for c in chats for m in c.messages if m.media_relative_path)
        self.stat_chats.set_value(len(chats))
        self.stat_msgs.set_value(total_msgs)
        self.stat_calls.set_value(len(calls))
        self.stat_contacts.set_value(len({c.jid for c in chats}))
        self.stat_media.set_value(media_msgs)
        self.stat_deleted.set_value(0)
        
        if chats:
            self.header_db_badge.configure(
                text=f"📂 DB: LOADED ({len(chats)} CHATS)",
                text_color=theme.ACCENT_GREEN
            )

    def _log(self, widget, message):
        # Safe to call from any thread — actual widget mutation happens
        # only on the main thread via _poll_ui_queue.
        self._ui_queue.put(("log", (widget, message)))

    def _pick_dir(self, entry):
        path = fd.askdirectory()
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)

    def _pick_file(self, entry):
        path = fd.askopenfilename()
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)


if __name__ == "__main__":
    app = App()
    app.mainloop()
