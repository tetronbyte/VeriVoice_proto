import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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

logger = logging.getLogger("verivoice.startup")

# Model readiness tracking — updated by _warmup_models(), read by /health
_model_status: dict[str, str] = {
    "ecapa_tdnn": "loading",
    "whisper": "loading",
    "w2v_bert_swahili": "loading",
}


def _warmup_models() -> None:
    """Load all ML models into memory so the first request is fast.

    Runs in a background thread so the server starts accepting health-check
    requests immediately while models load.
    """
    logger.info("Warming up ML models in background thread...")

    # 1. ECAPA-TDNN (speaker verification)
    try:
        from app.services.embedding_service import EmbeddingService

        EmbeddingService()._ensure_model()
        _model_status["ecapa_tdnn"] = "ready"
        logger.info("  ECAPA-TDNN loaded.")
    except Exception as e:
        _model_status["ecapa_tdnn"] = "failed"
        logger.warning("  ECAPA-TDNN warmup failed: %s", e)

    # 2. Whisper (English ASR)
    try:
        from app.services.transcription_service import TranscriptionService

        svc = TranscriptionService()
        svc._ensure_whisper()
        _model_status["whisper"] = "ready"
        logger.info("  Whisper %s loaded.", settings.WHISPER_MODEL)
    except Exception as e:
        _model_status["whisper"] = "failed"
        logger.warning("  Whisper warmup failed: %s", e)

    # 3. w2v-BERT Swahili ASR
    try:
        from app.services.transcription_service import TranscriptionService

        svc = TranscriptionService()
        loaded = svc._ensure_swahili()
        if loaded:
            _model_status["w2v_bert_swahili"] = "ready"
            logger.info("  w2v-BERT Swahili loaded.")
        else:
            _model_status["w2v_bert_swahili"] = "failed"
            logger.warning("  w2v-BERT Swahili failed to load (will fall back to Whisper).")
    except Exception as e:
        _model_status["w2v_bert_swahili"] = "failed"
        logger.warning("  w2v-BERT Swahili warmup failed: %s", e)

    logger.info("Model warmup complete.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle — warm up models on boot."""
    thread = threading.Thread(target=_warmup_models, name="model-warmup", daemon=True)
    thread.start()
    yield
    # Shutdown: nothing to clean up (models are singletons)


app = FastAPI(title="VeriVoice", version="0.1.0", lifespan=lifespan)

app.include_router(enrollment.router, prefix="/api/v1", tags=["enrollment"])
app.include_router(authentication.router, prefix="/api/v1", tags=["authentication"])
app.include_router(consent.router, prefix="/api/v1", tags=["consent"])
app.include_router(service.router, prefix="/api/v1", tags=["service"])
app.include_router(mosip.router, prefix="/api/v1", tags=["mosip"])
app.include_router(twilio_router, prefix="/twilio", tags=["twilio"])

# ── Serve gTTS audio files for Swahili IVR prompts ─────────────────────────
_tts_dir = Path(settings.TTS_AUDIO_DIR)
_tts_dir.mkdir(parents=True, exist_ok=True)
app.mount("/tts-audio", StaticFiles(directory=str(_tts_dir)), name="tts-audio")


@app.get("/health")
def health_check():
    from fastapi.responses import JSONResponse

    all_ready = all(v == "ready" for v in _model_status.values())
    return JSONResponse(
        content={
            "status": "ok" if all_ready else "warming_up",
            "version": app.version,
            "models": dict(_model_status),
        },
        status_code=200 if all_ready else 503,
    )
