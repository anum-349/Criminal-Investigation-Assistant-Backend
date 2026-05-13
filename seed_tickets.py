# migrations/seed_ticket_statuses.py  (run once)
from models import TicketStatus
from db import SessionLocal

def seed():
    db = SessionLocal()
    rows = [
        TicketStatus(code="OPEN",        label="Open",        sort_order=1),
        TicketStatus(code="IN_PROGRESS", label="In Progress", sort_order=2),
        TicketStatus(code="RESOLVED",    label="Resolved",    sort_order=3),
        TicketStatus(code="CLOSED",      label="Closed",      sort_order=4),
    ]
    for r in rows:
        if not db.query(TicketStatus).filter_by(code=r.code).first():
            db.add(r)
    db.commit()
    db.close()

if __name__ == "__main__":
    seed()