# VeriVoice

Privacy-preserving voice authentication and audio consent system for inclusive digital public services in East Africa.

VeriVoice augments existing national ID systems (Kenya's Huduma Namba, Uganda's Ndaga Muntu) with a voice biometric layer, enabling citizens — including those with low literacy — to authenticate and give informed consent using only their voice over a basic phone call.

> **Status:** Prototype / hackathon demo

## How It Works

```
Enrollment:    5 voice samples -> ECAPA-TDNN (192-dim) -> Paillier HE encrypt -> store ciphertext
Authentication: voice + phrase -> biometric match (HE dot product) + Whisper ASR transcript check
Consent:        voice auth -> Ed25519 sign consent token -> store in DB
Service Access: verify consent token -> voice Q&A via Whisper ASR
```

**Key privacy property:** Raw audio is never stored. Only homomorphically encrypted embeddings are persisted. Plaintext vectors are zeroed from memory immediately after encryption.

## Quick Start

### Prerequisites

- Python 3.10+
- ~4 GB disk space (for Whisper large-v3 model, downloaded on first use)
- GPU optional (auto-detects CUDA, falls back to CPU)

### Setup

```bash
# Clone and enter the project
git clone <repo-url>
cd VeriVoice_proto

# Create virtual environment
python -m venv venv
venv\Scripts\activate           # Windows
# source venv/bin/activate      # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Create your .env file
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --port 8000
```

### Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

### Streamlit UI

```bash
# In a second terminal (with venv activated, backend running on port 8000)
streamlit run streamlit_app/app.py --server.port 8501
```

Open `http://localhost:8501` to access the demo UI with four pages: Enroll, Authenticate, Consent, and Service Access.

## API Endpoints

All audio endpoints accept `multipart/form-data` and return JSON.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/api/v1/enroll` | POST | Enroll citizen with 5 voice samples |
| `/api/v1/challenge` | GET | Get random challenge phrase + TTS audio |
| `/api/v1/authenticate` | POST | Dual-stage voice auth (biometric + phrase) |
| `/api/v1/consent` | POST | Record verbal consent, get Ed25519 signed token |
| `/api/v1/service-access` | POST | Voice-driven form Q&A |

### Twilio IVR Webhooks

| Endpoint | Description |
|---|---|
| `/twilio/voice/welcome` | Welcome + language selection |
| `/twilio/voice/enroll` | 5-phrase enrollment via `<Record>` |
| `/twilio/voice/authenticate` | Challenge phrase auth |
| `/twilio/voice/consent` | Verbal consent recording |
| `/twilio/voice/service` | Health insurance form Q&A |

Interactive API docs available at `http://localhost:8000/docs` (Swagger UI).

## Architecture

```
Interface Layer     FastAPI routers | Streamlit app | Twilio webhooks
                         |                |               |
Service Layer       AudioPreprocessor, EmbeddingService, EncryptionService,
                    MatchingService, TranscriptionService, TTSService,
                    ConsentService, ChallengeService
                         |
Data Layer          SQLAlchemy ORM + SQLite (CITIZEN, VOICE_TEMPLATE,
                    AUTH_EVENT, CONSENT_TOKEN)
                         |
Config              Pydantic BaseSettings loading from .env
```

### Authentication Flow (Dual-Gate)

Both stages must pass for access to be granted:

1. **Voice Biometric** — Live audio is preprocessed, embedded via ECAPA-TDNN, then matched against the stored Paillier-encrypted centroid using homomorphic scalar multiplication. The decrypted dot product is compared against `MATCH_THRESHOLD` (0.45).

2. **Phrase Liveness** — The same audio is transcribed by Whisper ASR and compared against the expected challenge phrase (normalized: lowercase, stripped punctuation, unidecoded).

### Audio Preprocessing Pipeline

8-stage sequential pipeline (16 kHz, mono, float32):

1. Load via librosa
2. Butterworth frequency filter (80 Hz highpass / GSM 300-3400 Hz bandpass)
3. DC-offset removal
4. Spectral subtraction (conditional on noise floor)
5. Pre-emphasis (alpha = 0.97)
6. Peak normalization (target 0.95)
7. Voice Activity Detection (librosa, top_db=25)
8. Zero-pad to minimum 1 second

## Tech Stack

| Component | Technology |
|---|---|
| API Framework | FastAPI + Uvicorn |
| Speaker Verification | SpeechBrain ECAPA-TDNN (192-dim embeddings) |
| Speech-to-Text | OpenAI Whisper (large-v3) |
| Text-to-Speech | gTTS |
| Homomorphic Encryption | python-paillier (2048-bit Paillier) |
| Digital Signatures | PyNaCl (Ed25519) |
| Database | SQLite + SQLAlchemy + Alembic |
| IVR / Telephony | Twilio Voice API |
| Demo UI | Streamlit |
| Audio Processing | librosa, scipy, torchaudio |

## Project Structure

```
VeriVoice_proto/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py             # Pydantic settings (.env)
│   ├── models/               # SQLAlchemy models (Citizen, VoiceTemplate, AuthEvent, ConsentToken)
│   ├── schemas/              # Pydantic request/response schemas
│   ├── routers/              # API route handlers (enroll, authenticate, consent, service)
│   ├── services/             # Business logic (audio, embedding, encryption, matching, ASR, TTS)
│   ├── db/                   # Database engine, session, CRUD
│   └── utils/                # Audio helpers, key management
├── streamlit_app/app.py      # Streamlit demo UI
├── twilio_integration/       # Twilio IVR webhooks + call flow state machine
├── migrations/               # Alembic database migrations
├── tests/                    # pytest test suite
├── requirements.txt
├── alembic.ini
├── .env.example
└── CLAUDE.md                 # AI assistant context
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Individual test suites
pytest tests/test_db.py -v              # Database CRUD
pytest tests/test_preprocessor.py -v    # Audio pipeline
pytest tests/test_embedding.py -v       # ECAPA-TDNN (downloads model on first run)
pytest tests/test_encryption.py -v      # Paillier HE roundtrip
pytest tests/test_matching.py -v        # HE dot product scoring
pytest tests/test_transcription.py -v   # Whisper ASR
pytest tests/test_tts.py -v             # gTTS synthesis
pytest tests/test_challenge.py -v       # Phrase generation + matching
pytest tests/test_consent.py -v         # Ed25519 signing
pytest tests/test_schemas.py -v         # Pydantic validation
pytest tests/test_enrollment_api.py -v  # Enrollment endpoint
pytest tests/test_auth_api.py -v        # Authentication endpoint
pytest tests/test_twilio.py -v          # IVR TwiML responses
pytest tests/test_e2e.py -v -s          # Full end-to-end flow
```

## Configuration

All settings are loaded from `.env` via Pydantic `BaseSettings`. See `.env.example` for all available variables.

Key parameters:

| Parameter | Default | Description |
|---|---|---|
| `SAMPLE_RATE` | 16000 | Audio sample rate (Hz) |
| `MATCH_THRESHOLD` | 0.45 | Cosine similarity threshold for voice match |
| `PAILLIER_BITS` | 2048 | Paillier key size |
| `WHISPER_MODEL` | large-v3 | Whisper ASR model |
| `ECAPA_SOURCE` | speechbrain/spkrec-ecapa-voxceleb | Speaker embedding model |
| `ENROLLMENT_PHRASES` | 5 | Number of voice samples for enrollment |

## Twilio Setup (IVR)

To test the phone-based IVR flow:

1. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER` in `.env`
2. Expose your local server via [ngrok](https://ngrok.com/): `ngrok http 8000`
3. Set your Twilio phone number's Voice webhook to `https://<ngrok-url>/twilio/voice/welcome`

## Security

- **No raw audio storage** — only Paillier HE-encrypted 192-dim embeddings
- **Memory safety** — plaintext centroids and live embeddings zeroed after use
- **Paillier HE** — voice matching computed on encrypted data (2048-bit keys)
- **Ed25519** — consent tokens cryptographically signed for integrity and non-repudiation
- **Twilio request validation** — webhook endpoints verify `X-Twilio-Signature` when auth token is configured

## License

This project was developed as a hackathon prototype for the Digital Public Goods Alliance challenge.
