#!/usr/bin/env python3
"""
门店销售图表生成工具 — chart_store.py
接受 JSON 数据，生成图表输出 PNG。
密码从环境变量读取，不硬编码。
"""
# /// script
# dependencies = ["matplotlib", "pandas", "numpy"]
# ///

import argparse
import json
import os
import sys
from typing import Any

import matplotlib
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

matplotlib.use("Agg")

# 中文字体 fallback 链
CHINESE_FONTS = [
    "PingFang SC",
    "PingFang HK",
    "PingFang TC",
    "SimHei",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "AR PL UMing CN",
    "DejaVu Sans",
]

# 配色
C_BLUE = "#2563eb"
C_LIGHT_BLUE = "#60a5fa"
C_GREEN = "#16a34a"
C_RED = "#dc2626"
C_GOLD = "#d97706"
C_GRAY = "#94a3b8"
C_BG = "#f8f9fc"
C_GRID = "#e2e8f0"


def setup_chinese_font() -> str | None:
    """尝试设置中文字体，返回使用的字体名或 None。"""
    available = {f.name for f in fm.fontManager.ttflist}
    for font in CHINESE_FONTS:
        if font in available:
            plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["axes.unicode_minus"] = False
            return font
    plt.rcParams["axes.unicode_minus"] = False
    return None


def _extract_values(data: dict[str, Any], value_col: str) -> list[float]:
    """从 rows 中提取数值列，无效值转为 0。"""
    values: list[float] = []
    for r in data.get("rows", []):
        v = r.get(value_col, 0)
        try:
            values.append(float(v) if v else 0)
        except (ValueError, TypeError):
            values.append(0)
    return values


def _find_numeric_column(
    data: dict[str, Any], skip_first: bool = True
) -> str:
    """找到第一个数值列名。skip_first=True 时跳过第一列（通常是标签列）。"""
    cols = data.get("columns", [])
    rows = data.get("rows", [])
    if not rows:
        return "value"
    start = 1 if skip_first and len(cols) > 1 else 0
    for c in cols[start:]:
        try:
            float(rows[0].get(c, 0) or 0)
            return c
        except (ValueError, TypeError):
            continue
    return cols[start] if start < len(cols) else "value"


def make_bar(data: dict[str, Any], title: str, output: str) -> None:
    """柱状图 — 排行/对比。"""
    rows = data.get("rows", [])
    if not rows:
        print(json.dumps({"error": "数据为空，无法生成柱状图"}), file=sys.stderr)
        sys.exit(1)

    # 标签列取第一列，数值列取第一个非标签的数值列
    label_col = data.get("columns", [""])[0]
    labels = [str(r.get(label_col, "")) for r in rows]
    values_col = _find_numeric_column(data, skip_first=True)
    values = _extract_values(data, values_col)

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)

    # 前三名高亮：第1绿、末尾红、其余蓝
    colors = [C_BLUE] * len(values)
    if len(values) >= 3:
        colors[0] = C_GREEN
        colors[-1] = C_RED

    bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1], height=0.6)
    for bar, v in zip(bars, values[::-1]):
        ax.text(
            bar.get_width() + max(values) * 0.01,
            bar.get_y() + bar.get_height() / 2,
            str(v),
            ha="left",
            va="center",
            fontsize=11,
            color="#475569",
        )

    ax.set_title(title, fontsize=16, fontweight="bold", color="#1e293b", pad=16)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(C_GRID)
    ax.spines["bottom"].set_color(C_GRID)
    ax.tick_params(colors="#475569", labelsize=11)
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%d"))
    ax.grid(axis="x", alpha=0.3, color=C_GRID)

    plt.tight_layout()
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close()
    print(json.dumps({"chart": output, "type": "bar"}))


def make_line(data: dict[str, Any], title: str, output: str) -> None:
    """折线图 — 时间趋势。"""
    rows = data.get("rows", [])
    cols = data.get("columns", [])
    if not rows or len(cols) < 2:
        print(
            json.dumps({"error": "数据不足，无法生成折线图"}),
            file=sys.stderr,
        )
        sys.exit(1)

    x_col = cols[0]
    x_vals = [str(r.get(x_col, "")) for r in rows]
    # 简化 x 轴标签（日期显示 MM-DD）
    x_labels = [
        v[5:10] if len(v) >= 10 and v[4] == "-" else v for v in x_vals
    ]

    fig, ax = plt.subplots(figsize=(14, 5.5))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)

    for i in range(1, min(len(cols), 4)):
        y_col = cols[i]
        y_vals = _extract_values(data, y_col) if i < len(cols) else []
        # 确保只取当前列
        y_vals = _extract_values(
            {"rows": rows, "columns": cols}, y_col
        )
        color = [C_BLUE, C_GREEN, C_GOLD][(i - 1) % 3]
        ax.plot(
            range(len(x_vals)),
            y_vals,
            color=color,
            linewidth=2,
            marker="o",
            markersize=4,
            label=y_col,
        )

        # 标注最高最低点
        if not y_vals:
            continue
        max_idx = int(np.argmax(y_vals))
        min_idx = int(np.argmin(y_vals))
        ax.annotate(
            f"{y_vals[max_idx]:.0f}",
            (max_idx, y_vals[max_idx]),
            textcoords="offset points",
            xytext=(0, 10),
            fontsize=10,
            color=C_GREEN,
            fontweight="bold",
            ha="center",
        )
        ax.annotate(
            f"{y_vals[min_idx]:.0f}",
            (min_idx, y_vals[min_idx]),
            textcoords="offset points",
            xytext=(0, -15),
            fontsize=10,
            color=C_RED,
            fontweight="bold",
            ha="center",
        )

    # 每 5-7 个点显示一个 x 标签
    step = max(1, len(x_labels) // 10)
    ax.set_xticks(range(0, len(x_labels), step))
    ax.set_xticklabels(
        [x_labels[i] for i in range(0, len(x_labels), step)],
        rotation=30,
        ha="right",
        fontsize=9,
    )

    ax.set_title(title, fontsize=16, fontweight="bold", color="#1e293b", pad=16)
    ax.legend(fontsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(alpha=0.3, color=C_GRID)
    ax.tick_params(colors="#475569", labelsize=10)

    plt.tight_layout()
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close()
    print(json.dumps({"chart": output, "type": "line"}))


def make_pie(data: dict[str, Any], title: str, output: str) -> None:
    """饼图 — 占比。"""
    rows = data.get("rows", [])
    cols = data.get("columns", [])
    if not rows or len(cols) < 2:
        print(json.dumps({"error": "数据不足"}), file=sys.stderr)
        sys.exit(1)

    labels = [str(r.get(cols[0], "")) for r in rows]
    val_col = _find_numeric_column(data, skip_first=True)
    values = []
    for r in rows:
        v = r.get(val_col, 0)
        try:
            values.append(abs(float(v)) if v else 0)
        except (ValueError, TypeError):
            values.append(0)

    fig, ax = plt.subplots(figsize=(8, 8))
    fig.patch.set_facecolor(C_BG)
    colors_pie = [C_BLUE, C_GREEN, C_GOLD, C_RED, C_LIGHT_BLUE, C_GRAY]
    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        colors=colors_pie[: len(values)] * (len(values) // len(colors_pie) + 1),
        startangle=90,
        textprops={"fontsize": 12},
    )
    for at in autotexts:
        at.set_fontweight("bold")
    ax.set_title(title, fontsize=16, fontweight="bold", color="#1e293b", pad=16)

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close()
    print(json.dumps({"chart": output, "type": "pie"}))


def make_scatter(data: dict[str, Any], title: str, output: str) -> None:
    """散点图 — 关联分析。"""
    rows = data.get("rows", [])
    cols = data.get("columns", [])
    if not rows or len(cols) < 3:
        print(
            json.dumps({"error": "散点图需要至少 3 列"}),
            file=sys.stderr,
        )
        sys.exit(1)

    x_vals: list[float] = []
    y_vals: list[float] = []
    for r in rows:
        try:
            x_vals.append(float(r.get(cols[0], 0) or 0))
        except (ValueError, TypeError):
            x_vals.append(0)
        try:
            y_vals.append(float(r.get(cols[1], 0) or 0))
        except (ValueError, TypeError):
            y_vals.append(0)

    labels = (
        [str(r.get(cols[2], "")) for r in rows] if len(cols) > 2 else None
    )

    fig, ax = plt.subplots(figsize=(10, 7))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)
    ax.scatter(
        x_vals,
        y_vals,
        c=C_BLUE,
        s=60,
        alpha=0.7,
        edgecolors="white",
        linewidth=0.5,
    )
    if labels:
        for x, y, label in zip(x_vals, y_vals, labels):
            ax.annotate(label, (x, y), fontsize=9, alpha=0.8, ha="center", va="bottom")

    ax.set_xlabel(cols[0], fontsize=12, color="#475569")
    ax.set_ylabel(cols[1], fontsize=12, color="#475569")
    ax.set_title(title, fontsize=16, fontweight="bold", color="#1e293b", pad=16)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(alpha=0.3, color=C_GRID)

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close()
    print(json.dumps({"chart": output, "type": "scatter"}))


CHART_FUNCS = {
    "bar": make_bar,
    "line": make_line,
    "pie": make_pie,
    "scatter": make_scatter,
}


def auto_detect_chart_type(data: dict[str, Any]) -> str:
    """根据数据形状自动推断图表类型。"""
    cols = data.get("columns", [])
    rows = data.get("rows", [])
    row_count = len(rows)
    if row_count <= 15 and len(cols) == 2:
        return "bar"
    if row_count > 3:
        return "line"
    return "bar"


def main() -> None:
    setup_chinese_font()

    parser = argparse.ArgumentParser(description="门店销售图表生成工具")
    parser.add_argument("--data", type=str, required=True, help="JSON 数据文件路径")
    parser.add_argument("--title", type=str, default="图表", help="图表标题")
    parser.add_argument(
        "--type",
        type=str,
        default="auto",
        choices=["auto", "bar", "line", "pie", "scatter"],
        help="图表类型",
    )
    parser.add_argument("--output", type=str, required=True, help="输出 PNG 路径")
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(
            json.dumps({"error": f"数据文件不存在: {args.data}"}),
            file=sys.stderr,
        )
        sys.exit(1)

    with open(args.data, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "error" in data:
        print(
            json.dumps({"error": f"数据包含错误: {data['error']}"}),
            file=sys.stderr,
        )
        sys.exit(1)

    rows = data.get("rows", [])
    if not rows:
        print(
            json.dumps({"error": "查询结果为空，无法生成图表"}),
            file=sys.stderr,
        )
        sys.exit(1)

    chart_type = args.type
    if chart_type == "auto":
        chart_type = auto_detect_chart_type(data)

    CHART_FUNCS[chart_type](data, args.title, args.output)


if __name__ == "__main__":
    main()
