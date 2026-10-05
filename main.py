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

from ui.common import (
    APP_TITLE,
    StatCard,
    _LOCAL_TESSDATA,
    _apply_ocr_config,
    _autodetect_tesseract,
    _load_ocr_config,
    _save_ocr_config,
    ctk,
    custody,
    fd,
    imga,
    os,
    queue,
    theme,
    tk,
)
from ui.tabs.adb_tab import AdbTabMixin
from ui.tabs.cloud_tab import CloudTabMixin
from ui.tabs.scan_tab import ScanTabMixin
from ui.tabs.decrypt_tab import DecryptTabMixin
from ui.tabs.chat_tab import ChatTabMixin
from ui.tabs.calls_tab import CallsTabMixin
from ui.tabs.deleted_media_tab import DeletedMediaTabMixin
from ui.tabs.media_analysis_tab import MediaAnalysisTabMixin
from ui.tabs.search_tab import SearchTabMixin
from ui.tabs.export_tab import ExportTabMixin
from ui.tabs.case_tab import CaseTabMixin
from ui.tabs.timeline_tab import TimelineTabMixin
from ui.nav import SidebarShell


class App(CaseTabMixin, TimelineTabMixin, AdbTabMixin, CloudTabMixin, ScanTabMixin, DecryptTabMixin, ChatTabMixin, CallsTabMixin, DeletedMediaTabMixin, MediaAnalysisTabMixin, SearchTabMixin, ExportTabMixin, ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1360x860")
        self.minsize(1140, 740)
        self.configure(fg_color=theme.BG_APP)

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

    @staticmethod
    def _pill(parent, text, color):
        return ctk.CTkLabel(parent, text=text, font=theme.FONT_PILL, text_color=color,
                            fg_color=theme.BG_CARD, corner_radius=20, padx=12, pady=5)

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=theme.BG_HEADER, corner_radius=16,
                              border_color=theme.BORDER, border_width=1, height=68)
        header.pack(fill="x", padx=theme.PAD_LG, pady=(theme.PAD_LG, theme.PAD_MD))
        header.pack_propagate(False)

        # -- brand (left) ---------------------------------------------------
        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", padx=(18, 0))
        ctk.CTkLabel(left, text="🛡️", font=("Segoe UI Emoji", 22)).pack(side="left", padx=(0, 10))
        title = ctk.CTkFrame(left, fg_color="transparent")
        title.pack(side="left")
        ctk.CTkLabel(title, text="WHATSAPP FORENSICATOR", font=theme.FONT_H1,
                     text_color=theme.TEXT_PRIMARY).pack(anchor="w")
        ctk.CTkLabel(title, text="Offline Cyber Forensic Suite · v2.0", font=theme.FONT_SUB,
                     text_color=theme.TEXT_MUTED).pack(anchor="w")

        # -- custody + status (right) --------------------------------------
        right = ctk.CTkFrame(header, fg_color="transparent")
        right.pack(side="right", padx=(0, 18))

        # Chain of custody: examiner + case id stamped on every audit record.
        self.case_entry = ctk.CTkEntry(right, width=120, placeholder_text="Case ID",
                                       font=theme.FONT_SMALL, fg_color=theme.BG_INPUT,
                                       border_color=theme.BORDER)
        self.examiner_entry = ctk.CTkEntry(right, width=150, placeholder_text="Examiner",
                                           font=theme.FONT_SMALL, fg_color=theme.BG_INPUT,
                                           border_color=theme.BORDER)
        self.examiner_entry.pack(side="right", padx=(6, 0))
        self.case_entry.pack(side="right", padx=6)
        self.examiner_entry.bind("<FocusOut>", lambda e: custody.set_examiner(self.examiner_entry.get()))
        self.case_entry.bind("<FocusOut>", lambda e: custody.set_case_id(self.case_entry.get()))

        divider = ctk.CTkFrame(right, width=1, fg_color=theme.BORDER)
        divider.pack(side="right", fill="y", padx=10, pady=16)

        ocr_ok = imga.is_ocr_available()
        self._pill(right, "● OFFLINE", theme.ACCENT).pack(side="right", padx=4)
        self.header_ocr_badge = self._pill(
            right, "OCR READY" if ocr_ok else "OCR HEURISTIC",
            theme.ACCENT_CYAN if ocr_ok else theme.ACCENT_YELLOW)
        self.header_ocr_badge.pack(side="right", padx=4)
        self.header_db_badge = self._pill(right, "NO DATABASE", theme.TEXT_MUTED)
        self.header_db_badge.pack(side="right", padx=4)

    def _build_stats_bar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=theme.PAD_LG, pady=(0, theme.PAD_MD))
        self.stat_chats = StatCard(bar, "💬", 0, "Chats", accent_color=theme.ACCENT)
        self.stat_msgs = StatCard(bar, "✉️", 0, "Messages", accent_color=theme.ACCENT_CYAN)
        self.stat_deleted = StatCard(bar, "🗑️", 0, "Deleted", accent_color=theme.ACCENT_RED)
        self.stat_media = StatCard(bar, "🖼️", 0, "Media", accent_color=theme.ACCENT_BLUE)
        self.stat_calls = StatCard(bar, "📞", 0, "Calls", accent_color=theme.ACCENT_PURPLE)
        self.stat_contacts = StatCard(bar, "👤", 0, "Contacts", accent_color=theme.ACCENT_YELLOW)
        cards = (self.stat_chats, self.stat_msgs, self.stat_deleted,
                 self.stat_media, self.stat_calls, self.stat_contacts)
        for i, card in enumerate(cards):
            bar.grid_columnconfigure(i, weight=1, uniform="stat")
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))

    # Sidebar navigation: (section caption, [tab names], [build methods]).
    _NAV_LAYOUT = [
        ("Acquire", [
            ("📱  ADB Extractor", "_build_adb_tab"),
            ("☁️  Cloud Restore", "_build_cloud_restore_tab"),
            ("🔍  Filesystem Scan", "_build_scan_tab"),
        ]),
        ("Decode", [
            ("🔐  Decryptor", "_build_decrypt_tab"),
        ]),
        ("Review", [
            ("💬  Chat Viewer", "_build_chat_tab"),
            ("📞  Call Analysis", "_build_calls_tab"),
            ("🗑️  Deleted Media", "_build_deleted_media_tab"),
            ("🖼️  Media Analysis", "_build_media_analysis_tab"),
            ("🔎  Search", "_build_search_tab"),
            ("🕒  Timeline", "_build_timeline_tab"),
        ]),
        ("Report", [
            ("📦  Export", "_build_export_tab"),
            ("📁  Case", "_build_case_tab"),
        ]),
    ]

    def _build_tabs(self):
        self.tabs = SidebarShell(self)
        first = None
        for caption, items in self._NAV_LAYOUT:
            self.tabs.section(caption)
            for name, builder in items:
                frame = self.tabs.add(name)
                getattr(self, builder)(frame)
                if first is None:
                    first = name
        self.tabs.set(first)

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
        self.stat_deleted.set_value(sum(1 for c in chats for m in c.messages if getattr(m, "is_deleted", False)))

        if chats:
            self.header_db_badge.configure(
                text=f"● {len(chats)} CHATS LOADED",
                text_color=theme.ACCENT,
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
