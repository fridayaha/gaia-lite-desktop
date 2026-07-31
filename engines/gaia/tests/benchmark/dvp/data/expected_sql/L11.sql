-- L11: 分页 — testItem 按 create_time 排序分页（limit/offset）
-- 用例: 给定 :condition_type 和页码（:limit, :offset），返回该工况下按创建时间排序的一页试验项。
-- 断言: ordered_list
-- 参数: :condition_type (例 'front_collision'), :limit (例 20), :offset (例 0)
--
-- 验证分页语义（limit/offset，order by create_time）。
SELECT
    test_item_id,
    test_item_name,
    status,
    create_time
FROM t_test_item ti
WHERE ti.detail_condition_code IN (
    SELECT detail_condition_code FROM t_oper_condition_detail
    WHERE condition_type = :condition_type
)
ORDER BY ti.create_time ASC, ti.test_item_id ASC
LIMIT :limit OFFSET :offset;
