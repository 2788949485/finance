"""数据库抽象层：支持 SQLite（默认）和 PostgreSQL（公开部署）。

通过环境变量切换:
  DATABASE_URL=postgresql://user:pass@localhost:5432/financecrew

不切换时默认用 SQLite（无需改动现有代码）。

迁移步骤（从 SQLite 到 PostgreSQL）:
1. 安装依赖: pip install psycopg2-binary
2. 创建数据库: createdb financecrew
3. 设置环境变量: DATABASE_URL=postgresql://user:pass@localhost/financecrew
4. 迁移数据: python migrate_to_pg.py
5. 重启后端
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

# 检测数据库类型
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    _PG_CONFIG = None

    def _parse_pg_url(url: str) -> dict[str, str]:
        """解析 postgresql://user:pass@host:port/dbname"""
        from urllib.parse import urlparse
        p = urlparse(url)
        return {
            "host": p.hostname or "localhost",
            "port": str(p.port or 5432),
            "user": p.username or "",
            "password": p.password or "",
            "dbname": p.path.lstrip("/"),
        }

    def connect():
        cfg = _PG_CONFIG or _parse_pg_url(DATABASE_URL)
        conn = psycopg2.connect(**cfg, cursor_factory=RealDictCursor)
        conn.autocommit = False
        return conn

    def execute_sql(conn, sql: str, params: tuple = ()) -> Any:
        """PostgreSQL 兼容层：SQLite 用 ? 占位符，PG 用 %s。"""
        sql_pg = sql.replace("?", "%s")
        return conn.execute(sql_pg, params)

    # 自动建表适配
    def _init_with_pg(conn):
        """PostgreSQL 建表语句（与 SQLite 的 CREATE TABLE IF NOT EXISTS 兼容）。"""
        tables = [
            """CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_invited INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                risk_preference TEXT DEFAULT 'balanced',
                watchlist TEXT DEFAULT '[]',
                updated_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""",
        ]
        for t in tables:
            conn.execute(t)
        conn.commit()

else:
    # SQLite 模式（默认）-- 透明透传，不改变现有行为
    connect = None  # type: ignore
    execute_sql = None  # type: ignore
