# Stateless Ask — mỗi lần hỏi là một chat mới

**Ngày:** 2026-07-10
**Trạng thái:** Đã duyệt

## Mục tiêu

Bỏ cơ chế giữ phiên hội thoại (chat session) theo `Mcp-Session-Id`. Mỗi lần gọi `ask`, hệ thống mở một chat mới trên provider (Gemini) thay vì resume hội thoại cũ. Browser và đăng nhập Google vẫn giữ nguyên (persistent context, `profile_dir`).

## Quyết định đã chốt

- **Phạm vi "mới":** chat mới mỗi ask; browser context giữ nguyên, không launch lại.
- **Concurrency:** 1 page dùng chung, các ask xếp hàng tuần tự qua `PageWorker` queue (giữ nguyên cơ chế hiện tại).

## Thay đổi

### 1. `src/ai_router/mcp/tools.py`

- `handle_ask`:
  - Bỏ check `MISSING_SESSION` — không còn yêu cầu `Mcp-Session-Id`. Giữ tham số `mcp_session_id` chỉ để trace/log.
  - Thay `state.sessions.get_or_create(...)` bằng `page = await state.browser.new_page()`.
  - Gọi `adapter.ensure_page_ready(page)` (không còn `preserve_chat`) — với Gemini, hàm này navigate về `gemini.google.com` = chat mới, đồng thời verify đăng nhập. Adapter không có `ensure_page_ready` thì fallback `check_session` như cũ.
  - Bỏ `state.sessions.record_message(...)`.
- `AppState`: bỏ field `sessions`; `create_app_state` không tạo `SessionManager` nữa.

### 2. Xóa tầng session

- Xóa thư mục `src/ai_router/session/` (`manager.py`, `__init__.py`) gồm `SessionManager`, `ChatSession`, `CHAT_URL_RE`.
- Gỡ mọi import/tham chiếu còn lại (vd. callback `clear_all` khi reset context, nếu có).

### 3. Gemini adapter (`src/ai_router/adapters/gemini/adapter.py`)

- Bỏ method `resume_chat`.
- Bỏ tham số `preserve_chat` khỏi `ensure_page_ready` (không còn caller truyền `True`); hành vi luôn là navigate về trang chủ và chờ ô nhập prompt.

### 4. Giữ nguyên

- `BrowserManager`: persistent context + profile đăng nhập, tự phục hồi khi browser bị đóng.
- `PageWorker` / `PageQueueRegistry`: serialize các ask trên cùng 1 tab.
- Toàn bộ logic gửi prompt, đọc stream, phát hiện kết thúc trả lời.

## Xử lý lỗi

Không đổi: `LOGGED_OUT` → `NotLoggedInError`; browser đóng → `BrowserClosedError`; timeout → `TimeoutError_`.

## Kiểm thử

- Xóa các test về session/resume/`record_message`/`chat_url`.
- Thêm test: hai ask liên tiếp đều gọi `ensure_page_ready` (chat mới) — không resume URL cũ.
- Thêm test: ask không cần `Mcp-Session-Id` vẫn chạy được.
- Test hiện có về queue/stream giữ nguyên và phải pass.
