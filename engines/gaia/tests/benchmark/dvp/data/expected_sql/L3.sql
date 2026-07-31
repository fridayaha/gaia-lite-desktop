-- L3: 多表 JOIN 反查 — change_point_id → 经 component → structure → body → vehicle → project 反查项目令号
-- 用例: 给定 :change_point_id，6 跳 link traversal 反查所属项目令号。
-- 断言: set_eq
-- 参数: :change_point_id (例 'CP-20240601-001')
--
-- 链路: changePointEntity → component → structure(5类之一) → vehicleBody → projectVehicle → projectBase
-- 难点: component.structure_type 决定 join 哪张 structure 表。
--       这里用 UNION ALL 把 5 张 structure 表合并成统一视图 (structure_code, body_code)，
--       再 join。
--       真实业务里 component 只属于一种 structure，UNION 不会产生重复。
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
    pb.project_code
FROM t_change_point_entity cpe
JOIN t_component c ON cpe.component_id = c.component_id
JOIN all_structures s ON c.structure_code = s.structure_code
JOIN t_vehicle_body vb ON s.body_code = vb.body_code
JOIN t_project_vehicle pv ON vb.vehicle_code = pv.vehicle_code
JOIN t_project_base pb ON pv.project_code = pb.project_code
WHERE cpe.change_point_id = :change_point_id
  AND pb.delete_mark = 0;
