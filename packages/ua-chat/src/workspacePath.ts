/**
 * 工作区文件路径归一化（迁自 enduser utils/workspacePath.ts）。
 *
 * 模型常以绝对 profile 路径引用工作区内文件，如
 * /opt/data/profiles/<profile>/home/bill_products.png —— 剥掉
 * /opt/data/profiles/<profile>/ 前缀（兼容旧 /profiles/<profile>/ 格式），
 * 归一为相对路径 home/bill_products.png。profile 名被丢弃，manager safe_resolve_ws
 * 永远锚到当前用户自己的 hermes_home，不会跨 profile 泄漏。
 *
 * 与 gateway app.media_resolver.normalize_path 保持同构（不同语言，逻辑须一致）。
 */

/** 是否本地工作区路径引用（非 http/data/blob/file 远程 URL）。 */
export function isLocalImgSrc(src: string | null): boolean {
  return !!src && !/^(https?:|data:|blob:|file:)/i.test(src);
}

/** 把工作区文件路径归一化为相对 profile 根的路径。 */
export function normalizeWorkspacePath(p: string): string {
  let s = (p || "").trim();
  if (s.startsWith("file://")) s = s.slice(7);
  if (s.startsWith("./")) s = s.slice(2);
  if (s.startsWith("/")) {
    const m = s.match(/^\/(?:opt\/data\/)?profiles\/[^/]+\/(.+)$/);
    s = m ? m[1] : s.replace(/^\/+/, "");
  }
  return s;
}
