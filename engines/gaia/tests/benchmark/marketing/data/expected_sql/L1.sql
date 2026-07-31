-- ════════════════════════════════════════════════════════════════════════
-- L1: 单实体点查 — lead_id 反查客户信息
-- 来源: MySQL查询脚本.md 脚本 7
-- 修正: 无需修正（单表 user 查询）
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :lead_id
-- 断言: set_eq (customer_name, customer_phone)
SELECT
    u.user_name      AS customer_name,
    u.mobile         AS customer_phone
FROM t_ods_leads_server_leads_info_rt l
JOIN t_ods_leads_server_leads_user_rt u ON u.user_id = l.user_id
WHERE l.id = :lead_id;
