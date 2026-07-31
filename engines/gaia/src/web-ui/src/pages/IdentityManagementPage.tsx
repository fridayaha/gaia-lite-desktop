/**
 * IdentityManagementPage — User/Group management (ADR-016 §8.4, design §7.2).
 *
 * Two tabs (对齐 Palantir Platform Settings > Groups/Users):
 *   - Groups (primary): list + create + detail (members + role assignments +
 *     Project access view). This is the main entry point for permission
 *     management — groups are the sole permission carrier (组授权铁律).
 *   - Users (secondary): list + create (link to Better Auth via subject).
 *
 * Design principles:
 *   - Group-centric: all role grants target groups, never individuals.
 *     Users gain permissions by joining a group (人员异动只调组成员).
 *   - Progressive disclosure: the group detail panel shows members first
 *     (most common action), then role assignments (what this group can do),
 *     then Project access (where this group has roles).
 *   - Inline create forms (toggle on button click) to avoid modal overhead
 *     (consistent with MarkingsManagementPage pattern).
 *   - Permission gate: only role:manage principals can create/modify.
 */

import { useState, useEffect, useCallback } from 'react';
import {
  listUsers,
  createUser,
  listGroups,
  createGroup,
  listGroupMembers,
  addGroupMember,
  removeGroupMember,
  listUserGroups,
  listOrganizations,
  listRoleAssignments,
  deleteRoleAssignment,
  listProjects,
  type User,
  type Group,
  type Organization,
  type RoleAssignment,
  type Project,
} from '../api/permission';
import { useAllowedActions } from '../hooks/useAllowedActions';
import { PermissionGate } from '../components/permission';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';

type Tab = 'groups' | 'users';

export function IdentityManagementPage() {
  const [tab, setTab] = useState<Tab>('groups');
  // role:manage permission — gates all create/modify actions on this page.
  const { decisions } = useAllowedActions('ROLE', ['*']);

  return (
    <main className="p-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-semibold mb-1">身份管理</h1>
      <p className="text-sm text-fg-muted mb-6">
        用户组是权限的唯一载体（组授权铁律）。用户通过加入组获得权限，
        人员异动只需调整组成员，不影响资源权限。
      </p>

      {/* Tab switcher */}
      <div className="flex gap-1 mb-6 border-b border-border">
        <TabButton active={tab === 'groups'} onClick={() => setTab('groups')}>
          👥 用户组
        </TabButton>
        <TabButton active={tab === 'users'} onClick={() => setTab('users')}>
          🧑 用户
        </TabButton>
      </div>

      {tab === 'groups' ? <GroupsTab decisions={decisions} /> : <UsersTab decisions={decisions} />}
    </main>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
        active
          ? 'border-primary text-primary'
          : 'border-transparent text-fg-muted hover:text-fg',
      )}
    >
      {children}
    </button>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Groups tab: list + detail (members + roles + project access)
// ═══════════════════════════════════════════════════════════════════

function GroupsTab({ decisions }: { decisions: Record<string, { allowedActions: string[]; disabledReasons: Record<string, string> }> }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create-group form
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [gs, os] = await Promise.all([listGroups(), listOrganizations()]);
      setGroups(gs);
      setOrgs(os);
      if (gs.length > 0 && !selectedGroupId) {
        setSelectedGroupId(gs[0].id);
      }
    } catch (e) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }, [selectedGroupId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    if (!name.trim() || orgs.length === 0) return;
    setCreating(true);
    setError(null);
    try {
      await createGroup({
        name: name.trim(),
        organization_id: orgs[0].id, // single-tenant: default org
        description: desc.trim(),
      });
      setName('');
      setDesc('');
      setShowForm(false);
      await load();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setCreating(false);
    }
  };

  const selectedGroup = groups.find((g) => g.id === selectedGroupId);

  if (loading) return <div className="text-sm text-fg-muted">加载中…</div>;

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {/* Group list */}
      <div className="md:col-span-1 space-y-2">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold text-fg-muted">用户组 ({groups.length})</h2>
          <PermissionGate action="role:manage" resourceId="*" decisions={decisions} mode="hide">
            <button
              className="btn btn-xs btn-primary"
              onClick={() => setShowForm((v) => !v)}
            >
              {showForm ? '取消' : '+ 新建组'}
            </button>
          </PermissionGate>
        </div>

        {showForm && (
          <div className="mb-3 p-3 rounded border border-border bg-card space-y-2">
            <input
              className="input input-sm w-full"
              placeholder="组名（如 marketing-editors）"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              className="input input-sm w-full"
              placeholder="描述（可选）"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
            <button
              className="btn btn-sm btn-primary w-full"
              disabled={!name.trim() || creating}
              onClick={handleCreate}
            >
              {creating ? '创建中…' : '创建'}
            </button>
          </div>
        )}

        {groups.length === 0 ? (
          <div className="text-xs text-fg-muted p-4 text-center">还没有用户组</div>
        ) : (
          groups.map((g) => (
            <button
              key={g.id}
              onClick={() => setSelectedGroupId(g.id)}
              className={cn(
                'w-full text-left p-3 rounded border transition-colors',
                selectedGroupId === g.id
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-border-hover',
              )}
            >
              <div className="text-sm font-medium truncate">{g.name}</div>
              {g.description && (
                <div className="text-xs text-fg-muted truncate mt-0.5">{g.description}</div>
              )}
            </button>
          ))
        )}
      </div>

      {/* Group detail */}
      <div className="md:col-span-2">
        {selectedGroup ? (
          <GroupDetail group={selectedGroup} decisions={decisions} />
        ) : (
          <div className="text-sm text-fg-muted p-8 text-center">
            选择左侧的用户组查看详情
          </div>
        )}
      </div>

      {error && (
        <div className="md:col-span-3 p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}
    </div>
  );
}

// ── Group detail: members + role assignments + project access ──

function GroupDetail({ group, decisions }: { group: Group; decisions: Record<string, { allowedActions: string[]; disabledReasons: Record<string, string> }> }) {
  const [members, setMembers] = useState<User[]>([]);
  const [allUsers, setAllUsers] = useState<User[]>([]);
  const [assignments, setAssignments] = useState<RoleAssignment[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add-member form
  const [showAddMember, setShowAddMember] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState('');
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [mems, users, assigns, projs] = await Promise.all([
        listGroupMembers(group.id),
        listUsers(),
        listRoleAssignments({ group_id: group.id }),
        listProjects(),
      ]);
      setMembers(mems);
      setAllUsers(users);
      setAssignments(assigns);
      setProjects(projs);
    } catch (e) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }, [group.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleAddMember = async () => {
    if (!selectedUserId) return;
    setAdding(true);
    try {
      await addGroupMember(group.id, selectedUserId);
      setSelectedUserId('');
      setShowAddMember(false);
      await load();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setAdding(false);
    }
  };

  const handleRemoveAssignment = async (id: string) => {
    try {
      await deleteRoleAssignment(id);
      await load();
    } catch (e) {
      setError(formatError(e));
    }
  };

  const handleRemoveMember = async (userId: string) => {
    try {
      await removeGroupMember(group.id, userId);
      await load();
    } catch (e) {
      setError(formatError(e));
    }
  };

  // Users not yet in this group (for the add-member picker)
  const memberIds = new Set(members.map((m) => m.id));
  const availableUsers = allUsers.filter((u) => !memberIds.has(u.id));

  if (loading) return <div className="text-sm text-fg-muted">加载中…</div>;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold">{group.name}</h2>
        {group.description && (
          <p className="text-sm text-fg-muted mt-1">{group.description}</p>
        )}
        <p className="text-xs text-fg-muted mt-2 font-mono">ID: {group.id}</p>
      </div>

      {error && (
        <div className="p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}

      {/* Members section */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold">成员 ({members.length})</h3>
          <PermissionGate action="role:manage" resourceId="*" decisions={decisions} mode="hide">
            <button
              className="btn btn-xs btn-primary"
              onClick={() => setShowAddMember((v) => !v)}
            >
              {showAddMember ? '取消' : '+ 添加成员'}
            </button>
          </PermissionGate>
        </div>

        {showAddMember && (
          <div className="mb-3 p-3 rounded border border-border bg-card flex gap-2">
            <select
              className="input input-sm flex-1"
              value={selectedUserId}
              onChange={(e) => setSelectedUserId(e.target.value)}
            >
              <option value="">选择用户…</option>
              {availableUsers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email} ({u.subject.slice(0, 8)}…)
                </option>
              ))}
            </select>
            <button
              className="btn btn-sm btn-primary"
              disabled={!selectedUserId || adding}
              onClick={handleAddMember}
            >
              {adding ? '添加中…' : '添加'}
            </button>
          </div>
        )}

        {members.length === 0 ? (
          <div className="text-xs text-fg-muted p-4 text-center border border-dashed border-border rounded">
            还没有成员
          </div>
        ) : (
          <div className="space-y-1">
            {members.map((m) => (
              <div
                key={m.id}
                className="flex items-center justify-between p-2 rounded border border-border bg-card"
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{m.email}</div>
                  <div className="text-xs text-fg-muted font-mono">
                    subject: {m.subject.slice(0, 12)}…
                  </div>
                </div>
                {Object.keys(m.attributes || {}).length > 0 && (
                  <div className="flex gap-1 flex-shrink-0">
                    {Object.entries(m.attributes).slice(0, 3).map(([k, v]) => (
                      <span key={k} className="badge badge-xs badge-muted">
                        {k}: {String(v)}
                      </span>
                    ))}
                  </div>
                )}
                <PermissionGate
                  action="role:manage"
                  resourceId="*"
                 
                  decisions={decisions}
                  mode="hide"
                >
                  <button
                    className="btn btn-xs btn-ghost text-error"
                    onClick={() => handleRemoveMember(m.id)}
                  >
                    移除
                  </button>
                </PermissionGate>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Role assignments section (Project access) */}
      <section>
        <h3 className="text-sm font-semibold mb-2">
          角色授予 ({assignments.length})
        </h3>
        <p className="text-xs text-fg-muted mb-3">
          该组在哪些 Project/Space 上有什么角色。在资源详情的「访问控制」tab
          可以为组授予新角色。
        </p>

        {assignments.length === 0 ? (
          <div className="text-xs text-fg-muted p-4 text-center border border-dashed border-border rounded">
            还没有角色授予。前往本体或数据源详情页的「访问控制」tab 授予权限。
          </div>
        ) : (
          <div className="space-y-1">
            {assignments.map((a) => {
              const proj = projects.find((p) => p.id === a.scope_id);
              return (
                <div
                  key={a.id}
                  className="flex items-center justify-between p-2 rounded border border-border bg-card"
                >
                  <div className="flex items-center gap-2">
                    <span className="badge badge-sm badge-primary">{a.role_name}</span>
                    <span className="text-xs text-fg-muted">
                      {a.scope_type === 'GLOBAL'
                        ? '全局'
                        : proj
                          ? `Project: ${proj.display_name}`
                          : a.scope_id?.slice(0, 8) + '…'}
                    </span>
                  </div>
                  <PermissionGate
                    action="role:manage"
                    resourceId="*"
                   
                    decisions={decisions}
                    mode="hide"
                  >
                    <button
                      className="btn btn-xs btn-ghost text-error"
                      onClick={() => handleRemoveAssignment(a.id)}
                    >
                      撤销
                    </button>
                  </PermissionGate>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Users tab: list + create
// ═══════════════════════════════════════════════════════════════════

function UsersTab({ decisions }: { decisions: Record<string, { allowedActions: string[]; disabledReasons: Record<string, string> }> }) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create-user form
  const [showForm, setShowForm] = useState(false);
  const [email, setEmail] = useState('');
  const [subject, setSubject] = useState('');
  const [creating, setCreating] = useState(false);
  // User detail (click a user row to expand their groups)
  const [selectedUser, setSelectedUser] = useState<User | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (e) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    if (!email.trim() || !subject.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await createUser({
        email: email.trim(),
        subject: subject.trim(),
        attributes: {},
      });
      setEmail('');
      setSubject('');
      setShowForm(false);
      await load();
    } catch (e) {
      setError(formatError(e));
    } finally {
      setCreating(false);
    }
  };

  if (loading) return <div className="text-sm text-fg-muted">加载中…</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-fg-muted">用户 ({users.length})</h2>
        <PermissionGate action="role:manage" resourceId="*" decisions={decisions} mode="hide">
          <button
            className="btn btn-xs btn-primary"
            onClick={() => setShowForm((v) => !v)}
          >
            {showForm ? '取消' : '+ 新建用户'}
          </button>
        </PermissionGate>
      </div>

      {showForm && (
        <div className="mb-4 p-4 rounded border border-border bg-card space-y-3">
          <div>
            <label className="text-xs font-medium text-fg-muted block mb-1">
              邮箱 <span className="text-error">*</span>
            </label>
            <input
              className="input input-sm w-full"
              placeholder="user@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-fg-muted block mb-1">
              Subject (Better Auth UID / OIDC sub) <span className="text-error">*</span>
            </label>
            <input
              className="input input-sm w-full font-mono"
              placeholder="从 Better Auth 用户列表复制 UID"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
            <p className="text-xs text-fg-muted mt-1">
              Subject 是认证服务（Better Auth）侧的用户唯一标识，
              用于 JWT 登录后关联到 Gaia 授权属性。
            </p>
          </div>
          <button
            className="btn btn-sm btn-primary"
            disabled={!email.trim() || !subject.trim() || creating}
            onClick={handleCreate}
          >
            {creating ? '创建中…' : '创建'}
          </button>
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}

      {users.length === 0 ? (
        <div className="text-sm text-fg-muted p-8 text-center border border-dashed border-border rounded">
          还没有用户
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-fg-muted">
                <th className="py-2 pr-4">邮箱</th>
                <th className="py-2 pr-4">Subject</th>
                <th className="py-2 pr-4">属性</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr
                  key={u.id}
                  className={cn(
                    'border-b border-border/50 cursor-pointer hover:bg-bg-hover',
                    selectedUser?.id === u.id && 'bg-primary/5',
                  )}
                  onClick={() => setSelectedUser(selectedUser?.id === u.id ? null : u)}
                >
                  <td className="py-2 pr-4 font-medium">{u.email}</td>
                  <td className="py-2 pr-4 font-mono text-xs text-fg-muted">
                    {u.subject.slice(0, 16)}…
                  </td>
                  <td className="py-2 pr-4">
                    {Object.keys(u.attributes || {}).length > 0 ? (
                      <div className="flex gap-1 flex-wrap">
                        {Object.entries(u.attributes).map(([k, v]) => (
                          <span key={k} className="badge badge-xs badge-muted">
                            {k}: {String(v)}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-fg-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedUser && <UserDetailPanel user={selectedUser} />}
    </div>
  );
}

// ── User detail panel (shown when a user row is clicked) ──

function UserDetailPanel({ user }: { user: User }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listUserGroups(user.id)
      .then((gs) => { if (!cancelled) setGroups(gs); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [user.id]);

  return (
    <div className="mt-4 p-4 rounded border border-border bg-card">
      <h3 className="text-sm font-semibold mb-3">
        {user.email} 的用户组 ({groups.length})
      </h3>
      {loading ? (
        <div className="text-xs text-fg-muted">加载中…</div>
      ) : groups.length === 0 ? (
        <div className="text-xs text-fg-muted">
          还不属于任何用户组。前往「用户组」tab 添加成员。
        </div>
      ) : (
        <div className="flex gap-2 flex-wrap">
          {groups.map((g) => (
            <span key={g.id} className="badge badge-sm badge-primary">
              {g.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
