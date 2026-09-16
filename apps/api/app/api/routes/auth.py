from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

def _set_cookie(response: Response, token: str):
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.auth_token_expire_minutes * 60,
        path="/",
    )

@router.post("/signup", response_model=UserResponse)
def signup(
    user_in: UserCreate, 
    response: Response, 
    db: Session = Depends(get_db)
):
    user = db.scalar(select(User).where(User.email == user_in.email))
    if user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )
    
    user = User(
        email=user_in.email,
        name=user_in.name,
        password_hash=get_password_hash(user_in.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    token = create_access_token(subject=user.id)
    _set_cookie(response, token)
    
    return user

@router.post("/login", response_model=UserResponse)
def login(
    user_in: UserLogin, 
    response: Response, 
    db: Session = Depends(get_db)
):
    user = db.scalar(select(User).where(User.email == user_in.email))
    if not user or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    token = create_access_token(subject=user.id)
    _set_cookie(response, token)
    
    return user

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        key=settings.auth_cookie_name,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    return {"detail": "Logged out successfully"}

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
