"""
ADB Extractor
-------------
Wraps the Android Debug Bridge (adb) binary to pull WhatsApp artifacts from a
device the examiner already has lawful, physical access to and has unlocked
USB/Wireless debugging on. This module does not attempt to bypass locks,
escalate privileges, or interact with any device without an authorized ADB
session already present.

Two extraction paths:
  1. Root method       - direct `adb pull` from /data/data/com.whatsapp/...
                          (requires a rooted device / adb running as root)
  2. Non-root method   - uses `adb backup` (Android <12) or the
                          `bmgr`/full-data "backup transport" + the app's own
                          allowBackup flag, OR (Android 13-16) the
                          `adb shell cmd` content-provider export path via
                          Storage Access Framework, depending on what the
                          target device exposes. Falls back to instructing
                          the user to use WhatsApp's own "Chat Transfer" /
                          Google Drive export if the OS blocks direct pulls.

Requires: `adb` present on PATH (Android Platform Tools).
"""

import subprocess
import shutil
import os
import re
from dataclasses import dataclass
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed


def _find_adb_bin() -> str:
    """Find adb binary by searching project bin, system PATH, and Android SDK install locations."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled_paths = [
        os.path.join(project_root, "bin", "platform-tools", "adb.exe"),
        os.path.join(project_root, "bin", "adb.exe"),
        os.path.join(project_root, "platform-tools", "adb.exe"),
    ]
    for p in bundled_paths:
        if os.path.isfile(p):
            return p

    found = shutil.which("adb")
    if found:
        return found

    user_profile = os.environ.get("USERPROFILE", "")
    known_paths = [
        os.path.join(user_profile, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\platform-tools\adb.exe",
        r"C:\Program Files\Android\Android Studio\platform-tools\adb.exe",
        r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
    ]
    for p in known_paths:
        if p and os.path.isfile(p):
            return p

    return "adb"


ADB_BIN = _find_adb_bin()

WA_DATA_ROOT = "/data/data/com.whatsapp"
WA_MSGSTORE_PATHS = [
    f"{WA_DATA_ROOT}/databases/msgstore.db",
    f"{WA_DATA_ROOT}/databases/msgstore.db.crypt15",
    f"{WA_DATA_ROOT}/databases/msgstore.db.crypt14",
    f"{WA_DATA_ROOT}/databases/msgstore.db.crypt12",
]
WA_CONTACTS_DB = f"{WA_DATA_ROOT}/databases/wa.db"
WA_KEY_FILE = f"{WA_DATA_ROOT}/files/key"
WA_MEDIA_DIR_SDCARD = "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"


class AdbError(RuntimeError):
    pass


@dataclass
class DeviceInfo:
    serial: str
    model: str = ""
    android_version: str = ""
    is_root: bool = False
    connected_via: str = "usb"  # or "tcp"


def _run(args, timeout=30) -> str:
    try:
        result = subprocess.run(
            [ADB_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise AdbError(
            "adb binary not found. Install Android Platform Tools and "
            "ensure `adb` is on your PATH."
        )
    except subprocess.TimeoutExpired:
        raise AdbError(f"adb command timed out: {' '.join(args)}")

    if result.returncode != 0 and "error" in (result.stderr or "").lower():
        raise AdbError(result.stderr.strip() or f"adb failed: {' '.join(args)}")
    return result.stdout.strip()


def list_devices() -> list[str]:
    """Return serials of devices currently authorized over adb."""
    out = _run(["devices"])
    serials = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def connect_tcp(ip: str, port: int = 5555) -> str:
    """Connect to a device already paired for wireless debugging."""
    out = _run(["connect", f"{ip}:{port}"], timeout=15)
    if "connected" not in out.lower() and "already" not in out.lower():
        raise AdbError(f"Could not connect to {ip}:{port} — {out}")
    return f"{ip}:{port}"


def get_device_info(serial: str) -> DeviceInfo:
    model = _run(["-s", serial, "shell", "getprop", "ro.product.model"])
    version = _run(["-s", serial, "shell", "getprop", "ro.build.version.release"])
    whoami = _run(["-s", serial, "shell", "whoami"])
    return DeviceInfo(
        serial=serial,
        model=model,
        android_version=version,
        is_root=(whoami.strip() == "root"),
    )


def _adb_pull(serial: str, remote_path: str, local_path: str) -> bool:
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    try:
        out = _run(["-s", serial, "pull", remote_path, local_path], timeout=120)
        return os.path.exists(local_path) and os.path.getsize(local_path) > 0
    except AdbError:
        return False


def extract_root(serial: str, output_dir: str,
                  progress: Optional[Callable[[str], None]] = None) -> dict:
    """
    Direct pull from app-private storage. Only works if the device is rooted
    and `adb shell` is running as root (or `adb root` succeeds), since
    /data/data/com.whatsapp is not otherwise readable.
    """
    def log(msg):
        if progress:
            progress(msg)

    results = {"msgstore": None, "wa_db": None, "key": None, "media": None}

    try:
        _run(["-s", serial, "root"], timeout=10)
    except AdbError:
        pass  # device may already be root, or non-rootable — fall through

    def pull_msgstore():
        for candidate in WA_MSGSTORE_PATHS:
            local = os.path.join(output_dir, os.path.basename(candidate))
            if _adb_pull(serial, candidate, local):
                return local
        return None

    def pull_wa_db():
        local_wa = os.path.join(output_dir, "wa.db")
        return local_wa if _adb_pull(serial, WA_CONTACTS_DB, local_wa) else None

    def pull_key():
        local_key = os.path.join(output_dir, "key")
        return local_key if _adb_pull(serial, WA_KEY_FILE, local_key) else None

    def pull_media():
        media_out = os.path.join(output_dir, "Media")
        os.makedirs(media_out, exist_ok=True)
        try:
            _run(["-s", serial, "pull", WA_MEDIA_DIR_SDCARD, media_out], timeout=600)
            if os.listdir(media_out):
                return media_out
        except AdbError:
            pass
        return None

    # These four pulls are fully independent of one another. Running them
    # sequentially meant total time = sum of all four, dominated by the
    # (often large) media folder pull happening dead last. Running them
    # concurrently means total time ~= the slowest single one instead —
    # ADB's server already multiplexes multiple client connections to the
    # same device, so several concurrent `adb pull`/`adb shell` calls
    # against one serial is a normal, supported pattern.
    log("Pulling msgstore, contacts DB, key file, and media concurrently...")
    tasks = {"msgstore": pull_msgstore, "wa_db": pull_wa_db,
             "key": pull_key, "media": pull_media}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            value = future.result()
            results[name] = value
            log(f"{name}: {'found' if value else 'not found'}")

    if not any(results.values()):
        raise AdbError(
            "No WhatsApp files could be pulled. The device is likely not "
            "rooted, or ADB is not running with root permissions. Try the "
            "non-root extraction method instead."
        )

    return results


def extract_nonroot(serial: str, output_dir: str,
                     progress: Optional[Callable[[str], None]] = None) -> dict:
    """
    Non-root path for Android 12+. WhatsApp opts in to Android's
    key-value/full backup for a small, app-defined set of files when the
    device has no lock-screen credential set at backup time, exposed via
    `adb backup`. On Android 13-16 with a screen lock present, `adb backup`
    for third-party apps is typically blocked at the OS level entirely —
    in that case this function will surface a clear message rather than
    silently failing, and the examiner should fall back to a manual,
    on-device export (WhatsApp Settings > Chats > Chat backup, or the
    in-app "Transfer chats" flow) performed under the same lawful-access
    authorization.
    """
    def log(msg):
        if progress:
            progress(msg)

    backup_ab = os.path.join(output_dir, "whatsapp_backup.ab")
    log("Requesting Android backup for com.whatsapp (confirm on device screen)...")
    try:
        subprocess.run(
            [ADB_BIN, "-s", serial, "backup", "-f", backup_ab, "-noapk", "com.whatsapp"],
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise AdbError("Backup request timed out waiting for on-device confirmation.")

    if not os.path.exists(backup_ab) or os.path.getsize(backup_ab) < 32:
        raise AdbError(
            "Non-root backup returned no usable data. On Android 13-16 with "
            "a screen lock configured, the OS blocks `adb backup` for "
            "third-party apps by design. Use WhatsApp's own in-app chat "
            "export (Settings > Chats > Chat backup) instead, then import "
            "the resulting .crypt14/.crypt15 file via the Decryptor tab."
        )

    log("Backup received. Convert with `android-backup-extractor` to reach "
        "the tar payload, then locate msgstore.db.crypt* inside apps/com.whatsapp/.")
    return {"backup_ab": backup_ab}
