from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page

from ai_router.browser.profile import ProviderProfile
from ai_router.browser.state import StateReducer
from ai_router.errors import AiRouterError, RateLimitedError, TimeoutError_
from ai_router.logger import trace

CommandOp = Literal[
    "wait_idle",
    "clear_input",
    "type",
    "submit",
    "wait_generating",
    "wait_answer",
    "goto",
    "new_chat",
]


@dataclass
class Command:
    op: CommandOp
    args: dict[str, Any] = field(default_factory=dict)


class CommandExecutor:
    def __init__(
        self,
        page: Page,
        reducer: StateReducer,
        *,
        profile: ProviderProfile,
        job_id: str,
        page_id: str,
        answer_timeout_s: float,
        idle_streak_required: int,
    ) -> None:
        self._page = page
        self._reducer = reducer
        self._profile = profile
        self._job_id = job_id
        self._page_id = page_id
        self._answer_timeout_s = answer_timeout_s
        self._idle_streak_required = idle_streak_required
        self._last_prompt_len = 0
        self._response_count_at_submit = 0

    async def run(self, commands: list[Command]) -> str:
        before_count, _ = await self._profile.read_response_snapshot(self._page)
        trace(
            "cmd_script",
            page_id=self._page_id,
            job_id=self._job_id,
            before_count=before_count,
            steps=[c.op for c in commands],
        )
        for cmd in commands:
            trace(
                "cmd_start",
                page_id=self._page_id,
                job_id=self._job_id,
                op=cmd.op,
                phase=self._reducer.state.phase,
            )
            if cmd.op == "new_chat":
                hook = self._profile.on_new_chat
                if hook is None:
                    raise AiRouterError(
                        "ADAPTER_ERROR",
                        f"Provider {self._profile.provider_id} has no new_chat handler",
                    )
                await hook(self._page)
                before_count, _ = await self._profile.read_response_snapshot(self._page)
            elif cmd.op == "wait_idle":
                await self._wait_idle()
            elif cmd.op == "clear_input":
                await self._clear_input()
            elif cmd.op == "type":
                await self._type(cmd.args["prompt"])
            elif cmd.op == "submit":
                self._response_count_at_submit, _ = (
                    await self._profile.read_response_snapshot(self._page)
                )
                self._reducer.mark_submitting()
                await self._submit()
            elif cmd.op == "wait_generating":
                await self._wait_generating()
            elif cmd.op == "wait_answer":
                answer = await self._wait_answer(before_count=before_count)
                trace(
                    "cmd_done",
                    page_id=self._page_id,
                    job_id=self._job_id,
                    op=cmd.op,
                    answer_len=len(answer),
                )
                return answer
            elif cmd.op == "goto":
                await self._page.goto(cmd.args["url"], wait_until="domcontentloaded")
                # Let the fresh chat DOM settle before rebasing the baseline.
                await asyncio.sleep(0.3)
                before_count, _ = await self._profile.read_response_snapshot(self._page)
            trace(
                "cmd_done",
                page_id=self._page_id,
                job_id=self._job_id,
                op=cmd.op,
                phase=self._reducer.state.phase,
            )
        raise AiRouterError("ADAPTER_ERROR", "CommandScript missing wait_answer")

    def _provider_error(self) -> AiRouterError:
        st = self._reducer.state
        prefix = self._profile.provider_id.upper()
        if st.error_kind == "rate_limit":
            return RateLimitedError(st.error_text or "Rate limited")
        code = {
            "moderation": f"{prefix}_MODERATION",
            "incomplete": f"{prefix}_INCOMPLETE",
        }.get(st.error_kind or "", f"{prefix}_ERROR")
        return AiRouterError(code, st.error_text or "Provider error")

    async def _clear_input(self) -> None:
        box = self._page.locator(self._profile.selectors.prompt_input).first
        await box.click()
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")

    async def _type(self, prompt: str) -> None:
        box = self._page.locator(self._profile.selectors.prompt_input).first
        await box.click()
        trace(
            "cmd_type",
            page_id=self._page_id,
            job_id=self._job_id,
            prompt_len=len(prompt),
            prompt_preview=prompt[:60],
            phase=self._reducer.state.phase,
        )
        await self._page.keyboard.insert_text(prompt)
        # Rich-text composers (Quill, ProseMirror) only enable Send after an
        # input event, not raw insert_text.
        await box.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new InputEvent('input', { bubbles: true }));
            }"""
        )
        await asyncio.sleep(0.2)
        self._last_prompt_len = len(prompt)

    async def _input_text(self) -> str:
        box = self._page.locator(self._profile.selectors.prompt_input).first
        return (await box.inner_text()).strip()

    async def _verify_submitted(self) -> bool:
        text = await self._input_text()
        input_len = len(text)
        stop_visible = await self._profile.is_stop_visible(self._page)
        st = self._reducer.state
        stream_after_submit = (
            st.submitted_at is not None
            and st.last_stream_at is not None
            and st.last_stream_at >= st.submitted_at
        )
        count, _ = await self._profile.read_response_snapshot(self._page)
        new_turn = count > self._response_count_at_submit
        cleared = input_len < max(16, self._last_prompt_len // 5)
        verified = cleared or new_turn or stream_after_submit
        trace(
            "submit_verify",
            page_id=self._page_id,
            job_id=self._job_id,
            verified=verified,
            input_len=input_len,
            prompt_len=self._last_prompt_len,
            stop_visible=stop_visible,
            stream_after_submit=stream_after_submit,
            new_turn=new_turn,
            cleared=cleared,
        )
        return verified

    async def _submit(self) -> None:
        for attempt, method in enumerate(("enter", "enter", "click"), start=1):
            if attempt > 1:
                trace(
                    "submit_retry",
                    page_id=self._page_id,
                    job_id=self._job_id,
                    attempt=attempt,
                    method=method,
                )
            if method == "click":
                await self._try_send_click()
            else:
                await self._try_enter_submit()
            if await self._verify_submitted():
                hook = self._profile.after_submit
                if hook is not None:
                    await hook(self._page)
                return
            await asyncio.sleep(0.5)
        raise AiRouterError(
            "SUBMIT_FAILED",
            "Prompt still in input after Send/Enter retries",
        )

    async def _try_send_click(self) -> bool:
        submit = self._page.locator(self._profile.selectors.submit_button).last
        try:
            await submit.wait_for(state="visible", timeout=5000)
        except Exception:
            trace(
                "submit_no_button",
                page_id=self._page_id,
                job_id=self._job_id,
            )
            return False

        for _ in range(50):
            if await self._profile.submit_ready(self._page):
                break
            await asyncio.sleep(0.1)
        else:
            trace(
                "submit_disabled",
                page_id=self._page_id,
                job_id=self._job_id,
                action="not_ready",
            )
            return False

        await submit.click(force=True)
        trace(
            "submit_click",
            page_id=self._page_id,
            job_id=self._job_id,
            disabled=False,
        )
        return True

    async def _try_enter_submit(self) -> None:
        box = self._page.locator(self._profile.selectors.prompt_input).first
        await box.click()
        await self._page.keyboard.press("Enter")
        trace(
            "submit_enter",
            page_id=self._page_id,
            job_id=self._job_id,
        )

    async def _wait_generating_started(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        last_after_submit = 0.0
        while time.monotonic() < deadline:
            if await self._generating_started():
                return True
            if self._reducer.state.phase == "error":
                raise self._provider_error()
            hook = self._profile.after_submit
            now = time.monotonic()
            if hook is not None and now - last_after_submit >= 1.0:
                await hook(self._page)
                last_after_submit = now
            await asyncio.sleep(0.1)
        return False

    async def _generating_started(self) -> bool:
        st = self._reducer.state
        if await self._profile.is_stop_visible(self._page):
            return True
        if st.submitted_at is None:
            return False
        if st.last_stream_at is not None and st.last_stream_at >= st.submitted_at:
            return True
        if st.saw_generating_this_job:
            return True
        count, _ = await self._profile.read_response_snapshot(self._page)
        if count > self._response_count_at_submit:
            return True
        hook = self._profile.is_generating_started
        if hook is not None and await hook(self._page):
            return True
        return False

    async def _wait_generating(self) -> None:
        timeout_s = self._profile.generating_start_timeout_s or 15.0
        if await self._wait_generating_started(timeout_s):
            return
        input_len = len(await self._input_text())
        raise AiRouterError(
            "SUBMIT_FAILED",
            f"Generation not started (input_len={input_len})",
        )

    async def _wait_answer(self, *, before_count: int) -> str:
        deadline = time.monotonic() + self._answer_timeout_s
        last_log = 0.0
        while time.monotonic() < deadline:
            stop_visible = await self._profile.is_stop_visible(self._page)
            checks = self._reducer.answer_ready_checks(
                before_count=before_count, generating=stop_visible
            )
            if self._reducer.answer_ready(
                before_count=before_count, generating=stop_visible
            ):
                answer = self._reducer.answer_text()
                if self._profile.is_rate_limited(answer):
                    raise RateLimitedError(answer[:200])
                trace(
                    "wait_answer_ready",
                    page_id=self._page_id,
                    job_id=self._job_id,
                    before_count=before_count,
                    answer_len=len(answer),
                )
                return answer
            if self._reducer.state.phase == "error":
                raise self._provider_error()
            now = time.monotonic()
            if now - last_log >= 5.0:
                st = self._reducer.state
                dom_count, dom_text = await self._profile.read_response_snapshot(
                    self._page
                )
                missing = [k for k, ok in checks.items() if not ok]
                trace(
                    "wait_answer_poll",
                    page_id=self._page_id,
                    job_id=self._job_id,
                    phase=st.phase,
                    before_count=before_count,
                    reducer_count=st.response_count,
                    dom_count=dom_count,
                    idle_streak=st.idle_streak,
                    stable_streak=st.response_stable_streak,
                    text_len=len(st.last_response_text),
                    dom_text_len=len(dom_text),
                    stop_visible=stop_visible,
                    missing=",".join(missing) or "none",
                )
                last_log = now
            await asyncio.sleep(0.1)
        st = self._reducer.state
        checks = self._reducer.answer_ready_checks(before_count=before_count)
        dom_count, dom_text = await self._profile.read_response_snapshot(self._page)
        missing = [k for k, ok in checks.items() if not ok]
        trace(
            "wait_answer_timeout",
            page_id=self._page_id,
            job_id=self._job_id,
            before_count=before_count,
            reducer_count=st.response_count,
            dom_count=dom_count,
            text_len=len(st.last_response_text),
            dom_text_len=len(dom_text),
            missing=",".join(missing),
        )
        raise TimeoutError_("State polling timed out waiting for stable answer")

    async def _wait_idle(self) -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            challenge = self._profile.is_challenge_visible
            if challenge is not None and await challenge(self._page):
                prefix = self._profile.provider_id.upper()
                raise AiRouterError(
                    f"{prefix}_ERROR",
                    "Challenge or verification page detected",
                )
            st = self._reducer.state
            stop_visible = await self._profile.is_stop_visible(self._page)
            if (
                not stop_visible
                and st.phase == "idle"
                and st.idle_streak >= self._idle_streak_required
            ):
                return
            if st.phase == "error":
                raise self._provider_error()
            await asyncio.sleep(0.1)
        stop_visible = await self._profile.is_stop_visible(self._page)
        raise TimeoutError_(
            f"Timed out waiting for idle browser state (stop_visible={stop_visible})"
        )
