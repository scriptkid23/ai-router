import re

KIMI_URL = "https://www.kimi.com/"
KIMI_NEW_CHAT_URL = "https://www.kimi.com/?chat_enter_method=new_chat"

KIMI_CHAT_RE = re.compile(
    r"/apiv2/kimi\.gateway\.chat\.v1\.ChatService/Chat(?:\?|$)",
    re.I,
)

SEL_NEW_CHAT = (
    'button[aria-label*="New chat" i], '
    'a[aria-label*="New chat" i], '
    'button:has-text("New chat")'
)
SEL_PROMPT_INPUT = (
    'textarea:not([aria-hidden="true"]):visible, '
    'div[contenteditable="true"]:visible'
)
SEL_SUBMIT_BUTTON = (
    'button[aria-label*="Send" i], '
    'button[type="submit"]'
)
SEL_STOP_BUTTON = 'button[aria-label*="Stop" i]'
SEL_ASSISTANT_MAIN = ".segment-content-box"
SEL_ASSISTANT_TEXT = ".markdown-container .markdown"
SEL_LOGIN = 'a[href*="/login"], button:has-text("Log in")'
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

KIMI_ERROR_MARKERS = (
    "something went wrong",
    "unable to respond",
    "an error occurred",
)

FAILURE_STATUSES = frozenset({
    "MESSAGE_STATUS_FAILED",
    "MESSAGE_STATUS_CANCELLED",
    "MESSAGE_STATUS_ERROR",
})

COMPLETED_STATUS = "MESSAGE_STATUS_COMPLETED"
