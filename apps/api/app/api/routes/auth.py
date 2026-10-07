from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.oauth import (
    GoogleAuthError,
    build_authorization_url,
    exchange_code_and_validate,
    generate_nonce,
    generate_state,
)
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Cookie names for transient OAuth state (short-lived, not the session)
# ---------------------------------------------------------------------------
_OAUTH_STATE_COOKIE = "codelens_oauth_state"
_OAUTH_NONCE_COOKIE = "codelens_oauth_nonce"


def _set_cookie(response: Response, token: str) -> None:
    """Set the long-lived CodeLens session cookie."""
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.auth_token_expire_minutes * 60,
        path="/",
    )


def _set_oauth_state_cookies(response: Response, state: str, nonce: str) -> None:
    """Set short-lived, HTTP-only cookies to carry OAuth state + nonce."""
    for name, value in ((_OAUTH_STATE_COOKIE, state), (_OAUTH_NONCE_COOKIE, nonce)):
        response.set_cookie(
            key=name,
            value=value,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            max_age=600,  # 10 minutes
            path="/",
        )


def _clear_oauth_state_cookies(response: Response) -> None:
    """Delete the transient OAuth state/nonce cookies."""
    for name in (_OAUTH_STATE_COOKIE, _OAUTH_NONCE_COOKIE):
        response.delete_cookie(
            key=name,
            secure=settings.auth_cookie_secure,
            samesite="lax",
            path="/",
        )


# ---------------------------------------------------------------------------
# Email / password endpoints (unchanged)
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=UserResponse)
def signup(
    user_in: UserCreate,
    response: Response,
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == user_in.email))
    # Explicit null-password guard: OAuth-only users cannot log in with a password
    if not user or not user.password_hash or not verify_password(user_in.password, user.password_hash):
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


# ---------------------------------------------------------------------------
# Google OAuth 2.0 / OpenID Connect endpoints
# ---------------------------------------------------------------------------

@router.get("/google/login")
async def google_login():
    """
    Redirect the browser to Google's OAuth consent page.

    Sets short-lived HTTP-only state+nonce cookies and returns a 302 redirect.
    Returns 503 if Google OAuth is not configured on the server.
    """
    if not settings.google_oauth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not available.",
        )

    state = generate_state()
    nonce = generate_nonce()

    authorization_url = await build_authorization_url(
        client_id=settings.google_client_id,
        redirect_uri=settings.google_redirect_uri,
        state=state,
        nonce=nonce,
    )

    redirect_response = RedirectResponse(
        url=authorization_url,
        status_code=status.HTTP_302_FOUND,
    )
    _set_oauth_state_cookies(redirect_response, state=state, nonce=nonce)
    return redirect_response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Handle Google's authorization callback.

    Validates state, exchanges the code, validates the ID token, then
    finds/creates/links the CodeLens user, issues the session cookie,
    and redirects to the frontend dashboard.
    """
    # --- 1. Handle provider-side denial ---
    if error:
        return _fail_redirect(reason="google_denied")

    if not code or not state:
        return _fail_redirect(reason="missing_params")

    # --- 2. Validate state cookie ---
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    expected_nonce = request.cookies.get(_OAUTH_NONCE_COOKIE)

    if not expected_state or not expected_nonce:
        return _fail_redirect(reason="missing_state")

    if not _constant_time_compare(state, expected_state):
        return _fail_redirect(reason="invalid_state")

    if not settings.google_oauth_configured:
        return _fail_redirect(reason="not_configured")

    # --- 3. Exchange code + validate ID token ---
    try:
        claims = await exchange_code_and_validate(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
            authorization_response=str(request.url),
            expected_state=expected_state,
            expected_nonce=expected_nonce,
        )
    except (GoogleAuthError, Exception):
        return _fail_redirect(reason="token_error")

    # --- 4. Extract and validate required claims ---
    google_sub: str | None = claims.get("sub")
    email: str | None = claims.get("email")
    email_verified: bool = claims.get("email_verified") is True
    name: str | None = claims.get("name")

    if not isinstance(google_sub, str) or not google_sub or len(google_sub) > 255:
        return _fail_redirect(reason="missing_sub")

    if not isinstance(email, str) or not email or not email_verified:
        return _fail_redirect(reason="unverified_email")

    # --- 5. User resolution (Cases A/B/C/D) ---
    try:
        user = _resolve_user(db, google_sub=google_sub, email=email, name=name)
    except IntegrityError:
        db.rollback()
        return _fail_redirect(reason="identity_conflict")
    if user is None:
        # Conflict: google_sub belongs to one user, email to another — refuse silently
        return _fail_redirect(reason="identity_conflict")

    # --- 6. Issue CodeLens session (same mechanism as email/password login) ---
    token = create_access_token(subject=user.id)

    final_redirect = RedirectResponse(
        url=f"{settings.frontend_url}/dashboard",
        status_code=status.HTTP_302_FOUND,
    )
    _set_cookie(final_redirect, token)
    _clear_oauth_state_cookies(final_redirect)
    return final_redirect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_user(
    db: Session,
    *,
    google_sub: str,
    email: str,
    name: str | None,
) -> User | None:
    """
    Resolve the CodeLens user for the given Google identity.

    CASE A: google_sub already exists -> return that user.
    CASE B: google_sub new, email exists (verified) -> link sub, return user.
    CASE C: both new -> create user with NULL password_hash.
    CONFLICT: google_sub -> user A, email -> user B -> return None (refused).
    """
    user_by_sub = db.scalar(select(User).where(User.google_sub == google_sub))
    user_by_email = db.scalar(select(User).where(User.email == email))

    if user_by_sub is not None:
        # CASE A: known Google identity.
        if user_by_email is not None and user_by_email.id != user_by_sub.id:
            return None  # Conflict
        return user_by_sub

    if user_by_email is not None:
        # CASE B: existing CodeLens account — link Google identity.
        if user_by_email.google_sub is not None:
            return None  # Never replace an already linked different identity.
        user_by_email.google_sub = google_sub
        if not user_by_email.name and name:
            user_by_email.name = name
        db.commit()
        db.refresh(user_by_email)
        return user_by_email

    # CASE C: brand-new user.
    new_user = User(
        email=email,
        name=name,
        password_hash=None,
        google_sub=google_sub,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


def _fail_redirect(reason: str) -> RedirectResponse:
    """
    Redirect to the frontend login page with a provider-neutral error code.
    Never exposes tokens, secrets, or internal details.
    """
    response = RedirectResponse(
        url=f"{settings.frontend_url}/login?error={reason}",
        status_code=status.HTTP_302_FOUND,
    )
    _clear_oauth_state_cookies(response)
    return response


def _constant_time_compare(val1: str, val2: str) -> bool:
    """Compare two strings in constant time to prevent timing attacks."""
    import hmac
    return hmac.compare_digest(val1.encode(), val2.encode())
