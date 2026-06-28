import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import { saveDebugArtifacts, sleep, submitPrompt, resolvePromptInputMode, resolveTypeDelayMs, waitForStableText } from "./helpers.js";
import { homedir } from "node:os";
import { join } from "node:path";

const BASE_URL = "https://notebooklm.google.com";
const LOGIN_HINT = "accounts.google.com";
const NOTEBOOK_LINK = 'a[href*="/notebook/"]';
const CHAT_INPUT = "textarea.query-box-input";
const CHAT_RESPONSE = ".to-user-container .message-text-content";

export const notebooklmAdapter: ProviderAdapter = {
  id: "notebooklm",
  name: "NotebookLM",
  url: BASE_URL,
  keywords: ["notebooklm", "notebook lm", "@notebooklm"],
  limitations:
    "v1: chat only against an existing notebook; no source upload via MCP",

  async checkSession(page) {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await sleep(2000);
    if (page.url().includes(LOGIN_HINT)) return "logged_out";
    if (await page.locator(NOTEBOOK_LINK).first().isVisible().catch(() => false)) {
      return "logged_in";
    }
    return "unknown";
  },

  async ask(page, prompt, options) {
    try {
      const notebookUrl = options.config.providers.notebooklm.notebook_url;
      if (notebookUrl) {
        await page.goto(notebookUrl, {
          waitUntil: "domcontentloaded",
          timeout: 30_000,
        });
      } else {
        await page.goto(BASE_URL, {
          waitUntil: "domcontentloaded",
          timeout: 30_000,
        });
        await sleep(2000);
        const link = page.locator(NOTEBOOK_LINK).first();
        await link.waitFor({ state: "visible", timeout: 15_000 });
        await link.click();
        await page.waitForLoadState("domcontentloaded");
      }

      if (page.url().includes(LOGIN_HINT)) {
        throw new AiRouterError(
          "SESSION_EXPIRED",
          "NotebookLM session expired. Run login() to re-authenticate.",
        );
      }

      await submitPrompt(
        page,
        CHAT_INPUT,
        prompt,
        resolvePromptInputMode(options.config, options.promptInputMode),
        resolveTypeDelayMs(options.config),
      );
      await page.keyboard.press("Enter");
      return await waitForStableText(page, CHAT_RESPONSE, options.timeoutMs);
    } catch (err) {
      if (err instanceof AiRouterError) throw err;
      const debugDir = join(homedir(), ".ai-router", "debug");
      const shot = await saveDebugArtifacts(page, debugDir).catch(() => "unknown");
      throw new AiRouterError(
        "ADAPTER_ERROR",
        `NotebookLM adapter failed. Screenshot: ${shot}`,
      );
    }
  },
};
