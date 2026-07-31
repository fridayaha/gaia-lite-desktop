# 出厂预制资产（assets/）

随平台版本一起发布的预制资产目录。与 `samples/`（纯学习样例）职责区分：本目录是**出厂自带、可被平台加载注册**的正式资产，用户开箱即用。

## 目录结构

```
assets/
├── skills/              # 技能
│   └── <industry>/      # 行业
│       └── <skill-name>/
│           ├── manifest.json
│           ├── SKILL.md
│           └── README.md (可选)
├── agent-templates/     # 智能体模版（人设 + 技能组合 + 模型配置）
│   └── <industry>/<template-name>/template.json
├── mcp/                 # MCP 服务定义/注册（连接配置，非服务实现代码）
│   └── <industry>/<mcp-name>/manifest.json
└── tools/               # 预留：其他可扩展资产（连接器、知识包等）
```

**切分原则**：先按资产类型（skills / agent-templates / mcp / ...），再按行业。平台按类型加载（每类一个 loader 扫一个根），行业是过滤维度而非存储分区。

## 行业枚举

目录名固定为以下值，新增行业需同步更新本枚举与 manager 行业过滤下拉：

| 目录名 | 行业 |
|--------|------|
| `general` | 通用 / 跨行业 |
| `automotive` | 汽车 |
| `education` | 教育 |
| `healthcare` | 医疗 |
| `finance` | 金融 |

> 跨行业资产只在路径里放一份，用 manifest 的 `industries` 字段声明多归属（数组），靠字段过滤解决，不拷贝多份。

## 资产规范

每个资产一个目录，kebab-case 命名（如 `credential-checker`），至少含 `manifest.json`：

```json
{
  "name": "credential-checker",
  "version": "1.0.0",
  "industries": ["general"],
  "engine": ["HERMES"],
  "description": "...",
  "config_params": [...]
}
```

- `version`：资产自身版本，与平台 `VERSION` 解耦，独立递增。
- `industries`：归属行业数组，至少一项，`general` 表示通用。
- 主体文件按类型不同：skill → `SKILL.md`；template → `template.json`；mcp → 连接配置。

## 与版本发布的关系

`assets/` 在仓库内，随 git tag 自然发版，无需额外打包。manager 启动 / 升级时扫描 `assets/<type>/<industry>/*/manifest.json`，按 `name + version` **幂等 upsert** 进 DB（同版本已存在则跳过，不覆盖用户改动）。出厂资产是只读 canonical 源；用户"安装"到实例时再 copy / 引用，避免 fan-out 多份拷贝（见 [[todo-skill-symlink-sharing]] 待办）。
