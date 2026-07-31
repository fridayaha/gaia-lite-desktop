"""Gateway-level media resolution — reads files from engine workspace via manager API.

智能体在回复中用 ![alt](output/chart.png) 引用工作区内生成的图片。本模块提供
路径归一与单图解析能力，供企微等 IM 通道出站时把工作区图片转成可发送的字节
（web 门户走前端按需解析，不经此处的流式改写）。
"""

from __future__ import annotations

import logging
import re

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_REMOTE_PREFIXES = ("http://", "https://", "data:", "blob:")

_MIME_MAP = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "webp": "image/webp",
    "ico": "image/x-icon",
    "bmp": "image/bmp",
}

# ── 入站媒体大小上限（防御性，对齐 Hermes 量级）─────────────────────────────
# 企微 media/get 本身有上游限制，此处做 gateway 侧兜底，防止异常大字节写入 workspace。
INBOUND_IMAGE_MAX_BYTES = 10 * 1024 * 1024
INBOUND_VIDEO_MAX_BYTES = 10 * 1024 * 1024
INBOUND_VOICE_MAX_BYTES = 2 * 1024 * 1024
INBOUND_FILE_MAX_BYTES = 20 * 1024 * 1024

# 凭据/敏感路径片段：defense-in-depth，在到达 manager 前拒绝（manager safe_resolve_ws
# 已锚定工作区并阻 ..，此处仅做廉价前置过滤，避免任何边界 case 把凭据路径透传给 manager）。
_CRED_PATH_RE = re.compile(r"(^|/)\.(?:ssh|aws|gitconfig|env)(?:/|$)", re.IGNORECASE)


def looks_like_image(data: bytes) -> bool:
    """Return True if *data* starts with a known image magic-byte sequence.

    防止把 HTML 错误页/恶意内容当图片写入 workspace 或上传企微。移植自 Hermes
    ``_looks_like_image``。
    """
    if len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return True
    if data[:2] == b"BM":
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False


def is_safe_media_path(path: str) -> bool:
    """defense-in-depth：拒绝目录穿越/凭据路径，再交给 manager 锚定。

    manager ``safe_resolve_ws`` 必定锚到 workspace_root 并阻 ``..``，本函数只做前置
    廉价过滤——空串、含 ``..`` 段、或形如 ``.ssh/id_rsa`` 的凭据路径片段直接拒绝，
    不让其透传到 manager。
    """
    p = (path or "").strip()
    if not p:
        return False
    parts = p.replace("\\", "/").split("/")
    if any(seg == ".." for seg in parts):
        return False
    if _CRED_PATH_RE.search(p):
        return False
    return True


def find_local_image_matches(text: str) -> list[re.Match[str]]:
    """Return match objects for all ![alt](local_path) where path is not a remote URL.

    Each match: ``group(1)``=alt, ``group(2)``=path; use ``.start()``/``.end()`` for the
    full ``![alt](path)`` span. 供需要按 span 切分原文的场景（如企微出站逐段发送），
    避免 caller 再用 ``text.find`` 重定位（路径重复或路径串出现在 alt 文本时会错位）。
    """
    return [
        m
        for m in _IMG_PATTERN.finditer(text)
        if not m.group(2).strip().startswith(_REMOTE_PREFIXES)
    ]


# 文件链接 [name](local_path)——注意 (?<!\!) 排除图片语法 ![](path)（前导 ! 时不匹配）。
# group(1)=显示文本（文件名），group(2)=path。用于企微出站发 file msgtype。
_FILE_LINK_PATTERN = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)")


def find_local_file_links(text: str) -> list[re.Match[str]]:
    """Return match objects for all ``[name](local_path)`` where path is not a remote URL.

    排除图片 ``![](path)``（前导 ``!`` 时不匹配）。每条 match：``group(1)``=name、
    ``group(2)``=path；``.start()``/``.end()`` 为完整 ``[name](path)`` span。
    引擎生成文件时输出 ``[Aliyun_xxx.pptx](output/Aliyun_xxx.pptx)``，用此提取走下载/file 通道。
    """
    return [
        m
        for m in _FILE_LINK_PATTERN.finditer(text)
        if not m.group(2).strip().startswith(_REMOTE_PREFIXES)
    ]


def normalize_path(path: str) -> str:
    """Normalize a file path to be relative to the workspace root."""
    p = path.strip()
    if p.startswith("file://"):
        p = p[7:]
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("/"):
        # 模型常以绝对 profile 路径引用工作区内文件，如
        # /opt/data/profiles/<profile>/home/x.png —— 剥掉 /opt/data/profiles/<profile>/
        # 前缀（兼容旧 /profiles/<profile>/ 格式），归一为相对路径 home/x.png。
        # profile 名被丢弃，manager safe_resolve_ws 永远锚到当前用户自己的 hermes_home，
        # 不会跨 profile 泄漏。
        m = re.match(r"/(?:opt/data/)?profiles/[^/]+/(.+)$", p)
        if m:
            p = m[1]
        else:
            # 不匹配 profile 前缀的绝对路径（如 /home/x.png）：剥前导 / 降级为相对路径。
            # 安全：manager safe_resolve_ws 必定锚到 workspace_root 且阻 .. ，
            # /etc/passwd → etc/passwd → workspace_root/etc/passwd（不存在即 404），
            # 无法逃逸用户自己的工作区。
            p = p.lstrip("/")
    return p


def _guess_mime(path: str) -> str:
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    return _MIME_MAP.get(ext, "image/png")


def _auth_headers(client_token: str | None = None) -> dict[str, str]:
    """构造调 manager /files/content 的鉴权头。

    - JWT 客户端（client_token 非 sk- 开头）：转发 ``Authorization: Bearer <jwt>``，
      manager 走 get_current_user → resolve_user_profile，按用户隔离 profile（多用户实例
      各自解析自己工作区的图片）。
    - sk- API Key 客户端或无 token：用 ``X-Internal-Token``（gateway↔manager 服务间信任），
      manager 走 resolve_instance_profile 按 instance_id 解析（sk- key 已绑定 instance）。
      解决 sk- 客户端转发 sk- 会被 manager 当 JWT 拒收 401 的问题。
    """
    if client_token and not client_token.startswith("sk-"):
        return {"Authorization": f"Bearer {client_token}"}
    token = settings.internal_token
    return {"X-Internal-Token": token} if token else {}


async def _resolve_one(
    client: httpx.AsyncClient, agent_id: str, path: str, client_token: str | None = None
) -> str | None:
    """单图解析：用已开启的 client 调 manager /files/content，返回 data URL 或 None。"""
    norm = normalize_path(path)
    if not is_safe_media_path(norm):
        logger.warning("blocked unsafe media path (traversal/credential): %s", norm)
        return None
    url = f"{settings.controller_url}/api/manager/agent-instances/{agent_id}/files/content"
    try:
        resp = await client.get(url, headers=_auth_headers(client_token), params={"path": norm})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("is_image") and data.get("content_b64"):
            return f"data:{_guess_mime(norm)};base64,{data['content_b64']}"
    except Exception as e:
        logger.warning("media resolve failed for %s: %s", norm, e)
    return None


async def resolve_image_to_data_url(
    agent_id: str, path: str, client_token: str | None = None
) -> str | None:
    """独立解析单张图片（自建 client）。供企微出站发图等单图场景使用。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await _resolve_one(client, agent_id, path, client_token)


async def resolve_file_share_url(agent_id: str, path: str) -> tuple[str, str] | None:
    """获取文件分享下载链接（manager /files/share-link 签名令牌）。

    企微智能机器人（wecom_bot_callback）不支持文件附件，只能发下载 URL。返回
    ``(url, filename)`` 或 None。manager 端未实现该端点时返回 None（文件链接不展示，
    不影响文本/流式/卡片）。
    """
    norm = normalize_path(path)
    if not is_safe_media_path(norm):
        logger.warning("blocked unsafe media path (traversal/credential): %s", norm)
        return None
    url = f"{settings.controller_url}/api/manager/agent-instances/{agent_id}/files/share-link"
    headers = _auth_headers(None)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"path": norm}, headers=headers)
            if resp.status_code != 200:
                logger.warning("share-link failed (%s) for %s", resp.status_code, norm)
                return None
            data = resp.json()
            u = data.get("url")
            if u:
                return u, data.get("filename", "") or norm.rsplit("/", 1)[-1]
    except Exception as e:
        logger.warning("share-link error for %s: %s", norm, e)
    return None


async def resolve_file_bytes(agent_id: str, path: str) -> tuple[bytes, str] | None:
    """下载工作区文件的完整字节（经 manager /files/download，无截断）。

    供企微出站发 file msgtype：拿字节 → media/upload(type=file) → file msgtype。
    返回 ``(bytes, filename)``；失败（不存在/超限/不可访问）返回 None。
    用内部令牌鉴权（同 _resolve_one 的 sk-/无 token 分支），按 instance_id 解析 profile。
    """
    norm = normalize_path(path)
    if not is_safe_media_path(norm):
        logger.warning("blocked unsafe media path (traversal/credential): %s", norm)
        return None
    url = f"{settings.controller_url}/api/manager/agent-instances/{agent_id}/files/download"
    headers = _auth_headers(None)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=headers, params={"path": norm})
            if resp.status_code != 200:
                logger.warning(
                    "file download failed for %s: %s %s", norm, resp.status_code, resp.text[:200]
                )
                return None
            # filename 从 Content-Disposition 还原（manager 已按 RFC 5987 编码中文）
            fname = norm.rsplit("/", 1)[-1] or "download"
            cd = resp.headers.get("content-disposition", "")
            if "filename*=" in cd:
                try:
                    fname = cd.split("filename*=", 1)[1].split("''", 1)[1].split(";")[0]
                    from urllib.parse import unquote

                    fname = unquote(fname)
                except Exception:
                    pass
            return resp.content, fname
    except Exception as e:
        logger.warning("file download error for %s: %s", norm, e)
        return None
