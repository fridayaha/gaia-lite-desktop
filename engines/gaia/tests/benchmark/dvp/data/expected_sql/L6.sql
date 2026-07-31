-- L6: 增量查询 — 按 update_time 拉取某项目最近变更的 component
-- 用例: 给定 :project_code 和 :since_time，返回该项目下 update_time >= since_time 的零部件。
-- 断言: set_eq
-- 参数: :project_code (例 'P2024001'), :since_time (例 '2026-06-01 00:00:00')
--
-- 验证 range filter on datetime（回归缺陷#9 类比：range filter 不再 float 强转）。
-- 链路: projectBase → projectVehicle → vehicleBody → structure(UNION) → component
WITH all_structures AS (
    SELECT front_structure_code AS structure_code, body_code FROM t_front_structure
    UNION ALL
    SELECT side_structure_code, body_code FROM t_side_structure
    UNION ALL
    SELECT rear_structure_code, body_code FROM t_rear_structure
    UNION ALL
    SELECT chassis_structure_code, body_code FROM t_chassis_structure
    UNION ALL
    SELECT exterior_code, body_code FROM t_exterior_design
)
SELECT DISTINCT
    c.component_id,
    c.component_name,
    c.component_category,
    c.update_time
FROM t_project_base pb
JOIN t_project_vehicle pv ON pb.project_code = pv.project_code
JOIN t_vehicle_body vb ON pv.vehicle_code = vb.vehicle_code
JOIN all_structures s ON vb.body_code = s.body_code
JOIN t_component c ON s.structure_code = c.structure_code
WHERE pb.project_code = :project_code
  AND pb.delete_mark = 0
  AND c.update_time >= :since_time
ORDER BY c.update_time DESC, c.component_id;
