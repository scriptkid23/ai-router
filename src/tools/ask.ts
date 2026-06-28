import { existsSync } from "node:fs";
import type { AiRouterConfig } from "../config/types.js";
import { browserManager } from "../browser/manager.js";
import { isHeadlessAutomation } from "../browser/headless.js";
import { getAdapter } from "../adapters/registry.js";
import { resolveProvider } from "../router/resolve-provider.js";
import { AiRouterError } from "../errors.js";
import { log } from "../logger.js";

export async function handleAsk(
  config: AiRouterConfig,
  args: {
    prompt?: string;
    provider?: string;
    timeout_ms?: number;
    prompt_input_mode?: "fill" | "type";
  },
): Promise<Record<string, unknown>> {
  const prompt = args.prompt?.trim();
  if (!prompt) {
    throw new AiRouterError("PROMPT_EMPTY", "prompt is required and cannot be empty");
  }

  if (!existsSync(config.profileDir)) {
    throw new AiRouterError("NO_PROFILE", "No profile found. Run login() first.");
  }

  const { provider, routingReason } = resolveProvider(
    args.provider,
    prompt,
    config,
  );
  const adapter = getAdapter(provider);
  if (!adapter) {
    throw new AiRouterError(
      "PROVIDER_NOT_FOUND",
      `No adapter registered for "${provider}"`,
    );
  }

  const started = Date.now();
  const timeoutMs = args.timeout_ms ?? config.timeouts.ask_ms;

  return browserManager.withLock(async () => {
    const context = await browserManager.launchContext(config, {
      headless: isHeadlessAutomation(config),
    });
    try {
      const page = context.pages()[0] ?? (await context.newPage());
      const text = await adapter.ask(page, prompt, {
        timeoutMs,
        config,
        promptInputMode: args.prompt_input_mode,
      });
      const duration_ms = Date.now() - started;
      log("info", "ask complete", { provider, duration_ms, routingReason });
      return {
        text,
        provider,
        routing_reason: routingReason,
        duration_ms,
        url: adapter.url,
      };
    } finally {
      await context.close().catch(() => undefined);
    }
  });
}
