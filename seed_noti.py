# Run this in a backend shell or one-off script
from db import SessionLocal
from services import notification_service as notif

db = SessionLocal()
notif.push(
    db,
    user_id=2,                                  # your logged-in user
    type="CASE_UPDATE",
    title="WS test notification",
    message="If you see this in DevTools, WS works.",
    severity_label="Normal",
)
db.commit()
db.close()