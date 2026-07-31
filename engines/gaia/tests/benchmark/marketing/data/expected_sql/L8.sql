-- ════════════════════════════════════════════════════════════════════════
-- L8: 无源字段查询 — user.phone_brand / phone_device_model = null（修正 4）
-- 来源: 泛化
-- 修正 4: user 简化为单源；phone_brand/phone_device_model 无物理 backing（CDP 未接入）
-- 验证: 无源属性查询恒返回 null
-- ════════════════════════════════════════════════════════════════════════
-- 参数: :user_id
-- 断言: all_null (phone_brand, phone_device_model 两列全部为 null)
-- 物理 SQL: 这两个字段无物理源，物理侧查不到，expected 即为 null。
--           此 SQL 仅作"物理侧确认无此列"的对照（实际 expected = 全 null）。
SELECT
    NULL AS phone_brand,
    NULL AS phone_device_model
FROM t_ods_leads_server_leads_user_rt u
WHERE u.user_id = :user_id;
