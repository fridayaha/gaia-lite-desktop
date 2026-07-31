/**
 * PathFinder — 路径推理面板（Phase 2d find_paths）。
 *
 * 输入源 rid + 目标 rid → 调 findPaths API → 显示最短路径（rid 序列）。
 * 可选限定 link_types + max_depth。
 */
import { useState } from 'react';
import { findPaths } from '../api/graph';

interface PathFinderProps {
  ontology: string;
  /** 当前选中节点 rid（预填源）。 */
  selectedVid: string | null;
  /** 画布所有节点 rid（供下拉选择）。 */
  nodeVids: string[];
}

export function PathFinder({ ontology, selectedVid, nodeVids }: PathFinderProps) {
  const [source, setSource] = useState(selectedVid ?? '');
  const [target, setTarget] = useState('');
  const [maxDepth, setMaxDepth] = useState(5);
  const [paths, setPaths] = useState<string[][]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleFind = async () => {
    if (!source || !target) return;
    setLoading(true);
    setError('');
    setPaths([]);
    try {
      const result = await findPaths(ontology, {
        source_key: source,
        target_key: target,
        max_depth: maxDepth,
      });
      setPaths(result.paths);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-4 rounded border border-slate-200 p-2">
      <div className="mb-1.5 text-xs font-semibold text-slate-500">🔍 路径推理</div>
      <div className="space-y-1.5">
        <div>
          <label className="text-[10px] text-slate-400">源节点</label>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="w-full rounded border border-slate-300 px-1.5 py-0.5 text-xs"
          >
            <option value="">选择源…</option>
            {nodeVids.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[10px] text-slate-400">目标节点</label>
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="w-full rounded border border-slate-300 px-1.5 py-0.5 text-xs"
          >
            <option value="">选择目标…</option>
            {nodeVids.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-[10px] text-slate-400">最大跳数</label>
          <input
            type="number"
            min={1}
            max={10}
            value={maxDepth}
            onChange={(e) => setMaxDepth(Number(e.target.value))}
            className="w-16 rounded border border-slate-300 px-1.5 py-0.5 text-xs"
          />
          <button
            onClick={handleFind}
            disabled={!source || !target || loading}
            className="ml-auto rounded bg-slate-700 px-2 py-0.5 text-xs text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {loading ? '查找中…' : '查找路径'}
          </button>
        </div>
      </div>

      {error && <div className="mt-1.5 text-[10px] text-red-600">❌ {error}</div>}

      {paths.length > 0 && (
        <div className="mt-2 space-y-1">
          <div className="text-[10px] text-slate-400">
            找到 {paths.length} 条最短路径
          </div>
          {paths.map((path, i) => (
            <div
              key={i}
              className="flex flex-wrap items-center gap-1 rounded bg-slate-50 p-1.5 text-[10px]"
            >
              {path.map((rid, j) => (
                <span key={j} className="flex items-center gap-1">
                  <span className="rounded bg-white px-1 py-0.5 font-mono text-slate-700 shadow-sm">
                    {rid}
                  </span>
                  {j < path.length - 1 && <span className="text-slate-400">→</span>}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}

      {!loading && paths.length === 0 && source && target && !error && (
        <div className="mt-1.5 text-[10px] text-slate-400">
          点击「查找路径」查询
        </div>
      )}
    </div>
  );
}
