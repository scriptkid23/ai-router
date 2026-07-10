import re

CHATGPT_URL = "https://chatgpt.com/"

# Match the send/stream endpoint only — not /conversation/init or
# /conversation/<id> metadata calls.
CHATGPT_CONVERSATION_RE = re.compile(r"/backend-api/f/conversation(?:\?|$)", re.I)

# Starting points — verified against the live DOM during smoke testing.
SEL_PROMPT_INPUT = "#prompt-textarea"
SEL_SUBMIT_BUTTON = (
    'button[data-testid="send-button"], '
    'button[aria-label*="Send" i]'
)
SEL_STOP_BUTTON = (
    'button[data-testid="stop-button"], '
    'button[aria-label*="Stop" i]'
)
SEL_ASSISTANT_TURN = '[data-message-author-role="assistant"]'
SEL_ASSISTANT_TEXT = ".markdown"
SEL_LOGIN = (
    'button[data-testid="login-button"], '
    'a[href*="/auth/login"]'
)

RATE_LIMIT_MARKERS = (
    "too many requests",
    "you've reached your",
    "usage cap",
    "try again later",
)

CHATGPT_ERROR_MARKERS = (
    "something went wrong",
    "network error",
    "an error occurred",
)
