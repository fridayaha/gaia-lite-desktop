/**
 * SchemaPreview — Schema 预览组件（design.md §14.8）。
 *
 * 选中节点时展示推演出的输出 Schema：字段名、类型、可空性。
 * 契约错误标红展示。
 */
import type { Schema, ContractViolation } from '../../types/pipeline';

interface SchemaPreviewProps {
  outputSchema: Schema | null;
  errors: ContractViolation[];
  nodeLabel: string;
  nodeType: string;
}

const TYPE_COLORS: Record<string, string> = {
  STRING: 'text-emerald-600 bg-emerald-50',
  INTEGER: 'text-blue-600 bg-blue-50',
  LONG: 'text-blue-600 bg-blue-50',
  FLOAT: 'text-indigo-600 bg-indigo-50',
  DOUBLE: 'text-indigo-600 bg-indigo-50',
  DECIMAL: 'text-purple-600 bg-purple-50',
  BOOLEAN: 'text-amber-600 bg-amber-50',
  TIMESTAMP: 'text-rose-600 bg-rose-50',
  DATE: 'text-rose-600 bg-rose-50',
};

export function SchemaPreview({ outputSchema, errors, nodeLabel: _nodeLabel, nodeType }: SchemaPreviewProps) {
  if (!outputSchema || outputSchema.fields.length === 0) {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 p-4 text-center">
        <p className="text-xs text-slate-400">
          {nodeType === 'Source' ? '请选择数据集' : '连接上游节点后自动推演 Schema'}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Schema 摘要 */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-slate-600">
          {outputSchema.fields.length} 个字段
        </span>
        <span className="text-[10px] text-slate-400">
          {outputSchema.fields.filter((f) => f.primary_key).length} 主键 ·{' '}
          {outputSchema.fields.filter((f) => !f.nullable).length} 非空
        </span>
      </div>

      {/* 字段列表 */}
      <div className="overflow-hidden rounded-lg border border-slate-200">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50">
              <th className="px-3 py-1.5 text-left font-medium text-slate-500">字段名</th>
              <th className="px-3 py-1.5 text-left font-medium text-slate-500">类型</th>
              <th className="px-3 py-1.5 text-center font-medium text-slate-500">可空</th>
              <th className="px-3 py-1.5 text-center font-medium text-slate-500">PK</th>
            </tr>
          </thead>
          <tbody>
            {outputSchema.fields.map((field) => (
              <tr key={field.name} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-1.5 font-mono text-slate-700">{field.name}</td>
                <td className="px-3 py-1.5">
                  <span
                    className={`rounded px-1 py-0.5 text-[10px] ${
                      TYPE_COLORS[field.data_type] ?? 'text-slate-600 bg-slate-100'
                    }`}
                  >
                    {field.data_type}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-center">
                  {field.nullable ? (
                    <span className="text-slate-300">✓</span>
                  ) : (
                    <span className="font-medium text-slate-600">✗</span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-center">
                  {field.primary_key ? (
                    <span className="font-medium text-amber-600">PK</span>
                  ) : (
                    <span className="text-slate-300">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 契约错误 */}
      {errors.filter((e) => !e.valid).length > 0 && (
        <div>
          <h4 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-red-600">
            校验错误
          </h4>
          <div className="space-y-1">
            {errors
              .filter((e) => !e.valid)
              .map((e, i) => (
                <div
                  key={i}
                  className="rounded bg-red-50 px-2 py-1 text-[10px] text-red-700"
                >
                  {e.message}
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
