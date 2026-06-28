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
      description:
        "Open a headed browser with ChatGPT, Gemini, and NotebookLM tabs for manual login. " +
        "Log in to each site, then close the browser window. " +
        "Only call once per session — do not retry if session_status shows logged_in.",
      inputSchema: {
        start_url: z
          .string()
          .optional()
          .describe("Optional start URL (default: helper page)"),
      },
    },
    async (args) => {
      try {
        const result = await handleLogin(config, args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (err) {
        return {
          content: [{ type: "text", text: formatToolError(err) }],
          isError: true,
        };
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
        prompt_input_mode: z
          .enum(["fill", "type"])
          .optional()
          .describe(
            "fill = paste whole prompt (default); type = human-like keystrokes",
          ),
      },
    },
    async (args) => {
      try {
        const result = await handleAsk(config, args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (err) {
        return {
          content: [{ type: "text", text: formatToolError(err) }],
          isError: true,
        };
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
      content: [
        {
          type: "text",
          text: JSON.stringify(handleListProviders(config), null, 2),
        },
      ],
    }),
  );

  server.registerTool(
    "session_status",
    {
      description:
        "Check login status for each provider without sending a prompt. " +
        "Call this before login() — only run login() if status is logged_out.",
      inputSchema: {
        providers: z.array(z.string()).optional(),
      },
    },
    async (args) => {
      try {
        const result = await handleSessionStatus(config, args);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        };
      } catch (err) {
        return {
          content: [{ type: "text", text: formatToolError(err) }],
          isError: true,
        };
      }
    },
  );
}
