from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    badge_number = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    status = Column(String, default="active") 
    password = Column(String)
    contact_info = Column(String, nullable=True)
    address = Column(String, nullable=True)
    role = Column(String)  # "admin", "investigator"
    picture_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    investigator = relationship("Investigator", back_populates="user", uselist=False)
    admin = relationship("Admin", back_populates="user", uselist=False)
    user_roles = relationship("UserRole", back_populates="user")