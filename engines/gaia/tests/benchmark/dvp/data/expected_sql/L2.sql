-- L2: 单实体过滤+排序 — 某项目下所有车型按 dev_tier 排序
-- 用例: 给定 :project_code，返回该项目下所有车辆项目，按开发等级排序。
-- 断言: ordered_list + jaccard≥0.9（验证 order_by，回归缺陷#1 类比）
-- 参数: :project_code (例 'P2024001')
--
-- 返回字段:
--   vehicleCode ← vehicle_code
--   vehicleName ← vehicle_name
--   powerType ← power_type
--   driveType ← drive_type
--   devTier ← dev_tier
--   targetMarket ← target_market
SELECT
    vehicle_code,
    vehicle_name,
    power_type,
    drive_type,
    dev_tier,
    target_market
FROM t_project_vehicle
WHERE project_code = :project_code
  AND status = '1'  -- 有效
ORDER BY dev_tier ASC, vehicle_code ASC;
