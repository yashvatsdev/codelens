"""
Google OpenID Connect OAuth client.

Uses Authlib's AsyncOAuth2Client for the authorization redirect and
authorization-code exchange, and joserfc (bundled with Authlib 1.3+) for
ID token validation.

This module is intentionally stateless: state and nonce are stored in
short-lived signed HTTP-only cookies by the auth route layer.
"""
from __future__ import annotations

import secrets
import time

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt as jose_jwt
from joserfc.jwk import KeySet

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

# Scopes we request from Google
GOOGLE_SCOPES = "openid email profile"

# In-process JWKS cache.  Google rotates keys infrequently so this is fine.
_google_jwks: KeySet | None = None


async def _get_google_jwks(jwks_uri: str) -> KeySet:
    """Fetch Google's public JWKS, cached for the lifetime of the process."""
    global _google_jwks
    if _google_jwks is None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(jwks_uri, timeout=10.0)
            resp.raise_for_status()
            _google_jwks = KeySet.import_key_set(resp.json())
    return _google_jwks


async def _get_google_discovery() -> dict:
    """Fetch Google's OIDC discovery document."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(GOOGLE_DISCOVERY_URL, timeout=10.0)
        resp.raise_for_status()
        return resp.json()


def generate_state() -> str:
    """Cryptographically secure random state parameter."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Cryptographically secure random nonce."""
    return secrets.token_urlsafe(32)


async def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
) -> str:
    """Build the Google authorization URL to redirect the user to."""
    discovery = await _get_google_discovery()
    authorization_endpoint: str = discovery["authorization_endpoint"]

    async with AsyncOAuth2Client(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=GOOGLE_SCOPES,
    ) as client:
        url, _ = client.create_authorization_url(
            authorization_endpoint,
            state=state,
            nonce=nonce,
        )
    return url


async def exchange_code_and_validate(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    authorization_response: str,
    expected_state: str,
    expected_nonce: str,
) -> dict:
    """
    Exchange the authorization code for tokens and validate the ID token.

    Returns a plain dict of validated ID token claims.

    Raises GoogleAuthError on any failure (exchange, validation, missing claims).
    Never surfaces raw token material in the exception message.
    """
    discovery = await _get_google_discovery()
    token_endpoint: str = discovery["token_endpoint"]
    jwks_uri: str = discovery["jwks_uri"]

    # Exchange the authorization code for tokens
    async with AsyncOAuth2Client(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=GOOGLE_SCOPES,
        state=expected_state,
    ) as client:
        try:
            token = await client.fetch_token(
                token_endpoint,
                authorization_response=authorization_response,
                grant_type="authorization_code",
            )
        except Exception as exc:
            raise GoogleAuthError("Token exchange with Google failed.") from exc

    id_token_str = token.get("id_token")
    if not id_token_str:
        raise GoogleAuthError("Google did not return an ID token.")

    # Validate the ID token with joserfc
    jwks = await _get_google_jwks(jwks_uri)
    try:
        token_obj = jose_jwt.decode(id_token_str, jwks)
        claims = token_obj.claims
    except Exception as exc:
        raise GoogleAuthError("Google ID token could not be decoded.") from exc

    # Manual claim validation (joserfc doesn't auto-validate OIDC claims)
    now = int(time.time())

    # Issuer
    iss = claims.get("iss", "")
    if iss not in _GOOGLE_ISSUERS:
        raise GoogleAuthError("Google ID token issuer is invalid.")

    # Audience
    aud = claims.get("aud")
    if isinstance(aud, list):
        if client_id not in aud:
            raise GoogleAuthError("Google ID token audience mismatch.")
    elif aud != client_id:
        raise GoogleAuthError("Google ID token audience mismatch.")

    # Expiry
    if claims.get("exp", 0) < now:
        raise GoogleAuthError("Google ID token has expired.")

    # Nonce
    if claims.get("nonce") != expected_nonce:
        raise GoogleAuthError("Google ID token nonce mismatch.")

    return claims


class GoogleAuthError(Exception):
    """Raised when any step of the Google OAuth flow fails."""

