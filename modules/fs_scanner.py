"""
Filesystem Scanner
------------------
Enumerates WhatsApp-related files across whatever the current ADB session
can already see — nothing here requests or requires privilege beyond
whatever access level the session already has.

Three tiers, reported separately and honestly:

  1. WhatsApp's own app folders (no root needed, ever)
     /sdcard/WhatsApp, /sdcard/Android/media/com.whatsapp, and the
     scoped-storage equivalents under /storage/emulated/0. This is where
     WhatsApp's own local backup (.crypt12/14/15) and media actually live
     on the overwhelming majority of real devices — including fully
     stock, non-rooted, production Android 13-16 phones.

  2. Files shared/downloaded from WhatsApp and later moved elsewhere
     WhatsApp stamps every file it creates with PREFIX-YYYYMMDD-WA####.ext
     (IMG-/VID-/AUD-/PTT-/DOC-/STK-...). That naming survives a copy or
     move, so a filename-pattern search across common save locations
     (Download, Pictures, Movies, Documents, DCIM) — and, if a deep scan
     is requested, the entire external storage volume — finds files a
     user saved out of WhatsApp into shared storage, without needing any
     elevated access.

  3. App-private storage (/data/data/com.whatsapp/...)
     Only visible if the device is already rooted and `adb root`
     succeeds. If it's not, this scanner reports that plainly rather than
     silently returning nothing or trying to force its way in.

No exploit code, privilege escalation, or bypass of any kind is
implemented or attempted here — this only lists what's already readable.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .adb_extractor import _run, AdbError
from .media_resolver import is_thumbnail_path

EXTERNAL_ROOTS = [
    "/sdcard/WhatsApp",
    "/sdcard/Android/media/com.whatsapp",
    "/storage/emulated/0/WhatsApp",
    "/storage/emulated/0/Android/media/com.whatsapp",
]

# Common places a user (or another app) might have manually saved/moved a
# WhatsApp file to once it left the WhatsApp folder — checked quickly by
# default before any full-storage scan is attempted.
SHARED_LOOKUP_ROOTS = [
    "/sdcard/Download",
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/Documents",
    "/sdcard/DCIM",
    "/storage/emulated/0/Download",
    "/storage/emulated/0/Pictures",
    "/storage/emulated/0/Movies",
    "/storage/emulated/0/Documents",
]

# Whole-storage roots used only when a deep scan is explicitly requested,
# since recursing all of /sdcard is slower on devices with a lot of data.
DEEP_SCAN_ROOTS = [
    "/sdcard",
    "/storage/emulated/0",
]

# WhatsApp stamps every file it creates with this convention regardless of
# type: PREFIX-YYYYMMDD-WA####.ext (IMG/VID/AUD/PTT/DOC/STK/...). The "-WA"
# substring is what survives even after the file is copied/moved elsewhere,
# so a single glob catches all of them; we categorize by prefix afterward.
WA_NAMING_GLOB = "*-WA*"

WA_PREFIX_CATEGORIES = {
    "IMG": "Shared image",
    "VID": "Shared video",
    "AUD": "Shared audio",
    "PTT": "Shared voice note",
    "DOC": "Shared document",
    "STK": "Shared sticker",
    "GIF": "Shared GIF",
}

ROOT_ONLY_PATHS = [
    "/data/data/com.whatsapp",
]

# Patterns of interest, mapped to a human category for the UI.
PATTERNS = {
    "*.crypt12": "Encrypted backup (crypt12)",
    "*.crypt14": "Encrypted backup (crypt14)",
    "*.crypt15": "Encrypted backup (crypt15)",
    "msgstore.db": "Decrypted message store",
    "wa.db": "Contacts database",
    "key": "Backup encryption key",
    "*.jpg": "Media - image",
    "*.jpeg": "Media - image",
    "*.mp4": "Media - video",
    "*.opus": "Media - voice note",
    "*.pdf": "Media - document",
    "*.vcf": "Contact card",
}


def _categorize_by_wa_prefix(path: str) -> str:
    basename = os.path.basename(path)
    prefix = basename.split("-", 1)[0].upper()
    return WA_PREFIX_CATEGORIES.get(prefix, "Shared file (WhatsApp naming)")


def _categorize_hit(path: str) -> str:
    """Categorize a path by its own name/extension. Needed because the
    batched multi-pattern find (see _find_multi) matches several PATTERNS
    in one ADB round-trip, so unlike the old one-call-per-pattern
    approach we no longer know which specific pattern matched — we
    recover the same category post-hoc instead."""
    basename = os.path.basename(path).lower()
    if basename.endswith(".crypt12"):
        return "Encrypted backup (crypt12)"
    if basename.endswith(".crypt14"):
        return "Encrypted backup (crypt14)"
    if basename.endswith(".crypt15"):
        return "Encrypted backup (crypt15)"
    if basename == "msgstore.db":
        return "Decrypted message store"
    if basename == "wa.db":
        return "Contacts database"
    if basename == "key":
        return "Backup encryption key"
    if basename.endswith((".jpg", ".jpeg")):
        return "Media - image"
    if basename.endswith(".mp4"):
        return "Media - video"
    if basename.endswith(".opus"):
        return "Media - voice note"
    if basename.endswith(".pdf"):
        return "Media - document"
    if basename.endswith(".vcf"):
        return "Contact card"
    return "Other WhatsApp-related file"


@dataclass
class ScanHit:
    path: str
    category: str
    tier: str  # "external" | "root_only"


@dataclass
class ScanReport:
    hits: list[ScanHit] = field(default_factory=list)
    root_available: bool = False
    root_only_skipped: bool = False  # True if root paths existed but we had no access


def _find(serial: str, root: str, pattern: str, timeout: int = 60) -> list[str]:
    try:
        out = _run(
            ["-s", serial, "shell", "find", root, "-iname", f"'{pattern}'"],
            timeout=timeout,
        )
    except AdbError:
        return []
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    # Filter out shell noise like "No such file or directory" / permission errors
    return [l for l in lines if l.startswith("/") and "Permission denied" not in l
            and "No such file" not in l]


def _find_multi(serial: str, root: str, patterns: list[str], timeout: int = 90) -> list[str]:
    """
    Matches any of several -iname patterns in a SINGLE find call, instead
    of one ADB round-trip per pattern. Each `adb shell` invocation pays a
    real latency cost (process spawn + device round-trip); scanning
    EXTERNAL_ROOTS with the ~12 PATTERNS individually meant 4 roots x 12
    patterns = 48 separate round-trips. This cuts that to 1 per root.

    Safe without parentheses here: `find root -iname 'a' -o -iname 'b'`
    is a flat OR chain with an implicit trailing -print over the whole
    expression — grouping parentheses are only needed when a different
    action (like -exec) needs explicit precedence, which isn't the case
    here.
    """
    args = ["-s", serial, "shell", "find", root]
    for i, pattern in enumerate(patterns):
        if i > 0:
            args.append("-o")
        args += ["-iname", f"'{pattern}'"]
    try:
        out = _run(args, timeout=timeout)
    except AdbError:
        return []
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return [l for l in lines if l.startswith("/") and "Permission denied" not in l
            and "No such file" not in l]


def _check_root(serial: str) -> bool:
    try:
        _run(["-s", serial, "root"], timeout=10)
    except AdbError:
        pass
    try:
        whoami = _run(["-s", serial, "shell", "whoami"], timeout=10)
        return whoami.strip() == "root"
    except AdbError:
        return False


def scan_device(serial: str, progress: Optional[Callable[[str], None]] = None,
                 deep: bool = False, max_workers: int = 4) -> ScanReport:
    """
    deep=False (default): scans the WhatsApp app folders (fast) plus a
        handful of common "user might have saved it here" locations
        (Download, Pictures, Movies, Documents, DCIM) for files matching
        WhatsApp's own naming convention.
    deep=True: additionally recurses the entire external storage volume
        looking for the same naming convention, catching a file saved
        anywhere at all — slower (can take minutes on a device with a lot
        of data), so it's opt-in rather than the default.

    Roots within each phase are scanned concurrently (max_workers ADB
    shell sessions at once) rather than one after another — ADB
    multiplexes multiple shell sessions fine over one USB/TCP connection,
    and each `find` call is mostly waiting on device I/O, not local CPU,
    so this materially cuts wall-clock scan time. Progress callbacks may
    arrive out of strict root order as a result; callers must not assume
    a fixed ordering (main.py's progress handler already just appends to
    a log, which is fine).
    """
    def log(msg):
        if progress:
            progress(msg)

    report = ScanReport()
    seen_paths = set()

    def add_hits(paths, tier, category_fn):
        # Only ever called from the thread driving scan_device itself
        # (via the `for future in as_completed(...)` loops below), never
        # from inside a pool worker — so seen_paths needs no locking.
        for path in paths:
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if is_thumbnail_path(path):
                report.hits.append(ScanHit(path=path, category="Thumbnail cache (possible deleted original)",
                                            tier="thumbnail"))
            else:
                report.hits.append(ScanHit(path=path, category=category_fn(path), tier=tier))

    log("Scanning WhatsApp app folders (no root required)...")
    all_patterns = list(PATTERNS.keys())
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_find_multi, serial, root, all_patterns): root
                   for root in EXTERNAL_ROOTS}
        for future in as_completed(futures):
            root = futures[future]
            hits = future.result()
            add_hits(hits, "external", _categorize_hit)
            log(f"  scanned {root} ({len(hits)} hit(s))")

    log("Checking common save/download locations for WhatsApp-shared files...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_find, serial, root, WA_NAMING_GLOB, 45): root
                   for root in SHARED_LOOKUP_ROOTS}
        for future in as_completed(futures):
            root = futures[future]
            hits = future.result()
            add_hits(hits, "shared_elsewhere", _categorize_by_wa_prefix)
            if hits:
                log(f"  {len(hits)} WhatsApp-named file(s) in {root}")

    if deep:
        log("Deep scan: recursing entire external storage for WhatsApp-named "
            "files (this can take a while)...")
        # Deliberately low concurrency here — each of these is a full
        # storage-volume recursion, not a quick single-folder lookup, so
        # running many at once mostly just contends for the same device
        # I/O rather than actually parallelizing useful work.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(_find, serial, root, WA_NAMING_GLOB, 600): root
                       for root in DEEP_SCAN_ROOTS}
            for future in as_completed(futures):
                root = futures[future]
                hits = future.result()
                add_hits(hits, "shared_elsewhere", _categorize_by_wa_prefix)
                log(f"  {len(hits)} WhatsApp-named file(s) found under {root}")

    log("Checking root availability for app-private storage...")
    report.root_available = _check_root(serial)

    if report.root_available:
        log("Root available — scanning /data/data/com.whatsapp...")
        for root in ROOT_ONLY_PATHS:
            hits = _find_multi(serial, root, all_patterns)
            add_hits(hits, "root_only", _categorize_hit)
    else:
        log("Root not available on this device — app-private storage "
            "(/data/data/com.whatsapp) is not reachable. This is expected "
            "and correct behavior on a stock, non-rooted device; it is not "
            "something this tool will try to work around. External "
            "storage results above are still usable and, on most current "
            "devices, contain the local backup file directly.")
        report.root_only_skipped = True

    log(f"Scan complete — {len(report.hits)} file(s) found.")
    return report


def pull_hits(serial: str, hits: list[ScanHit], output_dir: str,
              progress: Optional[Callable[[str], None]] = None) -> list[tuple[ScanHit, str]]:
    """
    Pull a selected subset of scan hits to output_dir, preserving a flat
    layout with category subfolders to avoid name collisions.

    Returns (ScanHit, local_path) pairs rather than bare paths — the
    category-subfolder reorganization means the original ".Thumbs" path
    segment doesn't survive in the local copy, so callers that need to
    tell thumbnail-cache files apart from full media (e.g. the deleted-
    media check) must use hit.tier from here rather than re-deriving it
    from the local path after the fact.
    """
    from .adb_extractor import _adb_pull  # reuse existing pull helper

    pulled = []
    for hit in hits:
        category_dir = os.path.join(output_dir, hit.category.replace(" ", "_").replace("/", "-"))
        local_path = os.path.join(category_dir, os.path.basename(hit.path))
        if progress:
            progress(f"Pulling {hit.path} ...")
        if _adb_pull(serial, hit.path, local_path):
            pulled.append((hit, local_path))
    return pulled
