/**
 * 版本历史内联区（ADR Action Mutation Mapping P1）。
 *
 * 在 ActionTypeEditor 内联展示历史版本，支持回滚。调 listActionTypesVersions
 * + rollbackActionType。回滚后通知父组件重新加载草稿。
 */
import { useEffect, useState } from 'react';
import { listActionTypesVersions, rollbackActionType } from '../api/client';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { formatError } from '../lib/formatError';
import { cn } from '../lib/cn';
import type { ActionTypeVersion } from '../types';

export interface VersionHistoryInlineProps {
  ontology: string;
  actionApiName: string;
  currentVersion?: number | null;
  onRolledBack: () => void;
}

export function VersionHistoryInline({
  ontology,
  actionApiName,
  currentVersion,
  onRolledBack,
}: VersionHistoryInlineProps) {
  const { loading, error, run } = useAsyncAction();
  const [versions, setVersions] = useState<ActionTypeVersion[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadError(null);
      try {
        const vs = await listActionTypesVersions(ontology, actionApiName);
        if (!cancelled) setVersions(vs);
      } catch (e) {
        if (!cancelled) setLoadError(formatError(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ontology, actionApiName]);

  async function handleRollback(version: number) {
    if (!confirm(`确认回滚到 v${version}？将创建一个新版本（当前版本的快照保留）。`)) return;
    const res = await run(() => rollbackActionType(ontology, actionApiName, version));
    if (res) {
      onRolledBack();
    }
  }

  if (loadError) {
    return (
      <div className="mb-2 rounded-sm border border-error-border bg-error-bg px-2 py-1 text-xs text-error-text">
        {loadError}
      </div>
    );
  }

  return (
    <div className="mb-3 card p-2.5">
      <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
        版本历史
      </div>
      {versions.length === 0 ? (
        <p className="text-xs text-text-muted">无历史版本</p>
      ) : (
        <div className="flex flex-col gap-1">
          {[...versions]
            .sort((a, b) => b.version - a.version)
            .map((v) => {
              const isCurrent = v.version === currentVersion;
              return (
                <div
                  key={v.id}
                  className="flex items-center justify-between rounded px-2 py-1 text-xs hover:bg-[var(--bg)]"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold">v{v.version}</span>
                    {isCurrent && (
                      <span className="rounded-pill bg-[var(--accent-bg)] px-1.5 py-px text-[10px] text-accent-text">
                        当前
                      </span>
                    )}
                    <span className="text-text-muted">
                      {new Date(v.created_at).toLocaleString()}
                    </span>
                    <span className="text-text-muted">· {v.published_by}</span>
                  </div>
                  {!isCurrent && (
                    <button
                      className={cn('btn btn-xs btn-ghost', loading && 'is-loading')}
                      onClick={() => handleRollback(v.version)}
                      disabled={loading}
                    >
                      回滚
                    </button>
                  )}
                </div>
              );
            })}
        </div>
      )}
      {error && <div className="mt-1 text-xs text-error-text">{formatError(error)}</div>}
    </div>
  );
}
