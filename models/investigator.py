# models/investigator.py

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Investigator(Base):
    __tablename__ = "investigators"

    id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    department = Column(String)
    rank = Column(String)
    shift = Column(String, nullable=True)
    specialization = Column(String, nullable=True)

    user = relationship("User", back_populates="investigator")