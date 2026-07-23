import re

GEMINI_URL = "https://gemini.google.com/app"

# Target the visible Quill editor; exclude the hidden ql-clipboard helper.
SEL_PROMPT_INPUT = (
    '[data-test-id="textarea-inner"] .ql-editor[role="textbox"], '
    '.ql-editor.textarea[contenteditable="true"][role="textbox"], '
    'rich-textarea .ql-editor[contenteditable="true"][role="textbox"], '
    'div.ql-editor[contenteditable="true"][role="textbox"]'
)
# One element per assistant turn; avoid counting nested text nodes twice.
SEL_RESPONSE_BLOCK = "model-response"
SEL_RESPONSE_TEXT = ".model-response-text, [data-message-id], message-content"
SEL_RESPONSE_INNER = ".model-response-text, message-content"
SEL_GENERATING = (
    'div.send-button-container.visible gem-icon-button.stop, '
    'div[data-test-id="send-button-container"].visible gem-icon-button.stop, '
    'div.send-button-container.visible button[aria-label*="Stop" i], '
    'div.send-button-container.visible button[aria-label*="Dừng" i], '
    'div[data-test-id="send-button-container"].visible button[aria-label*="Stop" i], '
    'div[data-test-id="send-button-container"].visible button[aria-label*="Dừng" i], '
    '[data-test-id="stop-button"]'
)
SEL_SEND_CONTAINER = (
    "div.send-button-container.visible, "
    'div[data-test-id="send-button-container"].visible, '
    ".input-buttons-wrapper-bottom, "
    ".trailing-actions-wrapper"
)
SEL_SUBMIT_BUTTON = (
    "div.send-button-container.visible button[aria-label='Send message'], "
    'div[data-test-id="send-button-container"].visible button[aria-label="Send message"], '
    'button[aria-label="Send message"]:not([aria-label*="Stop" i]), '
    'button[aria-label*="Gửi" i]:not([aria-label*="Dừng" i]), '
    'gem-icon-button.send-button.submit button, '
    '.input-buttons-wrapper-bottom button[aria-label*="Send" i]:not([aria-label*="Stop" i])'
)
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

GEMINI_ERROR_MARKERS = (
    "something went wrong",
    "(1095)",
    "(1096)",
    "(1097)",
)
