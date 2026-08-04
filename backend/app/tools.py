"""对话智能体工具集：把数据层与投研流水线包装为 LangChain 工具。

智能体在对话中自主决定调用哪些工具、以什么顺序调用，基于真实数据回答。
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from . import data as datalayer
from .pipeline import run_analysis

# 热门公司名 -> A 股代码映射（智能体可传公司名，工具自动转码）
COMPANY_ALIASES: dict[str, str] = {
    "贵州茅台": "600519", "茅台": "600519", "五粮液": "000858",
    "平安银行": "000001", "招商银行": "600036", "招行": "600036",
    "工商银行": "601398", "工行": "601398", "建设银行": "601939", "建行": "601939",
    "宁德时代": "300750", "宁王": "300750", "比亚迪": "002594",
    "隆基绿能": "601012", "隆基": "601012", "中芯国际": "688981",
    "中国平安": "601318", "中国人寿": "601628", "美的集团": "000333", "美的": "000333",
    "格力电器": "000651", "格力": "000651", "海尔智家": "600690", "海尔": "600690",
    "万科": "000002", "万科A": "000002", "保利发展": "600048", "保利": "600048",
    "中国中免": "601888", "东方财富": "300059", "东财": "300059",
    "中信证券": "600030", "中国石油": "601857", "中石油": "601857",
    "中国石化": "600028", "中石化": "600028", "长江电力": "600900",
    "中国移动": "600941", "海康威视": "002415", "海康": "002415",
    "立讯精密": "002475", "京东方": "000725", "京东方A": "000725",
    "中兴通讯": "000063", "中兴": "000063", "用友网络": "600588", "用友": "600588",
    "科大讯飞": "002230", "讯飞": "002230", "三一重工": "600031", "三一": "600031",
    "恒瑞医药": "600276", "恒瑞": "600276", "药明康德": "603259", "药明": "603259",
    "片仔癀": "600436", "云南白药": "000538", "牧原股份": "002714", "牧原": "002714",
    "温氏股份": "300498", "顺丰控股": "002352", "顺丰": "002352",
    "上汽集团": "600104", "上汽": "600104", "长城汽车": "601633", "长城": "601633",
}

# 港股公司映射（腾讯行情接口 hk 前缀支持港股）
HK_ALIASES: dict[str, str] = {
    "腾讯": "hk00700", "腾讯控股": "hk00700", "腾讯音乐": "hk01698",
    "阿里巴巴": "hk09988", "阿里": "hk09988", "小米": "hk01810", "小米集团": "hk01810",
    "美团": "hk03690", "京东": "hk09618", "网易": "hk09999", "百度": "hk09888",
    "理想": "hk02015", "理想汽车": "hk02015", "蔚来": "hk09866",
    "小鹏": "hk09868", "小鹏汽车": "hk09868", "快手": "hk01024",
    "美团点评": "hk03690", "香港交易所": "hk00388", "港交所": "hk00388",
    "汇丰控股": "hk00005", "汇丰": "hk00005", "友邦保险": "hk01299", "友邦": "hk01299",
    "中国移动(港)": "hk00941", "中芯国际(港)": "hk00981",
}

# 美股公司映射（腾讯行情接口 us 前缀支持美股）
US_ALIASES: dict[str, str] = {
    "苹果": "usAAPL", "苹果公司": "usAAPL", "特斯拉": "usTSLA",
    "英伟达": "usNVDA", "微软": "usMSFT", "谷歌": "usGOOGL", "Alphabet": "usGOOGL",
    "亚马逊": "usAMZN", "Meta": "usMETA", "脸书": "usMETA", "奈飞": "usNFLX",
    "Netflix": "usNFLX", "英特尔": "usINTC", "AMD": "usAMD", "超威半导体": "usAMD",
    "台积电": "usTSM", "博通": "usAVGO", "甲骨文": "usORCL", "思科": "usCSCO",
    "IBM": "usIBM", "高通": "usQCOM", "迪士尼": "usDIS", "可口可乐": "usKO",
    "百事": "usPEP", "麦当劳": "usMCD", "星巴克": "usSBUX", "耐克": "usNKE",
    "波音": "usBA", "通用汽车": "usGM", "福特": "usF", "摩根大通": "usJPM",
    "高盛": "usGS", "美国银行": "usBAC", "富国银行": "usWFC", "花旗": "usC",
    "伯克希尔": "usBRK.B", "强生": "usJNJ", "辉瑞": "usPFE", "默沙东": "usMRK",
    "礼来": "usLLY", "联合健康": "usUNH", "宝洁": "usPG", "家得宝": "usHD",
    "沃尔玛": "usWMT", "好市多": "usCOST", "Visa": "usV", "万事达": "usMA",
    "PayPal": "usPYPL", "优步": "usUBER", "Lyft": "usLYFT", "爱彼迎": "usABNB",
    "Snowflake": "usSNOW", "Palantir": "usPLTR", "Unity": "usU", "Roblox": "usRBLX",
    "Coinbase": "usCOIN", "比特币矿机": "usMARA", "阿里巴巴(美)": "usBABA",
    "拼多多": "usPDD", "京东(美)": "usJD", "网易(美)": "usNTES", "百度(美)": "usBIDU",
    "蔚来(美)": "usNIO", "小鹏(美)": "usXPEV", "理想(美)": "usLI",
    "新东方": "usEDU", "好未来": "usTAL", "哔哩哔哩": "usBILI", "B站": "usBILI",
    "爱奇艺": "usIQ", "富途": "usFUTU", "老虎证券": "usTIGR",
}


def resolve_symbol(symbol: str) -> str:
    """把公司名/代码统一解析为标准代码：A股6位 / 港股 hk+5位 / 美股 us+代码。

    规则：hk/us 前缀原样；6位数字=A股；5位数字=港股（如 00700）；
    2-5位纯字母=美股代码（如 AAPL）；公司名查映射表（A股/港股/美股）。
    """
    s = str(symbol).strip()
    low = s.lower()
    # 已带市场前缀：前缀小写 + 代码部分大写（usNVDA -> usNVDA）
    if low.startswith(("hk", "us")):
        return s[:2].lower() + s[2:].upper()
    # 纯数字
    if s.isdigit():
        if len(s) == 6:
            return s
        if len(s) == 5:
            return "hk" + s
        return "hk" + s.zfill(5) if len(s) <= 4 else s
    # 纯 ASCII 字母：视为美股代码（AAPL -> usAAPL；中文公司名不走此分支）
    if s.isascii() and s.isalpha() and 1 <= len(s) <= 5:
        return "us" + s.upper()
    # 公司名映射（A股优先，其次港股，再美股）
    if s in COMPANY_ALIASES:
        return COMPANY_ALIASES[s]
    if s in HK_ALIASES:
        return HK_ALIASES[s]
    if s in US_ALIASES:
        return US_ALIASES[s]
    return s  # 未知文本交给数据层（会失败返回 None）


def _j(data: Any) -> str:
    """dict 转紧凑 JSON 字符串（中文不转义）。"""
    return json.dumps(data, ensure_ascii=False, default=str)


@tool
def get_quote(symbol: str) -> str:
    """查询实时行情快照：现价、涨跌幅、换手率、市盈率PE、市净率PB、总市值。
    参数 symbol: A股6位代码（如 600519）或公司名（如 贵州茅台/腾讯），
    也支持港股（hk00700 或 00700）与美股（usAAPL）。"""
    resolved = resolve_symbol(symbol)
    if not _valid_symbol(resolved):
        return f"无法识别 {symbol}，请提供 A 股 6 位代码（如 600519）、港股（如 hk00700 或 腾讯）或美股代码"
    brief = datalayer.get_stock_brief(resolved)
    if brief is None:
        return f"未查询到 {resolved} 的行情，请确认代码正确"
    return _j(brief)


def _valid_symbol(s: str) -> bool:
    """判断是否为可查询的标准代码：A股6位数字 / hk+5位 / us+代码。"""
    if s.isdigit() and len(s) == 6:
        return True
    if s.startswith("hk") and len(s) == 7 and s[2:].isdigit():
        return True
    if s.startswith("us") and len(s) > 2:
        return True
    return False


@tool
def get_kline(symbol: str, days: int = 120) -> str:
    """查询日K线数据（前复权），返回最近 days 个交易日的 OHLCV（日期/开/收/高/低/量）。
    参数 symbol: A股6位代码或公司名，也支持港股（hk00700/00700）；days: 返回天数，默认120，最大500。"""
    resolved = resolve_symbol(symbol)
    if not _valid_symbol(resolved):
        return f"无法识别 {symbol}，请提供 A 股 6 位代码或港股代码（如 hk00700）"
    df = datalayer.get_history(resolved, days=min(max(days, 30), 500))
    if df is None or df.empty:
        return "未获取到K线数据"
    rows = df.tail(days)[["date", "open", "close", "high", "low", "volume"]].to_dict(orient="records")
    return _j({"symbol": resolved, "bars": rows})


@tool
def get_financials(symbol: str) -> str:
    """查询A股财务摘要：最新报告期营收、净利润及同比增速、ROE、毛利率、资产负债率。
    参数 symbol: 6位A股代码或公司名。仅支持A股，港股美股无财务数据。"""
    resolved = resolve_symbol(symbol)
    if not resolved.isdigit() or len(resolved) != 6:
        return f"{symbol} 的财务数据暂不支持（当前仅支持A股，请提供6位A股代码）"
    fin = datalayer.get_financials(resolved)
    if fin is None:
        return "未获取到财务数据"
    return _j(fin)


@tool
def get_lhb(symbol: str) -> str:
    """查询A股最近30日龙虎榜记录（上榜原因、净买额、买卖额）。
    参数 symbol: 6位A股代码或公司名。仅支持A股。"""
    resolved = resolve_symbol(symbol)
    if not resolved.isdigit() or len(resolved) != 6:
        return f"{symbol} 的龙虎榜数据暂不支持（当前仅支持A股）"
    lhb = datalayer.get_lhb(resolved)
    if lhb is None:
        return "近30日无龙虎榜记录"
    return _j(lhb)


@tool
def get_news(symbol: str) -> str:
    """查询A股个股最新新闻标题与发布时间（最多8条）。
    参数 symbol: 6位股票代码。"""
    news = datalayer.get_news(symbol)
    if not news:
        return "暂无新闻数据"
    return _j(news)


@tool
def search_stock(query: str) -> str:
    """通过股票名称、代码或拼音搜索股票。当用户提到一个你不认识的股票名称时，先用此工具搜索代码。
    参数 query: 股票名称（如 嘉立创/茅台）、代码（如 600519）或拼音缩写（如 gzmt）。"""
    results = datalayer.search_stocks(query, limit=8)
    if not results:
        return f"未找到匹配 '{query}' 的股票"
    lines = [f"搜索 '{query}' 找到 {len(results)} 只股票："]
    for r in results:
        lines.append(f"  {r['name']} 代码:{r['code']} 市场:{r['market']}")
    return "\n".join(lines)


@tool
def web_search(query: str) -> str:
    """联网搜索最新信息。当用户问到训练数据之外的内容（如新上市公司、最新政策、今日新闻）时使用。
    参数 query: 搜索关键词。"""
    import requests
    import re

    try:
        # 优先用搜狗搜索（国内可达，结果质量好）
        r = requests.get(
            "https://www.sogou.com/web",
            params={"query": query, "ie": "utf8"},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        r.encoding = "utf-8"
        # 提取搜索结果摘要
        texts = re.findall(r'<p[^>]*class="[^"]*"[^>]*>(.*?)</p>', r.text, re.S)
        results = []
        for t in texts[:8]:
            clean = re.sub(r"<[^>]+>", "", t).strip()
            clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
            if len(clean) > 15 and not clean.startswith("相关推荐"):
                results.append(clean[:200])
        if results:
            return f"搜索 '{query}' 结果：\n" + "\n".join(f"- {r}" for r in results[:5])
    except Exception:
        pass

    # 降级：Bing
    try:
        r2 = requests.get(
            "https://www.bing.com/search",
            params={"q": query},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        r2.encoding = "utf-8"
        texts2 = re.findall(r"<p[^>]*>(.*?)</p>", r2.text, re.S)
        results2 = []
        for t in texts2[:8]:
            clean = re.sub(r"<[^>]+>", "", t).strip()
            if len(clean) > 15 and "cookie" not in clean.lower():
                results2.append(clean[:200])
        if results2:
            return f"搜索 '{query}' 结果：\n" + "\n".join(f"- {r}" for r in results2[:5])
    except Exception:
        pass

    return f"联网搜索 '{query}' 失败，请稍后再试"


@tool
def get_market_news(keyword: str = "") -> str:
    """查询实时财经快讯（新浪7x24全球财经直播，秒级更新）。
    参数 keyword: 可选关键词过滤（如 腾讯/茅台/芯片/AI），空则返回最新快讯。"""
    news = datalayer.get_flash_news(keyword=keyword, limit=8)
    if not news:
        return "暂无相关快讯"
    lines = [f"[{n['time']}] {n['title']}" for n in news]
    return "\n".join(lines)


@tool
def get_stock_news(symbol: str) -> str:
    """查询个股最新新闻（实时快讯按名称过滤 + 东财个股新闻兜底）。
    参数 symbol: A股6位代码或公司名，也支持港股/美股公司名（腾讯/苹果）。"""
    resolved = resolve_symbol(symbol)
    if not _valid_symbol(resolved):
        return f"无法识别 {symbol}，请提供股票代码或公司名"
    news = datalayer.get_news(resolved)
    if not news:
        return "暂未找到该个股的相关新闻"
    lines = [f"[{n['time']}] {n['title']}" for n in news]
    return "\n".join(lines)


@tool
def run_research(symbol: str, topic: str = "") -> str:
    """运行完整多智能体投研分析：5位分析师（宏观/基本面/技术面/情绪面/资金面）独立研判、
    多空辩论、共识评分、风控审查、交易计划。返回结构化报告JSON。
    参数 symbol: 6位A股代码或公司名；topic: 可选分析主题。耗时较长（约1-2分钟）。仅支持A股。"""
    resolved = resolve_symbol(symbol)
    if not resolved.isdigit() or len(resolved) != 6:
        return f"{symbol} 的深度投研暂不支持（当前仅支持A股，港股可查询行情/K线）"
    try:
        result = run_analysis(resolved, topic or None)
        return _j(result)
    except Exception as e:
        return f"投研分析失败: {e}"


@tool
def compare_industry(symbol: str) -> str:
    """行业对比：查询该股票与同行业竞争对手的 PE/PB/涨跌幅对比 + 行业均值。
    自动判断同行股票（数据库缓存或 LLM 生成），无需手动指定。
    参数 symbol: A股6位代码或公司名。"""
    resolved = resolve_symbol(symbol)
    if not _valid_symbol(resolved):
        return f"无法识别 {symbol}，请提供股票代码或公司名"
    data = datalayer.fetcher.get_industry_compare(resolved)
    if not data or not data.get("peers"):
        return f"{symbol} 暂不支持行业对比（可能是港股/美股或同行数据不足）"
    peers = data["peers"]
    avg_pe = data.get("avg_pe")
    avg_pb = data.get("avg_pb")
    lines = [f"行业对比（共{len(peers)}只）行业均PE {avg_pe} | 均PB {avg_pb}"]
    for p in peers:
        mark = " <--目标" if p.get("is_target") else ""
        pe = p.get("pe")
        pb = p.get("pb")
        chg = p.get("change_pct")
        pe_s = f"{pe:.1f}" if pe else "N/A"
        pb_s = f"{pb:.2f}" if pb else "N/A"
        chg_s = f"{chg:+.2f}%" if chg is not None else ""
        lines.append(f"  {p['name']}({p['code']}) PE={pe_s} PB={pb_s} {chg_s}{mark}")
    return "\n".join(lines)


@tool
def get_sentiment(symbol: str) -> str:
    """查询社交情绪面数据：东财人气榜排名、雪球关注度、今日主力资金净流入、综合情绪评分。
    参数 symbol: A股6位代码或公司名。仅支持A股。"""
    from .tools import resolve_symbol as _rs
    resolved = _rs(symbol)
    if not resolved.isdigit() or len(resolved) != 6:
        return f"{symbol} 的情绪数据暂不支持（当前仅支持A股）"
    data = datalayer.get_social_sentiment(resolved)
    if not data:
        return f"未获取到 {resolved} 的情绪数据"
    lines = [f"{resolved} 社交情绪面："]
    if data.get("hot_rank_trend"):
        latest_rank = data["hot_rank_trend"][-1]["rank"]
        lines.append(f"  东财人气榜排名: 第{latest_rank}名")
    if data.get("xq_followers"):
        lines.append(f"  雪球关注人数: {data['xq_followers']:,}")
    if data.get("vol_ratio") is not None:
        lines.append(f"  近5日量比: {data['vol_ratio']}（>1放量 <1缩量）")
    if data.get("price_5d_chg") is not None:
        lines.append(f"  近5日涨跌幅: {data['price_5d_chg']:+.2f}%")
    if data.get("momentum") is not None:
        lines.append(f"  资金动能: {data['momentum']:+.1f}（正=主力流入 负=流出）")
    if data.get("sentiment_score") is not None:
        lines.append(f"  综合情绪评分: {data['sentiment_score']}/100")
    return "\n".join(lines)


@tool
def get_valuation(symbol: str) -> str:
    """DCF现金流折现估值：计算股票内在价值，判断高估/低估。
    返回内在价值、上行空间、10年FCF预测、关键假设。
    参数 symbol: A股6位代码或公司名。仅支持有财务数据的股票。"""
    from .tools import resolve_symbol as _rs
    resolved = _rs(symbol)
    if not _valid_symbol(resolved):
        return f"无法识别 {symbol}"
    from .valuation import compute_dcf
    data = compute_dcf(resolved)
    if not data:
        return f"{resolved} 无法计算估值（财务数据不足）"
    lines = [f"{resolved} DCF估值："]
    lines.append(f"  当前价格: {data['current_price']}")
    lines.append(f"  内在价值: {data['intrinsic_value']}")
    lines.append(f"  上行空间: {data['upside_pct']:+.1f}%")
    lines.append(f"  结论: {data['verdict']}")
    a = data["assumptions"]
    lines.append(f"  假设: 增长率{a['base_growth']}% 折现率{a['discount_rate']}% 永续{a['terminal_growth']}%")
    return "\n".join(lines)


FINANCE_TOOLS = [get_quote, get_kline, get_financials, get_lhb, get_news, search_stock, web_search, compare_industry, get_sentiment, get_valuation, run_research]
