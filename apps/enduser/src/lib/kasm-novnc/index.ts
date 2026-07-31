// @ts-nocheck
// KasmVNC noVNC fork（@kasmtech/novnc v1.3.0，vendored 到 ./core）。
// core/*.js 是纯 JS（无类型声明）。用 @ts-nocheck 关闭本文件类型检查，避免 vue-tsc 对
// 无声明 .js 导入报 TS7016（云构建环境差异，本地不报）。tsconfig 已加 allowJs 让 TS 能
// 解析 .js 模块。运行时走真实 rfb.js；导出在此收口，调用点 BrowserView 不做构造签名校验。
import RFB from "./core/rfb.js"
import MouseButtonMapper, { XVNC_BUTTONS } from "./core/mousebuttonmapper.js"
export { MouseButtonMapper, XVNC_BUTTONS }
export default RFB
