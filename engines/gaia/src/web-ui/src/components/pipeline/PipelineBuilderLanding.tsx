/**
 * PipelineBuilderLanding — Pipeline Builder landing 模式（新建管道）。
 *
 * 设计文档 §14.2.1：用户输入自然语言描述 → 切到 editing 模式 →
 * AG-UI Agent 接管生成管道草稿。
 *
 * 对标图探索页面的 ExploreLanding 组件。
 */
import { useCallback, useState } from 'react';
import type { DatasetGovernance } from '../../types';

interface PipelineBuilderLandingProps {
  datasets: Pick<DatasetGovernance, 'api_name' | 'display_name'>[];
  /** landing 模式下的自然语言输入提交。 */
  onStartWithPrompt: (prompt: string) => void;
  /** 手动开始（空画布）。 */
  onStartBlank: () => void;
}

const EXAMPLES = [
  '清洗客户数据，过滤 inactive 状态，关联订单表计算总消费，输出到 customer_analysis',
  '每日从 orders_raw 同步订单，过滤异常订单，按地区聚合销售额',
  '从员工表取数据，关联部门表，计算各部门平均薪资',
];

export function PipelineBuilderLanding({
  datasets,
  onStartWithPrompt,
  onStartBlank,
}: PipelineBuilderLandingProps) {
  const [prompt, setPrompt] = useState('');

  const handleSubmit = useCallback(() => {
    if (prompt.trim()) {
      onStartWithPrompt(prompt.trim());
    }
  }, [prompt, onStartWithPrompt]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className="flex h-full flex-col items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-2xl">
        {/* 标题 */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold text-slate-800">
            数据管道构建器
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            描述你需要的数据管道，AI 帮你自动构建；或者从空白开始拖拽编辑
          </p>
        </div>

        {/* 输入框 */}
        <div className="mb-6">
          <div className="relative">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="例如：清洗客户数据，过滤 inactive，关联订单算总消费"
              rows={3}
              className="w-full resize-none rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm outline-none transition-shadow focus:border-blue-400 focus:shadow-sm focus:ring-1 focus:ring-blue-200"
            />
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!prompt.trim()}
              className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              🚀 AI 构建
            </button>
            <button
              type="button"
              onClick={onStartBlank}
              className="rounded-lg border border-slate-300 bg-white px-5 py-2 text-sm text-slate-600 hover:bg-slate-50"
            >
              从空白开始
            </button>
          </div>
        </div>

        {/* 示例 */}
        <div className="mb-8">
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
            示例
          </h3>
          <div className="space-y-2">
            {EXAMPLES.map((ex, i) => (
              <button
                key={i}
                type="button"
                onClick={() => {
                  setPrompt(ex);
                  onStartWithPrompt(ex);
                }}
                className="w-full rounded-lg border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-600 transition-colors hover:border-blue-200 hover:bg-blue-50"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>

        {/* 数据集提示 */}
        {datasets.length > 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
              可用数据集 ({datasets.length})
            </h3>
            <div className="flex flex-wrap gap-2">
              {datasets.map((ds) => (
                <span
                  key={ds.api_name}
                  className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600"
                >
                  📦 {ds.display_name || ds.api_name}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
