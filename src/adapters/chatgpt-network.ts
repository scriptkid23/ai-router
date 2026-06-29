import type { Page, Response as PwResponse } from "playwright-core";
import { AiRouterError } from "../errors.js";
import { sleep } from "./helpers.js";

declare global {
  interface Window {
    __aiRouterChatGpt?: {
      inFlight: number;
      completed: number;
      patched: boolean;
    };
  }
}

const CONVERSATION_URL_RE =
  /\/backend-api\/(f\/)?conversation|\/backend-anon\/(f\/)?conversation/;

const FETCH_PATCH_SOURCE = `
(() => {
  if (window.__aiRouterChatGpt?.patched) return;
  window.__aiRouterChatGpt = { inFlight: 0, completed: 0, patched: true };
  const state = window.__aiRouterChatGpt;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    const isConversation =
      method === "POST" &&
      /\\/backend-api\\/(f\\/)?conversation|\\/backend-anon\\/(f\\/)?conversation/.test(url);
    if (!isConversation) return originalFetch(input, init);
    state.inFlight++;
    try {
      const response = await originalFetch(input, init);
      void response.clone().text().finally(() => {
        state.inFlight--;
        state.completed++;
      });
      return response;
    } catch (err) {
      state.inFlight--;
      throw err;
    }
  };
})();
`;

export function isChatGptConversationResponse(
  url: string,
  method: string,
): boolean {
  return method.toUpperCase() === "POST" && CONVERSATION_URL_RE.test(url);
}

export async function installChatGptNetworkHooks(page: Page): Promise<void> {
  // addInitScript runs on every navigation; the caller always navigates after
  // installing, so the patch is active before the conversation starts.
  await page.addInitScript(FETCH_PATCH_SOURCE);
}

export interface ChatGptNetworkTracker {
  baselineCompleted: number;
  isBusy(): Promise<boolean>;
  hasActivity(): Promise<boolean>;
  dispose(): void;
}

/** Track conversation POST streams via Playwright + injected fetch (no fixed quiet window). */
export function attachChatGptNetworkTracker(page: Page): ChatGptNetworkTracker {
  let playwrightInFlight = 0;
  let playwrightCompleted = 0;
  let baselineCompleted = 0;

  const onResponse = (response: PwResponse): void => {
    const request = response.request();
    if (!isChatGptConversationResponse(response.url(), request.method())) {
      return;
    }
    playwrightInFlight++;
    void response.finished().finally(() => {
      playwrightInFlight--;
      playwrightCompleted++;
    });
  };

  page.on("response", onResponse);

  void page
    .evaluate(() => window.__aiRouterChatGpt?.completed ?? 0)
    .then((n) => {
      baselineCompleted = n;
    });

  return {
    baselineCompleted,
    async isBusy(): Promise<boolean> {
      const injected = await page.evaluate(() => ({
        inFlight: window.__aiRouterChatGpt?.inFlight ?? 0,
      }));
      return playwrightInFlight > 0 || injected.inFlight > 0;
    },
    async hasActivity(): Promise<boolean> {
      const injected = await page.evaluate(() => ({
        completed: window.__aiRouterChatGpt?.completed ?? 0,
      }));
      return (
        playwrightCompleted > 0 ||
        injected.completed > baselineCompleted
      );
    },
    dispose(): void {
      page.off("response", onResponse);
    },
  };
}

const POLL_MS = 200;

export interface ChatGptCompletionSignals {
  isUiGenerating: () => Promise<boolean>;
  readAssistantText: () => Promise<string>;
  isInterimText: (text: string) => boolean;
}

/**
 * Wait until ChatGPT is truly done — all streams finished, stop hidden, real answer in DOM.
 * No hardcoded "stable for N seconds"; long thinking/reasoning runs until timeout_ms.
 */
export async function waitForChatGptComplete(
  page: Page,
  timeoutMs: number,
  submit: () => Promise<void>,
  signals: ChatGptCompletionSignals,
  signal?: AbortSignal,
): Promise<string> {
  const tracker = attachChatGptNetworkTracker(page);
  const throwIfAborted = (): void => {
    if (signal?.aborted) {
      throw new AiRouterError("ABORTED", "ChatGPT request was cancelled");
    }
  };

  try {
    throwIfAborted();
    await submit();

    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
      throwIfAborted();
      const networkBusy = await tracker.isBusy();
      const uiGenerating = await signals.isUiGenerating();
      const text = await signals.readAssistantText();
      const hasFinalAnswer = Boolean(text && !signals.isInterimText(text));
      const sawStream = await tracker.hasActivity();

      if (sawStream && !networkBusy && !uiGenerating && hasFinalAnswer) {
        await sleep(POLL_MS);
        const confirm = await signals.readAssistantText();
        if (
          confirm === text &&
          confirm &&
          !signals.isInterimText(confirm) &&
          !(await tracker.isBusy()) &&
          !(await signals.isUiGenerating())
        ) {
          return confirm;
        }
      }

      await sleep(POLL_MS);
    }

    throw new AiRouterError(
      "TIMEOUT",
      `ChatGPT did not finish within ${timeoutMs}ms (network/UI still active or no final answer)`,
    );
  } finally {
    tracker.dispose();
  }
}
