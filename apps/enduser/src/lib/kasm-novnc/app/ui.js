// kasm noVNC 的 core/input/keyboard.js 引用 ../../app/ui.js（kasm web app 耦合），
// 仅用到 UI.rfb.translateShortcuts（短路求值，rfb 为 null 即跳过）。我们只当库用、不跑
// kasm web app，故用最小 stub 替代整个 app/ui.js（其依赖 window 全局 + WebUtil 等）。
export default { rfb: null };
