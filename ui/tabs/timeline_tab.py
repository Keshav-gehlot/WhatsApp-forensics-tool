"""TimelineTabMixin — one chronological view across chats, calls, reactions and edits."""

import os

from ui.common import (
    ctk,
    fd,
    fmt_ts,
    mb,
    theme,
    tk,
)
from modules import timeline as tl


class TimelineTabMixin:
    def _build_timeline_tab(self, tab):
        ctk.CTkLabel(tab, text="◆ TIMELINE (all times UTC) ◆", font=theme.FONT_LABEL,
                     text_color=theme.ACCENT_GREEN).pack(pady=(10, 4))
        top = ctk.CTkFrame(tab, fg_color="transparent"); top.pack(fill="x", padx=20, pady=6)
        self.tl_text = ctk.CTkEntry(top, width=200, placeholder_text="text contains…"); self.tl_text.pack(side="left", padx=4)
        self.tl_chat = ctk.CTkEntry(top, width=160, placeholder_text="chat / number"); self.tl_chat.pack(side="left", padx=4)
        self.tl_kind_vars = {}
        for kind in ("message", "call", "reaction", "edit"):
            v = tk.BooleanVar(value=True); self.tl_kind_vars[kind] = v
            ctk.CTkCheckBox(top, text=kind, variable=v, width=70).pack(side="left", padx=4)
        ctk.CTkButton(top, text="Refresh", width=80, command=self._refresh_timeline).pack(side="left", padx=4)
        ctk.CTkButton(top, text="Export CSV", width=90, command=self._export_timeline).pack(side="left", padx=4)
        self.tl_count = ctk.CTkLabel(tab, text="", font=theme.FONT_MONO_SMALL, text_color=theme.TEXT_MUTED)
        self.tl_count.pack(padx=20, anchor="w")
        self.tl_view = ctk.CTkTextbox(tab, font=theme.FONT_MONO_SMALL, fg_color=theme.BG_INPUT,
                                       text_color=theme.ACCENT_GREEN)
        self.tl_view.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        self._tl_events = []

    def _timeline_events(self):
        chats, calls = self.session.get("chats", []), self.session.get("calls", [])
        db = self.session.get("wa_database")
        reactions = edits = []
        if db is not None:
            try: reactions = db.get_reactions()
            except Exception: pass
            try: edits = db.get_edits()
            except Exception: pass
        return tl.build_timeline(chats, calls, reactions, edits)

    def _refresh_timeline(self):
        if not self.session.get("chats"):
            mb.showwarning("No data", "Load a database first."); return
        kinds = {k for k, v in self.tl_kind_vars.items() if v.get()}
        events = tl.filter_timeline(self._timeline_events(), text=self.tl_text.get(),
                                    chat=self.tl_chat.get(), kinds=kinds)
        self._tl_events = events
        self.tl_view.delete("1.0", tk.END)
        for e in events[:5000]:
            self.tl_view.insert(tk.END, f"{fmt_ts(e.timestamp, seconds=True, empty='?'):<25}"
                                        f"{e.kind:<9}{e.chat_name[:24]:<26}{e.who[:20]:<22}{e.summary}\n")
        extra = f" (showing first 5000)" if len(events) > 5000 else ""
        self.tl_count.configure(text=f"{len(events)} event(s){extra}")

    def _export_timeline(self):
        if not self._tl_events:
            mb.showwarning("Nothing to export", "Click Refresh first."); return
        start = self.case.exports_dir if getattr(self, "case", None) else None
        path = fd.asksaveasfilename(defaultextension=".csv", initialdir=start,
                                    initialfile="timeline.csv", filetypes=[("CSV", "*.csv")])
        if path:
            tl.export_timeline_csv(self._tl_events, path)
            from ui.common import custody
            custody.record(os.path.dirname(path), "export-timeline-csv", path)
            mb.showinfo("Exported", f"Timeline written to:\n{path}")
