/// <reference types="vite/client" />

// B6: __EDITION__ 是 vite.config.ts define 注入的构建期常量（'full' | 'lite'）。
// Rollup 据此做死代码消除——lite 下被守卫的 GraphExplorePage import 分支整段移除，
// 其 cytoscape/maplibre 依赖随之 tree-shake。
declare const __EDITION__: 'full' | 'lite';
