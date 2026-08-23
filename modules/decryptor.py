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

import os
import struct
from dataclasses import dataclass
from typing import Optional

from Crypto.Cipher import AES


class DecryptError(RuntimeError):
    pass


@dataclass
class DecryptResult:
    output_path: str
    backup_format: str  # "crypt12" | "crypt14" | "crypt15"


def load_key(key_file_path: str) -> bytes:
    """
    The WhatsApp key file is a fixed-size binary blob; the actual AES key
    is the final 32 bytes. Everything before it is a version/cipher-suite
    marker plus (on some versions) an HMAC of the key for integrity
    checking, which we don't need to verify to decrypt.
    """
    with open(key_file_path, "rb") as f:
        data = f.read()
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


def _stream_decrypt_gcm(key: bytes, iv: bytes, input_path: str,
                         body_start: int, output_path: str) -> None:
    """
    Decrypts a GCM-wrapped body in fixed-size chunks rather than loading
    the whole file into memory. WhatsApp backups routinely run from
    hundreds of MB to a couple GB; the previous implementation read the
    entire ciphertext into RAM and produced an equally large plaintext
    buffer before writing anything — roughly 2x the file size held in
    memory at once, for no real benefit at this file size.

    GCM's authentication tag lives at the very end of the file and can
    only be verified once every byte has been decrypted, so this writes
    to a temporary ".partial" file first and only moves it to the real
    output path via os.replace() after cipher.verify() succeeds. If the
    tag doesn't match, the partially-written (unauthenticated) bytes are
    deleted rather than left at the real output path — the same
    all-or-nothing guarantee the previous whole-buffer approach gave,
    just without holding the whole file in memory to get there.
    """
    file_size = os.path.getsize(input_path)
    ciphertext_len = file_size - body_start - 16
    if ciphertext_len < 0:
        raise DecryptError("File too small for the declared header size.")

    cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
    tmp_path = output_path + ".partial"

    try:
        with open(input_path, "rb") as fin, open(tmp_path, "wb") as fout:
            fin.seek(body_start)
            remaining = ciphertext_len
            while remaining > 0:
                chunk = fin.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                fout.write(cipher.decrypt(chunk))
            tag = fin.read(16)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    if len(tag) != 16:
        os.remove(tmp_path)
        raise DecryptError("Ciphertext too short to contain a GCM tag.")

    try:
        cipher.verify(tag)
    except ValueError as e:
        os.remove(tmp_path)
        raise DecryptError(
            "GCM authentication failed — wrong key, wrong IV offset, or "
            "corrupted/truncated backup file."
        ) from e

    os.replace(tmp_path, output_path)


def decrypt_crypt14_15(key: bytes, input_path: str, output_path: str) -> DecryptResult:
    # The protobuf header is always tiny (a handful of fields, the
    # largest being a 16-byte IV) — reading a generous fixed prefix
    # avoids loading the whole (potentially huge) file just to get the
    # header, while still being far more than any real header needs.
    HEADER_PREFIX_READ = 4096
    with open(input_path, "rb") as f:
        prefix = f.read(HEADER_PREFIX_READ)

    if len(prefix) < 4:
        raise DecryptError("File too small to be a valid crypt14/15 backup.")

    # Byte 0: version/reserved marker. Byte 1..: varint header length.
    header_len, pos = _read_varint(prefix, 1)
    if pos + header_len > len(prefix):
        raise DecryptError(
            f"Backup header ({header_len} bytes) exceeds the "
            f"{HEADER_PREFIX_READ}-byte prefix read — unexpected for this "
            "format; the header framing may differ for this version."
        )
    header = prefix[pos:pos + header_len]
    body_start = pos + header_len

    fields = _parse_backup_header(header)
    iv = fields.get(1)
    if not iv or len(iv) != 16:
        raise DecryptError(
            "Could not locate a 16-byte IV in the backup header. The "
            "header framing may differ for this WhatsApp version — "
            "cross-check against wa-crypt-tools."
        )

    fmt = "crypt15" if input_path.lower().endswith(".crypt15") else "crypt14"
    _stream_decrypt_gcm(key, iv, input_path, body_start, output_path)
    return DecryptResult(output_path=output_path, backup_format=fmt)


def decrypt_crypt12(key: bytes, input_path: str, output_path: str) -> DecryptResult:
    """
    crypt12 predates the protobuf header and uses a fixed-offset layout:
    a short plaintext header, then a 16-byte IV, then GCM ciphertext+tag.
    Offsets below match the widely-published crypt12 layout used by
    earlier WhatsApp-Key-DB-Extractor-style tools.
    """
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
    key = load_key(key_file_path)
    output_path = os.path.join(output_dir, "msgstore.decrypted.db")

    lower = input_path.lower()
    if lower.endswith(".crypt12"):
        return decrypt_crypt12(key, input_path, output_path)
    elif lower.endswith(".crypt14") or lower.endswith(".crypt15"):
        return decrypt_crypt14_15(key, input_path, output_path)
    else:
        raise DecryptError(
            f"Unrecognized backup extension for '{os.path.basename(input_path)}'. "
            "Expected .crypt12, .crypt14, or .crypt15."
        )
