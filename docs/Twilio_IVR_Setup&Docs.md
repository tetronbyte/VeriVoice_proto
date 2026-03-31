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
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Prerequisites

- Python 3.10+
- A [Twilio account](https://www.twilio.com/try-twilio) (free trial works)
- A Twilio phone number with **Voice** capability
- [ngrok](https://ngrok.com/) installed (for local development)
- VeriVoice backend running locally on port 8000

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
    v
2. A beep plays (playBeep="true")
    |  This signals: "start speaking now"
    v
3. Caller speaks their answer
    |  "Amina Juma Ochieng"
    v
4. Recording ENDS automatically — one of three triggers:
    |
    |── (a) SILENCE DETECTION (most common)
    |       Twilio detects ~5 seconds of silence after the
    |       caller stops talking and ends the recording.
    |       The caller does NOT need to press any button.
    |
    |── (b) MAX LENGTH HIT
    |       The recording hard-stops at maxLength seconds
    |       (we use 15s for form answers, 10s for enrollment).
    |       Prevents runaway recordings.
    |
    |── (c) KEYPRESS — # (optional fallback)
    |       By default, pressing # on the keypad ends the
    |       recording early (Twilio's default finishOnKey="#").
    |       Most callers won't need this — silence detection
    |       handles it — but it's there if they want to
    |       explicitly signal "I'm done".
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
2. **Hears a beep**
3. **Speaks their answer**
4. **Goes silent** — the system automatically picks up that they're done

There is no "press a button to submit your answer" step. This is critical for accessibility — VeriVoice targets users who may not be familiar with complex phone menu interactions. The IVR is designed so that **speaking and going silent is all you need to do**.

### Recording parameters we use

| Parameter | Value | Why |
|---|---|---|
| `maxLength` | 10–15s | Long enough for names/addresses, short enough to prevent dead air |
| `playBeep` | `true` | Clear audio cue that the system is listening |
| `trim` | `trim-silence` | Removes silence padding so Whisper gets clean audio |
| `finishOnKey` | `#` (default) | Implicit fallback — caller can press # to end early |
| `timeout` | 5s (default) | Seconds of silence before Twilio auto-stops recording |

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

**Step 4 — Enter national ID**

Twilio POSTs to: `POST /twilio/voice/enroll?lang=en&step=0`

The system plays:
> "Please enter your national ID number followed by the pound key."

Amina types her national ID on the keypad: `29384756#`

**Step 5 — First voice recording (sample 1 of 5)**

Twilio POSTs to: `POST /twilio/voice/enroll?lang=en&step=1` with `Digits=29384756`

The system plays:
> "Please say: The sun rises over the mountain every morning."

*\[Beep\]* — Amina speaks the phrase. She finishes and goes silent. After ~5 seconds of silence, Twilio automatically ends the recording and POSTs the `RecordingUrl` to the callback.

**Step 6 — Recording callback (sample 1 saved)**

Twilio POSTs to: `POST /twilio/voice/enroll/callback?lang=en&step=1&national_id=29384756` with `RecordingUrl=https://api.twilio.com/...`

The system advances to sample 2.

**Steps 7–14 — Samples 2 through 5**

The same prompt-record-callback cycle repeats for each remaining phrase. Each time:
1. System plays the phrase via `<Say>`
2. *\[Beep\]* plays
3. Amina speaks the phrase
4. She goes silent — Twilio auto-ends the recording
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

*\[Beep\]* — Amina speaks the phrase. She finishes and goes silent. Twilio detects the silence, ends the recording, and POSTs the `RecordingUrl` to the callback.

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

*\[Beep\]* — Amina says "Yes, I agree." She goes silent, Twilio auto-ends the recording.

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

*\[Beep\]* — Amina says: "Amina Juma Ochieng." She stops speaking. After ~5 seconds of silence, Twilio automatically ends the recording and fires the callback.

> **Why this question:** This is the strongest demo moment. Amina just authenticated via voiceprint, and now the system captures structured data from her speech. It shows Whisper ASR working on names — especially African names, which are non-trivial for speech recognition.

**Step 2 — Callback, advance to question 2**

Twilio POSTs to: `POST /twilio/voice/service/callback?lang=en&question_index=0` with `RecordingUrl=https://api.twilio.com/...`

The backend downloads the recording, runs it through the preprocessing pipeline, and transcribes via Whisper. The answer `"Amina Juma Ochieng"` is stored. The IVR redirects to question 2.

**Step 3 — Question 2: Dependants (numeric capture)**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=1`

> "How many dependants would you like to register?"

*\[Beep\]* — Amina says: "Three." She goes silent, recording ends automatically.

> **Why this question:** Shows the system handles different response types. When Whisper transcribes "three", the backend parses it to the number `3`. Works for both English ("three" -> 3) and Swahili ("tatu" -> 3). Proves the pipeline works beyond just capturing strings.

**Step 4 — Callback, advance to question 3**

The backend transcribes "three", parses it to `3`, and stores it. The IVR redirects to question 3.

**Step 5 — Question 3: Primary Facility (proper noun / complex response)**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=2`

> "Which hospital or health centre would you like as your primary facility?"

*\[Beep\]* — Amina says: "Kenyatta National Hospital." She goes silent, recording ends automatically.

> **Why this question:** This is the showstopper for the demo. It's a real-world question from actual health insurance enrollment (e.g., NHIF in Kenya). The answer is a proper noun (facility name), and it demonstrates Whisper handling longer, more complex spoken responses. When demoing in Swahili, this question is especially impressive.

**Step 6 — Callback, all questions answered**

The backend transcribes the facility name and stores it. All 3 answers are now collected.

**Step 7 — TTS Read-back Summary**

Twilio POSTs to: `POST /twilio/voice/service?lang=en&question_index=3` (index exceeds question count, triggers summary)

The system generates a TTS read-back of all collected answers:

> "Thank you. I have recorded: your name is Amina Juma Ochieng, you have 3 dependants, and your preferred facility is Kenyatta National Hospital. Is this correct?"

*\[Beep\]* — Amina says: "Yes." She goes silent, recording ends automatically.

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
                        |                     +-- [Beep] Record sample 1 --> [silence] auto-stop
                        |                     +-- [Beep] Record sample 2 --> [silence] auto-stop
                        |                     +-- [Beep] Record sample 3 --> [silence] auto-stop
                        |                     +-- [Beep] Record sample 4 --> [silence] auto-stop
                        |                     +-- [Beep] Record sample 5 --> [silence] auto-stop
                        |                           |
                        |                           v
                        |                     ENROLL_COMPLETE --> Hangup
                        |
                        +-- (Press 2) --> AUTH_CHALLENGE
                                              |
                                              +-- Play challenge phrase
                                              +-- [Beep] Record response --> [silence] auto-stop
                                                    |
                                                    v
                                              AUTH_RESULT --> Hangup
                                                    |
                                                    +-- (if granted) --> CONSENT_PROMPT
                                                    |                       |
                                                    |                       +-- [Beep] Record --> [silence] auto-stop
                                                    |                       |
                                                    |                       v
                                                    |                  CONSENT_RESULT
                                                    |                       |
                                                    |                       v
                                                    |                  SERVICE_QUESTION (x3)
                                                    |                       |
                                                    |                       +-- Q1: "Full name?"
                                                    |                       |   [Beep] --> speak --> [silence] auto-stop
                                                    |                       |
                                                    |                       +-- Q2: "How many dependants?"
                                                    |                       |   [Beep] --> speak --> [silence] auto-stop
                                                    |                       |
                                                    |                       +-- Q3: "Which facility?"
                                                    |                           [Beep] --> speak --> [silence] auto-stop
                                                    |                           |
                                                    |                           v
                                                    |                  TTS READ-BACK SUMMARY
                                                    |                  "Your name is X, Y dependants, facility Z.
                                                    |                   Is this correct?"
                                                    |                       |
                                                    |                       +-- [Beep] --> "Yes" --> [silence] auto-stop
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
5. Follow the voice prompts — remember, just speak and go silent. No buttons needed for voice responses.

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

## 13. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Call connects but no audio plays | Webhook URL is wrong or unreachable | Verify ngrok is running and URL matches the Twilio config |
| "Invalid Twilio signature" (403) | `TWILIO_AUTH_TOKEN` is set but the request URL doesn't match | Ensure the ngrok URL in Twilio Console matches exactly (including https) |
| "No recording received" | Caller hung up before speaking, or Twilio couldn't reach the callback | Check Twilio debugger logs for errors |
| Recording URL returns 401 | Twilio recordings require authentication to download | Use `httpx` with Twilio Basic Auth: `(ACCOUNT_SID, AUTH_TOKEN)` |
| Recording ends too early | Caller paused mid-sentence and silence detection kicked in | Increase `timeout` in `<Record>` (default 5s) or advise callers to speak continuously |
| Recording cuts off long answers | `maxLength` is too short | Increase `maxLength` (currently 15s for form answers, 10s for enrollment) |
| Caller confused about when to speak | No beep or unclear prompt | Ensure `playBeep="true"` is set; consider adding "speak after the beep" to prompts |
| Enrollment says complete but no voiceprint stored | Prototype callback doesn't download recordings yet | This is expected in the prototype — the callback flow is wired, but full processing requires production deployment |
| ngrok URL changed | Free ngrok assigns a new URL on each restart | Update the Twilio webhook URL, or use a paid ngrok plan for a stable subdomain |
| Swahili TTS sounds like English | The `<Say>` voice is set to `alice` / `en-US` for all languages | For production, switch to a Swahili-capable TTS voice or use gTTS with `<Play>` instead of `<Say>` |
| Read-back summary has wrong answers | Query parameter encoding issue with special characters in names | URL-encode answer values; check ngrok inspector for the raw query string |

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
"Please say: The sun rises over the mountain..."     --> [Beep] Speaks --> [silence] auto-stop
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
"Please say: The market opens early on Wednesday."   --> [Beep] Speaks --> [silence] auto-stop
  |
  v
Voice biometric: 0.87 (pass) + Transcript: 87.5% (pass)
  |
  v
"Authentication complete."                           --> Redirected to consent
  |
  v
"I consent to share my health records..."            --> [Beep] "Yes" --> [silence] auto-stop
  |
  v
"Your consent has been recorded."                    --> Redirected to service
  |
  v
"Please say your full name."                         --> [Beep] "Amina Juma Ochieng" --> [silence] auto-stop
  |
  v
"How many dependants would you like to register?"    --> [Beep] "Three" --> [silence] auto-stop
  |
  v
"Which hospital or health centre...?"                --> [Beep] "Kenyatta National Hospital" --> [silence] auto-stop
  |
  v
"Thank you. I have recorded: your name is            --> [Beep] "Yes" --> [silence] auto-stop
 Amina Juma Ochieng, you have 3 dependants,
 and your preferred facility is Kenyatta
 National Hospital. Is this correct?"
  |
  v
"Your form is complete. Thank you for                --> Hangup
 using VeriVoice."
```
