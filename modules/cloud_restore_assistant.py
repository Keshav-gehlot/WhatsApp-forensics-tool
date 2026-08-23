"""
Cloud Restore Assistant
------------------------
There is no supported way to pull WhatsApp's Google Drive/iCloud backup
directly via the Drive/iCloud APIs — it lives in an app-restricted
storage area only WhatsApp's own registered client can read, regardless
of whose credentials or consent are used. That restriction is enforced
by Google/Apple at the platform level, not something this tool works
around.

The actual working path is to let WhatsApp itself perform the restore
(via its own "Transfer or restore" flow, or a fresh install with the
same Google/Apple account signed in) and then extract the resulting
LOCAL, decrypted backup exactly the way the rest of this tool already
does. This module does not perform the restore itself — it:

  1. Runs pre-flight checks over ADB (Google account signed in, WhatsApp
     installed + version, network state) so the examiner knows the
     restore is likely to work before manually triggering it in
     WhatsApp's own UI.
  2. Captures a baseline of the on-device backup file (if any) before
     the restore starts.
  3. Passively polls for that file to appear/grow/stabilize as a
     best-effort signal that the restore has likely finished — this is
     an ESTIMATE, not a certainty. WhatsApp's own UI shows definitive
     restore progress ("Restoring... X of Y messages"); the examiner
     should visually confirm completion there, not rely solely on this.
  4. Once complete, hands off to the exact same extraction/decryption
     pipeline (adb_extractor, decryptor) already used elsewhere in this
     tool — no new extraction logic, just triggering what already
     exists once the file is confirmed present.
"""

import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from .adb_extractor import _run, _adb_pull, AdbError, WA_MSGSTORE_PATHS


@dataclass
class PreFlightCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class PreFlightReport:
    checks: list = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)


@dataclass
class BackupFileStatus:
    path: Optional[str]
    exists: bool
    size_bytes: Optional[int] = None
    mtime_epoch: Optional[int] = None


def check_google_account(serial: str) -> PreFlightCheck:
    """Looks for a signed-in Google account via `dumpsys account`. Doesn't
    (and can't) confirm that account actually has a WhatsApp backup on
    Drive — only that Drive restore has a chance of working at all."""
    try:
        out = _run(["-s", serial, "shell", "dumpsys", "account"], timeout=15)
    except AdbError as e:
        return PreFlightCheck("Google account signed in", False, f"Could not check: {e}")

    accounts = re.findall(r"Account\s*\{name=([^,]+),\s*type=([^}]+)\}", out)
    google_accounts = [name for name, acct_type in accounts if "google" in acct_type.lower()]

    if google_accounts:
        return PreFlightCheck("Google account signed in", True,
                               f"Found: {', '.join(google_accounts)}")
    return PreFlightCheck("Google account signed in", False,
                           "No Google account detected on this device — Drive restore "
                           "requires one to be signed in first.")


def check_whatsapp_installed(serial: str) -> PreFlightCheck:
    try:
        out = _run(["-s", serial, "shell", "dumpsys", "package", "com.whatsapp"], timeout=15)
    except AdbError as e:
        return PreFlightCheck("WhatsApp installed", False, f"Could not check: {e}")

    if "Unable to find package" in out or not out.strip():
        return PreFlightCheck("WhatsApp installed", False, "com.whatsapp is not installed on this device.")

    version_match = re.search(r"versionName=([^\s]+)", out)
    version = version_match.group(1) if version_match else "unknown"
    return PreFlightCheck("WhatsApp installed", True, f"Installed, version {version}")


def check_network_state(serial: str) -> PreFlightCheck:
    """Restore of a large backup typically wants Wi-Fi (WhatsApp defaults
    to Wi-Fi-only for restore/backup unless the user has changed that
    setting) — flagged as advisory, not a hard failure, since it's a
    setting the examiner can override in WhatsApp itself if needed."""
    try:
        wifi_state = _run(["-s", serial, "shell", "settings", "get", "global", "wifi_on"], timeout=10)
    except AdbError as e:
        return PreFlightCheck("Wi-Fi connectivity", False, f"Could not check: {e}")

    if wifi_state.strip() == "1":
        return PreFlightCheck("Wi-Fi connectivity", True, "Wi-Fi is enabled on the device.")
    return PreFlightCheck("Wi-Fi connectivity", False,
                           "Wi-Fi appears off. WhatsApp restore defaults to Wi-Fi-only for "
                           "large backups — enable Wi-Fi, or change WhatsApp's backup/restore "
                           "network setting if you need to proceed over mobile data.")


def run_preflight_checks(serial: str) -> PreFlightReport:
    return PreFlightReport(checks=[
        check_google_account(serial),
        check_whatsapp_installed(serial),
        check_network_state(serial),
    ])


def get_backup_file_status(serial: str) -> BackupFileStatus:
    """Checks WA_MSGSTORE_PATHS (the same candidate locations
    adb_extractor already searches) for the current on-device backup
    file, used both as a pre-restore baseline and as the post-restore
    completion signal."""
    for candidate in WA_MSGSTORE_PATHS:
        try:
            stat_out = _run(["-s", serial, "shell", "stat", "-c", "'%s %Y'", candidate], timeout=10)
        except AdbError:
            continue
        stat_out = stat_out.strip().strip("'")
        parts = stat_out.split()
        if len(parts) == 2 and parts[0].isdigit():
            return BackupFileStatus(path=candidate, exists=True,
                                     size_bytes=int(parts[0]), mtime_epoch=int(parts[1]))
    return BackupFileStatus(path=None, exists=False)


def poll_for_restore_completion(serial: str, baseline: BackupFileStatus,
                                 progress: Optional[Callable[[str], None]] = None,
                                 poll_interval: float = 5.0, stable_checks: int = 3,
                                 timeout: float = 1800.0,
                                 should_stop: Optional[Callable[[], bool]] = None
                                 ) -> Optional[BackupFileStatus]:
    """
    Best-effort restore-completion detector: polls the backup file's size
    until it exists and stops changing across `stable_checks` consecutive
    polls, spaced `poll_interval` seconds apart. Returns the final status
    once "stable," or None on timeout / if should_stop() returns True.

    This is a heuristic, not a certainty — WhatsApp's own in-app restore
    progress indicator is the authoritative signal; treat this as "likely
    done, go check the device screen," not "definitely done."
    """
    def log(msg):
        if progress:
            progress(msg)

    start = time.time()
    last_size = baseline.size_bytes
    stable_count = 0

    log("Watching for the backup file to appear and stabilize...")
    while time.time() - start < timeout:
        if should_stop and should_stop():
            log("Monitoring stopped by user.")
            return None

        status = get_backup_file_status(serial)

        if not status.exists:
            stable_count = 0
            last_size = None
            time.sleep(poll_interval)
            continue

        if status.size_bytes == last_size and status.size_bytes is not None:
            stable_count += 1
            log(f"File size stable at {status.size_bytes} bytes "
                f"({stable_count}/{stable_checks} consecutive checks)...")
            if stable_count >= stable_checks and (baseline.size_bytes is None
                                                    or status.size_bytes != baseline.size_bytes
                                                    or not baseline.exists):
                log("Backup file size has stabilized — restore is LIKELY complete. "
                    "Confirm on the device screen before extracting.")
                return status
        else:
            stable_count = 0
            log(f"File growing: {status.size_bytes} bytes so far...")
        last_size = status.size_bytes

        time.sleep(poll_interval)

    log("Timed out waiting for the restore to stabilize. Check the device "
        "screen directly — it may still be in progress on a slow connection.")
    return None
