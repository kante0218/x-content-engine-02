#!/usr/bin/env python3
"""テーマバンクから、わかなさんペルソナの新規ツイートを自動生成する。

- 直近に使ったテーマは一定数避けてローテーション(logs/recent_themes.json)。
- 口調・絵文字・長さ・NGはすべて polish_draft.SYSTEM_PROMPT に従う。
- 直接「投稿可能な完成文」を生成する(別途polish不要)。

Usage:
    python3 scripts/generate_draft.py             # 1本生成して標準出力
    python3 scripts/generate_draft.py --length 長文
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "scripts"))

from polish_draft import (  # noqa: E402
    MAX_CHARS,
    MODEL,
    SYSTEM_PROMPT,
    _pick_length_instruction,
    build_emoji_hint,
    record_used_emojis,
)
from config import enabled_themes, fetch_post_config  # noqa: E402

STATE = ROOT / "logs" / "recent_themes.json"
RECENT_KEEP = 6  # 直近6テーマは避ける

# (theme_key, 生成ヒント)
THEME_BANK = [
    ("ai_career", "AI時代のキャリア観。AIを使いこなせる人が強くなる、でも基礎力や理解も大事という話を、具体例まじりで"),
    ("recruit", "エンジニア採用で大事にしていること。納得感、学ぶ姿勢、価値観の一致。面談での実体験ベースで"),
    ("ses_transparency", "SES業界の透明化への課題意識。商流・評価の曖昧さに対して誠実に向き合いたい姿勢。ただし他社批判はしない"),
    ("ses_good", "SES業界の良さ。いろんな現場を経験できる、柔軟な働き方、相性の良い環境に出会える可能性"),
    ("woman_ceo", "28歳女性経営者・若手代表としての挑戦や不安、日々の気づき。等身大で、キラキラしすぎない"),
    ("ai_responsibility", "AI活用の責任。鵜呑みにしない、丸投げしない、最後の責任は人。便利だからこその線引き"),
    ("engineer_growth", "エンジニアの成長・市場価値。AIを使えるかで広がる可能性、学び続けることの大切さ"),
    ("interview_insight", "数百人のエンジニア面談で気づいたこと。本音を引き出す難しさ、キャリア選択の納得感"),
    ("inexperienced", "未経験エンジニアへのスタンス。簡単ではないけど可能性はある、現実も伝えた上で支援したい"),
    ("company_culture", "会社の文化・チームづくり。誠実さ・挑戦・納得感、一人で抱え込まないでほしいという想い"),
    ("learning", "学び続けること、変化を受け入れることの大切さ。AI時代を柔らかいトーンで"),
    ("daily", "日常・人間味。コーヒー、読書(詩集)、散歩、テニス、料理や美容、仙台のことなど、仕事の合間のふとした気づき。※東日本大震災には触れない"),
]
THEME_WEIGHTS = {"daily": 1}  # 日常系は頻度低め
DEFAULT_WEIGHT = 3
HINTS = dict(THEME_BANK)


def _active_bank() -> list[tuple[str, str, float]]:
    """(key, hint, weight) のリスト。ダッシュボード設定があればそれを、無ければ組み込み値を使う。"""
    cfg = fetch_post_config()
    themes = enabled_themes(cfg)
    if themes:
        bank = [
            (str(t["key"]), str(t.get("label", "")).strip(), float(t.get("weight", 1)))
            for t in themes
            if str(t.get("key", "")).strip() and str(t.get("label", "")).strip()
        ]
        if bank:
            sys.stderr.write(f"[config] テーマをダッシュボード設定から取得: {len(bank)}件\n")
            return bank
    return [(k, h, float(THEME_WEIGHTS.get(k, DEFAULT_WEIGHT))) for k, h in THEME_BANK]


def _load_recent() -> list[str]:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_recent(recent: list[str]) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(recent[-RECENT_KEEP:], ensure_ascii=False), encoding="utf-8")


def pick_theme(bank: list[tuple[str, str, float]]) -> tuple[str, str]:
    """重み付きでテーマを選ぶ。(key, hint) を返す。直近テーマは避ける。"""
    recent = _load_recent()
    hints = {k: h for k, h, _ in bank}
    candidates = [(k, w) for k, _, w in bank if k not in recent]
    if not candidates:
        candidates = [(k, w) for k, _, w in bank]
    keys = [k for k, _ in candidates]
    weights = [w for _, w in candidates]
    chosen = random.choices(keys, weights=weights, k=1)[0]
    _save_recent(recent + [chosen])
    return chosen, hints[chosen]


def _call(hint: str, length: str | None) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(".env に ANTHROPIC_API_KEY が未設定")
    _, length_instruction = _pick_length_instruction(length)
    emoji_hint = build_emoji_hint()
    user_msg = (
        "あなた(わかな)として、Xに投稿する新しいツイートを1つ書いてください。\n"
        "過去の投稿の焼き直しにならないよう、今日ふと感じたことのように、具体的なエピソードや切り口で。\n"
        "宣伝・募集の押し売りにはせず、自然な独り言や気づきのトーンで。\n"
        "【最重要】1行目に一番おもしろい核心・意外な気づき・具体的な数字・短い問いを置く。"
        "状況説明や前置き(「前職で〜していた頃」等)から始めない。冒頭280字だけ読まれても意味が通り、続きを読みたくなる形に。\n"
        "ソフトな声色は崩さず、構造だけアルゴリズムに最適化する(システムの『バズ最適化』に従う)。\n\n"
        f"テーマ: {hint}\n\n"
        f"{length_instruction}\n\n"
        f"{emoji_hint}"
    )
    client = Anthropic(api_key=api_key)
    res = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return "".join(b.text for b in res.content if b.type == "text").strip()


def generate(length: str | None = None) -> tuple[str, str]:
    """(theme_key, tweet_text) を返す。上限超なら1回だけ短めで再生成。"""
    bank = _active_bank()
    theme_key, hint = pick_theme(bank)
    text = _call(hint, length)
    if len(text) > MAX_CHARS:
        text = _call(hint, "中文")
    if len(text) > MAX_CHARS:
        raise RuntimeError(f"生成結果が{len(text)}文字>{MAX_CHARS}。テーマ={theme_key}")
    record_used_emojis(text)
    sys.stderr.write(f"[generate theme={theme_key} chars={len(text)}]\n")
    return theme_key, text


def main() -> int:
    length = None
    if "--length" in sys.argv:
        i = sys.argv.index("--length")
        length = sys.argv[i + 1]
    theme_key, text = generate(length=length)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
