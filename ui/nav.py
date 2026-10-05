"""
SidebarShell — a left-rail navigation that replaces CustomTkinter's top
CTkTabview. It exposes the same small API the app already used
(`add(name)`, `tab(name)`, `set(name)`), so existing tab-building code keeps
working, while fixing the tab-bar overflow and giving a cleaner layout.

`set()` matches loosely (ignores the leading emoji / case), which also fixes
a latent bug where a caller used `set("Decryptor")` instead of the full
"🔐 Decryptor" tab name.
"""

import customtkinter as ctk

from ui import theme


class _NavItem(ctk.CTkFrame):
    """One navigation row: a thin accent bar + a left-aligned button."""

    def __init__(self, master, text, command):
        super().__init__(master, fg_color="transparent", height=40)
        self.pack_propagate(False)
        self.bar = ctk.CTkFrame(self, width=3, corner_radius=3, fg_color="transparent")
        self.bar.pack(side="left", fill="y", padx=(6, 0), pady=3)
        self.btn = ctk.CTkButton(
            self, text="  " + text, anchor="w", command=command,
            fg_color="transparent", hover_color=theme.NAV_HOVER,
            text_color=theme.TEXT_SECONDARY, font=theme.FONT_NAV,
            height=36, corner_radius=9,
        )
        self.btn.pack(side="left", fill="x", expand=True, padx=(6, 8))

    def set_active(self, on: bool):
        self.bar.configure(fg_color=theme.ACCENT if on else "transparent")
        self.btn.configure(
            fg_color=theme.NAV_ACTIVE if on else "transparent",
            text_color=theme.TEXT_PRIMARY if on else theme.TEXT_SECONDARY,
        )


class SidebarShell:
    def __init__(self, parent):
        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.root.pack(fill="both", expand=True, padx=theme.PAD_LG, pady=(2, theme.PAD_LG))

        self.rail = ctk.CTkScrollableFrame(
            self.root, width=228, fg_color=theme.BG_SIDEBAR, corner_radius=16,
            scrollbar_button_color=theme.BG_CARD, scrollbar_button_hover_color=theme.BORDER,
        )
        self.rail.pack(side="left", fill="y", padx=(0, theme.PAD_LG))
        self.rail._scrollbar.grid_configure(padx=(0, 2))

        self.body = ctk.CTkFrame(
            self.root, fg_color=theme.BG_PANEL, corner_radius=16,
            border_width=1, border_color=theme.BORDER,
        )
        self.body.pack(side="left", fill="both", expand=True)

        self._frames = {}
        self._items = {}
        self._order = []
        self._current = None

    # -- construction --------------------------------------------------------
    def section(self, caption: str):
        ctk.CTkLabel(self.rail, text=caption.upper(), font=theme.FONT_SECTION,
                     text_color=theme.TEXT_FAINT, anchor="w").pack(
            fill="x", padx=18, pady=(16, 2))

    def add(self, name: str):
        """Create the nav item + its (hidden) content frame. Returns the
        content frame so callers can build into it, matching how the old
        code used `self.tabs.tab(name)`."""
        frame = ctk.CTkFrame(self.body, fg_color="transparent")
        item = _NavItem(self.rail, name, command=lambda n=name: self.set(n))
        item.pack(fill="x", padx=6, pady=1)
        self._frames[name] = frame
        self._items[name] = item
        self._order.append(name)
        return frame

    # -- API compatible with CTkTabview -------------------------------------
    def tab(self, name: str):
        return self._frames[self._match(name)]

    def set(self, name: str):
        key = self._match(name)
        if key == self._current:
            return
        if self._current is not None:
            self._frames[self._current].pack_forget()
            self._items[self._current].set_active(False)
        self._frames[key].pack(fill="both", expand=True, padx=theme.PAD_LG, pady=theme.PAD_MD)
        self._items[key].set_active(True)
        self._current = key

    # -- helpers -------------------------------------------------------------
    def _match(self, name: str) -> str:
        if name in self._frames:
            return name
        norm = self._norm(name)
        for key in self._order:
            if self._norm(key) == norm or norm in self._norm(key):
                return key
        raise KeyError(f"No nav item matching {name!r}")

    @staticmethod
    def _norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())
