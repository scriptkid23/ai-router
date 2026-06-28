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
  waitForChatGptConversationNetwork,
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

async function isStopButtonVisible(page: Page): Promise<boolean> {
  for (const sel of STOP_SELECTORS) {
    if (await page.locator(sel).first().isVisible().catch(() => false)) {
      return true;
    }
  }
  return false;
}

const INTERIM_TEXT = /^thinking[\s.…]*$/i;

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
  if (INTERIM_TEXT.test(t)) return true;
  if (/^thought for\b/i.test(t)) return true;
  if (/^đang suy nghĩ/i.test(t)) return true;
  if (/^analyzing[\s.…]*$/i.test(t)) return true;
  return false;
}

/** Read DOM after network stream finished; retry while UI still shows interim labels. */
async function readAssistantTextAfterNetwork(
  page: Page,
  timeoutMs: number,
): Promise<string> {
  const deadline = Date.now() + Math.min(timeoutMs, 30_000);

  while (Date.now() < deadline) {
    if (await isStopButtonVisible(page)) {
      await sleep(300);
      continue;
    }
    const text = await getLastAssistantText(page);
    if (text && !isInterimResponse(text)) {
      return text;
    }
    await sleep(300);
  }

  const fallback = await getLastAssistantText(page);
  if (fallback && !isInterimResponse(fallback)) {
    return fallback;
  }

  throw new AiRouterError(
    "TIMEOUT",
    "ChatGPT network finished but assistant message never appeared in the UI",
  );
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

      await waitForChatGptConversationNetwork(
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
      );

      return await readAssistantTextAfterNetwork(page, options.timeoutMs);
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
