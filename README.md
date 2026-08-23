# WhatsApp Forensicator — offline edition

Desktop tool for extracting, decrypting, and reviewing WhatsApp data from an
Android device you have **lawful, physical, and authorized access to**
(your own device, or a device you're examining under proper chain-of-custody
authorization). CustomTkinter GUI, matches a dark terminal aesthetic.

## Scope (read before you extend this)

This build intentionally **excludes** live VoIP packet sniffing and
real-time geolocation of a remote party during a call. That functionality
covertly tracks a *third party who is not the device owner* during a live
call, which is a wiretapping/interception capability, not device forensics
— it's a different legal and ethical category from everything else here,
which only ever touches data already sitting on a device already in your
possession. If you're building this for a course or portfolio, keep the
scope here; it's already a legitimate, complete forensics tool.

## What's included

| Tab | What it does |
|---|---|
| ADB Extractor | Pulls `msgstore.db(.crypt12/14/15)`, `wa.db`, and the key file from a connected device over USB/TCP. Root method (`adb pull` from app-private storage) and a non-root fallback path (`adb backup`, with guidance to WhatsApp's own in-app chat export when the OS blocks it). The msgstore search, wa.db pull, key pull, and media pull all run concurrently. |
| Cloud Restore Assistant | There's no supported way to pull WhatsApp's Google Drive/iCloud backup directly — it's app-restricted storage only WhatsApp's own client can read. This tab runs pre-flight checks (Google account signed in, WhatsApp installed/version, Wi-Fi state), then passively watches for WhatsApp's own restore (triggered manually on-device) to finish, and hands off directly into the Decryptor tab once it does. |
| Filesystem Scan | Finds WhatsApp backups/media in WhatsApp's own folders (no root needed), files shared/downloaded and later moved elsewhere (matched by WhatsApp's own filename convention), and thumbnail-cache survivors — all via batched, parallelized `adb shell find` calls. |
| Decryptor | Unwraps `.crypt12/.crypt14/.crypt15` backups into a plain SQLite file using the device's own key file — the same local backup format WhatsApp itself uses for restore. Streams in 4MB chunks rather than loading the whole file into memory, so it handles real (100s of MB-GB) backup sizes without ballooning RAM. |
| Chat Viewer | Parses `message`/`jid`/`chat` tables (modern schema) or the legacy `messages` table, renders chats, searchable by contact. |
| Call Analysis | Parses `call_log`, shows type (in/out/missed), duration, video flag, timestamp. |
| Deleted Media Check | For every media reference whose full-resolution file can't be found, checks whether a thumbnail survived — either as a blob embedded directly in `msgstore.db`, or as a leftover file in a hidden `.Thumbs` cache folder. |
| Media Analysis | **Find a specific person** — classical (LBPH) face recognition trained on-the-fly from reference photo(s) you supply, no external model download. **Similar/duplicate images** — perceptual-hash grouping of the same photo resent/recompressed/resized. **Document detection** — real OCR text-density (if `tesseract-ocr` is installed) or a weaker edge-density heuristic fallback (clearly labeled as such). |
| Search Messages | Full-text keyword search across every parsed chat — case-sensitive and whole-word options, most-recent-first. |
| Export | TXT, XLSX (openpyxl), WhatsApp-styled HTML with embedded media, full chat+call reconstruction, and call-log XLSX. |

## Setup

```bash
pip install -r requirements.txt
python main.py
```

Requires:
- Python 3.10+
- `adb` (Android Platform Tools) on PATH for the ADB Extractor tab
- Tk support in your Python install (bundled on Windows/Mac installers;
  on Linux install `python3-tk` via your package manager if missing)
- **For document detection's OCR path**: the `tesseract-ocr` system
  binary must be installed separately — it's not a pip package.
  `apt install tesseract-ocr` (Debian/Ubuntu), `brew install tesseract`
  (Mac), or the installer at https://github.com/UB-Mannheim/tesseract/wiki
  (Windows). Without it, document detection still runs but falls back to
  a much weaker edge-density heuristic, and says so explicitly in its
  output rather than silently degrading.

## Known limitations / things to verify before relying on this

- **Face/person matching** uses classical LBPH recognition, not a modern
  deep embedding model — meaningfully less accurate, especially across
  lighting/angle/age variation. Treat matches as investigative leads to
  manually verify, not as a confirmed identification.
- **Similar-image grouping** catches near-duplicate image *files* (the
  same photo resent, recompressed, or resized) via perceptual hashing.
  It does not do general object detection — it will not recognize the
  same physical object photographed from a different angle or in a
  different scene.
- **Cloud Restore Assistant's completion detection is a heuristic**, not
  a certainty — it watches the on-device backup file's size for three
  consecutive stable readings and reports that as "likely complete."
  Always confirm against WhatsApp's own in-app restore progress on the
  device screen before extracting, especially on a slow connection where
  the file could legitimately pause mid-restore. There is also no
  supported way to pull the Drive/iCloud backup directly via any API —
  this tab only detects when WhatsApp's own restore (triggered manually
  on-device) has finished, it does not perform the restore itself.
- **Decryption header parsing**: the crypt14/15 protobuf header parser is
  written defensively (real varint + tag decoding, not hardcoded offsets),
  but WhatsApp's framing has drifted slightly across versions historically.
  Validate output against a known-good sample before using this for actual
  casework — cross-check against the public `wa-crypt-tools` project if a
  particular version doesn't parse.
- **Non-root extraction on Android 13-16**: if the device has a lock-screen
  credential set, Android blocks `adb backup` for third-party apps outright.
  The non-root path surfaces this clearly rather than failing silently, and
  points you to WhatsApp's own in-app "Transfer chats" / local backup export
  as the fallback.
- **Schema detection**: `db_parser.py` probes for the modern
  (`message`/`jid`/`chat`) vs legacy (`messages`) schema and adapts. Very
  old or very new WhatsApp versions outside what was tested here may need
  schema tweaks.

## Project layout

```
wa_forensicator/
├── main.py                  # CustomTkinter app shell, tabs, session state
├── requirements.txt
├── ui/
│   └── theme.py              # colors/fonts
└── modules/
    ├── adb_extractor.py     # adb root + non-root extraction pipelines
    ├── cloud_restore_assistant.py  # preflight checks + backup size watcher
    ├── fs_scanner.py        # batched/parallelized adb shell find
    ├── decryptor.py         # crypt12/14/15 AES-GCM streaming decryption
    ├── db_parser.py         # SQLite msgstore + wa.db parsing & normalization
    ├── media_resolver.py    # local path resolution + thumbnail blob extraction
    ├── image_analysis.py    # LBPH face match, dHash grouping, OCR document check
    ├── message_search.py    # full-text keyword search across messages
    └── export.py            # TXT, XLSX, styled HTML exports
```
