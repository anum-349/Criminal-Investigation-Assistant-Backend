from sqlalchemy import ARRAY, Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class UserRole(Base):
    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True, index=True)
    role_name = Column(String)
    permissions = Column(ARRAY(String))  # List of permissions for this role
    user_id = Column(Integer, ForeignKey("users.id"))

    user = relationship("User", back_populates="user_roles")
