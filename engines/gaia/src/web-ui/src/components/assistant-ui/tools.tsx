/**
 * Tools toolkit for the AG-UI Agent — currently EMPTY by design.
 *
 * v4.0 (ADR-009): the v3.0 demo-tool renderers (suggest_object_types /
 * apply_suggestions) were deleted with the backend demo tools. The 13
 * read-only ontology tools (list_ontologies / get_object / filter_object /
 * aggregate_object / ...) all render via the DEFAULT tool-call renderer in
 * thread.tsx (ToolCallPart) — no per-tool renderer registration is needed.
 *
 * To add a bespoke renderer for a specific tool (e.g. a rich card for
 * `aggregate_object` results), add an entry here keyed by the backend tool
 * name and wire it via `Tools({ toolkit })` in AssistantUiChat. The default
 * renderer remains the fallback for unregistered tools.
 *
 * Keeping the file (rather than deleting) as the documented extension point.
 */
import type { Toolkit } from '@assistant-ui/react';

export const ontologyToolkit: Toolkit = {} satisfies Toolkit;
