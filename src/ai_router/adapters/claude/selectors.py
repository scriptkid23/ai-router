import re

CLAUDE_URL = "https://claude.ai/new"

CLAUDE_COMPLETION_RE = re.compile(
    r"/api/organizations/[^/]+/chat_conversations/[^/]+/completion(?:\?|$)",
    re.I,
)

SEL_PROMPT_INPUT = (
    'div[contenteditable="true"][data-placeholder], '
    'div.ProseMirror[contenteditable="true"], '
    "textarea"
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[data-testid="send-button"]'
)
SEL_STOP_BUTTON = (
    'button[aria-label*="Stop" i], '
    'div[data-is-streaming="true"]'
)
SEL_ASSISTANT_TURN = 'div[role="article"]'
SEL_ASSISTANT_MESSAGE = 'div[data-last-message="true"]'
SEL_ASSISTANT_TEXT = ".font-claude-response"
SEL_STREAMING = 'div[data-is-streaming="true"]'
SEL_LOGIN = 'a[href*="/login"], button:has-text("Log in")'

RATE_LIMIT_MARKERS = (
    "rate limit",
    "usage limit",
    "too many messages",
    "try again later",
)

CLAUDE_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)
