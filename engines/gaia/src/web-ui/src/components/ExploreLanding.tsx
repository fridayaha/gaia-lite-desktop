/**
 * ExploreLanding — 对话式空状态（graph-reasoning-frontend-design-v3.md §2.1）。
 *
 * 首次进入图探索的主入口：中央对话框 + 4 个可点击示例卡片 + 本体知识提示。
 * 对齐 2026 AI 空状态四属性：worked example + starting verb + model knowledge + action affordance。
 *
 * 零控件，业务用户 0 培训可用。
 */
import { useState } from 'react';
import { OntologyGraph } from './OntologyGraph';
import type { LinkTypeDef, ObjectType, ObjectTypeSummary, PropertyDef } from '../types';

interface ExploreLandingProps {
  ontology: string;
  /** 本体中文名（display_name），用于标题展示。未传时退回 api_name。 */
  ontologyDisplayName?: string;
  objectTypes: ObjectTypeSummary[];
  /** 完整 OT（含 properties.data_type），用于推导贴合本体的示例与场景模板。 */
  objectTypesFull?: ObjectType[];
  linkTypes: LinkTypeDef[];
  onAsk: (question: string) => void;
  /** 点击图谱中的对象类型节点 → 以该类型为起始集进入探索。 */
  onSelectObjectType?: (apiName: string) => void;
  loading?: boolean;
}

/** 起始动词 + worked example（v3 §1.2）。点击直接执行。 */
const STARTER_VERBS = ['查看', '找出', '追踪', '分析'];

/** 场景模板（预填问题，走 AI 编排，非硬编码步骤，design-v3 §2.4）。 */
interface Scenario {
  id: string;
  icon: string;
  label: string;
  description: string;
  question: string;
}

/** 本体能力探测：从完整 OT 的属性 data_type 推导本体具备哪些分析能力。 */
function detectCapabilities(ots: ObjectType[]) {
  let hasGeo = false;
  let hasTemporal = false;
  let hasNumeric = false;
  let hasIndexedString = false;
  for (const ot of ots) {
    for (const p of ot.properties ?? []) {
      const dt = p.data_type;
      if (dt === 'GEOPOINT' || dt === 'GEOSHAPE') hasGeo = true;
      if (dt === 'TIMESTAMP' || dt === 'DATE' || dt === 'TIME_SERIES' || dt === 'GEOTEMPORAL_SERIES') hasTemporal = true;
      if (['INTEGER', 'LONG', 'FLOAT', 'DOUBLE', 'DECIMAL', 'SHORT'].includes(dt)) hasNumeric = true;
      if (dt === 'STRING' && p.indexed) hasIndexedString = true;
    }
  }
  return { hasGeo, hasTemporal, hasNumeric, hasIndexedString };
}

/** 场景模板：按本体能力显隐（不再硬编码业务场景）。 */
function buildScenarios(ots: ObjectType[], linkTypes: LinkTypeDef[]): Scenario[] {
  const caps = detectCapabilities(ots);
  const scenarios: Scenario[] = [];
  // 有关系 → 关系网络分析
  if (linkTypes.length > 0) {
    scenarios.push({
      id: 'network',
      icon: '🕸',
      label: '关系网络分析',
      description: '多跳遍历 + 路径推理',
      question: '分析对象间的关系网络结构',
    });
  }
  // 有数值属性 → 分布与排序
  if (caps.hasNumeric) {
    scenarios.push({
      id: 'distribution',
      icon: '📊',
      label: '数值分布查看',
      description: '排序 + 分组统计',
      question: '查看各对象的数值属性分布',
    });
  }
  // 有时序属性 → 时序分析
  if (caps.hasTemporal) {
    scenarios.push({
      id: 'temporal',
      icon: '🕐',
      label: '时序变化分析',
      description: '时间窗 + 趋势对比',
      question: '分析对象随时间的变化趋势',
    });
  }
  // 有地理属性 → 地理分布
  if (caps.hasGeo) {
    scenarios.push({
      id: 'geo',
      icon: '🗺',
      label: '地理分布查看',
      description: '空间分布 + 框选过滤',
      question: '查看对象的地理分布',
    });
  }
  // 有可筛选属性 → 分类筛选
  if (caps.hasIndexedString) {
    scenarios.push({
      id: 'filter',
      icon: '🔍',
      label: '分类筛选对比',
      description: '按属性分组对比',
      question: '按关键属性分组对比对象',
    });
  }
  // 兑底：自然语言提问
  scenarios.push({
    id: 'explore',
    icon: '💬',
    label: '自由探索',
    description: '自然语言提问',
    question: '探索这个本体的业务数据',
  });
  return scenarios.slice(0, 4);
}

export function ExploreLanding({
  ontology,
  ontologyDisplayName,
  objectTypes,
  objectTypesFull,
  linkTypes,
  onAsk,
  onSelectObjectType,
  loading,
}: ExploreLandingProps) {
  const [input, setInput] = useState('');

  // 根据本体结构动态生成示例与场景（worked example，贴合实际数据）
  // 优先用完整 OT（含属性 data_type），未加载完时退回 summary。
  const otsForHints = objectTypesFull ?? objectTypes;
  const examples = buildExamples(otsForHints, linkTypes);
  const scenarios = buildScenarios(otsForHints as ObjectType[], linkTypes);

  const handleSubmit = () => {
    const q = input.trim();
    if (!q) return;
    onAsk(q);
  };

  return (
    <div className="flex h-full w-full items-center justify-center overflow-y-auto bg-gradient-to-b from-slate-50 to-white p-6">
      <div className="w-full max-w-5xl">
        {/* 标题 */}
        <div className="mb-6 text-center">
          <div className="mb-3 text-5xl">🔍</div>
          <h1 className="mb-1 text-2xl font-semibold text-slate-800">图探索</h1>
          <p className="text-sm text-slate-500">
            用自然语言探索「{ontologyDisplayName || ontology || '你的本体'}」的业务网络
          </p>
        </div>

        {/* 中央对话框 */}
        <div className="mb-6 flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSubmit();
            }}
            placeholder="问问看…（如：查看高风险供应商的关联订单）"
            className="flex-1 rounded-lg border border-slate-300 px-4 py-3 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            autoFocus
            disabled={loading}
          />
          <button
            onClick={handleSubmit}
            disabled={loading || !input.trim()}
            className="rounded-lg bg-blue-600 px-5 py-3 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? '探索中…' : '💬 探索'}
          </button>
        </div>

        {/* 示例卡片（worked example + starting verb）*/}
        <div className="mb-6">
          <div className="mb-2 text-xs font-medium text-slate-400">试试这些（单步查询）：</div>
          <div className="grid grid-cols-2 gap-2">
            {examples.map((ex, i) => (
              <button
                key={`${i}-${ex}`}
                onClick={() => onAsk(ex)}
                disabled={loading}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-left text-sm text-slate-700 shadow-sm transition hover:border-blue-300 hover:bg-blue-50 disabled:opacity-50"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>

        {/* 本体图谱（model knowledge）：可视化本体结构，点击节点即以此类型起手探索。
         *  替代原先的 api_name 清单——把内部标识符换成用户可读、可点的图形入口。 */}
        {objectTypes.length > 0 && (
          <div className="mt-2">
            <div className="mb-1.5 flex items-center justify-between px-1">
              <span className="text-xs font-medium text-slate-400">
                本体图谱 · {objectTypes.length} 类对象 / {linkTypes.length} 种关系
                {onSelectObjectType ? '（点击节点开始探索）' : ''}
              </span>
            </div>
            <div className="h-[420px] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
              <OntologyGraph
                objectTypes={objectTypes}
                links={linkTypes}
                visible
                onSelectObject={(apiName) => {
                  if (onSelectObjectType) {
                    onSelectObjectType(apiName);
                  } else {
                    onAsk(`查看所有${apiName}`);
                  }
                }}
              />
            </div>
          </div>
        )}

        {/* 场景模板（预填问题，走 AI 编排，design-v3 §2.4）*/}
        {objectTypes.length > 0 && (
          <div className="mt-4">
            <div className="mb-2 text-xs font-medium text-slate-400">场景模板（多步分析）：</div>
            <div className="grid grid-cols-2 gap-2">
              {scenarios.map((s) => (
                <button
                  key={s.id}
                  onClick={() => onAsk(s.question)}
                  disabled={loading}
                  className="flex items-center gap-2 rounded-lg border border-purple-200 bg-purple-50 px-3 py-2 text-left text-xs text-purple-800 shadow-sm transition hover:border-purple-400 hover:bg-purple-100 disabled:opacity-50"
                >
                  <span className="text-base">{s.icon}</span>
                  <div>
                    <div className="font-medium">{s.label}</div>
                    <div className="text-[10px] text-purple-500">{s.description}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 起始动词提示（极淡，引导但不抢戏）*/}
        <div className="mt-4 text-center text-[10px] text-slate-300">
          起始动词：{STARTER_VERBS.map((v) => `"${v}"`).join(' · ')}
        </div>
      </div>
    </div>
  );
}

/** 根据本体结构生成贴合数据的 worked example。 */
function buildExamples(
  objectTypes: { api_name: string; properties?: PropertyDef[] }[] | ObjectTypeSummary[],
  linkTypes: LinkTypeDef[],
): string[] {
  const ots = objectTypes as { api_name: string; properties?: PropertyDef[] }[];
  if (ots.length === 0) return [];
  const examples: string[] = [];

  // 选信息量最高的 OT（properties 多的）作为主示例对象
  const sorted = [...ots].sort(
    (a, b) => (b.properties?.length ?? 0) - (a.properties?.length ?? 0),
  );
  const primary = sorted[0];
  const primaryName = primary.api_name;
  const primaryProps = primary.properties ?? [];

  // 示例 1：查看主类型全集
  examples.push(`查看所有${primaryName}`);

  // 示例 2：按属性筛选（有 indexed STRING 属性 → 按它筛选）
  const filterProp = primaryProps.find((p) => p.data_type === 'STRING' && p.indexed);
  if (filterProp) {
    examples.push(`按${filterProp.api_name}分组查看${primaryName}`);
  } else if (primaryProps.some((p) => p.data_type === 'STRING')) {
    examples.push(`查看不同${primaryName}的分类`);
  } else {
    examples.push(`查看${primaryName}的详情`);
  }

  // 示例 3：追踪关系（基于真实 LinkType 的两端）
  if (linkTypes.length > 0 && ots.length > 1) {
    // 找出 link 两端的 OT 名（取与主类型不同的作为关联目标）
    const target = ots.find((o) => o.api_name && o.api_name !== primaryName);
    const targetName = target?.api_name ?? ots[1].api_name;
    examples.push(`追踪${primaryName}的关联${targetName}`);
  } else {
    // 无关系 → 按时序/数值退之
    if (primaryProps.some((p) => p.data_type === 'TIMESTAMP' || p.data_type === 'DATE')) {
      examples.push(`追踪近 7 天的${primaryName}变化`);
    } else if (primaryProps.some((p) => ['INTEGER', 'LONG', 'DECIMAL', 'DOUBLE'].includes(p.data_type))) {
      examples.push(`找出数值最大的 10 个${primaryName}`);
    } else {
      examples.push(`查看${primaryName}的全部属性`);
    }
  }

  // 示例 4：分析（取 links_count 多的 OT 做关系网络分析）
  const hubOt = sorted.find((o) => (o.properties?.length ?? 0) > 0) ?? primary;
  if (linkTypes.length > 0) {
    examples.push(`分析${hubOt.api_name}的关系网络`);
  } else {
    examples.push(`分析${hubOt.api_name}的数据特征`);
  }

  return examples.slice(0, 4);
}
