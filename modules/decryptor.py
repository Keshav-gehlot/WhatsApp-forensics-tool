"""
Decryptor
---------
Decrypts WhatsApp's local backup database formats (.crypt12 / .crypt14 /
.crypt15) into a plain SQLite file, given the device's own key file
(pulled from /data/data/com.whatsapp/files/key by adb_extractor, or
exported by the user from a device they control).

This targets the same, publicly documented backup format used by WhatsApp
itself for its own local backup/restore feature — the same algorithm
implemented by several public open-source tools (e.g. wa-crypt-tools,
WhatsApp-Key-DB-Extractor). No WhatsApp server, transport encryption, or
E2E messaging key is touched — this only unwraps the *local, on-device*
backup file using the key that already lives on that same device.

NOTE: WhatsApp has changed backup header framing slightly across versions.
The protobuf-style header parser below is written defensively (real varint
+ tag parsing rather than hardcoded byte offsets) so it should tolerate
minor version drift, but you should validate output against a known-good
sample before relying on this for casework. Cross-check against
wa-crypt-tools if you hit a version this doesn't parse cleanly.
"""

import hashlib
import hmac
import os
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Optional

from Crypto.Cipher import AES


class DecryptError(RuntimeError):
    pass


@dataclass
class DecryptResult:
    output_path: str
    backup_format: str  # "crypt12" | "crypt14" | "crypt15"
    custody_warning: str = ""   # non-empty if the audit-log write failed


def derive_crypt15_key(root_key: bytes) -> bytes:
    """crypt15 backups use a 32-byte "root" key (the 64-hex-digit end-to-end
    backup key shown by WhatsApp) from which the AES key is derived with
    an HMAC-SHA256 chain. Mirrors the scheme used by wa-crypt-tools —
    verify against it for your WhatsApp version."""
    prk = hmac.new(b"\x00" * 32, root_key, hashlib.sha256).digest()
    return hmac.new(prk, b"backup encryption\x01", hashlib.sha256).digest()


def load_key(key_file_path: str, crypt15: bool = False) -> bytes:
    """
    Returns the AES key.

    - A text file (or a pasted string path to one) containing 64 hex digits
      is treated as the end-to-end backup key. For crypt15 it is run
      through derive_crypt15_key().
    - Otherwise the file is treated as the binary on-device `key` file and
      the final 32 bytes are the AES key (crypt12/14).
    """
    with open(key_file_path, "rb") as f:
        data = f.read()
    text = data.strip()
    if re.fullmatch(rb"[0-9a-fA-F]{64}", text):
        raw = bytes.fromhex(text.decode())
        return derive_crypt15_key(raw) if crypt15 else raw
    if crypt15:
        raise DecryptError(
            "crypt15 backups need the 64-digit hex end-to-end backup key "
            "(saved in a text file) — the binary `key` file is not enough."
        )
    if len(data) < 32:
        raise DecryptError(
            f"Key file too small ({len(data)} bytes) — expected a WhatsApp "
            "key file of ~32-158 bytes."
        )
    return data[-32:]


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise DecryptError("Malformed protobuf header (varint overrun).")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _parse_backup_header(header: bytes) -> dict:
    """
    Minimal protobuf-wireformat scanner. Extracts length-delimited fields
    (wire type 2) keyed by field number — field 1 is the IV in every
    observed crypt14/15 sample. Unknown fields are skipped, not assumed.
    """
    fields = {}
    pos = 0
    while pos < len(header):
        tag, pos = _read_varint(header, pos)
        field_no = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            _, pos = _read_varint(header, pos)
        elif wire_type == 2:  # length-delimited (bytes/string/submessage)
            length, pos = _read_varint(header, pos)
            value = header[pos:pos + length]
            pos += length
            fields[field_no] = value
        elif wire_type == 5:  # 32-bit
            pos += 4
        elif wire_type == 1:  # 64-bit
            pos += 8
        else:
            raise DecryptError(f"Unsupported protobuf wire type {wire_type} in header.")
    return fields


CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB

# Bytes that may follow the 16-byte GCM tag at the end of the file
# (checksum footers differ by version). Tried in order until the tag
# verifies; a wrong guess just fails authentication and is discarded.
FOOTER_CANDIDATES = (0, 16, 20)


class _AttemptFailed(Exception):
    pass


def _attempt_gcm_zlib(key: bytes, iv: bytes, input_path: str, body_start: int,
                      footer: int, tmp_path: str) -> None:
    """One decrypt attempt assuming `footer` extra bytes after the tag.
    Streams: AES-GCM decrypt -> zlib inflate -> tmp_path. Raises
    _AttemptFailed on any authentication or decompression failure."""
    file_size = os.path.getsize(input_path)
    ciphertext_len = file_size - body_start - 16 - footer
    if ciphertext_len <= 0:
        raise _AttemptFailed("file too small")

    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    inflater = zlib.decompressobj()
    with open(input_path, "rb") as fin, open(tmp_path, "wb") as fout:
        fin.seek(body_start)
        remaining = ciphertext_len
        while remaining > 0:
            chunk = fin.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            plain = cipher.decrypt(chunk)
            try:
                fout.write(inflater.decompress(plain))
            except zlib.error as e:
                raise _AttemptFailed(f"zlib: {e}")
        tag = fin.read(16)
        try:
            fout.write(inflater.flush())
        except zlib.error as e:
            raise _AttemptFailed(f"zlib: {e}")
    if len(tag) != 16:
        raise _AttemptFailed("missing tag")
    try:
        cipher.verify(tag)
    except ValueError:
        raise _AttemptFailed("GCM authentication failed")
    if not inflater.eof:
        raise _AttemptFailed("zlib stream truncated")


def _stream_decrypt_gcm(key: bytes, iv: bytes, input_path: str,
                        body_start: int, output_path: str) -> None:
    """
    WhatsApp compresses the SQLite database with zlib and THEN encrypts it
    (AES-256-GCM), so decryption must also inflate. Output goes to a
    ".partial" file and is only moved to output_path after the GCM tag
    verifies and the zlib stream ends cleanly — unauthenticated bytes never
    sit at the real output path.
    """
    tmp_path = output_path + ".partial"
    last = "unknown"
    try:
        for footer in FOOTER_CANDIDATES:
            try:
                _attempt_gcm_zlib(key, iv, input_path, body_start, footer, tmp_path)
            except _AttemptFailed as e:
                last = str(e)
                continue
            os.replace(tmp_path, output_path)
            return
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    raise DecryptError(
        "Decryption failed (" + last + ") — wrong key, wrong header/IV "
        "offset, or a corrupted/truncated backup file."
    )


def _find_iv(fields_tree, depth=0):
    """Depth-first search for a 16-byte length-delimited value, preferring
    field 1 at the top level (older layouts) but also looking inside nested
    messages (newer layouts nest cipher info)."""
    if depth > 4:
        return None
    top = fields_tree.get(1)
    if isinstance(top, bytes) and len(top) == 16:
        return top
    for value in fields_tree.values():
        if isinstance(value, bytes):
            if len(value) == 16 and depth > 0:
                return value
            try:
                sub = _parse_backup_header(value)
            except DecryptError:
                continue
            found = _find_iv(sub, depth + 1)
            if found:
                return found
    return None


def decrypt_crypt14_15(key: bytes, input_path: str, output_path: str) -> DecryptResult:
    HEADER_PREFIX_READ = 4096
    with open(input_path, "rb") as f:
        prefix = f.read(HEADER_PREFIX_READ)
    if len(prefix) < 4:
        raise DecryptError("File too small to be a valid crypt14/15 backup.")

    fmt = "crypt15" if input_path.lower().endswith(".crypt15") else "crypt14"

    # Framing differs slightly between versions: the header-length varint
    # sits at byte 1 in some layouts, and some add one extra flag byte
    # before the protobuf. Try each; a wrong guess fails GCM auth cleanly.
    framings = []
    for len_off, skip in ((1, 0), (0, 1), (0, 0)):
        try:
            header_len, pos = _read_varint(prefix, len_off)
        except DecryptError:
            continue
        pos += skip
        if pos + header_len > len(prefix):
            continue
        framings.append((pos, header_len))

    last_err = None
    for pos, header_len in framings:
        try:
            fields = _parse_backup_header(prefix[pos:pos + header_len])
        except DecryptError as e:
            last_err = e
            continue
        iv = _find_iv(fields)
        if not iv:
            continue
        try:
            _stream_decrypt_gcm(key, iv, input_path, pos + header_len, output_path)
            return DecryptResult(output_path=output_path, backup_format=fmt)
        except DecryptError as e:
            last_err = e
    raise DecryptError(
        "Could not decrypt this backup with any known header layout"
        + (f" (last error: {last_err})" if last_err else "")
        + ". Cross-check the file and key against wa-crypt-tools."
    )


def decrypt_crypt12(key: bytes, input_path: str, output_path: str) -> DecryptResult:
    """crypt12: short plaintext header, 16-byte IV at offset 51, ciphertext
    from offset 67."""
    file_size = os.path.getsize(input_path)
    if file_size < 67 + 16:
        raise DecryptError("File too small to be a valid crypt12 backup.")
    with open(input_path, "rb") as f:
        prefix = f.read(67)
    iv = prefix[51:67]
    _stream_decrypt_gcm(key, iv, input_path, body_start=67, output_path=output_path)
    return DecryptResult(output_path=output_path, backup_format="crypt12")


def decrypt_backup(key_file_path: str, input_path: str, output_dir: str) -> DecryptResult:
    os.makedirs(output_dir, exist_ok=True)
    lower = input_path.lower()
    key = load_key(key_file_path, crypt15=lower.endswith(".crypt15"))
    output_path = os.path.join(output_dir, "msgstore.decrypted.db")

    if lower.endswith(".crypt12"):
        result = decrypt_crypt12(key, input_path, output_path)
    elif lower.endswith(".crypt14") or lower.endswith(".crypt15"):
        result = decrypt_crypt14_15(key, input_path, output_path)
    else:
        raise DecryptError(
            f"Unrecognized backup extension for '{os.path.basename(input_path)}'. "
            "Expected .crypt12, .crypt14, or .crypt15."
        )

    # Chain of custody: hash input and output, log both.
    try:
        from . import custody
        custody.record(output_dir, "decrypt-input", input_path)
        custody.record(output_dir, "decrypt-output", output_path,
                       note=f"format={result.backup_format}")
    except Exception as e:  # surface it — a missing audit record must be visible
        result.custody_warning = f"custody log could not be written ({e})"
    return result
