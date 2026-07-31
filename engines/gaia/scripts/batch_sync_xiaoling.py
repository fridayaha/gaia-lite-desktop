#!/usr/bin/env python3
"""Batch-create MANAGED datasets for all tables under the `xiaoling` datasource
via the "同步此表" path (sync task + dataset registration).

Excludes `spatial_ref_sys` (PostGIS internal table).

Idempotent: skips tables whose sync task / dataset already exist.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime

API = "http://127.0.0.1:8000/api"
DATASOURCE = "xiaoling"
EXCLUDE = {"spatial_ref_sys"}


def _camel(table: str) -> str:
    """cann_op -> CannOp ; chip_die -> ChipDie"""
    return "".join(p.capitalize() for p in table.split("_") if p)


def _sync_api_name(table: str) -> str:
    return f"{DATASOURCE}Sync{_camel(table)}"


def _dataset_api_name(table: str) -> str:
    return f"{table}_raw"


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list | str]:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def list_existing_sync_tasks() -> set[str]:
    code, data = req("GET", f"/datasources/{DATASOURCE}/sync-tasks")
    if code != 200:
        print(f"[WARN] list sync-tasks failed: {code} {data}", file=sys.stderr)
        return set()
    return {t["api_name"] for t in data}  # type: ignore[union-attr]


def list_existing_datasets() -> set[str]:
    code, data = req("GET", "/datasets")
    if code != 200:
        print(f"[WARN] list datasets failed: {code} {data}", file=sys.stderr)
        return set()
    return {d["api_name"] for d in data}  # type: ignore[union-attr]


def main() -> int:
    # 1. explore tables
    code, data = req("POST", f"/datasources/{DATASOURCE}/explore", {})
    if code != 200:
        print(f"[ERR] explore failed: {code} {data}", file=sys.stderr)
        return 1
    tables = [t["name"] for t in data["tables"]]  # type: ignore[index]
    tables = [t for t in tables if t not in EXCLUDE]
    print(f"[INFO] {len(tables)} tables to sync (excluded {EXCLUDE})")

    existing_sync = list_existing_sync_tasks()
    existing_ds = list_existing_datasets()
    print(f"[INFO] existing sync tasks: {len(existing_sync)}; existing datasets: {len(existing_ds)}")

    ok = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    for t in tables:
        sync_name = _sync_api_name(t)
        ds_name = _dataset_api_name(t)
        already_sync = sync_name in existing_sync
        already_ds = ds_name in existing_ds

        # 2. create sync task (skip if exists)
        if not already_sync:
            code, data = req(
                "POST",
                f"/datasources/{DATASOURCE}/sync-tasks",
                {
                    "api_name": sync_name,
                    "source_config": {"table": t},
                    "target_dataset_api_name": ds_name,
                    "sync_mode": "full_snapshot",
                    "transaction_type": "snapshot",
                },
            )
            if code != 201:
                failed.append((t, f"sync-task {code}: {data}"))
                print(f"[FAIL] {t}: sync-task -> {code}")
                continue
            status = data.get("status") if isinstance(data, dict) else "?"
            print(f"[SYNC] {t}: task={sync_name} status={status}")
        else:
            print(f"[SKIP] {t}: sync task {sync_name} already exists")

        # 3. register MANAGED dataset (skip if exists)
        if not already_ds:
            code, data = req(
                "POST",
                "/datasets",
                {
                    "api_name": ds_name,
                    "display_name": ds_name,
                    "data_source_api_name": DATASOURCE,
                    "kind": "MANAGED",
                },
            )
            if code != 201:
                failed.append((t, f"dataset {code}: {data}"))
                print(f"[FAIL] {t}: dataset -> {code}")
                continue
            print(f"[DATA] {t}: dataset={ds_name}")
        else:
            print(f"[SKIP] {t}: dataset {ds_name} already exists")

        ok += 1

    skipped = sum(1 for t in tables if _sync_api_name(t) in existing_sync and _dataset_api_name(t) in existing_ds)
    print("\n" + "=" * 60)
    print(f"[{datetime.now().isoformat(timespec='seconds')}] done")
    print(f"  total tables : {len(tables)}")
    print(f"  processed OK : {ok}")
    print(f"  failed       : {len(failed)}")
    if failed:
        print("  failures:")
        for t, msg in failed:
            print(f"    - {t}: {msg}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
