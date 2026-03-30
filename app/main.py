from fastapi import FastAPI

from app.config import settings
from app.routers import authentication, consent, enrollment, service
from twilio_integration.webhook_handler import router as twilio_router

app = FastAPI(title="VeriVoice", version="0.1.0")

app.include_router(enrollment.router, prefix="/api/v1", tags=["enrollment"])
app.include_router(authentication.router, prefix="/api/v1", tags=["authentication"])
app.include_router(consent.router, prefix="/api/v1", tags=["consent"])
app.include_router(service.router, prefix="/api/v1", tags=["service"])
app.include_router(twilio_router, prefix="/twilio", tags=["twilio"])


@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}
