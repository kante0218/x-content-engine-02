"""Anthropic 互換シム(実体は Google Gemini / 無料枠)。

既存コードは `from anthropic import Anthropic` → `client.messages.create(...)`
→ `res.content[i].text / .type` を使う。それをそのまま Gemini に流すための薄い互換層。
追加依存なし(標準ライブラリの urllib のみ)。

環境変数:
  GEMINI_API_KEY    … https://aistudio.google.com/apikey で発行したキー(必須)
  GEMINI_API_KEY_2  … 予備キー(任意)。主キーが無効化されたら自動で切替
  GEMINI_MODEL      … 最優先モデル(任意)。未指定なら gemini-flash-latest
使い方(既存の import を差し替えるだけ):
  from llm_gemini import Anthropic

堅牢化ポリシー(投稿を止めないため):
  - モデルは複数候補を順に試す(404/モデル起因の400は次のモデルへ)
  - キー無効(API_KEY_INVALID 等)は予備キーへ自動フォールバック
  - 429/5xx/ネットワークエラーは指数バックオフでリトライ
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 上から順に試す。gemini-flash-latest は常に最新の flash を指すエイリアスなので
# モデル世代の廃止(例: 2.5-flash が新規プロジェクトで 404)に強い。
_DEFAULT_MODEL_CHAIN = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-lite-latest",
]


def _model_chain() -> list[str]:
    env = os.getenv("GEMINI_MODEL")
    chain = ([env] if env else []) + _DEFAULT_MODEL_CHAIN
    seen: set[str] = set()
    out: list[str] = []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


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


def _is_key_error(code: int, detail: str) -> bool:
    if code in (401,):
        return True
    if code in (400, 403):
        d = detail.upper()
        return "API_KEY" in d or "API KEY" in d or "PERMISSION_DENIED" in d
    return False


class _Messages:
    def __init__(self, api_keys: list[str]):
        self._api_keys = api_keys

    def create(self, model=None, max_tokens=2048, system=None, messages=None, **kwargs):
        user_text = _extract_text(messages)
        body = {
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": {
                # 日本語は 1 文字あたりのトークンが多いので下限を確保
                "maxOutputTokens": max(int(max_tokens or 2048), 2048),
                "temperature": float(kwargs.get("temperature", 1.0)),
                # 思考を切って本文を直接出させる(空応答防止 & 無料枠節約)
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
        data = json.dumps(body).encode("utf-8")

        last_err: Exception | None = None
        for key in self._api_keys:
            for gm in _model_chain():
                text, err, next_action = self._try_model(gm, key, data)
                if text is not None:
                    return _Response(text)
                last_err = err
                print(f"[llm_gemini] model={gm} failed ({err}); next={next_action}")
                if next_action == "next_key":
                    break  # モデルを変えても無駄 → 次のキーへ
        raise RuntimeError(f"Gemini 応答取得に失敗(全モデル/全キー試行済): {last_err}")

    def _try_model(self, model: str, key: str, data: bytes):
        """(text, err, next_action) を返す。next_action: 'next_model' | 'next_key'"""
        url = _ENDPOINT.format(model=model)
        last_err: Exception | None = None
        for attempt in range(4):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = self._parse(payload)
                if text:
                    return text, None, ""
                last_err = RuntimeError(
                    f"Gemini 空応答: {json.dumps(payload, ensure_ascii=False)[:400]}"
                )
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    # 一時的エラー → 同モデルでリトライ
                    last_err = RuntimeError(f"Gemini HTTP {e.code}: {detail}")
                    time.sleep(2 ** attempt * 2)
                    continue
                err = RuntimeError(f"Gemini API エラー status={e.code} body={detail}")
                if _is_key_error(e.code, detail):
                    return None, err, "next_key"
                # 404(モデル廃止)やモデル起因の 400 等 → 次のモデルへ
                return None, err, "next_model"
            except urllib.error.URLError as e:
                last_err = e
                time.sleep(2 ** attempt * 2)
                continue
            time.sleep(2 ** attempt * 2)
        return None, last_err, "next_model"

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
        keys = [
            api_key,
            os.getenv("GEMINI_API_KEY"),
            os.getenv("GOOGLE_API_KEY"),
            os.getenv("GEMINI_API_KEY_2"),
        ]
        uniq: list[str] = []
        for k in keys:
            if k and k not in uniq:
                uniq.append(k)
        if not uniq:
            raise RuntimeError(
                "GEMINI_API_KEY が未設定(https://aistudio.google.com/apikey で無料発行)"
            )
        self.messages = _Messages(uniq)
