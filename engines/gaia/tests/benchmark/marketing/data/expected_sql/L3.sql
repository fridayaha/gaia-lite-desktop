-- ════════════════════════════════════════════════════════════════════════
-- L3: 多表 JOIN 反查 — lead_id → 销售手机号（经 lead_allocate_record → sales_consultant）
-- 来源: MySQL查询脚本.md 脚本 6
-- 修正: 无需修正
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :lead_id
-- 断言: set_eq (sales_consultant_phone)
SELECT sc.phone AS sales_consultant_phone
FROM t_ods_leads_server_leads_info_rt l
JOIN t_ods_source_data_leads_operation_record lar ON lar.leads_id = l.id
JOIN t_ods_master_data_staff sc ON sc.user_id = lar.sales_consultant_id
WHERE l.id = :lead_id;
