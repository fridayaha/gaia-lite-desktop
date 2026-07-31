/**
 * 本地联调 mock 中间件 —— A 团队 manager API 未就绪时，按 01-接口契约.md §2 返回契约形 fixture。
 *
 * 仅 dev 生效，由 VITE_USE_MOCK=true 开启（默认关闭，不影响真实后端联调）。
 * 开启方式：在 apps/admin/.env.local 写入 VITE_USE_MOCK=true，或 VITE_USE_MOCK=true pnpm dev。
 * 生产构建不接入（vite.config.ts 仅在 server 插件中按 env 注册）。
 *
 * 覆盖 C1 验收链路：login → 建定义 → 发布 → 建池 → 建实例 → 上线 → deploy → deploy/events(SSE)。
 * 内存态，刷新服务即重置。A/B 就绪后删除本文件 + vite.config.ts 引用即可。
 */
import type { Connect, Plugin } from "vite";

type Handler = (
  req: Connect.IncomingMessage,
  res: any,
  match: RegExpMatchArray
) => void | Promise<void>;

interface Route {
  method: string;
  pattern: RegExp;
  handler: Handler;
}

// ── 内存存储 ────────────────────────────────────────────────

const now = () => new Date().toISOString();
const uid = (p: string) => `${p}-${Math.random().toString(36).slice(2, 10)}`;

interface Def {
  id: string;
  name: string;
  description: string;
  avatar_color: string;
  engine_type: "HERMES" | "OPENCLAW";
  status: "DRAFT" | "PUBLISHED";
  group_id: string;
  group_name: string;
  current_version_id: string | null;
  current_version_no: string | null;
  marketplace_status: "PRIVATE" | "LISTED";
  persona_config: Record<string, any>;
  model_settings: Record<string, any>;
  skill_config: Record<string, any>;
  memory_config: Record<string, any>;
  created_by: string;
  creator_name: string;
  instance_count: number;
  created_at: string;
  updated_at: string;
  published_at: string | null;
  versions: any[];
}

interface Pool {
  id: string;
  name: string;
  description: string;
  group_id: string | null;
  group_name: string | null;
  min_cpu: string;
  max_cpu: string;
  min_memory: string;
  max_memory: string;
  min_replicas: number;
  max_replicas: number;
  max_sessions_per_pod: number;
  auto_recycle: boolean;
  idle_suspend_minutes: number;
  idle_destroy_hours: number;
  created_by: string;
  creator_name: string;
  instance_count: number;
  created_at: string;
  updated_at: string;
}

interface Inst {
  id: string;
  name: string;
  description: string;
  definition_id: string;
  definition_name: string;
  version_id: string | null;
  version_no: string | null;
  resource_pool_id: string;
  resource_pool_name: string;
  engine_type: "HERMES" | "OPENCLAW" | null;
  group_id: string;
  group_name: string;
  status: "DRAFT" | "PUBLISHED" | "OFFLINE";
  litellm_config: Record<string, any>;
  created_by: string;
  creator_name: string;
  created_at: string;
  updated_at: string;
  published_at: string | null;
  deployStatus: string; // PENDING|DEPLOYING|RUNNING|SUSPENDED|FAILED|ARCHIVED
  pod_name: string | null;
  pod_start_time: string | null;
}

const GROUP = { id: "grp-default", name: "默认组" };

const defs: Def[] = [
  {
    id: "def-seed-01",
    name: "通用助手（示例）",
    description: "Seed 定义，用于联调",
    avatar_color: "#386bf5",
    engine_type: "HERMES",
    status: "PUBLISHED",
    group_id: GROUP.id,
    group_name: GROUP.name,
    current_version_id: "ver-seed-01",
    current_version_no: "v1.0.0",
    marketplace_status: "PRIVATE",
    persona_config: { soul: "你是一个通用助手" },
    model_settings: {},
    skill_config: {},
    memory_config: {},
    created_by: "u-seed",
    creator_name: "seed",
    instance_count: 1,
    created_at: now(),
    updated_at: now(),
    published_at: now(),
    versions: [
      {
        id: "ver-seed-01",
        definition_id: "def-seed-01",
        version_no: "v1.0.0",
        persona_config: {},
        model_config: {},
        skill_config: {},
        memory_config: {},
        engine_type: "HERMES",
        change_log: "init",
        created_by: "u-seed",
        created_at: now()
      }
    ]
  }
];

const pools: Pool[] = [
  {
    id: "pool-seed-01",
    name: "默认资源池",
    description: "Seed 池",
    group_id: null,
    group_name: null,
    min_cpu: "500m",
    max_cpu: "2000m",
    min_memory: "512Mi",
    max_memory: "2Gi",
    min_replicas: 1,
    max_replicas: 3,
    max_sessions_per_pod: 50,
    auto_recycle: true,
    idle_suspend_minutes: 30,
    idle_destroy_hours: 24,
    created_by: "u-seed",
    creator_name: "seed",
    instance_count: 1,
    created_at: now(),
    updated_at: now()
  }
];

const instances: Inst[] = [
  {
    id: "inst-seed-01",
    name: "通用助手-生产实例",
    description: "Seed 实例",
    definition_id: "def-seed-01",
    definition_name: "通用助手（示例）",
    version_id: "ver-seed-01",
    version_no: "v1.0.0",
    resource_pool_id: "pool-seed-01",
    resource_pool_name: "默认资源池",
    engine_type: "HERMES",
    group_id: GROUP.id,
    group_name: GROUP.name,
    status: "PUBLISHED",
    litellm_config: {},
    created_by: "u-seed",
    creator_name: "seed",
    created_at: now(),
    updated_at: now(),
    published_at: now(),
    deployStatus: "RUNNING",
    pod_name: "engine-hermes-instseed-0",
    pod_start_time: now()
  }
];

// ── 系统管理内存存储 ────────────────────────────────────────

interface Permission {
  id: string;
  name: string;
  code: string;
  description: string;
  resource_type: string;
}

const permissions: Permission[] = [
  { id: "p-def-r", name: "查看智能体定义", code: "agent_definition:read", description: "", resource_type: "agent_definition" },
  { id: "p-def-w", name: "编辑智能体定义", code: "agent_definition:write", description: "", resource_type: "agent_definition" },
  { id: "p-inst-r", name: "查看实例", code: "agent_instance:read", description: "", resource_type: "agent_instance" },
  { id: "p-inst-w", name: "管理实例", code: "agent_instance:write", description: "", resource_type: "agent_instance" },
  { id: "p-inst-deploy", name: "部署实例", code: "agent_instance:deploy", description: "", resource_type: "agent_instance" },
  { id: "p-pool-r", name: "查看资源池", code: "resource_pool:read", description: "", resource_type: "resource_pool" },
  { id: "p-pool-w", name: "管理资源池", code: "resource_pool:write", description: "", resource_type: "resource_pool" },
  { id: "p-user-r", name: "查看用户", code: "user:read", description: "", resource_type: "user" },
  { id: "p-user-w", name: "管理用户", code: "user:write", description: "", resource_type: "user" },
  { id: "p-role-w", name: "管理角色", code: "role:write", description: "", resource_type: "role" },
  { id: "p-group-w", name: "管理用户组", code: "user_group:write", description: "", resource_type: "user_group" },
  { id: "p-litellm-w", name: "管理模型网关", code: "litellm:write", description: "", resource_type: "litellm" },
  { id: "p-hub-w", name: "管理能力中心", code: "hub:write", description: "", resource_type: "hub" },
  { id: "p-dash-r", name: "查看仪表盘", code: "dashboard:read", description: "", resource_type: "dashboard" }
];

const roles: any[] = [
  {
    id: "role-admin",
    name: "系统管理员",
    description: "拥有全部权限",
    permission_codes: permissions.map(p => p.code),
    user_count: 1,
    created_at: now()
  },
  {
    id: "role-ops",
    name: "运维人员",
    description: "组级运维",
    permission_codes: ["agent_definition:read", "agent_instance:read", "agent_instance:write", "agent_instance:deploy", "resource_pool:read", "dashboard:read"],
    user_count: 0,
    created_at: now()
  },
  {
    id: "role-user",
    name: "终端用户",
    description: "只读使用",
    permission_codes: ["agent_instance:read", "dashboard:read"],
    user_count: 0,
    created_at: now()
  }
];

const users: any[] = [
  { id: "usr-admin", username: "admin", email: "admin@ua.local", is_active: true, roles: ["系统管理员"], created_at: now() },
  { id: "usr-seed", username: "seed", email: "seed@ua.local", is_active: true, roles: ["终端用户"], created_at: now() }
];

const userGroups: any[] = [
  {
    id: GROUP.id,
    name: GROUP.name,
    code: "default",
    description: "默认组",
    member_count: 2,
    created_at: now(),
    members: [
      { id: "usr-admin", username: "admin", email: "admin@ua.local" },
      { id: "usr-seed", username: "seed", email: "seed@ua.local" }
    ]
  }
];

// ── Hub 能力中心内存存储（契约 §5，B 未就绪 mock）──────────

const hubItems: any[] = [
  {
    id: "hub-seed-01",
    name: "通用问答 Agent",
    type: "agent",
    description: "通用问答能力，可挂载知识库",
    status: "published",
    risk_level: "low",
    industry: "通用",
    scenario: "客服/助手",
    created_by: "seed",
    source_type: "preset",
    group_id: GROUP.id,
    created_at: now(),
    updated_at: now()
  },
  {
    id: "hub-seed-02",
    name: "代码审查 Skill",
    type: "skill",
    description: "对 PR 进行代码审查",
    status: "pending_review",
    risk_level: "medium",
    industry: "研发",
    scenario: "DevOps",
    created_by: "seed",
    source_type: "import",
    group_id: GROUP.id,
    created_at: now(),
    updated_at: now()
  }
];

const hubVersions: Record<string, any[]> = {
  "hub-seed-01": [
    { id: "hver-01", hub_item_id: "hub-seed-01", version: "1.0.0", status: "published", risk_level: "low", description: "init", created_by: "seed", created_at: now() }
  ],
  "hub-seed-02": [
    { id: "hver-02", hub_item_id: "hub-seed-02", version: "0.9.0", status: "pending_review", risk_level: "medium", description: "待审核", created_by: "seed", created_at: now() }
  ]
};

const hubScanReports: Record<string, any> = {
  "hub-seed-01": {
    id: "scan-01",
    hub_item_id: "hub-seed-01",
    version_id: "hver-01",
    status: "completed",
    risk_level: "low",
    finding_count: 1,
    findings: [
      { id: "f-1", severity: "low", rule_id: "INFO_LEAK_CHECK", message: "未检测到敏感信息泄露", location: "config.json" }
    ],
    scanned_at: now()
  },
  "hub-seed-02": {
    id: "scan-02",
    hub_item_id: "hub-seed-02",
    version_id: "hver-02",
    status: "completed",
    risk_level: "medium",
    finding_count: 2,
    findings: [
      { id: "f-2", severity: "medium", rule_id: "NET_ACCESS", message: "检测到外网访问权限，需确认白名单", location: "tool.net" },
      { id: "f-3", severity: "low", rule_id: "DEPS_AUDIT", message: "依赖审计通过", location: "requirements.txt" }
    ],
    scanned_at: now()
  }
};

// ── 工具 ────────────────────────────────────────────────────

function readBody(req: Connect.IncomingMessage): Promise<any> {
  return new Promise(resolve => {
    let raw = "";
    req.on("data", c => (raw += c));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function json(res: any, data: any, status = 200) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(data));
}

function paginate(items: any[], req: Connect.IncomingMessage) {
  const u = new URL(req.url || "", "http://localhost");
  const page = Number(u.searchParams.get("page") || 1);
  const page_size = Number(u.searchParams.get("page_size") || u.searchParams.get("pageSize") || 20);
  const start = (page - 1) * page_size;
  return { items: items.slice(start, start + page_size), total: items.length, page, page_size };
}

function deploymentStatusOf(i: Inst) {
  return {
    agent_id: i.id,
    status: i.deployStatus,
    engine_url:
      i.deployStatus === "RUNNING"
        ? `engine-hermes-${i.id.slice(0, 8)}.default.svc.cluster.local:8642`
        : null,
    last_active_at: now(),
    error_message: null,
    pod_name: i.pod_name,
    pod_start_time: i.pod_start_time,
    pod_phase: i.deployStatus === "RUNNING" ? "Running" : null
  };
}

// ── 路由表 ──────────────────────────────────────────────────

const routes: Route[] = [
  // auth
  {
    method: "POST",
    pattern: /^\/api\/manager\/auth\/login$/,
    handler: async (_req, res) => json(res, { access_token: "mock-access-token", refresh_token: "mock-refresh-token", token_type: "bearer" })
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/auth\/refresh$/,
    handler: async (_req, res) => json(res, { access_token: "mock-access-token", refresh_token: "mock-refresh-token", token_type: "bearer" })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/auth\/me$/,
    handler: async (_req, res) =>
      // 前端 welcome 按中文角色名分支（系统管理员/运维人员/终端用户）；
      // 注意：A 落地后若返回英文 role code，需与前端对齐（待向 A 澄清）
      json(res, { username: "admin", nickname: "管理员", roles: ["系统管理员"], avatar: "" })
  },
  // definitions
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-definitions$/,
    handler: async (req, res) => json(res, paginate(defs, req))
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-definitions$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const d: Def = {
        id: uid("def"),
        name: body.name || "未命名定义",
        description: body.description || "",
        avatar_color: body.avatar_color || "#386bf5",
        engine_type: body.engine_type || "HERMES",
        status: "DRAFT",
        group_id: body.group_id || GROUP.id,
        group_name: GROUP.name,
        current_version_id: null,
        current_version_no: null,
        marketplace_status: "PRIVATE",
        persona_config: body.persona_config || {},
        model_settings: body.model_settings || {},
        skill_config: body.skill_config || {},
        memory_config: body.memory_config || {},
        created_by: "u-seed",
        creator_name: "seed",
        instance_count: 0,
        created_at: now(),
        updated_at: now(),
        published_at: null,
        versions: []
      };
      defs.push(d);
      json(res, d);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-definitions\/([^/]+)$/,
    handler: async (req, res, m) => {
      const d = defs.find(x => x.id === m[1]);
      d ? json(res, d) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "PUT",
    pattern: /^\/api\/manager\/agent-definitions\/([^/]+)$/,
    handler: async (req, res, m) => {
      const d = defs.find(x => x.id === m[1]);
      if (!d) return json(res, { detail: "not found" }, 404);
      const body = await readBody(req);
      Object.assign(d, body, { updated_at: now() });
      json(res, d);
    }
  },
  {
    method: "DELETE",
    pattern: /^\/api\/manager\/agent-definitions\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const idx = defs.findIndex(x => x.id === m[1]);
      if (idx >= 0) defs.splice(idx, 1);
      json(res, { ok: true });
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-definitions\/([^/]+)\/publish$/,
    handler: async (req, res, m) => {
      const d = defs.find(x => x.id === m[1]);
      if (!d) return json(res, { detail: "not found" }, 404);
      const body = await readBody(req);
      const vno = `v${d.versions.length + 1}.0.0`;
      const v = {
        id: uid("ver"),
        definition_id: d.id,
        version_no: vno,
        persona_config: d.persona_config,
        model_config: d.model_settings,
        skill_config: d.skill_config,
        memory_config: d.memory_config,
        engine_type: d.engine_type,
        change_log: body.change_log || "",
        created_by: "u-seed",
        created_at: now()
      };
      d.versions.push(v);
      d.status = "PUBLISHED";
      d.current_version_id = v.id;
      d.current_version_no = vno;
      d.published_at = now();
      d.updated_at = now();
      json(res, v);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-definitions\/([^/]+)\/versions$/,
    handler: async (_req, res, m) => {
      const d = defs.find(x => x.id === m[1]);
      json(res, d ? d.versions : []);
    }
  },
  // resource pools
  {
    method: "GET",
    pattern: /^\/api\/manager\/resource-pools$/,
    handler: async (req, res) => json(res, paginate(pools, req))
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/resource-pools$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const p: Pool = {
        id: uid("pool"),
        name: body.name || "未命名池",
        description: body.description || "",
        group_id: body.group_id ?? null,
        group_name: body.group_id ? GROUP.name : null,
        min_cpu: body.min_cpu || "500m",
        max_cpu: body.max_cpu || "2000m",
        min_memory: body.min_memory || "512Mi",
        max_memory: body.max_memory || "2Gi",
        min_replicas: body.min_replicas ?? 1,
        max_replicas: body.max_replicas ?? 3,
        max_sessions_per_pod: body.max_sessions_per_pod ?? 50,
        auto_recycle: body.auto_recycle ?? true,
        idle_suspend_minutes: body.idle_suspend_minutes ?? 30,
        idle_destroy_hours: body.idle_destroy_hours ?? 24,
        created_by: "u-seed",
        creator_name: "seed",
        instance_count: 0,
        created_at: now(),
        updated_at: now()
      };
      pools.push(p);
      json(res, p);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/resource-pools\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const p = pools.find(x => x.id === m[1]);
      p ? json(res, p) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/resource-pools\/([^/]+)\/pods$/,
    handler: async (_req, res, m) => {
      const p = pools.find(x => x.id === m[1]);
      const items = p
        ? [
            {
              name: `engine-hermes-${p.id.slice(0, 8)}-0`,
              node: "k3s-node-1",
              status: "Running",
              cpu: "120m",
              memory: "640Mi",
              restarts: 0,
              age: "1h",
              created_at: now()
            }
          ]
        : [];
      json(res, { items, summary: { running: items.length, stopped: 0, abnormal: 0 } });
    }
  },
  // instances
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances$/,
    handler: async (req, res) => json(res, paginate(instances, req))
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/accessible$/,
    handler: async (_req, res) => json(res, { items: instances, total: instances.length, page: 1, page_size: 20 })
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const d = defs.find(x => x.id === body.definition_id);
      const p = pools.find(x => x.id === body.resource_pool_id);
      const i: Inst = {
        id: uid("inst"),
        name: body.name || "未命名实例",
        description: body.description || "",
        definition_id: body.definition_id,
        definition_name: d?.name || "",
        version_id: body.version_id || d?.current_version_id || null,
        version_no: d?.current_version_no || null,
        resource_pool_id: body.resource_pool_id,
        resource_pool_name: p?.name || "",
        engine_type: d?.engine_type || null,
        group_id: body.group_id || GROUP.id,
        group_name: GROUP.name,
        status: "DRAFT",
        litellm_config: {},
        created_by: "u-seed",
        creator_name: "seed",
        created_at: now(),
        updated_at: now(),
        published_at: null,
        deployStatus: "PENDING",
        pod_name: null,
        pod_start_time: null
      };
      instances.push(i);
      json(res, i);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      i ? json(res, i) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/publish$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      i.status = "PUBLISHED";
      i.published_at = now();
      i.updated_at = now();
      json(res, i);
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/offline$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      i.status = "OFFLINE";
      i.updated_at = now();
      json(res, i);
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/deploy$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      i.deployStatus = "DEPLOYING";
      json(res, { status: "DEPLOYING", message: "deploy submitted" });
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/resume$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      i.deployStatus = "DEPLOYING";
      json(res, { status: "DEPLOYING", message: "resume submitted" });
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/suspend$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      i.deployStatus = "SUSPENDED";
      json(res, { status: "SUSPENDED", message: "suspended" });
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/restart$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      json(res, { status: "RUNNING", message: "restart submitted" });
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/destroy$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      if (!i) return json(res, { detail: "not found" }, 404);
      i.deployStatus = "ARCHIVED";
      i.pod_name = null;
      json(res, { status: "ARCHIVED", message: "destroyed" });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/deployment-status$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      i ? json(res, deploymentStatusOf(i)) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/pods$/,
    handler: async (_req, res, m) => {
      const i = instances.find(x => x.id === m[1]);
      const items =
        i && i.deployStatus === "RUNNING"
          ? [
              {
                name: i.pod_name || `engine-${i.id.slice(0, 8)}-0`,
                node: "k3s-node-1",
                status: "Running",
                cpu: "120m",
                memory: "640Mi",
                restarts: 0,
                age: "1h",
                created_at: i.pod_start_time || now()
              }
            ]
          : [];
      json(res, { items, summary: { running: items.length, stopped: 0, abnormal: 0 } });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/overview$/,
    handler: async (_req, res) =>
      json(res, { conversationCount: 12, totalTokens: 8800, activeUsers: 3, conversationTrend: [] })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/metrics$/,
    handler: async (_req, res) =>
      json(res, { cpu: [], memory: [], requests: [], tokens: { input: [], output: [] } })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/channels$/,
    handler: async (_req, res, m) =>
      json(res, {
        items: [
          {
            id: "ch-wecom",
            instance_id: m[1],
            channel_type: "wecom",
            scope_type: "ALL",
            scope_target_id: null,
            profile_type: "INDEPENDENT",
            enabled: true,
            callback_url: `https://gw.example.com/api/gateway/channel/wecom/${m[1]}/callback`,
            config: {
              corp_id: "ww1234567890",
              secret: "********",
              token: "********",
              encoding_aes_key: "********",
              agent_id: "1000002"
            },
            created_at: now(),
            updated_at: now()
          },
          {
            id: "ch-aibot",
            instance_id: m[1],
            channel_type: "wecom_bot_callback",
            scope_type: "ALL",
            scope_target_id: null,
            profile_type: "INDEPENDENT",
            enabled: true,
            callback_url: `https://gw.example.com/api/gateway/channel/wecom_bot_callback/${m[1]}/callback`,
            config: { token: "********", encoding_aes_key: "********" },
            created_at: now(),
            updated_at: now()
          }
        ],
        total: 2
      })
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/channels$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      json(res, { id: uid("ch"), instance_id: "", channel_type: body.channel_type, scope_type: "ALL", scope_target_id: null, profile_type: "INDEPENDENT", enabled: true, config: body.config || {}, created_at: now(), updated_at: now() });
    }
  },
  // deploy events SSE
  {
    method: "GET",
    pattern: /^\/api\/manager\/agent-instances\/([^/]+)\/deploy\/events$/,
    handler: async (req, res, m) => streamDeployEvents(req, res, m[1])
  },
  // ── Dashboard（契约 §2.6）──────────────────────────────
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/activities$/,
    handler: async (_req, res) =>
      json(res, {
        items: [
          { user: "admin", action: "部署", target: "通用助手-生产实例", time: now(), type: "deploy" },
          { user: "admin", action: "发布", target: "通用助手（示例）", time: now(), type: "publish" },
          { user: "seed", action: "创建", target: "C1测试实例", time: now(), type: "create" }
        ]
      })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/group$/,
    handler: async (_req, res) =>
      json(res, {
        groupName: GROUP.name,
        agentCount: 5,
        memberCount: 3,
        todayConversations: 47,
        monthlyTokens: 880000,
        agentDistribution: [
          { name: "通用助手", value: 3, color: "#386bf5" },
          { name: "代码助手", value: 1, color: "#00a870" },
          { name: "客服助手", value: 1, color: "#f59e0b" }
        ]
      })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/health$/,
    handler: async (_req, res) =>
      json(res, {
        items: [
          { name: "manager", status: "ok", latencyMs: 12 },
          { name: "gateway", status: "ok", latencyMs: 8 },
          { name: "hub", status: "ok", latencyMs: 15 },
          { name: "litellm", status: "ok", latencyMs: 20 }
        ]
      })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/resources$/,
    handler: async (_req, res) =>
      json(res, { cpuUsed: 1200, cpuLimit: 4000, memUsed: 2048, memLimit: 8192, podCount: 3, metricsAvailable: true })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/billing$/,
    handler: async (_req, res) =>
      json(res, { todayTokens: 128000, monthlyTokens: 2800000, monthlyCost: 386.5 })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/instance-status$/,
    handler: async (_req, res) =>
      json(res, {
        items: [
          { name: "运行中", value: 2, color: "#00a870" },
          { name: "已暂停", value: 1, color: "#f59e0b" },
          { name: "已停用", value: 1, color: "#909399" }
        ]
      })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/dashboard\/top-agents$/,
    handler: async (_req, res) =>
      json(res, {
        items: [
          { agent_id: "inst-seed-01", name: "通用助手-生产实例", conversation_count: 128, total_tokens: 880000 },
          { agent_id: "inst-x", name: "代码助手", conversation_count: 64, total_tokens: 420000 }
        ]
      })
  },
  // ── LiteLLM（契约 §2.4）─────────────────────────────────
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/model-groups$/,
    handler: async (_req, res) =>
      json(res, [
        { model_group: "gpt-4o", model: "gpt-4o", provider: "openai" },
        { model_group: "claude-sonnet", model: "claude-3-5-sonnet", provider: "anthropic" },
        { model_group: "deepseek", model: "deepseek-chat", provider: "deepseek" }
      ])
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/models$/,
    handler: async (_req, res) =>
      json(res, [
        { model_name: "gpt-4o", litellm_params: { model: "gpt-4o", api_base: "https://api.openai.com/v1" }, model_info: { provider: "openai" } },
        { model_name: "claude-sonnet", litellm_params: { model: "claude-3-5-sonnet" }, model_info: { provider: "anthropic" } }
      ])
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/litellm\/models$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      json(res, { model_name: body.model_name, litellm_params: { model: body.model }, model_info: {} });
    }
  },
  {
    method: "DELETE",
    pattern: /^\/api\/manager\/litellm\/models\/([^/]+)$/,
    handler: async (_req, res) => json(res, { ok: true })
  },
  {
    method: "PUT",
    pattern: /^\/api\/manager\/litellm\/models\/([^/]+)$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      json(res, { model_name: body.model_name || "updated", litellm_params: { model: body.model }, model_info: {} });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/teams$/,
    handler: async (_req, res) =>
      json(res, [{ group_id: GROUP.id, name: GROUP.name, team_id: "team-default", synced: true }])
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/litellm\/teams\/sync$/,
    handler: async (_req, res) => json(res, { synced: ["team-default"], count: 1 })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/keys$/,
    handler: async (_req, res) =>
      json(res, [
        {
          key_id: "key-01",
          key: "sk-ua-mock-xxxxxxxx",
          key_alias: "默认组-主Key",
          models: ["gpt-4o", "claude-sonnet"],
          team_id: "team-default",
          max_budget: 100,
          budget_duration: "30d",
          rpm_limit: 60,
          tpm_limit: 100000,
          spend: 12.34,
          metadata: { agent_id: "inst-seed-01" }
        }
      ])
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/litellm\/keys$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      json(res, {
        key_id: uid("key"),
        key: "sk-ua-mock-" + Math.random().toString(36).slice(2, 12),
        key_alias: body.key_alias || "新建Key",
        models: body.models || [],
        team_id: "team-default",
        max_budget: body.max_budget ?? null,
        budget_duration: body.budget_duration ?? null,
        rpm_limit: body.rpm_limit ?? null,
        tpm_limit: body.tpm_limit ?? null,
        spend: 0,
        metadata: {}
      });
    }
  },
  {
    method: "PUT",
    pattern: /^\/api\/manager\/litellm\/keys\/([^/]+)$/,
    handler: async (_req, res) => json(res, { ok: true })
  },
  {
    method: "DELETE",
    pattern: /^\/api\/manager\/litellm\/keys\/([^/]+)$/,
    handler: async (_req, res) => json(res, { ok: true })
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/litellm\/keys\/([^/]+)\/(block|unblock)$/,
    handler: async (_req, res) => json(res, { ok: true })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/spend$/,
    handler: async (_req, res) => json(res, { items: [] })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/spend\/summary$/,
    handler: async (_req, res) =>
      json(res, { items: [{ group_id: GROUP.id, name: GROUP.name, team_id: "team-default", total_spend: 12.34 }] })
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/spend\/by-model$/,
    handler: async (_req, res) =>
      json(res, [
        { model: "gpt-4o", total_spend: 8.2 },
        { model: "claude-sonnet", total_spend: 4.14 }
      ])
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/litellm\/spend\/trend$/,
    handler: async (_req, res) => {
      const items = Array.from({ length: 7 }).map((_, i) => ({
        date: `2026-06-${19 + i}`,
        total_spend: Math.round((5 + i * 1.3) * 100) / 100
      }));
      json(res, { items });
    }
  },
  // ── 系统管理：用户/角色/用户组/权限（契约 §2.5）──────────
  {
    method: "GET",
    pattern: /^\/api\/manager\/users$/,
    handler: async (req, res) =>
      json(res, paginate(users, req))
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/users$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const u = {
        id: uid("usr"),
        username: body.username || "newuser",
        email: body.email || "",
        is_active: body.is_active ?? true,
        roles: body.roles || [],
        created_at: now()
      };
      users.push(u);
      json(res, u);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/users\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const u = users.find(x => x.id === m[1]);
      u ? json(res, u) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "PUT",
    pattern: /^\/api\/manager\/users\/([^/]+)$/,
    handler: async (req, res, m) => {
      const u = users.find(x => x.id === m[1]);
      if (!u) return json(res, { detail: "not found" }, 404);
      const body = await readBody(req);
      Object.assign(u, body);
      json(res, u);
    }
  },
  {
    method: "DELETE",
    pattern: /^\/api\/manager\/users\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const idx = users.findIndex(x => x.id === m[1]);
      if (idx >= 0) users.splice(idx, 1);
      json(res, { ok: true });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/roles\/permissions\/all$/,
    handler: async (_req, res) => json(res, permissions)
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/roles$/,
    handler: async (_req, res) => json(res, roles)
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/roles$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const r = {
        id: uid("role"),
        name: body.name || "新角色",
        description: body.description || "",
        permission_codes: (body.permission_ids || []).map((id: string) => permissions.find(p => p.id === id)?.code).filter(Boolean),
        user_count: 0,
        created_at: now()
      };
      roles.push(r);
      json(res, r);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/roles\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const r = roles.find(x => x.id === m[1]);
      r ? json(res, r) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "PUT",
    pattern: /^\/api\/manager\/roles\/([^/]+)$/,
    handler: async (req, res, m) => {
      const r = roles.find(x => x.id === m[1]);
      if (!r) return json(res, { detail: "not found" }, 404);
      const body = await readBody(req);
      if (body.name !== undefined) r.name = body.name;
      if (body.description !== undefined) r.description = body.description;
      if (body.permission_ids) {
        r.permission_codes = body.permission_ids.map((id: string) => permissions.find(p => p.id === id)?.code).filter(Boolean);
      }
      json(res, r);
    }
  },
  {
    method: "DELETE",
    pattern: /^\/api\/manager\/roles\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const idx = roles.findIndex(x => x.id === m[1]);
      if (idx >= 0) roles.splice(idx, 1);
      json(res, { ok: true });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/user-groups$/,
    handler: async (_req, res) => json(res, userGroups)
  },
  {
    method: "POST",
    pattern: /^\/api\/manager\/user-groups$/,
    handler: async (req, res) => {
      const body = await readBody(req);
      const g = {
        id: uid("grp"),
        name: body.name || "新用户组",
        code: body.code || "",
        description: body.description || "",
        member_count: 0,
        created_at: now(),
        members: []
      };
      userGroups.push(g);
      json(res, g);
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/manager\/user-groups\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const g = userGroups.find(x => x.id === m[1]);
      g ? json(res, { ...g, members: g.members || [] }) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "PUT",
    pattern: /^\/api\/manager\/user-groups\/([^/]+)$/,
    handler: async (req, res, m) => {
      const g = userGroups.find(x => x.id === m[1]);
      if (!g) return json(res, { detail: "not found" }, 404);
      const body = await readBody(req);
      Object.assign(g, body);
      if (body.members) g.members = body.members;
      g.member_count = g.members?.length || 0;
      json(res, g);
    }
  },
  {
    method: "DELETE",
    pattern: /^\/api\/manager\/user-groups\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const idx = userGroups.findIndex(x => x.id === m[1]);
      if (idx >= 0) userGroups.splice(idx, 1);
      json(res, { ok: true });
    }
  },
  // ── Hub 能力中心（契约 §5）──────────────────────────────
  {
    method: "GET",
    pattern: /^\/api\/hub\/items$/,
    handler: async (req, res) => {
      const u = new URL(req.url || "", "http://localhost");
      let items = hubItems.slice();
      if (u.searchParams.get("type")) items = items.filter(i => i.type === u.searchParams.get("type"));
      if (u.searchParams.get("status")) items = items.filter(i => i.status === u.searchParams.get("status"));
      if (u.searchParams.get("q")) {
        const q = u.searchParams.get("q")!.toLowerCase();
        items = items.filter(i => i.name.toLowerCase().includes(q));
      }
      json(res, { items, total: items.length });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/hub\/items\/([^/]+)$/,
    handler: async (_req, res, m) => {
      const it = hubItems.find(x => x.id === m[1]);
      it ? json(res, it) : json(res, { detail: "not found" }, 404);
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/hub\/presets\/init$/,
    handler: async (_req, res) => json(res, { created: 3 })
  },
  {
    method: "GET",
    pattern: /^\/api\/hub\/items\/([^/]+)\/versions$/,
    handler: async (_req, res, m) => json(res, hubVersions[m[1]] || [])
  },
  {
    method: "POST",
    pattern: /^\/api\/hub\/items\/([^/]+)\/versions$/,
    handler: async (req, res, m) => {
      const body = await readBody(req);
      const v = {
        id: uid("hver"),
        hub_item_id: m[1],
        version: body.version || "1.0.0",
        status: "draft",
        risk_level: "low",
        description: body.description || "",
        created_by: "admin",
        created_at: now()
      };
      (hubVersions[m[1]] ||= []).push(v);
      json(res, v);
    }
  },
  {
    method: "POST",
    pattern: /^\/api\/hub\/versions\/([^/]+)\/(submit-review|approve|reject|publish)$/,
    handler: async (_req, res, m) => {
      const vid = m[1];
      const action = m[2];
      const nextStatus: Record<string, string> = {
        "submit-review": "pending_review",
        approve: "approved",
        reject: "rejected",
        publish: "published"
      };
      for (const arr of Object.values(hubVersions)) {
        const v = arr.find(x => x.id === vid);
        if (v) v.status = nextStatus[action] || v.status;
      }
      if (action === "publish") {
        for (const arr of Object.values(hubVersions)) {
          const v = arr.find(x => x.id === vid);
          if (v) {
            const it = hubItems.find(i => i.id === v.hub_item_id);
            if (it) {
              it.status = "published";
              it.risk_level = v.risk_level || it.risk_level;
            }
          }
        }
      }
      json(res, { ok: true, status: nextStatus[action] });
    }
  },
  {
    method: "GET",
    pattern: /^\/api\/hub\/items\/([^/]+)\/scan-report$/,
    handler: async (_req, res, m) => {
      const r = hubScanReports[m[1]];
      r ? json(res, r) : json(res, { id: uid("scan"), hub_item_id: m[1], status: "completed", risk_level: "low", finding_count: 0, findings: [], scanned_at: now() });
    }
  },
  // ── 兜底：mock 模式下未匹配的 /api/manager|controller 请求返回空 200，
  //    避免落到真实后端（8002 需鉴权）触发 401 登出。具体路由须在前面上方定义。
  {
    method: "GET",
    pattern: /^\/api\/(manager|controller|hub)\/.*/,
    handler: async (_req, res) => json(res, { items: [], total: 0 })
  },
  {
    method: "POST",
    pattern: /^\/api\/(manager|controller|hub)\/.*/,
    handler: async (_req, res) => json(res, { ok: true })
  },
  {
    method: "PUT",
    pattern: /^\/api\/(manager|controller|hub)\/.*/,
    handler: async (_req, res) => json(res, { ok: true })
  },
  {
    method: "DELETE",
    pattern: /^\/api\/(manager|controller|hub)\/.*/,
    handler: async (_req, res) => json(res, { ok: true })
  }
];

// ── SSE 流：逐步推送 build_image → create_pod → wait_running → health_check → ready ──

function streamDeployEvents(req: Connect.IncomingMessage, res: any, instanceId: string) {
  const i = instances.find(x => x.id === instanceId);
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no"); // nginx 不缓冲（CLAUDE.md SSE 约束）

  const send = (obj: any) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
  const steps = [
    { key: "build_image", label: "构建镜像", delay: 500, progress: 20 },
    { key: "create_pod", label: "创建 Pod", delay: 600, progress: 45 },
    { key: "wait_running", label: "等待 Pod 就绪", delay: 700, progress: 75 },
    { key: "health_check", label: "健康检查", delay: 500, progress: 90 },
    { key: "ready", label: "引擎就绪", delay: 400, progress: 100 }
  ];

  let idx = 0;
  const tick = () => {
    if (req.destroyed || res.writableEnded) return;
    if (idx >= steps.length) {
      if (i) {
        i.deployStatus = "RUNNING";
        i.pod_name = `engine-hermes-${i.id.slice(0, 8)}-0`;
        i.pod_start_time = now();
      }
      send({
        type: "complete",
        step: "ready",
        status: "done",
        progress: 100,
        message: "部署完成",
        pod_name: i?.pod_name || undefined,
        timestamp: now()
      });
      res.write("data: [DONE]\n\n");
      res.end();
      return;
    }
    const s = steps[idx];
    send({ type: "step", step: s.key, label: s.label, status: "running", progress: s.progress, message: `${s.label}…`, timestamp: now() });
    idx++;
    setTimeout(() => {
      send({ type: "step", step: s.key, label: s.label, status: "done", progress: s.progress, timestamp: now() });
      setTimeout(tick, 120);
    }, s.delay);
  };
  // 开场心跳 + 起步
  send({ type: "step", step: "build_image", label: "构建镜像", status: "pending", progress: 5, message: "已接收部署请求", timestamp: now() });
  setTimeout(tick, 200);

  req.on("close", () => {
    /* 客户端断开，无需处理 */
  });
}

// ── 插件 ────────────────────────────────────────────────────

export function mockApiPlugin(env: Record<string, string>): Plugin {
  const enabled = env.VITE_USE_MOCK === "true";
  return {
    name: "unionagents-mock-api",
    configureServer(server) {
      if (!enabled) return;
      server.middlewares.use((req, res, next) => {
        const url = (req.url || "").split("?")[0];
        const method = (req.method || "GET").toUpperCase();
        // 纯 mock 预览：返回空动态路由，handleWholeMenus 用 constantMenus(静态模块) 建菜单
        if (url === "/api/manager/get-async-routes") {
          json(res, { code: 0, data: [] });
          return;
        }
        for (const r of routes) {
          if (r.method !== method) continue;
          const m = url.match(r.pattern);
          if (m) {
            Promise.resolve(r.handler(req, res, m)).catch(err => {
              if (!res.writableEnded) json(res, { detail: String(err) }, 500);
            });
            return;
          }
        }
        next();
      });
    }
  };
}
