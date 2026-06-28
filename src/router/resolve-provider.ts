import type { AiRouterConfig } from "../config/types.js";
import { AiRouterError } from "../errors.js";
import { getProviderIds } from "../adapters/registry.js";

export interface ResolveResult {
  provider: string;
  routingReason: string;
}

export function resolveProvider(
  explicit: string | undefined,
  prompt: string,
  config: AiRouterConfig,
): ResolveResult {
  const allowed = getProviderIds();

  if (explicit) {
    if (!allowed.includes(explicit)) {
      throw new AiRouterError(
        "PROVIDER_NOT_FOUND",
        `Unknown provider "${explicit}". Valid: ${allowed.join(", ")}`,
      );
    }
    return { provider: explicit, routingReason: "explicit" };
  }

  const lower = prompt.toLowerCase();
  for (const [providerId, keywords] of Object.entries(config.routing.keywords)) {
    for (const keyword of keywords) {
      if (lower.includes(keyword.toLowerCase())) {
        return { provider: providerId, routingReason: `keyword:${providerId}` };
      }
    }
  }

  return {
    provider: config.defaultProvider,
    routingReason: `default:${config.defaultProvider}`,
  };
}
