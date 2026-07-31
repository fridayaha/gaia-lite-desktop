"""E2E 生命周期测试包

前置条件：运行中的 k3s 集群，MinIO 已部署（make k8s-infra）

运行：
  pytest tests/e2e/ -v

这些测试会创建和删除真实的 K8s 资源（Deployment, Service）。
确保在无需保留资源的测试环境中运行。
"""
