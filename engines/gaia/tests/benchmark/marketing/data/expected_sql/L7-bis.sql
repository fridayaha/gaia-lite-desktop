-- ════════════════════════════════════════════════════════════════════════
-- L7-bis: VIRTUAL filter range — 竞品对比分析表走 Trino 联邦（VIRTUAL 对象）
-- 来源: 泛化
-- 修正: 无需修正
-- 回归: 缺陷#2 VIRTUAL filter range 语义错误
-- Tier: 2 (xfail，倒逼后端修复 VIRTUAL range filter)
-- ════════════════════════════════════════════════════════════════════════
-- 注: CompetitiveAnalysis 在本体中是 AI 产物（ontology-created, no backing），
--     本用例将其配置为 VIRTUAL（Trino 联邦指向外部表）以测 VIRTUAL range filter。
--     物理 SQL 这里用本体落库后的 PG 表模拟（ontology-created 数据落 PG）。
-- 参数: :td_id, :confidence_min (DECIMAL)
-- 断言: count_eq (expected 误差 ≤ 1)
SELECT COUNT(*) AS `count`
FROM competitive_analysis ca
WHERE ca.td_id = :td_id
  AND ca.confidence_score >= :confidence_min;
