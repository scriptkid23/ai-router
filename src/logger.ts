type LogLevel = "error" | "warn" | "info" | "debug";

const LEVELS: Record<LogLevel, number> = {
  error: 0,
  warn: 1,
  info: 2,
  debug: 3,
};

function currentLevel(): LogLevel {
  const raw = process.env.AI_ROUTER_LOG_LEVEL ?? "info";
  if (raw === "error" || raw === "warn" || raw === "info" || raw === "debug") {
    return raw;
  }
  return "info";
}

export function log(
  level: LogLevel,
  message: string,
  fields?: Record<string, unknown>,
): void {
  if (LEVELS[level] > LEVELS[currentLevel()]) return;
  const parts = [`[ai-router] level=${level} ${message}`];
  if (fields) {
    for (const [k, v] of Object.entries(fields)) {
      parts.push(`${k}=${JSON.stringify(v)}`);
    }
  }
  console.error(parts.join(" "));
}
