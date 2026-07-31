import { useEffect, useId, useRef, useState } from 'react';
import type { Core, EventObject, Singular } from 'cytoscape';
import { getThemeColors } from '../lib/themeColors';
import { registerExtensionsOnce } from '../lib/cytoscapeExtensions';
import { shouldRelayout } from '../lib/graphLayout';
import { useTheme } from '../hooks/useTheme';
import { cn } from '../lib/cn';
import { Select, SelectOption } from './ui/Select';
import type { ObjectTypeSummary, LinkTypeDef } from '../types';

// 扩展注册单例逻辑见 lib/cytoscapeExtensions.ts（单独成模块，避免 HMR preamble 问题）。

interface OntologyGraphProps {
  objectTypes: ObjectTypeSummary[];
  links: LinkTypeDef[];
  onSelectObject: (apiName: string) => void;
  onEditObject?: (apiName: string) => void;
  /** 当前图谱视图是否可见。组件用 CSS hidden 显隐，容器在 hidden 时尺寸为 0，
   *  cytoscape 实例首次在不可见容器中创建会画不出内容。
   *  visible 由 false→true 时需 cy.resize()+重新布局以适配真实尺寸。 */
  visible?: boolean;
}

/** 可切换的布局算法配置（Cytoscape 原生内置，无需扩展） */
const LAYOUTS = [
  {
    name: 'concentric',
    label: '同心圆',
    build: () => ({
      name: 'concentric',
      animate: false,
      padding: 30,
      fit: true,
      minNodeSpacing: 40,
      concentric: (n: { degree: () => number }) => n.degree(),
      levelWidth: () => 1,
    }),
  },
  {
    name: 'fcose',
    label: '力导向',
    build: () => ({
      // fcose（i-Vis Lab）：内置 cose 的快速高质量演进版，支持复合图与非均匀节点尺寸，
      // randomize 默认 true（全局打散+冷却），首跑即收敛良好，无内置 cose 的局部极小问题。
      // 借鉴 js.cytoscape.org/demos/fcose-gene：animate:true + nodeRepulsion 回调，
      // 节点变小时边长适度缩减，避免大小节点混排时拥挤。
      name: 'fcose',
      animate: true,
      animationDuration: 400,
      randomize: true,
      idealEdgeLength: (e: { source: () => { data: (k: string) => unknown }; target: () => { data: (k: string) => unknown } }) => {
        // 两端节点权重越大（核心对象），理想边长越长，给大节点留出空间
        const sw = Number(e.source().data('weight') ?? 0);
        const tw = Number(e.target().data('weight') ?? 0);
        let len = 70 + Math.min(sw, tw) * 2;
        // 叶子出边额外加长：把叶子推向外围，避免用户画像/试驾报告系列扎堆抱团
        const sTier = e.source().data('tier');
        const tTier = e.target().data('tier');
        if (sTier === 'L1' || tTier === 'L1') len += 50;
        return len;
      },
      // 增大排斥力：让四大橙色枢纾彼此拉开距离，叶子整体向外分散不扎堆
      nodeRepulsion: () => 18000,
      // 节点间距下限，避免大小节点贴在一起
      nodeSeparation: 60,
      // 适度降低全局引力，叶子节点向外扩散平衡画面重量
      gravity: 0.12,
      gravityRange: 3.0,
      padding: 30,
      fit: true,
      quality: 'default',
      // 配合非均匀节点尺寸，关闭 uniformNodeDimensions
      uniformNodeDimensions: false,
      packComponents: true,
      tile: true,
    }),
  },
  {
    name: 'breadthfirst',
    label: '层级树',
    build: () => ({
      name: 'breadthfirst',
      animate: false,
      padding: 30,
      fit: true,
      directed: true,
      spacingFactor: 1.2,
    }),
  },
  {
    name: 'circle',
    label: '环形',
    build: () => ({ name: 'circle', animate: false, padding: 30, fit: true }),
  },
  {
    name: 'grid',
    label: '网格',
    build: () => ({ name: 'grid', animate: false, padding: 30, fit: true, spacingFactor: 1.1 }),
  },
] as const;

/**
 * 本体图谱视图（Cytoscape.js）。
 *
 * 架构设计（遵循 Cytoscape + React 最佳实践）：
 *
 * 1. **实例只创建一次**：cytoscape 实例在组件挂载时创建，卸载时 destroy。
 *    组件本身由父组件用 CSS `hidden` 显隐 —— 切视图不卸载组件，
 *    实例/布局/缩放/拖拽位置全部保留，切回瞬间恢复。
 *
 * 2. **数据增量同步**：objectTypes/links 变化时，diff 增删节点与边。
 *
 * 3. **画布工具栏**：布局切换、重排、缩放、自适应、锁定/隐藏选中、导出 PNG。
 *    全部走 Cytoscape 原生 API，无需扩展。
 *
 * 4. **主题感知**：theme 变化时重建 stylesheet。
 *
 * 5. **懒加载**：cytoscape 包动态 import，不进首屏 chunk。
 */
export function OntologyGraph({
  objectTypes,
  links,
  onSelectObject,
  onEditObject,
  visible = true,
}: OntologyGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [loading, setLoading] = useState(true);
  const [layoutName, setLayoutName] = useState<string>('fcose');
  const layoutNameRef = useRef(layoutName);
  useEffect(() => {
    layoutNameRef.current = layoutName;
  }, [layoutName]);
  const [selectionInfo, setSelectionInfo] = useState({ count: 0, locked: false });
  const { theme } = useTheme();

  const onSelectObjectRef = useRef(onSelectObject);
  const onEditObjectRef = useRef(onEditObject);
  useEffect(() => {
    onSelectObjectRef.current = onSelectObject;
    onEditObjectRef.current = onEditObject;
  }, [onSelectObject, onEditObject]);
  // cxtmenu / navigator 实例引用（cleanup 用）
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cxtmenuRef = useRef<any[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const navigatorRef = useRef<any>(null);
  // navigator 容器唯一 id（传字符串选择器给扩展，避免传 DOM 被误判走 document.body）
  const navigatorContainerId = useId().replace(/[:]/g, '');
  const navigatorInitializedRef = useRef(false);

  // ── 1. 实例创建（仅一次）──
  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    setLoading(true);
    (async () => {
      // 单例锁注册扩展：保证只注册一次（StrictMode 双挂载/HMR 安全）
      const cytoscape = await registerExtensionsOnce();
      if (cancelled || !container) return;

      const cy = cytoscape({
        container,
        style: buildCyStyles(),
        minZoom: 0.3,
        maxZoom: 3,
        // 不设置 wheelSensitivity：cytoscape 官方警告自定义值会导致主流鼠标缩放不自然，
        // 除非能保证所有用户硬件一致。使用默认值。
        // 开启框选多选：Shift/Ctrl + 拖拽框选
        boxSelectionEnabled: true,
        autounselectify: false,
      });

      cy.on('tap', 'node', (evt: EventObject) => {
        const id = evt.target.id();
        const ot = objectTypes.find((o) => o.id === id);
        if (ot) onSelectObjectRef.current(ot.api_name);
      });
      cy.on('mouseover', 'node', (evt: EventObject) => {
        const n = (evt.target as Singular).closedNeighborhood();
        cy.elements()
          .not(n)
          .stop()
          .animate({ style: { opacity: 0.25 } }, { duration: 180 });
      });
      cy.on('mouseout', 'node', () =>
        cy
          .elements()
          .stop()
          .animate({ style: { opacity: 1 } }, { duration: 180 }),
      );
      // 选中变化 → 更新工具栏状态（锁定按钮禁用态、选中计数）
      cy.on('select unselect', () => {
        const selected = cy.$(':selected');
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const hasLocked = (selected.nodes() as any).some((n: any) => n.locked());
        setSelectionInfo({ count: selected.length, locked: hasLocked });
      });

      cyRef.current = cy;
      setLoading(false);
      syncElements(cy, objectTypes, links, true, layoutName);

      // ── 右键菜单（cxtmenu）：节点操作 + 背景操作 ──
      const c = getThemeColors();
      const fillColor = 'rgba(0, 0, 0, 0.75)';
      const activeFillColor = c.accent;
      // 节点右键菜单
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const nodeMenu = (cy as any).cxtmenu({
        selector: 'node',
        commands: [
          {
            content: '🔍 聚焦关联',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (ele: any) => focusNeighborhood(cy, ele.id(), 2),
          },
          {
            content: '✏️ 编辑',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (ele: any) => onEditObjectRef.current?.(ele.data('api_name') || ele.id()),
            enabled: !!onEditObject,
          },
          {
            content: '🔒 锁定',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (ele: any) => ele.lock(),
          },
          {
            content: '🗑 隐藏',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (ele: any) => {
              ele.remove();
              cy.fit(undefined, 40);
            },
          },
        ],
        fillColor,
        activeFillColor,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any);
      // 背景右键菜单
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const bgMenu = (cy as any).cxtmenu({
        selector: 'core',
        commands: [
          {
            content: '⤢ 自适应',
            select: () => cy.fit(undefined, 40),
          },
          {
            content: '↻ 重排',
            select: () => runLayout(cy, layoutNameRef.current),
          },
          {
            content: '✨ 显示全部',
            select: () => {
              cy.elements().style('opacity', 1);
              cy.fit(undefined, 40);
            },
          },
        ],
        fillColor,
        activeFillColor,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any);
      cxtmenuRef.current = [nodeMenu, bgMenu];

      // ── 鸟瞰缩略图（navigator）──
      // 移至 visible effect：容器 hidden 时初始化会尺寸 0 导致缩略图裂开、定位错乱。
      // navigatorRef 在首次可见时初始化。
    })();

    return () => {
      cancelled = true;
      // 销毁右键菜单实例
      cxtmenuRef.current.forEach((m) => {
        try {
          m.destroy();
        } catch {
          /* 已销毁 */
        }
      });
      cxtmenuRef.current = [];
      const cy = cyRef.current;
      if (cy) {
        // navigator 随 cy.destroy 自动清理
        try {
          navigatorRef.current?.destroy();
        } catch {
          /* 已销毁 */
        }
        navigatorRef.current = null;
        cy.destroy();
        cyRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载时创建实例
  }, []);

  // ── 2. 数据增量同步 ──
  // 依赖 loading：cy 就绪（loading→false）后即使数据未变也重跑一次，
  // 避免数据先于 cy 就绪到达导致节点未加入。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    syncElements(cy, objectTypes, links, false, layoutName);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objectTypes, links, loading]);

  // ── 2b. 可见性变化：容器从 hidden→可见时，尺寸从 0 变为真实值，
  //      需 cy.resize() 让 cytoscape 重算画布尺寸，并重新布局/适配。
  //      首次可见（从未在有效尺寸下布局过）重跑布局；后续切换只 resize+fit，
  //      保留用户拖拽位置。
  const everLaidOutRef = useRef(false);
  useEffect(() => {
    if (!visible) return;
    const cy = cyRef.current;
    if (!cy) return; // cy 未就绪：loading 变 false 后本 effect 会重跑（依赖 loading）
    // 容器刚显示，下一帧确保布局已应用尺寸
    const raf = requestAnimationFrame(() => {
      cy.resize();
      if (!everLaidOutRef.current && cy.nodes().length > 0) {
        runLayout(cy, layoutNameRef.current);
        everLaidOutRef.current = true;
      } else {
        cy.fit(undefined, 40);
      }
      // 布局完成后再初始化/刷新鸟瞰缩略图，确保缩略图基于正确布局导出
      if (!navigatorInitializedRef.current) {
        try {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const nav = (cy as any).navigator({
            container: `#${navigatorContainerId}`,
            viewLiveFramer: false,
            rerenderDelay: 200,
          });
          navigatorRef.current = nav;
          navigatorInitializedRef.current = true;
          // 初始化后强制触发一次缩略图重绘
          cy.emit('resize');
        } catch (err) {
          console.error('[OntologyGraph] navigator init failed:', err);
        }
      } else {
        // 后续可见：通知 navigator 重算尺寸并重绘缩略图
        try {
          navigatorRef.current?.resize?.();
          cy.emit('resize');
        } catch {
          /* noop */
        }
      }
    });
    return () => cancelAnimationFrame(raf);
    // navigatorContainerId 来自 useId，稳定不变，无需列入依赖
    // loading 作为 cy 就绪信号：cy 创建后 setLoading(false) 触发本 effect 重跑
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, loading]);

  // ── 3. 主题切换重建样式 ──
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.style(buildCyStyles());
  }, [theme]);

  // ── 工具栏操作 ──
  const handleRelayout = () => {
    const cy = cyRef.current;
    if (!cy) return;
    runLayout(cy, layoutName);
  };

  const handleLayoutChange = (name: string) => {
    setLayoutName(name);
    const cy = cyRef.current;
    if (cy) runLayout(cy, name);
  };

  const handleZoomIn = () =>
    cyRef.current?.zoom({
      level: cyRef.current.zoom() * 1.3,
      renderedPosition: { x: cyRef.current.width() / 2, y: cyRef.current.height() / 2 },
    });
  const handleZoomOut = () =>
    cyRef.current?.zoom({
      level: cyRef.current.zoom() / 1.3,
      renderedPosition: { x: cyRef.current.width() / 2, y: cyRef.current.height() / 2 },
    });
  const handleFit = () => cyRef.current?.fit(undefined, 40);

  const handleToggleLock = () => {
    const cy = cyRef.current;
    if (!cy) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const selected = cy.$(':selected').nodes() as any;
    if (selected.length === 0) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const anyLocked = selected.some((n: any) => n.locked());
    if (anyLocked) {
      selected.unlock();
    } else {
      selected.lock();
    }
    setSelectionInfo({ count: cy.$(':selected').length, locked: !anyLocked });
  };

  const handleHide = () => {
    const cy = cyRef.current;
    if (!cy) return;
    // 隐藏选中节点（仅画布隐藏，不影响后端数据；重排/刷新数据时 syncElements 会恢复）
    cy.$(':selected').remove();
    cy.fit(undefined, 40);
  };

  const handleExportPng = () => {
    const cy = cyRef.current;
    if (!cy) return;
    const png = cy.png({ full: true, scale: 2, bg: GRAPH_COLORS.canvasBg });
    const a = document.createElement('a');
    a.href = png;
    a.download = `ontology-graph-${Date.now()}.png`;
    a.click();
  };

  // 导出 SVG（矢量，与画布渲染一致）：用 cytoscape-svg 扩展序列化，
  // 而非手写 SVG（手写难以同步 tier 配色/尺寸/虚线边/haystack 边等样式，易与预览脱节）。
  const handleExportSvg = () => {
    const cy = cyRef.current;
    if (!cy) return;
    const svg = cy.svg({ full: true, scale: 2, bg: GRAPH_COLORS.canvasBg });
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ontology-graph-${Date.now()}.svg`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="canvas-container relative">
      {/* 画布工具栏 */}
      {!loading && (
        <div className="pointer-events-auto absolute left-3 top-3 z-10 flex items-center gap-1 rounded-md border border-border bg-surface/95 p-1 shadow-md backdrop-blur">
          {/* 布局下拉 */}
          <Select
            inputClassName="form-select m-0 w-auto border-none bg-transparent py-1 pr-6 text-xs text-text outline-none"
            value={layoutName}
            onChange={handleLayoutChange}
            aria-label="布局算法"
          >
            {LAYOUTS.map((l) => (
              <SelectOption key={l.name} value={l.name} label={l.label} />
            ))}
          </Select>

          <ToolbarDivider />

          <ToolbarBtn title="重排" onClick={handleRelayout}>
            ↻
          </ToolbarBtn>
          <ToolbarBtn title="放大" onClick={handleZoomIn}>
            ⊕
          </ToolbarBtn>
          <ToolbarBtn title="缩小" onClick={handleZoomOut}>
            ⊖
          </ToolbarBtn>
          <ToolbarBtn title="自适应 (Fit)" onClick={handleFit}>
            ⤢
          </ToolbarBtn>

          <ToolbarDivider />

          <ToolbarBtn
            title={selectionInfo.locked ? '解锁选中节点' : '锁定选中节点'}
            onClick={handleToggleLock}
            disabled={selectionInfo.count === 0}
            active={selectionInfo.locked}
          >
            🔒
          </ToolbarBtn>
          <ToolbarBtn
            title="隐藏选中节点（仅画布）"
            onClick={handleHide}
            disabled={selectionInfo.count === 0}
          >
            🗑
          </ToolbarBtn>

          <ToolbarDivider />

          <ToolbarBtn title="导出 PNG" onClick={handleExportPng}>
            ⤓
          </ToolbarBtn>
          <ToolbarBtn title="导出 SVG（矢量）" onClick={handleExportSvg}>
            <span className="text-[10px]">SVG</span>
          </ToolbarBtn>
        </div>
      )}

      {/* 选中计数提示 */}
      {!loading && selectionInfo.count > 0 && (
        <div className="pointer-events-none absolute right-3 top-3 z-10 rounded-pill bg-accent/90 px-2.5 py-1 text-[11px] font-medium text-bg">
          已选 {selectionInfo.count} 个
        </div>
      )}

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-text-muted">
          正在加载图谱引擎…
        </div>
      )}
      <div ref={containerRef} className="h-full w-full" style={{ background: GRAPH_COLORS.canvasBg }} />

      {/* 鸟瞰缩略图容器：navigator 扩展挂载点，固定右下角 */}
      <div id={navigatorContainerId} className="graph-navigator-container" aria-hidden="true" />
    </div>
  );
}

// ── 工具栏小组件 ──
function ToolbarBtn({
  children,
  onClick,
  title,
  disabled,
  active,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      className={cn(
        'flex h-7 w-7 items-center justify-center rounded text-sm transition-colors',
        'text-text-secondary hover:bg-accent/15 hover:text-accent-text',
        active && 'bg-accent/20 text-accent-text',
        disabled && 'cursor-not-allowed opacity-30 hover:bg-transparent hover:text-text-secondary',
      )}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
    >
      {children}
    </button>
  );
}

function ToolbarDivider() {
  return <span className="mx-0.5 h-5 w-px bg-border" />;
}

// ── 增量同步：diff 增删节点与边，仅在必要时跑布局 ──
function syncElements(
  cy: Core,
  objectTypes: ObjectTypeSummary[],
  links: LinkTypeDef[],
  isFirstSync: boolean,
  layoutName: string,
) {
  const wantedNodeIds = new Set(objectTypes.map((ot) => ot.id));
  const existingNodeIds = new Set(cy.nodes().map((n) => n.id()));
  const hasNewNodes = objectTypes.some((ot) => !existingNodeIds.has(ot.id));

  cy.nodes().forEach((n) => {
    if (!wantedNodeIds.has(n.id())) cy.remove(n);
  });

  for (const ot of objectTypes) {
    // 节点“权重”：属性数 + 连接数，用于映射节点大小（核心对象更突出，借鉴 fcose-gene 的 mapData 范式）
    const weight = ot.properties_count + ot.links_count;
    const el = cy.getElementById(ot.id);
    if (el.length > 0) {
      el.data('label', ot.display_name);
      el.data('properties', ot.properties_count);
      el.data('weight', weight);
    } else {
      cy.add({
        group: 'nodes',
        data: {
          id: ot.id,
          label: ot.display_name,
          properties: ot.properties_count,
          weight,
        },
      });
    }
  }

  const wantedEdgeIds = new Set(links.map((l) => l.id));
  cy.edges().forEach((e) => {
    if (!wantedEdgeIds.has(e.id())) cy.remove(e);
  });
  // 追踪是否有新边加入：拓扑变化（即使节点没变）也需重排，
  // 否则力导向布局会停留在「无边时排成的网格」上，边加上后节点仍挤在一起。
  // 典型场景：父组件把 objectTypes 和 links 分两次 setState（虽已改为原子更新，
  // 此处仍作防御，避免其他调用路径重现该 bug）。
  let hasNewEdges = false;
  for (const link of links) {
    if (
      !wantedNodeIds.has(link.source_object_type_id) ||
      !wantedNodeIds.has(link.target_object_type_id)
    )
      continue;
    const existing = cy.getElementById(link.id);
    if (existing.length > 0) {
      // 已有边：同步标签与基数/方向（用户改名或改基数后刷新）
      existing.data('label', link.display_name);
      existing.data('cardinality', link.cardinality);
      existing.data('direction', link.direction);
      continue;
    }
    hasNewEdges = true;
    cy.add({
      group: 'edges',
      data: {
        id: link.id,
        source: link.source_object_type_id,
        target: link.target_object_type_id,
        label: link.display_name,
        // cardinality/direction 用于按关系类型分色与箭头朝向（借鉴 fcose-gene 的 edge[group=...] 范式）
        cardinality: link.cardinality,
        direction: link.direction,
      },
    });
  }

  // 节点按 degree 分档染色（越连越多越强调），在边全部入图后计算保证准确。
  // 分档采用相对分位 + 下限保护，适配任意规模图谱：
  //  大图谱走四分位（P25/P50/P75）均分 4 档；小图谱走绝对下限，避免低连接被误判为枢纽。
  //  L1 叶子 / L2 中间 / L3 重要 / L4 枢纽，仅 tier 变化时写入。
  const degrees = cy.nodes().map((n) => n.degree(false)).sort((a, b) => a - b);
  const p = (q: number) => degrees[Math.min(degrees.length - 1, Math.floor(q * degrees.length))];
  const t2 = Math.max(p(0.25), 2);   // L2 阈值：≥ P25 且至少 2
  const t3 = Math.max(p(0.5), 3);    // L3 阈值：≥ P50 且至少 3
  const t4 = Math.max(p(0.75), 5);   // L4 阈值：≥ P75 且至少 5
  cy.nodes().forEach((n) => {
    const d = n.degree(false);
    const tier = d >= t4 ? 'L4' : d >= t3 ? 'L3' : d >= t2 ? 'L2' : 'L1';
    if (n.data('tier') !== tier) n.data('tier', tier);
  });

  // 布局策略（决策见 shouldRelayout）：
  // - 首次同步：跑布局 + fit
  // - 后续有新节点**或新边**：重新跑布局（拓扑变化需重排，否则边加在旧位置上节点挤在一起）
  // - 无变化：不动，保留用户拖拽/缩放
  // - 容器不可见（0 尺寸）时只加节点不跑布局：交给 visible effect 在可见后重排，
  //   避免 0 尺寸下跑出无效布局覆盖后续有效布局。
  const cyW = cy.width();
  const cyH = cy.height();
  const containerVisible = cyW > 0 && cyH > 0;
  if (shouldRelayout({ isFirstSync, hasNewNodes, hasNewEdges, containerVisible, nodeCount: objectTypes.length })) {
    runLayout(cy, layoutName);
  }
}

function runLayout(cy: Core, layoutName: string) {
  const config = LAYOUTS.find((l) => l.name === layoutName) ?? LAYOUTS[0];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const layout = cy.layout(config.build() as any);
  // 关键：fit 必须在 layoutstop（布局真正结束）后调用，不能紧跟 layout.run()。
  // fcose 带 animate:true 时是“连续布局”（异步），run() 只启动动画，节点位置要等
  // 动画结束才到位；若 run() 后立即 fit，读到的是动画起始“挤一团”的包围盒，
  // 算出错误的缩放/平移，导致切换本体后节点巨大、挤在一起、看不到关系。
  // （cytoscape 官方 issue #2559：cy.fit() 在 layout 初始化后立即调用不生效/错误）
  layout.one('layoutstop', () => {
    cy.fit(undefined, 40);
  });
  layout.run();
}

/**
 * 聚焦某节点 N 层关联：高亮该节点及其 N 层邻居，其余节点暗化。
 * 对应 Vertex 的 Search Around 周边探索（聚焦语义，非加载更多）。
 */
function focusNeighborhood(cy: Core, nodeId: string, depth: number) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const start = cy.getElementById(nodeId) as any;
  if (start.empty()) return;
  // 递归收集 N 层邻居
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let frontier: any = start.union(start.connectedEdges());
  let neighborhood = frontier;
  for (let i = 1; i < depth; i++) {
    frontier = frontier.neighborhood();
    neighborhood = neighborhood.union(frontier);
  }
  cy.elements().style('opacity', 0.15);
  neighborhood.style('opacity', 1);
  cy.fit(neighborhood, 60);
}


// ── 样式构建：独立配色，不读项目主题变量 ──
// 设计体系（融合 Ant Design / Cambridge Intelligence / ColorBrewer 最佳实践）：
//  - 画布纯白背景 #FFFFFF，深色枢纾与浅色叶子对比最干净，适配导出
//  - 节点按 degree 分 4 档，跨色系取色形成冷暖对比（青蓝→绿→黄→橙，温度递进）
//    色源：治愈系商用渐变色板四行各取一色
//  - 浅档用深字、深档用白字，保证对比度
//  - 节点半透明边框分隔相邻节点
//  - 边按 cardinality 分色（ONE 淡蓝/MANY 淡橙）+ 边宽映射基数 + 标签沿边 autorotate
//  - 选中态金色边框 #FFD700 高对比跳出
//  - 层次双重编码：degree→颜色档位 + weight→节点大小
const GRAPH_COLORS = {
  canvasBg: '#FFFFFF',        // 画布纯白背景（深色枢纾、浅色叶子对比最干净，适配导出）
  // 节点 4 档：跨色系取色，冷暖对比（青蓝→绿→黄→橙，越连越多越暖越浓）
  tierL1Bg: '#BEE6EE',        // L1 叶子：青蓝系极浅冰蓝（冷·淡）
  tierL1Text: '#546E7A',      //   浅灰字（弱化，不抢视觉）
  tierL2Bg: '#81C15F',        // L2 中间：草木绿系嫩草绿（中·自然）
  tierL2Text: '#1B5E20',      //   深绿字
  tierL3Bg: '#FDD351',        // L3 重要：暖黄系蜂蜜黄（暖·醒目）
  tierL3Text: '#3E2723',      //   深棕字
  tierL4Bg: '#ED6D00',        // L4 枢纽：暖橙系深橘橙（暖·高饱和）
  tierL4Text: '#ffffff',      //   白字
  nodeBorder: 'rgba(0,0,0,0.15)', // 半透明浅边框，分隔相邻节点
  hubBorder: '#ffffff',       // 枢纾白色粗描边，隔绝周边节点形成视觉孤岛
  selectBorder: '#FFD700',    // 选中金边（高对比）
  // 边（冷暖色相对比，一眼区分一对一/多对多）
  edgeOne: '#30B5C5',         // ONE 深青蓝实线（冷色）
  edgeMany: '#945C30',        // MANY 深棕虚线（暖色，加深以与实线拉开色相）
  edgeDefault: '#A0AAB5',     // 兑底中灰
  edgeLabel: '#616161',       // 边标签深灰
  edgeLabelBg: '#ffffff',     // 边标签白底
} as const;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildCyStyles(): any {
  const g = GRAPH_COLORS;
  return [
    // 画布背景 + 选区外观
    {
      selector: 'core',
      style: {
        'active-bg-color': g.nodeBorder,
        'active-bg-opacity': 0.08,
        'selection-box-color': withAlpha(g.selectBorder, 0.2),
        'selection-box-border-color': g.selectBorder,
        'selection-box-opacity': 0.5,
      },
    },
    // 默认节点（L1 叶子）：弱化降噪——小尺寸 + 低透明 + 浅灰字，不抢视觉
    {
      selector: 'node',
      style: {
        'background-color': g.tierL1Bg,
        'border-color': g.nodeBorder,
        'border-width': 1,
        width: 34,
        height: 34,
        label: 'data(label)',
        color: g.tierL1Text,
        'font-size': 10,
        'font-weight': 400,
        opacity: 0.75,
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 4,
        'text-wrap': 'wrap',
        'text-max-width': '60px',
        'overlay-padding': '4px',
      },
    },
    // L2 中间节点（degree 2-3）：嫩草绿，中等尺寸
    {
      selector: 'node[tier = "L2"]',
      style: {
        'background-color': g.tierL2Bg,
        color: g.tierL2Text,
        width: 52,
        height: 52,
        'font-size': 11,
        'font-weight': 500,
        opacity: 1,
        'text-valign': 'center',
        'text-halign': 'center',
        'text-margin-y': 0,
      },
    },
    // L3 重要节点（degree 4-6）：蜂蜜黄，较大 + 白描边，醒目
    {
      selector: 'node[tier = "L3"]',
      style: {
        'background-color': g.tierL3Bg,
        color: g.tierL3Text,
        'border-color': '#ffffff',
        'border-width': 2,
        width: 68,
        height: 68,
        'font-size': 13,
        'font-weight': 600,
        opacity: 1,
        'text-valign': 'center',
        'text-halign': 'center',
        'text-margin-y': 0,
      },
    },
    // L4 枢纽节点（degree ≥7）：深橘橙，最大尺寸 + 白色粗描边 + 大号加粗白字
    // 极致突出：尺寸×1.6、描边 4px 隔绝周边、字号 16 加粗，形成视觉焦点
    {
      selector: 'node[tier = "L4"]',
      style: {
        'background-color': g.tierL4Bg,
        color: g.tierL4Text,
        'border-color': g.hubBorder,
        'border-width': 4,
        width: 92,
        height: 92,
        'font-size': 16,
        'font-weight': 700,
        opacity: 1,
        'text-valign': 'center',
        'text-halign': 'center',
        'text-margin-y': 0,
        'text-wrap': 'wrap',
        'text-max-width': '82px',
        // z-index 提到最上层，确保枢纾不被叶子遮挡
        'z-index': 10,
      },
    },
    // 注：托管表(VIRTUAL)/虚拟表(MANAGED) 是数据集(Dataset)层的概念，
    // 不是 ObjectType 层的属性。图谱节点代表业务对象类型，其视觉表达应
    // 只由 degree（关系计数）决定，与底层挂在托管表还是虚拟表无关——
    // 所有节点一视同仁参与 tier 分档配色。故此处不设 storage_type 覆盖规则。
    // （原先的 VIRTUAL 蜜桃色覆盖规则会让全 VIRTUAL 本体如 DVP 所有节点
    //  变成同一颜色，且混淆了 Dataset 层与 Ontology 层的职责边界。）
    // 选中节点：金色加粗边框（高对比跳出）
    {
      selector: 'node:selected',
      style: { 'border-width': 5, 'border-color': g.selectBorder, 'border-opacity': 1 },
    },
    // 默认边：bezier 曲线 + 箭头表达方向，边宽按两端节点 tier 映射（枢纾出线粗、叶子细）
    //
    // 为何不用 haystack（原方案）：
    //   haystack 模式把边端点收缩到节点内部（haystack-radius=0.5 → 端点落在节点中心 0.5 倍半径处），
    //   箭头尖端随之落在节点圆盘内部，被节点本体完全遮挡 —— 实测箭头尖端距节点中心 6.9px，
    //   而节点渲染半径 14.4px，箭头被埋在节点内部 7.5px。叠加全局缩放 zoom≈0.3 + edgeWidth≈0.6px
    //   + arrow-scale=1，箭头仅约 1.5px，肉眼完全不可见。这正是"关系没有方向"的根因。
    //   haystack 是为"短直线、不强调方向"场景设计的，不适合有向关系图。
    // bezier 端点贴节点边缘，箭头露出；方向语义由 direction 字段决定箭头朝向（见下方两条规则）。
    {
      selector: 'edge',
      style: {
        // 按源头 tier 决定线宽：L4=2.0 / L3=1.5 / L2=1.0 / L1=0.6
        width: (e: { source: () => { data: (k: string) => unknown } }) => {
          const t = e.source().data('tier');
          return t === 'L4' ? 2.0 : t === 'L3' ? 1.5 : t === 'L2' ? 1.0 : 0.6;
        },
        'line-color': g.edgeDefault,
        'curve-style': 'bezier',
        // 箭头方向由 direction 决定（见下方 OUTGOING/INCOMING 规则）；此处先给 target 端兜底。
        // arrow-scale 1.1：略大于默认 1.0，全局缩放看图谱时箭头仍可见但不喧宾夺主。
        // （1.0 在 zoom≈0.3 下仅约 1.5px 偏小；1.5 实测偏大刺眼；1.1 为平衡点）
        'arrow-scale': 1.1,
        'target-arrow-color': g.edgeDefault,
        'target-arrow-shape': 'triangle',
        'source-arrow-color': g.edgeDefault,
        'source-arrow-shape': 'none',
        opacity: 0.5,
        label: 'data(label)',
        color: g.edgeLabel,
        'font-size': '10px',
        // 标签沿边方向旋转，密集时更紧凑
        'text-rotation': 'autorotate',
        'text-background-color': g.edgeLabelBg,
        'text-background-opacity': 0.9,
        'text-background-padding': '2px',
        'text-background-shape': 'roundrectangle',
      },
    },
    // OUTGOING：语义方向 = source→target，箭头在 target 端（默认即此，显式声明避免被覆盖）
    {
      selector: 'edge[direction = "OUTGOING"]',
      style: {
        'target-arrow-shape': 'triangle',
        'source-arrow-shape': 'none',
      },
    },
    // INCOMING：语义方向与物理连边相反 → 箭头反转到 source 端。
    // 不交换 source/target（会破坏 FK 映射语义），仅反转箭头朝向。
    {
      selector: 'edge[direction = "INCOMING"]',
      style: {
        'target-arrow-shape': 'none',
        'source-arrow-shape': 'triangle',
      },
    },
    // 一对一关系（ONE）：深青蓝（冷色，与 MANY 暖色拉开色相）
    // 基数完全靠边标签后缀表达（ONE 无后缀，MANY 带 `· N:1`），
    // 不再用实线/虚线区分——线型语义不直观（用户不会猜到“虚=多”）。
    // 颜色冷暖作为辅助编码：色盲不友好，但配合标签文字可双保险。
    {
      selector: 'edge[cardinality = "ONE"]',
      style: {
        'line-color': g.edgeOne,
        'target-arrow-color': g.edgeOne,
        'source-arrow-color': g.edgeOne,
        'line-style': 'solid',
        opacity: 0.6,
      },
    },
    // 多对一关系（MANY）：深棕（暖色），与 ONE 冷色拉开色相
    {
      selector: 'edge[cardinality = "MANY"]',
      style: {
        'line-color': g.edgeMany,
        'target-arrow-color': g.edgeMany,
        'source-arrow-color': g.edgeMany,
        'line-style': 'solid',
        opacity: 0.55,
      },
    },
    // 选中边：金色加粗，跳出（两端箭头同色）
    {
      selector: 'edge:selected',
      style: {
        width: 4,
        opacity: 1,
        'line-color': g.selectBorder,
        'target-arrow-color': g.selectBorder,
        'source-arrow-color': g.selectBorder,
        'line-style': 'solid',
      },
    },
    // 邻接高亮暗化态（mouseover 时由 JS 动画赋 opacity，此处提供 class 兑底）
    {
      selector: '.unhighlighted',
      style: { opacity: 0.2 },
    },
    {
      selector: '.highlighted',
      style: { 'z-index': 999999 },
    },
  ];
}

/** hex/rgb 转 rgba 字符串（cytoscape 不认 8 位 hex，需显式 rgba）。 */
function withAlpha(color: string, alpha: number): string {
  if (!color) return `rgba(0,0,0,${alpha})`;
  const hex = color.trim();
  if (hex.startsWith('#')) {
    const v = hex.slice(1);
    const full = v.length === 3 ? v.split('').map((x) => x + x).join('') : v;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  // 已是 rgb(a) 形式
  if (hex.startsWith('rgb')) {
    return hex.replace(/rgba?\(([^)]+)\)/, (_, body) => {
      const parts = String(body).split(',').map((s: string) => s.trim());
      return `rgba(${parts[0]},${parts[1]},${parts[2]},${alpha})`;
    });
  }
  return hex;
}
