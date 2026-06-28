import type { AiRouterConfig } from "../config/types.js";
import { listProviderInfo } from "../adapters/registry.js";

export function handleListProviders(
  config: AiRouterConfig,
): Record<string, unknown> {
  return {
    providers: listProviderInfo(),
    default_provider: config.defaultProvider,
  };
}
