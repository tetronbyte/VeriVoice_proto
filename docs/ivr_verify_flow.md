# IVR Identity Verification (DTMF + eSignet Direct OTP)

Lets a caller verify their national identity via MOSIP e-Signet **entirely over the phone call**, using keypad (DTMF) input — no SMS, no browser, no redirects. After verification, the call automatically continues into voice enrollment, linking the verified MOSIP individual_id to the new citizen record.

## Why DTMF, not browser OIDC?

The standard OIDC flow requires a browser (user taps SMS link, logs in on a web page). That works for web/mobile apps, but for a **phone IVR** it has three problems:

1. The user can't switch from a phone call to a browser smoothly.
2. Requires Twilio SMS geo-permissions, which aren't enabled by default for many regions (US, EU, etc.).
3. Requires public URLs (ngrok) for **both** the eSignet UI and the VeriVoice callback so the phone's browser can reach them.

Instead we call eSignet's **server-driven OAuth endpoints** directly from the backend: initiate a transaction, ask eSignet to send the OTP, collect the OTP over DTMF, and validate. The user never leaves the call.

## Flow

1. Caller dials the Twilio number → selects language (1=English / 2=Swahili) → presses **3** (Verify identity)
2. `/twilio/voice/verify/start` — IVR says *"Please enter your national identity number followed by the pound key."* (DTMF gather)
3. `/twilio/voice/verify/nid` — calls `MosipService.start_otp_auth(national_id)`:
   - GET `/v1/esignet/csrf/token`
   - POST `/v1/esignet/authorization/v3/oauth-details` — creates a transaction, we compute `oauth-details-hash`
   - POST `/v1/esignet/authorization/send-otp` — eSignet sends OTP to the identity's registered channels
   - Session (transaction_id, csrf, cookies, PKCE verifier, nonce) stored in Redis keyed by `CallSid`
   - IVR says *"An OTP has been sent. Please enter the six-digit OTP followed by #."*
4. Caller receives OTP (in mock: always `111111`) and enters it on the keypad
5. `/twilio/voice/verify/otp` — calls `MosipService.verify_otp_and_get_identity(session, national_id, otp)`:
   - POST `/v1/esignet/authorization/v3/authenticate` (challengeList with OTP)
   - POST `/v1/esignet/authorization/auth-code` — returns authorization code
   - POST `/v1/esignet/oauth/v2/token` with `code + code_verifier + client_assertion` (private_key_jwt + PKCE)
   - Validates id_token (PS256, aud, iss, nonce) → extracts `sub` = MOSIP individual_id
   - Stores `ivr:verified_identity:{CallSid} = {national_id, mosip_individual_id}` in Redis (10-min TTL)
6. IVR says *"Your identity has been verified. Now we will enroll your voice."*
7. Redirects to `/twilio/voice/enroll?lang=X&step=0&national_id=<NID>` — enrollment flow starts with national_id pre-filled
8. Caller records 5 voice samples → `_run_enrollment_pipeline` reads `ivr:verified_identity:{CallSid}` and calls `create_citizen(...mosip_individual_id=..., identity_verified=True)`

## Redis keys

| Key | Value | TTL | Purpose |
|---|---|---|---|
| `ivr:verify_session:{CallSid}` | JSON: `{national_id, session}` | 10 min | Pending eSignet transaction (held between NID entry and OTP entry) |
| `ivr:verified_identity:{CallSid}` | JSON: `{national_id, mosip_individual_id}` | 10 min | Read by enrollment pipeline to link MOSIP id to citizen |

The `session` dict carries `transaction_id`, `oauth_details_key`, `oauth_details_hash`, `csrf_token`, `cookies`, `code_verifier`, `nonce`, `state`.

## eSignet APIs used

| Step | Endpoint | Notes |
|---|---|---|
| 1 | `GET /v1/esignet/csrf/token` | CSRF cookie + token |
| 2 | `POST /v1/esignet/authorization/v3/oauth-details` | Creates transaction, requires PKCE `codeChallenge` (S256) |
| 3 | `POST /v1/esignet/authorization/send-otp` | Needs `oauth-details-key` + `oauth-details-hash` headers |
| 4 | `POST /v1/esignet/authorization/v3/authenticate` | `challengeList: [{authFactorType: "OTP", challenge: <otp>}]` |
| 5 | `POST /v1/esignet/authorization/auth-code` | Returns `code` |
| 6 | `POST /v1/esignet/oauth/v2/token` | `grant_type=authorization_code` + `client_assertion` (private_key_jwt) + `code_verifier` |
| 7 | Validate id_token | PS256 signature, exp, aud, iss, nonce |

`oauth-details-hash` is computed by the client as `base64url(sha256(JSON.stringify(response)))` of the oauth-details response body.

## Env config (.env)

```
ESIGNET_BASE_URL=http://localhost:8088
ESIGNET_ISSUER=http://localhost:8088/v1/esignet
ESIGNET_CLIENT_ID=verivoice-client-cf7d48a7
ESIGNET_CLIENT_SECRET=
ESIGNET_REDIRECT_URI=http://localhost:8000/api/v1/mosip/callback
ESIGNET_JWKS_URI=http://localhost:8088/v1/esignet/oauth/.well-known/jwks.json
ESIGNET_SCOPES=openid profile
ESIGNET_PRIVATE_KEY_PATH=esignet_private_key.pem
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+...
```

No `PUBLIC_BASE_URL` needed for verification itself (though it's still used by the rest of the IVR for audio serving). No SMS needed. No ngrok needed for eSignet.

## Testing with the mock user

Mock user: individualId `8267411571`, OTP `111111` (always).

1. Docker stack up (`docker ps` shows eSignet backend, UI, mock-identity, Redis, Postgres)
2. Start uvicorn: `uvicorn app.main:app --reload --port 8000`
3. Call your Twilio number (or dev-phone) → press 1 (English) → press 3 (Verify)
4. Enter `8267411571` on the keypad, press `#`
5. Enter `111111` on the keypad, press `#`
6. IVR says "Your identity has been verified. Now we will enroll your voice."
7. Voice enrollment flow starts with `national_id=8267411571` pre-filled

After enrollment, the `CITIZEN` row will have:
- `national_id_number` = `8267411571`
- `mosip_individual_id` = `s8-VG8_0sbZMmhKXB7qHS8L9yzlwTD2wvuY08p6kZsM` (or whatever `sub` eSignet issues)
- `identity_verified` = `True`

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| "That identity could not be verified" right after entering NID | `start_otp_auth` failed — check eSignet logs (`docker logs docker-compose-esignet-1`); wrong national_id; eSignet down |
| "OTP is incorrect" | Wrong OTP. Mock always uses `111111`. For real eSignet, check user's email/phone |
| "Session expired" | Took >10 minutes between entering NID and OTP |
| Hang-up after OTP with no verdict | Check uvicorn logs — exception in verify_otp_and_get_identity (auth-code or token exchange failed) |
| Enrollment proceeds but citizen not linked to MOSIP id | Check that `ivr:verified_identity:{CallSid}` key is present in Redis before enrollment pipeline runs |

## Relevant code

- `app/services/mosip_service.py`:
  - `start_otp_auth(individual_id)` — steps 1-3
  - `verify_otp_and_get_identity(session, individual_id, otp)` — steps 4-7
  - `_pkce_pair()`, `_compute_oauth_details_hash(response)` — helpers
  - `exchange_code(code, code_verifier=None)` — extended to support PKCE
- `twilio_integration/webhook_handler.py`:
  - `/voice/verify/start` — prompt for national ID
  - `/voice/verify/nid` — receive NID, trigger eSignet send-otp, prompt for OTP
  - `/voice/verify/otp` — validate OTP, redirect into enrollment
  - `_run_enrollment_pipeline` — reads verified identity from Redis, links `mosip_individual_id` + `identity_verified=True` to citizen
