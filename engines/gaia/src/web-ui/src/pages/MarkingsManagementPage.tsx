// MarkingsManagementPage — marking category/value management (ADR-016 §8.4).
//
// MARKING_ADMIN view: list + create marking categories, values, and grant
// markings to Groups. Separation of duties: only MARKING_ADMIN can define
// markings (design §7.4). Applying markings to resources (PROJECT_OWNER/
// EDITOR) is done in-place at the resource detail Access tab, not here.
//
// Two columns: categories (left) + markings under the selected category (right).
// Create forms are inline (toggle on button click) to avoid modal overhead.

import { useState, useEffect, useCallback } from 'react';
import {
  listMarkingCategories,
  listMarkings,
  createMarkingCategory,
  createMarking,
  grantMarking,
  type MarkingCategory,
  type Marking,
} from '../api/permission';
import { ApiError } from '../api/client';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';

export function MarkingsManagementPage() {
  const [categories, setCategories] = useState<MarkingCategory[]>([]);
  const [markings, setMarkings] = useState<Marking[]>([]);
  const [selectedCat, setSelectedCat] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create-category form
  const [showCatForm, setShowCatForm] = useState(false);
  const [catName, setCatName] = useState('');
  const [catDesc, setCatDesc] = useState('');
  const [creating, setCreating] = useState(false);

  // Create-marking form
  const [showMarkForm, setShowMarkForm] = useState(false);
  const [markName, setMarkName] = useState('');
  const [markDisplay, setMarkDisplay] = useState('');
  const [markDesc, setMarkDesc] = useState('');

  // Grant form (per marking, keyed by marking id)
  const [grantTarget, setGrantTarget] = useState<string | null>(null);
  const [grantGroupId, setGrantGroupId] = useState('');
  const [granting, setGranting] = useState(false);
  const [grantMsg, setGrantMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cats, marks] = await Promise.all([listMarkingCategories(), listMarkings()]);
      setCategories(cats);
      setMarkings(marks);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreateCategory() {
    if (!catName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await createMarkingCategory(catName.trim(), catDesc.trim());
      setCatName('');
      setCatDesc('');
      setShowCatForm(false);
      await load();
    } catch (e) {
      setError(formatError(e, '创建失败'));
    } finally {
      setCreating(false);
    }
  }

  async function handleCreateMarking() {
    if (!markName.trim() || !selectedCat) return;
    setCreating(true);
    setError(null);
    try {
      await createMarking({
        category_id: selectedCat,
        name: markName.trim(),
        display_name: markDisplay.trim() || markName.trim(),
        description: markDesc.trim(),
      });
      setMarkName('');
      setMarkDisplay('');
      setMarkDesc('');
      setShowMarkForm(false);
      await load();
    } catch (e) {
      setError(formatError(e, '创建失败'));
    } finally {
      setCreating(false);
    }
  }

  async function handleGrant(markingId: string) {
    if (!grantGroupId.trim()) return;
    setGranting(true);
    setGrantMsg(null);
    try {
      await grantMarking(markingId, grantGroupId.trim());
      setGrantMsg('授予成功');
      setGrantGroupId('');
    } catch (e) {
      setGrantMsg(formatError(e, '授予失败'));
    } finally {
      setGranting(false);
    }
  }

  const filteredMarkings = selectedCat
    ? markings.filter((m) => m.category_id === selectedCat)
    : markings;

  const selectedCatName = categories.find((c) => c.id === selectedCat)?.name;

  return (
    <main className="p-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-semibold mb-1">标记管理</h1>
      <p className="text-sm text-fg-muted mb-6">
        数据级强制访问控制（MAC）标记定义。系统标记由组织自动派生，不可手动删除。
        授予标记给 Group 后，Group 成员才能访问带该标记的资源。
      </p>

      {error && (
        <div className="mb-4 p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}

      {loading ? (
        <div className="text-sm text-fg-muted">加载中…</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Categories */}
          <div className="md:col-span-1">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-medium">分类</h2>
              <button
                onClick={() => setShowCatForm((v) => !v)}
                className="text-xs text-accent hover:underline"
              >
                {showCatForm ? '取消' : '+ 新建分类'}
              </button>
            </div>

            {showCatForm && (
              <div className="mb-3 p-3 rounded border border-border bg-surface space-y-2">
                <input
                  type="text"
                  value={catName}
                  onChange={(e) => setCatName(e.target.value)}
                  placeholder="分类名（如 密级）"
                  className="w-full px-2 py-1.5 rounded border border-border bg-bg text-sm"
                />
                <input
                  type="text"
                  value={catDesc}
                  onChange={(e) => setCatDesc(e.target.value)}
                  placeholder="描述（可选）"
                  className="w-full px-2 py-1.5 rounded border border-border bg-bg text-sm"
                />
                <button
                  onClick={handleCreateCategory}
                  disabled={creating || !catName.trim()}
                  className="btn btn-xs btn-primary disabled:opacity-50"
                >
                  {creating ? '创建中…' : '创建'}
                </button>
              </div>
            )}

            <div className="space-y-2">
              <button
                onClick={() => setSelectedCat(null)}
                className={cn(
                  'w-full text-left px-3 py-2 rounded border text-sm',
                  selectedCat === null
                    ? 'border-accent bg-accent/10'
                    : 'border-border bg-surface hover:bg-surface/50',
                )}
              >
                全部标记 ({markings.length})
              </button>
              {categories.map((cat) => {
                const count = markings.filter((m) => m.category_id === cat.id).length;
                return (
                  <button
                    key={cat.id}
                    onClick={() => setSelectedCat(cat.id)}
                    className={cn(
                      'w-full text-left px-3 py-2 rounded border text-sm',
                      selectedCat === cat.id
                        ? 'border-accent bg-accent/10'
                        : 'border-border bg-surface hover:bg-surface/50',
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{cat.name}</span>
                      {cat.is_system && (
                        <span className="text-xs px-1.5 py-0.5 rounded bg-info/20 text-info">系统</span>
                      )}
                    </div>
                    <div className="text-xs text-fg-muted mt-0.5">{count} 个标记</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Markings */}
          <div className="md:col-span-2">
            <div className="flex items-center justify-between mb-2">
              <h2 className="text-sm font-medium">
                标记值{selectedCatName && ` · ${selectedCatName}`}
                {selectedCat && ` (${filteredMarkings.length})`}
              </h2>
              {selectedCat && (
                <button
                  onClick={() => setShowMarkForm((v) => !v)}
                  className="text-xs text-accent hover:underline"
                >
                  {showMarkForm ? '取消' : '+ 新建标记'}
                </button>
              )}
            </div>

            {selectedCat && showMarkForm && (
              <div className="mb-3 p-3 rounded border border-border bg-surface space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={markName}
                    onChange={(e) => setMarkName(e.target.value)}
                    placeholder="标记名（如 CONFIDENTIAL）"
                    className="px-2 py-1.5 rounded border border-border bg-bg text-sm"
                  />
                  <input
                    type="text"
                    value={markDisplay}
                    onChange={(e) => setMarkDisplay(e.target.value)}
                    placeholder="显示名（可选）"
                    className="px-2 py-1.5 rounded border border-border bg-bg text-sm"
                  />
                </div>
                <input
                  type="text"
                  value={markDesc}
                  onChange={(e) => setMarkDesc(e.target.value)}
                  placeholder="描述（可选）"
                  className="w-full px-2 py-1.5 rounded border border-border bg-bg text-sm"
                />
                <button
                  onClick={handleCreateMarking}
                  disabled={creating || !markName.trim()}
                  className="btn btn-xs btn-primary disabled:opacity-50"
                >
                  {creating ? '创建中…' : '创建'}
                </button>
              </div>
            )}

            {filteredMarkings.length === 0 ? (
              <div className="p-8 text-center text-sm text-fg-muted border border-dashed border-border rounded-lg">
                暂无标记{selectedCat && '，点击「+ 新建标记」创建'}
              </div>
            ) : (
              <div className="space-y-2">
                {filteredMarkings.map((m) => (
                  <div
                    key={m.id}
                    className="p-3 rounded-lg border border-border bg-surface"
                  >
                    <div className="flex items-start justify-between">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <code className="text-sm font-medium">{m.name}</code>
                          {m.is_system && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-info/20 text-info">系统</span>
                          )}
                        </div>
                        {m.display_name && m.display_name !== m.name && (
                          <div className="text-xs text-fg-muted mt-0.5">{m.display_name}</div>
                        )}
                        {m.description && (
                          <div className="text-xs text-fg-muted mt-1">{m.description}</div>
                        )}
                      </div>
                      <button
                        onClick={() => {
                          setGrantTarget(grantTarget === m.id ? null : m.id);
                          setGrantMsg(null);
                        }}
                        className="text-xs text-accent hover:underline shrink-0"
                      >
                        {grantTarget === m.id ? '收起' : '授予 Group'}
                      </button>
                    </div>

                    {/* Inline grant form */}
                    {grantTarget === m.id && (
                      <div className="mt-2 pt-2 border-t border-border flex flex-wrap items-end gap-2">
                        <label className="block">
                          <span className="text-[11px] text-fg-muted">Group ID</span>
                          <input
                            type="text"
                            value={grantGroupId}
                            onChange={(e) => setGrantGroupId(e.target.value)}
                            placeholder="group-uuid"
                            className="mt-0.5 w-48 px-2 py-1 rounded border border-border bg-bg text-xs"
                          />
                        </label>
                        <button
                          onClick={() => handleGrant(m.id)}
                          disabled={granting || !grantGroupId.trim()}
                          className="btn btn-xs btn-primary disabled:opacity-50"
                        >
                          {granting ? '授予中…' : '授予'}
                        </button>
                        {grantMsg && (
                          <span className="text-[11px] text-fg-muted">{grantMsg}</span>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
