"""无账号模式的匿名浏览器会话。

浏览器只保存一个随机 Token，视频、总结和聊天记录仍保存在 SQLite。每次业务请求
到达后，后端把 Cookie 中的原始 Token 做 SHA-256，再用哈希值查找数据库会话。
这样既能识别同一个浏览器，又不会把原始凭证直接写入数据库。

这里的 Session 是“当前浏览器拥有哪些视频”的身份边界；它不同于 conversations
表里的一段聊天会话。一个匿名 Session 可以拥有多个视频，每个视频也可以有多段对话。
"""

from __future__ import annotations  # 允许在类型注解中使用类自身的名称

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException, Request, Response, status

from .database import (
    create_session,
    find_session_by_token_hash,
    touch_session,
)


# Cookie 名称固定后，浏览器才能在后续请求中自动携带同一个值。
COOKIE_NAME = "video_assistant_session"
# 默认有效期为 30 天，单位是秒。环境变量可在部署时覆盖这个值。
# 当前是“固定过期”：touch_session 只记录访问时间，不会重新延长 expires_at。
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(30 * 24 * 60 * 60)))


def _hash_token(token: str) -> str:
    """计算 Token 的 SHA-256 哈希。

    原始 Token 相当于匿名登录凭证，只存在浏览器 HttpOnly Cookie；SQLite 只保存
    哈希。查询时对浏览器传来的 Token 做同样计算，哈希相同就能找到对应会话。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _find_valid_session(request: Request) -> dict | None:
    """从本次 HTTP 请求中寻找仍然有效的匿名会话。

    ``request.cookies`` 由 FastAPI/Starlette 根据请求头解析。HttpOnly 只禁止前端
    JavaScript 读取 Cookie，并不妨碍浏览器把它自动放进请求头。
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # 第一次访问、主动清理 Cookie 或 Cookie 已过期时都会进入这里。
        return None
    # find_session_by_token_hash 还会检查数据库 expires_at，浏览器和服务端两边
    # 任意一边判定过期，这个会话都不能继续使用。
    return find_session_by_token_hash(_hash_token(token))


def ensure_session(request: Request, response: Response) -> dict:
    """复用有效会话；缺失或过期时创建新会话并写入响应 Cookie。

    浏览器刷新页面后会再次调用 ``POST /api/session``。如果旧 Cookie 有效，这里
    返回原会话；否则创建新会话。新会话无法读取旧会话拥有的视频数据。

    这个设计提供匿名归属校验，不是完整账号系统：清除浏览器 Cookie 后，用户无法
    证明新浏览器仍拥有旧数据，也没有跨设备登录或密码找回流程。
    """
    existing = _find_valid_session(request)
    if existing:
        # last_seen_at 用于观察活跃时间或后续清理，但不会延长固定过期时间。
        touch_session(existing["id"])
        return existing

    # token_urlsafe 生成难以猜测的随机字符串；它不是数据库 session_id，也不包含
    # 视频或用户信息，只负责证明浏览器持有这段随机凭证。
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE)
    session = create_session(
        session_id=f"sess_{uuid4().hex}",
        token_hash=_hash_token(token),
        expires_at=expires_at.isoformat(),
    )
    # set_cookie 最终生成 Set-Cookie 响应头，浏览器看到后负责保存。之后访问相同
    # 域名和路径时，浏览器会自动把 Cookie 放入请求，不需要 Vue 手动读取。
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        # Max-Age 告诉浏览器从现在起最多保存多少秒。
        max_age=SESSION_MAX_AGE,
        # 禁止 document.cookie 读取原始 Token，减少前端脚本泄漏凭证的风险。
        httponly=True,
        # Lax 会限制多数跨站请求携带 Cookie，同时不影响本项目同源 API 请求。
        samesite="lax",
        # 本地 HTTP 开发环境为 false；生产 HTTPS 部署时应设置 COOKIE_SECURE=true。
        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        # 整个站点的 /api 路径都需要使用该 Cookie，因此显式设置根路径。
        path="/",
    )
    return session


def get_current_session(request: Request) -> dict:
    """FastAPI 依赖函数：业务接口执行前必须取得有效匿名会话。

    路由参数中的 ``Depends(get_current_session)`` 会先执行本函数。校验失败时直接
    返回 401，后面的数据库查询和业务代码不会运行。业务路由随后仍要把返回的
    ``session["id"]`` 带入视频查询；仅验证 Cookie 还不足以授权某个具体 video_id。
    """
    session = _find_valid_session(request)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="匿名会话不存在或已过期，请刷新页面后重试",
        )
    # 请求通过后记录本次访问时间；这仍然不会延长固定的 expires_at。
    touch_session(session["id"])
    return session
