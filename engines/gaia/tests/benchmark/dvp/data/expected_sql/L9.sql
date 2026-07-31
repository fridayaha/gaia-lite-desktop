-- L9: 跨链路反查 — 某 lmsTargetDimension 被哪些 testItem 验证（反向 link）
-- 用例: 给定 :dimension_id，返回验证该目标维度的所有试验项。
-- 断言: set_eq
-- 参数: :dimension_id (例 'DIM-00001')
--
-- 验证反向 traversal：从 lmsTargetDimension 反查 testItem。
-- 正向 link 是 testItem → lmsTargetDimension (verifiesTarget)，反向即"谁验证了我"。
SELECT
    ti.test_item_id,
    ti.test_item_name,
    ti.status,
    ti.test_response,
    ti.detail_condition_code
FROM t_test_item ti
WHERE ti.dimension_id = :dimension_id
ORDER BY ti.test_item_id;
