# 文档模板

> 每篇 guide 文档复制本模板，按象限填写。四象限写法差异见 `doc-engineering-master-plan.md` §5.2。

```markdown
---
# frontmatter（可选，VitePress 自动取标题）
---

# <标题>

> **象限**：Explanation / Tutorial / How-to / Reference
> **读者**：决策者 / 集成开发者 / 贡献者
> **状态**：✅ 已实现 / 🟡 部分（注明缺口）
> **代码核实**：YYYY-MM-DD
> **预计阅读**：X min

## TL;DR

<3 行结论先行：这是什么 / 解决什么问题 / 何时用>

## <主体>

<!--
按象限写法：
- Tutorial：「本教程你将完成 X」+ 手把手步骤，可复制命令。禁止解释为什么（放概念链接）。
- How-to：「如何实现 X」+ 步骤导向，假设已会基础。禁止铺垫概念、讲设计权衡。
- Reference：端点/配置名 + 纯事实表格，无叙事。禁止讲故事。
- Explanation：「为什么这样设计」+ 有观点、有取舍、有 alternatives。禁止罗列 API、step-by-step。
-->

## 深入

- 设计决策：[ADR-0XX](/architecture/adr-0xx-...)
- 接口契约：[ICD-0X](/architecture/icd-0x-...)
- 实现状态：[implementation-status](/architecture/implementation-status)
- 源码：`src/ontology/...`
```

## 写作检查清单

提交前逐项确认：

- [ ] header 完整（象限/读者/状态/代码核实日期/预计阅读）
- [ ] TL;DR 3 行，结论先行
- [ ] 状态经代码核实（不只是 implementation-status 写了）
- [ ] 只描述已实现的东西（未实现特性不写）
- [ ] 与 implementation-status 不冲突（冲突则以代码为准并同步修）
- [ ] 链接有效
- [ ] 用户可见文案不暴露实现细节（how-to 用行为语言）

## 四象限速查

| 象限 | 开头 | 主体 | 禁止 |
|------|------|------|------|
| Tutorial | "本教程你将完成 X" | 手把手步骤 | 解释为什么 |
| How-to | "如何实现 X" | 步骤导向 | 铺垫概念 |
| Reference | 端点/配置名 | 纯事实表格 | 讲故事 |
| Explanation | "为什么这样设计" | 观点+取舍 | 罗列 API |
