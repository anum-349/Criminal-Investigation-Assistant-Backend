import os
import base64
import secrets
import re
from datetime import UTC, datetime
from typing import List, Optional, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, Request

from models import (
    User, Case,
    Evidence, EvidenceType, EvidencePhoto,
    Activity, Severity,
    TimelineEvent, TimelineEventType,
)
from services import audit_service as audit
from schemas.case_evidence_schema import (
    CaseEvidenceRow, CaseEvidenceList, EvidencePhotoOut,
    UpdateEvidenceRequest, PhotoUploadRequest,
    PhotoUploadResult, PhotoDeleteResult,
)
from services.service_helper import _format_officer_name, _resolve_case


UPLOADS_ROOT      = os.getenv("UPLOADS_DIR", "uploads")
UPLOADS_URL_PREFIX = os.getenv("UPLOADS_URL_PREFIX", "/uploads")
MAX_FILE_BYTES    = int(os.getenv("MAX_FILE_BYTES",  str(100 * 1024 * 1024)))  # 100 MB
MAX_PHOTO_BYTES   = int(os.getenv("MAX_PHOTO_BYTES", str(5   * 1024 * 1024)))  #   5 MB

STATUS_ANALYZED = "Analyzed"
STATUS_PENDING  = "Pending Analysis"
STATUS_OPTIONS  = [STATUS_ANALYZED, STATUS_PENDING]

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

def _resolve_evidence(db: Session, *, case: Case, evidence_id: str) -> Evidence:
    e = (
        db.query(Evidence)
        .filter(Evidence.case_id_fk == case.id, Evidence.evidence_id == evidence_id)
        .options(joinedload(Evidence.type), joinedload(Evidence.photos))
        .first()
    )
    if not e:
        raise HTTPException(status_code=404, detail=f"Evidence '{evidence_id}' not found")
    return e


def _derive_status(e: Evidence) -> str:
    if e.sha256_hash:
        return STATUS_ANALYZED
    if e.photos and len(e.photos) > 0:
        return STATUS_ANALYZED
    return STATUS_PENDING


def _photo_to_out(p: EvidencePhoto) -> EvidencePhotoOut:
    return EvidencePhotoOut(
        id=p.id,
        url=_public_url(p.file_path),
        file_name=p.file_name,
        caption=p.caption,
    )


def _row_from_evidence(e: Evidence) -> CaseEvidenceRow:
    return CaseEvidenceRow(
        id=e.evidence_id,
        type=e.type.label if e.type else "—",
        description=e.description,
        date=e.date_collected.strftime("%Y-%m-%d") if e.date_collected else "",
        collectedBy=e.collected_by,
        status=_derive_status(e),
        photos=[_photo_to_out(p) for p in (e.photos or [])],

        fileName=_public_url(e.file_name) if e.file_name else None,
        fileMime=e.file_mime,
    )


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


def _evidence_type_id(db: Session, label: Optional[str]) -> Optional[int]:
    if not label:
        return None
    row = db.query(EvidenceType).filter(EvidenceType.label == label).first()
    return row.id if row else None


def _timeline_event_type_id(db: Session, code: str) -> Optional[int]:
    row = db.query(TimelineEventType).filter(TimelineEventType.code == code).first()
    return row.id if row else None


def _severity_id(db: Session, label: str) -> Optional[int]:
    row = db.query(Severity).filter(Severity.label == label).first()
    return row.id if row else None


def _log_evidence_action(
    db: Session, *,
    case: Case, user: User, request: Optional[Request],
    title: str, description: str,
    audit_target_id: str,
) -> None:
    now = datetime.now(UTC)
    db.add(TimelineEvent(
        case_id_fk=case.id,
        event_id=f"EVT-{int(now.timestamp() * 1000):X}-{secrets.token_hex(2).upper()}",
        event_source="SYSTEM",
        event_type_id=_timeline_event_type_id(db, "EVIDENCE_ADDED"),
        title=title,
        description=description,
        officer_name=_format_officer_name(user),
        severity_id=_severity_id(db, "Normal"),
        event_date=now.date(),
        event_time=now.strftime("%H:%M"),
        created_at=now,
        editable=False,
    ))
    db.add(Activity(
        title=title, description=description, type="investigation",
        case_id=case.id, user_id=user.id, created_at=now,
    ))
    try:
        audit.log_event(
            db, user_id=user.id, action="UPDATE", module="Case Management",
            detail=f"{title} (case {case.case_id})",
            target_type="evidence", target_id=audit_target_id,
            request=request,
        )
    except Exception:
        pass


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


def _save_file_to_disk(
    raw: bytes,
    mime: str,
    *,
    case_id: str,
    evidence_id: str,
    original_name: Optional[str],
    sub_folder: str = "",          # e.g. "photos" to keep photos separate
) -> str:
    """
    Write bytes to uploads/evidence/<case_id>/<evidence_id>[/<sub_folder>]/
    Returns the absolute on-disk path.
    """
    parts = [UPLOADS_ROOT, "evidence", case_id, evidence_id]
    if sub_folder:
        parts.append(sub_folder)
    folder = os.path.join(*parts)
    os.makedirs(folder, exist_ok=True)
    ext = _ext_for_mime(mime, original_name)
    fname = f"{secrets.token_hex(8)}{ext}"
    abs_path = os.path.join(folder, fname)
    with open(abs_path, "wb") as f:
        f.write(raw)
    return abs_path


def list_evidences(
    db: Session,
    *,
    user: User,
    case_id: str,
    request: Optional[Request],
    search: str = "",
    date_filter: str = "",
    status_filter: str = "all",
    page: int = 1,
    page_size: int = 5,
) -> CaseEvidenceList:
    case = _resolve_case(db, user=user, case_id=case_id)

    q = (
        db.query(Evidence)
        .outerjoin(EvidenceType, Evidence.type_id == EvidenceType.id)
        .filter(Evidence.case_id_fk == case.id)
        .options(joinedload(Evidence.type), joinedload(Evidence.photos))
    )

    s = (search or "").strip()
    if s:
        like = f"%{s}%"
        q = q.filter(
            (Evidence.evidence_id.ilike(like))
            | (Evidence.collected_by.ilike(like))
            | (EvidenceType.label.ilike(like))
            | (Evidence.description.ilike(like))
        )

    if date_filter:
        try:
            d = datetime.strptime(date_filter, "%Y-%m-%d").date()
            q = q.filter(Evidence.date_collected == d)
        except ValueError:
            pass

    tf = (status_filter or "all").strip().lower()
    if tf != "all":
        q = q.filter(EvidenceType.code == tf.upper())

    rows = q.order_by(desc(Evidence.created_at)).distinct().all()
    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    page_rows = rows[start: start + page_size]

    type_options = [
        r.label for r in
        db.query(EvidenceType)
          .filter(EvidenceType.active == True)  # noqa: E712
          .order_by(EvidenceType.sort_order, EvidenceType.label)
          .all()
    ]

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=(
                f"Viewed evidence list (case={case_id}, search='{s}', "
                f"date={date_filter}, status={status_filter}, page={page}). "
                f"Returned {len(page_rows)}/{total}."
            ),
            target_type="evidence_list", target_id=case_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return CaseEvidenceList(
        items=[_row_from_evidence(e) for e in page_rows],
        total=total,
        page=page,
        page_size=page_size,
        type_options=type_options,
        status_options=STATUS_OPTIONS,
    )


def get_evidence(
    db: Session,
    *,
    user: User,
    case_id: str,
    evidence_id: str,
    request: Optional[Request],
) -> CaseEvidenceRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    e = _resolve_evidence(db, case=case, evidence_id=evidence_id)

    try:
        audit.log_event(
            db, user_id=user.id, action="VIEW", module="Case Management",
            detail=f"Viewed evidence '{evidence_id}' for case {case_id}.",
            target_type="evidence", target_id=evidence_id,
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()

    return _row_from_evidence(e)


def update_evidence(
    db: Session,
    *,
    user: User,
    case_id: str,
    evidence_id: str,
    body: UpdateEvidenceRequest,
    request: Optional[Request],
) -> CaseEvidenceRow:
    case = _resolve_case(db, user=user, case_id=case_id)
    e = _resolve_evidence(db, case=case, evidence_id=evidence_id)

    changes: List[str] = []
    written_file: Optional[str] = None   # track for rollback

    if body.type is not None:
        new_type_id = _evidence_type_id(db, body.type)
        if new_type_id and new_type_id != e.type_id:
            e.type_id = new_type_id
            changes.append(f"type → {body.type}")

    if body.description is not None and (body.description or None) != e.description:
        e.description = body.description or None
        changes.append("description updated")

    if body.dateCollected is not None:
        try:
            new_date = datetime.strptime(body.dateCollected, "%Y-%m-%d").date()
        except ValueError:
            new_date = None
        if new_date != e.date_collected:
            e.date_collected = new_date
            changes.append(f"date_collected → {body.dateCollected or '—'}")

    if body.collectedBy is not None and (body.collectedBy or None) != e.collected_by:
        e.collected_by = body.collectedBy or None
        changes.append(f"collected_by → {body.collectedBy or '—'}")

    if body.status is not None:
        if body.status == STATUS_ANALYZED and not e.sha256_hash and not e.photos:
            e.sha256_hash = secrets.token_hex(32)
            changes.append("status → Analyzed")
        elif body.status == STATUS_PENDING and e.sha256_hash:
            e.sha256_hash = None
            changes.append("status → Pending Analysis")

    if body.fileDataUrl:
        try:
            raw, mime = _decode_data_url(body.fileDataUrl, image_only=False)
        except HTTPException:
            raise  

        old_path = e.file_name
        if old_path and not old_path.startswith(("http://", "https://", "/")):
            try:
                os.remove(old_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        elif old_path and old_path.startswith("/uploads"):
            try:
                disk_path = os.path.join(
                    UPLOADS_ROOT,
                    old_path.lstrip("/").removeprefix(
                        UPLOADS_URL_PREFIX.lstrip("/") + "/"
                    ),
                )
                os.remove(disk_path)
            except Exception:
                pass

        abs_path = _save_file_to_disk(
            raw, mime,
            case_id=case_id,
            evidence_id=evidence_id,
            original_name=body.fileName,
        )
        written_file = abs_path

        # Store the on-disk path; _public_url converts it to a URL when serialising.
        e.file_name = abs_path
        e.file_mime = mime
        changes.append(f"file → {body.fileName or 'attachment'}")

    if changes:
        try:
            _log_evidence_action(
                db, case=case, user=user, request=request,
                title=f"Evidence Updated: {e.evidence_id}",
                description=", ".join(changes),
                audit_target_id=e.evidence_id,
            )
            db.commit()
        except HTTPException:
            db.rollback()
            if written_file:
                try: os.remove(written_file)
                except Exception: pass
            raise
        except Exception as ex:
            db.rollback()
            if written_file:
                try: os.remove(written_file)
                except Exception: pass
            raise HTTPException(status_code=500, detail=f"Update failed: {ex}")

    db.refresh(e)
    return _row_from_evidence(e)


def add_photo(
    db: Session,
    *,
    user: User,
    case_id: str,
    evidence_id: str,
    body: PhotoUploadRequest,
    request: Optional[Request],
) -> PhotoUploadResult:
    case = _resolve_case(db, user=user, case_id=case_id)
    e = _resolve_evidence(db, case=case, evidence_id=evidence_id)

    raw, mime = _decode_data_url(body.dataUrl, image_only=True)

    abs_path = _save_file_to_disk(
        raw, mime,
        case_id=case_id,
        evidence_id=evidence_id,
        original_name=body.fileName,
        sub_folder="photos",
    )

    photo = EvidencePhoto(
        evidence_id=e.id,
        file_path=abs_path,
        file_name=body.fileName or os.path.basename(abs_path),
        file_mime=mime,
        file_size=len(raw),
        caption=body.caption,
    )
    db.add(photo)

    try:
        db.flush()
        _log_evidence_action(
            db, case=case, user=user, request=request,
            title=f"Photo Added to Evidence {e.evidence_id}",
            description=f"Photo '{photo.file_name}' attached.",
            audit_target_id=e.evidence_id,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        try: os.remove(abs_path)
        except Exception: pass
        raise
    except Exception as ex:
        db.rollback()
        try: os.remove(abs_path)
        except Exception: pass
        raise HTTPException(status_code=500, detail=f"Photo upload failed: {ex}")

    db.refresh(e)
    return PhotoUploadResult(
        photo=_photo_to_out(photo),
        photos=[_photo_to_out(p) for p in e.photos],
    )


def delete_photo(
    db: Session,
    *,
    user: User,
    case_id: str,
    evidence_id: str,
    photo_id: int,
    request: Optional[Request],
) -> PhotoDeleteResult:
    case = _resolve_case(db, user=user, case_id=case_id)
    e = _resolve_evidence(db, case=case, evidence_id=evidence_id)

    photo = (
        db.query(EvidencePhoto)
        .filter(EvidencePhoto.id == photo_id, EvidencePhoto.evidence_id == e.id)
        .first()
    )
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    fname = photo.file_name or f"photo {photo.id}"
    file_path = photo.file_path
    db.delete(photo)

    try:
        _log_evidence_action(
            db, case=case, user=user, request=request,
            title=f"Photo Removed from Evidence {e.evidence_id}",
            description=f"Photo '{fname}' deleted.",
            audit_target_id=e.evidence_id,
        )
        db.commit()
    except HTTPException:
        db.rollback(); raise
    except Exception as ex:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Photo delete failed: {ex}")

    # disk cleanup.
    if file_path and not file_path.startswith(("http://", "https://")):
        prefix_with_slash = UPLOADS_URL_PREFIX.rstrip("/") + "/"
        looks_like_public_url = (
            file_path.startswith(prefix_with_slash) and not os.path.exists(file_path)
        )
        if not looks_like_public_url:
            try: os.remove(file_path)
            except FileNotFoundError: pass
            except Exception: pass

    db.refresh(e)
    return PhotoDeleteResult(
        deleted_id=photo_id,
        photos=[_photo_to_out(p) for p in e.photos],
    )