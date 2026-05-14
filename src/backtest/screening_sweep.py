"""スクリーニング条件スイープ。

エントリー条件（TP/SL/保有期間）は前回スイープのベスト値に固定し、
スクリーニング条件の各次元を独立に変化させてバックテストする。

固定パラメータ: TP=15%, SL=-5%, MaxHold=20日
"""

import logging

import pandas as pd

from config.settings import RESULTS_DIR
from src.backtest.engine import build_price_dict, run_backtest
from src.backtest.metrics import compute_metrics, trades_to_df

logger = logging.getLogger(__name__)

FIXED_TP   = 0.15
FIXED_SL   = -0.05
FIXED_HOLD = 20
BASE_GROWTH = 0.20


def run_screening_sweep(all_candidates: pd.DataFrame, quotes_df: pd.DataFrame) -> pd.DataFrame:
    """
    all_candidates: screen_candidates を緩い条件で呼び出した結果。
                    EarningsGrowth, SalesGrowth, OperatingMargin, ROE,
                    EquityRatio, ForwardGuidance, CurPerType, S17Nm
                    などの列を含む。
    """
    price_dict = build_price_dict(quotes_df)
    results = []

    def test(cands: pd.DataFrame, category: str, condition: str) -> None:
        if len(cands) < 5:
            return
        trades = run_backtest(
            cands, quotes_df,
            take_profit=FIXED_TP, stop_loss=FIXED_SL, max_hold=FIXED_HOLD,
            price_dict=price_dict,
        )
        if not trades:
            return
        m = compute_metrics(trades_to_df(trades))
        er = m.get("exit_reasons", {})
        results.append({
            "Category":  category,
            "Condition": condition,
            "Trades":    m.get("total_trades", 0),
            "WinRate":   round(m.get("win_rate", 0), 4),
            "PF":        round(m.get("profit_factor", 0), 3),
            "Expect":    round(m.get("expectancy_pct", 0), 4),
            "Return":    round(m.get("total_return", 0), 4),
            "MaxDD":     round(m.get("max_drawdown", 0), 4),
            "TP_cnt":    er.get("take_profit", 0),
            "SL_cnt":    er.get("stop_loss", 0),
            "Time_cnt":  er.get("time_exit", 0),
        })

    base = all_candidates[all_candidates["EarningsGrowth"] >= BASE_GROWTH].copy()
    logger.info(f"ベース候補数: {len(base)}")

    # ── 1. 営業利益成長率 ──────────────────────────────
    logger.info("次元1: 営業利益成長率")
    for eg in [0.10, 0.15, 0.20, 0.30, 0.50]:
        c = all_candidates[all_candidates["EarningsGrowth"] >= eg]
        test(c, "EarningsGrowth", f"EG>={eg:.0%}")

    # ── 2. 売上高成長率 ───────────────────────────────
    if "SalesGrowth" in all_candidates.columns:
        logger.info("次元2: 売上高成長率")
        test(base, "SalesGrowth", "EG>=20% (SG条件なし)")
        for sg in [0.05, 0.10, 0.20, 0.30]:
            c = base[base["SalesGrowth"] >= sg]
            test(c, "SalesGrowth", f"EG>=20% & SG>={sg:.0%}")

    # ── 3. 営業利益率 ─────────────────────────────────
    if "OperatingMargin" in all_candidates.columns:
        logger.info("次元3: 営業利益率")
        test(base, "OperatingMargin", "EG>=20% (OM条件なし)")
        for om in [0.03, 0.05, 0.08, 0.12, 0.15]:
            c = base[base["OperatingMargin"] >= om]
            test(c, "OperatingMargin", f"EG>=20% & OM>={om:.0%}")

    # ── 4. 自己資本比率 ───────────────────────────────
    if "EquityRatio" in all_candidates.columns:
        logger.info("次元4: 自己資本比率")
        test(base, "EquityRatio", "EG>=20% (EqR条件なし)")
        for er in [0.30, 0.40, 0.50, 0.60]:
            c = base[base["EquityRatio"] >= er]
            test(c, "EquityRatio", f"EG>=20% & EqR>={er:.0%}")

    # ── 5. ROE ───────────────────────────────────────
    if "ROE" in all_candidates.columns:
        logger.info("次元5: ROE")
        test(base, "ROE", "EG>=20% (ROE条件なし)")
        for roe in [0.08, 0.12, 0.15, 0.20]:
            c = base[base["ROE"] >= roe]
            test(c, "ROE", f"EG>=20% & ROE>={roe:.0%}")

    # ── 6. 来期予想増益率 ─────────────────────────────
    if "ForwardGuidance" in all_candidates.columns:
        logger.info("次元6: 来期予想増益率")
        test(base, "ForwardGuidance", "EG>=20% (FG条件なし)")
        for fg in [0.0, 0.05, 0.10, 0.20]:
            c = base[base["ForwardGuidance"] >= fg]
            test(c, "ForwardGuidance", f"EG>=20% & FG>={fg:.0%}")

    # ── 7. 決算種別 ───────────────────────────────────
    if "CurPerType" in all_candidates.columns:
        types = sorted(all_candidates["CurPerType"].dropna().unique().tolist())
        logger.info(f"次元7: 決算種別 {types}")
        test(base, "PeriodType", "EG>=20% (全種別)")
        for pt in types:
            c = base[base["CurPerType"] == pt]
            test(c, "PeriodType", f"EG>=20% & 種別={pt}")

    # ── 8. 業種別 ─────────────────────────────────────
    if "S17Nm" in all_candidates.columns:
        sectors = sorted(all_candidates["S17Nm"].dropna().unique().tolist())
        logger.info(f"次元8: 業種別 ({len(sectors)}業種)")
        test(base, "Sector", "EG>=20% (全業種)")
        for s in sectors:
            c = base[base["S17Nm"] == s]
            test(c, "Sector", f"EG>=20% & 業種={s}")

    # ── 9. RSI(14日) ──────────────────────────────────
    if "RSI14" in all_candidates.columns:
        logger.info("次元9: RSI(14日)")
        test(base, "RSI", "EG>=20% (RSI条件なし)")
        # 売られすぎ（低RSI = まだ上がっていない）
        for rsi_max in [40, 50, 60, 70]:
            c = base[base["RSI14"] <= rsi_max]
            test(c, "RSI_low", f"EG>=20% & RSI<={rsi_max}")
        # 上昇モメンタム（高RSI = 強い株）
        for rsi_min in [50, 60, 70]:
            c = base[base["RSI14"] >= rsi_min]
            test(c, "RSI_high", f"EG>=20% & RSI>={rsi_min}")

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("PF", ascending=False).reset_index(drop=True)
    return df


def save_screening_sweep_results(df: pd.DataFrame) -> None:
    path = RESULTS_DIR / "screening_sweep_results.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info(f"スクリーニングスイープ結果を保存: {path}")


def print_screening_sweep_top(df: pd.DataFrame, n: int = 30) -> None:
    print(f"\n{'='*100}")
    print(f"  スクリーニング条件スイープ TOP {n}  (PF降順, TP={FIXED_TP:.0%}/SL={FIXED_SL:.0%}/Hold={FIXED_HOLD}日固定)")
    print(f"{'='*100}")
    cols = ["Category", "Condition", "Trades", "WinRate", "PF", "Expect", "Return", "MaxDD"]
    top = df[cols].head(n).copy()
    top["WinRate"] = top["WinRate"].apply(lambda x: f"{x:.1%}")
    top["Expect"]  = top["Expect"].apply(lambda x: f"{x:.2%}")
    top["Return"]  = top["Return"].apply(lambda x: f"{x:.1%}")
    top["MaxDD"]   = top["MaxDD"].apply(lambda x: f"{x:.1%}")
    print(top.to_string(index=True))
    print(f"{'='*100}\n")

    # カテゴリ別ベスト表示
    print("── カテゴリ別ベスト ──")
    for cat, grp in df.groupby("Category"):
        best = grp.iloc[0]
        print(f"  {cat:<18} {best['Condition']:<45} PF={best['PF']:.3f}  Return={best['Return']:.1%}  Trades={best['Trades']}")
    print()
