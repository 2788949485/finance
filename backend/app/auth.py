"""认证与用户画像：注册/登录/JWT/用户偏好。

- 密码哈希：标准库 hashlib.pbkdf2_hmac（无额外依赖）
- Token：PyJWT，HS256，7 天有效期
- 用户画像：风险偏好（conservative/balanced/aggressive）+ 自选股 watchlist
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime
from typing import Any, Optional

import jwt

from .config import DB_PATH

JWT_ALGO = "HS256"
TOKEN_TTL = 7 * 24 * 3600  # 7 天


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                risk_preference TEXT DEFAULT 'balanced',
                watchlist TEXT DEFAULT '[]',
                updated_at TEXT
            )"""
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


# ---------- JWT secret ----------

def _get_secret() -> str:
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key='jwt_secret'").fetchone()
    if row:
        return row["value"]
    secret = secrets.token_hex(32)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_config (key, value) VALUES ('jwt_secret', ?)",
            (secret,),
        )
    return secret


# ---------- 密码 ----------

def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return digest.hex(), salt


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100_000)
    return hmac.compare_digest(digest.hex(), expected_hash)


# ---------- JWT ----------

def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL,
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGO)


def decode_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(token, _get_secret(), algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        return None


# ---------- 用户 CRUD ----------

def create_user(username: str, password: str) -> dict[str, Any]:
    """注册用户，返回用户信息；用户名重复抛 ValueError。"""
    _init_db()
    digest, salt = hash_password(password)
    try:
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (username, digest, salt, datetime.now().isoformat(timespec="seconds")),
            )
            user_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO user_profiles (user_id, risk_preference, watchlist) VALUES (?, 'balanced', '[]')",
                (user_id,),
            )
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    return {"id": user_id, "username": username}


def authenticate(username: str, password: str) -> Optional[dict[str, Any]]:
    """校验登录，成功返回用户信息。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        return None
    if not verify_password(password, row["salt"], row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT id, username, created_at FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_profile(user_id: int) -> dict[str, Any]:
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return {"risk_preference": "balanced", "watchlist": []}
    try:
        watchlist = json.loads(row["watchlist"])
    except json.JSONDecodeError:
        watchlist = []
    return {
        "risk_preference": row["risk_preference"],
        "watchlist": watchlist,
        "updated_at": row["updated_at"],
    }


def update_profile(user_id: int, risk_preference: Optional[str] = None, watchlist: Optional[list[str]] = None) -> dict[str, Any]:
    _init_db()
    cur = get_profile(user_id)
    if risk_preference is not None:
        if risk_preference not in ("conservative", "balanced", "aggressive"):
            raise ValueError("无效的风险偏好")
        cur["risk_preference"] = risk_preference
    if watchlist is not None:
        cur["watchlist"] = [str(w).zfill(6) if str(w).isdigit() else str(w) for w in watchlist][:30]
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO user_profiles (user_id, risk_preference, watchlist, updated_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, cur["risk_preference"], json.dumps(cur["watchlist"], ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
    return get_profile(user_id)
