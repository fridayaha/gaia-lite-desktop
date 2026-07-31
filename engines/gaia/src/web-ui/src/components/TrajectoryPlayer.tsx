/**
 * TrajectoryPlayer — 轨迹回放播放器（graph-reasoning-frontend-design.md §4.4 Phase 2b）。
 *
 * 选中有时序属性的对象 → 查 series_query → 按时间播放轨迹点。
 *
 * 功能：
 *  - 选定 series_property（TIME_SERIES/GEOTEMPORAL_SERIES 属性）
 *  - 拉取时序点（seriesQuery API）
 *  - 时间轴 scrubbing + 播放/暂停/速度
 *  - 在地图上画轨迹线 + 当前位置 marker（由父级 MapPanel 或独立显示）
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { useGraphExplore } from '../hooks/useGraphExplore';
import { seriesQuery } from '../api/graph';

interface TrajectoryPlayerProps {
  ontology: string;
  explore: ReturnType<typeof useGraphExplore>;
}

interface TrajectoryPoint {
  timestamp: string;
  lon: number;
  lat: number;
}

function extractCoord(row: Record<string, unknown>): [number, number] | null {
  const loc = row.location ?? row.geo ?? row.point;
  if (!loc) return null;
  if (Array.isArray(loc) && loc.length >= 2) return [loc[0] as number, loc[1] as number];
  if (typeof loc === 'object' && loc !== null) {
    const o = loc as Record<string, unknown>;
    if (typeof o.lon === 'number') return [o.lon, o.lat as number];
  }
  return null;
}

export function TrajectoryPlayer({ ontology, explore }: TrajectoryPlayerProps) {
  const [seriesProperty, setSeriesProperty] = useState('track');
  const [points, setPoints] = useState<TrajectoryPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const selectedVid = explore.selectedVid;
  const selectedNode = selectedVid ? explore.nodes.get(selectedVid) : null;

  // 加载轨迹
  const loadTrajectory = async () => {
    if (!selectedVid || !selectedNode) return;
    setLoading(true);
    setError('');
    try {
      const rows = await seriesQuery(ontology, {
        object_type: selectedNode.api_name,
        series_property: seriesProperty,
        series_ids: [selectedVid],
        limit: 5000,
      });
      const pts: TrajectoryPoint[] = [];
      for (const r of rows) {
        const coord = extractCoord(r);
        const ts = r.timestamp;
        if (coord && typeof ts === 'string') {
          pts.push({ timestamp: ts, lon: coord[0], lat: coord[1] });
        }
      }
      pts.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
      setPoints(pts);
      setCursor(0);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setPoints([]);
    } finally {
      setLoading(false);
    }
  };

  // 播放推进
  useEffect(() => {
    if (!playing) {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }
    timerRef.current = setInterval(() => {
      setCursor((c) => {
        if (c >= points.length - 1) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, 1000 / speed);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [playing, speed, points.length]);

  const currentPoint = points[cursor];
  const progress = points.length > 0 ? ((cursor + 1) / points.length) * 100 : 0;

  const range = useMemo(() => {
    if (points.length === 0) return null;
    return { start: points[0].timestamp, end: points[points.length - 1].timestamp };
  }, [points]);

  if (!selectedVid || !selectedNode) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-center text-xs text-slate-400">
        选中一个对象以查看轨迹
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      <div className="text-xs font-medium text-slate-600">
        轨迹回放 · {selectedNode.api_name} <span className="font-mono">{selectedVid}</span>
      </div>

      {/* 序列属性输入 + 加载 */}
      <div className="flex gap-2">
        <input
          value={seriesProperty}
          onChange={(e) => setSeriesProperty(e.target.value)}
          placeholder="序列属性名（如 track）"
          className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs"
        />
        <button
          onClick={loadTrajectory}
          disabled={loading}
          className="rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? '加载中…' : '加载轨迹'}
        </button>
      </div>

      {error && <div className="text-xs text-red-600">❌ {error}</div>}

      {points.length > 0 && (
        <>
          {/* 当前点信息 */}
          <div className="rounded bg-slate-50 p-2 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-400">时间</span>
              <span className="font-mono">{currentPoint?.timestamp ?? '-'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">位置</span>
              <span className="font-mono">
                {currentPoint ? `${currentPoint.lon.toFixed(4)}, ${currentPoint.lat.toFixed(4)}` : '-'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">点数</span>
              <span>{cursor + 1} / {points.length}</span>
            </div>
          </div>

          {/* 时间轴 */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPlaying((p) => !p)}
              className="rounded bg-slate-700 px-2 py-1 text-xs text-white hover:bg-slate-800"
            >
              {playing ? '⏸' : '▶'}
            </button>
            <button
              onClick={() => { setPlaying(false); setCursor(0); }}
              className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
            >
              ⏮
            </button>
            <input
              type="range"
              min={0}
              max={points.length - 1}
              value={cursor}
              onChange={(e) => setCursor(Number(e.target.value))}
              className="flex-1"
            />
            <select
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="rounded border border-slate-300 px-1 py-0.5 text-xs"
            >
              <option value={0.5}>0.5×</option>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
              <option value={4}>4×</option>
            </select>
          </div>

          {/* 进度条 */}
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
            <div className="h-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} />
          </div>

          {range && (
            <div className="flex justify-between text-[10px] text-slate-400">
              <span>{range.start}</span>
              <span>{range.end}</span>
            </div>
          )}
        </>
      )}

      {points.length === 0 && !loading && !error && (
        <div className="text-xs text-slate-400">输入序列属性名后点「加载轨迹」</div>
      )}
    </div>
  );
}
