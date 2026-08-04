"""认证与用户画像：注册/登录/JWT/用户偏好、登录频率限制。

- 密码哈希：标准库 hashlib.pbkdf2_hmac（无额外依赖）
- Token：PyJWT，HS256，7 天有效期
- 登录频率限制：5次失败后锁定15分钟（内存计数器，重启清零）
- LLM Key 加密：AES-256-GCM（同机部署，防DB泄露后key被直接读取）
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime
from typing import Any, Optional

import jwt

from .config import DB_PATH

JWT_ALGO = "HS256"
TOKEN_TTL = 7 * 24 * 3600  # 7 天

# 登录频率限制：{ip_or_username: (fail_count, first_fail_time, lock_until)}
_login_attempts: dict[str, dict[str, Any]] = {}
MAX_LOGIN_FAILS = 5
LOCK_DURATION = 15 * 60  # 锁定15分钟


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
                created_at TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_invited INTEGER DEFAULT 0
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
        # per-user LLM 配置
        conn.execute(
            """CREATE TABLE IF NOT EXISTS user_llm_config (
                user_id INTEGER PRIMARY KEY,
                provider TEXT DEFAULT 'deepseek',
                base_url TEXT DEFAULT 'https://api.deepseek.com/v1',
                api_key_enc TEXT DEFAULT '',
                model TEXT DEFAULT 'deepseek-chat',
                temperature REAL DEFAULT 0.3,
                max_tokens INTEGER DEFAULT 4096,
                updated_at TEXT
            )"""
        )
        # 邀请码
        conn.execute(
            """CREATE TABLE IF NOT EXISTS invite_codes (
                code TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                used_by INTEGER,
                created_at TEXT NOT NULL,
                used_at TEXT,
                note TEXT DEFAULT ''
            )"""
        )
        # 审计日志
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        # 确保旧表升级（ALTER ADD COLUMN）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        for col, default in [("is_admin", "0"), ("is_active", "1"), ("is_invited", "0")]:
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT {default}")

        # 首个用户自动成为管理员
        count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        if count == 0:
            # 会在 create_user 里设置 is_admin
            pass
        else:
            # 如果没有管理员，将第一个用户设为管理员
            admin = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_admin=1").fetchone()["c"]
            if admin == 0:
                first = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
                if first:
                    conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (first["id"],))


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


# ---------- LLM Key 加密/解密 ----------

def _get_enc_key() -> bytes:
    """获取加密密钥（与JWT secret不同，单独存储）。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_config WHERE key='enc_key'").fetchone()
    if row:
        return bytes.fromhex(row["value"])
    key = secrets.token_bytes(32)
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_config (key, value) VALUES ('enc_key', ?)",
            (key.hex(),),
        )
    return key


def encrypt_key(plaintext: str) -> str:
    """AES-256-GCM 加密API key。"""
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        import base64
        key = _get_enc_key()
        f = Fernet(base64.urlsafe_b64encode(key))
        return f.encrypt(plaintext.encode()).decode()
    except ImportError:
        # 无 cryptography 库时降级为 XOR（开发环境）
        key = _get_enc_key()
        return bytes(a ^ b for a, b in zip(plaintext.encode(), (key * 20)[:len(plaintext)])).hex()


def decrypt_key(ciphertext: str) -> str:
    """解密API key。"""
    if not ciphertext:
        return ""
    try:
        from cryptography.fernet import Fernet
        import base64
        key = _get_enc_key()
        f = Fernet(base64.urlsafe_b64encode(key))
        return f.decrypt(ciphertext.encode()).decode()
    except ImportError:
        key = _get_enc_key()
        raw = bytes.fromhex(ciphertext)
        return bytes(a ^ b for a, b in zip(raw, (key * 20)[:len(raw)])).decode()
    except Exception:
        return ""


# ---------- 登录频率限制 ----------

def check_rate_limit(identifier: str) -> tuple[bool, str]:
    """检查登录频率限制。返回(是否允许, 锁定提示)。"""
    now = int(time.time())
    rec = _login_attempts.get(identifier)

    if rec and rec.get("lock_until", 0) > now:
        remain = int((rec["lock_until"] - now) / 60)
        return False, f"登录失败次数过多，请{remain}分钟后再试"

    if rec and now - rec.get("first_fail", 0) > 3600:
        # 超过1小时重置
        del _login_attempts[identifier]

    return True, ""


def record_login_fail(identifier: str) -> None:
    """记录登录失败。"""
    now = int(time.time())
    rec = _login_attempts.get(identifier, {"count": 0, "first_fail": now, "lock_until": 0})
    rec["count"] += 1
    if rec["count"] >= MAX_LOGIN_FAILS:
        rec["lock_until"] = now + LOCK_DURATION
    _login_attempts[identifier] = rec


def record_login_success(identifier: str) -> None:
    """登录成功清除失败记录。"""
    _login_attempts.pop(identifier, None)


# ---------- per-user LLM 配置 ----------

def get_user_llm_config(user_id: int) -> dict[str, Any]:
    """读取用户专属LLM配置（api_key已解密）。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_llm_config WHERE user_id=?", (user_id,)
        ).fetchone()
    if row is None:
        # 首次：返回默认配置
        return {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
            "temperature": 0.3,
            "max_tokens": 4096,
        }
    return {
        "provider": row["provider"],
        "base_url": row["base_url"],
        "api_key": decrypt_key(row["api_key_enc"]),
        "model": row["model"],
        "temperature": row["temperature"],
        "max_tokens": row["max_tokens"],
    }


def save_user_llm_config(user_id: int, cfg: dict[str, Any]) -> dict[str, Any]:
    """保存用户专属LLM配置（api_key加密存储）。"""
    _init_db()
    now = datetime.now().isoformat(timespec="seconds")

    # 如果新key为空，保留旧key
    old_cfg = get_user_llm_config(user_id)
    api_key = cfg.get("api_key", "").strip()
    if not api_key:
        api_key = old_cfg["api_key"]

    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO user_llm_config
               (user_id, provider, base_url, api_key_enc, model, temperature, max_tokens, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                cfg.get("provider", old_cfg["provider"]),
                cfg.get("base_url", old_cfg["base_url"]),
                encrypt_key(api_key),
                cfg.get("model", old_cfg["model"]),
                float(cfg.get("temperature", old_cfg["temperature"])),
                int(cfg.get("max_tokens", old_cfg["max_tokens"])),
                now,
            ),
        )
    # 返回脱敏版
    result = get_user_llm_config(user_id)
    result["api_key"] = _mask_key(result["api_key"])
    return result


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


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

def create_user(username: str, password: str, invite_code: str = "") -> dict[str, Any]:
    """注册用户，返回用户信息；用户名重复抛 ValueError。

    invite_code: 如果系统中有邀请码表，需要提供有效邀请码（如果表非空）。
    首个注册用户自动成为管理员。
    """
    _init_db()
    # 邀请码校验：如果存在邀请码记录，则必须提供有效码
    with _connect() as conn:
        code_count = conn.execute("SELECT COUNT(*) as c FROM invite_codes").fetchone()["c"]
        if code_count > 0:
            if not invite_code:
                raise ValueError("当前需要邀请码注册")
            row = conn.execute("SELECT * FROM invite_codes WHERE code=? AND used_by IS NULL", (invite_code,)).fetchone()
            if row is None:
                raise ValueError("邀请码无效或已被使用")

    digest, salt = hash_password(password)
    try:
        with _connect() as conn:
            # 第一个用户自动管理员
            count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            is_admin = 1 if count == 0 else 0
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, salt, created_at, is_admin, is_active, is_invited) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (username, digest, salt, datetime.now().isoformat(timespec="seconds"), is_admin, 1 if invite_code else 0),
            )
            user_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO user_profiles (user_id, risk_preference, watchlist) VALUES (?, 'balanced', '[]')",
                (user_id,),
            )
            # 消耗邀请码
            if invite_code:
                conn.execute(
                    "UPDATE invite_codes SET used_by=?, used_at=? WHERE code=?",
                    (user_id, datetime.now().isoformat(timespec="seconds"), invite_code),
                )
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    return {"id": user_id, "username": username, "is_admin": is_admin}


def authenticate(username: str, password: str) -> Optional[dict[str, Any]]:
    """校验登录，成功返回用户信息。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        return None
    if not verify_password(password, row["salt"], row["password_hash"]):
        return None
    if row["is_active"] == 0:
        return {"_disabled": True}
    return {"id": row["id"], "username": row["username"], "is_admin": row["is_admin"]}


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT id, username, created_at, is_admin, is_active FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def is_admin(user_id: int) -> bool:
    """检查用户是否是管理员。"""
    with _connect() as conn:
        row = conn.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["is_admin"])


# ---------- 管理员功能 ----------

def list_all_users() -> list[dict[str, Any]]:
    """列出所有用户（管理员功能）。"""
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.created_at, u.is_admin, u.is_active, u.is_invited, "
            "(SELECT COUNT(*) FROM analyses WHERE analyses.user_id = u.id) as analysis_count "
            "FROM users u ORDER BY u.id"
        ).fetchall()
    return [dict(r) for r in rows]


def toggle_user_active(user_id: int) -> bool:
    """启用/禁用用户。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT is_active FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE users SET is_active=? WHERE id=?", (1 - row["is_active"], user_id))
    return True


def set_user_admin(user_id: int, is_admin_val: bool) -> bool:
    """设置/取消管理员。"""
    _init_db()
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if is_admin_val else 0, user_id))
        return cur.rowcount > 0


def create_invite_code(created_by: int, note: str = "") -> dict[str, Any]:
    """生成邀请码。"""
    _init_db()
    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:8].upper()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO invite_codes (code, created_by, created_at, note) VALUES (?, ?, ?, ?)",
            (code, created_by, datetime.now().isoformat(timespec="seconds"), note),
        )
    return {"code": code, "created_by": created_by, "note": note}


def list_invite_codes() -> list[dict[str, Any]]:
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT i.*, u.username as created_by_name FROM invite_codes i "
            "LEFT JOIN users u ON i.created_by = u.id ORDER BY i.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def audit_log(user_id: int, username: str, action: str, detail: str = "", ip: str = "") -> None:
    """记录审计日志。"""
    _init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (user_id, username, action, detail, ip, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, action, detail, ip, datetime.now().isoformat(timespec="seconds")),
        )


def list_audit_logs(limit: int = 100) -> list[dict[str, Any]]:
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_system_stats() -> dict[str, Any]:
    """系统统计信息（管理员面板用）。"""
    _init_db()
    import os
    db_size = os.path.getsize(DB_PATH) if DB_PATH.exists() else 0
    with _connect() as conn:
        user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        active_users = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active=1").fetchone()["c"]
        analysis_count = conn.execute("SELECT COUNT(*) as c FROM analyses").fetchone()["c"]
        session_count = conn.execute("SELECT COUNT(*) as c FROM chat_sessions").fetchone() if conn.execute("SELECT name FROM sqlite_master WHERE name='chat_sessions'").fetchone() else {"c": 0}
        alert_count = conn.execute("SELECT COUNT(*) as c FROM alerts").fetchone()["c"] if conn.execute("SELECT name FROM sqlite_master WHERE name='alerts'").fetchone() else {"c": 0}
        portfolio_count = conn.execute("SELECT COUNT(*) as c FROM portfolio").fetchone()["c"] if conn.execute("SELECT name FROM sqlite_master WHERE name='portfolio'").fetchone() else {"c": 0}
    return {
        "db_size_mb": round(db_size / 1024 / 1024, 2),
        "users": {"total": user_count, "active": active_users},
        "analyses": analysis_count,
        "chat_sessions": session_count.get("c", 0) if isinstance(session_count, dict) else 0,
        "alerts": alert_count.get("c", 0) if isinstance(alert_count, dict) else 0,
        "portfolios": portfolio_count,
    }


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


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """修改密码。需验证旧密码。返回是否成功。"""
    _init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return False
    if not verify_password(old_password, row["salt"], row["password_hash"]):
        return False
    if len(new_password) < 6:
        raise ValueError("新密码至少6位")
    new_hash, new_salt = hash_password(new_password)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, salt=? WHERE id=?",
            (new_hash, new_salt, user_id),
        )
    return True
