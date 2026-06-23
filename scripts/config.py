#!/usr/bin/env python3
"""ダッシュボードの投稿設定(xops_config)を取得する。

GET {DASHBOARD_URL}/api/config?account=wakana  (ヘッダ x-sync-secret)
- 取得できれば dict を返す: {"themes":[...], "lengthWeights":{...}, "times":[...]}
- DASHBOARD_URL/SYNC_SECRET 未設定・ネットワーク失敗・パース失敗なら None。
  → 呼び出し側は各スクリプトの組み込みデフォルトにフォールバックする(投稿は止めない)。
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

ACCOUNT = "wakana"
_TIMEOUT = 8


def fetch_post_config() -> dict | None:
    base = os.getenv("DASHBOARD_URL", "").rstrip("/")
    secret = os.getenv("SYNC_SECRET", "")
    if not base or not secret:
        return None
    url = f"{base}/api/config?account={ACCOUNT}"
    req = urllib.request.Request(url, headers={"x-sync-secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as e:
        print(f"[config] 取得失敗（デフォルトで継続）: {e}")
        return None
    cfg = payload.get("config")
    if not isinstance(cfg, dict):
        return None
    return cfg


def enabled_themes(cfg: dict | None) -> list[dict] | None:
    """有効なテーマ(weight>0)だけ返す。無効・空なら None。"""
    if not cfg:
        return None
    themes = [
        t
        for t in cfg.get("themes", [])
        if isinstance(t, dict) and t.get("enabled", True) and float(t.get("weight", 0)) > 0
    ]
    return themes or None


def length_weights(cfg: dict | None) -> dict | None:
    if not cfg:
        return None
    lw = cfg.get("lengthWeights")
    if not isinstance(lw, dict):
        return None
    out = {k: max(0.0, float(lw.get(k, 0))) for k in ("short", "medium", "long")}
    return out if sum(out.values()) > 0 else None
