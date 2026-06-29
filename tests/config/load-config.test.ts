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
    expect(config.server.port).toBe(8087);
    expect(config.profileDir).toContain(".ai-router");
  });

  it("merges user config over defaults", () => {
    writeFileSync(
      join(dir, "config.json"),
      JSON.stringify({ defaultProvider: "gemini" }),
    );
    const config = loadConfig(dir);
    expect(config.defaultProvider).toBe("gemini");
    expect(config.server.port).toBe(8087);
  });

  it("applies env overrides", () => {
    process.env.AI_ROUTER_DEFAULT_PROVIDER = "notebooklm";
    process.env.AI_ROUTER_PORT = "9090";
    const config = loadConfig(dir);
    expect(config.defaultProvider).toBe("notebooklm");
    expect(config.server.port).toBe(9090);
  });
});
