#!/usr/bin/env node
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
      console.log(
        JSON.stringify({ ok: false, url, error: String(err) }, null, 2),
      );
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
