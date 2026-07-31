-- L8: range filter 数值 — change_degree 在 [3,5] 区间的 changePointEntity
-- 用例: 返回变更程度在 3-5 之间（含）的变化点。
-- 断言: count_eq
-- 参数: 无
--
-- 验证 VIRTUAL range filter（**回归缺陷#2 专项**：marketing L7-bis 同类，
-- DVP 验证 range filter 语义在 VIRTUAL 路径下正确，min/max 闭区间）。
SELECT
    change_point_id,
    change_description,
    change_degree,
    weight,
    component_id
FROM t_change_point_entity
WHERE change_degree >= 3
  AND change_degree <= 5
ORDER BY change_point_id;
