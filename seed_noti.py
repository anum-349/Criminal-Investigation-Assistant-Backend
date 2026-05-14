from db import SessionLocal
from services.notification_service import push

db = SessionLocal()
push(db,
    user_id       = 2,           # ← your user id
    type          = "CASE_UPDATE",
    title         = "Test notification",
    message       = "This is a test to trigger the sound",
    severity_label= "High",
)
db.commit()
db.close()
print("Done — wait up to 30s or reopen the panel")