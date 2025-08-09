import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

import numpy as np
import pandas as pd
from dateutil import parser as dateparser


@dataclass
class Encoders:
    user_freq: Dict[str, int]
    ip_freq: Dict[str, int]
    device_freq: Dict[str, int]
    ua_freq: Dict[str, int]
    ja4_freq: Dict[str, int]
    ja4h_freq: Dict[str, int]
    ja4s_freq: Dict[str, int]


def _parse_ts(ts: Any) -> datetime:
    if isinstance(ts, (int, float)):
        return datetime.utcfromtimestamp(ts)
    if isinstance(ts, str):
        try:
            return dateparser.isoparse(ts)
        except Exception:
            # fallback: try float epoch in string
            try:
                return datetime.utcfromtimestamp(float(ts))
            except Exception:
                raise ValueError(f"Unrecognized timestamp format: {ts!r}")
    raise ValueError(f"Unsupported timestamp type: {type(ts)}")


def fit_encoders(df: pd.DataFrame) -> Encoders:
    def freq_map(series: pd.Series) -> Dict[str, int]:
        vc = series.fillna("__NA__").astype(str).value_counts()
        return {k: int(v) for k, v in vc.items()}

    return Encoders(
        user_freq=freq_map(df.get("user_id", pd.Series(dtype=object))),
        ip_freq=freq_map(df.get("ip", pd.Series(dtype=object))),
        device_freq=freq_map(df.get("device_id", pd.Series(dtype=object))),
        ua_freq=freq_map(df.get("user_agent", pd.Series(dtype=object))),
        ja4_freq=freq_map(df.get("ja4", pd.Series(dtype=object))),
        ja4h_freq=freq_map(df.get("ja4h", pd.Series(dtype=object))),
        ja4s_freq=freq_map(df.get("ja4s", pd.Series(dtype=object))),
    )


def save_encoders(enc: Encoders, path: str) -> None:
    with open(path, "w") as f:
        json.dump({
            "user_freq": enc.user_freq,
            "ip_freq": enc.ip_freq,
            "device_freq": enc.device_freq,
            "ua_freq": enc.ua_freq,
            "ja4_freq": enc.ja4_freq,
            "ja4h_freq": enc.ja4h_freq,
            "ja4s_freq": enc.ja4s_freq,
        }, f)


def load_encoders(path: str) -> Encoders:
    with open(path, "r") as f:
        data = json.load(f)
    return Encoders(**data)


def _row_to_features(row: Dict[str, Any], enc: Encoders) -> Tuple[np.ndarray, Dict[str, Any]]:
    ts = _parse_ts(row.get("timestamp"))
    hour = ts.hour
    dow = ts.weekday()

    success_val = row.get("success")
    if isinstance(success_val, bool):
        success = 1 if success_val else 0
    else:
        try:
            success = int(success_val)
            if success not in (0, 1):
                success = 0
        except Exception:
            success = 0

    user_id = str(row.get("user_id", "__NA__"))
    ip = str(row.get("ip", "__NA__"))
    device_id = str(row.get("device_id", "__NA__"))
    ua = str(row.get("user_agent", "__NA__"))

    # JA4+/ASN input fields

    ja4 = str(row.get("ja4", "__NA__"))
    ja4h = str(row.get("ja4h", "__NA__"))
    ja4s = str(row.get("ja4s", "__NA__"))
    # frequency encodings
    ja4f = enc.ja4_freq.get(ja4, 0)
    ja4hf = enc.ja4h_freq.get(ja4h, 0)
    ja4sf = enc.ja4s_freq.get(ja4s, 0)
    ja4f_log = math.log1p(ja4f)
    ja4hf_log = math.log1p(ja4hf)
    ja4sf_log = math.log1p(ja4sf)

    # ASN/Reputation features (filled later in service at runtime if available)
    asn = int(row.get("asn", 0)) if row.get("asn") not in (None, "", "__NA__") else 0
    ip_rep = float(row.get("ip_rep", 0.0)) if row.get("ip_rep") not in (None, "", "__NA__") else 0.0

    # frequency encodings with small smoothing to avoid zeros
    uf = enc.user_freq.get(user_id, 1) # 0
    ipf = enc.ip_freq.get(ip, 1) # 0
    df_ = enc.device_freq.get(device_id, 1) # 0
    uaf = enc.ua_freq.get(ua, 1) # 0

    uf_log = math.log1p(uf)
    ipf_log = math.log1p(ipf)
    df_log = math.log1p(df_)
    uaf_log = math.log1p(uaf)

    features = np.array([
        hour,
        dow,
        success,
        uf_log,
        ipf_log,
        df_log,
        uaf_log,
        ja4f_log,
        ja4hf_log,
        ja4sf_log,
        ip_rep,
    ], dtype=float)

    expl = {
        "hour": hour,
        "day_of_week": dow,
        "success": success,
        "user_freq": uf,
        "ip_freq": ipf,
        "device_freq": df_,
        "ua_freq": uaf,
        "ja4_freq": ja4f,
        "ja4h_freq": ja4hf,
        "ja4s_freq": ja4sf,
        "ip_rep": ip_rep,
    }
    return features, expl


def df_to_features(df: pd.DataFrame, enc: Encoders) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    feats = []
    expl_list = []
    for _, row in df.iterrows():
        f, e = _row_to_features(row.to_dict(), enc)
        feats.append(f)
        expl_list.append(e)
    X = np.vstack(feats) if feats else np.zeros((0, 11), dtype=float)
    return X, expl_list


def json_events_to_features(events: List[Dict[str, Any]], enc: Encoders) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    feats = []
    expl_list = []
    for ev in events:
        f, e = _row_to_features(ev, enc)
        feats.append(f)
        expl_list.append(e)
    X = np.vstack(feats) if feats else np.zeros((0, 11), dtype=float)
    return X, expl_list
