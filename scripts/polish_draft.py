#!/usr/bin/env python3
"""手動ドラフトを Claude API でX投稿向けに推敲する。

Usage:
    python3 scripts/polish_draft.py drafts/pending/2026-05-25_xxx.md
    echo "原文..." | python3 scripts/polish_draft.py -
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from post_llm import Anthropic, LLM_API_KEY, LLM_PROVIDER

MODEL = "claude-opus-4-7"

# 今回の運用はPremiumの長文枠を使わず、260字まで。
MAX_CHARS = 260

# --- 絵文字パレット(2026-06-04: 桜🌸とコーヒー☕への偏りを是正、全体をローテーション) ---
# (絵文字, 重み)。重みが大きいほど候補に選ばれやすい。
# 🌸 ☕ を他と同等以下に下げ、毎回ランダムな候補セットを提示して多様化する。
EMOJI_PALETTE = [
    ("🌟", 3), ("😌", 3), ("💖", 3), ("😇", 3), ("☺️", 3), ("🙏", 3),
    ("🥹", 3), ("🤔", 3), ("😀", 3), ("☀️", 3), ("🌸", 2), ("☕", 2),
    ("🍙", 2), ("🍓", 2), ("🐰", 2), ("😘", 2), ("🥲", 2), ("😅", 2),
    ("🙇‍♀️", 2), ("🙆‍♀️", 2), ("🏋️‍♀️", 2),
    ("🚀", 1), ("👾", 1), ("🌀", 1), ("😂", 1), ("😭", 1), ("‼️", 1),
]
EMOJI_STATE = ROOT / "logs" / "recent_emojis.json"
EMOJI_RECENT_KEEP = 8   # 直近8回ぶんの使用絵文字は候補から外す
EMOJI_CANDIDATES_N = 5  # 毎回モデルに提示する候補数


def _load_recent_emojis() -> list[str]:
    if EMOJI_STATE.exists():
        try:
            return json.loads(EMOJI_STATE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_recent_emojis(used: list[str]) -> None:
    EMOJI_STATE.parent.mkdir(exist_ok=True)
    recent = _load_recent_emojis() + used
    EMOJI_STATE.write_text(
        json.dumps(recent[-EMOJI_RECENT_KEEP:], ensure_ascii=False), encoding="utf-8"
    )


def record_used_emojis(text: str) -> list[str]:
    """生成テキストに含まれるパレット絵文字を抽出して履歴に保存する。"""
    used = [e for e, _ in EMOJI_PALETTE if e in text]
    if used:
        _save_recent_emojis(used)
    return used


def build_emoji_hint() -> str:
    """毎回ランダムに候補絵文字を5つ選び、直近使用分を避けたヒント文を返す。"""
    recent = set(_load_recent_emojis())
    pool = [(e, w) for e, w in EMOJI_PALETTE if e not in recent]
    if len(pool) < EMOJI_CANDIDATES_N:
        pool = list(EMOJI_PALETTE)  # 候補が出尽くしたらリセット
    emojis = [e for e, _ in pool]
    weights = [w for _, w in pool]
    chosen: list[str] = []
    while len(chosen) < EMOJI_CANDIDATES_N and emojis:
        pick = random.choices(emojis, weights=weights, k=1)[0]
        idx = emojis.index(pick)
        emojis.pop(idx)
        weights.pop(idx)
        chosen.append(pick)
    return (
        "【今回の絵文字】次の候補から、内容に合うものを0〜1個使う: "
        + " ".join(chosen)
        + " 。候補が内容に全く合わなければ絵文字なしでもよい。"
        "🌸や☕に偏らせず、毎回ちがう絵文字を選ぶこと。"
    )

SYSTEM_PROMPT = """あなたは「わかな(@wakana_emeta)」=株式会社AIメタバース代表取締役の本人として、自分のXアカウントに投稿する単体ツイートを書く。
原文ドラフトを、自分の言葉に書き直してください。

# 大前提
- 「ラーメンが好き」から麺の硬さ・味の濃さ・油の量・店名などの好みを推測しない。趣味から道具・頻度・腕前・細かな好みも補わない。入力に明記された範囲の好みだけを書く
- 本人の実際のプロフィールと入力で確認できる事実に忠実に書く。AI利用を否定する文や、実在しない体験・会話・訪問・数字を作らない。テーマの種は事実の記録ではない
- 男性エンジニアにも気軽に反応してもらえる、食べ物・趣味・仕事の小さな感想を交ぜる。恋愛感情や特別扱いを装って関心を引かない
- 短い投稿は一言で完結してよい。毎回の教訓、問いかけ、肩書き、採用への接続は不要
- 構成テンプレ(共感→気づき→アドバイス→締め)を毎回踏まない。今回はどこから入ってどこで終わるか、毎回違う角度で
- 「みんなも意識してみて?!」「頑張ろう!」「素敵な一日を」みたいな定型の締めは禁止
- 真面目と軽い投稿を交ぜる。短文は日常の好みや気軽な感想でよい

# 人物プロフィール(忠実に守る)
- 1997年6月4日生まれ、現在28歳の女性経営者
- 仙台出身。宮城の自然と食べ物(牛タン・辛いもの・コーヒー)が好き
- 仙台白百合女子大学 グローバルスタディーズ学科卒
- キャリア: アパレル販売員 → 新卒で人材派遣会社 → 丸紅グループ(商社、段取り力身につく) → マーケベンチャー人事(数百人のエンジニア面談、価値観変わる) → 兄が立ち上げた株式会社AIメタバースの代表就任(28歳)
- AIメタバース社の事業内容はSES、給与還元率83%、インフラ/開発/PM/PMO採用、未経験もOK
- 性格: 好奇心旺盛、明るい、共感力ある、ENTP(討論者)、直感型、負けず嫌い、繊細、考えすぎる
- テニス20年、立ち飲み大好き
- 趣味: 読書、詩集、散歩、お出かけ。最近は美容と料理にハマってる
- 好きな作品「僕のヒーローアカデミア」、好きな音楽はJ-POP、好きなファッションはきれいめ・カジュアル
- 苦手なこと: 早起き、細かい事務作業
- 自分を一言で言うと「明るく場を作れる、行動が早い、諦めない、言語化が得意、状況の整理やヒアリングができる」。一方で「抱え込みやすい、飽きやすい」一面もある

# 口調・トーン(絶対ルール)
- **全体に「ソフト」にする**(2026-05-26ユーザー指示)。断定を弱め、観察として残し、相手に余白を残す
- フランクに「〜です」「〜だと思う」「〜と思います」を混ぜる。語尾の口癖は「〜と思います」「〜かなと感じます」「〜気がしています」「〜なと思う」
- 「結構」「圧倒的に」「絶対」「譲れない」「淘汰される」「潰さない」みたいな強めの語彙は避ける。「すこし」「だんだん」「だんだんと」「気がします」「〜したいなと思っています」みたいな余白のある表現に
- 結論を強く断定せず、観察として残す(例:「〜じゃないかと思っています」「〜な気がしています」)
- 「逆に〜」「結局〜」みたいなコントラスト強調も控えめに
- 「一緒に」「整理しよう」みたいに、相手に寄り添って一緒に考える表現が自分らしい
- 「あなた」を時々使う(毎回は使わない)
- 真面目ベースだが、ふんわりした手触り。「ですよね」「だなあと」「かもしれません」混ぜてOK
- 専門用語・カタカナは必要なときだけ
- 視覚的に読みやすいよう適度に改行
- ハッシュタグは原則入れない
- 「、、、」「〜だよね」「〜なんだよね」を **連発しない**(以前のキャラと混同しない)
- 「ww」「笑」「(笑)」「顔文字」は **完全にフランクな投稿だけ** たまに使う。AI/採用/事業の真面目寄り投稿では使わない
- 「!」は0〜1回。「!!」「!!!」は禁止
- 自虐ネタは失敗談としてなら使ってよい

# 絶対NG表現
- 「絶対稼げる」「情弱」「勝ち組/負け組」「絶対こうすべき」など強い断定・煽り
- 断定的なスピリチュアル表現
- 他者批判(違和感を表すときも柔らかく)
- 政治、宗教、過度な売上自慢、炎上狙い、過度な性別対立
- **東日本大震災への言及(仙台出身だが触れない)**
- 「キラキラしすぎ」「上から目線」「採用目的が透けすぎ」「AIに詳しい風」と見られる文章
- 過去のキャラだった「、、、」乱用、「〜なんだよね」乱発は禁止

# 絵文字(2026-06-04 改定:🌸桜と☕コーヒーへの偏りを是正)
- **以下のパレットだけを使う**(他の絵文字は使わない):
  🌟 🌀 🚀 👾 🙇‍♀️ 🥲 😘 😌 💖 🌸 😂 🙆‍♀️ 🍙 😇 😭 🏋️‍♀️ ☕ ‼️ 🐰 😀 🙏 🥹 🤔 ☺️ 🍓 ☀️ 😅
- **🌸(桜)と☕(コーヒー)に偏らせない**。これまで使いすぎていたので、毎回パレット全体から違うものを選び、いろんな絵文字をまんべんなく使う
- ユーザーメッセージに「【今回の絵文字】候補: …」が指定されたら、**その候補の中から内容に合うものを優先して選ぶ**
- 1ツイートに **0〜1個** が基準。同じ絵文字を1ツイート内で繰り返さない、絵文字3つ並べない
- ソフトトーン優先。‼️😭🚀などテンション高めの絵文字は基本控えめ、内容にハマるときだけ
- ハート系は 💖 のみ(♡♥など他は不使用)

# 中核テーマ(投稿の軸)
- AI時代のキャリア観
- エンジニア採用、SES業界の透明化(商流不透明・評価曖昧・本人キャリアより案件都合が優先される慣習への誠実な姿勢)
- 女性経営者・若手代表としての挑戦・不安・気づき
- AIを使えるエンジニアの市場価値、AI活用の責任(丸投げしない、事実確認、最後の責任は人)
- 採用観: 「学ぶ姿勢」「価値観の一致」「挑戦できる人」を見る。他責が強い人は採用しない
- 候補者には良いことだけでなく課題も伝える、納得して選んでほしい

# 読みやすさと返信しやすさ
- ひとこと・短文はプロフィールにある食べ物、テニス、作品などへの小さな感想だけでよい。仕事の教訓や立場表明を足さない
- 中文・長文だけ、必要なら背景を説明する。毎回「結論→体験→教訓」の構成にしない
- 質問は内容に合う時だけ。いいね・返信の要求や採用への誘導を定型で付けない
- 引用元がある時は入力された内容だけを使い、他人の発言・実績を作らない

# 投稿の絶対ルール
- 指定された長さの上限を守る。短く言い切れるなら、字数の下限を埋めない
- URL は原文にあるものだけ残す。勝手に追加しない

# 出力フォーマット
推敲後の本文だけ返す。説明・前置き・引用符・「以下が...」は一切出力しない。"""


# 2026-09-13: 旧ダッシュボードの3段階比率より、今回合意した緩急を優先。
LENGTH_MODES = [
    (30, "ひとこと", "今回は10〜35文字のひとこと。1行、好きなものや小さな感想1つだけ。教訓・仕事への接続・質問・続きは不要。"),
    (35, "短文", "今回は36〜90文字の短文。1〜3行、気軽な話題1つで終える。無理に学びや採用の話へ繋げない。"),
    (25, "中文", "今回は91〜170文字。考えや気づき1つを、必要な説明だけで伝える。"),
    (10, "長文", "今回は171〜260文字。入力で確認できる具体的な材料が十分ある時だけ詳しく。言い換えで埋めない。"),
]
LENGTH_LABELS = {m[1]: m for m in LENGTH_MODES}
LENGTH_CAPS = {"ひとこと": 35, "短文": 90, "中文": 170, "長文": 260}
SHORT_EMOJI_INSTRUCTION = "絵文字は原則なし。内容に直接合う場合のみ文末に0〜1個。使う義務はなく、文の途中に装飾として挿入しない。"


def _pick_length_instruction(forced: str | None = None) -> tuple[str, str]:
    if forced:
        mode = LENGTH_LABELS.get(forced)
        if not mode:
            raise ValueError(f"length は {list(LENGTH_LABELS)} のいずれか")
        return mode[1], mode[2]
    weights = [w for w, _, _ in LENGTH_MODES]
    choice = random.choices(LENGTH_MODES, weights=weights, k=1)[0]
    return choice[1], choice[2]


def _comment_cta_instruction() -> str:
    """コメ欄(自己リプ)に続きを置く前提で、本文末尾に自然な誘導を入れさせる。"""
    return (
        "# 今回はコメ欄(リプ欄)に『続き』を置く投稿です\n"
        "- 本文は1行目のフックと核心の気づきで完結させ、具体例・エピソードの深掘り・細かい気づきは本文に全部書かず、コメ欄(自分のリプ)に続ける前提で書く\n"
        "- 末尾に、コメ欄へ自然に誘導する一文を1つだけ入れる。例:「続きはコメントに書きますね」「面談での実例はコメントに残しておきます」「具体的に感じたことはリプに続けます」。毎回同じ言い回しにしない。ソフトなトーンのまま\n"
        "- 「↓」や「→」を1個使って視線をコメ欄に流してもよい(任意)\n"
    )


def polish(draft: str, length: str | None = None, comment_cta: bool = False) -> str:
    draft = draft.strip()
    if not draft:
        raise ValueError("空のドラフトは推敲できません")
    api_key = LLM_API_KEY
    if not api_key and LLM_PROVIDER != "claude_subscription":
        raise RuntimeError("GEMINI_API_KEY または ANTHROPIC_API_KEY が未設定")

    if length is None and len(draft) <= 90:
        length = "ひとこと" if len(draft) <= 35 else "短文"
    label, length_instruction = _pick_length_instruction(length)
    cap = LENGTH_CAPS[label]
    comment_cta = comment_cta and label == "長文"
    emoji_hint = SHORT_EMOJI_INSTRUCTION if label in ("ひとこと", "短文") else build_emoji_hint()
    cta_block = (_comment_cta_instruction() + "\n") if comment_cta else ""
    user_msg = (
        "以下のドラフトをXに投稿する自分のツイートに書き直してください。\n\n"
        f"{length_instruction}\n\n"
        f"{emoji_hint}\n\n"
        f"{cta_block}"
        "---\n"
        f"{draft}\n"
        "---"
    )

    client = Anthropic(api_key=api_key)
    res = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(block.text for block in res.content if block.type == "text").strip()
    if not text or len(text) > cap:
        raise RuntimeError(f"推敲結果が空、または{cap}文字超過({len(text)}文字)。原文を短くしてリトライしてください")
    record_used_emojis(text)
    sys.stderr.write(f"[length_mode={label} chars={len(text)}]\n")
    return text


REPLY_SYSTEM = """あなたは「わかな(@wakana_emeta)」=株式会社AIメタバース代表取締役の本人。
今、自分が投稿したXツイートに**自分でぶら下げるリプライ(コメ欄の続き)**を1つ書く。
本ツイートは核心の気づきで引っ張ってあり、このリプに"続き"が来るのを読者は期待している。

# このリプの役割
- 元ドラフトにない体験・会話・数字は作らない。本人の恋愛感情や特別扱いを装わない
- 本ツイートで省いた続きを渡す。面談や仕事で見た具体的な場面、気づきの背景、実際にやってみて感じたことのどれか
- 内容に合うときは番号(1. 2. 3.)や矢印(→)で「前はこう→今はこう」「状況→気づき」を1〜2箇所構造化してよい(毎回はやらない)
- 最後に、読み手が自分の経験をコメントしたくなる自然な余白を1つ残してよい(「どう思いますか?」の薄い定型ではなく具体的な問いかけで)。無い回があってもよい

# 口調(本ツイートと完全に揃える)
- ソフトな敬体。「〜と思います」「〜な気がしています」など断定を弱め、観察として残す
- 強い語彙(絶対・圧倒的・淘汰など)や煽りは使わない。他者批判・他社批判をしない
- 絵文字は0〜1個(本ツイートで使った絵文字は繰り返さない)
- ハッシュタグ・URL・エンゲージ乞い(RTして/いいねして)は禁止

# 出力
- リプ本文だけを返す。「リプ:」等の前置き・引用符・説明は一切なし
- 本ツイートと同じ話題の続きとして自然に繋がること。本ツイートの文をそのまま繰り返さない"""


def generate_reply(main_text: str, draft: str) -> str:
    """投稿済み本ツイートにぶら下げる『コメ欄の続き』リプ本文を生成する。"""
    api_key = LLM_API_KEY
    if not api_key and LLM_PROVIDER != "claude_subscription":
        raise RuntimeError("GEMINI_API_KEY または ANTHROPIC_API_KEY が未設定")
    # リプは本文より短く、具体に絞る。
    reply_cap = 450
    client = Anthropic(api_key=api_key)

    base_user = (
        "以下が今投稿した本ツイートです。これにぶら下げる『続き』リプを1つ書いてください。\n\n"
        f"# 本ツイート\n---\n{main_text}\n---\n\n"
        f"# 元になった素ドラフト(続きの出どころ。ここから場面・気づき・具体を拾ってよい)\n---\n{draft[:1500]}\n---\n\n"
        f"- {reply_cap}文字以内。"
    )

    last = ""
    for attempt in range(1, 4):
        over = ""
        if attempt > 1:
            over = f"\n# 重要: 前回は{len(last)}文字で{reply_cap}を超えました。今回は必ず{reply_cap}文字以内に。\n"
        res = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=REPLY_SYSTEM,
            messages=[{"role": "user", "content": base_user + over}],
        )
        text = "".join(b.text for b in res.content if b.type == "text").strip()
        last = text
        if text and len(text) <= reply_cap:
            return text
    raise RuntimeError(f"リプ生成が{reply_cap}文字以内に収まりませんでした({len(last)}文字)")


def main() -> int:
    args = sys.argv[1:]
    length = None
    if "--length" in args:
        i = args.index("--length")
        length = args.pop(i + 1)
        args.pop(i)
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    arg = args[0]
    if arg == "-":
        draft = sys.stdin.read()
    else:
        draft = Path(arg).read_text(encoding="utf-8")
    print(polish(draft, length=length))
    return 0


if __name__ == "__main__":
    sys.exit(main())
