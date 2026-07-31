-- ════════════════════════════════════════════════════════════════════════
-- L7: 跨门店过滤 — 某门店所有销售的有效线索
-- 来源: 泛化（MySQL查询脚本.md 脚本 1 的门店维度扩展）
-- 修正: 无需修正
-- 安全前置: 此查询带 principal（销售只能查本门店），安全维度 S3 复用本 SQL 做行级/门店级隔离断言
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :store_code
-- 断言: set_eq (lead_id, customer_name, customer_phone)
SELECT
    l.id              AS lead_id,
    u.user_name       AS customer_name,
    u.mobile          AS customer_phone
FROM t_ods_source_data_leads_operation_record lar
JOIN t_ods_master_data_staff sc ON sc.user_id = lar.sales_consultant_id
JOIN t_ods_master_data_store d ON d.store_code = sc.store_code
JOIN t_ods_leads_server_leads_info_rt l ON l.id = lar.leads_id
JOIN t_ods_leads_server_leads_user_rt u ON u.user_id = l.user_id
WHERE d.store_code = :store_code
  AND l.leads_status = '100410';
