import { appendFileSync, mkdirSync, readdirSync, unlinkSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

type LogLevel = "error" | "warn" | "info" | "debug";

const LEVELS: Record<LogLevel, number> = {
  error: 0,
  warn: 1,
  info: 2,
  debug: 3,
};

const MAX_SESSION_FILES = 20;

function currentLevel(): LogLevel {
  const raw = process.env.AI_ROUTER_LOG_LEVEL ?? "info";
  if (raw === "error" || raw === "warn" || raw === "info" || raw === "debug") {
    return raw;
  }
  return "info";
}

let fileLogPath: string | null = null;
let fileInitFailed = false;

function logDir(): string {
  return process.env.AI_ROUTER_LOG_DIR ?? join(homedir(), ".ai-router", "logs");
}

/** Keep only the newest (MAX_SESSION_FILES - 1) logs so this session brings it to MAX. */
function pruneOldLogs(dir: string): void {
  try {
    const files = readdirSync(dir)
      .filter((f) => /^session-.*\.log$/.test(f))
      .sort(); // ISO timestamps in the name sort chronologically; oldest first
    const excess = files.length - (MAX_SESSION_FILES - 1);
    for (let i = 0; i < excess; i++) {
      try {
        unlinkSync(join(dir, files[i]));
      } catch {
        // best effort — a locked/removed file shouldn't block startup
      }
    }
  } catch {
    // dir unreadable — nothing to prune
  }
}

/** Lazily pick this session's log file path (once per process). null if unavailable. */
function ensureFilePath(): string | null {
  if (fileLogPath !== null || fileInitFailed) return fileLogPath;
  try {
    const dir = logDir();
    mkdirSync(dir, { recursive: true });
    pruneOldLogs(dir);
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    fileLogPath = join(dir, `session-${ts}.log`);
    return fileLogPath;
  } catch {
    fileInitFailed = true;
    return null;
  }
}

/**
 * Absolute path of this session's log file (resolves it on first call), or null
 * if file logging is unavailable.
 */
export function getSessionLogPath(): string | null {
  return ensureFilePath();
}

export function log(
  level: LogLevel,
  message: string,
  fields?: Record<string, unknown>,
): void {
  const parts = [
    `${new Date().toISOString()} [ai-router] level=${level} ${message}`,
  ];
  if (fields) {
    for (const [k, v] of Object.entries(fields)) {
      parts.push(`${k}=${JSON.stringify(v)}`);
    }
  }
  const line = parts.join(" ");

  // File: capture every level (incl. debug), independent of AI_ROUTER_LOG_LEVEL,
  // written synchronously so the last line survives a hang/crash.
  const path = ensureFilePath();
  if (path) {
    try {
      appendFileSync(path, `${line}\n`);
    } catch {
      // best effort — logging must never crash the server
    }
  }

  // stderr: respect the configured level.
  if (LEVELS[level] <= LEVELS[currentLevel()]) {
    console.error(line);
  }
}
