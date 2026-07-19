import re

DEEPSEEK_URL = "https://chat.deepseek.com/"

DEEPSEEK_COMPLETION_RE = re.compile(
    r"/api/v\d+/chat/completion(?:\?|$)",
    re.I,
)

SEL_NEW_CHAT = (
    'button[aria-label*="New chat" i], '
    'a[aria-label*="New chat" i], '
    'button:has-text("New chat")'
)
SEL_PROMPT_INPUT = (
    ".ds-chat-input-container textarea, "
    "#chat-input, "
    'textarea:not([aria-hidden="true"]):visible, '
    'div[contenteditable="true"]:visible'
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[type="submit"]'
)
SEL_STOP_BUTTON = 'button[aria-label*="Stop" i]'
SEL_ASSISTANT_MAIN = (
    '[data-testid="assistant-message"], '
    ".ds-assistant-message-main-content"
)
SEL_ASSISTANT_TEXT = (
    '[data-testid="assistant-message"] .ds-markdown, '
    ".ds-assistant-message-main-content .ds-markdown"
)
SEL_LOGIN = 'a[href*="/login"], button:has-text("Log in")'
SEL_SIDEBAR_CHAT = (
    'a[href*="/chat/"], '
    '[class*="sidebar"] a[href], '
    '[class*="conversation"] a, '
    'nav a[href*="/chat"]'
)
SEL_SEARCH_INDICATOR = (
    '[class*="search-status"]:visible, '
    '[class*="searching"]:visible'
)
# Composer toggles (DeepThink/Search buttons) also match [class*="think"] /
# [data-testid*="search"] — never use those for completion detection.
ACTIVE_GENERATING_MARKERS = (
    "searching the web",
    "searching for",
    "reading pages",
    "thinking...",
    "đang tìm",
)
SEL_CHALLENGE = (
    'iframe[src*="challenges.cloudflare.com"], '
    'iframe[src*="turnstile"], '
    '[class*="turnstile"]'
)

RATE_LIMIT_MARKERS = (
    "rate limit",
    "too many requests",
    "try again later",
)

CHALLENGE_MARKERS = (
    "checking your browser",
    "verify you are human",
)

GENERATING_BODY_MARKERS = ACTIVE_GENERATING_MARKERS

DEEPSEEK_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)

FAILURE_STATUSES = frozenset(
    {"ERROR", "FAILED", "CANCELLED", "INTERRUPTED", "ABORTED"}
)
