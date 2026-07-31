"""进程内图形验证码生成器。单副本够用；多副本升级 Redis。

TODO(multi-replica): 升级到 Redis（manager 多副本部署时）。
ECS 当前 manager 单副本（kubectl get deploy manager -o jsonpath='{.spec.replicas}' = 1）。

生成 4 位数字 PNG，进程内 dict 存 captcha_id → (answer, expires_at)，5min 有效，1 次性使用。
发码 endpoint 必传 captcha_id + captcha_answer，校验通过才进入限速 + 发码流程。
防 SMS/Email 轰炸：攻击者需先过图形验证码才能触发发码，单 captcha 只能试 1 次。
"""

import asyncio
import base64
import io
import random
import time
import uuid

from PIL import Image, ImageDraw

CAPTCHA_TTL = 300  # 5 min


class CaptchaService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._captchas: dict[str, tuple[str, float]] = {}  # id -> (answer, expires_at)
        self._last_gc = time.time()

    async def generate(self) -> tuple[str, str]:
        """生成图形验证码。返回 (captcha_id, base64_image)。"""
        async with self._lock:
            now = time.time()
            if now - self._last_gc > 60:
                self._gc(now)
                self._last_gc = now
            captcha_id = str(uuid.uuid4())
            answer = "".join(random.choices("0123456789", k=4))
            img = self._draw(answer)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            self._captchas[captcha_id] = (answer, now + CAPTCHA_TTL)
            return captcha_id, f"data:image/png;base64,{b64}"

    async def verify(self, captcha_id: str, answer: str) -> bool:
        """一次性校验 — 校验后立即从 dict 移除，不论成功失败。

        防爆破：单 captcha 只能试 1 次，错就重新生成。
        """
        async with self._lock:
            now = time.time()
            record = self._captchas.pop(captcha_id, None)
            if not record:
                return False
            stored_answer, expires = record
            if expires < now:
                return False
            return stored_answer == answer

    def _draw(self, answer: str) -> Image.Image:
        """画 120x40 PNG：白底 + 4 位数字 + 4 条干扰线。

        用 Pillow 默认位图字体（不依赖外部 .ttf 字体文件，Docker 镜像无需装 fontconfig）。
        """
        img = Image.new("RGB", (120, 40), "white")
        draw = ImageDraw.Draw(img)
        for i, ch in enumerate(answer):
            draw.text((10 + i * 28, random.randint(5, 15)), ch, fill="black")
        for _ in range(4):  # 干扰线
            draw.line(
                [
                    (random.randint(0, 120), random.randint(0, 40)),
                    (random.randint(0, 120), random.randint(0, 40)),
                ],
                fill="gray",
                width=1,
            )
        return img

    def _gc(self, now: float) -> None:
        expired = [k for k, (_, exp) in self._captchas.items() if exp < now]
        for k in expired:
            del self._captchas[k]

    async def reset(self) -> None:
        """测试用 — 清空所有 captcha。"""
        async with self._lock:
            self._captchas.clear()


captcha_service = CaptchaService()
