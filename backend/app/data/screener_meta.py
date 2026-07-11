"""
股票筛选器元数据 — 行业分类映射 + 预设筛选方案。

行业分类为面板标的的人工映射（研究工具用途，非监管口径）；
预设方案给出常见「发现工作流」的起点条件，用户可在此基础上微调。
"""

from __future__ import annotations

from app.data.models import Market

# ── 行业标签（中文，横跨三市统一口径）──────────────────────────
SECTORS: list[str] = [
    "科技", "半导体", "金融", "消费", "医药", "工业",
    "能源", "材料", "电信", "公用事业", "地产", "汽车", "ETF", "其他",
]

_A_SECTORS: dict[str, str] = {
    "600519": "消费", "601318": "金融", "600036": "金融", "600900": "公用事业",
    "601398": "金融", "601857": "能源", "600028": "能源", "601088": "能源",
    "600887": "消费", "601628": "金融", "600030": "金融", "600104": "汽车",
    "601601": "金融", "600809": "消费", "600941": "电信", "601899": "材料",
    "600309": "材料", "601166": "金融", "000858": "消费", "000002": "地产",
    "000651": "消费", "000333": "消费", "000001": "金融", "002594": "汽车",
    "002415": "科技", "002475": "科技", "000568": "消费", "002714": "消费",
    "300750": "工业", "300059": "金融", "300760": "医药", "601012": "工业",
    "603288": "消费", "002352": "工业", "002230": "科技", "000725": "科技",
}

_US_SECTORS: dict[str, str] = {
    "AAPL": "科技", "MSFT": "科技", "NVDA": "半导体", "GOOGL": "科技",
    "AMZN": "消费", "META": "科技", "TSLA": "汽车", "NFLX": "科技",
    "AMD": "半导体", "INTC": "半导体", "QCOM": "半导体", "AVGO": "半导体",
    "CRM": "科技", "ORCL": "科技", "UBER": "科技", "JPM": "金融",
    "BAC": "金融", "GS": "金融", "V": "金融", "MA": "金融", "BRK.B": "金融",
    "WMT": "消费", "MCD": "消费", "NKE": "消费", "DIS": "消费", "COST": "消费",
    "HD": "消费", "XOM": "能源", "CVX": "能源", "BA": "工业",
    "SPY": "ETF", "QQQ": "ETF", "GLD": "ETF",
}

_HK_SECTORS: dict[str, str] = {
    "00700": "科技", "09988": "科技", "03690": "科技", "00941": "电信",
    "01299": "金融", "00005": "金融", "02318": "金融", "01810": "科技",
    "00388": "金融", "02020": "消费", "09999": "科技", "09618": "消费",
    "09888": "科技", "02015": "汽车", "09868": "汽车", "06862": "消费",
    "01211": "汽车", "00175": "汽车", "03968": "金融", "01398": "金融",
    "00939": "金融", "01288": "金融", "03988": "金融", "02628": "金融",
    "00857": "能源", "00386": "能源", "00762": "电信", "00016": "地产",
    "01109": "地产", "00027": "消费",
}


def sector_of(market: Market, symbol: str) -> str:
    """查面板标的行业标签，未知返回「其他」。"""
    if market == Market.A:
        return _A_SECTORS.get(symbol, "其他")
    if market == Market.US:
        return _US_SECTORS.get(symbol.upper(), "其他")
    if market == Market.HK:
        key = symbol.lstrip("0").zfill(5) if symbol.isdigit() else symbol
        return _HK_SECTORS.get(key) or _HK_SECTORS.get(symbol, "其他")
    return "其他"


# ── 预设筛选方案（criteria 为 ScreenerCriteria 的部分字段）──────
# market_cap 阈值单位为「亿」（本币近似，跨市场仅作量级参考）。
PRESETS: list[dict] = [
    {
        "id": "value_blue_chip",
        "name": "低估值蓝筹",
        "desc": "大市值 + 低市盈率，稳健价值风格",
        "criteria": {"min_market_cap_yi": 1000, "max_pe": 20,
                     "sort_by": "market_cap", "sort_dir": "desc"},
    },
    {
        "id": "high_dividend",
        "name": "高股息",
        "desc": "股息率 ≥ 3%，现金回报优先",
        "criteria": {"min_dividend_yield": 3, "sort_by": "dividend_yield",
                     "sort_dir": "desc"},
    },
    {
        "id": "momentum",
        "name": "强势动量",
        "desc": "当日涨幅 ≥ 2%，追踪强势标的",
        "criteria": {"min_change_pct": 2, "sort_by": "change_pct",
                     "sort_dir": "desc"},
    },
    {
        "id": "oversold",
        "name": "超跌关注",
        "desc": "当日跌幅 ≥ 2%，潜在反弹候选",
        "criteria": {"max_change_pct": -2, "sort_by": "change_pct",
                     "sort_dir": "asc"},
    },
    {
        "id": "small_growth",
        "name": "中小成长",
        "desc": "市值 ≤ 500 亿且当日上涨",
        "criteria": {"max_market_cap_yi": 500, "min_change_pct": 0,
                     "sort_by": "change_pct", "sort_dir": "desc"},
    },
    {
        "id": "below_book",
        "name": "破净股",
        "desc": "市净率 < 1，账面价值折价",
        "criteria": {"max_pb": 1, "sort_by": "pb", "sort_dir": "asc"},
    },
]
