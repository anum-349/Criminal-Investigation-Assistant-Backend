import base64
from datetime import date, datetime
import os
import re
import secrets
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import Tuple
from sqlalchemy.orm import Session

from models import Case, Person, User

UPLOADS_ROOT       = os.getenv("UPLOADS_DIR", "uploads")
UPLOADS_URL_PREFIX = os.getenv("UPLOADS_URL_PREFIX", "/uploads")
MAX_PHOTO_BYTES    = int(os.getenv("MAX_PHOTO_BYTES", str(5 * 1024 * 1024)))  # 5 MB

MAX_FILE_BYTES    = int(os.getenv("MAX_FILE_BYTES",  str(100 * 1024 * 1024)))  # 100 MB

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w/+\-.]+);base64,(?P<body>.+)$", re.DOTALL
)
_MIME_TO_EXT = {
    "image/jpeg":      ".jpg",
    "image/jpg":       ".jpg",
    "image/png":       ".png",
    "image/gif":       ".gif",
    "image/webp":      ".webp",
    "image/bmp":       ".bmp",
    "image/svg+xml":   ".svg",

    "application/pdf":                                                        ".pdf",
    "application/msword":                                                     ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain":                                                             ".txt",
    "application/rtf":                                                        ".rtf",

    "video/mp4":       ".mp4",
    "video/webm":      ".webm",
    "video/ogg":       ".ogv",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",

    "application/zip":              ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed":  ".7z",
    "application/gzip":             ".gz",
    "application/x-tar":            ".tar",
}


def _decode_data_url(data_url: str, *, image_only: bool = False) -> Tuple[bytes, str]:
    """
    Decode a base-64 data URL into (raw_bytes, mime_string).

    When image_only=True (photo upload path) we restrict to image/* only.
    For general file uploads we accept any MIME type.
    """
    if not data_url:
        raise HTTPException(status_code=400, detail="Empty file payload")
    m = _DATA_URL_RE.match(data_url)
    if not m:
        raise HTTPException(status_code=400, detail="File must be a base64 data URL")
    mime = m.group("mime")
    if image_only and not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Expected image, got: {mime}")
    try:
        raw = base64.b64decode(m.group("body"), validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="File body is not valid base64")
    limit = MAX_PHOTO_BYTES if image_only else MAX_FILE_BYTES
    if len(raw) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {limit // (1024 * 1024)} MB limit",
        )
    return raw, mime


def _ext_for_mime(mime: str, fallback_name: Optional[str]) -> str:
    if mime in _MIME_TO_EXT:
        return _MIME_TO_EXT[mime]
    if fallback_name and "." in fallback_name:
        return "." + fallback_name.rsplit(".", 1)[-1].lower()
    return ".bin"

def _public_url(file_path: Optional[str]) -> str:
    if not file_path:
        return ""
    if file_path.startswith(("http://", "https://")):
        return file_path
    prefix_with_slash = UPLOADS_URL_PREFIX.rstrip("/") + "/"
    if file_path.startswith(prefix_with_slash) or file_path == UPLOADS_URL_PREFIX:
        return file_path
    try:
        abs_root = os.path.abspath(UPLOADS_ROOT)
        abs_file = os.path.abspath(file_path)
        rel = os.path.relpath(abs_file, abs_root)
        if rel.startswith(".."):
            return file_path
        rel = rel.replace(os.sep, "/")
        return f"{UPLOADS_URL_PREFIX.rstrip('/')}/{rel}"
    except Exception:
        return file_path



def _resolve_person(
    db: Session,
    *,
    name: Optional[str],
    cnic: Optional[str],
    age: Optional[int] = None,
    gender: Optional[str] = None,
    contact: Optional[str] = None,
    address: Optional[str] = None,
) -> Person:
    """
    Find an existing Person by CNIC, otherwise create one. CNIC is the
    natural key (it's unique on the table), so we never duplicate someone
    just because they're added to a second case.
    """
    if cnic:
        existing = db.query(Person).filter(Person.cnic == cnic).first()
        if existing:
            # Update fields that the new entry has and the old row doesn't.
            if name and not existing.full_name:    existing.full_name = name
            if age and not existing.age:           existing.age = age
            if gender and not existing.gender:     existing.gender = gender
            if contact and not existing.contact:   existing.contact = contact
            if address and not existing.address:   existing.address = address
            return existing

    person = Person(
        full_name=name or None,
        cnic=cnic or None,
        age=age,
        gender=gender,
        contact=contact,
        address=address,
        is_unknown=not bool(name),
    )
    db.add(person)
    db.flush()      # we need person.id immediately
    return person

def _resolve_case(db: Session, *, user: User, case_id: str) -> Case:
    case = (
        db.query(Case)
        .filter(Case.case_id == case_id, Case.is_deleted == False)  # noqa: E712
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    if user.role != "admin" and case.assigned_investigator_id != user.id:
        raise HTTPException(status_code=403, detail="You don't have access to this case")
    return case

def _format_officer_name(user: User) -> str:
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"

def _parse_ymd(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
    
def _ymd(d) -> str:
    if not d:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return ""