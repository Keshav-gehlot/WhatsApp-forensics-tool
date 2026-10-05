"""ChatTab — chat tab (split out of main.py)."""

from ui.common import (
    os,
    WaDatabase,
    ctk,
    fd,
    fmt_ts,
    mb,
    theme,
    threading,
    tk,
)


class ChatTabMixin:
    def _build_chat_tab(self, tab):
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=10)
        self.load_db_button = ctk.CTkButton(top, text="📂  Load Database", width=150,
                                             fg_color=theme.BTN_PRIMARY_BG, text_color="#04150E",
                                             hover_color=theme.BTN_PRIMARY_HOVER, command=self._load_database)
        self.load_db_button.pack(side="left")
        self.chat_search_entry = ctk.CTkEntry(top, placeholder_text="Filter by contact name or number…",
                                             fg_color=theme.BG_INPUT, border_color=theme.BORDER)
        self.chat_search_entry.pack(side="left", padx=10, fill="x", expand=True)
        self.chat_search_entry.bind("<Return>", lambda e: self._filter_chats())
        ctk.CTkButton(top, text="Search", width=80, fg_color=theme.BTN_SECONDARY_BG,
                      hover_color=theme.BTN_SECONDARY_HOVER, command=self._filter_chats).pack(side="left")

        body = ctk.CTkFrame(tab, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(0, 4))

        self.chat_list = ctk.CTkScrollableFrame(body, width=270, fg_color=theme.BG_CARD,
                                                corner_radius=12, border_color=theme.BORDER, border_width=1)
        self.chat_list.pack(side="left", fill="y", padx=(0, 12))

        self.chat_view = ctk.CTkScrollableFrame(body, fg_color=theme.BG_INPUT,
                                                corner_radius=12, border_color=theme.BORDER, border_width=1)
        self.chat_view.pack(side="left", fill="both", expand=True)
        self._chat_header = None
        self._show_chat_placeholder()

    def _show_chat_placeholder(self):
        for w in self.chat_view.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.chat_view, text="Select a conversation to view messages",
                     font=theme.FONT_BODY, text_color=theme.TEXT_FAINT).pack(pady=40)

    def _load_database(self):
        msgstore = self.session.get("msgstore_path")
        if not msgstore:
            msgstore = fd.askopenfilename(title="Select decrypted msgstore.db",
                                           filetypes=[("SQLite DB", "*.db")])
        if not msgstore:
            return
        wa_db = self.session.get("wa_db_path")
        if self.case and os.path.abspath(msgstore).startswith(self.case.evidence_dir):
            try:   # never open evidence directly: work on a verified copy
                msgstore = self.case.working_copy(msgstore)
            except Exception as e:
                mb.showerror("Case error", str(e)); return

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
                self._run_on_main(lambda err=str(e): (mb.showerror("Load Error", err),
                                            self._set_load_button_state(True, "📂 Load Database")))

        threading.Thread(target=worker, daemon=True).start()

    def _set_load_button_state(self, enabled: bool, text: str):
        self.load_db_button.configure(state=("normal" if enabled else "disabled"), text=text)

    _BUBBLE_CAP = 400   # max bubbles rendered at once, for responsiveness

    def _populate_chat_list(self, chats):
        for widget in self.chat_list.winfo_children():
            widget.destroy()
        if not chats:
            ctk.CTkLabel(self.chat_list, text="No chats", text_color=theme.TEXT_FAINT,
                         font=theme.FONT_SMALL).pack(pady=16)
            return
        for chat in chats:
            btn = ctk.CTkButton(
                self.chat_list, anchor="w", height=46, corner_radius=9,
                text=f"  {chat.display_name}\n  {len(chat.messages)} messages",
                font=theme.FONT_BODY, fg_color="transparent",
                hover_color=theme.NAV_HOVER, text_color=theme.TEXT_SECONDARY,
                command=lambda c=chat: self._show_chat(c))
            btn.pack(fill="x", pady=2, padx=4)

    def _show_chat(self, chat):
        for w in self.chat_view.winfo_children():
            w.destroy()

        head = ctk.CTkFrame(self.chat_view, fg_color=theme.BG_CARD, corner_radius=10)
        head.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(head, text=chat.display_name, font=theme.FONT_LABEL,
                     text_color=theme.TEXT_PRIMARY).pack(anchor="w", padx=14, pady=(8, 0))
        ctk.CTkLabel(head, text=f"{chat.jid}  ·  {len(chat.messages)} messages",
                     font=theme.FONT_SMALL, text_color=theme.TEXT_MUTED).pack(anchor="w", padx=14, pady=(0, 8))

        messages = chat.messages[-self._BUBBLE_CAP:]
        if len(chat.messages) > self._BUBBLE_CAP:
            ctk.CTkLabel(self.chat_view,
                         text=f"Showing the most recent {self._BUBBLE_CAP:,} of {len(chat.messages):,} messages "
                              f"— export for the full transcript",
                         font=theme.FONT_SMALL, text_color=theme.TEXT_FAINT).pack(pady=(0, 6))

        for m in messages:
            self._render_bubble(m, chat.display_name)

    def _render_bubble(self, m, contact_name):
        me = bool(m.from_me)
        row = ctk.CTkFrame(self.chat_view, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=3)

        bubble = ctk.CTkFrame(row, fg_color=theme.BUBBLE_ME if me else theme.BUBBLE_THEM,
                              corner_radius=12)
        bubble.pack(side="right" if me else "left", padx=(60, 0) if me else (0, 60))

        txt = m.text or ("🖼️ media" if getattr(m, "media_relative_path", None) else "")
        if getattr(m, "is_deleted", False):
            txt = "🚫 message deleted for everyone" + (f"\n{txt}" if txt else "")
        if txt:
            ctk.CTkLabel(bubble, text=txt, font=theme.FONT_BUBBLE, justify="left", wraplength=440,
                         text_color=theme.BUBBLE_ME_TX if me else theme.BUBBLE_THEM_TX).pack(
                anchor="w", padx=12, pady=(7, 1))
        meta = ("Me · " if me else f"{contact_name} · ") + fmt_ts(m.timestamp, empty="?")
        ctk.CTkLabel(bubble, text=meta, font=theme.FONT_BUBBLE_META,
                     text_color=theme.TEXT_MUTED).pack(anchor="e" if me else "w", padx=12, pady=(0, 6))

    def _filter_chats(self):
        query = self.chat_search_entry.get().strip().lower()
        chats = self.session.get("chats", [])
        if not query:
            self._populate_chat_list(chats)
            return
        filtered = [c for c in chats if query in c.display_name.lower() or query in c.jid.lower()]
        self._populate_chat_list(filtered)
