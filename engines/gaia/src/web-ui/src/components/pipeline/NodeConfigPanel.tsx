/**
 * NodeConfigPanel — 节点配置面板。
 *
 * 当用户点击画布上的节点时，右侧显示此面板，允许用户编辑节点参数。
 * 不同算子类型显示不同的配置表单。
 *
 * 设计原则（参考 Palantir Foundry / Databricks Lakeflow Designer / Dataiku）：
 * - 所有"列引用"用下拉选择（数据来自上游 schema，由 validate 接口回填到 node.input_schemas）
 * - 结构化条件替代手写 SQL（防注入 + 防拼写错误）
 * - 自由表达式保留（Filter/Expression 高级模式），但提示可用列名
 * - "把简单留给用户，把复杂留给系统"
 */
import { useCallback } from 'react';
import type { IRNode, IREdge, Schema, SchemaField, FilterCondition, JoinCondition, SortKey } from '../../types/pipeline';
import type { DatasetGovernance } from '../../types';
import { getNodeDef } from './NodeRegistry';

type DatasetOption = Pick<DatasetGovernance, 'api_name' | 'display_name'>;

interface NodeConfigPanelProps {
  node: IRNode;
  datasets: DatasetOption[];
  /** 每个节点的输出 Schema（node_id → Schema），由 validate 接口回填。 */
  nodeSchemas: Record<string, Schema>;
  /** 边列表（用于推导某节点的上游输入 schema）。 */
  irEdges: IREdge[];
  onChange: (nodeId: string, updates: Partial<IRNode>) => void;
  onClose: () => void;
}

export function NodeConfigPanel({ node, datasets, nodeSchemas, irEdges, onChange, onClose }: NodeConfigPanelProps) {
  const handleLabelChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onChange(node.id, { label: e.target.value });
    },
    [node.id, onChange],
  );

  const handleConfigChange = useCallback(
    (field: string, value: unknown) => {
      onChange(node.id, { config: { ...node.config, [field]: value } });
    },
    [node.id, node.config, onChange],
  );

  return (
    <div className="flex h-full flex-col bg-white">
      {/* 面板头部 */}
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-slate-800">{node.label}</h3>
          <p className="text-[11px] text-slate-500">{node.operator_type}</p>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-600"
        >
          ✕
        </button>
      </div>
      {/* 配置表单 */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* 节点名称 */}
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-slate-600">节点名称</label>
          <input
            type="text"
            value={node.label}
            onChange={handleLabelChange}
            className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-200"
          />
        </div>

        {/* 算子特有配置 */}
        {renderOperatorConfig(node, datasets, nodeSchemas, irEdges, handleConfigChange)}
      </div>
    </div>
  );
}

/** 从 nodeSchemas + irEdges 推导某节点的上游输入列名列表。 */
function getColumnNames(node: IRNode, nodeSchemas: Record<string, Schema>, irEdges: IREdge[], inputIndex = 0): string[] {
  return getColumns(node, nodeSchemas, irEdges, inputIndex).map((f) => f.name);
}

/** 从 nodeSchemas + irEdges 推导某节点的上游输入列字段（含类型信息）。 */
function getColumns(node: IRNode, nodeSchemas: Record<string, Schema>, irEdges: IREdge[], inputIndex = 0): SchemaField[] {
  // 找到本节点的上游节点 id（按边的顺序），取第 inputIndex 个上游的 output schema
  const upstreamIds = irEdges
    .filter((e) => e.target_id === node.id)
    .map((e) => e.source_id);
  const upstreamId = upstreamIds[inputIndex];
  if (!upstreamId) return [];
  const schema = nodeSchemas[upstreamId];
  return schema?.fields ?? [];
}

/** 列下拉选择器。schema 未回填时显示提示。 */
function ColumnSelect({
  value,
  columns,
  onChange,
  placeholder = '选择列...',
  allowEmpty = false,
}: {
  value: string;
  columns: string[];
  onChange: (v: string) => void;
  placeholder?: string;
  allowEmpty?: boolean;
}) {
  if (columns.length === 0) {
    return (
      <select disabled className="w-full rounded border border-slate-300 bg-slate-50 px-2 py-1.5 text-sm text-slate-400">
        <option>请先连接上游节点</option>
      </select>
    );
  }
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
    >
      <option value="">{placeholder}</option>
      {allowEmpty && <option value="__all__">所有列</option>}
      {columns.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );
}

/** 根据 operator_type 渲染对应的配置表单。 */
function renderOperatorConfig(
  node: IRNode,
  datasets: DatasetOption[],
  nodeSchemas: Record<string, Schema>,
  irEdges: IREdge[],
  onChange: (field: string, value: unknown) => void,
) {
  const def = node.operator_type ? getNodeDef(node.operator_type) : undefined;
  if (def?.configComponent) {
    const ConfigForm = def.configComponent;
    return (
      <ConfigForm
        node={node}
        datasets={datasets.map((d) => d.api_name)}
        onChange={(_nodeId, updates) => {
          if (updates.config) {
            Object.entries(updates.config).forEach(([k, v]) => onChange(k, v));
          }
        }}
      />
    );
  }

  switch (node.operator_type) {
    case 'Source':
      return <SourceConfig node={node} datasets={datasets} onChange={onChange} />;
    case 'Filter':
      return <FilterConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Select':
      return <SelectConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Rename':
      return <RenameConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'TypeCast':
      return <TypeCastConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Join':
      return <JoinConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Aggregate':
      return <AggregateConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Expression':
      return <ExpressionConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Deduplicate':
      return <DeduplicateConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Sort':
      return <SortConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    case 'Sink':
      return <SinkConfig node={node} datasets={datasets} onChange={onChange} />;
    case 'QualityCheck':
      return <QualityCheckConfig node={node} nodeSchemas={nodeSchemas} irEdges={irEdges} onChange={onChange} />;
    default:
      return <GenericConfig node={node} onChange={onChange} />;
  }
}

// ── Source 配置 ──

function SourceConfig({
  node,
  datasets,
  onChange,
}: {
  node: IRNode;
  datasets: DatasetOption[];
  onChange: (field: string, value: unknown) => void;
}) {
  const selectedApiName = (node.config.extra?.dataset as string | undefined) ?? '';
  // 按 display_name 排序，display_name 相同的按 api_name 排
  const sorted = [...datasets].sort((a, b) => {
    const na = a.display_name || a.api_name;
    const nb = b.display_name || b.api_name;
    return na.localeCompare(nb, 'zh-CN');
  });

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">数据集</label>
        <select
          value={selectedApiName}
          onChange={(e) =>
            onChange('extra', { ...(node.config.extra ?? {}), dataset: e.target.value })
          }
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
        >
          <option value="">选择数据集...</option>
          {sorted.map((ds) => (
            <option key={ds.api_name} value={ds.api_name}>
              {ds.display_name || ds.api_name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

// ── Filter 配置（结构化条件构建器 + 高级表达式回退） ──

const FILTER_OPERATORS: Array<{ value: FilterCondition['operator']; label: string; needsValue: boolean }> = [
  { value: 'eq', label: '等于 =', needsValue: true },
  { value: 'neq', label: '不等于 ≠', needsValue: true },
  { value: 'gt', label: '大于 >', needsValue: true },
  { value: 'gte', label: '大于等于 ≥', needsValue: true },
  { value: 'lt', label: '小于 <', needsValue: true },
  { value: 'lte', label: '小于等于 ≤', needsValue: true },
  { value: 'in', label: '属于 (多个值)', needsValue: false },
  { value: 'not_in', label: '不属于', needsValue: false },
  { value: 'is_null', label: '为空', needsValue: false },
  { value: 'is_not_null', label: '不为空', needsValue: false },
  { value: 'contains', label: '包含', needsValue: true },
  { value: 'not_contains', label: '不包含', needsValue: true },
  { value: 'starts_with', label: '以...开头', needsValue: true },
  { value: 'ends_with', label: '以...结尾', needsValue: true },
];

function FilterConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumnNames(node, nodeSchemas, irEdges, 0);
  const conditions = node.config.filter_conditions ?? [];
  const useAdvanced = conditions.length === 0 && !!node.config.expression;

  if (useAdvanced) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium text-slate-600">过滤条件（高级表达式）</label>
          <button
            type="button"
            onClick={() => {
              onChange('expression', null);
              onChange('filter_conditions', [{ column: '', operator: 'eq', value: '' }]);
            }}
            className="text-[11px] text-blue-600 hover:underline"
          >
            切换为条件构建器
          </button>
        </div>
        <textarea
          value={node.config.expression ?? ''}
          onChange={(e) => onChange('expression', e.target.value || null)}
          placeholder='例如: status = "active" AND amount > 100'
          rows={3}
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
        />
        <p className="text-[10px] text-slate-400">可用列：{columns.length > 0 ? columns.join(', ') : '（请先连接上游）'}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-slate-600">过滤条件</label>
        <button
          type="button"
          onClick={() => {
            onChange('expression', null);
            onChange('filter_conditions', [
              ...conditions,
              { column: '', operator: 'eq', value: '' } as FilterCondition,
            ]);
          }}
          className="text-[11px] text-blue-600 hover:underline"
        >
          + 添加条件
        </button>
      </div>
      {conditions.map((cond, i) => {
        const opDef = FILTER_OPERATORS.find((o) => o.value === cond.operator);
        const needsValues = cond.operator === 'in' || cond.operator === 'not_in';
        return (
          <div key={i} className="space-y-1.5 rounded border border-slate-200 bg-slate-50 p-2">
            <div className="flex items-center gap-2">
              <ColumnSelect
                value={cond.column}
                columns={columns}
                onChange={(v) => {
                  const next = [...conditions];
                  next[i] = { ...next[i], column: v };
                  onChange('filter_conditions', next);
                }}
              />
              <button
                type="button"
                onClick={() => {
                  onChange('filter_conditions', conditions.filter((_, idx) => idx !== i));
                }}
                className="text-xs text-red-400 hover:text-red-600"
              >
                ✕
              </button>
            </div>
            <select
              value={cond.operator}
              onChange={(e) => {
                const next = [...conditions];
                next[i] = { ...next[i], operator: e.target.value as FilterCondition['operator'] };
                onChange('filter_conditions', next);
              }}
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm outline-none focus:border-blue-400"
            >
              {FILTER_OPERATORS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {opDef?.needsValue && (
              <input
                type="text"
                value={String(cond.value ?? '')}
                placeholder="值"
                onChange={(e) => {
                  const next = [...conditions];
                  // 尝试解析为数字，否则保留字符串
                  const raw = e.target.value;
                  const num = Number(raw);
                  next[i] = { ...next[i], value: raw !== '' && !isNaN(num) ? num : raw };
                  onChange('filter_conditions', next);
                }}
                className="w-full rounded border border-slate-300 px-2 py-1 text-sm outline-none focus:border-blue-400"
              />
            )}
            {needsValues && (
              <input
                type="text"
                value={Array.isArray(cond.values) ? cond.values.join(', ') : ''}
                placeholder="值1, 值2, 值3"
                onChange={(e) => {
                  const next = [...conditions];
                  next[i] = {
                    ...next[i],
                    values: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                  };
                  onChange('filter_conditions', next);
                }}
                className="w-full rounded border border-slate-300 px-2 py-1 text-sm outline-none focus:border-blue-400"
              />
            )}
          </div>
        );
      })}
      {conditions.length === 0 && (
        <p className="text-[10px] text-slate-400">点击「+ 添加条件」创建过滤规则</p>
      )}
    </div>
  );
}

// ── Select 配置（列复选框列表） ──

function SelectConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumns(node, nodeSchemas, irEdges, 0);
  const selected = node.config.columns ?? [];

  if (columns.length === 0) {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-400">
        请先连接上游节点以选择列
      </div>
    );
  }

  const toggle = (name: string) => {
    if (selected.includes(name)) {
      onChange('columns', selected.filter((c) => c !== name));
    } else {
      onChange('columns', [...selected, name]);
    }
  };

  return (
    <div className="space-y-1">
      <div className="mb-1 flex items-center justify-between">
        <label className="text-xs font-medium text-slate-600">保留的列</label>
        <button
          type="button"
          onClick={() =>
            onChange('columns', selected.length === columns.length ? [] : columns.map((c) => c.name))
          }
          className="text-[11px] text-blue-600 hover:underline"
        >
          {selected.length === columns.length ? '取消全选' : '全选'}
        </button>
      </div>
      <div className="max-h-60 space-y-1 overflow-y-auto rounded border border-slate-200 p-2">
        {columns.map((col) => (
          <label key={col.name} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={selected.includes(col.name)}
              onChange={() => toggle(col.name)}
              className="rounded"
            />
            <span className="flex-1 truncate text-slate-700">{col.name}</span>
            <span className="text-[10px] text-slate-400">{col.data_type}</span>
          </label>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-slate-400">
        已选 {selected.length}/{columns.length} 列，留空表示保留所有列
      </p>
    </div>
  );
}

// ── Rename 配置（原列名下拉 + 新列名输入） ──

function RenameConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumnNames(node, nodeSchemas, irEdges, 0);
  const mapping = node.config.column_mapping ?? {};
  const entries = Object.entries(mapping);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-slate-600">列重命名</label>
        <button
          type="button"
          onClick={() => onChange('column_mapping', { ...mapping, '': '' })}
          className="text-[11px] text-blue-600 hover:underline"
        >
          + 添加
        </button>
      </div>
      {entries.map(([oldName, newName], i) => (
        <div key={i} className="flex items-center gap-1">
          <ColumnSelect
            value={oldName}
            columns={columns}
            placeholder="原列名"
            onChange={(v) => {
              const next = { ...mapping };
              delete next[oldName];
              next[v] = newName;
              onChange('column_mapping', next);
            }}
          />
          <span className="text-xs text-slate-400">→</span>
          <input
            type="text"
            value={newName}
            placeholder="新列名"
            onChange={(e) => onChange('column_mapping', { ...mapping, [oldName]: e.target.value })}
            className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm outline-none focus:border-blue-400"
          />
          <button
            type="button"
            onClick={() => {
              const next = { ...mapping };
              delete next[oldName];
              onChange('column_mapping', Object.keys(next).length > 0 ? next : null);
            }}
            className="text-xs text-red-400 hover:text-red-600"
          >
            ✕
          </button>
        </div>
      ))}
      {entries.length === 0 && (
        <p className="text-[10px] text-slate-400">点击「+ 添加」开始重命名列</p>
      )}
    </div>
  );
}

// ── TypeCast 配置（多列转换：列下拉 + 类型下拉） ──

const DATA_TYPES = [
  'STRING', 'INTEGER', 'LONG', 'FLOAT', 'DOUBLE', 'DECIMAL',
  'BOOLEAN', 'TIMESTAMP', 'DATE',
];

function TypeCastConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumnNames(node, nodeSchemas, irEdges, 0);
  const casts = node.config.cast_columns ?? [];

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-slate-600">类型转换</label>
        <button
          type="button"
          onClick={() =>
            onChange('cast_columns', [...casts, { column: '', target_type: 'STRING' }])
          }
          className="text-[11px] text-blue-600 hover:underline"
        >
          + 添加
        </button>
      </div>
      {casts.map((cast, i) => (
        <div key={i} className="flex items-center gap-1">
          <ColumnSelect
            value={cast.column}
            columns={columns}
            placeholder="选择列"
            onChange={(v) => {
              const next = [...casts];
              next[i] = { ...next[i], column: v };
              onChange('cast_columns', next);
            }}
          />
          <span className="text-xs text-slate-400">→</span>
          <select
            value={cast.target_type}
            onChange={(e) => {
              const next = [...casts];
              next[i] = { ...next[i], target_type: e.target.value };
              onChange('cast_columns', next);
            }}
            className="w-28 rounded border border-slate-300 px-1 py-1.5 text-sm outline-none focus:border-blue-400"
          >
            {DATA_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => onChange('cast_columns', casts.filter((_, idx) => idx !== i))}
            className="text-xs text-red-400 hover:text-red-600"
          >
            ✕
          </button>
        </div>
      ))}
      {casts.length === 0 && (
        <p className="text-[10px] text-slate-400">点击「+ 添加」转换列类型</p>
      )}
    </div>
  );
}

// ── Join 配置（多条件下拉：左列 = 右列） ──

function JoinConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const joinType = node.config.join_type ?? 'INNER';
  const leftColumns = getColumnNames(node, nodeSchemas, irEdges, 0);
  const rightColumns = getColumnNames(node, nodeSchemas, irEdges, 1);
  const conditions = node.config.join_conditions ?? [];

  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">关联类型</label>
        <select
          value={joinType}
          onChange={(e) => onChange('join_type', e.target.value)}
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
        >
          <option value="INNER">INNER JOIN（只保留匹配行）</option>
          <option value="LEFT">LEFT JOIN（保留左表全部行）</option>
          <option value="RIGHT">RIGHT JOIN（保留右表全部行）</option>
          <option value="FULL">FULL JOIN（保留两表全部行）</option>
        </select>
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs font-medium text-slate-600">关联条件</label>
          <button
            type="button"
            onClick={() =>
              onChange('join_conditions', [
                ...conditions,
                { left_column: '', right_column: '' } as JoinCondition,
              ])
            }
            className="text-[11px] text-blue-600 hover:underline"
          >
            + 添加条件
          </button>
        </div>
        {conditions.length === 0 && leftColumns.length === 0 && rightColumns.length === 0 && (
          <p className="text-[10px] text-slate-400">请先将两个上游节点连接到本节点（左、右各一个输入）</p>
        )}
        {conditions.length === 0 && (leftColumns.length > 0 || rightColumns.length > 0) && (
          <p className="text-[10px] text-slate-400">点击「+ 添加条件」设置关联键</p>
        )}
        {conditions.map((cond, i) => (
          <div key={i} className="flex items-center gap-1">
            <div className="flex-1">
              {leftColumns.length > 0 ? (
                <ColumnSelect
                  value={cond.left_column}
                  columns={leftColumns}
                  placeholder="左表列"
                  onChange={(v) => {
                    const next = [...conditions];
                    next[i] = { ...next[i], left_column: v };
                    onChange('join_conditions', next);
                  }}
                />
              ) : (
                <input
                  type="text"
                  value={cond.left_column}
                  placeholder="左表列（手动输入）"
                  onChange={(e) => {
                    const next = [...conditions];
                    next[i] = { ...next[i], left_column: e.target.value };
                    onChange('join_conditions', next);
                  }}
                  className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
                />
              )}
            </div>
            <span className="text-xs text-slate-400">=</span>
            <div className="flex-1">
              {rightColumns.length > 0 ? (
                <ColumnSelect
                  value={cond.right_column}
                  columns={rightColumns}
                  placeholder="右表列"
                  onChange={(v) => {
                    const next = [...conditions];
                    next[i] = { ...next[i], right_column: v };
                    onChange('join_conditions', next);
                  }}
                />
              ) : (
                <input
                  type="text"
                  value={cond.right_column}
                  placeholder="右表列（手动输入）"
                  onChange={(e) => {
                    const next = [...conditions];
                    next[i] = { ...next[i], right_column: e.target.value };
                    onChange('join_conditions', next);
                  }}
                  className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
                />
              )}
            </div>
            <button
              type="button"
              onClick={() =>
                onChange('join_conditions', conditions.filter((_, idx) => idx !== i))
              }
              className="text-xs text-red-400 hover:text-red-600"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Aggregate 配置（分组列多选 + 聚合行：列下拉 + 函数下拉 + 别名） ──

function AggregateConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumns(node, nodeSchemas, irEdges, 0);
  const groupBy = node.config.group_by ?? [];
  const aggregations = node.config.aggregations ?? [];

  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">分组字段</label>
        {columns.length === 0 ? (
          <p className="text-[11px] text-slate-400">请先连接上游节点</p>
        ) : (
          <div className="max-h-32 space-y-1 overflow-y-auto rounded border border-slate-200 p-2">
            {columns.map((col) => (
              <label key={col.name} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={groupBy.includes(col.name)}
                  onChange={() => {
                    if (groupBy.includes(col.name)) {
                      onChange('group_by', groupBy.filter((c) => c !== col.name));
                    } else {
                      onChange('group_by', [...groupBy, col.name]);
                    }
                  }}
                  className="rounded"
                />
                <span className="flex-1 truncate text-slate-700">{col.name}</span>
                <span className="text-[10px] text-slate-400">{col.data_type}</span>
              </label>
            ))}
          </div>
        )}
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs font-medium text-slate-600">聚合函数</label>
          <button
            type="button"
            onClick={() =>
              onChange('aggregations', [
                ...aggregations,
                { field: '', function: 'SUM', alias: '' },
              ])
            }
            className="text-[11px] text-blue-600 hover:underline"
          >
            + 添加
          </button>
        </div>
        <div className="space-y-2">
          {aggregations.map((agg, i) => (
            <div key={i} className="flex items-center gap-1">
              <div className="flex-1">
                <ColumnSelect
                  value={agg.field}
                  columns={getColumnNames(node, nodeSchemas, irEdges, 0)}
                  placeholder="字段"
                  onChange={(v) => {
                    const next = [...aggregations];
                    next[i] = { ...next[i], field: v };
                    onChange('aggregations', next);
                  }}
                />
              </div>
              <select
                value={agg.function}
                onChange={(e) => {
                  const next = [...aggregations];
                  next[i] = { ...next[i], function: e.target.value };
                  onChange('aggregations', next);
                }}
                className="w-20 rounded border border-slate-300 px-1 py-1.5 text-sm outline-none focus:border-blue-400"
              >
                <option value="SUM">SUM</option>
                <option value="COUNT">COUNT</option>
                <option value="AVG">AVG</option>
                <option value="MIN">MIN</option>
                <option value="MAX">MAX</option>
                <option value="COUNT_DISTINCT">去重计数</option>
              </select>
              <input
                type="text"
                value={agg.alias ?? ''}
                placeholder="别名"
                onChange={(e) => {
                  const next = [...aggregations];
                  next[i] = { ...next[i], alias: e.target.value };
                  onChange('aggregations', next);
                }}
                className="w-20 rounded border border-slate-300 px-1 py-1 text-sm outline-none focus:border-blue-400"
              />
              <button
                type="button"
                onClick={() => onChange('aggregations', aggregations.filter((_, idx) => idx !== i))}
                className="text-xs text-red-400 hover:text-red-600"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Expression 配置（保留表达式输入 + 显示可用列名提示） ──

function ExpressionConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumnNames(node, nodeSchemas, irEdges, 0);
  return (
    <div className="space-y-2">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">表达式</label>
        <textarea
          value={node.config.expression ?? ''}
          onChange={(e) => onChange('expression', e.target.value || null)}
          placeholder='例如: amount * 1.1 AS amount_with_tax'
          rows={3}
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
        />
        <p className="mt-1 text-[10px] text-slate-400">
          使用 SQL 表达式，支持 AS 别名。可用列：{columns.length > 0 ? columns.join(', ') : '（请先连接上游）'}
        </p>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">输出列名</label>
        <input
          type="text"
          value={(node.config.extra?.alias as string) ?? ''}
          onChange={(e) => onChange('extra', { ...(node.config.extra ?? {}), alias: e.target.value })}
          placeholder="结果列名（默认 _expr_result）"
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
        />
      </div>
    </div>
  );
}

// ── Deduplicate 配置（去重键列多选） ──

function DeduplicateConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumns(node, nodeSchemas, irEdges, 0);
  const keys = node.config.columns ?? [];

  if (columns.length === 0) {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-400">
        请先连接上游节点以选择去重键
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <label className="mb-1 block text-xs font-medium text-slate-600">去重键（按这些列去重）</label>
      <div className="max-h-48 space-y-1 overflow-y-auto rounded border border-slate-200 p-2">
        {columns.map((col) => (
          <label key={col.name} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={keys.includes(col.name)}
              onChange={() => {
                if (keys.includes(col.name)) {
                  onChange('columns', keys.filter((c) => c !== col.name));
                } else {
                  onChange('columns', [...keys, col.name]);
                }
              }}
              className="rounded"
            />
            <span className="flex-1 truncate text-slate-700">{col.name}</span>
            <span className="text-[10px] text-slate-400">{col.data_type}</span>
          </label>
        ))}
      </div>
      <p className="mt-1 text-[10px] text-slate-400">
        保留每组的首条记录，已选 {keys.length} 列
      </p>
    </div>
  );
}

// ── Sort 配置（排序列下拉 + ASC/DESC 切换） ──

function SortConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumnNames(node, nodeSchemas, irEdges, 0);
  const sortKeys = node.config.sort_keys ?? [];

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-slate-600">排序规则</label>
        <button
          type="button"
          onClick={() =>
            onChange('sort_keys', [
              ...sortKeys,
              { column: '', direction: 'ASC' } as SortKey,
            ])
          }
          className="text-[11px] text-blue-600 hover:underline"
        >
          + 添加
        </button>
      </div>
      {sortKeys.map((sk, i) => (
        <div key={i} className="flex items-center gap-1">
          <div className="flex-1">
            <ColumnSelect
              value={sk.column}
              columns={columns}
              placeholder="选择列"
              onChange={(v) => {
                const next = [...sortKeys];
                next[i] = { ...next[i], column: v };
                onChange('sort_keys', next);
              }}
            />
          </div>
          <button
            type="button"
            onClick={() => {
              const next = [...sortKeys];
              next[i] = { ...next[i], direction: sk.direction === 'ASC' ? 'DESC' : 'ASC' };
              onChange('sort_keys', next);
            }}
            className={`w-16 rounded border px-2 py-1.5 text-xs ${
              sk.direction === 'DESC'
                ? 'border-amber-300 bg-amber-50 text-amber-700'
                : 'border-slate-300 text-slate-600 hover:bg-slate-50'
            }`}
          >
            {sk.direction === 'ASC' ? '↑ 升序' : '↓ 降序'}
          </button>
          <button
            type="button"
            onClick={() => onChange('sort_keys', sortKeys.filter((_, idx) => idx !== i))}
            className="text-xs text-red-400 hover:text-red-600"
          >
            ✕
          </button>
        </div>
      ))}
      {sortKeys.length === 0 && (
        <p className="text-[10px] text-slate-400">点击「+ 添加」设置排序列</p>
      )}
    </div>
  );
}

// ── Sink 配置 ──

function SinkConfig({
  node,
  datasets,
  onChange,
}: {
  node: IRNode;
  datasets: DatasetOption[];
  onChange: (field: string, value: unknown) => void;
}) {
  const selectedDataset = (node.config.extra?.dataset as string | undefined) ?? '';
  const currentWriteMode =
    (node.config.extra?.write_mode as 'FULL_REFRESH' | 'APPEND' | undefined) ?? 'FULL_REFRESH';
  // 按 display_name 排序
  const sorted = [...datasets].sort((a, b) => {
    const na = a.display_name || a.api_name;
    const nb = b.display_name || b.api_name;
    return na.localeCompare(nb, 'zh-CN');
  });
  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">目标数据集</label>
        <select
          value={selectedDataset}
          onChange={(e) =>
            onChange('extra', { ...(node.config.extra ?? {}), dataset: e.target.value })
          }
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-blue-400"
        >
          <option value="">选择目标数据集...</option>
          {sorted.map((ds) => (
            <option key={ds.api_name} value={ds.api_name}>
              {ds.display_name || ds.api_name}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-slate-600">写入模式</label>
        <div className="flex gap-2">
          {(['FULL_REFRESH', 'APPEND'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() =>
                onChange('extra', { ...(node.config.extra ?? {}), write_mode: mode })
              }
              className={`flex-1 rounded border px-2 py-1.5 text-xs ${
                currentWriteMode === mode
                  ? 'border-blue-400 bg-blue-50 text-blue-700'
                  : 'border-slate-300 text-slate-600 hover:bg-slate-50'
              }`}
            >
              {mode === 'FULL_REFRESH' ? '全量重建' : '增量追加'}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[10px] text-slate-400">
          全量重建：覆盖目标表；增量追加：仅添加新行
        </p>
      </div>
    </div>
  );
}

// ── QualityCheck 配置（校验列下拉 + 规则参数） ──

function QualityCheckConfig({
  node,
  nodeSchemas,
  irEdges,
  onChange,
}: {
  node: IRNode;
  nodeSchemas: Record<string, Schema>;
  irEdges: IREdge[];
  onChange: (field: string, value: unknown) => void;
}) {
  const columns = getColumnNames(node, nodeSchemas, irEdges, 0);
  const rules = node.config.quality_rules ?? [];

  return (
    <div className="space-y-3">
      <label className="text-xs font-medium text-slate-600">质量规则</label>
      {rules.map((rule, i) => (
        <div key={i} className="space-y-2 rounded border border-slate-200 bg-slate-50 p-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-medium text-slate-500">
              {rule.rule_type === 'not_null' && '非空校验'}
              {rule.rule_type === 'unique' && '唯一校验'}
              {rule.rule_type === 'range' && '范围校验'}
              {rule.rule_type === 'regex' && '正则校验'}
            </span>
            <select
              value={rule.severity}
              onChange={(e) => {
                const next = [...rules];
                next[i] = { ...next[i], severity: e.target.value as typeof rule.severity };
                onChange('quality_rules', next);
              }}
              className="ml-auto rounded border border-slate-300 px-1 py-0.5 text-[10px]"
            >
              <option value="ERROR">ERROR</option>
              <option value="WARNING">WARNING</option>
              <option value="SPLIT">SPLIT</option>
            </select>
          </div>
          <div>
            <label className="mb-0.5 block text-[10px] text-slate-500">校验列</label>
            <ColumnSelect
              value={rule.field}
              columns={columns}
              placeholder="选择列"
              onChange={(v) => {
                const next = [...rules];
                next[i] = { ...next[i], field: v };
                onChange('quality_rules', next);
              }}
            />
          </div>
          {rule.rule_type === 'range' && (
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={String(rule.config.min ?? '')}
                placeholder="最小值"
                onChange={(e) => {
                  const next = [...rules];
                  next[i] = { ...next[i], config: { ...rule.config, min: Number(e.target.value) } };
                  onChange('quality_rules', next);
                }}
                className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
              />
              <span className="text-xs text-slate-400">~</span>
              <input
                type="number"
                value={String(rule.config.max ?? '')}
                placeholder="最大值"
                onChange={(e) => {
                  const next = [...rules];
                  next[i] = { ...next[i], config: { ...rule.config, max: Number(e.target.value) } };
                  onChange('quality_rules', next);
                }}
                className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
          )}
          {rule.rule_type === 'regex' && (
            <input
              type="text"
              value={String(rule.config.pattern ?? '')}
              placeholder="正则表达式"
              onChange={(e) => {
                const next = [...rules];
                next[i] = { ...next[i], config: { ...rule.config, pattern: e.target.value } };
                onChange('quality_rules', next);
              }}
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
            />
          )}
          <input
            type="text"
            value={rule.message}
            placeholder="失败提示信息（可选）"
            onChange={(e) => {
              const next = [...rules];
              next[i] = { ...next[i], message: e.target.value };
              onChange('quality_rules', next);
            }}
            className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
      ))}
      {columns.length === 0 && (
        <p className="text-[10px] text-slate-400">请先连接上游节点以选择校验列</p>
      )}
    </div>
  );
}

// ── 通用配置（回退） ──

function GenericConfig({
  node: _node,
  onChange: _onChange,
}: {
  node: IRNode;
  onChange: (field: string, value: unknown) => void;
}) {
  return (
    <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
      该算子没有专用配置表单。
      <br />
      可用 JSON 编辑模式配置高级参数。
    </div>
  );
}
