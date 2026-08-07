"""共享依赖：认证守卫。

从 main.py 拆出来避免循环导入（routes 模块需要 get_current_user，
但 main.py 又要导入 routes 模块来注册路由）。
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException

from . import auth


def get_current_user(authorization: str = Header(default="")) -> dict[str, Any]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    payload = auth.decode_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = auth.get_user(int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """管理员守卫：非管理员返回403。"""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
