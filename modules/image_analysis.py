"""
Image Analysis
--------------
Local, offline analysis over already-extracted/recovered images: face
detection and reference-photo person matching, perceptual-hash-based
similar/duplicate image grouping, and document classification.

Nothing here uploads any image anywhere or calls any external service.
Face detection uses OpenCV's classical Haar cascade — bundled inside the
opencv package itself, no model download required at any point. Person
matching uses LBPH face recognition (also fully local, no external
model, no internet dependency), trained on-the-fly from whatever
reference photo(s) the examiner supplies. This is classical, non-deep-
learning face recognition — meaningfully less accurate than a modern
embedding model, but fully self-contained, which matters for an offline
forensic tool that shouldn't need internet access to function. Treat
results as investigative leads to manually review, not as a conclusive
identification.

Document detection uses real OCR text-density via pytesseract IF the
system tesseract-ocr binary is installed, and degrades to a much weaker
edge-density heuristic (explicitly labeled as such) if it isn't —
tesseract is a system package this tool cannot install on the
examiner's machine on its own.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Callable

import cv2
import numpy as np
from PIL import Image
import imagehash

try:
    import pytesseract
    _PYTESSERACT_IMPORTED = True
except ImportError:
    _PYTESSERACT_IMPORTED = False


try:
    _cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    if os.path.isfile(_cascade_path):
        _FACE_CASCADE = cv2.CascadeClassifier(_cascade_path)
        if _FACE_CASCADE.empty():
            _FACE_CASCADE = None
    else:
        _FACE_CASCADE = None
except Exception:
    _FACE_CASCADE = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def set_tesseract_cmd(path: str) -> None:
    """Point pytesseract at a specific tesseract executable path.
    Call this before any OCR operation when the binary is not on PATH."""
    if _PYTESSERACT_IMPORTED:
        pytesseract.pytesseract.tesseract_cmd = path


def get_tesseract_info() -> Optional[str]:
    """Returns a human-readable version string if tesseract is reachable,
    or None if not. Useful for status display in the UI."""
    if not _PYTESSERACT_IMPORTED:
        return None
    try:
        ver = pytesseract.get_tesseract_version()
        return str(ver)
    except Exception:
        return None


def is_ocr_available() -> bool:
    """Checks the actual tesseract binary, not just whether the pytesseract
    Python package imported — the package can be installed with no system
    binary present, which would otherwise fail silently at call time."""
    return get_tesseract_info() is not None


# ---------------------------------------------------------------- faces

@dataclass
class FaceDetection:
    image_path: str
    bounding_boxes: list  # list of (x, y, w, h) in pixel coordinates

    @property
    def face_count(self) -> int:
        return len(self.bounding_boxes)


def detect_faces(image_path: str) -> Optional[FaceDetection]:
    """Returns None if the image can't be read at all (corrupt/unsupported
    format) rather than raising — callers scanning hundreds of recovered
    files shouldn't have one bad file abort the whole scan.

    Also returns None (no faces detected) when the Haar cascade is
    unavailable (headless OpenCV build without data files)."""
    if _FACE_CASCADE is None:
        return FaceDetection(image_path=image_path, bounding_boxes=[])
    img = cv2.imread(image_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    boxes = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    return FaceDetection(image_path=image_path, bounding_boxes=[tuple(int(v) for v in b) for b in boxes])


def scan_for_faces(image_paths: list[str], progress: Optional[Callable[[str], None]] = None
                    ) -> list[FaceDetection]:
    results = []
    for i, path in enumerate(image_paths):
        det = detect_faces(path)
        if det and det.face_count > 0:
            results.append(det)
        if progress and i % 20 == 0:
            progress(f"Scanned {i + 1}/{len(image_paths)} images for faces...")
    return results


# ----------------------------------------------------- person matching

@dataclass
class PersonMatch:
    image_path: str
    confidence: float   # LBPH distance — LOWER means a closer match
    bounding_box: tuple


class PersonMatcher:
    """
    Trains a local LBPH face recognizer from one or more reference
    photos of a specific person, then scores candidate images for how
    closely any detected face matches.
    """

    def __init__(self):
        self._recognizer = cv2.face.LBPHFaceRecognizer_create()
        self._trained = False

    def train(self, reference_image_paths: list[str]) -> int:
        """Returns the number of reference faces actually used. Raises
        ValueError if none of the reference images had a detectable face."""
        faces = []
        for path in reference_image_paths:
            det = detect_faces(path)
            if not det or det.face_count == 0:
                continue
            img = cv2.imread(path)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            x, y, w, h = det.bounding_boxes[0]  # largest/first detected face
            faces.append(cv2.resize(gray[y:y + h, x:x + w], (200, 200)))

        if not faces:
            raise ValueError(
                "No face could be detected in any of the reference images. "
                "Use a clearer, more front-facing reference photo."
            )

        labels = np.ones(len(faces), dtype=np.int32)
        self._recognizer.train(faces, labels)
        self._trained = True
        return len(faces)

    def match(self, candidate_image_paths: list[str], max_distance: float = 80.0,
              progress: Optional[Callable[[str], None]] = None) -> list[PersonMatch]:
        """
        max_distance: LBPH distance threshold below which a detected face
        is reported as a plausible match. This is a heuristic cutoff, not
        a calibrated probability — lower is stricter. Results are sorted
        best-match-first.
        """
        if not self._trained:
            raise RuntimeError("Call train() with reference photos before match().")

        results = []
        for i, path in enumerate(candidate_image_paths):
            det = detect_faces(path)
            if det and det.face_count > 0:
                img = cv2.imread(path)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                for (x, y, w, h) in det.bounding_boxes:
                    face = cv2.resize(gray[y:y + h, x:x + w], (200, 200))
                    _, distance = self._recognizer.predict(face)
                    if distance <= max_distance:
                        results.append(PersonMatch(image_path=path, confidence=distance,
                                                    bounding_box=(x, y, w, h)))
            if progress and i % 20 == 0:
                progress(f"Checked {i + 1}/{len(candidate_image_paths)} images for person match...")

        results.sort(key=lambda m: m.confidence)
        return results


# --------------------------------------------- similar/duplicate images

def compute_phash(image_path: str):
    try:
        with Image.open(image_path) as img:
            return imagehash.phash(img)
    except Exception:
        return None


def group_similar_images(image_paths: list[str], max_distance: int = 8,
                          progress: Optional[Callable[[str], None]] = None) -> list[list[str]]:
    """
    Groups images that are visually near-identical — the same photo
    resent, re-compressed, resized, or lightly edited — using perceptual
    hashing. max_distance is a Hamming-distance threshold on the 64-bit
    hash: smaller is stricter (near-exact duplicates only), larger
    catches more distant variants at the cost of more false groupings.

    This is NOT general object detection — it will not recognize "the
    same physical object photographed from a different angle or in a
    different scene." It catches near-duplicate image FILES, which is
    the common real-world case for repeatedly shared/forwarded media
    (WhatsApp recompresses on send, so the same photo forwarded twice
    is rarely byte-identical but is still perceptually near-identical).
    """
    hashes = {}
    for i, path in enumerate(image_paths):
        h = compute_phash(path)
        if h is not None:
            hashes[path] = h
        if progress and i % 20 == 0:
            progress(f"Hashed {i + 1}/{len(image_paths)} images...")

    paths = list(hashes.keys())
    visited = set()
    groups = []
    for i, p1 in enumerate(paths):
        if p1 in visited:
            continue
        group = [p1]
        visited.add(p1)
        for p2 in paths[i + 1:]:
            if p2 in visited:
                continue
            if hashes[p1] - hashes[p2] <= max_distance:
                group.append(p2)
                visited.add(p2)
        if len(group) > 1:
            groups.append(group)
    return groups


# ------------------------------------------------------- document scan

@dataclass
class DocumentFinding:
    image_path: str
    likely_document: bool
    reason: str
    extracted_text: Optional[str] = None


def classify_document(image_path: str, ocr_min_chars: int = 25) -> Optional[DocumentFinding]:
    """
    Classifies whether an image is "a photographed/scanned document" as
    opposed to an ordinary photo. Uses real OCR text-density if
    tesseract is installed (the reliable signal); otherwise falls back
    to a much weaker aspect-ratio/edge-density heuristic and says so
    explicitly in the finding's reason string — never silently claims
    heuristic-only output is OCR-verified.
    """
    try:
        img = Image.open(image_path)
    except Exception:
        return None

    if is_ocr_available():
        try:
            text = pytesseract.image_to_string(img).strip()
        except Exception:
            text = ""
        if len(text) >= ocr_min_chars:
            return DocumentFinding(image_path=image_path, likely_document=True,
                                    reason=f"OCR extracted {len(text)} characters of text",
                                    extracted_text=text)
        return DocumentFinding(image_path=image_path, likely_document=False,
                                reason="OCR found little/no text", extracted_text=text or None)

    # Fallback heuristic — meaningfully weaker than OCR, flagged as such.
    cv_img = cv2.imread(image_path)
    if cv_img is None:
        return None
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edge_density = float(np.count_nonzero(edges)) / edges.size
    likely = edge_density > 0.08
    return DocumentFinding(
        image_path=image_path, likely_document=likely,
        reason=(f"heuristic edge-density estimate ({edge_density:.3f}) — "
                f"tesseract-ocr is not installed on this machine, so this is "
                f"NOT real OCR-based detection and should be treated as a "
                f"rough guess only, not evidence-grade"),
    )


def scan_for_documents(image_paths: list[str], progress: Optional[Callable[[str], None]] = None
                        ) -> list[DocumentFinding]:
    results = []
    for i, path in enumerate(image_paths):
        finding = classify_document(path)
        if finding and finding.likely_document:
            results.append(finding)
        if progress and i % 10 == 0:
            progress(f"Checked {i + 1}/{len(image_paths)} images for documents...")
    return results
