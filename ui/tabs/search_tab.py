"""SearchTab — search tab (split out of main.py)."""

from ui.common import (
    ctk,
    fmt_ts,
    mb,
    search_messages,
    theme,
    tk,
)


class SearchTabMixin:
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
            ts = fmt_ts(h.message.timestamp)
            who = "Me" if h.message.from_me else h.chat_name
            ctk.CTkLabel(row, text=f"{h.chat_name}  ·  {who}  ·  {ts}", font=theme.FONT_LABEL,
                         anchor="w").pack(anchor="w", padx=8, pady=(6, 0))
            ctk.CTkLabel(row, text=h.snippet, font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED,
                         wraplength=850, justify="left").pack(anchor="w", padx=8, pady=(0, 6))
