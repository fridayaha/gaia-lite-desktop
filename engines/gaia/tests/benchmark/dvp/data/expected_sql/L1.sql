-- L1: 单实体点查 — project_code 反查项目信息
-- 用例: 给定 :project_code，返回该项目的核心信息。
-- 断言: set_eq（无序集合，按 project_code 对齐）
-- 参数: :project_code (例 'P2024001')
--
-- 返回字段（属性 apiName ← 物理列）:
--   projectCode ← project_code
--   projectName ← project_name
--   brand ← brand
--   projectType ← project_type
--   devTier ← dev_tier
--   lifecycleState ← lifecycle_state
--   projectStatus ← project_status
--   managerName ← manager_name
--   researchUnit ← research_unit
SELECT
    project_code,
    project_name,
    brand,
    project_type,
    dev_tier,
    lifecycle_state,
    project_status,
    manager_name,
    research_unit
FROM t_project_base
WHERE project_code = :project_code
  AND delete_mark = 0;
