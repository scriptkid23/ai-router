import { existsSync } from "node:fs";
import type { AiRouterConfig } from "../config/types.js";
import { browserManager } from "../browser/manager.js";
import { getAdapter, getProviderIds } from "../adapters/registry.js";
import { AiRouterError } from "../errors.js";

export async function handleSessionStatus(
  config: AiRouterConfig,
  args: { providers?: string[] },
): Promise<Record<string, unknown>> {
  const profile_exists = existsSync(config.profileDir);
  if (!profile_exists) {
    return { profile_exists: false, sessions: [] };
  }

  const ids = args.providers?.length ? args.providers : getProviderIds();
  for (const id of ids) {
    if (!getAdapter(id)) {
      throw new AiRouterError("PROVIDER_NOT_FOUND", `Unknown provider "${id}"`);
    }
  }

  return browserManager.withLock(async () => {
    // Headless mode makes ChatGPT/Gemini show a login wall even with valid cookies.
    const context = await browserManager.launchContext(config, { headless: false });
    try {
      const page = context.pages()[0] ?? (await context.newPage());
      const sessions = [];
      for (const id of ids) {
        const adapter = getAdapter(id)!;
        const status = await adapter.checkSession(page);
        sessions.push({
          provider: id,
          status,
          checked_at: new Date().toISOString(),
        });
      }
      return { profile_exists: true, sessions };
    } finally {
      await context.close().catch(() => undefined);
    }
  });
}
