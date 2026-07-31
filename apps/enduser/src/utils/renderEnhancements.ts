/**
 * 富媒体后处理——已迁入 @ua/chat 共享包。
 * 本文件保留 enduser 专属的 `enhanceRendered(container, agentId?)` 签名，
 * 内部把 agentId 封装成 imageResolver 闭包（调 manager readAgentFileContent）传给共享版。
 */
import { enhanceRendered as _enhanceRendered, type ImageResolver } from "@ua/chat";
import { readAgentFileContent } from "@/api/endpoints";

export async function enhanceRendered(
  container: HTMLElement | null,
  agentId?: string
): Promise<void> {
  const resolver: ImageResolver | undefined = agentId
    ? (ref) => readAgentFileContent(agentId, ref) as Promise<{ is_image: boolean; content_b64: string }>
    : undefined;
  return _enhanceRendered(container, resolver);
}
