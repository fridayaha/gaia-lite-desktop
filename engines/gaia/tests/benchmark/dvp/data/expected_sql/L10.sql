-- L10: 多条件组合 — 某项目 + 某工况类型 + 某状态 的 testItem（and 组合）
-- 用例: 给定 :project_code、:condition_type、:status，返回满足全部条件的试验项。
-- 断言: set_eq
-- 参数: :project_code (例 'P2024001'), :condition_type (例 'front_collision'),
--       :status (例 '1' 待执行)
--
-- 验证复合 filter（and 组合，跨 detail_condition_code join）。
-- 链路: projectBase → dvpDesign → experimentItemRound → operConditionDetail(按 condition_type) → testItem(按 status)
SELECT DISTINCT
    ti.test_item_id,
    ti.test_item_name,
    ti.status,
    ti.test_response,
    ti.plan_end_time
FROM t_project_base pb
JOIN t_dvp_design dd ON pb.project_code = dd.project_code
JOIN t_experiment_item_round eir ON dd.dvp_code = eir.dvp_code
JOIN t_oper_condition_detail ocd ON eir.condition_code = ocd.condition_code
JOIN t_test_item ti ON ocd.detail_condition_code = ti.detail_condition_code
WHERE pb.project_code = :project_code
  AND pb.delete_mark = 0
  AND ocd.condition_type = :condition_type
  AND ti.status = :status
ORDER BY ti.test_item_id;
