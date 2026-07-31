-- ════════════════════════════════════════════════════════════════════════
-- L5: LEFT JOIN 可选关联 — 已完成试驾 + 录音（recording 合成表）
-- 来源: MySQL查询脚本.md 脚本 4
-- 修正 3: recording 是合成实体，JOIN 合成 recording 表（非物理源表）
--         recording_id = sha1('test_drive:'+original_record_url)[:16]
-- 断言: set_eq + null_allowed（recording_url 缺失时为 null）
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :date_pattern (YYYY-MM-DD)
SELECT
    td.id                AS test_drive_id,
    sc.phone             AS sc_phone,
    td.name              AS customer_name,
    td.phone             AS customer_phone,
    td.schedule_time     AS schedule_time,
    td.begin_time        AS start_time,
    td.end_time          AS end_time,
    td.order_status      AS order_status,
    rec.recording_url    AS rec_url,
    tc.car_series_name   AS vehicle_model,
    tc.car_model_name    AS vehicle_variant
FROM t_ods_test_drive_test_drive_rt td
JOIN t_ods_master_data_staff sc ON sc.user_id = td.sale_id
LEFT JOIN recording rec ON rec.recording_id = td.original_record_url
LEFT JOIN t_ods_test_drive_car_model tc ON tc.id = td.test_drive_car_id
WHERE td.end_time LIKE CONCAT(:date_pattern, '%');
-- 注: 合成 recording 表的 recording_id 在 seed 阶段由
--     sha1('test_drive:' || original_record_url)[:16] 生成；
--     test_drive.original_record_url 列直接存该合成 id（seed 时写入），
--     因此此处 JOIN 条件 = td.original_record_url。
