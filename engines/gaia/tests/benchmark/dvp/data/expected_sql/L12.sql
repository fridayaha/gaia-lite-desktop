-- L12: VIRTUAL range filter datetime — lmsTargetIteration 按 iteration_date 区间
-- 用例: 给定 :start_date 和 :end_date，返回该日期区间内的目标迭代记录。
-- 断言: count_eq
-- 参数: :start_date (例 '2026-01-01'), :end_date (例 '2026-06-30')
--
-- **回归缺陷#2 专项**：marketing L7-bis 同类（VIRTUAL filter range 语义错误）。
-- DVP 验证 VIRTUAL 路径下 range filter on DATE 列的闭区间语义正确。
-- 这是 Tier2（xfail 倒逼）：若回归#2 未完全修复，预期 FAIL。
SELECT
    iteration_id,
    dimension_id,
    iteration_version,
    iteration_date,
    iteration_threshold,
    status
FROM t_lms_target_iteration
WHERE iteration_date >= :start_date
  AND iteration_date <= :end_date
ORDER BY iteration_date, iteration_id;
