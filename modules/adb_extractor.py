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
    from .paths import app_root
    project_root = app_root()
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


def _run(args, timeout=30, check=True) -> str:
    """Run an adb command and return stripped stdout.

    check=True (default): ANY non-zero exit raises AdbError, with stderr
    (or stdout) as the message. Callers that legitimately tolerate partial
    failure — e.g. `find` printing "Permission denied" for some folders
    while still listing others — pass check=False and filter the output."""
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

    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise AdbError(detail or f"adb failed (exit {result.returncode}): {' '.join(args)}")
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


def _su_available(serial: str) -> bool:
    """True if `su` exists on the device and grants uid 0 (Magisk-style
    root, which production builds expose instead of `adb root`)."""
    try:
        out = _run(["-s", serial, "shell", "su", "-c", "id"], timeout=15, check=False)
    except AdbError:
        return False
    return "uid=0" in out


def _pull_via_su(serial: str, remote_path: str, local_path: str) -> bool:
    """Copy a root-only file by streaming it through `su -c cat` with
    exec-out (binary-safe, unlike `adb shell` which may translate line
    endings)."""
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    try:
        with open(local_path, "wb") as f:
            proc = subprocess.run(
                [ADB_BIN, "-s", serial, "exec-out", "su", "-c", f"cat '{remote_path}'"],
                stdout=f, stderr=subprocess.PIPE, timeout=600,
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        proc = None
    ok = proc is not None and proc.returncode == 0 and os.path.getsize(local_path) > 0
    if not ok and os.path.exists(local_path):
        os.remove(local_path)
    return ok


@dataclass
class WaInstall:
    package: str          # com.whatsapp | com.whatsapp.w4b
    user_id: int          # Android user/profile (0 = owner; others = work profile, Dual Messenger…)
    data_root: str        # app-private storage (root only)
    media_dir: str        # shared-storage media folder
    label: str            # unique folder-name-safe label


WA_PACKAGES = ("com.whatsapp", "com.whatsapp.w4b")      # WhatsApp, WhatsApp Business
_MEDIA_SUBDIR = {"com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business"}


def _make_install(package: str, user_id: int) -> WaInstall:
    data_root = f"/data/data/{package}" if user_id == 0 else f"/data/user/{user_id}/{package}"
    storage = "/sdcard" if user_id == 0 else f"/storage/emulated/{user_id}"
    media = f"{storage}/Android/media/{package}/{_MEDIA_SUBDIR[package]}/Media"
    return WaInstall(package, user_id, data_root, media, f"{package}_u{user_id}")


def list_installs(serial: str) -> list:
    """Every WhatsApp / WhatsApp Business install on the device, across all
    Android users (covers work profiles and Samsung/Xiaomi dual-app
    clones, which live in extra user IDs). Falls back to user 0 only if
    `pm list users` can't be read."""
    user_ids = [0]
    try:
        out = _run(["-s", serial, "shell", "pm", "list", "users"], timeout=15, check=False)
        found = [int(m) for m in re.findall(r"UserInfo\{(\d+):", out)]
        if found:
            user_ids = sorted(set(found))
    except AdbError:
        pass
    installs = []
    for uid in user_ids:
        for pkg in WA_PACKAGES:
            try:
                out = _run(["-s", serial, "shell", "pm", "list", "packages", "--user", str(uid), pkg],
                           timeout=15, check=False)
            except AdbError:
                continue
            if f"package:{pkg}" in [l.strip() for l in out.splitlines()]:
                installs.append(_make_install(pkg, uid))
    return installs


def _extract_install_root(serial, out_dir, inst: WaInstall, pull_private, log) -> dict:
    results = {"msgstore": None, "wa_db": None, "key": None, "media": None}
    candidates = [f"{inst.data_root}/databases/msgstore.db{ext}"
                  for ext in ("", ".crypt15", ".crypt14", ".crypt12")]

    def pull_msgstore():
        for candidate in candidates:
            local = os.path.join(out_dir, os.path.basename(candidate))
            if pull_private(candidate, local):
                return local
        return None

    def pull_wa_db():
        local = os.path.join(out_dir, "wa.db")
        return local if pull_private(f"{inst.data_root}/databases/wa.db", local) else None

    def pull_key():
        local = os.path.join(out_dir, "key")
        return local if pull_private(f"{inst.data_root}/files/key", local) else None

    def pull_media():
        media_out = os.path.join(out_dir, "Media")
        os.makedirs(media_out, exist_ok=True)
        try:
            _run(["-s", serial, "pull", inst.media_dir, media_out], timeout=600)
            if os.listdir(media_out):
                return media_out
        except AdbError:
            pass
        return None

    # The four pulls are independent; ADB multiplexes concurrent clients,
    # so total time ~ the slowest one rather than the sum.
    tasks = {"msgstore": pull_msgstore, "wa_db": pull_wa_db, "key": pull_key, "media": pull_media}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
            log(f"[{inst.label}] {name}: {'found' if results[name] else 'not found'}")
    return results


def extract_root(serial: str, output_dir: str,
                 progress: Optional[Callable[[str], None]] = None,
                 installs: Optional[list] = None) -> dict:
    """
    Direct pull from app-private storage for EVERY WhatsApp / WhatsApp
    Business install found (all Android users). Needs `adb root` or a
    working `su`. With one install, files go straight into output_dir; with
    several, each gets its own sub-folder named after the install label.

    Returns the flat {msgstore, wa_db, key, media} dict of the FIRST install
    that yielded data (so existing callers keep working), plus
    "installs": {label: that install's dict}.
    """
    def log(msg):
        if progress:
            progress(msg)

    try:
        _run(["-s", serial, "root"], timeout=10)
    except AdbError:
        pass  # production builds refuse `adb root` — we try `su` below

    use_su = False
    try:
        if _run(["-s", serial, "shell", "whoami"], timeout=10).strip() != "root":
            use_su = _su_available(serial)
            if use_su:
                log("`adb root` unavailable; using `su` (Magisk-style root) for private files.")
    except AdbError:
        pass

    def pull_private(remote, local):
        if _adb_pull(serial, remote, local):
            return True
        return use_su and _pull_via_su(serial, remote, local)

    installs = installs or list_installs(serial) or [_make_install("com.whatsapp", 0)]
    log("Found install(s): " + ", ".join(i.label for i in installs))

    per_install, first = {}, None
    for inst in installs:
        sub = output_dir if len(installs) == 1 else os.path.join(output_dir, inst.label)
        os.makedirs(sub, exist_ok=True)
        log(f"Pulling {inst.label} ...")
        res = _extract_install_root(serial, sub, inst, pull_private, log)
        per_install[inst.label] = res
        if first is None and any(res.values()):
            first = res
        # Chain of custody: hash + log every file pulled; media is covered
        # by a per-file SHA-256 manifest.
        try:
            from . import custody
            for name in ("msgstore", "wa_db", "key"):
                if res.get(name):
                    custody.record(sub, f"adb-pull-{name}", res[name], serial=serial, note=inst.label)
            if res.get("media"):
                log(f"Hashing pulled media folder ({inst.label})...")
                custody.record_tree(sub, f"adb-pull-media-{inst.label}", res["media"], serial=serial)
        except Exception as e:  # never lose the extraction over a log problem
            log(f"WARNING: custody logging failed: {e}")

    if first is None:
        raise AdbError(
            "No WhatsApp files could be pulled. The device is likely not "
            "rooted (neither `adb root` nor `su` worked). Try the "
            "non-root extraction method instead."
        )
    out = dict(first)
    out["installs"] = per_install
    return out


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

    try:
        packages = sorted({i.package for i in list_installs(serial) if i.user_id == 0}) or ["com.whatsapp"]
    except AdbError:
        packages = ["com.whatsapp"]
    backup_ab = os.path.join(output_dir, "whatsapp_backup.ab")
    log(f"Requesting Android backup for {', '.join(packages)} (confirm on device screen)...")
    try:
        subprocess.run(
            [ADB_BIN, "-s", serial, "backup", "-f", backup_ab, "-noapk", *packages],
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

    try:
        from . import custody
        custody.record(output_dir, "adb-backup", backup_ab, serial=serial)
    except Exception as e:
        log(f"WARNING: custody logging failed: {e}")
    log("Backup received. Convert with `android-backup-extractor` to reach "
        "the tar payload, then locate msgstore.db.crypt* inside apps/com.whatsapp/.")
    return {"backup_ab": backup_ab}
