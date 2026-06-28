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
    /** Headless breaks session reuse on ChatGPT/Gemini — keep false unless testing */
    headless: boolean;
    /** fill = one-shot DOM set (default); type = human-like keystrokes */
    prompt_input_mode: "fill" | "type";
    type_delay_ms: number;
  };
}
