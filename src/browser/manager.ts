import { launchPersistentContext } from "cloakbrowser";
import type { BrowserContext } from "playwright-core";
import { mkdirSync } from "node:fs";
import type { AiRouterConfig } from "../config/types.js";
import { AiRouterError } from "../errors.js";
import { log } from "../logger.js";

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
      throw new AiRouterError(
        "BROWSER_BUSY",
        "Browser is busy with another operation",
      );
    }
    this.loginInProgress = true;
  }

  markLoginFinished(): void {
    this.loginInProgress = false;
  }

  async withLock<T>(fn: () => Promise<T>): Promise<T> {
    if (this.locked) {
      throw new AiRouterError(
        "BROWSER_BUSY",
        "Browser is busy with another operation",
      );
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
