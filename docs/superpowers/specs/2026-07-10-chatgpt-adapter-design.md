# ChatGPT Adapter — phát hiện "done" qua SSE

**Ngày:** 2026-07-10
**Trạng thái:** Đã duyệt

## Mục tiêu

Thêm provider **ChatGPT** vào ai-router theo đúng pattern Gemini: gõ/submit trên web UI thật,
passive-listen response streaming để biết khi nào câu trả lời **kết thúc thành công**, và đọc
nội dung trả lời từ DOM. Điểm cốt lõi là parse Server-Sent Events (SSE) của endpoint
`/backend-api/f/conversation` để phát hiện completion + phân loại lỗi.

Đồng thời gỡ phần đang **hardcode Gemini** trong tầng browser, tách provider-specific ra sau một
lớp abstraction để ChatGPT (và provider tương lai) cắm vào sạch sẽ.

## Quyết định đã chốt

- **Cách tích hợp:** drive web UI + passive-listen SSE qua `page.on("response")`. **Không** forge
  sentinel/turnstile/conduit token, **không** API-replay bằng `fetch`.
- **Nguồn answer text:** đọc từ **DOM** (như Gemini). SSE chỉ trả *tín hiệu* done/ok/error, không lấy text.
- **Refactor:** abstract theo adapter — mỗi adapter cấp một "provider profile"; tầng browser dùng generic.
- **Thinking models (o3, gpt-5-thinking):** có support. Đợi `end_turn` trên `channel:"final"`, không
  false-done trong lúc reasoning; timeout dài hơn và cấu hình được.
- **Phạm vi detect lỗi:** đầy đủ — rate-limit/usage-cap, moderation, stream abort, error field trong SSE.
- **Recovery on abort:** reload trang + retry **1 lần** (giống cơ chế `GEMINI_ERROR` hiện tại).
- **Scope tối giản:** không xử lý nút "Continue generating", không multi-turn trong cùng chat, không
  stream từng token ra ngoài (chỉ trả final answer).

## Cấu trúc SSE của `/backend-api/f/conversation` (tham chiếu parser)

Body là chuỗi dòng `event: <name>` và `data: <json|literal>`. Các mốc quan trọng, theo thứ tự
độ tin cậy để kết luận "turn này xong OK":

1. Patch cuối đặt `"/message/status" → "finished_successfully"` **và** `"/message/end_turn" → true`
   trên assistant message có `channel:"final"`. ← mốc "turn kết thúc OK" chuẩn nhất.
2. `{"type":"message_marker","marker":"last_token","event":"last"}`.
3. `{"type":"message_stream_complete"}` — toàn bộ SSE đã đóng.
4. `data: [DONE]` — terminator.

**Phải bỏ qua** (dễ nhầm là done): message `reasoning_recap` / marker `cot_token` (phase suy nghĩ),
message `role:"system"`, `input_message` (echo prompt user), `resume_conversation_token`, và mọi
message có `is_visually_hidden_from_conversation:true`. Lưu ý message reasoning **cũng** có
`status:"finished_successfully"` → vì vậy bắt buộc scope theo `channel:"final"`, không bắt bừa.

## Thay đổi

### 1. Lớp abstraction `ProviderProfile`

Tạo module mới `src/ai_router/browser/profile.py` định nghĩa dataclass `ProviderProfile` +
`StreamDone`, gom phần provider-specific:

- `stream_url_re: re.Pattern` — nhận diện URL response streaming.
- `parse_stream_done(status: int, body: str) -> StreamDone` — parse body → kết luận.
- `dom_snapshot(page) -> dict` — trả `{generating, response_count, response_text, error_text}`.
- `selectors` — input / submit / stop / response block (dùng bởi `CommandExecutor`).
- `is_stop_visible(page) -> bool`, `read_response_snapshot(page) -> tuple[int,str]`,
  `is_rate_limited(text) -> bool`.
- `planner` — sinh command script.
- `error_markers: tuple[str,...]`, `answer_timeout_s: float | None` (override per-provider).

`StreamDone`: dataclass `{done: bool, ok: bool, error_kind: str | None, error_text: str | None}`
với `error_kind ∈ {None, "rate_limit", "moderation", "incomplete", "error"}`.

Mỗi adapter (`GeminiAdapter`, `ChatGPTAdapter`) expose một `profile` (thuộc tính hoặc method
`build_profile()`), để lookup theo `adapter.id`.

### 2. Gỡ hardcode Gemini ở tầng browser

- `src/ai_router/browser/events.py`: `attach_listeners(page, channel, *, stream_url_re, parse_stream_done)`.
  Bỏ import trực tiếp `STREAM_GENERATE_RE`/`is_stream_end` từ gemini; nhận qua tham số. Khi response
  khớp `stream_url_re`, đọc body sau `response.finished()`, gọi `parse_stream_done(status, body)`;
  nếu `done` → emit `stream_end` kèm payload `ok/error_kind/error_text`.
- `src/ai_router/browser/state.py`: `StateReducer` nhận `stream_url_re` + `error_markers` qua ctor
  (bỏ import gemini). `apply_stream_end` nhận thêm `ok/error_kind/error_text`; nếu lỗi → set
  `error_text`/phase `error`.
- `src/ai_router/browser/page_worker.py`: `PageWorker.__init__` nhận `profile` (thay vì import
  `GeminiPlanner`/`GEMINI_ERROR_MARKERS`/`is_stop_visible`/`read_response_snapshot`). `_dom_snapshot`
  gọi `profile.dom_snapshot`. Bỏ `if job.provider_id != "gemini"`; dùng `profile.planner`. Lấy profile
  từ registry theo `job.provider_id`.
- `src/ai_router/browser/commands.py`: `CommandExecutor` nhận `selectors` + các hàm wait
  (`is_stop_visible`, `read_response_snapshot`, `is_rate_limited`) qua ctor thay vì import gemini.
  Logic submit (enter/click, verify) tham số hóa theo selectors.
- `src/ai_router/mcp/tools.py`: `ensure_worker` truyền profile (từ `state.registry`) vào `PageWorker`.
  `handle_ask` set `AskJob.timeout_s` theo `profile.answer_timeout_s or config.answer_timeout_s`.

### 3. Module ChatGPT

- `src/ai_router/adapters/chatgpt/stream.py` — **parser SSE** (mục "Cấu trúc SSE"). Hàm
  `parse_stream_done(status, body) -> StreamDone`. Xử lý:
  - HTTP `status` 403/429 → `error_kind="rate_limit"`.
  - Duyệt từng dòng `data:`; JSON-decode (bỏ qua dòng không phải JSON như `"v1"`, `[DONE]`).
  - Theo dõi assistant message trên `channel:"final"`: đạt `finished_successfully`+`end_turn` hoặc
    marker `last_token/event:"last"` → `done=True, ok=True`.
  - Có `message_stream_complete` nhưng thiếu success trên final channel → `done=True, ok=False,
    error_kind="incomplete"`.
  - Bất kỳ `error`/`error_code` khác null, hoặc message moderation/blocked → `error_kind` tương ứng.
  - Không tìm thấy dấu kết thúc nào (stream cụt) → `done=False` (để DOM hybrid + timeout quyết định).
- `src/ai_router/adapters/chatgpt/selectors.py` — hằng số + `CHATGPT_CONVERSATION_RE =
  re.compile(r"/backend-api/f/conversation")`. Selectors khởi điểm (verify lúc implement):
  input `#prompt-textarea`, send `[data-testid="send-button"]`, stop `[data-testid="stop-button"]`,
  assistant turn `[data-message-author-role="assistant"]`, text `.markdown`. `RATE_LIMIT_MARKERS`,
  `CHATGPT_ERROR_MARKERS`.
- `src/ai_router/adapters/chatgpt/wait.py` — `is_stop_visible`, `read_response_snapshot`,
  `is_rate_limited` cho ChatGPT (đọc DOM).
- `src/ai_router/adapters/chatgpt/planner.py` — `ChatGPTPlanner.plan(job, recovery=False)`:
  `wait_idle, clear_input, type, submit, wait_generating, wait_answer`; recovery thêm
  `goto chatgpt.com/ + wait_idle` ở đầu.
- `src/ai_router/adapters/chatgpt/adapter.py` — bỏ stub. `status="available"`, `check_session`/
  `ensure_page_ready` (goto `https://chatgpt.com/`, chờ `#prompt-textarea`, không thấy thì kiểm tra
  nút login), `open_new_chat`, và `profile`/`build_profile()`.

### 4. Config

- `src/ai_router/config.py`: thêm `chatgpt_answer_timeout_s: float = 300.0` (env
  `AI_ROUTER_CHATGPT_ANSWER_TIMEOUT_S`); thêm `chatgpt` vào `providers` mặc định
  (`https://chatgpt.com/`). `ChatGPTAdapter.profile.answer_timeout_s` đọc giá trị này.

## Xử lý lỗi

- Rate-limit (HTTP 403/429 hoặc DOM markers) → `RateLimitedError`.
- Moderation → `AiRouterError("CHATGPT_MODERATION", <text>)`.
- Stream abort / incomplete → `AiRouterError("CHATGPT_INCOMPLETE")`, worker thử `planner.plan(recovery=True)`
  **1 lần** (reload + gửi lại), sau đó raise.
- Không thấy `stream_end` (transport đổi sang WebSocket, edge case) → dựa DOM hybrid
  (stop-button biến mất + text ổn định + `stream_quiet_s`) + timeout; ghi log rõ.
- Logged-out → `NotLoggedInError`; browser đóng → `BrowserClosedError`; timeout → `TimeoutError_`.

## Kiểm thử (TDD)

- **Parser SSE (`stream.py`)** — trọng tâm:
  - Fixture = chính SSE mẫu ("hi" → o3) ⇒ `done=True, ok=True`.
  - Fixture moderation ⇒ `error_kind="moderation"`.
  - Fixture thiếu `message_stream_complete`/success ⇒ `error_kind="incomplete"`.
  - `status=429` ⇒ `error_kind="rate_limit"`.
  - Chỉ có reasoning/system/`input_message` (chưa tới final) ⇒ `done=False`.
- **Profile injection:** `events.py`/`state.py`/`page_worker.py` chạy đúng với profile Gemini *và*
  ChatGPT; test Gemini hiện có vẫn pass (không hồi quy).
- **Planner ChatGPT:** đúng chuỗi command; recovery chèn `goto`+`wait_idle`.
- **Config:** `chatgpt_answer_timeout_s` load từ yaml/env; `AskJob.timeout_s` = 300 cho ChatGPT.

## Ngoài phạm vi (YAGNI)

Forge token (sentinel/turnstile/conduit); API-replay bằng `fetch`; stream từng token ra ngoài; nút
"Continue generating"; multi-turn trong cùng một chat.
