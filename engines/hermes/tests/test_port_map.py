"""port_map.py 单测：Pod 内 profile→port 唯一真相管理。

用 tmp_path + HERMES_HOME 环境变量隔离每个用例的 port_map.json / profiles 目录，
不污染真实 PVC。验证：alloc 幂等、扫描法回收已删端口、原子写、并发无冲突、
reconcile 孤儿/stale、端口耗尽、损坏 json 恢复。
"""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import port_map  # noqa: E402


@pytest.fixture
def pm(tmp_path, monkeypatch):
    """每个用例独立的 HERMES_DATA 目录。"""
    data_dir = tmp_path / "data"
    (data_dir / "profiles").mkdir(parents=True)
    monkeypatch.setattr(port_map, "HERMES_DATA", str(data_dir))
    monkeypatch.setattr(port_map, "PORT_MAP_PATH", str(data_dir / "port_map.json"))
    monkeypatch.setattr(port_map, "LOCK_PATH", str(data_dir / "port_map.json.lock"))
    monkeypatch.setattr(port_map, "PROFILES_DIR", str(data_dir / "profiles"))
    return port_map


def _mk_profile_dir(pm, name: str) -> str:
    os.makedirs(os.path.join(pm.PROFILES_DIR, name), exist_ok=True)
    return name


def test_alloc_idempotent(pm):
    """同名 alloc 两次返回同端口，不重复推进。"""
    p1 = pm.alloc("profA")
    p2 = pm.alloc("profA")
    assert p1 == p2 == 8644
    assert pm.all_profiles() == {"profA": 8644}


def test_alloc_sequential_then_reclaim(pm):
    """删除后新建复用被释放的端口（扫描法无泄漏）。"""
    a = pm.alloc("profA")
    b = pm.alloc("profB")
    c = pm.alloc("profC")
    assert (a, b, c) == (8644, 8645, 8646)
    pm.remove("profB")
    # profB 释放 → 新 profile 复用 8645
    d = pm.alloc("profD")
    assert d == 8645
    assert pm.all_profiles() == {"profA": 8644, "profC": 8646, "profD": 8645}


def test_get_missing_returns_none(pm):
    assert pm.get("nope") is None
    pm.alloc("x")
    assert pm.get("x") == 8644


def test_set_explicit(pm):
    pm.set_port("p", 8700)
    assert pm.get("p") == 8700
    # set 不影响扫描法的 used 集合
    assert pm.alloc("q") == 8644


def test_atomic_write_no_partial(pm):
    """写后文件是合法 json，无半成品。"""
    for i in range(20):
        pm.alloc(f"p{i}")
    data = json.loads(open(pm.PORT_MAP_PATH).read())
    assert data["version"] == 1
    assert len(data["profiles"]) == 20
    # 所有端口唯一
    ports = list(data["profiles"].values())
    assert len(set(ports)) == len(ports)


def test_concurrent_alloc_no_collision(pm):
    """多线程并发 alloc 不同 name，端口全部唯一。"""
    results: dict[str, int] = {}
    barrier = threading.Barrier(20)

    def worker(name: str):
        barrier.wait()
        results[name] = pm.alloc(name)

    threads = [threading.Thread(target=worker, args=(f"p{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ports = list(results.values())
    assert len(set(ports)) == len(ports) == 20
    assert set(ports) == set(range(8644, 8664))


def test_concurrent_alloc_same_name_idempotent(pm):
    """多线程并发 alloc 同一 name，返回同一端口。"""
    results = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        results.append(pm.alloc("same"))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r == 8644 for r in results)


def test_reconcile_orphan_dirs_get_ports(pm):
    """目录在但不在 map → 分配新端口。"""
    _mk_profile_dir(pm, "orphan1")
    _mk_profile_dir(pm, "orphan2")
    result = pm.reconcile_from_disk()
    assert set(result.keys()) == {"orphan1", "orphan2"}
    assert result["orphan1"] != result["orphan2"]
    # base 目录被排除
    assert "base" not in result


def test_reconcile_stale_entries_removed(pm):
    """map 有但目录不存在 → 删除条目。"""
    pm.alloc("ghost")  # 在 map 里
    # 但 PVC 上没有 ghost 目录
    result = pm.reconcile_from_disk()
    assert "ghost" not in result
    assert result == {}


def test_reconcile_keeps_existing(pm):
    """目录与 map 一致 → 端口保留不变。"""
    _mk_profile_dir(pm, "keep")
    pm.alloc("keep")
    first = pm.get("keep")
    pm.reconcile_from_disk()
    assert pm.get("keep") == first  # 不变


def test_reconcile_missing_file_rebuilds_from_dirs(pm):
    """port_map.json 缺失 → 从目录重建。"""
    _mk_profile_dir(pm, "r1")
    _mk_profile_dir(pm, "r2")
    assert not os.path.exists(pm.PORT_MAP_PATH)
    result = pm.reconcile_from_disk()
    assert set(result.keys()) == {"r1", "r2"}
    assert os.path.exists(pm.PORT_MAP_PATH)  # 重建后落盘


def test_corrupt_json_recovery(pm):
    """port_map.json 损坏 → _load 返回空骨架，reconcile 重建。"""
    _mk_profile_dir(pm, "alive")
    pm.alloc("alive")
    # 写损坏内容
    open(pm.PORT_MAP_PATH, "w").write("garbage{not json")
    assert pm.all_profiles() == {}  # _load 容错
    # reconcile 从目录恢复 alive
    result = pm.reconcile_from_disk()
    assert "alive" in result


def test_corrupt_json_wrong_shape_recovery(pm):
    """json 合法但结构不对（profiles 非 dict）→ 视为空骨架。"""
    open(pm.PORT_MAP_PATH, "w").write(json.dumps({"version": 1, "profiles": "not a dict"}))
    assert pm.all_profiles() == {}


def test_port_exhausted(pm, monkeypatch):
    """区间耗尽 → raise PortExhausted。"""
    monkeypatch.setattr(pm, "PORT_MIN", 8644)
    monkeypatch.setattr(pm, "PORT_MAX", 8645)
    pm.alloc("a")  # 8644
    pm.alloc("b")  # 8645
    with pytest.raises(pm.PortExhausted):
        pm.alloc("c")


def test_remove_idempotent(pm):
    pm.alloc("a")
    pm.remove("a")
    pm.remove("a")  # 再删不抛
    assert pm.all_profiles() == {}


def test_cli_alloc_get_all(pm, capsys):
    """CLI 子命令输出可被 bash/manager 解析。"""
    assert pm._cli(["port_map.py", "alloc", "cliA"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == "8644"
    assert pm._cli(["port_map.py", "get", "cliA"]) == 0
    assert capsys.readouterr().out.strip() == "8644"
    assert pm._cli(["port_map.py", "get", "missing"]) == 0
    assert capsys.readouterr().out == ""  # missing 打印空
    assert pm._cli(["port_map.py", "all"]) == 0
    assert json.loads(capsys.readouterr().out) == {"cliA": 8644}


def test_flock_lockfile_created(pm):
    """alloc 后锁文件存在（供跨进程 flock 共享）。"""
    pm.alloc("a")
    assert os.path.exists(pm.LOCK_PATH)
