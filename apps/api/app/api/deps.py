from typing import Annotated
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.user import User
from app.core.security import decode_session_user_id

def get_token_from_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.auth_cookie_name)

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = get_token_from_cookie(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    
    try:
        user_id = decode_session_user_id(token)
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
        
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user

CurrentUser = Annotated[User, Depends(get_current_user)]
