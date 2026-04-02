# MOSIP Mock Biometric Device Service (Mock MDS)

A socket-based TCP server that simulates biometric capture devices (fingerprint, iris, face) for the [MOSIP](https://mosip.io/) identity platform. It implements the **Secure Biometric Interface (SBI)** protocol, enabling development and testing of MOSIP registration and authentication flows without requiring real biometric hardware.

## Purpose and Use Cases

- **Development & Testing** -- Run a mock biometric device on localhost so that MOSIP Registration Client or Authentication Client can discover and capture biometrics without physical scanners.
- **Integration Testing** -- Embed the service programmatically in test suites via the `CentralizedMockSBI` API and verify end-to-end biometric workflows.
- **Auth Flow Simulation** -- Test the authentication pipeline with signed, encrypted biometric capture responses, including JWT and certificate-based payloads.
- **Registration Flow Simulation** -- Test live-streaming and registration capture (RCAPTURE) workflows with configurable quality scores and profiles.
- **Quality & Error Simulation** -- Use the admin APIs to change quality scores, inject delays, toggle device status, and switch biometric profiles at runtime.

## Prerequisites

- **Java 11** or later

## Quick Start

All commands run from the `target/` directory.

### Windows -- Auth Mode

```cmd
cd target
java -cp mock-mds-1.2.1-SNAPSHOT.jar;lib\* io.mosip.mock.sbi.test.TestMockSBI ^
  "mosip.mock.sbi.device.purpose=Auth" ^
  "mosip.mock.sbi.biometric.type=Biometric Device" ^
  "mosip.mock.sbi.biometric.image.type=WSQ"
```

Or use the included batch file:

```cmd
cd target
run_auth.bat
```

### Linux/macOS -- Registration Mode

```bash
cd target
java -cp mock-mds-1.2.1-SNAPSHOT.jar:lib/* io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Registration" \
  "mosip.mock.sbi.biometric.type=Biometric Device"
```

Or run in the background:

```bash
cd target
./run.sh
# Logs written to /tmp/mock_mds.log
```

The service listens on the first available port in **4501--4600** on `127.0.0.1`.

## Command-Line Arguments

All arguments are passed as `key=value` strings:

| # | Key | Values | Required |
|---|-----|--------|----------|
| 1 | `mosip.mock.sbi.device.purpose` | `Registration` or `Auth` | Yes |
| 2 | `mosip.mock.sbi.biometric.type` | `Biometric Device`, `Finger`, `Face`, or `Iris` | Yes |
| 3 | `mosip.mock.sbi.biometric.image.type` | `JP2000` or `WSQ` | No |

- **`Biometric Device`** starts all device types (finger, iris, face) simultaneously.
- **`WSQ`** image type is only valid with `Auth` purpose. Registration always uses `JP2000`.
- If image type is omitted, it defaults based on purpose.

### Examples

Start only the **face device** in auth mode:

```bash
java -cp mock-mds-1.2.1-SNAPSHOT.jar:lib/* io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Auth" \
  "mosip.mock.sbi.biometric.type=Face"
```

Start only **fingerprint** in registration mode:

```bash
java -cp mock-mds-1.2.1-SNAPSHOT.jar:lib/* io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Registration" \
  "mosip.mock.sbi.biometric.type=Finger"
```

Start only **iris** in registration mode:

```bash
java -cp mock-mds-1.2.1-SNAPSHOT.jar:lib/* io.mosip.mock.sbi.test.TestMockSBI \
  "mosip.mock.sbi.device.purpose=Registration" \
  "mosip.mock.sbi.biometric.type=Iris"
```

## SBI Protocol Endpoints

The service exposes raw HTTP endpoints (not REST framework). Clients connect to `http://127.0.0.1:<port>` and issue requests using SBI-specific HTTP verbs.

### Device Discovery -- `MOSIPDISC`

Discover available biometric devices.

```bash
curl -X MOSIPDISC http://127.0.0.1:4501/device \
  -H "Content-Type: application/json" \
  -d '{"type": "Biometric Device"}'
```

**Response:**

```json
[
  {
    "deviceId": "3",
    "deviceStatus": "Ready",
    "certification": "L0",
    "serviceVersion": "0.9.5",
    "deviceSubId": ["0"],
    "callbackId": "",
    "digitalId": "<base64-encoded-signed-digital-id>",
    "deviceCode": "b692b595-3523-face-99fc-bd76e35f190f",
    "specVersion": ["0.9.5"],
    "purpose": "Auth",
    "error": null
  }
]
```

The `type` field accepts: `Biometric Device` (all), `Finger`, `Face`, or `Iris`.

### Device Info -- `MOSIPDINFO`

Get detailed information about connected devices.

```bash
curl -X MOSIPDINFO http://127.0.0.1:4501/info \
  -H "Content-Type: application/json" \
  -d '{"type": "Biometric Device"}'
```

**Response:**

```json
[
  {
    "deviceStatus": "Ready",
    "deviceId": "3",
    "firmware": "MOSIP.FACE.1.0.0.0",
    "certification": "L0",
    "serviceVersion": "0.9.5",
    "deviceSubId": ["0"],
    "callbackId": "",
    "digitalId": "<signed-jwt>",
    "deviceCode": "b692b595-3523-face-99fc-bd76e35f190f",
    "env": "Staging",
    "purpose": "Auth",
    "specVersion": ["0.9.5"]
  }
]
```

### Biometric Capture -- `CAPTURE` (Auth Mode)

Capture biometrics for authentication.

```bash
curl -X CAPTURE http://127.0.0.1:4501/capture \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Auth",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "transactionId": "txn-001",
    "bio": [
      {
        "type": "Face",
        "count": 1,
        "requestedScore": 70,
        "deviceId": "3",
        "deviceSubId": "0",
        "bioSubType": null
      }
    ]
  }'
```

**Response:**

```json
{
  "biometrics": [
    {
      "specVersion": "0.9.5",
      "data": "<signed-jwt-containing-biometric-data>",
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

### Registration Capture -- `RCAPTURE` (Registration Mode)

Capture biometrics for registration. Requires an active live stream first.

```bash
curl -X RCAPTURE http://127.0.0.1:4501/capture \
  -H "Content-Type: application/json" \
  -d '{
    "env": "Staging",
    "purpose": "Registration",
    "specVersion": "0.9.5",
    "timeout": 30000,
    "transactionId": "txn-002",
    "bio": [
      {
        "type": "Finger",
        "count": 4,
        "requestedScore": 70,
        "deviceId": "2",
        "deviceSubId": "1",
        "bioSubType": null
      }
    ]
  }'
```

### Live Stream -- `STREAM` (Registration Mode Only)

Start a live video feed from a device. Returns a continuous MJPEG-style stream.

```bash
curl -X STREAM http://127.0.0.1:4501/stream \
  -H "Content-Type: application/json" \
  -d '{
    "deviceId": "3",
    "deviceSubId": "0",
    "timeout": "60000"
  }'
```

**Notes:**
- Streaming is only available for Registration devices (not Auth).
- The stream must be active before RCAPTURE can be used.
- The device status changes to "Busy" during streaming.

### CORS / OPTIONS

All endpoints respond with CORS headers:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: OPTIONS, RCAPTURE, CAPTURE, MOSIPDINFO, MOSIPDISC, STREAM, GET, POST
Access-Control-Allow-Credentials: true
```

## Admin APIs

Control device behavior at runtime. All admin APIs use `POST` with a JSON body.

### Set Quality Score

```bash
curl -X POST http://127.0.0.1:4501/admin/score \
  -H "Content-Type: application/json" \
  -d '{
    "type": "Biometric Device",
    "qualityScore": "85",
    "fromIso": false
  }'
```

- `qualityScore`: 0--100
- `fromIso`: if `true`, derives score from the ISO biometric data

### Set Response Delay

Add artificial delay to specific methods (useful for timeout testing):

```bash
curl -X POST http://127.0.0.1:4501/admin/delay \
  -H "Content-Type: application/json" \
  -d '{
    "type": "Biometric Device",
    "delay": "5000",
    "method": ["CAPTURE", "RCAPTURE"]
  }'
```

- `delay`: milliseconds (must be >= 0)
- `method`: array of `MOSIPDISC`, `MOSIPDINFO`, `CAPTURE`, `STREAM`, `RCAPTURE`

### Set Device Status

```bash
curl -X POST http://127.0.0.1:4501/admin/status \
  -H "Content-Type: application/json" \
  -d '{
    "type": "Biometric Device",
    "deviceStatus": "Not Ready"
  }'
```

Valid statuses: `Ready`, `Busy`, `Not Ready`, `Not Registered`

### Switch Biometric Profile

```bash
curl -X POST http://127.0.0.1:4501/admin/profile \
  -H "Content-Type: application/json" \
  -d '{
    "profileId": "Automatic"
  }'
```

Available profiles are directories under `target/Profile/`. Ships with `Default` and `Automatic`.

## Programmatic Usage (Java API)

Embed the mock service in your Java test suite:

```java
import io.mosip.mock.sbi.test.CentralizedMockSBI;

// Start a mock MDS instance -- returns the port it bound to
int port = CentralizedMockSBI.startSBI(
    "myTestContext",        // unique context name
    "Auth",                 // purpose: "Auth" or "Registration"
    "Biometric Device",     // type: "Biometric Device", "Finger", "Face", or "Iris"
    "./Biometric Devices"   // path to device config directory
);

System.out.println("Mock MDS running on port: " + port);

// ... run your tests against http://127.0.0.1:<port> ...

// Stop the instance
CentralizedMockSBI.stopSBI("myTestContext");

// Or stop all running instances
CentralizedMockSBI.stopAllSBI();
```

## Configuration

### application.properties

Located at `target/application.properties`. Key settings:

| Property | Default | Description |
|----------|---------|-------------|
| `server.minport` | `4501` | Minimum port to bind |
| `server.maxport` | `4600` | Maximum port to bind |
| `server.serveripaddress` | `127.0.0.1` | Bind address |
| `mosip.mock.sbi.quality.score` | `90` | Default biometric quality score |
| `mosip.auth.server.url` | collab environment URL | MOSIP auth server endpoint |
| `mosip.ida.server.url` | collab environment URL | IDA certificate endpoint |

### Biometric Device Configs

Located under `target/Biometric Devices/`. Each device type has:

```
Biometric Devices/
  Face/
    DigitalId.json          # Device digital identity
    DeviceInfo.json         # Device metadata
    DeviceDiscovery.json    # Discovery response template
    Keys/
      device-dsk-partner.p12   # Device signing keystore (pwd: qwerty@123)
      ftm-csk-partner.p12      # FTM certificate keystore (pwd: qwerty@123)
    Stream Image/
      0.jpeg                # Image used for live streaming
  Finger/
    Slap/  ...              # 4-finger slap scanner (deviceSubId: 1,2,3)
    Single/ ...             # Single finger scanner (deviceSubId: 0)
  Iris/
    Double/ ...             # Binocular iris scanner (deviceSubId: 1,2,3)
    Single/ ...             # Monocular iris scanner (deviceSubId: 0)
```

### Biometric Profiles

Located under `target/Profile/`. Each profile contains `.iso` biometric data files and JPEG images:

```
Profile/
  Default/
    Finger/    # Left_Index.iso, Right_Thumb.iso, etc. (10 fingers + WSQ variants)
    Iris/      # Left_Iris.iso, Right_Iris.iso
    Face/      # Face.iso, Exception_Photo.iso
  Automatic/   # Same structure, auto-generated profiles
```

Custom profiles can be added by creating a new directory under `Profile/` with the same structure, then switching to it via the `/admin/profile` API.

## Error Codes

| Range | Category | Examples |
|-------|----------|----------|
| 0 | Success | `0` -- Success |
| 100--119 | Device errors | `106` -- Device not found, `110` -- Device not ready, `111` -- Device busy |
| 500--507 | Validation errors | `501` -- Invalid type in discovery, `505` -- Quality score out of range |
| 601--610 | Stream errors | `601` -- Stream not allowed for Auth, `609` -- Stream timeout |
| 700--710 | RCapture errors | `700` -- Stream was stopped, `709` -- RCapture not allowed for Auth |
| 800--810 | Auth Capture errors | `809` -- Auth capture not allowed for Registration |
| 999 | Unknown | `999` -- Unknown error |

## Supported Biometric Sub-Types

**Finger:**
`Left IndexFinger`, `Left MiddleFinger`, `Left RingFinger`, `Left LittleFinger`, `Left Thumb`, `Right IndexFinger`, `Right MiddleFinger`, `Right RingFinger`, `Right LittleFinger`, `Right Thumb`

**Iris:**
`Left`, `Right`

**Face:**
`Full face`
