import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from app.core.oauth import exchange_code_and_validate, GoogleAuthError

@pytest.mark.anyio
async def test_invalid_nonce_rejected():
    with patch('app.core.oauth._get_google_discovery', new_callable=AsyncMock) as mock_discovery, \
         patch('app.core.oauth.AsyncOAuth2Client') as mock_client_cls, \
         patch('app.core.oauth._get_google_jwks', new_callable=AsyncMock) as mock_jwks, \
         patch('app.core.oauth.jose_jwt.decode') as mock_decode:
        
        mock_discovery.return_value = {
            "token_endpoint": "http://fake/token",
            "jwks_uri": "http://fake/jwks"
        }
        
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.fetch_token = AsyncMock(return_value={"id_token": "fake-jwt-string"})
        
        mock_token_obj = MagicMock()
        mock_token_obj.claims = {
            "iss": "https://accounts.google.com",
            "aud": "client_id",
            "exp": int(time.time()) + 3600,
            "nonce": "wrong-nonce"
        }
        mock_decode.return_value = mock_token_obj
        
        with pytest.raises(GoogleAuthError, match="nonce mismatch"):
            await exchange_code_and_validate(
                client_id="client_id",
                client_secret="secret",
                redirect_uri="http://fake",
                authorization_response="http://fake",
                expected_state="state",
                expected_nonce="correct-nonce"
            )

@pytest.mark.anyio
async def test_invalid_issuer_rejected():
    with patch('app.core.oauth._get_google_discovery', new_callable=AsyncMock) as mock_discovery, \
         patch('app.core.oauth.AsyncOAuth2Client') as mock_client_cls, \
         patch('app.core.oauth._get_google_jwks', new_callable=AsyncMock) as mock_jwks, \
         patch('app.core.oauth.jose_jwt.decode') as mock_decode:
        
        mock_discovery.return_value = {
            "token_endpoint": "http://fake/token",
            "jwks_uri": "http://fake/jwks"
        }
        
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.fetch_token = AsyncMock(return_value={"id_token": "fake-jwt-string"})
        
        mock_token_obj = MagicMock()
        mock_token_obj.claims = {
            "iss": "https://evil.com",
            "aud": "client_id",
            "exp": int(time.time()) + 3600,
            "nonce": "correct-nonce"
        }
        mock_decode.return_value = mock_token_obj
        
        with pytest.raises(GoogleAuthError, match="issuer is invalid"):
            await exchange_code_and_validate(
                client_id="client_id",
                client_secret="secret",
                redirect_uri="http://fake",
                authorization_response="http://fake",
                expected_state="state",
                expected_nonce="correct-nonce"
            )

@pytest.mark.anyio
async def test_expired_token_rejected():
    with patch('app.core.oauth._get_google_discovery', new_callable=AsyncMock) as mock_discovery, \
         patch('app.core.oauth.AsyncOAuth2Client') as mock_client_cls, \
         patch('app.core.oauth._get_google_jwks', new_callable=AsyncMock) as mock_jwks, \
         patch('app.core.oauth.jose_jwt.decode') as mock_decode:
        
        mock_discovery.return_value = {
            "token_endpoint": "http://fake/token",
            "jwks_uri": "http://fake/jwks"
        }
        
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.fetch_token = AsyncMock(return_value={"id_token": "fake-jwt-string"})
        
        mock_token_obj = MagicMock()
        mock_token_obj.claims = {
            "iss": "https://accounts.google.com",
            "aud": "client_id",
            "exp": int(time.time()) - 3600,
            "nonce": "correct-nonce"
        }
        mock_decode.return_value = mock_token_obj
        
        with pytest.raises(GoogleAuthError, match="expired"):
            await exchange_code_and_validate(
                client_id="client_id",
                client_secret="secret",
                redirect_uri="http://fake",
                authorization_response="http://fake",
                expected_state="state",
                expected_nonce="correct-nonce"
            )

def test_duplicate_google_sub_raises_integrity_error():
    from sqlalchemy.exc import IntegrityError
    from app.db.database import SessionLocal
    from app.models.user import User
    
    db = SessionLocal()
    # Cleanup first
    db.query(User).filter_by(google_sub="dup-sub").delete()
    db.commit()

    u1 = User(email="u1@example.com", password_hash="placeholder", google_sub="dup-sub")
    u2 = User(email="u2@example.com", password_hash="placeholder", google_sub="dup-sub")
    db.add(u1)
    db.commit()
    
    db.add(u2)
    with pytest.raises(IntegrityError):
        db.commit()
    
    db.rollback()
    
    db.query(User).filter_by(google_sub="dup-sub").delete()
    db.commit()
    db.close()
