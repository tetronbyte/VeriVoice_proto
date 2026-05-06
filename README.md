# VeriVoice

Privacy-preserving voice authentication and audio consent system for inclusive digital public services in East Africa.

VeriVoice augments existing national ID systems (Kenya's Huduma Namba, Uganda's Ndaga Muntu) with a voice biometric layer, enabling citizens — including those with low literacy — to authenticate and give informed consent using only their voice over a basic phone call. Through MOSIP e-Signet integration, VeriVoice can cryptographically verify a citizen's identity against the national ID system before linking their voice biometric.

> **Status:** Prototype / hackathon demo

## How It Works

```
Identity:       MOSIP e-Signet OTP verification (DTMF over phone, no browser)
                -> verifies citizen via national ID before voice enrollment
Enrollment:     5 voice samples -> ECAPA-TDNN (192-dim) -> Paillier HE encrypt -> store ciphertext
                -> linked to verified mosip_individual_id
Authentication: voice + phrase -> biometric match (HE dot product) + Whisper ASR transcript check
Consent:        voice auth -> Ed25519 sign consent token -> store in DB
Service Access: pick 1 of 5 services via DTMF -> 3 questions + read-back
                -> yes persists SERVICE_FORM, no triggers per-question correction
```

**Key privacy property:** Raw audio is never stored. Only homomorphically encrypted embeddings are persisted. Plaintext vectors are zeroed from memory immediately after encryption.

---

## Prerequisites

- **Python 3.10+**
- **Redis** (for challenge phrase sessions, OIDC state, IVR verified-identity tokens)
- **~8 GB disk space** for ML model weights (Whisper ~3GB, w2v-BERT Swahili ~4.5GB, ECAPA-TDNN ~90MB)
- **GPU optional** (auto-detects CUDA, falls back to CPU)
- **ngrok** (for Twilio IVR — exposes local server to the internet)
- **Twilio account** with a Voice-enabled phone number (for IVR)
- **Java 11+** (only for MOSIP Mock MDS biometric device simulators, if testing browser-based eSignet)
- **Docker Desktop** (only for local eSignet stack, if testing identity verification)
- **Node.js 20.x/22.x** (only for Twilio Dev Phone browser-based testing)

---

## Quick Start (API Only)

The minimal setup to get the REST API running — no Twilio, no eSignet, no phone calls.

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

# Pre-download ML model weights (~8 GB, one-time)
python scripts/download_models.py

# Create your .env file
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start the API server (models auto-warm on boot)
uvicorn app.main:app --reload --port 8000
```

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

Interactive API docs: `http://localhost:8000/docs` (Swagger UI).

On startup, all 3 ML models (ECAPA-TDNN, Whisper large-v3, w2v-BERT Swahili) are loaded into memory in a background thread. The `/health` endpoint responds immediately; models are ready by the time the first real request arrives.

---

## Full Development Setup

This section covers the complete local setup for testing the phone-based IVR flow with optional MOSIP eSignet identity verification. Each component builds on the previous one.

### 1. Start Redis

Redis is required for challenge phrase sessions, OIDC state/nonce, and IVR verified-identity tokens.

```bash
# Option A: Docker (recommended)
docker run -d --name verivoice-redis -p 6379:6379 redis:7-alpine

# Option B: Local install
redis-server
```

Verify: `redis-cli ping` should return `PONG`.

### 2. Start the VeriVoice Backend

```bash
# Activate your virtual environment
venv\Scripts\activate           # Windows
# source venv/bin/activate      # macOS/Linux

# Pre-download ML models (one-time, ~8 GB)
python scripts/download_models.py

# Run database migrations (first time or after schema changes)
alembic upgrade head

# Start the FastAPI server (models auto-warm on boot)
uvicorn app.main:app --reload --port 8000
```

Verify: `curl http://localhost:8000/health`

### 3. Start ngrok (for Twilio)

Twilio needs a public URL to reach your local server for webhooks and to fetch gTTS audio files.

```bash
ngrok http 8000
```

Copy the `https://` forwarding URL (e.g., `https://abcd-1234.ngrok-free.app`).

Update `.env`:

```env
PUBLIC_BASE_URL=https://abcd-1234.ngrok-free.app
```

> **Important:** Restart the backend after changing `PUBLIC_BASE_URL` so gTTS audio URLs point to the correct ngrok address. The free ngrok URL changes every restart — you'll need to update both `.env` and the Twilio webhook URL each time.

### 4. Configure Twilio

1. Set your Twilio credentials in `.env`:

   ```env
   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_AUTH_TOKEN=your_auth_token_here
   TWILIO_PHONE_NUMBER=+1234567890
   ```

2. In the [Twilio Console](https://console.twilio.com/), go to **Phone Numbers** > your number > **Voice Configuration**:
   - **A call comes in:** Webhook
   - **URL:** `https://<your-ngrok-url>/twilio/voice/welcome`
   - **HTTP Method:** POST

3. Call your Twilio number — you should hear *"Welcome to VeriVoice. Press 1 for English. Press 2 for Swahili."*

When `TWILIO_AUTH_TOKEN` is set in `.env`, all incoming webhook requests are validated against Twilio's `X-Twilio-Signature` header. Leave it blank during initial testing to skip validation.

### 5. Start eSignet (Optional — for Identity Verification)

Required only if testing the IVR identity verification flow (Press 3 from the main menu) or the Streamlit MOSIP verification page.

#### Option A: Local Docker Stack

```bash
# Clone the eSignet repo (one-time)
git clone https://github.com/mosip/esignet.git
cd esignet/docker-compose

# Start the stack
docker compose --file docker-compose.yml up -d
```

This starts:

| Service | URL |
|---|---|
| eSignet UI | http://localhost:3000 |
| eSignet Backend | http://localhost:8088 |
| Mock Identity System | http://localhost:8082 |
| PostgreSQL | localhost:5455 |
| Redis (eSignet) | localhost:6379 |

Verify:

```bash
curl http://localhost:8088/v1/esignet/actuator/health
curl http://localhost:8082/v1/mock-identity-system/actuator/health
```

Update `.env` for local Docker:

```env
ESIGNET_BASE_URL=http://localhost:8088
ESIGNET_UI_URL=http://localhost:3000
ESIGNET_ISSUER=http://localhost:8088/v1/esignet
ESIGNET_CLIENT_ID=your_registered_client_id
ESIGNET_REDIRECT_URI=http://localhost:8000/api/v1/mosip/callback
ESIGNET_JWKS_URI=http://localhost:8088/v1/esignet/oauth/.well-known/jwks.json
ESIGNET_SCOPES=openid profile
ESIGNET_PRIVATE_KEY_PATH=./esignet_private_key.pem
```

> For RSA key generation, OIDC client registration, and mock user creation, see [`docs/esignet_local_setup.md`](docs/esignet_local_setup.md).

**Create test users** (pre-built profiles included):

```bash
bash scripts/esignet_test_users/create_all.sh
```

This creates 5 test users (Amina, Kwame, Fatuma, Joseph, Grace) in the mock-identity-system. Default OTP for all mock users: `111111`.

#### Option B: MOSIP Collab Environment

Use the hosted MOSIP collab sandbox instead of running Docker locally:

```env
ESIGNET_BASE_URL=https://esignet.collab.mosip.net
ESIGNET_JWKS_URI=https://esignet.collab.mosip.net/.well-known/jwks.json
```

### 6. Start Mock MDS (Optional — for Browser-Based eSignet)

Only needed if testing eSignet via a browser (the Streamlit UI path). The IVR path uses server-driven OTP and does **not** need Mock MDS.

```bash
# Terminal: Registration Mock MDS (port 4501)
cd MOSIP_eSignet/collab-mock-mds-reg/target
java -cp "classes;lib/*" io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Registration" \
  "mosip.mock.sbi.biometric.type=Biometric Device"

# Terminal: Auth Mock MDS (port 4502)
cd MOSIP_eSignet/collab-mock-mds-auth/target
java -cp "mock-mds-1.2.1-SNAPSHOT.jar;lib/*" io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Auth" \
  "mosip.mock.sbi.biometric.type=Biometric Device"
```

Requires Java 11+. Verify: `netstat -ano | findstr :4501`

### 7. Start the Streamlit UI (Optional)

```bash
# In a separate terminal (with venv activated, backend running)
streamlit run streamlit_app/app.py --server.port 8501
```

Open `http://localhost:8501` — five pages: Enroll, Authenticate, Consent, Service Access, and Verify Identity (MOSIP).

### 8. Twilio Dev Phone (Optional — Browser-Based IVR Testing)

Test IVR calls from your browser without a physical phone. The Dev Phone routes calls through Twilio's real infrastructure, so webhooks fire identically to a real call.

```bash
# Install (one-time)
npm install -g twilio-cli
twilio plugins:install @twilio-labs/plugin-dev-phone

# Log in to Twilio
twilio login

# Run the Dev Phone (opens browser UI)
twilio dev-phone
```

Make sure ngrok is running and the Twilio webhook URL is set before placing a call. See [`docs/dev_phone_setup&docs.md`](docs/dev_phone_setup&docs.md) for details.

### Startup Checklist

| # | Component | Command | Required? |
|---|---|---|---|
| 0 | Download ML models | `python scripts/download_models.py` | First time only (~8 GB) |
| 1 | Redis | `docker run -d -p 6379:6379 redis:7-alpine` | Always |
| 2 | VeriVoice Backend | `uvicorn app.main:app --reload --port 8000` | Always (auto-warms models) |
| 3 | ngrok | `ngrok http 8000` | For IVR |
| 4 | Twilio webhook | Configure in Twilio Console | For IVR |
| 5 | eSignet Docker | `docker compose up -d` | For identity verification |
| 6 | Mock MDS (Java) | See above | For browser-based eSignet only |
| 7 | Streamlit UI | `streamlit run streamlit_app/app.py --server.port 8501` | For web demo |
| 8 | Dev Phone | `twilio dev-phone` | For browser-based IVR testing |

### Port Reference

| Service | Port |
|---|---|
| VeriVoice Backend (FastAPI) | 8000 |
| Streamlit UI | 8501 |
| Redis | 6379 |
| eSignet Backend | 8088 |
| eSignet UI | 3000 |
| Mock Identity System | 8082 |
| Mock MDS | 4501-4600 |

---

## Environment Configuration

All settings are loaded from `.env` via Pydantic `BaseSettings`. See `.env.example` for the full template.

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./verivoice.db` | SQLite database path |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `BACKEND_URL` | `http://localhost:8000` | Backend URL (used by Streamlit) |

### ML Models

| Variable | Default | Description |
|---|---|---|
| `ECAPA_SOURCE` | `speechbrain/spkrec-ecapa-voxceleb` | Speaker verification model |
| `WHISPER_MODEL` | `large-v3` | English ASR model |
| `SWAHILI_ASR_MODEL` | `badrex/w2v-bert-2.0-swahili-asr` | Swahili ASR model |
| `MATCH_THRESHOLD` | `0.45` | Voice biometric cosine similarity threshold |
| `TRANSCRIPT_MATCH_THRESHOLD` | `0.75` | Phrase transcript word-similarity threshold |

### Twilio IVR

| Variable | Default | Description |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | *(empty)* | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | *(empty)* | Twilio Auth Token (enables webhook signature validation) |
| `TWILIO_PHONE_NUMBER` | *(empty)* | Twilio phone number with Voice capability |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Public URL for Twilio to fetch gTTS audio (set to ngrok URL) |
| `TTS_AUDIO_DIR` | `./tts_audio` | Directory for cached gTTS Swahili audio files |

### MOSIP eSignet (OIDC)

| Variable | Default | Description |
|---|---|---|
| `ESIGNET_BASE_URL` | *(empty)* | eSignet backend API URL |
| `ESIGNET_UI_URL` | *(empty)* | eSignet login UI URL |
| `ESIGNET_ISSUER` | *(empty)* | JWT `iss` claim value |
| `ESIGNET_CLIENT_ID` | *(empty)* | Registered OIDC client ID |
| `ESIGNET_REDIRECT_URI` | `http://localhost:8000/api/v1/mosip/callback` | OIDC callback URL |
| `ESIGNET_JWKS_URI` | *(empty)* | JWKS endpoint for JWT validation |
| `ESIGNET_SCOPES` | `openid profile` | OIDC scopes |
| `ESIGNET_PRIVATE_KEY_PATH` | *(empty)* | RSA private key PEM for `private_key_jwt` auth |

---

## API Endpoints

All audio endpoints accept `multipart/form-data` and return JSON.

### REST API

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/api/v1/enroll` | POST | Enroll citizen with 5 voice samples |
| `/api/v1/challenge` | GET | Get random challenge phrase + TTS audio |
| `/api/v1/authenticate` | POST | Dual-stage voice auth (biometric + phrase) |
| `/api/v1/consent` | POST | Record verbal consent, get Ed25519 signed token |
| `/api/v1/service-access` | POST | Voice-driven form Q&A (3 bilingual questions) |
| `/api/v1/service-access/summary` | POST | TTS read-back summary of form answers |
| `/api/v1/mosip/authorize` | GET | Initiate MOSIP e-Signet OIDC login (returns redirect URL) |
| `/api/v1/mosip/callback` | GET | e-Signet OIDC callback (exchanges code for verified identity) |
| `/api/v1/mosip/link` | POST | Link verified MOSIP identity to existing citizen |

### Twilio IVR Webhooks

| Endpoint | Description |
|---|---|
| `/twilio/voice/welcome` | Welcome + language selection (1=English, 2=Swahili) |
| `/twilio/voice/verify/start` | DTMF identity verification — prompt for national ID |
| `/twilio/voice/verify/nid` | Trigger eSignet OTP for entered national ID |
| `/twilio/voice/verify/otp` | Verify OTP via eSignet, redirect to enrollment |
| `/twilio/voice/enroll` | National ID input + 5 random-phrase enrollment via `<Record>` |
| `/twilio/voice/authenticate` | National ID input + challenge phrase + real auth pipeline (1 retry) |
| `/twilio/voice/consent` | Verbal consent recording (Yes/No + Ed25519 sign + persist) |
| `/twilio/voice/service/menu` | 5-service menu (pension/mpesa/aid/sim swap/telemedicine) via DTMF 1-5 |
| `/twilio/voice/service` | 3 questions + read-back for the chosen service |
| `/twilio/voice/service/confirm` | Yes/No: "yes" persists SERVICE_FORM + reference ID; "no" -> correction |
| `/twilio/voice/service/correct` | Asks "which question 1/2/3?", re-asks that single question |

**IVR identity verification (DTMF):** From the main menu, pressing **3** starts an eSignet-backed identity check using only the phone keypad. The IVR prompts for the national ID, calls eSignet's server-driven OAuth endpoints, and extracts a verified MOSIP `individual_id` from the signed id_token. On success the call proceeds directly into voice enrollment with `identity_verified=True`. No SMS, no browser — the whole flow happens on the call. See [`docs/ivr_verify_flow.md`](docs/ivr_verify_flow.md).

**Full call continuity:** After successful authentication, the IVR stays in the same call: consent -> service menu (DTMF 1-5) -> 3 questions -> TTS read-back -> Yes/No confirmation -> (optional per-question correction) -> persist + announce reference ID -> goodbye.

**IVR TTS strategy:** English prompts use Twilio's built-in `<Say voice="alice">` (zero latency). Swahili prompts use gTTS audio served via `<Play>` — Google TTS has proper Swahili pronunciation. Set `PUBLIC_BASE_URL` to your ngrok URL in `.env`.

**Recording controls:** All voice recordings use **# to stop** or **5-second silence auto-stop**. DTMF gathers allow 30 seconds for input.

For the complete IVR flow walkthrough, recording mechanics, state machine diagram, and troubleshooting, see [`docs/Twilio_IVR_Setup&Docs.md`](docs/Twilio_IVR_Setup&Docs.md).

---

## Architecture

```
Interface Layer     FastAPI routers | Streamlit app | Twilio webhooks
                         |                |               |
Service Layer       AudioPreprocessor, EmbeddingService, EncryptionService,
                    MatchingService, TranscriptionService, TTSService,
                    ConsentService, ChallengeService, MosipService
                         |
Data Layer          SQLAlchemy ORM + SQLite (CITIZEN, VOICE_TEMPLATE,
                    AUTH_EVENT, CONSENT_TOKEN, SERVICE_FORM)
                         |
Identity Layer      MOSIP e-Signet (OIDC) + Mock MDS (SBI biometric devices)
                         |
Config              Pydantic BaseSettings loading from .env
```

### Authentication Flow (Dual-Gate)

Both stages must pass for access to be granted:

1. **Voice Biometric** — Live audio is preprocessed, embedded via ECAPA-TDNN, then matched against the stored Paillier-encrypted centroid using homomorphic scalar multiplication. The decrypted dot product is compared against `MATCH_THRESHOLD` (0.45).

2. **Phrase Liveness** — The same audio is transcribed (Whisper for English, w2v-BERT for Swahili) and compared against the expected challenge phrase. Word-level similarity must meet `TRANSCRIPT_MATCH_THRESHOLD` (0.75).

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

---

## Tech Stack

| Component | Technology |
|---|---|
| API Framework | FastAPI + Uvicorn |
| Speaker Verification | SpeechBrain ECAPA-TDNN (192-dim embeddings) |
| Speech-to-Text (English) | OpenAI Whisper (large-v3) |
| Speech-to-Text (Swahili) | w2v-BERT 2.0 (`badrex/w2v-bert-2.0-swahili-asr`) |
| Text-to-Speech | gTTS |
| Homomorphic Encryption | python-paillier (2048-bit Paillier) |
| Digital Signatures | PyNaCl (Ed25519) |
| Database | SQLite + SQLAlchemy + Alembic |
| Identity Provider | MOSIP e-Signet (OpenID Connect) |
| Mock Biometric Devices | MOSIP Mock MDS (SBI protocol, Java 11+) |
| OIDC Client | authlib (OIDC flow) + PyJWT (PS256 JWT validation) |
| IVR / Telephony | Twilio Voice API |
| Session / State | Redis |
| Demo UI | Streamlit |
| Audio Processing | librosa, scipy, torchaudio |

---

## Project Structure

```
VeriVoice_proto/
├── app/
│   ├── main.py              # FastAPI entry point (lifespan model warmup)
│   ├── config.py            # Pydantic settings (.env)
│   ├── models/              # SQLAlchemy models (Citizen, VoiceTemplate, AuthEvent, ConsentToken, ServiceForm)
│   ├── schemas/             # Pydantic schemas (enrollment, auth, consent, mosip)
│   ├── routers/             # API routes (enroll, authenticate, consent, service, mosip)
│   ├── services/            # Business logic (audio, embedding, encryption, matching, ASR, TTS, consent, mosip)
│   ├── db/                  # Database engine, session, CRUD
│   └── utils/               # Audio helpers, key management, OIDC client
├── streamlit_app/app.py     # Streamlit demo UI (5 pages incl. MOSIP verification)
├── twilio_integration/      # Twilio IVR webhooks, call flow, service catalog
│   ├── webhook_handler.py   # All IVR webhook endpoints
│   ├── ivr_flow.py          # IVR state enum, enrollment phrase picker
│   └── service_catalog.py   # 5-service catalog (EN+SW questions, read-back templates)
├── MOSIP_eSignet/           # MOSIP e-Signet tooling (Java, external)
│   ├── collab-mock-mds-reg/ # Mock MDS for Registration
│   └── collab-mock-mds-auth/# Mock MDS for Auth
├── scripts/
│   ├── download_models.py   # Pre-download ML model weights (~8 GB)
│   ├── create_esignet_user.py # Create individual test users in mock-identity-system
│   └── esignet_test_users/  # Batch-create 5 pre-built test user profiles
├── migrations/              # Alembic database migrations
├── tests/                   # pytest test suite
├── docs/                    # Detailed setup guides and flow documentation
├── dev-phone/               # Twilio Dev Phone serverless scaffold
├── requirements.txt
├── alembic.ini
├── .env.example             # Environment variable template
└── CLAUDE.md                # AI assistant context
```

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Individual test suites
pytest tests/test_db.py -v              # Database CRUD
pytest tests/test_preprocessor.py -v    # Audio pipeline
pytest tests/test_embedding.py -v       # ECAPA-TDNN
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
pytest tests/test_mosip.py -v           # MosipService (OIDC, JWT, replay protection)
pytest tests/test_mosip_schemas.py -v   # MOSIP Pydantic schemas
pytest tests/test_mosip_api.py -v       # MOSIP API endpoints
pytest tests/test_e2e.py -v -s          # Full end-to-end flow
pytest tests/test_esignet_e2e.py -v -s  # Full MOSIP e-Signet + VeriVoice e2e flow
```

---

## Security

- **No raw audio storage** — only Paillier HE-encrypted 192-dim embeddings
- **Memory safety** — plaintext centroids and live embeddings zeroed after use
- **Paillier HE** — voice matching computed on encrypted data (2048-bit keys)
- **Ed25519** — consent tokens cryptographically signed for integrity and non-repudiation
- **Twilio request validation** — webhook endpoints verify `X-Twilio-Signature` when auth token is configured
- **MOSIP e-Signet OIDC** — JWT validation (PS256 signature via JWKS, expiry, audience, issuer, nonce)
- **OIDC replay protection** — state/nonce stored in Redis with 5-min TTL, consumed atomically on callback
- **Identity-verified enrollment** — MOSIP verification token single-use (consumed on enrollment)

---

## Documentation

Detailed guides are in the `docs/` folder:

| Document | Description |
|---|---|
| [`Twilio_IVR_Setup&Docs.md`](docs/Twilio_IVR_Setup&Docs.md) | Comprehensive IVR guide: recording mechanics, complete user journey walkthrough, webhook reference, state machine diagram, troubleshooting |
| [`ivr_verify_flow.md`](docs/ivr_verify_flow.md) | IVR identity verification via DTMF + eSignet server-driven OAuth (no browser) |
| [`esignet_local_setup.md`](docs/esignet_local_setup.md) | Local eSignet Docker stack setup: RSA keygen, OIDC client registration, mock user creation |
| [`esignet_browser_test.md`](docs/esignet_browser_test.md) | Manual browser test of the eSignet OIDC authorization code flow |
| [`dev_phone_setup&docs.md`](docs/dev_phone_setup&docs.md) | Twilio Dev Phone setup for browser-based IVR testing |

---

## License

This project was developed as a hackathon prototype for the Digital Public Goods Alliance challenge.
