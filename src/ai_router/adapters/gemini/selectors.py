import re

GEMINI_URL = "https://gemini.google.com/app"

SEL_PROMPT_INPUT = (
    'div.ql-editor[contenteditable="true"], '
    'rich-textarea div[contenteditable="true"]'
)
SEL_RESPONSE_BLOCK = "model-response, .model-response-text, message-content"
SEL_GENERATING = 'button[aria-label*="Stop"], button[aria-label*="Dừng"]'
SEL_SIGN_IN = (
    'a[href*="accounts.google.com/ServiceLogin"], '
    'a[href*="accounts.google.com/signin"]'
)

STREAM_GENERATE_RE = re.compile(
    r"assistant\.lamda\.BardFrontendService/StreamGenerate", re.I
)

RATE_LIMIT_MARKERS = (
    "too many requests",
    "try again later",
    "you've reached your limit",
    "quá nhiều yêu cầu",
    "đã đạt đến giới hạn",
    "thử lại sau",
)
