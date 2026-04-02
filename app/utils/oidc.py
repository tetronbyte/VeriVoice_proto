from authlib.integrations.httpx_client import AsyncOAuth2Client
from app.config import settings


def create_esignet_client() -> AsyncOAuth2Client:
    """Create an Authlib OAuth2 client configured for MOSIP e-Signet."""
    return AsyncOAuth2Client(
        client_id=settings.ESIGNET_CLIENT_ID,
        client_secret=settings.ESIGNET_CLIENT_SECRET,
        redirect_uri=settings.ESIGNET_REDIRECT_URI,
        scope=settings.ESIGNET_SCOPES,
        token_endpoint=f"{settings.ESIGNET_BASE_URL}/v1/esignet/oauth/v2/token",
        authorization_endpoint=f"{settings.ESIGNET_BASE_URL}/v1/esignet/authorize",
    )
