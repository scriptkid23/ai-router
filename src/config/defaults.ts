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
      headless: false,
      prompt_input_mode: "fill",
      type_delay_ms: 20,
    },
  };
}
