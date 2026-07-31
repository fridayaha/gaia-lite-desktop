/**
 * Frontend mirror of backend `core/naming.py::derive_api_name`.
 *
 * Keeps the wizard's live apiName preview in sync with what the backend will
 * actually derive, so the user sees the real result before submit. The
 * backend remains the source of truth (it enforces uniqueness via
 * `_derive_unique_api_name`); this module is for display only.
 *
 * Derivation priority (matches backend):
 *   1. displayName satisfies SOURCE_PATTERN → derive from displayName
 *   2. backingColumn satisfies SOURCE_PATTERN → derive from backingColumn
 *   3. fallback `{prefix}{N}` (property0 / ObjectType0 / ...)
 *
 * Pattern validation replaces "has-token" checks: a Chinese displayName's
 * first char is non-ASCII, fails SOURCE_PATTERN, auto-falls-back to
 * backingColumn.
 *
 * Style per entity (Gaia decision, see docs/reference-palantir-ontology.md):
 *   - ObjectType/Ontology apiName: PascalCase  ^[A-Z][a-zA-Z0-9]{0,99}$
 *     (fallback prefix `ObjectType`, capital O, so the placeholder matches
 *     the pattern while the AI suggest kicks in)
 *   - Property/Link/Action/param:     camelCase  ^[a-z][a-zA-Z0-9]{0,99}$
 */

// Source (displayName / backingColumn) pattern: ASCII letter start, allows
// alphanumerics, space, underscore, hyphen. Chinese fails this → fallback.
const SOURCE_PATTERN = /^[A-Za-z][A-Za-z0-9 _-]{0,99}$/;
const WORD_RE = /[A-Za-z][A-Za-z0-9]*/g;

// apiName patterns (Gaia decision).
export const OBJECT_TYPE_API_NAME_PATTERN = /^[A-Z][a-zA-Z0-9]{0,99}$/;
export const PROPERTY_API_NAME_PATTERN = /^[a-z][a-zA-Z0-9]{0,99}$/;

export interface DeriveApiNameOptions {
  /** PascalCase (object type, first letter upper) vs camelCase (property, first word lower). */
  pascal?: boolean;
  /** Backing dataset column; used when displayName is non-ASCII (e.g. Chinese). */
  backingColumn?: string;
  /** Fallback prefix: "property" / "objectType" / "actionType" / "linkType". */
  fallbackPrefix?: string;
  /** Existing same-prefix fallbacks count, to generate a unique N. */
  existingCount?: number;
}

function toApiCase(words: string[], pascal: boolean): string {
  if (words.length === 0) return '';
  const capitalize = (w: string) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  if (pascal) return words.map(capitalize).join('');
  return words[0].toLowerCase() + words.slice(1).map(capitalize).join('');
}

/**
 * Derive an apiName from displayName (display-only preview).
 *
 * Returns the derived name, or a `prefixN` fallback when neither displayName
 * nor backingColumn satisfies SOURCE_PATTERN (e.g. pure Chinese with no
 * backing column). Callers should offer the AI-suggest button when a
 * fallback is returned. PascalCase entities pass `fallbackPrefix: 'ObjectType'`
 * (capital O) so the placeholder itself is pattern-valid.
 */
export function deriveApiName(displayName: string, opts: DeriveApiNameOptions = {}): string {
  const { pascal = false, backingColumn, fallbackPrefix = 'property', existingCount = 0 } = opts;

  if (displayName && SOURCE_PATTERN.test(displayName)) {
    const words = displayName.match(WORD_RE);
    if (words && words.length > 0) return toApiCase(words, pascal);
  }
  if (backingColumn && SOURCE_PATTERN.test(backingColumn)) {
    const words = backingColumn.match(WORD_RE);
    if (words && words.length > 0) return toApiCase(words, pascal);
  }
  return `${fallbackPrefix}${existingCount}`;
}

/**
 * Whether the derived result is a real derivation (vs. a `prefixN` fallback).
 * Used to decide whether to show the "✨ AI 推导" button.
 */
export function isFallbackResult(name: string, fallbackPrefix: string): boolean {
  return new RegExp(`^${fallbackPrefix}\\d+$`).test(name);
}

/** Validate a user-edited apiName against the entity's pattern. */
export function isValidApiName(name: string, pascal: boolean): boolean {
  return (pascal ? OBJECT_TYPE_API_NAME_PATTERN : PROPERTY_API_NAME_PATTERN).test(name);
}
