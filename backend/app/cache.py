"""数据缓存层：外部行情/财务数据的 SQLite 缓存，带 TTL 过期。

避免重复请求外部接口（腾讯/同花顺），本地数据库优先。
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional

from .config import DB_PATH

# 各数据类型 TTL（秒）
TTL = {
    "quote": 60,        # 实时行情 1 分钟
    "kline": 3600,      # 日K线 1 小时
    "financials": 86400,  # 财务 24 小时
    "lhb": 21600,       # 龙虎榜 6 小时
    "news": 900,        # 新闻 15 分钟
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS data_cache (
                cache_key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )"""
        )


def get_cached(cache_key: str) -> Optional[Any]:
    """读取缓存；不存在或已过期返回 None。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value, expires_at FROM data_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
    if row is None:
        return None
    if row["expires_at"] < time.time():
        with _connect() as conn:
            conn.execute("DELETE FROM data_cache WHERE cache_key=?", (cache_key,))
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return None


def set_cached(cache_key: str, value: Any, ttl: int) -> None:
    """写入缓存（value 为可 JSON 序列化的对象）。"""
    _init_db()
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO data_cache (cache_key, value, expires_at, created_at)
               VALUES (?, ?, ?, ?)""",
            (cache_key, json.dumps(value, ensure_ascii=False, default=str), now + ttl, now),
        )


# 缓存版本号：数据源/解析逻辑变更时 +1，使旧缓存全部失效（避免旧数据污染）
KEY_VERSION = "v2"


def cached(cache_key: str, ttl: int, fetch_fn, *args, **kwargs):
    """缓存装饰函数：命中返回缓存，未命中调用 fetch_fn 并写入。

    key 带 KEY_VERSION 前缀：数据源逻辑变更时改版本号即可全局失效。
    """
    hit = get_cached(f"{KEY_VERSION}:{cache_key}")
    if hit is not None:
        return hit
    value = fetch_fn(*args, **kwargs)
    if value is not None:
        set_cached(f"{KEY_VERSION}:{cache_key}", value, ttl)
    return value
