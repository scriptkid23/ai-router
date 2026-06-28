import type { Page } from "playwright-core";
import type { AiRouterConfig } from "../config/types.js";
import type { PromptInputMode } from "./helpers.js";

export interface AskOptions {
  timeoutMs: number;
  signal?: AbortSignal;
  config: AiRouterConfig;
  promptInputMode?: PromptInputMode;
}

export type SessionStatus = "logged_in" | "logged_out" | "unknown";

export interface ProviderAdapter {
  id: string;
  name: string;
  url: string;
  keywords: string[];
  limitations?: string;

  checkSession(page: Page): Promise<SessionStatus>;
  ask(page: Page, prompt: string, options: AskOptions): Promise<string>;
}

export interface ProviderInfo {
  id: string;
  name: string;
  url: string;
  keywords: string[];
  limitations?: string;
}
