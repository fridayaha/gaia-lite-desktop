# Secret Scanner 部署方案设计

版本：v0.1 | 日期：2026-05-29 | 状态：P2-2B BetterleaksScannerAdapter 已实现（v1.3.1 真实 CLI 实测通过）。P2-2C GitleaksScannerAdapter 已实现。

---

## 1. 背景

Hub 接入两种 secret scanner 作为外部扫描器：

| Provider | 角色 | 状态 |
|----------|------|:---:|
| Betterleaks v1.3.1 | primary | ✅ P2-2B 已实现 |
| Gitleaks v8.30.1 | fallback | ✅ P2-2C 已实现 |

两者均通过 `subprocess.run()` 调用 CLI 二进制，扫描 HubItemVersion 写入临时目录的内容。需要明确部署方式，确保工具可用、安全可控、不引入额外运维复杂度。

---

## 2. 当前部署模型

当前采用**方案 A：随 Hub API 进程安装 CLI**。

```
Hub API 进程
├── BuiltInRuleScanner（内置，必跑）
├── BetterleaksScannerAdapter
│   └── subprocess.run("betterleaks dir tmpdir -f json -r report.json --redact")
└── GitleaksScannerAdapter
    └── subprocess.run("gitleaks dir tmpdir --report-format=json --report-path=report.json --redact")
```

特点：
- Hub API 直接通过 subprocess 调用 CLI
- 扫描在 Hub API 进程内完成
- 不新增网络调用
- 不需要独立服务
- 临时目录自动清理

---

## 3. 四种部署方案对比

### 方案 A：随 Hub API 容器/主机安装 CLI（推荐 P1）

```
Hub API 镜像/主机
├── Python 3.12 + FastAPI
├── betterleaks（/usr/local/bin/betterleaks）
└── gitleaks（/usr/local/bin/gitleaks）
```

| 维度 | 评价 |
|------|------|
| 落地难度 | ⭐ 最低 |
| 运维复杂度 | ⭐ 最低 |
| 性能影响 | 扫描消耗 Hub API CPU/IO，但 PoC 阶段可接受 |
| 隔离性 | 低（共享 Host CPU/IO） |
| 适用阶段 | P1/P2 |
| 安全边界 | CLI 失败不能导致 500 |

**部署要求**：
- Hub API 镜像或主机中安装 betterleaks / gitleaks
- 配置 `HUB_BETTERLEAKS_BIN` / `HUB_GITLEAKS_BIN` 指向二进制路径
- 设置 `HUB_BETTERLEAKS_TIMEOUT_SECONDS` / `HUB_GITLEAKS_TIMEOUT_SECONDS`
- 默认 disabled（`HUB_BETTERLEAKS_ENABLED=false`，`HUB_GITLEAKS_ENABLED=false`）

**当前已实现配置**：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HUB_BETTERLEAKS_ENABLED` | `false` | 默认禁用 |
| `HUB_BETTERLEAKS_BIN` | `betterleaks` | 二进制路径 |
| `HUB_BETTERLEAKS_TIMEOUT_SECONDS` | `30` | 超时秒数 |
| `HUB_BETTERLEAKS_CONFIG` | `` | 可选自定义 config |
| `HUB_GITLEAKS_ENABLED` | `false` | 默认禁用 |
| `HUB_GITLEAKS_BIN` | `gitleaks` | 二进制路径 |
| `HUB_GITLEAKS_TIMEOUT_SECONDS` | `30` | 超时秒数 |

**风险**：
- 多实例部署时每个实例都需安装工具
- 工具版本需统一（pinned to v1.3.1 / v8.30.1）
- 较大 manifest 扫描可能消耗显著 CPU
- CLI 失败 → `scanner_error` finding，不导致 500

### 方案 B：扫描 Worker（P2/P3）

```
Hub API                     Scan Worker
├── 提交扫描任务 ──────────> ├── betterleaks scan
├── 查询扫描结果 <────────── ├── gitleaks scan
└── 写 ScanReport            └── 回写 finding
```

| 维度 | 评价 |
|------|------|
| 落地难度 | ⭐⭐⭐ 需要队列 + Worker + 状态管理 |
| 运维复杂度 | ⭐⭐⭐ 新增 Worker 部署 |
| 性能影响 | 低（扫描与 API 分离） |
| 隔离性 | 高（独立 Worker 进程/容器） |
| 适用阶段 | P2/P3（扫描量大或文件大时） |

**需要**：
- 任务队列（Redis/Celery 等 — 当前禁止）
- Worker 进程
- 任务状态管理
- 对象存储（临时文件传递）
- 扫描结果回写机制

**当前不做**。

### 方案 C：工具容器 Sidecar / Job（P3+）

```
Hub API Pod                  Sidecar Container
├── 共享临时卷 <────────────> ├── betterleaks
└── 读取 report JSON          └── gitleaks
```

| 维度 | 评价 |
|------|------|
| 落地难度 | ⭐⭐⭐⭐ 依赖编排系统 + 卷管理 |
| 运维复杂度 | ⭐⭐⭐ Sidecar 生命周期管理 |
| 性能影响 | 低（隔离执行） |
| 隔离性 | 最高（独立容器） |
| 适用阶段 | P3+（Kubernetes / 平台部署） |

**风险**：
- 安全边界复杂（共享卷权限、临时文件脱敏）
- 依赖编排系统（K8s Job/InitContainer/Sidecar）
- 当前不建议 P1 做

### 方案 D：平台统一扫描服务（P2+）

```
Hub API                     Platform Scanner Service
├── CommonScannerAdapter ──> ├── betterleaks
└── 接收 normalized finding   ├── gitleaks
                              └── semgrep / osv / ...
```

| 维度 | 评价 |
|------|------|
| 落地难度 | ⭐⭐ 需平台扫描服务 API |
| 运维复杂度 | ⭐ 复用平台能力 |
| 性能影响 | 低（外部服务） |
| 隔离性 | 高 |
| 适用阶段 | P2+（如平台已有统一扫描服务） |

**前提**：
- 平台已有统一扫描服务 API
- 接口已知且稳定
- Hub 通过 CommonScannerAdapter 对接

---

## 4. 推荐方案

| 阶段 | 方案 | 说明 |
|:---:|------|------|
| **P1（当前）** | 方案 A | 随 Hub API 安装 CLI，最快落地 |
| P2 | 方案 B 或 D | 扫描量增大时拆 Worker，或复用平台统一扫描服务 |
| P3+ | 方案 C | Kubernetes 编排下考虑 Sidecar/Job |

**当前决策**：
- P1 采用方案 A，BetterleaksScannerAdapter 和 GitleaksScannerAdapter 已基于此模型实现
- 暂不实现 Worker / Sidecar / 平台统一扫描对接
- 如平台已有统一扫描服务，优先走方案 D（通过 CommonScannerAdapter 对接）

---

## 5. 工具版本管理

| 工具 | 推荐版本 | 安装方式 |
|------|----------|----------|
| Betterleaks | v1.3.1 | `go install github.com/zricethezav/betterleaks/cmd/betterleaks@v1.3.1` |
| Gitleaks | v8.30.1 | `go install github.com/gitleaks/gitleaks/v8@v8.30.1` |

**约束**：
- 不内置二进制到 Hub 仓库
- 不打包分发（license 未法务确认）
- 部署时由运维安装到 PATH
- 版本通过 `betterleaks --version` / `gitleaks version` 检测

---

## 6. 安全约束

无论采用哪种部署方案：

| 约束 | 说明 |
|------|------|
| 默认 disabled | 需显式 `HUB_*_ENABLED=true` |
| 不记录 secret 原文 | Adapter 强制剥离 Secret/Match/Line |
| 不记录 raw stdout/stderr | 仅提取 JSON report |
| scanner_error 不 500 | 生成低 severity finding |
| 临时目录自动清理 | `tempfile.TemporaryDirectory` |
| 超时控制 | `HUB_*_TIMEOUT_SECONDS` |
| 不联网 validation | Betterleaks `--validation` opt-in，默认不开启 |

---

## 7. 配置完整清单

### Betterleaks

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HUB_BETTERLEAKS_ENABLED` | `false` | 启用 Betterleaks |
| `HUB_BETTERLEAKS_BIN` | `betterleaks` | 二进制路径（PATH 查找） |
| `HUB_BETTERLEAKS_TIMEOUT_SECONDS` | `30` | subprocess 超时秒数 |
| `HUB_BETTERLEAKS_CONFIG` | ``（空） | 可选自定义 config 文件 |

### Gitleaks

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HUB_GITLEAKS_ENABLED` | `false` | 启用 Gitleaks fallback |
| `HUB_GITLEAKS_BIN` | `gitleaks` | 二进制路径（PATH 查找） |
| `HUB_GITLEAKS_TIMEOUT_SECONDS` | `30` | subprocess 超时秒数 |

### 两者同时启用

Betterleaks 先执行，Gitleaks 后执行。

```
扫描顺序:
1. BuiltInRuleScanner（内置，必跑）
2. BetterleaksScannerAdapter（如启用）
3. GitleaksScannerAdapter（如启用）
```

本阶段不做 finding dedup。两者同时启用可能产生重复 finding（后续再做去重）。

---

## 8. 与现有架构的关系

```
CompositeScanner
├── BuiltInRuleScanner（内置）
├── BetterleaksScannerAdapter  ← 方案 A
└── GitleaksScannerAdapter    ← 方案 A

scan_service._build_externals()
  1. Betterleaks（如 HUB_BETTERLEAKS_ENABLED=true）
  2. Gitleaks（如 HUB_GITLEAKS_ENABLED=true）
```

扫描结果统一通过 `FindingNormalizer` 归一化为内部格式，写入 `ScanFinding` / `ScanReport`。

---

## 9. 后续路线

| 阶段 | 内容 |
|:---:|------|
| P2-2B | ✅ BetterleaksScannerAdapter 已实现（方案 A） |
| P2-2C | ✅ GitleaksScannerAdapter 已实现（方案 A） |
| P2-2D | finding dedup（两者同时启用时去重） |
| P2-3 | Semgrep CLI Adapter（方案 A 或 D） |
| P3 | 方案 B 或 C（Worker/Sidecar） |
