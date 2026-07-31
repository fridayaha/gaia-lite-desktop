/**
 * useTimeFilter — 全局时间过滤 hook（design-v2 §1.3）。
 *
 * 维护时间窗 + 活跃过滤，提供节点可见性判断（有时序属性且窗内有数据→活跃）。
 * 不直接查后端，时序数据由上层传入（避免 hook 耦合 API）。
 */
import { useCallback, useState } from 'react';

export interface TimeRange {
  start: number; // ms timestamp
  end: number;
}

export function useTimeFilter() {
  const [range, setRange] = useState<TimeRange | null>(null);
  const [activeOnly, setActiveOnly] = useState(false);
  const [playing, setPlaying] = useState(false);

  /** 设置时间窗。 */
  const setWindow = useCallback((start: number, end: number) => {
    setRange({ start, end });
  }, []);

  /** 清除时间窗。 */
  const clearWindow = useCallback(() => {
    setRange(null);
    setPlaying(false);
  }, []);

  /** 切换"仅显示活跃实体"。 */
  const toggleActiveOnly = useCallback(() => {
    setActiveOnly((v) => !v);
  }, []);

  /** 切换播放。 */
  const togglePlay = useCallback(() => {
    setPlaying((v) => !v);
  }, []);

  /** 判断某时间点是否在当前窗内。 */
  const inWindow = useCallback(
    (ts: number): boolean => {
      if (!range) return true;
      return ts >= range.start && ts <= range.end;
    },
    [range],
  );

  return {
    range,
    activeOnly,
    playing,
    setWindow,
    clearWindow,
    toggleActiveOnly,
    togglePlay,
    inWindow,
  };
}
