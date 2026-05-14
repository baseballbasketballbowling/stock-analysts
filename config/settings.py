import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

# --- J-Quants 認証 ---
# マイページの「API Key」をセット（Googleアカウント登録ユーザー）
JQUANTS_API_KEY = os.environ.get("JQUANTS_API_KEY", "")
# メール登録ユーザーは以下を使用
JQUANTS_REFRESH_TOKEN = os.environ.get("JQUANTS_REFRESH_TOKEN", "")
JQUANTS_EMAIL = os.environ.get("JQUANTS_EMAIL", "")
JQUANTS_PASSWORD = os.environ.get("JQUANTS_PASSWORD", "")

# --- スクリーニング条件 ---
MARKET_CAP_MIN = 300e8    # 300億円
MARKET_CAP_MAX = 3000e8   # 3,000億円
EARNINGS_GROWTH_MIN = 0.20  # 前年同期比 +20%

# --- 売買ルール ---
TAKE_PROFIT = 0.07   # +7%
STOP_LOSS = -0.03    # −3%
MAX_HOLD_DAYS = 10   # 最大10営業日（約2週間）

# --- バックテスト ---
BACKTEST_START = "2022-01-01"
BACKTEST_END = "2024-12-31"
INITIAL_CAPITAL = 10_000_000  # 初期資金 1,000万円
POSITION_SIZE = 1_000_000     # 1トレードあたり 100万円

# --- パス ---
DATA_DIR = BASE_DIR / "data"
RESULTS_DIR = BASE_DIR / "results"
DATA_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# --- API ---
JQUANTS_BASE_URL = "https://api.jquants.com/v2"
