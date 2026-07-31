# Benchmark 设计原则、最佳实践与避坑指南

> 本文是替换现有 benchmark 前的研究沉淀，综合多份权威来源，作为新 benchmark 设计的依据。
> 现有 `BENCHMARK_DESIGN.md` 的可复用骨架（harness / 双基线 / 固定种子推导预期 / 三层金字塔 / 断言引擎 / 独立清理）仍可沿用，本文聚焦"如何把新 benchmark 做得更严谨"。

---

## 一、权威来源（按相关度排序）

| # | 来源 | 核心贡献 | 与本 benchmark 的关系 |
|---|------|---------|----------------------|
| 1 | **ABC: Agentic Benchmark Checklist** (Zhu et al., 2025, arXiv:2507.02825) | agent benchmark 的 task validity + outcome validity + reporting 三类检查项，评估 10 个主流 agent benchmark 发现误差最高 100% | 直接对应我们的 Agent 维度（Text-to-SQL vs Text-to-Ontology 双模式） |
| 2 | **Gernot Heiser — Systems Benchmarking Crimes** (gernot-heiser.org) | 系统 benchmark 避坑权威清单：selective benchmarking / 显著性 / 几何平均 / 基线 / 校准≠验证 / warmup 等 | 直接对应读/写/安全性能维度 |
| 3 | **How to benchmark: METI loop** (Scherer, INRIA, 2026) | benchmark 是迭代循环（Measure-Explain-Test-Improve），"未测试的结果可假定是错的"；首要敌人是人在环中的自己 | 适用于所有维度，方法论层面 |
| 4 | **Cube — Semantic Layer paired benchmark** (cube.dev, 2026) | 2025 重测 semantic layer vs text-to-SQL：语义层 +17~23pp，模型间无显著差异，paired + McNemar 检验 | 直接论证我们双模式设计的时效性与统计方法 |
| 5 | **Spider / BIRD text-to-SQL 评测** (taoyds/spider, bird-bench) | hardness 分级 + partial matching + execution accuracy（警告假阳性）+ valid efficiency score | 对应读/Agent 维度的评分器设计 |
| 6 | **Fair Benchmarking Considered Difficult** (Raasveldt & Mühleisen, DBTest 2018) | 数据库 benchmark 常见陷阱：测量偏差、缓存预热、单次运行 | 对应读路径性能维度 |
| 7 | **Production LLM eval harness** (dev.to, 2025) | flake-aware / cost-bounded / golden versioning / regression-only gating / multi-metric | 对应 Agent 维度的重试与断言工程化 |

---

## 二、核心原则（必须遵循）

### 原则 1：Benchmark 是迭代循环，不是一次性产物（METI）

> 来源：INRIA METI loop。**"未测试的 benchmark 结果可假定是错的。"**

- **Measure → Explain → Test → Improve** 反复迭代，直到所有显著结果都有被验证的解释
- 最常见错误是"只跑半圈"：measure 一次 + explain 一下就写报告，跳过 Test（验证解释）和 Improve（修 bug）
- **首要是警惕"人在环中的自己"**：我们倾向于用玫瑰色眼镜看结果（已知自己的系统该快）。Test 步骤就是防止自欺
- **记录预期**：跑之前先写下预期结果，跑完对比，强迫诚实面对"意外"
- 落地：新 benchmark 必须有"解释栏"——每条用例结果都要配一个 plausible explanation，且对反常结果必须 Test（如改实现验证归因）

### 原则 2：双准则——既要证提升，也要证不退化

> 来源：Heiser "progressive + conservative criterion"。**只跑对自己有利的场景 = 欺骗。**

- 任何"性能改进"必须同时证明：① 在目标场景显著提升（progressive）；② 在其他场景不显著退化（conservative）
- **禁止 selective benchmarking**：用例集不能偏向系统擅长方向。如果某个能力我们预期做得差，也必须有用例覆盖（既验证短板，也防止 setup bug 被掩盖）
- 禁止"我们挑了代表性子集""典型结果如下"——没有代表性子集这回事
- 落地：用例覆盖矩阵必须显式列出"我们预期表现差的场景"并确保有用例

### 原则 3：Task Validity——任务必须"可解当且仅当具备目标能力"

> 来源：ABC §4.1。这是 agent benchmark 最高频失效点。

- **不存在捷径**：agent 不能靠"什么都不做""列出所有可能答案""dump 全表"通过
  - τ-bench 教训：do-nothing agent 在"故意不可解任务"上得 38%（因成功定义为"状态不变"）
  - τ-bench 教训：substring matching + 全表 dump → 40% 通过
- **不存在不可解任务**（除非故意设且正确处理）：每条任务必须有 Oracle solver 证明可解
- **环境隔离**：agent 不能窥探 ground truth（SWE-Lancer 教训：测试文件虽密码保护但目录可列出、内容可覆盖 → 100% 通过却没解题）
- **任务间状态清理**：每条任务跑前必须 fully clean legacy state（KernelBench 教训：ground truth 残留 GPU 内存可被越界读出）
- **环境冻结**：依赖外部动态资源（持续更新的网站）不推荐；环境在发布时冻结
- 落地：我们的写路径 Action 用例尤其要查"捷径"——能否绕过校验、能否靠空响应通过、postcondition 是否真覆盖了所有变更

### 原则 3.5：写路径只走系统 API，读路径方可直连数据系统（本项目硬约束）

> 这是 task validity 在本项目写路径上的具体落地，也是与一般 benchmark 的关键差异点。

**写路径（产生数据 / 状态变更 / 元数据注册）——只能通过系统 API：**
- ✅ 本体注册、Action 执行、数据源 / 同步任务创建、索引同步触发等**一律走 Ontology REST API**
- ❌ **禁止 benchmark 直连 MySQL / PG / Doris / Iceberg 执行写操作**（INSERT/UPDATE/DELETE/DDL）来产生被测数据或变更状态
- **为什么**：直连写会绕过系统的校验、权限、OCC 乐观锁、Outbox、write-back 反馈环等全部治理逻辑，测出来的"正确性"是假的；更严重的是会制造**捷径**（对应 ABC task validity 失效）——agent / 用例能靠直接写库蒙混通过，而系统的真实缺陷（如权限未生效、OCC 失败）被掩盖
- 这条同时保证了 benchmark 测的是**真实系统行为**，而非"理想环境下应该怎样"

**读路径（验证数据 / 状态 / 推导预期）——可以直接读数据系统：**
- ✅ 直连 MySQL / PG / Doris / Iceberg 做**只读查询**是允许且必需的：
  - **黄金真值推导**：用物理 SQL 从同份种子数据计算预期结果（可复现性最佳实践）
  - **postcondition 校验**：Action 执行后直连物理库验证真实落库（写路径的断言依据）
  - **同步一致性校验**：轮询多源（MySQL↔Iceberg↔Doris）行数 / 校验和
  - **物理基线对比**：性能压测的 raw 基线（物理直连 vs 本体 API 的 Tax%）
- 读不改变状态，不制造捷径，不影响被测系统行为，因此无约束

**唯一例外：源端数据准备（fixture seeding）**
- 数据生成器在 benchmark **开始前**直接写 MySQL 生成源端物理数据，这属于**测试夹具准备**，不是"通过系统操作产生被测数据"，不在禁令范围
- 判据：这些数据是**系统的输入**（源端），不是**系统的输出**（经 API 产生的状态）。系统后续通过同步管道读取它们并落地到 Iceberg/Doris，才是"系统行为"
- 但要注意：源端数据准备好之后，**进入系统的过程**（MySQL→Iceberg→Doris 同步）必须走系统的同步管道 API，不能 benchmark 自己直连 Iceberg/Doris 写

**落地检查清单**（每条写路径用例 / 每个 setup 脚本都要过）：
- [ ] 该写操作是否走系统 API？（POST /ontologies、POST /actions/execute、POST /datasources ...）
- [ ] 是否有直连数据系统的写操作？（若有，要么改走 API，要么论证属于 fixture seeding 例外）
- [ ] postcondition 校验是否只读？（直连读允许）
- [ ] 状态清理脚本例外：cleanup 脚本**可以**直连数据系统删除（它是销毁不是产生，且需 --dry-run/--confirm 双保险，不参与正确性断言）

### 原则 4：Outcome Validity——评测结果必须真反映任务成功

> 来源：ABC §4.2。grading 是 agent benchmark 第二高频失效点。

- **多答案等价性**：同一问题可能有多个正确答案（BIRD 教训：`WHERE Population=(SELECT min(...))` vs `ORDER BY Population LIMIT 1`）。评分必须比较"执行结果集合"而非"SQL 字符串"，且容忍集合等价
- **解析假设显式化**：parser 不能隐式假设答案格式（如"以 Answer: 开头"）。必须在 task description 里显式规定格式，违反格式=真失败
- **避免 enumeration 攻击**：任务设计要防止"列出所有可能答案"蒙混过关（要求精确数量、或唯一性约束）
- **execution accuracy 的假阳性**：不同 SQL 可能返回相同结果（如都返回 NULL/空集）但语义不同。需配合结构化断言，不能只看行数/集合相等
- **LLM-as-Judge 须先验证**：用 LLM 评分前必须 pilot 实验 quantifying 其准确率与自一致性；并处理对抗输入/reward hacking
- **partial credit 优于全有全无**：Spider 按 SQL 组件（SELECT/WHERE/GROUP BY/ORDER BY...）分解打 F1，比整体 0/1 更能定位问题
- 落地：断言引擎要支持"集合等价""ordered 集合""count 区间""结构化 postcondition"，Jaccard 阈值要有依据而非拍脑袋

### 原则 5：统计严谨——平均值不够，要给显著性

> 来源：Heiser + Cube + ABC R.10。

- **绝不能只报原始平均值**：必须给标准差/置信区间。Heiser：系统通常很确定性，std < 0.1%；>1% 就该响警报
- **paired comparison 优于独立比较**：比较两个系统时，同一问题同时跑两个，用 paired test（McNemar for 二项、paired t-test for 连续），统计效力远高于独立样本
  - Cube 用 McNemar 双侧精确检验 + 95% CI，n=99，p≤0.0015
- **几何平均而非算术平均**：跨用例聚合 speedup/比率时用几何平均（Fleming & Wallace）。算术平均对正常化分数无意义
- **相对数 + 绝对数都要给**：只给比率无法 sanity check，可能掩盖"两个都很烂"或"两个都无所谓"
- **校准集 ≠ 验证集**：模型/阈值调参用的数据，绝不能拿来报最终指标（会过拟合，失去预测力）
- **样本量与置信区间**：100 条用例的准确率，若 ground truth 有 11.65% 噪声，排名可能整体错位（ABC 给了 BIRD 的正态近似公式）
- 落地：报告必须含每维度的 n、mean、std/CI；Agent 双模式必须 paired；Tax% 要给区间不只是点估计

### 原则 6：诚实报告——主动暴露局限，而非掩盖

> 来源：ABC §4.3 (R.1-R.13)。ABC 评估的 10 个 benchmark **全部**在 reporting 上有缺陷。

- **开源数据 + harness**（R.1-2），防数据污染（R.3-4，held-out 私有集 + 定期更新）
- **明确构造效度**（R.5-6）：说清楚"测的是什么能力""指标和能力如何对应"
- **量化局限影响**（R.7-9）：当评测有不可避免缺陷（如 LLM judge、ground truth 噪声），要给定性 + 定量影响估计，用采样/不确定性量化
- **报 trivial baseline + 非 AI baseline**（R.12-13）：必须报"什么都不做的 agent"和"人类专家"的成绩做 sanity check。trivial agent >0% 说明评测有漏洞
- **结果解释指南**（R.11）：告诉用户"由于 X 缺陷，不要单看成功率做决策，建议参考 CI"
- 落地：报告模板加"trivial baseline 行""人类/Oracle 行""已知局限及量化影响"三节

### 原则 7：可复现——固定种子 + 冻结环境 + 版本化

> 来源：现有 benchmark 已践行 + Heiser + INRIA。

- **固定随机种子 + 确定性分布**（现有 RANDOM_SEED=42 是对的）：预期结果由同一份种子数据用物理 SQL 推导，不手写
- **环境版本固化**：所有依赖（Doris/Trino/Gravitino/SeaTunnel/LLM 模型）版本写进报告，用 commit hash / image tag
- **golden set versioning**：预期结果随数据/模型版本绑定，drift 时能区分"答案漂移"还是"模型漂移"
- **单次全自动化运行产出最终结果**（INRIA/Tratt）：禁止拼接多次 run 的结果（会引入 stale/cache 误差）。一次 `make` 跑完全流程出报告
- **warmup 迭代不计时**：性能测量前先跑 warmup 填缓存，正式计时只取 warmup 后的样本
- 落地：保留固定种子；新增"环境版本指纹"写入报告头；性能脚本加 warmup 阶段

### 原则 8：性能测量避坑——warmup / cache / 病态点 / 测量偏差

> 来源：Heiser + Raasveldt/Mühleisen + INRIA。

- **warmup**：跑正式测量前先填缓存（Doris 索引、Iceberg manifest、连接池）。warmup 样本不进统计
- **多次运行看 std**：每个并发档跑多次，std >1% 报警，找环境噪声源
- **避免 2^n 病态点**：数据规模/并发档不要只用 2 的幂（易命中 cache 边界/pathological case）。混入 2^n-1、2^n+1、随机点
- **测量偏差源**：CPU 频率缩放（笔记本要插电+固定频率）、turbo boost、其他进程、内存布局（Linux 物理内存分配影响 cache conflict miss）
- **消除 loop overhead**：计时用 noop 版本做对照
- **invert 顺序 + 连续与离散混合**：同一数据点连续跑两次（查不该有的缓存）+ 隔其他点后再跑两次（查该有缓存的失效）
- **throughput 退化 ≠ overhead**：throughput 降 10% 不等于 overhead 10%。要配合 CPU 利用率，按"每比特处理成本"算
- 落地：`load_driver` 加 warmup 参数；并发档从 `[1,5,10]` 改含非 2 幂；记录 CPU 利用率辅助 tax 解释

### 原则 9：评测 harness 工程稳健性

> 来源：Production LLM eval harness + ABC。

- **flake-aware**：LLM 是随机的，单次断言是噪声。跑 N 次取 pass_rate，阈值通过（如 10 次过 8 次）。temperature=0 只是隐藏方差不是测量方差
- **cost-bounded**：全局 + 单测成本上限，一个失控 prompt 不能烧光预算。超限 fail-fast 中止
- **regression-only gating**：CI 按相对 baseline 退化挂，不按绝对阈值（绝对阈值会 bit-rot）
- **multi-metric**：任一单独指标都会撒谎。语义相似 + 结构化断言 + token 成本，三者交叉
- **超时与重试策略要显式**：网络/LLM 超时不能静默吞，要记为 ERROR 而非 FAIL（区分"系统崩"和"答错"）
- **flaky test 检测**：用例若间歇性失败，要标记并排查（常是环境/时序问题，不是被测系统问题）
- 落地：Agent benchmark 的"重试 1 次取最好"要改成"跑 N 次报 pass_rate + 最佳"；加 cost cap；区分 ERROR/FAIL/XFAIL

---

## 三、与现有 benchmark 的差距清单（替换时优先补齐）

基于上述原则，对照现有 `BENCHMARK_DESIGN.md` + 报告 §7 的诚实记录，新 benchmark 应补齐：

### 统计严谨性（差距最大）
- [ ] **缺置信区间/标准差**：现有报告只给 P95 点估计和 pass 计数。新报告每维度给 n/mean/std/CI
- [ ] **Agent 双模式未 paired**：现两条模式独立跑。应同一 question paired 跑，McNemar/paired t 检验
- [ ] **Tax% 无区间**：`(onto_p95 - raw_p95)/raw_p95` 只给点值。应给 bootstrap CI
- [ ] **跨用例聚合用算术平均**：speedup/tax 跨用例聚合应改几何平均
- [ ] **校准/验证未分离**：若用 golden 集调过阈值（如 Jaccard 0.9），最终报分必须用 held-out 集

### Task Validity（写/安全维度有捷径风险）
- [ ] **缺 trivial baseline**：没报"do-nothing agent""随机 agent""全表 dump agent"的成绩。这些 >0% 即暴露评测漏洞
- [ ] **缺 Oracle solver**：没证明每条任务可解。应有确定性 solver 跑通每条
- [ ] **写路径捷径未审计**：Action 能否靠空 payload/绕过校验/postcondition 不全覆盖通过？
- [ ] **状态清理验证**：任务间状态是否真的 fully clean？（现有 cleanup 是手动的，未必每用例间都清）
- [ ] **写路径是否恪守"只走系统 API"**（原则 3.5）：现有 benchmark 已践行但未显式成规，需在新设计中作为硬约束检查——有无 setup 脚本 / 用例 / 断言逻辑直连数据系统写？是否混清了 fixture seeding 例外？

### Outcome Validity（评分器）
- [ ] **Jaccard 0.9 阈值无依据**：为什么 0.9 不是 0.85/0.95？应基于 pilot 数据分布定，或改用更细的 partial credit
- [ ] **execution 假阳性未防**：集合相等可能掩盖语义差异（如都返回空集）。关键用例要加结构化 postcondition
- [ ] **解析脆弱**：`parse_response_keys` 用正则提 ID，对"列出所有可能答案"无防御。应要求精确数量或加唯一性约束
- [ ] **partial credit 缺失**：读路径全有全无。可借鉴 Spider 按 filter/order/limit 组件分解打分

### 性能测量
- [ ] **无 warmup**：`load_driver` 直接计时，首请求含冷启动。应加 warmup 迭代
- [ ] **并发档全 2/5/10**：缺非病态点。加 2^n-1 类点
- [ ] **无 CPU 利用率**：tax 解释不了根因（Heiser：throughput 降≠overhead）。应记 CPU%
- [ ] **多次运行 std 未报**：每档只跑一次 duration。应跑多轮报 std

### 报告诚实度
- [ ] **无构造效度声明**：报告没说清"每个维度测的是什么能力、指标如何对应"
- [ ] **无量化局限影响**：报告 §7 列了缺陷但没量化对分数的影响（ABC R.9）
- [ ] **无结果解释指南**：没告诉用户"因 X 缺陷，别单看成功率"

---

## 四、可直接复用的设计模式（现有 benchmark 已做对的）

这些是现有 benchmark 的亮点，新 benchmark 应保留：

1. **双数据集**（golden 正确性 + perf 性能）——对应 Heiser 的 micro/macro benchmark 区分
2. **固定种子 + 物理 SQL 推导预期**——可复现性最佳实践，杜绝手写预期的人为偏差
3. **三层金字塔诚实策略**（Tier1 可跑 / Tier2 倒逼 / Tier3 北极星 xfail）——避免假绿，对应 ABC 的"不可解任务标 xfail"
4. **双基线对比 + Tax%**——对应 Heiser 的 proper baseline（物理直连 = native 基线）
5. **独立清理 + dry-run/confirm 双保险**——工程稳健性
6. **断言引擎多 kind**（set/ordered_list/count/error/action_rejected/forbidden...）——比单一 0/1 灵活
7. **camelCase↔snake_case 双向匹配**——对物理层(snake)与 API 层(camel)命名差异的务实处理
8. **幂等注册（409 跳过）**——可重复跑，对应可复现性

---

## 五、新 benchmark 设计建议清单（给替换实施用）

基于以上研究，新 benchmark 的设计应包含：

### 评测维度（保留四维，但补齐方法论）
1. **读路径**：加 warmup、多轮 std、非 2 幂并发点、CPU 利用率、Tax% 的 bootstrap CI；读侧可直连数据系统做物理基线与黄金真值推导（原则 3.5）
2. **写路径**：加 trivial baseline（空 payload/绕校验）、Oracle solver 证明可解、postcondition 全覆盖审计；**所有写操作只走系统 API，禁止直连数据系统写**（原则 3.5），除 fixture seeding 与 cleanup 例外
3. **安全**：加 leak 量化（不只 0/1，报 leak calls/total）、trivial attacker baseline
4. **Agent**：改 paired 双模式 + McNemar 检验 + CI；flake-aware 多次跑取 pass_rate；cost cap；trivial agent（do-nothing/dump-all）

### 评分器升级
- 引入 partial credit（按 filter/order/limit/aggregate 组件分解）
- Jaccard 阈值改基于 pilot 数据定，或改更细粒度
- 关键用例加结构化 postcondition（防 execution 假阳性）
- 防 enumeration 攻击（要求精确数量/唯一性）

### 报告模板（对应 ABC R.1-R.13）
- 环境指纹（组件版本 + commit/image tag）
- 构造效度声明（每维度测什么能力、指标对应关系）
- 每维度：n / mean / std / CI / paired 检验 p 值
- trivial baseline 行 + Oracle/human baseline 行
- 已知局限 + 量化影响
- 结果解释指南

### 工程化
- warmup 阶段
- 单次全自动化运行出报告（禁止拼接多次 run）
- golden set versioning（数据/模型版本绑定）
- cost cap + flake-aware（Agent 维度）
- ERROR vs FAIL vs XFAIL 严格区分

---

## 六、一句话总结

> **现有 benchmark 的骨架（harness / 双基线 / 固定种子推导预期 / 三层金字塔 / 断言引擎 / 独立清理）是好的，可沿用；但替换时必须把"统计严谨性""task/outcome validity""诚实报告"三块补齐——这是现有 benchmark 最大的短板，也是 ABC 论文评估的 10 个主流 benchmark 普遍翻车的地方。**
>
> **额外本项目硬约束（原则 3.5）：写路径只走系统 API，禁止直连数据系统写（fixture seeding 与 cleanup 例外）；读路径方可直连。** 这条保证 benchmark 测的是真实系统行为而非理想环境，也防止直连写制造捷径掩盖系统缺陷。
