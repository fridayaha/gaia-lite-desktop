-- L14: 时间旅行 — spec 历史快照对比
-- 用例: 对比 spec 在两个时间点的快照差异。
-- 断言: snapshot_diff
-- 参数: :snapshot_id_1, :snapshot_id_2
--
-- 北极星（Tier3，预期 XFAIL）：VIRTUAL 虚拟表不落地 Iceberg，无快照能力。
-- VIRTUAL 查询总是查 MySQL 当前状态，无法做时间旅行。
-- 本用例验证系统对"VIRTUAL 时间旅行"的拒绝/降级行为，预期 ERROR 或 XFAIL。
--
-- 黄金真值：无（VIRTUAL 无快照），本 SQL 仅作占位，harness 应预期 XFAIL。
SELECT spec_code, spec_name, status, update_time
FROM t_spec
WHERE spec_code = :spec_code;
