# MOSIP e-Signet — What It Is and How VeriVoice Uses It

## What is MOSIP e-Signet?

### The Problem It Solves

Imagine a country like Kenya or Uganda has a national ID system — millions of citizens registered with their name, photo, fingerprints, iris scans. This data sits in a government database (MOSIP). But now a third-party app (like VeriVoice) needs to answer one question:

> "Is this person really who they claim to be?"

The app **should not** get direct access to the government's biometric database. That would be a security and privacy nightmare. So you need a middleman — a secure gateway that lets citizens prove their identity without exposing the raw national ID data to the app.

**That's e-Signet.** It's MOSIP's identity gateway.

### How e-Signet Works (General)

e-Signet is an **OpenID Connect (OIDC) identity provider** — the same protocol that powers "Sign in with Google" or "Sign in with Apple." But instead of Google verifying you have a Gmail account, e-Signet verifies you exist in the **national ID system** and your biometrics match.

The flow in plain English:

```
1. App says:     "Hey e-Signet, I need to verify this person"
2. e-Signet says: "OK, send them to me"
3. Citizen goes to e-Signet's page and proves who they are
                   (fingerprint scan, iris scan, face scan, or OTP)
4. e-Signet says: "Yes, this is citizen #12345. Here's a signed token proving it."
5. App receives the token, verifies the signature, and now KNOWS
   the person is who they claim — without ever touching the national DB
```

The signed token (called an `id_token`) is a JWT — a cryptographically signed JSON document that says "MOSIP confirms this person is individual #12345" and is signed with MOSIP's private key (PS256 algorithm). Anyone can verify it using MOSIP's public key (from the JWKS endpoint), but nobody can forge it.

### Key Point: e-Signet Never Gives You Biometric Data

The app never sees fingerprints, iris scans, or photos. It only gets:
- A `sub` claim (the MOSIP individual ID — a unique string like `"3920481"`)
- Confirmation that the person authenticated successfully
- Basic profile info (if requested via OIDC scopes)

This is the privacy-preserving design. The biometrics stay inside MOSIP.

---

## How VeriVoice Uses e-Signet

### The Problem in VeriVoice

VeriVoice enrolls citizens with voice biometrics. During enrollment, the citizen says "my national ID is 12345678." But how do we know they're telling the truth? Anyone could call and claim any national ID number.

Without e-Signet, VeriVoice just **trusts** the caller — `identity_verified = False`. That's fine for a demo but terrible for real use. Someone could enroll under another person's national ID and then authenticate as them.

### The Solution: Verify Before Enroll

e-Signet lets VeriVoice **prove** the caller really owns that national ID before linking their voice to it.

VeriVoice has **two paths** for this:

---

### Path 1: IVR (Phone Call) — OTP-Based

This is the clever one. The citizen is on a phone call — no browser, no fingerprint scanner. So VeriVoice uses e-Signet's **server-driven OTP flow**:

```
Caller presses 3 from main menu
        |
IVR: "Enter your national ID followed by #"
        |
Caller types: 1 2 3 4 5 6 7 8 #    (DTMF keypad)
        |
VeriVoice server calls e-Signet behind the scenes:
  1. "Hey, I want to authenticate citizen 12345678"     (oauth-details)
  2. "Send them an OTP"                                  (send-otp)
        |
Citizen receives OTP on their registered phone (SMS)
        |
IVR: "Enter the OTP you received followed by #"
        |
Caller types: 1 1 1 1 1 1 #
        |
VeriVoice server calls e-Signet again:
  3. "Verify this OTP for citizen 12345678"              (authenticate)
  4. "Give me an authorization code"                      (auth-code)
  5. "Exchange this code for a signed id_token"           (token)
        |
VeriVoice validates the JWT signature against MOSIP's public keys
        |
Extracts: sub = "mosip_individual_id_xyz"
        |
Stores in Redis: "This call has verified identity mosip_individual_id_xyz"
        |
IVR redirects straight into enrollment with identity_verified = True
```

The entire thing happens within the phone call. No browser, no app, no fingerprint scanner. Just the phone keypad.

---

### Path 2: Browser (Streamlit / REST API) — Full OIDC

This is the standard web flow, used from the Streamlit UI:

```
User clicks "Verify Identity" in Streamlit
        |
VeriVoice calls /api/v1/mosip/authorize
  -> Generates state + nonce, stores in Redis (5-min TTL)
  -> Returns a URL to e-Signet's login page
        |
User opens URL in browser -> e-Signet login page
  -> Scans fingerprint / iris / face via Mock MDS (dev) or real device (prod)
  -> e-Signet validates biometrics against MOSIP database
        |
e-Signet redirects back to /api/v1/mosip/callback?code=xxx&state=yyy
        |
VeriVoice:
  1. Looks up nonce for this state from Redis (atomic GETDEL)
  2. Exchanges code for id_token at e-Signet's /token endpoint
  3. Validates JWT: signature (PS256 via JWKS), expiry, audience, issuer, nonce
  4. Extracts sub = "mosip_individual_id_xyz"
  5. Stores verified identity in Redis
        |
User can now enroll with identity_verified = True
```

---

### What Changes in VeriVoice When Identity Is Verified

| Field | Without e-Signet | With e-Signet |
|-------|-----------------|---------------|
| `citizen.identity_verified` | `False` | `True` |
| `citizen.mosip_individual_id` | `NULL` | `"mosip_xyz_123"` |
| Trust level | "They told us their national ID" | "MOSIP confirmed their national ID" |

The voice biometric enrollment is **identical** either way — 5 recordings, ECAPA-TDNN embeddings, Paillier encryption. The difference is whether the national ID they gave is **verified** or just **claimed**.

---

### Why This Matters for the Prototype

In the real world, this prevents:
- **Identity theft** — Can't enroll someone else's voice under their national ID
- **Duplicate fraud** — MOSIP individual IDs are unique; can't link the same verified ID to two citizens
- **Impersonation** — Even if you know someone's national ID number, you can't pass the OTP/biometric check

For the prototype/demo, it shows that VeriVoice **can** integrate with a real national ID system without ever touching the raw identity database — only through the OIDC gateway.
