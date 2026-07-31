# V3 架构图 — 源码与生成脚本

本目录维护 `docs/architecture-v3.html` 的图表源码与生成器。修改图表后重新生成 HTML。

## 文件

- `src/*.mmd` — 15 张 mermaid 图源码（m1…m12，含 m3a/3b/4a/4b/6a/6c 子图）
- `gen.py` — 生成器：读取 `.mmd` 源 + 内联 mermaid 库，组装 `docs/architecture-v3.html`（运行时渲染、离线可用、点击缩放）
- `mermaid.min.js` — mermaid 库（**不入库**，生成前下载，见下）

## 重新生成

```bash
cd docs/architecture-v3-src

# 1) 下载 mermaid 库（仅首次或升级版本时）
curl -sL https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js -o mermaid.min.js

# 2) 生成 HTML
python3 gen.py
# → 写出 ../architecture-v3.html
```

## 改图流程

1. 编辑 `src/<name>.mmd`（`<name>` 对应 `gen.py` 里 `diagram()` 调用的 id）
2. 新增/删除图：在 `gen.py` 的 `body` 列表加 `diagram('mN','标题')`，并在 `src_names` 列表加该 id
3. `python3 gen.py` 重新生成
4. 浏览器打开 `docs/architecture-v3.html` 验证

## 渲染方式说明

- **运行时渲染**（当前方案）：mermaid 在浏览器内执行，用本机中文字体，无文字遮挡；库内联进 HTML，离线可用。
- 早期尝试过 mermaid CLI（mmdc）预渲染静态 SVG，但无头 chrome 字体度量与真实浏览器不一致导致中文标签遮挡，已弃用。
- 缩放交互：点击图表/「放大查看」全屏，滚轮缩放 / 拖拽平移 / 双击复位 / Esc 关闭。
