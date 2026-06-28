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
      // Return immediately so ChatGPT UI can consume the stream.
      // Track completion in background when the SSE body finishes.
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

/** Patch fetch in-page so we see SSE stream completion, not just UI "Thinking". */
export async function installChatGptNetworkHooks(page: Page): Promise<void> {
  await page.addInitScript(FETCH_PATCH_SOURCE);
  await page.evaluate(FETCH_PATCH_SOURCE);
}

async function readInjectedNetworkState(page: Page): Promise<{
  inFlight: number;
  completed: number;
}> {
  return page.evaluate(() => ({
    inFlight: window.__aiRouterChatGpt?.inFlight ?? 0,
    completed: window.__aiRouterChatGpt?.completed ?? 0,
  }));
}

/**
 * Wait until ChatGPT conversation POST stream(s) finish — Playwright response
 * listener + injected fetch hook (no arbitrary 2s DOM stable guess).
 */
export async function waitForChatGptConversationNetwork(
  page: Page,
  timeoutMs: number,
  submit: () => Promise<void>,
): Promise<void> {
  let playwrightInFlight = 0;
  let playwrightCompleted = 0;

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

  const baseline = await readInjectedNetworkState(page);

  try {
    await submit();

    const deadline = Date.now() + timeoutMs;
    let quietSince = 0;

    while (Date.now() < deadline) {
      const injected = await readInjectedNetworkState(page);
      const newInjected = injected.completed - baseline.completed;
      const networkIdle =
        playwrightInFlight === 0 &&
        injected.inFlight === 0 &&
        (playwrightCompleted > 0 || newInjected > 0);

      if (networkIdle) {
        if (quietSince === 0) {
          quietSince = Date.now();
        } else if (Date.now() - quietSince >= 2500) {
          return;
        }
      } else {
        quietSince = 0;
      }

      await sleep(150);
    }

    throw new AiRouterError(
      "TIMEOUT",
      "ChatGPT conversation network stream did not finish before timeout",
    );
  } finally {
    page.off("response", onResponse);
  }
}
