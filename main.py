#!/usr/bin/env python3
"""
株式スクリーニング & バックテスト CLI

使い方:
  python main.py backtest [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  python main.py screen   [--date YYYY-MM-DD]
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

# dotenv があれば .env を読み込む
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config.settings import BACKTEST_START, BACKTEST_END

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def cmd_backtest(args) -> None:
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info,
        load_statements,
        load_daily_quotes,
        screen_candidates,
    )
    from src.backtest.engine import run_backtest
    from src.backtest.metrics import trades_to_df, compute_metrics, print_metrics, save_results

    start = args.start
    end = args.end

    logger.info(f"バックテスト期間: {start} 〜 {end}")

    try:
        logger.info("STEP1: クライアント初期化")
        client = JQuantsClient()

        logger.info("STEP2: 上場銘柄取得")
        listed_df = load_listed_info(client)
        logger.info(f"上場銘柄数: {len(listed_df)}")

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        logger.info(f"STEP3: 財務データ取得 ({stmt_start} 〜 {end})")
        stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
        logger.info(f"財務データ件数: {len(stmt_df)}")

        logger.info(f"STEP4: 日足データ取得 ({start} 〜 {end})")
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)
        logger.info(f"日足データ件数: {len(quotes_df)}")

        logger.info("STEP5: スクリーニング")
        # 0 = 上限なし、それ以外は億円単位をYen単位に変換
        cap_max = None if args.market_cap_max == 0 else args.market_cap_max * 1e8
        candidates = screen_candidates(stmt_df, quotes_df, listed_df, roe_min=0.12,
                                       market_cap_max=cap_max)

        if candidates.empty:
            logger.warning("候補銘柄が見つかりませんでした。")
            sys.exit(1)

        logger.info(f"候補銘柄数: {len(candidates)}")

        logger.info("STEP6: バックテスト")
        trades = run_backtest(candidates, quotes_df)

        if not trades:
            logger.warning("トレードが生成されませんでした。")
            sys.exit(1)

        trades_df = trades_to_df(trades)
        metrics = compute_metrics(trades_df)
        print_metrics(metrics)
        save_results(trades_df, metrics)
        logger.info("完了。results/ フォルダに結果を保存しました。")

    except Exception as e:
        logger.error(f"エラー発生: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_screen_sweep(args) -> None:
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.backtest.screening_sweep import (
        run_screening_sweep, save_screening_sweep_results, print_screening_sweep_top,
    )

    start = args.start
    end   = args.end
    logger.info(f"スクリーニング条件スイープ期間: {start} 〜 {end}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df   = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)

        # 緩い閾値で全候補を取得（追加指標カラムも含む）
        logger.info("ベーススクリーニング（成長率 >= 5%）")
        all_candidates = screen_candidates(
            stmt_df, quotes_df, listed_df,
            earnings_growth_min=0.05,
        )

        if all_candidates.empty:
            logger.warning("候補が見つかりませんでした。")
            sys.exit(1)

        logger.info(f"全候補数: {len(all_candidates)}")
        logger.info(f"利用可能な指標列: {[c for c in all_candidates.columns if c not in ('Code','DisclosedDate','FiscalPeriodEnd','MarketCap','EntryDate','EntryOpen')]}")

        sweep_df = run_screening_sweep(all_candidates, quotes_df)

        if sweep_df.empty:
            logger.warning("スイープ結果が空です。")
            sys.exit(1)

        save_screening_sweep_results(sweep_df)
        print_screening_sweep_top(sweep_df, n=30)
        logger.info("完了。results/screening_sweep_results.csv に保存しました。")

    except Exception as e:
        logger.error(f"エラー発生: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_sweep(args) -> None:
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.backtest.sweep import PARAM_GRID, run_parameter_sweep, save_sweep_results, print_sweep_top

    start = args.start
    end   = args.end
    logger.info(f"パラメータスイープ期間: {start} 〜 {end}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)

        # 最も緩い成長率フィルタで候補を取得（スイープ内で再フィルタする）
        min_growth = min(PARAM_GRID["growth_min"])
        logger.info(f"ベーススクリーニング（成長率 >= {min_growth:.0%}）")
        candidates_full = screen_candidates(
            stmt_df, quotes_df, listed_df,
            earnings_growth_min=min_growth,
        )

        if candidates_full.empty:
            logger.warning("候補銘柄が見つかりませんでした。")
            sys.exit(1)

        logger.info(f"ベース候補数: {len(candidates_full)}")
        sweep_df = run_parameter_sweep(candidates_full, quotes_df)

        if sweep_df.empty:
            logger.warning("スイープ結果が空です。")
            sys.exit(1)

        save_sweep_results(sweep_df)
        print_sweep_top(sweep_df, n=20)
        logger.info("完了。results/sweep_results.csv に保存しました。")

    except Exception as e:
        logger.error(f"エラー発生: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_screen(args) -> None:
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info,
        load_statements,
        load_daily_quotes,
        screen_candidates,
    )

    target_date = args.date or datetime.today().strftime("%Y-%m-%d")
    start = str(int(target_date[:4]) - 2) + target_date[4:]  # 2年前から取得

    client = JQuantsClient()
    listed_df = load_listed_info(client)
    stmt_df = load_statements(client, date_from=start, date_to=target_date)
    quotes_df = load_daily_quotes(client, date_from=start, date_to=target_date)

    candidates = screen_candidates(stmt_df, quotes_df, listed_df, target_date=target_date)

    if candidates.empty:
        print("候補銘柄なし")
        return

    pd_import()
    print(f"\n=== スクリーニング結果 ({target_date}) ===")
    print(candidates.to_string(index=False))
    print(f"\n計: {len(candidates)} 銘柄")


def _build_notify_message(candidates, target_date: str, lookback_days: int, listed_df=None) -> str:
    """メール本文を組み立てる。"""
    import pandas as pd
    from config.settings import TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS

    # 企業名マップ（5桁/4桁コード両対応）
    name_map = {}
    if listed_df is not None:
        for col in ["CompanyName", "Name", "会社名"]:
            if col in listed_df.columns:
                for _, r in listed_df[["Code", col]].iterrows():
                    c = str(r["Code"])
                    name_map[c] = r[col]
                    if len(c) == 5 and c.endswith("0"):
                        name_map[c[:-1]] = r[col]
                break

    n = len(candidates)
    date_label = target_date if lookback_days == 1 else f"直近{lookback_days}日"
    lines = [
        f"[株式スクリーニング {target_date}]",
        f"{date_label}の決算開示から {n}件 が条件に合致しました。",
        "",
    ]

    for _, row in candidates.iterrows():
        code = str(row.get("Code", "????"))
        company = name_map.get(code, "")
        disclosed = pd.to_datetime(row.get("DisclosedDate")).strftime("%m/%d")
        per_type = row.get("CurPerType", "")
        eg   = row.get("EarningsGrowth", float("nan"))
        sg   = row.get("SalesGrowth", float("nan"))
        om   = row.get("OperatingMargin", float("nan"))
        roe  = row.get("ROE", float("nan"))
        cap  = row.get("MarketCap", float("nan"))
        rsi  = row.get("RSI14", float("nan"))
        fg   = row.get("ForwardGuidance", float("nan"))
        s17  = row.get("S17Nm", "")
        entry_date  = row.get("EntryDate")
        entry_open  = row.get("EntryOpen")
        prev_close  = row.get("PrevClose")

        def pct(v, plus=True):
            if pd.isna(v): return "-"
            return (f"+{v:.1%}" if v >= 0 and plus else f"{v:.1%}")

        cap_str   = f"{cap/1e8:.0f}億円" if pd.notna(cap) else "-"
        rsi_str   = f"{rsi:.0f}" if pd.notna(rsi) else "-"
        entry_str = pd.to_datetime(entry_date).strftime("%m/%d") if pd.notna(entry_date) else "TBD"
        price_str = f"  {entry_open:,.0f}円" if pd.notna(entry_open) else ""

        gap_str = ""
        if pd.notna(entry_open) and pd.notna(prev_close) and prev_close > 0:
            gap = (entry_open / prev_close) - 1
            direction = "↑GU" if gap > 0.01 else ("↓GD" if gap < -0.01 else "→FL")
            gap_str = f"  前日比{gap:+.1%} {direction}"

        header = f"◆ {code}"
        if company:
            header += f"  {company}"
        header += f"  ({per_type} / {disclosed}開示)"
        lines.append(header)
        if s17:
            lines.append(f"  業種: {s17}  時価総額: {cap_str}")
        else:
            lines.append(f"  時価総額: {cap_str}")
        lines.append(f"  営業利益成長: {pct(eg)}  売上成長: {pct(sg)}")
        lines.append(f"  営業利益率: {pct(om, False)}  ROE: {pct(roe, False)}")
        rsi_fg = f"  RSI14: {rsi_str}"
        if pd.notna(fg):
            rsi_fg += f"  来期予想: {pct(fg)}"
        lines.append(rsi_fg)
        lines.append(f"  エントリー予定: {entry_str}寄付き{price_str}{gap_str}")
        lines.append("")

    lines += [
        "─" * 40,
        f"利確: +{TAKE_PROFIT:.0%}  損切り: {STOP_LOSS:.0%}  最大保有: {MAX_HOLD_DAYS}日",
        "※ 翌営業日始値でエントリー。価格未確定の場合は TBD。",
    ]
    return "\n".join(lines)


def _build_threads_message(candidates, target_date: str, listed_df=None) -> str:
    """Threads投稿用メッセージ（500文字以内）。"""
    import pandas as pd
    from config.settings import TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS

    name_map = {}
    if listed_df is not None:
        for col in ["CompanyName", "Name", "会社名"]:
            if col in listed_df.columns:
                for _, r in listed_df[["Code", col]].iterrows():
                    c = str(r["Code"])
                    name_map[c] = r[col]
                    if len(c) == 5 and c.endswith("0"):
                        name_map[c[:-1]] = r[col]
                break

    dt = pd.to_datetime(target_date)
    n = len(candidates)
    lines = [
        f"【AI株式スクリーニング {dt.strftime('%m/%d')}】",
        f"決算開示から{n}銘柄が条件クリア！",
        "",
    ]
    for _, row in candidates.iterrows():
        code = str(row.get("Code", ""))
        company = name_map.get(code, code)
        eg = row.get("EarningsGrowth", float("nan"))
        entry_date = row.get("EntryDate")
        entry_str = pd.to_datetime(entry_date).strftime("%m/%d") if pd.notna(entry_date) else "TBD"
        eg_str = f"+{eg:.0%}" if pd.notna(eg) else "-"
        lines.append(f"◆{company}  営業利益{eg_str}")
        lines.append(f"  {entry_str}寄付きエントリー予定")
    lines += [
        "",
        f"TP+{TAKE_PROFIT:.0%} / SL{STOP_LOSS:.0%} / 最大{MAX_HOLD_DAYS}日保有",
        "#株式投資 #決算スクリーニング #AIトレード",
    ]
    return "\n".join(lines)


def _build_disclosure_threads_message(
    total_disclosed: int,
    mid_large_disclosed: int,
    hits,
    target_date: str,
    listed_df=None,
) -> str:
    """18:00 JST用・開示まとめThreadsメッセージ。"""
    import pandas as pd

    name_map = {}
    if listed_df is not None:
        for col in ["CompanyName", "Name", "会社名"]:
            if col in listed_df.columns:
                for _, r in listed_df[["Code", col]].iterrows():
                    c = str(r["Code"])
                    name_map[c] = r[col]
                    if len(c) == 5 and c.endswith("0"):
                        name_map[c[:-1]] = r[col]
                break

    dt = pd.to_datetime(target_date).strftime("%m/%d")
    n_hits = len(hits) if hits is not None and not hits.empty else 0

    lines = [
        f"【決算開示まとめ {dt}】",
        f"本日開示: {total_disclosed}件 / 中大型株: {mid_large_disclosed}件",
        f"条件クリア: {n_hits}件{'  🎯' if n_hits > 0 else ''}",
        "",
    ]

    if n_hits == 0:
        lines.append("本日はヒットなし")
    else:
        for _, row in hits.iterrows():
            code = str(row.get("Code", ""))
            company = name_map.get(code, code)
            s17 = row.get("S17Nm", "")
            cap = row.get("MarketCap", float("nan"))
            eg = row.get("EarningsGrowth", float("nan"))
            sg = row.get("SalesGrowth", float("nan"))
            roe = row.get("ROE", float("nan"))
            entry_open = row.get("EntryOpen")
            entry_date = row.get("EntryDate")

            cap_str = f"{cap/1e8:.0f}億円" if pd.notna(cap) else "-"
            eg_str = f"{eg:+.0%}" if pd.notna(eg) else "-"
            sg_str = f"{sg:+.0%}" if pd.notna(sg) else "-"
            roe_str = f"{roe:.0%}" if pd.notna(roe) else "-"
            entry_str = pd.to_datetime(entry_date).strftime("%m/%d") if pd.notna(entry_date) else "TBD"
            price_str = f"{entry_open:,.0f}円" if pd.notna(entry_open) else "-"

            header = f"◆{company}"
            if s17:
                header += f"（{s17}）"
            lines.append(header)
            lines.append(f"  OP {eg_str} / 売上 {sg_str} / ROE {roe_str}")
            lines.append(f"  {cap_str} / {entry_str}寄り {price_str}予定")
            lines.append("")

    lines.append("#株式投資 #決算スクリーニング #AIトレード")
    return "\n".join(lines)


def cmd_disclosure_post(args) -> None:
    """本日の開示銘柄一覧をThreadsに投稿する（18:00 JST用）。"""
    import os
    import traceback as _tb
    from datetime import timedelta
    import pandas as pd
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes,
        screen_candidates, compute_financial_metrics,
    )
    from src.notifier.threads_notify import post_to_threads

    threads_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not threads_token:
        logger.warning("THREADS_ACCESS_TOKEN 未設定。スキップ。")
        return

    target_date = args.date or datetime.today().strftime("%Y-%m-%d")
    target_dt = pd.to_datetime(target_date)
    stmt_start = str(int(target_date[:4]) - 2) + target_date[4:]
    quotes_start = (target_dt - timedelta(days=90)).strftime("%Y-%m-%d")

    logger.info(f"開示銘柄一覧投稿: {target_date}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)
        stmt_df = load_statements(client, date_from=stmt_start, date_to=target_date)
        quotes_df = load_daily_quotes(client, date_from=quotes_start, date_to=target_date)

        if stmt_df.empty:
            logger.info("財務データなし。スキップ。")
            return

        # 本日の全開示件数
        today_stmt = stmt_df[stmt_df["DisclosedDate"] == target_dt]
        total_disclosed = len(today_stmt)

        # 本日のMid400+Large70開示件数
        mid_large_count = 0
        if not today_stmt.empty:
            fin_today = compute_financial_metrics(today_stmt)
            if not fin_today.empty:
                scale_col = next(
                    (c for c in ["ScaleCategory", "ScaleCat"] if c in listed_df.columns), None
                )
                if scale_col:
                    fin_today["ScaleCat"] = fin_today["Code"].astype(str).map(
                        dict(zip(listed_df["Code"].astype(str), listed_df[scale_col]))
                    )
                    mid_large_count = int(fin_today["ScaleCat"].isin(
                        ["TOPIX Mid400", "TOPIX Large70"]
                    ).sum())

        # ヒット銘柄（本日開示のみ）
        hits = pd.DataFrame()
        if not quotes_df.empty:
            all_cands = screen_candidates(
                stmt_df, quotes_df, listed_df,
                roe_min=0.12, require_entry_price=False,
            )
            if not all_cands.empty:
                hits = all_cands[all_cands["DisclosedDate"] == target_dt].copy()

        logger.info(f"全開示: {total_disclosed}件 / 中大型: {mid_large_count}件 / ヒット: {len(hits)}件")

        message = _build_disclosure_threads_message(
            total_disclosed, mid_large_count, hits, target_date, listed_df
        )
        print(message)
        post_to_threads(threads_token, message)

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_notify(args) -> None:
    import os
    import traceback as _tb
    from datetime import timedelta
    import pandas as pd
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info,
        load_statements,
        load_daily_quotes,
        screen_candidates,
        compute_financial_metrics,
    )
    from src.notifier.email_notify import send_email
    from config.settings import RESULTS_DIR

    gmail_user = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    notify_to = os.environ.get("NOTIFY_TO") or gmail_user
    target_date = args.date or datetime.today().strftime("%Y-%m-%d")
    lookback_days = args.lookback_days
    target_dt = pd.to_datetime(target_date)

    logger.info(f"毎日スクリーニング: {target_date} (遡及{lookback_days}日)")

    # 財務YoY比較のため2年分のstatement、market cap/RSI用に90日分のquotes
    stmt_start = str(int(target_date[:4]) - 2) + target_date[4:]
    quotes_start = (target_dt - timedelta(days=90)).strftime("%Y-%m-%d")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        logger.info(f"財務データ取得: {stmt_start} 〜 {target_date}")
        stmt_df = load_statements(client, date_from=stmt_start, date_to=target_date)

        logger.info(f"日足データ取得: {quotes_start} 〜 {target_date}")
        quotes_df = load_daily_quotes(client, date_from=quotes_start, date_to=target_date)

        if stmt_df.empty or quotes_df.empty:
            logger.info("データが空です。終了。")
            return

        # 指定期間を先に確定
        cutoff = target_dt - timedelta(days=lookback_days - 1)

        # 開示全件CSV（スクリーニングフィルタ前の全銘柄）
        _fin_all = compute_financial_metrics(stmt_df)
        if not _fin_all.empty:
            _disc_window = _fin_all[
                (_fin_all["DisclosedDate"] >= cutoff) & (_fin_all["DisclosedDate"] <= target_dt)
            ].copy()
            if not _disc_window.empty:
                _name_col = next(
                    (c for c in ["CompanyName", "Name", "会社名"] if c in listed_df.columns), None
                )
                if _name_col:
                    _disc_window["CompanyName"] = _disc_window["Code"].astype(str).map(
                        dict(zip(listed_df["Code"].astype(str), listed_df[_name_col]))
                    )
                _scale_col = next(
                    (c for c in ["ScaleCategory", "ScaleCat"] if c in listed_df.columns), None
                )
                if _scale_col:
                    _disc_window["ScaleCat"] = _disc_window["Code"].astype(str).map(
                        dict(zip(listed_df["Code"].astype(str), listed_df[_scale_col]))
                    )
                RESULTS_DIR.mkdir(exist_ok=True)
                _disc_csv = RESULTS_DIR / f"daily_disclosed_{target_date}.csv"
                _disc_window.to_csv(_disc_csv, index=False, encoding="utf-8-sig")
                logger.info(f"開示全件CSV: {_disc_csv} ({len(_disc_window)} 件)")

        # 全候補スクリーニング（EntryOpenなしも含む = 当日開示銘柄対応）
        all_candidates = screen_candidates(
            stmt_df, quotes_df, listed_df,
            roe_min=0.12,
            require_entry_price=False,
        )

        if all_candidates.empty:
            logger.info("条件に合致する銘柄なし。通知しません。")
            return

        recent = all_candidates[
            (all_candidates["DisclosedDate"] >= cutoff) &
            (all_candidates["DisclosedDate"] <= target_dt)
        ].copy()

        logger.info(f"直近{lookback_days}日の候補: {len(recent)} 件")

        # CSV保存
        RESULTS_DIR.mkdir(exist_ok=True)
        csv_path = RESULTS_DIR / f"daily_screen_{target_date}.csv"
        recent.to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"保存: {csv_path}")

        if recent.empty:
            logger.info(f"直近{lookback_days}日の開示で条件に合致する銘柄なし。通知しません。")
            return

        # ヒット銘柄をポートフォリオに自動登録（翌営業日寄り約定待ち）
        # ※ 30日遡り確認などの長期lookback実行では登録しない
        if lookback_days <= 3:
            from src.notifier.portfolio_notify import (
                load_portfolio, save_portfolio, add_pending_position,
            )
            _name_col2 = next(
                (c for c in ["CompanyName", "Name", "会社名"] if c in listed_df.columns), None
            )
            _names = (
                dict(zip(listed_df["Code"].astype(str), listed_df[_name_col2]))
                if _name_col2 else {}
            )
            portfolio = load_portfolio()
            added = []
            for _, row in recent.iterrows():
                code = str(row["Code"])
                entry_date = row.get("EntryDate")
                if pd.isna(entry_date):
                    entry_date = pd.to_datetime(row["DisclosedDate"]) + pd.offsets.BDay(1)
                company = _names.get(code, code)
                if add_pending_position(
                    portfolio, code, company,
                    pd.to_datetime(entry_date).strftime("%Y-%m-%d"),
                ):
                    added.append(f"{code}({company})")
            if added:
                save_portfolio(portfolio)
                logger.info(f"ポートフォリオに自動登録: {added}")

        body = _build_notify_message(recent, target_date, lookback_days, listed_df=listed_df)
        subject = f"【株式スクリーニング】{len(recent)}件ヒット ({target_date})"
        print(body)

        if not gmail_user or not app_password:
            logger.warning("GMAIL_USER / GMAIL_APP_PASSWORD 未設定。メール送信をスキップ（結果はCSVに保存済み）。")
        else:
            send_email(gmail_user, app_password, notify_to, subject, body)

        # Threads 投稿
        threads_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
        if threads_token:
            from src.notifier.threads_notify import post_to_threads
            threads_text = _build_threads_message(recent, target_date, listed_df=listed_df)
            post_to_threads(threads_token, threads_text)
        else:
            logger.info("THREADS_ACCESS_TOKEN 未設定。Threads投稿をスキップ。")

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_entry_delay(args) -> None:
    """決算翌日寄り vs 2日後寄り の比較分析。"""
    import traceback as _tb
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.backtest.engine import run_backtest, build_price_dict
    from src.backtest.metrics import trades_to_df, compute_metrics

    start = args.start
    end = args.end
    logger.info(f"エントリータイミング比較分析: {start} 〜 {end}")

    try:
        client = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)

        candidates = screen_candidates(stmt_df, quotes_df, listed_df, roe_min=0.12)
        if candidates.empty:
            logger.warning("候補銘柄なし")
            return

        price_dict = build_price_dict(quotes_df)
        logger.info(f"候補銘柄数: {len(candidates)}")

        print("\n" + "=" * 65)
        print("  【比較】エントリータイミング: 決算翌日寄り vs 2日後寄り")
        print("=" * 65)

        label_map = {"take_profit": "利確  ", "stop_loss": "損切り", "time_exit": "期間切れ"}
        for delay, label in [(0, "決算翌日寄り (現行, Day+1)"), (1, "2日後寄り  (1日待ち, Day+2)")]:
            trades = run_backtest(
                candidates, quotes_df,
                price_dict=price_dict, entry_delay=delay,
            )
            if not trades:
                print(f"\n[{label}] トレードなし")
                continue
            df = trades_to_df(trades)
            m = compute_metrics(df)
            print(f"\n--- {label} ---")
            print(f"  トレード数   : {m.get('total_trades', 0)}")
            print(f"  勝率         : {m.get('win_rate', 0):.1%}")
            print(f"  PF           : {m.get('profit_factor', 0):.2f}")
            print(f"  期待値       : {m.get('expectancy_pct', 0):+.2%}")
            print(f"  総リターン   : {m.get('total_return', 0):+.1%}")
            print(f"  最大DD       : {m.get('max_drawdown', 0):.1%}")
            print(f"  平均利益     : {m.get('avg_win_pct', 0):+.2%}")
            print(f"  平均損失     : {m.get('avg_loss_pct', 0):+.2%}")
            exit_d = m.get("exit_detail", {})
            if exit_d:
                print("  エグジット内訳 (件数 / 勝率 / 平均損益 / 平均保有):")
                for reason, d in exit_d.items():
                    rl = label_map.get(reason, reason)
                    hold = f"{d['avg_hold_days']:.0f}日" if d.get("avg_hold_days") else "-"
                    print(f"    {rl}: {d['count']:>3}件  勝率{d['win_rate']:.1%}  avg{d['avg_pnl']:+.2%}  保有{hold}")

        print("\n" + "=" * 65)
        logger.info("完了")

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_portfolio_post(args) -> None:
    """保有ポジションの自動管理（約定・売却判定）とThreads投稿。

    毎回実行時に:
      1. 約定待ち(pending)ポジションをエントリー日寄り付きで約定
      2. openポジションのTP/SL/保有期限到達を日足High/Lowで判定 → 自動クローズ
      3. クローズ発生時は売却シグナルをThreadsに投稿
      4. --style morning: 今日の注文プラン / evening: 保有状況 を投稿
    """
    import os
    import traceback as _tb
    import pandas as pd
    from src.api.jquants_client import JQuantsClient
    from src.notifier.portfolio_notify import (
        load_portfolio, save_portfolio,
        fill_pending_positions, check_exits,
        build_portfolio_threads_message, build_morning_plan_message,
        build_exit_alert_message,
    )
    from src.notifier.threads_notify import post_to_threads

    threads_token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    target_date = args.date or datetime.today().strftime("%Y-%m-%d")
    style = getattr(args, "style", "evening")

    try:
        portfolio = load_portfolio()
        positions = portfolio.get("positions", [])
        if not positions:
            logger.info("保有ポジションなし。スキップ。")
            return

        codes = [str(p["code"]) for p in positions]
        logger.info(f"管理銘柄: {codes}")

        # 保有銘柄を1件ずつ取得（軽量）。High/Low含む日足履歴を構築
        client = JQuantsClient()
        history = {}
        for code in codes:
            try:
                rows = client.get_daily_quotes(code=code)
                if not rows:
                    continue
                df = pd.DataFrame(rows)
                df["Date"] = pd.to_datetime(df["Date"])
                df = df[df["Date"] <= pd.Timestamp(target_date)].sort_values("Date")
                if df.empty:
                    continue
                for std, cands in [
                    ("Open",  ["AdjustmentOpen", "Open", "O"]),
                    ("High",  ["AdjustmentHigh", "High", "H"]),
                    ("Low",   ["AdjustmentLow", "Low", "L"]),
                    ("Close", ["AdjustmentClose", "Close", "C"]),
                ]:
                    col = next((c for c in cands if c in df.columns), None)
                    df[std] = pd.to_numeric(df[col], errors="coerce") if col else float("nan")
                history[code] = df[["Date", "Open", "High", "Low", "Close"]].reset_index(drop=True)
                latest = history[code].iloc[-1]
                logger.info(f"  {code}: {latest['Close']:,.0f}円 ({latest['Date'].date()})")
            except Exception as e:
                logger.warning(f"  {code} 価格取得失敗: {e}")

        # 約定処理 → 売却判定
        filled = fill_pending_positions(portfolio, history)
        for pos in filled:
            logger.info(f"約定: {pos['code']} {pos['entry_date']} {pos['entry_price']:,.0f}円 "
                        f"TP={pos['tp_price']:,.0f} SL={pos['sl_price']:,.0f}")
        exits = check_exits(portfolio, history)
        for e in exits:
            logger.info(f"売却シグナル: {e['code']} {e['exit_reason']} {e['pnl_pct']:+.1%}")
        save_portfolio(portfolio)

        prices = {c: float(df.iloc[-1]["Close"]) for c, df in history.items() if not df.empty}

        if not threads_token:
            logger.warning("THREADS_ACCESS_TOKEN 未設定。投稿スキップ（ポートフォリオは更新済み）。")
            return

        if exits:
            alert = build_exit_alert_message(exits, target_date)
            print(alert)
            post_to_threads(threads_token, alert)

        if style == "morning":
            message = build_morning_plan_message(portfolio, prices, target_date)
        else:
            message = build_portfolio_threads_message(portfolio, prices, target_date)
        print(message)
        post_to_threads(threads_token, message)

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def cmd_volume_backtest(args) -> None:
    """出来高急騰モメンタムバックテスト。"""
    import traceback as _tb
    import pandas as pd
    from src.api.jquants_client import JQuantsClient
    from src.screener.screener import (
        load_listed_info, load_statements, load_daily_quotes, screen_candidates,
    )
    from src.screener.volume_screener import screen_volume_surge
    from src.backtest.engine import run_backtest, build_price_dict

    start = args.start
    end   = args.end
    # モメンタム戦略用パラメータ（決算戦略より広め）
    MOM_TP       = 0.20   # +20% 利確
    MOM_SL       = -0.07  # -7%  損切り
    MOM_HOLD     = 30     # 最大30営業日
    PRICE_CHG    = 0.03   # 当日終値 +3%以上（陽線確認）
    multipliers  = [3.0, 5.0, 10.0]
    mid_large    = ["TOPIX Mid400", "TOPIX Large70"]

    logger.info(f"出来高モメンタムバックテスト: {start} 〜 {end}")
    logger.info(f"  戦略パラメータ: TP={MOM_TP:.0%}, SL={MOM_SL:.0%}, MaxHold={MOM_HOLD}日, 陽線≥{PRICE_CHG:.0%}")

    try:
        client    = JQuantsClient()
        listed_df = load_listed_info(client)

        stmt_start = str(int(start[:4]) - 1) + start[4:]
        stmt_df   = load_statements(client, date_from=stmt_start, date_to=end)
        quotes_df = load_daily_quotes(client, date_from=start, date_to=end)
        price_dict = build_price_dict(quotes_df)

        # ── 決算スクリーニング候補（AND比較用）
        earnings_candidates = screen_candidates(
            stmt_df, quotes_df, listed_df, roe_min=0.12,
        )
        earnings_keys = set(
            zip(earnings_candidates["Code"].astype(str),
                pd.to_datetime(earnings_candidates["EntryDate"]).dt.date)
        ) if not earnings_candidates.empty else set()
        logger.info(f"決算候補: {len(earnings_keys)} 件")

        from src.backtest.metrics import trades_to_df, compute_metrics

        def _print_result(label, cands, tp, sl, hold):
            if cands.empty:
                print(f"\n  {label}: 候補0件")
                return
            trades = run_backtest(cands, quotes_df, price_dict=price_dict,
                                  take_profit=tp, stop_loss=sl, max_hold=hold)
            if not trades:
                print(f"\n  {label}: トレードなし")
                return
            df = trades_to_df(trades)
            m  = compute_metrics(df)
            n  = m.get("total_trades", 0)
            wr = m.get("win_rate", 0)
            pf = m.get("profit_factor", 0)
            ret = m.get("total_return", 0)
            mdd = m.get("max_drawdown", 0)
            exp = m.get("expectancy_pct", 0)
            exit_d = m.get("exit_detail", {})
            tp_n  = exit_d.get("take_profit", {}).get("count", 0)
            sl_n  = exit_d.get("stop_loss",   {}).get("count", 0)
            te_n  = exit_d.get("time_exit",    {}).get("count", 0)
            print(f"\n  {label} ({n}件)")
            print(f"    勝率 {wr:.1%}  PF {pf:.2f}  期待値 {exp:+.2%}")
            print(f"    総リターン {ret:+.1%}  最大DD {mdd:.1%}")
            print(f"    利確:{tp_n}件 / 損切:{sl_n}件 / 期間切:{te_n}件")

        header = "=" * 70
        print(f"\n{header}")
        print("  【出来高モメンタム戦略バックテスト】")
        print(f"  対象: TOPIX Mid400 + Large70  /  TP={MOM_TP:.0%}  SL={MOM_SL:.0%}  最大{MOM_HOLD}日")
        print(f"  フィルタ: 当日終値≥前日終値×{1+PRICE_CHG:.2f}（陽線+3%以上）")
        print(header)

        for mult in multipliers:
            print(f"\n▼ 出来高 {mult:.0f}倍以上")

            # 陽線確認あり（メイン戦略）
            vol_up = screen_volume_surge(
                quotes_df, listed_df,
                vol_multiplier=mult,
                require_up=True,
                price_change_min=PRICE_CHG,
                date_from=start, date_to=end,
                scale_cats=mid_large,
            )
            vol_up_bt = vol_up.rename(columns={"SurgeDate": "DisclosedDate"}).copy()
            vol_up_bt["EarningsGrowth"] = float("nan")

            # 陽線確認なし（緩い版：出来高急増のみ）
            vol_any = screen_volume_surge(
                quotes_df, listed_df,
                vol_multiplier=mult,
                require_up=True,
                price_change_min=0.0,
                date_from=start, date_to=end,
                scale_cats=mid_large,
            )
            vol_any_bt = vol_any.rename(columns={"SurgeDate": "DisclosedDate"}).copy()
            vol_any_bt["EarningsGrowth"] = float("nan")

            # AND: 決算通過 & 出来高急騰
            and_mask = vol_up_bt.apply(
                lambda r: (str(r["Code"]), pd.Timestamp(r["EntryDate"]).date()) in earnings_keys,
                axis=1,
            )
            and_cands = vol_up_bt[and_mask].copy()

            _print_result(f"出来高{mult:.0f}倍＋陽線+3%", vol_up_bt, MOM_TP, MOM_SL, MOM_HOLD)
            _print_result(f"出来高{mult:.0f}倍（陽線のみ）", vol_any_bt, MOM_TP, MOM_SL, MOM_HOLD)
            _print_result(f"出来高{mult:.0f}倍＋陽線+3% AND 決算", and_cands, MOM_TP, MOM_SL, MOM_HOLD)

        # 参考: 現行決算戦略との比較
        from config.settings import TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS
        print(f"\n{header}")
        print(f"  【参考: 現行決算戦略】 TP={TAKE_PROFIT:.0%}  SL={STOP_LOSS:.0%}  最大{MAX_HOLD_DAYS}日")
        print(header)
        if not earnings_candidates.empty:
            _print_result("現行決算スクリーニング", earnings_candidates,
                          TAKE_PROFIT, STOP_LOSS, MAX_HOLD_DAYS)

        print(f"\n{header}\n")
        logger.info("完了")

    except Exception as e:
        logger.error(f"エラー: {type(e).__name__}: {e}")
        _tb.print_exc()
        sys.exit(1)


def pd_import():
    import pandas as pd
    return pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="J-Quants 株式スクリーニング & バックテスト",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # backtest サブコマンド
    bt = sub.add_parser("backtest", help="バックテスト実行")
    bt.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    bt.add_argument("--end", default=BACKTEST_END, help="終了日 (YYYY-MM-DD)")
    bt.add_argument("--market-cap-max", type=float, default=3000.0,
                    help="時価総額上限 (億円, デフォルト3000, 0=上限なし=大型株も含む)")
    bt.set_defaults(func=cmd_backtest)

    # screen_sweep サブコマンド
    ss = sub.add_parser("screen_sweep", help="スクリーニング条件スイープ実行")
    ss.add_argument("--start", default=BACKTEST_START)
    ss.add_argument("--end",   default=BACKTEST_END)
    ss.set_defaults(func=cmd_screen_sweep)

    # sweep サブコマンド
    sw = sub.add_parser("sweep", help="パラメータスイープ実行")
    sw.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    sw.add_argument("--end", default=BACKTEST_END, help="終了日 (YYYY-MM-DD)")
    sw.set_defaults(func=cmd_sweep)

    # screen サブコマンド
    sc = sub.add_parser("screen", help="スクリーニング実行")
    sc.add_argument("--date", default=None, help="基準日 (YYYY-MM-DD、省略時は今日)")
    sc.set_defaults(func=cmd_screen)

    # entry_delay サブコマンド
    ed = sub.add_parser("entry_delay", help="エントリータイミング比較 (決算翌日 vs 2日後)")
    ed.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    ed.add_argument("--end", default=BACKTEST_END, help="終了日 (YYYY-MM-DD)")
    ed.set_defaults(func=cmd_entry_delay)

    # volume_backtest サブコマンド
    vb = sub.add_parser("volume_backtest", help="出来高急騰バックテスト (2/3/5倍スイープ)")
    vb.add_argument("--start", default=BACKTEST_START, help="開始日 (YYYY-MM-DD)")
    vb.add_argument("--end",   default=BACKTEST_END,   help="終了日 (YYYY-MM-DD)")
    vb.set_defaults(func=cmd_volume_backtest)

    # portfolio_post サブコマンド
    pp = sub.add_parser("portfolio_post", help="保有ポジション管理（約定/売却判定）& Threads投稿")
    pp.add_argument("--date", default=None, help="基準日 (YYYY-MM-DD、省略時は今日)")
    pp.add_argument("--style", choices=["morning", "evening"], default="evening",
                    help="morning: 今日の注文プラン / evening: 保有状況 (デフォルト)")
    pp.set_defaults(func=cmd_portfolio_post)

    # disclosure_post サブコマンド
    dp = sub.add_parser("disclosure_post", help="本日の開示銘柄一覧をThreadsに投稿（18:00 JST用）")
    dp.add_argument("--date", default=None, help="基準日 (YYYY-MM-DD、省略時は今日)")
    dp.set_defaults(func=cmd_disclosure_post)

    # notify サブコマンド
    nt = sub.add_parser("notify", help="毎日スクリーニング & メール通知")
    nt.add_argument("--date", default=None, help="スクリーニング基準日 (YYYY-MM-DD、省略時は今日)")
    nt.add_argument("--lookback-days", type=int, default=1,
                    help="何日前まで遡って開示をチェックするか (デフォルト: 1)")
    nt.set_defaults(func=cmd_notify)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
