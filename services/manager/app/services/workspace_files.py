"""终端用户工作区只读文件浏览（F-END-030 落地）。

设计约束（用户明确）：**只读**——仅目录列表 + 文件内容展示，不做引擎级 CRUD。

实现思路（用户提议，规避 sidecar）：复用 manager 既有 k8s exec 能力（与日志/指标同机制）
在引擎 Pod 内执行只读 python 脚本，读取该用户 profile 的工作区目录。引擎无关，后续接
Dify/OpenClaw 不需每引擎一套 sidecar。

安全（移植自 hermes-webui `api/workspace.py`）：
- ``safe_resolve_ws`` 路径锚定：``rel`` 解析后必须落在 workspace 根内，拒 ``..`` / 绝对路径
- 敏感文件屏蔽：dotfiles / config.yaml / secrets.enc / .env / *.key / *.pem 不列表、不可读
- ``MAX_FILE_BYTES=400_000`` 截断，超限返回 ``truncated=true``
- exec 命令以 argv 传路径（shlex.quote），无 shell 注入
- 仅 GET，exec 脚本只含 os.scandir / open 读，无任何写操作
"""
from __future__ import annotations

import base64
import json
import logging
import shlex
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentInstance, AgentStatus, User, user_group_members
from pkg.common.models import AgentDeployment, AgentProfile

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 400_000
# 全文件下载上限（对齐企微 media/upload 文件 20MB 限制，兼顾 manager/pod 内存）
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# 预览类型（前端按扩展名决定渲染方式）
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp"}


def sniff_mime(buf: bytes) -> str | None:
    """按 magic bytes 嗅探图片 MIME；非图片或数据不足返回 None。

    用于上传时校验真实类型，避免仅凭扩展名判定 is_image（假扩展名会误导下游图片解析）。
    参考 openclaw media-core IMAGE_SIGNATURES。WebP 签名中间 4 字节是文件大小，
    只比对 RIFF 前缀 + 偏移 8-12 的 WEBP 标记。
    """
    if not buf:
        return None
    if buf.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if buf.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if buf.startswith(b"GIF87a") or buf.startswith(b"GIF89a"):
        return "image/gif"
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"
    if buf.startswith(b"BM"):
        return "image/bmp"
    return None

MD_EXTS = {".md", ".markdown", ".mdown"}
TEXT_EXTS = {
    ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh", ".csv", ".log",
    ".html", ".css", ".xml", ".sql", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".rb", ".php", ".vue", ".md", ".rst",
}

# 敏感/内部文件名/后缀：不列表、不可读（防泄漏 profile 凭据/配置 + 隐藏引擎运行时 internals）
_SENSITIVE_NAMES = {
    "config.yaml", "config.yml", "secrets.enc", ".env", "auth.json",
    "soul.md",  # 人设文件，业务敏感
    "gateway_state.json", "channel_directory.json",  # 引擎路由内部状态
    "gateway.lock", "gateway.pid", "auth.lock",  # 运行时锁/PID
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".enc", ".db", ".db-shm", ".db-wal", ".lock", ".pid", ".log"}


def _is_sensitive(name: str) -> bool:
    if name.startswith("."):
        return True
    low = name.lower()
    if low in _SENSITIVE_NAMES:
        return True
    if any(low.endswith(suf) for suf in _SENSITIVE_SUFFIXES):
        return True
    if name == "__pycache__":
        return True
    return False


def safe_resolve_ws(workspace_root: Path, rel: str) -> Path:
    """将 ``rel`` 锚定到 ``workspace_root`` 之下，拒绝穿越/绝对路径。

    移植自 hermes-webui ``api/workspace.py:safe_resolve_ws``，简化掉 openat-walk
    （manager 以 root exec 读已校验路径，且路径在此处已锚定）。
    """
    root = workspace_root.resolve()
    if not rel or rel in (".", "./"):
        return root
    if rel.startswith("/"):
        raise ValueError("absolute path not allowed")
    # 拒绝任何 .. 段（即便解析后仍在根内，也直接拒，避免歧义）
    if ".." in Path(rel).parts:
        raise ValueError("path traversal not allowed")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes workspace") from exc
    # 敏感文件不可读
    if _is_sensitive(target.name):
        raise ValueError("sensitive file not accessible")
    return target


# ── Pod 内执行脚本（只读） ──────────────────────────────────────────────
# 以 argv 接收已校验的绝对路径，无 shell 注入。输出单行 JSON。

_LIST_SCRIPT = r"""
import sys, os, json
d = sys.argv[1]
out = []
try:
    for e in os.scandir(d):
        n = e.name
        if n.startswith('.') or n == '__pycache__':
            continue
        low = n.lower()
        if low in ('config.yaml','config.yml','secrets.enc','.env','auth.json','soul.md',
                   'gateway_state.json','channel_directory.json',
                   'gateway.lock','gateway.pid','auth.lock'):
            continue
        if any(low.endswith(s) for s in ('.key','.pem','.enc','.db','.db-shm','.db-wal','.lock','.pid','.log')):
            continue
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if e.is_symlink():
            continue
        is_dir = e.is_dir(follow_symlinks=False)
        out.append({
            "name": n,
            "is_dir": is_dir,
            "size": 0 if is_dir else st.st_size,
            "mtime_ns": st.st_mtime_ns,
        })
    out.sort(key=lambda x: (not x["is_dir"], x["name"]))
    print(json.dumps({"entries": out}))
except FileNotFoundError:
    print(json.dumps({"error": "not found", "entries": []}))
except NotADirectoryError:
    print(json.dumps({"error": "not a directory", "entries": []}))
except Exception as ex:
    print(json.dumps({"error": str(ex)}))
"""

_READ_SCRIPT = r"""
import sys, os, json, base64
p = sys.argv[1]
# 图片允许更大读取限制（5MB），文本保持 400KB
_IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp'}
_ext = os.path.splitext(p)[1].lower()
MAX = 5_000_000 if _ext in _IMG_EXTS else 400_000
try:
    if not os.path.isfile(p):
        print(json.dumps({"error": "not a file"}))
        sys.exit(0)
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        raw = f.read(MAX + 1)
    truncated = len(raw) > MAX
    raw = raw[:MAX]
    is_text = b"\x00" not in raw[:4096]
    content_b64 = base64.b64encode(raw).decode("ascii")
    print(json.dumps({
        "size": size,
        "truncated": truncated,
        "is_text": is_text,
        "content_b64": content_b64,
    }))
except FileNotFoundError:
    print(json.dumps({"error": "not found"}))
except Exception as ex:
    print(json.dumps({"error": str(ex)}))
"""


async def resolve_user_profile(
    db: AsyncSession, instance_id: UUID, user: User
) -> tuple[AgentProfile, AgentDeployment, AgentInstance] | None:
    """解析用户在某实例上的 profile + deployment + instance。

    鉴权：实例必须 PUBLISHED，且用户为其组成员（或平台管理员）。
    Profile 匹配：恒按 user_id（INDEPENDENT 用户级独占，SHARED 已下线）。
    未找到（用户从未与该 agent 交互过、profile 尚未懒创建）返回 None。
    """
    inst_result = await db.execute(
        select(AgentInstance).where(AgentInstance.id == instance_id)
    )
    instance = inst_result.scalar_one_or_none()
    if not instance or instance.status != AgentStatus.PUBLISHED:
        return None

    is_admin = _is_platform_admin(user)
    if not is_admin:
        user_group_ids = select(user_group_members.c.group_id).where(
            user_group_members.c.user_id == user.id
        )
        # 校验用户在该实例的组内
        in_group = await db.execute(
            select(user_group_members.c.group_id).where(
                user_group_members.c.user_id == user.id,
                user_group_members.c.group_id == instance.group_id,
            )
        )
        if not in_group.scalar_one_or_none():
            return None

    # 按 user_id 精确匹配该用户的 INDEPENDENT profile（_ensure_profile 给每条 profile
    # 都写了 user_id；SHARED 已下线，不再有 user_id IS NULL 的组级共享 profile）
    prof_result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.instance_id == instance_id,
            AgentProfile.is_active.is_(True),
            AgentProfile.user_id == user.id,
        ).order_by(AgentProfile.created_at.desc())
    )
    profile = prof_result.scalars().first()
    if not profile:
        return None

    dep_result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.id == profile.deployment_id)
    )
    deployment = dep_result.scalar_one_or_none()
    if not deployment:
        return None

    # 注：deployment.pod_name 在 V3 架构下是废弃字段（Pod 名通过 agent_id + label 动态查询，
    # 见 _resolve_workspace_pod）。不在这里校验 pod_name，Pod 存在性 + running 状态由
    # _resolve_workspace_pod 负责检查（返 409 "引擎 Pod 未运行"）。

    return profile, deployment, instance


def _is_platform_admin(user: User) -> bool:
    """避免与 app.core.auth.is_platform_admin 循环引用，本地复刻判定。"""
    from app.core.auth import is_platform_admin
    return is_platform_admin(user)


async def resolve_instance_profile(
    db: AsyncSession, instance_id: UUID
) -> tuple[AgentProfile, AgentDeployment, AgentInstance] | None:
    """内部调用（gateway 服务间，X-Internal-Token 已鉴权）：按 instance_id 解析 profile。

    不做用户级鉴权——gateway 已对 client 鉴权并解析出 instance_id。SHARED 已下线，
    无组级共享 profile，取最近创建的活跃（用户级 INDEPENDENT）profile。
    """
    inst_result = await db.execute(
        select(AgentInstance).where(AgentInstance.id == instance_id)
    )
    instance = inst_result.scalar_one_or_none()
    if not instance or instance.status != AgentStatus.PUBLISHED:
        return None

    prof_result = await db.execute(
        select(AgentProfile).where(
            AgentProfile.instance_id == instance_id,
            AgentProfile.is_active.is_(True),
        ).order_by(AgentProfile.created_at.desc())
    )
    profile = prof_result.scalars().first()
    if not profile:
        return None

    dep_result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.id == profile.deployment_id)
    )
    deployment = dep_result.scalar_one_or_none()
    if not deployment or not deployment.pod_name:
        return None

    return profile, deployment, instance


async def list_files(
    k8s_manager, pod_name: str, workspace_root: Path, rel: str
) -> dict:
    """列出 workspace_root/rel 下的目录条目（只读）。"""
    abs_dir = safe_resolve_ws(workspace_root, rel)
    cmd = "python3 -c " + shlex.quote(_LIST_SCRIPT) + " " + shlex.quote(str(abs_dir))
    stdout = await k8s_manager.exec_command_in_pod(pod_name, [cmd])
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("list_files: failed to parse exec output for %s", abs_dir)
        return {"entries": [], "error": "parse error"}
    if "error" in data:
        return {"entries": [], "error": data["error"]}
    # 补 path 字段（相对 workspace 根），供前端请求内容用
    prefix = "" if rel in (".", "", "./") else rel.strip("/") + "/"
    for e in data.get("entries", []):
        e["path"] = prefix + e["name"]
        e["is_text"] = _guess_is_text(e["name"], e["is_dir"])
    return {"entries": data.get("entries", []), "path": rel or "."}


async def read_file_content(
    k8s_manager, pod_name: str, workspace_root: Path, rel: str
) -> dict:
    """读取 workspace_root/rel 的文件内容（只读，base64 返回）。"""
    abs_path = safe_resolve_ws(workspace_root, rel)
    cmd = "python3 -c " + shlex.quote(_READ_SCRIPT) + " " + shlex.quote(str(abs_path))
    stdout = await k8s_manager.exec_command_in_pod(pod_name, [cmd])
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("read_file_content: failed to parse exec output for %s", abs_path)
        return {"error": "parse error"}
    if "error" in data:
        return {"error": data["error"]}
    # 解码 base64 → 文本（文本文件）或保留 b64（二进制，前端做 data URL）
    raw = base64.b64decode(data["content_b64"]) if data.get("content_b64") else b""
    is_text = data.get("is_text", True)
    content = raw.decode("utf-8", errors="replace") if is_text else None
    name = abs_path.name
    is_image = Path(name).suffix.lower() in IMAGE_EXTS
    return {
        "path": rel,
        "name": name,
        "size": data.get("size", len(raw)),
        "truncated": data.get("truncated", False),
        "is_text": is_text,
        "content": content,
        # 图片即使被判为文本（如 SVG 无 null 字节）也保留 content_b64——否则网关
        # resolve_image_to_data_url 的 is_image-and-content_b64 守卫会失败，SVG 永不解析。
        "content_b64": data.get("content_b64") if (not is_text or is_image) else None,
        "is_image": is_image,
        "is_markdown": Path(name).suffix.lower() in MD_EXTS,
        "max_bytes": MAX_FILE_BYTES,
    }


# agent 常把生成物写在子目录，但回复里偶漏前缀。下载时裸文件名直路径找不到则按序兜底。
_DOWNLOAD_FALLBACK_SUBDIRS = ("output", "charts")


async def read_file_bytes(
    k8s_manager, pod_name: str, workspace_root: Path, rel: str
) -> dict:
    """读取 workspace_root/rel 的**完整**文件字节（无截断），供下载用。

    与 read_file_content 区别：不截断、不限图片/文本、返回原始 bytes + 用于下载的
    Content-Disposition 文件名/mime。超 MAX_DOWNLOAD_BYTES 返回 error="too large"。

    实现用 k8s_manager.exec_read_file_bytes 直接二进制读取，避免旧实现把整文件
    base64 编码成单行 JSON 经 stdout 回传导致的 WebSocket 缓冲/截断问题。

    Fallback：agent 偶尔在回复里 emit 裸文件名（漏 output/ 前缀），而文件实际在
    output/、charts/ 等子目录。直路径找不到时，若 rel 是裸文件名（无路径分隔），
    按 basename 在常见子目录里搜一遍，兜住漏前缀的情况。
    """
    result = await _read_file_bytes_once(k8s_manager, pod_name, workspace_root, rel)
    if "error" in result and result["error"] in ("not a file", "not found"):
        # 仅对裸文件名做 fallback，避免对带路径的 rel 盲目重试
        if rel and "/" not in rel and "\\" not in rel:
            for sub in _DOWNLOAD_FALLBACK_SUBDIRS:
                cand = f"{sub}/{rel}"
                r2 = await _read_file_bytes_once(k8s_manager, pod_name, workspace_root, cand)
                if "error" not in r2:
                    return r2
    return result


async def _read_file_bytes_once(
    k8s_manager, pod_name: str, workspace_root: Path, rel: str
) -> dict:
    abs_path = safe_resolve_ws(workspace_root, rel)
    try:
        raw = await k8s_manager.exec_read_file_bytes(
            pod_name, str(abs_path), MAX_DOWNLOAD_BYTES
        )
    except FileNotFoundError:
        return {"error": "not found"}
    except ValueError as exc:
        if "too large" in str(exc):
            try:
                size = int(str(exc).rsplit(":", 1)[1])
            except ValueError:
                size = None
            return {
                "error": "too large",
                "size": size,
                "max": MAX_DOWNLOAD_BYTES,
            }
        raise
    name = abs_path.name
    return {
        "name": name,
        "size": len(raw),
        "bytes": raw,
        "mime": _guess_download_mime(name),
    }


# 下载用的 MIME 推断（扩展名优先，覆盖常见文档类型；未知用 octet-stream 触发下载）
_DOWNLOAD_MIME_MAP = {
    **{ext: f"image/{sub}" for ext, sub in [
        (".png", "png"), (".jpg", "jpeg"), (".jpeg", "jpeg"), (".gif", "gif"),
        (".svg", "svg+xml"), (".webp", "webp"), (".ico", "x-icon"), (".bmp", "bmp"),
    ]},
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".zip": "application/zip",
    ".html": "text/html",
}


def _guess_download_mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    return _DOWNLOAD_MIME_MAP.get(ext, "application/octet-stream")



def _guess_is_text(name: str, is_dir: bool) -> bool:
    if is_dir:
        return False
    return Path(name).suffix.lower() in TEXT_EXTS or Path(name).suffix.lower() in MD_EXTS


# ── 文件上传（写入 profile 工作区）─────────────────────────────────────

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB


# ── 工作区文件管理（增删改）────────────────────────────────────────────
# 历史约束为只读，现按产品需求开放：新建文件夹、删除、移动。

_MKDIR_SCRIPT = r"""
import sys, os, json
p = sys.argv[1]
try:
    if os.path.exists(p):
        print(json.dumps({"error": "already exists"}))
    else:
        os.makedirs(p, exist_ok=False)
        print(json.dumps({"ok": True}))
except Exception as ex:
    print(json.dumps({"error": str(ex)}))
"""

_DELETE_SCRIPT = r"""
import sys, os, json, shutil
p = sys.argv[1]
try:
    if os.path.isdir(p):
        shutil.rmtree(p)
    elif os.path.isfile(p):
        os.remove(p)
    else:
        print(json.dumps({"error": "not found"}))
        sys.exit(0)
    print(json.dumps({"ok": True}))
except FileNotFoundError:
    print(json.dumps({"error": "not found"}))
except Exception as ex:
    print(json.dumps({"error": str(ex)}))
"""

_MOVE_SCRIPT = r"""
import sys, os, json
src = sys.argv[1]
dst = sys.argv[2]
try:
    if not os.path.exists(src):
        print(json.dumps({"error": "source not found"}))
        sys.exit(0)
    if os.path.exists(dst):
        print(json.dumps({"error": "destination already exists"}))
        sys.exit(0)
    os.rename(src, dst)
    print(json.dumps({"ok": True}))
except Exception as ex:
    print(json.dumps({"error": str(ex)}))
"""


async def create_folder(
    k8s_manager, pod_name: str, workspace_root: Path, rel: str, name: str
) -> dict:
    """在 workspace_root/rel 下新建名为 name 的文件夹。"""
    if _is_sensitive(name):
        return {"error": "sensitive name"}
    parent = safe_resolve_ws(workspace_root, rel)
    target = (parent / name).resolve()
    try:
        target.relative_to(workspace_root.resolve())
    except ValueError as exc:
        return {"error": "path escapes workspace"}
    cmd = (
        "python3 -c "
        + shlex.quote(_MKDIR_SCRIPT)
        + " "
        + shlex.quote(str(target))
    )
    stdout = await k8s_manager.exec_command_in_pod(pod_name, [cmd])
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("create_folder: failed to parse exec output for %s", target)
        return {"error": "parse error"}


async def delete_entry(
    k8s_manager, pod_name: str, workspace_root: Path, rel: str
) -> dict:
    """删除 workspace_root/rel 指定的文件或文件夹。"""
    abs_path = safe_resolve_ws(workspace_root, rel)
    cmd = (
        "python3 -c "
        + shlex.quote(_DELETE_SCRIPT)
        + " "
        + shlex.quote(str(abs_path))
    )
    stdout = await k8s_manager.exec_command_in_pod(pod_name, [cmd])
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("delete_entry: failed to parse exec output for %s", abs_path)
        return {"error": "parse error"}


async def move_entry(
    k8s_manager, pod_name: str, workspace_root: Path, from_rel: str, to_rel: str
) -> dict:
    """将 workspace_root/from_rel 移动到 workspace_root/to_rel。"""
    src = safe_resolve_ws(workspace_root, from_rel)
    dst = safe_resolve_ws(workspace_root, to_rel)
    cmd = (
        "python3 -c "
        + shlex.quote(_MOVE_SCRIPT)
        + " "
        + shlex.quote(str(src))
        + " "
        + shlex.quote(str(dst))
    )
    stdout = await k8s_manager.exec_command_in_pod(pod_name, [cmd])
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("move_entry: failed to parse exec output %s -> %s", src, dst)
        return {"error": "parse error"}


def _sanitize_upload_name(filename: str) -> str:
    """清理上传文件名（移植自 hermes-webui _sanitize_upload_name）。"""
    import re
    safe = re.sub(r"[^\w.\-]", "_", Path(filename).name)[:200]
    if not safe or safe.strip(".") == "":
        raise ValueError("Invalid filename")
    return safe


async def write_upload(
    k8s_manager, pod_name: str, workspace_root: Path, filename: str, content: bytes, rel_dir: str = "uploads"
) -> dict:
    """将上传文件写入 profile 工作区的 rel_dir 子目录。

    返回 ``{filename, path, size, is_image}``（path 为相对工作区根的路径）。
    """
    import mimetypes
    import uuid

    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File too large (max {MAX_UPLOAD_BYTES // 1024 // 1024}MB)")

    safe_name = _sanitize_upload_name(filename)
    abs_dir = safe_resolve_ws(workspace_root, rel_dir)
    # 去重须基于 Pod 实际目录状态——workspace_root 只挂在引擎 Pod 上，manager 本地
    # mkdir/exists 会污染 manager 磁盘且去重永远不触发（dest.exists() 恒 False）→ 同名静默覆盖。
    existing = await list_files(k8s_manager, pod_name, workspace_root, rel_dir)
    existing_names = {e["name"] for e in existing.get("entries", [])}
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    final_name = safe_name
    idx = 1
    while final_name in existing_names:
        final_name = f"{stem}-{idx}{suffix}"
        idx += 1
    safe_name = final_name
    dest = (abs_dir / safe_name)
    try:
        dest.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise ValueError("Upload destination escapes workspace") from exc

    # base64 编码 → 分块 heredoc 写入临时文件 → base64 -d 写目标
    b64 = base64.b64encode(content).decode("ascii")
    tmp = f"/tmp/.ua_upload_{uuid.uuid4().hex}.b64"
    dest_quoted = shlex.quote(str(dest))
    try:
        chunk_size = 32768
        await k8s_manager.exec_command_in_pod(pod_name, [f"rm -f {tmp}"])
        for i in range(0, len(b64), chunk_size):
            chunk = b64[i : i + chunk_size]
            await k8s_manager.exec_command_in_pod(
                pod_name, [f"cat >> {tmp} <<'UA_EOF'\n{chunk}\nUA_EOF"]
            )
        await k8s_manager.exec_command_in_pod(
            pod_name,
            [
                f"mkdir -p {shlex.quote(str(abs_dir))}"
                f" && base64 -d {tmp} > {dest_quoted} && rm -f {tmp}"
            ],
        )
        # chown 为 profile 属主（k8s exec 以 root 运行，写入的文件属主是 root，
        # profile 进程 UID 20000+ 无法删除/管理 → 用 --reference 从 workspace root 继承属主）
        ws_quoted = shlex.quote(str(workspace_root.resolve()))
        await k8s_manager.exec_command_in_pod(
            pod_name, [f"chown --reference={ws_quoted} {shlex.quote(str(abs_dir))} {dest_quoted}"]
        )
    except Exception as exc:
        # 清理临时文件
        try:
            await k8s_manager.exec_command_in_pod(pod_name, [f"rm -f {tmp}"])
        except Exception:
            pass
        raise RuntimeError(f"Failed to write file: {exc}") from exc

    # 先按 magic bytes 嗅探真实图片类型（防假扩展名），嗅探不到再退回扩展名猜测。
    # SVG 是文本无 magic bytes，仍靠扩展名判定。
    sniffed = sniff_mime(content)
    if sniffed:
        mime = sniffed
    else:
        mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    rel_path = f"{rel_dir.rstrip('/')}/{safe_name}" if rel_dir not in (".", "", "./") else safe_name
    return {
        "filename": safe_name,
        "path": rel_path,
        "size": len(content),
        "mime": mime,
        "is_image": mime.startswith("image/"),
    }
