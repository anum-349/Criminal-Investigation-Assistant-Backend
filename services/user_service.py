from dotenv import load_dotenv
from sqlalchemy.orm import Session
from models.investigator import Investigator
from models.user import User
from passlib.context import CryptContext
from auth.jwt import create_access_token
import os

load_dotenv() 

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def _generate_badge_number(db: Session, role: str) -> str:
    last_user = db.query(User).order_by(User.id.desc()).first()
    next_number = 1 if not last_user else last_user.id + 1

    while True:
        if role == "admin":
            badge_number = f"ADMIN{next_number:06d}"    
        else:
            badge_number = f"INV{next_number:06d}"
        exists = db.query(User).filter(User.badge_number == badge_number).first()
        if not exists:
            return badge_number
        next_number += 1

def register_user(db: Session, username, password, role, secret_code):
    if db.query(User).filter(User.username == username).first():
        raise Exception("Username already exists")
    
    if role == "admin" and secret_code != os.getenv("ADMIN_SECRET_CODE"):
        raise Exception("Invalid admin secret code")
    
    badge_number = _generate_badge_number(db, role)
    hashed_password = pwd_context.hash(password[:72])
    new_user = User(username=username, badge_number=badge_number, password=hashed_password, role=role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token({"id": new_user.id, "role": new_user.role})
    return {
        "id": new_user.id,
        "username": new_user.username,
        "badge_number": new_user.badge_number,
        "role": new_user.role,
        "access_token": token,
        "token_type": "bearer"
    }

def login_user(db: Session, badge_number, password, secret_code):
    db_user = db.query(User).filter(User.badge_number == badge_number).first()
    if not db_user or not pwd_context.verify(password, db_user.password):
        raise Exception("Invalid credentials")
    if db_user.status != "active":
        raise Exception("User account is not active")
    if db_user.role == "admin" and secret_code != os.getenv("ADMIN_SECRET_CODE"):
        raise Exception("Invalid admin secret code")
    
    token = create_access_token({"id": db_user.id, "role": db_user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": db_user.username,
        "role": db_user.role
    }

def update_user_profile(db: Session, user_id: int, data: dict):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise Exception("User not found")

    for key, value in data.items():
        setattr(user, key, value)
    
    db.commit()
    db.refresh(user)
    return user

def update_investigator_profile(db: Session, user_id: int, data: dict):
    inv = db.query(Investigator).filter(Investigator.id == user_id).first()
    if not inv:
        raise Exception("Investigator profile not found")
    
    for key, value in data.items():
        setattr(inv, key, value)
    
    db.commit()
    db.refresh(inv)
    return inv
