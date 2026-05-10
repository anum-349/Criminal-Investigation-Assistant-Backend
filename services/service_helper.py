from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Case, Person, User

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