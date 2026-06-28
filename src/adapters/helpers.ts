import type { Page } from "playwright-core";
import { AiRouterError } from "../errors.js";

export type PromptInputMode = "fill" | "type";

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fillPrompt(
  page: Page,
  selector: string,
  prompt: string,
): Promise<void> {
  const input = page.locator(selector).first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();

  const filled = await input.evaluate((el, text) => {
    if (el instanceof HTMLTextAreaElement) {
      el.value = text;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    if (el instanceof HTMLElement && el.isContentEditable) {
      el.focus();
      el.innerText = text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      return true;
    }
    return false;
  }, prompt);

  if (filled) {
    await sleep(200);
    return;
  }

  const modifier = process.platform === "darwin" ? "Meta" : "Control";
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate(async (text) => {
    await navigator.clipboard.writeText(text);
  }, prompt);
  await page.keyboard.press(`${modifier}+KeyV`);
  await sleep(200);
}

/** Human-like typing; uses Shift+Enter for newlines so ChatGPT does not submit early. */
export async function typePrompt(
  page: Page,
  selector: string,
  prompt: string,
  delayMs = 20,
): Promise<void> {
  const input = page.locator(selector).first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();

  const lines = prompt.split("\n");
  for (let i = 0; i < lines.length; i++) {
    if (i > 0) {
      await page.keyboard.down("Shift");
      await page.keyboard.press("Enter");
      await page.keyboard.up("Shift");
    }
    if (lines[i].length > 0) {
      await input.type(lines[i], { delay: delayMs });
    }
  }
  await sleep(200);
}

export async function submitPrompt(
  page: Page,
  selector: string,
  prompt: string,
  mode: PromptInputMode = "fill",
  typeDelayMs = 20,
): Promise<void> {
  if (mode === "type") {
    await typePrompt(page, selector, prompt, typeDelayMs);
  } else {
    await fillPrompt(page, selector, prompt);
  }
}

export async function waitForStableText(
  page: Page,
  selector: string,
  timeoutMs: number,
  stableMs = 2000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let lastText = "";
  let stableSince = Date.now();

  while (Date.now() < deadline) {
    const el = page.locator(selector).last();
    const count = await el.count();
    if (count === 0) {
      await sleep(500);
      continue;
    }
    const text = ((await el.innerText()) ?? "").trim();
    if (text && text === lastText) {
      if (Date.now() - stableSince >= stableMs) return text;
    } else {
      lastText = text;
      stableSince = Date.now();
    }
    await sleep(500);
  }

  if (lastText) return lastText;
  throw new AiRouterError(
    "TIMEOUT",
    "Response text did not stabilize before timeout",
  );
}

export async function saveDebugArtifacts(
  page: Page,
  debugDir: string,
): Promise<string> {
  const { mkdirSync, writeFileSync } = await import("node:fs");
  const { join } = await import("node:path");
  mkdirSync(debugDir, { recursive: true });
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const pngPath = join(debugDir, `${ts}.png`);
  await page.screenshot({ path: pngPath, fullPage: true });
  if (process.env.AI_ROUTER_DEBUG === "1") {
    const html = await page.content();
    writeFileSync(join(debugDir, `${ts}.html`), html, "utf8");
  }
  return pngPath;
}

export function resolvePromptInputMode(
  config: { browser: { prompt_input_mode?: PromptInputMode } },
  override?: PromptInputMode,
): PromptInputMode {
  return override ?? config.browser.prompt_input_mode ?? "fill";
}

export function resolveTypeDelayMs(
  config: { browser: { type_delay_ms?: number } },
): number {
  return config.browser.type_delay_ms ?? 20;
}
