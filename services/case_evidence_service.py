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


UPLOADS_ROOT = os.getenv("UPLOADS_DIR", "uploads")
UPLOADS_URL_PREFIX = os.getenv("UPLOADS_URL_PREFIX", "/uploads")
MAX_PHOTO_BYTES = int(os.getenv("MAX_PHOTO_BYTES", str(5 * 1024 * 1024)))  # 5 MB

STATUS_ANALYZED = "Analyzed"
STATUS_PENDING  = "Pending Analysis"
STATUS_OPTIONS  = [STATUS_ANALYZED, STATUS_PENDING]

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
    """Until a real status column exists, treat 'has hash OR has photos' as analyzed."""
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
        fileName=e.file_name,
        fileMime=e.file_mime,
    )


def _public_url(file_path: Optional[str]) -> str:
    """Convert an on-disk path to a public URL.

    Three cases:
      1. Already a full URL (http:// or https://) → return as-is.
      2. Starts with the UPLOADS_URL_PREFIX (already a public URL string,
         e.g. '/uploads/foo.jpg' from a previous response) → return as-is.
      3. On-disk filesystem path → resolve relative to UPLOADS_ROOT and
         prepend the URL prefix.
    """
    if not file_path:
        return ""
    if file_path.startswith(("http://", "https://")):
        return file_path
    prefix_with_slash = UPLOADS_URL_PREFIX.rstrip("/") + "/"
    if file_path.startswith(prefix_with_slash) or file_path == UPLOADS_URL_PREFIX:
        return file_path
    # On-disk path. Try to make it relative to UPLOADS_ROOT.
    try:
        abs_root = os.path.abspath(UPLOADS_ROOT)
        abs_file = os.path.abspath(file_path)
        rel = os.path.relpath(abs_file, abs_root)
        # If file isn't under UPLOADS_ROOT, relpath returns a path with '..'
        if rel.startswith(".."):
            return file_path  # last-ditch fallback
        rel = rel.replace(os.sep, "/")
        return f"{UPLOADS_URL_PREFIX.rstrip('/')}/{rel}"
    except Exception:
        return file_path


def _format_officer_name(user: User) -> str:
    rank = ""
    if user.investigator and user.investigator.rank:
        rank = f"{user.investigator.rank}. "
    return f"{rank}{user.username}"


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
):
    """Triple-write helper for every mutation on the Evidence tab."""
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
    """Server-side filter + paginate. Same field names the JSX uses."""
    case = _resolve_case(db, user=user, case_id=case_id)

    q = (
        db.query(Evidence)
        .filter(Evidence.case_id_fk == case.id)
        .options(
            joinedload(Evidence.type),
            joinedload(Evidence.photos),
        )
    )

    # Free text — match against external ID, type label, collected_by
    s = (search or "").strip()
    if s:
        like = f"%{s}%"
        q = q.outerjoin(Evidence.type).filter(
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

    apply_status_post = False
    sf = (status_filter or "all").lower()
    if sf == "analyzed":
        apply_status_post = True
    elif sf in ("pending", "pending analysis"):
        apply_status_post = True

    rows = q.order_by(desc(Evidence.created_at)).distinct().all()

    if apply_status_post:
        if sf == "analyzed":
            rows = [r for r in rows if _derive_status(r) == STATUS_ANALYZED]
        else:
            rows = [r for r in rows if _derive_status(r) == STATUS_PENDING]

    total = len(rows)
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = rows[start:end]

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
            # Synthesise a marker hash so _derive_status returns Analyzed
            e.sha256_hash = secrets.token_hex(32)
            changes.append("status → Analyzed")
        elif body.status == STATUS_PENDING and e.sha256_hash:
            e.sha256_hash = None
            changes.append("status → Pending Analysis")

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
            db.rollback(); raise
        except Exception as ex:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Update failed: {ex}")

    db.refresh(e)
    return _row_from_evidence(e)


_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+\-.]+);base64,(?P<body>.+)$")


def _decode_data_url(data_url: str) -> Tuple[bytes, str]:
    """Returns (raw_bytes, mime). Raises HTTPException on bad input."""
    if not data_url:
        raise HTTPException(status_code=400, detail="Empty photo payload")
    m = _DATA_URL_RE.match(data_url)
    if not m:
        raise HTTPException(status_code=400, detail="Photo must be a base64 data URL")
    mime = m.group("mime")
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail=f"Unsupported mime: {mime}")
    try:
        raw = base64.b64decode(m.group("body"), validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Photo body is not valid base64")
    if len(raw) > MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Photo exceeds {MAX_PHOTO_BYTES // (1024 * 1024)} MB limit",
        )
    return raw, mime


def _ext_for_mime(mime: str, fallback_name: Optional[str]) -> str:
    table = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
        "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    }
    if mime in table:
        return table[mime]
    if fallback_name and "." in fallback_name:
        return "." + fallback_name.rsplit(".", 1)[-1].lower()
    return ".bin"


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

    raw, mime = _decode_data_url(body.dataUrl)

    folder = os.path.join(UPLOADS_ROOT, "evidence", case_id, evidence_id)
    os.makedirs(folder, exist_ok=True)
    ext = _ext_for_mime(mime, body.fileName)
    fname = f"{secrets.token_hex(8)}{ext}"
    abs_path = os.path.join(folder, fname)
    with open(abs_path, "wb") as f:
        f.write(raw)

    photo = EvidencePhoto(
        evidence_id=e.id,
        file_path=abs_path,
        file_name=body.fileName or fname,
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

    if file_path and not file_path.startswith(("http://", "https://")):
        prefix_with_slash = UPLOADS_URL_PREFIX.rstrip("/") + "/"
        looks_like_public_url = (
            file_path.startswith(prefix_with_slash)
            and not os.path.exists(file_path)
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