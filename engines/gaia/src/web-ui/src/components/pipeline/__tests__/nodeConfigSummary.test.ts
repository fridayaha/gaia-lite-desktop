/**
 * nodeConfigSummary 单元测试 — 验证各算子配置摘要的推导逻辑。
 *
 * 覆盖：
 * - 每种算子的正常路径（显示核心信息）
 * - 空配置（显示"未设置"提示）
 * - 多条件/多列降级为计数摘要
 * - 数据集 api_name → display_name 解析
 */
import { describe, it, expect } from 'vitest';
import { getConfigSummary } from '../nodeConfigSummary';
import type { IRNode } from '../../../types/pipeline';
import type { DatasetGovernance } from '../../../types';

function makeNode(
  operatorType: IRNode['operator_type'],
  config: IRNode['config'] = {},
  label = '测试节点',
): IRNode {
  return {
    id: 'n1',
    type: 'Transform',
    operator_type: operatorType,
    label,
    description: '',
    input_schemas: [],
    output_schema: null,
    config,
    position: { x: 0, y: 0 },
  };
}

const datasets: Array<Pick<DatasetGovernance, 'api_name' | 'display_name'>> = [
  { api_name: 'orders', display_name: '订单表' },
  { api_name: 'customers', display_name: '客户表' },
];

describe('getConfigSummary', () => {
  describe('Source', () => {
    it('已选数据集 → 显示 display_name', () => {
      const node = makeNode('Source', { extra: { dataset: 'orders' } });
      const summary = getConfigSummary(node, datasets);
      expect(summary).toEqual([{ text: '订单表', primary: true }]);
    });

    it('未选数据集 → 显示提示', () => {
      const node = makeNode('Source', { extra: {} });
      const summary = getConfigSummary(node, datasets);
      expect(summary[0].text).toBe('未选择数据集');
    });

    it('display_name 缺失时回退 api_name', () => {
      const node = makeNode('Source', { extra: { dataset: 'unknown_ds' } });
      const summary = getConfigSummary(node, datasets);
      expect(summary[0].text).toBe('unknown_ds');
    });
  });

  describe('Sink', () => {
    it('显示目标 + 写入模式', () => {
      const node = makeNode('Sink', { extra: { dataset: 'orders', write_mode: 'APPEND' } });
      const summary = getConfigSummary(node, datasets);
      expect(summary).toEqual([
        { text: '订单表', primary: true },
        { text: '增量追加' },
      ]);
    });

    it('默认写入模式为全量重建', () => {
      const node = makeNode('Sink', { extra: { dataset: 'orders' } });
      const summary = getConfigSummary(node, datasets);
      expect(summary[1].text).toBe('全量重建');
    });
  });

  describe('Filter', () => {
    it('单条件 → 显示条件文本', () => {
      const node = makeNode('Filter', {
        filter_conditions: [{ column: 'status', operator: 'eq', value: 'active' }],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('status = "active"');
    });

    it('多条件 → 显示第一个 + 计数', () => {
      const node = makeNode('Filter', {
        filter_conditions: [
          { column: 'status', operator: 'eq', value: 'active' },
          { column: 'amount', operator: 'gt', value: 100 },
          { column: 'region', operator: 'eq', value: 'CN' },
        ],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('status = "active"');
      expect(summary[1].text).toBe('共 3 个条件');
    });

    it('高级表达式 → 显示表达式', () => {
      const node = makeNode('Filter', { expression: 'amount > 100 AND status = 1' });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('amount > 100 AND status = 1');
    });

    it('空配置 → 显示未设置', () => {
      const node = makeNode('Filter', {});
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('未设置条件');
    });

    it('is_null 操作符不显示值', () => {
      const node = makeNode('Filter', {
        filter_conditions: [{ column: 'email', operator: 'is_null' }],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('email 为空');
    });
  });

  describe('Select', () => {
    it('≤3 列 → 显示列名', () => {
      const node = makeNode('Select', { columns: ['a', 'b'] });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('a, b');
    });

    it('>3 列 → 显示前 3 + 计数', () => {
      const node = makeNode('Select', { columns: ['a', 'b', 'c', 'd', 'e'] });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('a, b, c …');
      expect(summary[1].text).toBe('共 5 列');
    });

    it('空 → 保留所有列', () => {
      const node = makeNode('Select', {});
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('保留所有列');
    });
  });

  describe('Join', () => {
    it('单关联键 → 显示类型 + 条件', () => {
      const node = makeNode('Join', {
        join_type: 'LEFT',
        join_conditions: [{ left_column: 'order_id', right_column: 'order_id' }],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('LEFT · order_id = order_id');
    });

    it('多关联键 → 显示第一个 + 计数', () => {
      const node = makeNode('Join', {
        join_type: 'INNER',
        join_conditions: [
          { left_column: 'a', right_column: 'a' },
          { left_column: 'b', right_column: 'b' },
        ],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('INNER · a = a');
      expect(summary[1].text).toBe('共 2 个关联键');
    });

    it('未设置关联键 → 提示', () => {
      const node = makeNode('Join', { join_type: 'INNER' });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('INNER · 未设置关联键');
    });
  });

  describe('Aggregate', () => {
    it('单聚合 → 显示分组 + 聚合', () => {
      const node = makeNode('Aggregate', {
        group_by: ['user_id'],
        aggregations: [{ field: 'amount', function: 'SUM' }],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('按 user_id · SUM(amount)');
    });

    it('多聚合 → 显示分组 + 计数', () => {
      const node = makeNode('Aggregate', {
        group_by: ['user_id'],
        aggregations: [
          { field: 'amount', function: 'SUM' },
          { field: 'id', function: 'COUNT' },
        ],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('按 user_id');
      expect(summary[1].text).toBe('2 个聚合');
    });

    it('无分组', () => {
      const node = makeNode('Aggregate', { aggregations: [{ field: 'amount', function: 'SUM' }] });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('按 无分组 · SUM(amount)');
    });
  });

  describe('Sort', () => {
    it('显示排序键（含方向箭头）', () => {
      const node = makeNode('Sort', {
        sort_keys: [
          { column: 'amount', direction: 'DESC' },
          { column: 'created_at', direction: 'ASC' },
        ],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('amount↓  created_at↑');
    });

    it('空 → 未设置', () => {
      const node = makeNode('Sort', {});
      expect(getConfigSummary(node)[0].text).toBe('未设置排序');
    });
  });

  describe('QualityCheck', () => {
    it('单规则 → 显示规则类型 + 字段', () => {
      const node = makeNode('QualityCheck', {
        quality_rules: [{ rule_type: 'not_null', field: 'email', config: {}, severity: 'ERROR', message: '' }],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('非空: email');
    });

    it('多规则 → 显示计数', () => {
      const node = makeNode('QualityCheck', {
        quality_rules: [
          { rule_type: 'not_null', field: 'email', config: {}, severity: 'ERROR', message: '' },
          { rule_type: 'unique', field: 'phone', config: {}, severity: 'ERROR', message: '' },
          { rule_type: 'range', field: 'age', config: { min: 0, max: 150 }, severity: 'WARNING', message: '' },
        ],
      });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('3 条质量规则');
    });
  });

  describe('其他算子', () => {
    it('Rename 单列', () => {
      const node = makeNode('Rename', { column_mapping: { old_name: 'new_name' } });
      expect(getConfigSummary(node)[0].text).toBe('old_name → new_name');
    });

    it('TypeCast 多列降级计数', () => {
      const node = makeNode('TypeCast', {
        cast_columns: [
          { column: 'a', target_type: 'STRING' },
          { column: 'b', target_type: 'INTEGER' },
          { column: 'c', target_type: 'DATE' },
        ],
      });
      const summary = getConfigSummary(node);
      expect(summary[1].text).toBe('共 3 列转换');
    });

    it('Deduplicate 多键降级', () => {
      const node = makeNode('Deduplicate', { columns: ['a', 'b', 'c', 'd'] });
      const summary = getConfigSummary(node);
      expect(summary[0].text).toBe('按 a, b, c … 去重');
      expect(summary[1].text).toBe('共 4 个键');
    });

    it('Expression 显示表达式', () => {
      const node = makeNode('Expression', { expression: 'amount * 1.1' });
      expect(getConfigSummary(node)[0].text).toBe('amount * 1.1');
    });

    it('Union 固定摘要', () => {
      const node = makeNode('Union', {});
      expect(getConfigSummary(node)[0].text).toBe('纵向合并');
    });

    it('未知算子 → 空数组', () => {
      const node = makeNode('' as IRNode['operator_type'], {});
      expect(getConfigSummary(node)).toEqual([]);
    });
  });

  describe('截断', () => {
    it('长表达式被截断', () => {
      const longExpr = 'a'.repeat(50);
      const node = makeNode('Expression', { expression: longExpr });
      const summary = getConfigSummary(node);
      expect(summary[0].text.length).toBeLessThan(longExpr.length);
      expect(summary[0].text.endsWith('…')).toBe(true);
    });
  });
});
