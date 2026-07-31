#!/usr/bin/env python3
"""
解析 pytest 输出（从 stdin 读取），提取 passed/failed/skipped/errors 数量，
更新到 JSON 结果文件中。

用法:
    _parse_pytest.py <result_file> <module_name> <exit_code> <start_ts> <end_ts> < pytest_output
"""
import json
import re
import sys


def parse_pytest_output(text: str) -> dict:
    """从 pytest 输出文本中提取 passed/failed/skipped/errors 数量。"""
    result = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    # pytest 最后一行格式: "N passed, N failed, N skipped, N errors in Xs"
    # 也可能是 "N passed, N skipped in Xs" 等各种组合
    # 取最后一行包含 "passed" 的行
    lines = text.strip().split("\n")
    summary_line = ""
    for line in reversed(lines):
        if "passed" in line or "failed" in line or "error" in line:
            summary_line = line
            break

    if not summary_line:
        return result

    # 提取各种状态的数量
    for key in result:
        pattern = r"(\d+)\s+" + key
        m = re.search(pattern, summary_line)
        if m:
            result[key] = int(m.group(1))

    return result


def main():
    if len(sys.argv) < 6:
        print("Usage: _parse_pytest.py <result_file> <module_name> <exit_code> <start_ts> <end_ts>", file=sys.stderr)
        sys.exit(1)

    result_file = sys.argv[1]
    module_name = sys.argv[2]
    exit_code = int(sys.argv[3])
    start_ts = sys.argv[4]
    end_ts = sys.argv[5]

    # 从 stdin 读取 pytest 输出
    pytest_output = sys.stdin.read()

    # 解析结果
    counts = parse_pytest_output(pytest_output)

    status = "pass" if exit_code == 0 else "fail"

    # 更新 JSON
    with open(result_file, "r") as f:
        data = json.load(f)

    data["modules"][module_name] = {
        "status": status,
        "passed": counts["passed"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "errors": counts["errors"],
        "duration_start": start_ts,
        "duration_end": end_ts,
        "exit_code": exit_code,
    }

    with open(result_file, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
