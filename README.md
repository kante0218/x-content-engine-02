# x-automation-wakana

@wakana_emeta(株式会社AIメタバース 代表 わかなさん)向け X投稿自動化(運用代行案件)

- API ティア: **Free** (500投稿/月、書き込みのみ)
- 認証: OAuth 1.0a User Context
- スケジューラ: Anthropic Cloud Routines
- ドラフト方式: 手動ドラフト + Claude API推敲

## ディレクトリ

```
x-automation-wakana/
├── .env               # 秘密鍵(コミット禁止)
├── .env.example       # 雛形
├── drafts/
│   ├── pending/       # 投稿待ちドラフト(.md or .txt)
│   ├── posted/        # 投稿済(_YYYYMMDD_HHMMSS_<原ファイル名> + .result.json)
│   └── failed/        # 失敗(推敲/投稿エラー)
├── logs/pipeline.log  # JSON Lines
└── scripts/
    ├── post_tweet.py    # 投稿のみ
    ├── polish_draft.py  # 推敲のみ
    └── pipeline.py      # pending → 推敲 → 投稿 → posted
```

## 初回セットアップ

1. `.env.example` を `.env` にコピーし、X Developer Portal の Keys を貼る
2. venv 起動: `source venv/bin/activate`
3. ドライラン: `python3 scripts/pipeline.py --dry-run`
4. 本番投稿に切り替え: `.env` の `X_LIVE_POST=true`

## 本番投稿

```bash
source venv/bin/activate
python3 scripts/pipeline.py
```

## ドラフトの書き方

`drafts/pending/` に好きなファイル名で原文を置く。1ファイル1ツイート。
推敲スクリプトが @wakana_emeta のペルソナ・280文字制限・採用文脈に合わせて整形する。
