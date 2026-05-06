# VeriVoice IVR Setup Guide — Twilio Voice API

This guide covers how to set up, configure, and use the VeriVoice Interactive Voice Response (IVR) system powered by the Twilio Voice API. It walks through the complete end-to-end flow a caller experiences — from dialing in through enrollment, authentication, consent, and service access.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Twilio Account Setup](#2-twilio-account-setup)
3. [Local Development Setup](#3-local-development-setup)
4. [Environment Configuration](#4-environment-configuration)
5. [Exposing Your Local Server (ngrok)](#5-exposing-your-local-server-ngrok)
6. [Configuring Twilio Webhooks](#6-configuring-twilio-webhooks)
7. [IVR Architecture Overview](#7-ivr-architecture-overview)
8. [How Recording Works — The `<Record>` Verb](#8-how-recording-works--the-record-verb)
9. [Complete User Journey — End-to-End Example](#9-complete-user-journey--end-to-end-example)
   - [Phase 1: Welcome and Language Selection](#phase-1-welcome-and-language-selection)
   - [Phase 2: Enrollment (First-time User)](#phase-2-enrollment-first-time-user)
   - [Phase 2b: Identity-Verified Enrollment via eSignet (Press 3)](#phase-2b-identity-verified-enrollment-via-esignet-press-3)
   - [Phase 3: Authentication (Returning User)](#phase-3-authentication-returning-user)
   - [Phase 4: Consent](#phase-4-consent)
   - [Phase 5: Service Access (5-service catalog)](#phase-5-service-access--5-service-catalog-same-call-after-consent)
10. [Webhook Endpoint Reference](#10-webhook-endpoint-reference)
11. [IVR State Machine](#11-ivr-state-machine)
12. [Testing the IVR Locally](#12-testing-the-ivr-locally)
13. [Testing MOSIP Identity Verification with IVR](#13-testing-mosip-identity-verification-with-ivr)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

- Python 3.10+
- A [Twilio account](https://www.twilio.com/try-twilio) (free trial works)
- A Twilio phone number with **Voice** capability
- [ngrok](https://ngrok.com/) installed (for local development)
- VeriVoice backend running locally on port 8000
- Redis running locally (for challenge phrase sessions and OIDC state)
- (Optional) Java 11+ for MOSIP Mock MDS if testing identity-verified enrollment via IVR

## 2. Twilio Account Setup

### 2.1 Create an Account

1. Sign up at [twilio.com/try-twilio](https://www.twilio.com/try-twilio)
2. Verify your personal phone number (required for trial accounts)
3. From the Twilio Console dashboard, note your:
   - **Account SID** (starts with `AC...`)
   - **Auth Token** (click to reveal)

### 2.2 Purchase a Phone Number

1. Go to **Phone Numbers** > **Manage** > **Buy a number**
2. Select a number with **Voice** capability enabled
3. For East African testing, look for numbers in Kenya (+254) or Tanzania (+255) under the country filter. If unavailable, a US number works for development
4. Note the purchased number (e.g., `+1234567890`)

## 3. Local Development Setup

Start the VeriVoice FastAPI backend:

```bash
# Install dependencies (if not already done)
pip install -r requirements.txt

# Start the server
uvicorn app.main:app --reload --port 8000
```

Verify the server is running:

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","version":"0.1.0"}
```

## 4. Environment Configuration

Add your Twilio credentials to the `.env` file in the project root:

```env
# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_PHONE_NUMBER=+1234567890
```

The app reads these via `app/config.py`. When `TWILIO_AUTH_TOKEN` is set, all incoming webhook requests are validated against Twilio's `X-Twilio-Signature` header. Leave it blank during initial local testing to skip validation.

### Swahili TTS (gTTS via `<Play>`)

Swahili IVR prompts use gTTS (Google Text-to-Speech) instead of Twilio's `<Say>` voice, because `alice` does not support Swahili pronunciation. The generated MP3 files are served from the FastAPI server at `/tts-audio/` and played via `<Play>`. Add these to your `.env`:

```env
# Public URL (must match your ngrok URL so Twilio can fetch the audio)
PUBLIC_BASE_URL=https://a1b2c3d4.ngrok-free.app
TTS_AUDIO_DIR=./tts_audio
```

> **Note:** `PUBLIC_BASE_URL` must be the same URL that Twilio uses to reach your server (your ngrok URL). If this is wrong, Swahili prompts will fail with a Twilio error because it can't fetch the audio file. English prompts are unaffected (they use `<Say>` which requires no file serving).

Static Swahili prompts (like "Bonyeza # ukimaliza") are cached in memory after first generation, so subsequent calls have zero gTTS latency.

### MOSIP e-Signet (Optional)

If you want to test identity-verified enrollment via the IVR (where a citizen's identity is verified against the MOSIP national ID system before voice enrollment), also add:

```env
# MOSIP e-Signet (OIDC)
ESIGNET_BASE_URL=https://esignet.collab.mosip.net
ESIGNET_CLIENT_ID=your_client_id
ESIGNET_CLIENT_SECRET=your_client_secret
ESIGNET_REDIRECT_URI=http://localhost:8000/api/v1/mosip/callback
ESIGNET_JWKS_URI=https://esignet.collab.mosip.net/.well-known/jwks.json
ESIGNET_SCOPES=openid profile
```

And start the MOSIP Mock MDS services (see README.md for commands). The Mock MDS simulates biometric capture devices (fingerprint, iris, face) on localhost ports 4501-4600 so that e-Signet can perform biometric authentication without real hardware.

> **Note:** MOSIP identity verification is available in the IVR via **Press 3** at the main menu. This uses eSignet's server-driven OAuth endpoints over DTMF — no browser, no SMS. The caller enters their national ID and OTP on the keypad, and the backend verifies their identity against eSignet before proceeding into voice enrollment with `identity_verified=True`. See Phase 2b below and [`ivr_verify_flow.md`](ivr_verify_flow.md) for the detailed flow. Alternatively, identity can be verified via the Streamlit web UI or direct API calls.

## 5. Exposing Your Local Server (ngrok)

Twilio needs a public URL to send webhook requests to your local machine.

### 5.1 Start ngrok

```bash
ngrok http 8000
```

ngrok will output something like:

```
Forwarding  https://a1b2c3d4.ngrok-free.app -> http://localhost:8000
```

Copy the `https://...ngrok-free.app` URL. This is your **Base URL**.

### 5.2 Verify the Tunnel

```bash
curl https://a1b2c3d4.ngrok-free.app/health
# Expected: {"status":"ok","version":"0.1.0"}
```

> **Note:** The free ngrok URL changes every time you restart ngrok. You will need to update the Twilio webhook URL each time. Consider `ngrok http 8000 --subdomain=verivoice` on a paid plan for a stable URL.

## 6. Configuring Twilio Webhooks

1. Go to the [Twilio Console](https://console.twilio.com/)
2. Navigate to **Phone Numbers** > **Manage** > **Active Numbers**
3. Click your VeriVoice phone number
4. Under **Voice Configuration**:
   - Set **"A call comes in"** to **Webhook**
   - URL: `https://a1b2c3d4.ngrok-free.app/twilio/voice/welcome`
   - HTTP Method: **POST**
5. Click **Save configuration**

That's it. When someone calls your Twilio number, Twilio will POST to `/twilio/voice/welcome` and the IVR begins.

## 7. IVR Architecture Overview

The IVR is built as a series of **stateless webhook endpoints** that return [TwiML](https://www.twilio.com/docs/voice/twiml) (Twilio Markup Language) XML responses. State is passed between steps via **URL query parameters** — no server-side sessions are needed.

```
Caller dials in
    |
    v
Twilio POSTs to /twilio/voice/welcome
    |
    v
Server returns TwiML (Say + Gather)
    |
    v
Caller presses DTMF digits
    |
    v
Twilio POSTs digits to the next webhook (action URL)
    |
    v
Server processes input, returns next TwiML step
    |
    ... (continues through the flow)
```

**Key TwiML verbs used:**

| Verb | Purpose |
|---|---|
| `<Say>` | Text-to-speech (plays a message to the caller) |
| `<Gather>` | Collects DTMF keypad input |
| `<Record>` | Records the caller's voice and POSTs the recording URL back |
| `<Redirect>` | Forwards to another webhook endpoint |
| `<Hangup>` | Ends the call |

## 8. How Recording Works — The `<Record>` Verb

This is important to understand because every voice capture in VeriVoice (enrollment samples, authentication responses, consent, form answers) uses the same mechanism.

### What happens when the IVR needs the caller to speak

When the server returns TwiML like this:

```xml
<Response>
  <Say>Please say your full name.</Say>
  <Record maxLength="15" playBeep="true" trim="trim-silence"
          action="/twilio/voice/service/callback?lang=en&question_index=0"
          method="POST" />
</Response>
```

The following sequence occurs:

```
Server returns <Say> + <Record> TwiML
    |
    v
1. Twilio plays the question via TTS
    |  "Please say your full name."
    |  "Press pound when you are done."
    v
2. A beep plays (playBeep="true")
    |  This signals: "start speaking now"
    v
3. Caller speaks their answer
    |  "Amina Juma Ochieng"
    v
4. Recording ENDS when any of these conditions is met:
    |
    |── (a) KEYPRESS — # (primary method)
    |       The caller presses # on their phone keypad
    |       to signal "I'm done speaking." This gives the
    |       caller explicit control over when recording stops.
    |
    |── (b) SILENCE TIMEOUT — 5 seconds of silence (auto-stop)
    |       If the caller stops speaking for 5 seconds without
    |       pressing #, Twilio automatically ends the recording.
    |       This prevents the call from hanging if the caller
    |       forgets to press #.
    |
    |── (c) MAX LENGTH HIT (safety fallback)
    |       The recording hard-stops at maxLength seconds
    |       (15s for form answers, 10s for enrollment, 5s for
    |       confirmation). Prevents runaway recordings.
    |
    v
5. trim="trim-silence" strips leading/trailing silence
    |  from the saved audio file
    v
6. Twilio saves the recording and POSTs to the action URL
    |  with RecordingUrl=https://api.twilio.com/2010-04-01/...
    v
7. Our callback endpoint processes the recording
   (download -> preprocess -> Whisper ASR / ECAPA-TDNN)
```

### In practice — what the caller experiences

The caller simply:
1. **Hears the question** (TTS)
2. **Hears "Press pound when you are done"**
3. **Hears a beep** — recording starts
4. **Speaks their answer**
5. **Presses # on the keypad** — OR waits 5 seconds of silence — recording stops and is submitted

A 5-second silence timeout automatically ends the recording if the caller forgets to press `#`. This is long enough for natural pauses between words (names, addresses) but short enough to prevent the call from hanging indefinitely.

### Recording parameters we use

| Parameter | Value | Why |
|---|---|---|
| `maxLength` | 5–15s | 15s for form answers, 10s for enrollment/auth/consent, 5s for confirmation |
| `playBeep` | `true` | Clear audio cue that the system is listening |
| `trim` | `trim-silence` | Removes silence padding so Whisper gets clean audio |
| `finishOnKey` | `#` | Caller presses # to stop recording (primary stop method) |
| `timeout` | `5` seconds | Auto-stops recording after 5s of silence if caller doesn't press # |

### Important: RecordingUrl authentication

The `RecordingUrl` that Twilio POSTs to our callback is a URL hosted on Twilio's servers. To download the actual audio file, you need to authenticate with your Twilio credentials:

```python
import httpx

async def download_recording(recording_url: str) -> bytes:
    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    async with httpx.AsyncClient(auth=auth) as client:
        resp = await client.get(f"{recording_url}.wav")
        return resp.content
```

---

## 9. Complete User Journey — End-to-End Example

Below is a detailed walkthrough of a caller named **Amina** interacting with VeriVoice for the first time, then returning later to authenticate and access a service. This covers every IVR endpoint.

---

### Phase 1: Welcome and Language Selection

**Amina dials the VeriVoice phone number.**

**Step 1 — Welcome message**

Twilio POSTs to: `POST /twilio/voice/welcome`

The system plays:
> "Welcome to VeriVoice. Press 1 for English. Press 2 for Swahili."

Amina presses **1** on her phone keypad.

If no input is received within 30 seconds, the system defaults to English.

**Step 2 — Language confirmed, action selection**

Twilio POSTs to: `POST /twilio/voice/welcome/language` with `Digits=1`

The system plays:
> "You selected English. Press 1 to enroll. Press 2 to authenticate. Press 3 to verify your identity."

Amina is a first-time user. She can either:
- Press **1** to enroll directly (manual national ID entry, `identity_verified=False`), **or**
- Press **3** to verify her identity via e-Signet first (DTMF OTP flow), which then proceeds into enrollment with `identity_verified=True` + linked `mosip_individual_id`.

**Step 3 — Route to enrollment**

Twilio POSTs to: `POST /twilio/voice/welcome/action?lang=en` with `Digits=1`

The system redirects Amina to the enrollment flow.

---

### Phase 2: Enrollment (First-time User)

Enrollment collects **5 voice samples** to build a voiceprint. For each sample, the system randomly selects a phrase from the bilingual phrase pool, plays it via TTS, and the caller repeats it — pressing **#** to stop recording. The system also collects the caller's national ID number via keypad input.

> **IVR vs Streamlit:** The IVR flow plays random TTS-prompted phrases for enrollment. The Streamlit (web) flow does not use random phrases — users upload their own pre-recorded audio files instead. Both paths produce the same voice biometric (ECAPA-TDNN embedding centroid).

> **MOSIP Integration Note:** Citizens can verify their identity via MOSIP e-Signet in two ways: **(a)** Streamlit web UI using the browser OIDC flow, or **(b)** IVR DTMF OTP flow (Press **3** at the menu) where the entire verification happens on the phone call via e-Signet's server-driven OAuth endpoints. In both cases, successful verification sets `identity_verified=True` and links `mosip_individual_id` on the citizen record. Enrolling directly via Press 1 without verification still works but creates the citizen with `identity_verified=False`. See `docs/ivr_verify_flow.md` for the DTMF flow details.

**Step 4 — Enter national ID**

Twilio POSTs to: `POST /twilio/voice/enroll?lang=en&step=0`

The system plays:
> "Please enter your national ID number followed by the pound key."

Amina types her national ID on the keypad: `29384756#`

**Step 5 — First voice recording (sample 1 of 5)**

Twilio POSTs to: `POST /twilio/voice/enroll?lang=en&step=1` with `Digits=29384756`

The system randomly selects a phrase from the pool and plays it:
> "Please say: The market opens early on Wednesday."

(The phrase is randomly chosen each time — it may be any phrase from the bilingual pool.)

*\[Beep\]* — Amina repeats the phrase. She finishes and presses **#** on her keypad. Twilio ends the recording and POSTs the `RecordingUrl` to the callback.

**Step 6 — Recording callback (sample 1 saved)**

Twilio POSTs to: `POST /twilio/voice/enroll/callback?lang=en&step=1&national_id=29384756` with `RecordingUrl=https://api.twilio.com/...`

The system advances to sample 2.

**Steps 7–14 — Samples 2 through 5**

The same prompt-record-callback cycle repeats for each remaining sample. Each time:
1. System randomly selects a phrase from the pool and plays it via `<Say>`
2. *\[Beep\]* plays
3. Amina repeats the phrase
4. She presses **#** — Twilio ends the recording
5. Callback fires, system advances to the next sample

Each sample gets a fresh random phrase from the bilingual phrase pool. Duplicates are possible but acceptable — what matters is capturing the caller's voice biometric, not phrase uniqueness.

**Step 15 — Enrollment complete**

After the 5th recording callback, the system plays:
> "All five samples recorded. Enrollment complete. Thank you."

The call ends (hangup).

**What happens behind the scenes:**

1. Each recording URL points to a WAV file hosted by Twilio
2. The backend downloads each recording and runs it through:
   - `AudioPreprocessor` (16 kHz, VAD, noise reduction, normalization)
   - `EmbeddingService` (ECAPA-TDNN extracts a 192-dimensional embedding)
3. The 5 embeddings are averaged into a **centroid vector** and L2-normalized
4. The centroid is encrypted with **Paillier homomorphic encryption** (2048-bit)
5. The plaintext centroid is zeroed from memory
6. The encrypted ciphertext is stored in the `VOICE_TEMPLATE` table linked to Amina's `CITIZEN` record

---

### Phase 2b: Identity-Verified Enrollment via eSignet (Press 3)

Instead of enrolling directly with just a national ID (Press 1, Phase 2), the caller can press **3** at the action menu to first verify their identity against MOSIP via eSignet. The entire verification happens **on the phone call** using DTMF — no SMS, no browser, no public URL exposure for eSignet. After successful verification, the call falls through into the same Phase 2 enrollment flow, but the resulting `CITIZEN` row gets linked to the verified MOSIP `individual_id` and `identity_verified=True` is set automatically.

Use this path when you want a **MOSIP-verified** voice enrollment. The voice biometric pipeline is identical to Phase 2; only the identity-binding step is added at the front.

**Step 1 — Choose Verify Identity**

After language selection (Phase 1), the action menu plays:
> "You selected English. Press 1 to enroll. Press 2 to authenticate. Press 3 to verify your identity."

Amina presses **3**. Twilio POSTs to `/twilio/voice/welcome/action?lang=en` with `Digits=3`. The handler redirects to `/twilio/voice/verify/start`.

**Step 2 — Enter national ID**

Twilio POSTs to: `POST /twilio/voice/verify/start?lang=en`

The system plays:
> "Please enter your national identity number followed by the pound key."

Amina types her national ID on the keypad: `8267411571#`. Twilio fires the gather action `/twilio/voice/verify/nid` with `Digits=8267411571`.

**Step 3 — Backend triggers eSignet OTP**

`POST /twilio/voice/verify/nid?lang=en` (form: `Digits=8267411571`, `CallSid=CA…`)

The backend's `MosipService.start_otp_auth(national_id)` runs the server-driven OAuth dance against eSignet:

1. `GET /v1/esignet/csrf/token` → CSRF token + cookies
2. `POST /v1/esignet/authorization/v3/oauth-details` with the registered `verivoice-client-cf7d48a7` `clientId`, PKCE challenge, nonce, state, `acrValues=mosip:idp:acr:generated-code` → returns `transactionId` + `oauth-details-hash`
3. `POST /v1/esignet/authorization/send-otp` with `transactionId` + `individualId` + `otpChannels=["EMAIL","PHONE"]` → eSignet's mock-identity-system sends the OTP (in dev mode, the OTP **is** the user's PIN)

The full session (transaction_id, oauth_details_key, oauth_details_hash, code_verifier, nonce, state, cookies_jar) is serialized to JSON and stored in Redis under `ivr:verify_session:{CallSid}` with a 10-minute TTL.

> If `start_otp_auth` raises (invalid individual ID, eSignet down, client not registered), the IVR plays *"That identity could not be verified. Please try again."* and loops back to `/voice/verify/start`.

**Step 4 — Prompt for OTP**

Still in the same response, the system plays:
> "An OTP has been sent. Please enter the six-digit OTP followed by the pound key."

A `<Gather>` with `numDigits=6`, `finishOnKey=#`, `timeout=30` waits for keypad input. Amina types her PIN `111111#`.

> **Mock dev note:** in eSignet's mock-identity-system the user's PIN doubles as the OTP, so you enter the same number you set when creating the user via `bash scripts/esignet_test_users/create_all.sh`. In production this would be a real OTP delivered out-of-band.

**Step 5 — Backend verifies OTP and exchanges for id_token**

`POST /twilio/voice/verify/otp?lang=en` (form: `Digits=111111`, `CallSid=CA…`)

The backend reads the session from Redis using `CallSid`, then calls `MosipService.verify_otp_and_get_identity(session, national_id, otp)`:

1. `POST /v1/esignet/authorization/v3/authenticate` with `transactionId`, `individualId`, and `challengeList=[{authFactorType:"OTP", challenge:<otp>, format:"alpha-numeric"}]`
2. `POST /v1/esignet/authorization/auth-code` to convert the authenticated transaction into an OAuth `code`
3. `POST /v1/esignet/oauth/v2/token` with the code + `private_key_jwt` client assertion (signed using `esignet_private_key.pem`, RS256)
4. Validate the returned `id_token` against eSignet's JWKS (PS256 signature, issuer, audience, exp, nonce match) using PyJWT
5. Extract the verified `sub` claim — that's the MOSIP `individual_id`

> If anything fails — wrong OTP, expired session, signature invalid — the IVR plays *"OTP is incorrect. Please try again."* and loops back to `/voice/verify/start`.

**Step 6 — Persist verified identity for the enrollment step**

On success, the backend writes:
```
ivr:verified_identity:{CallSid} = {"national_id": "8267411571",
                                    "mosip_individual_id": "s8-VG8_0sbZMmhKXB7qHS8L9yzlwTD2wvuY08p6kZsM"}
```
to Redis (10-min TTL), then deletes the `ivr:verify_session:{CallSid}` key. The system plays:
> "Your identity has been verified. Now we will enroll your voice."

…and **redirects to** `/voice/enroll?lang=en&step=0&national_id=8267411571`.

**Step 7 — Voice enrollment (same as Phase 2)**

The call now flows through Phase 2 exactly as documented above: 5 random phrase recordings, ECAPA-TDNN embeddings, Paillier HE encryption, store in `VOICE_TEMPLATE`. The only difference is that when the enrollment background task creates the `CITIZEN` row, it reads `ivr:verified_identity:{CallSid}` from Redis, finds the `mosip_individual_id` for this call, and creates the row with:

- `national_id_number = "8267411571"` (from URL)
- `mosip_individual_id = "s8-VG8_0sbZMmhKXB7qHS8L9yzlwTD2wvuY08p6kZsM"` (from Redis)
- `identity_verified = True`

The Redis verification key is consumed (one-time use) so the same verification can't be replayed for a different enrollment.

**Step 8 — Enrollment complete**

After the 5th recording is processed, the system plays:
> "Enrollment complete. Thank you."

The call ends. The new citizen is now MOSIP-verified — Streamlit's "MOSIP Verified" badge will show, and any future authentication via Phase 3 will reflect the verified status.

**Endpoint summary for this phase**

| Step | Endpoint | Inputs | Notes |
|---|---|---|---|
| 2 | `POST /twilio/voice/verify/start` | `lang` (query) | Plays NID prompt + Gather |
| 3 | `POST /twilio/voice/verify/nid` | `Digits` (form), `lang` (query), `CallSid` (form) | Calls eSignet `oauth-details` + `send-otp`, stores session in Redis |
| 5 | `POST /twilio/voice/verify/otp` | `Digits` (form, 6-digit OTP), `lang` (query), `CallSid` (form) | Calls eSignet `authenticate` + `auth-code` + `token`, validates JWT, extracts MOSIP `sub`, redirects to `/voice/enroll` |
| 7 | `POST /twilio/voice/enroll?step=0&national_id={nid}` | -- | Standard Phase 2 enrollment, but reads `ivr:verified_identity:{CallSid}` to link MOSIP ID |

**Common failure modes**

| Symptom | Likely cause | Fix |
|---|---|---|
| `eSignet start_otp_auth failed: 'NoneType' object is not subscriptable` | OIDC client `verivoice-client-cf7d48a7` not registered (likely after `docker compose down`) | Re-register the client — see `docs/esignet_local_setup.md` Section 3 |
| `That identity could not be verified` | National ID doesn't exist in `mock-identity-system` | Run `bash scripts/esignet_test_users/create_all.sh` |
| `OTP is incorrect` | Wrong PIN entered, or session expired (>10 min between NID and OTP) | Restart from Press 3, enter the correct PIN (which equals the OTP in mock mode) |
| `Session expired. Please start again.` | Caller waited too long between steps | The verify session has a 10-minute TTL — start over |

For deeper detail on the eSignet endpoints used, see `docs/ivr_verify_flow.md`.

---

### Phase 3: Authentication (Returning User)

**Amina calls back later to authenticate.**

She goes through the welcome flow (Phase 1) and this time presses **2** to authenticate.

**Step 1 — Enter national ID**

Twilio POSTs to: `POST /twilio/voice/authenticate?lang=en`

The system plays:
> "Please enter your national ID number followed by the pound key."

Amina types her national ID on the keypad: `29384756#`

**Step 2 — Challenge phrase played**

Twilio POSTs to: `POST /twilio/voice/authenticate?lang=en&attempt=0` with `Digits=29384756`

The backend generates a random challenge phrase from the pool:
> "Please say the following phrase: The market opens early on Wednesday."

A `challenge_id` (UUID) is generated and stored in memory.

*\[Beep\]* — Amina speaks the phrase. She presses **#** on her keypad. Twilio ends the recording and POSTs the `RecordingUrl` to the callback.

**Step 3 — Authentication callback (real pipeline)**

Twilio POSTs to: `POST /twilio/voice/authenticate/callback?lang=en&challenge_id=<uuid>&national_id=29384756&attempt=0` with `RecordingUrl=https://api.twilio.com/...`

The system plays:
> "Your voice is being processed. Please wait."

**What happens behind the scenes (dual-factor authentication):**

**Factor 1 — Voice Biometric Match:**

1. Recording is downloaded from Twilio (authenticated request with TWILIO_ACCOUNT_SID/AUTH_TOKEN)
2. Audio is preprocessed (16 kHz, VAD, noise reduction, normalization)
3. ECAPA-TDNN extracts a live 192-dim embedding
4. Citizen is looked up by national ID; stored HE-encrypted centroid is loaded
5. An HE dot product is computed between the live embedding and encrypted centroid
6. The result is decrypted with the Paillier private key
7. The cosine similarity score is compared against the threshold (0.45)
8. The live embedding is zeroed from memory

**Factor 2 — Transcript Match (2FA):**

1. The same audio is transcribed using the language-appropriate ASR model:
   - **English:** OpenAI Whisper (large-v3)
   - **Swahili:** w2v-BERT 2.0 (`badrex/w2v-bert-2.0-swahili-as`) — outperforms Whisper on Swahili speech
2. Both the transcript and the expected challenge phrase are normalized:
   - Unicode normalization (unidecode)
   - Lowercased
   - Punctuation stripped
   - Whitespace collapsed
3. A **word-level similarity score** is computed:
   - Each word in the expected phrase is checked against the transcript
   - Score = (matched words) / (total expected words)
   - The threshold is **75%** (configurable via `TRANSCRIPT_MATCH_THRESHOLD`)
   - Example: expected has 8 words, ASR gets 6 correct = 75% = pass

**Decision:** Both factors must pass. The result (`granted` or `denied`) is logged in the `AUTH_EVENT` table. The voice score is announced to the caller.

**If GRANTED:**
> "Access granted. Your voice score is 0.87. Available services: the services menu. You will now be directed to consent."

The call **continues** (no hangup) — Amina is redirected to the consent flow.

**If DENIED (first attempt):**
> "Access denied. Your voice score is 0.32. Please try again."

Amina gets a second attempt with a new challenge phrase.

**If DENIED (second attempt):**
> "Access denied again. Please try again later. Goodbye."

The call ends (hangup).

---

### Phase 4: Consent (same call, after auth granted)

After successful authentication, the IVR stays in the same call and redirects Amina to record verbal consent for data sharing.

**Step 1 — Consent text played**

Twilio POSTs to: `POST /twilio/voice/consent?lang=en&citizen_id=<uuid>`

The system reads the consent statement:
> "I consent to share my health records with the Ministry of Health. Say Yes to agree."

*\[Beep\]* — Amina says "Yes, I agree." She presses **#**. Twilio ends the recording.

**Step 2 — Consent callback → redirect to service access**

Twilio POSTs to: `POST /twilio/voice/consent/callback?lang=en&citizen_id=<uuid>` with `RecordingUrl=https://api.twilio.com/...`

**What happens behind the scenes:**

1. The recording is processed through the voice authentication pipeline to verify it is actually Amina speaking
2. If verified, an **Ed25519 digital signature** is generated over the consent details (ministry code, data scope, timestamp)
3. The signed `ConsentToken` is stored in the `CONSENT_TOKEN` table
4. No raw audio is stored — only the cryptographic proof of consent

The system plays:
> "Your consent has been recorded. You will now be directed to the services menu."

The call **continues** — Amina is redirected to the service access flow (no hangup).

---

### Phase 5: Service Access — 5-service catalog (same call, after consent)

After consent is recorded, the IVR redirects Amina into a **service menu**. She picks one of 5 services via DTMF, answers **3 questions** specific to that service, hears a **TTS read-back summary**, and confirms Yes/No. If she says No, the IVR asks which question to fix and only re-asks that single question before replaying the summary.

The 5 services, defined in `twilio_integration/service_catalog.py`:

| DTMF | service_code | Service | Ministry |
|:---:|---|---|---|
| 1 | `pension` | Inua Jamii Pension Withdrawal | MLSP |
| 2 | `mpesa_transfer` | M-Pesa Fund Transfer | SCL |
| 3 | `aid_verification` | Aid Verification (Proof of Life) | UNHCR |
| 4 | `sim_swap` | SIM Swap Protection | TELCO |
| 5 | `telemedicine` | Telemedicine Check-In | MOH |

Each service has its own 3 questions and read-back template in both English and Swahili.

**Step 1 — Service menu (DTMF gather)**

Twilio POSTs to: `POST /twilio/voice/service/menu?lang=en&citizen_id=...&consent_token_id=...`

Before the menu plays, the backend **verifies the `consent_token_id`** exists, is not revoked, and belongs to the citizen. If the consent token is missing or invalid, the call hangs up with "Consent is not valid. Goodbye."

> "Please select a service. Press 1 for Inua Jamii Pension Withdrawal. Press 2 for M-Pesa Fund Transfer. Press 3 for Aid Verification. Press 4 for SIM Swap Protection. Press 5 for Telemedicine Check-In."

Amina presses **1**. Twilio POSTs the DTMF digit back to the same endpoint, which looks up the `service_code` and redirects to `/voice/service?service_code=pension&question_index=0`.

**Step 2 — Service-specific questions (example: pension)**

Twilio POSTs to: `POST /voice/service?service_code=pension&question_index=0&...`

The backend looks up the question set for `pension` and plays Q1:

> "How many times have you received Inua Jamii payments before? Please say the number, or say first time."

*\[Beep\]* — Amina says: "Three." She presses **#**. Twilio fires `/voice/service/callback?question_index=0&service_code=pension`, which:
1. Returns hold audio ("Processing your answer. Please hold.") keeping the call alive
2. Starts a background task that downloads + transcribes the recording via Whisper/w2v-BERT
3. When done, uses the Twilio REST API (`POST /Calls/{CallSid}.json`) to instantly redirect the live call to `/voice/service?service_code=pension&question_index=1&q0=three&...`

This same pattern (hold audio + background ASR + REST API interrupt) is used for Q2 and Q3.

Q2 of pension:

> "Would you like to withdraw the full amount or a partial amount? Please say: full, or partial."

Q3 of pension:

> "How would you like to receive your funds? Please say: M-Pesa wallet, or cash at agent."

The answers are accumulated across requests as URL-encoded query params `q0`, `q1`, `q2` (generic, not service-specific).

**Step 3 — TTS Read-back Summary**

After all 3 answers, Twilio hits `/voice/service?service_code=pension&question_index=3&...`. The backend calls `build_readback(service_code, lang, answers)` from the service catalog, which formats the answers into the service's read-back template:

> "You have requested a full Inua Jamii withdrawal, with three prior payments, delivered via M-Pesa wallet. Is this correct? Say yes or no."

*\[Beep\]* — Amina says: "Yes." She presses **#**.

**Step 4a — Confirmation: YES (happy path)**

Twilio POSTs to `/voice/service/confirm?service_code=pension&q0=...&q1=...&q2=...` with the recording. A background task runs ASR → `classify_yes_no(transcript, lang)`. On "yes", the backend:

1. Persists a `SERVICE_FORM` row with `service_code="pension"`, `ministry_code="MLSP"`, `answers_json='{"payment_count":"three","withdrawal_type":"full","delivery_method":"M-Pesa wallet"}'`
2. Uses the last 8 characters of the form_id (uppercase) as a short **reference ID**, e.g. `B928FDF5`
3. Updates the live call via REST API with:

> "Request submitted successfully. Your reference ID is B928FDF5. Thank you for using VeriVoice."

The call ends.

**Step 4b — Confirmation: NO (correction loop)**

If Amina says "No" at the read-back, the `/voice/service/confirm` background task redirects the call to `/voice/service/correct`:

> "Which question would you like to change? Please say One, Two, or Three."

*\[Beep\]* — Amina says: "Two." She presses **#**.

The `/voice/service/correct/callback` background task runs ASR → `parse_question_number(transcript, lang)` which recognises "one/two/three", "moja/mbili/tatu", "first/second/third", digits, etc. and returns an index 0/1/2.

On success, the call is redirected to `/voice/service?question_index=1&correcting_index=1&...`. Q2 is re-asked. After Amina gives a new answer, the background task sees `correcting_index == question_index` and jumps **straight back to the read-back** (`question_index=3`) rather than advancing to Q3 (which she already answered).

The read-back plays again with the updated answer. Correction cycles are capped at 3; a third "no" ends the call with an apology.

**Step 4c — Confirmation: unclear**

If the caller's answer can't be classified (silence, noise, unexpected word), the IVR re-plays the prompt once. A second unclear answer ends the call.

**The same flow in Swahili:**

If Amina had selected Swahili (lang=sw), the pension menu item would be "Malipo ya Pensheni ya Inua Jamii", and the 3 questions would be:

1. "Umepokea malipo ya Inua Jamii mara ngapi hapo awali? Tafadhali sema nambari, au sema mara ya kwanza."
2. "Ungependa kutoa kiasi chote au kiasi cha sehemu? Tafadhali sema: chote, au sehemu."
3. "Ungependa kupokeaje pesa zako? Tafadhali sema: mkoba wa M-Pesa, au pesa taslimu kwa wakala."

Read-back: "Umeomba kutoa pensheni ya Inua Jamii ya chote, na malipo ya awali tatu, kupitia mkoba wa M-Pesa. Je, hii ni sahihi? Sema Ndiyo au Hapana."

Yes/no classifier recognises "Ndiyo/Sawa/Kubali" for yes and "Hapana/La" for no. Question-number classifier recognises "Moja/Mbili/Tatu" and "Kwanza/Pili/Tatu".

**Key architectural points:**

- **Consent-gated**: every `/voice/service*` endpoint verifies `consent_token_id` before running.
- **Stateless URL params**: no server-side call session; all state (answers, correction counter, service_code) travels in query params between requests.
- **Generic schema**: `SERVICE_FORM.answers_json` is a JSON object keyed by each service's `field_keys` — adding a new service is purely a catalog edit, no DB migration.
- **Background ASR + REST API**: keeps the caller on the line with hold audio while slow ML runs, then instantly redirects the call via Twilio's REST API when done.

---

## 10. Webhook Endpoint Reference

All endpoints are mounted under the `/twilio` prefix and return TwiML XML.

| Endpoint | Method | Purpose | Inputs |
|---|---|---|---|
| `/twilio/voice/welcome` | POST | Welcome message + language selection | -- |
| `/twilio/voice/welcome/language` | POST | Handle language DTMF input | `Digits` (form) |
| `/twilio/voice/welcome/action` | POST | Route to enroll, authenticate, or verify identity | `Digits` (form), `lang` (query) |
| `/twilio/voice/verify/start` | POST | DTMF identity verification — prompt for national ID | `lang` (query) |
| `/twilio/voice/verify/nid` | POST | Trigger eSignet OTP for entered national ID, prompt for OTP | `Digits` (form), `lang` (query) |
| `/twilio/voice/verify/otp` | POST | Verify OTP with eSignet, redirect to enrollment with national_id pre-filled | `Digits` (form), `lang` (query) |
| `/twilio/voice/enroll` | POST | National ID input or play random **unique** phrase + record | `lang`, `step`, `national_id`, `session_id` (query) |
| `/twilio/voice/enroll/callback` | POST | Handle completed enrollment recording; on final sample kicks off background ECAPA+Paillier pipeline | `RecordingUrl`, `CallSid` (form), `lang`, `step`, `national_id`, `session_id` (query) |
| `/twilio/voice/authenticate` | POST | National ID input + challenge phrase + record | `lang`, `national_id`, `attempt` (query) |
| `/twilio/voice/authenticate/callback` | POST | Kick off background auth pipeline (voice match + transcript); REST-API redirects call on result | `RecordingUrl`, `CallSid` (form), `lang`, `challenge_id`, `national_id`, `attempt` (query) |
| `/twilio/voice/consent` | POST | Read consent text + record Yes/No | `lang`, `citizen_id`, `unclear_attempt` (query) |
| `/twilio/voice/consent/callback` | POST | Background ASR → `classify_yes_no` → on "yes" Ed25519-sign+persist CONSENT_TOKEN, REST-API redirect to service menu | `RecordingUrl`, `CallSid` (form), `lang`, `citizen_id`, `unclear_attempt` (query) |
| `/twilio/voice/service/menu` | POST | Play 5-service menu + gather DTMF 1–5; verifies `consent_token_id` up-front | `Digits` (form), `lang`, `citizen_id`, `consent_token_id` (query) |
| `/twilio/voice/service` | POST | Play question for chosen `service_code` or TTS read-back summary | `lang`, `citizen_id`, `consent_token_id`, `service_code`, `question_index`, `q0`, `q1`, `q2`, `correction_attempts`, `unclear_attempt`, `correcting_index` (query) |
| `/twilio/voice/service/callback` | POST | Kick off background ASR for one answer; on completion REST-API advances to next Q (or read-back if `correcting_index` matches) | `RecordingUrl`, `CallSid` (form) + same query params as above |
| `/twilio/voice/service/confirm` | POST | Handle read-back Yes/No. On yes persists SERVICE_FORM (service_code + answers_json), announces 8-char reference ID, hangs up. On no redirects to correction. | `RecordingUrl`, `CallSid` (form) + service params |
| `/twilio/voice/service/correct` | POST | Ask "which question — one, two, or three?" after caller says "no" at read-back | `lang`, `citizen_id`, `consent_token_id`, `service_code`, `q0/q1/q2`, `correction_attempts`, `unclear_attempt` (query) |
| `/twilio/voice/service/correct/callback` | POST | Background ASR → `parse_question_number` → REST-API redirect to re-ask that single question with `correcting_index` set | `RecordingUrl`, `CallSid` (form) + same query params |

### Related REST API Endpoints (Non-IVR)

The following endpoints are used by the Streamlit web UI and direct API callers, not by the Twilio IVR flow:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/mosip/authorize` | GET | Initiate MOSIP e-Signet OIDC identity verification |
| `/api/v1/mosip/callback` | GET | Handle e-Signet redirect (exchange code for verified MOSIP ID) |
| `/api/v1/mosip/link` | POST | Link verified MOSIP identity to an existing citizen |

These enable identity-verified enrollment via the Streamlit web UI. The IVR flow has its own MOSIP verification path that uses eSignet's server-driven OAuth endpoints directly (DTMF OTP) — see `/twilio/voice/verify/*` above and `docs/ivr_verify_flow.md`.

## 11. IVR State Machine

The IVR uses a stateless design — state is encoded in URL query parameters, not server-side sessions. The `IVRState` enum in `twilio_integration/ivr_flow.py` documents the logical states:

```
WELCOME
  |
  +-- (Press 1) --> LANGUAGE: English
  +-- (Press 2) --> LANGUAGE: Swahili
                        |
                        +-- (Press 1) --> ENROLL_PROMPT
                        |                     |
                        |                     +-- Enter national ID (#)
                        |                     |
                        |                     +-- Play random phrase 1 --> [Beep] Repeat --> presses #
                        |                     +-- Play random phrase 2 --> [Beep] Repeat --> presses #
                        |                     +-- Play random phrase 3 --> [Beep] Repeat --> presses #
                        |                     +-- Play random phrase 4 --> [Beep] Repeat --> presses #
                        |                     +-- Play random phrase 5 --> [Beep] Repeat --> presses #
                        |                           |
                        |                           v
                        |                     ENROLL_COMPLETE --> Hangup
                        |
                        +-- (Press 3) --> VERIFY_START
                        |                     |
                        |                     +-- Enter national ID (#) --> VERIFY_NID
                        |                     |                                |
                        |                     |                                +-- call eSignet send-otp
                        |                     |                                v
                        |                     +-- Enter 6-digit OTP (#) --> VERIFY_OTP
                        |                                                      |
                        |                                                      +-- call eSignet authenticate + auth-code + token
                        |                                                      |
                        |                                                      +-- success: redirect to ENROLL_PROMPT
                        |                                                      |                (with national_id pre-filled)
                        |                                                      +-- failure: back to VERIFY_START
                        |
                        +-- (Press 2) --> AUTH_NATIONAL_ID
                                              |
                                              +-- Enter national ID (#)
                                              |
                                              v
                                         AUTH_CHALLENGE
                                              |
                                              +-- Play challenge phrase
                                              +-- [Beep] Record response --> presses #
                                              |
                                              v
                                         AUTH_PIPELINE (download + process)
                                              |  Voice biometric match (ECAPA-TDNN + HE)
                                              |  Transcript match (Whisper/w2v-BERT)
                                              |  Score announced to caller
                                              |
                                              +-- (if GRANTED) ─────��────────────────────┐
                                              |                                          |
                                              +-- (if DENIED, attempt 1)                 |
                                              |       |                                  |
                                              |       v                                  |
                                              |   "Try again" --> AUTH_CHALLENGE          |
                                              |       |                                  |
                                              |       +-- (if DENIED, attempt 2)         |
                                              |               |                          |
                                              |               v                          |
                                              |           Hangup                         |
                                              |                                          |
                                              v                                          |
                                         CONSENT_PROMPT  <───────────────────────────────┘
                                              |  (same call, no hangup)
                                              |
                                              +-- [Beep] Record "Yes/No" --> presses #
                                              |
                                              v
                                         CONSENT_PIPELINE (background)
                                              |  ASR → classify_yes_no
                                              |    yes  → Ed25519 sign + persist CONSENT_TOKEN
                                              |    no   → hangup
                                              |    unclear → retry once, then hangup
                                              |
                                              v
                                         SERVICE_MENU (DTMF 1-5)
                                              |  1=pension, 2=mpesa_transfer, 3=aid_verification,
                                              |  4=sim_swap, 5=telemedicine
                                              |
                                              +-- consent_token_id verified first
                                              |
                                              v
                                         SERVICE_QUESTION (x3 for chosen service_code)
                                              |
                                              +-- Q1 → [Beep] → speak → # → background ASR → q0
                                              +-- Q2 → [Beep] → speak → # → background ASR → q1
                                              +-- Q3 → [Beep] → speak → # → background ASR → q2
                                                  |
                                                  v
                                             TTS READ-BACK SUMMARY (service-specific template)
                                                  |
                                                  +-- [Beep] "Yes/No" --> presses #
                                                  |
                                                  v
                                             SERVICE_CONFIRM (background)
                                                  |  ASR → classify_yes_no
                                                  |    yes → persist SERVICE_FORM, announce ref ID, hangup
                                                  |    no  → SERVICE_CORRECT
                                                  |    unclear → retry once, then hangup
                                                  |
                                                  v
                                             SERVICE_CORRECT — "Which question, 1/2/3?"
                                                  |  ASR → parse_question_number
                                                  |  re-ask that single Q only
                                                  |  correcting_index → jumps back to READ-BACK
                                                  |  cap: 3 correction cycles
                                                  v
                                             SERVICE_COMPLETE --> Hangup
```

## 12. Testing the IVR Locally

### Option A: Call from your phone

1. Start the backend: `uvicorn app.main:app --reload --port 8000`
2. Start ngrok: `ngrok http 8000`
3. Configure the Twilio webhook URL (Section 6)
4. Call your Twilio number from your phone
5. Follow the voice prompts — speak your answer after the beep, then press **#** on the keypad to submit.

### Option B: Use the Twilio Dev Phone

1. Install the [Twilio Dev Phone](https://www.twilio.com/docs/labs/dev-phone) plugin
2. Run `twilio dev-phone` in your terminal
3. Open the browser UI and make a call to your Twilio number

### Option C: cURL testing (raw TwiML)

You can test individual endpoints without making a phone call. Each returns TwiML XML that you can inspect to verify the flow logic.

```bash
# Test the welcome endpoint
curl -X POST http://localhost:8000/twilio/voice/welcome

# Test language selection (English)
curl -X POST http://localhost:8000/twilio/voice/welcome/language \
  -d "Digits=1"

# Test action routing (enroll)
curl -X POST "http://localhost:8000/twilio/voice/welcome/action?lang=en" \
  -d "Digits=1"

# Test enrollment prompt (national ID step)
curl -X POST "http://localhost:8000/twilio/voice/enroll?lang=en&step=0"

# Test authentication — national ID prompt
curl -X POST "http://localhost:8000/twilio/voice/authenticate?lang=en"

# Test authentication — after entering national ID (generates challenge phrase)
curl -X POST "http://localhost:8000/twilio/voice/authenticate?lang=en&attempt=0" \
  -d "Digits=29384756"

# Test the service menu (expects Digits 1-5)
curl -X POST "http://localhost:8000/twilio/voice/service/menu?lang=en&citizen_id=<uuid>&consent_token_id=<uuid>"

# Test pension question 1 (needs valid consent_token_id)
curl -X POST "http://localhost:8000/twilio/voice/service?lang=en&service_code=pension&question_index=0&citizen_id=<uuid>&consent_token_id=<uuid>"

# Test pension read-back summary (question_index=3 triggers summary)
curl -X POST "http://localhost:8000/twilio/voice/service?lang=en&service_code=pension&question_index=3&citizen_id=<uuid>&consent_token_id=<uuid>&q0=three&q1=full&q2=M-Pesa%20wallet"

# Test the same in Swahili
curl -X POST "http://localhost:8000/twilio/voice/service?lang=sw&service_code=pension&question_index=0&citizen_id=<uuid>&consent_token_id=<uuid>"
```

### Option D: Twilio Console Debugger

1. Go to **Monitor** > **Logs** > **Calls** in the Twilio Console
2. Click on a call SID to see the full webhook request/response chain
3. Each step shows the TwiML returned and any errors
4. The recording tab shows all captured audio files with playback

## 13. Testing MOSIP Identity Verification with IVR

There are **two ways** to verify a citizen's MOSIP identity in VeriVoice:

1. **IVR DTMF OTP flow (in-call, recommended)** — the entire verification happens over the phone call using keypad input, via eSignet's server-driven OAuth endpoints. No browser needed.
2. **Streamlit browser OIDC flow (out-of-band)** — citizen completes eSignet login in a browser, then links to an existing citizen record.

### Scenario A: In-call DTMF verification (recommended)

This is the most realistic user experience for a citizen calling in from a phone. No laptop, no browser, no SMS. Uses e-Signet's server-driven OAuth endpoints.

**Step 1 — Start the stack**

```bash
# Terminal 1: eSignet Docker stack (if not already running)
cd esignet/docker-compose
docker compose up -d

# Terminal 2: FastAPI backend
uvicorn app.main:app --reload --port 8000

# Terminal 3: ngrok for Twilio webhooks
ngrok http 8000
```

**Step 2 — Configure the Twilio Voice webhook** to `<ngrok-url>/twilio/voice/welcome` (see Section 6).

**Step 3 — Call the IVR**

1. Dial your Twilio number
2. Press **1** (English) → you hear *"Press 1 to enroll. Press 2 to authenticate. Press 3 to verify your identity."*
3. Press **3** (Verify)
4. IVR: *"Please enter your national identity number followed by the pound key."* → enter your mock user's ID (`8267411571`) + `#`
5. Backend calls eSignet `oauth-details` + `send-otp`
6. IVR: *"An OTP has been sent. Please enter the six-digit OTP followed by the pound key."* → enter `111111` + `#` (mock OTP is always 111111)
7. Backend calls eSignet `authenticate` + `auth-code` + `token`, validates id_token
8. IVR: *"Your identity has been verified. Now we will enroll your voice."*
9. Call redirects into enrollment with `national_id=8267411571` pre-filled — record 5 voice samples
10. Enrollment completes; citizen row has `mosip_individual_id=<verified sub>` + `identity_verified=True`

**Verify the citizen record:**

```bash
sqlite3 verivoice.db "SELECT national_id_number, mosip_individual_id, identity_verified FROM CITIZEN WHERE national_id_number = '8267411571';"
```

Expected output:
```
8267411571|s8-VG8_0sbZMmhKXB7qHS8L9yzlwTD2wvuY08p6kZsM|1
```

See `docs/ivr_verify_flow.md` for the detailed API call sequence.

### Scenario B: Browser OIDC via Streamlit (out-of-band)

#### Scenario B1: Verify on web first, then enroll via IVR

This is useful when a registration agent assists a citizen in person. The agent verifies identity on a laptop/tablet, then the citizen enrolls their voice by calling the IVR.

**Step 1 -- Start all services (4 terminals)**

```bash
# Terminal 1: FastAPI backend
uvicorn app.main:app --reload --port 8000

# Terminal 2: Streamlit UI
streamlit run streamlit_app/app.py --server.port 8501

# Terminal 3: Mock MDS Auth (simulates biometric scanner)
cd MOSIP_eSignet/collab-mock-mds-auth/target
java -cp "mock-mds-1.2.1-SNAPSHOT.jar;lib/*" io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Auth" \
  "mosip.mock.sbi.biometric.type=Biometric Device"

# Terminal 4: ngrok tunnel for Twilio
ngrok http 8000
```

**Step 2 -- Verify identity via Streamlit**

1. Open `http://localhost:8501`
2. Navigate to **Verify Identity (MOSIP)** in the sidebar
3. Click **Verify with MOSIP**
4. Click the green **Open MOSIP e-Signet Login** button
5. On the e-Signet page, authenticate using Mock MDS biometrics (the Mock MDS on port 4502 simulates a fingerprint/iris/face capture)
6. After successful authentication, e-Signet redirects back to Streamlit
7. The page shows: **Identity Confirmed** with the verified MOSIP Individual ID
8. Note the MOSIP ID (e.g., `MOSIP-IND-12345`) -- you'll need it later

**Step 3 -- Enroll the citizen via IVR phone call**

1. Call your Twilio number from the citizen's phone
2. Press **1** for English, then **1** to enroll
3. Enter the national ID on the keypad, press **#**
4. For each of 5 samples, listen to the random phrase, repeat it, and press **#**
5. Enrollment completes -- the citizen now has a voice template with `identity_verified=False`

**Step 4 -- Link the MOSIP identity to the enrolled citizen**

Back on the Streamlit UI:

1. Navigate to **Verify Identity (MOSIP)** (your verified MOSIP ID is still in the session)
2. Under **Link to Existing Citizen**, paste the citizen's `citizen_id` (from the enrollment response or database)
3. Click **Link Identity**
4. The citizen's record is now updated: `identity_verified=True`, `mosip_individual_id` set

**Verify via cURL:**

```bash
# Check the citizen record directly
curl http://localhost:8000/api/v1/mosip/link \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"citizen_id": "<citizen-uuid>", "mosip_individual_id": "MOSIP-IND-12345"}'
```

#### Scenario B2: Enroll via IVR First, Verify MOSIP Later

Useful when the registration agent doesn't have MOSIP access at the time of voice enrollment (e.g., field registration in a remote area). The citizen enrolls their voice over the phone, then visits a registration centre later to verify their MOSIP identity.

1. Citizen calls the IVR, enrolls with 5 voice samples (manual national ID entry)
2. Later, at a registration centre with MOSIP access:
   - Agent opens Streamlit > **Verify Identity (MOSIP)** > verifies citizen via e-Signet
   - Agent links the verified MOSIP ID to the citizen's existing record
3. The citizen's record is upgraded from `identity_verified=False` to `identity_verified=True`

#### Scenario B3: API-Only Testing (No Streamlit, No Phone)

Test the entire MOSIP + IVR chain via cURL without a browser or phone call.

```bash
# 1. Initiate MOSIP OIDC (get the authorize URL)
curl -s http://localhost:8000/api/v1/mosip/authorize | python -m json.tool
# Returns: {"authorize_url": "https://esignet.collab.mosip.net/...", "state": "abc123"}

# 2. (In a real flow, the citizen would visit the authorize_url in a browser
#     and authenticate via e-Signet. The callback URL receives code + state.)
#
#     For testing, you can mock the callback if e-Signet is not reachable:
#     The test suite (tests/test_esignet_e2e.py) shows how to mock this.

# 3. After MOSIP verification, enroll with the verified MOSIP ID:
curl -X POST http://localhost:8000/api/v1/enroll \
  -F "national_id_number=KE-MOSIP-001" \
  -F "preferred_language=en" \
  -F "phone_number=+254700000099" \
  -F "mosip_individual_id=MOSIP-IND-12345" \
  -F "audio_files=@sample1.wav" \
  -F "audio_files=@sample2.wav" \
  -F "audio_files=@sample3.wav" \
  -F "audio_files=@sample4.wav" \
  -F "audio_files=@sample5.wav"
# Returns: {"citizen_id": "...", "identity_verified": true, ...}

# 4. Authenticate (same as non-MOSIP -- voice pipeline is unchanged)
curl -s http://localhost:8000/api/v1/challenge?language=en | python -m json.tool
curl -X POST http://localhost:8000/api/v1/authenticate \
  -F "citizen_id=<citizen-uuid>" \
  -F "challenge_phrase_id=<challenge-uuid>" \
  -F "audio_file=@response.wav"

# 5. The consent token is now linked to a MOSIP-verified citizen
curl -X POST http://localhost:8000/api/v1/consent \
  -F "citizen_id=<citizen-uuid>" \
  -F "ministry_code=MOH" \
  -F "data_scope=health_records" \
  -F "audio_file=@consent.wav"
```

### What Gets Verified in the MOSIP + IVR Chain

```
Web (Streamlit/API)                    IVR (Twilio phone call)
==================                     =======================

e-Signet OIDC login                    Citizen dials in
  |                                      |
  v                                      v
MOSIP biometric auth                   Welcome + language select
(fingerprint/iris/face                   |
 via Mock MDS in dev)                    v
  |                                    Enter national ID (#)
  v                                      |
Verified MOSIP ID returned               v
(mosip_individual_id)                  Record 5 voice samples
  |                                      |
  |                                      v
  |                                    Enrollment complete
  |                                    (identity_verified=False)
  |                                      |
  +---------- Link Identity ----------->+
  |            POST /mosip/link          |
  v                                      v
identity_verified = True               Citizen record updated
mosip_individual_id set                (MOSIP-verified)
                                         |
                                         v
                                       Authenticate, Consent,
                                       Service Access all work
                                       with the verified identity
```

### Automated Testing

The test suite includes a full e2e test that exercises this chain with mocked e-Signet:

```bash
# Runs OIDC -> enrollment -> auth -> consent -> service access
# Verifies identity_verified=True persists through the entire chain
pytest tests/test_esignet_e2e.py -v -s
```

This test takes ~90 seconds (loads ECAPA-TDNN + Whisper models) and verifies:
- OIDC state is consumed after callback (no replay)
- Citizen has `identity_verified=True` and `mosip_individual_id` set
- Consent token references the MOSIP-verified citizen
- All Redis `esignet:*` keys are cleaned up

---

## 14. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Call connects but no audio plays | Webhook URL is wrong or unreachable | Verify ngrok is running and URL matches the Twilio config |
| "Invalid Twilio signature" (403) | `TWILIO_AUTH_TOKEN` is set but the request URL doesn't match | Ensure the ngrok URL in Twilio Console matches exactly (including https) |
| "No recording received" | Caller hung up before speaking, or Twilio couldn't reach the callback | Check Twilio debugger logs for errors |
| Recording URL returns 401 | Twilio recordings require authentication to download | Use `httpx` with Twilio Basic Auth: `(ACCOUNT_SID, AUTH_TOKEN)` |
| Recording ends too early | Caller hit maxLength before pressing # | Increase `maxLength` in `<Record>` or remind caller to press # promptly |
| Recording cuts off long answers | `maxLength` is too short | Increase `maxLength` (currently 15s for form answers, 10s for enrollment) |
| Caller confused about when to speak | No beep or unclear prompt | Ensure `playBeep="true"` is set; consider adding "speak after the beep" to prompts |
| Auth says "Citizen not found" | National ID entered via keypad doesn't match any enrolled citizen | Verify the citizen was enrolled first; check the national ID was entered correctly (digits only, no dashes) |
| Auth always denied | Voice score below 0.45 threshold or transcript doesn't match | Check backend logs for exact scores; ensure the caller speaks the challenge phrase clearly; recording quality may be poor over phone |
| Auth callback takes a long time | ECAPA-TDNN + Whisper models loading for the first time | First auth call loads ML models (~30s). Subsequent calls are fast. Twilio may time out; increase `max_length` if needed |
| ngrok URL changed | Free ngrok assigns a new URL on each restart | Update the Twilio webhook URL, or use a paid ngrok plan for a stable subdomain |
| Swahili prompts not playing | `PUBLIC_BASE_URL` doesn't match your ngrok URL | Set `PUBLIC_BASE_URL` in `.env` to your current ngrok URL (e.g., `https://a1b2c3d4.ngrok-free.app`). Twilio fetches gTTS audio from this URL |
| Swahili audio sounds robotic | gTTS quality is lower than commercial TTS | Expected — gTTS is free-tier Google Translate TTS. Quality is acceptable for a prototype and far better than Twilio `alice` mispronouncing Swahili |
| Read-back summary has wrong answers | Query parameter encoding issue with special characters in names | URL-encode answer values; check ngrok inspector for the raw query string |
| MOSIP identity verification fails in IVR | eSignet Docker stack not running, or mock user not created | Start eSignet (`docker compose up -d`), create test users (`bash scripts/esignet_test_users/create_all.sh`). See Phase 2b and [`ivr_verify_flow.md`](ivr_verify_flow.md) |
| Redis connection refused | Redis not running or wrong URL | Start Redis (`redis-server`) and check `REDIS_URL` in `.env` |

---

## Summary of the Full Call Flow (Amina's Journey)

```
Amina dials +1234567890
  |
  v
"Welcome to VeriVoice. Press 1 for English..."     --> Presses 1
  |
  v
"Press 1 to enroll. Press 2 to authenticate."       --> Presses 1
  |
  v
"Enter your national ID followed by #."              --> Types 29384756#
  |
  v
"Please say: <random phrase from pool>..."             --> [Beep] Speaks --> presses #
  |                                                       (x5 random phrases)
  v
"Enrollment complete. Thank you."                    --> Hangup
  |
  =============== (later that day) ===============
  |
  v
Amina calls again, selects English, presses 2
  |
  v
"Enter your national ID followed by #."              --> Types 29384756#
  |
  v
"Please say: The market opens early on Wednesday."   --> [Beep] Speaks --> presses #
  |
  v
Backend downloads recording, runs full auth pipeline:
  Voice biometric: 0.87 (pass) + Transcript: 87.5% (pass)
  |
  v
"Access granted. Your voice score is 0.87.           --> (same call continues)
 Available services menu.
 You will now be directed to consent."
  |
  v
"I consent to share my health records... Say Yes     --> [Beep] "Yes" --> presses #
 to agree or No to decline."
  |
  v
Backend (background): ASR → classify_yes_no → "yes"
  → Ed25519 sign + persist CONSENT_TOKEN
  → REST API redirects call to service menu
  |
  v
"Please select a service. Press 1 for Inua Jamii      --> Presses 1
 Pension Withdrawal. Press 2 for M-Pesa Fund
 Transfer. Press 3 for Aid Verification.
 Press 4 for SIM Swap Protection.
 Press 5 for Telemedicine Check-In."
  |
  v
"How many times have you received Inua Jamii         --> [Beep] "Three" --> presses #
 payments before?"                                      (background ASR → q0="three")
  |
  v
"Would you like to withdraw the full amount or a     --> [Beep] "Full" --> presses #
 partial amount?"                                       (background ASR → q1="full")
  |
  v
"How would you like to receive your funds?"          --> [Beep] "M-Pesa wallet" --> presses #
                                                         (background ASR → q2="M-Pesa wallet")
  |
  v
"You have requested a full Inua Jamii withdrawal,    --> [Beep] "Yes" --> presses #
 with three prior payments, delivered via
 M-Pesa wallet. Is this correct? Say yes or no."
  |
  v
Backend (background): persist SERVICE_FORM, compute ref ID
  |
  v
"Request submitted successfully. Your reference      --> Hangup
 ID is B928FDF5. Thank you for using VeriVoice."
```

If Amina had said **"No"** at the read-back, she would have heard:

```
"Which question would you like to change?            --> [Beep] "Two" --> presses #
 Please say One, Two, or Three."
  |
  v
(only Q2 is re-asked)
"Would you like to withdraw the full amount or a     --> [Beep] "Partial" --> presses #
 partial amount?"                                       (background ASR → q1="partial")
  |
  v
(read-back replays with the updated answer)
"You have requested a partial Inua Jamii             --> [Beep] "Yes" --> presses #
 withdrawal, with three prior payments,
 delivered via M-Pesa wallet. Is this
 correct? Say yes or no."
  |
  v
"Request submitted successfully..."                  --> Hangup
```
