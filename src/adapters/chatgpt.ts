import type { Page } from "playwright-core";
import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import {
  saveDebugArtifacts,
  sleep,
  submitPrompt,
  resolvePromptInputMode,
  resolveTypeDelayMs,
} from "./helpers.js";
import {
  installChatGptNetworkHooks,
  waitForChatGptComplete,
} from "./chatgpt-network.js";
import { homedir } from "node:os";
import { join } from "node:path";

const URL = "https://chatgpt.com";
const INPUT_SELECTORS = [
  "#prompt-textarea",
  'textarea[placeholder*="Message"]',
  'div[contenteditable="true"]',
];
const ASSISTANT_MESSAGE = '[data-message-author-role="assistant"]';
const ASSISTANT_CONTENT = `${ASSISTANT_MESSAGE} .markdown, ${ASSISTANT_MESSAGE} .prose`;
const LOGIN_SELECTOR = 'a:has-text("Log in"), button:has-text("Log in")';

const STOP_SELECTORS = [
  'button[data-testid="stop-button"]',
  'button[aria-label*="Stop"]',
  'button[aria-label*="Dừng"]',
];

const GENERATING_SELECTORS = [
  ...STOP_SELECTORS,
  '[data-testid="stop-button"]',
  `${ASSISTANT_MESSAGE} [class*="result-streaming"]`,
  `${ASSISTANT_MESSAGE} .animate-pulse`,
];

async function isUiGenerating(page: Page): Promise<boolean> {
  for (const sel of GENERATING_SELECTORS) {
    if (await page.locator(sel).first().isVisible().catch(() => false)) {
      return true;
    }
  }
  return false;
}

async function getLastAssistantText(page: Page): Promise<string> {
  const content = page.locator(ASSISTANT_CONTENT).last();
  if ((await content.count()) > 0) {
    return ((await content.innerText()) ?? "").trim();
  }
  const msg = page.locator(ASSISTANT_MESSAGE).last();
  if ((await msg.count()) === 0) return "";
  return ((await msg.innerText()) ?? "").trim();
}

function isInterimResponse(text: string): boolean {
  const t = text.trim();
  if (!t) return true;
  if (/^thinking[\s.…]*$/i.test(t)) return true;
  if (/^thought for\b/i.test(t)) return true;
  if (/^đang suy nghĩ/i.test(t)) return true;
  if (/^analyzing[\s.…]*$/i.test(t)) return true;
  // Reasoning summary without body yet (e.g. "Thought for 12 seconds")
  if (/^thought for .+ seconds?\.?$/i.test(t)) return true;
  return false;
}

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
      await installChatGptNetworkHooks(page);
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

      return await waitForChatGptComplete(
        page,
        options.timeoutMs,
        async () => {
          await submitPrompt(
            page,
            inputSelector,
            prompt,
            resolvePromptInputMode(options.config, options.promptInputMode),
            resolveTypeDelayMs(options.config),
          );
          await page.keyboard.press("Enter");
        },
        {
          isUiGenerating: () => isUiGenerating(page),
          readAssistantText: () => getLastAssistantText(page),
          isInterimText: isInterimResponse,
        },
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
