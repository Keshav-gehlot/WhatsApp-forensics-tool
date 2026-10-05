# Third-party notices

WhatsApp Forensicator itself is MIT-licensed (see `LICENSE`). It uses the
following third-party software. Licenses below are what each project publishes
as far as we know — **verify against the copies you actually ship.**

## Python packages (installed via pip, not stored in this repo)

| Package | License |
|---|---|
| customtkinter | MIT |
| pycryptodome | BSD-2-Clause (+ public-domain parts) |
| openpyxl | MIT |
| Pillow | MIT-CMU (HPND) |
| opencv-contrib-python-headless | Apache-2.0 (OpenCV); wheel bundles FFmpeg (LGPL) and other libs |
| numpy | BSD-3-Clause |
| imagehash | BSD-2-Clause |
| pytesseract | Apache-2.0 |
| reportlab | BSD-3-Clause |
| PyInstaller (build only) | GPL-2.0 with a special exception allowing bundled apps under any license |

## Tesseract OCR (the `bin/tesseract/` folder)

`bin/` holds a Windows Tesseract OCR installation. It is **git-ignored**, so it
is not in the repository; it is only on a machine where someone placed it
(and in `build_exe.bat` output).

- **Tesseract** is licensed under the **Apache License 2.0**
  (Copyright Tesseract OCR authors; originally Hewlett-Packard / Google). The
  license text is in `bin/tesseract/doc/LICENSE`; `AUTHORS` is alongside it.
- **Language data (`tessdata/*.traineddata`)** comes from the Tesseract project
  and is also published under Apache-2.0.
- The Windows build also ships **runtime DLLs** (e.g. libcurl, libarchive,
  Leptonica, ICU, glib/pango/cairo/harfbuzz/fribidi, libstdc++, libiconv). Their
  licenses vary (MIT/BSD/zlib-style, LGPL, GPL-with-runtime-exception). Several
  are LGPL, which is fine for dynamically-linked, replaceable DLLs shipped
  unmodified.
- `bin/tesseract/tessdata/` also contains a few `.jar` files from Tesseract's
  training tools (jaxb-api, piccolo2d, ScrollView). They are not used by this
  app; omit them if you redistribute.

**If you redistribute `bin/`** (for example inside a release zip): keep
`bin/tesseract/doc/LICENSE` and the DLL license files with it, do not remove
copyright notices, and link to the upstream source
(https://github.com/tesseract-ocr/tesseract, installer by UB Mannheim:
https://github.com/UB-Mannheim/tesseract/wiki). Consider shipping only the
languages you need to keep downloads small.

## Optional face models (`models/`)

YuNet and SFace ONNX models come from the OpenCV Zoo
(https://github.com/opencv/opencv_zoo). They are **not** bundled; check each
model's license on its model card before redistributing.
