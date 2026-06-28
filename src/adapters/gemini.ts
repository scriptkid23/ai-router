import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import { saveDebugArtifacts, sleep, submitPrompt, resolvePromptInputMode, resolveTypeDelayMs, waitForStableText } from "./helpers.js";
import { homedir } from "node:os";
import { join } from "node:path";

const URL = "https://gemini.google.com/app";
const INPUT_SELECTORS = [
  "rich-textarea div[contenteditable=true]",
  'div[contenteditable="true"][aria-label]',
  "textarea",
];
const RESPONSE_SELECTOR =
  ".model-response-text, [data-message-id], message-content";
const LOGIN_HINT = "accounts.google.com";

export const geminiAdapter: ProviderAdapter = {
  id: "gemini",
  name: "Gemini",
  url: URL,
  keywords: ["gemini", "@gemini", "hỏi gemini"],

  async checkSession(page) {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await sleep(2000);
    if (page.url().includes(LOGIN_HINT)) return "logged_out";
    for (const sel of INPUT_SELECTORS) {
      if (await page.locator(sel).first().isVisible().catch(() => false)) {
        return "logged_in";
      }
    }
    return "unknown";
  },

  async ask(page, prompt, options) {
    try {
      await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
      await sleep(2000);
      if (page.url().includes(LOGIN_HINT)) {
        throw new AiRouterError(
          "SESSION_EXPIRED",
          "Gemini session expired. Run login() to re-authenticate.",
        );
      }

      let inputSelector = INPUT_SELECTORS[0];
      for (const sel of INPUT_SELECTORS) {
        if (await page.locator(sel).first().isVisible().catch(() => false)) {
          inputSelector = sel;
          break;
        }
      }

      await submitPrompt(
        page,
        inputSelector,
        prompt,
        resolvePromptInputMode(options.config, options.promptInputMode),
        resolveTypeDelayMs(options.config),
      );
      await page.keyboard.press("Enter");
      return await waitForStableText(
        page,
        RESPONSE_SELECTOR,
        options.timeoutMs,
      );
    } catch (err) {
      if (err instanceof AiRouterError) throw err;
      const debugDir = join(homedir(), ".ai-router", "debug");
      const shot = await saveDebugArtifacts(page, debugDir).catch(() => "unknown");
      throw new AiRouterError(
        "ADAPTER_ERROR",
        `Gemini adapter failed. Screenshot: ${shot}`,
      );
    }
  },
};
