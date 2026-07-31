# Hub 技术验证文档

> 本文档体系用于评估后续是否继续当前 Hub、是否引入开源框架、是否需要重构。

## 文档索引

| # | 文档 | 定位 |
|---|------|------|
| 00 | [技术验证计划](./00_technical_validation_plan.md) | 验证工作顶层计划：5 阶段、9 条退出标准、P1-P5 优先级 |
| 01 | [开源方案复用矩阵](./01_open_source_reuse_matrix.md) | 10 候选 × 16 维度逐格评估 + 深度评估（改造侵入/上游维护/复用定位） |
| 02 | [统一管理 vs 分散治理](./02_unified_vs_separate_management.md) | 四类资产统一 vs 分散的论证：统一治理面 + 类型化内容层 |
| 03 | [能力关系模型设计](./03_item_relation_design.md) | HubItemRelation：4 关系类型 + 2 scope + 3 版本策略 + P0 API |
| 04 | [Runtime Discover / Resolve](./04_runtime_discover_design.md) | 运行态发现接口：硬过滤规则 + 依赖递归展开 + 两级权限模型 |
| 05 | [下载与导出体系](./05_download_export_design.md) | 三类下载 + manifest_hash/package_hash + 签名预留 |
| 06 | [Manifest Spec v0.1](./06_manifest_spec_design.md) | 四类 manifest 规范：通用字段 + 类型特有字段 + 三级校验策略 |
| 07 | [身份权限边界](./07_identity_permission_design.md) | 6 人类角色 + Agent 权限 + Hub/IAM/Gateway/Runtime PE 划分 |
| 08 | [最终推荐方案](./08_final_recommendation.md) | 全部验证结论汇总：8 阶段路线 + 确定性结论 + 待 Spike 验证项 |

## 阅读顺序

- **技术负责人**：08 → 00 → 02 → 01
- **架构师**：02 → 03 → 04 → 06
- **开发者**：03 → 04 → 05 → 06 → 07
- **管理层**：08 → 00

## 配套文档

- 历史技术选型评估和方案设计文档已从主仓库移除，可通过 Git 历史追溯。
