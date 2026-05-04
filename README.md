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

## Quick Start

### Prerequisites

- Python 3.10+
- Java 11+ (for MOSIP Mock MDS biometric device simulators)
- Redis (for session/OIDC state management)
- ~8 GB disk space (for ML model weights: Whisper ~3GB, w2v-BERT Swahili ~4.5GB, ECAPA-TDNN ~90MB)
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

# Pre-download ML model weights (~8 GB, one-time)
python scripts/download_models.py

# Create your .env file
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start the API server (models auto-warm on boot)
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

Open `http://localhost:8501` to access the demo UI with five pages: Enroll, Authenticate, Consent, Service Access, and Verify Identity (MOSIP).

### MOSIP Mock MDS (Biometric Device Simulators)

For e-Signet identity verification during development, start the Mock MDS Java services:

```bash
# Terminal 3: Registration Mock MDS (port 4501)
cd MOSIP_eSignet/collab-mock-mds-reg/target
java -cp "classes;lib/*" io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Registration" \
  "mosip.mock.sbi.biometric.type=Biometric Device"

# Terminal 4: Auth Mock MDS (port 4502)
cd MOSIP_eSignet/collab-mock-mds-auth/target
java -cp "mock-mds-1.2.1-SNAPSHOT.jar;lib/*" io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Auth" \
  "mosip.mock.sbi.biometric.type=Biometric Device"
```

Verify with: `netstat -ano | findstr :4501`

## API Endpoints

All audio endpoints accept `multipart/form-data` and return JSON.

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
| `/twilio/voice/welcome` | Welcome + language selection |
| `/twilio/voice/verify/start` | DTMF identity verification — prompt for national ID |
| `/twilio/voice/verify/nid` | Trigger eSignet OTP for entered national ID |
| `/twilio/voice/verify/otp` | Verify OTP via eSignet, redirect to enrollment |
| `/twilio/voice/enroll` | National ID input + 5 random-phrase enrollment via `<Record>` |
| `/twilio/voice/authenticate` | National ID input + challenge phrase + real auth pipeline (1 retry on denied) |
| `/twilio/voice/consent` | Verbal consent recording (Yes/No → Ed25519-signs + persists CONSENT_TOKEN) |
| `/twilio/voice/service/menu` | 5-service menu (pension / mpesa / aid / sim swap / telemedicine) via DTMF 1-5 |
| `/twilio/voice/service` | 3 questions + read-back for the chosen `service_code` |
| `/twilio/voice/service/confirm` | Yes/No confirmation: "yes" persists SERVICE_FORM + announces reference ID; "no" → correction flow |
| `/twilio/voice/service/correct` | Asks "which question — 1/2/3?" after "no" at read-back, re-asks only that single question |

**IVR identity verification (DTMF):** From the main menu, pressing **3** starts an eSignet-backed identity check using only the phone keypad. The IVR prompts for the national ID, calls eSignet's server-driven OAuth (`oauth-details` → `send-otp` → `authenticate` → `auth-code` → token), and extracts a verified MOSIP `individual_id` from the signed id_token. On success the call proceeds directly into voice enrollment, and the new `CITIZEN` row is created with `mosip_individual_id` + `identity_verified=True`. No SMS, no browser, no public URLs for eSignet — the whole flow happens on the call. See `docs/ivr_verify_flow.md`.

**Full call continuity:** After successful authentication, the IVR stays in the same call and flows through consent → service menu (DTMF 1-5) → 3 questions for the chosen service → TTS read-back → Yes/No confirmation → (optional per-question correction) → persist + announce reference ID → goodbye. On auth denial, the caller gets one retry; if denied again, the call ends.

**IVR authentication pipeline:** The IVR auth callback downloads the Twilio recording, runs the full dual-stage pipeline (ECAPA-TDNN voice biometric match + Whisper/w2v-BERT transcript match), announces the score, and routes accordingly. This is the same pipeline used by the REST API `POST /api/v1/authenticate`.

All IVR voice recordings use **keypad # to stop** or **5-second silence auto-stop**. The caller hears a beep, speaks, and presses `#` when done (or waits 5s of silence for auto-submit). DTMF gathers allow 30 seconds for input. Processing hold messages repeat every 20 seconds for up to 5 minutes while ML pipelines run in the background.

**IVR TTS strategy:** English prompts use Twilio's built-in `<Say voice="alice">` (zero latency). Swahili prompts use gTTS-generated audio served via `<Play>` — Google TTS has proper Swahili pronunciation while Twilio's `alice` voice does not. Audio files are served from `/tts-audio/` with automatic caching of repeated prompts. Set `PUBLIC_BASE_URL` to your ngrok URL in `.env`.

Interactive API docs available at `http://localhost:8000/docs` (Swagger UI).

## Architecture

```
Interface Layer     FastAPI routers | Streamlit app | Twilio webhooks
                         |                |               |
Service Layer       AudioPreprocessor, EmbeddingService, EncryptionService,
                    MatchingService, TranscriptionService, TTSService,
                    ConsentService, ChallengeService, MosipService
                         |
Data Layer          SQLAlchemy ORM + SQLite (CITIZEN, VOICE_TEMPLATE,
                    AUTH_EVENT, CONSENT_TOKEN)
                         |
Identity Layer      MOSIP e-Signet (OIDC) + Mock MDS (SBI biometric devices)
                         |
Config              Pydantic BaseSettings loading from .env
```

### Authentication Flow (Dual-Gate)

Both stages must pass for access to be granted:

1. **Voice Biometric** — Live audio is preprocessed, embedded via ECAPA-TDNN, then matched against the stored Paillier-encrypted centroid using homomorphic scalar multiplication. The decrypted dot product is compared against `MATCH_THRESHOLD` (0.45).

2. **Phrase Liveness** — The same audio is transcribed (Whisper for English, w2v-BERT for Swahili) and compared against the expected challenge phrase (normalized: lowercase, stripped punctuation, unidecoded).

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
| Speech-to-Text (English) | OpenAI Whisper (large-v3) |
| Speech-to-Text (Swahili) | w2v-BERT 2.0 (`badrex/w2v-bert-2.0-swahili-asr`) |
| Text-to-Speech | gTTS |
| Homomorphic Encryption | python-paillier (2048-bit Paillier) |
| Digital Signatures | PyNaCl (Ed25519) |
| Database | SQLite + SQLAlchemy + Alembic |
| Identity Provider | MOSIP e-Signet (OpenID Connect) |
| Mock Biometric Devices | MOSIP Mock MDS (SBI protocol, Java 11+) |
| OIDC Client | authlib + python-jose (JWT validation) |
| IVR / Telephony | Twilio Voice API |
| Session / State | Redis (challenge phrases, OIDC state/nonce) |
| Demo UI | Streamlit |
| Audio Processing | librosa, scipy, torchaudio |

## Project Structure

```
VeriVoice_proto/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py             # Pydantic settings (.env) including ESIGNET_* config
│   ├── models/               # SQLAlchemy models (Citizen, VoiceTemplate, AuthEvent, ConsentToken)
│   ├── schemas/              # Pydantic schemas (enrollment, auth, consent, mosip)
│   ├── routers/              # API routes (enroll, authenticate, consent, service, mosip)
│   ├── services/             # Business logic (audio, embedding, encryption, matching, ASR, TTS, mosip)
│   ├── db/                   # Database engine, session, CRUD
│   └── utils/                # Audio helpers, key management, OIDC client
├── streamlit_app/app.py      # Streamlit demo UI (5 pages incl. MOSIP verification)
├── twilio_integration/       # Twilio IVR webhooks + call flow state machine
├── MOSIP_eSignet/            # MOSIP e-Signet tooling (external, Java)
│   ├── collab-mock-mds-reg/  # Mock MDS for Registration (SBI device simulator)
│   └── collab-mock-mds-auth/ # Mock MDS for Auth (SBI device simulator)
├── migrations/               # Alembic database migrations
├── tests/                    # pytest test suite (incl. MOSIP e2e)
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
pytest tests/test_enrollment_api.py -v  # Enrollment endpoint (incl. MOSIP-verified)
pytest tests/test_auth_api.py -v        # Authentication endpoint
pytest tests/test_twilio.py -v          # IVR TwiML responses
pytest tests/test_mosip.py -v           # MosipService (OIDC, JWT, replay protection)
pytest tests/test_mosip_schemas.py -v   # MOSIP Pydantic schemas
pytest tests/test_mosip_api.py -v       # MOSIP API endpoints (authorize, callback, link)
pytest tests/test_e2e.py -v -s          # Full end-to-end flow
pytest tests/test_esignet_e2e.py -v -s  # Full MOSIP e-Signet + VeriVoice e2e flow
```

## Configuration

All settings are loaded from `.env` via Pydantic `BaseSettings`. See `.env.example` for all available variables.

Key parameters:

| Parameter | Default | Description |
|---|---|---|
| `SAMPLE_RATE` | 16000 | Audio sample rate (Hz) |
| `MATCH_THRESHOLD` | 0.45 | Cosine similarity threshold for voice match |
| `TRANSCRIPT_MATCH_THRESHOLD` | 0.75 | Word-level similarity threshold for phrase 2FA |
| `PAILLIER_BITS` | 2048 | Paillier key size |
| `WHISPER_MODEL` | large-v3 | Whisper ASR model (English) |
| `SWAHILI_ASR_MODEL` | badrex/w2v-bert-2.0-swahili-asr | w2v-BERT ASR model (Swahili) |
| `ECAPA_SOURCE` | speechbrain/spkrec-ecapa-voxceleb | Speaker embedding model |
| `ENROLLMENT_PHRASES` | 5 | Number of voice samples for enrollment |
| `ESIGNET_BASE_URL` | *(env)* | MOSIP e-Signet server URL |
| `ESIGNET_CLIENT_ID` | *(env)* | OIDC client ID registered with e-Signet |
| `ESIGNET_REDIRECT_URI` | `http://localhost:8000/api/v1/mosip/callback` | OIDC callback URL |
| `ESIGNET_SCOPES` | `openid profile` | OIDC scopes |

## Full Development Setup (IVR + eSignet)

This section covers the complete local setup for testing the phone-based IVR flow with optional MOSIP eSignet identity verification.

### 1. Start Redis

Redis is required for challenge phrase sessions, OIDC state/nonce, and IVR verified-identity tokens.

```bash
# If using Docker:
docker run -d --name verivoice-redis -p 6379:6379 redis:7-alpine

# Or if Redis is installed locally:
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

On startup, all 3 ML models (ECAPA-TDNN, Whisper large-v3, w2v-BERT Swahili) are loaded into memory in a background thread. The `/health` endpoint responds immediately; models are ready by the time the first real request arrives.

Verify: `curl http://localhost:8000/health` should return `{"status":"ok","version":"0.1.0"}`.

### 3. Start ngrok (Twilio Tunnel)

Twilio needs a public URL to reach your local server for webhooks and to fetch gTTS audio files.

```bash
ngrok http 8000
```

Copy the `https://` forwarding URL (e.g., `https://abcd-1234.ngrok-free.app`).

Update `.env`:
```env
PUBLIC_BASE_URL=https://abcd-1234.ngrok-free.app
```

> **Important:** Restart the backend after changing `PUBLIC_BASE_URL` so gTTS audio URLs point to the correct ngrok address.

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

3. Call your Twilio number — you should hear _"Welcome to VeriVoice. Press 1 for English. Press 2 for Swahili."_

### 5. Start eSignet (Optional — for Identity Verification)

Required only if testing the IVR identity verification flow (Press 3 from the main menu). Two options:

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
ESIGNET_PRIVATE_KEY_PATH=./esignet_private_key.pem
```

> See `docs/esignet_local_setup.md` for RSA key generation, OIDC client registration, and mock identity creation.

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

## Security

- **No raw audio storage** — only Paillier HE-encrypted 192-dim embeddings
- **Memory safety** — plaintext centroids and live embeddings zeroed after use
- **Paillier HE** — voice matching computed on encrypted data (2048-bit keys)
- **Ed25519** — consent tokens cryptographically signed for integrity and non-repudiation
- **Twilio request validation** — webhook endpoints verify `X-Twilio-Signature` when auth token is configured
- **MOSIP e-Signet OIDC** — JWT validation (RS256 signature via JWKS, expiry, audience, issuer, nonce)
- **OIDC replay protection** — state/nonce stored in Redis with 5-min TTL, consumed atomically (GETDEL) on callback
- **Identity-verified enrollment** — MOSIP verification token single-use (consumed on enrollment)

## Changelog

### v1.9.6 (2026-05-04)

**New Features**
- **Pre-download ML models script** — `python scripts/download_models.py` caches all 3 model weights (ECAPA-TDNN ~90MB, Whisper large-v3 ~3GB, w2v-BERT Swahili ~4.5GB) so the first server start doesn't block on downloads (`scripts/download_models.py`)
- **Auto-warm models on server start** — FastAPI lifespan hook loads ECAPA-TDNN, Whisper, and w2v-BERT into memory in a background thread at boot. `/health` responds immediately; models are ready before the first real request (`app/main.py`)

### v1.9.5 (2026-05-04)

**Fixes**
- **Swahili ASR model loading** — Fixed 3 bugs preventing `badrex/w2v-bert-2.0-swahili-asr` from loading: model name typo (`-as` → `-asr`), wrong loader classes (`Auto*` → `Wav2Vec2Bert*`), wrong tensor key (`input_values` → `input_features`) (`app/config.py`, `app/services/transcription_service.py`)

### v1.9.4 (2026-05-04)

**Improvements**
- **IVR DTMF timeouts increased** — All gather timeouts raised from 5-10s to 30s, giving callers more time to respond (`twilio_integration/webhook_handler.py`)
- **Hold audio extended to 5 minutes** — Processing steps now keep the call alive for up to 5 min (was ~3 min). Reassurance message repeats once every 20 seconds (was every ~3s) to reduce caller irritation (`twilio_integration/webhook_handler.py`)
- **Recording silence auto-stop** — All `<Record>` verbs now have `timeout=5` (5s of silence ends the recording) instead of `timeout=0` (disabled). Callers can still press `#` to end early (`twilio_integration/webhook_handler.py`)

### v1.7.0 (2026-04-05)

**New Features**
- **5-service IVR catalog** — Replaces the single Health Insurance form with 5 selectable services: Inua Jamii Pension Withdrawal, M-Pesa Fund Transfer, Aid Verification, SIM Swap Protection, Telemedicine Check-In. Each has 3 questions + read-back template in English and Swahili (`twilio_integration/service_catalog.py`)
- **Service menu endpoint** — New `/twilio/voice/service/menu` plays the 5 options and gathers DTMF 1-5; verifies `consent_token_id` up-front (`twilio_integration/webhook_handler.py`)
- **Per-question correction flow** — When the caller says "No" at read-back, `/twilio/voice/service/correct` asks "which question — 1/2/3?". ASR + `parse_question_number` classify the response (supports "one/two/three", "moja/mbili/tatu", "first/second/third", digits). Only the named question is re-asked, then the read-back replays. Capped at 3 correction cycles.
- **Unique enrollment phrases** — `pick_random_enrollment_phrase` now takes an `exclude` list so the 5 IVR prompts in one enrollment session are always distinct (`twilio_integration/ivr_flow.py`, `twilio_integration/webhook_handler.py`)

**Schema/DB Changes**
- `SERVICE_FORM` table refactored — dropped health-specific columns (`full_name`, `dependants`, `primary_facility`, `form_type`), added generic `service_code` + `answers_json` (TEXT, JSON keyed by each service's field_keys). Adding a new service is now a catalog edit, no migration (`app/models/service_form.py`, migration `f47b92c64a23`)

### v1.6.0 (2026-04-04)

**New Features**
- **Real IVR consent flow** — `/twilio/voice/consent/callback` now runs full pipeline in background: downloads recording → transcribes → `classify_yes_no` → on "yes" Ed25519-signs + persists `CONSENT_TOKEN`, REST-API redirects to service menu; on "no" hangs up; on "unclear" retries once (`twilio_integration/webhook_handler.py`)
- **Consent-gate on service flow** — `/voice/service*` endpoints verify consent token exists, is not revoked, and belongs to the citizen before any form question is asked
- **Service form persistence** — New `SERVICE_FORM` table stores completed submissions with a reference ID derived from the form_id
- **Yes/No intent classifier** — `classify_yes_no(transcript, lang)` helper with English + Swahili word lists (`app/services/confirmation_service.py`)

### v1.5.0 (2026-04-04)

**New Features**
- **IVR background ML pipelines with REST API call updates** — Enrollment, auth, and service-answer ASR now run as background tasks while the caller hears hold audio. When the pipeline finishes, the Twilio REST API (`POST /Calls/{CallSid}.json`) instantly redirects the live call to the result — no polling, no webhook timeouts.
- **IVR enrollment now writes to DB** — Downloads all 5 Twilio recordings, runs the full ECAPA-TDNN + Paillier HE pipeline, creates the `CITIZEN` record and `VOICE_TEMPLATE` (previously the IVR just said "complete" without persisting anything).

### v1.4.0 (2026-04-04)

**New Features**
- **Live IVR authentication pipeline** -- IVR auth callback now downloads the Twilio recording, runs the full dual-stage authentication pipeline (ECAPA-TDNN voice biometric match + Whisper/w2v-BERT transcript match), and announces the result to the caller with their voice score. Replaces the previous prototype placeholder that always said "Authentication complete" (`twilio_integration/webhook_handler.py`)
- **IVR national ID input for authentication** -- Authentication flow now prompts the caller to enter their national ID via keypad before the challenge phrase, enabling citizen lookup and voice template retrieval (`twilio_integration/webhook_handler.py`)
- **Full call continuity** -- After successful authentication, the IVR stays in the same call and flows: auth granted → available services announcement → consent recording → health insurance form (3 questions) → TTS read-back summary → confirmation → goodbye. No more hanging up between stages (`twilio_integration/webhook_handler.py`)
- **Auth retry on denial** -- If authentication is denied, the caller gets one retry with a new challenge phrase. If denied again, the call ends with a goodbye message (`twilio_integration/webhook_handler.py`)
- **Twilio recording download** -- New `_download_twilio_recording()` async helper downloads recorded audio from Twilio's servers with Basic Auth (`twilio_integration/webhook_handler.py`)
- **citizen_id propagation** -- The authenticated citizen's UUID is passed through query params across consent and service access endpoints, maintaining identity context throughout the entire call (`twilio_integration/webhook_handler.py`)

**Fixes**
- **Streamlit JSON decode error** -- Error handlers for non-success API responses now gracefully handle empty or non-JSON response bodies instead of crashing with `JSONDecodeError` (`streamlit_app/app.py`)

**Files Changed**
| File | Change |
|---|---|
| `twilio_integration/webhook_handler.py` | Rewrote auth flow with real pipeline, national ID gather, retry logic, call continuity; added `_download_twilio_recording()`; added `citizen_id` param to consent and service endpoints; added detailed print logging |
| `streamlit_app/app.py` | Wrapped `resp.json()` in try/except for all 5 error handlers |
| `README.md` | Updated IVR webhook table, added call continuity docs, changelog |
| `VeriVoice_PRD.md` | Updated IVR auth flow, demo flow, Twilio integration sections |
| `docs/Twilio_IVR_Setup&Docs.md` | Updated auth walkthrough, webhook reference, state machine, troubleshooting |

### v1.3.1 (2026-04-02)

**Changes**
- **Swahili IVR prompts via gTTS `<Play>`** -- All Swahili voice prompts in the IVR now use gTTS-generated audio played via Twilio `<Play>` instead of `<Say voice="alice">`, which has no Swahili pronunciation support. English prompts remain `<Say>`. Audio files are served via FastAPI StaticFiles at `/tts-audio/` with an in-memory cache to avoid re-generating static prompts (`twilio_integration/webhook_handler.py`, `app/services/tts_service.py`, `app/main.py`)

**Files Changed**
| File | Change |
|---|---|
| `twilio_integration/webhook_handler.py` | Added `_say_or_play()` helper; all prompts use it instead of raw `.say()` |
| `app/services/tts_service.py` | Added `synthesize_with_url()` with hash-based caching; default output dir from config |
| `app/main.py` | Mounted `/tts-audio/` StaticFiles for serving gTTS audio to Twilio |
| `app/config.py` | Added `PUBLIC_BASE_URL` and `TTS_AUDIO_DIR` settings |
| `.env.example` | Added `PUBLIC_BASE_URL` and `TTS_AUDIO_DIR` |
| `tests/test_twilio.py` | Updated Swahili test to expect `<Play>` instead of `<Say>` |

### v1.3.0 (2026-04-02)

**New Features**
- **Dual ASR: w2v-BERT for Swahili, Whisper for English** -- `TranscriptionService` now routes Swahili (`language="sw"`) to `badrex/w2v-bert-2.0-swahili-asr` (a fine-tuned w2v-BERT 2.0 CTC model that outperforms Whisper large-v3 on Swahili speech) and keeps Whisper large-v3 for English and all other languages. Both models are lazy-loaded singletons. Applies to all flows: authentication, consent, and service access — in both IVR and Streamlit (`app/services/transcription_service.py`, `app/config.py`)

**Files Changed**
| File | Change |
|---|---|
| `app/services/transcription_service.py` | Rewritten with dual backend: `_transcribe_whisper()` + `_transcribe_swahili()`, auto-routed by language |
| `app/config.py` | Added `SWAHILI_ASR_MODEL` setting (default: `badrex/w2v-bert-2.0-swahili-asr`) |
| `README.md` | Updated tech stack, config table, auth flow description, changelog |
| `VeriVoice_PRD.md` | Updated ASR references for dual-model architecture |
| `docs/Twilio_IVR_Setup&Docs.md` | Updated auth walkthrough to mention language-specific ASR |

### v1.2.1 (2026-04-02)

**Changes**
- **IVR random enrollment phrases** -- Each of the 5 enrollment recordings now plays a randomly selected phrase from the phrase pool (via `pick_random_enrollment_phrase()`), replacing the previous fixed phrase order. Phrases are drawn from the same bilingual pool used by the challenge service (`twilio_integration/ivr_flow.py`, `twilio_integration/webhook_handler.py`)
- **Streamlit enrollment clarification** -- Enrollment UI now explicitly states that users upload their own pre-recorded audio files with no specific phrase required. The IVR is the only path that uses random TTS-prompted phrases (`streamlit_app/app.py`)

**Files Changed**
| File | Change |
|---|---|
| `twilio_integration/ivr_flow.py` | Replaced fixed `ENROLLMENT_PROMPTS` dict with `pick_random_enrollment_phrase()` function |
| `twilio_integration/webhook_handler.py` | Uses `pick_random_enrollment_phrase()` instead of indexing fixed prompts |
| `streamlit_app/app.py` | Updated enrollment UI text to clarify free-form audio upload |
| `README.md` | Updated changelog and enrollment descriptions |
| `VeriVoice_PRD.md` | Updated enrollment flow descriptions for IVR vs Streamlit |
| `docs/Twilio_IVR_Setup&Docs.md` | Updated enrollment walkthrough to reflect random phrases |

### v1.2.0 (2026-04-02)

**New Features**
- **MOSIP e-Signet OIDC integration** -- Citizens can verify their identity against the MOSIP national ID system via OpenID Connect before or after voice enrollment. Uses biometric authentication (fingerprint/iris/face) through e-Signet, with Mock MDS device simulators for development (`app/services/mosip_service.py`, `app/routers/mosip.py`)
- **Identity-verified enrollment** -- `POST /api/v1/enroll` now accepts optional `mosip_individual_id` from a prior e-Signet callback. If provided and valid (checked via Redis), the citizen is created with `identity_verified=True` (`app/routers/enrollment.py`)
- **3 new MOSIP API endpoints** -- `GET /api/v1/mosip/authorize` (initiate OIDC), `GET /api/v1/mosip/callback` (exchange code for verified identity), `POST /api/v1/mosip/link` (link MOSIP identity to existing citizen) (`app/routers/mosip.py`)
- **OIDC replay protection** -- State/nonce stored in Redis with 5-min TTL, consumed atomically via GETDEL on callback. Verification tokens are single-use (`app/services/mosip_service.py`)
- **Streamlit MOSIP verification page** -- New "Verify Identity (MOSIP)" page with e-Signet redirect flow, callback handling via `st.query_params`, identity linking, and session persistence across pages (`streamlit_app/app.py`)
- **MOSIP verification badges** -- Enroll and Authenticate page headers show "MOSIP Verified" or "Unverified" status; sidebar displays verified MOSIP ID (`streamlit_app/app.py`)
- **MOSIP-aware enrollment toggle** -- When a MOSIP identity is verified in the session, the Enroll page shows "Use Verified MOSIP Identity for Enrollment" toggle (`streamlit_app/app.py`)
- **MOSIP Mock MDS tooling** -- Pre-built Java services for simulating biometric capture devices (SBI protocol) during development. Registration MDS (port 4501) and Auth MDS (port 4502) (`MOSIP_eSignet/`)
- **IVR keypad-stop recordings** -- Added explicit `#` keypress to end recordings (`finishOnKey=#`) with 5-second silence auto-stop (`timeout=5`). Adds bilingual "Press pound when you are done" / "Bonyeza # ukimaliza" prompt before every recording. Prevents mid-sentence cutoffs from pauses (`twilio_integration/webhook_handler.py`)
- **Twilio Dev Phone** -- Added Twilio Dev Phone tooling for browser-based IVR testing without a physical phone (`dev-phone/`)

**Schema/DB Changes**
- `CITIZEN` table: added `mosip_individual_id` (VARCHAR, unique, nullable) and `identity_verified` (BOOLEAN, default False) via Alembic migration `d82f872e1b6f`
- `EnrollmentRequest`: added optional `mosip_individual_id` field
- `EnrollmentResponse`: added `identity_verified` boolean field
- New `app/schemas/mosip.py`: `MosipAuthorizeResponse`, `MosipCallbackRequest`, `MosipIdentityResponse`, `MosipLinkRequest`, `MosipLinkResponse`

**New Dependencies**
- `authlib` -- OIDC client for e-Signet authorization code flow
- `python-jose[cryptography]` -- JWT validation (RS256 signature, JWKS)

**Files Changed/Added**
| File | Change |
|---|---|
| `app/config.py` | Added 6 `ESIGNET_*` settings |
| `app/models/citizen.py` | Added `mosip_individual_id`, `identity_verified` columns |
| `app/db/crud.py` | Added `link_mosip_identity()`, `get_citizen_by_mosip_id()`, updated `create_citizen()` |
| `app/services/mosip_service.py` | New -- MosipService (OIDC authorize, token exchange, JWT validation, Redis state) |
| `app/schemas/mosip.py` | New -- 5 Pydantic schemas for MOSIP endpoints |
| `app/routers/mosip.py` | New -- 3 endpoints (authorize, callback, link) |
| `app/routers/enrollment.py` | Extended with optional `mosip_individual_id` + Redis verification check |
| `app/schemas/enrollment.py` | Added `mosip_individual_id` (request), `identity_verified` (response) |
| `app/utils/oidc.py` | New -- Authlib AsyncOAuth2Client factory |
| `app/main.py` | Registered MOSIP router |
| `streamlit_app/app.py` | Added MOSIP verification page, enrollment toggle, badges |
| `.env.example` | Added `ESIGNET_*` configuration keys |
| `requirements.txt` | Added `authlib`, `python-jose[cryptography]` |
| `MOSIP_eSignet/` | New -- Mock MDS Registration + Auth (pre-built Java) |
| `migrations/versions/d82f872e1b6f_*` | New -- Alembic migration for MOSIP columns |
| `tests/test_mosip.py` | New -- 15 tests (OIDC, JWT validation, replay protection) |
| `tests/test_mosip_schemas.py` | New -- 17 tests (schema validation) |
| `tests/test_mosip_api.py` | New -- 10 tests (API endpoints, full authorize->callback->link flow) |
| `tests/test_esignet_e2e.py` | New -- Full MOSIP + VeriVoice e2e test (OIDC->enroll->auth->consent->service) |
| `tests/test_db.py` | Added 6 MOSIP identity linking tests |
| `tests/test_enrollment_api.py` | Added 5 MOSIP-verified enrollment tests |
| `twilio_integration/webhook_handler.py` | All 5 Record calls: `timeout=0`, `finishOnKey=#`, bilingual stop prompt |
| `dev-phone/` | New -- Twilio Dev Phone for browser-based IVR testing |

### v1.1.0 (2026-03-31)

**New Features**
- **Bilingual Consent page** — Streamlit consent UI now supports English and Swahili (`en`/`sw`) with language selector, matching Enroll and Authenticate pages (`streamlit_app/app.py`)
- **Health insurance form redesign** — Service Access overhauled with 3 demo-optimized questions: full name (string capture), dependants (numeric parsing), primary facility (proper noun). Replaces the old generic name/DOB/address questions (`app/routers/service.py`)
- **TTS read-back summary** — New `POST /api/v1/service-access/summary` endpoint generates a spoken confirmation: _"Your name is X, you have Y dependants, and your preferred facility is Z. Is this correct?"_ (`app/routers/service.py`, `app/schemas/consent.py`)
- **Spoken number parsing** — Dependants question parses spoken words to digits: "three" -> 3, "tatu" -> 3 (English + Swahili) (`app/routers/service.py`)
- **Step-by-step Streamlit form wizard** — Service Access UI tracks progress across 3 questions with session state, shows collected answers, displays raw vs parsed transcriptions, and plays TTS summary audio (`streamlit_app/app.py`)
- **IVR form + read-back + confirmation** — Twilio IVR service flow updated with 3 bilingual questions, TTS summary read-back, and yes/no voice confirmation via new `/twilio/voice/service/confirm` endpoint (`twilio_integration/webhook_handler.py`)
- **Twilio IVR setup documentation** — Comprehensive guide covering setup, recording mechanics, and full end-to-end example (`docs/Twilio_IVR_Setup&Docs.md`)

**Fixes**
- **Transcript matching was 100% exact** — Authentication 2FA phrase check now uses word-level similarity with 75% threshold instead of exact string match. Configurable via `TRANSCRIPT_MATCH_THRESHOLD` (`app/services/challenge_service.py`, `app/config.py`)
- **Transcript match score not exposed** — Authentication response now returns `transcript_match_score` (0.0–1.0) alongside the boolean `transcript_match` (`app/schemas/authentication.py`, `app/routers/authentication.py`)

**Files Changed**
| File | Change |
|---|---|
| `app/config.py` | Added `TRANSCRIPT_MATCH_THRESHOLD` (0.75) |
| `app/services/challenge_service.py` | Added `_word_similarity()`, `match_transcript()` returns score dict |
| `app/schemas/authentication.py` | Added `transcript_match_score` field |
| `app/routers/authentication.py` | Wired threshold + score into auth flow |
| `app/routers/service.py` | Rewrote with 3 bilingual questions, number parsing, summary endpoint |
| `app/schemas/consent.py` | Added `field_key`, `raw_transcription`, `questions_remaining`, `ServiceFormSummary` |
| `streamlit_app/app.py` | Consent language selector; Service Access step wizard + TTS playback |
| `twilio_integration/webhook_handler.py` | Service flow with 3 questions, read-back, `/voice/service/confirm` |
| `docs/Twilio_IVR_Setup&Docs.md` | New — full IVR setup guide and documentation |

### v1.0.0 (2026-03-31)

Initial commit — core prototype with enrollment, dual-stage authentication, consent, and service access.

## License

This project was developed as a hackathon prototype for the Digital Public Goods Alliance challenge.
