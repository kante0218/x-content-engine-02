"""Anthropic 互換シム(実体は Google Gemini / 無料枠)。

既存コードは `from anthropic import Anthropic` → `client.messages.create(...)`
→ `res.content[i].text / .type` を使う。それをそのまま Gemini に流すための薄い互換層。
追加依存なし(標準ライブラリの urllib のみ)。

環境変数:
  GEMINI_API_KEY  … https://aistudio.google.com/apikey で無料発行したキー(必須)
  GEMINI_MODEL    … 既定 gemini-2.5-flash(無料枠)。gemini-2.0-flash 等に上書き可
使い方(既存の import を差し替えるだけ):
  from llm_gemini import Anthropic
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class _Block:
    """Anthropic の content block 互換(.text / .type)。"""

    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _Response:
    def __init__(self, text: str):
        self.content = [_Block(text)]


def _extract_text(messages) -> str:
    parts = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
    return "\n\n".join(p for p in parts if p)


class _Messages:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def create(self, model=None, max_tokens=2048, system=None, messages=None, **kwargs):
        user_text = _extract_text(messages)
        body = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                # 日本語は 1 文字あたりのトークンが多いので下限を確保
                "maxOutputTokens": max(int(max_tokens or 2048), 2048),
                "temperature": float(kwargs.get("temperature", 1.0)),
                # 2.5 flash の思考を切って本文を直接出させる(空応答防止 & 無料枠節約)
                "thinkingConfig": {"thinkingBudget": 0},
            },
            # ペルソナ投稿が安全フィルタで誤ブロックされないよう緩める
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        url = _ENDPOINT.format(model=GEMINI_MODEL)
        data = json.dumps(body).encode("utf-8")

        last_err = None
        for attempt in range(4):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = self._parse(payload)
                if text:
                    return _Response(text)
                last_err = RuntimeError(
                    f"Gemini 空応答: {json.dumps(payload, ensure_ascii=False)[:400]}"
                )
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                # 429/5xx は一時的 → リトライ。それ以外は即失敗
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    last_err = RuntimeError(f"Gemini HTTP {e.code}: {detail}")
                    time.sleep(2 ** attempt * 2)
                    continue
                raise RuntimeError(f"Gemini API エラー status={e.code} body={detail}") from None
            except urllib.error.URLError as e:
                last_err = e
                time.sleep(2 ** attempt * 2)
                continue
            time.sleep(2 ** attempt * 2)
        raise RuntimeError(f"Gemini 応答取得に失敗: {last_err}")

    @staticmethod
    def _parse(payload: dict) -> str:
        cands = payload.get("candidates") or []
        if not cands:
            return ""
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()


class Anthropic:
    """`Anthropic(api_key=...)` 互換。api_key 省略時は GEMINI_API_KEY を使用。"""

    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY が未設定(https://aistudio.google.com/apikey で無料発行)"
            )
        self.messages = _Messages(key)
