import os
from datetime import UTC, datetime, timedelta
from dotenv import load_dotenv
from jose import jwt

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET is not set. Add it to your .env file. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440 # 24 hours

def create_access_token(data: dict) -> str:
    """Encode a JWT with `exp` set to now+24h. Caller passes payload like
    {"id": user.id, "role": user.role}."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT. Raises JWTError on invalid/expired token —
    caller should catch and re-raise as HTTPException(401)."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])