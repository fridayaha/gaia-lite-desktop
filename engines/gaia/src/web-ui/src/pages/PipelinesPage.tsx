/**
 * PipelinesPage — 管道列表页面。
 *
 * 展示已创建的管道，支持搜索、筛选、新建、删除。
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listPipelines, deletePipeline } from '../api/client';
import type { PipelineResponse } from '../types/pipeline';

export function PipelinesPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<PipelineResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listPipelines({
        search: searchText || undefined,
        status: statusFilter || undefined,
        limit: 50,
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (err) {
      console.error('加载管道列表失败', err);
    } finally {
      setLoading(false);
    }
  }, [searchText, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = () => {
    navigate('/pipelines/new');
  };

  const handleDelete = async (apiName: string) => {
    if (!confirm(`确认删除管道 "${apiName}"？`)) return;
    try {
      await deletePipeline(apiName);
      void load();
    } catch (err) {
      console.error('删除失败', err);
    }
  };

  const statusLabel = (s: string) => {
    switch (s) {
      case 'PUBLISHED': return '已发布';
      case 'DRAFT': return '草稿';
      case 'DEPRECATED': return '已废弃';
      case 'ARCHIVED': return '已归档';
      default: return s;
    }
  };

  const statusColor = (s: string) => {
    switch (s) {
      case 'PUBLISHED': return 'bg-green-50 text-green-700';
      case 'DRAFT': return 'bg-amber-50 text-amber-700';
      case 'DEPRECATED': return 'bg-red-50 text-red-700';
      case 'ARCHIVED': return 'bg-slate-50 text-slate-500';
      default: return 'bg-slate-50 text-slate-600';
    }
  };

  return (
    <div className="main">
      <div className="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-lg font-semibold text-slate-800">管道管理</h1>
          <button
            onClick={handleCreate}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
          >
            + 新建管道
          </button>
          {total > 0 && (
            <span className="text-xs text-slate-400">共 {total} 个管道</span>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">所有状态</option>
            <option value="DRAFT">草稿</option>
            <option value="PUBLISHED">已发布</option>
            <option value="DEPRECATED">已废弃</option>
          </select>
          <div className="relative">
            <input
              type="text"
              placeholder="搜索管道..."
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm"
              style={{ width: 260 }}
            />
            {searchText.length === 0 && (
              <span
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400"
              >
                🔍
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }, (_, i) => (
            <div
              key={i}
              className="h-16 animate-pulse rounded-lg bg-slate-100"
            />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex h-64 items-center justify-center">
          <div className="text-center">
            <p className="text-sm text-slate-400">
              {searchText || statusFilter ? '没有匹配的管道' : '暂无管道'}
            </p>
            {!searchText && !statusFilter && (
              <button
                onClick={handleCreate}
                className="mt-2 text-sm text-blue-600 hover:underline"
              >
                创建第一个管道
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((p) => (
            <div
              key={p.api_name}
              className="flex cursor-pointer items-center gap-4 rounded-lg border border-slate-200 bg-white px-4 py-3 transition-shadow hover:shadow-sm"
              onClick={() => navigate(`/pipelines/${p.api_name}`)}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-sm">
                🔧
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-slate-800">
                    {p.display_name}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] ${statusColor(p.status)}`}>
                    {statusLabel(p.status)}
                  </span>
                </div>
                <div className="mt-0.5 text-xs text-slate-400">
                  {p.description || `输出: ${p.sink_dataset_api_name}`}
                </div>
                <div className="mt-0.5 text-[10px] text-slate-300">
                  v{p.current_version_number ?? '-'} · 更新于{' '}
                  {new Date(p.updated_at).toLocaleString()}
                </div>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(p.api_name);
                }}
                className="rounded p-1.5 text-xs text-slate-400 hover:bg-red-50 hover:text-red-600"
                title="删除"
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
