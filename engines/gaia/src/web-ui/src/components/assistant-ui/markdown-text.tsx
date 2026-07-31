/**
 * MarkdownText — renders an assistant message text part as markdown.
 *
 * Built on MarkdownTextPrimitive from @assistant-ui/react-markdown with
 * remark-gfm (tables / strikethrough / task lists — AI replies heavily use
 * tables for object-type listings). Memoized per component instance so
 * streaming deltas don't re-parse the whole document each tick.
 *
 * The `aui-md` class + the dot stylesheet provide base block styling
 * (headings, lists, code, tables). Project CSS variables are reused for
 * colors so markdown blocks match the Gaia theme.
 *
 * See .pi/skills/markdown/SKILL.md and assistant-ui.com/llms.txt.
 */
import '@assistant-ui/react-markdown/styles/dot.css';
import { memo } from 'react';
import { MarkdownTextPrimitive } from '@assistant-ui/react-markdown';
import remarkGfm from 'remark-gfm';

export const MarkdownText = memo(function MarkdownText() {
  return <MarkdownTextPrimitive remarkPlugins={[remarkGfm]} className="aui-md" />;
});
