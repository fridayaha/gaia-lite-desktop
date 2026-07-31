# ADR-003: 使用 RustFS 而非 MinIO 作为 S3 兼容对象存储

| 字段 | 内容 |
| ---- | ---- |
| **状态** | 已采纳 |
| **决策日期** | 2026-05（架构 v5 终稿） |
| **影响层** | `docker-compose.yml`（RustFS 服务）、`IcebergStore`（S3 endpoint 配置）、`SeaTunnelEngine`（S3 sink/source 配置） |
| **相关 ICD** | ICD-03 IcebergStore（storage_location 指向 RustFS S3） |
| **关联文档** | `architecture_plan.md` §1.3 组件版本矩阵、`docker-compose.yml` |

---

## 背景

Iceberg 表格式需要一个 S3 兼容的对象存储作为底层 Durable Storage（存储数据文件 + manifest + metadata）。开发/测试环境需要本地可运行的 S3 实现，生产环境可切换云厂商 S3。

候选方案：

| 方案 | 定位 | 状态 |
| ---- | ---- | ---- |
| MinIO | Go 实现的 S3 兼容对象存储，开源界最流行 | 开源版 2025.12 进入维护模式 |
| RustFS | Rust 实现的 S3 兼容对象存储 | 活跃开发中 |
| Ceph | 企业级分布式存储，提供 S3 接口 (RGW) | 功能全但过重 |
| 云厂商 S3 | AWS S3 / 阿里云 OSS 等 | 生产环境标准选择，但本地开发不可用 |

## 决策

**开发/测试环境使用 RustFS V1（`rustfs/rustfs:latest`），生产环境可切换云厂商 S3。**

### 1. MinIO 开源版已进入维护模式

MinIO 开源版（AGPLv3）于 2025 年 12 月进入维护模式，新功能和企业支持转向商业版 (MinIO Commercial License)。继续依赖开源版意味着：
- 不再获得新功能更新
- 安全补丁仅维护性
- 社区活跃度下降

这是一个明确的弃用信号，需在架构基线确立时就规避。

### 2. RustFS 是活跃的替代品

RustFS 用 Rust 实现，提供完整的 S3 API 兼容性，活跃开发中。其作为 Iceberg 存储底座经本项目验证可用（建表、追加、快照、时间旅行全链路通过）。

### 3. 与 Iceberg / SeaTunnel / pyiceberg 的兼容性已验证

- **Iceberg REST Catalog**（Gravitino 内置 9001）通过 S3 凭证访问 RustFS 读写 metadata 文件 ✅
- **SeaTunnel** Iceberg sink 通过 `fs.s3a.endpoint` 指向 RustFS 写数据文件 ✅（见 ADR-014 S3File connector 配置）
- **Trino** Iceberg connector 通过 S3 凭证读取数据文件 ✅

### 4. 抽象在 S3 API 层，存储底座可替换

Iceberg 表格式本身与具体 S3 实现解耦——只要符合 S3 API，RustFS / MinIO / Ceph / 云 S3 均可。因此开发环境用 RustFS 不锁死生产环境选择，生产可无缝切换云厂商 S3（改 endpoint + 凭证即可）。

## 后果

### 正面

- **规避 MinIO 维护模式风险**：不在架构基线上依赖已弃用的开源项目
- **本地开发零成本**：单容器 RustFS 即可提供完整 S3 能力
- **生产可迁移**：S3 API 抽象层保证存储底座可替换，无厂商锁定

### 负面 / 已知限制

- **RustFS 成熟度低于 MinIO**：作为较新项目，社区规模、生产案例、长期稳定性证明不如 MinIO。生产环境上线前需做压力测试
- **文档相对较少**：遇到问题时社区资源不如 MinIO 丰富
- **Console 已禁用 web**：RustFS 9002 端口的 web console 在本项目配置中禁用，仅用 9000 S3 API

## 替代方案（否决）

| 方案 | 否决原因 |
| ---- | -------- |
| **MinIO 开源版** | 2025.12 进入维护模式，新架构基线不应建立在弃用项目上。仅作为 RustFS 不可用时的临时回退 |
| **Ceph (RGW)** | 功能最全但部署运维过重（需维护 MON/OSD/RGW 多角色），单机开发环境不适用；本项目不需要 Ceph 级别的分布式能力 |
| **直接云 S3** | 生产环境标准选择，但本地开发/CI 无法访问；作为生产部署的目标，与本地 RustFS 互补而非替代 |

## 回归条件

出现以下任一情况，需重新评估存储底座：

1. RustFS 出现影响数据可靠性的严重 bug 且社区无及时修复
2. RustFS 项目停滞（类似 MinIO 进入维护模式）
3. 生产环境要求企业级存储能力（多 AZ 副本、版本化、生命周期策略）且 RustFS 不支持，此时应评估 Ceph 或直接用云 S3

## 修订记录

- **2026-05 初始决策**：架构 v5 终稿选定 RustFS 替代 MinIO
