#!/usr/bin/env python3
"""1投稿サイクル: drafts/pending があれば推敲、無ければテーマバンクから自動生成 → 投稿。

- pending に手動ドラフトがあればそれを推敲(手動が常に優先)。
- pending が空なら generate_draft でテーマバンクから自動生成。
- X_LIVE_POST=true のときだけ実投稿。false ならドライラン。
- 失敗したドラフトは drafts/failed/ に移動して理由ログを残す。
- ドラフトの1行目が `quote: <URL>` の場合は、そのツイートを引用RT扱いで投稿する。
- 自動生成投稿は drafts/posted/ に記録ファイルを残す。

Usage:
    python3 scripts/pipeline.py            # 通常(pending優先、無ければ自動生成)
    python3 scripts/pipeline.py --dry-run  # 実投稿せず生成/推敲のみ
    python3 scripts/pipeline.py --length 長文
    python3 scripts/pipeline.py --no-generate  # pending空なら何もしない(自動生成しない)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "scripts"))

from generate_draft import generate  # noqa: E402
from polish_draft import generate_reply, polish  # noqa: E402
from post_tweet import post  # noqa: E402


def _reply_thread_rate() -> float:
    """自己リプ(コメ欄に続き)を付ける確率。0〜1。既定0.3。"""
    try:
        r = float(os.getenv("X_REPLY_THREAD_RATE", "0.3"))
    except ValueError:
        return 0.3
    return min(max(r, 0.0), 1.0)

PENDING = ROOT / "drafts" / "pending"
POSTED = ROOT / "drafts" / "posted"
FAILED = ROOT / "drafts" / "failed"
LOGS = ROOT / "logs"

QUOTE_LINE_RE = re.compile(r"^\s*quote\s*:\s*(\S+)\s*$", re.IGNORECASE)
TWEET_ID_RE = re.compile(r"status/(\d+)")


def oldest_pending() -> Path | None:
    files = sorted(p for p in PENDING.iterdir() if p.is_file() and not p.name.startswith("."))
    return files[0] if files else None


def append_log(name: str, payload: dict) -> None:
    LOGS.mkdir(exist_ok=True)
    log_path = LOGS / "pipeline.log"
    payload = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "file": name, **payload}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def parse_quote_header(raw: str) -> tuple[str | None, str]:
    """ドラフト先頭の `quote: <URL>` 行を抽出。なければ (None, raw) を返す。"""
    lines = raw.splitlines()
    if not lines:
        return None, raw
    m = QUOTE_LINE_RE.match(lines[0])
    if not m:
        return None, raw
    url = m.group(1)
    id_m = TWEET_ID_RE.search(url)
    if not id_m:
        raise ValueError(f"quote URLからtweet_idを抽出できません: {url}")
    body = "\n".join(lines[1:]).lstrip()
    return id_m.group(1), body


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    no_generate = "--no-generate" in sys.argv
    live = os.getenv("X_LIVE_POST", "false").lower() == "true" and not dry_run

    length = None
    if "--length" in sys.argv:
        i = sys.argv.index("--length")
        length = sys.argv[i + 1]

    draft_path = oldest_pending()

    # --- コンテンツ確定: 手動ドラフト優先、無ければ自動生成 ---
    quote_id: str | None = None
    source_label: str  # ログ・ファイル名用
    is_generated: bool
    thread = False  # コメ欄に『続き』を自己リプで置くスレッド投稿にするか
    reply_text: str | None = None

    if draft_path is not None:
        is_generated = False
        source_label = draft_path.name
        raw = draft_path.read_text(encoding="utf-8")
        print(f"[draft] {draft_path.name}\n---\n{raw}\n---")
        try:
            quote_id, body = parse_quote_header(raw)
        except Exception as e:
            FAILED.mkdir(exist_ok=True)
            shutil.move(str(draft_path), str(FAILED / draft_path.name))
            append_log(draft_path.name, {"event": "parse_failed", "error": str(e)})
            print(f"[parse ERROR] {e}", file=sys.stderr)
            return 1
        if quote_id:
            print(f"[quote_rt] quote_tweet_id={quote_id}")
        # コメ欄に『続き』を置くスレッド投稿にするか(引用RTとは併用しない)
        thread = (quote_id is None) and (random.random() < _reply_thread_rate())
        try:
            text = polish(body, length=length, comment_cta=thread)
        except Exception as e:
            FAILED.mkdir(exist_ok=True)
            shutil.move(str(draft_path), str(FAILED / draft_path.name))
            append_log(draft_path.name, {"event": "polish_failed", "error": str(e)})
            print(f"[polish ERROR] {e}", file=sys.stderr)
            return 1
        # スレッド投稿: コメ欄に置くリプ本文を"投稿前に"生成する。
        # 失敗したら、本文だけが「コメ欄に続き」を約束する宙ぶらりんを避けるため
        # comment_cta なしで推敲し直し、単発投稿に落とす。
        if thread:
            try:
                reply_text = generate_reply(text, body)
                print(f"[reply] ({len(reply_text)}文字)\n---\n{reply_text}\n---")
            except Exception as e:
                print(f"[reply gen failed → 単発に切替] {e}", file=sys.stderr)
                append_log(draft_path.name, {"event": "reply_gen_failed", "error": str(e)})
                thread = False
                reply_text = None
                try:
                    text = polish(body, length=length)
                except Exception as e2:
                    FAILED.mkdir(exist_ok=True)
                    shutil.move(str(draft_path), str(FAILED / draft_path.name))
                    append_log(draft_path.name, {"event": "polish_failed", "error": str(e2)})
                    print(f"[polish ERROR] {e2}", file=sys.stderr)
                    return 1
    else:
        if no_generate:
            append_log("(none)", {"event": "no_pending_skip"})
            print("pending が空。--no-generate のため自動生成せず終了。", file=sys.stderr)
            return 0
        is_generated = True
        try:
            theme_key, text = generate(length=length)
        except Exception as e:
            append_log("(auto)", {"event": "generate_failed", "error": str(e)})
            print(f"[generate ERROR] {e}", file=sys.stderr)
            return 1
        source_label = f"auto:{theme_key}"
        print(f"[auto-generate] theme={theme_key}")

    print(f"[text] ({len(text)}文字)\n---\n{text}\n---")

    if not live:
        append_log(
            source_label,
            {"event": "dry_run", "text": text, "chars": len(text), "generated": is_generated, "quote_tweet_id": quote_id, "thread": thread, "reply_text": reply_text},
        )
        print("[dry-run] 実投稿はしませんでした。X_LIVE_POST=true で本番投稿。")
        return 0

    try:
        result = post(text, quote_tweet_id=quote_id)
    except Exception as e:
        msg = str(e)
        m = re.search(r"status=(\d+)", msg)
        code = int(m.group(1)) if m else None
        # 402(クレジット枯渇)/429(レート制限)/5xx(一時的サーバ障害)は、手動ドラフトを
        # failed に捨てず pending に温存し、赤ランにもせず soft skip(exit 0)。
        # クレジット復活後の次回スケジュール実行で自動リトライされ、投稿が失われない。
        if code in {402, 429, 500, 502, 503, 504} or "CreditsDepleted" in msg:
            append_log(source_label, {"event": "post_deferred", "status": code, "error": msg, "generated": is_generated})
            print(f"[post DEFERRED status={code}] 一時的エラー。次回リトライ: {msg}", file=sys.stderr)
            return 0
        if draft_path is not None:
            FAILED.mkdir(exist_ok=True)
            shutil.move(str(draft_path), str(FAILED / draft_path.name))
        append_log(source_label, {"event": "post_failed", "error": msg, "text": text, "generated": is_generated})
        print(f"[post ERROR] {e}", file=sys.stderr)
        return 1

    tweet_id = result.get("data", {}).get("id")

    # スレッド投稿: 本文投稿成功後、コメ欄に『続き』リプをぶら下げる。
    # リプ投稿失敗は本文投稿を巻き戻せないため、ログだけ残して成功扱いにする。
    reply_id: str | None = None
    if thread and reply_text and tweet_id:
        try:
            reply_result = post(reply_text, in_reply_to_tweet_id=tweet_id)
            reply_id = reply_result.get("data", {}).get("id")
            print(f"[reply posted] https://x.com/wakana_emeta/status/{reply_id}")
        except Exception as e:
            append_log(source_label, {"event": "reply_post_failed", "error": str(e), "tweet_id": tweet_id})
            print(f"[reply post failed(本文は投稿済み)] {e}", file=sys.stderr)

    POSTED.mkdir(exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    if is_generated:
        posted_name = f"{ts}_{theme_key}.md"
        (POSTED / posted_name).write_text(text + "\n", encoding="utf-8")
    else:
        posted_name = f"{ts}_{draft_path.name}"
        shutil.move(str(draft_path), str(POSTED / posted_name))
    (POSTED / (posted_name + ".result.json")).write_text(
        json.dumps(
            {"tweet_id": tweet_id, "text": text, "generated": is_generated, "source": source_label, "quote_tweet_id": quote_id, "thread": thread, "reply_tweet_id": reply_id, "reply_text": reply_text, "raw": result},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    event_payload = {
        "event": "posted",
        "tweet_id": tweet_id,
        "url": f"https://x.com/wakana_emeta/status/{tweet_id}",
        "generated": is_generated,
        "source": source_label,
    }
    if quote_id:
        event_payload["quote_tweet_id"] = quote_id
    if reply_id:
        event_payload["reply_tweet_id"] = reply_id
    append_log(posted_name, event_payload)
    print(f"[posted] https://x.com/wakana_emeta/status/{tweet_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
