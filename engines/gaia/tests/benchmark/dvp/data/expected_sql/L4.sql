-- L4: 聚合统计 — 某项目下各工况的 testItem 数量（COUNT group by）
-- 用例: 给定 :project_code，统计该项目下每个细分工况的试验项数量。
-- 断言: count_eq
-- 参数: :project_code (例 'P2024001')
--
-- 链路: projectBase → projectVehicle → vehicleBody → structure(5类UNION)
--       → component → changePointEntity → operCondition → operConditionDetail
--       → testItem
-- 但这链路太长且 changePoint→operCondition 是 M:N（一个变化点触发多个工况），
-- 真实业务统计"某项目下各工况试验项数"更合理的口径是：
--   该项目 DVP 计划 → 轮次 → 排程工况 → 工况下的试验项（不经过 changePoint）。
-- 这里用 DVP 计划口径（更符合"项目下"的语义）：
--   projectBase → dvpDesign → experimentItemRound → operCondition
--   → operConditionDetail → testItem
SELECT
    ocd.condition_type AS detail_condition_type,
    COUNT(ti.test_item_id) AS test_item_count
FROM t_project_base pb
JOIN t_dvp_design dd ON pb.project_code = dd.project_code
JOIN t_experiment_item_round eir ON dd.dvp_code = eir.dvp_code
JOIN t_oper_condition_detail ocd ON eir.condition_code = ocd.condition_code
JOIN t_test_item ti ON ocd.detail_condition_code = ti.detail_condition_code
WHERE pb.project_code = :project_code
  AND pb.delete_mark = 0
GROUP BY ocd.condition_type
ORDER BY ocd.condition_type;
