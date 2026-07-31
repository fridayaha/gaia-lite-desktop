/**
 * 节点配置摘要 — 把节点 config 提炼为画布上可读的 1-3 行摘要。
 *
 * 设计原则（参考 n8n 节点卡片摘要 + Dify NodeBody）：
 * - 尽可能显示核心信息（值、列名、条件），让用户从画布即可理解流程逻辑
 * - 信息过多时降级为计数摘要（"3 个条件""5 列"），完整内容双击弹窗查看
 * - 纯函数，无副作用，便于单元测试
 * - 主次分明：第一行是核心动作，后续行是补充
 */
import type { IRNode } from '../../types/pipeline';
import type { DatasetGovernance } from '../../types';

type DatasetOption = Pick<DatasetGovernance, 'api_name' | 'display_name'>;

/** 单行摘要。primary=true 时样式更重（主信息）。 */
export interface SummaryLine {
  text: string;
  /** 是否为主信息（决定渲染样式：主信息更深色/更粗）。 */
  primary?: boolean;
}

/** 截断辅助：超过 maxLen 截断并加省略号。中文按字符数计。 */
function truncate(s: string, maxLen: number): string {
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen - 1) + '…';
}

/** 把 api_name 解析为 display_name（若有）。 */
function datasetLabel(apiName: string, datasets: DatasetOption[]): string {
  const ds = datasets.find((d) => d.api_name === apiName);
  return ds?.display_name || apiName;
}

/** 过滤条件 → 可读字符串。 */
function filterConditionToText(cond: {
  column: string;
  operator: string;
  value?: unknown;
  values?: unknown[];
}): string {
  const OP_LABEL: Record<string, string> = {
    eq: '=',
    neq: '≠',
    gt: '>',
    gte: '≥',
    lt: '<',
    lte: '≤',
    in: '∈',
    not_in: '∉',
    is_null: '为空',
    is_not_null: '不为空',
    contains: '包含',
    not_contains: '不包含',
    starts_with: '以…开头',
    ends_with: '以…结尾',
  };
  const col = cond.column || '?';
  const op = OP_LABEL[cond.operator] ?? cond.operator;
  if (cond.operator === 'is_null' || cond.operator === 'is_not_null') {
    return `${col} ${op}`;
  }
  if (cond.operator === 'in' || cond.operator === 'not_in') {
    const vals = Array.isArray(cond.values) ? cond.values : [];
    return `${col} ${op} [${vals.length}项]`;
  }
  const v = cond.value;
  const valStr = typeof v === 'string' ? `"${truncate(v, 12)}"` : String(v ?? '');
  return `${col} ${op} ${valStr}`;
}

/** 排序键 → 可读字符串。 */
function sortKeyToText(sk: { column: string; direction: string }): string {
  const arrow = sk.direction === 'DESC' ? '↓' : '↑';
  return `${sk.column || '?'}${arrow}`;
}

/**
 * 根据算子类型推导节点配置摘要。
 *
 * @param node IR 节点
 * @param datasets 数据集列表（用于 Source/Sink 把 api_name 转 display_name）
 * @returns 1-3 行摘要；空数组表示无配置可显示
 */
export function getConfigSummary(
  node: IRNode,
  datasets: DatasetOption[] = [],
): SummaryLine[] {
  const cfg = node.config ?? {};
  switch (node.operator_type) {
    case 'Source': {
      const ds = (cfg.extra?.dataset as string | undefined) ?? '';
      if (!ds) return [{ text: '未选择数据集', primary: true }];
      return [{ text: truncate(datasetLabel(ds, datasets), 28), primary: true }];
    }

    case 'Sink': {
      const ds = (cfg.extra?.dataset as string | undefined) ?? '';
      const mode = (cfg.extra?.write_mode as string | undefined) ?? 'FULL_REFRESH';
      const modeLabel = mode === 'APPEND' ? '增量追加' : '全量重建';
      if (!ds) return [{ text: '未选择目标', primary: true }];
      return [
        { text: truncate(datasetLabel(ds, datasets), 28), primary: true },
        { text: modeLabel },
      ];
    }

    case 'Filter': {
      const conds = cfg.filter_conditions ?? [];
      const expr = cfg.expression;
      if (expr) {
        return [{ text: truncate(expr, 32), primary: true }];
      }
      if (conds.length === 0) {
        return [{ text: '未设置条件', primary: true }];
      }
      if (conds.length === 1) {
        return [{ text: truncate(filterConditionToText(conds[0]), 32), primary: true }];
      }
      // 多条件：显示第一个 + 计数
      return [
        { text: truncate(filterConditionToText(conds[0]), 28), primary: true },
        { text: `共 ${conds.length} 个条件` },
      ];
    }

    case 'Select': {
      const cols = cfg.columns ?? [];
      if (cols.length === 0) return [{ text: '保留所有列', primary: true }];
      if (cols.length <= 3) {
        return [{ text: cols.join(', '), primary: true }];
      }
      return [
        { text: cols.slice(0, 3).join(', ') + ' …', primary: true },
        { text: `共 ${cols.length} 列` },
      ];
    }

    case 'Rename': {
      const mapping = cfg.column_mapping ?? {};
      const entries = Object.entries(mapping).filter(([k]) => k);
      if (entries.length === 0) return [{ text: '未设置', primary: true }];
      if (entries.length === 1) {
        const [o, n] = entries[0];
        return [{ text: `${o} → ${n}`, primary: true }];
      }
      return [
        { text: `${entries[0][0]} → ${entries[0][1]}`, primary: true },
        { text: `共 ${entries.length} 列重命名` },
      ];
    }

    case 'TypeCast': {
      const casts = cfg.cast_columns ?? [];
      if (casts.length === 0) return [{ text: '未设置', primary: true }];
      if (casts.length <= 2) {
        return [
          { text: casts.map((c) => `${c.column}→${c.target_type}`).join(', '), primary: true },
        ];
      }
      return [
        { text: casts.slice(0, 2).map((c) => `${c.column}→${c.target_type}`).join(', ') + ' …', primary: true },
        { text: `共 ${casts.length} 列转换` },
      ];
    }

    case 'Join': {
      const joinType = cfg.join_type ?? 'INNER';
      const conds = cfg.join_conditions ?? [];
      const typeLabel = joinType === 'INNER' ? 'INNER' : joinType === 'LEFT' ? 'LEFT' : joinType === 'RIGHT' ? 'RIGHT' : 'FULL';
      if (conds.length === 0) {
        return [{ text: `${typeLabel} · 未设置关联键`, primary: true }];
      }
      const first = conds[0];
      const condText = `${first.left_column || '?'} = ${first.right_column || '?'}`;
      if (conds.length === 1) {
        return [{ text: `${typeLabel} · ${condText}`, primary: true }];
      }
      return [
        { text: `${typeLabel} · ${condText}`, primary: true },
        { text: `共 ${conds.length} 个关联键` },
      ];
    }

    case 'Aggregate': {
      const groupBy = cfg.group_by ?? [];
      const aggs = cfg.aggregations ?? [];
      const groupText = groupBy.length > 0 ? truncate(groupBy.join(', '), 16) : '无分组';
      if (aggs.length === 0) {
        return [{ text: `按 ${groupText} 聚合`, primary: true }];
      }
      if (aggs.length === 1) {
        const a = aggs[0];
        const aggText = `${a.function}(${a.field || '?'})`;
        return [{ text: `按 ${groupText} · ${aggText}`, primary: true }];
      }
      return [
        { text: `按 ${groupText}`, primary: true },
        { text: `${aggs.length} 个聚合` },
      ];
    }

    case 'Union': {
      return [{ text: '纵向合并', primary: true }];
    }

    case 'Expression': {
      const expr = cfg.expression;
      if (!expr) return [{ text: '未设置表达式', primary: true }];
      return [{ text: truncate(expr, 32), primary: true }];
    }

    case 'Deduplicate': {
      const keys = cfg.columns ?? [];
      if (keys.length === 0) return [{ text: '未设置去重键', primary: true }];
      if (keys.length <= 3) {
        return [{ text: `按 ${keys.join(', ')} 去重`, primary: true }];
      }
      return [
        { text: `按 ${keys.slice(0, 3).join(', ')} … 去重`, primary: true },
        { text: `共 ${keys.length} 个键` },
      ];
    }

    case 'Sort': {
      const keys = cfg.sort_keys ?? [];
      if (keys.length === 0) return [{ text: '未设置排序', primary: true }];
      return [{ text: keys.slice(0, 3).map(sortKeyToText).join('  '), primary: true }];
    }

    case 'QualityCheck': {
      const rules = cfg.quality_rules ?? [];
      if (rules.length === 0) return [{ text: '未设置规则', primary: true }];
      const RULE_LABEL: Record<string, string> = {
        not_null: '非空',
        unique: '唯一',
        range: '范围',
        regex: '正则',
        expression: '表达式',
      };
      if (rules.length === 1) {
        const r = rules[0];
        const label = RULE_LABEL[r.rule_type] ?? r.rule_type;
        const field = r.field || '?';
        return [{ text: `${label}: ${field}`, primary: true }];
      }
      return [
        { text: `${rules.length} 条质量规则`, primary: true },
        { text: rules.slice(0, 2).map((r) => r.field || '?').join(', ') + ' …' },
      ];
    }

    default:
      return [];
  }
}
