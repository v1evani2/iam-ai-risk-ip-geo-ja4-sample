# AI risk scoring usage with IsolationForest + encoders + per-user geo baseline to Login and Token API
AI risk scoring to a simple Login &amp; Token API and enforces allow / deny / MFA decisions. ML model that scores login attempts based on geo, device, network, and historical behavior.
This repo is a runnable reference that adds AI risk scoring to a simple Login & Token API and enforces allow / deny / MFA decisions.

You’ll train an unsupervised model, run a scoring API with a live dashboard, optionally stream events in via Kafka, trigger a signed webhook for high-risk events, and front it with a password login + JWT tokens service.

---

## What’s inside

```
iam-ai-month1-login-risk/
├─ src/
│  ├─ service.py           # Risk scoring API (+ dashboard, Kafka consumer, webhook, geo)
│  ├─ feature.py           # Feature engineering (freq encodings, JA4+, ip_rep)
│  ├─ geoutils.py          # City → coords + haversine_km
│  ├─ iprep.py             # Tiny in-memory IP → ASN/reputation demo DB
│  ├─ train.py             # Train IsolationForest + encoders + per-user geo baseline
│  ├─ simulate_logins.py   # Synthetic logins CSV generator
│  ├─ auth_service.py      # Sample Login & Token API (policy gate + JWTs)
│
├─ data/
│  ├─ sample_logins.csv    # 20k samples CSV
│  ├─ users.json           # Demo users (bcrypt hashed)
│
├─ models/                 # (created after training) model.joblib, encoders.json, user_geo.json
├─ scripts/
│  ├─ run_service.sh       # Start risk scoring API
│  ├─ run_auth.sh          # Start login/token API
│  ├─ simulate.sh          # Generate synthetic CSV
│  ├─ kafka_producer.py    # Stream CSV rows into Kafka (optional)
│
├─ requirements.txt
└─ README.md               # (this)
```

---

## 1) Prerequisites (macOS)

1. **Homebrew**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. **Python 3 + venv**

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
```

3. **Project deps**

```bash
pip install -r requirements.txt
```

> Optional (for streaming):
> `brew install kafka`
> On Apple Silicon, binaries are usually in `/opt/homebrew/bin`.

---

## 2) Generate training data (optional but recommended)

Use the simulator to create a larger CSV:

```bash
python src/simulate_logins.py --out data/sample_logins.csv --rows 20000
```

---

## 3) Train the model

This creates:

* `models/model.joblib` (IsolationForest)
* `models/encoders.json` (freq maps for user/ip/device/ua/JA4\*)
* `models/user_geo.json` (per-user mean lat/lon from observed cities)

```bash
python src/train.py --input data/sample_logins.csv --model_dir models
```

---

## 4) Run the **Risk Scoring API** (port 8000)

```bash
uvicorn src.service:app --host 0.0.0.0 --port 8000 --reload
```

Endpoints:

* `GET /health` → `{"status":"ok", "kafka_enabled": ...}`
* `GET /dashboard` → live table of the **last N** scored events (auto-refresh/2s)
* `GET /recent?limit=100` → recent scored events (JSON)
* `POST /score` → score a single event
* `POST /bulk_score` → score multiple

Example score:

```bash
curl -X POST http://localhost:8000/score \
  -H 'Content-Type: application/json' \
  -d '{
    "timestamp":"2025-08-09T12:00:00Z",
    "user_id":"u1",
    "ip":"198.51.100.9",
    "user_agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X)",
    "device_id":"dev-1",
    "success": true,
    "country":"US","region":"CA","city":"San Jose",
    "ja4":"t13d301000_1234_5678",
    "ja4h":"ge11nn100000_9999",
    "ja4s":"654321"
  }'
```

**How risk is computed (in brief):**

* Features: hour, day-of-week, success flag, **frequency encodings** (user/ip/device/UA/JA4\*), **IP reputation**, and optional **geo distance** from user baseline.
* Model: **IsolationForest** (unsupervised) → decision function normalized into **0-100 risk** (higher = riskier).
* Label: model **inlier/outlier**; response includes `risk_score`, `risk_level`, and an **explain** block.

---

## 5) Optional: **Kafka streaming** into the scorer

Start Kafka:

```bash
brew services start zookeeper
brew services start kafka
kafka-topics --bootstrap-server localhost:9092 --create --topic login-events --if-not-exists
```

Run scorer with Kafka enabled:

```bash
export KAFKA_BROKER=localhost:9092
uvicorn src.service:app --host 0.0.0.0 --port 8000 --reload
```

Produce events from CSV:

```bash
python scripts/kafka_producer.py --csv data/sample_logins.csv --topic login-events
```

Watch `/dashboard` update live.

---

## 6) Optional: **High-risk webhook** (MFA/alert)

Configure the scorer to POST scored events when risk ≥ threshold:

```bash
export WEBHOOK_URL="http://localhost:9000/notify"   # or any endpoint
export WEBHOOK_RISK_THRESHOLD=80                     # default 80
uvicorn src.service:app --host 0.0.0.0 --port 8000 --reload
```

**HMAC-signed MFA webhook (enhanced):**

```bash
export MFA_WEBHOOK_URL="http://localhost:9000/trigger-mfa"
export MFA_HMAC_SECRET="supersecret"
export MFA_HMAC_HEADER="X-Signature"    # default
export MFA_TENANT="example-tenant"
export WEBHOOK_RISK_THRESHOLD=75
```

Payload includes `risk_score`, `risk_level`, `explain` (with `geo_distance_km`, `ip_rep`, `asn`, `ja4*`), and a header `X-Signature: hex(hmac_sha256(secret, payload))`.

> Tip: you can test with any local HTTP receiver; verify HMAC in your handler before acting.

---

## 7) Run the **Login & Token API** (port 9000)

This service:

* Verifies passwords (bcrypt, demo users in `data/users.json`)
* Calls the **Risk Scoring API**
* Applies policy: **deny on high**, **MFA required on medium**, **allow on low**
* Issues **JWT access/refresh tokens** on allow

Start it:

```bash
uvicorn src.auth_service:app --host 0.0.0.0 --port 9000 --reload
```

Environment (optional):

```bash
export RISK_API_URL="http://localhost:8000"
export JWT_SECRET="local-dev-secret"
export JWT_ISSUER="iam-ai-sample"
export JWT_AUDIENCE="iam-clients"
export ACCESS_TTL_MIN=15
export REFRESH_TTL_DAYS=7
export LOGIN_POLICY_HIGH_DENY=true
export LOGIN_POLICY_MEDIUM_REQUIRES_MFA=true
```

### Users (demo)

* `alice` / `Password123!`
* `bob`   / `S3cret!xyz`

### Test: likely **allow**

```bash
curl -X POST http://localhost:9000/login -H 'Content-Type: application/json' -d '{
  "username":"alice","password":"Password123!",
  "timestamp":"2025-08-09T12:34:56Z",
  "ip":"198.51.100.9","user_agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X)",
  "device_id":"dev-1","success": true,
  "country":"US","region":"CA","city":"San Jose",
  "ja4":"t13d301000_1234_5678","ja4h":"ge11nn100000_9999","ja4s":"654321"
}'
```

### Test: likely **deny**

```bash
curl -X POST http://localhost:9000/login -H 'Content-Type: application/json' -d '{
  "username":"bob","password":"S3cret!xyz",
  "timestamp":"2025-08-09T02:00:00Z",
  "ip":"203.0.113.200","user_agent":"WeirdAgent/1.0",
  "device_id":"dev-x","success": true,
  "country":"RU","region":"XX","city":"??",
  "ja4":"t13d301000_bad","ja4h":"ge11nn100000_bad","ja4s":"999999"
}'
```

### Refresh token

```bash
curl -X POST http://localhost:9000/token -H 'Content-Type: application/json' -d '{
  "grant_type":"refresh_token",
  "refresh_token":"<paste from /login response>"
}'
```

> Note: The “MFA required” branch currently **returns a structured response** to simulate step-up. You can wire it to your real MFA by:
>
> * Enabling the scorer’s **MFA webhook** (Sec. 6), or
> * Implementing `/login/complete` that verifies a code and then issues tokens.

---

## 8) Data schema (event)

Fields accepted by `/score` (and sent by the Login API):

```json
{
  "timestamp": "ISO8601 or epoch",
  "user_id": "string",
  "ip": "string",
  "user_agent": "string",
  "device_id": "string",
  "success": true,
  "country": "US",
  "region": "CA",
  "city": "San Jose",
  "ja4": "optional",
  "ja4h": "optional",
  "ja4s": "optional",
  "asn": 64500,          // optional (overrides lookup)
  "ip_rep": 5.0          // optional (overrides lookup)
}
```

**Explain** includes: `hour`, `day_of_week`, `success`, frequency counts (`user_freq`, `ip_freq`, `device_freq`, `ua_freq`, `ja4*`), `ip_rep`, optional `geo_distance_km`, and enrichment (`asn`, `asn_name`).

---

## 9) Configuration reference

### Risk Scoring API (`src/service.py`)

| Env                      | Default        | Purpose                              |
| ------------------------ | -------------- | ------------------------------------ |
| `MODEL_DIR`              | `models`       | Model artifacts path                 |
| `RECENT_MAX`             | `500`          | Dashboard ring buffer size           |
| `KAFKA_BROKER`           | (unset)        | `host:port` to enable Kafka consumer |
| `KAFKA_TOPIC_IN`         | `login-events` | Topic name                           |
| `WEBHOOK_URL`            | (unset)        | POST scored events ≥ threshold       |
| `WEBHOOK_RISK_THRESHOLD` | `80`           | Trigger threshold                    |
| `MFA_WEBHOOK_URL`        | `WEBHOOK_URL`  | Dedicated MFA webhook                |
| `MFA_HMAC_SECRET`        | (unset)        | HMAC key for webhook signing         |
| `MFA_HMAC_HEADER`        | `X-Signature`  | HMAC header name                     |
| `MFA_TENANT`             | `default`      | Included in webhook payload          |

### Login & Token API (`src/auth_service.py`)

| Env                                | Default                 | Purpose                    |
| ---------------------------------- | ----------------------- | -------------------------- |
| `RISK_API_URL`                     | `http://localhost:8000` | Scorer base URL            |
| `JWT_SECRET`                       | `local-dev-secret`      | HS256 signing key          |
| `JWT_ISSUER`                       | `iam-ai-sample`         | Token issuer               |
| `JWT_AUDIENCE`                     | `iam-clients`           | Token audience             |
| `ACCESS_TTL_MIN`                   | `15`                    | Access token TTL (minutes) |
| `REFRESH_TTL_DAYS`                 | `7`                     | Refresh token TTL (days)   |
| `LOGIN_POLICY_HIGH_DENY`           | `true`                  | Deny on high risk          |
| `LOGIN_POLICY_MEDIUM_REQUIRES_MFA` | `true`                  | MFA on medium risk         |

---

## 10) Architecture quick view

```
[ Login API ] --calls--> [ Risk Scorer ] --(webhook/HMAC)--> [ MFA/SIEM/SOAR ]
       |                       ^
       |                       |
   [ JWTs ]                [ Kafka (opt) ]
       |
   Clients
```

---

## 11) Run Samples

```
curl -X POST http://localhost:9000/login -H 'Content-Type: application/json' -d '{
  "username":"alice","password":"Password123!",
  "timestamp":"2025-08-09T18:34:56Z",
  "ip":"198.51.100.9","user_agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X)",
  "device_id":"dev-1","success": true,
  "country":"US","region":"CA","city":"San Jose",
  "ja4":"t22213d301000_1234_5678","ja4h":"ge11nn100000_9999","ja4s":"654321"
}'
{"allowed":true,"session_id":"5fe96d39-1321-4a69-ac04-911c4c970f85","tokens":{"access_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJpYW0tYWktc2FtcGxlIiwiYXVkIjoiaWFtLWNsaWVudHMiLCJzdWIiOiJhbGljZSIsInNjb3BlIjoib3BlbmlkIHByb2ZpbGUgZW1haWwiLCJpYXQiOjE3NTQ3Nzg5MTIsImV4cCI6MTc1NDc3OTgxMiwianRpIjoiYzI5MjQwNjQtNGVjZi00YWIyLThiOTMtYWVmM2NiZGRkZDgxIn0.9vZCMf8cZ0R4q9yP6JZeR3MEY3TFXLoe3w7BMQBL2P0","expires_in":900,"token_type":"Bearer","refresh_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJpYW0tYWktc2FtcGxlIiwiYXVkIjoiaWFtLWNsaWVudHMiLCJzdWIiOiJhbGljZSIsInR5cCI6InJlZnJlc2giLCJpYXQiOjE3NTQ3Nzg5MTIsImV4cCI6MTc1NTM4MzcxMiwianRpIjoiNzUyMzdjM2MtYWY5NS00MWI1LTgzY2YtODM4ZTU3MWRlMTQxIn0.SvhQzs8xsgt1mqx9BxpnLsE0XpXR5BnJhfnmozKg1bU","refresh_expires_in":604800,"scope":"openid profile email"},"score":{"risk_score":50.0,"risk_level":"low","model_label":1,"explain":{"hour":18,"day_of_week":5,"success":1,"user_freq":1,"ip_freq":93,"device_freq":8,"ua_freq":5060,"ja4_freq":0,"ja4h_freq":0,"ja4s_freq":0,"ip_rep":5.0},"ts_scored":1754778912.435404}}%              

curl -X POST http://localhost:9000/login -H 'Content-Type: application/json' -d '{
  "username":"alice","password":"Password123!",
  "timestamp":"2025-08-09T18:34:56Z",
  "ip":"198.01.100.9","user_agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X)",
  "device_id":"dev-1","success": true,
  "country":"US","region":"CA","city":"San Jose",
  "ja4":"t22213d301000_1234_5678","ja4h":"ge11nn100000_9999","ja4s":"654321"
}'
{"allowed":false,"mfa_required":true,"score":{"risk_score":50.0,"risk_level":"high","model_label":-1,"explain":{"hour":18,"day_of_week":5,"success":1,"user_freq":1,"ip_freq":1,"device_freq":8,"ua_freq":5060,"ja4_freq":0,"ja4h_freq":0,"ja4s_freq":0,"ip_rep":0.0},"ts_scored":1754778943.720663},"message":"MFA required due to elevated risk."}                                             
```

---

## 12) Troubleshooting

* **`ModuleNotFoundError`**: activate venv and re-install deps
  `source .venv/bin/activate && pip install -r requirements.txt`

* **Port already in use**:
  Change `--port` or kill the other process.

* **Kafka CLI not found**:
  On Apple Silicon try `/opt/homebrew/bin/kafka-topics`.

* **Dashboard empty**:
  Hit `/score` or stream events; the ring buffer fills from latest.

* **Webhook not received**:
  Check `WEBHOOK_RISK_THRESHOLD`; confirm your listener accepts POST and returns 2xx; if HMAC enabled, verify the signature header.

---


Need me to drop this README straight into your repo ZIP and re-package, or wire `/login/complete` + a tiny MFA simulator next?

