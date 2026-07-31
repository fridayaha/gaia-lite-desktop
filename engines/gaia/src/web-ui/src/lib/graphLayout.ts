/**
 * 图谱布局重排决策（纯函数，从 OntologyGraph 抽离以便单测，且避免在组件文件
 * 里 export 非组件函数破坏 React Fast Refresh）。
 */

/**
 * 何时需要重排：
 * - 容器不可见（0 尺寸）：永不重排（交给 visible effect 在可见后处理），
 *   避免 0 尺寸下跑出无效布局覆盖后续有效布局。
 * - 首次同步且有节点：重排（首次入图必须布局）。
 * - 后续有新节点**或新边**：重排——拓扑变化需重算力导向位置。
 *   特别地，只新增边（节点不变）也必须重排，否则边会加在「无边时排成的网格」上，
 *   节点挤在一起看不到关系（切换本体时的回归 bug）。
 * - 其余（无节点变化、无边变化）：不重排，保留用户拖拽/缩放。
 */
export function shouldRelayout(args: {
  isFirstSync: boolean;
  hasNewNodes: boolean;
  hasNewEdges: boolean;
  containerVisible: boolean;
  nodeCount: number;
}): boolean {
  if (!args.containerVisible) return false;
  if (args.isFirstSync && args.nodeCount > 0) return true;
  return args.hasNewNodes || args.hasNewEdges;
}
