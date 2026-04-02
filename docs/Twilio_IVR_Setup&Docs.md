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
   - [Phase 3: Authentication (Returning User)](#phase-3-authentication-returning-user)
   - [Phase 4: Consent](#phase-4-consent)
   - [Phase 5: Service Access (Health Insurance Form)](#phase-5-service-access-health-insurance-form)
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

> **Note:** MOSIP identity verification is primarily used via the Streamlit web UI or direct API calls. The IVR flow currently supports voice-only enrollment without MOSIP verification. Integrating MOSIP into the IVR flow would require a web-based pre-verification step before the caller dials in.

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
4. Recording ENDS when the caller presses # on the keypad:
    |
    |── (a) KEYPRESS — # (primary method)
    |       The caller presses # on their phone keypad
    |       to signal "I'm done speaking." This gives the
    |       caller explicit control over when recording stops.
    |       Silence detection is DISABLED (timeout=0) so the
    |       recording will NOT auto-stop on pauses.
    |
    |── (b) MAX LENGTH HIT (safety fallback)
    |       The recording hard-stops at maxLength seconds
    |       (15s for form answers, 10s for enrollment, 5s for
    |       confirmation). Prevents runaway recordings if the
    |       caller forgets to press #.
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
5. **Presses # on the keypad** — recording stops and is submitted

Silence detection is disabled (`timeout=0`) so the caller can pause, think, or take their time without the recording cutting out mid-sentence. This is important for names, addresses, and facility names where callers may pause between words. The `#` keypress gives the caller explicit, predictable control.

### Recording parameters we use

| Parameter | Value | Why |
|---|---|---|
| `maxLength` | 5–15s | 15s for form answers, 10s for enrollment/auth/consent, 5s for confirmation |
| `playBeep` | `true` | Clear audio cue that the system is listening |
| `trim` | `trim-silence` | Removes silence padding so Whisper gets clean audio |
| `finishOnKey` | `#` | Caller presses # to stop recording (primary stop method) |
| `timeout` | `0` (disabled) | Silence detection is OFF — recording only stops on # or maxLength |

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

If no input is received within 5 seconds, the system defaults to English.

**Step 2 — Language confirmed, action selection**

Twilio POSTs to: `POST /twilio/voice/welcome/language` with `Digits=1`

The system plays:
> "You selected English. Press 1 to enroll. Press 2 to authenticate."

Amina is a first-time user, so she presses **1** to enroll.

**Step 3 — Route to enrollment**

Twilio POSTs to: `POST /twilio/voice/welcome/action?lang=en` with `Digits=1`

The system redirects Amina to the enrollment flow.

---

### Phase 2: Enrollment (First-time User)

Enrollment collects **5 voice samples** to build a voiceprint. The system also collects the caller's national ID number via keypad input.

> **MOSIP Integration Note:** In the web UI (Streamlit), citizens can optionally verify their identity via MOSIP e-Signet before enrollment, which sets `identity_verified=True` on their citizen record. The IVR flow uses manual national ID entry (keypad) and sets `identity_verified=False`. Both paths create valid voice enrollments — the MOSIP verification adds a stronger identity anchor but is not required.

**Step 4 — Enter national ID**

Twilio POSTs to: `POST /twilio/voice/enroll?lang=en&step=0`

The system plays:
> "Please enter your national ID number followed by the pound key."

Amina types her national ID on the keypad: `29384756#`

**Step 5 — First voice recording (sample 1 of 5)**

Twilio POSTs to: `POST /twilio/voice/enroll?lang=en&step=1` with `Digits=29384756`

The system plays:
> "Please say: The sun rises over the mountain every morning."

*\[Beep\]* — Amina speaks the phrase. She finishes and presses **#** on her keypad. Twilio ends the recording and POSTs the `RecordingUrl` to the callback.

**Step 6 — Recording callback (sample 1 saved)**

Twilio POSTs to: `POST /twilio/voice/enroll/callback?lang=en&step=1&national_id=29384756` with `RecordingUrl=https://api.twilio.com/...`

The system advances to sample 2.

**Steps 7–14 — Samples 2 through 5**

The same prompt-record-callback cycle repeats for each remaining phrase. Each time:
1. System plays the phrase via `<Say>`
2. *\[Beep\]* plays
3. Amina speaks the phrase
4. She presses **#** — Twilio ends the recording
5. Callback fires, system advances to the next sample

| Sample | Phrase |
|---|---|
| 2 | "My voice is my password and it is unique." |
| 3 | "The market opens early on Wednesday." |
| 4 | "I confirm this request with my own voice." |
| 5 | "Please verify my identity for this service." |

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

### Phase 3: Authentication (Returning User)

**Amina calls back later to authenticate.**

She goes through the welcome flow (Phase 1) and this time presses **2** to authenticate.

**Step 1 — Challenge phrase played**

Twilio POSTs to: `POST /twilio/voice/authenticate?lang=en`

The backend generates a random challenge phrase from the pool:
> "Please say the following phrase: The market opens early on Wednesday."

A `challenge_id` (UUID) is generated and stored in memory.

*\[Beep\]* — Amina speaks the phrase. She presses **#** on her keypad. Twilio ends the recording and POSTs the `RecordingUrl` to the callback.

**Step 2 — Authentication callback**

Twilio POSTs to: `POST /twilio/voice/authenticate/callback?lang=en&challenge_id=<uuid>` with `RecordingUrl=https://api.twilio.com/...`

The system plays:
> "Your voice is being processed. Please wait."

**What happens behind the scenes (dual-factor authentication):**

**Factor 1 — Voice Biometric Match:**

1. Recording is downloaded from Twilio and preprocessed
2. ECAPA-TDNN extracts a live 192-dim embedding
3. The stored HE-encrypted centroid is loaded from the database
4. An HE dot product is computed between the live embedding and encrypted centroid
5. The result is decrypted with the Paillier private key
6. The cosine similarity score is compared against the threshold (0.45)
7. The live embedding is zeroed from memory

**Factor 2 — Transcript Match (2FA):**

1. The same audio is transcribed using OpenAI Whisper (large-v3)
2. Both the transcript and the expected challenge phrase are normalized:
   - Unicode normalization (unidecode)
   - Lowercased
   - Punctuation stripped
   - Whitespace collapsed
3. A **word-level similarity score** is computed:
   - Each word in the expected phrase is checked against the transcript
   - Score = (matched words) / (total expected words)
   - The threshold is **75%** (configurable via `TRANSCRIPT_MATCH_THRESHOLD`)
   - Example: expected has 8 words, Whisper gets 6 correct = 75% = pass

**Decision:** Both factors must pass. The result (`granted` or `denied`) is logged in the `AUTH_EVENT` table.

After processing:
> "Authentication complete. Thank you."

The call ends.

---

### Phase 4: Consent

After successful authentication, the system can redirect Amina to record verbal consent for data sharing.

**Step 1 — Consent text played**

Twilio POSTs to: `POST /twilio/voice/consent?lang=en`

The system reads the consent statement:
> "I consent to share my health records with the Ministry of Health. Say Yes to agree."

*\[Beep\]* — Amina says "Yes, I agree." She presses **#**. Twilio ends the recording.

**Step 2 — Consent callback**

Twilio POSTs to: `POST /twilio/voice/consent/callback?lang=en` with `RecordingUrl=https://api.twilio.com/...`

**What happens behind the scenes:**

1. The recording is processed through the voice authentication pipeline to verify it is actually Amina speaking
2. If verified, an **Ed25519 digital signature** is generated over the consent details (ministry code, data scope, timestamp)
3. The signed `ConsentToken` is stored in the `CONSENT_TOKEN` table
4. No raw audio is stored — only the cryptographic proof of consent

The system plays:
> "Your consent has been recorded. Thank you."

The call ends.

---

### Phase 5: Service Access (Health Insurance Form)

After consent is granted, Amina is redirected to the voice-driven health insurance form. The form asks **3 questions**, each designed to demonstrate a different ASR capability, followed by a **TTS read-back summary** for confirmation.

**Step 1 — Question 1: Full Name (string capture)**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=0`

> "Please say your full name."

*\[Beep\]* — Amina says: "Amina Juma Ochieng." She presses **#**. Twilio ends the recording and fires the callback.

> **Why this question:** This is the strongest demo moment. Amina just authenticated via voiceprint, and now the system captures structured data from her speech. It shows Whisper ASR working on names — especially African names, which are non-trivial for speech recognition.

**Step 2 — Callback, advance to question 2**

Twilio POSTs to: `POST /twilio/voice/service/callback?lang=en&question_index=0` with `RecordingUrl=https://api.twilio.com/...`

The backend downloads the recording, runs it through the preprocessing pipeline, and transcribes via Whisper. The answer `"Amina Juma Ochieng"` is stored. The IVR redirects to question 2.

**Step 3 — Question 2: Dependants (numeric capture)**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=1`

> "How many dependants would you like to register?"

*\[Beep\]* — Amina says: "Three." She presses **#**. Recording ends.

> **Why this question:** Shows the system handles different response types. When Whisper transcribes "three", the backend parses it to the number `3`. Works for both English ("three" -> 3) and Swahili ("tatu" -> 3). Proves the pipeline works beyond just capturing strings.

**Step 4 — Callback, advance to question 3**

The backend transcribes "three", parses it to `3`, and stores it. The IVR redirects to question 3.

**Step 5 — Question 3: Primary Facility (proper noun / complex response)**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=2`

> "Which hospital or health centre would you like as your primary facility?"

*\[Beep\]* — Amina says: "Kenyatta National Hospital." She presses **#**. Recording ends.

> **Why this question:** This is the showstopper for the demo. It's a real-world question from actual health insurance enrollment (e.g., NHIF in Kenya). The answer is a proper noun (facility name), and it demonstrates Whisper handling longer, more complex spoken responses. When demoing in Swahili, this question is especially impressive.

**Step 6 — Callback, all questions answered**

The backend transcribes the facility name and stores it. All 3 answers are now collected.

**Step 7 — TTS Read-back Summary**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=3` (index exceeds question count, triggers summary)

The system generates a TTS read-back of all collected answers:

> "Thank you. I have recorded: your name is Amina Juma Ochieng, you have 3 dependants, and your preferred facility is Kenyatta National Hospital. Is this correct?"

*\[Beep\]* — Amina says: "Yes." She presses **#**. Recording ends.

**Step 8 — Confirmation**

Twilio POSTs to: `POST /twilio/voice/service/confirm?lang=en&full_name=...&dependants=...&primary_facility=...` with `RecordingUrl=...`

The backend would transcribe the confirmation and check for "yes" / "ndiyo". For the prototype, it assumes confirmed.

> "Your form is complete. Thank you for using VeriVoice."

The call ends (hangup).

**What happens behind the scenes for each answer:**

1. Twilio saves the recording and provides a `RecordingUrl`
2. Backend downloads the audio via authenticated HTTP request
3. `AudioPreprocessor` cleans the audio (16 kHz, VAD, noise reduction)
4. `TranscriptionService` (Whisper large-v3) transcribes speech to text
5. For numeric answers (dependants): spoken words are parsed to digits ("three" -> 3, "tatu" -> 3)
6. Answers are accumulated across steps via URL query parameters (stateless design)
7. After all 3 answers, TTS generates a summary read-back for human confirmation

**The same flow in Swahili:**

If Amina had selected Swahili (lang=sw), the questions would be:
1. "Tafadhali sema jina lako kamili."
2. "Ungependa kusajili wategemezi wangapi?"
3. "Ungependa hospitali au kituo kipi cha afya kuwa kituo chako kikuu?"

And the read-back:
> "Asante. Nimekusanya: jina lako ni Amina Juma Ochieng, una wategemezi 3, na kituo chako kikuu ni Hospitali ya Kenyatta. Je, hii ni sahihi?"

---

## 10. Webhook Endpoint Reference

All endpoints are mounted under the `/twilio` prefix and return TwiML XML.

| Endpoint | Method | Purpose | Inputs |
|---|---|---|---|
| `/twilio/voice/welcome` | POST | Welcome message + language selection | -- |
| `/twilio/voice/welcome/language` | POST | Handle language DTMF input | `Digits` (form) |
| `/twilio/voice/welcome/action` | POST | Route to enroll or authenticate | `Digits` (form), `lang` (query) |
| `/twilio/voice/enroll` | POST | National ID input or play recording prompt | `lang`, `step`, `national_id` (query) |
| `/twilio/voice/enroll/callback` | POST | Handle completed enrollment recording | `RecordingUrl` (form), `lang`, `step`, `national_id` (query) |
| `/twilio/voice/authenticate` | POST | Play challenge phrase + record | `lang` (query) |
| `/twilio/voice/authenticate/callback` | POST | Process auth recording | `RecordingUrl` (form), `lang`, `challenge_id` (query) |
| `/twilio/voice/consent` | POST | Read consent text + record | `lang` (query) |
| `/twilio/voice/consent/callback` | POST | Process consent recording | `RecordingUrl` (form), `lang` (query) |
| `/twilio/voice/service` | POST | Play form question or TTS read-back summary | `lang`, `question_index`, `full_name`, `dependants`, `primary_facility` (query) |
| `/twilio/voice/service/callback` | POST | Process form answer, advance to next question | `RecordingUrl` (form), `lang`, `question_index`, `full_name`, `dependants`, `primary_facility` (query) |
| `/twilio/voice/service/confirm` | POST | Handle yes/no confirmation after read-back | `RecordingUrl` (form), `lang`, `full_name`, `dependants`, `primary_facility` (query) |

### Related REST API Endpoints (Non-IVR)

The following endpoints are used by the Streamlit web UI and direct API callers, not by the Twilio IVR flow:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/mosip/authorize` | GET | Initiate MOSIP e-Signet OIDC identity verification |
| `/api/v1/mosip/callback` | GET | Handle e-Signet redirect (exchange code for verified MOSIP ID) |
| `/api/v1/mosip/link` | POST | Link verified MOSIP identity to an existing citizen |

These enable identity-verified enrollment via the web UI. The IVR flow does not currently use MOSIP — callers enroll via keypad national ID entry.

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
                        |                     +-- [Beep] Record sample 1 --> presses #
                        |                     +-- [Beep] Record sample 2 --> presses #
                        |                     +-- [Beep] Record sample 3 --> presses #
                        |                     +-- [Beep] Record sample 4 --> presses #
                        |                     +-- [Beep] Record sample 5 --> presses #
                        |                           |
                        |                           v
                        |                     ENROLL_COMPLETE --> Hangup
                        |
                        +-- (Press 2) --> AUTH_CHALLENGE
                                              |
                                              +-- Play challenge phrase
                                              +-- [Beep] Record response --> presses #
                                                    |
                                                    v
                                              AUTH_RESULT --> Hangup
                                                    |
                                                    +-- (if granted) --> CONSENT_PROMPT
                                                    |                       |
                                                    |                       +-- [Beep] Record --> presses #
                                                    |                       |
                                                    |                       v
                                                    |                  CONSENT_RESULT
                                                    |                       |
                                                    |                       v
                                                    |                  SERVICE_QUESTION (x3)
                                                    |                       |
                                                    |                       +-- Q1: "Full name?"
                                                    |                       |   [Beep] --> speak --> presses #
                                                    |                       |
                                                    |                       +-- Q2: "How many dependants?"
                                                    |                       |   [Beep] --> speak --> presses #
                                                    |                       |
                                                    |                       +-- Q3: "Which facility?"
                                                    |                           [Beep] --> speak --> presses #
                                                    |                           |
                                                    |                           v
                                                    |                  TTS READ-BACK SUMMARY
                                                    |                  "Your name is X, Y dependants, facility Z.
                                                    |                   Is this correct?"
                                                    |                       |
                                                    |                       +-- [Beep] --> "Yes" --> presses #
                                                    |                       |
                                                    |                       v
                                                    |                  SERVICE_COMPLETE --> Hangup
                                                    |
                                                    +-- (if denied) --> Hangup
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

# Test authentication (generates a challenge phrase)
curl -X POST "http://localhost:8000/twilio/voice/authenticate?lang=en"

# Test service access question 1
curl -X POST "http://localhost:8000/twilio/voice/service?lang=en&question_index=0"

# Test service access read-back summary (after all 3 answers)
curl -X POST "http://localhost:8000/twilio/voice/service?lang=en&question_index=3&full_name=Amina&dependants=3&primary_facility=Kenyatta"

# Test the same in Swahili
curl -X POST "http://localhost:8000/twilio/voice/service?lang=sw&question_index=0"
```

### Option D: Twilio Console Debugger

1. Go to **Monitor** > **Logs** > **Calls** in the Twilio Console
2. Click on a call SID to see the full webhook request/response chain
3. Each step shows the TwiML returned and any errors
4. The recording tab shows all captured audio files with playback

## 13. Testing MOSIP Identity Verification with IVR

MOSIP e-Signet uses a browser-based OpenID Connect flow (citizen is redirected to the e-Signet login page to authenticate via fingerprint/iris/face). This means identity verification **cannot happen mid-phone-call** -- it must be done via the web UI (Streamlit) or direct API calls before or after the IVR enrollment.

The practical pattern is: **verify identity on the web, then enroll via phone** (or vice versa, then link afterwards).

### Scenario A: Verify First, Then Enroll via IVR

This is the recommended flow for a registration agent assisting a citizen in person. The agent verifies identity on a laptop/tablet, then the citizen enrolls their voice by calling the IVR.

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
4. Speak the 5 enrollment phrases (speak, then go silent after each)
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

### Scenario B: Enroll via IVR First, Verify MOSIP Later

Useful when the registration agent doesn't have MOSIP access at the time of voice enrollment (e.g., field registration in a remote area). The citizen enrolls their voice over the phone, then visits a registration centre later to verify their MOSIP identity.

1. Citizen calls the IVR, enrolls with 5 voice samples (manual national ID entry)
2. Later, at a registration centre with MOSIP access:
   - Agent opens Streamlit > **Verify Identity (MOSIP)** > verifies citizen via e-Signet
   - Agent links the verified MOSIP ID to the citizen's existing record
3. The citizen's record is upgraded from `identity_verified=False` to `identity_verified=True`

### Scenario C: API-Only Testing (No Streamlit, No Phone)

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
| Enrollment says complete but no voiceprint stored | Prototype callback doesn't download recordings yet | This is expected in the prototype — the callback flow is wired, but full processing requires production deployment |
| ngrok URL changed | Free ngrok assigns a new URL on each restart | Update the Twilio webhook URL, or use a paid ngrok plan for a stable subdomain |
| Swahili TTS sounds like English | The `<Say>` voice is set to `alice` / `en-US` for all languages | For production, switch to a Swahili-capable TTS voice or use gTTS with `<Play>` instead of `<Say>` |
| Read-back summary has wrong answers | Query parameter encoding issue with special characters in names | URL-encode answer values; check ngrok inspector for the raw query string |
| MOSIP identity verification not available in IVR | By design -- e-Signet OIDC requires a web browser | Use the Streamlit UI (`/Verify Identity`) to verify via MOSIP before or after IVR enrollment |
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
"Please say: The sun rises over the mountain..."     --> [Beep] Speaks --> presses #
  |                                                       (x5 phrases)
  v
"Enrollment complete. Thank you."                    --> Hangup
  |
  =============== (later that day) ===============
  |
  v
Amina calls again, selects English, presses 2
  |
  v
"Please say: The market opens early on Wednesday."   --> [Beep] Speaks --> presses #
  |
  v
Voice biometric: 0.87 (pass) + Transcript: 87.5% (pass)
  |
  v
"Authentication complete."                           --> Redirected to consent
  |
  v
"I consent to share my health records..."            --> [Beep] "Yes" --> presses #
  |
  v
"Your consent has been recorded."                    --> Redirected to service
  |
  v
"Please say your full name."                         --> [Beep] "Amina Juma Ochieng" --> presses #
  |
  v
"How many dependants would you like to register?"    --> [Beep] "Three" --> presses #
  |
  v
"Which hospital or health centre...?"                --> [Beep] "Kenyatta National Hospital" --> presses #
  |
  v
"Thank you. I have recorded: your name is            --> [Beep] "Yes" --> presses #
 Amina Juma Ochieng, you have 3 dependants,
 and your preferred facility is Kenyatta
 National Hospital. Is this correct?"
  |
  v
"Your form is complete. Thank you for                --> Hangup
 using VeriVoice."
```
