-- ════════════════════════════════════════════════════════════════════════
-- L3-bis: 试驾 → 销售顾问反查（修正 1 回归）
-- 来源: MySQL查询脚本.md 脚本 14 子集
-- 修正 1: 移除 test_drive_consultant_id（物理列不存在，笔误）；
--         试驾→销售顾问仅用 td.sale_id → sc.user_id
-- 验证: 移除歧义属性后，试驾→销售顾问查询结果稳定
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :test_drive_id
-- 断言: set_eq (sales_consultant_phone)
SELECT sc.phone AS sales_consultant_phone
FROM t_ods_test_drive_test_drive_rt td
JOIN t_ods_master_data_staff sc ON sc.user_id = td.sale_id
WHERE td.id = :test_drive_id;
