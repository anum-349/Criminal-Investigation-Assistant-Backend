"""
pipeline/text_extractor.py
Extracts raw text from:
  - Native text PDFs   → pdfplumber (fast, accurate)
  - Scanned PDFs       → pdf2image + Tesseract OCR
  - Images (PNG/JPG)   → Tesseract OCR directly

Tesseract language: eng+urd  (falls back to eng if urd pack not installed)
"""

import os
import pdfplumber
import pytesseract
from PIL import Image

# Try to use both English and Urdu Tesseract language packs
def _tesseract_lang() -> str:
    try:
        langs = pytesseract.get_languages()
        return "eng+urd" if "urd" in langs else "eng"
    except Exception:
        return "eng"

TESSERACT_LANG = _tesseract_lang()
TESSERACT_CONFIG = f"--oem 1 --psm 3"   # LSTM engine, auto page segmentation


def extract_text(file_path: str) -> dict:
    """
    Returns:
        {
          "text": str,            # raw extracted text
          "method": str,          # "pdfplumber" | "ocr_pdf" | "ocr_image"
          "pages": int,
          "confidence": float,    # 0-1 estimate
        }
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}:
        return _extract_image(file_path)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")


def _extract_pdf(path: str) -> dict:
    """Try pdfplumber first; fall back to OCR if text is sparse."""
    text_pages = []
    with pdfplumber.open(path) as pdf:
        num_pages = len(pdf.pages)
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_pages.append(t)

    combined = "\n".join(text_pages).strip()

    # If text is too sparse (scanned PDF), fall back to OCR
    if len(combined) < 50:
        return _ocr_pdf(path, num_pages)

    return {
        "text": combined,
        "method": "pdfplumber",
        "pages": num_pages,
        "confidence": 0.95,
    }


def _ocr_pdf(path: str, num_pages: int) -> dict:
    """Convert PDF pages to images, then OCR each page."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise RuntimeError("pdf2image not installed. Run: pip install pdf2image")

    images = convert_from_path(path, dpi=300)
    texts = []
    for img in images:
        t = pytesseract.image_to_string(img, lang=TESSERACT_LANG, config=TESSERACT_CONFIG)
        texts.append(t)

    return {
        "text": "\n".join(texts).strip(),
        "method": "ocr_pdf",
        "pages": num_pages,
        "confidence": 0.75,
    }


def _extract_image(path: str) -> dict:
    img = Image.open(path)
    # Upscale small images for better OCR
    w, h = img.size
    if w < 1000:
        scale = 1000 / w
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    text = pytesseract.image_to_string(img, lang=TESSERACT_LANG, config=TESSERACT_CONFIG)
    return {
        "text": text.strip(),
        "method": "ocr_image",
        "pages": 1,
        "confidence": 0.70,
    }


"""
text_extractor.py
─────────────────
Extracts raw text from uploaded FIR documents.

Supported input types:
  • PDF  — tries pdfplumber (text-based) first, then pdf2image + Tesseract (scanned).
  • Image (JPG / PNG / BMP / TIFF) — Tesseract OCR with auto PSM detection.

Tesseract language: 'urd+eng' if Urdu data present, else 'eng'.
Falls back gracefully when 'urd' tessdata is not installed.
"""

import io
import re
from pathlib import Path
from typing import Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image, ImageEnhance, ImageFilter

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_URDU_PATTERN = re.compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_MIN_TEXT_LEN = 40           # chars; below this we assume OCR needed


def _preprocess_image(img: Image.Image) -> Image.Image:
    """Enhance image for better OCR accuracy."""
    img = img.convert("L")                                # greyscale
    img = ImageEnhance.Contrast(img).enhance(2.0)        # boost contrast
    img = img.filter(ImageFilter.SHARPEN)
    # Upscale small images
    w, h = img.size
    if w < 1800:
        scale = 1800 / w
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _has_urdu(text: str) -> bool:
    return bool(_URDU_PATTERN.search(text))


def _ocr_image(img: Image.Image, hint_urdu: bool = False) -> str:
    """Run Tesseract on a PIL image."""
    img = _preprocess_image(img)
    # Try bilingual first if Urdu is expected
    lang = "urd+eng" if hint_urdu else "eng"
    try:
        text = pytesseract.image_to_string(img, lang=lang, config="--psm 6")
    except pytesseract.TesseractError:
        # Urdu tessdata not installed → fall back to eng only
        text = pytesseract.image_to_string(img, lang="eng", config="--psm 6")
    return text.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

class ExtractionResult:
    def __init__(self, text: str, method: str, pages: int, error: Optional[str] = None):
        self.text = text
        self.method = method          # 'pdf_text' | 'pdf_ocr' | 'image_ocr'
        self.pages = pages
        self.error = error
        self.has_urdu = _has_urdu(text)

    def to_dict(self):
        return {
            "text": self.text,
            "method": self.method,
            "pages": self.pages,
            "has_urdu": self.has_urdu,
            "char_count": len(self.text),
            "error": self.error,
        }


def extract_text(file_bytes: bytes, filename: str) -> ExtractionResult:
    """
    Main entry point. Returns ExtractionResult.

    Args:
        file_bytes: raw bytes of the uploaded file
        filename:   original filename (used to detect extension)
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _extract_from_pdf(file_bytes)
    elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"):
        return _extract_from_image(file_bytes)
    else:
        return ExtractionResult("", "unsupported", 0,
                                f"Unsupported file type: {ext}")


def _extract_from_pdf(file_bytes: bytes) -> ExtractionResult:
    """Try text-layer extraction first; fall back to OCR on each page."""
    pages_text = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                pages_text.append(t)
    except Exception as e:
        return ExtractionResult("", "pdf_text", 0, str(e))

    full_text = "\n\n".join(pages_text).strip()

    # If we got enough text, use it
    if len(full_text) >= _MIN_TEXT_LEN:
        return ExtractionResult(full_text, "pdf_text", len(pages_text))

    # Otherwise, OCR each page image
    ocr_pages = []
    try:
        images = convert_from_bytes(file_bytes, dpi=300)
        hint_urdu = _has_urdu(full_text)  # small hint from text layer
        for img in images:
            ocr_pages.append(_ocr_image(img, hint_urdu=hint_urdu))
        full_ocr = "\n\n".join(ocr_pages).strip()
        return ExtractionResult(full_ocr, "pdf_ocr", len(images))
    except Exception as e:
        return ExtractionResult(full_text, "pdf_text", len(pages_text),
                                f"OCR fallback failed: {e}")


def _extract_from_image(file_bytes: bytes) -> ExtractionResult:
    """OCR a single image file."""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        # First pass: try to detect if Urdu
        text_eng = _ocr_image(img, hint_urdu=False)
        if _has_urdu(text_eng):
            text = _ocr_image(img, hint_urdu=True)
        else:
            text = text_eng
        return ExtractionResult(text, "image_ocr", 1)
    except Exception as e:
        return ExtractionResult("", "image_ocr", 1, str(e))