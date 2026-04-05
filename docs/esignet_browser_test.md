# Testing eSignet OIDC Flow (Option C — Browser Test)

Manual browser test of the e-Signet OIDC authorization code flow with VeriVoice. Validates that VeriVoice can redirect to eSignet, the user logs in with the mock identity, and eSignet redirects back with a verified MOSIP individual_id.

**Status: WORKING end-to-end (local Docker stack).**

## Prerequisites

- eSignet Docker stack running (see `esignet_local_setup.md`)
- FastAPI running: `uvicorn app.main:app --reload --port 8000`
- Mock user created with individualId `8267411571`
- `.env` configured (see below)

## `.env` values

```
ESIGNET_BASE_URL=http://localhost:8088
ESIGNET_UI_URL=http://localhost:3000
ESIGNET_ISSUER=http://localhost:8088/v1/esignet
ESIGNET_CLIENT_ID=verivoice-client-cf7d48a7
ESIGNET_CLIENT_SECRET=
ESIGNET_REDIRECT_URI=http://localhost:8000/api/v1/mosip/callback
ESIGNET_JWKS_URI=http://localhost:8088/v1/esignet/oauth/.well-known/jwks.json
ESIGNET_SCOPES=openid profile
ESIGNET_PRIVATE_KEY_PATH=esignet_private_key.pem
```

`ESIGNET_BASE_URL` is the backend API (for the token endpoint). `ESIGNET_UI_URL` is the eSignet login page (served by the UI container on port 3000). `ESIGNET_ISSUER` is what actually appears in the id_token `iss` claim — it differs from the base URL.

## Step 1. Get the authorize URL

```bash
curl http://localhost:8000/api/v1/mosip/authorize
```

Response:
```json
{
  "authorize_url": "http://localhost:3000/authorize?response_type=code&client_id=...&state=...&nonce=...",
  "state": "..."
}
```

## Step 2. Open the URL in a browser

You should see the eSignet login page: "VeriVoice is requesting authentication for verification".

## Step 3. Log in with the mock user

1. Click **"Verify with OTP"**
2. Enter Individual ID: `8267411571`
3. Click **Get OTP** (mock system always sends `111111`)
4. Enter OTP: `111111`
5. Click **Verify**
6. On the consent page, click **Allow**

## Step 4. eSignet redirects to the callback

After Allow, eSignet redirects to:
```
http://localhost:8000/api/v1/mosip/callback?code=<auth_code>&state=<state>
```

VeriVoice's callback:
1. Validates `state` (atomic GET+DELETE from Redis)
2. Exchanges `code` for `id_token` using `private_key_jwt` client auth (RSA-signed client assertion)
3. Validates the id_token: PS256 signature, exp, audience, issuer, nonce
4. Returns:

```json
{
  "mosip_individual_id": "s8-VG8_0sbZMmhKXB7qHS8L9yzlwTD2wvuY08p6kZsM",
  "identity_verified": true,
  "linked_citizen_id": null
}
```

## Issues we hit and fixed

| Issue | Cause | Fix |
|---|---|---|
| `/v1/esignet/authorize` returned 404 JSON | Backend API doesn't serve the login UI — the UI container on port 3000 does | Added `ESIGNET_UI_URL=http://localhost:3000`; authorize URL now built against UI |
| `unknown command GETDEL` | Redis 6.0 in the eSignet docker stack (GETDEL needs 6.2+) | Replaced with atomic `GET`+`DELETE` pipeline |
| `The specified alg value is not allowed` | `jose.jwt.decode` was hard-coded to RS256 | Broadened to RS/PS family |
| `Unable to find an algorithm for key` | python-jose 3.x **does not support PS256** even with the cryptography backend. eSignet signs id_tokens with PS256. | Swapped JWT validation to **PyJWT** (already in venv, supports PS256) |
| `Invalid issuer` | Token `iss` is `http://localhost:8088/v1/esignet`, but we compared against `ESIGNET_BASE_URL` (`http://localhost:8088`) | Added separate `ESIGNET_ISSUER` setting |

## Client auth: `private_key_jwt`

Our OIDC client was registered with `clientAuthMethods: ["private_key_jwt"]` — no client secret. The token request includes a signed JWT assertion instead:

```
grant_type=authorization_code
code=<auth_code>
redirect_uri=http://localhost:8000/api/v1/mosip/callback
client_id=verivoice-client-cf7d48a7
client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
client_assertion=<RS256-signed JWT>
```

Assertion claims:
```json
{
  "iss": "verivoice-client-cf7d48a7",
  "sub": "verivoice-client-cf7d48a7",
  "aud": "http://localhost:8088/v1/esignet/oauth/v2/token",
  "jti": "<random-uuid>",
  "iat": <now>,
  "exp": <now + 5min>
}
```

Signed with `RS256` using `esignet_private_key.pem`. The corresponding public JWK was registered on the client.

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `curl: (7) Failed to connect ... 8000` | uvicorn isn't running |
| eSignet page doesn't load | `docker compose ps` — check containers up |
| `invalid_client` on login page | Client ID mismatch between `.env` and registered client |
| `redirect_uri_mismatch` | Redirect URI in request must exactly match one registered on the OIDC client |
| 400 "Invalid or expired OIDC state" | State expired (5-min TTL) or was already consumed — retry from Step 1 |
| 401 "Token exchange failed" | Private key path wrong, or assertion JWT expired/invalid |
