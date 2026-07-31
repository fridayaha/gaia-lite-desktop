/**
 * usePipelineBuilder — Pipeline Builder 画布状态管理（Zustand）。
 *
 * ADR-018 D10 核心设计：graph (nodes+edges) 是单一真相源，
 * 表单/JSON/布局都是派生视图。
 *
 * Undo/redo 通过手动维护历史栈实现（轻量级，不依赖 zundo）。
 */
import { create } from 'zustand';
import {
  applyEdgeChanges,
  type NodeChange,
  type EdgeChange,
  type Connection,
} from '@xyflow/react';
import type {
  IRNode,
  IREdge,
  PipelineIR,
  NodeType,
  OperatorType,
  PipelineResponse,
  BuildResponse,
  ContractViolation,
  Schema,
} from '../types/pipeline';
import type { DatasetGovernance } from '../types';
import { getNodeDef } from '../components/pipeline/NodeRegistry';

// ── Helper: 生成唯一 ID ──

let _nodeCounter = 0;
function generateNodeId(): string {
  _nodeCounter += 1;
  return `node_${Date.now().toString(36)}_${_nodeCounter}`;
}

function generateEdgeId(source: string, target: string, sourcePort = 'default', targetPort = 'default'): string {
  // 含 port 维度：同一 source→target 不同端口组合需独立 id
  if (sourcePort === 'default' && targetPort === 'default') {
    return `edge_${source}_${target}`;
  }
  return `edge_${source}_${sourcePort}_${target}_${targetPort}`;
}

// ── 深拷贝（优先 structuredClone，回退 JSON.parse 以兼容旧环境）──
const deepClone: <T>(obj: T) => T =
  typeof structuredClone === 'function'
    ? structuredClone
    : (obj) => JSON.parse(JSON.stringify(obj));

// ── 历史快照 ──

interface Snapshot {
  irNodes: IRNode[];
  irEdges: IREdge[];
}

// ── State 类型 ──

export interface PipelineBuilderState {
  /** 当前编辑的管道元信息。 */
  pipeline: PipelineResponse | null;
  /** IR 节点列表（单一真相源）。 */
  irNodes: IRNode[];
  /** IR 边列表（单一真相源）。 */
  irEdges: IREdge[];
  /** 选中的节点 ID（单选，用于配置弹窗/辅助面板高亮）。 */
  selectedNodeId: string | null;
  /** 多选节点 ID 集合（框选/Shift+点选，用于对齐/分布/复制等批量操作）。 */
  selectedNodeIds: string[];
  /** 左侧算子面板是否折叠。 */
  nodePanelCollapsed: boolean;
  /** Schema 校验状态。 */
  validationErrors: ContractViolation[];
  validationValid: boolean;
  /** 每个节点的输出 Schema（由 validate 接口回填，供配置面板渲染列下拉）。 */
  nodeSchemas: Record<string, Schema>;
  /** 当前选中的辅助视图 tab（Schema/执行/JSON 弹窗用）。configure 已移除（配置走弹窗）。 */
  activePanelTab: 'schema' | 'history' | 'json';
  /** 是否正在加载。 */
  loading: boolean;
  /** 错误信息。 */
  error: string | null;
  /** 构建历史（最近列表）。 */
  builds: BuildResponse[];
  /** 数据集列表（供 Source/Sink 选择）。 */
  datasets: Pick<DatasetGovernance, 'api_name' | 'display_name'>[];
  /** 是否有未保存的改动。 */
  isDirty: boolean;

  // ── Undo/Redo ──
  pastSnapshots: Snapshot[];
  futureSnapshots: Snapshot[];

  // ── Actions ──

  /** 初始化/加载管道。 */
  loadPipeline: (pipeline: PipelineResponse, graph: PipelineIR) => void;
  /** 重置状态（新建管道时）。 */
  reset: () => void;
  /** 添加一个 IR 节点（从算子面板拖拽创建）。 */
  addNode: (type: NodeType, operatorType: OperatorType, label: string, position: { x: number; y: number }) => string;
  /** 更新一个 IR 节点。 */
  updateNode: (nodeId: string, updates: Partial<IRNode>) => void;
  /** 删除一个 IR 节点。 */
  removeNode: (nodeId: string) => void;
  /** React Flow 节点变更回调。 */
  onNodesChange: (changes: NodeChange[]) => void;
  /** React Flow 边变更回调。 */
  onEdgesChange: (changes: EdgeChange[]) => void;
  /** React Flow 连线回调。 */
  onConnect: (connection: Connection) => void;
  /** 设置单选节点（同时清空多选）。 */
  setSelectedNode: (nodeId: string | null) => void;
  /** 设置多选节点集合。 */
  setSelectedNodeIds: (ids: string[]) => void;
  /** 复制选中节点（生成副本，位移错开，返回新节点 id 列表）。 */
  duplicateNodes: (ids: string[]) => string[];
  /** 批量更新节点位置（对齐/分布用，单次快照）。 */
  updateNodePositions: (updates: Array<{ id: string; position: { x: number; y: number } }>) => void;
  /** 切换左侧面板折叠。 */
  toggleNodePanel: () => void;
  /** 设置右侧面板 tab。 */
  setActivePanelTab: (tab: 'schema' | 'history' | 'json') => void;
  /** 设置数据集列表。 */
  setDatasets: (datasets: Pick<DatasetGovernance, 'api_name' | 'display_name'>[]) => void;
  /** 设置构建历史。 */
  setBuilds: (builds: BuildResponse[]) => void;
  /** 设置校验状态。 */
  setValidation: (
    valid: boolean,
    errors: ContractViolation[],
    nodeSchemas?: Record<string, Schema>,
  ) => void;
  /** 将当前 IR 序列化为 PipelineIR。 */
  serializeIR: () => PipelineIR;
  /** 从 PipelineIR 反序列化恢复画布。 */
  deserializeIR: (ir: PipelineIR) => void;
  /** 标记为已保存。 */
  markClean: () => void;

  // ── Undo/Redo Actions ──
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;

  // ── 内部 ──
  /** 保存当前 IR 快照到历史栈（修改 IR 前调用）。 */
  _pushSnapshot: () => void;
}

// ── Store ──

const MAX_HISTORY = 50;

export const usePipelineBuilder = create<PipelineBuilderState>()(
  (set, get) => ({
    pipeline: null,
    irNodes: [],
    irEdges: [],
    selectedNodeId: null,
    selectedNodeIds: [],
    nodePanelCollapsed: false,
    validationErrors: [],
    validationValid: true,
    nodeSchemas: {},
    activePanelTab: 'schema',
    loading: false,
    error: null,
    builds: [],
    datasets: [],
    isDirty: false,
    pastSnapshots: [],
    futureSnapshots: [],

    _pushSnapshot: () => {
      set((state) => {
        const snapshot: Snapshot = {
          irNodes: deepClone(state.irNodes),
          irEdges: deepClone(state.irEdges),
        };
        const past = [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY);
        return { pastSnapshots: past, futureSnapshots: [] };
      });
    },

    loadPipeline: (pipeline, graph) => {
      set({
        pipeline,
        irNodes: graph.nodes,
        irEdges: graph.edges ?? [],
        selectedNodeId: null,
        selectedNodeIds: [],
        isDirty: false,
        error: null,
        pastSnapshots: [],
        futureSnapshots: [],
      });
    },

    reset: () => {
      set({
        pipeline: null,
        irNodes: [],
        irEdges: [],
        selectedNodeId: null,
        selectedNodeIds: [],
        validationErrors: [],
        validationValid: true,
        nodeSchemas: {},
        isDirty: false,
        error: null,
        pastSnapshots: [],
        futureSnapshots: [],
      });
    },

    addNode: (type, operatorType, label, position) => {
      const id = generateNodeId();
      const def = getNodeDef(operatorType);
      const node: IRNode = {
        id,
        type,
        operator_type: operatorType,
        label,
        description: def?.description ?? '',
        input_schemas: [],
        output_schema: null,
        config: {},
        position,
      };
      set((state) => ({
        irNodes: [...state.irNodes, node],
        isDirty: true,
        selectedNodeId: id,
        activePanelTab: 'schema',
        pastSnapshots: [
          ...state.pastSnapshots,
          {
            irNodes: deepClone(state.irNodes),
            irEdges: deepClone(state.irEdges),
          },
        ].slice(-MAX_HISTORY),
        futureSnapshots: [],
      }));
      return id;
    },

    updateNode: (nodeId, updates) => {
      set((state) => ({
        irNodes: state.irNodes.map((n) =>
          n.id === nodeId
            ? { ...n, ...updates, config: { ...n.config, ...((updates as any).config ?? {}) } }
            : n,
        ),
        isDirty: true,
      }));
    },

    removeNode: (nodeId) => {
      set((state) => {
        const snapshot: Snapshot = {
          irNodes: deepClone(state.irNodes),
          irEdges: deepClone(state.irEdges),
        };
        return {
          irNodes: state.irNodes.filter((n) => n.id !== nodeId),
          irEdges: state.irEdges.filter((e) => e.source_id !== nodeId && e.target_id !== nodeId),
          selectedNodeId: state.selectedNodeId === nodeId ? null : state.selectedNodeId,
          selectedNodeIds: state.selectedNodeIds.filter((id) => id !== nodeId),
          isDirty: true,
          pastSnapshots: [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY),
          futureSnapshots: [],
        };
      });
    },

    onNodesChange: (changes) => {
      set((state) => {
        // 处理 remove 变更（Delete 键 / deleteElements）：复用 removeNode 逻辑
        // （带 snapshot 历史 + 级联删边 + 清理选中态）。
        const removeIds = new Set<string>();
        // 处理 position 变更（拖拽）
        const updatedPositions = new Map<string, { x: number; y: number }>();
        const updatedMeasured = new Map<string, { width: number; height: number }>();
        // 处理 select 变更（框选 / Shift+点选）→ 写入多选集合 selectedNodeIds
        // 注意：不动 selectedNodeId（单选仍由 onNodeClick 管理），避免 xyflow #2405 虚假 select change 闪退
        const selectChanges: Array<{ id: string; selected: boolean }> = [];
        for (const change of changes) {
          if (change.type === 'remove') {
            removeIds.add(change.id);
            continue;
          }
          if (change.type === 'select') {
            selectChanges.push({ id: change.id, selected: !!change.selected });
            continue;
          }
          if (change.type === 'position' && change.position) {
            updatedPositions.set(change.id, {
              x: change.position.x ?? change.positionAbsolute?.x ?? 0,
              y: change.position.y ?? change.positionAbsolute?.y ?? 0,
            });
          }
          // React Flow v12: dimensions change 携带节点测量尺寸，EdgeRenderer 依赖它渲染边
          if (change.type === 'dimensions' && change.dimensions) {
            updatedMeasured.set(change.id, {
              width: change.dimensions.width,
              height: change.dimensions.height,
            });
          }
        }

        // remove 走单独分支：需要快照历史 + 级联删边 + 清选中
        if (removeIds.size > 0) {
          const snapshot: Snapshot = {
            irNodes: deepClone(state.irNodes),
            irEdges: deepClone(state.irEdges),
          };
          return {
            irNodes: state.irNodes.filter((n) => !removeIds.has(n.id)),
            irEdges: state.irEdges.filter(
              (e) => !removeIds.has(e.source_id) && !removeIds.has(e.target_id),
            ),
            selectedNodeId: removeIds.has(state.selectedNodeId ?? '')
              ? null
              : state.selectedNodeId,
            selectedNodeIds: state.selectedNodeIds.filter((id) => !removeIds.has(id)),
            isDirty: true,
            pastSnapshots: [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY),
            futureSnapshots: [],
          };
        }

        // 选中态不由 onNodesChange 管理：受控模式下程序化 setNodes 会触发虚假的
        // select change（xyflow #2405），若在此处理会误清 selectedNodeId 导致配置面板闪退。
        // 选中态完全交由 onNodeClick / onPaneClick 显式管理（见 PipelineBuilderPage）。
        //
        // position change 标脏规则：
        // - dragging === true  ：用户正在拖拽中（过程事件），不标脏（避免高频触发 auto-save）
        // - dragging === false ：用户拖拽结束（松手），标脏
        // - dragging === undefined：程序化 setNodes（auto-save 回调、loadPipeline、ELK 布局回写），
        //   不标脏——否则会形成 "布局回写 position → onNodesChange → isDirty → auto-save → 回调再 setNodes" 的回弹循环
        const userDragEnded = changes.some(
          (c) => c.type === 'position' && c.dragging === false,
        );
        const hasUpdates = updatedPositions.size > 0 || updatedMeasured.size > 0;

        // 多选集合更新：基于现有 selectedNodeIds 增量应用 selectChanges
        let nextSelectedIds = state.selectedNodeIds;
        if (selectChanges.length > 0) {
          const idSet = new Set(state.selectedNodeIds);
          for (const sc of selectChanges) {
            if (sc.selected) idSet.add(sc.id);
            else idSet.delete(sc.id);
          }
          nextSelectedIds = Array.from(idSet);
          // 用户开始框选/多选时，清空单选（避免单选与多选高亮并存造成困惑）
          // 仅当多选不止一个时才清单选，单选单击仍由 onNodeClick 处理
          if (nextSelectedIds.length > 1) {
            return {
              irNodes: hasUpdates
                ? state.irNodes.map((n) => {
                    const pos = updatedPositions.get(n.id);
                    const measured = updatedMeasured.get(n.id);
                    return pos || measured
                      ? { ...n, position: pos ?? n.position, measured: measured ?? n.measured }
                      : n;
                  })
                : state.irNodes,
              isDirty: userDragEnded ? true : state.isDirty,
              selectedNodeIds: nextSelectedIds,
              selectedNodeId: null,
            };
          }
        }

        return {
          irNodes: hasUpdates
            ? state.irNodes.map((n) => {
                const pos = updatedPositions.get(n.id);
                const measured = updatedMeasured.get(n.id);
                return pos || measured
                  ? { ...n, position: pos ?? n.position, measured: measured ?? n.measured }
                  : n;
              })
            : state.irNodes,
          isDirty: userDragEnded ? true : state.isDirty,
          selectedNodeIds: nextSelectedIds,
        };
      });
    },

    onEdgesChange: (changes) => {
      set((state) => {
        // remove 变更走单独分支：需要快照历史（对齐 onNodesChange 的 remove 分支），
        // 否则删边后无法撤销。
        const removeIds = new Set<string>();
        const otherChanges = [];
        for (const change of changes) {
          if (change.type === 'remove') {
            removeIds.add(change.id);
          } else {
            otherChanges.push(change);
          }
        }

        if (removeIds.size > 0) {
          const snapshot: Snapshot = {
            irNodes: deepClone(state.irNodes),
            irEdges: deepClone(state.irEdges),
          };
          return {
            irEdges: state.irEdges.filter((e) => !removeIds.has(e.id)),
            isDirty: true,
            pastSnapshots: [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY),
            futureSnapshots: [],
          };
        }

        const rfEdges = state.irEdges.map((e) => ({
          id: e.id,
          source: e.source_id,
          target: e.target_id,
          sourceHandle: e.source_port,
          targetHandle: e.target_port,
          selected: e.selected,
        }));
        const updated = applyEdgeChanges(otherChanges, rfEdges);
        // 判断是否仅选中变更（不涉及数据修改，不标 dirty）
        const onlySelection = otherChanges.every((c) => c.type === 'select');
        return {
          irEdges: updated.map((e) => ({
            id: e.id,
            source_id: e.source,
            target_id: e.target,
            source_port: (e.sourceHandle as string) ?? 'default',
            target_port: (e.targetHandle as string) ?? 'default',
            selected: e.selected,
          })),
          ...(onlySelection ? {} : { isDirty: true }),
        };
      });
    },

    onConnect: (connection) => {
      if (!connection.source || !connection.target) return;
      const sourcePort = (connection.sourceHandle as string) ?? 'default';
      const targetPort = (connection.targetHandle as string) ?? 'default';
      const edgeId = generateEdgeId(connection.source, connection.target, sourcePort, targetPort);
      set((state) => {
        // 端口级单输入约束：同一个输入端口只能有一条入边。
        // （旧版只按 source→target 去重，导致同一输入端口可接多条边）
        const portOccupied = state.irEdges.some(
          (e) => e.target_id === connection.target && e.target_port === targetPort,
        );
        if (portOccupied) return state;
        // 同 source→target 同 port 组合去重
        const exists = state.irEdges.some(
          (e) =>
            e.source_id === connection.source &&
            e.target_id === connection.target &&
            e.source_port === sourcePort &&
            e.target_port === targetPort,
        );
        if (exists) return state;

        const snapshot: Snapshot = {
          irNodes: deepClone(state.irNodes),
          irEdges: deepClone(state.irEdges),
        };

        return {
          irEdges: [
            ...state.irEdges,
            {
              id: edgeId,
              source_id: connection.source,
              target_id: connection.target,
              source_port: sourcePort,
              target_port: targetPort,
            },
          ],
          isDirty: true,
          pastSnapshots: [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY),
          futureSnapshots: [],
        };
      });
    },

    setSelectedNode: (nodeId) => {
      // 单选时清空多选集合（单选与多选互斥）
      set({ selectedNodeId: nodeId, selectedNodeIds: [] });
    },

    setSelectedNodeIds: (ids) => {
      set({ selectedNodeIds: ids });
    },

    duplicateNodes: (ids) => {
      const state = get();
      const toDup = state.irNodes.filter((n) => ids.includes(n.id));
      if (toDup.length === 0) return [];
      const snapshot: Snapshot = {
        irNodes: deepClone(state.irNodes),
        irEdges: deepClone(state.irEdges),
      };
      const newIds: string[] = [];
      const newNodes: IRNode[] = toDup.map((n) => {
        const newId = `${n.operator_type.toLowerCase()}_${crypto.randomUUID().slice(0, 8)}`;
        newIds.push(newId);
        return {
          ...deepClone(n),
          id: newId,
          label: `${n.label} 副本`,
          position: { x: n.position.x + 40, y: n.position.y + 40 },
          measured: undefined,
          output_schema: null,
        };
      });
      set({
        irNodes: [...state.irNodes, ...newNodes],
        isDirty: true,
        pastSnapshots: [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY),
        futureSnapshots: [],
        selectedNodeIds: newIds,
        selectedNodeId: null,
      });
      return newIds;
    },

    updateNodePositions: (updates) => {
      const state = get();
      if (updates.length === 0) return;
      const snapshot: Snapshot = {
        irNodes: deepClone(state.irNodes),
        irEdges: deepClone(state.irEdges),
      };
      const posMap = new Map(updates.map((u) => [u.id, u.position]));
      set({
        irNodes: state.irNodes.map((n) => {
          const pos = posMap.get(n.id);
          return pos ? { ...n, position: pos } : n;
        }),
        isDirty: true,
        pastSnapshots: [...state.pastSnapshots, snapshot].slice(-MAX_HISTORY),
        futureSnapshots: [],
      });
    },

    toggleNodePanel: () => {
      set((state) => ({ nodePanelCollapsed: !state.nodePanelCollapsed }));
    },

    setActivePanelTab: (tab) => {
      set({ activePanelTab: tab });
    },

    setDatasets: (datasets) => {
      set({ datasets });
    },

    setBuilds: (builds) => {
      set({ builds });
    },

    setValidation: (valid, errors, nodeSchemas) => {
      // 只存 nodeSchemas 到 state，不回填到 irNodes（避免修改 irNodes 引用
      // 触发 validate effect 循环）。配置面板通过 nodeSchemas + irEdges
      // 自行推导上游 input schema。
      if (nodeSchemas) {
        set({ validationValid: valid, validationErrors: errors, nodeSchemas });
      } else {
        set({ validationValid: valid, validationErrors: errors });
      }
    },

    serializeIR: () => {
      const state = get();
      return {
        nodes: state.irNodes,
        edges: state.irEdges,
        write_mode: state.pipeline?.write_mode ?? 'FULL_REFRESH',
        trigger_index_sync: false,
        tags: [],
        owner: null,
        business_domain: null,
      };
    },

    deserializeIR: (ir) => {
      set({
        irNodes: ir.nodes,
        irEdges: ir.edges,
        isDirty: false,
        selectedNodeId: null,
        selectedNodeIds: [],
        pastSnapshots: [],
        futureSnapshots: [],
      });
    },

    markClean: () => {
      set({ isDirty: false });
    },

    undo: () => {
      const state = get();
      if (state.pastSnapshots.length === 0) return;
      const snapshot = state.pastSnapshots[state.pastSnapshots.length - 1];
      const newPast = state.pastSnapshots.slice(0, -1);
      const currentSnapshot: Snapshot = {
        irNodes: deepClone(state.irNodes),
        irEdges: deepClone(state.irEdges),
      };
      set({
        irNodes: snapshot.irNodes,
        irEdges: snapshot.irEdges,
        pastSnapshots: newPast,
        futureSnapshots: [...state.futureSnapshots, currentSnapshot],
        isDirty: true,
      });
    },

    redo: () => {
      const state = get();
      if (state.futureSnapshots.length === 0) return;
      const snapshot = state.futureSnapshots[state.futureSnapshots.length - 1];
      const newFuture = state.futureSnapshots.slice(0, -1);
      const currentSnapshot: Snapshot = {
        irNodes: deepClone(state.irNodes),
        irEdges: deepClone(state.irEdges),
      };
      set({
        irNodes: snapshot.irNodes,
        irEdges: snapshot.irEdges,
        pastSnapshots: [...state.pastSnapshots, currentSnapshot],
        futureSnapshots: newFuture,
        isDirty: true,
      });
    },

    canUndo: () => get().pastSnapshots.length > 0,
    canRedo: () => get().futureSnapshots.length > 0,
  }),
);
