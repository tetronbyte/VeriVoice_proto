# eSignet Local Setup (Docker + Mock Identity)

Local eSignet stack used for VeriVoice OIDC integration testing. No MOSIP collab account needed.

## Prerequisites

- Docker Desktop running
- Python 3.10+ with `cryptography` + `python-jose` installed
- `curl` available on PATH

## 1. Start the eSignet stack

```bash
cd <path-to-esignet-repo>/docker-compose
docker compose --file docker-compose.yml up -d
```

Services started:

| Service | URL |
|---|---|
| eSignet UI | http://localhost:3000 |
| eSignet backend | http://localhost:8088 |
| Mock identity system | http://localhost:8082 |
| PostgreSQL | localhost:5455 |
| Redis | localhost:6379 |

Verify all are healthy:
```bash
curl http://localhost:8088/v1/esignet/actuator/health
curl http://localhost:8082/v1/mock-identity-system/actuator/health
```

## 2. Generate RSA key pair (JWK)

eSignet uses `private_key_jwt` auth — no client_secret. Instead, you register a public key and sign JWTs with the private key.

```python
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwk
from jose.constants import Algorithms
import json

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Save private key PEM
priv_pem = private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption()
)
with open('esignet_private_key.pem', 'wb') as f:
    f.write(priv_pem)

# Save public key JWK
pub_pem = private_key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo
)
jwk_key = jwk.RSAKey(algorithm=Algorithms.RS256, key=pub_pem.decode())
with open('esignet_public_key.jwk', 'w') as f:
    json.dump(jwk_key.to_dict(), f, indent=2)
```

Keep `esignet_private_key.pem` — it's needed to sign auth assertions when exchanging an authorization code for a token.

## 3. Create an OIDC client

eSignet requires a CSRF token first, then a POST to register the client.

```bash
# Get CSRF token (saves cookie to cookies.txt)
CSRF=$(curl -s -c cookies.txt http://localhost:8088/v1/esignet/csrf/token \
    | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Create OIDC client (assumes create_client.json is built — see payload below)
curl -X POST http://localhost:8088/v1/esignet/client-mgmt/client \
    -H "Content-Type: application/json" \
    -H "X-XSRF-TOKEN: $CSRF" \
    -b cookies.txt \
    --data @create_client.json
```

### create_client.json payload

```json
{
  "requestTime": "2026-04-04T16:39:00.000Z",
  "request": {
    "clientId": "verivoice-client-cf7d48a7",
    "clientName": "VeriVoice",
    "publicKey": { /* contents of esignet_public_key.jwk */ },
    "relyingPartyId": "mock-relying-party-id",
    "userClaims": ["name", "email", "gender", "phone_number", "picture", "birthdate"],
    "authContextRefs": [
      "mosip:idp:acr:generated-code",
      "mosip:idp:acr:password",
      "mosip:idp:acr:linked-wallet"
    ],
    "logoUri": "https://placehold.co/100x100",
    "redirectUris": [
      "http://localhost:8000/api/v1/mosip/callback",
      "http://localhost:3000/*"
    ],
    "grantTypes": ["authorization_code"],
    "clientAuthMethods": ["private_key_jwt"],
    "additionalConfig": {
      "userinfo_response_type": "JWS",
      "purpose": {"type": "verify"},
      "signup_banner_required": true,
      "forgot_pwd_link_required": true,
      "consent_expire_in_mins": 20
    }
  }
}
```

Success response:
```json
{"response": {"clientId": "verivoice-client-cf7d48a7", "status": "ACTIVE"}, "errors": []}
```

## 4. Create a mock user

Mock users are stored in the mock-identity-system. They let you log in via the eSignet UI without a real MOSIP ID.

```bash
curl -X POST http://localhost:8082/v1/mock-identity-system/identity \
    -H "Content-Type: application/json" \
    --data @create_user.json
```

### create_user.json payload (test_verivoice_user)

```json
{
  "requestTime": "2026-04-04T16:41:34.000Z",
  "request": {
    "individualId": "8267411571",
    "pin": "111111",
    "email": "test_verivoice_user@example.com",
    "phone": "+255769301466",
    "fullName": [{"language": "eng", "value": "test_verivoice_user"}],
    "nickName": [{"language": "eng", "value": "test_verivoice_user"}],
    "preferredUsername": [{"language": "eng", "value": "test_verivoice_user"}],
    "givenName": [{"language": "eng", "value": "test"}],
    "middleName": [{"language": "eng", "value": "verivoice"}],
    "familyName": [{"language": "eng", "value": "user"}],
    "name": [{"language": "eng", "value": "test_verivoice_user"}],
    "dateOfBirth": "1995/01/01",
    "gender": [{"language": "eng", "value": "male"}],
    "streetAddress": [{"language": "eng", "value": "Dar es Salaam"}],
    "locality": [{"language": "eng", "value": "Dar es Salaam"}],
    "region": [{"language": "eng", "value": "Dar"}],
    "postalCode": "12345",
    "country": [{"language": "eng", "value": "Tanzania"}],
    "encodedPhoto": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
    "individualBiometrics": ""
  }
}
```

**Note:** `givenName`, `middleName`, and `familyName` must all be non-empty — the mock validator rejects empty strings.

### Test user credentials

| Field | Value |
|---|---|
| Individual ID | `8267411571` |
| PIN | `111111` |
| Email | `test_verivoice_user@example.com` |
| Phone | `+255769301466` |

## 5. `.env` values for VeriVoice

```
ESIGNET_BASE_URL=http://localhost:8088
ESIGNET_CLIENT_ID=verivoice-client-cf7d48a7
ESIGNET_CLIENT_SECRET=
ESIGNET_REDIRECT_URI=http://localhost:8000/api/v1/mosip/callback
ESIGNET_JWKS_URI=http://localhost:8088/v1/esignet/oauth/.well-known/jwks.json
ESIGNET_SCOPES=openid profile
ESIGNET_PRIVATE_KEY_PATH=esignet_private_key.pem
```

## 6. Testing the OIDC flow

Open in browser:
```
http://localhost:8088/authorize?response_type=code
  &client_id=verivoice-client-cf7d48a7
  &scope=openid+profile
  &redirect_uri=http://localhost:8000/api/v1/mosip/callback
  &state=abc123
  &nonce=xyz789
  &acr_values=mosip:idp:acr:generated-code
```

Log in with individualId `8267411571` + PIN `111111` → eSignet redirects to `http://localhost:8000/api/v1/mosip/callback?code=...&state=abc123`.

VeriVoice's callback handler should exchange the `code` for an `id_token` using `private_key_jwt` (signed with `esignet_private_key.pem`).

## Common errors

| Error | Fix |
|---|---|
| `invalid_middlename` / `invalid_givenname` etc. | Fill empty name fields with real values |
| `ConnectionRefusedError` (Redis) | Docker redis container must be up (`docker ps` to check) |
| `CSRF token mismatch` | Use the same cookie jar (`-b cookies.txt`) across GET csrf + POST create |

## Stopping the stack

```bash
cd <path-to-esignet-repo>/docker-compose
docker compose --file docker-compose.yml down
```
