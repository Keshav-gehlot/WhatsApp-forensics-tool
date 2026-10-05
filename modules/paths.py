"""Where the app lives — works from source and from a PyInstaller build."""
import os
import sys


def app_root() -> str:
    """Folder holding bin/, models/ and .ocr_config.json: the project folder
    when run from source, the folder containing the .exe when frozen."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
