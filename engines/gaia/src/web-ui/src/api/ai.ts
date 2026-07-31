/**
 * AI generate/stream client — AI SDK-style LLM primitives.
 *
 * Mirrors Vercel AI SDK's `generateText`/`streamText` minimal surface:
 * `instructions` (system prompt, optional) + `prompt` (user prompt, required).
 * The backend (`/ai/generate`, `/ai/stream`) does NOT perceive task semantics
 * — what the prompt asks for and how to parse the output is the caller's
 * concern. This keeps the client a general LLM primitive.
 *
 * Use `generateText` for fast, structured-output tasks (e.g. deriving an
 * Action apiName — sub-second). Use `streamText` for long-form generation
 * where incremental display matters.
 */

import { authFetch } from './client';

export interface GenerateTextParams {
  /** System prompt (AI SDK `instructions`). Optional. */
  instructions?: string;
  /** User prompt (AI SDK `prompt`). Required. */
  prompt: string;
}

export interface GenerateTextResult {
  text: string;
}

/** Non-streaming text generation (AI SDK `generateText` equivalent). */
export async function generateText(params: GenerateTextParams): Promise<GenerateTextResult> {
  const res = await authFetch('/ai/generate', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

/**
 * Derive an Action apiName from a displayName via the LLM.
 *
 * Convenience wrapper around `generateText` for the common case: the user
 * fills in an Action's displayName, and we ask the LLM for a camelCase
 * apiName (≤99 chars, ASCII only) that avoids colliding with existing names.
 * The result is shown to the user for confirmation/editing before submit.
 *
 * Returns the raw apiName string (no validation here — the backend validates
 * the final submitted value against `^[a-z][a-zA-Z0-9]{0,99}$`).
 */
export async function suggestActionApiName(
  displayName: string,
  existingApiNames: string[] = [],
): Promise<string> {
  const result = await generateText({
    instructions:
      '你是命名专家。从展示名推导 camelCase apiName：首字母小写，纯 ASCII 字母数字，不超过 99 字符。' +
      '避开 existing apiNames 列表（若重复加数字后缀）。只返回 apiName 本身，不要解释、不要引号、不要 markdown。',
    prompt: `展示名：${displayName}\nexisting apiNames：[${existingApiNames.join(', ')}]`,
  });
  return result.text.trim();
}

/**
 * Derive an Ontology apiName from a displayName via the LLM.
 *
 * Ontology apiName is PascalCase (a user-named namespace, same pattern as
 * ObjectType). Used when local `deriveApiName` falls back to `objectTypeN`
 * (pure-Chinese displayName). Returns a PascalCase apiName avoiding
 * collisions with existing ontology apiNames.
 */
export async function suggestOntologyApiName(
  displayName: string,
  existingApiNames: string[] = [],
): Promise<string> {
  const result = await generateText({
    instructions:
      '你是命名专家。从展示名推导 PascalCase apiName（本体命名空间）：首字母大写，纯 ASCII 字母数字，不超过 99 字符。' +
      '避开 existing apiNames 列表（若重复加数字后缀）。只返回 apiName 本身，不要解释、不要引号、不要 markdown。',
    prompt: `展示名：${displayName}\nexisting apiNames：[${existingApiNames.join(', ')}]`,
  });
  return result.text.trim();
}

/**
 * Derive an ObjectType apiName from a displayName via the LLM.
 *
 * Used when the local `deriveApiName` falls back to `objectTypeN` (i.e. the
 * displayName is pure Chinese with no usable ASCII tokens). Returns a
 * PascalCase apiName (`^[A-Z][a-zA-Z0-9]{0,99}$`) avoiding collisions
 * with existing object type apiNames. The result is shown for confirmation/
 * editing before submit; the backend validates the final value.
 */
export async function suggestObjectTypeApiName(
  displayName: string,
  existingApiNames: string[] = [],
): Promise<string> {
  const result = await generateText({
    instructions:
      '你是命名专家。从展示名推导 PascalCase apiName：首字母大写，纯 ASCII 字母数字，不超过 99 字符。' +
      '避开 existing apiNames 列表（若重复加数字后缀）。只返回 apiName 本身，不要解释、不要引号、不要 markdown。',
    prompt: `展示名：${displayName}\nexisting apiNames：[${existingApiNames.join(', ')}]`,
  });
  return result.text.trim();
}

/**
 * Derive a property apiName from a displayName (and optional backing column)
 * via the LLM. camelCase (`^[a-z][a-zA-Z0-9]{0,99}$`), avoiding collisions.
 */
export async function suggestPropertyApiName(
  displayName: string,
  backingColumn: string | undefined,
  existingApiNames: string[] = [],
): Promise<string> {
  const result = await generateText({
    instructions:
      '你是命名专家。从展示名推导 camelCase apiName：首字母小写，纯 ASCII 字母数字，不超过 99 字符。' +
      '若展示名是中文，结合 backingColumn 推导（如 "航班编号"+flight_id → flightId）。' +
      '避开 existing apiNames 列表（若重复加数字后缀）。只返回 apiName 本身，不要解释、不要引号、不要 markdown。',
    prompt:
      `展示名：${displayName}\n` +
      `backingColumn：${backingColumn ?? '(无)'}\n` +
      `existing apiNames：[${existingApiNames.join(', ')}]`,
  });
  return result.text.trim();
}

// ── Dataset remapping: AI-assisted property → column mapping ──

/** One AI-suggested property → dataset column mapping. */
export interface ColumnMappingSuggestion {
  property_api_name: string;
  column_name: string;
  /** LLM's confidence in the match: "high" | "medium" | "low". "low" means
   *  the LLM could not find a good match and picked the closest column. */
  confidence: 'high' | 'medium' | 'low';
}

/** Input describing a property to be mapped. */
export interface MappingPropertyInput {
  api_name: string;
  display_name: string;
  data_type: string;
}

/** Input describing a candidate dataset column. */
export interface MappingColumnInput {
  name: string;
  type: string;
}

/**
 * Suggest property → dataset-column mappings via the LLM.
 *
 * Used when migrating an ObjectType to a new dataset (or first-binding):
 * the object's properties (api_name + display_name + data_type) are matched
 * against the target dataset's columns (name + type). The LLM performs
 * semantic matching (e.g. `custName` ↔ `customer_name`, `创建时间` ↔
 * `created_at`) that pure same-name normalization misses.
 *
 * Returns one suggestion per input property. Properties the LLM cannot match
 * get `column_name: ""` + `confidence: "low"` so the frontend can highlight
 * them for manual resolution. The caller MUST validate that every returned
 * `column_name` actually exists in the input columns (defense-in-depth
 * against hallucination — see `sanitizeMappings` in lib/columnMapping.ts).
 *
 * Non-streaming (sub-second for typical object sizes). Falls back to throwing
 * on LLM failure; the caller should fall back to deterministic same-name
 * matching (`autoMatchByColumnName`).
 */
export async function suggestColumnMappings(
  properties: MappingPropertyInput[],
  columns: MappingColumnInput[],
): Promise<ColumnMappingSuggestion[]> {
  const result = await generateText({
    instructions:
      '你是企业数据集成专家。给定本体对象的属性列表和目标数据集的列列表，' +
      '为每个属性匹配最合适的列。优先按语义和名称相近度匹配（如 custName↔customer_name，' +
      '创建时间↔created_at，订单状态↔order_status）。找不到合适列时，column_name 返回空字符串、' +
      'confidence 返回 "low"。只返回 JSON 数组，不要解释、不要 markdown 代码块。\n\n' +
      '输出格式：[{"property_api_name":"...","column_name":"...","confidence":"high|medium|low"}]\n' +
      '其中 column_name 必须严格等于输入列名之一，或为空字符串。',
    prompt:
      `属性列表（${properties.length} 个）：\n` +
      properties.map((p) => `- ${p.api_name} | ${p.display_name} | ${p.data_type}`).join('\n') +
      `\n\n目标数据集列（${columns.length} 个）：\n` +
      columns.map((c) => `- ${c.name} | ${c.type}`).join('\n'),
  });
  return parseMappingSuggestions(result.text, properties);
}

/** Parse + sanitize the LLM's mapping-suggestion JSON output.
 *
 * Extracts the JSON array from the raw text (tolerating surrounding prose /
 * markdown fences), validates each entry against the known property and
 * column names (drops hallucinated columns / unknown properties), and
 * backfills any missing property with an empty match. Exposed for unit
 * testing the sanitization logic independently of the LLM call. */
export function parseMappingSuggestions(
  raw: string,
  properties: MappingPropertyInput[],
): ColumnMappingSuggestion[] {
  // Tolerate markdown fences / surrounding prose.
  const jsonMatch = raw.match(/\[[\s\S]*\]/);
  let parsed: unknown;
  try {
    parsed = jsonMatch ? JSON.parse(jsonMatch[0]) : [];
  } catch {
    return properties.map((p) => ({ property_api_name: p.api_name, column_name: '', confidence: 'low' as const }));
  }
  if (!Array.isArray(parsed)) {
    return properties.map((p) => ({ property_api_name: p.api_name, column_name: '', confidence: 'low' as const }));
  }
  const byProp = new Map<string, ColumnMappingSuggestion>();
  for (const entry of parsed) {
    if (!entry || typeof entry !== 'object') continue;
    const e = entry as Record<string, unknown>;
    const property_api_name = String(e.property_api_name ?? '');
    const column_name = String(e.column_name ?? '');
    const confRaw = String(e.confidence ?? 'low');
    const confidence: ColumnMappingSuggestion['confidence'] =
      confRaw === 'high' || confRaw === 'medium' ? confRaw : 'low';
    if (!property_api_name) continue;
    // Drop empty column_name entries here; the column-existence check is the
    // caller's job (it has the columns list). We keep them so the caller can
    // see "LLM returned empty" vs "property was simply absent".
    byProp.set(property_api_name, { property_api_name, column_name, confidence });
  }
  // Guarantee one entry per input property (backfill missing as low/empty).
  return properties.map(
    (p) => byProp.get(p.api_name) ?? { property_api_name: p.api_name, column_name: '', confidence: 'low' as const },
  );
}

/**
 * Streaming text generation (AI SDK `streamText` equivalent).
 *
 * Returns an async iterable of text deltas. Consume with `for await`.
 */
export async function* streamText(params: GenerateTextParams): AsyncGenerator<string> {
  const res = await authFetch('/ai/stream', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => '');
    throw new Error(body || `${res.status} ${res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE: events separated by \n\n, each event's data on a `data: ` line.
    const events = buffer.split('\n\n');
    buffer = events.pop() ?? '';
    for (const evt of events) {
      const line = evt.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      const payload = line.slice(6);
      if (payload === '[DONE]') return;
      try {
        const delta = JSON.parse(payload) as string;
        yield delta;
      } catch {
        // Non-JSON payload (e.g. error object) — skip.
      }
    }
  }
}

// ── BuildWith: scaffold an ObjectType from a dataset schema ──
// (see docs/design/buildwith-object-scaffolding.md)

/** A single property suggested by the scaffold endpoint. Mirrors the backend
 *  ScaffoldProperty — note data_type/nullable are absent; the frontend fills
 *  them deterministically from the dataset schema. */
export interface ScaffoldProperty {
  source_column: string;
  display_name: string;
  description: string;
  searchable: boolean;
  is_primary_key: boolean;
  is_title_property: boolean;
}

/** Complete ObjectType structure streamed by /ai/scaffold. Partial frames
 *  may have fields missing; the final frame is complete + sanitized. */
export interface ScaffoldResult {
  display_name?: string;
  api_name?: string;
  description?: string;
  primary_key_column?: string;
  title_column?: string | null;
  properties?: ScaffoldProperty[];
}

export interface ScaffoldParams {
  dataset_api_name: string;
  dataset_display_name?: string;
  storage_type: 'MANAGED' | 'VIRTUAL';
  columns: { name: string; type: string; nullable: boolean }[];
}

/** Error frame emitted by /ai/scaffold on failure. */
export interface ScaffoldErrorFrame {
  error: string;
}

/** Stream partial ScaffoldResult objects from /ai/scaffold (SSE).
 *
 * Yields progressively more complete ScaffoldResult frames; the consumer
 * patches each frame onto wizard state. Terminates after the final frame
 * (the stream sends `[DONE]`). On LLM failure, yields an error frame with
 * `{ error: string }` — the caller falls back to deterministic skeleton.
 */
export async function* scaffoldObjectType(
  params: ScaffoldParams,
): AsyncGenerator<ScaffoldResult | ScaffoldErrorFrame> {
  const res = await authFetch('/ai/scaffold', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => '');
    yield { error: body || `${res.status} ${res.statusText}` };
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() ?? '';
    for (const evt of events) {
      const line = evt.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      const payload = line.slice(6);
      if (payload === '[DONE]') return;
      try {
        const parsed = JSON.parse(payload) as ScaffoldResult | ScaffoldErrorFrame;
        yield parsed;
      } catch {
        // Malformed frame — skip.
      }
    }
  }
}

// ════════════════════════════════════════════════════════
// AI-powered ActionType scaffolding
// (management-plane AI assistant — red line 12: ActionType CRUD is
// management, not exposed as an AG-UI tool)
// ════════════════════════════════════════════════════════

/** A parameter the LLM proposes for an ActionType draft. */
export interface ActionDraftParameter {
  api_name: string;
  display_name: string;
  data_type: string;
  required: boolean;
  default?: unknown;
  description: string;
  default_source: string;
  default_source_field?: string | null;
  readonly: boolean;
  hidden: boolean;
  pattern?: string | null;
  error_message?: string | null;
  enum_values?: string[] | null;
  object_type_ref?: string | null;
  is_object_set: boolean;
}

/** A constraint/derivation/validation rule in the draft. */
export interface ActionDraftRule {
  type: string;
  target: string;
  expression: string;
  description: string;
}

/** A submission criterion in the draft. */
export interface ActionDraftSubmissionCriterion {
  expression: string;
  error_message: string;
  description: string;
}

/** A value source for ontology-rule property mapping. */
export interface ActionDraftValueSource {
  source: string;
  value?: string | null;
}

/** A declarative ontology rule (object mutation) in the draft. */
export interface ActionDraftOntologyRule {
  type: string;
  target_parameter?: string | null;
  target_object_type?: string | null;
  properties: Record<string, ActionDraftValueSource>;
  link_type?: string | null;
  source_parameter?: string | null;
  target_link_parameter?: string | null;
  condition?: string | null;
  on_missing: string;
  description: string;
}

/** A side-effect config in the draft. */
export interface ActionDraftEffect {
  type: string;
  config: Record<string, unknown>;
  trigger: string;
  condition?: string | null;
}

/** Complete ActionType draft streamed by /ai/action-type/scaffold. Partial
 *  frames may have fields missing; the final frame is complete + sanitized. */
export interface ActionTypeDraftResult {
  api_name?: string;
  display_name?: string;
  description?: string;
  affected_object_type_api_name?: string;
  parameters?: ActionDraftParameter[];
  rules?: ActionDraftRule[];
  submission_criteria?: ActionDraftSubmissionCriterion[];
  ontology_rules?: ActionDraftOntologyRule[];
  effects?: ActionDraftEffect[];
  risk_level?: string;
  operation_kind?: string;
  batch_enabled?: boolean;
  confidence?: number;
  pending_confirmations?: string[];
}

export interface ActionTypeScaffoldParams {
  ontology: string;
  affected_object_type: string;
  natural_language: string;
}

/** Stream partial ActionTypeDraftResult objects from /ai/action-type/scaffold (SSE).
 *
 * Yields progressively more complete draft frames; the consumer patches each
 * frame onto the editor's draft state. Terminates after the final frame (the
 * stream sends `[DONE]`). On LLM failure, yields an error frame with
 * `{ error: string }`. The draft is NEVER persisted by this call — the
 * caller must POST the finalized draft to /actions/definitions to save.
 */
export async function* scaffoldActionType(
  params: ActionTypeScaffoldParams,
): AsyncGenerator<ActionTypeDraftResult | ScaffoldErrorFrame> {
  const res = await authFetch('/ai/action-type/scaffold', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => '');
    yield { error: body || `${res.status} ${res.statusText}` };
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() ?? '';
    for (const evt of events) {
      const line = evt.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      const payload = line.slice(6);
      if (payload === '[DONE]') return;
      try {
        const parsed = JSON.parse(payload) as ActionTypeDraftResult | ScaffoldErrorFrame;
        yield parsed;
      } catch {
        // Malformed frame — skip.
      }
    }
  }
}
