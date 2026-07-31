/**
 * GraphCanvas — 对象实例图画布（graph-reasoning-frontend-design.md §3）。
 *
 * 区别于 OntologyGraph（设计期 ObjectType+LinkType schema），本组件渲染
 * 运行期对象实例 + 关系边，支持右键 Search Around 增量扩展。
 *
 * 复用 cytoscapeExtensions 单例注册（cxtmenu/navigator），不重造轮子。
 */
import { useEffect, useRef, useState } from 'react';
import type { EventObject, Core } from 'cytoscape';
import cytoscape from 'cytoscape';
import dagre from 'cytoscape-dagre';
import { registerExtensionsOnce } from '../lib/cytoscapeExtensions';

// 注册 dagre 布局扩展（层级布局，流程链路用）
let dagreRegistered = false;
function ensureDagre() {
  if (!dagreRegistered) {
    cytoscape.use(dagre);
    dagreRegistered = true;
  }
}
import type { useGraphExplore, LayerStyle } from '../hooks/useGraphExplore';
import type { LinkTypeDef } from '../types';

interface GraphCanvasProps {
  explore: ReturnType<typeof useGraphExplore>;
  linkTypes: LinkTypeDef[];
  /** LOD 折叠态：节点超阈值时隐藏 label + 缩小节点，避免画布卡顿（C9 防线）。 */
  collapsed?: boolean;
  /** 图层样式（着色/大小，设计 §3.4 Layers tab）。 */
  layerStyle?: LayerStyle;
  /** 布局算法：fcose(默认)/dagre(层级)/circle(环形)/grid(网格)。 */
  layout?: 'fcose' | 'dagre' | 'circle' | 'grid';
  /** 需变灰/隐藏的节点 rid 集（时间轴窗外节点，design-v2 §1.3）。 */
  dimmedVids?: Set<string>;
  /** 是否隐藏 dimmed 节点（activeOnly=true 时）。 */
  hideDimmed?: boolean;
  onSearchAround: (rid: string, linkType: string) => void;
}

/** ObjectType api_name → 着色（按类型着色，调色板循环）。 */
const TYPE_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b',
  '#8b5cf6', '#ec4899', '#14b8a6', '#f97316',
];

function colorForType(apiName: string): string {
  let hash = 0;
  for (let i = 0; i < apiName.length; i++) {
    hash = (hash * 31 + apiName.charCodeAt(i)) | 0;
  }
  return TYPE_COLORS[Math.abs(hash) % TYPE_COLORS.length];
}

function buildStyles(): any[] {
  return [
    {
      selector: 'node',
      style: {
        'background-color': 'data(color)',
        label: 'data(label)',
        'text-valign': 'center',
        'text-halign': 'center',
        color: '#fff',
        'font-size': 10,
        width: 40,
        height: 40,
        'border-width': 2,
        'border-color': '#fff',
      },
    },
    {
      selector: 'node:selected',
      style: { 'border-width': 4, 'border-color': '#fbbf24' },
    },
    {
      selector: 'edge',
      style: {
        width: 2,
        'line-color': '#94a3b8',
        'target-arrow-color': '#94a3b8',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        label: 'data(label)',
        'font-size': 8,
        color: '#64748b',
        'text-background-color': '#fff',
        'text-background-padding': 2,
        'text-background-opacity': 0.8,
      },
    },
  ];
}

export function GraphCanvas({ explore, linkTypes, collapsed = false, layerStyle, layout = 'fcose', dimmedVids, hideDimmed = false, onSearchAround }: GraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  // 用 ref 保持 linkTypes/onSearchAround/layerStyle 最新（cxtmenu 创建时闭包会捕获初始值）。
  const linkTypesRef = useRef(linkTypes);
  linkTypesRef.current = linkTypes;
  const onSearchAroundRef = useRef(onSearchAround);
  onSearchAroundRef.current = onSearchAround;
  const layerStyleRef = useRef(layerStyle);
  layerStyleRef.current = layerStyle;
  const cyRef = useRef<Core | null>(null);
  // cy 就绪标记：初始化 effect 是 async（registerExtensionsOnce），cy 创建
  // 完成后才置 true。作为同步 effect 的依赖，使组件重挂载（图谱→地图→图谱
  // 时 GraphCanvas 被条件渲染卸载再重挂载）后，即便 explore.nodes/edges
  // 引用未变（数据在上层 useGraphExplore 里没丢），同步 effect 也会因
  // cyReady false→true 重跑一次，把既有节点/边灌进新建的空 cy 实例——
  // 否则画布空白（"数据丢了"现象）。
  const [cyReady, setCyReady] = useState(false);

  // 初始化 cytoscape 实例（只一次）。
  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    (async () => {
      const cytoscape = await registerExtensionsOnce();
      if (cancelled || !container) return;

      const cy = cytoscape({
        container,
        style: buildStyles(),
        minZoom: 0.2,
        maxZoom: 3,
        boxSelectionEnabled: true,
        autounselectify: false,
      });

      cy.on('tap', 'node', (evt: EventObject) => {
        explore.setSelectedVid(evt.target.id());
      });

      // 右键菜单：动态枚举该本体所有 LinkType 作为 Search Around 项 + 操作。
      // select 回调读 ref 拿最新 linkTypes/onSearchAround（cxtmenu 闭包捕获初始值）。
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const buildCommands = () => {
        const links = linkTypesRef.current;
        const cmds: Array<{ content: string; select: (elt: any) => void; fillColor?: string }> =
          links.map((lt) => ({
            content: `🔍 ${lt.api_name}`,
            fillColor: '#3b82f6',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (elt: any) => {
              onSearchAroundRef.current(elt.id(), lt.api_name);
            },
          }));
        cmds.push(
          {
            content: '✕ 从画布移除',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (elt: any) => explore.removeNode(elt.id()),
            fillColor: '#ef4444',
          },
          {
            content: '⧉ 复制 rid',
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            select: (elt: any) => {
              void navigator.clipboard?.writeText(elt.id());
            },
          },
        );
        return cmds;
      };

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const nodeMenu = (cy as any).cxtmenu({
        selector: 'node',
        commands: buildCommands(),
      });

      cyRef.current = cy;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (cy as any).nodeMenu = nodeMenu;
      setCyReady(true);

      // cytoscape 创建时容器可能尚未有尺寸（flex 布局），defer 两帧再 resize+fit。
      // 对齐 OntologyGraph 的 requestAnimationFrame 模式（cytoscape #2559）。
      // 用双 raf 确保布局已完成（单 raf 时 flex 容器可能仍 0 尺寸）。
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          cy.resize();
          cy.fit(undefined, 30);
        });
      });

      // 容器尺寸后续变化时也重排。
      const ro = new ResizeObserver(() => {
        cy.resize();
        cy.fit(undefined, 30);
      });
      ro.observe(container);

      return () => {
        ro.disconnect();
        nodeMenu.destroy();
        cy.destroy();
        cyRef.current = null;
      };
    })();

    return () => {
      cancelled = true;
      setCyReady(false);
      const cy = cyRef.current;
      if (cy) {
        cy.destroy();
        cyRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // linkTypes 变化时重建右键菜单（cxtmenu commands 创建时固定，需重建刷新）。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const oldMenu = (cy as any).nodeMenu;
    if (oldMenu) {
      try { oldMenu.destroy(); } catch { /* ignore */ }
    }
    const links = linkTypesRef.current;
    const cmds: Array<{ content: string; select: (elt: any) => void; fillColor?: string }> =
      links.map((lt) => ({
        content: `🔍 ${lt.api_name}`,
        fillColor: '#3b82f6',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        select: (elt: any) => {
          onSearchAroundRef.current(elt.id(), lt.api_name);
        },
      }));
    cmds.push(
      {
        content: '✕ 从画布移除',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        select: (elt: any) => explore.removeNode(elt.id()),
        fillColor: '#ef4444',
      },
      {
        content: '⧉ 复制 rid',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        select: (elt: any) => {
          void navigator.clipboard?.writeText(elt.id());
        },
      },
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (cy as any).nodeMenu = (cy as any).cxtmenu({ selector: 'node', commands: cmds });
  }, [linkTypes]);

  // LOD 折叠：节点超阈值时隐藏 label + 缩小节点，避免画布卡顿（C9 防线）。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (collapsed) {
      cy.style()
        .selector('node')
        .style({ label: '', width: 14, height: 14, 'border-width': 1 })
        .update();
    } else {
      cy.style()
        .selector('node')
        .style({ label: 'data(label)', width: 40, height: 40, 'border-width': 2 })
        .update();
    }
  }, [collapsed]);

  // 图层样式：colorBy/sizeBy 变化时重算所有节点颜色/大小 data。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !layerStyle) return;
    const style = layerStyle;
    const palette = ['#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6', '#6366f1'];
    const valueColorMap: Record<string, string> = style.colorMap ?? {};
    let colorIdx = 0;

    cy.nodes().forEach((n) => {
      // 着色
      let color = colorForType(n.data('api_name'));
      if (style.colorBy === 'property' && style.colorProp) {
        const node = explore.nodes.get(n.id());
        const val = node ? String(node.props?.[style.colorProp] ?? '—') : '—';
        if (!(val in valueColorMap)) {
          valueColorMap[val] = palette[colorIdx % palette.length];
          colorIdx++;
        }
        color = valueColorMap[val];
      }
      n.data('color', color);

      // 大小
      let size = 40;
      if (style.sizeBy === 'degree') {
        size = Math.min(70, 24 + n.degree(false) * 4);
      } else if (style.sizeBy === 'property' && style.sizeProp) {
        const node = explore.nodes.get(n.id());
        const v = node ? Number(node.props?.[style.sizeProp]) : NaN;
        if (!Number.isNaN(v)) size = Math.max(16, Math.min(80, 20 + v * 2));
      }
      if (!collapsed) {
        n.style({ width: size, height: size });
      }
    });
    // 同步回 hook 的 colorMap（供 LayersPanel 显示图例）。
    if (style.colorBy === 'property') {
      explore.setLayerStyle({ ...style, colorMap: valueColorMap });
    }
  }, [layerStyle, explore, collapsed]);

  // 布局切换（design-v2 §2.1）：layout prop 变化时重跑布局。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    if (layout === 'dagre') ensureDagre();
    const opts: Record<string, unknown> = { animate: true, animationDuration: 400 };
    if (layout === 'fcose') opts.randomize = false;
    if (layout === 'dagre') { opts.rankDir = 'LR'; opts.nodeSep = 40; opts.rankSep = 60; }
    cy.layout({ name: layout, ...opts } as any).run();
  }, [layout]);

  // 时间轴过滤：dimmedVids 变化时设节点 dim/隐藏（design-v2 §1.3 联动）。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      const dimmed = dimmedVids ? dimmedVids.has(n.id()) : false;
      if (dimmed && hideDimmed) {
        n.style({ display: 'none' });
      } else if (dimmed) {
        n.style({ opacity: 0.15, 'overlay-opacity': 0 });
      } else {
        n.style({ opacity: 1, display: 'element' });
      }
    });
    // 边：两端任一 dimmed 则边也 dim
    cy.edges().forEach((e) => {
      const sDim = dimmedVids ? dimmedVids.has(e.source().id()) : false;
      const tDim = dimmedVids ? dimmedVids.has(e.target().id()) : false;
      if ((sDim || tDim) && hideDimmed) {
        e.style({ display: 'none' });
      } else if (sDim || tDim) {
        e.style({ opacity: 0.1, display: 'element' });
      } else {
        e.style({ opacity: 1, display: 'element' });
      }
    });
  }, [dimmedVids, hideDimmed]);

  // 增量同步 nodes/edges 到 cytoscape（diff，不重渲染全图）。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !cyReady) return;

    // 同步节点
    const existingNodeIds = new Set(cy.nodes().map((n) => n.id()));
    const toAdd: cytoscape.ElementDefinition[] = [];
    for (const [rid, node] of explore.nodes) {
      if (!existingNodeIds.has(rid)) {
        toAdd.push({
          group: 'nodes',
          data: {
            id: rid,
            label: String(node.props?.name ?? node.props?.[node.api_name] ?? rid.slice(0, 8)),
            color: colorForType(node.api_name),
            api_name: node.api_name,
          },
        });
      }
    }
    if (toAdd.length > 0) {
      cy.add(toAdd);
      // 增量加节点后跑布局（fcose，randomize=false 保留已有节点位置）。
      cy.layout({ name: 'fcose', animate: true, animationDuration: 300, randomize: false } as any).run();
    }

    // 同步边
    const existingEdgeIds = new Set(cy.edges().map((e) => e.id()));
    const existingNodeIdsForEdges = new Set(cy.nodes().map((n) => n.id()));
    const edgesToAdd: cytoscape.ElementDefinition[] = [];
    for (const [id, edge] of explore.edges) {
      if (existingEdgeIds.has(id)) continue;
      // 防御：端点节点不存在时跳过（水合竞态——边端点可能在 prev 但尚未同步到 cytoscape）
      if (!existingNodeIdsForEdges.has(edge.source) || !existingNodeIdsForEdges.has(edge.target)) {
        continue;
      }
      edgesToAdd.push({
        group: 'edges',
        data: { id, source: edge.source, target: edge.target, label: edge.linkType },
      });
    }
    if (edgesToAdd.length > 0) {
      cy.add(edgesToAdd);
    }

    // 移除已删除的元素
    const currentNodeIds = new Set(explore.nodes.keys());
    cy.nodes().forEach((n) => {
      if (!currentNodeIds.has(n.id())) cy.remove(n);
    });
    const currentEdgeIds = new Set(explore.edges.keys());
    cy.edges().forEach((e) => {
      if (!currentEdgeIds.has(e.id())) cy.remove(e);
    });
  }, [explore.nodes, explore.edges, cyReady]);

  // 选中态同步
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$(':selected').unselect();
    if (explore.selectedVid) {
      cy.getElementById(explore.selectedVid).select();
    }
  }, [explore.selectedVid]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {explore.nodeCount === 0 && !explore.loading && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="text-center text-slate-400">
            <div className="text-4xl mb-2">🔍</div>
            <div>搜索对象开始探索</div>
          </div>
        </div>
      )}
    </div>
  );
}
