-- L5: LEFT JOIN 可选关联 — testItem LEFT JOIN spec（部分试验项无规范）
-- 用例: 给定 :detail_condition_code，列出该工况下所有试验项及其规范（无规范的为 NULL）。
-- 断言: set_eq + null_allowed
-- 参数: :detail_condition_code (例 'FC-001')
--
-- 验证 LEFT JOIN 联邦语义（部分 test_item.spec_code 为 NULL）。
SELECT
    ti.test_item_id,
    ti.test_item_name,
    ti.status,
    ti.spec_code,
    s.spec_name,
    s.pass_threshold
FROM t_test_item ti
LEFT JOIN t_spec s ON ti.spec_code = s.spec_code
WHERE ti.detail_condition_code = :detail_condition_code
ORDER BY ti.test_item_id;
