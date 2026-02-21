# models/investigator.py

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    admin_level = Column(String)

    user = relationship("User", back_populates="admin")