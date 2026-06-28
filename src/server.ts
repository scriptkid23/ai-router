import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { loadConfig } from "./config/load-config.js";
import { registerTools } from "./mcp/register-tools.js";
import { log } from "./logger.js";

function readBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      if (!raw) {
        resolve(undefined);
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

export async function startServer(): Promise<void> {
  const config = loadConfig();
  const sseTransports = new Map<string, SSEServerTransport>();

  const httpServer = createServer(async (req, res) => {
    const url = new URL(
      req.url ?? "/",
      `http://${config.server.host}:${config.server.port}`,
    );

    if (req.method === "GET" && url.pathname === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, service: "ai-router" }));
      return;
    }

    if (req.method === "GET" && url.pathname === config.server.path) {
      log("info", "SSE client connected");
      const transport = new SSEServerTransport(
        config.server.messagesPath,
        res,
      );
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
