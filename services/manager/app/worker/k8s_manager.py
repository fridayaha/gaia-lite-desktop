"""Kubernetes manager — manages engine Pod lifecycle via K8s API.

命名规范 (由 Controller 创建时保证，Gateway 据此路由):
  Deployment:  engine-hermes-{agent_id[:8]}
  Service:     engine-hermes-{agent_id[:8]}
  Label:       agent.unionagents/agent-id={agent_id}
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import UTC, datetime

import kubernetes
from kubernetes.client import (
    AppsV1Api,
    CoreV1Api,
    CustomObjectsApi,
    NetworkingV1Api,
    V1Affinity,
    V1Container,
    V1ContainerPort,
    V1Deployment,
    V1DeploymentSpec,
    V1EmptyDirVolumeSource,
    V1EnvVar,
    V1EnvVarSource,
    V1HTTPGetAction,
    V1KeyToPath,
    V1LabelSelector,
    V1LocalObjectReference,
    V1NodeAffinity,
    V1NodeSelectorRequirement,
    V1NodeSelectorTerm,
    V1NetworkPolicy,
    V1NetworkPolicyIngressRule,
    V1NetworkPolicyPeer,
    V1NetworkPolicyPort,
    V1NetworkPolicyPort,
    V1NetworkPolicySpec,
    V1ObjectMeta,
    V1PersistentVolumeClaim,
    V1PersistentVolumeClaimSpec,
    V1PersistentVolumeClaimVolumeSource,
    V1PodSpec,
    V1PodTemplateSpec,
    V1PreferredSchedulingTerm,
    V1Probe,
    V1ProjectedVolumeSource,
    V1ResourceRequirements,
    V1Secret,
    V1SecretKeySelector,
    V1SecretProjection,
    V1Service,
    V1ServicePort,
    V1ServiceSpec,
    V1TCPSocketAction,
    V1Volume,
    V1VolumeMount,
)

logger = logging.getLogger(__name__)


def _engine_port(engine_type: str | None) -> int:
    """按 engine_type 取引擎监听端口（ENGINE_RUNTIMES，替代硬编码 8642）。"""
    from pkg.common.config import get_engine_runtime

    return get_engine_runtime(engine_type)["port"]


def _parse_cpu(q: str) -> int:
    """K8s CPU 量 → millicores 整数。支持 K8s 资源量全部后缀。

    如 "1"→1000, "100m"→100, "0.5"→500, "3303374n"→3, "500u"→0。
    metrics-server 对低/空闲 CPU 常以 nanocores('n') 上报，必须解析否则用量恒为 0。
    """
    if not q:
        return 0
    s = str(q).strip()
    # 后缀 → 相对 millicores 的乘数（value 为该后缀单位下的数值）
    suffixes = {
        "n": 1e-6,  # nanocores → millicores
        "u": 1e-3,  # microcores → millicores
        "m": 1,  # millicores
        "k": 1e6,  # kilocores → millicores
        "M": 1e9,
        "G": 1e12,
        "T": 1e15,
    }
    try:
        for suf, mult in suffixes.items():
            if s.endswith(suf):
                return int(float(s[: -len(suf)]) * mult)
        # 无后缀 → cores
        return int(float(s) * 1000)
    except (ValueError, TypeError):
        return 0


def _parse_mem(q: str) -> int:
    """K8s 内存量 → bytes。支持 Ki/Mi/Gi/Ti 及纯数字。"""
    if not q:
        return 0
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, mult in units.items():
        if q.endswith(suffix):
            try:
                return int(float(q[: -len(suffix)]) * mult)
            except (ValueError, TypeError):
                return 0
    try:
        return int(float(q))
    except (ValueError, TypeError):
        return 0


def _format_mem(b: int) -> str:
    """bytes → 人类可读 Mi（向上取整）。"""
    if not b:
        return ""
    return f"{round(b / 1024 / 1024)}Mi"


def _agent_short_id(agent_id: str) -> str:
    """取 UUID 前 8 位作为 K8s 资源名后缀"""
    return agent_id.replace("-", "")[:8]


def _scope_hash(scope_type: str, scope_target_id: str | None) -> str:
    """生成 scope 的短哈希"""
    raw = f"{scope_type}:{scope_target_id or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:6]


def _is_pod_ready(pod) -> bool:
    """pod 对象的 Ready 条件是否为 True（供 wait_deployment_ready 判定滚动完成）。"""
    for cond in pod.status.conditions or []:
        if cond.type == "Ready" and cond.status == "True":
            return True
    return False


def _engine_name(agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None) -> str:
    """命名: ALL scope 用旧格式（向后兼容），scoped 用新格式"""
    short_id = _agent_short_id(agent_id)
    if scope_type == "ALL" and not scope_target_id:
        return f"engine-hermes-{short_id}"  # 向后兼容现有部署
    shash = _scope_hash(scope_type, scope_target_id)
    return f"engine-hermes-{short_id}-{shash}"


# Hermes 引擎数据目录（V2：PVC 挂载于 /opt/data，profile 在 /opt/data/profiles/{name}）


def _pvc_name(agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None) -> str:
    """PVC 命名: engine-data-{short_id}[-{shash}]，与 _engine_name 对应"""
    short_id = _agent_short_id(agent_id)
    if scope_type == "ALL" and not scope_target_id:
        return f"engine-data-{short_id}"
    shash = _scope_hash(scope_type, scope_target_id)
    return f"engine-data-{short_id}-{shash}"


# ── 浏览器沙箱 Pod 命名（per-profile，独立 Pod，与引擎 Pod 解耦）──
# Chrome 强制把 CDP 绑 127.0.0.1（忽略 --remote-debugging-address，P0 实测），
# 故 browser Pod 内跑 CDP 代理（0.0.0.0:9222 → chrome 127.0.0.1:9223）暴露给引擎跨 Pod 访问。


def _profile_hash(profile_name: str) -> str:
    """profile_name → 6 位短哈希（browser 资源名后缀，命名确定性保证 cdp_url 跨重建稳定）"""
    return hashlib.sha256(str(profile_name).encode()).hexdigest()[:6]


def _browser_name(agent_id: str, profile_name: str) -> str:
    """browser Pod/Service 命名: browser-{short}-{ph}"""
    return f"browser-{_agent_short_id(agent_id)}-{_profile_hash(profile_name)}"


def _browser_pvc_name(agent_id: str, profile_name: str) -> str:
    """browser-data PVC 命名: browser-data-{short}-{ph}"""
    return f"browser-data-{_agent_short_id(agent_id)}-{_profile_hash(profile_name)}"


def _browser_vnc_secret_name(agent_id: str, profile_name: str) -> str:
    """VNC_PW Secret 命名: browser-vnc-{short}-{ph}"""
    return f"browser-vnc-{_agent_short_id(agent_id)}-{_profile_hash(profile_name)}"


def _browser_network_policy_name(agent_id: str, profile_name: str) -> str:
    """NetworkPolicy 命名: browser-net-{short}-{ph}"""
    return f"browser-net-{_agent_short_id(agent_id)}-{_profile_hash(profile_name)}"


# browser Pod 内 CDP 代理：cdp-proxy sidecar 跑 socat（browser-v2 镜像内置），
# 0.0.0.0:9222 → chrome 127.0.0.1:9223。chrome 强制绑 loopback（忽略
# --remote-debugging-address，P0 实测），socat 把 CDP 暴露到 Pod 网络供引擎跨 Pod 访问。



# 引擎 Pod 销毁前数据备份 finalizer：Pod 被 delete（含外部 kubectl delete
# pod/deployment/namespace、SUSPEND scale=0）时，finalizer 使其保持 Terminating 但
# 容器仍运行，给 manager 留出 exec tar → MinIO 的窗口；备份完成后再移除放行。
DATA_BACKUP_FINALIZER = "unionagents.io/data-backup"

# exec_tar_data_by_pod 总超时（秒）：tar 120 + cat 120 + rm 10 最坏 250s，留余量
_EXEC_TAR_TOTAL_TIMEOUT = 300


class K8sManager:
    """Kubernetes 客户端封装，管理 Agent Engine Pod 生命周期"""

    def __init__(self, namespace: str = "unionagents"):
        self.namespace = namespace
        try:
            kubernetes.config.load_incluster_config()
            logger.info("Loaded in-cluster K8s config")
        except kubernetes.config.ConfigException:
            kubernetes.config.load_kube_config()
            logger.info("Loaded kubeconfig for local development")

        self.apps_v1 = AppsV1Api()
        self.core_v1 = CoreV1Api()
        self.custom_v1 = CustomObjectsApi()
        self.net_v1 = NetworkingV1Api()

    # ── PVC 管理 ──────────────────────────────────────────

    def _build_pvc(self, pvc_name: str, labels: dict) -> V1PersistentVolumeClaim:
        """构建 PVC 对象"""
        from pkg.common.config import settings

        return V1PersistentVolumeClaim(
            metadata=V1ObjectMeta(name=pvc_name, labels=labels),
            spec=V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=V1ResourceRequirements(
                    requests={"storage": settings.pvc_storage_size},
                ),
                storage_class_name=settings.pvc_storage_class,
            ),
        )

    def create_pvc(self, pvc_name: str, labels: dict) -> bool:
        """创建 PVC，返回 True=新建，False=已存在"""
        try:
            pvc = self._build_pvc(pvc_name, labels)
            self.core_v1.create_namespaced_persistent_volume_claim(
                self.namespace,
                pvc,
            )
            logger.info(f"Created PVC {pvc_name}")
            return True
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 409:
                logger.info(f"PVC {pvc_name} already exists, reusing")
                return False
            raise

    def delete_pvc(self, pvc_name: str):
        """删除 PVC"""
        try:
            self.core_v1.delete_namespaced_persistent_volume_claim(
                pvc_name,
                self.namespace,
            )
            logger.info(f"Deleted PVC {pvc_name}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    # ── 销毁前数据备份 finalizer ──────────────────────────

    async def list_terminating_engine_pods(self) -> list[dict]:
        """列出处于 Terminating（deletion_timestamp 已设）且带 data-backup finalizer
        的引擎 Pod，供 reconcile 循环销毁前备份。

        返回 [{agent_id, pod_name, terminating_since(datetime|None)}]。
        """
        out: list[dict] = []
        try:
            pods = self.core_v1.list_namespaced_pod(
                self.namespace, label_selector="agent-id"
            )
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"list_terminating_engine_pods failed: {e}")
            return out
        for p in pods.items or []:
            meta = p.metadata
            if meta.deletion_timestamp is None:
                continue
            finals = meta.finalizers or []
            if DATA_BACKUP_FINALIZER not in finals:
                continue
            out.append(
                {
                    "agent_id": (meta.labels or {}).get("agent-id"),
                    "pod_name": meta.name,
                    "terminating_since": meta.deletion_timestamp,
                }
            )
        return out

    async def is_pod_container_running(self, pod_name: str, container: str = "engine") -> bool:
        """检查 Pod 的指定容器是否仍在运行（可 exec 进去）。

        finalizer 只阻止 pod 对象删除，不阻止 k8s 在 grace period 后 SIGKILL 容器。
        容器 terminated 后 exec 进不去 → reconcile 应据此跳过备份、直接移除 finalizer。
        """
        try:
            pod = self.core_v1.read_namespaced_pod(pod_name, self.namespace)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                return False  # pod 已删除
            logger.warning("is_pod_container_running read %s failed: %s", pod_name, e)
            return False
        for cs in pod.status.container_statuses or []:
            if cs.name == container:
                return cs.state is not None and cs.state.running is not None
        return False  # 容器不存在

    async def is_pod_ready(self, pod_name: str) -> bool:
        """检查 Pod 的 Ready 条件是否为 True（readiness probe 通过 = deploy 成功）。

        用于 get_agent_status DEPLOYING 分支：若 pod 已 Ready 但状态仍 DEPLOYING，
        说明 _run_deploy 被中断/取消未更新状态 → 据此恢复 RUNNING。
        """
        try:
            pod = self.core_v1.read_namespaced_pod(pod_name, self.namespace)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                return False
            logger.warning("is_pod_ready read %s failed: %s", pod_name, e)
            return False
        for cond in pod.status.conditions or []:
            if cond.type == "Ready" and cond.status == "True":
                return True
        return False

    async def remove_finalizer(self, pod_name: str) -> None:
        """从指定 Pod 移除 data-backup finalizer，放行其终止。"""
        try:
            pod = self.core_v1.read_namespaced_pod(pod_name, self.namespace)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                return
            raise
        finals = pod.metadata.finalizers or []
        new_finals = [f for f in finals if f != DATA_BACKUP_FINALIZER]
        if len(new_finals) == len(finals):
            return  # finalizer 不在，无需 patch
        # patch_namespaced_pod 默认 strategic merge patch，对空 finalizers list 不放行（实测
        # finalizer 不移除）；用 null 删除 finalizers 字段（strategic merge 对 null 删字段才生效）。
        # 否则 suspend/destroy 后 Pod 永远卡 Terminating，靠手动 kubectl patch 清理。
        body = {"metadata": {"finalizers": new_finals or None}}
        self.core_v1.patch_namespaced_pod(pod_name, self.namespace, body)
        logger.info(f"Removed {DATA_BACKUP_FINALIZER} from {pod_name}")

    async def remove_finalizer_from_agent_pods(self, agent_id: str) -> None:
        """移除某 agent 所有 *非 Terminating* Pod 的 finalizer。

        供 _do_suspend 在已同步备份后、scale_to_zero 前调用，避免 reconcile 重复 tar。
        Terminating 的 Pod 交给 reconcile 处理。
        """
        try:
            pods = self.core_v1.list_namespaced_pod(
                self.namespace, label_selector=f"agent-id={agent_id}"
            )
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"remove_finalizer_from_agent_pods list failed: {e}")
            return
        for p in pods.items or []:
            if p.metadata.deletion_timestamp is not None:
                continue
            try:
                await self.remove_finalizer(p.metadata.name)
            except Exception as e:
                logger.warning(f"remove finalizer {p.metadata.name} failed: {e}")

    async def remove_finalizer_from_all_pods(self, agent_id: str) -> None:
        """移除某 agent **所有** Pod（含 Terminating）的 finalizer，供测试/运维强制清理。

        与 remove_finalizer_from_agent_pods 的区别：不跳过 Terminating Pod。reconcile 循环
        未运行时（如 e2e），级联删除产生的 Terminating Pod 会因 finalizer 卡住，需此方法放行。
        """
        try:
            pods = self.core_v1.list_namespaced_pod(
                self.namespace, label_selector=f"agent-id={agent_id}"
            )
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"remove_finalizer_from_all_pods list failed: {e}")
            return
        for p in pods.items or []:
            try:
                await self.remove_finalizer(p.metadata.name)
            except Exception as e:
                logger.warning(f"remove finalizer {p.metadata.name} failed: {e}")

    def pvc_exists(self, pvc_name: str) -> bool:
        """检查 PVC 是否存在"""
        try:
            self.core_v1.read_namespaced_persistent_volume_claim(
                pvc_name,
                self.namespace,
            )
            return True
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                return False
            raise

    async def wait_pvc_deleted(self, pvc_name: str, timeout: int = 60) -> bool:
        """等待 PVC 被删除（解决 delete 后立即重建同名 PVC 的 409/Terminating 竞态）。"""
        for _ in range(timeout):
            if not self.pvc_exists(pvc_name):
                return True
            await asyncio.sleep(1)
        return not self.pvc_exists(pvc_name)

    # ── Pod 创建 ──────────────────────────────────────────

    async def create_agent_engine(
        self,
        agent_id: str,
        config: dict = None,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
        resource_spec: dict | None = None,
        preferred_node: str = None,
        engine_instance_image: str | None = None,
        engine_type: str | None = None,
        group_code: str | None = None,
    ) -> str:
        """为 Agent 创建引擎 Deployment + Service（按 scope 维度）

        scope_type/scope_target_id: 部署范围
        resource_spec: ResourcePool 的资源规格 override（min_cpu/max_cpu/min_memory/max_memory）
        preferred_node: 若提供，Pod 会优先调度到该节点（preferredDuringScheduling）。
        engine_type: 引擎类型，用于查 ENGINE_RUNTIMES 取镜像/端口（替代硬编码 8642）。
        group_code: UserGroup 机器码（user_groups.code），写入 Pod/PVC/Service label
            `group.unionagents/group-code`，便于按组查询/导出/清理 K8s 资源。

        经 to_thread 调同步实现，不阻塞 event loop（创建 Deployment+Service 的 K8s 往返
        期间不再卡住其他协程，避免连接池死锁）。
        """
        return await asyncio.to_thread(
            self._create_agent_engine_sync,
            agent_id,
            config,
            scope_type,
            scope_target_id,
            resource_spec,
            preferred_node,
            engine_instance_image,
            engine_type,
            group_code,
        )

    def _create_agent_engine_sync(
        self,
        agent_id: str,
        config: dict = None,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
        resource_spec: dict | None = None,
        preferred_node: str = None,
        engine_instance_image: str | None = None,
        engine_type: str | None = None,
        group_code: str | None = None,
    ) -> str:
        """create_agent_engine 同步实现（K8s API 调用，由 async 版 to_thread 调用）"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        short_id = _agent_short_id(agent_id)
        shash = _scope_hash(scope_type, scope_target_id)
        # 引擎端口按 engine_type 解析（ENGINE_RUNTIMES）
        port = _engine_port(engine_type)
        # selector / template labels
        pod_labels = {
            "app": f"engine-hermes-{short_id}",
            "agent-id": agent_id,
            "scope-hash": shash,
            # 常量组件 label：供 browser Pod NetworkPolicy 选择 engine Pod 做 CDP ingress 白名单
            "unionagents.io/component": "engine",
        }
        resource_labels = {
            "app": "engine-hermes",
            "agent.unionagents/agent-id": agent_id,
            "agent.unionagents/scope-hash": shash,
        }
        # 组隔离 label：按组查询/导出/清理 K8s 资源（group_code 缺失时不加，避免空值）
        if group_code:
            _gc = str(group_code)
            pod_labels["group.unionagents/group-code"] = _gc
            resource_labels["group.unionagents/group-code"] = _gc

        # 使用 resource_spec 覆盖默认资源
        if resource_spec:
            req_cpu = resource_spec.get("min_cpu", "100m")
            lim_cpu = resource_spec.get("max_cpu", "1")
            req_mem = resource_spec.get("min_memory", "128Mi")
            lim_mem = resource_spec.get("max_memory", "1Gi")
        else:
            req_cpu, lim_cpu = "100m", "1"
            req_mem, lim_mem = "128Mi", "1Gi"

        # ── Engine 镜像配置：调用方传入（已按 engine_type 解析）> ENGINE_RUNTIMES 默认 ──
        from pkg.common.config import get_engine_runtime, settings

        _runtime = get_engine_runtime(engine_type)
        engine_image = engine_instance_image or _runtime["image"]
        engine_image_pull_policy = os.getenv("UA_ENGINE_IMAGE_PULL_POLICY", "IfNotPresent")
        engine_image_pull_secrets_name = os.getenv("UA_ENGINE_IMAGE_PULL_SECRETS", "")

        # ── Env from config ──
        env = [
            V1EnvVar(name="TZ", value="Asia/Shanghai"),
            V1EnvVar(name="AGENT_ID", value=agent_id),
            V1EnvVar(
                name="API_SERVER_KEY",
                value_from=V1EnvVarSource(
                    secret_key_ref=V1SecretKeySelector(
                        name="unionagents-secret", key="api-server-key"
                    )
                ),
            ),
            V1EnvVar(name="API_SERVER_ENABLED", value="true"),
            V1EnvVar(name="API_SERVER_HOST", value="0.0.0.0"),
            V1EnvVar(name="API_SERVER_PORT", value=str(port)),
            V1EnvVar(name="GATEWAY_ALLOW_ALL_USERS", value="true"),
            # Hermes Langfuse 插件凭据（双写关联用，与 Gateway 同 Langfuse 实例）。
            # optional=True：Secret 缺这些 key 时 Pod 仍能启动，插件 fail open。
            # 详见 engines/hermes/CLAUDE.md「Langfuse trace 归属」节。
            V1EnvVar(
                name="HERMES_LANGFUSE_PUBLIC_KEY",
                value_from=V1EnvVarSource(
                    secret_key_ref=V1SecretKeySelector(
                        name="unionagents-secret",
                        key="hermes-langfuse-public-key",
                        optional=True,
                    )
                ),
            ),
            V1EnvVar(
                name="HERMES_LANGFUSE_SECRET_KEY",
                value_from=V1EnvVarSource(
                    secret_key_ref=V1SecretKeySelector(
                        name="unionagents-secret",
                        key="hermes-langfuse-secret-key",
                        optional=True,
                    )
                ),
            ),
            V1EnvVar(
                name="HERMES_LANGFUSE_BASE_URL",
                value_from=V1EnvVarSource(
                    secret_key_ref=V1SecretKeySelector(
                        name="unionagents-secret",
                        key="hermes-langfuse-base-url",
                        optional=True,
                    )
                ),
            ),
            # manager 内部令牌：current-user-info 预置 skill 调
            # /api/controller/profiles/{profile_name}/user-context 时作
            # X-Internal-Token 鉴权（与 manager/gateway 共用 internal-token）。
            # optional=True：Secret 无此 key 时不阻断启动（端点未配 token 时放行）。
            V1EnvVar(
                name="UA_INTERNAL_TOKEN",
                value_from=V1EnvVarSource(
                    secret_key_ref=V1SecretKeySelector(
                        name="unionagents-secret",
                        key="internal-token",
                        optional=True,
                    )
                ),
            ),
        ]
        if config:
            for key, val in config.items():
                env.append(V1EnvVar(name=key, value=str(val)))

        # ── Node affinity: 优先调度到之前的节点（利用已有镜像缓存）──
        affinity = None
        if preferred_node:
            affinity = V1Affinity(
                node_affinity=V1NodeAffinity(
                    preferred_during_scheduling_ignored_during_execution=[
                        V1PreferredSchedulingTerm(
                            weight=100,
                            preference=V1NodeSelectorTerm(
                                match_expressions=[
                                    V1NodeSelectorRequirement(
                                        key="kubernetes.io/hostname",
                                        operator="In",
                                        values=[preferred_node],
                                    )
                                ]
                            ),
                        )
                    ]
                )
            )
            logger.info("Engine %s prefers node %s", short_id, preferred_node)

        # ── PVC: 在 Deployment 前创建（PVC 独立于 Pod 生命周期）──
        pvc_name = _pvc_name(agent_id, scope_type, scope_target_id)
        self.create_pvc(pvc_name, resource_labels)

        # ── Volume: PVC 挂载到 /opt/data（V2 多 profile 布局，已去除 V1 emptyDir）──
        _mount_path = "/opt/data"
        _volumes = [
            V1Volume(
                name="hermes-data",
                persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc_name,
                ),
            ),
            # credential-encryption-key 投影为只读文件 → sidecar 每请求重读，key 轮换无需
            # 重启 Pod（kubelet ~60s 刷新 projected volume 内容）。optional=True：本地 k3s
            # dev 的 secret.yaml 无此 key 时空目录，sidecar 回退 env/dev（与 dev 兜底一致）。
            V1Volume(
                name="credential-key",
                projected=V1ProjectedVolumeSource(
                    sources=[
                        V1SecretProjection(
                            name="unionagents-secret",
                            items=[
                                V1KeyToPath(
                                    key="credential-encryption-key",
                                    path="credential-encryption-key",
                                )
                            ],
                            optional=True,
                        ),
                    ]
                ),
            ),
        ]

        # ── Deployment ──
        deployment = V1Deployment(
            metadata=V1ObjectMeta(name=name, labels=resource_labels),
            spec=V1DeploymentSpec(
                replicas=1,
                selector=V1LabelSelector(match_labels=pod_labels),
                template=V1PodTemplateSpec(
                    metadata=V1ObjectMeta(
                        labels=pod_labels,
                        # 销毁前数据备份 finalizer：见 DATA_BACKUP_FINALIZER 注释
                        finalizers=[DATA_BACKUP_FINALIZER],
                    ),
                    spec=V1PodSpec(
                        affinity=affinity,
                        containers=[
                            V1Container(
                                name="engine",
                                image=engine_image,
                                image_pull_policy=engine_image_pull_policy,
                                ports=[V1ContainerPort(container_port=port)],
                                env=env,
                                volume_mounts=[
                                    V1VolumeMount(
                                        name="hermes-data",
                                        mount_path=_mount_path,
                                    ),
                                ],
                                resources=V1ResourceRequirements(
                                    requests={"cpu": req_cpu, "memory": req_mem},
                                    limits={"cpu": lim_cpu, "memory": lim_mem},
                                ),
                                readiness_probe=V1Probe(
                                    http_get=V1HTTPGetAction(path="/health", port=port),
                                    initial_delay_seconds=5,
                                    period_seconds=2,
                                    timeout_seconds=3,
                                    failure_threshold=60,
                                    success_threshold=1,
                                ),
                            ),
                            # skill-secret-sidecar：持 credential_encryption_key，解密 secrets.enc
                            # 返回明文给 skill execute_code（hermes 沙箱读不到 env，故 sidecar 代解密）
                            # 镜像默认裸名（本地/有 docker mirror 的环境可用）；云端走 ACR 须设
                            # UA_SKILL_SIDECAR_IMAGE 指向 ACR 路径（裸名走 docker.io 国内被墙）
                            V1Container(
                                name="skill-secret-sidecar",
                                image=os.getenv(
                                    "UA_SKILL_SIDECAR_IMAGE",
                                    "unionagents/skill-secret-sidecar:latest",
                                ),
                                image_pull_policy="IfNotPresent",
                                ports=[V1ContainerPort(container_port=8004)],
                                env=[
                                    V1EnvVar(
                                        name="CREDENTIAL_ENCRYPTION_KEY",
                                        value=settings.credential_encryption_key or "",
                                    ),
                                    V1EnvVar(name="UA_SKILLS_ROOT", value="/opt/data/skills"),
                                ],
                                volume_mounts=[
                                    V1VolumeMount(name="hermes-data", mount_path=_mount_path),
                                    V1VolumeMount(
                                        name="credential-key",
                                        mount_path="/etc/ua/credential-key",
                                        read_only=True,
                                    ),
                                ],
                                resources=V1ResourceRequirements(
                                    requests={"cpu": "50m", "memory": "64Mi"},
                                    limits={"cpu": "200m", "memory": "128Mi"},
                                ),
                            ),
                        ],
                        image_pull_secrets=[
                            V1LocalObjectReference(name=engine_image_pull_secrets_name)
                        ]
                        if engine_image_pull_secrets_name
                        else None,
                        volumes=_volumes,
                    ),
                ),
            ),
        )

        # ── Service (selector 必须与 pod_labels 一致) ──
        service = V1Service(
            metadata=V1ObjectMeta(name=name, labels=resource_labels),
            spec=V1ServiceSpec(
                ports=[V1ServicePort(port=port, target_port=port)],
                selector=pod_labels,
            ),
        )

        # Apply：先清理该 agent 的旧引擎 Deployment（去重，避免重复部署/孤儿堆积）
        # 按 deployment metadata label agent.unionagents/agent-id 匹配
        # （app 标签是通用 engine-hermes）
        try:
            olds = self.apps_v1.list_namespaced_deployment(
                self.namespace, label_selector=f"agent.unionagents/agent-id={agent_id}"
            )
            for old in olds.items:
                # 跳过即将创建的同名 Deployment（理论上 scope_hash 不同，不会同名）
                if old.metadata.name == name:
                    continue
                try:
                    self.apps_v1.delete_namespaced_deployment(old.metadata.name, self.namespace)
                    logger.info(
                        f"Cleaned up old engine deployment {old.metadata.name} for agent {short_id}"
                    )
                except kubernetes.client.exceptions.ApiException as e:
                    if e.status != 404:
                        raise
        except kubernetes.client.exceptions.ApiException:
            pass  # 列表失败不阻断创建

        try:
            self.apps_v1.create_namespaced_deployment(self.namespace, deployment)
            logger.info(f"Created Deployment {name}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 409:  # Already exists (e.g., SUSPENDED with replicas=0)
                # Patch: update spec (new image/env) + scale up to replicas=1.
                # Without this, a SUSPENDED (replicas=0) Deployment won't scale up
                # → wait_engine_ready times out → FAILED.
                #
                # selector 不可变：老 Deployment（af808a9 前创建）的 selector /
                # pod template labels 缺 `unionagents.io/component=engine`，直接整体
                # patch 会 422 immutable。读出现有 selector 钉住（no-op），strategic-
                # merge 仍会把缺失的 template label 合并进去（browser Pod NetworkPolicy
                # 放行 engine→9222 CDP 依赖该 label）+ 更新 image/env/replicas。
                try:
                    existing = self.apps_v1.read_namespaced_deployment(name, self.namespace)
                    deployment.spec.selector = existing.spec.selector
                except kubernetes.client.exceptions.ApiException as read_err:
                    if read_err.status != 404:
                        raise
                self.apps_v1.patch_namespaced_deployment(
                    name=name, namespace=self.namespace, body=deployment
                )
                logger.info(f"Updated Deployment {name} (re-deploy/scale up, labels backfilled)")
            else:
                raise

        try:
            self.core_v1.create_namespaced_service(self.namespace, service)
            logger.info(f"Created Service {name}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 409:
                logger.info(f"Service {name} already exists")
            else:
                raise

        return name

    # ── 状态查询 ──────────────────────────────────────────

    def _get_pod_status_sync(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ) -> dict:
        """返回 Pod 状态信息（同步实现，供 async 版 to_thread 调用不阻塞 event loop）"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        try:
            pod_list = self.core_v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"app={name}",
            )
            if not pod_list.items:
                return {"running": False, "phase": "NotFound", "reason": None}

            # 优先选 Running 的 Pod：stale Error/Unknown Pod（前次 rollout 残留）
            # 非 Terminating 但已死，exec 命中会 Connection reset。
            # 其次选非 Terminating，最后回退第一个（报状态用）。
            pod = None
            for p in pod_list.items:
                if (
                    p.status.phase == "Running"
                    and p.metadata.deletion_timestamp is None
                ):
                    pod = p
                    break
            if pod is None:
                for p in pod_list.items:
                    if p.metadata.deletion_timestamp is None:
                        pod = p
                        break
            if pod is None:
                pod = pod_list.items[0]
            status = pod.status
            node_name = pod.spec.node_name
            return {
                "running": status.phase == "Running",
                "terminating": pod.metadata.deletion_timestamp is not None,
                "phase": status.phase,
                "reason": getattr(status, "reason", None),
                "pod_name": pod.metadata.name,
                "pod_ip": getattr(status, "pod_ip", None),
                "start_time": str(status.start_time) if status.start_time else None,
                "node_name": node_name,
            }
        except Exception as e:
            logger.error(f"Failed to get pod status for {agent_id}: {e}")
            return {"running": False, "phase": "Error", "reason": str(e)}

    async def get_pod_status(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ) -> dict:
        """返回 Pod 状态信息（经 to_thread 不阻塞 event loop）"""
        return await asyncio.to_thread(
            self._get_pod_status_sync, agent_id, scope_type, scope_target_id
        )

    async def get_pods_for_instance(self, engine_instance_id: str, db) -> list[dict]:
        """Return detailed pod info for all deployments under an engine instance.

        Queries AgentDeployment records by engine_instance_id, then fetches
        real-time K8s pod data for each.
        """
        from sqlalchemy import select

        from pkg.common.models import AgentDeployment, DeploymentStatus

        result = await db.execute(
            select(AgentDeployment).where(
                AgentDeployment.resource_pool_id == engine_instance_id,
                AgentDeployment.status == DeploymentStatus.RUNNING,
            )
        )
        deployments = list(result.scalars().all())

        pods = []
        for dep in deployments:
            agent_id = str(dep.instance_id)
            name = _engine_name(agent_id, dep.scope_type, dep.scope_target_id)
            try:
                pod_list = self.core_v1.list_namespaced_pod(
                    namespace=self.namespace,
                    label_selector=f"app={name}",
                )
                if not pod_list.items:
                    pods.append(
                        {
                            "name": dep.pod_name or f"{name}-unknown",
                            "status": dep.status.value,
                            "node": dep.node_name or "",
                            "cpu": "",
                            "memory": "",
                            "restarts": 0,
                            "age": "",
                            "agent_id": agent_id,
                            "deployment_id": str(dep.id),
                        }
                    )
                    continue

                pod = pod_list.items[0]
                container = pod.spec.containers[0] if pod.spec.containers else None
                cs = pod.status.container_statuses[0] if pod.status.container_statuses else None

                req_cpu = ""
                req_mem = ""
                if container and container.resources and container.resources.requests:
                    req_cpu = str(container.resources.requests.get("cpu", ""))
                    req_mem = str(container.resources.requests.get("memory", ""))

                age = ""
                if pod.metadata.creation_timestamp:
                    delta = datetime.now(UTC) - pod.metadata.creation_timestamp
                    days = delta.days
                    hours = delta.seconds // 3600
                    if days > 0:
                        age = f"{days}d"
                    elif hours > 0:
                        age = f"{hours}h"
                    else:
                        age = f"{delta.seconds // 60}m"

                pods.append(
                    {
                        "name": pod.metadata.name,
                        "status": pod.status.phase,
                        "node": pod.spec.node_name or "",
                        "cpu": req_cpu,
                        "memory": req_mem,
                        "restarts": cs.restart_count if cs else 0,
                        "age": age,
                        "agent_id": agent_id,
                        "deployment_id": str(dep.id),
                    }
                )
            except Exception as e:
                logger.error(f"Failed to get pod for agent {agent_id}: {e}")
                pods.append(
                    {
                        "name": dep.pod_name or name,
                        "status": "Unknown",
                        "node": dep.node_name or "",
                        "cpu": "",
                        "memory": "",
                        "restarts": 0,
                        "age": "",
                        "agent_id": agent_id,
                        "deployment_id": str(dep.id),
                    }
                )

        return pods

    async def get_pod_metrics(self, pod_names: list[str]) -> dict[str, dict[str, str]]:
        """查询 metrics-server，返回 {pod_name: {cpu, memory}}（当前瞬时用量）。

        metrics-server 未部署时抛 ApiException（404/分页为空），调用方应捕获并降级。
        仅返回 pod_names 中的 pod。cpu/memory 为原始字符串（如 "120m"、"256Mi"）。
        """
        result: dict[str, dict[str, str]] = {name: {"cpu": "", "memory": ""} for name in pod_names}
        try:
            resp = self.custom_v1.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self.namespace,
                plural="pods",
            )
        except kubernetes.client.exceptions.ApiException as e:
            logger.warning(f"metrics-server query failed: {e}")
            raise
        for item in resp.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            if name not in result:
                continue
            containers = item.get("containers", [])
            # 聚合所有容器的用量
            total_cpu = 0  # millicores
            total_mem = 0  # bytes
            for c in containers:
                usage = c.get("usage", {})
                cpu = usage.get("cpu", "0")
                mem = usage.get("memory", "0")
                total_cpu += _parse_cpu(cpu)
                total_mem += _parse_mem(mem)
            result[name] = {
                "cpu": f"{total_cpu}m" if total_cpu else "",
                "memory": _format_mem(total_mem),
            }
        return result

    async def get_pod_logs(self, pod_name: str, tail_lines: int = 200) -> str:
        """Return the last N lines of logs from a specified K8s Pod.

        用 _preload_content=False 取原始 bytes 再解码：默认预加载会把 bytes 经 str()
        转成 "b'...'" 字面量，导致前端看到 b" 前缀转义串而非真实日志。
        """
        try:
            resp = self.core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=self.namespace,
                tail_lines=tail_lines,
                _preload_content=False,
            )
            data = resp.data if hasattr(resp, "data") else resp.read()
            if isinstance(data, bytes):
                return data.decode(errors="replace")
            return data or ""
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                raise ValueError(f"Pod {pod_name} not found")
            if e.status == 400:
                return ""
            raise

    # Profile 名白名单：Hermes profile 名由 agent_short-scope_hash-user_short 构成，
    # 仅允许字母数字下划线短横线；拒绝任何 shell 元字符防注入。
    _PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

    async def list_profile_log_files(self, pod_name: str) -> list[str]:
        """列出 Pod 内 /tmp/gateway-*.log 对应的 profile 名（含 base）。

        每个 profile 网关日志写到 /tmp/gateway-{profile}.log，read_namespaced_pod_log
        只能拿容器 stdout（nginx/entrypoint），拿不到这些网关日志，故用 exec 列目录。
        """
        command = ["/bin/sh", "-c", "ls -1 /tmp/gateway-*.log 2>/dev/null"]
        stdout, _rc, _err = await asyncio.to_thread(
            self._ws_exec_sync, pod_name, command, stdin_data=None, binary=False, timeout=30
        )
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        profiles: list[str] = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("/tmp/gateway-") or not line.endswith(".log"):
                continue
            name = line[len("/tmp/gateway-") : -len(".log")]
            if self._PROFILE_NAME_RE.match(name):
                profiles.append(name)
        return profiles

    async def get_profile_gateway_logs(
        self, pod_name: str, profile_name: str, tail_lines: int = 200
    ) -> str:
        """返回某 profile 网关日志的最后 N 行（exec tail /tmp/gateway-{profile}.log）。"""
        if not self._PROFILE_NAME_RE.match(profile_name):
            raise ValueError(f"invalid profile name: {profile_name}")
        command = [
            "tail",
            "-n",
            str(int(tail_lines)),
            f"/tmp/gateway-{profile_name}.log",
        ]
        stdout, _rc, _err = await asyncio.to_thread(
            self._ws_exec_sync, pod_name, command, stdin_data=None, binary=False, timeout=30
        )
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        return stdout or ""

    async def delete_pod(self, pod_name: str) -> None:
        """Delete a Pod by name. Deployment controller will recreate it."""
        try:
            self.core_v1.delete_namespaced_pod(pod_name, self.namespace)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                raise ValueError(f"Pod {pod_name} not found")
            raise

    async def wait_pod_ready(
        self,
        agent_id: str,
        timeout: int = 120,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ) -> bool:
        """等待 Pod 进入 Running 状态"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        for _ in range(timeout):
            status = await self.get_pod_status(agent_id)
            if status["running"]:
                return True
            await asyncio.sleep(1)
        logger.warning(f"Timeout waiting for pod {name} to be ready")
        return False

    async def wait_engine_ready(
        self,
        agent_id: str,
        timeout: int = 300,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ) -> bool:
        """等待引擎就绪 — 通过 K8s Pod Ready 条件检测（由 readinessProbe 驱动）。"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        label_selector = f"app={name}"
        last_log = 0
        for i in range(timeout):
            try:
                pod_list = await asyncio.to_thread(
                    self.core_v1.list_namespaced_pod,
                    self.namespace,
                    label_selector=label_selector,
                )
                if pod_list.items:
                    # 遍历找任一 Ready pod：stale Error/Terminating pod（前次 rollout
                    # 残留）可能排在 items[0]，只看首个会误判超时。优先 Running+Ready。
                    for pod in pod_list.items:
                        if pod.status.phase != "Running":
                            continue
                        for condition in pod.status.conditions or []:
                            if condition.type == "Ready" and condition.status == "True":
                                logger.info(
                                    "Engine %s ready after ~%ds (K8s Ready condition)",
                                    agent_id[:8],
                                    i,
                                )
                                return True
            except Exception as e:
                logger.warning("K8s API error waiting for engine %s: %s", agent_id[:8], e)

            if i - last_log >= 30:
                logger.info("Waiting for engine %s... (%ds)", agent_id[:8], i)
                last_log = i
            await asyncio.sleep(1)

        logger.warning("Timeout waiting for engine %s to be ready", agent_id[:8])
        return False

    async def get_service_url(
        self,
        agent_id: str,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
        engine_type: str | None = None,
    ) -> str:
        """返回 Pod 的 K8s Service DNS 地址（用于 deploy 完成后的信息展示）"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        port = _engine_port(engine_type)
        return f"http://{name}.{self.namespace}.svc.cluster.local:{port}"

    # ── 生命周期管理 ──────────────────────────────────────

    async def scale_to_zero(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ):
        """休眠：replicas=0，释放 Pod 资源（数据已备份到 OSS）"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        try:
            self.apps_v1.patch_namespaced_deployment_scale(
                name=name,
                namespace=self.namespace,
                body={"spec": {"replicas": 0}},
            )
            logger.info(f"Suspended Deployment {name} (scale=0)")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    async def resume(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ) -> bool:
        """恢复：replicas=1，返回 True 表示恢复成功，False 表示 Deployment 不存在"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        try:
            self.apps_v1.patch_namespaced_deployment_scale(
                name=name,
                namespace=self.namespace,
                body={"spec": {"replicas": 1}},
            )
            logger.info(f"Resumed Deployment {name} (scale=1)")
            return True
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                logger.warning(f"Deployment {name} not found, will create new one")
                return False
            raise

    async def delete_agent_engine(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ):
        """删除 Deployment + Service + PVC"""
        name = _engine_name(agent_id, scope_type, scope_target_id)

        # Delete Deployment
        try:
            self.apps_v1.delete_namespaced_deployment(name, self.namespace)
            logger.info(f"Deleted Deployment {name}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        # Delete Service
        try:
            self.core_v1.delete_namespaced_service(name, self.namespace)
            logger.info(f"Deleted Service {name}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        # Delete PVC（受 pvc_reclaim_on_destroy 控制，关闭时可保留现场调试）
        from pkg.common.config import settings as _settings

        if _settings.pvc_reclaim_on_destroy:
            pvc_name = _pvc_name(agent_id, scope_type, scope_target_id)
            self.delete_pvc(pvc_name)

    # ── 浏览器沙箱 Pod 生命周期（per-profile，独立 Pod）──────────────────
    #
    # browser Pod = kasmweb/chrome 容器（VNC 6901 + chrome CDP loopback 9223）
    #             + cdp-proxy sidecar（python3 TCP relay 0.0.0.0:9222 → 127.0.0.1:9223）
    # chrome 强制绑 127.0.0.1（忽略 --remote-debugging-address，P0 实测），故需 sidecar 代理
    # 把 CDP 暴露到 Pod 网络，引擎经 Service DNS http://browser-{short}-{ph}.{ns}.svc:9222 访问。
    # VNC_PW 每 Pod 随机入 Secret；NetworkPolicy 限 9222 仅 engine Pod、6901 仅 gateway Pod ingress。

    def _build_browser_pvc(self, pvc_name: str, labels: dict) -> V1PersistentVolumeClaim:
        from pkg.common.config import settings

        return V1PersistentVolumeClaim(
            metadata=V1ObjectMeta(name=pvc_name, labels=labels),
            spec=V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=V1ResourceRequirements(
                    requests={"storage": settings.browser_pvc_size},
                ),
                storage_class_name=settings.browser_pvc_storage_class,
            ),
        )

    def _create_browser_vnc_secret(self, secret_name: str, labels: dict) -> str:
        """确保 VNC_PW Secret 存在，返回明文密码（总返回，供 gateway 经 DB 取用做 Basic auth）。

        已存在则读出原密码（不覆盖，重建 Pod 复用）；不存在则随机生成并建 Secret。
        """
        import base64
        import secrets as _secrets

        try:
            existing = self.core_v1.read_namespaced_secret(secret_name, self.namespace)
            # 已存在：读出原密码返回（保留原密码，重建 Pod 复用）
            data = getattr(existing, "data", None) or {}
            pw_b64 = data.get("VNC_PW", "") if isinstance(data, dict) else ""
            if pw_b64:
                try:
                    return base64.b64decode(pw_b64).decode()
                except Exception:
                    pass  # 解码失败 → 落到下面重建
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise
        password = _secrets.token_urlsafe(18)
        try:
            self.core_v1.create_namespaced_secret(
                self.namespace,
                V1Secret(
                    metadata=V1ObjectMeta(name=secret_name, labels=labels),
                    string_data={"VNC_PW": password},
                ),
            )
            logger.info("Created VNC Secret %s", secret_name)
            return password
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 409:
                raise
            # 409：Secret 已存在但上面读出的 VNC_PW 为空/损坏（否则已在 read 路径返回）。
            # 用新密码 patch 自愈——此处是建新 Pod（_create_browser_pod_sync），新 Pod 启动时
            # 从 Secret 读新密码，gateway 也拿新密码，一致。不 patch 会让损坏的 Secret 永远
            # 卡住 browser Pod 创建（ensure 静默返回 None，无清理无报错）。
            self.core_v1.patch_namespaced_secret(
                secret_name,
                self.namespace,
                body={"stringData": {"VNC_PW": password}},
            )
            logger.warning("Healed corrupt VNC Secret %s (VNC_PW was empty/invalid)", secret_name)
            return password

    def _create_browser_pod_sync(
        self,
        agent_id: str,
        profile_name: str,
        group_code: str | None = None,
    ) -> dict:
        """create_browser_pod 同步实现：建 browser Deployment+Service+PVC+VNC Secret+NetworkPolicy。

        返回 {"name": pod/service 名, "vnc_pw": VNC 明文密码}。
        name 即 browser-{short}-{ph}，cdp_url = http://<name>.{ns}.svc:9222。
        vnc_pw 供 manager 写入 internal_port_map["browsers"][name]，gateway 经 DB 取用做上游
        Basic auth（gateway 架构约束不许调 k8s API，故密码经 DB 传递）。
        """
        from pkg.common.config import settings

        name = _browser_name(agent_id, profile_name)
        short_id = _agent_short_id(agent_id)
        ph = _profile_hash(profile_name)
        cdp_proxy_port = settings.browser_cdp_proxy_port
        chrome_cdp_port = settings.browser_cdp_chrome_port
        vnc_port = settings.browser_vnc_port

        pod_labels = {
            "app": name,
            "agent-id": agent_id,
            "profile-hash": ph,
            "unionagents.io/component": "browser",
        }
        resource_labels = {
            "app": "browser",
            "agent.unionagents/agent-id": agent_id,
            "agent.unionagents/profile-hash": ph,
        }
        if group_code:
            pod_labels["group.unionagents/group-code"] = str(group_code)
            resource_labels["group.unionagents/group-code"] = str(group_code)

        # ── VNC_PW Secret（每 Pod 随机，总返回明文供 gateway DB 取用）──
        vnc_secret_name = _browser_vnc_secret_name(agent_id, profile_name)
        vnc_pw = self._create_browser_vnc_secret(vnc_secret_name, resource_labels)

        # ── PVC: kasm /config 持久化 cookies/登录态 ──
        pvc_name = _browser_pvc_name(agent_id, profile_name)
        try:
            self.core_v1.create_namespaced_persistent_volume_claim(
                self.namespace, self._build_browser_pvc(pvc_name, resource_labels)
            )
            logger.info("Created browser PVC %s", pvc_name)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 409:
                raise

        # ── chrome 容器：kasm 默认 entrypoint（VNC 桌面 + custom_startup 拉 chrome）──
        # APP_ARGS 注 CDP flag：chrome 起 9223 loopback（--remote-debugging-address 被 chrome
        # 忽略，故不设，由 sidecar 代理暴露）；user-data-dir=/config/browser-profile 落 PVC。
        chrome_env = [
            V1EnvVar(
                name="APP_ARGS",
                value=(
                    f"--remote-debugging-port={chrome_cdp_port} "
                    f"--user-data-dir=/config/browser-profile "
                    f"--no-first-run --disable-default-apps"
                ),
            ),
            V1EnvVar(
                name="VNC_PW",
                value_from=V1EnvVarSource(
                    secret_key_ref=V1SecretKeySelector(name=vnc_secret_name, key="VNC_PW")
                ),
            ),
            V1EnvVar(name="LAUNCH_URL", value="about:blank"),
            V1EnvVar(name="VNC_RESOLUTION", value="1280x720"),
            # 禁 Kasm 终端/sudo 提权面（CLAUDE.md 安全约束）
            V1EnvVar(name="KASM_RESTRICTED_FILE_CHOOSER", value="true"),
            # RFB 层 NoAuth：kasm 的 -rfbauth ~/.vnc/passwd 是镜像内置的空密码默认文件
            # （vnc_startup.sh 只写 ~/.kasmpasswd，从不重生成 ~/.vnc/passwd），noVNC 标准的
            # VncAuth 送 VNC_PW 必被拒。设 SecurityTypes=None 关掉 RFB 层认证，noVNC 走 NoAuth。
            # WS 升级层的 Basic auth（~/.kasmpasswd = kasm_user:VNC_PW）仍由 kasm 强制（实测
            # 无/错 Basic auth → 401），叠加 gateway JWT，安全性不降。vnc_startup.sh 只往
            # VNCOPTIONS 追加（-select-de manual 等），不重置，故 env 值被保留透传给 Xvnc。
            V1EnvVar(name="VNCOPTIONS", value="-SecurityTypes None -DisconnectClients 1"),
        ]

        pull_policy = os.getenv("UA_ENGINE_IMAGE_PULL_POLICY", "IfNotPresent")
        pull_secrets_name = os.getenv("UA_ENGINE_IMAGE_PULL_SECRETS", "")

        chrome_container = V1Container(
            name="chrome",
            image=settings.browser_sidecar_image,
            image_pull_policy=pull_policy,
            ports=[
                V1ContainerPort(container_port=vnc_port, name="vnc"),
                V1ContainerPort(container_port=chrome_cdp_port, name="cdp"),
            ],
            env=chrome_env,
            volume_mounts=[
                V1VolumeMount(name="browser-data", mount_path="/config"),
                # chrome 需较大 /dev/shm，否则复杂页面 Aw,Snap 崩
                V1VolumeMount(name="dshm", mount_path="/dev/shm"),
            ],
            resources=V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "512Mi"},
                limits={"cpu": "2", "memory": "2Gi"},
            ),
            # VNC web 就绪探针（kasm 6901 HTTPS 自签，用 TCP 探针即可）
            readiness_probe=V1Probe(
                tcp_socket=V1TCPSocketAction(port=vnc_port),
                initial_delay_seconds=10,
                period_seconds=5,
                failure_threshold=60,
            ),
        )

        # ── cdp-proxy sidecar：CDP 感知代理（browser-v2 内置 cdp_proxy.py）──
        # 同 Pod 共享网络命名空间，sidecar 的 127.0.0.1 即 chrome 的 loopback。
        # chrome DevTools 强制绑 127.0.0.1 + Host 头 DNS-rebinding 保护 + loopback webSocketDebuggerUrl，
        # 裸 TCP relay（socat/python）跨 Pod 不可用；cdp_proxy.py 重写 Host→localhost + 响应
        # webSocketDebuggerUrl→外部地址 + WS 隧道，使 hermes 经 browser-svc:9222 跨 Pod 驱动 chrome。
        proxy_container = V1Container(
            name="cdp-proxy",
            image=settings.browser_sidecar_image,
            image_pull_policy=pull_policy,
            command=["python3", "/opt/cdp_proxy.py"],
            ports=[V1ContainerPort(container_port=cdp_proxy_port, name="cdp-proxy")],
            resources=V1ResourceRequirements(
                requests={"cpu": "50m", "memory": "64Mi"},
                limits={"cpu": "200m", "memory": "256Mi"},
            ),
        )

        volumes = [
            V1Volume(
                name="browser-data",
                persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(claim_name=pvc_name),
            ),
            V1Volume(name="dshm", empty_dir=V1EmptyDirVolumeSource(medium="Memory")),
        ]

        # initContainer：清 chrome profile SingletonLock（PVC 跨 pod 重建留 stale 锁，
        # chrome 见锁拒绝启动 "profile in use by another process"）。chrome 启动前清掉。
        lock_init = V1Container(
            name="clean-profile-lock",
            image=settings.browser_sidecar_image,
            image_pull_policy=pull_policy,
            command=["sh", "-c", "rm -f /config/browser-profile/SingletonLock "
                    "/config/browser-profile/SingletonCookie /config/browser-profile/SingletonSocket || true"],
            volume_mounts=[V1VolumeMount(name="browser-data", mount_path="/config")],
        )

        deployment = V1Deployment(
            metadata=V1ObjectMeta(name=name, labels=resource_labels),
            spec=V1DeploymentSpec(
                replicas=1,
                selector=V1LabelSelector(match_labels=pod_labels),
                template=V1PodTemplateSpec(
                    metadata=V1ObjectMeta(labels=pod_labels),
                    spec=V1PodSpec(
                        init_containers=[lock_init],
                        containers=[chrome_container, proxy_container],
                        image_pull_secrets=[V1LocalObjectReference(name=pull_secrets_name)]
                        if pull_secrets_name
                        else None,
                        volumes=volumes,
                    ),
                ),
            ),
        )

        # Service：暴露 CDP 9222（引擎访问）+ VNC 6901（gateway 桥接）
        service = V1Service(
            metadata=V1ObjectMeta(name=name, labels=resource_labels),
            spec=V1ServiceSpec(
                ports=[
                    V1ServicePort(port=cdp_proxy_port, target_port=cdp_proxy_port, name="cdp"),
                    V1ServicePort(port=vnc_port, target_port=vnc_port, name="vnc"),
                ],
                selector=pod_labels,
            ),
        )

        try:
            self.apps_v1.create_namespaced_deployment(self.namespace, deployment)
            logger.info("Created browser Deployment %s", name)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 409:
                # 已存在：仅 scale up(replicas=1)，绝不动 spec.selector / template.labels。
                # selector immutable（改了 → 422）；且老 Deployment 的 selector↔template↔Service
                # 标签自洽，patch 任一会破坏三者匹配致 Pod 失联。create_browser_pod 幂等复用即可
                # （selector 漂移靠重建 Pod 解决，不在此 reconcile）。vnc_pw 由上方 secret 幂等复用。
                self.apps_v1.patch_namespaced_deployment(
                    name=name, namespace=self.namespace, body={"spec": {"replicas": 1}}
                )
                logger.info("Reused existing browser Deployment %s (scaled to 1)", name)
            else:
                raise

        try:
            self.core_v1.create_namespaced_service(self.namespace, service)
            logger.info("Created browser Service %s", name)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 409:
                raise

        self._ensure_browser_network_policy(agent_id, profile_name, pod_labels)

        return {"name": name, "vnc_pw": vnc_pw}

    def _ensure_browser_network_policy(
        self, agent_id: str, profile_name: str, pod_labels: dict
    ) -> None:
        """NetworkPolicy：browser Pod ingress 9222 仅 engine Pod、6901 仅 gateway Pod。

        注：需 CNI 支持（Calico/Cilium）；flannel 不强制（策略建了但 noop）。
        无 CNI 强制时，CDP 9222 在 namespace 内可达——靠命名不外泄 + 后续 CNI 加固。
        """
        from pkg.common.config import settings

        name = _browser_network_policy_name(agent_id, profile_name)
        cdp_port = settings.browser_cdp_proxy_port
        vnc_port = settings.browser_vnc_port
        policy = V1NetworkPolicy(
            metadata=V1ObjectMeta(name=name, labels={"app": "browser", "agent.unionagents/agent-id": agent_id}),
            spec=V1NetworkPolicySpec(
                pod_selector=V1LabelSelector(
                    match_labels={"unionagents.io/component": "browser", "agent-id": agent_id,
                                  "profile-hash": pod_labels["profile-hash"]}
                ),
                policy_types=["Ingress"],
                ingress=[
                    # CDP 9222：仅引擎 Pod
                    V1NetworkPolicyIngressRule(
                        _from=[V1NetworkPolicyPeer(
                            pod_selector=V1LabelSelector(
                                match_labels={"unionagents.io/component": "engine"}
                            )
                        )],
                        ports=[V1NetworkPolicyPort(port=cdp_port)],
                    ),
                    # VNC 6901：仅 gateway Pod
                    V1NetworkPolicyIngressRule(
                        _from=[V1NetworkPolicyPeer(
                            pod_selector=V1LabelSelector(match_labels={"app": "gateway"})
                        )],
                        ports=[V1NetworkPolicyPort(port=vnc_port)],
                    ),
                ],
            ),
        )
        try:
            self.net_v1.create_namespaced_network_policy(self.namespace, policy)
            logger.info("Created browser NetworkPolicy %s", name)
        except kubernetes.client.exceptions.ApiException as e:
            # best-effort：NetworkPolicy 是 defense-in-depth，CNI 不支持 / CRD 缺失 / 字段拒绝
            # 均不阻断 browser Pod 创建（CDP 隔离靠 ClusterIP-only Service + 命名不外泄兜底）
            logger.warning("browser NetworkPolicy %s skipped (status=%s): %s", name, e.status, str(e)[:120])

    async def create_browser_pod(
        self, agent_id: str, profile_name: str, group_code: str | None = None
    ) -> dict:
        """为 profile 创建浏览器 Pod（kasmweb/chrome + cdp-proxy sidecar）+ Service + PVC + NetworkPolicy。

        返回 {"name": Pod/Service 名, "vnc_pw": VNC 明文密码}。
        cdp_url 即 http://<name>.{ns}.svc:9222；vnc_pw 经 DB internal_port_map 给 gateway 做上游 Basic auth。
        """
        return await asyncio.to_thread(
            self._create_browser_pod_sync, agent_id, profile_name, group_code
        )

    async def scale_browser_to_zero(self, agent_id: str, profile_name: str):
        """休眠 browser Pod（replicas=0，保留 PVC 登录态）。"""
        name = _browser_name(agent_id, profile_name)
        try:
            self.apps_v1.patch_namespaced_deployment_scale(
                name=name, namespace=self.namespace, body={"spec": {"replicas": 0}}
            )
            logger.info("Suspended browser Deployment %s (scale=0)", name)
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    async def resume_browser_pod(self, agent_id: str, profile_name: str) -> bool:
        """恢复 browser Pod（replicas=1）。返回 False 表示 Deployment 不存在需重建。"""
        name = _browser_name(agent_id, profile_name)
        try:
            self.apps_v1.patch_namespaced_deployment_scale(
                name=name, namespace=self.namespace, body={"spec": {"replicas": 1}}
            )
            logger.info("Resumed browser Deployment %s (scale=1)", name)
            return True
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                return False
            raise

    async def delete_browser_pod(self, agent_id: str, profile_name: str):
        """删除 browser Pod + Service + PVC + VNC Secret + NetworkPolicy。"""
        name = _browser_name(agent_id, profile_name)
        # best-effort 全试：每个资源独立 try，非 404 只 warn 不 raise——此前第一个非 404
        # 错误（如 Deployment 删除瞬时 500/超时）会 raise 中止循环，剩余 4 个资源（PVC/
        # Secret/NetPol）永不删除，且 DESTROY 时 DB 行已删无人重试 → 永久泄漏。
        for deleter in (
            lambda: self.apps_v1.delete_namespaced_deployment(name, self.namespace),
            lambda: self.core_v1.delete_namespaced_service(name, self.namespace),
            lambda: self.core_v1.delete_namespaced_persistent_volume_claim(
                _browser_pvc_name(agent_id, profile_name), self.namespace
            ),
            lambda: self.core_v1.delete_namespaced_secret(
                _browser_vnc_secret_name(agent_id, profile_name), self.namespace
            ),
            lambda: self.net_v1.delete_namespaced_network_policy(
                _browser_network_policy_name(agent_id, profile_name), self.namespace
            ),
        ):
            try:
                deleter()
            except kubernetes.client.exceptions.ApiException as e:
                if e.status != 404:
                    logger.warning(
                        "delete browser resource %s/%s failed (status=%s): %s",
                        agent_id[:8], profile_name[:16], e.status, str(e)[:120],
                    )
        logger.info("Deleted browser resources for %s/%s", agent_id[:8], profile_name[:16])

    # ── Pod exec 低层 WebSocket 封装 ─────────────────────

    def _ws_exec_sync(
        self,
        pod_name: str,
        command: list[str],
        stdin_data: bytes | None = None,
        binary: bool = False,
        timeout: int = 300,
        container: str = "engine",
    ) -> tuple[bytes, int, str]:
        """同步 WebSocket exec：连接 Pod，执行命令，返回 (stdout, returncode, stderr)

        container 必须显式指定：引擎 Pod 现含多个容器（engine + skill-secret-sidecar 等），
        不带 container 参数 k8s 会返回 400 "a container name must be specified"，
        导致 finalizer 销毁备份 exec 失败、Pod 卡 Terminating。默认 engine 容器。
        """
        from urllib.parse import urlencode

        from kubernetes.stream.ws_client import WSClient, get_websocket_url

        config = self.core_v1.api_client.configuration

        # 构建 exec URL
        path = f"/api/v1/namespaces/{self.namespace}/pods/{pod_name}/exec"
        params = [("container", container)]
        for c in command:
            params.append(("command", c))
        params.append(("container", container))
        params.append(("stdout", "1"))
        params.append(("stderr", "1"))
        params.append(("stdin", "1" if stdin_data is not None else "0"))
        params.append(("tty", "0"))

        http_url = config.host + path + "?" + urlencode(params)
        ws_url = get_websocket_url(http_url)

        headers = {}
        api_key = getattr(config, "api_key", None) or {}
        api_key_prefix = getattr(config, "api_key_prefix", None) or {}
        # k8s client 根据不同认证方式使用不同 key: BearerToken / authorization / etc.
        auth_token = None
        for key in ("authorization", "BearerToken", "bearertoken"):
            if key in api_key:
                auth_token = str(api_key[key])
                prefix = api_key_prefix.get(key, "")
                if prefix and not auth_token.startswith(prefix):
                    auth_token = f"{prefix} {auth_token}"
                break
        if auth_token:
            headers["authorization"] = auth_token

        ws = WSClient(config, ws_url, headers, capture_all=True, binary=binary)

        # 写入 stdin 数据并关闭 stdin channel
        if stdin_data is not None:
            ws.write_stdin(stdin_data)
            ws.close_channel(0)  # close stdin channel to signal EOF

        # 等待命令执行完成
        ws.run_forever(timeout=timeout)

        # 读取 stdout + stderr
        stdout = ws.read_all()
        stderr = ws.read_stderr() or b"" if binary else ws.read_stderr() or ""

        # 安全获取 returncode — ERROR_CHANNEL 可能为空（命令成功时）
        try:
            rc = ws.returncode
        except Exception:
            rc = 0  # WebSocket 正常关闭即为成功
        if rc is None:
            rc = 0
        ws.close()

        return stdout, rc, stderr

    # ── 数据备份（exec tar → MinIO）─────────────────────────

    async def exec_tar_data(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ) -> bytes:
        """exec 进 Pod，tar 数据目录，返回 tar 流

        使用 WSClient 直连 WebSocket（binary=True），避免 k8s_stream 的
        websocket NoneType decode bug。
        """
        status = await self.get_pod_status(agent_id, scope_type, scope_target_id)
        if not status.get("pod_name"):
            raise RuntimeError(f"Pod not found for agent {agent_id}")
        return await self.exec_tar_data_by_pod(status["pod_name"])

    async def exec_tar_data_by_pod(self, pod_name: str, agent_id_tag: str = "") -> bytes:
        """exec 进指定 Pod，tar /opt/data 返回 tar 流（供 finalizer 销毁备份复用）。

        V2 模式下 Profile 数据位于 opt/data/profiles/{name}/，包含 state.db、sessions、memories。
        K8s WebSocket binary 模式不分离 stdout/stderr 通道，stderr 的 tar 警告会与 tar.gz
        二进制混流，故先写 Pod 内临时文件再 cat 读取。

        总超时 300s（tar 120 + cat 120 + rm 10 最坏 250s，留余量），防止 exec 卡死占连接。
        """
        return await asyncio.wait_for(
            self._exec_tar_data_by_pod_impl(pod_name, agent_id_tag),
            timeout=_EXEC_TAR_TOTAL_TIMEOUT,
        )

    async def _exec_tar_data_by_pod_impl(self, pod_name: str, agent_id_tag: str = "") -> bytes:
        """exec_tar_data_by_pod 实现（无总超时，由调用方 asyncio.wait_for 兜底）"""
        _tag = agent_id_tag or pod_name[:12]
        # V2 模式下 Profile 数据位于 opt/data/profiles/{name}/，包含 state.db、sessions、memories
        _tar_file = f"/tmp/backup-{_tag[:8]}.tar.gz"
        _tar_cmd = [
            "/bin/sh",
            "-c",
            # 注意：$? 不要转义（rf-string 不解释 $，shell 展开 $?）；误写 \$? 会导致
            # echo 输出字面 "EXIT=$?"，退出码校验恒失败（tar backup may have failed 误告警）。
            rf"tar czf {_tar_file} -C / opt/data 2>/dev/null; echo EXIT=$?",
        ]
        _read_cmd = ["cat", _tar_file]
        _clean_cmd = ["rm", "-f", _tar_file]

        # 执行 tar（stderr 被 shell 重定向，不影响文件内容）
        _out, rc, _err = await asyncio.to_thread(
            self._ws_exec_sync,
            pod_name,
            _tar_cmd,
            stdin_data=None,
            binary=False,
            timeout=120,
        )
        _exit_str = _out.decode(errors="replace") if isinstance(_out, bytes) else str(_out)
        # tar 退出码语义：0=成功；1=非致命警告（常见「file changed as we read it」，引擎在线
        # 写 /opt/data 时会发生，归档仍有效）；2=致命错误（如目录不存在）。exec 异常则无 EXIT 标记。
        _tar_exit = None
        for tok in _exit_str.split():
            if tok.startswith("EXIT="):
                try:
                    _tar_exit = int(tok[5:])
                except ValueError:
                    pass
        if _tar_exit is None:
            raise RuntimeError(
                f"tar backup failed for {_tag[:8]}: no EXIT marker ({_exit_str[:80] or 'no output'})"
            )
        if _tar_exit == 2:
            raise RuntimeError(f"tar backup fatal for {_tag[:8]}: exit=2 ({_exit_str[:80]})")
        if _tar_exit == 1:
            logger.warning("tar backup warnings for %s (exit=1, archive still valid)", _tag[:8])

        # 读取临时文件
        stdout, rc, stderr = await asyncio.to_thread(
            self._ws_exec_sync,
            pod_name,
            _read_cmd,
            stdin_data=None,
            binary=True,
            timeout=120,
        )

        # 清理临时文件
        await asyncio.to_thread(
            self._ws_exec_sync,
            pod_name,
            _clean_cmd,
            stdin_data=None,
            binary=False,
            timeout=10,
        )

        if rc != 0:
            _err_str = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
            raise RuntimeError(f"tar read failed (rc={rc}): {_err_str[:200]}")
        return stdout

    async def exec_untar_data(
        self,
        agent_id: str,
        tar_data: bytes,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ):
        """将 tar 数据通过 exec 解压到 Pod 的数据目录

        使用 WSClient 直连 WebSocket（binary=True），避免 k8s_stream 的
        websocket NoneType decode bug。
        """
        status = await self.get_pod_status(agent_id, scope_type, scope_target_id)
        if not status.get("pod_name"):
            raise RuntimeError(f"Pod not found for agent {agent_id}")

        await self.exec_untar_to_in_pod(status["pod_name"], "/", tar_data)

    async def exec_untar_to_in_pod(self, pod_name: str, dest_dir: str, tar_data: bytes):
        """将 tar 数据解压到指定 Pod 的 dest_dir。

        不走 WSClient stdin（write_stdin + close_channel EOF 实测挂死，见
        exec_write_file_in_pod 注释）：tar base64 编码后分块用 heredoc append 到 Pod
        内临时文件，再 ``base64 -d | tar xzf -`` 解压。heredoc 经命令字符串传递
        （``_ws_exec_sync(stdin_data=None)``），不触发 stdin 挂死；分块避免单条命令
        超 pod 内 ARG_MAX（~128KB）。

        --same-owner：root exec 下保留 tar 内属主（per-profile UID 目录跨备份/恢复
        保持 {uid}:{uid}，否则回退 root 破坏隔离）。
        """
        import base64
        import shlex
        import uuid

        b64 = base64.b64encode(tar_data).decode("ascii")
        tmp = shlex.quote(f"/tmp/.ua_untar_{uuid.uuid4().hex}.b64")
        dest = shlex.quote(dest_dir)
        try:
            # 32KB/块 → 单条 cmd ~33KB，远低于 pod ARG_MAX；heredoc 不走 stdin
            chunk_size = 32768
            await self.exec_command_in_pod(pod_name, [f"rm -f {tmp}"])
            for i in range(0, len(b64), chunk_size):
                chunk = b64[i : i + chunk_size]
                await self.exec_command_in_pod(
                    pod_name, [f"cat >> {tmp} <<'UA_EOF'\n{chunk}\nUA_EOF"]
                )
            stdout, rc, stderr = await asyncio.to_thread(
                self._ws_exec_sync,
                pod_name,
                ["/bin/bash", "-c", f"base64 -d {tmp} | tar xzf - --same-owner -C {dest}"],
                stdin_data=None,
                binary=False,
                timeout=300,
                container="engine",
            )
        finally:
            try:
                await self.exec_command_in_pod(pod_name, [f"rm -f {tmp}"])
            except Exception:  # noqa: BLE001
                pass
        if rc != 0:
            stderr_str = (
                stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
            )
            raise RuntimeError(f"untar failed (rc={rc}): {stderr_str[:200]}")

    async def exec_write_file(
        self,
        agent_id: str,
        path: str,
        content: str,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ):
        """通过 k8s exec 将内容写入 Pod 指定文件"""
        status = await self.get_pod_status(agent_id, scope_type, scope_target_id)
        if not status.get("pod_name"):
            raise RuntimeError(f"Pod not found for agent {agent_id}")
        await self.exec_write_file_in_pod(status["pod_name"], path, content)

    async def exec_write_file_in_pod(
        self, pod_name: str, path: str, content: str, mode: int | None = None
    ):
        """通过 k8s exec 将内容写入指定 Pod 的文件（按 pod_name 直连，支持跨 agent 共享 Pod）。

        用 heredoc 经 _ws_exec_sync（无 stdin）写入——WSClient 的 stdin 管道
        （write_stdin + close_channel EOF）对 python3 的 sys.stdin.read() 不生效，
        实测挂死 >35s（heal 写 config.yaml 超时 → gateway 启动读空 config → LiteLLM 400）。
        heredoc <<'UA_WEOF' 引用 marker 禁止变量展开，content 原样写入，无 stdin 走快速路径
        （与 exec_command_in_pod 同款，实测秒成）。mode 非 None 时写完追加 chmod {mode:o}
        （heredoc 结束符 UA_WEOF 必须独占一行，bash 不接受行首 &&，故 chmod 换行后单独执行）。
        """
        cmd = f"mkdir -p \"$(dirname {path})\" && cat > {path} <<'UA_WEOF'\n{content}\nUA_WEOF"
        if mode is not None:
            cmd += f"\nchmod {mode:o} {path}"
        try:
            stdout, rc, stderr = await asyncio.wait_for(
                asyncio.to_thread(
                    self._ws_exec_sync,
                    pod_name,
                    ["/bin/bash", "-c", cmd],
                    stdin_data=None,
                    binary=False,
                    timeout=30,
                ),
                timeout=35,
            )
        except TimeoutError:
            raise RuntimeError(f"write_file {path} in pod {pod_name} timed out (>35s)")

        if rc != 0:
            raise RuntimeError(f"write_file failed: {stderr}")
        logger.info(f"Wrote {path} in pod {pod_name}")

    async def patch_agent_envs(
        self,
        agent_id: str,
        env_overrides: dict,
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ):
        """Patch 运行中 Deployment 的环境变量（不触发重启）"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        try:
            # 获取当前 Deployment
            dep = self.apps_v1.read_namespaced_deployment(name, self.namespace)
            container = dep.spec.template.spec.containers[0]

            # 构建 env dict（保留现有 env，覆盖需要更新的 key）
            existing = {e.name: e for e in (container.env or [])}
            for key, val in env_overrides.items():
                existing[key] = V1EnvVar(name=key, value=str(val))

            # 更新 container env
            container.env = list(existing.values())

            # Apply
            self.apps_v1.patch_namespaced_deployment(
                name=name,
                namespace=self.namespace,
                body=dep,
            )
            logger.info(f"Patched env vars for {name}: {list(env_overrides.keys())}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                logger.warning(f"Deployment {name} not found, cannot patch env vars")
            else:
                raise

    async def rollout_restart(
        self, agent_id: str, scope_type: str = "ALL", scope_target_id: str | None = None
    ):
        """触发 Deployment 滚动重启使新配置生效"""
        name = _engine_name(agent_id, scope_type, scope_target_id)
        try:
            # 使用 kubectl patch 触发 rollout restart
            # 或修改 template annotations 触发滚动更新
            self.apps_v1.patch_namespaced_deployment(
                name=name,
                namespace=self.namespace,
                body={
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "kubectl.kubernetes.io/restartedAt": datetime.now(
                                        UTC
                                    ).isoformat()
                                }
                            }
                        }
                    }
                },
            )
            logger.info(f"Rollout restart triggered for {name}")
        except kubernetes.client.exceptions.ApiException as e:
            if e.status != 404:
                raise

    # ── 引擎镜像滚动发布（engine-rollout） ─────────────────

    def read_engine_image(self, name: str) -> str | None:
        """读取 Deployment engine 容器当前镜像，Deployment 不存在返回 None。"""
        try:
            dep = self.apps_v1.read_namespaced_deployment(
                name=name, namespace=self.namespace
            )
        except kubernetes.client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            raise
        for c in dep.spec.template.spec.containers:
            if c.name == "engine":
                return c.image
        return None

    def patch_engine_image(
        self, name: str, target_image: str, force_repull: bool = False
    ) -> str | None:
        """更新 engine 容器镜像，返回 patch 前旧镜像（Deployment 不存在返回 None）。

        strategic-merge patch spec.template.spec.containers[name=engine].image，
        K8s 因 image 变化自动触发滚动更新。force_repull=True 同时把 imagePullPolicy
        改 Always，解决「复用同一 tag + IfNotPresent 命中旧 digest」不重拉的问题。
        """
        old_image = self.read_engine_image(name)
        if old_image is None:
            return None
        container_patch: dict = {"name": "engine", "image": target_image}
        if force_repull:
            container_patch["imagePullPolicy"] = "Always"
        patch = {
            "spec": {"template": {"spec": {"containers": [container_patch]}}}
        }
        self.apps_v1.patch_namespaced_deployment(
            name=name, namespace=self.namespace, body=patch
        )
        logger.info(
            "Patched engine image %s: %s -> %s (force_repull=%s)",
            name, old_image, target_image, force_repull,
        )
        return old_image

    def wait_deployment_ready(
        self,
        name: str,
        target_image: str,
        timeout: int = 300,
        poll_interval: float = 2.0,
    ) -> bool:
        """[同步] 轮询直到 Deployment 滚动完成，超时返回 False。

        判据：desired_replicas>0 且 updated_replicas>=desired 且 available_replicas>=1
        且当前运行 pod 的 engine 容器 image 已是 target_image（防 IfNotPresent 假成功：
        旧 digest 仍在跑但 updated_replicas 已计数）。replicas=0（SUSPENDED）直接返回 True
        ——调用方对 SUSPENDED 实例只 patch 不等待。

        本方法为同步（kubernetes client 是同步阻塞的），调用方须用 asyncio.to_thread
        包裹以免阻塞事件循环。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                dep = self.apps_v1.read_namespaced_deployment(
                    name=name, namespace=self.namespace
                )
            except kubernetes.client.exceptions.ApiException as e:
                if e.status == 404:
                    return False
                raise
            desired = dep.spec.replicas or 1
            status = dep.status
            if desired == 0:
                return True
            updated = status.updated_replicas or 0
            available = status.available_replicas or 0
            if updated >= desired and available >= 1:
                # 二次确认 pod 实际跑的是 target_image（按 selector 列 pod）
                if self._pod_image_matches(dep, target_image):
                    return True
            time.sleep(poll_interval)
        return False

    def _pod_image_matches(self, dep, target_image: str) -> bool:
        """[同步] Deployment 下任一 ready pod 的 engine 容器 image 已是 target_image 则 True。

        按 dep.spec.selector.match_labels 构造 label_selector 列 pod。selector 可能含
        match_labels（当前 UA 部署只用 match_labels）。
        """
        sel = dep.spec.selector
        match_labels = getattr(sel, "match_labels", None) or {}
        if not match_labels:
            return True  # 无法判定时放行，依赖 updated_replicas 判据
        label_selector = ",".join(f"{k}={v}" for k, v in match_labels.items())
        try:
            pods = self.core_v1.list_namespaced_pod(
                self.namespace, label_selector=label_selector
            )
        except kubernetes.client.exceptions.ApiException:
            return True  # 列 pod 失败不阻断，退化到 updated_replicas 判据
        for pod in pods.items:
            if not _is_pod_ready(pod):
                continue
            for cs in pod.status.container_statuses or []:
                if cs.name == "engine" and cs.image == target_image:
                    return True
        return False

    # ── Hermes CLI 执行（Profile 管理） ────────────────────

    async def exec_hermes_command(
        self,
        agent_id: str,
        commands: list[str],
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ):
        """在 Pod 内执行 Hermes CLI 命令（复用 Hermes 原生能力）

        用于 Profile 生命周期管理: create, start, stop, delete
        使用 WSClient 直连 WebSocket，避免 k8s_stream 的 NoneType decode bug。
        文本命令不会触发 binary 数据相关的 WebSocket 问题。
        """
        pod_status = await self.get_pod_status(agent_id, scope_type, scope_target_id)
        pod_name = pod_status.get("pod_name")
        if not pod_name:
            raise RuntimeError(f"No pod found for agent {agent_id}")
        return await self.exec_command_in_pod(pod_name, commands)

    async def exec_command_in_pod(self, pod_name: str, commands: list[str]):
        """在指定 Pod 内执行 shell 命令（按 pod_name 直连，支持跨 agent 共享 Pod）"""
        last_stdout = ""
        for cmd in commands:
            logger.info("exec[%s]: %s", pod_name[:30], cmd[:120])
            # 硬超时兜底：_ws_exec_sync 的 timeout=60 对挂死的 websocket 不生效，
            # asyncio.wait_for 强制抛 TimeoutError，避免 handler 永久阻塞持锁。
            try:
                stdout, rc, stderr = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._ws_exec_sync,
                        pod_name,
                        ["/bin/bash", "-c", cmd],
                        stdin_data=None,
                        binary=False,
                        timeout=60,
                    ),
                    timeout=65,
                )
            except TimeoutError:
                logger.warning("exec[%s] timed out (>65s): %s", pod_name[:30], cmd[:80])
                stdout, rc, stderr = "", 124, "exec timeout"
            last_stdout = stdout or ""
            if rc != 0:
                logger.warning("exec[%s] failed (rc=%d): %s", pod_name[:30], rc, str(stderr)[:200])
        return last_stdout

    async def exec_read_file_bytes(
        self,
        pod_name: str,
        file_path: str,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> bytes:
        """从 Pod 中读取完整文件字节（二进制 WebSocket exec，避免 base64 JSON 经 stdout 缓冲）。

        先 exec 校验文件存在性与大小，再用 ``binary=True`` 直接 ``cat`` 读取原始字节。
        异常：
          - FileNotFoundError：文件不存在
          - ValueError：文件超过 max_bytes
          - RuntimeError：exec 失败或返回非预期输出
        """
        import shlex

        check_script = (
            "import sys,os\n"
            "p=sys.argv[1]\n"
            "m=int(sys.argv[2])\n"
            "if not os.path.isfile(p):\n"
            "    print('NOT_FOUND')\n"
            "    sys.exit(0)\n"
            "s=os.path.getsize(p)\n"
            "if s>m:\n"
            "    print(f'TOO_LARGE:{s}')\n"
            "    sys.exit(0)\n"
            "print(f'OK:{s}')"
        )
        check_cmd = (
            "python3 -c "
            + shlex.quote(check_script)
            + " "
            + shlex.quote(file_path)
            + " "
            + str(max_bytes)
        )
        stdout = await self.exec_command_in_pod(pod_name, [check_cmd])
        out = (stdout or "").strip()
        if out == "NOT_FOUND":
            raise FileNotFoundError(file_path)
        if out.startswith("TOO_LARGE:"):
            raise ValueError(f"file too large: {out.split(':', 1)[1]}")
        if not out.startswith("OK:"):
            raise RuntimeError(f"unexpected check output: {out[:200]}")

        stdout, rc, stderr = await asyncio.wait_for(
            asyncio.to_thread(
                self._ws_exec_sync,
                pod_name,
                ["cat", file_path],
                stdin_data=None,
                binary=True,
                timeout=60,
            ),
            timeout=65,
        )
        if rc != 0:
            err = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
            raise RuntimeError(f"read file failed: {err[:200]}")
        return stdout

    async def update_nginx_config(
        self,
        agent_id: str,
        profiles_map: dict[str, int],
        scope_type: str = "ALL",
        scope_target_id: str | None = None,
    ):
        """根据当前所有 profile 的端口映射，重新生成 nginx 配置并 reload。

        profiles_map: {profile_name: port, ...}
        """
        if not profiles_map:
            logger.info("update_nginx_config: %s no profiles, skipping", agent_id[:8])
            return

        # 动态生成 nginx 配置
        map_entries = []
        upstream_blocks = []
        for pname, port in sorted(profiles_map.items(), key=lambda x: x[1]):
            safe_name = pname.replace("-", "_").replace(".", "_")
            upstream_blocks.append(f"upstream profile_{safe_name} {{ server 127.0.0.1:{port}; }}")
            map_entries.append(f'    "{pname}" "127.0.0.1:{port}";')

        config = (
            "map $http_x_hermes_profile $backend {\n"
            '    default "127.0.0.1:8643";\n'
            + "\n".join(map_entries)
            + "\n}\n\n"
            + "\n".join(upstream_blocks)
            + """

server {
    listen 8642;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_set_header Origin "";
    proxy_set_header Referer "";

    location / {
        proxy_pass http://$backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Hermes-Profile $http_x_hermes_profile;
    }

    location /health {
        return 200 '{"status":"ok","service":"engine-nginx"}';
        add_header Content-Type application/json;
    }
}
"""
        )

        # 通过 Python + base64 写入配置（避免 shell 转义问题）
        import base64

        encoded = base64.b64encode(config.encode()).decode()
        commands = [
            'python3 -c "import base64; '
            f"open('/etc/nginx/conf.d/hermes-profiles.conf','w')."
            f"write(base64.b64decode('{encoded}').decode()); "
            "print('nginx config written')\"",
            "nginx -s reload",
        ]
        try:
            await self.exec_hermes_command(
                agent_id,
                commands,
                scope_type=scope_type,
                scope_target_id=scope_target_id,
            )
            logger.info(
                "update_nginx_config: %s reloaded with %d profiles", agent_id[:8], len(profiles_map)
            )
        except Exception as e:
            logger.warning("update_nginx_config: %s failed: %s", agent_id[:8], e)


# Singleton
k8s_manager = K8sManager()
