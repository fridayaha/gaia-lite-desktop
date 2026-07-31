/**
 * Skill bootstrap — role-based skill and tool configuration.
 *
 * When spawning an engine instance, the appropriate skill and tool set
 * is determined by the role (dev or debug). This module provides
 * the configuration for each role.
 *
 * - Dev instance: skill-creator skill (preinstalled into .pi/skills/) + full tool set.
 *   The dev persona + scope guardrail is injected in-memory as an appended
 *   system prompt by the worker (see readDevPersona in engine-worker.ts),
 *   not via this config.
 * - Debug instance: user's SKILL.md (mirrored into .pi/skills/user-skill/ by
 *   the worker) + read-only tool set.
 */

export interface BootstrapConfig {
  /** Skill directory name under .pi/skills/ */
  skillName: string;
  /** Tools to include */
  tools: string[];
  /** Tools to exclude */
  excludeTools?: string[];
}

/**
 * Dev instance bootstrap: skill-creator skill + full tool set.
 * (skill-creator is preinstalled into .pi/skills/ at workspace creation;
 *  the worker re-syncs it on spawn and injects the dev persona via
 *  appendSystemPrompt — see readDevPersona in engine-worker.ts.)
 */
export function devBootstrap(): BootstrapConfig {
  return {
    skillName: "skill-creator",
    // clarify：需求澄清/二次确认的结构化问卷工具（customTools 注册，
    // 受 tools allowlist 过滤，须在此列出才对 agent 可用）。
    tools: ["read", "write", "edit", "bash", "grep", "find", "ls", "clarify"],
  };
}

/**
 * Debug instance bootstrap: user's SKILL.md as skill + full execution tool set.
 *
 * 调试会话模拟「真实用户调用技能」，技能本身要能产出文件 / 跑脚本 / 联网（借 agent
 * 的 write/bash 等工具执行），故 debug 与 dev 一样开放执行工具。区别在加载的技能
 * 与 scope（见 APPEND_SYSTEM_DEBUG.md）：debug 必须通过 user-skill 行动、不绕过技能
 * 直接答通用问题。
 *
 * 不含 clarify（那是 skill-creator 的结构化问卷工具，用户技能一般不用）。
 * 沙箱隔离待后续规划（当前 bash 直接在 skill-engine Pod 内执行）。
 */
export function debugBootstrap(): BootstrapConfig {
  return {
    skillName: "user-skill",
    tools: ["read", "write", "edit", "bash", "grep", "find", "ls"],
  };
}
