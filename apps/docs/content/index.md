---
layout: home

hero:
  name: UnionAgents
  text: 企业级 AI 智能体平台
  tagline: 多引擎接入 · 统一鉴权 · 可观测 · 私有化部署
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/getting-started
    - theme: alt
      text: API 调用
      link: /guide/api-usage
    - theme: alt
      text: 进入控制台
      link: http://190.92.230.115:30080/

features:
  - title: 多引擎接入
    details: 支持 Hermes / Dify / OpenClaw 等引擎，统一适配层归一化协议，按智能体实例路由。
  - title: OpenAI 兼容 API
    details: 每个智能体可签发 sk- 风格 API Key，外部 OpenAI SDK 直连，无需改造客户端。
  - title: 统一鉴权
    details: JWT + sk- API Key 双路径，Gateway 统一鉴权，admin 门户 nginx 反代对外。
  - title: 全链路可观测
    details: Langfuse 链路追踪 + LiteLLM 用量统计 + Prometheus 指标 + Loki 日志，监控中心一站查看。
  - title: IM 渠道集成
    details: 企微 / 飞书 / 钉钉等 IM 平台 webhook 接入，智能体即 IM 机器人。
  - title: 私有化部署
    details: K8s 原生，Helm/k3s 一键部署，数据存档与 PVC 策略保证数据安全。
---
