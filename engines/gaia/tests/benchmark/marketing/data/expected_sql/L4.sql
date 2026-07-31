-- ════════════════════════════════════════════════════════════════════════
-- L4: 聚合统计 — 今日呼出数（COUNT）
-- 来源: MySQL查询脚本.md 脚本 9
-- 修正: 无需修正
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :sales_phone, :date (YYYY-MM-DD)
-- 断言: count_eq
SELECT COUNT(*) AS `count`
FROM t_ods_leads_server_sale_call_record_rt moc
JOIN t_ods_leads_server_leads_info_rt l ON l.id = moc.lead_id
JOIN t_ods_source_data_leads_operation_record lar ON lar.leads_id = l.id
JOIN t_ods_master_data_staff sc ON sc.user_id = lar.sales_consultant_id
WHERE sc.phone = :sales_phone
  AND moc.call_time LIKE CONCAT(:date, '%')
  AND l.leads_status = '100410';
