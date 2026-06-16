from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.config.db import get_session
from app.auth.auth import decode_access_token
from app.schemas.authschema import AuthUser

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> AuthUser:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await session.execute(
        text("""
            SELECT u.id, u.email, u.full_name, u.role, u.is_verified, u.is_active, u.created_at, u.last_login,
                   sa.profile_picture
            FROM users u
            LEFT JOIN social_accounts sa ON sa.user_id = u.id AND sa.provider = 'google'
            WHERE u.id = :id AND u.is_active = TRUE AND u.is_verified = TRUE
        """),
        {"id": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not verified",
        )

    return AuthUser(
        id=row[0],
        email=row[1],
        full_name=row[2],
        role=row[3],
        is_verified=row[4],
        is_active=row[5],
        created_at=row[6],
        last_login=row[7],
        picture=row[8],
    )


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def require_admin_or_manager(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or Manager access required",
        )
    return user
