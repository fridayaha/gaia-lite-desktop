/**
 * GraphExplorePage — 图探索主页面（graph-reasoning-frontend-design.md §3）。
 *
 * 布局：顶栏（本体选择 + 搜索 + 撤销/清空）+ 画布 + 右侧栏（选中详情）。
 * 起手：选 ObjectType → queryDataFrame 起始集 → 画布渲染 → 右键 Search Around。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useOutletContext, useSearchParams } from 'react-router-dom';
import { useGraphExplore } from '../hooks/useGraphExplore';
import { GraphCanvas } from '../components/GraphCanvas';
import { EvidenceDrawer } from '../components/EvidenceDrawer';
import { MapPanel } from '../components/MapPanel';
import { TrajectoryPlayer } from '../components/TrajectoryPlayer';
import { LayersPanel } from '../components/LayersPanel';
import { HistogramPanel } from '../components/HistogramPanel';
import { PathFinder } from '../components/PathFinder';
import { SearchAroundConfigPanel } from '../components/SearchAroundConfigPanel';
import { useSearchAroundConfig } from '../hooks/useSearchAroundConfig';
import { ExploreLanding } from '../components/ExploreLanding';
import { OntologyContextSelector } from '../components/OntologyContextSelector';
import { AssistantUiChat } from '../components/AssistantUiChat';
import { useGraphExploreAgent } from '../hooks/useGraphExploreAgent';
import { TimeScrubber } from '../components/TimeScrubber';
import { useTimeFilter } from '../hooks/useTimeFilter';
import type { GraphFilter } from '../types';
import type { CanvasSnapshot } from '../types/canvas';
import { listOntologies, listObjectTypeSummaries, listObjectTypes, listLinkTypes } from '../api/client';
import { useActionTrigger } from '../hooks/useActionTrigger';
import { ExecuteActionDialog } from '../components/ExecuteActionDialog';
import type { LayoutOutletContext } from '../components/Layout';
import type { LinkTypeDef, ObjectSetIR, Ontology, ObjectType, ObjectTypeSummary } from '../types';

export function GraphExplorePage() {
  const { ontology: routeOntology } = useParams<{ ontology?: string }>();
  const [searchParams] = useSearchParams();
  const { setFullBleed } = useOutletContext<LayoutOutletContext>();
  const [ontology, setOntology] = useState(routeOntology ?? '');
  const [ontologies, setOntologies] = useState<Ontology[]>([]);
  const [objectTypes, setObjectTypes] = useState<ObjectTypeSummary[]>([]);
  /** landing 示例推导用的完整 OT（含 properties.data_type），仅 landing 模式加载。 */
  const [objectTypesFull, setObjectTypesFull] = useState<ObjectType[]>([]);
  const [linkTypes, setLinkTypes] = useState<LinkTypeDef[]>([]);
  const [selectedOt, setSelectedOt] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [view, setView] = useState<'graph' | 'map' | 'split'>('graph');
  const [showTrajectory, setShowTrajectory] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<'selection' | 'layers' | 'histogram' | 'explore'>('selection');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [baseIR, setBaseIR] = useState<ObjectSetIR | null>(null);
  const [layout, setLayout] = useState<'fcose' | 'dagre' | 'circle' | 'grid'>('fcose');
  const [mode, setMode] = useState<'landing' | 'exploring'>('landing');
  /** landing 对话框提交后传入 exploring 模式作为 AG-UI Thread 的首问。 */
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [showConversation, setShowConversation] = useState(true);

  // 当前选中对象类型的完整对象（供 useActionTrigger 取 id）
  const selectedOtObj = objectTypes.find((ot) => ot.api_name === selectedOt) ?? null;
  const actionTrigger = useActionTrigger(ontology, selectedOtObj);
  // baseIR ref（供画布过滤重载读取最新值）
  const baseIRRef = useRef<ObjectSetIR | null>(null);
  baseIRRef.current = baseIR;
  const searchAroundConfig = useSearchAroundConfig();
  const timeFilter = useTimeFilter();
  const explore = useGraphExplore(ontology);

  // ADR-015：AG-UI Agent 驱动画布。state.canvas 变化 → 调 useGraphExplore API。
  // 探索查询（searchAround）：applyCanvasSnapshot 增量合并节点+边，多步串联
  // 形成可视化轨迹（如 S000→物料→订单）。纯查询：覆盖刷新画布。
  //
  // 死循环防护（三层）：
  // 1. useGraphExploreAgent 的 SSE tap 指纹去重（防止 runtime re-render 重放
  //    事件流导致同一 snapshot 反复处理）
  // 2. applyingRef 防并发（applyCanvasSnapshot 异步水合期间不重入）
  // 3. lastAppliedFingerprint 兜底去重（相同 objects+edges 不重复应用）
  const applyingRef = useRef(false);
  const lastAppliedFingerprint = useRef('');
  const onCanvasState = useCallback(
    (canvas: CanvasSnapshot, exp: typeof explore) => {
      // 视图切换 + 着色优先执行（轻量、幂等），不受数据去重拦截——
      // switch_view / color_by 产生的 snapshot 与上一步数据完全相同（rids/edges 不变），
      // 若放在数据去重 return 之后会被直接吞掉，导致视图永远不切、颜色不更新。
      if (canvas.view && canvas.view !== view) setView(canvas.view);
      if (canvas.color_by !== null) {
        exp.setLayerStyle({ ...exp.layerStyle, colorBy: 'property', colorProp: canvas.color_by });
      }

      if (canvas.object_count === 0 && (canvas.edges?.length ?? 0) === 0) return;
      // 数据应用去重（仅针对水合全量属性的重活）
      const rids = canvas.objects.map((o) => o.rid).sort().join(',');
      const fingerprint = `${rids}|${canvas.edges?.length ?? 0}`;
      if (fingerprint === lastAppliedFingerprint.current) return;
      if (applyingRef.current) return;
      lastAppliedFingerprint.current = fingerprint;
      applyingRef.current = true;
      void exp.applyCanvasSnapshot(canvas).finally(() => {
        applyingRef.current = false;
      });
    },
    [view],
  );
  const { agent: graphAgent } = useGraphExploreAgent({ ontology, explore, onCanvasState });

  // 从画布节点 props 提取时序时间戳（ms），供 TimeScrubber 推算范围。
  // createdAt 是数字字符串（如 '1751328000000'），Date.parse 不解析纯数字字符串，
  // 需先尝试 Number 转 timestamp。
  const nodeTimestamps = useMemo(() => {
    const m = new Map<string, number[]>();
    for (const [rid, node] of explore.nodes) {
      const ts = node.props?.timestamp ?? node.props?.created_at ?? node.props?.createdAt ?? node.props?.time;
      if (ts === undefined || ts === null) continue;
      let ms = NaN;
      if (typeof ts === 'number') {
        ms = ts;
      } else if (typeof ts === 'string') {
        // 先按纯数字解析（毫秒时间戳），再退回 Date.parse（ISO 字符串）
        const num = Number(ts);
        ms = !Number.isNaN(num) && ts.trim() !== '' ? num : Date.parse(ts);
      }
      if (!Number.isNaN(ms)) m.set(rid, [ms]);
    }
    return m;
  }, [explore.nodes]);

  // 时间轴过滤：窗外且有时序的节点 → dimmed（design-v2 §1.3 画布联动）。
  const dimmedVids = useMemo(() => {
    if (!timeFilter.range || nodeTimestamps.size === 0) return null;
    const s = new Set<string>();
    for (const [rid, ts] of nodeTimestamps) {
      // 节点有时序但不在窗内 → dim
      const inWindow = ts.some((t) => t >= timeFilter.range!.start && t <= timeFilter.range!.end);
      if (!inWindow) s.add(rid);
    }
    return s;
  }, [timeFilter.range, nodeTimestamps]);

  // fullBleed：画布撑满 main-content（无 padding）。
  useEffect(() => {
    setFullBleed(true);
    return () => setFullBleed(false);
  }, [setFullBleed]);

  // URL 预填充（design-v3 §3.4，对齐 Vertex）：objects/view/question。
  // 从其他页面带上下文进入图探索，如对象详情页“在图中查看”按钮。
  const prefilledObjects = searchParams.get('objects');
  const prefilledView = searchParams.get('view');
  const prefilledQuestion = searchParams.get('question');
  useEffect(() => {
    // 预填视图
    if (prefilledView === 'map' || prefilledView === 'split' || prefilledView === 'graph') {
      setView(prefilledView);
      setMode('exploring');
    }
    // 预填对象集（直接加载）
    if (prefilledObjects && ontology) {
      const rids = prefilledObjects.split(',').map((v) => v.trim()).filter(Boolean);
      if (rids.length > 0) {
        const ir = { type: 'static', objects: rids } as ObjectSetIR;
        void explore.loadStartSet(ir).then(() => {
          setBaseIR(ir);
          setMode('exploring');
        });
      }
    }
    // 预填问题（自动执行）—— ADR-015：走 AG-UI Agent，传给 exploring 模式的 Thread
    if (prefilledQuestion && ontology) {
      setPendingQuestion(prefilledQuestion);
      setMode('exploring');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ontology, prefilledObjects, prefilledView, prefilledQuestion]);

  // 切本体时清画布（AG-UI runtime 随 ontology 重建，自动开新会话）
  useEffect(() => {
    explore.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ontology]);

  // 加载本体列表
  useEffect(() => {
    void listOntologies().then((onts) => {
      setOntologies(onts);
      if (!ontology && onts.length > 0) setOntology(onts[0].api_name);
    });
  }, []);

  // 加载本体元数据（ObjectTypes + LinkTypes）。切本体时先清空，避免中间态示例错乱。
  useEffect(() => {
    if (!ontology) return;
    setObjectTypes([]);
    setObjectTypesFull([]);
    setLinkTypes([]);
    void listObjectTypeSummaries(ontology).then((ots) => setObjectTypes(ots));
    // landing 示例推导需要属性 data_type，并行拉完整版（仅 landing 用）
    void listObjectTypes(ontology).then((ots) => setObjectTypesFull(ots));
    void listLinkTypes(ontology).then((lts) => setLinkTypes(lts));
  }, [ontology]);

  // 起手搜索：加载某 ObjectType 的对象集
  const handleStart = async () => {
    if (!selectedOt) return;
    const filters = statusFilter
      ? [{ field: 'status', op: 'exactMatch' as const, value: statusFilter }]
      : undefined;
    const ir: ObjectSetIR = {
      type: 'objectType',
      object_type: selectedOt,
      filters,
    };
    await explore.loadStartSet(ir);
    setBaseIR(ir);
  };

  // HistogramPanel 应用筛选后重载（在 baseIR 上叠加 filters）
  const handleApplyFilter = async (filters: GraphFilter[]) => {
    if (!baseIR) return;
    await explore.loadStartSet({ ...baseIR, filters: filters.length > 0 ? filters : undefined });
  };

  // ADR-015：所有 NL 查询走 AG-UI Agent（ReAct 循环）。landing 对话框 / 示例点击 /
  // URL 预填问题都调此函数——切到 exploring 模式，问题传给 AG-UI Thread。
  // Agent 自行决定调用 query_with_dataframe / traverse_link / switch_view / color_by，
  // 通过 STATE_SNAPSHOT 驱动画布；每轮读 state 决策（0 对象自然终止，不编结论）。
  const handleAsk = useCallback((question: string) => {
    if (!question.trim() || !ontology) return;
    setPendingQuestion(question);
    setMode('exploring');
  }, [ontology]);

  // Search Around 回调
  const handleSearchAround = async (rid: string, linkType: string) => {
    await explore.searchAround(rid, linkType, 'forward');
  };

  const selectedNode = explore.selectedVid
    ? explore.nodes.get(explore.selectedVid)
    : null;

  if (mode === 'landing') {
    return (
      <div className="flex h-full flex-col">
        {/* landing 模式：极简顶栏（本体切换）+ ExploreLanding */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2">
          <OntologyContextSelector
            ontologies={ontologies}
            value={ontology}
            onChange={setOntology}
          />
          <span className="text-xs text-slate-400">图探索 · 对话模式</span>
        </div>
        <ExploreLanding
          ontology={ontology}
          ontologyDisplayName={ontologies.find((o) => o.api_name === ontology)?.display_name}
          objectTypes={objectTypes}
          objectTypesFull={objectTypesFull}
          linkTypes={linkTypes}
          onAsk={handleAsk}
          onSelectObjectType={(apiName) => handleAsk(`查看所有${apiName}`)}
          loading={false}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶栏 */}
      <div className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
        <OntologyContextSelector
          ontologies={ontologies}
          value={ontology}
          onChange={setOntology}
        />
        <button
          type="button"
          onClick={() => {
            explore.clear();
            setPendingQuestion(null);
            setMode('landing');
          }}
          className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-600 hover:bg-slate-50"
          title="开始新对话"
        >
          💬 新对话
        </button>
        <button
          type="button"
          onClick={() => setShowConversation((v) => !v)}
          className={`rounded border px-2 py-1 text-sm ${showConversation ? 'border-blue-300 bg-blue-50 text-blue-700' : 'border-slate-300 text-slate-600 hover:bg-slate-50'}`}
          title={showConversation ? '隐藏对话流（高级模式）' : '显示对话流'}
        >
          {showConversation ? '💬' : '⚙'}
        </button>
        <select
          value={selectedOt}
          onChange={(e) => setSelectedOt(e.target.value)}
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          aria-label="选择对象类型"
        >
          <option value="">选择对象类型...</option>
          {objectTypes.map((ot) => (
            <option key={ot.id} value={ot.api_name}>
              {ot.api_name}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="status 过滤值（可选）"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="w-44 rounded border border-slate-300 px-2 py-1 text-sm"
        />
        <button
          type="button"
          onClick={handleStart}
          disabled={!selectedOt || explore.loading}
          className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {explore.loading ? '加载中...' : '加载对象'}
        </button>
        {/* NL 查询入口：ADR-015 走 AG-UI Agent，切回 landing 对话框输入 */}
        <button
          type="button"
          onClick={() => { setPendingQuestion(null); setMode('landing'); }}
          className="rounded border border-purple-300 bg-purple-50 px-2 py-1 text-sm text-purple-700 hover:bg-purple-100"
          title="自然语言提问（走 AI Agent，结果加载到画布）"
        >
          💬 提问
        </button>
        {/* 统计信息（从底栏移来，给时间轴腾位置）*/}
        <div className="flex items-center gap-3 text-xs text-slate-500">
          <span>节点 {explore.nodeCount}</span>
          <span>边 {explore.edgeCount}</span>
          {explore.lastEvidenceId && (
            <button
              onClick={() => setEvidenceId(explore.lastEvidenceId)}
              className="text-blue-600 hover:underline"
              title="查看证据链详情"
            >
              证据 {explore.lastEvidenceId.slice(0, 8)}
            </button>
          )}
          {explore.truncated && (
            <button
              onClick={() => void explore.loadMore()}
              disabled={explore.loading}
              className="rounded border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs text-amber-700 hover:bg-amber-100 disabled:opacity-50"
            >
              ⚠ 截断 · 加载更多
            </button>
          )}
          {explore.error && <span className="text-red-600">❌ {explore.error}</span>}
        </div>
        <div className="flex-1" />
        <button
          type="button"
          onClick={explore.undo}
          disabled={explore.nodeCount === 0}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
        >
          ↶ 撤销
        </button>
        <button
          type="button"
          onClick={explore.clear}
          disabled={explore.nodeCount === 0}
          className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
        >
          清空
        </button>
      </div>

      {/* 主体：对话流 + 画布/地图/分屏 + 侧栏 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 左侧对话流（ADR-015：AG-UI Agent ReAct 循环，替代旧 ConversationPanel）*/}
        {showConversation && (
          <div className="w-[400px] shrink-0 border-r border-slate-200 bg-white lg:w-[440px]">
            <AssistantUiChat
              key={ontology}
              agent={graphAgent}
              ontology={ontology}
              autoSend={pendingQuestion}
              systemPrompt={`你是图探索助手。用户会用自然语言提问，你需要调用工具探索本体数据并驱动画布。
可用工具：query_with_dataframe（加载对象/图遍历/空间过滤）、traverse_link（展开关系）、switch_view（切图谱/地图/分屏）、color_by（按属性着色）。
决策原则：先加载数据，看到结果后再决定是否展开关系或调整可视化。若查询返回 0 个对象，如实告知用户当前本体无相关数据，不要编造分析结论。`}
            />
          </div>
        )}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* 视图切换 */}
          <div className="flex items-center gap-1 border-b border-slate-200 bg-slate-50 px-2 py-1">
            {(['graph', 'map', 'split'] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`rounded px-2 py-0.5 text-xs ${
                  view === v ? 'bg-slate-700 text-white' : 'text-slate-500 hover:bg-slate-200'
                }`}
              >
                {v === 'graph' ? '图谱' : v === 'map' ? '地图' : '分屏'}
              </button>
            ))}
            <select
              value={layout}
              onChange={(e) => setLayout(e.target.value as typeof layout)}
              className="rounded border border-slate-300 px-1 py-0.5 text-xs"
              title="布局算法"
            >
              <option value="fcose">力导向</option>
              <option value="dagre">层级</option>
              <option value="circle">环形</option>
              <option value="grid">网格</option>
            </select>
            <div className="flex-1" />
            <button
              onClick={() => setShowTrajectory((s) => !s)}
              className={`rounded px-2 py-0.5 text-xs ${
                showTrajectory ? 'bg-blue-600 text-white' : 'text-slate-500 hover:bg-slate-200'
              }`}
              title="轨迹回放面板"
            >
              ▶ 轨迹
            </button>
          </div>
          {/* 主体内容区 */}
          <div className="flex flex-1 overflow-hidden">
            {(view === 'graph' || view === 'split') && (
              <div className={view === 'split' ? 'w-1/2 border-r border-slate-200' : 'flex-1'}>
                <GraphCanvas
                  explore={explore}
                  linkTypes={linkTypes}
                  collapsed={explore.shouldCollapse}
                  layerStyle={explore.layerStyle}
                  layout={layout}
                  dimmedVids={dimmedVids ?? undefined}
                  hideDimmed={timeFilter.activeOnly}
                  onSearchAround={handleSearchAround}
                />
              </div>
            )}
            {(view === 'map' || view === 'split') && (
              <div className={view === 'split' ? 'w-1/2' : 'flex-1'}>
                <MapPanel ontology={ontology} explore={explore} />
              </div>
            )}
          </div>
        </div>
        {/* 侧栏：可折叠（折叠时变窄图标条 w-12，展开 w-80）。画布优先最大化。 */}
        <aside
          className={`flex flex-col border-l border-slate-200 bg-white transition-[width] ${
            sidebarCollapsed ? 'w-12' : 'w-80'
          }`}
        >
          {/* 折叠态：竖排图标条 */}
          {sidebarCollapsed ? (
            <div className="flex flex-col items-center gap-1 py-2">
              <button
                onClick={() => setSidebarCollapsed(false)}
                className="rounded p-1.5 text-slate-500 hover:bg-slate-100"
                title="展开侧栏"
              >
                «
              </button>
              {([
                ['selection', '选中'],
                ['layers', '图层'],
                ['histogram', '分布'],
                ['explore', '探索'],
              ] as const).map(([t, label]) => (
                <button
                  key={t}
                  onClick={() => {
                    setSidebarTab(t);
                    setSidebarCollapsed(false);
                  }}
                  className={`rounded p-1.5 text-sm ${
                    sidebarTab === t ? 'bg-blue-50 text-blue-600' : 'text-slate-500 hover:bg-slate-100'
                  }`}
                  title={label}
                >
                  {t === 'selection' ? '◉' : t === 'layers' ? '▤' : t === 'histogram' ? '📊' : '🔍'}
                </button>
              ))}
            </div>
          ) : showTrajectory ? (
            <>
              <div className="flex items-center justify-between border-b border-slate-200 px-2 py-1">
                <span className="text-xs text-slate-500">轨迹回放</span>
                <button
                  onClick={() => setSidebarCollapsed(true)}
                  className="rounded p-1 text-xs text-slate-500 hover:bg-slate-100"
                  title="折叠侧栏"
                >
                  »
                </button>
              </div>
              <TrajectoryPlayer ontology={ontology} explore={explore} />
            </>
          ) : (
            <>
              {/* tab 头 + 折叠按钮 */}
              <div className="flex border-b border-slate-200">
                {(['selection', 'layers', 'histogram', 'explore'] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setSidebarTab(t)}
                    className={`flex-1 py-1.5 text-xs ${
                      sidebarTab === t
                        ? 'border-b-2 border-blue-500 font-medium text-blue-600'
                        : 'text-slate-500 hover:bg-slate-50'
                    }`}
                  >
                    {t === 'selection' ? '选中' : t === 'layers' ? '图层' : t === 'histogram' ? '分布' : '探索'}
                  </button>
                ))}
                <button
                  onClick={() => setSidebarCollapsed(true)}
                  className="shrink-0 px-1.5 text-xs text-slate-400 hover:bg-slate-50 hover:text-slate-600"
                  title="折叠侧栏"
                >
                  »
                </button>
              </div>
              {/* tab 内容 */}
              <div className="flex-1 overflow-y-auto">
                {sidebarTab === 'selection' &&
                  (selectedNode ? (
                    <div className="p-3">
                      <h3 className="mb-2 text-sm font-semibold text-slate-700">
                        {selectedNode.api_name}
                      </h3>
                      <dl className="space-y-1 text-xs">
                        <div>
                          <dt className="text-slate-400">rid</dt>
                          <dd className="break-all font-mono text-slate-700">
                            {selectedNode.rid}
                          </dd>
                        </div>
                        {Object.entries(selectedNode.props).map(([k, v]) => (
                          <div key={k}>
                            <dt className="text-slate-400">{k}</dt>
                            <dd className="text-slate-700">{String(v)}</dd>
                          </div>
                        ))}
                      </dl>
                      {/* Search Around 快捷操作 */}
                      <div className="mt-4">
                        <div className="mb-1 text-xs font-semibold text-slate-500">
                          Search Around
                        </div>
                        {linkTypes.map((lt) => (
                          <button
                            key={lt.id}
                            type="button"
                            onClick={() => handleSearchAround(selectedNode.rid, lt.api_name)}
                            disabled={explore.loading}
                            className="mb-1 block w-full rounded border border-slate-200 px-2 py-1 text-left text-xs hover:bg-blue-50 disabled:opacity-50"
                          >
                            🔗 {lt.api_name} ({lt.cardinality})
                          </button>
                        ))}
                      </div>
                      <PathFinder
                        ontology={ontology}
                        selectedVid={selectedNode.rid}
                        nodeVids={Array.from(explore.nodes.keys())}
                      />
                      {/* ⚡ 可执行操作（Phase 2e 分析→行动闭环）*/}
                      <div className="mt-4">
                        <div className="mb-1 text-xs font-semibold text-slate-500">⚡ 可执行操作</div>
                        {actionTrigger.loading ? (
                          <div className="text-xs text-slate-400">加载操作…</div>
                        ) : actionTrigger.applicableActions.length === 0 ? (
                          <div className="text-xs text-slate-400">该对象类型无可执行操作</div>
                        ) : (
                          actionTrigger.applicableActions.map((a) => (
                            <button
                              key={a.id}
                              type="button"
                              onClick={() => actionTrigger.trigger(a)}
                              className="mb-1 block w-full rounded border border-amber-200 bg-amber-50 px-2 py-1 text-left text-xs text-amber-800 hover:bg-amber-100"
                            >
                              ⚡ {a.display_name}
                            </button>
                          ))
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="p-3 text-sm text-slate-400">选中节点查看详情</div>
                  ))}
                {sidebarTab === 'layers' && <LayersPanel explore={explore} />}
                {sidebarTab === 'histogram' && (
                  <HistogramPanel
                    explore={explore}
                    onApplyFilter={handleApplyFilter}
                  />
                )}
                {sidebarTab === 'explore' && (
                  <SearchAroundConfigPanel
                    ontology={ontology}
                    linkTypes={linkTypes}
                    config={searchAroundConfig}
                    selectedVids={explore.selectedVid ? [explore.selectedVid] : []}
                    onExecute={async (ir) => {
                      explore.clear();
                      await explore.loadStartSet(ir);
                      setBaseIR(ir);
                    }}
                  />
                )}
              </div>
            </>
          )}
        </aside>
      </div>

      {/* 底栏：全局时间轴（design-v2 §1.3）*/}
      <TimeScrubber timeFilter={timeFilter} nodeTimestamps={nodeTimestamps} />

      <EvidenceDrawer
        ontology={ontology}
        analysisId={evidenceId}
        onClose={() => setEvidenceId(null)}
      />

      {/* ⚡ Action 执行对话框（复用 ExecuteActionDialog，预填 rid）*/}
      {actionTrigger.execAction && selectedNode && (
        <ExecuteActionDialog
          open
          onClose={actionTrigger.close}
          ontology={ontology}
          objectType={selectedNode.api_name}
          action={actionTrigger.execAction}
          initialParameters={{ rid: selectedNode.rid }}
          onApplied={() => {
            // read-your-writes：刷新该节点属性
            void explore.refreshNode(selectedNode.rid);
            actionTrigger.close();
          }}
        />
      )}
    </div>
  );
}
