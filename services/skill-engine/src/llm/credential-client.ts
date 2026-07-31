/**
 * credential-client.ts — 运行时向 manager 拉取 LLM 凭证。
 *
 * skill-engine 不再把 LiteLLM master key 放进 pod env，而是启动时（首 spawn）
 * 调 manager 内部端点 `GET /api/manager/internal/skill-engine/litellm-key`
 * （X-Internal-Token 鉴权）拿 {api_key, base_url}，注入到 worker 子进程 env。
 * manager 宕机或 token 错误时返回 null，worker 无 LLM（manager 为核心服务，可接受）。
 */

export interface LlmCredentials {
  apiKey: string;
  baseUrl: string;
}

export class LlmCredentialClient {
  constructor(
    private readonly managerBaseUrl: string,
    private readonly internalToken: string,
  ) {}

  /**
   * Fetch LiteLLM credentials from manager. Returns null on any failure
   * (401, network error, empty key) so the caller can log + degrade.
   */
  async fetchCredentials(): Promise<LlmCredentials | null> {
    try {
      const res = await fetch(
        `${this.managerBaseUrl}/api/manager/internal/skill-engine/litellm-key`,
        { headers: { "X-Internal-Token": this.internalToken } },
      );
      if (!res.ok) {
        console.error(
          `[LlmCredentialClient] manager returned ${res.status} fetching litellm key`,
        );
        return null;
      }
      const body = (await res.json()) as { api_key?: string; base_url?: string };
      if (!body.api_key) {
        console.error("[LlmCredentialClient] manager returned empty api_key");
        return null;
      }
      return { apiKey: body.api_key, baseUrl: body.base_url ?? "" };
    } catch (err) {
      console.error(
        `[LlmCredentialClient] failed to fetch litellm key:`,
        (err as Error).message,
      );
      return null;
    }
  }
}
