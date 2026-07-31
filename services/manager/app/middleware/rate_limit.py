"""进程内滑动窗口限流。单副本部署够用；多副本时升级到 Redis。

TODO(multi-replica): 升级到 Redis sliding window log（manager 多副本部署时）。
ECS 当前 manager 单副本（kubectl get deploy manager -o jsonpath='{.spec.replicas}' = 1），
进程内 dict + asyncio.Lock 足够。多副本时升级到 Redis（已在 ECS 部署）。

两道闸（仅作用于 /api/manager/auth/login）：
1. 单 IP 每分钟 10 次登录尝试（含成功+失败）→ 超过返回 429 too_many_requests
2. 单 IP 每小时 50 次失败 → 该 IP 拉黑 1h，期间所有 /auth/* 拒绝（403 ip_banned）

设计要点：
- 滑动窗口 log（ZSET 等价的 deque + 时间戳），而非固定窗口计数，避免边界突发。
- asyncio.Lock 保护 dict 并发安全；GC 周期 60s 清理过期 key，避免内存泄漏。
- 黑名单（_ip_blacklist）是「IP -> expires_at」映射，过期自动 GC。
"""
import asyncio
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class RateLimiter:
    def __init__(
        self,
        minute_limit: int = 10,
        hour_failure_limit: int = 50,
        ban_seconds: int = 3600,
    ):
        self._lock = asyncio.Lock()
        self._minute_limit = minute_limit
        self._hour_failure_limit = hour_failure_limit
        self._ban_seconds = ban_seconds
        # ip -> deque[timestamp]（每分钟窗口）
        self._minute_buckets: dict[str, deque[float]] = defaultdict(deque)
        # ip -> deque[timestamp]（每小时失败窗口）
        self._hour_failures: dict[str, deque[float]] = defaultdict(deque)
        # ip -> 拉黑到期时间戳
        self._ip_blacklist: dict[str, float] = {}
        self._last_gc = time.time()

    async def check_login(self, ip: str) -> None:
        """登录前调用。通过则记一次尝试；超限抛 429 / 403。"""
        async with self._lock:
            now = time.time()
            # 周期 GC（每 60s 清一次过期 key，避免内存泄漏）
            if now - self._last_gc > 60:
                self._gc(now)
                self._last_gc = now

            # 黑名单检查（命中 → 403 ip_banned）
            banned_until = self._ip_blacklist.get(ip)
            if banned_until and banned_until > now:
                wait = int(banned_until - now)
                raise HTTPException(
                    status_code=403,
                    detail="ip_banned",
                    headers={"Retry-After": str(wait)},
                )

            # 每分钟窗口检查
            minute_bucket = self._minute_buckets[ip]
            while minute_bucket and minute_bucket[0] < now - 60:
                minute_bucket.popleft()
            if len(minute_bucket) >= self._minute_limit:
                raise HTTPException(
                    status_code=429,
                    detail="too_many_requests",
                    headers={"Retry-After": "60"},
                )
            minute_bucket.append(now)

    async def record_failure(self, ip: str) -> None:
        """登录失败后调用。每小时 N 次失败 → 拉黑 1h。"""
        async with self._lock:
            now = time.time()
            bucket = self._hour_failures[ip]
            while bucket and bucket[0] < now - 3600:
                bucket.popleft()
            bucket.append(now)
            if len(bucket) >= self._hour_failure_limit:
                self._ip_blacklist[ip] = now + self._ban_seconds
                bucket.clear()

    async def reset(self) -> None:
        """测试用：清空所有状态。"""
        async with self._lock:
            self._minute_buckets.clear()
            self._hour_failures.clear()
            self._ip_blacklist.clear()

    def _gc(self, now: float) -> None:
        """清理所有过期 key（不再被任何 IP 引用）。GC 调用时持锁。"""
        for d in (self._minute_buckets, self._hour_failures):
            empty_keys = [k for k, v in d.items() if not v or v[0] < now - 3700]
            for k in empty_keys:
                del d[k]
        expired_bans = [k for k, exp in self._ip_blacklist.items() if exp < now]
        for k in expired_bans:
            del self._ip_blacklist[k]


# 全局单例（main.py middleware 引用；测试可通过 reset() 清空状态）
rate_limiter = RateLimiter()


class CodeRateLimiter:
    """验证码发送限流 — 单 target 1/interval, 5/hour, daily_limit/day；单 IP 10/hour。

    Phase 1（0.8.104+）：与登录 RateLimiter 分开存状态，避免 IP 拉黑互相干扰
    （登录失败的 IP 拉黑不影响该 IP 改密码 / 改绑）。

    限速 4 道闸：
    1. 单 target interval_seconds 间隔（从 SmsConfig/EmailConfig 取，默认 60s）
    2. 单 target 5/hour（每小时最多 5 条，防同一号被反复轰炸）
    3. 单 target daily_limit/day（从 cfg 取，默认 10）
    4. 单 IP 10/hour（防扫描式攻击 — 攻击者拿一批手机号/邮箱轮询触发发码）

    任一超限抛 HTTPException(429/403)。
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._target_minute: dict[str, deque[float]] = defaultdict(deque)
        self._target_hour: dict[str, deque[float]] = defaultdict(deque)
        self._target_day: dict[str, deque[float]] = defaultdict(deque)
        self._ip_hour: dict[str, deque[float]] = defaultdict(deque)
        self._last_gc = time.time()

    async def check_send(
        self,
        target: str,
        ip: str,
        daily_limit: int = 10,
        interval_seconds: int = 60,
    ) -> None:
        """发码前调用。超限抛 HTTPException。

        Args:
            target: 手机号或邮箱（用作限速 key）
            ip: 客户端 IP
            daily_limit: 单日上限（从 SmsConfig/EmailConfig.daily_limit 取）
            interval_seconds: 同号最小间隔（从 cfg.interval_seconds 取）
        """
        async with self._lock:
            now = time.time()
            if now - self._last_gc > 60:
                self._gc(now)
                self._last_gc = now

            # 1. interval_seconds 间隔
            t_min = self._target_minute[target]
            while t_min and t_min[0] < now - interval_seconds:
                t_min.popleft()
            if t_min:
                raise HTTPException(
                    status_code=429,
                    detail="code_too_frequent",
                    headers={"Retry-After": str(interval_seconds)},
                )
            t_min.append(now)

            # 2. 5/hour
            t_hour = self._target_hour[target]
            while t_hour and t_hour[0] < now - 3600:
                t_hour.popleft()
            if len(t_hour) >= 5:
                raise HTTPException(
                    status_code=429,
                    detail="code_target_hourly_limit",
                    headers={"Retry-After": "3600"},
                )
            t_hour.append(now)

            # 3. daily_limit/day
            t_day = self._target_day[target]
            while t_day and t_day[0] < now - 86400:
                t_day.popleft()
            if len(t_day) >= daily_limit:
                raise HTTPException(
                    status_code=429,
                    detail="code_target_daily_limit",
                    headers={"Retry-After": "3600"},
                )
            t_day.append(now)

            # 4. 10/hour per IP
            ip_hour = self._ip_hour[ip]
            while ip_hour and ip_hour[0] < now - 3600:
                ip_hour.popleft()
            if len(ip_hour) >= 10:
                raise HTTPException(
                    status_code=403,
                    detail="ip_code_banned",
                    headers={"Retry-After": "3600"},
                )
            ip_hour.append(now)

    def _gc(self, now: float) -> None:
        for d in (self._target_minute, self._target_hour, self._target_day, self._ip_hour):
            empty = [k for k, v in d.items() if not v or v[0] < now - 86500]
            for k in empty:
                del d[k]

    async def reset(self) -> None:
        """测试用 — 清空所有限速状态。"""
        async with self._lock:
            self._target_minute.clear()
            self._target_hour.clear()
            self._target_day.clear()
            self._ip_hour.clear()


# 全局单例（auth.py 引用；测试可通过 reset() 清空状态）
code_rate_limiter = CodeRateLimiter()
