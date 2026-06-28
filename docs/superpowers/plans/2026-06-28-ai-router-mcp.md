# ai-router MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MCP server that routes prompts to ChatGPT, Gemini, and NotebookLM via CloakBrowser persistent sessions, exposed over HTTP/SSE for `mcp-remote`.

**Architecture:** Long-running Node.js daemon binds `127.0.0.1:8088`, serves MCP over legacy SSE (`GET /mcp/sse` + `POST /mcp/messages`) for `mcp-remote` compatibility. Shared `BrowserManager` mutex wraps CloakBrowser `launchPersistentContext`. Provider-specific DOM logic lives in adapters behind a registry.

**Tech Stack:** TypeScript, Node 20+, `@modelcontextprotocol/sdk`, `cloakbrowser`, `vitest`

**Spec:** `docs/superpowers/specs/2026-06-28-ai-router-mcp-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `package.json` | deps, scripts, bin entry |
| `tsconfig.json` | ESM, strict, outDir `dist` |
| `vitest.config.ts` | unit test runner |
| `src/config/load-config.ts` | defaults, merge, env override, tilde expand |
| `src/config/defaults.ts` | default config object |
| `src/config/types.ts` | `AiRouterConfig` type |
| `src/errors.ts` | `AiRouterError` + error codes |
| `src/logger.ts` | stderr structured logging |
| `src/router/resolve-provider.ts` | keyword + default routing |
| `src/adapters/types.ts` | `ProviderAdapter`, `AskOptions` |
| `src/adapters/registry.ts` | id → adapter map |
| `src/adapters/helpers.ts` | shared wait/sleep/typePrompt/extract |
| `src/adapters/chatgpt.ts` | ChatGPT DOM adapter |
| `src/adapters/gemini.ts` | Gemini DOM adapter |
| `src/adapters/notebooklm.ts` | NotebookLM DOM adapter |
| `src/browser/manager.ts` | mutex + CloakBrowser launch |
| `src/tools/login.ts` | `login` tool handler |
| `src/tools/ask.ts` | `ask` tool handler |
| `src/tools/list-providers.ts` | `list_providers` handler |
| `src/tools/session-status.ts` | `session_status` handler |
| `src/mcp/register-tools.ts` | wire handlers to `McpServer` |
| `src/server.ts` | HTTP server + SSE transport |
| `src/cli.ts` | `serve` and `status` subcommands |
| `src/index.ts` | re-export / entry if needed |
| `tests/router/resolve-provider.test.ts` | routing unit tests |
| `tests/config/load-config.test.ts` | config unit tests |
| `tests/browser/manager.test.ts` | mutex unit tests |
| `.gitignore` | node_modules, dist |
| `README.md` | setup, MCP config, workflow |

---

### Task 1: Project scaffold

**Files:**
- Create: `package.json`
- Create: `tsconfig.json`
- Create: `vitest.config.ts`
- Create: `.gitignore`

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "ai-router",
  "version": "0.1.0",
  "description": "MCP server routing prompts to ChatGPT, Gemini, NotebookLM via CloakBrowser",
  "type": "module",
  "bin": {
    "ai-router": "./dist/cli.js"
  },
  "scripts": {
    "build": "tsc",
    "serve": "node dist/cli.js serve",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit"
  },
  "engines": {
    "node": ">=20"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.10.0",
    "cloakbrowser": "^0.4.4"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "typescript": "^5.7.0",
    "vitest": "^3.0.0"
  }
}
```

- [ ] **Step 2: Create `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": true,
    "sourceMap": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "tests"]
}
```

- [ ] **Step 3: Create `vitest.config.ts`**

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
  },
});
```

- [ ] **Step 4: Create `.gitignore`**

```
node_modules/
dist/
*.log
.DS_Store
```

- [ ] **Step 5: Install dependencies**

Run: `npm install`
Expected: `node_modules/` created, no errors

- [ ] **Step 6: Commit**

```bash
git add package.json tsconfig.json vitest.config.ts .gitignore
git commit -m "chore: scaffold TypeScript project with vitest"
```

---

### Task 2: Config types and loader

**Files:**
- Create: `src/config/types.ts`
- Create: `src/config/defaults.ts`
- Create: `src/config/load-config.ts`
- Create: `tests/config/load-config.test.ts`

- [ ] **Step 1: Write failing config test**

```typescript
// tests/config/load-config.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "../../src/config/load-config.js";

describe("loadConfig", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ai-router-test-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
    delete process.env.AI_ROUTER_DEFAULT_PROVIDER;
    delete process.env.AI_ROUTER_PORT;
  });

  it("returns defaults when config file missing", () => {
    const config = loadConfig(dir);
    expect(config.defaultProvider).toBe("chatgpt");
    expect(config.server.port).toBe(8088);
    expect(config.profileDir).toContain(".ai-router");
  });

  it("merges user config over defaults", () => {
    writeFileSync(
      join(dir, "config.json"),
      JSON.stringify({ defaultProvider: "gemini" }),
    );
    const config = loadConfig(dir);
    expect(config.defaultProvider).toBe("gemini");
    expect(config.server.port).toBe(8088);
  });

  it("applies env overrides", () => {
    process.env.AI_ROUTER_DEFAULT_PROVIDER = "notebooklm";
    process.env.AI_ROUTER_PORT = "9090";
    const config = loadConfig(dir);
    expect(config.defaultProvider).toBe("notebooklm");
    expect(config.server.port).toBe(9090);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/config/load-config.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Implement config modules**

```typescript
// src/config/types.ts
export interface AiRouterConfig {
  server: {
    host: string;
    port: number;
    path: string;
    messagesPath: string;
  };
  defaultProvider: string;
  profileDir: string;
  timeouts: {
    ask_ms: number;
    session_check_ms: number;
  };
  routing: {
    keywords: Record<string, string[]>;
  };
  providers: {
    notebooklm: {
      notebook_url: string | null;
    };
  };
  browser: {
    fingerprint_seed: string;
    humanize: boolean;
  };
}
```

```typescript
// src/config/defaults.ts
import { homedir } from "node:os";
import { join } from "node:path";
import type { AiRouterConfig } from "./types.js";

export function defaultConfig(): AiRouterConfig {
  const home = homedir();
  return {
    server: {
      host: "127.0.0.1",
      port: 8088,
      path: "/mcp/sse",
      messagesPath: "/mcp/messages",
    },
    defaultProvider: "chatgpt",
    profileDir: join(home, ".ai-router", "profile"),
    timeouts: {
      ask_ms: 120_000,
      session_check_ms: 30_000,
    },
    routing: {
      keywords: {
        gemini: ["gemini", "@gemini", "hỏi gemini"],
        notebooklm: ["notebooklm", "notebook lm", "@notebooklm"],
        chatgpt: ["chatgpt", "gpt", "@chatgpt"],
      },
    },
    providers: {
      notebooklm: {
        notebook_url: null,
      },
    },
    browser: {
      fingerprint_seed: "42069",
      humanize: true,
    },
  };
}
```

```typescript
// src/config/load-config.ts
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { defaultConfig } from "./defaults.js";
import type { AiRouterConfig } from "./types.js";

export function getConfigDir(override?: string): string {
  return override ?? join(homedir(), ".ai-router");
}

function shallowMerge<T extends Record<string, unknown>>(
  base: T,
  patch: Record<string, unknown>,
): T {
  const out = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      typeof base[key as keyof T] === "object"
    ) {
      out[key as keyof T] = shallowMerge(
        base[key as keyof T] as Record<string, unknown>,
        value as Record<string, unknown>,
      ) as T[keyof T];
    } else if (value !== undefined) {
      out[key as keyof T] = value as T[keyof T];
    }
  }
  return out;
}

export function loadConfig(configDir?: string): AiRouterConfig {
  const dir = getConfigDir(configDir);
  const configPath = join(dir, "config.json");
  let config = defaultConfig();

  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  if (!existsSync(configPath)) {
    writeFileSync(configPath, JSON.stringify(config, null, 2));
  } else {
    const raw = readFileSync(configPath, "utf8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    config = shallowMerge(config, parsed);
  }

  if (process.env.AI_ROUTER_DEFAULT_PROVIDER) {
    config.defaultProvider = process.env.AI_ROUTER_DEFAULT_PROVIDER;
  }
  if (process.env.AI_ROUTER_PROFILE_DIR) {
    config.profileDir = process.env.AI_ROUTER_PROFILE_DIR;
  }
  if (process.env.AI_ROUTER_PORT) {
    config.server.port = Number(process.env.AI_ROUTER_PORT);
  }
  if (process.env.AI_ROUTER_HOST) {
    config.server.host = process.env.AI_ROUTER_HOST;
  }

  return config;
}
```

- [ ] **Step 4: Run tests**

Run: `npm test -- tests/config/load-config.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/config tests/config
git commit -m "feat: add config loader with defaults and env overrides"
```

---

### Task 3: Errors and logger

**Files:**
- Create: `src/errors.ts`
- Create: `src/logger.ts`

- [ ] **Step 1: Implement `src/errors.ts`**

```typescript
export type ErrorCode =
  | "BROWSER_BUSY"
  | "LOGIN_IN_PROGRESS"
  | "NO_PROFILE"
  | "SESSION_EXPIRED"
  | "PROVIDER_NOT_FOUND"
  | "TIMEOUT"
  | "ADAPTER_ERROR"
  | "PROMPT_EMPTY";

export class AiRouterError extends Error {
  readonly code: ErrorCode;

  constructor(code: ErrorCode, message: string) {
    super(`[${code}] ${message}`);
    this.name = "AiRouterError";
    this.code = code;
  }
}

export function formatToolError(err: unknown): string {
  if (err instanceof AiRouterError) return err.message;
  if (err instanceof Error) return `[ADAPTER_ERROR] ${err.message}`;
  return `[ADAPTER_ERROR] ${String(err)}`;
}
```

- [ ] **Step 2: Implement `src/logger.ts`**

```typescript
type LogLevel = "error" | "warn" | "info" | "debug";

const LEVELS: Record<LogLevel, number> = {
  error: 0,
  warn: 1,
  info: 2,
  debug: 3,
};

function currentLevel(): LogLevel {
  const raw = process.env.AI_ROUTER_LOG_LEVEL ?? "info";
  if (raw === "error" || raw === "warn" || raw === "info" || raw === "debug") {
    return raw;
  }
  return "info";
}

export function log(level: LogLevel, message: string, fields?: Record<string, unknown>): void {
  if (LEVELS[level] > LEVELS[currentLevel()]) return;
  const parts = [`[ai-router] level=${level} ${message}`];
  if (fields) {
    for (const [k, v] of Object.entries(fields)) {
      parts.push(`${k}=${JSON.stringify(v)}`);
    }
  }
  console.error(parts.join(" "));
}
```

- [ ] **Step 3: Commit**

```bash
git add src/errors.ts src/logger.ts
git commit -m "feat: add error types and stderr logger"
```

---

### Task 4: Provider router

**Files:**
- Create: `src/router/resolve-provider.ts`
- Create: `tests/router/resolve-provider.test.ts`

- [ ] **Step 1: Write failing router tests**

```typescript
// tests/router/resolve-provider.test.ts
import { describe, it, expect } from "vitest";
import { resolveProvider } from "../../src/router/resolve-provider.js";
import { defaultConfig } from "../../src/config/defaults.js";

describe("resolveProvider", () => {
  const config = defaultConfig();

  it("uses explicit provider", () => {
    const result = resolveProvider("gemini", "hello", config);
    expect(result.provider).toBe("gemini");
    expect(result.routingReason).toBe("explicit");
  });

  it("matches keyword in prompt", () => {
    const result = resolveProvider(undefined, "hỏi gemini về AI", config);
    expect(result.provider).toBe("gemini");
    expect(result.routingReason).toBe("keyword:gemini");
  });

  it("falls back to default provider", () => {
    const result = resolveProvider(undefined, "explain recursion", config);
    expect(result.provider).toBe("chatgpt");
    expect(result.routingReason).toBe("default:chatgpt");
  });

  it("throws for unknown explicit provider", () => {
    expect(() => resolveProvider("claude", "hi", config)).toThrow("PROVIDER_NOT_FOUND");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/router/resolve-provider.test.ts`
Expected: FAIL

- [ ] **Step 3: Implement router**

```typescript
// src/router/resolve-provider.ts
import type { AiRouterConfig } from "../config/types.js";
import { AiRouterError } from "../errors.js";
import { getProviderIds } from "../adapters/registry.js";

export interface ResolveResult {
  provider: string;
  routingReason: string;
}

export function resolveProvider(
  explicit: string | undefined,
  prompt: string,
  config: AiRouterConfig,
): ResolveResult {
  const allowed = getProviderIds();

  if (explicit) {
    if (!allowed.includes(explicit)) {
      throw new AiRouterError(
        "PROVIDER_NOT_FOUND",
        `Unknown provider "${explicit}". Valid: ${allowed.join(", ")}`,
      );
    }
    return { provider: explicit, routingReason: "explicit" };
  }

  const lower = prompt.toLowerCase();
  for (const [providerId, keywords] of Object.entries(config.routing.keywords)) {
    for (const keyword of keywords) {
      if (lower.includes(keyword.toLowerCase())) {
        return { provider: providerId, routingReason: `keyword:${providerId}` };
      }
    }
  }

  return {
    provider: config.defaultProvider,
    routingReason: `default:${config.defaultProvider}`,
  };
}
```

- [ ] **Step 4: Create minimal registry stub for tests**

```typescript
// src/adapters/registry.ts  (stub — expanded in Task 5)
const PROVIDER_IDS = ["chatgpt", "gemini", "notebooklm"];

export function getProviderIds(): string[] {
  return [...PROVIDER_IDS];
}
```

- [ ] **Step 5: Run tests**

Run: `npm test -- tests/router/resolve-provider.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/router tests/router src/adapters/registry.ts
git commit -m "feat: add provider routing with keyword and default fallback"
```

---

### Task 5: Adapter types, registry, helpers

**Files:**
- Create: `src/adapters/types.ts`
- Modify: `src/adapters/registry.ts`
- Create: `src/adapters/helpers.ts`
- Create: `src/adapters/chatgpt.ts` (stub)
- Create: `src/adapters/gemini.ts` (stub)
- Create: `src/adapters/notebooklm.ts` (stub)

- [ ] **Step 1: Implement adapter types**

```typescript
// src/adapters/types.ts
import type { Page } from "playwright-core";
import type { AiRouterConfig } from "../config/types.js";

export interface AskOptions {
  timeoutMs: number;
  signal?: AbortSignal;
  config: AiRouterConfig;
}

export type SessionStatus = "logged_in" | "logged_out" | "unknown";

export interface ProviderAdapter {
  id: string;
  name: string;
  url: string;
  keywords: string[];
  limitations?: string;

  checkSession(page: Page): Promise<SessionStatus>;
  ask(page: Page, prompt: string, options: AskOptions): Promise<string>;
}

export interface ProviderInfo {
  id: string;
  name: string;
  url: string;
  keywords: string[];
  limitations?: string;
}
```

- [ ] **Step 2: Implement helpers**

```typescript
// src/adapters/helpers.ts
import type { Page } from "playwright-core";
import { AiRouterError } from "../errors.js";

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function typePrompt(page: Page, selector: string, prompt: string): Promise<void> {
  const input = page.locator(selector).first();
  await input.waitFor({ state: "visible", timeout: 15_000 });
  await input.click();
  await input.type(prompt, { delay: 20 });
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
  throw new AiRouterError("TIMEOUT", "Response text did not stabilize before timeout");
}

export async function saveDebugArtifacts(page: Page, debugDir: string): Promise<string> {
  const { mkdirSync } = await import("node:fs");
  const { join } = await import("node:path");
  mkdirSync(debugDir, { recursive: true });
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  const pngPath = join(debugDir, `${ts}.png`);
  await page.screenshot({ path: pngPath, fullPage: true });
  if (process.env.AI_ROUTER_DEBUG === "1") {
    const { writeFileSync } = await import("node:fs");
    const html = await page.content();
    writeFileSync(join(debugDir, `${ts}.html`), html, "utf8");
  }
  return pngPath;
}
```

- [ ] **Step 3: Wire registry with stub adapters**

Each adapter file exports a const implementing `ProviderAdapter` with `checkSession` returning `"unknown"` and `ask` throwing until Task 7–9. Registry:

```typescript
// src/adapters/registry.ts
import type { ProviderAdapter, ProviderInfo } from "./types.js";
import { chatgptAdapter } from "./chatgpt.js";
import { geminiAdapter } from "./gemini.js";
import { notebooklmAdapter } from "./notebooklm.js";

const adapters: ProviderAdapter[] = [chatgptAdapter, geminiAdapter, notebooklmAdapter];

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
```

- [ ] **Step 4: Commit**

```bash
git add src/adapters
git commit -m "feat: add adapter types, registry, and shared DOM helpers"
```

---

### Task 6: BrowserManager with mutex

**Files:**
- Create: `src/browser/manager.ts`
- Create: `tests/browser/manager.test.ts`

- [ ] **Step 1: Write failing mutex test**

```typescript
// tests/browser/manager.test.ts
import { describe, it, expect } from "vitest";
import { BrowserManager } from "../../src/browser/manager.js";
import { AiRouterError } from "../../src/errors.js";

describe("BrowserManager mutex", () => {
  it("rejects concurrent withLock calls", async () => {
    const mgr = new BrowserManager();
    let releaseFirst!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      void mgr.withLock(async () => {
        resolve();
        await new Promise<void>((r) => {
          releaseFirst = r;
        });
      });
    });
    await firstStarted;
    await expect(
      mgr.withLock(async () => "second"),
    ).rejects.toMatchObject({ code: "BROWSER_BUSY" satisfies AiRouterError["code"] });
    releaseFirst();
  });

  it("tracks login in progress", async () => {
    const mgr = new BrowserManager();
    expect(mgr.isLoginInProgress()).toBe(false);
    mgr.markLoginStarted();
    expect(mgr.isLoginInProgress()).toBe(true);
    mgr.markLoginFinished();
    expect(mgr.isLoginInProgress()).toBe(false);
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `npm test -- tests/browser/manager.test.ts`

- [ ] **Step 3: Implement BrowserManager**

```typescript
// src/browser/manager.ts
import { launchPersistentContext } from "cloakbrowser";
import type { BrowserContext } from "playwright-core";
import type { AiRouterConfig } from "../config/types.js";
import { AiRouterError } from "../errors.js";
import { log } from "../logger.js";
import { mkdirSync } from "node:fs";

export class BrowserManager {
  private locked = false;
  private loginInProgress = false;

  isLoginInProgress(): boolean {
    return this.loginInProgress;
  }

  markLoginStarted(): void {
    if (this.loginInProgress) {
      throw new AiRouterError("LOGIN_IN_PROGRESS", "login() is already running");
    }
    if (this.locked) {
      throw new AiRouterError("BROWSER_BUSY", "Browser is busy with another operation");
    }
    this.loginInProgress = true;
  }

  markLoginFinished(): void {
    this.loginInProgress = false;
  }

  async withLock<T>(fn: () => Promise<T>): Promise<T> {
    if (this.locked) {
      throw new AiRouterError("BROWSER_BUSY", "Browser is busy with another operation");
    }
    this.locked = true;
    try {
      return await fn();
    } finally {
      this.locked = false;
    }
  }

  async launchContext(
    config: AiRouterConfig,
    opts: { headless: boolean },
  ): Promise<BrowserContext> {
    mkdirSync(config.profileDir, { recursive: true });
    log("info", "launching persistent context", {
      headless: opts.headless,
      profileDir: config.profileDir,
    });
    return launchPersistentContext({
      userDataDir: config.profileDir,
      headless: opts.headless,
      humanize: config.browser.humanize,
      args: [`--fingerprint=${config.browser.fingerprint_seed}`],
    });
  }
}

export const browserManager = new BrowserManager();
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `npm test -- tests/browser/manager.test.ts`

- [ ] **Step 5: Commit**

```bash
git add src/browser tests/browser
git commit -m "feat: add BrowserManager with mutex and CloakBrowser launch"
```

---

### Task 7: ChatGPT adapter

**Files:**
- Modify: `src/adapters/chatgpt.ts`

- [ ] **Step 1: Implement ChatGPT adapter**

```typescript
// src/adapters/chatgpt.ts
import type { Page } from "playwright-core";
import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import { saveDebugArtifacts, sleep, typePrompt, waitForStableText } from "./helpers.js";
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
    await sleep(2000);
    if (await page.locator(LOGIN_SELECTOR).first().isVisible().catch(() => false)) {
      return "logged_out";
    }
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
      const session = await chatgptAdapter.checkSession(page);
      if (session === "logged_out") {
        throw new AiRouterError("SESSION_EXPIRED", "ChatGPT session expired. Run login() to re-authenticate.");
      }

      let inputSelector = INPUT_SELECTORS[0];
      for (const sel of INPUT_SELECTORS) {
        if (await page.locator(sel).first().isVisible().catch(() => false)) {
          inputSelector = sel;
          break;
        }
      }

      await typePrompt(page, inputSelector, prompt);
      await page.keyboard.press("Enter");
      const text = await waitForStableText(page, ASSISTANT_SELECTOR, options.timeoutMs);
      return text;
    } catch (err) {
      if (err instanceof AiRouterError) throw err;
      const debugDir = join(homedir(), ".ai-router", "debug");
      const shot = await saveDebugArtifacts(page, debugDir).catch(() => "unknown");
      throw new AiRouterError("ADAPTER_ERROR", `ChatGPT adapter failed. Screenshot: ${shot}`);
    }
  },
};
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/adapters/chatgpt.ts
git commit -m "feat: implement ChatGPT provider adapter"
```

---

### Task 8: Gemini adapter

**Files:**
- Modify: `src/adapters/gemini.ts`

- [ ] **Step 1: Implement Gemini adapter** (same error/session pattern as ChatGPT)

```typescript
// src/adapters/gemini.ts
import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import { saveDebugArtifacts, sleep, typePrompt, waitForStableText } from "./helpers.js";
import { homedir } from "node:os";
import { join } from "node:path";

const URL = "https://gemini.google.com/app";
const INPUT_SELECTORS = [
  "rich-textarea div[contenteditable=true]",
  'div[contenteditable="true"][aria-label]',
  "textarea",
];
const RESPONSE_SELECTOR = ".model-response-text, [data-message-id], message-content";
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
        throw new AiRouterError("SESSION_EXPIRED", "Gemini session expired. Run login() to re-authenticate.");
      }

      let inputSelector = INPUT_SELECTORS[0];
      for (const sel of INPUT_SELECTORS) {
        if (await page.locator(sel).first().isVisible().catch(() => false)) {
          inputSelector = sel;
          break;
        }
      }

      await typePrompt(page, inputSelector, prompt);
      await page.keyboard.press("Enter");
      const text = await waitForStableText(page, RESPONSE_SELECTOR, options.timeoutMs);
      return text;
    } catch (err) {
      if (err instanceof AiRouterError) throw err;
      const debugDir = join(homedir(), ".ai-router", "debug");
      const shot = await saveDebugArtifacts(page, debugDir).catch(() => "unknown");
      throw new AiRouterError("ADAPTER_ERROR", `Gemini adapter failed. Screenshot: ${shot}`);
    }
  },
};
```

- [ ] **Step 2: Commit**

```bash
git add src/adapters/gemini.ts
git commit -m "feat: implement Gemini provider adapter"
```

---

### Task 9: NotebookLM adapter

**Files:**
- Modify: `src/adapters/notebooklm.ts`

- [ ] **Step 1: Implement NotebookLM adapter**

```typescript
// src/adapters/notebooklm.ts
import type { ProviderAdapter } from "./types.js";
import { AiRouterError } from "../errors.js";
import { saveDebugArtifacts, sleep, typePrompt, waitForStableText } from "./helpers.js";
import { homedir } from "node:os";
import { join } from "node:path";

const BASE_URL = "https://notebooklm.google.com";
const LOGIN_HINT = "accounts.google.com";
const NOTEBOOK_LINK = 'a[href*="/notebook/"]';
const CHAT_INPUT = 'textarea, div[contenteditable="true"]';
const CHAT_RESPONSE = ".markdown, [class*='response'], [class*='message']";

export const notebooklmAdapter: ProviderAdapter = {
  id: "notebooklm",
  name: "NotebookLM",
  url: BASE_URL,
  keywords: ["notebooklm", "notebook lm", "@notebooklm"],
  limitations: "v1: chat only against an existing notebook; no source upload via MCP",

  async checkSession(page) {
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await sleep(2000);
    if (page.url().includes(LOGIN_HINT)) return "logged_out";
    if (await page.locator(NOTEBOOK_LINK).first.isVisible().catch(() => false)) {
      return "logged_in";
    }
    return "unknown";
  },

  async ask(page, prompt, options) {
    try {
      const notebookUrl = options.config.providers.notebooklm.notebook_url;
      if (notebookUrl) {
        await page.goto(notebookUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
      } else {
        await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
        await sleep(2000);
        const link = page.locator(NOTEBOOK_LINK).first();
        await link.waitFor({ state: "visible", timeout: 15_000 });
        await link.click();
        await page.waitForLoadState("domcontentloaded");
      }

      if (page.url().includes(LOGIN_HINT)) {
        throw new AiRouterError("SESSION_EXPIRED", "NotebookLM session expired. Run login() to re-authenticate.");
      }

      await typePrompt(page, CHAT_INPUT, prompt);
      await page.keyboard.press("Enter");
      const text = await waitForStableText(page, CHAT_RESPONSE, options.timeoutMs);
      return text;
    } catch (err) {
      if (err instanceof AiRouterError) throw err;
      const debugDir = join(homedir(), ".ai-router", "debug");
      const shot = await saveDebugArtifacts(page, debugDir).catch(() => "unknown");
      throw new AiRouterError("ADAPTER_ERROR", `NotebookLM adapter failed. Screenshot: ${shot}`);
    }
  },
};
```

- [ ] **Step 2: Commit**

```bash
git add src/adapters/notebooklm.ts
git commit -m "feat: implement NotebookLM provider adapter"
```

---

### Task 10: Tool handlers

**Files:**
- Create: `src/tools/login.ts`
- Create: `src/tools/ask.ts`
- Create: `src/tools/list-providers.ts`
- Create: `src/tools/session-status.ts`

- [ ] **Step 1: Implement `login.ts`**

```typescript
// src/tools/login.ts
import type { AiRouterConfig } from "../config/types.js";
import { browserManager } from "../browser/manager.js";
import { AiRouterError } from "../errors.js";
import { log } from "../logger.js";

const PROVIDER_URLS = [
  "https://chatgpt.com",
  "https://gemini.google.com",
  "https://notebooklm.google.com",
];

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
      const page = context.pages()[0] ?? (await context.newPage());

      const helperHtml = `<!DOCTYPE html><html><body><h1>ai-router login</h1><ul>${PROVIDER_URLS.map(
        (u) => `<li><a href="${u}" target="_blank">${u}</a></li>`,
      ).join("")}</ul><p>Log in to each provider in new tabs, then close the browser window.</p></body></html>`;

      if (args.start_url && args.start_url !== "about:blank") {
        await page.goto(args.start_url);
      } else {
        await page.setContent(helperHtml);
      }

      await new Promise<void>((resolve) => {
        context.on("close", () => resolve());
      });

      const duration_ms = Date.now() - started;
      log("info", "login complete", { duration_ms });
      return {
        success: true,
        message: "Browser closed. Session saved.",
        profile_path: config.profileDir,
        duration_ms,
      };
    } finally {
      browserManager.markLoginFinished();
      await context?.close().catch(() => undefined);
    }
  });
}
```

- [ ] **Step 2: Implement `ask.ts`**

```typescript
// src/tools/ask.ts
import { existsSync } from "node:fs";
import type { AiRouterConfig } from "../config/types.js";
import { browserManager } from "../browser/manager.js";
import { getAdapter } from "../adapters/registry.js";
import { resolveProvider } from "../router/resolve-provider.js";
import { AiRouterError } from "../errors.js";
import { log } from "../logger.js";

export async function handleAsk(
  config: AiRouterConfig,
  args: { prompt?: string; provider?: string; timeout_ms?: number },
): Promise<Record<string, unknown>> {
  const prompt = args.prompt?.trim();
  if (!prompt) throw new AiRouterError("PROMPT_EMPTY", "prompt is required and cannot be empty");

  if (!existsSync(config.profileDir)) {
    throw new AiRouterError("NO_PROFILE", "No profile found. Run login() first.");
  }

  const { provider, routingReason } = resolveProvider(args.provider, prompt, config);
  const adapter = getAdapter(provider);
  if (!adapter) {
    throw new AiRouterError("PROVIDER_NOT_FOUND", `No adapter registered for "${provider}"`);
  }

  const started = Date.now();
  const timeoutMs = args.timeout_ms ?? config.timeouts.ask_ms;

  return browserManager.withLock(async () => {
    const context = await browserManager.launchContext(config, { headless: true });
    try {
      const page = context.pages()[0] ?? (await context.newPage());
      const text = await adapter.ask(page, prompt, { timeoutMs, config });
      const duration_ms = Date.now() - started;
      log("info", "ask complete", { provider, duration_ms, routingReason });
      return {
        text,
        provider,
        routing_reason: routingReason,
        duration_ms,
        url: adapter.url,
      };
    } finally {
      await context.close().catch(() => undefined);
    }
  });
}
```

- [ ] **Step 3: Implement `list-providers.ts` and `session-status.ts`**

```typescript
// src/tools/list-providers.ts
import type { AiRouterConfig } from "../config/types.js";
import { listProviderInfo } from "../adapters/registry.js";

export function handleListProviders(config: AiRouterConfig): Record<string, unknown> {
  return {
    providers: listProviderInfo(),
    default_provider: config.defaultProvider,
  };
}
```

```typescript
// src/tools/session-status.ts
import { existsSync } from "node:fs";
import type { AiRouterConfig } from "../config/types.js";
import { browserManager } from "../browser/manager.js";
import { getAdapter, getProviderIds } from "../adapters/registry.js";
import { AiRouterError } from "../errors.js";

export async function handleSessionStatus(
  config: AiRouterConfig,
  args: { providers?: string[] },
): Promise<Record<string, unknown>> {
  const profile_exists = existsSync(config.profileDir);
  if (!profile_exists) {
    return { profile_exists: false, sessions: [] };
  }

  const ids = args.providers?.length ? args.providers : getProviderIds();
  for (const id of ids) {
    if (!getAdapter(id)) {
      throw new AiRouterError("PROVIDER_NOT_FOUND", `Unknown provider "${id}"`);
    }
  }

  return browserManager.withLock(async () => {
    const context = await browserManager.launchContext(config, { headless: true });
    try {
      const page = context.pages()[0] ?? (await context.newPage());
      const sessions = [];
      for (const id of ids) {
        const adapter = getAdapter(id)!;
        const status = await adapter.checkSession(page);
        sessions.push({
          provider: id,
          status,
          checked_at: new Date().toISOString(),
        });
      }
      return { profile_exists: true, sessions };
    } finally {
      await context.close().catch(() => undefined);
    }
  });
}
```

- [ ] **Step 4: Typecheck**

Run: `npm run typecheck`

- [ ] **Step 5: Commit**

```bash
git add src/tools
git commit -m "feat: add MCP tool handlers for login, ask, list, session_status"
```

---

### Task 11: MCP server (SSE transport)

**Files:**
- Create: `src/mcp/register-tools.ts`
- Create: `src/server.ts`

- [ ] **Step 1: Implement tool registration**

```typescript
// src/mcp/register-tools.ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { AiRouterConfig } from "../config/types.js";
import { handleLogin } from "../tools/login.js";
import { handleAsk } from "../tools/ask.js";
import { handleListProviders } from "../tools/list-providers.js";
import { handleSessionStatus } from "../tools/session-status.js";
import { formatToolError } from "../errors.js";

export function registerTools(server: McpServer, config: AiRouterConfig): void {
  server.registerTool(
    "login",
    {
      description: "Open a headed browser to manually log in to ChatGPT, Gemini, NotebookLM. Close the browser when done.",
      inputSchema: {
        start_url: z.string().optional().describe('Optional start URL (default: helper page)'),
      },
    },
    async (args) => {
      try {
        const result = await handleLogin(config, args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (err) {
        return { content: [{ type: "text", text: formatToolError(err) }], isError: true };
      }
    },
  );

  server.registerTool(
    "ask",
    {
      description: "Send a prompt to an AI provider and return the response.",
      inputSchema: {
        prompt: z.string().describe("The question or prompt to send"),
        provider: z.enum(["chatgpt", "gemini", "notebooklm"]).optional(),
        timeout_ms: z.number().optional(),
      },
    },
    async (args) => {
      try {
        const result = await handleAsk(config, args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (err) {
        return { content: [{ type: "text", text: formatToolError(err) }], isError: true };
      }
    },
  );

  server.registerTool(
    "list_providers",
    {
      description: "List supported AI providers and routing keywords.",
      inputSchema: {},
    },
    async () => ({
      content: [{ type: "text", text: JSON.stringify(handleListProviders(config), null, 2) }],
    }),
  );

  server.registerTool(
    "session_status",
    {
      description: "Check login status for each provider without sending a prompt.",
      inputSchema: {
        providers: z.array(z.string()).optional(),
      },
    },
    async (args) => {
      try {
        const result = await handleSessionStatus(config, args);
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
      } catch (err) {
        return { content: [{ type: "text", text: formatToolError(err) }], isError: true };
      }
    },
  );
}
```

Note: add `"zod"` to `package.json` dependencies (`"@modelcontextprotocol/sdk` peers may include it — if build fails, run `npm install zod`).

- [ ] **Step 2: Implement SSE HTTP server**

```typescript
// src/server.ts
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { loadConfig } from "./config/load-config.js";
import { registerTools } from "./mcp/register-tools.js";
import { log } from "./logger.js";

function readBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) return resolve(undefined);
      try {
        resolve(JSON.parse(raw));
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

export async function startServer(): Promise<void> {
  const config = loadConfig();
  const sseTransports = new Map<string, SSEServerTransport>();

  const httpServer = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://${config.server.host}:${config.server.port}`);

    if (req.method === "GET" && url.pathname === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, service: "ai-router" }));
      return;
    }

    if (req.method === "GET" && url.pathname === config.server.path) {
      log("info", "SSE client connected");
      const transport = new SSEServerTransport(config.server.messagesPath, res);
      sseTransports.set(transport.sessionId, transport);
      transport.onclose = () => {
        sseTransports.delete(transport.sessionId);
        log("info", "SSE client disconnected");
      };
      const server = new McpServer({ name: "ai-router", version: "0.1.0" });
      registerTools(server, config);
      await server.connect(transport);
      return;
    }

    if (req.method === "POST" && url.pathname === config.server.messagesPath) {
      const sessionId = url.searchParams.get("sessionId") ?? "";
      const transport = sseTransports.get(sessionId);
      if (!transport) {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Session not found" }));
        return;
      }
      const body = await readBody(req);
      await transport.handlePostMessage(req, res, body);
      return;
    }

    res.writeHead(404).end("Not found");
  });

  await new Promise<void>((resolve) => {
    httpServer.listen(config.server.port, config.server.host, resolve);
  });

  log("info", "MCP SSE server listening", {
    url: `http://${config.server.host}:${config.server.port}${config.server.path}`,
  });
}
```

- [ ] **Step 3: Add `zod` if missing and build**

Run: `npm install zod && npm run build`
Expected: `dist/` created

- [ ] **Step 4: Commit**

```bash
git add src/mcp src/server.ts package.json package-lock.json
git commit -m "feat: add MCP SSE server with tool registration"
```

---

### Task 12: CLI

**Files:**
- Create: `src/cli.ts`

- [ ] **Step 1: Implement CLI**

```typescript
#!/usr/bin/env node
// src/cli.ts
import { startServer } from "./server.js";
import { loadConfig } from "./config/load-config.js";
import { log } from "./logger.js";

async function main(): Promise<void> {
  const [command] = process.argv.slice(2);

  if (command === "serve" || !command) {
    await startServer();
    return;
  }

  if (command === "status") {
    const config = loadConfig();
    const url = `http://${config.server.host}:${config.server.port}/health`;
    try {
      const res = await fetch(url);
      const body = await res.json();
      console.log(JSON.stringify({ ok: res.ok, url, body }, null, 2));
    } catch (err) {
      console.log(JSON.stringify({ ok: false, url, error: String(err) }, null, 2));
      process.exit(1);
    }
    return;
  }

  log("error", `Unknown command: ${command}`);
  console.error("Usage: ai-router serve | status");
  process.exit(1);
}

main().catch((err) => {
  log("error", "fatal", { error: String(err) });
  process.exit(1);
});
```

- [ ] **Step 2: Build and smoke test health endpoint**

Run:
```bash
npm run build
node dist/cli.js serve
```
In another terminal:
```bash
curl http://127.0.0.1:8088/health
```
Expected: `{"ok":true,"service":"ai-router"}`

- [ ] **Step 3: Commit**

```bash
git add src/cli.ts
git commit -m "feat: add CLI with serve and status commands"
```

---

### Task 13: README and final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write README**

Include:
- Prerequisites: Node 20+, first run downloads CloakBrowser binary
- Install: `npm install && npm run build`
- Start: `npm run serve`
- Cursor MCP config:

```json
{
  "mcpServers": {
    "ai-router": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "http://127.0.0.1:8088/mcp/sse"]
    }
  }
}
```

- Workflow: serve → login → ask → session_status
- Security warning about `~/.ai-router/profile`
- Config file location and env vars

- [ ] **Step 2: Run full test suite and typecheck**

Run:
```bash
npm test
npm run typecheck
npm run build
```
Expected: all tests pass, no type errors

- [ ] **Step 3: Manual integration checklist**

- [ ] `npm run serve` starts without error
- [ ] Cursor connects via mcp-remote
- [ ] `list_providers` returns 3 providers
- [ ] `login` opens headed browser; closing saves profile
- [ ] `session_status` shows logged_in for providers you logged into
- [ ] `ask({ prompt: "hello", provider: "chatgpt" })` returns a response
- [ ] `ask({ prompt: "hỏi gemini: what is 2+2?" })` routes to gemini (`routing_reason: keyword:gemini`)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and MCP configuration"
```

---

## Spec Coverage Checklist

| Spec requirement | Task |
|------------------|------|
| HTTP/SSE + mcp-remote | Task 11, 12 |
| 4 MCP tools | Task 10, 11 |
| CloakBrowser persistent profile | Task 6 |
| Login headed, close = done | Task 10 |
| Ask headless | Task 10 |
| Mutex BROWSER_BUSY | Task 6, 10 |
| 3 provider adapters + extensible registry | Task 5, 7–9 |
| Keyword routing + default + routing_reason | Task 4, 10 |
| Config ~/.ai-router/config.json | Task 2 |
| Error codes | Task 3, all tools/adapters |
| Debug screenshots | Task 5, adapters |
| session_status | Task 10 |
| NotebookLM notebook_url config | Task 2, 9 |
| README workflow | Task 13 |

---

## Post-Implementation Notes

- DOM selectors will break when providers update UI — check `~/.ai-router/debug/` screenshots on `ADAPTER_ERROR`
- SSE transport is legacy but required for `mcp-remote` URL pattern matching docgraph; migrate to Streamable HTTP post-v1 if `mcp-remote` supports it
- CloakBrowser first launch downloads ~200MB binary — document in README
