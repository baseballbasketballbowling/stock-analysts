"""出来高急騰スクリーニング。

条件:
  - 当日出来高 > 過去 vol_window 日平均の vol_multiplier 倍以上
  - require_up=True のとき終値 > 前日終値（上昇を伴う急騰のみ）
  - エントリー: 翌営業日の始値
"""

import logging
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def screen_volume_surge(
    quotes_df: pd.DataFrame,
    listed_df: pd.DataFrame,
    vol_multiplier: float = 2.0,
    vol_window: int = 20,
    require_up: bool = True,
    price_change_min: float = 0.0,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    scale_cats: Optional[list] = None,
) -> pd.DataFrame:
    """
    出来高急騰銘柄を抽出して、翌営業日エントリー候補を返す。

    Returns DataFrame with columns:
      Code, SurgeDate, EntryDate, EntryOpen, PrevClose, VolRatio, ScaleCat
    """
    vol_col = "Volume" if "Volume" in quotes_df.columns else "Vo"
    close_col = "AdjustmentClose" if "AdjustmentClose" in quotes_df.columns else "Close"
    open_col  = "AdjustmentOpen"  if "AdjustmentOpen"  in quotes_df.columns else "Open"

    if vol_col not in quotes_df.columns:
        logger.warning("出来高カラムが見つかりません")
        return pd.DataFrame()

    df = quotes_df[["Code", "Date", vol_col, close_col, open_col]].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df[vol_col]   = pd.to_numeric(df[vol_col],   errors="coerce")
    df[close_col] = pd.to_numeric(df[close_col], errors="coerce")
    df[open_col]  = pd.to_numeric(df[open_col],  errors="coerce")
    df = df.sort_values(["Code", "Date"]).reset_index(drop=True)

    # 前日終値・20日平均出来高（当日を含まず）
    df["PrevClose"] = df.groupby("Code")[close_col].shift(1)
    df["VolMA"]     = df.groupby("Code")[vol_col].transform(
        lambda x: x.shift(1).rolling(vol_window, min_periods=vol_window // 2).mean()
    )
    df["VolRatio"]  = df[vol_col] / df["VolMA"]

    # 翌営業日の始値・日付
    df["EntryOpen"] = df.groupby("Code")[open_col].shift(-1)
    df["EntryDate"] = df.groupby("Code")["Date"].shift(-1)

    # 急騰フィルタ
    cond = df["VolRatio"] >= vol_multiplier
    if require_up:
        cond = cond & (df[close_col] > df["PrevClose"])
    if price_change_min > 0:
        cond = cond & (df[close_col] >= df["PrevClose"] * (1 + price_change_min))

    surges = df[cond].copy()

    # 日付範囲フィルタ（SurgeDate = 急騰した日）
    if date_from:
        surges = surges[surges["Date"] >= pd.Timestamp(date_from)]
    if date_to:
        surges = surges[surges["Date"] <= pd.Timestamp(date_to)]

    # EntryOpen が取れないもの（期間末尾）は除外
    surges = surges.dropna(subset=["EntryOpen", "EntryDate"])
    surges = surges[surges["EntryOpen"] > 0]

    # ScaleCat を付与
    scale_map = {}
    if listed_df is not None and "ScaleCategory" in listed_df.columns:
        for _, r in listed_df[["Code", "ScaleCategory"]].iterrows():
            scale_map[str(r["Code"])] = r["ScaleCategory"]
    elif listed_df is not None:
        for col in ["ScaleCat", "Scale", "Topix"]:
            if col in listed_df.columns:
                for _, r in listed_df[["Code", col]].iterrows():
                    scale_map[str(r["Code"])] = r[col]
                break

    surges["ScaleCat"] = surges["Code"].astype(str).map(scale_map)

    # 規模フィルタ
    if scale_cats:
        surges = surges[surges["ScaleCat"].isin(scale_cats)]

    result = surges.rename(columns={"Date": "SurgeDate"})[
        ["Code", "SurgeDate", "EntryDate", "EntryOpen", "PrevClose", "VolRatio", "ScaleCat"]
    ].copy()

    logger.info(
        f"出来高急騰: {vol_multiplier:.0f}倍以上 / require_up={require_up} / price_change_min={price_change_min:.0%} → {len(result)} 件"
    )
    return result.reset_index(drop=True)
