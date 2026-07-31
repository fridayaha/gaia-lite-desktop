-- ════════════════════════════════════════════════════════════════════════
-- L6: 增量同步 — 按 update_time 拉取销售顾问
-- 来源: MySQL查询脚本.md 脚本 15
-- 修正: 无需修正
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :formatted_time (YYYY-MM-DD HH:MM:SS)
-- 断言: set_eq
SELECT
    s.user_id            AS sales_consultant_id,
    s.user_name          AS sales_consultant_name,
    s.phone              AS phone,
    s.job_number         AS job_number,
    s.is_store_admin     AS is_store_admin,
    s.gender             AS gender,
    s.email              AS email,
    s.leave_status       AS leave_status,
    s.termination_time   AS termination_time,
    s.status             AS status,
    s.update_time        AS update_time,
    d.store_code         AS dealership_id,
    d.org_name           AS dealership_name
FROM t_ods_master_data_staff s
JOIN t_ods_master_data_store d ON d.store_code = s.store_code
WHERE s.update_time > :formatted_time;
