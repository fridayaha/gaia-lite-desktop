"""APK patch 引擎——替换 assets/server_config.json 占位符 + 重签名。

发布流程（admin 点「发布」触发）：
1. 输入 base APK bytes（带占位符 `__UA_MANAGER_URL__` / `__UA_GATEWAY_URL__`）
2. Python zipfile 替换 assets/server_config.json，跳过 META-INF/*（旧 v1 签名）
3. 保留原 compress_type（resources.arsc 必须 STORED，否则运行期 mmap 失败）
4. zipalign 对齐未压缩 entry
5. apksigner sign 写入 v1+v2+v3 签名
6. 返回 patched APK bytes

调用方（app.api.app_releases publish 端点）拿到 bytes 后存 MinIO。

设计：
- `patch()` 是 async，但内部 zipfile/subprocess 是同步阻塞——用 asyncio.to_thread 包裹
- 构造期注入二进制路径 + keystore 信息，便于单测 mock（不依赖实际 Android SDK 安装）
- 临时文件用 tempfile.NamedTemporaryFile，finally 清理
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# APK 内的 server_config.json 路径——与 Android 端 assets/server_config.json 一致
# patch 前是占位符 `__UA_MANAGER_URL__` / `__UA_GATEWAY_URL__`，patch 后是实际 URL
_ASSET_PATH = "assets/server_config.json"


def _is_v1_signature_file(name: str) -> bool:
    """识别 v1 JAR 签名文件（apksigner 重签会重新生成）。

    只跳过真正的 v1 签名文件，保留 META-INF/services/ 等 Java ServiceLoader 元数据。
    """
    if not name.startswith("META-INF/"):
        return False
    basename = name[len("META-INF/"):]
    if basename == "MANIFEST.MF":
        return True
    # *.SF / *.RSA / *.DSA / *.EC（v1 签名块，文件名可能含别名如 CERT.SF / KEY0.RSA）
    return any(basename.endswith(ext) for ext in (".SF", ".RSA", ".DSA", ".EC"))


class ApkPatchError(Exception):
    """patch 流程失败（zipalign/apksigner 返回非零，或 zip 损坏）。"""


@dataclass
class ApkPatcher:
    """APK patch 引擎。注入二进制路径 + keystore 信息，便于单测 mock。

    prod 由 settings.apk_* 配置注入（zipalign/apksigner 在 manager 镜像 PATH 里）。
    单测可构造 ApkPatcher(zipalign_bin="echo", apksigner_bin="echo", ...) 跑通流程。
    """

    keystore_path: str
    keystore_alias: str
    keystore_password: str
    key_password: str
    zipalign_bin: str = "zipalign"
    apksigner_bin: str = "apksigner"

    async def patch(self, base_apk_bytes: bytes, manager_url: str, gateway_url: str) -> bytes:
        """返回 patched + signed APK bytes。失败抛 ApkPatchError。"""
        return await asyncio.to_thread(
            self._patch_sync, base_apk_bytes, manager_url, gateway_url
        )

    # ── 同步实现 ─────────────────────────────────────────────────
    def _patch_sync(self, base_apk_bytes: bytes, manager_url: str, gateway_url: str) -> bytes:
        # 1. zipfile 替换 assets/server_config.json，跳过 META-INF/*
        replaced = self._replace_server_config(base_apk_bytes, manager_url, gateway_url)

        # 2-3. 写临时文件 → zipalign → apksigner sign → 读回
        tmp_dir = Path(tempfile.mkdtemp(prefix="apk-patch-"))
        try:
            unaligned_path = tmp_dir / "unaligned.apk"
            aligned_path = tmp_dir / "aligned.apk"
            signed_path = tmp_dir / "signed.apk"

            unaligned_path.write_bytes(replaced)
            self._run_zipalign(unaligned_path, aligned_path)
            self._run_apksigner_sign(aligned_path, signed_path)
            self._run_apksigner_verify(signed_path)
            return signed_path.read_bytes()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _replace_server_config(self, base_apk_bytes: bytes, manager_url: str, gateway_url: str) -> bytes:
        """替换 APK 内 assets/server_config.json，跳过 v1 签名文件。

        保留原 compress_type（resources.arsc 必须 STORED）；其他 entry 原样拷贝。
        v2/v3 签名块在 zip central directory 外，Python zipfile 重写后自然丢失；
        v1 签名（MANIFEST.MF / *.SF / *.RSA / *.DSA / *.EC）是普通文件，显式跳过。
        apksigner sign 会重新生成 v1+v2+v3 签名。
        """
        new_config = json.dumps(
            {"manager_url": manager_url, "gateway_url": gateway_url},
            ensure_ascii=False,
        ).encode("utf-8")

        in_buf = io.BytesIO(base_apk_bytes)
        out_buf = io.BytesIO()
        with zipfile.ZipFile(in_buf, "r") as zin, zipfile.ZipFile(out_buf, "w") as zout:
            for item in zin.infolist():
                if _is_v1_signature_file(item.filename):
                    continue
                data = zin.read(item.filename)
                if item.filename == _ASSET_PATH:
                    data = new_config
                # 保留原 compress_type（resources.arsc 必须 STORED，否则 mmap 失败）
                zout.writestr(item, data, compress_type=item.compress_type)
        return out_buf.getvalue()

    def _run_zipalign(self, src: Path, dst: Path) -> None:
        result = subprocess.run(
            [self.zipalign_bin, "-f", "-v", "4", str(src), str(dst)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("zipalign failed: %s", result.stderr)
            raise ApkPatchError(f"zipalign failed: {result.stderr or result.stdout}")

    def _run_apksigner_sign(self, src: Path, dst: Path) -> None:
        # apksigner 不支持原地覆盖，先复制再签
        shutil.copyfile(src, dst)
        result = subprocess.run(
            [
                self.apksigner_bin,
                "sign",
                "--ks", self.keystore_path,
                "--ks-key-alias", self.keystore_alias,
                "--ks-pass", f"pass:{self.keystore_password}",
                "--key-pass", f"pass:{self.key_password}",
                str(dst),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("apksigner sign failed: %s", result.stderr)
            raise ApkPatchError(f"apksigner sign failed: {result.stderr or result.stdout}")

    def _run_apksigner_verify(self, apk: Path) -> None:
        result = subprocess.run(
            [self.apksigner_bin, "verify", "--verbose", str(apk)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("apksigner verify failed: %s", result.stderr)
            raise ApkPatchError(f"apksigner verify failed: {result.stderr or result.stdout}")
