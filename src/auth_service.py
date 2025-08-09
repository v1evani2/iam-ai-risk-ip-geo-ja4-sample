import os, time, json, uuid
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from passlib.hash import bcrypt

# --- Config ---
RISK_API_URL = os.environ.get("RISK_API_URL", "http://localhost:8000")
JWT_SECRET = os.environ.get("JWT_SECRET", "local-dev-secret")
JWT_ISSUER = os.environ.get("JWT_ISSUER", "iam-ai-sample")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "iam-clients")
ACCESS_TTL_MIN = int(os.environ.get("ACCESS_TTL_MIN", "15"))
REFRESH_TTL_DAYS = int(os.environ.get("REFRESH_TTL_DAYS", "7"))

# Risk policy
HIGH_DENY = os.environ.get("LOGIN_POLICY_HIGH_DENY", "true").lower() == "true"
MEDIUM_REQUIRES_MFA = os.environ.get("LOGIN_POLICY_MEDIUM_REQUIRES_MFA", "true").lower() == "true"

# Redis (optional; falls back to in-memory if not configured)
try:
    import redis  # type: ignore
    REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    r = redis.from_url(REDIS_URL, decode_responses=True)
except Exception:
    r = None

SESSIONS: Dict[str, Dict[str, Any]] = {}
REFRESH_STORE: Dict[str, Dict[str, Any]] = {}

def _session_put(session_id: str, data: dict, ttl_seconds: int = 86400):
    if r:
        key = f"session:{session_id}"
        r.hset(key, mapping=data)
        r.expire(key, ttl_seconds)
    else:
        SESSIONS[session_id] = data

def _refresh_put(refresh_token: str, sub: str, exp_epoch: int):
    if r:
        key = f"rt:{refresh_token}"
        r.hset(key, mapping={"sub": sub, "exp": exp_epoch})
        r.expireat(key, exp_epoch)
    else:
        REFRESH_STORE[refresh_token] = {"sub": sub, "exp": exp_epoch}

def _refresh_get(refresh_token: str):
    if r:
        m = r.hgetall(f"rt:{refresh_token}")
        if not m:
            return None
        return {"sub": m.get("sub"), "exp": int(m.get("exp"))}
    return REFRESH_STORE.get(refresh_token)

def _refresh_del(refresh_token: str):
    if r:
        r.delete(f"rt:{refresh_token}")
    else:
        REFRESH_STORE.pop(refresh_token, None)


USERS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "users.json")

app = FastAPI(title="Sample Login + Token API", version="1.0.1")


class LoginRequest(BaseModel):
    username: str
    password: str
    # context for risk scoring
    timestamp: Optional[str] = None
    ip: str
    user_agent: str
    device_id: str
    success: bool = Field(default=True)
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    ja4: Optional[str] = None
    ja4h: Optional[str] = None
    ja4s: Optional[str] = None


class TokenRequest(BaseModel):
    grant_type: str = Field(description="supported: refresh_token")
    refresh_token: Optional[str] = None


def _load_users() -> Dict[str, Any]:
    with open(USERS_PATH, "r") as f:
        return json.load(f)


def _issue_tokens(sub: str, scope: str = "openid profile email") -> Dict[str, Any]:
    now = int(time.time())
    access_exp = now + ACCESS_TTL_MIN * 60
    refresh_exp = now + REFRESH_TTL_DAYS * 86400
    jti = str(uuid.uuid4())
    access = jwt.encode(
        {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "sub": sub,
            "scope": scope,
            "iat": now,
            "exp": access_exp,
            "jti": jti,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    refresh = jwt.encode(
        {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "sub": sub,
            "typ": "refresh",
            "iat": now,
            "exp": refresh_exp,
            "jti": str(uuid.uuid4()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    _refresh_put(refresh, sub, refresh_exp)
    return {
        "access_token": access,
        "expires_in": ACCESS_TTL_MIN * 60,
        "token_type": "Bearer",
        "refresh_token": refresh,
        "refresh_expires_in": REFRESH_TTL_DAYS * 86400,
        "scope": scope,
    }


async def _score_event(ctx: Dict[str, Any]) -> Dict[str, Any]:
    # Call risk scoring API
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(f"{RISK_API_URL}/score", json=ctx)
        r.raise_for_status()
        return r.json()


@app.post("/login")
async def login(req: LoginRequest, request: Request):
    users = _load_users()
    info = users.get(req.username)
    # allow JA4 from edge headers when not in body
    ctx = req.model_dump()

    ctx["user_id"] = req.username
    if not ctx.get("timestamp"):
        from datetime import datetime, timezone
        ctx["timestamp"] = datetime.now(timezone.utc).isoformat()

    ctx.setdefault("ja4",  request.headers.get("x-ja4"))
    ctx.setdefault("ja4h", request.headers.get("x-ja4h"))
    ctx.setdefault("ja4s", request.headers.get("x-ja4s"))

    print("DEBUG users.json keys:", list(users.keys()))
    print("DEBUG sample hash prefix:", info["password_bcrypt"][:10])


    if not info or not bcrypt.verify(req.password, info["password_bcrypt"]):
        # score anyway with success=false for visibility
        ctx["success"] = False
        try:
            await _score_event(ctx)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="invalid_credentials")

    # valid credentials -> score the event with success=True
    ctx["success"] = True
    score = await _score_event(ctx)

    # Apply policy
#    level = (score.get("risk_level") or "low").lower()
#    if level == "high" and HIGH_DENY:
#        raise HTTPException(status_code=403, detail={"reason": "high_risk", "score": score})

#    if level == "medium" and MEDIUM_REQUIRES_MFA:
#        # Return MFA required response (sample)
#        return {
#            "allowed": False,
#            "mfa_required": True,
#            "score": score,
#            "message": "MFA required due to medium risk. Complete MFA then call /login/complete."
#        }

    level = (score.get("risk_level") or "low").lower()
    risk = float(score.get("risk_score", 0))
    ex   = score.get("explain", {})
    ip_rep = float(ex.get("ip_rep", 0.0))
    geo = float(ex.get("geo_distance_km", 0) or 0)

    def is_really_high():
        # deny only on strong signals
        return (
            level == "high" and (
                risk >= 80.0 or        # numeric risk high
                ip_rep >= 60.0 or      # bad reputation IP/ASN
                geo >= 1500.0          # far travel
            )
        )

    if is_really_high() and HIGH_DENY:
        raise HTTPException(status_code=403, detail={"reason": "high_risk", "score": score})

    # medium & weak-high -> MFA instead of deny
    if (level == "high" or level == "medium") and MEDIUM_REQUIRES_MFA:
        return {
            "allowed": False,
            "mfa_required": True,
            "score": score,
            "message": "MFA required due to elevated risk."
        }


    # low (or allowed) -> issue session + tokens
    session_id = str(uuid.uuid4())
    _session_put(session_id, {
        "username": req.username,
        "created": int(time.time()),
        "ip": req.ip,
        "device_id": req.device_id,
        "ua": req.user_agent,
    })
    tokens = _issue_tokens(req.username)
    return {"allowed": True, "session_id": session_id, "tokens": tokens, "score": score}


@app.post("/token")
def token(req: TokenRequest):
    if req.grant_type != "refresh_token" or not req.refresh_token:
        raise HTTPException(status_code=400, detail="unsupported_grant")

    rec = _refresh_get(req.refresh_token)
    now = int(time.time())
    if not rec or rec["exp"] < now:
        raise HTTPException(status_code=401, detail="invalid_or_expired_refresh_token")

    # rotate refresh token
    _refresh_del(req.refresh_token)
    return _issue_tokens(rec["sub"])
