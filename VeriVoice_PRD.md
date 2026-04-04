**VeriVoice**

Privacy-Preserving Voice Authentication & Audio Consent

Infrastructure for Inclusive Digital Public Services

**Product Requirements Document (PRD)**

Prototype Stage

Document Version: 1.1

Date: April 2026

1\. Executive Summary

Sub-Saharan Africa's Digital Public Infrastructure (DPI) faces a
critical last-mile accessibility crisis. The majority of rural East
Africans (60--70% low-literacy, 70% feature-phone users) are
systematically excluded from critical public services including cash
transfers, health insurance enrolment, and refugee aid distribution.
Existing biometric authentication methods (fingerprint readers and
facial recognition) routinely fail under real-world field conditions
such as dusty sensors and poor lighting. Text-heavy consent and
authentication flows require smartphone literacy that most rural
beneficiaries do not possess.

VeriVoice is a privacy-preserving voice authentication and audio consent
infrastructure layer designed to solve these problems without replacing
existing national identity systems. Rather than building a parallel
identity framework, VeriVoice augments proven DPI rails (Kenya's Huduma
Namba, Uganda's Ndaga Muntu) by anchoring a governed voice biometric
layer to the existing national ID as its single source of truth. Through
integration with MOSIP e-Signet (an OpenID Connect identity provider
built on the MOSIP national ID platform), VeriVoice can cryptographically
verify a citizen's identity against the national ID system before linking
their voice biometric — replacing manual ID entry with federated,
biometric-verified identity anchoring.

During enrollment, voice samples are converted into encrypted
mathematical voice templates (never storing raw audio), preserving
privacy while enabling secure authentication. Citizens authenticate via
a short-code call from any GSM feature phone through an IVR interface,
speak a randomised challenge phrase, and receive service authorisation
through a two-stage verification process (voice biometric match + phrase
transcript match) that requires no smartphone, no data connection, and
no literacy.

2\. Problem Statement

Rural East Africans face massive drop-offs in DPI services like cash
transfers, health insurance, and refugee aid due to text-heavy
authentication and consent flows that require smartphone literacy. Rural
realities (GSM audio degradation, market noise, voice instability from
illness/pregnancy) plus cultural recording distrust exclude them
systematically. Meanwhile, refugee/host double-dipping wastes aid
resources. Current fingerprint/face recognition fails on dusty sensors
and in poor lighting conditions; no accessible alternative exists.

3\. Proposed Solution

VeriVoice enhances the security and inclusivity of existing Digital ID
registries by adding a governed, privacy-preserving voice authentication
and consent layer anchored to the national ID. Voice functions as an
optional authentication and consent interface cryptographically bound to
an existing ID number in the national registry. With MOSIP e-Signet
integration, the identity anchor is no longer a self-reported national ID
number — it is a cryptographically verified identity obtained through
MOSIP's OpenID Connect flow, where the citizen authenticates via
traditional biometrics (fingerprint, iris, or face) against the national
ID system before their voice biometric is enrolled.

3.1 Core Value Proposition

-   **Accessibility:** Dramatically reduces DPI service drop-off rates
    among rural and low-literacy populations by using voice as the
    primary interface.

-   **Fraud Prevention:** Prevents cross-registry aid fraud through
    privacy-preserving voice deduplication (the Janus module, planned
    for MVP).

-   **Auditable Consent:** Transforms spoken consent into
    cryptographically signed, auditable, purpose-limited DPI consent
    tokens.

-   **Privacy by Design:** Homomorphic encryption ensures voice matching
    occurs on encrypted data without exposing biometric templates.

4\. User Stories

-   **Rural Citizen:** "As a rural citizen, I want to authenticate
    myself using my voice so I can access government services without
    needing a smartphone or literacy skills."

-   **Refugee/Aid Recipient:** "As a refugee or aid recipient, I want a
    secure way to verify my identity so I can receive benefits without
    fraud or duplicate registrations."

-   **Government Service Provider:** "As a government service provider,
    I want a reliable authentication system so benefits reach the
    correct individuals while preventing fraud."

-   **Low-Literacy User:** "As a low-literacy user, I want to interact
    with the system through voice instructions so I can use digital
    services without reading complex text interfaces."

-   **Registration Agent (MOSIP):** "As a registration agent, I want to
    verify a citizen's identity against the national ID system using MOSIP
    e-Signet before enrolling their voice, so I can be sure the voice
    biometric is anchored to a verified identity rather than a
    self-reported ID number."

5\. System Use Cases

5.1 Voice Enrollment (One-Time Registration)

A citizen enrols their voice through an assisted registration centre.
Identity is established in one of two ways: (a) manual national ID entry
(legacy flow), or (b) MOSIP e-Signet identity verification, where the
citizen authenticates via MOSIP biometrics (fingerprint/iris/face) through
an OIDC flow and receives a cryptographically verified MOSIP individual
ID. A set of 5 voice samples are recorded. In the IVR (phone) flow, the
system plays a randomly selected phrase via TTS for each sample and the
citizen repeats it (pressing # to stop recording). In the Streamlit (web)
flow, the citizen uploads their own pre-recorded audio files — no specific
phrase is required. The system converts each audio into a 192-dimensional
speaker embedding using ECAPA-TDNN,
computes an L2-normalised centroid, encrypts the centroid using Paillier
homomorphic encryption, and stores the ciphertext in SQLite. No raw
audio is stored. When enrolled via e-Signet, the citizen's record is
marked as identity-verified with their MOSIP individual ID linked.

5.1.1 MOSIP e-Signet Identity Verification

Before or after voice enrollment, a citizen's identity can be verified
against the MOSIP national ID system via the e-Signet OpenID Connect flow:

1.  VeriVoice redirects the citizen to the e-Signet authorization endpoint.
2.  The citizen authenticates on the e-Signet page using MOSIP biometrics
    (fingerprint, iris, or face) captured via an SBI-compliant device
    (Mock MDS in development, real hardware in production).
3.  e-Signet redirects back to VeriVoice with an authorization code.
4.  VeriVoice exchanges the code for an id_token at e-Signet's token
    endpoint.
5.  VeriVoice validates the JWT signature against MOSIP's public key (JWKS).
6.  The verified `sub` claim (MOSIP individual_id) is extracted and linked
    to the citizen's record, setting `identity_verified=True`.

5.2 Voice Authentication (Every-Time Access)

The user calls the system or uses the Streamlit web UI. The IVR plays a
random challenge phrase via gTTS. The user speaks the phrase back.
Authentication proceeds in two stages:

1.  **Voice Biometric Match:** The live audio is processed through
    ECAPA-TDNN to generate a 192-dim embedding. This embedding is
    scalar-multiplied with the Paillier HE-encrypted stored centroid.
    The homomorphic dot product is decrypted and compared against a
    configurable threshold (default: 0.45).

2.  **Phrase Transcript Match:** The spoken audio is transcribed using
    the language-appropriate ASR model — Whisper large-v3 for English,
    w2v-BERT 2.0 (`badrex/w2v-bert-2.0-swahili-as`) for Swahili. The
    transcription is compared against the original challenge phrase to
    verify the user said the correct words.

Both stages must pass for the authentication to be granted.

5.3 Voice Consent

After authentication, the system reads consent information to the user
using gTTS (text-to-speech) in their preferred language. The user
responds verbally (e.g., "Yes"). The system follows the same voice
authentication pipeline to verify the speaker is the enrolled citizen,
then generates a cryptographically signed consent token using Ed25519
(PyNaCl) and stores it in the database.

5.4 Service Access Simulation

After authentication and consent, the user accesses a simulated health
insurance form. The system uses gTTS to play each question's audio and
the language-appropriate ASR model (Whisper for English, w2v-BERT for
Swahili) for speech-to-text to capture the user's spoken answers.
Voice re-authentication is performed once at the start of the session
(not per reply). The form responses are captured and stored.

6\. Prototype Scope

6.1 In-Scope (Prototype)

-   Voice enrollment with 5 spoken phrases per user

-   ECAPA-TDNN 192-dim speaker embedding extraction

-   L2-normalised centroid computation from enrollment embeddings

-   Paillier HE encryption of centroid (2048-bit keys)

-   HE-encrypted voice matching with cosine similarity scoring

-   Dual ASR: Whisper large-v3 for English, w2v-BERT 2.0
    (`badrex/w2v-bert-2.0-swahili-as`) for Swahili speech-to-text

-   gTTS for text-to-speech playback (challenge phrases, consent text,
    form questions)

-   Random challenge phrase generation and transcript matching

-   Ed25519 consent token generation and storage (to be built)

-   Simulated health insurance form with voice-driven Q&A

-   Dual frontend: Twilio Voice API/IVR (primary) + Streamlit Web UI
    (audio upload)

-   SQLite database for all persistent storage

-   Audio preprocessing pipeline (highpass filter, DC removal, spectral
    subtraction, pre-emphasis, VAD)

-   MOSIP e-Signet OIDC integration for identity verification against
    the national ID system (authorization code flow, JWT validation)

-   MOSIP Mock MDS (Mock Device Service) for simulating biometric
    capture devices (fingerprint, iris, face) during development

-   Identity-verified enrollment: linking voice biometrics to a
    MOSIP-verified identity instead of self-reported national ID

-   Streamlit UI page for e-Signet identity verification flow

6.2 Out-of-Scope (Deferred to MVP)

-   Janus refugee deduplication module

-   AfroDigits dataset integration for noise filtering

-   SMS OTP as a third authentication factor

-   Anti-spoofing / deepfake detection

-   MakerereNLP ASR integration

-   Migration to Supabase / PostgreSQL

-   Production deployment to AWS

-   Multi-telco architecture and solar CSC nodes

-   Continuous fairness auditing across dialects

7\. System Architecture

7.1 Architecture Overview

The system follows a modular, layered architecture with clear separation
between the interface layer, the API/orchestration layer, the ML/crypto
processing layer, and the data persistence layer.

7.1.1 High-Level Data Flow

Enrollment (Manual ID): Audio capture → Preprocessing → ECAPA-TDNN
(192-dim embedding) → L2-normalise → Centroid from 5 embeddings →
Paillier HE encrypt → Store ciphertext in SQLite

Enrollment (e-Signet Verified): Citizen authenticates via e-Signet OIDC
(MOSIP biometrics) → receives verified id_token with MOSIP individual_id
→ VeriVoice links voice enrollment to verified identity → same voice
pipeline as above → citizen record marked identity_verified=True

Authentication: Audio capture → Preprocessing → ECAPA-TDNN (192-dim
embedding) → L2-normalise → HE scalar multiply with stored ciphertext →
Decrypt dot product → Score vs. threshold + ASR transcript (Whisper for
English, w2v-BERT for Swahili) vs. challenge phrase → Verdict

MOSIP e-Signet Identity Verification: VeriVoice redirects to e-Signet
/authorize → citizen authenticates via MOSIP biometrics (SBI device /
Mock MDS) → e-Signet returns authorization code → VeriVoice exchanges
code for id_token → validates JWT → extracts verified sub
(individual_id) → links to CITIZEN record

Consent: gTTS plays consent text → User speaks response → Voice auth
pipeline → Ed25519 sign consent token → Store token

Service Access: gTTS plays form questions → ASR transcribes
answers → Store responses

7.2 Tech Stack (Prototype)

  --------------------- -------------------------------------------------
  **Component**         Technology

  **Core Language**     Python 3.10+

  **Web Framework**     FastAPI + Uvicorn

  **Frontend            Twilio Voice API / IVR
  (Primary)**           

  **Frontend            Streamlit (Web UI for audio file upload)
  (Secondary)**         

  **Speaker             ECAPA-TDNN via SpeechBrain
  Verification**        (speechbrain/spkrec-ecapa-voxceleb)

  **Speech-to-Text      OpenAI Whisper large-v3 (English);
  (ASR)**               w2v-BERT 2.0 badrex/w2v-bert-2.0-swahili-as (Swahili)

  **Text-to-Speech      gTTS (Google Text-to-Speech)
  (TTS)**               

  **Homomorphic         python-paillier (2048-bit keys)
  Encryption**          

  **Digital             PyNaCl (Ed25519)
  Signatures**          

  **Database**          SQLite (via SQLAlchemy ORM + Alembic migrations)

  **Caching / Session** Redis

  **Audio Processing**  librosa, scipy, numpy, torchaudio

  **ML Runtime**        PyTorch (CPU/CUDA)

  **Identity            MOSIP e-Signet (OpenID Connect identity provider)
  Provider**

  **Mock Biometric      MOSIP Mock MDS (SBI protocol, Java 11+)
  Devices**

  **OIDC Client**       authlib + httpx (OIDC authorization code flow)

  **Infrastructure**    Vercel (hosting)
  --------------------- -------------------------------------------------

8\. Project Application Structure

The codebase follows an OOP-based modular architecture. Individual
components (ECAPA embedding, Paillier encryption, Whisper transcription)
have been tested independently and will be integrated via class-based
service modules.

**verivoice/**

├── app/

│ ├── main.py \# FastAPI application entry point

│ ├── config.py \# Configuration (thresholds, paths, keys)

│ ├── models/

│ │ ├── \_\_init\_\_.py

│ │ ├── citizen.py \# Citizen SQLAlchemy model

│ │ ├── voice_template.py \# VoiceTemplate model (HE ciphertext)

│ │ ├── auth_event.py \# AuthEvent model

│ │ └── consent_token.py \# ConsentToken model

│ ├── schemas/

│ │ ├── \_\_init\_\_.py

│ │ ├── enrollment.py \# Pydantic request/response schemas

│ │ ├── authentication.py

│ │ ├── consent.py

│ │ └── mosip.py \# e-Signet OIDC request/response schemas

│ ├── routers/

│ │ ├── \_\_init\_\_.py

│ │ ├── enrollment.py \# POST /api/v1/enroll

│ │ ├── authentication.py \# POST /api/v1/authenticate

│ │ ├── consent.py \# POST /api/v1/consent

│ │ ├── service.py \# POST /api/v1/service-access

│ │ └── mosip.py \# e-Signet OIDC endpoints (authorize, callback, link)

│ ├── services/

│ │ ├── \_\_init\_\_.py

│ │ ├── audio_preprocessor.py \# AudioPreprocessor class

│ │ ├── embedding_service.py \# EmbeddingService (ECAPA-TDNN wrapper)

│ │ ├── encryption_service.py \# EncryptionService (Paillier HE)

│ │ ├── matching_service.py \# MatchingService (HE dot product +
threshold)

│ │ ├── transcription_service.py \# TranscriptionService (Whisper + w2v-BERT)

│ │ ├── tts_service.py \# TTSService (gTTS wrapper)

│ │ ├── consent_service.py \# ConsentService (Ed25519 signing)

│ │ ├── challenge_service.py \# ChallengeService (random phrase gen +
match)

│ │ └── mosip_service.py \# MosipService (e-Signet OIDC client)

│ ├── db/

│ │ ├── \_\_init\_\_.py

│ │ ├── database.py \# SQLAlchemy engine + session factory

│ │ └── crud.py \# CRUD operations

│ └── utils/

│ ├── \_\_init\_\_.py

│ ├── audio_utils.py \# Audio loading, format conversion

│ └── security.py \# Key management helpers

├── streamlit_app/

│ └── app.py \# Streamlit UI for audio upload demo

├── twilio_integration/

│ ├── webhook_handler.py \# Twilio voice webhook endpoints

│ └── ivr_flow.py \# IVR call flow logic

├── MOSIP_eSignet/ \# MOSIP e-Signet integration tooling (external, Java)

│ ├── collab-mock-mds-reg/ \# Mock MDS for Registration (SBI device sim)

│ │ └── target/ \# Pre-built JAR + classes + biometric profiles

│ └── collab-mock-mds-auth/ \# Mock MDS for Auth (SBI device sim)

│   └── target/ \# Pre-built JAR + classes + biometric profiles

├── migrations/

│ └── alembic/ \# Alembic migration scripts

├── tests/

│ ├── test_preprocessor.py

│ ├── test_embedding.py

│ ├── test_encryption.py

│ ├── test_matching.py

│ ├── test_transcription.py

│ └── test_mosip.py \# e-Signet OIDC integration tests

├── pretrained_ecapa/ \# Downloaded ECAPA-TDNN model weights

├── requirements.txt

├── alembic.ini

├── .env

└── README.md

9\. Core Backend Modules (Detailed)

9.1 AudioPreprocessor

Responsible for all audio loading and cleaning before embedding
extraction. Implemented as a class with configurable parameters.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Sample Rate**       16,000 Hz (required by ECAPA-TDNN)

  **Filter Mode**       Configurable: GSM bandpass (300--3400 Hz) or 80
                        Hz highpass (default)

  **DC-Offset Removal** Subtract mean from audio signal

  **Spectral            Safety-checked: only applied if first 0.3s RMS \<
  Subtraction**         0.05 (quiet noise floor)

  **Pre-emphasis**      Coefficient 0.97 (α = 0.97)

  **Peak                Normalise to 0.95 peak amplitude; reject silent
  Normalisation**       files (peak \< 1e-6)

  **VAD**               librosa.effects.split with top_db=25; concatenate
                        voiced segments

  **Min Duration**      Pad to 1 second minimum if shorter after VAD

  **Output**            Float32 numpy array at 16 kHz
  --------------------- -------------------------------------------------

Note: AfroDigits dataset noise filtering is excluded from the prototype.
The current pipeline uses librosa-based spectral subtraction as the
noise reduction mechanism.

9.2 EmbeddingService

Wraps the SpeechBrain ECAPA-TDNN model for speaker embedding extraction.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Model**             speechbrain/spkrec-ecapa-voxceleb

  **Output Dimension**  192

  **Normalisation**     L2-norm (reject zero-norm vectors, threshold
                        1e-8)

  **Device**            Auto-detect: CUDA if available, else CPU

  **Centroid Build**    Mean of N enrollment embeddings → L2-normalise

  **Singleton Loading** Model loaded once, reused across requests
  --------------------- -------------------------------------------------

9.3 EncryptionService

Handles Paillier homomorphic encryption for storing and matching voice
templates without decrypting the enrolled biometric.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Library**           python-paillier (phe)

  **Key Size**          2048-bit

  **Key Storage**       JSON file (prototype); HSM/KMS for MVP

  **Encrypt**           Dimension-wise encryption of each float in the
                        centroid vector

  **Ciphertext Format** JSON blob: {n, dim, ciphertexts: \[{c, exp},
                        \...\]}

  **Stored As**         BYTEA / BLOB in VOICE_TEMPLATE.he_ciphertext

  **Memory Safety**     Plaintext centroid zeroed from memory after
                        encryption
  --------------------- -------------------------------------------------

9.4 MatchingService

Performs the homomorphic encrypted dot product between a live embedding
and the stored encrypted centroid, then decrypts and scores.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Operation**         live_i × Enc(enrolled_i) for each of 192 dims,
                        then homomorphic sum

  **Decryption**        Private key decrypts the summed ciphertext → raw
                        cosine score

  **Score Clipping**    Clip to \[-1.0, 1.0\]

  **Threshold**         Default 0.45 (configurable; tuning deferred to
                        MVP)

  **Verdict**           score ≥ threshold → "granted"; else "denied"

  **Memory Safety**     Live embedding zeroed after matching
  --------------------- -------------------------------------------------

9.5 TranscriptionService

Dual-backend ASR service that routes to the best model for the detected
language. Used for both the challenge phrase transcript match and for
capturing form answers during service access.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **English Model**     OpenAI Whisper large-v3

  **Swahili Model**     w2v-BERT 2.0 (badrex/w2v-bert-2.0-swahili-as) —
                        CTC model fine-tuned on Swahili speech, outperforms
                        Whisper large-v3 on Swahili

  **Routing**           language="sw" → w2v-BERT; all other languages → Whisper

  **Input**             Audio file path or numpy array (16 kHz, mono, float32)

  **Output**            Transcribed text string

  **Use Cases**         Challenge phrase verification, consent response
                        capture, form answer capture

  **Singleton**         Both models are lazy-loaded on first use and reused
  --------------------- -------------------------------------------------

9.6 TTSService

Uses gTTS (Google Text-to-Speech) to generate audio from text for IVR
playback.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Library**           gTTS

  **Use Cases**         Challenge phrase audio, consent text playback,
                        form question playback

  **Language Support**  Multiple languages via gTTS lang parameter (e.g.,
                        "en", "sw")

  **Output Format**     MP3 audio file, convertible to WAV for processing
  --------------------- -------------------------------------------------

9.7 ChallengeService

Generates random challenge phrases and validates transcription matches.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Phrase Pool**       Predefined set of simple, language-appropriate
                        phrases

  **Selection**         Random selection from pool per authentication
                        attempt

  **Matching**          Normalised string comparison (lowercase, strip
                        punctuation) between expected phrase and Whisper
                        transcription

  **Language**          Phrases available in user's preferred language
  --------------------- -------------------------------------------------

9.8 ConsentService (To Be Built)

Generates cryptographically signed consent tokens after a citizen
verbally confirms consent.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Library**           PyNaCl (Ed25519)

  **Signing Key**       Ed25519 private key (prototype: file-based; MVP:
                        secure key store)

  **Token Payload**     citizen_id, ministry_code, data_scope, issued_at
                        timestamp

  **Signature**         Ed25519 signature over serialised payload

  **Storage**           CONSENT_TOKEN table in SQLite

  **Revocation**        is_revoked boolean flag on the token record

  **Status**            Not yet implemented. Module structure and
                        interface defined; implementation pending.
  --------------------- -------------------------------------------------

9.9 MosipService

Handles the OpenID Connect integration with MOSIP e-Signet for
identity verification.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Protocol**          OpenID Connect 1.0 (Authorization Code Flow)

  **Library**           authlib (OIDC client), httpx (HTTP transport)

  **Identity Provider** MOSIP e-Signet

  **Key Operations**    Build authorize URL, exchange authorization code
                        for tokens, validate id_token JWT, extract
                        verified individual_id

  **JWT Validation**    Fetch JWKS from e-Signet, verify signature +
                        expiry + audience + nonce

  **State Management**  OIDC state and nonce stored in Redis with 5-min
                        TTL to prevent replay attacks

  **Output**            Verified MOSIP individual_id (the `sub` claim
                        from the id_token)

  **Dev Environment**   Uses MOSIP collab environment
                        (esignet.collab.mosip.net); Mock MDS simulates
                        biometric devices on localhost:4501-4600
  --------------------- -------------------------------------------------

9.10 MOSIP Mock MDS (External Dependency)

Pre-built Java services that simulate biometric capture devices during
development. Not part of the Python codebase — run as separate processes.

  --------------------- -------------------------------------------------
  **Property**          Detail

  **Language**          Java 11+

  **Protocol**          MOSIP SBI (Secure Biometric Interface)

  **Modalities**        Fingerprint (slap/single), Iris
                        (binocular/monocular), Face

  **Registration MDS**  Supports RCAPTURE + STREAM for enrollment
                        biometric capture

  **Auth MDS**          Supports CAPTURE for authentication biometric
                        capture

  **Ports**             4501-4600 on 127.0.0.1

  **Profiles**          Default and Automatic biometric data profiles
                        with ISO 19794-compliant data

  **Admin APIs**        /admin/score, /admin/delay, /admin/status,
                        /admin/profile for runtime tuning

  **Collab Server**     Connects to api-internal.collab.mosip.net for
                        auth tokens and IDA certificates
  --------------------- -------------------------------------------------

10\. API Layer

The backend exposes a RESTful API via FastAPI, served by Uvicorn. All
endpoints accept multipart/form-data for audio file uploads and return
JSON responses.

10.1 API Endpoints

POST /api/v1/enroll

  --------------------- -------------------------------------------------
  **Description**       Enroll a new citizen with voice samples

  **Request Body**      national_id_number (string), preferred_language
                        (string), phone_number (string), audio_files (5
                        audio files, multipart)

  **Processing**        1\. Create CITIZEN record 2. Preprocess each
                        audio 3. Extract 5 ECAPA-TDNN embeddings 4.
                        Compute L2-normalised centroid 5. Paillier HE
                        encrypt centroid 6. Store ciphertext in
                        VOICE_TEMPLATE

  **Response**          {citizen_id, enrolled_at, template_id, status:
                        "enrolled"}

  **Errors**            400: Missing/invalid audio files; 409: National
                        ID already enrolled
  --------------------- -------------------------------------------------

POST /api/v1/authenticate

  --------------------- -------------------------------------------------
  **Description**       Authenticate a citizen via voice biometric +
                        phrase match

  **Request Body**      citizen_id or national_id_number (string),
                        audio_file (1 audio file, multipart),
                        challenge_phrase_id (string)

  **Processing**        1\. Retrieve stored HE ciphertext 2. Preprocess
                        audio 3. Extract ECAPA-TDNN embedding 4. HE match
                        (scalar multiply + decrypt) 5. ASR transcribe
                        (Whisper/w2v-BERT) 6. Compare transcript vs. challenge
                        phrase 7. Both must pass

  **Response**          {event_id, voice_match_score, transcript_match:
                        bool, result: "granted"/"denied",
                        event_timestamp}

  **Errors**            404: Citizen not found; 400: Invalid audio
  --------------------- -------------------------------------------------

GET /api/v1/challenge

  --------------------- -------------------------------------------------
  **Description**       Get a random challenge phrase for authentication

  **Query Params**      language (string, optional, default: "en")

  **Response**          {challenge_id, phrase_text, audio_url
                        (gTTS-generated audio)}
  --------------------- -------------------------------------------------

POST /api/v1/consent

  --------------------- -------------------------------------------------
  **Description**       Record voice consent and generate signed token

  **Request Body**      citizen_id (string), ministry_code (string),
                        data_scope (string), audio_file (1 audio file,
                        multipart)

  **Processing**        1\. Voice auth pipeline (verify speaker) 2.
                        ASR (Whisper/w2v-BERT) to confirm "Yes" / affirmative 3.
                        Ed25519 sign consent payload 4. Store
                        CONSENT_TOKEN

  **Response**          {token_id, ministry_code, data_scope, issued_at,
                        digital_signature (hex)}

  **Status**            Endpoint defined; Ed25519 signing implementation
                        pending
  --------------------- -------------------------------------------------

POST /api/v1/service-access

  --------------------- -------------------------------------------------
  **Description**       Simulated health insurance form with voice Q&A

  **Request Body**      citizen_id (string), consent_token_id (string),
                        audio_file (1 audio file per question, multipart)

  **Processing**        1\. Verify valid consent token 2. gTTS generates
                        question audio 3. ASR (Whisper/w2v-BERT) transcribes user
                        answer 4. Store response

  **Response**          {form_id, question, transcribed_answer, status}
  --------------------- -------------------------------------------------

GET /api/v1/mosip/authorize

  --------------------- -------------------------------------------------
  **Description**       Initiate MOSIP e-Signet OIDC login flow

  **Query Params**      None (state and nonce generated server-side)

  **Processing**        1\. Generate cryptographic state and nonce 2.
                        Store in Redis with 5-min TTL 3. Build e-Signet
                        authorize URL

  **Response**          {authorize_url, state}

  **Notes**             Client should redirect user to authorize_url
  --------------------- -------------------------------------------------

GET /api/v1/mosip/callback

  --------------------- -------------------------------------------------
  **Description**       e-Signet OIDC callback after citizen
                        authentication

  **Query Params**      code (string), state (string)

  **Processing**        1\. Validate state against Redis 2. Exchange code
                        for id_token at e-Signet /token endpoint 3.
                        Validate JWT signature via JWKS 4. Validate
                        nonce, expiry, audience 5. Extract verified sub
                        (MOSIP individual_id)

  **Response**          {mosip_individual_id, identity_verified: true,
                        linked_citizen_id (if already linked)}

  **Errors**            400: Invalid state or code; 401: JWT validation
                        failed
  --------------------- -------------------------------------------------

POST /api/v1/mosip/link

  --------------------- -------------------------------------------------
  **Description**       Link verified MOSIP identity to existing citizen

  **Request Body**      citizen_id (string), mosip_individual_id (string)

  **Processing**        1\. Verify citizen exists 2. Verify
                        mosip_individual_id was obtained from a valid
                        e-Signet callback 3. Set mosip_individual_id and
                        identity_verified=True on citizen record

  **Response**          {citizen_id, mosip_individual_id,
                        identity_verified: true, linked_at}

  **Errors**            404: Citizen not found; 409: MOSIP ID already
                        linked to another citizen; 400: Invalid session
  --------------------- -------------------------------------------------

10.2 Twilio Voice Webhook Endpoints

For the IVR/telephony interface, FastAPI also exposes TwiML-compatible
webhook endpoints that Twilio calls during a voice session:

-   **/twilio/voice/welcome:** Plays the welcome message (gTTS audio),
    prompts language selection.

-   **/twilio/voice/enroll:** Plays 5 randomly selected phrases via TTS;
    the caller repeats each one and presses # to stop recording.

-   **/twilio/voice/authenticate:** Plays the challenge phrase, records
    the user's spoken response, triggers the dual-stage auth pipeline.

-   **/twilio/voice/consent:** Reads consent text, records affirmation,
    triggers consent service.

-   **/twilio/voice/service:** Plays form questions sequentially,
    records answers.

11\. Database System

11.1 Database Choice

The prototype uses SQLite for simplicity and zero-configuration
deployment. SQLAlchemy ORM is used for data access, with Alembic for
schema migrations. This ensures a clean migration path to
PostgreSQL/Supabase in the MVP stage without changing application code.

11.2 Entity Relationship Diagram

The data model consists of four core tables with the following
relationships:

-   **CITIZEN → VOICE_TEMPLATE:** One-to-many. A citizen can have
    multiple voice templates (e.g., re-enrollment). Only the latest
    active template (is_active = true) is used for matching.

-   **CITIZEN → AUTH_EVENT:** One-to-many. Every authentication attempt
    (granted or denied) is logged with score and timestamp.

-   **CITIZEN → CONSENT_TOKEN:** One-to-many. Each consent event
    produces a separate signed token, which can be independently
    revoked.

11.3 Table Definitions

CITIZEN

  -------------------- ------------ --------- ------------------------------------
  **Column**           **Type**     **Key**   **Notes**

  citizen_id           UUID         PK        Auto-generated unique identifier

  national_id_number   VARCHAR      UQ        Manually entered or obtained from
                                              MOSIP e-Signet

  mosip_individual_id  VARCHAR      NULLABLE  Verified MOSIP subject ID from
                                              e-Signet id_token (sub claim)

  identity_verified    BOOLEAN                Default False; True when linked
                                              via e-Signet OIDC flow

  preferred_language   VARCHAR                ISO 639-1 code (e.g., "sw", "en")

  phone_number         VARCHAR                Citizen's GSM phone number

  enrolled_at          TIMESTAMP              UTC timestamp of enrollment
  -------------------- ------------ --------- ------------------------------------

VOICE_TEMPLATE

  ---------------- ------------ --------- ------------------------------------
  **Column**       **Type**     **Key**   **Notes**

  template_id      UUID         PK        Auto-generated

  citizen_id       UUID         FK        References CITIZEN.citizen_id

  he_ciphertext    BLOB                   Paillier HE-encrypted 192-dim
                                          centroid (JSON serialised)

  is_active        BOOLEAN                Only one active template per citizen

  created_at       TIMESTAMP              UTC timestamp
  ---------------- ------------ --------- ------------------------------------

AUTH_EVENT

  ------------------- ------------ --------- ------------------------------------
  **Column**          **Type**     **Key**   **Notes**

  event_id            UUID         PK        Auto-generated

  citizen_id          UUID         FK        References CITIZEN.citizen_id

  voice_match_score   FLOAT                  Cosine similarity score from HE
                                             match

  result              VARCHAR                "granted" or "denied"

  event_timestamp     TIMESTAMP              UTC timestamp of auth attempt
  ------------------- ------------ --------- ------------------------------------

CONSENT_TOKEN

  ------------------- ------------ --------- ------------------------------------
  **Column**          **Type**     **Key**   **Notes**

  token_id            UUID         PK        Auto-generated

  citizen_id          UUID         FK        References CITIZEN.citizen_id

  ministry_code       VARCHAR                Code identifying the
                                             ministry/service (e.g., "MOH")

  data_scope          VARCHAR                What data the consent covers (e.g.,
                                             "health_records")

  digital_signature   BLOB                   Ed25519 signature bytes

  issued_at           TIMESTAMP              UTC timestamp of consent

  is_revoked          BOOLEAN                Flag for consent revocation
  ------------------- ------------ --------- ------------------------------------

12\. Security & Cryptography

12.1 Homomorphic Encryption (Paillier)

Paillier HE is an additively homomorphic encryption scheme. It allows
scalar multiplication and addition operations on encrypted data without
decrypting it. In VeriVoice, the enrolled voice centroid is encrypted
dimension-wise. During authentication, the live embedding's scalar
values are multiplied with each encrypted dimension, and the products
are homomorphically summed. Only the final dot product is decrypted to
produce the cosine similarity score.

-   **Why Paillier:** Allows matching without exposing the stored
    biometric template. Even the server performing the match never sees
    the enrolled voiceprint.

-   **Key Management (Prototype):** Keys stored as JSON file on disk.
    The public key (n) is used for encryption; the private key (p, q) is
    used only for decryption after matching.

-   **Key Management (MVP):** Migrate to HSM or cloud KMS for secure key
    storage.

12.2 Digital Signatures (Ed25519)

Consent tokens are signed using Ed25519 (via PyNaCl) to ensure integrity
and non-repudiation. The signed payload includes the citizen ID,
ministry code, data scope, and timestamp. Anyone with the public
verification key can verify the token is authentic and untampered.

Implementation status: To be built. The ConsentService module interface
is defined in the project structure; the Ed25519 signing logic is
pending implementation.

12.3 Memory Safety Practices

-   Plaintext centroid vectors are zeroed from memory immediately after
    Paillier encryption.

-   Live embeddings are zeroed from memory after matching is complete.

-   No raw audio is stored in the database at any point; only encrypted
    embeddings persist.

13\. Audio Preprocessing Pipeline

All audio inputs pass through the following sequential processing stages
before embedding extraction. The pipeline is implemented in the
AudioPreprocessor class and has been independently tested.

3.  **Audio Loading:** librosa loads audio at 16 kHz, mono, float32.

4.  **Frequency Filtering:** Configurable: GSM mode applies a 4th-order
    Butterworth bandpass (300--3400 Hz); default mode applies an 80 Hz
    highpass filter. Applied via scipy.signal.filtfilt (zero-phase).

5.  **DC-Offset Removal:** Subtract the mean of the audio signal.

6.  **Spectral Subtraction:** If the first 0.3 seconds have an RMS below
    0.05 (indicating a quiet noise floor), compute a noise profile from
    that segment and subtract 1.5× the noise magnitude from the full
    STFT. This avoids aggressive noise removal on already-clean audio.

7.  **Pre-emphasis:** Apply a pre-emphasis filter with coefficient α =
    0.97 to boost high-frequency content.

8.  **Peak Normalisation:** Scale the signal to 0.95 peak amplitude.
    Reject files where peak amplitude is below 1e-6 (silent files).

9.  **Voice Activity Detection:** Use librosa.effects.split with
    top_db=25 to identify voiced segments. Concatenate only the voiced
    intervals, removing internal silence gaps.

10. **Minimum Duration Padding:** If the resulting audio is shorter than
    1 second (16,000 samples), zero-pad to 1 second.

14\. Authentication Flow (Detailed)

14.1 Enrollment Flow

14.1.1 Manual ID Enrollment (Legacy)

11. Agent enters citizen's national ID number, preferred language, and
    phone number.

12. System creates a CITIZEN record in SQLite (identity_verified=False).

13. Citizen provides 5 voice samples. In the IVR flow, the system plays
    a randomly selected phrase via TTS for each sample and the citizen
    repeats it (pressing # to stop). In the Streamlit flow, the citizen
    uploads their own pre-recorded audio files (no specific phrase required).

14. Each audio file is preprocessed by AudioPreprocessor.

15. Each preprocessed audio is passed through ECAPA-TDNN to produce a
    192-dim embedding.

16. Each embedding is L2-normalised.

17. A centroid is computed as the mean of the 5 normalised embeddings,
    then L2-normalised again.

18. The centroid is encrypted dimension-wise using Paillier HE (2048-bit
    public key).

19. The plaintext centroid is zeroed from memory.

20. The encrypted ciphertext is stored in the VOICE_TEMPLATE table.

21. An enrollment confirmation is returned.

14.1.2 e-Signet Verified Enrollment

1.  Agent initiates e-Signet identity verification via VeriVoice UI.

2.  VeriVoice redirects to e-Signet /authorize endpoint (generates
    cryptographic state + nonce, stores in Redis).

3.  Citizen authenticates on e-Signet page using MOSIP biometrics
    (fingerprint/iris/face via SBI device or Mock MDS in dev).

4.  e-Signet redirects back to VeriVoice /api/v1/mosip/callback with
    authorization code.

5.  VeriVoice exchanges code for id_token, validates JWT, extracts
    verified MOSIP individual_id.

6.  System creates CITIZEN record with mosip_individual_id set and
    identity_verified=True (or links to existing citizen).

7.  Steps 13--21 above (voice enrollment pipeline) proceed as normal.

8.  Enrollment confirmation includes identity_verified=True status.

14.2 Authentication Flow

22. User initiates authentication (via Twilio call or Streamlit UI).

23. System generates a random challenge phrase and plays it via gTTS.

24. User speaks the challenge phrase.

25. System preprocesses the audio through the AudioPreprocessor
    pipeline.

26. ECAPA-TDNN extracts a 192-dim embedding from the preprocessed audio;
    L2-normalise.

27. Stage 1 --- Voice Biometric Match: The live embedding is
    scalar-multiplied dimension-wise with the stored Paillier
    HE-encrypted centroid. The homomorphic sum produces Enc(dot
    product). The private key decrypts the result. The cosine similarity
    score is compared against the threshold (default 0.45).

28. Stage 2 --- Phrase Transcript Match: The same audio is transcribed
    using the language-appropriate ASR model (Whisper large-v3 for English,
    w2v-BERT 2.0 for Swahili). The transcription is normalised
    (lowercase, stripped punctuation) and compared against the expected
    challenge phrase.

29. Both stages must pass for the result to be "granted."

30. An AUTH_EVENT record is created with the score and result.

31. Live embedding is zeroed from memory.

14.3 Consent Flow

32. System plays consent information in the citizen's preferred language
    via gTTS.

33. Citizen speaks their response (e.g., "Yes").

34. System runs the full voice auth pipeline on the response audio to
    verify the speaker.

35. ASR (Whisper or w2v-BERT, based on language) transcribes the response
    to confirm affirmative consent.

36. ConsentService signs the consent payload (citizen_id, ministry_code,
    data_scope, timestamp) using Ed25519.

37. A CONSENT_TOKEN record is created with the digital signature.

38. Consent confirmation is returned to the citizen.

14.4 Service Access Flow

39. System verifies that a valid, non-revoked consent token exists for
    the citizen and service.

40. Voice authentication is performed once at the start of the session.

41. For each form question: gTTS generates the question audio, ASR
    (Whisper or w2v-BERT) transcribes the citizen's spoken answer.

42. Transcribed answers are stored and associated with the form session.

15\. Demo End-to-End Flow

The following sequence demonstrates the complete prototype flow for the
hackathon demo:

43. **Identity Verification (Optional):** Agent initiates MOSIP e-Signet
    flow. Citizen authenticates via MOSIP biometrics (Mock MDS in dev).
    Verified MOSIP individual_id is returned and linked to citizen record.

44. **Enrollment:** Agent enrolls a citizen (with or without MOSIP
    verification). In the IVR flow, the system plays 5 randomly selected
    phrases via TTS and the citizen repeats each one (pressing # to stop).
    In the Streamlit flow, the citizen uploads 5 pre-recorded audio files.
    Voice template (encrypted centroid) is created and stored. If e-Signet
    was used, enrollment is identity-verified.

45. **Authentication:** Citizen "calls" the system (Twilio IVR or
    Streamlit upload). In the IVR flow, the citizen first enters their
    national ID via keypad. The system plays a random challenge phrase.
    Citizen speaks the phrase. System downloads the recording and
    performs the full dual-stage authentication: voice biometric match
    (ECAPA-TDNN + Paillier HE dot product) + phrase transcript match
    (Whisper for English, w2v-BERT for Swahili). The voice score is
    announced to the caller. Result: "granted" or "denied." If denied,
    the caller gets one retry with a new challenge phrase; if denied
    again, the call ends.

46. **Consent (same call):** Upon successful authentication, the IVR
    stays in the same call and announces available services (Health
    Insurance Form). System reads consent text (gTTS). Citizen says
    "Yes." Consent is recorded. The call continues to service access.

47. **Service Access (same call):** System plays health insurance form
    questions (gTTS). Citizen answers verbally. ASR transcribes answers
    (Whisper for English, w2v-BERT for Swahili). After all 3 questions,
    TTS reads back a summary for confirmation. Form completed. Call ends.

16\. Configuration & Thresholds

  ------------------------- -------------------------------------------------
  **Parameter**             Value

  **SAMPLE_RATE**           16,000 Hz

  **EMBEDDING_DIM**         192

  **PAILLIER_BITS**         2048

  **MATCH_THRESHOLD**       0.45 (configurable; threshold tuning deferred to
                            MVP)

  **ECAPA_SOURCE**          speechbrain/spkrec-ecapa-voxceleb

  **WHISPER_MODEL**         large-v3 (English ASR)

  **SWAHILI_ASR_MODEL**     badrex/w2v-bert-2.0-swahili-as (Swahili ASR)

  **GSM_ENFORCE**           False (default: 80 Hz highpass; True: 300--3400
                            Hz bandpass)

  **VAD_TOP_DB**            25

  **PRE_EMPHASIS_COEFF**    0.97

  **PEAK_NORM_TARGET**      0.95

  **NOISE_RMS_THRESHOLD**   0.05 (spectral subtraction trigger)

  **MIN_AUDIO_DURATION**    1 second (16,000 samples)

  **ENROLLMENT_PHRASES**    5

  **ESIGNET_BASE_URL**      e-Signet server URL (env-specific, e.g.,
                            esignet.collab.mosip.net)

  **ESIGNET_CLIENT_ID**     OIDC client ID registered with e-Signet

  **ESIGNET_REDIRECT_URI**  http://localhost:8000/api/v1/mosip/callback

  **ESIGNET_SCOPES**        openid profile

  **MOCK_MDS_PORTS**        4501-4600
  ------------------------- -------------------------------------------------

17\. Key Libraries & Dependencies

The following is the complete list of Python dependencies for the
prototype (requirements.txt):

  --------------------- ---------------------- ----------------------------
  **Package**           **Purpose**            **Notes**

  fastapi               Web framework          REST API endpoints

  uvicorn\[standard\]   ASGI server            Serves FastAPI app

  pydantic              Data validation        Request/response schemas

  pydantic-settings     Config management      .env file loading

  sqlalchemy            ORM                    Database models and queries

  alembic               DB migrations          Schema version control

  speechbrain           Speaker verification   ECAPA-TDNN model loading

  torch                 ML runtime             PyTorch for model inference

  torchaudio            Audio processing       Audio tensor operations

  openai-whisper        Speech-to-text         Whisper large-v3 ASR (English)

  transformers          Speech-to-text         w2v-BERT 2.0 ASR (Swahili)

  gTTS                  Text-to-speech         Google TTS for audio
                                               generation

  python-paillier       Homomorphic encryption Paillier HE for voice
                                               templates

  pynacl                Digital signatures     Ed25519 for consent tokens

  numpy                 Numerical computing    Array operations, embeddings

  librosa               Audio analysis         Loading, resampling, VAD

  scipy                 Signal processing      Butterworth filters,
                                               spectral subtraction

  sounddevice           Audio I/O              Microphone recording
                                               (Streamlit)

  redis                 Caching/sessions       Session management

  streamlit             Web UI                 Secondary frontend for audio
                                               upload

  twilio                Telephony              Twilio Voice API integration

  transformers          ML utilities           HuggingFace model support

  authlib               OIDC client            e-Signet authorization code
                                               flow, JWT validation

  httpx                 HTTP client            Async HTTP transport for
                                               authlib OIDC calls
  --------------------- ---------------------- ----------------------------

18\. Integration Notes

18.1 OOP Integration Strategy

Individual components (ECAPA embedding extraction, Paillier encryption,
Whisper transcription, audio preprocessing) have been tested
independently as standalone scripts. For the integrated prototype, these
will be refactored into class-based service modules following OOP
principles. Each service class will encapsulate its dependencies,
configuration, and lifecycle (e.g., singleton model loading for
ECAPA-TDNN and Whisper).

18.2 Twilio Integration

The Twilio Voice API integration requires a publicly accessible webhook
URL. During development, ngrok or a similar tunneling service can expose
the local FastAPI server. In production (Vercel), the webhook endpoints
are directly accessible. Twilio's \<Record\> verb captures audio, which
is then downloaded and processed through the backend pipeline. All IVR
recordings use explicit keypad stop: the caller hears a beep (recording
starts), speaks their response, and presses \# on the keypad to end the
recording. Silence detection is disabled (timeout=0) to prevent
mid-sentence cutoffs from pauses. A bilingual prompt ("Press pound when
you are done" / "Bonyeza # ukimaliza") plays before each recording.

The IVR authentication callback downloads the recording from Twilio's
servers (using Basic Auth with TWILIO_ACCOUNT_SID/AUTH_TOKEN), then runs
the full dual-stage authentication pipeline — the same pipeline used by
the REST API. Upon success, the call continues seamlessly through
consent and service access without hanging up. On denial, the caller
gets one retry; a second denial ends the call. The citizen_id is
propagated through all subsequent IVR steps via URL query parameters.

IVR TTS strategy varies by language: English prompts use Twilio's built-in
\<Say voice="alice"\> (rendered inline with zero latency). Swahili prompts
use gTTS (Google Text-to-Speech) which has proper Swahili pronunciation —
audio files are generated server-side, served via /tts-audio/, and played
via Twilio's \<Play\> verb. Static prompts (e.g., "Bonyeza # ukimaliza")
are cached in memory so repeated calls avoid re-generation.

During IVR enrollment, the system randomly selects a phrase from the
bilingual phrase pool for each of the 5 recordings and plays it via TTS.
The caller repeats the phrase and presses # when done. This ensures
natural voice variation across samples while giving the caller clear
guidance on what to say.

18.3 Streamlit Integration

The Streamlit app serves as a secondary frontend for demo purposes. It
provides file upload widgets for audio files, calls the FastAPI backend
endpoints, and displays results (enrollment confirmation, match scores,
transcriptions, consent tokens). It does not replace the Twilio IVR flow
but provides a convenient browser-based demo path. For enrollment, the
Streamlit path does not use random phrase prompts — citizens upload their
own pre-recorded audio files (any spoken content is acceptable, as the
enrollment pipeline only extracts voice biometric features, not transcript
content).

18.4 Redis Usage

Redis is used for session state management and temporary data such as
challenge phrase associations (mapping a session to the expected phrase
for transcript matching) and OIDC state/nonce storage for e-Signet flows
(with 5-minute TTL to prevent replay attacks). It is not used for SMS
OTP in the prototype.

18.5 MOSIP e-Signet Integration

VeriVoice integrates with MOSIP e-Signet via the standard OpenID Connect
authorization code flow. The MosipService class handles all OIDC
communication (authorize URL generation, code-for-token exchange, JWT
validation against MOSIP JWKS). During development, MOSIP Mock MDS
services (pre-built Java applications in `MOSIP_eSignet/`) simulate
biometric capture devices so that e-Signet's authentication page can
capture fingerprint/iris/face biometrics without real hardware. The Mock
MDS services must be started as separate Java processes before testing
e-Signet flows. The prototype uses the MOSIP collab environment
(`collab.mosip.net`). Identity verification via e-Signet is optional —
the system supports both manual national ID entry and e-Signet-verified
enrollment, ensuring backward compatibility.

19\. MVP Roadmap (Post-Prototype)

The following enhancements are planned for the MVP stage after the
prototype:

-   **Database Migration:** Move from SQLite to Supabase (PostgreSQL)
    for production-grade persistence.

-   **SMS OTP (3rd Auth Factor):** Add Redis-backed OTP generation and
    SMS delivery as a third authentication stage for high-value
    transactions.

-   **AfroDigits Dataset:** Integrate AfroDigits for improved noise
    filtering and African-language audio preprocessing.

-   **MakerereNLP ASR:** Evaluate MakerereNLP models for additional
    African languages beyond Swahili (Swahili is now served by
    w2v-BERT 2.0 in the prototype).

-   **Anti-Spoofing:** Implement deepfake detection and replay attack
    prevention.

-   **Threshold Tuning:** Rigorous threshold calibration using diverse
    voice datasets.

-   **Janus Module:** Privacy-preserving cross-registry voice
    deduplication for fraud prevention.

-   **HSM/KMS Key Management:** Migrate Paillier and Ed25519 keys from
    file-based to hardware security modules.

-   **Infrastructure:** Deploy to AWS with production monitoring,
    logging, and scaling.

-   **Fairness Auditing:** Implement demographic fairness monitoring
    across dialects and voice characteristics.

-   **Real Biometric Hardware:** Replace Mock MDS with production
    SBI-compliant biometric scanners for e-Signet authentication.

20\. Glossary

  --------------------- -------------------------------------------------
  **Term**              **Definition**

  **DPI**               Digital Public Infrastructure --- shared digital
                        systems (identity, payments, data exchange) that
                        governments build to deliver services at scale.

  **ECAPA-TDNN**        Emphasized Channel Attention, Propagation and
                        Aggregation in TDNN --- a neural network
                        architecture for speaker verification that
                        produces fixed-length speaker embeddings.

  **Paillier HE**       Paillier Homomorphic Encryption --- a public-key
                        cryptosystem that allows addition and scalar
                        multiplication on encrypted data without
                        decrypting it.

  **Ed25519**           An elliptic-curve digital signature algorithm
                        used for signing and verifying data integrity.

  **Centroid**          The mean of multiple speaker embeddings,
                        representing an average "voiceprint" for a
                        citizen.

  **L2-Norm**           Euclidean normalisation: dividing a vector by its
                        magnitude so it has unit length. Required for
                        cosine similarity to equal the dot product.

  **Cosine Similarity** A measure of similarity between two vectors,
                        computed as their dot product when both are
                        L2-normalised. Range: \[-1, 1\].

  **VAD**               Voice Activity Detection --- identifying and
                        extracting segments of an audio signal that
                        contain speech.

  **ASR**               Automatic Speech Recognition --- converting
                        spoken audio into text (speech-to-text).

  **TTS**               Text-to-Speech --- converting text into spoken
                        audio.

  **gTTS**              Google Text-to-Speech --- a Python library that
                        interfaces with Google's TTS API.

  **IVR**               Interactive Voice Response --- a telephony system
                        that interacts with callers through voice prompts
                        and keypad/voice inputs.

  **USSD**              Unstructured Supplementary Service Data --- a GSM
                        protocol for real-time, session-based
                        communication via short codes.

  **OTP**               One-Time Password --- a single-use code for
                        authentication (deferred to MVP).

  **HSM**               Hardware Security Module --- a physical device
                        for managing and protecting cryptographic keys
                        (deferred to MVP).

  **MOSIP**             Modular Open Source Identity Platform --- an
                        open-source framework for building national
                        digital identity systems, supporting biometric
                        enrollment, authentication, and ID issuance.

  **e-Signet**          MOSIP's OpenID Connect (OIDC) identity provider
                        that enables third-party services to authenticate
                        citizens against the national ID system using
                        biometrics.

  **SBI**               Secure Biometric Interface --- MOSIP's standard
                        protocol for communication between biometric
                        capture devices and MOSIP software. Defines
                        HTTP-based methods (MOSIPDISC, MOSIPDINFO,
                        CAPTURE, RCAPTURE, STREAM).

  **Mock MDS**          Mock Device Service --- a pre-built Java
                        application that simulates SBI-compliant
                        biometric capture devices (fingerprint, iris,
                        face) for development and testing without real
                        hardware.

  **OIDC**              OpenID Connect --- an identity layer built on
                        OAuth 2.0 that enables clients to verify
                        identity based on authentication performed by
                        an authorization server and obtain basic
                        profile information.

  **JWKS**              JSON Web Key Set --- a set of public keys used
                        to verify JWT signatures. VeriVoice fetches
                        MOSIP's JWKS to validate e-Signet id_tokens.
  --------------------- -------------------------------------------------
