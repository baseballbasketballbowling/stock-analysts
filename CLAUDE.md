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
