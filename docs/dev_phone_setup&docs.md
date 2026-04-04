# Twilio Dev Phone Setup Guide

Browser-based IVR testing without a physical phone. The Dev Phone simulates a real phone call through Twilio's infrastructure, so your IVR webhooks fire exactly as they would with a real caller.

---

## Prerequisites

- **Node.js 20.x or 22.x**
- **Twilio CLI** installed globally: `npm install -g twilio-cli`
- **Twilio Dev Phone plugin**: `twilio plugins:install @twilio-labs/plugin-dev-phone`
- **VeriVoice backend** running on port 8000
- **ngrok** installed (to expose local server for Twilio webhooks)
- **Twilio credentials** — Account SID and Auth Token (from Twilio Console)

---

## Steps

### 1. Start the VeriVoice backend

```bash
uvicorn app.main:app --reload --port 8000
```

### 2. Expose it via ngrok (separate terminal)

```bash
ngrok http 8000
```

### 3. Set the ngrok URL as your Twilio webhook

In Twilio Console > Phone Numbers > your number > Voice webhook:

```
https://<ngrok-id>.ngrok.io/twilio/voice/welcome
```

### 4. Run the Dev Phone (separate terminal)

```bash
twilio dev-phone
```

This opens a browser-based phone UI where you can make calls to your Twilio number.

---

## The `dev-phone/` Folder

The `dev-phone/` directory in the repo is a Twilio Serverless project scaffold (used by `twilio-run`). It has boilerplate functions/assets, but the main way to use Dev Phone is through the **Twilio CLI plugin** (`twilio dev-phone`), not by running `npm start` in that folder.

---

## Configuration

The `.env` in `dev-phone/` has `ACCOUNT_SID` but the `AUTH_TOKEN` is empty. You can either:

- Fill in the `AUTH_TOKEN` in `dev-phone/.env`, or
- Use the Twilio CLI login (`twilio login`), which stores credentials globally

---

## Usage Notes

- Press **#** on the keypad to end recordings (per the IVR's `finishOnKey=#` setting)
- The Dev Phone routes calls through Twilio's real infrastructure — all webhook endpoints, TwiML responses, and recording callbacks behave identically to a real phone call
- Make sure ngrok is running and the webhook URL is updated before placing a call
