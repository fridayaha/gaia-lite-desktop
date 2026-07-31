/**
 * EvidenceDrawer — 证据链快照抽屉（graph-reasoning-frontend-design.md §10.1 EvidenceDrawer）。
 *
 * 点击底栏「证据 {id}」弹出，展示 AnalysisRecord 详情：
 *  - 查询 IR（ObjectSet）摘要
 *  - 执行步骤明细（step / engine / elapsed / count）
 *  - 引擎使用 + 总耗时
 *  - evidence_pointers（matched_vids / object_count）
 *
 * 用于合规可追溯：用户可回看任意一次图探索的完整推理链。
 */
import { useEffect, useState } from 'react';
import type { AnalysisRecord } from '../types';
import { getAnalysis } from '../api/graph';

interface EvidenceDrawerProps {
  ontology: string;
  analysisId: string | null;
  onClose: () => void;
}

export function EvidenceDrawer({ ontology, analysisId, onClose }: EvidenceDrawerProps) {
  const [record, setRecord] = useState<AnalysisRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!analysisId) {
      setRecord(null);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    getAnalysis(ontology, analysisId)
      .then(setRecord)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [ontology, analysisId]);

  if (!analysisId) return null;

  const totalMs = record
    ? Object.values(record.result_summary.timings || {}).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <>
      {/* 遮罩 */}
      <div
        className="fixed inset-0 z-[var(--z-modal)] bg-black/40"
        onClick={onClose}
        aria-hidden
      />
      {/* 抽屉 */}
      <aside
        role="dialog"
        aria-label="证据链详情"
        className="fixed right-0 top-0 z-[calc(var(--z-modal)+1)] flex h-full w-[440px] max-w-[90vw] flex-col border-l border-slate-200 bg-white shadow-2xl"
      >
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">证据链详情</h2>
            <p className="font-mono text-xs text-slate-400">{analysisId}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto px-5 py-4 text-sm">
          {loading && <div className="text-slate-400">加载中…</div>}
          {error && <div className="text-red-600">❌ {error}</div>}
          {!loading && !error && record && (
            <div className="space-y-4">
              {/* 元信息 */}
              <Section title="基本信息">
                <Row label="本体" value={record.ontology_id} />
                <Row label="操作人" value={record.principal} />
                <Row label="创建时间" value={new Date(record.created_at).toLocaleString('zh-CN')} />
                <Row label="总耗时" value={`${totalMs.toFixed(0)} ms`} />
              </Section>

              {/* 引擎 */}
              <Section title="使用引擎">
                <div className="flex flex-wrap gap-1.5">
                  {(record.result_summary.engines_used || []).map((eng) => (
                    <span
                      key={eng}
                      className="rounded bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700"
                    >
                      {eng}
                    </span>
                  ))}
                  {(!record.result_summary.engines_used || record.result_summary.engines_used.length === 0) && (
                    <span className="text-xs text-slate-400">无</span>
                  )}
                </div>
              </Section>

              {/* 结果统计 */}
              <Section title="结果统计">
                <Row label="对象总数" value={String(record.result_summary.total_vids ?? 0)} />
                <Row label="已水合" value={String(record.result_summary.hydrated ?? 0)} />
                <Row label="步骤数" value={String(record.result_summary.steps ?? 0)} />
                {record.result_summary.truncated && (
                  <span className="text-amber-600">⚠ 结果已截断</span>
                )}
              </Section>

              {/* 执行步骤明细 */}
              {record.result_summary.steps_detail &&
                record.result_summary.steps_detail.length > 0 && (
                  <Section title="执行步骤">
                    <div className="overflow-hidden rounded border border-slate-200">
                      <table className="w-full text-xs">
                        <thead className="bg-slate-50 text-slate-500">
                          <tr>
                            <th className="px-2 py-1 text-left">步骤</th>
                            <th className="px-2 py-1 text-left">引擎</th>
                            <th className="px-2 py-1 text-right">耗时</th>
                            <th className="px-2 py-1 text-right">数量</th>
                          </tr>
                        </thead>
                        <tbody>
                          {record.result_summary.steps_detail.map((s, i) => (
                            <tr key={i} className="border-t border-slate-100">
                              <td className="px-2 py-1 font-mono text-slate-700">{s.step}</td>
                              <td className="px-2 py-1 text-slate-500">{s.engine}</td>
                              <td className="px-2 py-1 text-right tabular-nums text-slate-500">
                                {s.elapsed.toFixed(0)}ms
                              </td>
                              <td className="px-2 py-1 text-right tabular-nums text-slate-500">
                                {s.count}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Section>
                )}

              {/* 证据指针 */}
              <Section title="证据指针">
                <Row label="对象计数" value={String(record.evidence_pointers?.object_count ?? 0)} />
                {record.evidence_pointers?.matched_vids &&
                  record.evidence_pointers.matched_vids.length > 0 && (
                    <div className="mt-1">
                      <div className="mb-1 text-xs text-slate-400">
                        匹配 rid（{record.evidence_pointers.matched_vids.length}）
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {record.evidence_pointers.matched_vids.slice(0, 50).map((rid) => (
                          <span
                            key={rid}
                            className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600"
                          >
                            {rid}
                          </span>
                        ))}
                        {record.evidence_pointers.matched_vids.length > 50 && (
                          <span className="text-xs text-slate-400">
                            …+{record.evidence_pointers.matched_vids.length - 50}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
              </Section>

              {/* 查询 IR */}
              <Section title="查询 IR（ObjectSet）">
                <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-600">
                  {JSON.stringify(record.object_set_ir, null, 2)}
                </pre>
              </Section>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title}
      </h3>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2 text-xs">
      <span className="text-slate-400">{label}</span>
      <span className="text-right font-medium text-slate-700">{value}</span>
    </div>
  );
}
