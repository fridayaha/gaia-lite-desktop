# 研究文档

> 本节包含技术调研、方法论研究、竞品分析等前期研究文档。

- [文档工程总纲](doc-engineering-master-plan) — Gaia 系统性技术文档的架构与执行计划
- [技术文档写作方法论](tech-doc-writing-research) — Diátaxis + arc42 等写作框架调研
- [Palantir 能力差距分析](palantir-capability-gap-analysis)
- [Palantir 权限隔离参考](palantir-permission-isolation-reference)
- [Palantir 权限回顾与行业对比](palantir-permission-review-and-industry-comparison)
- [权限数据下推与 Python 组件](permission-data-pushdown-and-python-components)
- [权限前端 UX 与开发者体验](permission-frontend-ux-and-developer-experience)
- [权限技术栈深潜](permission-tech-stack-deep-dive)

## 图推理与虚拟表联邦

> **身份模型决策（2026-07-15）**：采用 Palantir [Resource Identifier](https://github.com/palantir/resource-identifier) 规范取代裸 UUID。格式 `ri.<service>.<instance>.<type>.<locator>`，Gaia 对象 RID 为 `ri.ontology.main.object.{uuid}`。locator 用 UUID（系统身份，稳定不变），与 primary key（业务身份）正交分离——这是 Palantir 核心设计，不因 primary key 改变而变 RID。命名统一为 `rid`（通用概念，所有资源都用，靠 type 段区分，与 `ontology.rid` 同名不冲突）。废弃原 `vid`/`object_id` 命名。Iceberg 不用 RID（用业务主键列）。应用层判等用 `(typeId, primaryKey)`。详见 [三场景模拟分析](three-scenarios-ontology-graph-federation) 身份模型决策注。

- [虚拟表填充 Neo4j 可行性调研](virtual-table-neo4j-projection-feasibility) — 评估 VIRTUAL 对象能否填充 Neo4j，业界三种主流模式 + Neo4j Virtual Graph 版本门槛核实 + 路径选型
- [Ontop 源码分析](ontop-source-analysis) — 虚拟知识图谱（VKG）实现剖析，五阶段查询翻译流水线，Trino 支持现状，与 Gaia 的对照及可参考点
- [Palantir 动态本体映射 Neo4j 方案对照分析](palantir-neo4j-mapping-proposal-comparison) — 用户提供的企业级方案逐节对照，识别可借鉴点（基数校验/lattice/批量优化）与拒绝点（主存分歧/元属性进 Neo4j），整合 Ontop 结论形成决策三角
- [三场景模拟分析](three-scenarios-ontology-graph-federation) — 纯 Ontop / 纯 Palantir / Gaia 折中（PK+描述+索引列入 Neo4j，其余 Trino 联邦）端到端场景推演，逐项验证拓扑连通/水合/剪枝/一致性/工程成本，确认折中方案为唯一架构合规路径
