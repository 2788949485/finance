"""路由模块: auth"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from .. import auth
from ..models import LLMConfig


@router.post("/api/auth/register")
def register(body: dict[str, str], request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    allowed, msg = auth.check_rate_limit(f"register:{client_ip}")
    if not allowed:
        raise HTTPException(429, msg)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    invite_code = body.get("invite_code", "").strip()
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(400, "用户名需 2-20 个字符")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    try:
        user = auth.create_user(username, password, invite_code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = auth.create_token(user["id"], user["username"])
    auth.record_login_success(f"register:{client_ip}")
    auth.audit_log(user["id"], username, "register", f"invite_code={invite_code}", client_ip)
    return {"token": token, "user": user, "profile": auth.get_profile(user["id"])}



@router.post("/api/auth/login")
def login(body: dict[str, str], request: Request) -> dict[str, Any]:
    client_ip = request.client.host if request.client else "unknown"
    username = (body.get("username") or "").strip()
    for ident in [f"login_ip:{client_ip}", f"login_user:{username}"]:
        allowed, msg = auth.check_rate_limit(ident)
        if not allowed:
            raise HTTPException(429, msg)

    result = auth.authenticate(username, body.get("password") or "")
    if not result:
        for ident in [f"login_ip:{client_ip}", f"login_user:{username}"]:
            auth.record_login_fail(ident)
        raise HTTPException(401, "用户名或密码错误")
    if result.get("_disabled"):
        raise HTTPException(403, "账号已被禁用，请联系管理员")
    user = result
    token = auth.create_token(user["id"], user["username"])
    for ident in [f"login_ip:{client_ip}", f"login_user:{username}"]:
        auth.record_login_success(ident)
    auth.audit_log(user["id"], username, "login", ip=client_ip)
    return {"token": token, "user": user, "profile": auth.get_profile(user["id"])}



@router.get("/api/auth/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user, "profile": auth.get_profile(user["id"])}



@router.get("/api/auth/profile")
def get_profile(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return auth.get_profile(user["id"])



@router.put("/api/auth/profile")
def put_profile(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return auth.update_profile(
            user["id"],
            risk_preference=body.get("risk_preference"),
            watchlist=body.get("watchlist"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))



@router.post("/api/auth/change-password")
async def change_password_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """修改密码。需提供旧密码。"""
    body = await request.json()
    old_pwd = body.get("old_password", "")
    new_pwd = body.get("new_password", "")
    if not old_pwd or not new_pwd:
        raise HTTPException(400, "请填写旧密码和新密码")
    try:
        ok = auth.change_password(user["id"], old_pwd, new_pwd)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(400, "旧密码错误")
    return {"status": "ok"}


# ---------- per-user LLM 配置 ----------


@router.get("/api/auth/llm-config")
def get_llm_config_api(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """获取当前用户的LLM配置（api_key脱敏）。"""
    return auth.get_user_llm_config(user["id"]) | {"api_key": auth._mask_key(auth.get_user_llm_config(user["id"])["api_key"])}



@router.put("/api/auth/llm-config")
async def save_llm_config_api(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """保存当前用户的LLM配置（api_key加密存储）。"""
    body = await request.json()
    return auth.save_user_llm_config(user["id"], body)


# ---------- 投研分析 ----------

def _resolve_ticker(ticker: str) -> str | None:
    """把用户输入（公司名/代码）解析为标准代码，复用 tools.resolve_symbol。"""
    from .tools import resolve_symbol
    resolved = resolve_symbol(ticker)
    # 校验是否合法：A股6位数字 / hk+5位 / us+代码
    if resolved.isdigit() and len(resolved) == 6:
        return resolved
    if resolved.startswith(("hk", "us")):
        return resolved
    return None



@router.put("/api/auth/analyst-config")
def save_analyst_config(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """保存用户分析师配置（启用哪些分析师）。"""
    enabled = body.get("enabled_analysts")
    if not isinstance(enabled, list):
        raise HTTPException(status_code=400, detail="enabled_analysts must be a list")
    from .agents.analysts import ALL_ANALYSTS
    valid_roles = {cls.role for cls in ALL_ANALYSTS}
    enabled = [r for r in enabled if r in valid_roles]
    auth.update_profile(user["id"], analyst_config=enabled)
    return {"enabled_analysts": enabled}



@router.get("/api/admin/users")
def admin_list_users(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    return auth.list_all_users()



@router.post("/api/admin/users/{user_id}/toggle-active")
def admin_toggle_user(user_id: int, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, str]:
    ok = auth.toggle_user_active(user_id)
    auth.audit_log(admin["id"], admin["username"], "toggle_user", f"target_id={user_id}")
    return {"status": "ok" if ok else "not_found"}



@router.post("/api/admin/users/{user_id}/set-admin")
async def admin_set_admin(user_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, str]:
    body = await request.json()
    ok = auth.set_user_admin(user_id, bool(body.get("is_admin", False)))
    auth.audit_log(admin["id"], admin["username"], "set_admin", f"target_id={user_id} value={body.get('is_admin')}")
    return {"status": "ok" if ok else "not_found"}



@router.post("/api/admin/invite-codes")
async def admin_create_invite(request: Request, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    body = await request.json()
    note = body.get("note", "")
    code = auth.create_invite_code(admin["id"], note)
    auth.audit_log(admin["id"], admin["username"], "create_invite", f"code={code['code']}")
    return code



@router.get("/api/admin/invite-codes")
def admin_list_invites(admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    return auth.list_invite_codes()



@router.get("/api/admin/audit-logs")
def admin_audit_logs(admin: dict[str, Any] = Depends(require_admin), limit: int = 100) -> list[dict[str, Any]]:
    return auth.list_audit_logs(limit)



@router.get("/api/admin/stats")
def admin_stats(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return auth.get_system_stats()



@router.get("/api/auth/is-admin")
def check_is_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"is_admin": bool(user.get("is_admin"))}
