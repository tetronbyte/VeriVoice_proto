# IVR Identity Verification (MOSIP e-Signet Magic Link)

Lets a caller verify their national identity via MOSIP e-Signet during a Twilio IVR call, without exiting the call.

## Flow

1. Caller dials the Twilio number → selects Swahili/English
2. Caller presses **3** at the menu ("Press 3 to verify your identity")
3. IVR (`/twilio/voice/verify/start`):
   - Generates a new OIDC `state` + `nonce`, links `state → CallSid` in Redis
   - Sends an SMS to the caller's `From` number with the eSignet authorize URL
   - Plays: *"I have sent you a link by SMS. Please open it, log in, then return to this call."*
   - Redirects to `/twilio/voice/verify/poll`
4. Caller opens SMS on their phone → eSignet login page → enters individualId + OTP → Allow
5. eSignet redirects to `/api/v1/mosip/callback` (must be reachable from phone)
6. Callback exchanges code → validates id_token → writes `esignet:verified_state:{state} = individual_id` to Redis
7. IVR poll loop (`/twilio/voice/verify/poll`):
   - Checks Redis every ~5s
   - If verified → says *"Your identity has been verified"* → hangs up
   - Else → pauses, loops (max ~100s, then times out)

## Redis keys

| Key | Value | TTL | Purpose |
|---|---|---|---|
| `esignet:state:{state}` | nonce | 5 min | OIDC state/nonce mapping (consumed by callback) |
| `esignet:state_call:{state}` | call_sid | 5 min | Links state to phone call |
| `esignet:verified_state:{state}` | individual_id | 5 min | IVR poll target |
| `esignet:verified:{individual_id}` | "1" | 5 min | Legacy per-user marker |

## Required env config

```
PUBLIC_BASE_URL=https://<your-ngrok-subdomain>.ngrok-free.dev
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+...  # SMS-capable number
ESIGNET_BASE_URL=http://localhost:8088
ESIGNET_UI_URL=http://localhost:3000
ESIGNET_CLIENT_ID=verivoice-client-cf7d48a7
ESIGNET_REDIRECT_URI=http://localhost:8000/api/v1/mosip/callback
ESIGNET_PRIVATE_KEY_PATH=esignet_private_key.pem
ESIGNET_ISSUER=http://localhost:8088/v1/esignet
```

## Network reachability (IMPORTANT)

The phone browser must be able to reach:

- **eSignet UI** (`http://localhost:3000`) — currently localhost-only
- **VeriVoice callback** (`http://localhost:8000/api/v1/mosip/callback`) — reachable via `PUBLIC_BASE_URL` (ngrok)

For the phone to complete the flow, **both** the eSignet UI and the FastAPI server need public URLs. Options for demo:

1. **Single-laptop demo** — user clicks the SMS link on the same laptop that runs the stack. Works immediately, no extra ngrok.
2. **Phone demo** — run ngrok for both ports (requires paid ngrok or two separate tunnels). Update `ESIGNET_UI_URL` and `ESIGNET_REDIRECT_URI` to ngrok URLs, and update the OIDC client's `redirectUris` in eSignet.

## Testing locally (laptop demo)

1. Ensure eSignet stack is running (`docker ps`)
2. Ensure ngrok is pointing to port 8000 and `PUBLIC_BASE_URL` is set in `.env`
3. Configure the Twilio number's voice webhook to `{PUBLIC_BASE_URL}/twilio/voice/welcome`
4. Make sure `TWILIO_PHONE_NUMBER` is SMS-capable (Toll-free / long code; Tanzanian numbers have restrictions)
5. Call your Twilio number from a phone
6. Press 1 (English) → 3 (Verify)
7. Check your phone for the SMS — or your Twilio console logs if SMS didn't arrive
8. Open the link in a browser (on the same machine running the stack for the laptop demo)
9. Log in with individualId `8267411571`, OTP `111111`, click Allow
10. The IVR should say "Your identity has been verified" within a few seconds

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| IVR says "Failed to send SMS" | Twilio error — check account balance, number SMS-enabled, phone number format (+ country code) |
| SMS arrives but link 404 | `ESIGNET_UI_URL` wrong, or phone can't reach it (localhost issue) |
| Verification times out in IVR | User didn't click Allow, OR callback stored the wrong `state`, OR poll reads a different Redis DB |
| 403 "Invalid Twilio signature" | Expected if testing directly with curl; real Twilio requests will pass |
| Tanzanian + number can't send SMS | Twilio trial accounts or unregistered long codes may block — verify number capabilities in Twilio console |

## New code locations

- `twilio_integration/webhook_handler.py`:
  - `/voice/welcome/language` — added "Press 3" prompt
  - `/voice/welcome/action` — routes 3 to `/voice/verify/start`
  - `/voice/verify/start` — generates state, sends SMS, redirects to poll
  - `/voice/verify/poll` — Redis poll loop
  - `_send_sms_link()` — Twilio REST SMS helper
- `app/services/mosip_service.py`:
  - `store_oidc_context(state, nonce, call_sid=None)` — optional call_sid mapping
  - `store_verified_identity(state, individual_id)` — called from callback
  - `get_verified_identity(state)` — read by IVR poll
  - `get_authorize_url(call_sid=None)` — forwards call_sid to context
- `app/routers/mosip.py`:
  - Callback now calls `store_verified_identity(state, individual_id)` on success
