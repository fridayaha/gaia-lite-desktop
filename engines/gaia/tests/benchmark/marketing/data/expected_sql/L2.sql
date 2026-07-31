-- ════════════════════════════════════════════════════════════════════════
-- L2: 单实体过滤+排序 — 待邀约线索（next_follow_time + leads_status + is_test_drive）
-- 来源: MySQL查询脚本.md 脚本 1
-- 修正: 无需修正
-- 回归: 缺陷#1 order_by 字段映射失效（camelCase nextFollowTime → snake_case next_follow_time）
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :sales_phone, :date (YYYY-MM-DD)
-- 断言: ordered_list + jaccard≥0.9（排序必须生效，回归缺陷#1）
SELECT
    sc.phone                AS sc_phone,
    u.user_name             AS customer_name,
    u.mobile                AS customer_phone,
    l.next_follow_time      AS next_follow_time
FROM t_ods_source_data_leads_operation_record lar
JOIN t_ods_master_data_staff sc ON sc.user_id = lar.sales_consultant_id
JOIN t_ods_leads_server_leads_info_rt l ON l.id = lar.leads_id
JOIN t_ods_leads_server_leads_user_rt u ON u.user_id = l.user_id
WHERE sc.phone = :sales_phone
  AND l.next_follow_time LIKE CONCAT(:date, '%')
  AND l.leads_status   = '100410'
  AND l.test_drive     = '0'
ORDER BY l.next_follow_time ASC;
