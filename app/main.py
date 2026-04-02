import logging

from fastapi import FastAPI

from app.config import settings
from app.routers import authentication, consent, enrollment, mosip, service
from twilio_integration.webhook_handler import router as twilio_router

# ── Configure VeriVoice logging ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-22s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
# Ensure all verivoice.* loggers propagate to root at INFO level
logging.getLogger("verivoice").setLevel(logging.INFO)

app = FastAPI(title="VeriVoice", version="0.1.0")

app.include_router(enrollment.router, prefix="/api/v1", tags=["enrollment"])
app.include_router(authentication.router, prefix="/api/v1", tags=["authentication"])
app.include_router(consent.router, prefix="/api/v1", tags=["consent"])
app.include_router(service.router, prefix="/api/v1", tags=["service"])
app.include_router(mosip.router, prefix="/api/v1", tags=["mosip"])
app.include_router(twilio_router, prefix="/twilio", tags=["twilio"])


@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version}
