import type { AiRouterConfig } from "../config/types.js";
import { browserManager } from "../browser/manager.js";
import { log } from "../logger.js";

const PROVIDER_URLS = [
  "https://chatgpt.com",
  "https://gemini.google.com",
  "https://notebooklm.google.com",
];

const MIN_LOGIN_MS = 30_000;

export async function handleLogin(
  config: AiRouterConfig,
  args: { start_url?: string },
): Promise<Record<string, unknown>> {
  const started = Date.now();
  browserManager.markLoginStarted();

  return browserManager.withLock(async () => {
    let context;
    try {
      context = await browserManager.launchContext(config, { headless: false });
      const firstPage = context.pages()[0] ?? (await context.newPage());

      const startUrl = args.start_url?.trim();
      if (startUrl && startUrl !== "about:blank") {
        await firstPage.goto(startUrl, {
          waitUntil: "domcontentloaded",
          timeout: 60_000,
        });
      } else {
        for (let i = 0; i < PROVIDER_URLS.length; i++) {
          const page =
            i === 0 ? firstPage : await context.newPage();
          await page.goto(PROVIDER_URLS[i], {
            waitUntil: "domcontentloaded",
            timeout: 60_000,
          });
        }
      }

      log("info", "login browser open — log in to each tab, then close the browser window");
      // No timeout: login is a manual step, wait until the user closes the window.
      // (Playwright's default waitForEvent timeout is 30s, which would cut login short.)
      await context.waitForEvent("close", { timeout: 0 });

      const duration_ms = Date.now() - started;
      log("info", "login complete", { duration_ms });

      const result: Record<string, unknown> = {
        success: true,
        message: "Browser closed. Session saved.",
        profile_path: config.profileDir,
        duration_ms,
      };

      if (duration_ms < MIN_LOGIN_MS) {
        result.warning =
          "Browser was open less than 30 seconds. You may not have finished logging in to all providers.";
      }

      return result;
    } finally {
      browserManager.markLoginFinished();
      await context?.close().catch(() => undefined);
    }
  });
}
