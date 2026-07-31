# 工具使用约束

## 核心规则

本 skill 通过企微回调触发，走 Hermes API Server 通道。查询用 **`terminal` 工具执行固定脚本**，分两次调用：

1. `scripts/run.py` —— 纯取数（调 API + 算 hints），自动 tee：stdout 给 agent 直读 + 写 `.skill_tmp/tdr.json`（不再重定向、不需 read_file 回读）
2. `scripts/validate_card.py` —— 校验 AI 手写的卡片草稿 + 消毒 + 兜底，stdin 吃步骤 1 run.py 写的 tdr.json

脚本只用 Python 标准库，terminal 工具的系统 python3 即可运行，无第三方依赖。

## 为什么用 terminal + 固定脚本，而不是 execute_code

`execute_code` 权限现已放通（API Server 通道可用）。但本 skill 仍用 `terminal` 跑**固定脚本**，原因：

1. **取数是确定性变换**——`run.py` 固定逻辑，没有需要 `execute_code` 的动态代码。LLM 只传 CLI args。
2. **固定脚本更安全、更确定**：`run.py` / `validate_card.py` 内容固定；`execute_code` 让 LLM 现写 Python，每次写法可能飘、token 更贵、且能跑任意 Python。
3. **卡片校验必须走固定脚本**：`validate_card.py` 的校验/消毒/兜底规则是反幻觉的关键——若用 `execute_code` 让 LLM 自校验，等于让幻觉源自己判自己，失去兜底意义。
4. **CLI args 天生安全**：argparse 解析，无需 LLM 处理 URL 编码/shell 转义。

> 卡片 JSON 草稿由 AI 手写（这是本 skill 的核心能力——AI 自主选形态），但**校验必须交给固定的 `validate_card.py`**，不能由 AI 自检。

## terminal 调用模板

### 步骤 1：取数（+ 并行加载卡片格式）

```bash
python3 {{profile_skills_dir}}/test-drive-report/scripts/run.py \
  [--customer-name <客户名>] \
  [--customer-phone <手机号/尾号>] \
  [--drive-date YYYY-MM-DD]
# sales_phone 由 run.py 自动从 USER.md「业务手机号」读取，不传 CLI（不接受对话指定）
```

- `--customer-name` / `--customer-phone` / `--drive-date` 可选（缺失的不传）；`sales_phone` 自动读、不传。
- 中文客户名直接传，无需编码（argparse + urllib.parse.urlencode 自动处理）。
- **run.py 自动 tee**：stdout 直接给你读（决定卡片形态），同时写 `.skill_tmp/tdr.json`（供步骤 2 校验器 stdin）。**不需 `> tdr.json` 重定向，不需 read_file 回读**——直接读 terminal stdout 的 `items`/`hints`。
- **同一次往返并行调** `skill_view(file_path="references/card-format.md")` 加载卡片格式（与 run.py 互不依赖，并行省一次往返）。
- 输出 `items` 已截到 6 条（卡片上限）；`total` = 全量命中数。
- ⚠️ API 只支持到日期，不支持时段过滤——不传 `--time-of-day`（已移除）。"上午/下午"按当天日期查，卡片用 🕐 标注区分。

### 步骤 2：校验 + 兜底

```bash
python3 {{profile_skills_dir}}/test-drive-report/scripts/validate_card.py \
  --card-json '<你手写的卡片 JSON 草稿>' < "${HERMES_HOME:-$HOME}/.skill_tmp/tdr.json"
```

- `--card-json`：你按 SKILL.md「卡片选择指南」手写的 `{"msgtype":"template_card",...}` 草稿。
- stdin：步骤 1 run.py 自动写的 `${HERMES_HOME:-$HOME}/.skill_tmp/tdr.json`（提供 items 供校验数据真实性）。
- stdout：最终卡片 JSON（消毒后）或纯文本——**原样透传给用户**。

## 失败处理

- `run.py` 输出 `{"ok":false}` → 取数失败，直接回复"试驾报告查询失败，请稍后重试或联系管理员"，**不进步骤 2**。
- `run.py` 输出 `items:[]` → 0 命中，直接回复"没找到…要不要换个日期/尾号"，**不进步骤 2**。
- `validate_card.py` 草稿无效 → 自动回退到真实 items 建的兜底卡，仍输出合法卡片 JSON——透传即可。

**严禁编造数据**（见 SKILL.md Gotchas 14）——校验器会拦幻觉 url，但 AI 仍应自律，失败就如实转述。
