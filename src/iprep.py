from typing import Optional, Dict, Tuple

# Tiny demo IP -> (ASN, reputation_score[0..100], as_name)
# reputation_score: higher is worse in this demo.
_REP_DB: Dict[str, Tuple[int, int, str]] = {
    "203.0.113.200": (64550, 80, "BadNet Inc"),
    "203.0.113.201": (64550, 75, "BadNet Inc"),
    "203.0.113.10":  (64551, 60, "ShadyCloud"),
    "198.51.100.9":  (64500, 5,  "CorpISP"),
}

def lookup_ip(ip: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    rec = _REP_DB.get(ip)
    if not rec:
        # Unknown IP: treat as low reputation risk (good) for demo
        return None, 0, None
    return rec
