import os
from typing import Optional
import bcrypt
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-to-a-long-random-string")
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="auth-salt")


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    pwd_bytes = password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    try:
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception:
        return False


async def create_user(db: AsyncSession, name: str, email: str, password: str) -> User:
    hashed_password = hash_password(password)
    user = User(
        name=name,
        email=email.lower().strip(),
        hashed_password=hashed_password
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    email_clean = email.lower().strip()
    stmt = select(User).where(User.email == email_clean)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_session_token(email: str) -> str:
    return serializer.dumps(email)


def decode_session_token(token: str, max_age: int = 86400) -> Optional[str]:
    try:
        return serializer.loads(token, max_age=max_age)
    except Exception:
        return None
