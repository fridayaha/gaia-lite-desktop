import type { ColumnInfo } from '../types';

interface PreviewTableProps {
  /** 完整列信息（含类型/注释），用于渲染富表头。 */
  columns: ColumnInfo[];
  rows: Record<string, unknown>[];
  /** 最多展示多少行（默认 100，对齐 Snowflake/Databricks）。 */
  maxRows?: number;
  loading?: boolean;
  error?: string | null;
}

/**
 * 数据预览表。
 *
 * 设计对齐 Snowflake Snowsight / Databricks Catalog Explorer 的 Data Preview tab：
 * - 表头 = 原始列名（mono，保留大小写）+ 类型副标题 + 注释 tooltip
 * - 表头 sticky（纵向滚动时始终可见）
 * - 表体独立横向+纵向滚动，不与外层页面联动
 * - 首列（行号）sticky 左侧锁定，横向滚动时不丢行号
 *
 * 不复用通用 DataTable：预览表需要 sticky 表头 + 列级 tooltip + 行号锁定等
 * 特化能力，与 DataTable 的「只读展示表」定位不同，独立实现更清晰。
 */
export function PreviewTable({ columns, rows, maxRows = 100, loading, error }: PreviewTableProps) {
  if (loading) {
    return (
      <div className="preview-table-wrap">
        <div className="p-3 text-center text-sm text-text-muted">加载数据中…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="preview-table-wrap">
        <div className="preview-error">⚠ {error}</div>
      </div>
    );
  }

  if (!columns.length) {
    return (
      <div className="preview-table-wrap">
        <div className="p-3 text-center text-sm text-text-muted">无数据</div>
      </div>
    );
  }

  const displayRows = rows.slice(0, maxRows);

  return (
    <div className="preview-table-wrap">
      <div className="preview-table-scroll">
        <table className="preview-table">
          <thead>
            <tr>
              <th className="preview-row-index" scope="col">#</th>
              {columns.map((col) => (
                <th key={col.name} scope="col" title={col.comment || undefined}>
                  <span className="preview-col-name">{col.name}</span>
                  {col.data_type && (
                    <span className="preview-col-type">{col.data_type}</span>
                  )}
                  {col.comment && (
                    <span className="preview-col-comment" title={col.comment}>
                      {col.comment}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayRows.length === 0 ? (
              <tr>
                <td className="preview-empty" colSpan={columns.length + 1}>
                  暂无数据
                </td>
              </tr>
            ) : (
              displayRows.map((row, i) => (
                <tr key={i}>
                  <td className="preview-row-index">{i + 1}</td>
                  {columns.map((col) => {
                    // Trino 小写化所有列标识符（跨方言联邦一致性），返回的 row dict key
                    // 全是小写（modelId → modelid）。表头显示原始大小写（col.name，来自
                    // Gravitino REST），取值时用小写 key 对齐 Trino 数据。
                    const val = row[col.name.toLowerCase()];
                    if (val === null || val === undefined) {
                      return (
                        <td key={col.name}>
                          <span className="preview-null">NULL</span>
                        </td>
                      );
                    }
                    const text = typeof val === 'object' ? JSON.stringify(val) : String(val);
                    return <td key={col.name} title={text}>{text}</td>;
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {rows.length >= maxRows ? (
        <div className="preview-table-foot">最多显示前 {maxRows} 行</div>
      ) : rows.length > 0 ? (
        <div className="preview-table-foot">共 {rows.length} 行</div>
      ) : (
        <div className="preview-table-foot">该表无数据</div>
      )}
    </div>
  );
}
