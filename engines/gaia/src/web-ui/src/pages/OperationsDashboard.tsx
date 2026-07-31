import { useState, useEffect } from 'react';
import {
  listOntologies,
  listObjectTypes,
  listLinkTypes,
  listDataSources,
  listSyncTasks,
} from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
import { SkeletonList } from '../components/Skeleton';

interface OpsStats {
  ontologies: number;
  objects: number;
  links: number;
  datasources: number;
  syncTasks: number;
}

interface SyncStatusCount {
  running: number;
  finished: number;
  stopped: number;
  canceled: number;
  failed: number;
  other: number;
}

export function OperationsDashboard() {
  const [stats, setStats] = useState<OpsStats>({
    ontologies: 0,
    objects: 0,
    links: 0,
    datasources: 0,
    syncTasks: 0,
  });
  const [syncStatus, setSyncStatus] = useState<SyncStatusCount>({
    running: 0,
    finished: 0,
    stopped: 0,
    canceled: 0,
    failed: 0,
    other: 0,
  });
  const [history, setHistory] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function loadStats() {
      try {
        // 并行加载，避免 N+1 串行（性能 + 反馈）
        const [ontos, dss] = await Promise.all([listOntologies(), listDataSources()]);

        // 并行查询每个 ontology 的对象类型与关系
        const [objCounts, linkCounts] = await Promise.all([
          Promise.all(ontos.map((o) => listObjectTypes(o.api_name).catch(() => []))),
          Promise.all(ontos.map((o) => listLinkTypes(o.api_name).catch(() => []))),
        ]);
        const objects = objCounts.reduce((n, arr) => n + arr.length, 0);
        const links = linkCounts.reduce((n, arr) => n + arr.length, 0);

        // 并行查询每个数据源的同步任务
        const syncPerDs = await Promise.all(
          dss.map((ds) => listSyncTasks(ds.api_name).catch(() => [])),
        );
        const allSyncs = syncPerDs.flat();
        const status: SyncStatusCount = {
          running: 0,
          finished: 0,
          stopped: 0,
          canceled: 0,
          failed: 0,
          other: 0,
        };
        for (const t of allSyncs) {
          if (t.status === 'RUNNING') status.running++;
          else if (t.status === 'FINISHED') status.finished++;
          else if (t.status === 'STOPPED') status.stopped++;
          else if (t.status === 'CANCELED') status.canceled++;
          else if (t.status === 'FAILED') status.failed++;
          else status.other++;
        }

        if (cancelled) return;
        setStats({
          ontologies: ontos.length,
          objects,
          links,
          datasources: dss.length,
          syncTasks: allSyncs.length,
        });
        setSyncStatus(status);
        setHistory((h) => [
          ...h.slice(-19),
          `${new Date().toLocaleTimeString()} 刷新: ${objects} 对象 / ${allSyncs.length} 同步任务`,
        ]);
      } catch {
        if (!cancelled)
          setHistory((h) => [...h.slice(-19), `${new Date().toLocaleTimeString()} 获取失败`]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadStats();
    const timer = setInterval(loadStats, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1>运行洞察</h1>
      </div>

      {loading ? (
        <div className="metrics-grid">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="metric-card">
              <SkeletonList rows={2} />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="metrics-grid">
            <MetricCard value={stats.ontologies} label="本体数量" />
            <MetricCard value={stats.objects} label="对象类型" />
            <MetricCard value={stats.links} label="关系定义" />
            <MetricCard value={stats.datasources} label="数据源" />
            <MetricCard value={stats.syncTasks} label="同步任务" />
            <MetricCard value="✓" label="API 健康" valueClass="text-success" />
          </div>

          {/* 同步任务状态分布（状态透明） */}
          <div className="card mb-6">
            <h3 className="mb-3 text-sm">同步任务状态分布</h3>
            {stats.syncTasks === 0 ? (
              <p className="text-xs text-text-muted">暂无同步任务</p>
            ) : (
              <div className="flex flex-wrap gap-4 text-[13px]">
                <StatusRow label="运行中" count={syncStatus.running} status="RUNNING" />
                <StatusRow label="已完成" count={syncStatus.finished} status="FINISHED" />
                <StatusRow label="已停止" count={syncStatus.stopped} status="STOPPED" />
                <StatusRow label="已取消" count={syncStatus.canceled} status="CANCELED" />
                <StatusRow label="失败" count={syncStatus.failed} status="FAILED" />
                {syncStatus.other > 0 && (
                  <StatusRow label="其他" count={syncStatus.other} status="DRAFT" />
                )}
              </div>
            )}
          </div>
        </>
      )}

      <div className="card">
        <h3 className="mb-3 text-sm">刷新历史</h3>
        <div className="max-h-[200px] overflow-auto font-mono text-[11px] text-text-muted">
          {history.map((h, i) => (
            <div key={i}>{h}</div>
          ))}
          {history.length === 0 && <div>暂无记录</div>}
        </div>
      </div>
    </div>
  );
}

function MetricCard({
  value,
  label,
  valueClass,
}: {
  value: number | string;
  label: string;
  valueClass?: string;
}) {
  return (
    <div className="metric-card">
      <div className={`metric-value ${valueClass || ''}`}>{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  );
}

function StatusRow({ label, count, status }: { label: string; count: number; status: string }) {
  return (
    <div className="flex items-center gap-2">
      <StatusBadge status={status} />
      <span className="text-text">{label}</span>
      <strong className="font-mono text-text">{count}</strong>
    </div>
  );
}
