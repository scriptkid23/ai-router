import type { AiRouterConfig } from "../config/types.js";

/** Headless for ask/session_status. Login always uses a visible browser. */
export function isHeadlessAutomation(config: AiRouterConfig): boolean {
  return config.browser.headless ?? true;
}
