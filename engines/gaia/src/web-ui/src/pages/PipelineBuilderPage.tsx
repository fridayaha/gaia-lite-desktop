/**
 * PipelineBuilderPage — Pipeline Builder 主页面（landing + editing 双模式）。
 *
 * 布局 ──── 画布最大化（2026-07 重构）：
 * ┌────────────┬─────────────────────────────┐
 * │            │   工具栏 (PipelineToolbar)   │
 * │  算子面板   │                             │
 * │ (NodePanel) │   主画布 (React Flow)       │
 * │            │                             │
 * └────────────┴─────────────────────────────┘
 * 配置/Schema/执行/JSON 全部走弹窗（双击节点 → NodeConfigModal；
 * 工具栏按钮 → PipelineAuxModal），画布获得最大留白。
 *
 * AI FDE：复用 AG-UI Agent（图探索 ADR-015 一致范式）。
 * 双模式：landing（对话框引导）→ editing（画布拖拽编辑）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  BackgroundVariant,
  ControlButton,
  type Node,
  type Edge,
  type Connection,
  useReactFlow,
  ReactFlowProvider,
  SelectionMode,
  ConnectionLineType,
  type NodeTypes,
} from '@xyflow/react';
import { usePipelineBuilder } from '../hooks/usePipelineBuilder';
import { useElkLayout } from '../hooks/useElkLayout';
import { useAutoSave } from '../hooks/useAutoSave';
import { getNodeDef, getNodeDefByPanelKey, NODE_COLORS } from '../components/pipeline/NodeRegistry';
import { NodePanel } from '../components/pipeline/NodePanel';
import { PipelineToolbar } from '../components/pipeline/PipelineToolbar';
import type { AuxTab } from '../components/pipeline/PipelineRightPanel';
import { NodeConfigModal } from '../components/pipeline/NodeConfigModal';
import { PipelineAuxModal } from '../components/pipeline/PipelineAuxModal';
import { getConfigSummary } from '../components/pipeline/nodeConfigSummary';
import { SelectionToolbar } from '../components/pipeline/SelectionToolbar';
import { alignNodes, distributeNodes, type AlignMode, type DistributeMode } from '../components/pipeline/nodeAlignment';
import { DeletableEdge } from '../components/pipeline/DeletableEdge';
import { PipelineBuilderLanding } from '../components/pipeline/PipelineBuilderLanding';
import {
  PipelineAiDock,
  loadPipelineDockCollapsed,
  persistPipelineDockCollapsed,
} from '../components/pipeline/PipelineAiDock';
import { usePipelineBuilderAgent } from '../hooks/usePipelineBuilderAgent';
import { BaseSourceNode } from '../components/pipeline/BaseSourceNode';
import { BaseTransformNode } from '../components/pipeline/BaseTransformNode';
import { BaseSinkNode } from '../components/pipeline/BaseSinkNode';
import {
  createPipeline,
  getPipeline,
  savePipelineVersion,
  deployPipeline,
  buildPipeline,
  listPipelineBuilds,
  getPipelineVersion,
  listDatasets,
  validateRawGraph,
} from '../api/client';
import type {
  IRNode,
  IREdge,
  PipelineIR,
  PipelineNodeData,
  PipelineCreate,
  PipelineResponse,
  NodeType,
  OperatorType,
} from '../types/pipeline';

// ── React Flow NodeTypes 注册（key = operator_type，与后端 registry 一致）──

const nodeTypes: NodeTypes = {
  Source: BaseSourceNode,
  Filter: BaseTransformNode,
  Select: BaseTransformNode,
  Rename: BaseTransformNode,
  TypeCast: BaseTransformNode,
  Join: BaseTransformNode,
  Aggregate: BaseTransformNode,
  Union: BaseTransformNode,
  Expression: BaseTransformNode,
  Deduplicate: BaseTransformNode,
  Sort: BaseTransformNode,
  Sink: BaseSinkNode,
  QualityCheck: BaseTransformNode,
};

// ── React Flow EdgeTypes 注册（自定义可删除边）──
const edgeTypes = {
  deletable: DeletableEdge,
};

function PipelineBuilderInner() {
  const { apiName } = useParams<{ apiName?: string }>();
  const navigate = useNavigate();
  const reactFlowInstance = useReactFlow();

  // ── Store ──
  const store = usePipelineBuilder();
  const {
    pipeline,
    irNodes,
    irEdges,
    selectedNodeId,
    selectedNodeIds,
    nodePanelCollapsed,
    validationValid,
    builds,
    datasets,
    isDirty,
    addNode,
    updateNode,
    onNodesChange: storeNodesChange,
    onEdgesChange: storeEdgesChange,
    onConnect: storeConnect,
    setSelectedNode,
    setSelectedNodeIds,
    duplicateNodes,
    updateNodePositions,
    removeNode,
    toggleNodePanel,
    setDatasets,
    setBuilds,
    serializeIR,
    deserializeIR,
    loadPipeline,
    markClean,
    reset,
    undo,
    redo,
    pastSnapshots,
    futureSnapshots,
    validationErrors,
    setValidation,
    nodeSchemas,
  } = store;

  const [showMiniMap, setShowMiniMap] = useState(false);
  const [jsonText, setJsonText] = useState('');
  const [saving, setSaving] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [building, setBuilding] = useState(false);
  const [mode, setMode] = useState<'landing' | 'editing'>('landing');
  const [aiDockCollapsed, setAiDockCollapsed] = useState<boolean>(loadPipelineDockCollapsed);
  const [aiInitialQuestion, setAiInitialQuestion] = useState<string | undefined>(undefined);
  // 节点配置弹窗：当前编辑的节点 id（双击打开，关闭置 null）
  const [configNodeId, setConfigNodeId] = useState<string | null>(null);
  // 辅助视图弹窗（Schema/执行/JSON）：是否打开 + 当前 tab
  const [auxOpen, setAuxOpen] = useState(false);
  const [auxTab, setAuxTab] = useState<AuxTab>('schema');

  // ── 将 IR 节点转为 React Flow 节点 ──
  // configSummary 由 nodeConfigSummary 纯函数推导（画布直接展示核心配置）；
  // validationStatus 从 validationErrors 回填（修原 'unknown' 写死的 bug）。
  const rfNodes: Node<PipelineNodeData>[] = useMemo(() => {
    return irNodes.map((n) => {
      const def = getNodeDef(n.operator_type);
      const nodeErrors = validationErrors.filter(
        (e) => e.node_id === n.id && !e.valid,
      );
      const hasError = nodeErrors.some((e) => e.level === 'ERROR');
      const hasWarning = nodeErrors.some((e) => e.level === 'WARNING');
      const validationStatus: PipelineNodeData['validationStatus'] =
        nodeErrors.length === 0
          ? 'valid'
          : hasError
            ? 'error'
            : hasWarning
              ? 'warning'
              : 'valid';
      return {
        id: n.id,
        type: n.operator_type,
        position: n.position,
        // v12: 传递 measured 让 EdgeRenderer 能渲染边（首次 mount 后由 dimensions change 填充）
        measured: n.measured,
        data: {
          irNodeId: n.id,
          label: n.label,
          nodeType: n.type,
          operatorType: n.operator_type as PipelineNodeData['operatorType'],
          validationStatus,
          validationMessages: nodeErrors.map((e) => e.message),
          outputSchemaSummary: n.output_schema
            ? `${n.output_schema.fields.length} 字段`
            : '',
          configSummary: getConfigSummary(n, datasets),
          isRunning: false,
          configurable: def?.configurable ?? true,
        },
        selected: n.id === selectedNodeId || selectedNodeIds.includes(n.id),
      };
    });
  }, [irNodes, selectedNodeId, selectedNodeIds, validationErrors, datasets]);

  // ── 将 IR 边转为 React Flow 边 ──
  const rfEdges: Edge[] = useMemo(() => {
    return irEdges.map((e) => ({
      id: e.id,
      source: e.source_id,
      target: e.target_id,
      sourceHandle: e.source_port,
      targetHandle: e.target_port,
      type: 'deletable',
      selectable: true,
      selected: e.selected,
    }));
  }, [irEdges]);

  // ── 选中节点（用于辅助弹窗 Schema 预览高亮） ──
  const selectedNode = useMemo(
    () => irNodes.find((n) => n.id === selectedNodeId) ?? null,
    [irNodes, selectedNodeId],
  );

  // ── 配置弹窗编辑的节点（双击打开） ──
  const configNode = useMemo(
    () => irNodes.find((n) => n.id === configNodeId) ?? null,
    [irNodes, configNodeId],
  );

  // ── ELK 自动布局（design §14.7，2026-07 从 dagre 升级）──
  const handleLayout = useCallback(
    (updates: Array<{ id: string; position: { x: number; y: number } }>) => {
      // 批量更新位置（避免逐个 dispatch）
      for (const u of updates) {
        updateNode(u.id, { position: u.position });
      }
    },
    [updateNode],
  );

  // 布局完成后下一帧 fitView（避开 RF v12 setNodes+fitView 同步 bug xyflow#3946）
  const handleLayoutDone = useCallback(() => {
    requestAnimationFrame(() => {
      reactFlowInstance.fitView({ maxZoom: 1, padding: 0.2 });
    });
  }, [reactFlowInstance]);

  const { runLayout, markManuallyPositioned, resetManuallyPositioned } = useElkLayout(
    irNodes,
    irEdges,
    handleLayout,
    {
      enabled: mode === 'editing' && irNodes.length > 0 && irNodes.length <= 50,
      direction: 'RIGHT',
      onLayoutDone: handleLayoutDone,
    },
  );

  // ── 手动「整理布局」（工具栏按钮 / 快捷键 Cmd+O）──
  // 强制重排所有连边节点，清除手动定位标记，布局后 fitView
  const handleTidyUp = useCallback(() => {
    resetManuallyPositioned();
    void runLayout({ force: true });
  }, [resetManuallyPositioned, runLayout]);

  // ── 多选节点集合（从 store）──
  const multiSelectedNodes = useMemo(
    () => irNodes.filter((n) => selectedNodeIds.includes(n.id)),
    [irNodes, selectedNodeIds],
  );

  // ── 对齐 / 分布 ──
  const handleAlign = useCallback(
    (mode: AlignMode) => {
      const updates = alignNodes(multiSelectedNodes, mode);
      if (updates.length > 0) {
        updateNodePositions(updates);
        updates.forEach((u) => markManuallyPositioned(u.id));
      }
    },
    [multiSelectedNodes, updateNodePositions, markManuallyPositioned],
  );

  const handleDistribute = useCallback(
    (mode: DistributeMode) => {
      const updates = distributeNodes(multiSelectedNodes, mode);
      if (updates.length > 0) {
        updateNodePositions(updates);
        updates.forEach((u) => markManuallyPositioned(u.id));
      }
    },
    [multiSelectedNodes, updateNodePositions, markManuallyPositioned],
  );

  // ── 多选复制 ──
  const handleDuplicate = useCallback(() => {
    if (selectedNodeIds.length > 0) {
      duplicateNodes(selectedNodeIds);
    }
  }, [selectedNodeIds, duplicateNodes]);

  // ── 多选删除 ──
  const handleDeleteSelected = useCallback(() => {
    selectedNodeIds.forEach((id) => removeNode(id));
    setSelectedNodeIds([]);
  }, [selectedNodeIds, removeNode, setSelectedNodeIds]);

  // ── 键盘快捷键（画布级）──
  // 1=fitView, 0=重置缩放, Cmd/Ctrl+O=整理布局
  // 注意：不在 input/textarea 聚焦时触发
  useEffect(() => {
    if (mode !== 'editing') return;
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;
      if (e.key === '1' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        reactFlowInstance.fitView({ maxZoom: 1, padding: 0.2 });
      } else if (e.key === '0' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        reactFlowInstance.setViewport({ x: 0, y: 0, zoom: 1 });
      } else if ((e.ctrlKey || e.metaKey) && (e.key === 'o' || e.key === 'O')) {
        e.preventDefault();
        handleTidyUp();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [mode, reactFlowInstance, handleTidyUp]);

  // ── 加载管道 ──
  useEffect(() => {
    if (!apiName) {
      setMode('landing');
      reset();
      return;
    }
    setSaving(true);
    void (async () => {
      try {
        const p = await getPipeline(apiName);
        const latestVersion = await getPipelineVersion(apiName, p.current_version_number ?? 1);
        loadPipeline(p, latestVersion.graph);
        setJsonText(JSON.stringify(latestVersion.graph, null, 2));
        setMode('editing');
      } catch (err) {
        console.error('加载管道失败', err);
      } finally {
        setSaving(false);
      }
    })();
  }, [apiName]);

  // ── 加载数据集列表 ──
  useEffect(() => {
    void listDatasets().then((dss) => {
      setDatasets(dss.map((ds) => ({ api_name: ds.api_name, display_name: ds.display_name })));
    });
  }, []);

  // ── 加载构建历史 ──
  useEffect(() => {
    if (!apiName) return;
    void listPipelineBuilds(apiName, 10).then((bs) => {
      setBuilds(bs);
    });
  }, [apiName]);

  // ── 实时 schema 推导（debounce 500ms）：irNodes/irEdges 变化后调 validate_raw_graph，
  //    回填 nodeSchemas 到各节点的 input_schemas/output_schema，供配置面板渲染列下拉。 ──
  // ── 用序列化字符串比较避免 immer 引用变化导致死循环 ──
  const irNodesSig = useMemo(() => JSON.stringify(irNodes), [irNodes]);
  const irEdgesSig = useMemo(() => JSON.stringify(irEdges), [irEdges]);

  useEffect(() => {
    if (mode !== 'editing' || irNodes.length === 0) return;
    const graph = serializeIR();
    const timer = setTimeout(() => {
      void validateRawGraph(graph)
        .then((res) => {
          setValidation(res.valid, res.contracts ?? [], res.node_schemas ?? {});
        })
        .catch((err) => {
          console.warn('schema 推导失败', err);
        });
    }, 500);
    return () => clearTimeout(timer);
    // serialized sigs to avoid immer reference churn loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [irNodesSig, irEdgesSig, mode]);

  // ── 同步 JSON（辅助弹窗打开到 JSON tab 时刷新文本） ──
  useEffect(() => {
    if (auxOpen && auxTab === 'json') {
      setJsonText(JSON.stringify(serializeIR(), null, 2));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auxOpen, auxTab, irNodes, irEdges]);

  // ── 处理节点选择 ──
  const handleNodesChange = useCallback(
    (changes: Parameters<typeof storeNodesChange>[0]) => {
      // 用户拖拽节点后标记为手动定位，后续 autoLayout 不覆盖其位置
      for (const c of changes) {
        if (c.type === 'position' && c.dragging === false) {
          markManuallyPositioned(c.id);
        }
      }
      storeNodesChange(changes);
    },
    [storeNodesChange, markManuallyPositioned],
  );

  const handleEdgesChange = useCallback(
    (changes: Parameters<typeof storeEdgesChange>[0]) => {
      storeEdgesChange(changes);
    },
    [storeEdgesChange],
  );

  const handleConnect = useCallback(
    (connection: Parameters<typeof storeConnect>[0]) => {
      storeConnect(connection);
    },
    [storeConnect],
  );

  // ── 拖拽创建节点 ──
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const panelKey = event.dataTransfer.getData('application/reactflow');
      if (!panelKey || !reactFlowInstance || !reactFlowWrapper.current) return;

      const def = getNodeDefByPanelKey(panelKey);
      if (!def) return;

      // screenToFlowPosition 接收视口坐标（clientX/clientY），
      // 不要减去 wrapper 的 bounds —— 那样会把坐标转成相对 wrapper 的局部坐标，
      // 导致落点偏移（偏移量 = wrapper 相对视口的 left/top）。
      const position = reactFlowInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      addNode(def.defaultNodeType, def.type, def.displayName, position);
      // Apply default config (e.g. QualityCheck preset rule_type)
      if (def.defaultConfig) {
        // addNode returns the new node id; update its config immediately
        // (addNode already created the node, we patch config after)
        // Using a microtask to ensure the node exists in store
        queueMicrotask(() => {
          const lastNode = usePipelineBuilder.getState().irNodes[
            usePipelineBuilder.getState().irNodes.length - 1
          ];
          if (lastNode) {
            updateNode(lastNode.id, { config: { ...lastNode.config, ...def.defaultConfig } as typeof lastNode.config });
          }
        });
      }
    },
    [reactFlowInstance, addNode, updateNode],
  );

  const onDragStart = useCallback((event: React.DragEvent, nodeType: string) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  }, []);

  // ── 保存管道 ──
  const handleSave = useCallback(async () => {
    const ir = serializeIR();
    setSaving(true);
    manualSavingRef.current = true; // 同步锁，立即阻止 auto-save 的 doSave
    try {
      if (pipeline) {
        const version = await savePipelineVersion(pipeline.api_name, ir, '保存');
        setJsonText(JSON.stringify(ir, null, 2));
        // 更新 lastSavedIRRef 让 auto-save 后续不会重复保存同一份 IR
        // （通过 setState 触发重渲染即可，React 19 的 PR#25700 已修复 uSES batching）
        usePipelineBuilder.setState({ isDirty: false });
        setSaving(false);
        return version;
      } else {
        // 新管道：从 Sink 节点的 config.extra.dataset 取输出数据集名
        const sinkNode = ir.nodes.find((n) => n.type === 'Sink');
        const sinkDs =
          (sinkNode?.config.extra?.dataset as string | undefined) ??
          datasets[0]?.api_name ??
          'default_dataset';
        const createData: PipelineCreate = {
          api_name: `pipeline_${Date.now().toString(36)}`,
          display_name: '新管道',
          sink_dataset_api_name: sinkDs,
          graph: ir,
        };
        const p = await createPipeline(createData);
        loadPipeline(p, ir);
        navigate(`/pipelines/${p.api_name}`, { replace: true });
        markClean();
      }
    } catch (err) {
      console.error('保存失败', err);
    } finally {
      manualSavingRef.current = false;
      setSaving(false);
    }
  }, [pipeline, serializeIR, navigate, markClean, loadPipeline, datasets]);

  // ── 部署 ──
  const handleDeploy = useCallback(async () => {
    if (!pipeline) return;
    setDeploying(true);
    try {
      const ir = serializeIR();
      const version = await savePipelineVersion(pipeline.api_name, ir, '部署前保存');
      await deployPipeline(pipeline.api_name, { version_id: version.id });
    } catch (err) {
      console.error('部署失败', err);
    } finally {
      setDeploying(false);
    }
  }, [pipeline, serializeIR]);

  // ── 执行构建 ──
  const handleBuild = useCallback(async () => {
    if (!pipeline) return;
    setBuilding(true);
    try {
      await buildPipeline(pipeline.api_name, { force_build: true });
      const bs = await listPipelineBuilds(pipeline.api_name, 10);
      setBuilds(bs);
    } catch (err) {
      console.error('构建失败', err);
    } finally {
      setBuilding(false);
    }
  }, [pipeline]);

  // ── JSON 编辑 ──
  const handleApplyJson = useCallback(() => {
    try {
      const ir = JSON.parse(jsonText) as PipelineIR;
      deserializeIR(ir);
    } catch {
      // 不合法时忽略
    }
  }, [jsonText, deserializeIR]);

  // ── Undo/Redo ──
  const canUndo = pastSnapshots.length > 0;
  const canRedo = futureSnapshots.length > 0;

  // ── 节点配置变更回调 ──
  const handleNodeChange = useCallback(
    (nodeId: string, updates: Partial<IRNode>) => {
      updateNode(nodeId, updates);
    },
    [updateNode],
  );

  const handleCloseConfig = useCallback(() => {
    setConfigNodeId(null);
  }, []);

  // ── isValidConnection：连线时校验端口兼容性（design §14.11） ──
  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      const source = connection.source;
      const target = connection.target;
      if (!source || !target) return false;
      if (source === target) return false; // 自环

      const sourceNode = irNodes.find((n) => n.id === source);
      const targetNode = irNodes.find((n) => n.id === target);
      if (!sourceNode || !targetNode) return false;

      const sourceDef = getNodeDef(sourceNode.operator_type);
      const targetDef = getNodeDef(targetNode.operator_type);

      // Source 只能连 Transform/Sink/Quality（不连 Source）
      if (sourceDef?.category === 'source' && targetDef?.category === 'source') return false;
      // Sink 只能被连（不连出去）
      if (sourceDef?.category === 'sink') return false;
      // Quality 只能连 Transform/Sink
      if (sourceDef?.category === 'quality' && targetDef?.category === 'source') return false;

      // 检查目标输入端口是否已被占用（端口级单输入约束）
      const targetPort = (connection.targetHandle as string) ?? 'default';
      const portOccupied = irEdges.some(
        (e) => e.target_id === target && e.target_port === targetPort,
      );
      if (portOccupied) return false;

      // 检查是否已经存在相同的连线（端口级去重）
      const sourcePort = (connection.sourceHandle as string) ?? 'default';
      const exists = irEdges.some(
        (e) =>
          e.source_id === source &&
          e.target_id === target &&
          e.source_port === sourcePort &&
          e.target_port === targetPort,
      );
      if (exists) return false;

      // 检查是否会形成环（BFS 检测）
      const visited = new Set<string>();
      const queue = [target];
      while (queue.length > 0) {
        const current = queue.shift()!;
        if (current === source) return false; // 环
        if (visited.has(current)) continue;
        visited.add(current);
        const outgoing = irEdges.filter((e) => e.source_id === current);
        for (const edge of outgoing) {
          if (!visited.has(edge.target_id)) queue.push(edge.target_id);
        }
      }

      return true;
    },
    [irNodes, irEdges],
  );

  // ── 快捷键处理（design §14.11） ──
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      // 输入框/文本区中不拦截快捷键（避免与正常输入冲突）
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName ?? '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) {
        return;
      }
      // Ctrl+S / Cmd+S 保存
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        void handleSave();
        return;
      }
      // Ctrl+Z 撤销
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
        return;
      }
      // Ctrl+Shift+Z / Ctrl+Y 重做
      if (((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'z') ||
          ((e.ctrlKey || e.metaKey) && e.key === 'y')) {
        e.preventDefault();
        redo();
        return;
      }
    },
    [handleSave, undo, redo],
  );

  useEffect(() => {
    if (mode !== 'editing') return;
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [mode, handleKeyDown]);

  // ── Landing 回调 ──
  const handleStartWithPrompt = useCallback(
    (prompt: string) => {
      // 切到 editing 模式 + 创建空管道 + 把 prompt 作为 Agent 初始消息自动发送。
      const newPipelineName = `pipeline_${Date.now().toString(36)}`;
      reset();
      setMode('editing');
      setAiInitialQuestion(`${prompt}#${Date.now()}`);  // timestamp 强制重发
      setAiDockCollapsed(false);
      navigate(`/pipelines/${newPipelineName}`, { replace: true });
    },
    [reset, navigate],
  );

  const handleStartBlank = useCallback(() => {
    reset();
    setMode('editing');
  }, [reset]);

  // ── 自动保存（design §14.3 F-10：debounce 2s + sendBeacon 兜底） ──
  // manualSavingRef：手动保存进行中的同步锁。用 ref 而非 state——
  // state 更新要等 React 渲染才生效，无法阻止 auto-save 已到期的 timer。
  // 详见 docs/bugfix/pipeline-builder-autosave-loop-and-config-panel-dismiss.md §4
  // 根因：React 18+ useSyncExternalStore 提前 flush（react#25191），
  // 外部 store 更新和 useState 不能同批提交，导致 isDirty 回弹。
  const manualSavingRef = useRef(false);
  const handlePipelineSaved = useCallback(
    (_p: PipelineResponse) => {
      // auto-save 只负责标记 clean，不更新 pipeline 对象。
      // pipeline 更新会触发 React Flow 重渲染 → onNodesChange → isDirty=true 回弹。
      usePipelineBuilder.setState({ isDirty: false });
    },
    [],
  );
  useAutoSave({
    pipeline,
    serializeIR,
    isDirty,
    onPipelineSaved: handlePipelineSaved,
    enabled: mode === 'editing',
    manualSavingRef,
  });

  // ── Landing 模式（所有 hooks 必须在此之前调用，避免违反 Rules of Hooks）──
  // 实际渲染在下方（usePipelineBuilderAgent 之后）

  // ── AG-UI Agent（design §14.5 AI FDE：复用图探索 ADR-015 范式） ──
  // 当 AI 发送 STATE_SNAPSHOT 时，把画布快照同步到 Zustand store
  const handleAgentCanvasState = useCallback(
    (canvas: Parameters<typeof usePipelineBuilderAgent>[0]['onCanvasState'] extends (c: infer C) => void ? C : never) => {
      // Agent 驱动画布：批量创建/更新节点。
      // 使用单个快照重建画布，避免逐个 addNode 产生多个 undo 快照
      // （AI 一次生成 N 节点应为单个 undo 步骤）。
      const newNodes: IRNode[] = canvas.nodes.map((n) => {
        const existing = irNodes.find((en) => en.id === n.id);
        return existing
          ? {
              ...existing,
              config: { ...existing.config, ...n.config } as typeof existing.config,
              position: n.position,
            }
          : {
              id: n.id,
              type: n.type as NodeType,
              operator_type: n.operator_type as OperatorType,
              label: n.label,
              description: '',
              input_schemas: [],
              output_schema: null,
              config: n.config as Record<string, unknown> as import('../types/pipeline').NodeConfig,
              position: n.position,
            };
      });
      const newEdges: IREdge[] = canvas.edges.map((e) => ({
        id: e.id,
        source_id: e.source_id,
        target_id: e.target_id,
        source_port: 'default',
        target_port: 'default',
      }));
      // 直接替换整个画布（不逐个 addNode，避免 undo 膨胀）
      // loadPipeline 会重置历史栈，这里用一个轻量 replace
      usePipelineBuilder.setState({
        irNodes: newNodes,
        irEdges: newEdges,
        selectedNodeId: canvas.selected_node_id ?? null,
        isDirty: true,
      });
    },
    [irNodes],
  );
  const { agent: pipelineAgent } = usePipelineBuilderAgent({
    ontology: pipeline?.api_name ?? '',
    onCanvasState: handleAgentCanvasState,
  });

  // ── Landing 模式（所有 hooks 已在上文调用，此处安全提前返回）──
  if (mode === 'landing' && !apiName) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
          <span className="text-sm font-semibold text-slate-700">🔧 管道编排</span>
          <span className="text-xs text-slate-400">Pipeline Builder</span>
        </div>
        <PipelineBuilderLanding
          datasets={datasets}
          onStartWithPrompt={handleStartWithPrompt}
          onStartBlank={handleStartBlank}
        />
      </div>
    );
  }

  // ── Editing 模式 ──
  return (
    <div className="flex h-full flex-col bg-slate-50">
      {/* 工具栏 */}
      <PipelineToolbar
        pipelineName={pipeline?.display_name ?? '新管道'}
        pipelineStatus={pipeline?.status ?? 'DRAFT'}
        isDirty={isDirty}
        validationValid={validationValid}
        loading={saving || deploying || building}
        onSave={handleSave}
        onDeploy={handleDeploy}
        onBuild={handleBuild}
        onUndo={undo}
        onRedo={redo}
        canUndo={canUndo}
        canRedo={canRedo}
        onToggleAi={() => setAiDockCollapsed((v) => !v)}
        showAiChat={!aiDockCollapsed}
        onOpenAux={(tab) => {
          setAuxTab(tab);
          setAuxOpen(true);
        }}
      />
      {/* 主区域：左侧算子面板 + 画布 + 右侧 AI 助手 Dock */}
      <div className="flex flex-1 overflow-hidden">
        {/* 左侧：算子面板 */}
        <NodePanel
          onDragStart={onDragStart}
          collapsed={nodePanelCollapsed}
          onToggleCollapse={toggleNodePanel}
        />
        {/* 画布（占满剩余空间） */}
        <div className="flex-1" ref={reactFlowWrapper} style={{ minHeight: 0 }}>
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onConnect={handleConnect}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodeClick={(_e, node) => setSelectedNode(node.id)}
            onNodeDoubleClick={(_e, node) => {
              const def = getNodeDef(node.type ?? '');
              if (def?.configurable ?? true) setConfigNodeId(node.id);
            }}
            onPaneClick={() => setSelectedNode(null)}
            isValidConnection={isValidConnection}
            connectionRadius={28}
            connectionLineType={ConnectionLineType.Bezier}
            deleteKeyCode="Delete"
            multiSelectionKeyCode="Shift"
            selectionMode={SelectionMode.Partial}
            // 框选：允许拖拽框选多选节点（n8n/Dify 标配）
            selectionOnDrag
            // 按住 Space 进入手型平移模式（Figma/n8n 标配），避免与默认 pointer-drag 平移冲突
            panActivationKeyCode="Space"
            // 双击用于打开节点配置弹窗，禁用画布缩放
            zoomOnDoubleClick={false}
            fitView
            fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
            minZoom={0.2}
            maxZoom={3}
            snapToGrid
            snapGrid={[20, 20]}
            defaultEdgeOptions={{
              type: 'deletable',
              selectable: true,
            }}
          >
            {/* 多选工具条：选中 ≥2 个节点时在画布顶部中央浮现（对标 Dify 多选右键菜单） */}
            {multiSelectedNodes.length >= 2 && (
              <Panel position="top-center" className="mt-2">
                <SelectionToolbar
                  selectedNodes={multiSelectedNodes}
                  onAlign={handleAlign}
                  onDistribute={handleDistribute}
                  onDuplicate={handleDuplicate}
                  onDelete={handleDeleteSelected}
                />
              </Panel>
            )}
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#e2e8f0" />
            <Controls
              showInteractive={false}
              orientation="horizontal"
              className="rounded border border-slate-200 bg-white shadow-sm"
            >
              {/* 整理布局（强制 ELK 重排，对标 n8n Tidy up） */}
              <ControlButton
                onClick={handleTidyUp}
                title="整理布局（Cmd/Ctrl+O）"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect width="6" height="14" x="4" y="5" rx="2" />
                  <rect width="6" height="10" x="14" y="7" rx="2" />
                  <path d="M17 22v-5" />
                  <path d="M17 2v5" />
                  <path d="M7 22v-3" />
                  <path d="M7 2v3" />
                </svg>
              </ControlButton>
              {/* 小地图开关 */}
              <ControlButton
                onClick={() => setShowMiniMap((v) => !v)}
                title="小地图"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
                  <rect x="1.5" y="1.5" width="5" height="5" rx="0.5" />
                  <rect x="7.5" y="1.5" width="5" height="5" rx="0.5" opacity={showMiniMap ? 1 : 0.4} />
                  <rect x="1.5" y="7.5" width="5" height="5" rx="0.5" opacity={showMiniMap ? 1 : 0.4} />
                  <rect x="7.5" y="7.5" width="5" height="5" rx="0.5" opacity={showMiniMap ? 1 : 0.4} />
                </svg>
              </ControlButton>
            </Controls>
            {showMiniMap && (
              <MiniMap
                nodeColor={(node) => {
                  const def = getNodeDef(node.type ?? '');
                  return NODE_COLORS[def?.category ?? 'transform'];
                }}
                maskColor="rgba(0,0,0,0.1)"
                className="rounded border border-slate-200 shadow-sm"
                style={{ width: 180, height: 120 }}
              />
            )}
          </ReactFlow>
        </div>

      {/* 右侧 AI 助手 Dock（参考本体 OntologyWorkspace AiAssistantDock：可折叠/拖拽调宽/持久化） */}
      <PipelineAiDock
        agent={pipelineAgent}
        ontology={pipeline?.api_name ?? ''}
        systemPrompt={'你是管道构建助手。用户会用自然语言描述数据管道，你需要调用工具在画布上构建。可用工具：list_datasets、add_source、add_transform、add_sink、modify_node、remove_node、connect。先了解可用数据再逐步构建。'}
        autoSend={aiInitialQuestion}
        collapsed={aiDockCollapsed}
        onCollapsedChange={(c) => {
          setAiDockCollapsed(c);
          persistPipelineDockCollapsed(c);
        }}
      />
      </div>

      {/* 节点配置弹窗（双击节点打开） */}
      <NodeConfigModal
        node={configNode}
        datasets={datasets}
        nodeSchemas={nodeSchemas}
        irEdges={irEdges}
        onChange={handleNodeChange}
        onClose={handleCloseConfig}
      />

      {/* 辅助视图弹窗（工具栏 Schema/执行/JSON 按钮打开） */}
      <PipelineAuxModal
        open={auxOpen}
        activeTab={auxTab}
        onTabChange={setAuxTab}
        onClose={() => setAuxOpen(false)}
        selectedNode={selectedNode}
        irNodes={irNodes}
        irEdges={irEdges}
        builds={builds}
        validationErrors={validationErrors}
        validationValid={validationValid}
        pipelineStatus={pipeline?.status ?? 'DRAFT'}
        jsonString={jsonText}
        onJsonChange={setJsonText}
        onApplyJson={handleApplyJson}
      />
    </div>
  );
}

// ── 导出带 Provider 的包装组件 ──

export function PipelineBuilderPage() {
  return (
    <ReactFlowProvider>
      <PipelineBuilderInner />
    </ReactFlowProvider>
  );
}
