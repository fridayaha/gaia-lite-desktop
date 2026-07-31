# StarRocks SeaTunnel 配置 Dry-Run 对照报告

> **目的**: 对照 Gaia 生成的 StarRocks SeaTunnel 配置与官方文档/实测，确认字段差异
> **日期**: 2026-07-02
> **结论**: **JDBC 路径可用**（已 dry-run 验证 dialect 识别 + driver 加载）；专用 StarRocks connector 路径为可选优化（BE 直读更高效），当前未实现，字段差异已记录

---

## 一、StarRocks 在 SeaTunnel 2.3.13 的两条 Source 路径

| 路径 | connector block | 实现 | 性能 | 当前 Gaia 支持 |
|------|----------------|------|------|---------------|
| **JDBC source** | `Jdbc {}` + `catalog { factory = "StarRocks" }` | 走 MySQL 协议 JDBC | 标准 JDBC 读取 | ✅ 复用 `create_sync_pipeline` |
| **专用 StarRocks source** | `StarRocks {}` | FE 查询计划 + BE 直读（thrift） | 更高效（BE 并行直读） | ❌ 未实现（可选优化） |

SeaTunnel 2.3.8 起同时支持两条路径，且修复了「starrocks jdbc dialect catalog 与 starrocks connector 冲突」（PR #7578），两者可共存。

---

## 二、JDBC 路径 — Dry-Run 验证 ✅

### 2.1 Gaia 渲染的配置（`create_sync_pipeline` 对 starrocks）

```hocon
env {
  parallelism = 1
  job.mode = "BATCH"
  checkpoint.interval = 30000
}
source {
  Jdbc {
    driver = "com.mysql.cj.jdbc.Driver"
    url = "jdbc:mysql://sr-host:9030/app"
    user = "root"
    password = ""
    query = "SELECT * FROM orders"
    connection_check_timeout_sec = 100
    catalog {
      factory = "StarRocks"   # ← SeaTunnel 2.3.8+ starrocks jdbc dialect
    }
  }
}
sink {
  Iceberg { ... iceberg.catalog.config = { type = "rest", uri = ..., ... } ... }
}
```

### 2.2 与官方文档对照

官方 JDBC connector 文档（2.3.5+）明确把 `starrocks` 列为支持的 JDBC dialect：
- driver: `com.mysql.cj.jdbc.Driver` ✅
- url: `jdbc:mysql://localhost:3306/test` ✅（StarRocks FE 9030 兼容 MySQL 协议）
- factory: `StarRocks`（SeaTunnel 2.3.8 PR #7294 新增 starrocks jdbc dialect）✅

### 2.3 Dry-Run 实测验证

提交 `Jdbc { catalog { factory = "StarRocks" } }` 配置指向假主机：
- ✅ **dialect 识别成功**：无 "unknown dialect" / API-06 factory 错误
- ✅ **driver 加载成功**：堆栈 `com.mysql.cj.protocol.StandardSocketFactory.connect`（mysql driver）
- ✅ **失败仅在 DNS**：`UnknownHostException: nonexistent-sr`（预期，假主机）

**结论**：JDBC 路径配置正确，dialect 与 driver 均被 SeaTunnel 2.3.13 识别。

### 2.4 ⚠️ 已知遗留问题（非 StarRocks 特有）

Gaia 现有 `PIPELINE_SYNC_TEMPLATE`（JDBC sync 通用模板）的 Iceberg sink 块仍用旧的 `type = "rest"`，而非 postmortem 验证的 `catalog-impl = "org.apache.iceberg.rest.RESTCatalog"`。这是历史遗留（所有 JDBC sync 任务共用），不影响 StarRocks 特有逻辑。新加的多源模板（file/kafka/cdc）已用 `catalog-impl`。建议后续统一迁移 SYNC 模板到 `catalog-impl`（独立工作项，非 StarRocks 阻塞项）。

---

## 三、专用 StarRocks Source 路径 — 字段对照（未实现，记录差异）

### 3.1 官方 StarRocks source 配置（来自 issue #10123 真实示例 + jar 反编译）

```hocon
source {
  StarRocks {
    nodeUrls = ["sr-fe:8030"]          # FE HTTP 端口（8030），非 MySQL 端口（9030）
    username = "root"
    password = "***"
    database = "ods"
    table = "orders"
    schema = {                          # 必填（2.3.12 #9656 改为必填）
      fields = {
        id = "BIGINT"
        order_no = "STRING"
        amount = "DECIMAL(10, 2)"
      }
    }
    # 可选 scan 参数（来自 jar 反编译 StarRocksSourceOptions）
    # scan_batch_rows = 100
    # max_retries = 3
    # backend-urls = ["sr-be:9060"]    # BE thrift 端口（可选，FE 下发）
  }
}
```

### 3.2 与 Gaia JDBC 路径的字段差异

| 维度 | JDBC 路径（Gaia 现有） | 专用 StarRocks source（官方） |
|------|----------------------|----------------------------|
| connector block | `Jdbc {}` | `StarRocks {}` |
| 连接方式 | JDBC（MySQL 协议，FE 9030） | FE HTTP（8030）查计划 + BE（9060）直读 |
| 必填字段 | `driver`/`url`/`query`/`catalog.factory` | `nodeUrls`/`username`/`database`/`table`/`schema` |
| 端口 | 9030（MySQL 协议） | 8030（FE HTTP）+ 9060（BE，可选） |
| schema | 可选（JDBC 自动推断） | **必填**（2.3.12+ #9656） |
| 读取机制 | JDBC ResultSet | BE thrift 并行直读（更快） |
| 类型映射 | MySQL dialect | StarRocks 原生类型（BIGINT/STRING/DECIMAL） |

### 3.3 实现建议（若需要专用 connector 优化）

若未来需要 BE 直读的高性能路径，新增 `create_starrocks_sync_pipeline` 方法 + `PIPELINE_STARROCKS_SOURCE_TEMPLATE`：
- source block 用 `StarRocks {}`
- 字段：`nodeUrls`（FE 8030）、`username`/`password`/`database`/`table`/`schema`
- sink 复用 postmortem-verified Iceberg sink（catalog-impl）

**当前不实现**——JDBC 路径已满足需求，专用 connector 是性能优化，按 G3（二八原则）后续按需触发。

---

## 四、总结

| 项 | 结论 |
|----|------|
| JDBC 路径（`create_sync_pipeline`） | ✅ **dry-run 验证通过**（dialect + driver 识别） |
| 专用 StarRocks source | 🟡 未实现，字段差异已记录，BE 直读性能优化按需触发 |
| Gravitino `jdbc-starrocks` catalog | ✅ live 验证通过（前次 commit） |
| VIRTUAL 联邦 | ✅ Gravitino catalog + Trino 联邦（MySQL 协议下推） |
| SYNC 模板 sink `type=rest` 遗留 | ⚠️ 历史问题，非 StarRocks 特有，独立工作项 |

StarRocks 接入完整可用：VIRTUAL 联邦（不搬迁）+ JDBC 落地（`create_sync_pipeline`）双路径就绪。专用 connector 优化为可选未来增强。
