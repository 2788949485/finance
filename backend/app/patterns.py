"""K线形态自动识别：锤子线/十字星/吞没/早晨之星/黄昏之星/乌云盖顶。

基于经典K线形态定义，从最近N根K线识别形态。
"""
from __future__ import annotations

from typing import Any, Optional
import pandas as pd


def _body_size(row) -> float:
    """实体大小"""
    return abs(row["close"] - row["open"])


def _upper_shadow(row) -> float:
    """上影线"""
    return row["high"] - max(row["close"], row["open"])


def _lower_shadow(row) -> float:
    """下影线"""
    return min(row["close"], row["open"]) - row["low"]


def _total_range(row) -> float:
    """总振幅"""
    return row["high"] - row["low"]


def detect_patterns(df: pd.DataFrame) -> list[dict[str, Any]]:
    """从最近K线数据识别经典形态。

    返回识别到的形态列表，每项含：
    - name: 形态名称
    - date: 日期
    - direction: 看涨/看跌/中性
    - description: 描述
    """
    if df is None or len(df) < 3:
        return []

    results = []
    n = len(df)

    for i in range(max(1, n - 10), n):  # 只检查最近10根
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None
        date = str(row["date"])[:10]
        body = _body_size(row)
        upper = _upper_shadow(row)
        lower = _lower_shadow(row)
        tr = _total_range(row)
        if tr == 0:
            continue

        is_up = row["close"] > row["open"]
        body_ratio = body / tr if tr > 0 else 0
        lower_ratio = lower / tr if tr > 0 else 0
        upper_ratio = upper / tr if tr > 0 else 0

        # 1. 锤子线：下影线长(>=2x实体)，上影线短，实体小
        if lower >= 2 * body and upper_ratio < 0.2 and body_ratio < 0.4:
            if is_up:
                results.append({"name": "锤子线", "date": date, "direction": "看涨",
                                "description": "下影线较长，出现在下跌中可能反转向上"})
            else:
                results.append({"name": "倒锤子线", "date": date, "direction": "看涨",
                                "description": "上影线较长，出现在下跌中可能反转向上"})

        # 2. 上吊线：形态同锤子线但出现在上涨中
        if lower >= 2 * body and upper_ratio < 0.2 and body_ratio < 0.4 and not is_up:
            # 检查前几根是否在上涨
            if i >= 2:
                uptrend = df.iloc[i-2]["close"] < df.iloc[i-1]["close"] < row["close"]
                if uptrend:
                    results.append({"name": "上吊线", "date": date, "direction": "看跌",
                                    "description": "上涨中出现锤子形态，可能反转向下"})

        # 3. 十字星：实体非常小，开盘=收盘
        if body_ratio < 0.1 and tr > 0:
            results.append({"name": "十字星", "date": date, "direction": "中性",
                            "description": "多空力量均衡，可能变盘"})
            # 长腿十字星
            if lower_ratio > 0.3 and upper_ratio > 0.3:
                results.append({"name": "长腿十字星", "date": date, "direction": "中性",
                                "description": "上下影线均长，多空争夺激烈"})

        # 4. 蜻蜓线（T字）：只有下影线
        if body_ratio < 0.15 and lower_ratio > 0.5 and upper_ratio < 0.1:
            results.append({"name": "蜻蜓线", "date": date, "direction": "看涨",
                            "description": "只有下影线，底部看涨信号"})

        # 5. 吞没形态：需要前后两根K线
        if prev is not None:
            prev_body = _body_size(prev)
            prev_up = prev["close"] > prev["open"]

            # 看涨吞没：前阴后阳，后根实体包住前根实体
            if not prev_up and is_up and body > prev_body:
                if row["open"] <= prev["close"] and row["close"] >= prev["open"]:
                    results.append({"name": "看涨吞没", "date": date, "direction": "看涨",
                                    "description": "阳线完全吞没前根阴线，强烈看涨信号"})

            # 看跌吞没：前阳后阴，后根实体包住前根实体
            if prev_up and not is_up and body > prev_body:
                if row["open"] >= prev["close"] and row["close"] <= prev["open"]:
                    results.append({"name": "看跌吞没", "date": date, "direction": "看跌",
                                    "description": "阴线完全吞没前根阳线，强烈看跌信号"})

        # 6. 早晨之星：三根K线组合
        if i >= 2 and prev is not None:
            d1 = df.iloc[i - 2]
            d2 = df.iloc[i - 1]
            d3 = row

            d1_down = d1["close"] < d1["open"]
            d1_body = _body_size(d1)
            d2_body = _body_size(d2)
            d3_up = d3["close"] > d3["open"]
            d3_body = _body_size(d3)

            # 早晨之星：大阴 + 小实体（跳空） + 大阳收回
            if (d1_down and d2_body < d1_body * 0.5 and d3_up and
                    d3_body > d2_body and d3["close"] > (d1["open"] + d1["close"]) / 2):
                results.append({"name": "早晨之星", "date": date, "direction": "看涨",
                                "description": "三根K线组合底部反转信号"})

            # 黄昏之星：大阳 + 小实体 + 大阴
            d1_up = d1["close"] > d1["open"]
            if (d1_up and d2_body < d1_body * 0.5 and not d3_up and
                    d3_body > d2_body and d3["close"] < (d1["open"] + d1["close"]) / 2):
                results.append({"name": "黄昏之星", "date": date, "direction": "看跌",
                                "description": "三根K线组合顶部反转信号"})

        # 7. 大阳线/大阴线
        if body_ratio > 0.7:
            if is_up:
                results.append({"name": "大阳线", "date": date, "direction": "看涨",
                                "description": f"实体占比{body_ratio*100:.0f}%，强势上涨"})
            else:
                results.append({"name": "大阴线", "date": date, "direction": "看跌",
                                "description": f"实体占比{body_ratio*100:.0f}%，强势下跌"})

    # 去重（同一天同一形态只保留一条）
    seen = set()
    unique = []
    for r in results:
        key = (r["name"], r["date"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[-5:]  # 最多返回5个形态


def get_pattern_summary(df: pd.DataFrame) -> Optional[dict[str, Any]]:
    """获取最近K线形态摘要（用于行情卡片）"""
    patterns = detect_patterns(df)
    if not patterns:
        return None

    latest = patterns[-1]
    return {
        "pattern": latest["name"],
        "direction": latest["direction"],
        "description": latest["description"],
        "all_patterns": patterns,
    }
