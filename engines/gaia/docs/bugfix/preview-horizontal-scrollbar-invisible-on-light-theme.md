# 数据预览水平滚动条亮色主题下不可见复盘

**记录时间**: 2026-07-25
**影响模块**: 前端 `index.css`（全局滚动条 + `.preview-table-scroll`）/ `components/PreviewTable.tsx`
**状态**: ✅ 已修复（滚动条改用中性灰语义 token + track 衬底）

---

## 现象

数据源详情页「浏览 Schema」→ 选表 →「数据预览」tab，预览表底部水平滚动条在**亮色主题**下与背景几乎没有区分度，用户感知「跟背景基本上一样」。暗色主题下勉强可见但也不够醒目。

预览表列多（实测一张表 12+ 列，`scrollWidth=2077` vs `clientWidth=482`），水平滚动是高频操作，滚动条不可见直接影响可用性。

## 根因

`index.css` 里滚动条样式有两处问题，预览表 + 全局都中招：

### 1. thumb 用品牌强调色（accent），不是中性灰

```css
/* 修复前 — .preview-table-scroll */
.preview-table-scroll::-webkit-scrollbar-thumb {
  background: var(--color-accent);   /* 亮色 #b8682f 橙色 */
}
.preview-table-scroll::-webkit-scrollbar-track {
  background: transparent;            /* ← 关键问题：无衬底 */
}
```

亮色下 accent 是 `#b8682f`（深橙），在白色 surface 上对比度数值 4.15:1 看似够，但：

- **橙色在白色背景上视觉语义是「链接 / 装饰强调」**，不是用户心智模型里「滚动条」该有的颜色，大脑不会把它识别为可抓取的滚动控件。
- 配合 `track: transparent`，thumb 直接贴在 table 最后一行 / 底部 `bg-bg`（米白 `#f7f6f3`）上，没有独立衬底形成「滚动通道」的视觉边界，看起来就是一根细橙线。

### 2. track 透明 + thumb 太细 + 无内边距

- `height: 8px` + `border-radius: 4px` + 无 `background-clip: padding-box` 留白 → thumb 贴满整个 8px 通道，显得粗糙且细。
- `track: transparent` → 滚动条区域与 table 内容区无视觉分隔。

### 3. 全局滚动条在亮色下完全不可见（连带问题）

```css
/* 修复前 — 全局 */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1);   /* ← 白色半透明，亮色下完全隐形 */
}
/* 没有 track 规则 */
```

`rgba(255,255,255,0.1)` 在暗色下勉强可见，在亮色（白底）下**完全看不见**。这是同类问题，只是预览表因为自己覆盖了 thumb 颜色所以表现稍好，但全局其他滚动区域（sidebar、长列表、对话框等）在亮色下都受影响。

## 最终修复

### 1. 新增滚动条语义 token（`@layer base`，亮暗各定义）

```css
/* :root（暗色） */
--scrollbar-thumb: rgba(255, 255, 255, 0.22);
--scrollbar-thumb-hover: rgba(255, 255, 255, 0.38);
--scrollbar-track: rgba(255, 255, 255, 0.05);

/* .light（亮色） */
--scrollbar-thumb: rgba(60, 70, 84, 0.42);
--scrollbar-thumb-hover: rgba(40, 48, 58, 0.62);
--scrollbar-track: rgba(60, 70, 84, 0.08);
```

**为什么用中性灰而非 accent**：滚动条是导航辅助元素，不应抢视觉焦点。中性灰是 GitHub / VSCode / Linear / macOS 原生滚动条的通行做法，accent 品牌色应留给真正的交互强调（按钮、链接、focus 环）。中性灰在浅/深背景上都有稳定、可预期的对比度。

### 2. 全局滚动条改用 token + 补全 track / hover / Firefox 兼容

```css
* {
  scrollbar-width: thin;                                    /* Firefox */
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: var(--scrollbar-track); }
::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 6px;
  border: 2px solid transparent;        /* 留白，避免贴满通道 */
  background-clip: padding-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
```

### 3. 预览表滚动条复用全局 token，宽度略加大

```css
.preview-table-scroll {
  @apply max-h-[calc(100vh-280px)] min-h-[200px] overflow-auto;
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}
.preview-table-scroll::-webkit-scrollbar { width: 12px; height: 12px; }
/* thumb / track / hover 全部用 var(--scrollbar-*)，不再用 accent */
```

预览表水平滚动频繁（列多），宽度从 8px 提到 12px 提升可抓取性。

## 验证

本地 k3s 环境（数据源 `xiaoling`，预览一张 12 列表）实测，用 Python 解析截图像素确认两个主题下水平滚动条都清晰可辨：

| 主题 | thumb 像素 | 背景 像素 | track 像素 | 结果 |
|------|-----------|----------|-----------|------|
| 亮色 | `(160,163,169)` 中性灰 | `(247,246,243)` 米白 | `(232,231,231)` 浅灰衬底 | ✅ thumb + track 双层清晰可辨 |
| 暗色 | `(74,78,82)` 浅灰 | `(12,17,23)` 深蓝黑 | `(24,29,34)` 略亮衬底 | ✅ thumb + track 双层清晰可辨 |

- `npm run build` 通过（仅已有的 chunk size / dynamic import 警告，与本次改动无关）。
- `PreviewTable` 现有 8 个单测全绿（滚动条是 CSS 视觉细节，jsdom 不渲染 webkit 伪元素，无需也无法单测）。

## 教训

1. **滚动条不要用品牌强调色**：accent 色在数值上对比度可能够，但在用户心智里「橙色 = 链接/装饰」，不会被识别为滚动控件。滚动条是导航辅助，应该用稳定的中性灰，把 accent 留给真正的交互强调。这是 UI 行业共识（GitHub/VSCode/Linear/macOS 原生都这么干）。

2. **track 不能 transparent，要给 thumb 一个衬底**：`track: transparent` 让 thumb 直接贴在内容背景上，失去「滚动通道」的视觉边界。track 用半透明中性色（亮色 `rgba(60,70,84,0.08)` / 暗色 `rgba(255,255,255,0.05)`）形成独立层，thumb 在 track 上才有稳定对比。

3. **改样式要在两个主题都验证，不能只看暗色**：本项目默认暗色主题，开发时容易只在暗色下确认「看得见」就提交。`rgba(255,255,255,0.1)` 这种「暗色勉强可见、亮色完全隐形」的值就是典型反例。任何颜色/对比度改动，亮暗都要实测（本次用截图 + 像素采样量化确认，比肉眼可靠）。

4. **全局滚动条是「隐形基础设施」，改一处全局受益**：本次用户只反馈了数据预览，但排查发现全局 `::-webkit-scrollbar-thumb` 在亮色下也完全不可见。修 token 时一并修全局，sidebar / 长列表 / 对话框等所有滚动区域都受益，避免「每个组件自己覆盖一遍滚动条颜色」的碎片化。

5. **token 要按「语义」而非「色值」分层**：本次新增 `--scrollbar-thumb` / `--scrollbar-track` 语义 token，亮暗各自定义中性灰值。这比直接在组件里写 `rgba(...)` 字面量更易维护——后续要调滚动条风格只需改一处 token。与项目 [frontend-standards S2「禁止硬编码颜色」](../engineer/frontend-standards.md) 一致。
