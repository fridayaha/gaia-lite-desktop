/**
 * File read/write within workspace directories.
 *
 * All paths are resolved relative to the workspace root and guarded
 * against path traversal attacks.
 */

import {
  readFileSync,
  writeFileSync,
  existsSync,
  mkdirSync,
  statSync,
  readdirSync,
} from "node:fs";
import { join, resolve, normalize, posix, sep } from "node:path";

import { WorkspaceNotFoundError } from "../utils/errors.js";

/** Read result — text content returned as string, binary as Buffer. */
export interface FileReadResult {
  content: string | Buffer;
  isText: boolean;
  size: number;
  path: string; // Relative path within workspace
}

/** Write result. */
export interface FileWriteResult {
  ok: boolean;
  path: string; // Relative path within workspace
  size: number;
}

/** A file tree entry (file or directory) within a workspace. */
export interface FileEntry {
  path: string; // Posix relative path within workspace
  size: number; // Bytes for files, 0 for directories
  isDir: boolean;
  modifiedAt: string; // ISO timestamp
}

/** Directories excluded from the file tree (engine internals / VCS noise). */
const EXCLUDED_DIRS = new Set([".pi", "node_modules"]);
/** Directory/file names excluded from the file tree. */
const isHidden = (name: string): boolean => name.startsWith(".");

/**
 * Compute a workspace-relative path using posix separators (`/`),
 * independent of the host platform's path separator.
 */
function relativePosix(from: string, to: string): string {
  const rel = normalize(to).slice(normalize(from).length);
  return rel.split(sep).filter(Boolean).join("/");
}

export class FileStore {
  constructor(private readonly baseDir: string) {}

  /**
   * Read a file from a workspace.
   * Text files are returned as string; binary files as Buffer.
   */
  readFile(workspaceId: string, filePath: string): FileReadResult {
    const absPath = this.safeResolve(workspaceId, filePath);

    if (!existsSync(absPath)) {
      throw new WorkspaceNotFoundError(
        `File not found: ${filePath} in workspace ${workspaceId}`,
      );
    }

    const stat = statSync(absPath);
    if (stat.isDirectory()) {
      throw new WorkspaceNotFoundError(
        `Path is a directory, not a file: ${filePath}`,
      );
    }

    const raw = readFileSync(absPath);
    const isText = this._isText(raw);

    return {
      content: isText ? raw.toString("utf-8") : raw,
      isText,
      size: stat.size,
      path: filePath,
    };
  }

  /**
   * Read a file as base64 (for image rendering in chat). Returns content_b64
   * for any file (text or binary — SVG is text but still base64-encoded here
   * so the frontend can build a data URL uniformly). `is_image` is decided by
   * extension. Used by the chat imageResolver to render skill-produced charts.
   */
  readFileBase64(workspaceId: string, filePath: string): {
    contentB64: string;
    isImage: boolean;
    mime: string;
    size: number;
    path: string;
  } {
    const absPath = this.safeResolve(workspaceId, filePath);
    if (!existsSync(absPath)) {
      throw new WorkspaceNotFoundError(
        `File not found: ${filePath} in workspace ${workspaceId}`,
      );
    }
    const stat = statSync(absPath);
    if (stat.isDirectory()) {
      throw new WorkspaceNotFoundError(
        `Path is a directory, not a file: ${filePath}`,
      );
    }
    const raw = readFileSync(absPath);
    const mime = mimeFromExt(filePath);
    return {
      contentB64: raw.toString("base64"),
      isImage: mime.startsWith("image/"),
      mime,
      size: stat.size,
      path: filePath,
    };
  }

  /**
   * Write a file to a workspace. Creates parent directories as needed.
   */
  writeFile(
    workspaceId: string,
    filePath: string,
    content: string,
  ): FileWriteResult {
    const absPath = this.safeResolve(workspaceId, filePath);

    // Ensure parent directories exist
    mkdirSync(resolve(absPath, ".."), { recursive: true });

    writeFileSync(absPath, content, "utf-8");
    const size = Buffer.byteLength(content, "utf-8");

    return { ok: true, path: filePath, size };
  }

  /**
   * List the file tree of a workspace (recursive).
   *
   * Returns both file and directory entries with posix relative paths.
   * Excludes engine internals (`.pi/`), VCS/build noise (`node_modules`,
   * `.git`, `.DS_Store`, etc.), so the tree reflects user-visible skill
   * files only. Returns an empty array if the workspace directory does not
   * exist yet (e.g. freshly created, no engine has written to it).
   */
  listFiles(workspaceId: string): FileEntry[] {
    const workspaceDir = join(this.baseDir, workspaceId);
    if (!existsSync(workspaceDir)) {
      return [];
    }

    const entries: FileEntry[] = [];
    const root = resolve(workspaceDir);
    // Explicit stack to avoid deep-recursion stack overflow on wide trees.
    const stack: string[] = [root];

    while (stack.length > 0) {
      const current = stack.pop()!;
      let names: string[];
      try {
        names = readdirSync(current);
      } catch {
        continue; // Unreadable directory — skip rather than fail the whole list
      }

      for (const name of names) {
        // Skip hidden entries (.git, .DS_Store, .pi, ...) and node_modules.
        // `.pi` is also in EXCLUDED_DIRS but isHidden already covers it; the
        // explicit set documents intent and future-proofs non-dot exclusions.
        if (isHidden(name) || EXCLUDED_DIRS.has(name)) {
          continue;
        }

        const abs = join(current, name);
        let stat;
        try {
          stat = statSync(abs);
        } catch {
          continue;
        }

        // Relative posix path from workspace root.
        const rel = relativePosix(root, abs);
        entries.push({
          path: rel,
          size: stat.isDirectory() ? 0 : stat.size,
          isDir: stat.isDirectory(),
          modifiedAt: stat.mtime.toISOString(),
        });

        if (stat.isDirectory()) {
          stack.push(abs);
        }
      }
    }

    return entries;
  }

  /**
   * Safely resolve a file path within a workspace directory.
   * Rejects path traversal (..), absolute paths, and null bytes.
   */
  safeResolve(workspaceId: string, filePath: string): string {
    const workspaceDir = join(this.baseDir, workspaceId);

    // Reject null bytes
    if (filePath.includes("\0")) {
      throw new Error("Null byte in file path");
    }

    // Normalize and reject traversal
    const normalized = normalize(filePath);
    if (normalized.startsWith("..") || posix.isAbsolute(normalized)) {
      throw new Error(`Path traversal rejected: ${filePath}`);
    }

    const absPath = resolve(workspaceDir, normalized);

    // Final check: resolved path must be under workspace dir
    if (!absPath.startsWith(resolve(workspaceDir) + "/")) {
      throw new Error(`Path traversal rejected: ${filePath}`);
    }

    return absPath;
  }

  // ── Private ─────────────────────────────────────────────────

  /**
   * Detect whether a buffer contains text or binary data.
   * Checks the first 8KB for null bytes.
   */
  private _isText(buf: Buffer): boolean {
    const sample = buf.subarray(0, 8192);
    for (let i = 0; i < sample.length; i++) {
      if (sample[i] === 0) return false;
    }
    return true;
  }
}

/** 扩展名 → MIME（仅覆盖常见图片类型，其它一律 application/octet-stream）。 */
const MIME_MAP: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".bmp": "image/bmp",
};

function mimeFromExt(filePath: string): string {
  const ext = filePath.slice(filePath.lastIndexOf(".")).toLowerCase();
  return MIME_MAP[ext] ?? "application/octet-stream";
}
