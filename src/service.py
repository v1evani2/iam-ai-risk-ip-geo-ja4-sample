from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
import joblib
import os
import numpy as np
import threading
import httpx
import hmac
import hashlib
import json
import time
from collections import deque

# Optional Kafka
KAFKA_BROKER = os.environ.get("KAFKA_BROKER", "").strip()
KAFKA_TOPIC_IN = os.environ.get("KAFKA_TOPIC_IN", "login-events")
KAFKA_CONSUMER_GROUP = os.environ.get("KAFKA_CONSUMER_GROUP", "risk-scorer")

try:
    from kafka import KafkaConsumer
    KAFKA_AVAILABLE = True
except Exception:
    KAFKA_AVAILABLE = False

from feature import load_encoders, json_events_to_features
from geoutils import lookup_city_coords, haversine_km
from iprep import lookup_ip

MODEL_DIR = os.environ.get("MODEL_DIR", "models")
MODEL_PATH = os.path.join(MODEL_DIR, "model.joblib")
ENCODERS_PATH = os.path.join(MODEL_DIR, "encoders.json")
USER_GEO_PATH = os.path.join(MODEL_DIR, "user_geo.json")

if not (os.path.exists(MODEL_PATH) and os.path.exists(ENCODERS_PATH)):
    raise RuntimeError("Model artifacts not found. Run training first (see README).")

clf = joblib.load(MODEL_PATH)
enc = load_encoders(ENCODERS_PATH)
user_geo = {}
if os.path.exists(USER_GEO_PATH):
    try:
        import json as _json
        with open(USER_GEO_PATH, 'r') as _f:
            user_geo = _json.load(_f)
    except Exception:
        user_geo = {}

app = FastAPI(title="Login Risk Scoring API", version="1.2.0")

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
WEBHOOK_RISK_THRESHOLD = float(os.environ.get("WEBHOOK_RISK_THRESHOLD", "80"))
MFA_WEBHOOK_URL = os.environ.get("MFA_WEBHOOK_URL", WEBHOOK_URL).strip()
MFA_HMAC_SECRET = os.environ.get("MFA_HMAC_SECRET", "").encode("utf-8")
MFA_HMAC_HEADER = os.environ.get("MFA_HMAC_HEADER", "X-Signature")
MFA_TENANT = os.environ.get("MFA_TENANT", "default")


# Recent scores ring buffer (for dashboard)
RECENT_MAX = int(os.environ.get("RECENT_MAX", "500"))
recent_scores = deque(maxlen=RECENT_MAX)


class LoginEvent(BaseModel):
    timestamp: Any
    user_id: str
    ip: str
    user_agent: str
    device_id: str
    success: bool = Field(default=True)
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    # JA4+ fields
    ja4: Optional[str] = None
    ja4h: Optional[str] = None
    ja4s: Optional[str] = None
    # optional client-provided reputation/asn overrides
    asn: Optional[int] = None
    ip_rep: Optional[float] = None



def _hmac_signature(payload_bytes: bytes) -> Optional[str]:
    if not MFA_HMAC_SECRET:
        return None
    mac = hmac.new(MFA_HMAC_SECRET, payload_bytes, hashlib.sha256).hexdigest()
    return mac


def score_to_risk(decision_values: np.ndarray) -> np.ndarray:
    """
    Convert IsolationForest decision_function values (higher is more normal)
    into a 0..100 risk score (higher is more risky).
    """
    if decision_values.size == 0:
        return decision_values

    lo = float(np.min(decision_values))
    hi = float(np.max(decision_values))
    if hi - lo < 1e-6:
        return np.full_like(decision_values, 50.0)

    norm = (decision_values - lo) / (hi - lo)
    risk = (1.0 - norm) * 100.0
    return risk


def score_event_dict(event_dict: Dict[str, Any]) -> Dict[str, Any]:
     # Enrich with ASN/reputation if missing
    if event_dict.get('asn') is None or event_dict.get('ip_rep') is None:
        asn, rep, as_name = lookup_ip(str(event_dict.get('ip','')))
        if event_dict.get('asn') is None:
            event_dict['asn'] = asn
        if event_dict.get('ip_rep') is None:
            event_dict['ip_rep'] = rep
        event_dict['asn_name'] = as_name
    X, expl = json_events_to_features([event_dict], enc)
    decision = clf.decision_function(X)  # higher is more normal
    risk = score_to_risk(decision)[0].item()
    label = int(clf.predict(X)[0])
    risk_level = "high" if label == -1 else ("medium" if risk >= 60 else "low")

    ex = expl[0]
    # Geo-distance (km) to user's baseline if known
    dist_km = None
    u = str(event_dict.get('user_id')) if event_dict.get('user_id') is not None else None
    city = event_dict.get('city')
    ccoords = lookup_city_coords(city)
    if u and ccoords and isinstance(user_geo, dict) and u in user_geo:
        try:
            dist_km = haversine_km(user_geo[u]['lat'], user_geo[u]['lon'], ccoords[0], ccoords[1])
        except Exception:
            dist_km = None
    if dist_km is not None:
        ex['geo_distance_km'] = round(float(dist_km), 2)

    result = {
        "risk_score": round(float(risk), 2),
        "risk_level": risk_level,
        "model_label": label,
        "explain": ex,
        "event": event_dict,
        "ts_scored": time.time(),
    }

    # Webhook: notify when risk >= threshold
    if WEBHOOK_URL and result["risk_score"] >= WEBHOOK_RISK_THRESHOLD:
        try:
            with httpx.Client(timeout=3.0) as client:
                client.post(WEBHOOK_URL, json=result)
        except Exception as _e:
            # non-fatal
            pass
    recent_scores.appendleft(result)
    return result


@app.get("/health")
def health():
    return {"status": "ok", "kafka_enabled": bool(KAFKA_BROKER and KAFKA_AVAILABLE)}


@app.get("/recent")
def recent(limit: int = 50):
    limit = max(1, min(limit, RECENT_MAX))
    return {"results": list(list(recent_scores)[0:limit])}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Login Risk Dashboard</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 20px; }
    h1 { margin-bottom: 0; }
    .meta { color: #666; margin-top: 4px; }
    table { border-collapse: collapse; width: 100%; margin-top: 16px; }
    th, td { border: 1px solid #ddd; padding: 8px; font-size: 14px; }
    th { background: #f5f5f5; text-align: left; }
    .low { color: #2e7d32; }
    .medium { color: #f57c00; }
    .high { color: #c62828; font-weight: 600; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 9999px; background: #eee; }
  </style>
</head>
<body>
  <h1>Login Risk Dashboard</h1>
  <div class="meta">Auto-refreshing every 2 seconds • Showing latest scored events</div>
  <table id="tbl">
    <thead>
      <tr>
        <th>Risk</th><th>Level</th><th>User</th><th>IP</th><th>Device</th><th>UA</th><th>Hour/DOW</th><th>Success</th><th>When</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
<script>
async function refresh() {
  try {
    const r = await fetch('/recent?limit=100');
    const data = await r.json();
    const rows = data.results || [];
    const tbody = document.querySelector('#tbl tbody');
    tbody.innerHTML = '';
    for (const row of rows) {
      const e = row.event || {};
      const ex = row.explain || {};
      const tr = document.createElement('tr');
      const level = row.risk_level || 'low';
      tr.innerHTML = `
        <td><span class="pill">${row.risk_score?.toFixed ? row.risk_score.toFixed(2) : row.risk_score}</span></td>
        <td class="${level}">${level.toUpperCase()}</td>
        <td>${e.user_id || e.user || ''}</td>
        <td>${e.ip || ''}</td>
        <td>${e.device_id || ''}</td>
        <td>${(e.user_agent||'').slice(0,42)}</td>
        <td>${ex.hour ?? ''}/${ex.day_of_week ?? ''}</td>
        <td>${ex.success == 1 ? '✅' : '❌'}</td>
        <td>${new Date((row.ts_scored||0)*1000).toLocaleString()}</td>
      `;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
  }
}
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
    """
    return HTMLResponse(html)


@app.post("/score")
def score(event: LoginEvent):
    result = score_event_dict(event.model_dump())
    return {k: v for k, v in result.items() if k != "event"}


@app.post("/bulk_score")
def bulk_score(events: List[LoginEvent]):
    if not events:
        return {"results": []}
    results = []
    for e in events:
        results.append(score_event_dict(e.model_dump()))
    # Strip original event from the response for brevity
    for r in results:
        r.pop("event", None)
    return {"results": results}


def _run_kafka_consumer():
    if not (KAFKA_BROKER and KAFKA_AVAILABLE):
        return
    try:
        consumer = KafkaConsumer(
            KAFKA_TOPIC_IN,
            bootstrap_servers=[KAFKA_BROKER],
            group_id=KAFKA_CONSUMER_GROUP,
            auto_offset_reset='earliest',
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode('utf-8', errors='ignore')),
        )
        for msg in consumer:
            try:
                event = msg.value
                if isinstance(event, dict):
                    score_event_dict(event)
            except Exception as e:
                # Keep the thread alive
                print("Kafka consume/score error:", e)
    except Exception as e:
        print("Kafka thread failed to start:", e)


def maybe_start_kafka_thread():
    if KAFKA_BROKER and KAFKA_AVAILABLE:
        t = threading.Thread(target=_run_kafka_consumer, daemon=True)
        t.start()
        print(f"Kafka consumer thread started for {KAFKA_TOPIC_IN} @ {KAFKA_BROKER}")
    else:
        if KAFKA_BROKER and not KAFKA_AVAILABLE:
            print("KAFKA_BROKER set, but kafka-python not available. Install requirements.txt.")
        else:
            print("Kafka disabled (no KAFKA_BROKER set).")


# Start Kafka consumer thread on import
maybe_start_kafka_thread()
