# Parallel Ask — Gemini + ChatGPT song song trên tab riêng

**Ngày:** 2026-07-10
**Trạng thái:** Đã duyệt

## Mục tiêu

Cho phép gửi **cùng một prompt** tới nhiều provider (Gemini, ChatGPT) **song song thật** — mỗi
provider chạy trên tab Playwright riêng — và nhận về **tất cả** câu trả lời để client so sánh /
chọn answer tốt nhất.

Không cần hai browser profile (`profile_dir` vẫn là một). Thay đổi nằm ở **page routing**: hiện
tại `BrowserManager.new_page()` luôn reuse `ctx.pages[0]` nên mọi `ask` xếp hàng tuần tự trên cùng
một tab và navigate lần lượt giữa các site.

**Phụ thuộc:** ChatGPT adapter + `ProviderProfile` abstraction (spec
`2026-07-10-chatgpt-adapter-design.md`) và stateless ask (spec
`2026-07-10-stateless-ask-design.md`).

## Quyết định đã chốt

| # | Quyết định | Lựa chọn | Căn cứ |
|---|------------|----------|--------|
| 1 | Tab routing | **Pinned tab per provider** | Parallel thật; warm session; khớp design event-queue ("different pages run in parallel") |
| 2 | Chat lifecycle | **Chat mới mỗi ask** | Spec stateless đã duyệt; `ensure_page_ready` / `open_new_chat` = `goto` home; ChatGPT YAGNI multi-turn |
| 3 | `ask_multi` strategy mặc định | **`all`** (trả hết answers) | Use case so sánh; ChatGPT timeout 300s — `first` sẽ luôn thiên Gemini |
| 4 | Warm-up | **Lazy per provider** | Khớp lazy browser hiện tại; `ai serve` không popup browser khi chưa có `ask` |
| 5 | Browser profile | **Một** `profile_dir` chung | Cookie/login Gemini + ChatGPT trong cùng persistent context |
| 6 | `ask` hiện tại | **Giữ nguyên API** | Backward compatible; chỉ đổi routing bên trong |

## Kiến trúc

```
┌─────────────────────────────────────────────────────────────┐
│  ask_multi(prompt, providers?)                              │
│    └─ asyncio.gather(handle_ask(gemini), handle_ask(chatgpt))│
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     ┌─────────────────┐       ┌─────────────────┐
     │  PageRouter     │       │  PageRouter     │
     │  key="gemini"   │       │  key="chatgpt"  │
     └────────┬────────┘       └────────┬────────┘
              ▼                         ▼
     ┌─────────────────┐       ┌─────────────────┐
     │  PageWorker     │       │  PageWorker     │
     │  (FIFO queue)   │       │  (FIFO queue)   │
     └────────┬────────┘       └────────┬────────┘
              ▼                         ▼
     ┌─────────────────┐       ┌─────────────────┐
     │  Tab 1          │       │  Tab 2          │
     │  gemini.google  │       │  chatgpt.com    │
     └─────────────────┘       └─────────────────┘
              └──────── 1 browser profile ────────┘
```

### Parallelism model (mở rộng spec event-queue)

| Scope | Hành vi |
|-------|---------|
| Cùng provider / cùng tab | FIFO — job N+1 chờ job N xong và state `idle` |
| Khác provider / khác tab | **Song song** — Gemini generating không block ChatGPT |
| Cùng browser context | Shared `profile_dir`; nhiều tab được phép (`max_pages`) |

### Lifecycle: warm-up vs per-ask

| Giai đoạn | Khi nào | Hành vi |
|-----------|---------|---------|
| **Warm-up** (lazy, 1 lần per provider) | Lần `ask`/`ask_multi` đầu tiên dùng provider X | Tạo pinned tab → `ensure_page_ready()` (goto + verify login, timeout 15s) |
| **Per-ask** (mỗi job) | Mỗi `handle_ask` | `adapter.open_new_chat(page)` (= `goto` provider home) → enqueue job |
| **ask_multi cold start** | Lần đầu gọi với 2 provider chưa warm | `asyncio.gather(warm("gemini"), warm("chatgpt"))` — song song ~15s |

Pinned tab **không đóng** giữa các ask; chỉ navigate về home để reset chat (stateless).

## Thay đổi

### 1. `PageRouter` (module mới: `src/ai_router/browser/page_router.py`)

Trách nhiệm: map `provider_id` → dedicated `Page` + `PageWorker`.

```python
class PageRouter:
    def __init__(self, browser: BrowserManager, state: AppState) -> None: ...

    async def page_for(self, provider_id: str) -> Page:
        """Return pinned tab for provider; create + warm on first use."""

    async def warm(self, provider_id: str) -> None:
        """Lazy warm-up: new tab + ensure_page_ready (login verify)."""

    async def open_new_chat(self, provider_id: str) -> Page:
        """Per-ask: goto provider home on pinned tab (fresh chat)."""
```

- `AppState` thêm field `page_router: PageRouter`.
- `provider_pages: dict[str, Page]` nội bộ router (hoặc trên `BrowserManager`).

### 2. `BrowserManager` — sửa `new_page()`

Hiện tại:

```python
if ctx.pages:
    return ctx.pages[0]  # luôn reuse tab đầu — gây serialize
```

Thêm:

```python
async def new_page_for(self, key: str) -> Page:
    """Pinned tab per key (provider_id). Creates a NEW tab if key unseen."""
```

- `new_page()` không tham số: giữ behavior cũ (reuse `pages[0]`) cho backward compat tạm thời,
  hoặc delegate tới `default_provider` key — **khuyến nghị:** `handle_ask` luôn qua `PageRouter`,
  deprecate direct `new_page()` trong ask path.
- `max_pages` (mặc định 10) giới hạn số tab pinned.

### 3. `handle_ask` (`src/ai_router/mcp/tools.py`)

Thay:

```python
page = await state.browser.new_page()
status = await adapter.ensure_page_ready(page)
```

Bằng:

```python
page = await state.page_router.open_new_chat(adapter.id)
# open_new_chat: warm nếu cần, rồi adapter.open_new_chat(page) (= goto home)
status = await adapter.check_session(page)  # nhẹ: chỉ verify selector, không full goto lặp
```

Hoặc tách rõ:

1. `await state.page_router.warm(adapter.id)` — lần đầu
2. `await adapter.open_new_chat(page)` — mỗi ask
3. Verify login: nếu `SEL_PROMPT_INPUT` missing → `NotLoggedInError`

**Lưu ý:** `ensure_page_ready` hiện = goto + login check. Sau refactor:
- **Warm:** `ensure_page_ready` (đầy đủ)
- **Per-ask:** `open_new_chat` (chỉ goto home, đã có docstring tương đương trên cả Gemini và ChatGPT)

### 4. `PageWorker` — một profile per tab (cleanup tùy chọn)

Phase 1: giữ `profiles: dict[str, ProviderProfile]` như hiện tại — worker vẫn switch theo
`job.provider_id`.

Phase 2 (cleanup): ctor nhận **một** `ProviderProfile` vì mỗi tab chỉ phục vụ một provider.
`StateReducer.stream_url_res` chỉ cần một regex — đơn giản hóa, tránh cross-talk SSE.

### 5. MCP tool `ask_multi` (`src/ai_router/mcp/server.py` + `tools.py`)

```python
async def handle_ask_multi(
    state: AppState,
    *,
    prompt: str,
    providers: list[str] | None = None,
    strategy: Literal["all", "first", "longest"] = "all",
    mcp_session_id: str | None,
) -> dict: ...
```

**Input:**

| Field | Kiểu | Mặc định | Mô tả |
|-------|------|----------|-------|
| `prompt` | `str` | required | Cùng prompt gửi tất cả provider |
| `providers` | `list[str] \| null` | all `available` | Subset provider ids |
| `strategy` | `"all" \| "first" \| "longest"` | `"all"` | Cách chọn `selected` |

**Output:**

```json
{
  "answers": [
    {
      "provider": "gemini",
      "answer": "...",
      "duration_s": 12.3,
      "routing_reason": "explicit param",
      "error": null
    },
    {
      "provider": "chatgpt",
      "answer": "...",
      "duration_s": 87.1,
      "routing_reason": "explicit param",
      "error": null
    }
  ],
  "selected": null
}
```

- `strategy="all"` → `selected: null`; client tự so sánh.
- `strategy="first"` → `selected` = answer về đầu tiên (theo `asyncio.wait` FIRST_COMPLETED).
- `strategy="longest"` → `selected` = answer có `len(answer)` lớn nhất trong các entry không lỗi.

**Fan-out:** `asyncio.gather` với wrapper bắt `AiRouterError` per provider — **không fail-fast**
khi một provider lỗi.

### 6. Config (`src/ai_router/config.py`)

```yaml
# ~/.ai-router/config.yaml (optional)
parallel_ask:
  default_providers:   # ask_multi khi omit providers
    - gemini
    - chatgpt
  default_strategy: all
```

Env không bắt buộc phase 1; có thể thêm sau nếu cần.

## Xử lý lỗi

| Tình huống | Hành vi |
|------------|---------|
| Gemini OK, ChatGPT timeout | `answers`: 1 entry OK + 1 entry `error: "TIMEOUT"`; không raise (trừ khi cả 2 lỗi và strategy cần `selected`) |
| Provider logged out | Entry `error: "NOT_LOGGED_IN"`; provider kia vẫn chạy |
| Provider `coming_soon` | Bỏ qua trong fan-out; hoặc entry lỗi `PROVIDER_NOT_READY` |
| Tab crash / context reset | `PageRouter` drop pin → recreate + warm lần ask sau |
| `max_pages` exceeded | `AiRouterError("BROWSER_BUSY")` — không tạo tab mới |
| Cả 2 provider lỗi | Return `answers` toàn lỗi; `selected: null`; **không** raise (client quyết định) |

Giữ nguyên error types hiện có: `NotLoggedInError`, `BrowserClosedError`, `TimeoutError_`,
`ProviderNotReadyError`, `RateLimitedError`.

## Kiểm thử

### Unit

- `PageRouter.warm` tạo tab mới per `provider_id` (không reuse `pages[0]`).
- Hai `handle_ask` concurrent (fake browser, fake worker) — enqueue song song, không block.
- `handle_ask_multi` trả `answers` length = số provider; `strategy="all"` → `selected is None`.
- `strategy="first"` / `"longest"` chọn đúng entry.
- Một provider raise `NotLoggedInError` — entry lỗi, provider kia vẫn có answer.
- Per-ask: hai `ask` liên tiếp cùng provider gọi `open_new_chat` (goto), không resume URL cũ
  (giữ invariant stateless).

### Integration (manual)

1. `poetry run ai browser login` — login cả Gemini và ChatGPT.
2. `poetry run ai serve`
3. `ask_multi` với prompt ngắn → cả 2 answer trong vòng ~max(gemini, chatgpt) chứ không phải tổng tuần tự.
4. `ask` đơn (gemini / chatgpt) — không regression.

## Ngoài phạm vi (YAGNI)

- LLM-as-judge tự chọn answer tốt nhất trong router.
- Auto-retry provider lỗi trong `ask_multi`.
- Multi-turn / giữ context hội thoại trên pinned tab.
- Nhiều browser profile (`profile_dir` per provider).
- Tab pool động (tab mới mỗi concurrent ask không pin).
- Stream partial tokens ra client.

## Tương thích ngược

- Tool MCP `ask` — signature và response **không đổi**.
- `test_each_ask_opens_new_chat` — đổi assert từ `ensure_page_ready` sang `open_new_chat` nếu
  refactor tách warm/per-ask; invariant "fresh chat mỗi ask" **giữ nguyên**.
- Browser event-queue design — mở rộng parallelism model, không thay thế.

## Thứ tự implement (gợi ý)

1. `BrowserManager.new_page_for(key)` + `PageRouter` + tests routing.
2. `handle_ask` dùng `PageRouter`; verify stateless + full suite green.
3. `handle_ask_multi` + MCP tool + tests fan-out / partial failure.
4. Manual smoke: parallel latency < sequential sum.
