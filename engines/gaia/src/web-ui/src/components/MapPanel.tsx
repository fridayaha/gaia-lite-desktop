/**
 * MapPanel — 空间分布地图面板（graph-reasoning-frontend-design.md §4.3 Phase 2b）。
 *
 * 基于 MapLibre GL（开源 WebGL 地图，无商业依赖）。
 *
 * 功能：
 *  - 从画布节点中提取带 GEOPOINT 属性的节点，渲染为 marker
 *  - 框选/圈选空间过滤 → spatialFilter API → 高亮命中节点（F6）
 *  - 选中节点与图谱视图联动（点击 marker → explore.setSelectedVid）
 *
 * 节点 location 属性约定：{lon, lat} 或 [lon, lat] 或 GeoJSON Point。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { useGraphExplore } from '../hooks/useGraphExplore';
import { spatialFilter } from '../api/graph';

interface MapPanelProps {
  ontology: string;
  explore: ReturnType<typeof useGraphExplore>;
}

/** 从节点 props 提取 [lon, lat]，兼容多种格式。 */
function extractLocation(props: Record<string, unknown>): [number, number] | null {
  // 优先 location 字段，其次 geo/point/coordinates
  const candidates = ['location', 'geo', 'point', 'coordinates'];
  for (const key of candidates) {
    const v = props[key];
    if (!v) continue;
    if (Array.isArray(v) && v.length >= 2 && typeof v[0] === 'number') {
      return [v[0] as number, v[1] as number];
    }
    if (typeof v === 'object' && v !== null) {
      const obj = v as Record<string, unknown>;
      if (typeof obj.lon === 'number' && typeof obj.lat === 'number') {
        return [obj.lon, obj.lat];
      }
      if (Array.isArray(obj.coordinates) && obj.coordinates.length >= 2) {
        return [obj.coordinates[0] as number, obj.coordinates[1] as number];
      }
    }
    // JSON 字符串：'{"lon":116.4,"lat":39.9}'
    if (typeof v === 'string') {
      try {
        const obj = JSON.parse(v) as Record<string, unknown>;
        if (typeof obj.lon === 'number' && typeof obj.lat === 'number') {
          return [obj.lon, obj.lat];
        }
      } catch { /* ignore */ }
    }
  }
  return null;
}

export function MapPanel({ ontology, explore }: MapPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
  const [boxSelecting, setBoxSelecting] = useState(false);
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());
  const [filtering, setFiltering] = useState(false);
  const [webglError, setWebglError] = useState(false);

  // 检测 WebGL 可用性（headless/无 GPU 环境降级）。
  useEffect(() => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) setWebglError(true);
  }, []);

  // 提取有 location 的节点
  const geoNodes = useMemo(() => {
    const result: Array<{ rid: string; apiName: string; coord: [number, number]; label: string }> = [];
    for (const [rid, node] of explore.nodes) {
      const coord = extractLocation(node.props);
      if (coord) {
        const label = String(node.props.name ?? node.rid);
        result.push({ rid, apiName: node.api_name, coord, label });
      }
    }
    return result;
  }, [explore.nodes]);

  // 初始化地图（无 tile，纯几何容器；用空白底图 + 经纬度网格）
  useEffect(() => {
    if (webglError || !containerRef.current || mapRef.current) return;
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
      container: containerRef.current,
      // 使用高德地图瓦片（国内可访问，无需 token）。多域名 webrd0{1-4} 并行加载。
      style: {
        version: 8,
        sources: {
          'amap-tiles': {
            type: 'raster',
            tiles: [
              'https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
              'https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
              'https://webrd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
              'https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
            ],
            tileSize: 256,
            attribution: '© AutoNavi (高德地图)',
          },
        },
        layers: [
          { id: 'amap-layer', type: 'raster', source: 'amap-tiles' },
        ],
      },
      center: [116.4, 39.9], // 默认北京
      zoom: 4,
    });
    mapRef.current = map;
    } catch {
      // WebGL 初始化失败（headless/无 GPU），降级显示提示。
      setWebglError(true);
    }
    return () => {
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        markersRef.current.clear();
      }
    };
  }, [webglError]);

  // 同步 marker（增量）
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const existing = markersRef.current;
    const seen = new Set<string>();

    for (const gn of geoNodes) {
      seen.add(gn.rid);
      const isHi = highlighted.has(gn.rid);
      const color = isHi ? '#ef4444' : '#3b82f6';
      let marker = existing.get(gn.rid);
      if (!marker) {
        const el = document.createElement('div');
        el.className = 'flex h-5 w-5 cursor-pointer items-center justify-center rounded-full border-2 border-white text-[8px] font-bold text-white shadow-md';
        el.style.backgroundColor = color;
        el.textContent = gn.apiName[0] ?? '?';
        el.title = `${gn.label} (${gn.rid})`;
        el.addEventListener('click', () => explore.setSelectedVid(gn.rid));
        marker = new maplibregl.Marker({ element: el }).setLngLat(gn.coord).addTo(map);
        existing.set(gn.rid, marker);
        (marker as maplibregl.Marker & { _el?: HTMLElement })._el = el;
      } else {
        const el = (marker as maplibregl.Marker & { _el?: HTMLElement })._el;
        if (el) el.style.backgroundColor = color;
      }
    }

    // 移除消失的 marker
    for (const [rid, marker] of existing) {
      if (!seen.has(rid)) {
        marker.remove();
        existing.delete(rid);
      }
    }

    // 自适应视野
    if (geoNodes.length > 0) {
      const bounds = new maplibregl.LngLatBounds();
      for (const gn of geoNodes) bounds.extend(gn.coord);
      map.fitBounds(bounds, { padding: 40, maxZoom: 12 });
    }
  }, [geoNodes, highlighted, explore]);

  // 框选过滤：按住 shift 拖拽画框 → bbox spatialFilter
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !boxSelecting) return;
    let startLngLat: maplibregl.LngLat | null = null;
    let box: HTMLDivElement | null = null;

    const onMouseDown = (e: maplibregl.MapMouseEvent) => {
      startLngLat = e.lngLat;
      box = document.createElement('div');
      box.style.cssText =
        'position:absolute;border:2px solid #3b82f6;background:rgba(59,130,246,0.15);pointer-events:none;z-index:10';
      map.getContainer().appendChild(box);
      map.dragPan.disable();
    };

    const onMouseMove = (e: maplibregl.MapMouseEvent) => {
      if (!startLngLat || !box) return;
      const start = map.project(startLngLat);
      const end = map.project(e.lngLat);
      const minX = Math.min(start.x, end.x);
      const minY = Math.min(start.y, end.y);
      box.style.left = `${minX}px`;
      box.style.top = `${minY}px`;
      box.style.width = `${Math.abs(end.x - start.x)}px`;
      box.style.height = `${Math.abs(end.y - start.y)}px`;
    };

    const onMouseUp = async (e: maplibregl.MapMouseEvent) => {
      if (!startLngLat) return;
      const minLng = Math.min(startLngLat.lng, e.lngLat.lng);
      const maxLng = Math.max(startLngLat.lng, e.lngLat.lng);
      const minLat = Math.min(startLngLat.lat, e.lngLat.lat);
      const maxLat = Math.max(startLngLat.lat, e.lngLat.lat);
      startLngLat = null;
      box?.remove();
      box = null;
      map.dragPan.enable();

      const candidateVids = geoNodes.map((n) => n.rid);
      if (candidateVids.length === 0) {
        setBoxSelecting(false);
        return;
      }
      setFiltering(true);
      try {
        const otName = geoNodes[0]?.apiName ?? '';
        const hits = await spatialFilter(ontology, {
          object_type: otName,
          candidate_rids: candidateVids,
          op: 'withinBoundingBox',
          bbox: [[minLng, minLat], [maxLng, maxLat]],
        });
        setHighlighted(new Set(hits));
      } catch {
        // 静默失败（空间表不存在等）
      } finally {
        setFiltering(false);
        setBoxSelecting(false);
      }
    };

    map.on('mousedown', onMouseDown);
    map.on('mousemove', onMouseMove);
    map.on('mouseup', onMouseUp);
    return () => {
      map.off('mousedown', onMouseDown);
      map.off('mousemove', onMouseMove);
      map.off('mouseup', onMouseUp);
      map.dragPan.enable();
    };
  }, [boxSelecting, geoNodes, ontology]);

  const hasGeo = geoNodes.length > 0;

  if (webglError) {
    // WebGL 不可用（headless/无 GPU 环境）：用简易坐标列表降级展示。
    return (
      <div className="flex h-full w-full flex-col">
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1 text-xs text-amber-700">
          ⚠ 当前环境不支持 WebGL，地图降级为坐标列表（真实浏览器可显示交互地图）
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          {geoNodes.length === 0 ? (
            <div className="text-xs text-slate-400">画布节点暂无 GEOPOINT 属性</div>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-slate-400">
                <tr>
                  <th className="py-1 text-left">rid</th>
                  <th className="text-left">名称</th>
                  <th className="text-right">经度</th>
                  <th className="text-right">纬度</th>
                </tr>
              </thead>
              <tbody>
                {geoNodes.map((n) => (
                  <tr
                    key={n.rid}
                    className={`cursor-pointer border-t border-slate-100 hover:bg-blue-50 ${
                      highlighted.has(n.rid) ? 'bg-red-50' : ''
                    }`}
                    onClick={() => explore.setSelectedVid(n.rid)}
                  >
                    <td className="py-1 font-mono">{n.rid}</td>
                    <td>{n.label}</td>
                    <td className="text-right tabular-nums">{n.coord[0].toFixed(4)}</td>
                    <td className="text-right tabular-nums">{n.coord[1].toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      {/* 工具栏 */}
      <div className="absolute right-2 top-2 z-10 flex flex-col gap-1">
        <button
          onClick={() => setBoxSelecting((v) => !v)}
          className={`rounded border px-2 py-1 text-xs shadow ${
            boxSelecting
              ? 'border-blue-500 bg-blue-500 text-white'
              : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
          }`}
          title="框选过滤（按住拖拽）"
        >
          {boxSelecting ? '框选中…' : '▭ 框选'}
        </button>
        {highlighted.size > 0 && (
          <button
            onClick={() => setHighlighted(new Set())}
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600 shadow hover:bg-slate-50"
          >
            清除高亮（{highlighted.size}）
          </button>
        )}
      </div>
      {filtering && (
        <div className="absolute left-1/2 top-2 z-10 -translate-x-1/2 rounded bg-blue-600 px-3 py-1 text-xs text-white shadow">
          空间过滤中…
        </div>
      )}
      {!hasGeo && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <div className="rounded bg-white/80 px-4 py-2 text-xs text-slate-500 shadow">
            画布节点暂无 GEOPOINT 属性，无法显示地图
          </div>
        </div>
      )}
    </div>
  );
}
