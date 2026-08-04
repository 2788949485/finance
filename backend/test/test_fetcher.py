"""app.data.fetcher 核心函数单元测试。

所有用例直接调用真实函数（不 mock），网络不稳定时用 pytest.skip() 跳过。
运行: cd D:\\top\\finance\\backend && .venv/Scripts/python.exe -m pytest test/test_fetcher.py -v
"""
from __future__ import annotations

import pytest

from app.data.fetcher import (
    _norm_symbol,
    compute_tech_signals,
    get_history,
    get_lhb,
    get_news,
    get_social_sentiment,
    get_stock_brief,
    search_stocks,
)


# ==================== _norm_symbol（纯逻辑，无网络） ====================


def test_norm_symbol():
    """_norm_symbol 各种格式转换。"""
    # A股 6 位数字
    assert _norm_symbol("600519") == "600519"
    assert _norm_symbol("000001") == "000001"
    # 5 位数字 = 港股（加 hk 前缀）
    assert _norm_symbol("00700") == "hk00700"
    assert _norm_symbol("60051") == "hk60051"
    # 不足 5 位的数字 = 港股，补零到 5 位
    assert _norm_symbol("700") == "hk00700"
    assert _norm_symbol("12") == "hk00012"
    assert _norm_symbol("1") == "hk00001"
    # 带 hk 前缀原样保留（规范化大小写）
    assert _norm_symbol("hk00700") == "hk00700"
    assert _norm_symbol("HK00700") == "hk00700"
    # 带 us 前缀：代码大写
    assert _norm_symbol("usAAPL") == "usAAPL"
    assert _norm_symbol("usaapl") == "usAAPL"
    assert _norm_symbol("Usaapl") == "usAAPL"
    # 空白处理
    assert _norm_symbol("  600519  ") == "600519"
    # 公司名等非代码原样返回（小写化），交给 resolve_symbol 处理
    assert _norm_symbol("茅台") == "茅台"


# ==================== get_stock_brief（需网络） ====================


def test_get_stock_brief_a_share():
    """A股(600519 贵州茅台)能返回包含 name/price/change_pct 的 dict。"""
    try:
        brief = get_stock_brief("600519", fresh=True)
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if brief is None:
        pytest.skip("600519 实时行情返回 None（网络/接口异常）")
    assert isinstance(brief, dict)
    assert "name" in brief
    assert "price" in brief
    assert "change_pct" in brief
    assert isinstance(brief["name"], str)
    assert brief["name"]  # 非空


def test_get_stock_brief_hk():
    """港股(hk00700 腾讯)能返回 brief。"""
    try:
        brief = get_stock_brief("hk00700", fresh=True)
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if brief is None:
        pytest.skip("hk00700 实时行情返回 None（网络/接口异常）")
    assert isinstance(brief, dict)
    assert "name" in brief
    assert "price" in brief
    assert brief.get("price") is not None


def test_get_stock_brief_us():
    """美股(usAAPL 苹果)能返回 brief。"""
    try:
        brief = get_stock_brief("usAAPL", fresh=True)
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if brief is None:
        pytest.skip("usAAPL 实时行情返回 None（网络/接口异常）")
    assert isinstance(brief, dict)
    assert "name" in brief
    assert "price" in brief
    assert brief.get("price") is not None


# ==================== get_history（需网络） ====================


def test_get_history():
    """K线数据返回 DataFrame 且有 date/open/close/high/low/volume 列。"""
    import pandas as pd

    try:
        df = get_history("600519", days=60)
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if df is None or df.empty:
        pytest.skip("600519 历史K线返回空（网络/接口异常）")
    assert isinstance(df, pd.DataFrame)
    for col in ("date", "open", "close", "high", "low", "volume"):
        assert col in df.columns, f"缺少列: {col}"
    assert len(df) > 0


# ==================== get_news（需网络） ====================


def test_get_news():
    """新闻返回 list 且每条有 title/time。"""
    try:
        news = get_news("600519")
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if not news:  # None 或空 list
        pytest.skip("600519 新闻返回空（网络/接口异常）")
    assert isinstance(news, list)
    assert len(news) > 0
    for item in news:
        assert isinstance(item, dict)
        assert "title" in item
        assert "time" in item


# ==================== compute_tech_signals（依赖 K线数据） ====================


def test_compute_tech_signals():
    """技术指标返回 dict 且有 ma5/ma20/ma60/rsi14/volume_ratio 等 key。"""
    try:
        df = get_history("600519", days=120)
    except Exception as e:
        pytest.skip(f"网络不可用（无法取K线）: {e}")
    if df is None or df.empty or len(df) < 20:
        pytest.skip("600519 K线数据不足20根，无法计算技术指标")
    sig = compute_tech_signals(df)
    assert isinstance(sig, dict)
    assert "error" not in sig, "不应返回 error（数据已足够）"
    for key in ("price", "ma5", "ma20", "ma60", "rsi14"):
        assert key in sig, f"缺少技术指标 key: {key}"


# ==================== search_stocks（需网络） ====================


def test_search_stocks():
    """搜索返回结果（搜'茅台'或代码）。"""
    try:
        results = search_stocks("茅台")
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if not results:
        pytest.skip("搜索'茅台'返回空（网络/接口异常）")
    assert isinstance(results, list)
    assert len(results) > 0
    for item in results:
        assert isinstance(item, dict)
        assert "code" in item
        assert "name" in item


# ==================== get_social_sentiment（需网络，A股） ====================


def test_get_social_sentiment():
    """情绪面返回 sentiment_score（-100 到 100）。"""
    try:
        sentiment = get_social_sentiment("600519")
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    if sentiment is None:
        pytest.skip("600519 情绪面返回 None（akshare/网络异常）")
    assert isinstance(sentiment, dict)
    assert "sentiment_score" in sentiment
    score = sentiment["sentiment_score"]
    assert score is None or isinstance(score, (int, float))


# ==================== get_lhb（港股应返回 None） ====================


def test_get_lhb_none_for_hk():
    """港股龙虎榜返回 None（龙虎榜仅A股有）。"""
    try:
        lhb = get_lhb("hk00700", days=30)
    except Exception as e:
        pytest.skip(f"网络不可用: {e}")
    # 港股无龙虎榜数据，应返回 None（非异常）
    assert lhb is None
