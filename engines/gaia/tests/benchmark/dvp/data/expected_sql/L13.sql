-- L13: 共用物理表跨工况 UNION — frontCollision + sideCollision 的 testItem 合集
-- 用例: 返回正面碰撞 + 侧面碰撞两种工况下的试验项合集（去重）。
-- 断言: set_eq
-- 参数: 无
--
-- 验证多 ObjectType 共用同一物理表 t_oper_condition_detail 时，
-- 跨 condition_type 的 UNION 查询不混淆（DESIGN.md §3.4 修正4 关键回归点）。
-- Tier2（xfail 倒逼）：若本体 API 不能正确处理"同一 dataset 多 OT"的查询，
-- 预期 FAIL。
SELECT DISTINCT
    ocd.condition_type,
    ocd.detail_condition_code,
    ocd.detail_condition_name,
    ti.test_item_id,
    ti.test_item_name,
    ti.status
FROM t_oper_condition_detail ocd
JOIN t_test_item ti ON ocd.detail_condition_code = ti.detail_condition_code
WHERE ocd.condition_type IN ('front_collision', 'side_collision')
ORDER BY ocd.condition_type, ti.test_item_id;
