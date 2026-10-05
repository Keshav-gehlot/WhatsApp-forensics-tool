"""CallsTab — calls tab (split out of main.py)."""

from ui.common import (
    ctk,
    fmt_ts,
    theme,
    tk,
)


class CallsTabMixin:
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
        header = f"{'TYPE':<12}{'NUMBER':<20}{'VIDEO':<7}{'DURATION':<10}{'TIMESTAMP (UTC)':<26}{'RAW RESULT'}\n"
        self.calls_view.insert(tk.END, header)
        self.calls_view.insert(tk.END, "-" * 84 + "\n")
        for c in calls:
            ts = fmt_ts(c.timestamp, seconds=True, empty="?")
            dur = f"{c.duration_seconds // 60}:{c.duration_seconds % 60:02d}"
            self.calls_view.insert(
                tk.END,
                f"{c.call_type:<12}{c.phone_number:<20}{'Y' if c.is_video else 'N':<7}{dur:<10}{ts:<26}"
                f"{'' if c.result_code is None else c.result_code}\n"
            )
