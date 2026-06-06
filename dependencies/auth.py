"""
Centralized authentication & authorization module.

Contains JWT creation/decode, user resolution, and role-based access checks.
This is the single source of truth for all auth logic across the application.
"""

import os
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt, ExpiredSignatureError
from sqlalchemy.orm import Session

from db import get_db
from models import User

log = logging.getLogger(__name__)
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# JWT Configuration
# ─────────────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET is not set. Add it to your .env file. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours

# ─────────────────────────────────────────────────────────────────────────────
# JWT Token Operations
# ─────────────────────────────────────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    """
    Encode a JWT with `exp` set to now+24h.
    
    Args:
        data: Payload like {"id": user.id, "role": user.role}
    
    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a JWT. Raises JWTError on invalid/expired token.
    Caller should catch and re-raise as HTTPException(401).
    
    Args:
        token: JWT token string
    
    Returns:
        Decoded payload dictionary
    
    Raises:
        JWTError: If token is invalid or expired
    """
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Dependencies
# ─────────────────────────────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve the current user from the bearer token.
    
    Raises 401 if the token is missing, invalid, or expired.
    Raises 403 if the user is locked/inactive.
    
    Args:
        token: JWT token from Authorization header
        db: Database session
    
    Returns:
        Authenticated User object
    
    Raises:
        HTTPException: 401 (auth failed), 403 (account inactive/locked)
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exc

    user_id = payload.get("id")
    if not user_id:
        raise credentials_exc

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise credentials_exc

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {user.status}. Contact administrator.",
        )

    if user.locked_until and user.locked_until > datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is locked until {user.locked_until.isoformat()}.",
        )

    return user


# ─────────────────────────────────────────────────────────────────────────────
# Role-Based Access Control
# ─────────────────────────────────────────────────────────────────────────────

def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    Admin-only access check. Reuses get_current_user, then validates role.
    
    Args:
        current_user: Authenticated user from get_current_user
    
    Returns:
        Admin user object
    
    Raises:
        HTTPException: 403 if user is not admin
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


def get_current_investigator(current_user: User = Depends(get_current_user)) -> User:
    """
    Investigator-only access check. Admins also have access (admin is a superset role).
    
    Args:
        current_user: Authenticated user from get_current_user
    
    Returns:
        Investigator or admin user object
    
    Raises:
        HTTPException: 403 if user is not investigator or admin
    """
    if current_user.role not in ("investigator", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Investigator access required",
        )
    return current_user


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket JWT Resolution (for real-time notifications)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_user_from_token(token: Optional[str]) -> Optional[User]:
    """
    Decode JWT from query string token (used in WebSocket handshakes).
    Returns the User, or None on any failure.
    
    Accepts any of these claim names for the user ID, in priority order:
      • id        (what THIS app issues — see auth/jwt.py)
      • sub       (RFC 7519 standard)
      • user_id   (legacy fallback)
    
    Args:
        token: JWT token from WebSocket query string, or None
    
    Returns:
        User object if token is valid and user exists, None otherwise
    """
    if not token:
        log.debug("WS auth: no token in query string")
        return None
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        log.debug("WS auth: JWT decode failed: %s", e)
        return None
    
    # Order matters — this app uses "id", so check it first.
    user_id = (
        payload.get("id")
        or payload.get("sub")
        or payload.get("user_id")
    )
    if user_id is None:
        log.debug("WS auth: no user-id claim in token, claims=%s", list(payload.keys()))
        return None
    
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        log.debug("WS auth: non-int user-id claim: %r", user_id)
        return None
    
    from db import SessionLocal
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            log.debug("WS auth: user_id=%s not found in DB", user_id)
        return user
    finally:
        db.close()
