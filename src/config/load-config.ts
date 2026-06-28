import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { defaultConfig } from "./defaults.js";
import type { AiRouterConfig } from "./types.js";

export function getConfigDir(override?: string): string {
  return override ?? join(homedir(), ".ai-router");
}

function shallowMerge(
  base: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const out = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      typeof base[key] === "object" &&
      base[key] !== null &&
      !Array.isArray(base[key])
    ) {
      out[key] = shallowMerge(
        base[key] as Record<string, unknown>,
        value as Record<string, unknown>,
      );
    } else if (value !== undefined) {
      out[key] = value;
    }
  }
  return out;
}

function shallowMergeConfig(
  base: AiRouterConfig,
  patch: Record<string, unknown>,
): AiRouterConfig {
  const out = { ...base } as Record<string, unknown>;
  for (const [key, value] of Object.entries(patch)) {
    const baseValue = base[key as keyof AiRouterConfig];
    if (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      typeof baseValue === "object" &&
      baseValue !== null &&
      !Array.isArray(baseValue)
    ) {
      out[key] = shallowMerge(
        baseValue as Record<string, unknown>,
        value as Record<string, unknown>,
      );
    } else if (value !== undefined) {
      out[key] = value;
    }
  }
  return out as unknown as AiRouterConfig;
}

export function loadConfig(configDir?: string): AiRouterConfig {
  const dir = getConfigDir(configDir);
  const configPath = join(dir, "config.json");
  let config = defaultConfig();

  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  if (!existsSync(configPath)) {
    writeFileSync(configPath, JSON.stringify(config, null, 2));
  } else {
    const raw = readFileSync(configPath, "utf8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    config = shallowMergeConfig(config, parsed);
  }

  if (process.env.AI_ROUTER_DEFAULT_PROVIDER) {
    config.defaultProvider = process.env.AI_ROUTER_DEFAULT_PROVIDER;
  }
  if (process.env.AI_ROUTER_PROFILE_DIR) {
    config.profileDir = process.env.AI_ROUTER_PROFILE_DIR;
  }
  if (process.env.AI_ROUTER_PORT) {
    config.server.port = Number(process.env.AI_ROUTER_PORT);
  }
  if (process.env.AI_ROUTER_HOST) {
    config.server.host = process.env.AI_ROUTER_HOST;
  }
  if (process.env.AI_ROUTER_HEADLESS !== undefined) {
    config.browser.headless =
      process.env.AI_ROUTER_HEADLESS === "1" ||
      process.env.AI_ROUTER_HEADLESS.toLowerCase() === "true";
  }

  return config;
}
