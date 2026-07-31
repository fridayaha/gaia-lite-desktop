import { useMemo, useRef, useState } from 'react';
import type { DataSource, DataSourceCreate } from '../types';
import { createDataSource } from '../api/client';
import { useFieldId } from '../hooks/useFormId';
import { cn } from '../lib/cn';
import { TextInput } from './ui/TextField';
import {
  CAPABILITY_LABELS,
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  CONNECTOR_CATALOG,
  CONNECTOR_META,
  connectorSortRank,
  FILTERABLE_CAPABILITIES,
  type Capability,
  type ConfigField,
  type ConnectorCategory,
  type ConnectorMeta,
} from '../constants/connectorCatalog';

interface FetchError extends Error {
  message: string;
}

// ── Component ──

interface DataSourceFormProps {
  /** 创建模式回调。 */
  onCreated?: (ds: DataSource) => void;
  /** 编辑模式回调。 */
  onUpdated?: (ds: DataSource) => void;
  onCancel: () => void;
  /** 编辑模式：初始数据。提供后即切换为编辑模式。 */
  initialData?: DataSource;
}

export function DataSourceForm({ onCreated, onUpdated, onCancel, initialData }: DataSourceFormProps) {
  const isEdit = !!initialData;
  const [step, setStep] = useState<'catalog' | 'config'>(isEdit ? 'config' : 'catalog');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState<ConnectorCategory | null>(null);
  const [activeCapabilities, setActiveCapabilities] = useState<Set<Capability>>(new Set());
  // 各品类 section 的 DOM ref（用于点击左侧品类导航时滚动定位）
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const gridContainerRef = useRef<HTMLDivElement | null>(null);
  const [form, setForm] = useState<DataSourceCreate>({
    api_name: initialData?.api_name ?? '',
    display_name: initialData?.display_name ?? '',
    description: initialData?.description ?? '',
    connector_type: initialData?.connector_type ?? 'mysql',
    connector_config: Object.fromEntries(
      Object.entries(initialData?.connector_config ?? {}).map(([k, v]) => [k, String(v)]),
    ),
  });

  function updateField<K extends keyof DataSourceCreate>(key: K, value: DataSourceCreate[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function updateConfig(key: string, value: string) {
    setForm((prev) => ({
      ...prev,
      connector_config: { ...prev.connector_config, [key]: value },
    }));
  }

  function selectConnector(meta: ConnectorMeta) {
    // 初始化 connector_config 默认值（端口/默认值）
    const initialConfig: Record<string, string> = {};
    for (const f of meta.configSchema) {
      if (f.default) initialConfig[f.key] = f.default;
      if (f.key === 'port' && meta.defaultPort) initialConfig[f.key] = meta.defaultPort;
    }
    setForm((prev) => ({
      ...prev,
      connector_type: meta.key,
      connector_config: initialConfig,
    }));
    setStep('config');
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      if (isEdit && initialData) {
        const { updateDataSource } = await import('../api/client');
        const ds = await updateDataSource(initialData.api_name, {
          display_name: form.display_name,
          description: form.description,
          connector_config: form.connector_config,
        });
        onUpdated?.(ds);
      } else {
        const ds = await createDataSource(form);
        onCreated?.(ds);
      }
    } catch (err: unknown) {
      const fetchErr = err as FetchError;
      setError(fetchErr.message || '操作失败');
    } finally {
      setLoading(false);
    }
  }

  // ── 全量连接器（经搜索 + 能力筛选），按品类分组为 { category: connectors[] } 的有序 Map ──
  const groupedCatalog = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = CONNECTOR_CATALOG.filter((c) => {
      if (q) {
        const haystack = `${c.label} ${c.description} ${c.key} ${(c.keywords ?? []).join(' ')}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      if (activeCapabilities.size > 0) {
        for (const cap of activeCapabilities) {
          if (!c.capabilities.includes(cap)) return false;
        }
      }
      return true;
    });
    // 按 CATEGORY_ORDER 排序，品类内按「华为 > 流行度 > 使用量 > 成熟度」排序
    const byCat = new Map<ConnectorCategory, ConnectorMeta[]>();
    for (const cat of CATEGORY_ORDER) byCat.set(cat, []);
    for (const c of filtered) byCat.get(c.category)?.push(c);
    for (const [, list] of byCat) list.sort(connectorSortRank);
    // 筛掉空品类
    const result: [ConnectorCategory, ConnectorMeta[]][] = [];
    for (const cat of CATEGORY_ORDER) {
      const list = byCat.get(cat)!;
      if (list.length > 0) result.push([cat, list]);
    }
    return result;
  }, [search, activeCapabilities]);

  // ── 品类计数（全量目录统计，不可搜索/筛选影响，作为导航 rail 的稳定信标）──
  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const c of CONNECTOR_CATALOG) counts[c.category] = (counts[c.category] || 0) + 1;
    return counts;
  }, []);

  function scrollToCategory(cat: ConnectorCategory) {
    setActiveCategory(cat);
    sectionRefs.current[cat]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function toggleCapabilityFilter(cap: Capability) {
    setActiveCategory(null);
    setActiveCapabilities((prev) => {
      const next = new Set(prev);
      if (next.has(cap)) next.delete(cap);
      else next.add(cap);
      return next;
    });
  }

  // ── Step 1: 连接器目录（左品类导航 rail 点击定位 + 右全量分组展示）──
  if (step === 'catalog') {
    const anyFilterActive = search.trim() || activeCapabilities.size > 0;
    return (
      <div className="overlay-backdrop" onClick={onCancel}>
        <div
          className="overlay-panel max-w-[860px]"
          role="dialog"
          aria-modal="true"
          aria-label="选择数据源类型"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="overlay-header">
            <h3>选择数据源类型</h3>
            <button className="btn btn-sm" aria-label="关闭" onClick={onCancel}>
              ✕
            </button>
          </div>
          <div className="overlay-body connector-catalog-body">
            {/* 顶部：搜索 + 能力筛选（跨品类全局生效） */}
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <div className="flex-1" style={{ minWidth: 220 }}>
                <TextInput
                  inputClassName="form-input"
                  value={search}
                  onChange={(v) => { setSearch(v); setActiveCategory(null); }}
                  placeholder="搜索连接器名称、关键词或别名（如「Kafka」「对象存储」「国产库」）…"
                  aria-label="搜索连接器"
                  autoFocus
                />
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {FILTERABLE_CAPABILITIES.map((cap) => (
                  <button
                    key={cap}
                    type="button"
                    className={cn(
                      'btn btn-sm',
                      activeCapabilities.has(cap) ? 'btn-primary' : '',
                    )}
                    onClick={() => toggleCapabilityFilter(cap)}
                    aria-pressed={activeCapabilities.has(cap)}
                  >
                    {CAPABILITY_LABELS[cap]}
                  </button>
                ))}
              </div>
            </div>

            <div className="connector-catalog-layout">
              {/* 左：品类导航 rail — 点击滚动到右侧对应 section */}
              <nav className="connector-rail" aria-label="数据源品类">
                {CATEGORY_ORDER.map((cat) => {
                  const count = categoryCounts[cat] || 0;
                  const isActive = !anyFilterActive && activeCategory === cat;
                  return (
                    <button
                      key={cat}
                      type="button"
                      className={cn('rail-btn', isActive && 'active')}
                      onClick={() => scrollToCategory(cat)}
                      aria-current={isActive ? 'true' : undefined}
                      aria-label={`${CATEGORY_LABELS[cat]}（${count}）`}
                    >
                      <span className="rail-btn-label">{CATEGORY_LABELS[cat]}</span>
                      <span className="rail-btn-count">{count}</span>
                    </button>
                  );
                })}
              </nav>

              {/* 右：全量分组展示 — 自上而下按品类顺序排列，点击品类左侧定位 */}
              <div className="connector-grid" ref={gridContainerRef}>
                {groupedCatalog.length === 0 ? (
                  <div className="py-8 text-center text-sm text-text-muted connector-grid-empty">
                    未找到匹配的连接器
                  </div>
                ) : (
                  groupedCatalog.map(([cat, connectors]) => (
                    <div
                      key={cat}
                      ref={(el) => { sectionRefs.current[cat] = el; }}
                      className="connector-category-section"
                    >
                      <h4 className="connector-category-title">{CATEGORY_LABELS[cat]}</h4>
                      <div className="connector-category-tiles">
                        {connectors.map((meta) => (
                          <ConnectorCard key={meta.key} meta={meta} onSelect={() => selectConnector(meta)} />
                        ))}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Step 2: 连接配置（按 configSchema 动态渲染）──
  const meta = CONNECTOR_META[form.connector_type] || CONNECTOR_META['mysql'];
  const isJdbc =
    meta.category === 'databases' || meta.category === 'generic';

  // 将配置字段按 flex 行分组（连续的 flex 字段排成一行，非 flex 字段独占一行）
  const fieldRows = groupFieldsByRow(meta.configSchema);

  return (
    <div className="overlay-backdrop" onClick={onCancel}>
      <div
        className="overlay-panel max-w-[560px]"
        role="dialog"
        aria-modal="true"
        aria-label={`连接 ${meta.label}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="overlay-header">
          <div className="flex items-center gap-2">
            <span className="text-xl">{meta.icon}</span>
            <h3>{isEdit ? `编辑 ${initialData!.display_name}` : `连接 ${meta.label}`}</h3>
          </div>
          <button className="btn btn-sm" aria-label="关闭" onClick={onCancel}>
            ✕
          </button>
        </div>

        <div className="overlay-body">
          {error && <div className="error-box">{error}</div>}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label" htmlFor="ds-display-name">
                数据源名称
              </label>
              <TextInput
                id="ds-display-name"
                inputClassName="form-input"
                value={form.display_name}
                onChange={(v) => updateField('display_name', v)}
                placeholder="例如：ERP 生产库"
                required
                autoFocus
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="ds-api-name">
                API 标识
              </label>
              {isEdit ? (
                <div className="form-input form-input-mono bg-bg text-text-muted select-none cursor-not-allowed">
                  {form.api_name}
                </div>
              ) : (
                <TextInput
                  id="ds-api-name"
                  inputClassName="form-input form-input-mono"
                  value={form.api_name}
                  onChange={(v) => updateField('api_name', v)}
                  placeholder="erp_mysql_prod"
                  required
                  pattern="^[a-z][a-zA-Z0-9_]*$"
                />
              )}
              <span className="form-hint">{isEdit ? '创建后不可修改' : '小写字母开头，仅含字母数字下划线'}</span>
            </div>

            {/* 动态配置字段 */}
            {isJdbc && (
              <div className="mb-2 rounded-md border border-border bg-bg p-2 text-[11px] text-text-muted">
                {meta.pgKernel && 'PG 内核：连接绑定单个数据库'}
                {meta.mysqlProto && 'MySQL 协议：连接绑定整个实例'}
                {meta.category === 'generic' && '通用 JDBC：需手动提供完整 URL 与 Driver'}
              </div>
            )}
            {fieldRows.map((row, rowIdx) => (
              <div key={rowIdx} className={cn('form-row', row.length === 1 && 'block')}>
                {row.map((field) => (
                  <ConfigFieldInput
                    key={field.key}
                    field={field}
                    value={form.connector_config[field.key] || ''}
                    onChange={(v) => updateConfig(field.key, v)}
                  />
                ))}
              </div>
            ))}

            {/* 避坑提示 */}
            {meta.pitfalls && meta.pitfalls.length > 0 && (
              <div className="mt-3 rounded-md border border-[var(--warning-border,orange)] bg-[var(--warning-bg,#fff7ed)] p-2 text-[11px] text-[var(--warning-text,#9a3412)]">
                <div className="mb-1 font-semibold">⚠️ 避坑提示</div>
                <ul className="m-0 list-disc pl-4">
                  {meta.pitfalls.map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              {!isEdit && (
                <button type="button" className="btn" onClick={() => setStep('catalog')}>
                  ← 返回
                </button>
              )}
              <button type="submit" className="btn btn-primary" disabled={loading}>
                {loading ? (isEdit ? '保存中...' : '创建中...') : (isEdit ? '保存修改' : '创建数据源')}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

// ── 连接器卡片 ──

function ConnectorCard({ meta, onSelect }: { meta: ConnectorMeta; onSelect: () => void }) {
  return (
    <div
      className="connector-tile"
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="connector-tile-head">
        <span className="connector-tile-icon">{meta.icon}</span>
        <span className="connector-tile-label">{meta.label}</span>
        <span
          className={cn(
            'connector-tile-maturity',
            meta.maturity === 'GA'
              ? 'is-ga'
              : 'is-beta',
          )}
        >
          {meta.maturity}
        </span>
      </div>
      <div className="connector-tile-desc">{meta.description}</div>
      <div className="connector-tile-caps">
        {meta.capabilities.map((cap) => (
          <span key={cap} className="connector-tile-cap">
            {CAPABILITY_LABELS[cap]}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── 配置字段渲染 ──

function groupFieldsByRow(fields: ConfigField[]): ConfigField[][] {
  const rows: ConfigField[][] = [];
  let currentRow: ConfigField[] = [];
  for (const f of fields) {
    if (f.flex) {
      currentRow.push(f);
    } else {
      if (currentRow.length > 0) {
        rows.push(currentRow);
        currentRow = [];
      }
      rows.push([f]);
    }
  }
  if (currentRow.length > 0) rows.push(currentRow);
  return rows;
}

function ConfigFieldInput({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: string;
  onChange: (v: string) => void;
}) {
  const fieldId = useFieldId(`ds-${field.key}`);
  const flexClass = field.flex ? `flex-[${field.flex}]` : '';
  return (
    <div className={cn('form-group', flexClass)}>
      <label className="form-label" htmlFor={fieldId.id}>
        {field.label}
      </label>
      <TextInput
        id={fieldId.id}
        inputClassName="form-input form-input-mono"
        type={field.type === 'password' ? 'password' : undefined}
        value={value}
        onChange={onChange}
        placeholder={field.placeholder}
        required={field.required}
      />
      {field.hint && <span className="form-hint">{field.hint}</span>}
    </div>
  );
}
