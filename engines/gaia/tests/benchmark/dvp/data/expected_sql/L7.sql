-- L7: 跨工况过滤 — 所有 frontCollision 工况下状态为"待执行"的 testItem
-- 用例: 返回 condition_type='front_collision' 且 test_item.status='1'(待执行) 的试验项。
-- 断言: set_eq
-- 参数: 无（固定过滤）
--
-- 验证共用物理表 t_oper_condition_detail 的 condition_type filter 不串数据
-- （DESIGN.md §3.4 修正4 关键回归点）。
-- 注意: 4 个 ObjectType(FrontCollision/RearCollision/...)共用同一物理表，
--       本体 API 查 FrontCollision 时应只返回 condition_type='front_collision' 的行。
--       黄金真值 SQL 显式带 condition_type 过滤，与本体语义对齐。
SELECT
    ocd.detail_condition_code,
    ocd.detail_condition_name,
    ti.test_item_id,
    ti.test_item_name,
    ti.status,
    ti.test_response
FROM t_oper_condition_detail ocd
JOIN t_test_item ti ON ocd.detail_condition_code = ti.detail_condition_code
WHERE ocd.condition_type = 'front_collision'
  AND ti.status = '1'  -- 待执行
ORDER BY ti.test_item_id;
