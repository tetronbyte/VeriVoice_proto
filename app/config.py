from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite:///./verivoice.db"

    # Paillier HE key file path
    PAILLIER_KEY_PATH: str = "./keys/paillier_keys.json"

    # Ed25519 signing key file path
    ED25519_KEY_PATH: str = "./keys/ed25519_key.bin"

    # ML Models
    ECAPA_SOURCE: str = "speechbrain/spkrec-ecapa-voxceleb"
    WHISPER_MODEL: str = "large-v3"
    SWAHILI_ASR_MODEL: str = "badrex/w2v-bert-2.0-swahili-as"

    # Audio preprocessing
    SAMPLE_RATE: int = 16000
    EMBEDDING_DIM: int = 192
    GSM_ENFORCE: bool = False
    VAD_TOP_DB: int = 25
    PRE_EMPHASIS_COEFF: float = 0.97
    PEAK_NORM_TARGET: float = 0.95
    NOISE_RMS_THRESHOLD: float = 0.05
    MIN_AUDIO_DURATION: float = 1.0

    # Encryption
    PAILLIER_BITS: int = 2048

    # Matching
    MATCH_THRESHOLD: float = 0.45
    TRANSCRIPT_MATCH_THRESHOLD: float = 0.75

    # Enrollment
    ENROLLMENT_PHRASES: int = 5

    # MOSIP e-Signet (OIDC)
    ESIGNET_BASE_URL: str = ""
    ESIGNET_UI_URL: str = ""
    ESIGNET_ISSUER: str = ""
    ESIGNET_CLIENT_ID: str = ""
    ESIGNET_CLIENT_SECRET: str = ""
    ESIGNET_REDIRECT_URI: str = "http://localhost:8000/api/v1/mosip/callback"
    ESIGNET_JWKS_URI: str = ""
    ESIGNET_SCOPES: str = "openid profile"
    ESIGNET_PRIVATE_KEY_PATH: str = ""

    # Public base URL for this server (ngrok URL in dev) — used in SMS links
    PUBLIC_BASE_URL: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # TTS audio serving (for Swahili IVR prompts via gTTS + <Play>)
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    TTS_AUDIO_DIR: str = "./tts_audio"

    # Streamlit (used by frontend, but matched in .env)
    BACKEND_URL: str = "http://localhost:8000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
