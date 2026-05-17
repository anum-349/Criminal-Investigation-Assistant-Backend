from __future__ import annotations

import logging
import os
import re
from typing import Dict, Optional, List, Tuple

import pdfplumber
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logger = logging.getLogger("fir.ocr")

_URDU_PATTERN = re.compile(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_MIN_PDF_TEXT_LEN = 40   # below this we assume the PDF is scanned, not digital


# ─────────────────────────────────────────────────────────────────────────────
# OCR backend selection — try Paddle first, fall back to Tesseract
# ─────────────────────────────────────────────────────────────────────────────

_paddle_ocr = None       # Lazy-loaded singleton
_paddle_unavailable = False
_tesseract_lang = None


def _get_paddle_ocr():
    """Lazy-load PaddleOCR. Returns None if unavailable."""
    global _paddle_ocr, _paddle_unavailable
    if _paddle_unavailable:
        return None
    if _paddle_ocr is not None:
        return _paddle_ocr

    try:
        from paddleocr import PaddleOCR
        logger.info("Initialising PaddleOCR (Urdu/Arabic model)…")
        # use_angle_cls=True: detects rotated text (phones often scan tilted)
        # lang='arabic': covers Urdu, Arabic, Persian, Uyghur (all Nastaliq-family)
        # show_log=False: PaddleOCR is very verbose by default
        _paddle_ocr = PaddleOCR(
            use_angle_cls=True,
            lang='arabic',
            show_log=False,
            use_gpu=False,
        )
        logger.info("✓ PaddleOCR loaded")
        return _paddle_ocr
    except Exception as e:
        logger.warning("PaddleOCR unavailable (%s) — falling back to Tesseract", e)
        _paddle_unavailable = True
        return None


def _detect_tesseract_lang() -> str:
    """Cached Tesseract language detection. Used only when Paddle fails."""
    global _tesseract_lang
    if _tesseract_lang is not None:
        return _tesseract_lang
    try:
        import pytesseract
        langs = set(pytesseract.get_languages())
        if "urd" in langs and "eng" in langs:
            _tesseract_lang = "urd+eng"
        elif "eng" in langs:
            _tesseract_lang = "eng"
        else:
            _tesseract_lang = next(iter(langs - {"osd"}), "eng")
    except Exception as e:
        logger.warning("Tesseract unavailable: %s", e)
        _tesseract_lang = "eng"
    return _tesseract_lang


# ─────────────────────────────────────────────────────────────────────────────
# Image preprocessing — gentle, because PaddleOCR is more robust than Tesseract
# ─────────────────────────────────────────────────────────────────────────────

def _preprocess_image(img: Image.Image, for_paddle: bool = True) -> Image.Image:
    """Lightweight preprocessing.
    
    PaddleOCR handles raw images well — over-processing actually hurts it.
    We just auto-orient and ensure decent resolution. Tesseract gets more
    aggressive preprocessing because it's more sensitive to image quality.
    """
    # Auto-orient based on EXIF (phones rotate scans)
    img = ImageOps.exif_transpose(img)

    if for_paddle:
        # Paddle works on RGB and handles its own normalisation.
        # Just ensure decent resolution.
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if w < 1600:
            scale = 1600 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return img

    # Tesseract path: greyscale + contrast + sharpen + upscale (more aggressive)
    img = img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=2)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)
    w, h = img.size
    if w < 2400:
        scale = 2400 / w
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


# ─────────────────────────────────────────────────────────────────────────────
# OCR functions
# ─────────────────────────────────────────────────────────────────────────────

def _ocr_with_paddle(img: Image.Image) -> Tuple[str, float]:
    """Run PaddleOCR. Returns (text, confidence_proxy)."""
    import numpy as np
    
    ocr = _get_paddle_ocr()
    if ocr is None:
        return "", 0.0

    img = _preprocess_image(img, for_paddle=True)
    img_array = np.array(img)

    # PaddleOCR returns a list of pages; each page is a list of detections.
    # Each detection is [box_coords, (text, confidence)].
    try:
        results = ocr.ocr(img_array, cls=True)
    except Exception as e:
        logger.warning("PaddleOCR inference failed (%s) — falling back to Tesseract", e)
        return "", 0.0

    if not results or not results[0]:
        return "", 0.0

    # Sort detections top-to-bottom, left-to-right to preserve reading order.
    # For Urdu (RTL), we'd ideally sort right-to-left within rows, but for
    # mixed Urdu+English FIRs the regex doesn't care about strict reading order.
    detections = results[0]
    
    # Each detection: [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], (text, conf)]
    def _y_then_x(det):
        box = det[0]
        # Top-left y, top-left x
        return (box[0][1], box[0][0])
    
    detections.sort(key=_y_then_x)
    
    lines: List[str] = []
    confs: List[float] = []
    for det in detections:
        text = det[1][0]
        conf = det[1][1]
        if text and text.strip():
            lines.append(text.strip())
            confs.append(float(conf))
    
    full_text = "\n".join(lines)
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    return full_text, avg_conf


def _ocr_with_tesseract(img: Image.Image) -> str:
    """Tesseract fallback."""
    try:
        import pytesseract
    except ImportError:
        logger.error("Neither PaddleOCR nor pytesseract available — cannot OCR")
        return ""

    img = _preprocess_image(img, for_paddle=False)
    config = "--oem 1 --psm 6 -c preserve_interword_spaces=1"
    return pytesseract.image_to_string(
        img,
        lang=_detect_tesseract_lang(),
        config=config,
    )


def _ocr_image(img: Image.Image) -> Tuple[str, float, str]:
    """Try Paddle first, fall back to Tesseract.
    Returns (text, confidence, backend_used)."""
    text, conf = _ocr_with_paddle(img)
    if text and len(text.strip()) >= 30:
        return text, conf, "paddleocr"

    # Paddle returned little or nothing — try Tesseract
    text = _ocr_with_tesseract(img)
    return text, 0.6, "tesseract"  # rough confidence for Tesseract


# ─────────────────────────────────────────────────────────────────────────────
# Top-level dispatch
# ─────────────────────────────────────────────────────────────────────────────

def extract_text(file_path: str) -> Dict:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}:
        return _extract_image_file(file_path)
    raise ValueError(f"Unsupported file extension: {ext}")


def _extract_pdf(path: str) -> Dict:
    """Try pdfplumber first (digital text); fall back to OCR if sparse."""
    text_pages: List[str] = []
    num_pages = 0
    try:
        with pdfplumber.open(path) as pdf:
            num_pages = len(pdf.pages)
            for page in pdf.pages:
                text_pages.append(page.extract_text() or "")
    except Exception as e:
        logger.warning("pdfplumber failed (%s) — will OCR.", e)

    combined = "\n".join(text_pages).strip()
    if len(combined) >= _MIN_PDF_TEXT_LEN:
        return {
            "text":       combined,
            "method":     "pdfplumber",
            "pages":      num_pages,
            "confidence": 0.95,
        }

    return _ocr_pdf(path, num_pages or 1)


def _ocr_pdf(path: str, page_hint: int) -> Dict:
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        raise RuntimeError(
            "pdf2image is required for OCR of scanned PDFs. "
            "Install poppler-utils and `pip install pdf2image`."
        ) from e

    images = convert_from_path(path, dpi=300)
    texts: List[str] = []
    confs: List[float] = []
    backend = "paddleocr"
    for img in images:
        t, c, b = _ocr_image(img)
        texts.append(t)
        confs.append(c)
        backend = b  # last one wins; in practice all pages use the same backend

    return {
        "text":       "\n\n".join(texts).strip(),
        "method":     f"ocr_pdf:{backend}",
        "pages":      len(images),
        "confidence": sum(confs) / len(confs) if confs else 0.6,
    }


def _extract_image_file(path: str) -> Dict:
    img = Image.open(path)
    text, conf, backend = _ocr_image(img)
    return {
        "text":       text.strip(),
        "method":     f"ocr_image:{backend}",
        "pages":      1,
        "confidence": conf,
    }