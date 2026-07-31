/**
 * Pipeline Builder 节点组件注册表。
 *
 * ADR-018 §14.1 F8：NodeRegistry 注册表，避免 switch-case 膨胀。
 * 每个节点类型注册自己的渲染组件 + 配置面板组件。
 *
 * IMPORTANT: ``type``（即 ``operator_type``）必须与后端
 * ``SchemaInferenceEngine`` registry 完全一致（单一真相源在后端
 * ``/api/v1/pipelines/operators``）。前端 NodeRegistry 的 ``type`` 是后端
 * operator_type 的镜像，新增算子必须同时改后端 registry + 这里。
 */
import type { ComponentType } from 'react';
import type { Node, NodeProps } from '@xyflow/react';
import { BaseSourceNode } from './BaseSourceNode';
import { BaseTransformNode } from './BaseTransformNode';
import { BaseSinkNode } from './BaseSinkNode';
import type { IRNode } from '../../types/pipeline';
import type { PipelineNodeData, OperatorType, NodeType } from '../../types/pipeline';

export interface NodeDefinition {
  /** 节点展示类型 key（用于 NodePanel 分组 + nodeTypes 注册）。 */
  panelKey: string;
  /** 算子类型 — 与后端 SchemaInferenceEngine registry 一致。 */
  type: OperatorType;
  category: 'source' | 'transform' | 'sink' | 'quality' | 'kestra';
  displayName: string;
  description: string;
  component: ComponentType<NodeProps<Node<PipelineNodeData>>>;
  /** 可选：节点配置表单组件。不注册时使用通用配置面板。 */
  configComponent?: ComponentType<{
    node: IRNode;
    datasets: string[];
    onChange: (nodeId: string, updates: Partial<IRNode>) => void;
  }>;
  inputPorts: number;
  outputPorts: number;
  defaultNodeType: NodeType;
  /** 创建节点时的默认 config（预设子类型参数，如 QualityCheck 的 rule_type）。 */
  defaultConfig?: Record<string, unknown>;
  configurable: boolean;
}

const nodeDefinitions: NodeDefinition[] = [
  // ── Source 节点 ──
  {
    panelKey: 'Source',
    type: 'Source',
    category: 'source',
    displayName: '数据集 Source',
    description: '读取一个已有的 MANAGED Dataset（Iceberg 表）作为数据源',
    component: BaseSourceNode,
    inputPorts: 0,
    outputPorts: 1,
    defaultNodeType: 'Source',
    configurable: true,
  },
  // ── Transform 节点 ──
  {
    panelKey: 'Filter',
    type: 'Filter',
    category: 'transform',
    displayName: '过滤',
    description: '按条件过滤行，只保留满足条件的记录',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Select',
    type: 'Select',
    category: 'transform',
    displayName: '选择列',
    description: '选择/裁剪列，只保留需要的字段',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Rename',
    type: 'Rename',
    category: 'transform',
    displayName: '重命名列',
    description: '重命名一个或多个列',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'TypeCast',
    type: 'TypeCast',
    category: 'transform',
    displayName: '类型转换',
    description: '转换列的数据类型',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Join',
    type: 'Join',
    category: 'transform',
    displayName: '关联 Join',
    description: '将两个数据源按条件关联（Inner/Left/Right/Full）',
    component: BaseTransformNode,
    inputPorts: 2,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Aggregate',
    type: 'Aggregate',
    category: 'transform',
    displayName: '聚合',
    description: '分组聚合计算（SUM/AVG/COUNT/MIN/MAX）',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Union',
    type: 'Union',
    category: 'transform',
    displayName: '合并 Union',
    description: '纵向合并多个数据源（UNION ALL）',
    component: BaseTransformNode,
    inputPorts: 2,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: false,
  },
  {
    panelKey: 'Expression',
    type: 'Expression',
    category: 'transform',
    displayName: '表达式',
    description: '自定义 SQL 表达式生成新列或计算',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Deduplicate',
    type: 'Deduplicate',
    category: 'transform',
    displayName: '去重',
    description: '按指定列去重，保留首条/末条',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  {
    panelKey: 'Sort',
    type: 'Sort',
    category: 'transform',
    displayName: '排序',
    description: '按指定列排序（升/降序）',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'Transform',
    configurable: true,
  },
  // ── Sink 节点 ──
  {
    panelKey: 'Sink',
    type: 'Sink',
    category: 'sink',
    displayName: '输出到 Dataset',
    description: '将结果写入 Iceberg Dataset（全量重建或增量追加）',
    component: BaseSinkNode,
    inputPorts: 1,
    outputPorts: 0,
    defaultNodeType: 'Sink',
    configurable: true,
  },
  // ── QualityCheck 节点（operator_type 统一为 QualityCheck，用 defaultConfig 预设 rule_type）──
  {
    panelKey: 'QualityCheck-NotNull',
    type: 'QualityCheck',
    category: 'quality',
    displayName: '非空校验',
    description: '检查指定列是否有空值',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'QualityCheck',
    defaultConfig: { quality_rules: [{ rule_type: 'not_null', field: '', config: {}, severity: 'ERROR', message: '' }] },
    configurable: true,
  },
  {
    panelKey: 'QualityCheck-Unique',
    type: 'QualityCheck',
    category: 'quality',
    displayName: '唯一校验',
    description: '检查指定列值是否唯一',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'QualityCheck',
    defaultConfig: { quality_rules: [{ rule_type: 'unique', field: '', config: {}, severity: 'ERROR', message: '' }] },
    configurable: true,
  },
  {
    panelKey: 'QualityCheck-Range',
    type: 'QualityCheck',
    category: 'quality',
    displayName: '范围校验',
    description: '检查数值列是否在指定范围内',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'QualityCheck',
    defaultConfig: { quality_rules: [{ rule_type: 'range', field: '', config: { min: 0, max: 100 }, severity: 'ERROR', message: '' }] },
    configurable: true,
  },
  {
    panelKey: 'QualityCheck-Regex',
    type: 'QualityCheck',
    category: 'quality',
    displayName: '正则校验',
    description: '检查文本列是否匹配指定正则',
    component: BaseTransformNode,
    inputPorts: 1,
    outputPorts: 1,
    defaultNodeType: 'QualityCheck',
    defaultConfig: { quality_rules: [{ rule_type: 'regex', field: '', config: { pattern: '' }, severity: 'ERROR', message: '' }] },
    configurable: true,
  },
];

/** 根据 operator_type 查找节点定义（取第一个匹配 — QualityCheck 多个子类型共享渲染）。 */
export function getNodeDef(operatorType: string): NodeDefinition | undefined {
  return nodeDefinitions.find((d) => d.type === operatorType);
}

/** 根据 panelKey 查找特定节点定义（NodePanel 拖拽源用）。 */
export function getNodeDefByPanelKey(panelKey: string): NodeDefinition | undefined {
  return nodeDefinitions.find((d) => d.panelKey === panelKey);
}

/** 根据 category 获取节点定义列表。 */
export function getNodeDefsByCategory(category: string): NodeDefinition[] {
  return nodeDefinitions.filter((d) => d.category === category);
}

/** 获取所有节点定义。 */
export function getAllNodeDefs(): NodeDefinition[] {
  return [...nodeDefinitions];
}

/** 节点类型 → 卡片主题色。 */
export const NODE_COLORS: Record<string, string> = {
  source: '#3b82f6',    // blue
  transform: '#10b981', // emerald
  sink: '#f59e0b',      // amber
  quality: '#8b5cf6',   // violet
  kestra: '#6b7280',    // gray
};

/** 节点类型 → 中文标签。 */
export const NODE_CATEGORY_LABELS: Record<string, string> = {
  source: '数据源',
  transform: '转换',
  sink: '输出',
  quality: '质量',
  kestra: '扩展',
};
