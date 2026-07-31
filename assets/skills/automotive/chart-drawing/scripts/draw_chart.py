#!/usr/bin/env python3
"""draw_chart.py — matplotlib 图表绘制脚本

stdin 读 JSON config → matplotlib 绘图 → PNG → stdout 相对路径。

支持图表类型：bar / line / pie / scatter / area
只用 Python 标准库 + matplotlib（引擎镜像需含 matplotlib）。

用法：
  echo '{"chart_type":"bar",...}' | python3 draw_chart.py
"""

import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")  # 非交互后端（无需 display）
import matplotlib.font_manager as _fm
import matplotlib.pyplot as plt
import numpy as np

# ── 常量 ──────────────────────────────────────────────────────

DEFAULT_COLORS = [
    "#FF6B6B",
    "#4ECDC4",
    "#45B7D1",
    "#FFA07A",
    "#98D8C8",
    "#F7DC6F",
    "#BB8FCE",
    "#85C1E9",
]

CHART_TYPES = {"bar", "line", "pie", "scatter", "area", "radar"}

# CJK 字体：用 fontManager.addfont 直接加载文件路径（绕过 fontconfig，
# 引擎镜像是 python:3.11-slim 无 fc-list/fontconfig，rcParams 按名字找不到系统字体）。


for _fp in [
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]:
    if os.path.exists(_fp):
        _fm.fontManager.addfont(_fp)

plt.rcParams["font.sans-serif"] = [
    "WenQuanYi Micro Hei",
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "WenQuanYi Zen Hei",
    "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


# ── 辅助函数 ──────────────────────────────────────────────────


def _color(idx: int, series: dict) -> str:
    c = series.get("color") if isinstance(series, dict) else None
    return c or DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]


# ── 图表渲染器 ────────────────────────────────────────────────


def _render_bar(ax, series_list, categories):
    n_cat = len(categories)
    n_ser = len(series_list)
    x = np.arange(n_cat)
    width = 0.7 / n_ser
    for i, s in enumerate(series_list):
        offset = (i - n_ser / 2 + 0.5) * width
        ax.bar(
            x + offset,
            s["values"],
            width,
            label=s.get("name", f"Series {i + 1}"),
            color=_color(i, s),
        )
    ax.set_xticks(x)
    ax.set_xticklabels(categories)


def _render_line(ax, series_list, categories):
    x = np.arange(len(categories))
    for i, s in enumerate(series_list):
        ax.plot(
            x,
            s["values"],
            marker="o",
            markersize=5,
            label=s.get("name", f"Series {i + 1}"),
            color=_color(i, s),
            linewidth=2,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(categories)


def _render_pie(ax, series_list, categories):
    vals = series_list[0]["values"]
    colors = [DEFAULT_COLORS[i % len(DEFAULT_COLORS)] for i in range(len(vals))]
    ax.pie(
        vals,
        labels=categories,
        autopct="%1.0f%%",
        colors=colors,
        startangle=90,
    )
    ax.axis("equal")


def _render_scatter(ax, series_list, categories):
    x = np.arange(len(categories))
    for i, s in enumerate(series_list):
        ax.scatter(
            x,
            s["values"],
            label=s.get("name", f"Series {i + 1}"),
            color=_color(i, s),
            s=60,
        )
    ax.set_xticks(x)
    ax.set_xticklabels(categories)


def _render_area(ax, series_list, categories):
    x = np.arange(len(categories))
    for i, s in enumerate(series_list):
        color = _color(i, s)
        ax.fill_between(
            x, s["values"], alpha=0.35, color=color, label=s.get("name", f"Series {i + 1}")
        )
        ax.plot(x, s["values"], color=color, linewidth=2)
    ax.set_xticks(x)
    ax.set_xticklabels(categories)


def _render_radar(ax, series_list, categories):
    # polar 投影：N 个维度均匀分布圆周，闭合首尾连成多边形
    n_cat = len(categories)
    angles = [n / float(n_cat) * 2 * math.pi for n in range(n_cat)]
    angles += angles[:1]  # 闭合
    for i, s in enumerate(series_list):
        vals = list(s["values"]) + [s["values"][0]]  # 闭合，不污染原 list
        color = _color(i, s)
        ax.plot(
            angles,
            vals,
            color=color,
            linewidth=2,
            label=s.get("name", f"Series {i + 1}"),
        )
        ax.fill(angles, vals, color=color, alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories)


RENDERERS = {
    "bar": _render_bar,
    "line": _render_line,
    "pie": _render_pie,
    "scatter": _render_scatter,
    "area": _render_area,
    "radar": _render_radar,
}


# ── 主逻辑 ────────────────────────────────────────────────────


def draw_chart(config: dict) -> tuple[str | None, str | None]:
    """绘图 → 保存 PNG → 返回 (相对路径, None) 或 (None, 错误信息)。"""
    chart_type = config.get("chart_type", "")
    if chart_type not in CHART_TYPES:
        return None, f"unsupported chart_type: {chart_type}. Supported: {sorted(CHART_TYPES)}"

    series = config.get("series")
    if not series or not isinstance(series, list):
        return None, "series is required and must be a non-empty list"
    for i, s in enumerate(series):
        vals = s.get("values")
        if not vals or not isinstance(vals, list):
            return None, f"series[{i}].values is required (non-empty list)"

    output = config.get("output", "").strip()
    if not output:
        return None, "output path is required (e.g. output/chart.png)"
    if ".." in output or output.startswith("/"):
        return None, "output must be a relative path without .."

    categories = config.get("categories", [])
    if not categories:
        n = max(len(s["values"]) for s in series)
        categories = [str(i + 1) for i in range(n)]

    # 创建 figure（radar 用 polar 投影）
    w = config.get("width", 800) / 100
    h = config.get("height", 600) / 100
    subplot_kw = {"projection": "polar"} if chart_type == "radar" else {}
    fig, ax = plt.subplots(figsize=(w, h), dpi=100, subplot_kw=subplot_kw)

    # 标题 + 轴标签（radar 是 polar 投影，无 cartesian 轴，跳过 x/y_label）
    title = config.get("title", "")
    if title:
        ax.set_title(title, fontsize=16, fontweight="bold")
    if chart_type != "radar":
        if config.get("x_label"):
            ax.set_xlabel(config["x_label"], fontsize=12)
        if config.get("y_label"):
            ax.set_ylabel(config["y_label"], fontsize=12)

    # 网格（pie 不画网格；radar 用 polar 自带网格）
    if chart_type == "pie":
        pass
    elif chart_type == "radar":
        ax.grid(True, alpha=0.3)
    else:
        ax.grid(True, alpha=0.3, linestyle="--")

    # 渲染图表
    RENDERERS[chart_type](ax, series, categories)

    # 图例（pie 用 labels 不用 legend）
    if chart_type != "pie":
        ax.legend(loc="best", fontsize=10)

    plt.tight_layout()

    # 保存
    home = os.environ.get("HERMES_HOME") or os.environ.get("HOME") or "."
    full_path = os.path.join(home, output)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    fig.savefig(full_path, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)

    return output, None


def main():
    try:
        config = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": f"invalid JSON config: {e}"}))
        return

    result, error = draw_chart(config)
    if error:
        print(json.dumps({"error": error}))
    else:
        print(result)


if __name__ == "__main__":
    main()
