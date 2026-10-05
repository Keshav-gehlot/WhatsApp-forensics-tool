"""Shared imports, OCR config helpers and widgets for the UI."""
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
from modules.db_parser import WaDatabase, fmt_ts
from modules import custody
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

from modules.paths import app_root

_PROJECT_ROOT = app_root()
_OCR_CONFIG_PATH = os.path.join(_PROJECT_ROOT, ".ocr_config.json")

# Project-bundled binary (takes priority over system installs)
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
    def __init__(self, master, icon, value, label, accent_color=theme.ACCENT, **kwargs):
        super().__init__(master, fg_color=theme.BG_CARD,
                          border_color=theme.BORDER, border_width=1,
                          corner_radius=12, **kwargs)
        self.accent_color = accent_color

        # thin accent strip along the top edge
        strip = ctk.CTkFrame(self, height=3, corner_radius=3, fg_color=accent_color)
        strip.pack(fill="x", padx=12, pady=(10, 0))

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(expand=True, fill="both", padx=14, pady=(8, 12))

        self.value_label = ctk.CTkLabel(container, text=str(value),
                                        font=theme.FONT_STAT_VALUE, text_color=theme.TEXT_PRIMARY)
        self.value_label.pack(anchor="w")

        row = ctk.CTkFrame(container, fg_color="transparent")
        row.pack(anchor="w", fill="x")
        ctk.CTkLabel(row, text=icon, font=("Segoe UI Emoji", 12)).pack(side="left", padx=(0, 5))
        ctk.CTkLabel(row, text=label.upper(), font=theme.FONT_STAT_LABEL,
                     text_color=theme.TEXT_MUTED).pack(side="left")

    def set_value(self, value):
        self.value_label.configure(text=str(value))
