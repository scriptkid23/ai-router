import type { ProviderAdapter, ProviderInfo } from "./types.js";
import { chatgptAdapter } from "./chatgpt.js";
import { geminiAdapter } from "./gemini.js";
import { notebooklmAdapter } from "./notebooklm.js";

const adapters: ProviderAdapter[] = [
  chatgptAdapter,
  geminiAdapter,
  notebooklmAdapter,
];

export function getProviderIds(): string[] {
  return adapters.map((a) => a.id);
}

export function getAdapter(id: string): ProviderAdapter | undefined {
  return adapters.find((a) => a.id === id);
}

export function listProviderInfo(): ProviderInfo[] {
  return adapters.map(({ id, name, url, keywords, limitations }) => ({
    id,
    name,
    url,
    keywords,
    limitations,
  }));
}
