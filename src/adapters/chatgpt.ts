import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import { saveDebugArtifacts, sleep, submitPrompt, resolvePromptInputMode, resolveTypeDelayMs, waitForStableText } from "./helpers.js";
import { homedir } from "node:os";
import { join } from "node:path";

const URL = "https://chatgpt.com";
const INPUT_SELECTORS = [
  "#prompt-textarea",
  'textarea[placeholder*="Message"]',
  'div[contenteditable="true"]',
];
const ASSISTANT_SELECTOR = '[data-message-author-role="assistant"]';
const LOGIN_SELECTOR = 'a:has-text("Log in"), button:has-text("Log in")';

export const chatgptAdapter: ProviderAdapter = {
  id: "chatgpt",
  name: "ChatGPT",
  url: URL,
  keywords: ["chatgpt", "gpt", "@chatgpt"],

  async checkSession(page) {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await sleep(3000);

    for (const sel of INPUT_SELECTORS) {
      if (await page.locator(sel).first().isVisible().catch(() => false)) {
        return "logged_in";
      }
    }

    const loginVisible = await page
      .locator(LOGIN_SELECTOR)
      .first()
      .isVisible()
      .catch(() => false);
    if (loginVisible) {
      return "logged_out";
    }

    return "unknown";
  },

  async ask(page, prompt, options) {
    try {
      await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
      await sleep(2000);
      const session = await chatgptAdapter.checkSession(page);
      if (session === "logged_out") {
        throw new AiRouterError(
          "SESSION_EXPIRED",
          "ChatGPT session expired. Run login() to re-authenticate.",
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
        ASSISTANT_SELECTOR,
        options.timeoutMs,
      );
    } catch (err) {
      if (err instanceof AiRouterError) throw err;
      const debugDir = join(homedir(), ".ai-router", "debug");
      const shot = await saveDebugArtifacts(page, debugDir).catch(() => "unknown");
      throw new AiRouterError(
        "ADAPTER_ERROR",
        `ChatGPT adapter failed. Screenshot: ${shot}`,
      );
    }
  },
};
