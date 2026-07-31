import { StatusBadge } from './StatusBadge';
import { cn } from '../lib/cn';
import { CONNECTOR_META } from '../constants/connectorCatalog';
import type { DataSource } from '../types';

/**
 * 数据源卡片。
 *
 * 两种形态（variant）：
 *  - `list`（默认，列表页用）：高密度可扫描行，仅展示连接性 + 连接目标 +
 *    资产概览（同步任务 / 虚拟表计数 + 最近同步）+ 测试连接 / 进入详情 / 删除。
 *    能力操作（浏览 Schema / 新建同步 / CDC / 虚拟表）收进详情页，避免列表页噪音（渐进式披露）。
 *  - `detail`（详情页用）：保留 children 容纳 tabs / explore / sync / settings。
 *
 * 资产概览（assetsSummary）由列表页从已加载的 SyncTask[] + Dataset[] 推导后传入，
 * 避免卡片自身耦合同步任务/虚拟表渲染（第二原则：组件只渲染 + 交互，不绑定布局）。
 */
interface DataSourceCardProps {
  ds: DataSource;
  variant?: 'list' | 'detail';
  /** 列表形态：数据源衍生资产概览（同步任务 + 虚拟表）。 */
  assetsSummary?: {
    /** 同步任务数。 */
    syncCount: number;
    /** 已登记虚拟表数。 */
    virtualTableCount: number;
    /** 最近一次同步的展示文案，无则 undefined。 */
    lastRunLabel?: string;
    /** 最近一次同步状态（用于色点），无则 undefined。 */
    lastStatus?: string;
  };
  onTestConnection?: () => void;
  /** 列表形态：进入详情。 */
  onClick?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
  /** 测试连接进行中（用于禁用 + 文案）。 */
  testing?: boolean;
  children?: React.ReactNode;
}

/** 从 connector_config 推导人类可读的连接目标串。 */
function describeTarget(ds: DataSource): string {
  const c = ds.connector_config;
  if (c.host) return `${c.host}${c.port ? ':' + c.port : ''}${c.database ? '/' + c.database : ''}`;
  if (c.endpoint) return `${c.endpoint}${c.bucket ? '/' + c.bucket : ''}`;
  if (c.bootstrap_servers) return `brokers: ${c.bootstrap_servers}`;
  return '';
}

export function DataSourceCard({
  ds,
  variant = 'list',
  assetsSummary,
  onTestConnection,
  onClick,
  onEdit,
  onDelete,
  testing = false,
  children,
}: DataSourceCardProps) {
  const meta = CONNECTOR_META[ds.connector_type];
  const icon = meta?.icon || '🔌';
  const typeLabel = meta?.label || ds.connector_type.toUpperCase();
  const target = describeTarget(ds);
  const isList = variant === 'list';

  return (
    <div className={cn('ds-card', isList && 'ds-card-list')}>
      {/* Header row */}
      <div className="ds-card-header">
        <div
          className={cn('ds-card-title', onClick ? 'cursor-pointer' : 'cursor-default')}
          onClick={onClick}
          title={onClick ? '查看详情' : undefined}
        >
          <span className="text-lg leading-none">{icon}</span>
          <span
            className={cn(
              'status-dot',
              ds.status === 'CONNECTED'
                ? 'connected'
                : ds.status === 'ERROR'
                  ? 'error'
                  : 'disconnected',
            )}
          />
          <span className="ds-card-name">{ds.display_name}</span>
          <code className="font-mono text-[11px] text-text-muted">{ds.api_name}</code>
          <span className="badge ds-card-type">{typeLabel}</span>
          <StatusBadge status={ds.status} />
        </div>

        <div className="ds-card-actions">
          {onTestConnection && (
            <button className="btn btn-sm" onClick={onTestConnection} disabled={testing}>
              {testing ? '测试中…' : '测试连接'}
            </button>
          )}
          {onClick && (
            <button className="btn btn-sm" onClick={onClick} title="查看详情">
              查看详情 ↗
            </button>
          )}
          {onEdit && (
            <button className="btn btn-sm" onClick={onEdit} title="编辑连接配置">
              编辑
            </button>
          )}
          {onDelete && (
            <>
              <span className="mx-0.5 h-4 w-px self-center bg-border" aria-hidden="true" />
              <button
                className="btn btn-sm border-error text-error"
                aria-label={`删除数据源 ${ds.display_name}`}
                title="删除（高危，需二次确认）"
                onClick={onDelete}
              >
                删除
              </button>
            </>
          )}
        </div>
      </div>

      {/* Meta row: 连接目标 + 描述 */}
      {(target || ds.description) && (
        <div className="ds-card-meta">
          {target && <code className="ds-card-target">{target}</code>}
          {ds.description && <span className="ds-card-desc">{ds.description}</span>}
        </div>
      )}

      {ds.status === 'ERROR' && <div className="mb-1 text-xs text-error">⚠ 连接异常</div>}

      {/* List 形态：资产概览摘要行（同步任务 + 虚拟表，点击进详情） */}
      {isList && assetsSummary && (
        <button
          className="ds-card-sync-summary"
          onClick={onClick}
          disabled={!onClick}
          title={onClick ? '查看详情' : undefined}
        >
          <span className="ds-card-sync-count">
            {assetsSummary.syncCount > 0
              ? `${assetsSummary.syncCount} 个同步任务`
              : '暂无同步任务'}
          </span>
          {assetsSummary.virtualTableCount > 0 && (
            <>
              <span className="ds-card-sync-sep">·</span>
              <span className="ds-card-asset-link" title="已登记的虚拟表（VIRTUAL 联邦不落地）">
                🔗 {assetsSummary.virtualTableCount} 个虚拟表
              </span>
            </>
          )}
          {assetsSummary.lastRunLabel && (
            <>
              <span className="ds-card-sync-sep">·</span>
              <span
                className={cn(
                  'status-dot',
                  assetsSummary.lastStatus === 'RUNNING'
                    ? 'connected'
                    : assetsSummary.lastStatus === 'FAILED'
                      ? 'error'
                      : 'disconnected',
                )}
              />
              <span className="text-text-muted">{assetsSummary.lastRunLabel}</span>
            </>
          )}
          {assetsSummary.syncCount === 0 && assetsSummary.virtualTableCount === 0 && (
            <span className="ds-card-sync-hint">点击创建 →</span>
          )}
        </button>
      )}

      {/* Detail 形态：tabs / explore / sync / settings（由父组件 children 注入） */}
      {children}
    </div>
  );
}
