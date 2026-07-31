/**
 * hub-client.ts — 调用 Hub 服务的 import / scan / item API。
 *
 * Hub 部署为 hub:8003（namespace unionagents）。skill-engine 作为内部服务直接
 * 调用，注入 service 身份头（X-Actor-ID/X-Actor-Type=service/X-Roles=platform_admin，
 * platform_admin 持有 asset:import + scan:run），dev 与 header 认证模式都通过。
 *
 * 通过 testDecorators.hubClient 注入 mock，便于单测（不依赖真实 Hub）。
 */

export interface ImportResult {
  itemId: string;
  versionId: string;
  warnings: Array<Record<string, unknown>>;
}

/** Hub ScanReportRead（只取发布流程关心的字段，其余透传）。 */
export interface ScanReport {
  riskLevel: string;
  summary: Record<string, unknown>;
  findings: Array<Record<string, unknown>>;
  [k: string]: unknown;
}

export interface HubItem {
  currentVersionId: string | null;
  [k: string]: unknown;
}

/** service 身份头，platform_admin 持有 asset:import + scan:run 权限。 */
const SERVICE_HEADERS: Record<string, string> = {
  "X-Actor-ID": "skill-engine",
  "X-Actor-Type": "service",
  "X-Roles": "platform_admin",
  "X-Service-Name": "skill-engine",
};

export class HubClient {
  constructor(private readonly baseUrl: string) {}

  /** 导入 zip 包到 Hub，创建 draft 版本。返回 item_id + version_id。 */
  async importPackage(zip: Buffer): Promise<ImportResult> {
    const form = new FormData();
    form.append("file", new Blob([new Uint8Array(zip)]), "skill.zip");
    const res = await fetch(`${this.baseUrl}/api/hub/imports/package`, {
      method: "POST",
      headers: SERVICE_HEADERS,
      body: form,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new HubError(
        `hub import failed (${res.status})`,
        res.status,
        body,
      );
    }
    const b = body as Record<string, unknown>;
    return {
      itemId: String(b.item_id ?? ""),
      versionId: String(b.version_id ?? ""),
      warnings: Array.isArray(b.warnings) ? (b.warnings as Array<Record<string, unknown>>) : [],
    };
  }

  /** 同步扫描某版本，返回 ScanReport。 */
  async scanVersion(versionId: string, operator = "skill-engine"): Promise<ScanReport> {
    const res = await fetch(
      `${this.baseUrl}/api/hub/versions/${versionId}/scan`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", ...SERVICE_HEADERS },
        body: JSON.stringify({ operator }),
      },
    );
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new HubError(
        `hub scan failed (${res.status})`,
        res.status,
        body,
      );
    }
    const b = body as Record<string, unknown>;
    return {
      riskLevel: String(b.risk_level ?? "unknown"),
      summary: (b.summary as Record<string, unknown>) ?? {},
      findings: Array.isArray(b.findings) ? (b.findings as Array<Record<string, unknown>>) : [],
      ...b,
    };
  }

  /** 取 hub_item（含 current_version_id），用于 scan 端点找最新版本。 */
  async getItem(itemId: string): Promise<HubItem> {
    const res = await fetch(`${this.baseUrl}/api/hub/items/${itemId}`, {
      headers: SERVICE_HEADERS,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new HubError(`hub getItem failed (${res.status})`, res.status, body);
    }
    const b = body as Record<string, unknown>;
    return {
      currentVersionId: b.current_version_id ? String(b.current_version_id) : null,
      ...b,
    };
  }
}

export class HubError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = "HubError";
  }
}
