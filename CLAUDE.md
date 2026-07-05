# Stock Analysts — J-Quants スクリーニング & バックテストシステム

## プロジェクト概要

J-Quants API を使って中型株の決算スクリーニングを行い、
エントリー・エグジット条件に基づいてバックテストを実施するシステム。

## ディレクトリ構成

```
stock-analysts/
├── CLAUDE.md
├── requirements.txt
├── config/
│   └── settings.py          # 定数・パラメータ設定
├── src/
│   ├── api/
│   │   └── jquants_client.py  # J-Quants API クライアント
│   ├── screener/
│   │   └── screener.py        # 銘柄スクリーニングロジック
│   └── backtest/
│       ├── engine.py          # バックテストエンジン
│       └── metrics.py         # パフォーマンス指標計算
├── data/                      # キャッシュデータ（gitignore）
├── results/                   # バックテスト結果出力
├── tests/
│   └── test_*.py
└── main.py                    # エントリーポイント CLI
```

## スクリーニング条件

| 項目 | 条件 |
|------|------|
| 時価総額 | 300〜3,000億円（Mid400 + Large70） |
| 営業利益 YoY | 直近決算で前年同期比 +20% 以上 |
| ROE | 12% 以上 |

## 売買ルール

| 項目 | 条件 |
|------|------|
| エントリー | 決算発表翌営業日の寄り付き（始値） |
| 利確 | +15% |
| 損切り | −5% |
| 最大保有期間 | 20営業日（約1ヶ月） |

## 自動ポートフォリオ管理（portfolio.json）

ポジションは pending → open → closed のライフサイクルで自動管理される：

1. **自動登録**: 夜19:00のスクリーニングでヒットした銘柄は pending（約定待ち）として自動登録
2. **自動約定**: `portfolio_post` 実行時にエントリー日の寄り付きで約定、TP/SL価格を自動設定
3. **自動売却判定**: 日足High/LowでTP/SL/保有期限到達を判定（バックテストエンジンと同一ロジック、
   同日両到達は損切り優先）。到達時は closed へ移動し **売却シグナル** をThreadsに投稿
4. エントリー日以降の全日足を毎回スキャンするため、実行が数日空いても取りこぼさない

### Threads 投稿スケジュール（GitHub Actions）

| JST | ジョブ | 内容 |
|-----|--------|------|
| 9:15 | morning-portfolio | 売却シグナル + **今日の注文プラン**（OCO指値の具体額・新規寄成買い） |
| 18:00 | disclosure-post | 本日の開示まとめ（全件数・中大型件数・ヒット詳細） |
| 19:00 | daily-screen | スクリーニング（ヒットは自動登録）→ 売却シグナル + 保有状況 |

portfolio.json の変更は各ジョブが github-script でリポジトリに自動コミットする。

⚠️ THREADS_ACCESS_TOKEN は60日で失効する。失効前に再発行して GitHub Secrets を更新すること。

## セットアップ

```bash
pip install -r requirements.txt
cp config/settings.py.example config/settings.py  # APIキーを設定
```

## J-Quants API 認証

J-Quants は**メールアドレス＋パスワード → IDトークン → リフレッシュトークン**の
2段階認証を使う。以下の環境変数を設定する：

```bash
export JQUANTS_EMAIL="your@email.com"
export JQUANTS_PASSWORD="yourpassword"
# または config/settings.py に直接記入（.gitignore 済み）
```

## バックテスト実行

```bash
# 過去3年分でバックテスト（デフォルト）
python main.py backtest

# 期間指定
python main.py backtest --start 2022-01-01 --end 2024-12-31

# スクリーニング結果のみ確認
python main.py screen --date 2024-03-31
```

## 出力ファイル

`results/` 配下に以下が生成される：

- `backtest_summary.csv` — 全トレード一覧
- `backtest_metrics.json` — PnL / 勝率 / PF / MDD 等
- `equity_curve.png` — 資産曲線グラフ

## 主要依存ライブラリ

| ライブラリ | 用途 |
|-----------|------|
| `requests` | J-Quants REST API 呼び出し |
| `pandas` | データ処理・時系列操作 |
| `numpy` | 数値計算 |
| `matplotlib` | グラフ描画 |
| `tqdm` | 進捗バー |

## 注意事項

- J-Quants の無料プランは日足データのみ（分足不可）。
- 決算データ（`/fins/statements`）は四半期単位で取得。
- `data/` フォルダはキャッシュ用。初回実行はAPIコール多数のため時間がかかる。
- レート制限：J-Quants は 1分あたり約60リクエストが目安。
