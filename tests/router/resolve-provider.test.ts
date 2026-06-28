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
    expect(() => resolveProvider("claude", "hi", config)).toThrow(
      "PROVIDER_NOT_FOUND",
    );
  });
});
