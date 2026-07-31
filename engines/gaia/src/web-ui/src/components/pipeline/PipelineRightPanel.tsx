/**
 * PipelineAuxPanel — 管道辅助面板（Schema / 执行历史 / JSON 编辑）。
 *
 * 重构说明（2026-07）：
 *  - 原 PipelineRightPanel 是常驻右侧抽屉，含 configure/schema/history/json 四个 tab，
 *    与节点配置面板互斥抢位置，且 configure tab 是死代码。
 *  - 现配置走双击弹窗（NodeConfigModal），右侧抽屉移除以最大化画布留白。
 *  - 本面板改为「无外壳」的纯内容容器，由 PipelineAuxModal 包裹在弹窗中按需调出。
 *  - 去掉 configure tab，只保留 schema/history/json 三个辅助视图。
 */
import type {
  IRNode,
  IREdge,
  BuildResponse,
  ContractViolation,
} from '../../types/pipeline';
import { SchemaPreview } from './SchemaPreview';

export type AuxTab = 'schema' | 'history' | 'json';

interface PipelineAuxPanelProps {
  activeTab: AuxTab;
  onTabChange: (tab: AuxTab) => void;
  selectedNode: IRNode | null;
  irNodes: IRNode[];
  irEdges: IREdge[];
  builds: BuildResponse[];
  validationErrors: ContractViolation[];
  validationValid: boolean;
  pipelineStatus: string;
  jsonString: string;
  onJsonChange: (json: string) => void;
  onApplyJson: () => void;
}

export function PipelineAuxPanel({
  activeTab,
  onTabChange,
  selectedNode,
  irNodes,
  irEdges,
  builds,
  validationErrors,
  validationValid,
  pipelineStatus,
  jsonString,
  onJsonChange,
  onApplyJson,
}: PipelineAuxPanelProps) {
  const tabs: Array<{ id: AuxTab; label: string }> = [
    { id: 'schema', label: 'Schema' },
    { id: 'history', label: '执行' },
    { id: 'json', label: 'JSON' },
  ];

  return (
    <div className="flex h-full flex-col">
      {/* Tab 头 */}
      <div className="flex border-b border-slate-200">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex-1 py-2 text-xs ${
              activeTab === tab.id
                ? 'border-b-2 border-blue-500 font-medium text-blue-600'
                : 'text-slate-500 hover:bg-slate-50'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 内容 */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'schema' && (
          <SchemaTab
            nodes={irNodes}
            edges={irEdges}
            validationErrors={validationErrors}
            validationValid={validationValid}
            selectedNode={selectedNode}
          />
        )}

        {activeTab === 'history' && (
          <HistoryTab builds={builds} pipelineStatus={pipelineStatus} />
        )}

        {activeTab === 'json' && (
          <JsonTab jsonString={jsonString} onChange={onJsonChange} onApply={onApplyJson} />
        )}
      </div>
    </div>
  );
}

// ── Schema Tab（增强版，支持选中节点预览 + 全链路视图）──

function SchemaTab({
  nodes,
  edges: _edges,
  validationErrors,
  validationValid,
  selectedNode,
}: {
  nodes: IRNode[];
  edges: IREdge[];
  validationErrors: ContractViolation[];
  validationValid: boolean;
  selectedNode: IRNode | null;
}) {
  return (
    <div className="p-4">
      {/* 整体校验状态 */}
      <div className={`mb-4 rounded border px-3 py-2 text-xs ${
        validationValid
          ? 'border-green-200 bg-green-50 text-green-700'
          : 'border-red-200 bg-red-50 text-red-700'
      }`}>
        {validationValid ? '✅ Schema 校验通过' : '❌ Schema 校验有错误'}
      </div>

      {/* 选中节点的 Schema 预览（增强，design §14.8）*/}
      {selectedNode && (
        <div className="mb-4">
          <h4 className="mb-2 text-xs font-semibold text-slate-600">
            当前节点：{selectedNode.label}
          </h4>
          <SchemaPreview
            outputSchema={selectedNode.output_schema}
            errors={validationErrors.filter((e) => e.node_id === selectedNode.id)}
            nodeLabel={selectedNode.label}
            nodeType={selectedNode.type}
          />
        </div>
      )}

      {/* 全链路 Schema 总览 */}
      <div className="space-y-3">
        <h4 className="text-xs font-semibold text-slate-600">全链路 ({nodes.length} 节点)</h4>
        {nodes.length === 0 && (
          <p className="text-xs text-slate-400">从左侧算子面板拖入节点开始构建</p>
        )}
        {nodes.map((node) => {
          const nodeErrors = validationErrors.filter(
            (e) => e.node_id === node.id && !e.valid,
          );
          return (
            <div key={node.id} className="rounded border border-slate-200 p-2">
              <div className="mb-1 flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-medium text-slate-700">
                  {node.label}
                  {nodeErrors.length > 0 && (
                    <span className="text-red-500" title={nodeErrors.map((e) => e.message).join('; ')}>
                      ⚠
                    </span>
                  )}
                </span>
                <span className="text-[10px] text-slate-400">{node.operator_type}</span>
              </div>
              {node.output_schema && node.output_schema.fields.length > 0 ? (
                <div className="space-y-0.5">
                  {node.output_schema.fields.map((f) => (
                    <div key={f.name} className="flex items-center gap-2 text-[10px]">
                      <span className="font-mono text-slate-600">{f.name}</span>
                      <span className="text-slate-400">{f.data_type}</span>
                      {f.nullable && <span className="text-slate-300">NULL</span>}
                    </div>
                  ))}
                </div>
              ) : (
                <span className="text-[10px] text-slate-300">待推演</span>
              )}
              {/* 节点级错误 */}
              {nodeErrors.length > 0 && (
                <div className="mt-1 space-y-0.5">
                  {nodeErrors.map((e, i) => (
                    <div key={i} className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-600">
                      {e.message}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 全局校验错误汇总 */}
      {validationErrors.filter((e) => !e.valid && e.node_id === '').length > 0 && (
        <div className="mt-4">
          <h4 className="mb-1 text-xs font-semibold text-red-600">全局错误</h4>
          <div className="space-y-1">
            {validationErrors
              .filter((e) => !e.valid && e.node_id === '')
              .map((e, i) => (
                <div key={i} className="rounded bg-red-50 p-2 text-[10px] text-red-700">
                  {e.message}
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── History Tab ──

function HistoryTab({ builds, pipelineStatus }: { builds: BuildResponse[]; pipelineStatus: string }) {
  return (
    <div className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-xs font-semibold text-slate-600">构建历史</h4>
        {pipelineStatus === 'PUBLISHED' ? (
          <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] text-green-600">已部署</span>
        ) : (
          <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">未部署</span>
        )}
      </div>

      {builds.length === 0 ? (
        <p className="text-xs text-slate-400">暂无执行记录。点击「执行」按钮开始构建。</p>
      ) : (
        <div className="space-y-2">
          {builds.map((build) => (
            <div
              key={build.build_id}
              className="rounded border border-slate-200 p-2 text-xs"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-700">
                  #{build.version_number}
                </span>
                <span className={`rounded px-1 text-[10px] ${
                  build.status === 'SUCCESS' ? 'bg-green-50 text-green-600' :
                  build.status === 'FAILED' ? 'bg-red-50 text-red-600' :
                  build.status === 'RUNNING' ? 'bg-blue-50 text-blue-600' :
                  'bg-slate-50 text-slate-500'
                }`}>
                  {build.status}
                </span>
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                {build.started_at && new Date(build.started_at).toLocaleString()}
                {build.duration_ms != null && ` · ${Math.round(build.duration_ms / 1000)}s`}
              </div>
              {build.error_message && (
                <div className="mt-1 text-[10px] text-red-500">{build.error_message}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── JSON Tab ──

function JsonTab({
  jsonString,
  onChange,
  onApply,
}: {
  jsonString: string;
  onChange: (json: string) => void;
  onApply: () => void;
}) {
  return (
    <div className="flex h-full flex-col p-4">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-xs font-semibold text-slate-600">Pipeline IR (JSON)</h4>
        <button
          type="button"
          onClick={onApply}
          className="rounded bg-blue-600 px-2 py-0.5 text-[10px] text-white hover:bg-blue-700"
        >
          应用
        </button>
      </div>
      <textarea
        value={jsonString}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 resize-none rounded border border-slate-300 p-2 font-mono text-[11px] outline-none focus:border-blue-400"
        spellCheck={false}
      />
      <p className="mt-1 text-[10px] text-slate-400">
        编辑后点击「应用」同步到画布。修改不合法的 JSON 会忽略。
      </p>
    </div>
  );
}
