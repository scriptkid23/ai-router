export type ErrorCode =
  | "BROWSER_BUSY"
  | "LOGIN_IN_PROGRESS"
  | "NO_PROFILE"
  | "SESSION_EXPIRED"
  | "PROVIDER_NOT_FOUND"
  | "TIMEOUT"
  | "ADAPTER_ERROR"
  | "PROMPT_EMPTY";

export class AiRouterError extends Error {
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(`[${code}] ${message}`);
    this.name = "AiRouterError";
    this.code = code;
  }
}

export function formatToolError(err: unknown): string {
  if (err instanceof AiRouterError) return err.message;
  if (err instanceof Error) return `[ADAPTER_ERROR] ${err.message}`;
  return `[ADAPTER_ERROR] ${String(err)}`;
}
