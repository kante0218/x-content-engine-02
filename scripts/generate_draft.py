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

# --- ネタの種(seeds): エミリ型。各テーマに具体的なエピソード起点を持たせ、
#     「ふわっとした一般論」を防ぎ保存・共感されやすい(バズる)投稿にする。
#     すべて わかな本人の世界(AIメタバースSES代表/仙台/テニス/丸紅/数百人面談/未経験OK/還元率83%)に限定。
#     ダッシュボード(xops_config)の各テーマに seeds があればそちらを優先する。 ---
THEME_SEEDS: dict[str, list[str]] = {
    "ai_career": [
        "「年収で選んだ転職ほど、あとで理由が思い出せなくなる」と面談で何度も見てきた話",
        "AIを使いこなす人と使われる人の差は、技術より『自分で問いを立てられるか』だと気づいた",
        "28歳でSESの代表になって、AI時代のキャリアで一番効くのは“基礎の理解”だと痛感した瞬間",
        "「AIで仕事がなくなる」より「AIを前提に設計できる人が強くなる」と感じるようになった理由",
        "便利なツールを渡された時、伸びる人と止まる人で最初の一言が違う話",
        "丸紅グループにいた頃の段取り力が、AI時代になって急に価値を持ち始めた不思議",
        "キャリアは“登る”より“組み替える”時代になったと、数百人と話して思うようになった",
        "スキルより先に「何をやりたくないか」を言える人のほうが、遠くまで行く気がしている",
    ],
    "recruit": [
        "採用で一番見ているのは、実は「できます」より「わかりません」を正直に言えるかどうか",
        "面談で経歴より深く聞くのは「前の会社で何に納得できなかったか」だという話",
        "スキルが高いのに見送った人と、荒削りでも採った人の分かれ目",
        "「学ぶ姿勢」を面談30分でどう見ているか、自分なりの質問の型",
        "内定を出すとき、良いことより先に“うちの課題”を全部話すようにしている理由",
        "他責が強い人を採用しないと決めたのは、過去に一度つまずいたから",
        "「価値観が合う」を綺麗事で終わらせないために、面談で必ずする逆質問",
        "覇気がない人を落とすより、その人が輝く場所を一緒に探したいと思っている",
    ],
    "ses_transparency": [
        "SESで「商流が何次請けか」を候補者に隠さず話すようにしたら、承諾の質が変わった話",
        "「案件都合で人を動かす」のが当たり前だった業界に、ずっと違和感があった",
        "還元率を83%と公開している理由。数字を出せない会社ほど、何かを隠している気がする",
        "エンジニアを“リソース”と呼ぶ文化が、実は一番人を静かに辞めさせている",
        "評価が曖昧なまま「頑張って」と言われ続けた人が、面談で少し疲れて見えた",
        "単価の話をタブーにしないほうが、結局みんな健やかに働ける気がしている",
        "「透明化」と言うのは簡単で、自社の商流を先に開示するのは正直こわい、という本音",
        "業界の不透明さに文句を言うより、自分の会社を一つ透明にするほうを選んだ",
    ],
    "ses_good": [
        "SESの一番の良さは「合わなければ次の現場に行ける」ことだと気づいた面談",
        "いろんな現場を渡り歩いた人ほど、引き出しが多くて話していて面白い",
        "自社開発が正義みたいな空気があるけど、SESで伸びた人を何人も見てきた",
        "「未経験からインフラで入って、少しずつPMOまで来ました」の人が眩しかった話",
        "働き方を柔らかく選べるのは、実はSESの隠れた強みだと思う",
        "一つの会社しか知らないより、複数の現場を知っているほうが強くなれる場面",
        "案件を“選べる”だけで、人の目の輝きが変わるのを何度も見た",
        "SESを下に見る風潮に、静かに違うよと言いたくなる時がある",
    ],
    "woman_ceo": [
        "28歳で代表になって、いまだに「社長」と呼ばれると一瞬固まる話",
        "若い女性経営者として軽く見られた瞬間より、信頼された瞬間のほうを覚えていたい",
        "兄が立ち上げた会社を継いで、比べられる怖さと向き合った日",
        "経営で一番こわいのは数字より「一人ひとりへのフォローが薄くなること」だと気づいた",
        "「行動が早い」が強みだけど、早すぎて抱え込む癖もある、という自己開示",
        "決められない日ほど、テニスに逃げて頭を空っぽにしている話",
        "弱音を吐ける相手がいるかどうかで、経営の折れやすさが変わる気がする",
        "「若いのにすごい」より「一緒に働きたい」と言われるほうが、正直うれしい",
    ],
    "ai_responsibility": [
        "AIの答えをそのまま出して少し反省した経験から学んだ「最後の責任は人」という線引き",
        "便利だからこそ、AIに“考えること”まで丸投げしない自分ルール",
        "AIが出した数字を鵜呑みにして一度ヒヤッとした話",
        "「AIがそう言ったので」を理由にする人には、仕事を任せにくいと感じる",
        "AIを使うほど、事実確認の地味な作業が大事になっていく不思議",
        "丸投げと活用は紙一重で、その境目は“自分で検証したか”だと思う",
        "効率化のためにAIを入れたはずが、人の判断を鈍らせないか時々こわくなる",
        "AIに“それっぽい間違い”を書かれた時、気づける人と気づけない人の差",
    ],
    "engineer_growth": [
        "市場価値が上がる人は、新しい技術より「学び方」をアップデートしている",
        "「AIを使えるか」で年収レンジがだんだん分かれ始めた実感",
        "数百人と面談して、伸びる人が共通して持っていた“一つの口ぐせ”",
        "30代からでも市場価値は上げられる、と面談で何度も見てきた話",
        "資格より「自分で調べて動いた経験」を語れる人のほうが強い",
        "成長が止まる人は、たいてい「学ぶ時間がない」から話し始める",
        "技術×コミュニケーションの掛け算ができる人が、今いちばん採られている",
        "「今のスキルであと何年戦えるか」を、たまに立ち止まって考えてほしい",
    ],
    "interview_insight": [
        "数百人と面談して一番意外だったのは「質問が上手い人=本音を話す人」ではなかったこと",
        "「志望動機」より「前職で何が悲しかったか」のほうが、その人が見える",
        "沈黙がこわくて喋りすぎる面談官だった自分を、少しずつ手放した話",
        "経歴が完璧な人ほど、なぜか一番聞きたいことを話してくれない",
        "面談で思わず胸が熱くなった人の話が、いまも忘れられない",
        "「本音を引き出す」って、質問の技術じゃなくて“待てるか”だと気づいた",
        "承諾後に残る人・離れる人の違いは、面談の“最後の5分”に出ていた",
        "何百回やっても面談は毎回こわい。慣れないことが誠実さだと思うようにしている",
    ],
    "inexperienced": [
        "「未経験OK」と言いながら、現実の厳しさも隠さず伝えるようにしている理由",
        "未経験から入って一番伸びた人は、たいてい“素直に手を動かせる人”だった",
        "「簡単に稼げる」と煽る広告に、静かにもやっとする",
        "未経験の人に最初に伝えるのは、夢より“最初の半年の地味さ”",
        "可能性はある、でも魔法はない。この2つを同時に伝える難しさ",
        "未経験を採るのは優しさじゃなくて、伸びしろへの投資だと思っている",
        "「年齢的にもう遅いですか」と聞かれるたび、遅くないと言える根拠",
        "下積みを下積みと思える人が、結局いちばん遠くまで行く",
    ],
    "company_culture": [
        "「一人で抱え込まないで」と言い続けるのは、自分が抱え込む人間だから",
        "誠実さって、うまくいってる時じゃなくて“失敗した時”に出ると思う",
        "チームで一番大事にしているのは、わからないことを“わからない”と言える空気",
        "会社の文化は理念のポスターじゃなくて、忙しい日の一言に出る",
        "挑戦してと言う前に、失敗を責めない環境を先に作らないといけないと気づいた",
        "「納得して働けているか」を、定期的にちゃんと聞くようにしている",
        "小さな会社だからこそ、一人が辞める重さと丁寧に向き合いたい",
        "強いチームより、正直なチームを作りたいと思っている",
    ],
    "learning": [
        "学び続けられる人と止まる人の差は、才能じゃなくて“変化を面白がれるか”",
        "28歳になって、知らないことを認めるのが前より少しこわくなくなった話",
        "AIが進むほど、人間側は“学び方”を学び直す必要がある気がしている",
        "詩集を読む習慣が、実は仕事の言語化に効いているという発見",
        "「わからない」を放置しない人が、静かに一番伸びていく",
        "変化を受け入れるって、頑張ることじゃなくて力を抜くことかもしれない",
        "学びが続かないのは意志の問題じゃなくて、環境の問題だと思うようになった",
        "昨日より少しだけ賢くなる、を続けた人の表情はだんだん変わる",
    ],
    "daily": [
        "仙台の朝、濃いめのコーヒーを淹れる時間だけは誰にも渡したくない",
        "テニス20年、勝ち負けより“無心になれる時間”に救われている話",
        "最近ハマった料理で、段取りは仕事と同じだと気づいた夜",
        "詩集を一篇読んでから一日を始めると、言葉が少し優しくなる気がする",
        "立ち飲みで隣の人と交わした何気ない一言が、妙に心に残っている",
        "美容にハマり始めて、自分を後回しにしない練習をしている話",
        "散歩中にふと浮かんだ考えが、会議より本質を突いていることがある",
        "牛タンと辛いものと詩集があれば、だいたいの週末は機嫌がいい",
    ],
}


def _active_bank() -> list[tuple[str, str, float, list[str]]]:
    """(key, hint, weight, seeds) のリスト。ダッシュボード設定があればそれを、無ければ組み込み値を使う。"""
    cfg = fetch_post_config()
    themes = enabled_themes(cfg)
    if themes:
        bank: list[tuple[str, str, float, list[str]]] = []
        for t in themes:
            key = str(t.get("key", "")).strip()
            label = str(t.get("label", "")).strip()
            if not key or not label:
                continue
            seeds = [str(s).strip() for s in t.get("seeds", []) if str(s).strip()]
            if not seeds:
                seeds = THEME_SEEDS.get(key, [])  # 設定にseedsが無ければ組み込みで補完
            bank.append((key, label, float(t.get("weight", 1)), seeds))
        if bank:
            sys.stderr.write(f"[config] テーマをダッシュボード設定から取得: {len(bank)}件\n")
            return bank
    return [
        (k, h, float(THEME_WEIGHTS.get(k, DEFAULT_WEIGHT)), THEME_SEEDS.get(k, []))
        for k, h in THEME_BANK
    ]


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


def pick_theme(bank: list[tuple[str, str, float, list[str]]]) -> tuple[str, str, str]:
    """重み付きでテーマを選ぶ。(key, hint, seed) を返す。直近テーマは避ける。

    seed = 選んだテーマの具体ネタ種を1つランダムに(無ければ空文字)。
    """
    recent = _load_recent()
    by_key = {k: (h, seeds) for k, h, _, seeds in bank}
    candidates = [(k, w) for k, _, w, _ in bank if k not in recent]
    if not candidates:
        candidates = [(k, w) for k, _, w, _ in bank]
    keys = [k for k, _ in candidates]
    weights = [w for _, w in candidates]
    chosen = random.choices(keys, weights=weights, k=1)[0]
    _save_recent(recent + [chosen])
    hint, seeds = by_key[chosen]
    seed = random.choice(seeds) if seeds else ""
    return chosen, hint, seed


def _call(hint: str, seed: str, length: str | None) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY が未設定(https://aistudio.google.com/apikey で無料発行)")
    _, length_instruction = _pick_length_instruction(length)
    emoji_hint = build_emoji_hint()
    seed_block = (
        f"今日書く具体的なネタの種(この切り口・エピソードを起点に): {seed}\n"
        "このネタの種は“起点”であって、そのままコピーしない。自分の体験・情景・具体を足して膨らませる。\n\n"
        if seed
        else ""
    )
    user_msg = (
        "あなた(わかな)として、Xに投稿する新しいツイートを1つ書いてください。\n"
        "過去の投稿の焼き直しにならないよう、今日ふと感じたことのように、具体的なエピソードや切り口で。\n"
        "宣伝・募集の押し売りにはせず、自然な独り言や気づきのトーンで。\n"
        "【最重要】1行目に一番おもしろい核心・意外な気づき・具体的な数字・短い問いを置く。"
        "状況説明や前置き(「前職で〜していた頃」等)から始めない。冒頭280字だけ読まれても意味が通り、続きを読みたくなる形に。\n"
        "ソフトな声色は崩さず、構造だけアルゴリズムに最適化する(システムの『バズ最適化』に従う)。\n\n"
        f"テーマ: {hint}\n\n"
        f"{seed_block}"
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
    theme_key, hint, seed = pick_theme(bank)
    text = _call(hint, seed, length)
    if len(text) > MAX_CHARS:
        text = _call(hint, seed, "中文")
    if len(text) > MAX_CHARS:
        raise RuntimeError(f"生成結果が{len(text)}文字>{MAX_CHARS}。テーマ={theme_key}")
    record_used_emojis(text)
    sys.stderr.write(f"[generate theme={theme_key} seed={'y' if seed else 'n'} chars={len(text)}]\n")
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
