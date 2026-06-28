import { describe, it, expect } from "vitest";
import { BrowserManager } from "../../src/browser/manager.js";

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
    await expect(mgr.withLock(async () => "second")).rejects.toMatchObject({
      code: "BROWSER_BUSY",
    });
    releaseFirst();
  });

  it("tracks login in progress", () => {
    const mgr = new BrowserManager();
    expect(mgr.isLoginInProgress()).toBe(false);
    mgr.markLoginStarted();
    expect(mgr.isLoginInProgress()).toBe(true);
    mgr.markLoginFinished();
    expect(mgr.isLoginInProgress()).toBe(false);
  });
});
