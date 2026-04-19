import jwt
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from axiom.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer()

# JWK Client for asymmetric verification (ES256, RS256)
_jwks_client = jwt.PyJWKClient(settings.supabase_jwks_url)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verifies a Supabase JWT using either symmetric secret or JWKS."""
    token = credentials.credentials
    
    try:
        # 1. First, peek at the header to see which algorithm is used
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        
        if alg == "HS256":
            # Symmetric verification
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False} 
            )
        else:
            # Asymmetric verification (ES256, RS256) using JWKS
            # This is the enterprise-grade way to handle modern identity providers
            try:
                signing_key = _jwks_client.get_signing_key_from_jwt(token)
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256", "ES256"],
                    options={"verify_aud": False}
                )
            except Exception as jwk_err:
                logger.error("JWKS verification failed: %s", jwk_err)
                # Fallback to local secret just in case it was a PEM string after all
                payload = jwt.decode(
                    token,
                    settings.supabase_jwt_secret,
                    algorithms=["RS256", "ES256"],
                    options={"verify_aud": False}
                )

        # 2. Extract user ID
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials (missing sub)",
            )
        return user_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError as e:
        logger.error("JWT validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )
    except Exception as e:
        logger.exception("Unexpected error during token verification")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Internal authentication error",
        )
