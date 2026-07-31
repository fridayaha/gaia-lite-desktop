/**
 * TimeScrubber — 全局时间轴（design-v2 §1.3）。
 *
 * 画布底部时间轴条，拖动选时间窗 → 过滤整个画布（仅显示窗内活跃实体）。
 * 对齐 Kibana Graph Timebar + KronoGraph 范式（全局分析维度，非单对象回放）。
 *
 * 当前实现：基于时间范围的双滑块 + 预设 + 播放。
 * 时序数据来源由上层传入（nodeTimestamps：rid → 时间戳数组）。
 */
import { useEffect, useRef, useState } from 'react';
import type { useTimeFilter } from '../hooks/useTimeFilter';

interface TimeScrubberProps {
  timeFilter: ReturnType<typeof useTimeFilter>;
  /** 节点的时序时间戳（ms），用于推算总范围 + 判断活跃。 */
  nodeTimestamps: Map<string, number[]>;
}

const PRESETS: Array<{ label: string; hours: number }> = [
  { label: '1h', hours: 1 },
  { label: '24h', hours: 24 },
  { label: '48h', hours: 48 },
  { label: '7d', hours: 24 * 7 },
];

export function TimeScrubber({ timeFilter, nodeTimestamps }: TimeScrubberProps) {
  const { activeOnly, playing, setWindow, clearWindow, toggleActiveOnly, togglePlay } =
    timeFilter;

  // 推算总时间范围（所有节点时间戳的 min/max）
  const bounds = (() => {
    let min = Infinity;
    let max = -Infinity;
    for (const ts of nodeTimestamps.values()) {
      for (const t of ts) {
        if (t < min) min = t;
        if (t > max) max = t;
      }
    }
    if (min === Infinity) return null;
    return { min, max };
  })();

  const [localRange, setLocalRange] = useState<{ start: number; end: number } | null>(null);
  const playTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // bounds 变化时初始化 localRange
  useEffect(() => {
    if (bounds && !localRange) {
      setLocalRange({ start: bounds.min, end: bounds.max });
    }
  }, [bounds, localRange]);

  // 播放：时间窗自动右移
  useEffect(() => {
    if (!playing || !bounds || !localRange) return;
    const span = localRange.end - localRange.start;
    const step = span / 20; // 20 步走完
    playTimer.current = setInterval(() => {
      setLocalRange((cur) => {
        if (!cur || !bounds) return cur;
        const newStart = cur.start + step;
        const newEnd = cur.end + step;
        if (newEnd > bounds.max) {
          // 播放结束，回到起点
          return { start: bounds.min, end: bounds.min + span };
        }
        return { start: newStart, end: newEnd };
      });
    }, 500);
    return () => {
      if (playTimer.current) clearInterval(playTimer.current);
    };
  }, [playing, bounds, localRange]);

  // localRange 变化时同步到 hook
  useEffect(() => {
    if (localRange) {
      setWindow(localRange.start, localRange.end);
    }
  }, [localRange, setWindow]);

  if (!bounds) {
    return (
      <div className="border-t border-slate-200 bg-slate-50 px-4 py-1.5 text-center text-xs text-slate-400">
        画布节点无时序数据，时间轴不可用
      </div>
    );
  }

  const totalSpan = bounds.max - bounds.min;
  const fmt = (ts: number) => new Date(ts).toLocaleString('zh-CN', { hour12: false });

  const applyPreset = (hours: number) => {
    const span = hours * 3600 * 1000;
    setLocalRange({ start: bounds.max - span, end: bounds.max });
  };

  // 滑块位置（百分比）
  const startPct = localRange ? ((localRange.start - bounds.min) / totalSpan) * 100 : 0;
  const endPct = localRange ? ((localRange.end - bounds.min) / totalSpan) * 100 : 100;

  return (
    <div className="border-t border-slate-200 bg-white px-4 py-2">
      <div className="mb-1.5 flex items-center gap-3 text-xs">
        <button
          onClick={togglePlay}
          className="rounded bg-slate-700 px-2 py-0.5 text-white hover:bg-slate-800"
          title="播放/暂停时间窗滑动"
        >
          {playing ? '⏸' : '▶'}
        </button>
        <span className="text-slate-500">
          {localRange ? `${fmt(localRange.start)} → ${fmt(localRange.end)}` : '全时段'}
        </span>
        <div className="flex gap-1">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => applyPreset(p.hours)}
              className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-slate-500 hover:bg-slate-50"
            >
              {p.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-slate-500">
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={toggleActiveOnly}
          />
          仅活跃实体
        </label>
        <div className="flex-1" />
        <button
          onClick={() => {
            clearWindow();
            setLocalRange({ start: bounds.min, end: bounds.max });
          }}
          className="text-slate-400 hover:text-slate-600"
        >
          重置
        </button>
      </div>
      {/* 时间轴滑块（pointer 事件拖拽手柄，避免 range input 重叠问题）*/}
      <div
        className="relative h-8 select-none"
        onPointerDown={(e) => {
          const track = e.currentTarget;
          const rect = track.getBoundingClientRect();
          const pct = ((e.clientX - rect.left) / rect.width) * 100;
          // 点击离哪个手柄近就拖哪个
          const distStart = Math.abs(pct - startPct);
          const distEnd = Math.abs(pct - endPct);
          const dragging = distStart < distEnd ? 'start' : 'end';
          track.setPointerCapture(e.pointerId);
          (track as HTMLElement & { _drag?: string })._drag = dragging;
        }}
        onPointerMove={(e) => {
          const track = e.currentTarget;
          const drag = (track as HTMLElement & { _drag?: string })._drag;
          if (!drag) return;
          const rect = track.getBoundingClientRect();
          let pct = ((e.clientX - rect.left) / rect.width) * 100;
          pct = Math.max(0, Math.min(100, pct));
          const ts = bounds.min + (pct / 100) * totalSpan;
          setLocalRange((cur) => {
            const start = cur?.start ?? bounds.min;
            const end = cur?.end ?? bounds.max;
            if (drag === 'start') return { start: Math.min(ts, end), end };
              return { start, end: Math.max(ts, start) };
          });
        }}
        onPointerUp={(e) => {
          const track = e.currentTarget;
          (track as HTMLElement & { _drag?: string })._drag = undefined;
          try { track.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
        }}
      >
        {/* 整体轨道 */}
        <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded bg-slate-200" />
        {/* 选中窗 */}
        <div
          className="absolute top-1/2 h-2 -translate-y-1/2 rounded bg-blue-400"
          style={{ left: `${startPct}%`, right: `${100 - endPct}%` }}
        />
        {/* 起始手柄 */}
        <div
          className="absolute top-1/2 z-10 h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border-2 border-blue-600 bg-white shadow hover:bg-blue-50"
          style={{ left: `${startPct}%` }}
        />
        {/* 结束手柄 */}
        <div
          className="absolute top-1/2 z-10 h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border-2 border-blue-600 bg-white shadow hover:bg-blue-50"
          style={{ left: `${endPct}%` }}
        />
      </div>
    </div>
  );
}
