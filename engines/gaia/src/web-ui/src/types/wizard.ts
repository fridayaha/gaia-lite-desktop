/** Shared types for the CreateObjectWizard and OntologyWorkspace. */

import type { DataType, BackingColumnRef } from './index';

/** Cached column from the bound dataset's schema — drives F2 source-column dropdown. */
export interface DatasetSchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
}

/**
 * Property draft in the wizard.
 *
 * `api_name` is NOT present: the backend derives it from `display_name` /
 * `backing_column` (see `core.naming.derive_api_name`). The frontend shows a
 * live preview via `deriveApiName()` but does not submit it.
 *
 * `description` is the LLM-facing business semantics text (Gaia "semantic
 * brain" principle). `is_primary_key` / `is_title_property` are the
 * authoritative key flags; the backend resolves ObjectType.primary_key /
 * title_property from them (no api_name string reference needed).
 */
export interface PropertyDraft {
  display_name: string;
  description: string;
  data_type: DataType;
  is_primary_key: boolean;
  is_title_property: boolean;
  searchable: boolean;
  nullable: boolean;
  source_column?: string;
  backing_mapping?: BackingColumnRef | null;
  /** UI-only: live-derived apiName preview (not submitted). */
  _preview_api_name?: string;
  /** UI-only: whether the user manually edited the preview (stop auto-deriving). */
  _user_overrode_preview?: boolean;
  /** UI-only: accordion expansion state. */
  _expanded?: boolean;
}

export interface LinkDraft {
  display_name: string;
  target_object_type_id: string;
  cardinality: 'ONE' | 'MANY';
  direction: 'OUTGOING';
}

export interface ActionDraft {
  display_name: string;
  description: string;
}

export interface ObjectWizardData {
  /** Object apiName: PascalCase, caller-supplied. Live-derived from display_name,
   *  user may edit. Submitted to backend. */
  api_name: string;
  display_name: string;
  description: string;
  storage_type: 'MANAGED' | 'VIRTUAL';
  /** api_name of the bound dataset. Replaces the old datasource_path string. */
  dataset_api_name: string;
  /** Cached schema of the bound dataset (F1 loads on selection). */
  dataset_schema?: DatasetSchemaColumn[];
  /** MANAGED objects may defer binding ("暂不关联"); VIRTUAL must bind. */
  skip_dataset?: boolean;
  /** Legacy field kept for draft-restoration compatibility with older sessions. */
  datasource_path?: string;
  properties: PropertyDraft[];
  links: LinkDraft[];
  actions: ActionDraft[];
}
