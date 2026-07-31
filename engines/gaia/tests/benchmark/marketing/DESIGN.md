# Marketing Benchmark — 设计文档（DESIGN）

> 场景：汽车门店营销链路（线索 → 跟进 → 试驾 → 外呼 → 成交）。
> 业务输入：`门店本体建模-1.1.1.md`（51 实体 / 422 属性 / 55 关系）+ `MySQL查询脚本.md`（17 个业务查询）。
> 方法论依据：`docs/engineer/research-benchmark-principles.md`（9 大原则）。
> 本体命名规则：`docs/handoff-apiname-derivation.md`。
> 本文件是 Marketing Benchmark 的**单一真相源**；harness 代码、用例 YAML、报告模板均以本文为准。

---

## 〇、TL;DR

- **测什么**：汽车门店营销场景下，Gaia ontology 后端（FastAPI）在**读 / 写 / 安全 / Agent** 四维的正确性、性能、隔离性、统计严谨性。
- **怎么测**：固定种子构造万级种子数据 → 注册营销本体 → 写路径走系统 API（原则 3.5）、读路径可直连物理库推导黄金真值 → 四维用例 paired 跑 → 产出含 CI / trivial baseline / Oracle / 局限量化的诚实报告。
- **硬约束**：每用例 ≤ 60s（超时记 ERROR 不算 FAIL）；全局 wall-clock ≤ 2h；写路径禁止直连数据系统写（fixture seeding 与 cleanup 例外）；AI 产物嵌入写路径（不独立成维）。
- **不是什么**：不是极限性能压测工具（规模万级，单表上限 10 万，非百万级）；不是生产监控（无持续运行）；不是替换旧航空 benchmark（那是已删的独立产物，本 benchmark 从零新建，仅复用其检出的 7 个后端缺陷作回归目标）。

---

## 一、总览

### 1.1 被测系统（SUT）

Gaia ontology 后端 FastAPI app（`ontology.main:app`），通过 REST API 暴露能力：

| API 组 | prefix | 被测能力 |
|---|---|---|
| Ontology | `/ontologies` | 本体注册 / ObjectType / LinkType / dataset-link |
| Query | `/objects` | 本体语义查询（Doris 主 / Trino 降级） |
| Action | `/actions` | Action 执行（PG 原子 + Outbox + write-back） |
| DataSource | `/api/datasources` | 数据源 / 虚拟表 / 同步任务 |
| AI | `/ai` | AG-UI Agent / scaffold / generate（Agent 维度 + AI 产物推导） |

**运行前提**（benchmark 启动前由 harness 负责）：
1. 本地 `docker compose up -d` 全栈已起（postgres / gravitino / rustfs / doris / trino / seatunnel-master / seatunnel-worker）。**必须用 `scripts/bootstrap_all.sh`** 启动——它起全所有服务（含 seatunnel-worker）并做 Gravitino metalake/pg-catalog/Iceberg namespace 的幂等初始化。
2. 一个**真实 MySQL 实例**作为源端，schema 按 marketing 来源数据表建表（见 §3.2）。
3. 后端 app 已启动。
4. MySQL 源端数据已按固定种子 seed 完成（fixture seeding，见 §5.1）。

**系统初始化自动化边界**（这些由后端代码/初始化脚本自动完成，**禁止 benchmark 或外部命令手动建**，违反即原则 3.5 的捷径）：
- **RustFS `ontology-warehouse` bucket**：由 `IcebergStore.ensure_warehouse_bucket()` 用 aiobotocore 自动 `CreateBucket`（在 `ensure_namespace` 前调用）。Iceberg/Gravitino/S3FileIO 都不自动建 bucket，这是绕不开的 S3 API。
- **Doris `ontology` database**：由 `DorisIndexStore._get_pool()` 自动 `CREATE DATABASE IF NOT EXISTS`（首次连接时）。
- **Iceberg namespace**：由 `IcebergStore.ensure_namespace()` 自动创建。
- **Gravitino metalake/pg-catalog**：由 `bootstrap_all.sh` 幂等创建。
- **Trino gravitino connector 插件**：jar 包入库 `config/trino/gravitino/`（`gravitino-trino-connector-473-478-1.3.0.tar.gz` 解压），重建 Trino 生效。

### 1.2 方法论原则映射

| research 原则 | 本 benchmark 落地 |
|---|---|
| 1 METI 循环 | 每用例配"解释栏"；反常结果必须 Test（改实现验证归因） |
| 2 双准则 | 用例覆盖矩阵显式列"预期表现差场景"（如 VIRTUAL 写、无源属性查询） |
| 3 Task Validity | trivial baseline（do-nothing/dump-all/enumeration）+ Oracle solver + 任务间状态清理 + 环境冻结 |
| 3.5 写路径只走 API | 所有写走 Action API；postcondition 只读直连；fixture seeding / cleanup 例外 |
| 4 Outcome Validity | 集合等价 / ordered / count 区间 / 结构化 postcondition / partial credit / 防 enumeration |
| 5 统计严谨 | n/mean/std/CI；paired（McNemar / paired t）；几何平均；校准≠验证集分离 |
| 6 诚实报告 | 环境指纹 / 构造效度 / trivial + Oracle + human baseline / 局限量化 / 解释指南 |
| 7 可复现 | 固定种子 RANDOM_SEED=42；物理 SQL 推导 expected；golden set versioning |
| 8 性能避坑 | warmup / 非病态点 / CPU 利用率 / loop overhead / invert 顺序 |
| 9 harness 稳健 | flake-aware / cost cap / regression-only gating / multi-metric / ERROR/FAIL/XFAIL |

### 1.3 与已删航空 benchmark 的关系

旧航空 benchmark 已删除，但其端到端验证检出的 **7 个后端真实缺陷**（见 `docs/bugfix/benchmark-detected-backend-defects.md`）独立留存。本 benchmark **针对每条缺陷设计回归用例**，修复后验证不复发：

| 缺陷 | 回归用例 | 目标 |
|---|---|---|
| #1 order_by 字段映射失效 | 读路径 L2 | jaccard ≥ 0.9 |
| #2 VIRTUAL filter range 语义错误 | 读路径 L7-bis（VIRTUAL 子集） | count 误差 ≤ 1 |
| #3 Action 规则类型转换 | 写路径 W11 | 422 → 200 |
| #4 write-back NOT NULL 列缺失 | 写路径 W10 | postcondition 通过 |
| #5 OCC 冲突成功率低 | 写路径 W6 | 成功率 > 99% |
| #6 对象类型级权限未生效 | 安全 S1 | 403 而非 200 |
| #7 连接池泄漏 | 全局：benchmark 跑完连接池 idle=0 | 无泄漏 |

---

## 二、工程约束（硬性，不可违反）

### 2.1 时间预算

| 约束 | 值 | 违反处理 |
|---|---|---|
| 单用例执行 | ≤ 60s | 超时记 `ERROR_TIMEOUT`，不算 FAIL，不污染正确率 |
| Agent 单次 retry | ≤ 60s 内跑 N 次（至少 3） | 3 次未完成则降级单次 + 标 `flake_n=1` |
| 性能用例 warmup | 1 次（非默认 3） | warmup 不计时但占预算 |
| 性能用例测量 | 3 轮，单轮超时即中止 | 该档记 `ERROR_TIMEOUT` |
| 全局 wall-clock | ≤ 2h | fail-fast 中止，产出部分报告 + "未完成维度"标注 |

### 2.2 写路径硬约束（原则 3.5）

- **所有写操作走系统 API**：`POST /actions/execute`、`POST /ontologies`、`POST /api/datasources` 等。
- **禁止 benchmark 直连 MySQL / PG / Doris / Iceberg 执行写**（INSERT/UPDATE/DELETE/DDL）来产生被测数据或变更状态。
- **例外 1（fixture seeding）**：benchmark 开始前数据生成器直连 MySQL 生成源端物理数据（系统输入，非系统输出）。
- **例外 2（cleanup）**：任务间状态清理脚本可直连数据系统删除，需 `--dry-run` / `--confirm` 双保险，不参与正确性断言。
- **读路径无约束**：直连 MySQL / PG / Doris / Iceberg 做只读查询（黄金真值推导 / postcondition 校验 / 同步一致性校验 / 物理基线对比）允许且必需。

**落地检查清单**（每条写用例 / setup 脚本都要过）：
- [ ] 写操作走系统 API？
- [ ] 无直连数据系统写（除 seeding/cleanup 例外）？
- [ ] postcondition 校验只读？
- [ ] cleanup 脚本有 dry-run/confirm 双保险？

### 2.3 数据规模（万级，单表上限 10 万）

各表量级按营销链路基数关系 + 用例覆盖需求评估。最大单表 3 万行（lead_allocate_record / lead_follow_record），远低于 10 万上限。涉及大表的用例必须带过滤条件（lead_id / 门店 / 日期 / phone），禁止全表扫，保证 60s 内完成。

| 实体 | 行数 | 评估理由 |
|---|---|---|
| dealership | 20 | 多门店隔离（S3）需足够门店做交叉验证，覆盖大区/省域分布 |
| sales_consultant | 200 | 每门店 10 销售，支撑行级隔离（S2）+ 跨销售反查 |
| lead_source | 30 | 4 级层级，覆盖一级/二级组合 |
| user | 10,000 | 客户基数，支撑增量同步（L6）和聚合统计（L4）的统计效力 |
| lead | 10,000 | 1 user:1 lead，查询主实体，万级保证过滤/排序用例有足够命中行 |
| lead_allocate_record | 30,000 | 每线索平均 3 次操作（下发+分配+转移/回收），支撑分配/转移/回收写用例的历史上下文 |
| lead_distribute_record | 15,000 | 每线索 1-2 次下发 |
| lead_follow_record | 30,000 | 每线索平均 3 次跟进，支撑跟进写入（W4）和反查 |
| manual_outbound_call | 20,000 | 每线索平均 2 次外呼，支撑呼出统计（L4）和外呼写入（W6） |
| ai_outbound_call | 10,000 | AI 外呼量约为人工外呼一半 |
| test_drive | 5,000 | 约 50% 线索试驾，支撑试驾状态机（W5）和试驾报告（W7） |
| test_drive_car | 200 | 每门店 10 试驾车 |
| test_drive_route | 100 | 每门店 5 路线 |
| recording（合成） | 30,000 | 从 test_drive+manual_outbound+ai_outbound 的 url 归并，去重后约 3 万 |
| chat_record | 20,000 | 部分用户的微信聊天 |
| 试驾报告 5 表 | 5,000 试驾 × 多行 ≈ 25,000 行/表 | AI 产物，W7 触发生成 |
| 用户画像 8 表 | 500 user × 多行 ≈ 4,000 行/表 | AI 产物，W8 触发生成，只对有试驾/外呼的 user 生成 |

**用例查询范围约束**（保证 60s）：
- L2/L4/L7 等大表用例必须带 `:sales_phone` / `:store_code` / `:date` 过滤，命中行数控制在百~千级。
- L6 增量同步用 `:formatted_time` 过滤，单次拉取行数 ≤ 1,000。
- 禁止任何用例对 lead/user/lead_follow_record 等万级表做全表扫描。
- AI 产物用例（W7/W8）单次触发样本 ≤ 20 条试驾/用户，保证 LLM 批量推导在 60s 内完成。

### 2.4 数据语言（中文为主）

- **生成数据以中文为主**：人名、门店名、地址、城市、车系/车型名、跟进内容、话术、试驾路线名、用户画像标签、试驾报告摘要等业务文本字段均为中文。
- **Faker locale = `zh_CN`**：人名（`name`）、城市（`city`/`province`）、地址（`address`）、公司名等走中文 locale，固定种子保证可复现。
- **枚举值中文**：业务字典（线索状态、试驾单状态、外呼状态、操作类型等）的**展示值**用中文（如试驾单状态「待排程/待签署协议/待开始/进行中/已结束/已评价/已取消」），但**物理存储值**保留原编码（如 `order_status='1'`），通过本体 displayName 映射，避免破坏物理 SQL 推导。
- **标识符 ASCII**：主键、外键、手机号、vin 码、车牌号、车系/车型 code 等**标识符字段**保持 ASCII（`'L20240601001'`、`'京A12345'`、`'VIN...'`），保证 join/filter/order 稳定，不受 locale 干扰。
- **自然语言用例中文**：Agent 维度（§4.4）的自然语言问题、读路径的查询意图描述均为中文，贴合真实营销人员表达。
- **不影响物理列名**：物理列名仍按 §3.2 修正后契约用 snake_case ASCII（`follow_purpose`），中文只出现在**值**里，不出现在列名/表名。
- **AI 产物中文**：试驾报告摘要（`summary`）、用户画像描述（`description`/`reasoning_detail`）、话术优化建议（`optimization_advice`）等 AI 生成文本为中文，与 LLM 推导语言一致。

### 2.5 确定性与可复现

- `RANDOM_SEED = 42`（全局），所有数据生成、采样、并发顺序均由此推导。
- expected 由同一份种子数据用**物理 SQL** 推导，不手写。
- golden set versioning：expected 随数据/模型版本绑定，drift 时区分"答案漂移"vs"模型漂移"。
- 单次全自动化运行产出最终报告（`make benchmark-marketing`），禁止拼接多次 run 结果。

---

## 三、本体建模（裁剪 + 修正）

### 3.1 实体裁剪

源本体 51 实体，裁剪规则：
- **保留**：营销核心链路实体 + 有真实数据的 AI 分析产物实体。
- **移除**：占位实体（仅 1 属性 `xxx_id`、备注"占位，待补充"）——共 13 个（benefits/competitor/inspection_standard/outbound_call_specialist/policy/script_strategy/generate_script_strategy/scheduling/verify/manual_invite/sales_consult_profile/outbound_call_robot/order）。
- **例外**：`competitor` 被 `competitive_analysis` 引用，保留（仅作外键引用，不参与查询主路径）。

**裁剪后实体集（38 个）**：

| 类别 | 实体 |
|---|---|
| 主数据 | dealership, sales_consultant, lead_source, user |
| 线索链路 | lead, lead_allocate_record, lead_distribute_record, lead_follow_record |
| 外呼 | manual_outbound_call, ai_outbound_call, recording（合成） |
| 试驾 | test_drive, test_drive_car, test_drive_route |
| 试驾报告（AI 产物） | td_analysis_details, competitive_analysis, strategy_execution_audit, script_execution_analysis, focus_resistance_points |
| 用户画像（AI 产物） | user_profile_basic_note, user_profile_overview, customer_profile_emotion, customer_profile_inferred_tag, customer_profile_usage_scenario, customer_profile_purchase_motivation, customer_profile_product_preference, customer_profile_resistance |
| 产品知识 | vehicle_series, vehicle_model, config_feature, product_capability, capability_metric, usp, usp_tag, pitch, scenario |
| 其他 | reception, chat_record, competitor |

### 3.2 第 5 点修正：本体↔物理映射契约

源本体有 4 处映射不一致/笔误，按 `docs/handoff-apiname-derivation.md` 命名规则修正。**修正后的契约是黄金真值推导与本体 API 查询的共同基准。**

#### 修正 1：`test_drive.test_drive_consultant_id` 移除

- **问题**：本体属性 `test_drive_consultant_id`（试驾顾问）来源字段填 `test_drive_id`（那是主键，笔误）。物理表 `test_drive_rt` 实际只有 `sale_id` 关联销售顾问，无试驾顾问列。导致"两个属性指向同一实体"歧义。
- **修正**：
  - 移除 `test_drive.test_drive_consultant_id` 属性。
  - 移除关系 #38 `test_drive__test_drive_consultant`。
  - 试驾→销售顾问仅保留 `test_drive.sales_consultant_id`（→ `sale_id`）。
- **回归用例**：读路径 L3-bis 验证"移除歧义属性后，试驾→销售顾问查询结果稳定"。

#### 修正 2：`lead_follow_record` 列名统一 snake_case

- **问题**：本体来源字段栏混用 camelCase 连写（`followpurpose`/`nextfollowtime`...）。按命名规则，backingColumn 是物理列名（snake_case 保词界），属性 apiName 由后端从 backingColumn 推导为 camelCase。
- **修正**：物理列名统一 snake_case，本体属性 displayName 用中文、apiName 后端推导：

| 本体属性 apiName（推导） | displayName | backing_column（snake_case） |
|---|---|---|
| followPurpose | 跟进目的 | follow_purpose |
| communicationMethods | 沟通方式 | communication_methods |
| followResult | 跟进结果 | follow_result |
| followContent | 跟进内容 | follow_content |
| intendedLevel | 意向级别 | intended_level |
| vehicleModelCode | 意向车型编码 | vehicle_model_code |
| vehicleModelName | 意向车型 | vehicle_model_name |
| businessNo | 订单号 | business_no |
| nextFollowTime | 下次跟进时间 | next_follow_time |
| followShopId | 跟进门店编码 | follow_shop_id |
| arriveTime | 到店时间 | arrive_time |
| defeatType | 战败类型 | defeat_type |
| changeBusinessOpportunity | 是否转商机 | change_business_opportunity |
| createTime | 创建时间 | create_time |

#### 修正 3：`recording` 合成实体

- **问题**：物理侧无 `recording` 表，`recording_id` 是 ontology 层合成 id。但 `MySQL查询脚本.md` 脚本 4/14/17 直接 `JOIN recording`，物理上跑不通。
- **修正**：fixture seeding 阶段从三处归并生成合成 `recording` 物理表：
  - 来源 1：`test_drive.original_record_url`
  - 来源 2：`manual_outbound_call.original_record_url`
  - 来源 3：`sale_call_record.original_record_url`（人工外呼原始表）
  - `recording_id` = `sha1(source_table + ':' + original_record_url)[:16]`（固定种子，可复现）
  - `recording_url` = 原 url；`recording_text` = NULL（暂不填充）
- 黄金真值 SQL 用此合成表做 LEFT/INNER JOIN。

#### 修正 4：`user` CDP 来源简化

- **问题**：本体 `user` 后 2 属性（`phone_brand`/`phone_device_model`）来自跨库 CDP 事件表 `t_ods_linkflow_event`，需按 user 聚合，规则未定义。
- **修正**（按用户指示"暂时不用 join，构造统一来源"）：
  - `user` 物理表统一只用 `t_ods_leads_server_leads_user_rt` 主源，4 列：`user_id`/`reg_time`→`register_time`/`user_name`/`mobile`→`phone_number`。
  - `phone_brand`/`phone_device_model` 属性在本体保留，backing_mapping 留空（标记"无物理源"）。
  - 用例 expected：这两个字段 = null。作为"无源字段查询"用例覆盖（L8）。

#### 物理表清单（fixture 建表依据）

| 物理表 | 对应本体实体 | 列名风格 |
|---|---|---|
| `t_ods_master_data_store` | dealership | snake_case |
| `t_ods_master_data_staff` | sales_consultant | snake_case |
| `t_ods_leads_server_leads_source` | lead_source | snake_case |
| `t_ods_leads_server_leads_user_rt` | user | snake_case |
| `t_ods_leads_server_leads_info_rt` | lead | snake_case |
| `t_ods_source_data_leads_operation_record` | lead_allocate_record / lead_distribute_record | snake_case |
| `t_ods_source_data_leads_follow_record` | lead_follow_record | snake_case（修正 2） |
| `t_ods_leads_server_sale_call_record_rt` | manual_outbound_call | snake_case |
| `t_ods_leads_server_ai_call_out_result_rt` | ai_outbound_call | snake_case |
| `t_ods_test_drive_test_drive_rt` | test_drive | snake_case |
| `t_ods_test_drive_car_model` | test_drive_car | snake_case |
| `t_ods_test_drive_route` | test_drive_route | snake_case |
| `recording`（合成） | recording | snake_case |
| 试驾报告 5 表 / 用户画像 8 表 | AI 产物 | snake_case（ontology 自建，非源端） |

### 3.3 关系裁剪

源 55 关系，移除：
- #38 `test_drive__test_drive_consultant`（修正 1）。
- 涉及已移除占位实体的关系（competitive_analysis__competitor 保留，competitor 保留）。

裁剪后约 53 关系。关系→外键映射沿用 `MySQL查询脚本.md` §6.1 对照表（"多"侧持外键）。

---

## 四、四维用例矩阵

> 每维度用例分 Tier（沿用三层金字塔诚实策略）：
> - **Tier1 可跑**：当前系统应通过。
> - **Tier2 倒逼**：当前预期失败（xfail），倒逼后端修复。
> - **Tier3 北极星**：远期目标，xfail。

### 4.1 维度 1：读路径（Read）

**测什么**：本体语义查询（ObjectQueryService，Doris 主 / Trino 降级）相对物理直连的正确性 + 性能 Tax%。

**黄金真值**：物理 SQL（`MySQL查询脚本.md` 改写版，含 recording 合成表 join）推导 expected。

| ID | Tier | 场景 | 来源脚本 | 断言 kind | trivial baseline |
|---|---|---|---|---|---|
| L1 | 1 | 单实体点查：lead_id 反查客户信息 | 7 | set_eq | do-nothing/dump-all/random-id |
| L2 | 1 | 单实体过滤+排序：待邀约线索（next_follow_time + leads_status + is_test_drive） | 1 | ordered_list + jaccard≥0.9 | （回归缺陷#1 order_by） |
| L3 | 1 | 多表 JOIN 反查：lead_id → 销售手机号（经 lead_allocate_record → sales_consultant） | 6 | set_eq | |
| L3-bis | 1 | 试驾 → 销售顾问反查（修正 1 回归） | 14 子集 | set_eq | |
| L4 | 1 | 聚合统计：今日呼出数（COUNT） | 9 | count_eq | |
| L5 | 1 | LEFT JOIN 可选关联：已完成试驾 + 录音（recording 合成表） | 4 | set_eq + null_allowed | |
| L6 | 1 | 增量同步：按 update_time 拉取销售顾问 | 15 | set_eq | |
| L7 | 1 | 跨门店过滤：某门店所有销售的有效线索 | 泛化 | set_eq | |
| L7-bis | 2 | VIRTUAL filter range（竞品对比分析表走 Trino 联邦） | 泛化 | count_eq | （回归缺陷#2） |
| L8 | 1 | 无源字段查询：user.phone_brand = null（修正 4） | 泛化 | set_eq(all_null) | |
| L9 | 3 | 时间旅行：试驾历史快照对比 | — | snapshot_diff | 北极星 |

**性能子项**（L1/L2/L4/L6 加性能测量）：
- warmup 1 次 + 测量 3 轮，并发档 `[1,3,7]`（非 2 幂），单轮 ≤ 60s。
- Tax% = `(onto_p95 - raw_p95)/raw_p95`，bootstrap 95% CI。
- 几何平均跨用例聚合 speedup。
- 记录 CPU 利用率辅助 tax 解释。
- 物理基线 = 直连 MySQL 同查询。

### 4.2 维度 2：写路径（Write）—— 含 AI 产物嵌入

**测什么**：Action 执行（ActionService → PG 原子 → Outbox → write-back）的 postcondition 正确性 + OCC 并发。

**硬约束**：所有写走 `POST /actions/execute`；postcondition 只读直连。

| ID | Tier | 场景 | Action | postcondition | 回归 |
|---|---|---|---|---|---|
| W1 | 1 | 线索分配（operation_type=2） | allocateLead | lead_allocate_record 新增行 + lead.sales_consultant_id 更新 | |
| W2 | 1 | 线索转移（operation_type=3） | transferLead | 旧销售记录 + 新销售记录 + lead 归属变更 | |
| W3 | 1 | 线索回收（operation_type=4） | reclaimLead | lead_allocate_record + lead.claim_status | |
| W4 | 1 | 线索跟进记录写入 | recordFollow | lead_follow_record 新增行 | |
| W5 | 1 | 试驾状态流转（0→1→2→3→4） | progressTestDrive | test_drive.order_status 状态机 | |
| W6 | 1 | 外呼记录写入 | logManualCall | manual_outbound_call 新增 + recording 关联 | |
| W7 | 1 | **AI 产物生成**：试驾完成 → 触发分析 → 5 张试驾报告表 | analyzeTestDrive | 5 表行数 + schema 校验 + confidence≥0.6 | |
| W8 | 1 | **AI 产物生成**：用户画像 8 张表 | generateUserProfile | 8 表行数 + schema + confidence | |
| W9 | 2 | OCC 并发：50 并发分配同一线索 | allocateLead | 成功率 > 99% | 缺陷#5 |
| W10 | 2 | write-back NOT NULL 列补齐 | progressTestDrive | 源端 MySQL 行写入成功 | 缺陷#4 |
| W11 | 2 | Action 规则类型转换（ObjectReference 比较） | reassignTestDriveCar | 200 而非 422 | 缺陷#3 |
| W12 | 3 | 跨本体 Action 联动 | — | — | 北极星 |

**trivial baseline**（写路径捷径审计）：
- 空 payload Action（应 422，不能蒙混通过）。
- 绕过校验的 Action（非法 operation_type）。
- 缺 NOT NULL 列的 write-back。
- do-nothing agent（不执行任何 Action，验证 postcondition 未变 = 失败）。

**Oracle solver**：每条写用例有确定性 solver（直接调 API 走通完整流程），证明可解。

### 4.3 维度 3：安全（Security）

**测什么**：多门店 / 多销售数据隔离 + 权限边界。

**leak 量化**：报 `leak_calls / total_calls`，不只 0/1。

| ID | Tier | 场景 | 期望 | 回归 |
|---|---|---|---|---|
| S1 | 2 | 对象类型级读权限：无 read 权限角色查 lead | 403 | 缺陷#6（P0） |
| S2 | 1 | 行级隔离：销售 A 查销售 B 的线索 | 403 / 空集 | |
| S3 | 1 | 门店隔离：销售查他店数据 | 403 / 空集 | |
| S4 | 2 | 写权限：销售操作未分配给自己的线索 | 403 | |
| S5 | 2 | AI 产物可见性：用户画像按角色分级 | 低权角色不可见情绪/抗性 | |

**trivial attacker baseline**：
- 越权直查（不带 principal）。
- 枚举 lead_id 探测他人数据。
- 越权写（伪造 sales_consultant_id）。

### 4.4 维度 4：Agent（Text-to-Ontology vs Text-to-SQL paired）

**测什么**：同一自然语言问题，本体语义查询 vs 物理 SQL 双模式准确率差异（paired）。

**场景**：读路径 L1-L8 的查询自然语言化（如"查销售张三今天要回访的线索"），paired 跑双模式。

**统计**：
- McNemar 双侧精确检验 + 95% CI（n = 用例数）。
- flake-aware：60s 内跑 N 次（≥3）取 pass_rate，阈值通过（如 3 次过 2 次）。
- cost cap：全局 token 上限，超限 fail-fast 中止 Agent 维度。

**trivial agent**：
- do-nothing（返回空）。
- dump-all（全表）。
- enumeration（列出所有 lead_id）。

| ID | Tier | 场景 | 自然语言示例 |
|---|---|---|---|
| A1 | 1 | 单实体点查 NL | "查线索 L123 的客户姓名和电话" |
| A2 | 1 | 过滤+排序 NL | "销售张三今天要邀约的线索" |
| A3 | 1 | 多表反查 NL | "线索 L123 对应的销售手机号" |
| A4 | 1 | 聚合 NL | "销售张三今天打了多少通电话" |
| A5 | 1 | 可选关联 NL | "昨天完成的试驾和它们的录音" |
| A6 | 1 | 增量 NL | "昨天之后更新的销售顾问" |
| A7 | 1 | 跨门店 NL | "北京门店所有销售的有效线索" |
| A8 | 2 | 模糊/歧义 NL | "最近表现好的销售"（测歧义处理） |
| A9 | 3 | 多轮对话 NL | "帮我找出待回访线索，再按试驾完成排序" |

---

## 五、黄金真值推导器

### 5.1 fixture seeding（例外 1）

数据生成器（`scripts/seed_marketing.py`）在 benchmark 开始前直连 MySQL 生成源端物理数据：
- `RANDOM_SEED = 42`，所有随机值由此推导（Faker `zh_CN` locale + 固定分布，见 §2.4）。
- 生成 §2.3 规模的数据，含 recording 合成表（修正 3）。
- 业务文本字段（人名/门店名/地址/跟进内容等）以**中文为主**；标识符字段（主键/外键/手机号/vin/车牌/code）保持 ASCII。
- **进入系统的过程**（MySQL → Iceberg → Doris 同步）走系统同步管道 API，不直连 Iceberg/Doris 写。

### 5.2 物理 SQL 推导 expected

`MySQL查询脚本.md` 的 17 个 SQL 作为推导器输入，按修正后契约改写：
- 补 recording 合成表 join（修正 3）。
- `lead_follow_record` 列名改 snake_case（修正 2）。
- 移除 `test_drive_consultant_id` 相关 join（修正 1）。
- `user.phone_brand`/`phone_device_model` 查询 expected = null（修正 4）。

改写后的物理 SQL 集合存 `data/expected_sql/`，每条对应一个用例 ID。推导器跑这些 SQL 得 expected，与本体 API 实际结果对比。

### 5.3 同步一致性校验

写路径用例 postcondition 完成后，轮询多源一致性（只读）：
- MySQL（源端）↔ Iceberg ↔ Doris 行数 / 校验和。
- 超时（60s）未一致记 `ERROR_SYNC_TIMEOUT`。

---

## 六、断言引擎

支持多 kind，比单一 0/1 灵活：

| kind | 语义 | 用途 |
|---|---|---|
| `set_eq` | 集合相等（无序） | 多数读路径 |
| `ordered_list` | 有序集合相等 | L2 排序查询 |
| `count_eq` | 行数相等 | 聚合统计 |
| `count_range` | 行数在区间 | 容忍 AI 产物数量波动 |
| `jaccard` | Jaccard 相似度 ≥ 阈值 | 排序近似（阈值基于 pilot 数据定） |
| `null_allowed` | 允许 null 的列 | L5 LEFT JOIN |
| `all_null` | 全部为 null | L8 无源字段 |
| `snapshot_diff` | 快照差异 | 时间旅行 |
| `action_rejected` | Action 被 403/422 拒绝 | 安全 / trivial baseline |
| `forbidden` | 禁止出现的状态 | 捷径审计 |
| `schema_valid` | 结构化 postcondition（JSONB schema） | AI 产物 |
| `set_equiv` | 集合等价（多答案） | 同一问题多正确答案 |

**partial credit**：读路径按 SQL 组件（filter / order / limit / aggregate）分解打 F1，借鉴 Spider，比整体 0/1 更能定位问题。

**camelCase↔snake_case 双向匹配**：物理层（snake）与 API 层（camel）命名差异的务实处理（沿用旧 benchmark 亮点）。

**防 enumeration 攻击**：要求精确数量或唯一性约束，防止"列出所有可能答案"蒙混。

---

## 七、统计方法

| 维度 | 方法 |
|---|---|
| 读路径 | paired（同 question 物理直连 vs 本体 API）；Tax% bootstrap 95% CI；几何平均 speedup；n/mean/std/CI |
| 写路径 | 成功率 + 95% CI；OCC 并发成功率（目标 >99%）；postcondition 通过率 |
| 安全 | leak_calls/total；漏沙率 + 95% CI |
| Agent | McNemar 双侧精确检验（双模式 paired）；pass_rate（flake-aware）；cost 统计 |

**通用**：
- 绝不只报原始平均值，必给 std/CI。
- 几何平均聚合跨用例比率。
- 校准集 ≠ 验证集（调阈值用 held-out 集）。
- 样本量与 CI：100 条用例准确率，若 ground truth 有噪声，给正态近似 CI。

---

## 八、harness 工程化

### 8.1 用例结果分类（严格区分）

| 分类 | 含义 | 计入正确率 |
|---|---|---|
| PASS | 通过 | 是 |
| FAIL | 答错 | 是 |
| XFAIL | 预期失败（Tier2/3） | 否（修复后转 PASS 触发告警） |
| ERROR | 系统崩 / 超时 / 同步失败 | 否 |
| ERROR_TIMEOUT | 超 60s | 否 |
| ERROR_SYNC_TIMEOUT | 同步一致性超时 | 否 |

### 8.2 稳健性机制

- **flake-aware**：Agent 跑 N 次取 pass_rate。
- **cost-bounded**：全局 + 单测 token 上限，超限 fail-fast。
- **regression-only gating**：CI 按相对 baseline 退化挂，不按绝对阈值（防 bit-rot）。
- **multi-metric**：语义相似 + 结构化断言 + token 成本，三者交叉。
- **幂等注册**：409 跳过，可重复跑（沿用旧 benchmark）。
- **独立清理**：每用例跑前 fully clean legacy state；cleanup 脚本 `--dry-run`/`--confirm` 双保险。

### 8.3 性能测量避坑

- warmup 1 次填缓存（Doris 索引 / Iceberg manifest / 连接池），不计入统计。
- 每并发档跑 3 轮，std > 1% 报警找环境噪声。
- 并发档 `[1,3,7]`（非 2 幂，避免病态点）。
- 记录 CPU 利用率（throughput 降 ≠ overhead）。
- invert 顺序 + 连续/离散混合（查不该有/该有缓存）。

### 8.4 单次全自动化

`make benchmark-marketing` 一键跑完全流程出报告：
1. 拉起全栈 + 后端 + seed MySQL（harness 负责）。
2. 注册营销本体（幂等）。
3. 跑四维用例（按 60s / 2h 预算）。
4. 产出报告（见 §9）。
- 禁止拼接多次 run 结果（引入 stale/cache 误差）。

---

## 九、报告模板

```markdown
# Marketing Benchmark 报告 v<version>

## 环境指纹
- 组件版本：Gravitino 1.3.0 / Iceberg 1.11.0 / Doris 4.0.5 / Trino 478 / SeaTunnel 2.3.13 / PostgreSQL 16
- 后端 commit: <hash>
- LLM 模型: <AI_MODEL>
- 数据种子: RANDOM_SEED=42
- 运行时间: <start> ~ <end>（wall-clock <h>m）

## 构造效度声明
- 读路径测：本体语义查询正确性 + 性能 Tax%。指标：jaccard / Tax% CI。
- 写路径测：Action postcondition 正确性 + OCC。指标：postcondition 通过率 / 并发成功率。
- 安全测：数据隔离 + 权限边界。指标：漏沙率 / leak_calls。
- Agent 测：双模式准确率差异。指标：McNemar p / pass_rate。

## 各维度结果

### 读路径
| 用例 | Tier | n | mean | std | 95% CI | 结果 |
| ... |
- trivial baseline: do-nothing=<x%> dump-all=<x%> random-id=<x%>
- Oracle: <x%>
- 性能 Tax%: <mean> [95% CI <lo,hi>]，几何平均 speedup=<x>

### 写路径 / 安全 / Agent
（同结构）

## 已知局限及量化影响
- 缺陷#1 order_by：影响所有排序查询，量化：<x%> 用例受影响
- VIRTUAL 降级：影响 <x%> 用例
- LLM judge 噪声：Agent 维度 ground truth 噪声 ~<x%>，CI 宽度 <x>

## 结果解释指南
- 因缺陷#6 权限未生效，S1 全部漏沙，**不要据 S1 成功率做安全决策**，参考 S2/S3 行级隔离。
- 因缺陷#5 OCC，W9 成功率 <99%，**不要据 W9 做并发容量规划**，修复后重测。
- Agent 维度 n=<x>，CI 较宽，单看 pass_rate 不可靠，参考 McNemar p 值。

## 未完成维度（若有）
- 因全局 wall-clock 超限，<维度> 未完成。
```

---

## 十、实施路线

| 阶段 | 内容 | 产出 |
|---|---|---|
| P0 | 目录骨架 + 本体 JSON + 物理 SQL 改写 | `data/ontology/marketing-ontology.json` + `data/expected_sql/*.sql` |
| P1 | fixture seeding 脚本（含 recording 合成表） | `scripts/seed_marketing.py` |
| P2 | 本体注册脚本（幂等） | `scripts/01_setup_ontology.py` |
| P3 | 读路径 harness + 用例 L1-L8 | `harness/read_harness.py` + `cases/read/*.yaml` |
| P4 | 写路径 harness + 用例 W1-W11（含 AI 产物） | `harness/write_harness.py` + `cases/write/*.yaml` |
| P5 | 安全 harness + 用例 S1-S5 | `harness/security_harness.py` + `cases/security/*.yaml` |
| P6 | Agent harness + 用例 A1-A9（paired） | `harness/agent_harness.py` + `cases/agent/*.yaml` |
| P7 | 报告生成 + Makefile 集成 | `scripts/generate_report.py` + `Makefile` target |
| P8 | METI 循环：跑首轮 + 解释反常 + Test + Improve | 首份报告 + 缺陷回归验证 |

---

## 十一、目录结构

```
tests/benchmark/marketing/
├── DESIGN.md                      # ← 本文件
├── data/
│   ├── ontology/
│   │   └── marketing-ontology.json   # 裁剪+修正后的营销本体
│   ├── expected_sql/                 # 改写后的物理 SQL（黄金真值推导）
│   │   ├── L1.sql ... L9.sql
│   ├── cases/                        # 用例 YAML（含 expected、tier、trivial baseline）
│   │   ├── read/  write/  security/  agent/
│   └── seed/                         # 种子数据生成配置
├── scripts/
│   ├── seed_marketing.py             # fixture seeding（直连 MySQL，例外 1）
│   ├── 01_setup_ontology.py          # 本体注册（走 API，幂等）
│   ├── run_benchmark.py              # 单次全自动化入口
│   ├── generate_report.py            # 报告生成
│   └── cleanup.py                    # 状态清理（--dry-run/--confirm 双保险）
├── harness/
│   ├── __init__.py
│   ├── base.py                       # 基类：超时/分类/paired/统计
│   ├── read_harness.py
│   ├── write_harness.py
│   ├── security_harness.py
│   ├── agent_harness.py
│   ├── assertion_engine.py           # 多 kind + partial credit + 集合等价
│   ├── golden_truth.py               # 物理 SQL 推导 expected
│   ├── stats.py                      # McNemar / bootstrap CI / 几何平均
│   └── trivial_baselines.py          # do-nothing/dump-all/enumeration
└── reports/                          # 产出报告（.gitignore）
```

---

## 十二、实施进度（2026-06-30 快照，下次会话接续依据）

### ✅ 已完成

| 阶段 | 内容 | 产出 |
|---|---|---|
| P0 | 本体 JSON + 物理 SQL + 目录骨架 | `build_ontology.py` / `marketing-ontology.json`（39 OT / 282 属性 / 53 link / 9 Action）/ `data/expected_sql/L1-L8 + L3-bis + L7-bis` |
| P1 | fixture seeding（万级中文数据） | `seed_marketing.py` / `marketing_schema.sql`（15 物理表，固定种子 42，Faker zh_CN，recording 合成表） |
| P2 | 本体注册脚本 | `01_setup_ontology.py`（走系统 API，幂等，link 两阶段创建） |
| P2.5 | 批量串行同步脚本 | `02_sync_all_tables.py`（15 表串行同步 + 进度记录 + 断点续传 + `--resync-doris`/`--only`/`--retry-failed`） |
| 前置 | 同步链路跑通（全 15 表） | `verify_sync_chain.py` + 02 脚本（MySQL→SeaTunnel→Iceberg→sync_now→Doris→ObjectQueryService 全链路） |
| P3 | 读路径 harness + 用例 L1-L8 + L3-bis + L7-bis | `harness/{base,stats,golden_truth,assertion_engine,trivial_baselines,param_resolver,read_harness}.py` |
| P4 | 写路径 harness + 用例 W1/W4/W5/W6/W7/W9/W11 | `harness/write_harness.py`（含 OCC 并发 W9、规则类型转换 W11、AI 产物 W7 gated） |
| P5 | 安全 harness + 用例 S1-S4 | `harness/security_harness.py`（leak 量化 leak_calls/total） |
| P6 | Agent harness 骨架 + 用例 A1-A9 | `harness/agent_harness.py`（paired 双模式 + McNemar；需 LLM，未配置时全维 SKIP） |
| P7 | 报告生成 + 单次全自动化 + Makefile + cleanup | `run_benchmark.py` / `generate_report.py` / `cleanup.py`（`--dry-run`/`--confirm` 双保险）/ `scripts/start_backend.py`（Popen start_new_session 可靠守护）/ Makefile `benchmark-marketing*` target |
| P8 | METI 首轮跑通 | `reports/latest.md`（读 9/9=100%、写 3/3=100%、安全 leak 100%、Agent SKIP）+ 解释反常 |

### 首轮报告结论（2026-06-30）

- **读路径**: 9/9 = 100%（Wilson CI [70%, 100%]）。L1-L8 + L3-bis 全 PASS；L7-bis XFAIL（AI 产物表未生成）。trivial baseline 全部正确失败 → 用例有效。
- **写路径**: 3/3 = 100%（W1/W4/W6 PASS；W9 OCC 50 并发 100% 成功，**回归 #5 已修复**；W11 XFAIL 422=回归 #3 仍存在；W5 ERROR Action 规则 `value in [...]` simpleeval 不支持 List 字面量；W7 SKIP 无 LLM）。
- **安全**: leak rate 4/4 = 100%（S1 XFAIL 回归 #6；S2/S3 FAIL 无行级/门店隔离；S4 XFAIL）。MVP principal=anonymous，安全维全 leak 是已知现状。
- **Agent**: 全维 SKIP（未配置 AI_MODEL）。
- **反常解释**: L2/L4 性能 Tax% 虚高（236997%/215099%）——本体 API 无单调用多跳 JOIN filter，harness 多次 API 串联模拟，Tax% 含编排开销；L1/L6 Tax% ~600-925% 是较干净信号。

### 后端修复（本 benchmark 期间发现并修复的后端缺口）

| # | 缺口 | 修复 | 文件 |
|---|---|---|---|
| 1 | Trino gravitino connector 插件 jar 缺失 | 下载 `gravitino-trino-connector-473-478-1.3.0` 入库 | `config/trino/gravitino/`（32 jar） |
| 2 | RustFS `ontology-warehouse` bucket 不自动建 | `IcebergStore.ensure_warehouse_bucket()` 用 aiobotocore 自动 CreateBucket | `iceberg_store.py` |
| 3 | Doris `ontology` database 不自动建 | `DorisIndexStore._get_pool()` 自动 CREATE DATABASE | `doris_index_store.py` |
| 4 | IndexFieldExtractor PK 列非 schema 前缀（Doris 建表失败） | extract 末尾把 PRIMARY_KEY 移到 fields[0] | `index_field_extractor.py` |
| 5 | Link api_name 不支持 caller-supplied（中文 display → linkTypeN） | `LinkInput`/`LinkTypeDefCreate` 加 api_name + `_resolve_link_api_name` | `schemas/ontology.py` / `ontology_service.py` |
| 6 | Property api_name 不支持 caller-supplied（中文无源属性 → propertyN） | `PropertyInput`/`PropertyDefCreate` 加 api_name + `_resolve_property_api_name` | 同上 |
| 7 | Dataset 行数 `row_count_estimate` 全空（update_dataset_stats 死代码） | sync_now 用 len(records) 回填 + `refresh_row_count`（Trino 查 Iceberg/联邦）+ refresh route | `index_sync_service.py` / `datasource_service.py` / `routes/datasource.py` |
| 8 | Property backing_mapping 缺 dataset_api_name（ObjectType↔dataset 未关联） | `obj()` post-process 补 `dataset_api_name` | `build_ontology.py` |
| 9 | **range filter 强转 float**（datetime 字符串 `float('2026-04-16...')` 报错 → Trino 降级） | `IndexFilter.min/max` 放宽为 `str|int|float|None`；`_flatten_filter_to_index` 不再 `float()` 强转，原样透传 | `core/schemas/index.py` / `object_query_service.py` |
| 10 | **resolve_backing_table 用 PascalCase api_name 当表名**（Trino 找 `salesconsultant` 而非 `sales_consultant`） | 改用 `managed_dataset_api_name`（snake_case） | `gravitino_registry.py` |
| 11 | **constraint 规则表达式 `value` 未绑定**（`value in [...]` 报 `'value' is not defined`） | constraint 规则 eval 时把 `value` 绑定为 `parameters[rule.target]` | `action_rule_engine.py` |
| 12 | **LeadFollowRecord 无 leadsId/followerId FK 属性**（写后无法按 lead 反查） | build_ontology 补全 Lead/LeadAllocateRecord/ManualOutboundCall/TestDrive/AiOutboundCall 的 FK 列属性 | `build_ontology.py` |
| 13 | **Lead 建档时间与留资时间重复映射 filing_time**（Doris Duplicate column） | 建档时间改映射 `filing_create_time`（新增列）；User phone_brand/device_model 改为 always-null 真实列（修正 4 务实化） | `build_ontology.py` / `marketing_schema.sql` / `seed_marketing.py` |

**新增依赖**：`aiobotocore>=3.7,<4.0`（运行时，bucket 创建）、`faker>=30.0,<40.0`（dev，数据生成）。

### ❌ 未完成 / 后续路标

#### P6 Agent 维度（需 LLM 才能跑）
- 当前 `agent_harness.py` 骨架已就绪（A1-A9 + McNemar + flake-aware + cost cap），但未配置 `AI_MODEL`/provider key 时全维 SKIP。
- 接续：配置 `.env` 的 `AI_MODEL=deepseek:deepseek-chat` + `DEEPSEEK_API_KEY`，重跑 `run_benchmark.py`（不加 `--skip-agent`）。
- 注意：`_text_to_ontology` 用 `/ai/generate` 生成 LoadObjectsRequest JSON 再执行，是简化路径；真实 AG-UI `/ai/agent` 流式路径待接入（A9 多轮对话需 SSE）。

#### W7/W8 AI 产物 postcondition（需 LLM）
- `analyzeTestDrive`/`generateUserProfile` Action 需 LLM 推导生成 5/8 张表数据；未配置时 SKIP。
- 接续：配置 LLM 后重跑写维度，验证 AI 产物表行数 + schema + confidence。

#### W2/W3/W8/W10/W12 写用例补全
- 当前实现 W1/W4/W5/W6/W7/W9/W11；W2(transferLead)/W3(reclaimLead)/W8(generateUserProfile)/W10(write-back NOT NULL)/W12(跨本体联动) 待补。
- W10 回归 #4 需源端 MySQL 写入校验（read-only 直连）。

#### S5 安全用例 + 行级隔离真实修复
- S2/S3 当前全 leak（MVP principal=anonymous 无行级隔离）；S5（AI 产物可见性分级）未实现。
- 这些是倒逼后端 Sprint-3 auth 落地的目标，不是 benchmark 本身的 bug。

#### 性能 Tax% 测量改进
- L2/L4 的 multi-call JOIN 模拟导致 Tax% 虚高。待本体 API 支持 `search_around` 单调用多跳 filter 后，重测可得干净 Tax%。
- 可考虑并发档 `[1,3,7]` 的多轮压测（当前只跑单并发 p95）。

#### METI 循环深化
- 首轮已跑通 + 解释反常（L2/L4 Tax%、安全全 leak、W5/W11 规则缺陷）。
- Test 阶段：针对 W5（simpleeval List 字面量）改实现验证归因；针对 W11（regression #3）验证修复后 422→200。
- Improve 阶段：补 W5 的 enum 规则改用 `enum_values` 字段而非表达式；推进 regression #3/#6 修复。

### 🔧 环境状态（下次会话启动前提）

- **Docker 全栈**：用 `scripts/bootstrap_all.sh` 启动（含 seatunnel-worker + Gravitino metalake/pg-catalog/Iceberg namespace 幂等初始化）。
- **marketing-mysql 容器**：已创建，已 join `gaia_default` 网络（容器名 `marketing-mysql`，root/marketing123）。
- **.env**：`SEATUNNEL_SOURCE_HOST_OVERRIDE=marketing-mysql` / `CATALOG_JDBC_HOST_OVERRIDE=marketing-mysql`。
- **后端**：`.venv/bin/python scripts/start_backend.py`（Popen start_new_session 可靠守护）。
- **MySQL 数据**：`MARKETING_MYSQL_PASSWORD=marketing123 python -m tests.benchmark.marketing.scripts.seed_marketing --drop`（幂等，固定种子）。
- **本体**：`python -m tests.benchmark.marketing.scripts.01_setup_ontology`（幂等，Marketing 本体已注册 39 OT + 282 属性 + 53 link + 9 Action）。
- **同步状态**：15 表全部 synced（见 `.sync_state.json`）；改 build_ontology 后需 PG 硬删本体 + 重 setup + `--resync-doris`。
- **系统初始化自动化**（代码自动，禁止外部命令）：bucket（`ensure_warehouse_bucket`）/ Doris 库（`_get_pool`）/ Iceberg namespace（`ensure_namespace`）/ Gravitino metalake（`bootstrap_all.sh`）。

### 📌 下次会话启动指引

1. 启全栈：`bash scripts/bootstrap_all.sh`（确认 SeaTunnel 集群 2 节点）。
2. 启 marketing-mysql：`docker start marketing-mysql`（已 join 网络）。
3. 启后端：`.venv/bin/python scripts/start_backend.py`（Popen start_new_session 可靠守护；**勿用** nohup/setsid/disown——本环境下不稳）。
4. 确认数据：`curl localhost:8000/ontologies/Marketing/object-types/summary`（应 39 个）。
5. （可选）重跑同步：`.venv/bin/python -m tests.benchmark.marketing.scripts.02_sync_all_tables --resync-doris`。
6. 跑全量 benchmark：`make benchmark-marketing-read`（只读维）或 `.venv/bin/python -m tests.benchmark.marketing.scripts.run_benchmark [--skip-agent]`（全四维）。
7. 看 report：`tests/benchmark/marketing/reports/latest.md`。
8. （配 LLM 后）跑 Agent 维：`.env` 设 `AI_MODEL=deepseek:deepseek-chat` + `DEEPSEEK_API_KEY`，重跑不加 `--skip-agent`。

### ⚠️ 关键陷阱与 API 约定（踩坑记录，避免重蹈覆辙）

#### 属性/对象 api_name 推导规则（极易错）
- `derive_api_name` 优先级：displayName（ASCII）> backing_column（ASCII）> fallback `propertyN`/`linkTypeN`。
- **中文 displayName 不满足 SOURCE_PATTERN**（`^[A-Za-z]...`），必须有 ASCII 锚点。
- 有源属性：靠 `backing_mapping.backing_column`（snake_case）推导 camelCase。例：「门店ID」+ `store_code` → `storeCode`。
- 无源属性（AI 产物 / MVP 主数据）：**必须显式传 `api_name`**（caller-supplied），否则 fallback `propertyN`。已在 `PropertyInput`/`LinkInput` 支持 `api_name` 字段。
- **ObjectType 主键 api_name 从 backing_column 推导**，不是从 display_name。例：Dealership 主键是 `storeCode`（来自 `store_code`），不是 `dealershipId`。provision/sync_now 传 `primary_key="storeCode"`。
- **FK 列属性必须显式建模**：Lead(userId)、LeadAllocateRecord(leadsId/salesConsultantId)、ManualOutboundCall(leadId/userId/originalRecordUrl)、TestDrive(saleId/userId/leadsId/testDriveCarId/originalRecordUrl)、AiOutboundCall(leadsInfoId)。这些 FK 同时也是 link，但**必须作为可查询属性**才能在 read harness 里 filter——光建 link 不够（ObjectQueryService 的 filter 走属性 api_name，不走 link traversal）。
- **TestDrive 的销售 FK 是 `saleId`（来自 `sale_id`），不是 `salesConsultantId`**（物理列名 `sale_id`，推导出 `saleId`）。其他 OT 的销售 FK 是 `salesConsultantId`（来自 `sales_consultant_id`）。

#### ObjectQueryService 查询约定
- `LoadObjectsRequest.object_set.object_type_api_name` 格式是 **`{ontology}.{type}`**（如 `Marketing.Dealership`），内部 `split(".")` 取 ontology + type。只传 `Dealership` 会 NotFoundError。
- `LoadObjectsRequest.properties: list[str]` **必填**（指定返回哪些属性 api_name）。
- 返回的 dict key 是属性 api_name（本体语义层），不是物理列名。

#### DataSource 创建约定
- `create_datasource` 的 `credential_id` 是 **credential 的 UUID id，不是 api_name**。需先 `create_credential` → `get_credential(api_name).id` → 传 UUID。
- `connector_type="mysql"` 会自动注册 Gravitino/Trino catalog（经 `register_catalog_in_trino`）。

#### 物理表共用与黄金真值去重
- **`lead_allocate_record` 与 `lead_distribute_record` 共用物理表 `t_ods_source_data_leads_operation_record`**（seed 时两实体数据写入同一张物理表，共 45000 行 = 30000 allocate + 15000 distribute）。
- `ENTITY_TO_TABLE` 映射（seed_marketing.py）处理此共用。
- **黄金真值 SQL 的重复行**：`lead_allocate_record` 一对多 JOIN 会产生重复行（一个 lead 多条分配记录）。断言引擎 `set_eq` **必须去重**（默认 set 语义去重），否则 jaccard 失真。

#### 环境与进程稳定性
- **vite dev 缓存损坏**：改 .tsx 后偶发 transform 返回空模块（179 字节空 sourceMappingURL），报 "does not provide an export named X"。typecheck/build 仍过。修复：`rm -rf node_modules/.vite && 重启 vite`。
- **后端进程启动不稳**：`setsid`/`nohup &` 在某些 shell 会随命令退出被杀。`pkill -f uvicorn` 可能误杀新进程。可靠方式：`nohup .venv/bin/python -m uvicorn ... & disown`，或 `(setsid ... < /dev/null &)`。
- **后端本体软删**：`DELETE /ontologies/X` 是软删（`deleted_at` 标记），`get_ontology` 默认把软删本体当 NotFound。重新 setup 前需 PG 硬删：`DELETE FROM ontologies WHERE api_name='Marketing'`（CASCADE 清子表）。
- **本体 status=DEPRECATED 时无法创建子资源**：`get_ontology` 把 DEPRECATED 当 NotFound。restore 后要 PATCH `status=ACTIVE`。

#### SeaTunnel 同步约定（写 02_sync_all_tables.py 时参考）
- `create_sync_task` + `start_sync` 触发 SeaTunnel MySQL→Iceberg 同步。
- `source_config={"table": <物理表名>, "schema": "marketing_benchmark"}`，`target_dataset_api_name="marketing.<实体 snake>"`。
- SeaTunnel job 是异步，需轮询 Iceberg 落地（用 Trino `SELECT COUNT(*) FROM iceberg.ontology.<table>`，比 pyiceberg scan_latest 可靠）。
- **必须串行**（避免资源不足），记录进度（断点续传）。
- `recording` 是合成表（fixture 直接 seed，无 SeaTunnel 同步）；AI 产物 13 表无源端（不经同步）。
- **sync_now 默认 limit=10000**，大表（lead_allocate_record 45000）需传 `limit=200_000` 否则 Doris 只有 1 万行。
- **sync-task api_name 必须 camelCase**（`^[a-z][a-zA-Z0-9]+$`，无下划线）：用 `<ot api_name 首字母小写>Sync`（如 `leadSourceSync`），**不是** `lead_sourceSync`。
- **Doris BE 内存敏感**（3GB 限制）：并发 resync 多张大表会 OOM；串行 + 失败重试。主机内存紧张时先 `docker rm` 闲置容器。
- **后端进程守护**：本环境下 `nohup/setsid/disown &` 不稳（工具 shell 退出会杀子进程）；可靠方式是 `subprocess.Popen(start_new_session=True)`（见 `scripts/start_backend.py`）。`pkill -9 -f uvicorn` 会误杀新进程，启动脚本里**不要带 pkill**。
- **本体软删重建**：改 build_ontology 后需重建已注册 OT。最快路径：PG 硬删 `DELETE FROM ontologies WHERE api_name='Marketing'`（CASCADE 清子表）→ 重跑 `01_setup_ontology` → `02_sync_all_tables --resync-doris`（Iceberg 数据还在，只重建 Doris 索引表）。
- **range filter datetime**：`QueryFilter.range` 的 min/max 传 datetime 字符串（`'2026-04-16 09:00:00'`），后端已修复不再 `float()` 强转（修复 #9）。

---

## 十三、一句话总结

> Marketing Benchmark 以汽车门店营销链路为场景，固定种子构造万级数据（单表上限 10 万），写路径走系统 API、读路径直连推导黄金真值，四维（读/写/安全/Agent）paired 跑，每用例 ≤ 60s，产出含 CI / trivial baseline / Oracle / 局限量化的诚实报告，并针对已删航空 benchmark 检出的 7 个后端缺陷设计回归用例。本体建模按命名规则修正 4 处映射不一致，AI 产物嵌入写路径（不独立成维）。
