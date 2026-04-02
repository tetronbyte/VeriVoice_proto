# MOSIP Mock MDS (Mock Device Service) - Registration

A pre-built mock implementation of MOSIP's **Secure Biometric Interface (SBI)** specification for simulating biometric capture devices (Fingerprint, Iris, Face) during **Registration** workflows. This eliminates the need for real biometric hardware during development and testing.

## Prerequisites

- **Java 8+** (JRE or JDK)
- Java must be available on your system `PATH`

Verify with:

```bash
java -version
```

## Quick Start

```bash
cd target
java -cp "classes;lib/*" io.mosip.mock.sbi.test.CentralizedMockSBI
```

> **Linux/macOS**: Use colon as classpath separator instead of semicolon:
> ```bash
> java -cp "classes:lib/*" io.mosip.mock.sbi.test.CentralizedMockSBI
> ```

Once started, the Mock MDS listens on **http://127.0.0.1** on ports **4501-4600** (configurable in `application.properties`).

## Simulated Devices

The Mock MDS exposes three biometric device types:

| Device Type | Device ID | Model | Device Sub-IDs | Sub Type |
|---|---|---|---|---|
| **Iris** (Double) | 1 | IRIS01 | 1 (Left), 2 (Right), 3 (Both) | Double |
| **Finger** (Slap) | 2 | SLAP01 | 1 (Left hand), 2 (Right hand), 3 (Thumbs) | Slap |
| **Face** | 3 | FACE01 | 0 | Full face |

Additional device variants available: Finger Single, Iris Single (monocular).

## SBI Protocol Endpoints

All endpoints are accessed via HTTP on the device's `callbackId` URL (e.g., `http://127.0.0.1:4501/`).

| Method | Purpose |
|---|---|
| `MOSIPDISC` | Discover connected biometric devices |
| `MOSIPDINFO` | Get detailed device information |
| `STREAM` | Start live video stream from a device (Registration only) |
| `RCAPTURE` | Registration capture - capture biometrics for enrollment |
| `CAPTURE` | Auth capture - capture biometrics for authentication |

## API Usage Examples

### 1. Device Discovery (MOSIPDISC)

Discover all connected devices or filter by type.

**Discover all devices:**

```bash
curl -X MOSIPDISC http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{"type": "Biometric Device"}'
```

**Discover only fingerprint devices:**

```bash
curl -X MOSIPDISC http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{"type": "Finger"}'
```

**Discover only iris devices:**

```bash
curl -X MOSIPDISC http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{"type": "Iris"}'
```

**Discover only face devices:**

```bash
curl -X MOSIPDISC http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{"type": "Face"}'
```

Valid `type` values: `Biometric Device`, `Finger`, `Face`, `Iris`

**Example response:**

```json
[
  {
    "deviceId": "1",
    "deviceStatus": "Ready",
    "certification": "L0",
    "serviceVersion": "0.9.5",
    "deviceSubId": [1, 2, 3],
    "callbackId": "http://127.0.0.1:4501/",
    "digitalId": "<base64-encoded-digital-id>",
    "deviceCode": "b692b595-3523-iris-99fc-bd76e35f190f",
    "specVersion": ["0.9.5"],
    "purpose": "Registration",
    "error": {
      "errorCode": "0",
      "errorInfo": "Success"
    }
  }
]
```

### 2. Device Info (MOSIPDINFO)

Get detailed information about all connected devices. The response `deviceInfo` field is a signed JWT.

```bash
curl -X MOSIPDINFO http://127.0.0.1:4501/
```

**Example response:**

```json
[
  {
    "deviceInfo": "<signed-JWT-containing-device-details>",
    "error": {
      "errorCode": "0",
      "errorInfo": "No Action Necessary."
    }
  }
]
```

The JWT payload (once decoded) contains fields like `deviceId`, `deviceCode`, `purpose`, `firmware`, `deviceStatus`, `certification`, `deviceType`, `deviceSubType`, etc.

### 3. Live Stream (STREAM) - Registration Only

Start a live video stream from a device. This is required before performing an `RCAPTURE`.

```bash
curl -X STREAM http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "deviceId": "3",
    "deviceSubId": 0,
    "timeout": 30000
  }'
```

- `deviceId`: The ID of the device (e.g., `"3"` for Face, `"2"` for Finger Slap, `"1"` for Iris)
- `deviceSubId`: Sub-device selector (see device table above)
- `timeout`: Stream timeout in milliseconds

### 4. Registration Capture (RCAPTURE) - Registration Only

Capture biometrics for enrollment/registration. A live stream must typically be active before calling RCAPTURE.

**Capture face:**

```bash
curl -X RCAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "bio": [
      {
        "type": "Face",
        "count": 1,
        "requestedScore": 40,
        "deviceId": "3",
        "deviceSubId": 0,
        "bioSubType": ["UNKNOWN"],
        "previousHash": ""
      }
    ]
  }'
```

**Capture left hand fingerprints (4 fingers):**

```bash
curl -X RCAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "bio": [
      {
        "type": "Finger",
        "count": 4,
        "requestedScore": 40,
        "deviceId": "2",
        "deviceSubId": 1,
        "bioSubType": ["Left IndexFinger", "Left MiddleFinger", "Left RingFinger", "Left LittleFinger"],
        "previousHash": ""
      }
    ]
  }'
```

**Capture right hand fingerprints:**

```bash
curl -X RCAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "bio": [
      {
        "type": "Finger",
        "count": 4,
        "requestedScore": 40,
        "deviceId": "2",
        "deviceSubId": 2,
        "bioSubType": ["Right IndexFinger", "Right MiddleFinger", "Right RingFinger", "Right LittleFinger"],
        "previousHash": ""
      }
    ]
  }'
```

**Capture thumbs:**

```bash
curl -X RCAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "bio": [
      {
        "type": "Finger",
        "count": 2,
        "requestedScore": 40,
        "deviceId": "2",
        "deviceSubId": 3,
        "bioSubType": ["Left Thumb", "Right Thumb"],
        "previousHash": ""
      }
    ]
  }'
```

**Capture both irises:**

```bash
curl -X RCAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "bio": [
      {
        "type": "Iris",
        "count": 2,
        "requestedScore": 40,
        "deviceId": "1",
        "deviceSubId": 3,
        "bioSubType": ["Left", "Right"],
        "previousHash": ""
      }
    ]
  }'
```

**Capture with biometric exceptions (e.g., missing finger):**

```bash
curl -X RCAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "bio": [
      {
        "type": "Finger",
        "count": 3,
        "requestedScore": 40,
        "deviceId": "2",
        "deviceSubId": 1,
        "bioSubType": ["Left IndexFinger", "Left MiddleFinger", "Left RingFinger"],
        "exception": ["Left LittleFinger"],
        "previousHash": ""
      }
    ]
  }'
```

**Example RCAPTURE response:**

```json
{
  "biometrics": [
    {
      "specVersion": "0.9.5",
      "data": "<signed-JWT-containing-biometric-data>",
      "hash": "<sha256-hash>",
      "sessionKey": "<encrypted-session-key>",
      "thumbprint": "<certificate-thumbprint>",
      "error": {
        "errorCode": "0",
        "errorInfo": "Success"
      }
    }
  ]
}
```

### 5. Auth Capture (CAPTURE) - Auth Only

Capture biometrics for authentication. Similar to RCAPTURE but uses `purpose: "Auth"`.

```bash
curl -X CAPTURE http://127.0.0.1:4501/ \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Auth",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "captureTime": "2024-01-01T00:00:00.000+05:30",
    "transactionId": "unique-transaction-id-123",
    "bio": [
      {
        "type": "Finger",
        "count": 1,
        "requestedScore": 40,
        "deviceId": "2",
        "deviceSubId": 1,
        "bioSubType": ["Left IndexFinger"],
        "previousHash": ""
      }
    ]
  }'
```

## Admin APIs

The Mock MDS exposes admin endpoints for runtime tuning:

### Set Quality Score

Control the biometric quality score returned by capture operations (0-100):

```bash
curl -X POST http://127.0.0.1:4501/admin/score \
  -H "Content-Type: application/json" \
  -d '{"score": 85}'
```

Default quality score: `90`

### Set Capture Delay

Add artificial delay to capture responses (in milliseconds):

```bash
curl -X POST http://127.0.0.1:4501/admin/delay \
  -H "Content-Type: application/json" \
  -d '{"delay": 2000}'
```

### Set Device Status

Change device status to simulate different states:

```bash
curl -X POST http://127.0.0.1:4501/admin/status \
  -H "Content-Type: application/json" \
  -d '{"status": "Ready"}'
```

Valid status values: `Ready`, `Busy`, `Not Ready`, `Not Registered`

### Set Biometric Profile

Switch between biometric data profiles:

```bash
curl -X POST http://127.0.0.1:4501/admin/profile \
  -H "Content-Type: application/json" \
  -d '{"profileId": "Default"}'
```

Available profiles: `Default`, `Automatic`

## Configuration

All configuration is in `target/application.properties`:

| Property | Default | Description |
|---|---|---|
| `server.minport` | 4501 | First port in the port range |
| `server.maxport` | 4600 | Last port in the port range |
| `server.serveripaddress` | 127.0.0.1 | Bind address |
| `mosip.mock.sbi.quality.score` | 90 | Default biometric quality score (0-100) |
| `mosip.mock.sbi.device.purpose.registration` | Registration | Purpose mode for registration devices |
| `mosip.mock.sbi.device.purpose.auth` | Auth | Purpose mode for auth devices |
| `mosip.mock.sbi.env.*` | Staging/Developer/... | Environment identifiers |
| `mosip.auth.server.url` | collab endpoint | MOSIP auth manager URL for token retrieval |
| `mosip.ida.server.url` | collab endpoint | MOSIP IDA URL for certificate retrieval |

### Environment Values

Valid `env` values for capture requests:
- `Staging`
- `Developer`
- `Pre-Production`
- `Production`

## Biometric Data Profiles

Profiles provide different sets of pre-recorded biometric ISO data. Located under `target/Profile/`:

```
Profile/
  Default/
    Registration/    # ISO files: Face, all 10 fingers, both irises, exception photo
    Auth/            # ISO files: Face, fingers (with WSQ variants), irises
  Automatic/
    Registration/
    Auth/
```

Each profile contains ISO 19794-compliant biometric data files (`.iso` format) for all supported modalities.

## Bio Sub-Type Reference

### Finger
`Left IndexFinger`, `Left MiddleFinger`, `Left RingFinger`, `Left LittleFinger`, `Left Thumb`, `Right IndexFinger`, `Right MiddleFinger`, `Right RingFinger`, `Right LittleFinger`, `Right Thumb`, `UNKNOWN`

### Iris
`Left`, `Right`

### Face
`UNKNOWN`

## Exception Values (for registration captures)

Exceptions indicate missing biometrics:

**Finger exceptions:** `Left IndexFinger`, `Left MiddleFinger`, `Left RingFinger`, `Left LittleFinger`, `Left Thumb`, `Right IndexFinger`, `Right MiddleFinger`, `Right RingFinger`, `Right LittleFinger`, `Right Thumb`

**Iris exceptions:** `Left`, `Right`

## Count Limits

| Device Type | Max Count |
|---|---|
| Finger (Slap - 4 fingers) | 4 |
| Iris (Double) | 2 |
| Face | 1 |

## Error Codes

| Code | Description |
|---|---|
| 0 | Success |
| 100 | Device not registered |
| 101 | Unable to detect a biometric object |
| 102 | Technical error during extraction |
| 106 | Device not found |
| 110 | Device is not ready |
| 111 | Device is busy |
| 114 | Device Type can be only (Finger/Iris/Face) |
| 116 | Count Value Mismatch |
| 500 | Invalid URL |
| 501 | Invalid Type Value in Device Discovery Request |
| 505 | Quality Score not in range 0-100 |
| 601 | Livestream request cannot be done for Auth Devices |
| 700 | RCapture failed - live streaming was stopped |
| 703 | RCapture process already in progress |
| 709 | RCapture request cannot be done for Auth Devices |
| 800 | Auth Capture Failed |
| 809 | Auth Capture request cannot be done for Registration Devices |
| 999 | Unknown Error |

## Typical Registration Workflow

A complete registration capture session follows this sequence:

```
1. MOSIPDISC        ->  Discover available devices
2. MOSIPDINFO       ->  Get device info and verify readiness
3. STREAM           ->  Start live stream for face device (deviceId=3)
4. RCAPTURE         ->  Capture face (deviceId=3, deviceSubId=0)
5. STREAM           ->  Start live stream for finger device (deviceId=2, deviceSubId=1)
6. RCAPTURE         ->  Capture left hand fingers (deviceId=2, deviceSubId=1)
7. STREAM           ->  Start live stream for finger device (deviceId=2, deviceSubId=2)
8. RCAPTURE         ->  Capture right hand fingers (deviceId=2, deviceSubId=2)
9. STREAM           ->  Start live stream for finger device (deviceId=2, deviceSubId=3)
10. RCAPTURE        ->  Capture thumbs (deviceId=2, deviceSubId=3)
11. STREAM          ->  Start live stream for iris device (deviceId=1, deviceSubId=3)
12. RCAPTURE        ->  Capture both irises (deviceId=1, deviceSubId=3)
```

## File Structure

```
target/
  application.properties          # Main configuration
  classes/                        # Compiled Java classes
  lib/                            # Dependency JARs
  Biometric Devices/
    Face/                         # Face device config (DigitalId, DeviceInfo, Keys, Stream Image)
    Finger/
      Slap/                       # 4-finger slap scanner config
      Single/                     # Single finger scanner config
    Iris/
      Double/                     # Dual iris scanner config
      Single/                     # Single iris scanner config
  Profile/
    Default/                      # Default biometric data profile
      Registration/               # ISO biometric files for registration
      Auth/                       # ISO biometric files for auth
    Automatic/                    # Automatic profile variant
  files/
    keys/                         # PEM certificates and private keys for signing
    sdkDependeny/                 # Neurotechnology SDK (DLLs, licenses)
    MockMDS/                      # Sample request/response payloads
```

## Troubleshooting

- **Port already in use**: Change `server.minport` and `server.maxport` in `application.properties`.
- **Device not found**: Ensure you're using the correct `deviceId` (`1`=Iris, `2`=Finger Slap, `3`=Face).
- **RCapture fails**: Start a `STREAM` session before calling `RCAPTURE`. The stream must be active.
- **Auth Capture on Registration device**: This Mock MDS is configured for Registration. Auth Capture (`CAPTURE`) may require a separate Auth-purpose mock MDS instance.
- **ClassNotFoundException**: Make sure you run from the `target/` directory so relative paths in `application.properties` resolve correctly.
