"""Pod Manager — manages engine Pod lifecycle on k3s.

Creates, suspends, resumes, and destroys engine Pods based on activity.
"""

import logging
import time

logger = logging.getLogger(__name__)


class PodManager:
    """Manages engine Pod lifecycle.

    In Phase 1, this uses kubectl commands. Future versions should use
    the kubernetes Python client library for direct API access.
    """

    def __init__(self, namespace: str = "unionagents"):
        self.namespace = namespace
        self.active_pods: dict[str, dict] = {}  # agent_id -> pod info

    def ensure_pod(self, agent_id: str, engine_type: str, config: dict | None = None):
        """Ensure an engine Pod exists for the given agent."""
        if agent_id in self.active_pods:
            pod = self.active_pods[agent_id]
            if pod.get("status") == "suspended":
                self.resume_pod(agent_id)
            return self.active_pods[agent_id]

        logger.info(f"Creating engine Pod for agent {agent_id} (type: {engine_type})")

        # In production, this would call k8s API to create Deployment + Service
        pod_info = {
            "agent_id": agent_id,
            "engine_type": engine_type,
            "status": "creating",
            "created_at": time.time(),
            "last_active": time.time(),
        }
        self.active_pods[agent_id] = pod_info
        pod_info["status"] = "running"
        return pod_info

    def suspend_pod(self, agent_id: str):
        """Suspend an engine Pod (scale to 0)."""
        if agent_id in self.active_pods:
            logger.info(f"Suspending engine Pod for agent {agent_id}")
            self.active_pods[agent_id]["status"] = "suspended"

    def resume_pod(self, agent_id: str):
        """Resume a suspended engine Pod (scale to 1)."""
        if agent_id in self.active_pods:
            logger.info(f"Resuming engine Pod for agent {agent_id}")
            self.active_pods[agent_id]["status"] = "running"
            self.active_pods[agent_id]["last_active"] = time.time()

    def destroy_pod(self, agent_id: str):
        """Destroy an engine Pod and its PVC."""
        if agent_id in self.active_pods:
            logger.info(f"Destroying engine Pod for agent {agent_id}")
            del self.active_pods[agent_id]

    def record_activity(self, agent_id: str):
        """Record activity timestamp for idle tracking."""
        if agent_id in self.active_pods:
            self.active_pods[agent_id]["last_active"] = time.time()

    def idle_check(self, suspend_after: int = 1800, destroy_after: int = 86400):
        """Check for idle Pods and suspend/destroy them.

        Args:
            suspend_after: Seconds of inactivity before suspend (default: 30min)
            destroy_after: Seconds of inactivity before destroy (default: 24h)
        """
        now = time.time()
        for agent_id, pod in list(self.active_pods.items()):
            idle = now - pod.get("last_active", now)
            if idle > destroy_after and pod["status"] in ("running", "suspended"):
                self.destroy_pod(agent_id)
            elif idle > suspend_after and pod["status"] == "running":
                self.suspend_pod(agent_id)


# Singleton
pod_manager = PodManager()
