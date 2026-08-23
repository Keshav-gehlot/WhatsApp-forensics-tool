"""
Media Resolver
--------------
Links a message's on-device media reference (a path like
"Media/WhatsApp Images/IMG-20260110-WA0003.jpg" recorded in msgstore.db)
to an actual local file — whether that file came from the extracted
WhatsApp Media folder, or was found elsewhere on the device via the
filesystem scanner because it had been saved or moved out of the
WhatsApp folder.

Resolution is by basename match. Message rows only ever recorded
WhatsApp's own filename convention (IMG-/VID-/AUD-/PTT-/DOC-...-WA####.ext),
and that convention is exactly what survives a copy or move — the same
property fs_scanner already relies on to find shared/downloaded files
system-wide.

This module also distinguishes full-resolution media from thumbnail
CACHE files (hidden ".Thumbs"/".thumbnails" folders). Those two are kept
in separate indexes deliberately: a thumbnail surviving after the
original was deleted is a meaningful forensic finding in its own right
(recoverable evidence that a photo/video existed even though the full
file is gone), and conflating the two would misreport a low-res cache
thumbnail as if it were the original file.
"""

import os
from dataclasses import dataclass
from typing import Optional, Iterable

THUMB_DIR_MARKERS = {".thumbs", ".thumbnails", "thumbs", "thumbnails"}


def is_thumbnail_path(path: str) -> bool:
    parts = {p.lower() for p in path.replace("\\", "/").split("/")}
    return bool(parts & THUMB_DIR_MARKERS)


class MediaIndex:
    """Indexes full-resolution media only — thumbnail-cache files are
    deliberately excluded so they can never be mistaken for an original."""

    def __init__(self):
        self._by_basename: dict[str, str] = {}

    def add_directory(self, directory: Optional[str]):
        if not directory or not os.path.isdir(directory):
            return
        for root, _dirs, files in os.walk(directory):
            for fname in files:
                full = os.path.join(root, fname)
                if is_thumbnail_path(full):
                    continue
                self._by_basename.setdefault(fname, full)

    def add_paths(self, paths: Iterable[str]):
        for p in paths:
            if p and os.path.isfile(p) and not is_thumbnail_path(p):
                self._by_basename.setdefault(os.path.basename(p), p)

    def all_paths(self) -> list:
        """All indexed local file paths — used to feed a bulk scan (image
        analysis, etc.) rather than resolving one specific reference."""
        return list(self._by_basename.values())

    def image_paths(self) -> list:
        """Subset of all_paths() that look like image files, by extension."""
        image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
        return [p for p in self._by_basename.values()
                if os.path.splitext(p)[1].lower() in image_exts]

    def resolve(self, relative_path: Optional[str]) -> Optional[str]:
        if not relative_path:
            return None
        basename = os.path.basename(relative_path)
        return self._by_basename.get(basename)

    def __len__(self):
        return len(self._by_basename)


class ThumbnailIndex:
    """Indexes ONLY files found under a thumbnail-cache directory. Used to
    recover a preview of media whose full-resolution original is gone."""

    def __init__(self):
        self._by_basename: dict[str, str] = {}

    def add_directory(self, directory: Optional[str]):
        if not directory or not os.path.isdir(directory):
            return
        for root, _dirs, files in os.walk(directory):
            for fname in files:
                full = os.path.join(root, fname)
                if is_thumbnail_path(full):
                    self._by_basename.setdefault(fname, full)

    def add_paths(self, paths: Iterable[str]):
        for p in paths:
            if p and os.path.isfile(p):
                self._by_basename.setdefault(os.path.basename(p), p)

    def resolve(self, relative_path: Optional[str]) -> Optional[str]:
        if not relative_path:
            return None
        basename = os.path.basename(relative_path)
        return self._by_basename.get(basename)

    def __len__(self):
        return len(self._by_basename)


@dataclass
class DeletedMediaFinding:
    chat_jid: str
    chat_name: str
    message: object                      # the db_parser.Message this refers to
    original_filename: str
    has_thumbnail: bool
    thumbnail_source: Optional[str]      # "db_blob" | "thumbnail_cache_file" | None
    thumbnail_local_path: Optional[str] = None   # only set for thumbnail_cache_file


def find_deleted_media(chats, media_index: "MediaIndex",
                        thumbnail_index: "ThumbnailIndex") -> list[DeletedMediaFinding]:
    """
    Walks every message that references media and checks whether the
    full-resolution original is actually recoverable. For every one where
    it is NOT, reports whether a thumbnail survived — either as a cache
    file (ThumbnailIndex) or as a blob stored directly inside msgstore.db
    (Message.thumbnail_blob), which persists independently of the
    filesystem entirely.

    A message with media_relative_path set but no resolvable original is
    the working definition of "this media was deleted" used here: the
    database still references it, but nothing on disk answers to that
    filename anymore.
    """
    findings = []
    for chat in chats:
        for msg in chat.messages:
            if not getattr(msg, "media_relative_path", None):
                continue
            if media_index.resolve(msg.media_relative_path):
                continue  # original still present — not a deleted-media case

            thumb_path = thumbnail_index.resolve(msg.media_relative_path)
            has_blob = bool(getattr(msg, "thumbnail_blob", None))
            source = "db_blob" if has_blob else ("thumbnail_cache_file" if thumb_path else None)

            findings.append(DeletedMediaFinding(
                chat_jid=chat.jid,
                chat_name=chat.display_name,
                message=msg,
                original_filename=os.path.basename(msg.media_relative_path),
                has_thumbnail=has_blob or bool(thumb_path),
                thumbnail_source=source,
                thumbnail_local_path=thumb_path,
            ))
    return findings
