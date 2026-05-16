"""
pipeline/text_extractor.py
──────────────────────────
Extracts raw text from FIR uploads.

Supported input:
    • PDF    → pdfplumber (digital text) with Tesseract OCR fallback
    • Images → Tesseract OCR with preprocessing (greyscale, contrast, upscale)

Tesseract language packs:
    Use 'urd+eng' if the Urdu language pack is installed, else 'eng'. We
    auto-detect at module import via `pytesseract.get_languages()` and cache
    the chosen string.

Why OCR preprocessing?
    Scanned FIRs are typically photographed on a phone — uneven lighting,
    rotated, low resolution. Tesseract degrades quickly below ~150 DPI
    effective. Greyscale + contrast boost + upscaling reliably gains 10-30
    percentage points of character accuracy on real-world inputs.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, Optional

import pdfplumber
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger("fir.ocr")

_URDU_PATTERN = re.compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_MIN_PDF_TEXT_LEN = 40   # below this we assume the PDF is scanned, not digital


# ─────────────────────────────────────────────────────────────────────────────
# Tesseract language detection (one-shot at import)
# ─────────────────────────────────────────────────────────────────────────────

def _detect_tesseract_lang() -> str:
    try:
        langs = set(pytesseract.get_languages())
        if "urd" in langs and "eng" in langs:
            return "urd+eng"
        if "eng" in langs:
            return "eng"
        # Some installs only have osd (orientation/script detection)
        return next(iter(langs - {"osd"}), "eng")
    except Exception as e:
        logger.warning("Could not query Tesseract languages (%s). Defaulting to 'eng'.", e)
        return "eng"


TESSERACT_LANG = _detect_tesseract_lang()
# PSM 3 = fully automatic page segmentation, no OSD. Best general-purpose
# setting for FIR documents which mix prose, labels, and tabular fields.
TESSERACT_CONFIG = "--oem 1 --psm 3"

logger.info("Tesseract language: %s", TESSERACT_LANG)


# ─────────────────────────────────────────────────────────────────────────────
# Image preprocessing
# ─────────────────────────────────────────────────────────────────────────────

def _preprocess_image(img: Image.Image) -> Image.Image:
    """Greyscale + contrast + sharpen + upscale-if-small.

    Order matters: greyscale first (cuts noise), then contrast (sharpens
    text-vs-background), then sharpen filter, then upscale (so we don't
    sharpen aliasing artifacts)."""
    img = img.convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)
    w, h = img.size
    if w < 1800:
        scale = 1800 / w
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _ocr_image(img: Image.Image, lang: Optional[str] = None) -> str:
    """Run Tesseract on a PIL image."""
    img = _preprocess_image(img)
    return pytesseract.image_to_string(
        img,
        lang=lang or TESSERACT_LANG,
        config=TESSERACT_CONFIG,
    )


def _has_urdu(text: str) -> bool:
    return bool(_URDU_PATTERN.search(text))


# ─────────────────────────────────────────────────────────────────────────────
# Top-level dispatch
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(file_path: str) -> Dict:
    """Return a dict with `text`, `method`, `pages`, `confidence`.

    Confidence is a coarse 0-1 score we use only for the XAI panel — it
    reflects which method we used, not per-character accuracy.
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}:
        return _extract_image_file(file_path)
    raise ValueError(f"Unsupported file extension: {ext}")


def _extract_pdf(path: str) -> Dict:
    """Try pdfplumber first (digital text); fall back to OCR if sparse."""
    text_pages = []
    num_pages = 0
    try:
        with pdfplumber.open(path) as pdf:
            num_pages = len(pdf.pages)
            for page in pdf.pages:
                text_pages.append(page.extract_text() or "")
    except Exception as e:
        logger.warning("pdfplumber failed (%s) — falling through to OCR.", e)

    combined = "\n".join(text_pages).strip()
    if len(combined) >= _MIN_PDF_TEXT_LEN:
        return {
            "text":       combined,
            "method":     "pdfplumber",
            "pages":      num_pages,
            "confidence": 0.95,
        }

    # Scanned PDF — render pages to images and OCR each
    return _ocr_pdf(path, num_pages or 1)


def _ocr_pdf(path: str, page_hint: int) -> Dict:
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        raise RuntimeError(
            "pdf2image is required for OCR of scanned PDFs. "
            "Install poppler-utils and `pip install pdf2image`."
        ) from e

    # 300 DPI is the Tesseract sweet spot for legibility vs memory.
    images = convert_from_path(path, dpi=300)
    texts = [_ocr_image(img) for img in images]
    return {
        "text":       "\n\n".join(texts).strip(),
        "method":     "ocr_pdf",
        "pages":      len(images),
        "confidence": 0.75,
    }


def _extract_image_file(path: str) -> Dict:
    img = Image.open(path)
    text = _ocr_image(img)
    return {
        "text":       text.strip(),
        "method":     "ocr_image",
        "pages":      1,
        "confidence": 0.70,
    }