/**
 * PipelineToolbar — 管道画布顶部工具栏。
 *
 * 含：管道名称、保存/部署/执行按钮、undo/redo、写入模式切换等。
 */
interface PipelineToolbarProps {
  pipelineName: string;
  pipelineStatus: string;
  isDirty: boolean;
  validationValid: boolean;
  loading: boolean;
  onSave: () => void;
  onDeploy: () => void;
  onBuild: () => void;
  onUndo: () => void;
  onRedo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onToggleAi?: () => void;
  showAiChat?: boolean;
  /** 打开辅助视图弹窗（Schema/执行/JSON），传入要定位的 tab。 */
  onOpenAux?: (tab: 'schema' | 'history' | 'json') => void;
}

export function PipelineToolbar({
  pipelineName,
  pipelineStatus,
  isDirty,
  validationValid,
  loading,
  onSave,
  onDeploy,
  onBuild,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  onToggleAi,
  showAiChat,
  onOpenAux,
}: PipelineToolbarProps) {
  const statusLabel =
    pipelineStatus === 'PUBLISHED' ? '已发布' :
    pipelineStatus === 'DRAFT' ? '草稿' :
    pipelineStatus;

  const statusColor =
    pipelineStatus === 'PUBLISHED' ? 'text-green-600 bg-green-50' :
    pipelineStatus === 'DRAFT' ? 'text-amber-600 bg-amber-50' :
    'text-slate-600 bg-slate-50';

  return (
    <div className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
      {/* 管道名称 */}
      <div className="flex items-center gap-2">
        <span className="max-w-48 truncate text-sm font-semibold text-slate-800">
          {pipelineName || '未命名管道'}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${statusColor}`}>
          {statusLabel}
        </span>
        {isDirty && (
          <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-600">
            未保存
          </span>
        )}
      </div>

      {/* 状态验证 */}
      {!validationValid && (
        <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-600">
          Schema 校验未通过
        </span>
      )}

      <div className="flex-1" />

      {/* 操作按钮 */}
      <button
        type="button"
        onClick={onUndo}
        disabled={!canUndo}
        className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-30"
        title="撤销"
      >
        ↩
      </button>
      <button
        type="button"
        onClick={onRedo}
        disabled={!canRedo}
        className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-30"
        title="重做"
      >
        ↪
      </button>

      <div className="h-4 w-px bg-slate-200" />

      {/* 辅助视图：Schema / 执行历史 / JSON（原右侧抽屉，改为按需弹窗以最大化画布留白） */}
      {onOpenAux && (
        <>
          <button
            type="button"
            onClick={() => onOpenAux('schema')}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            title="Schema 预览与校验"
          >
            Schema
          </button>
          <button
            type="button"
            onClick={() => onOpenAux('history')}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            title="执行历史"
          >
            执行
          </button>
          <button
            type="button"
            onClick={() => onOpenAux('json')}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            title="JSON 编辑"
          >
            JSON
          </button>
        </>
      )}

      {onToggleAi && (
        <button
          type="button"
          onClick={onToggleAi}
          className={`rounded border px-2 py-1 text-xs ${
            showAiChat
              ? 'border-purple-300 bg-purple-50 text-purple-700'
              : 'border-slate-300 text-slate-600 hover:bg-slate-50'
          }`}
          title="AI 助手"
        >
          🤖 AI
        </button>
      )}

      <div className="h-4 w-px bg-slate-200" />

      <button
        type="button"
        onClick={onSave}
        disabled={!isDirty || loading}
        className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
      >
        {loading ? '保存中...' : '💾 保存'}
      </button>
      <button
        type="button"
        onClick={onDeploy}
        disabled={!validationValid || loading}
        className="rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        title="部署到 Kestra（不执行，只更新逻辑）"
      >
        🚀 部署
      </button>
      <button
        type="button"
        onClick={onBuild}
        disabled={!validationValid || loading}
        className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50"
        title="执行构建（物化数据）"
      >
        ▶ 执行
      </button>
    </div>
  );
}
