/**
 * Cytoscape 扩展注册单例锁。
 *
 * 单独成模块的原因：
 * 1. cytoscape 官方约束——一个扩展在整个 app 生命周期只能注册一次。
 *    用模块级 Promise 保证注册逻辑只执行，彻底避免 StrictMode 双挂载、
 *    HMR、多实例等场景下的重复注册（重复注册会触发
 *    "already exists in the prototype and can not be overridden" 警告）。
 * 2. 把可变单例状态（registrationPromise）从组件模块中剥离，降低
 *    @vitejs/plugin-react Fast Refresh 的 preamble 负担，避免 HMR 时
 *    因模块顶层可变状态丢失导致 "does not provide an export" 错误。
 * 3. core 扩展挂到 cytoscape 内部私有类 Core.prototype，无法通过
 *    cytoscape.prototype 安全探测，故用单例锁而非原型检查。
 */
let registrationPromise: Promise<typeof import('cytoscape')> | null = null;

/**
 * 注册 cxtmenu + navigator 扩展（仅一次），并返回 cytoscape 默认导出。
 * 并发调用会复用同一个 Promise，不会重复执行 cytoscape.use()。
 */
export async function registerExtensionsOnce(): Promise<typeof import('cytoscape')> {
  if (registrationPromise) return registrationPromise;
  registrationPromise = (async () => {
    const cytoscape = (await import('cytoscape')).default;
    const cxtmenu = (await import('cytoscape-cxtmenu')).default;
    const navigator = (await import('cytoscape-navigator')).default;
    const fcose = (await import('cytoscape-fcose')).default;
    const svg = (await import('cytoscape-svg')).default;
    cytoscape.use(cxtmenu);
    cytoscape.use(navigator);
    cytoscape.use(fcose);
    cytoscape.use(svg);
    return cytoscape;
  })();
  return registrationPromise;
}
