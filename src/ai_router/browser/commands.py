from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from playwright.async_api import Page

from ai_router.adapters.gemini.selectors import (
    SEL_PROMPT_INPUT,
    SEL_SUBMIT_BUTTON,
)
from ai_router.adapters.gemini.wait import is_rate_limited, read_response_snapshot
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
        job_id: str,
        page_id: str,
        answer_timeout_s: float,
        idle_streak_required: int,
    ) -> None:
        self._page = page
        self._reducer = reducer
        self._job_id = job_id
        self._page_id = page_id
        self._answer_timeout_s = answer_timeout_s
        self._idle_streak_required = idle_streak_required

    async def run(self, commands: list[Command]) -> str:
        before_count, _ = await read_response_snapshot(self._page)
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
            if cmd.op == "wait_idle":
                await self._wait_idle()
            elif cmd.op == "clear_input":
                await self._clear_input()
            elif cmd.op == "type":
                await self._type(cmd.args["prompt"])
            elif cmd.op == "submit":
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
            trace(
                "cmd_done",
                page_id=self._page_id,
                job_id=self._job_id,
                op=cmd.op,
                phase=self._reducer.state.phase,
            )
        raise AiRouterError("ADAPTER_ERROR", "CommandScript missing wait_answer")

    async def _clear_input(self) -> None:
        box = self._page.locator(SEL_PROMPT_INPUT).first
        await box.click()
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")

    async def _type(self, prompt: str) -> None:
        box = self._page.locator(SEL_PROMPT_INPUT).first
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
        # Quill only enables Send after an input event, not raw insert_text.
        await box.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new InputEvent('input', { bubbles: true }));
            }"""
        )
        await asyncio.sleep(0.2)

    async def _submit(self) -> None:
        submit = self._page.locator(SEL_SUBMIT_BUTTON).last
        await submit.wait_for(state="visible", timeout=5000)
        disabled = True
        for _ in range(50):
            disabled = await submit.is_disabled()
            if not disabled:
                break
            await asyncio.sleep(0.1)
        if disabled:
            trace(
                "submit_disabled",
                page_id=self._page_id,
                job_id=self._job_id,
                action="enter_fallback",
            )
            box = self._page.locator(SEL_PROMPT_INPUT).first
            await box.click()
            await self._page.keyboard.press("Enter")
            return
        await submit.click()
        trace(
            "submit_click",
            page_id=self._page_id,
            job_id=self._job_id,
            disabled=False,
        )

    async def _wait_generating(self) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self._reducer.state.saw_generating_this_job:
                return
            if self._reducer.state.phase == "error":
                raise AiRouterError(
                    "GEMINI_ERROR",
                    self._reducer.state.error_text or "Gemini error",
                )
            await asyncio.sleep(0.1)
        raise AiRouterError("SUBMIT_FAILED", "Send click did not start generation")

    async def _wait_answer(self, *, before_count: int) -> str:
        deadline = time.monotonic() + self._answer_timeout_s
        last_log = 0.0
        while time.monotonic() < deadline:
            checks = self._reducer.answer_ready_checks(before_count=before_count)
            if all(checks.values()):
                answer = self._reducer.state.last_response_text
                if is_rate_limited(answer):
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
                raise AiRouterError(
                    "GEMINI_ERROR",
                    self._reducer.state.error_text or "Gemini error",
                )
            now = time.monotonic()
            if now - last_log >= 5.0:
                st = self._reducer.state
                dom_count, dom_text = await read_response_snapshot(self._page)
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
                    missing=",".join(missing) or "none",
                )
                last_log = now
            await asyncio.sleep(0.1)
        st = self._reducer.state
        checks = self._reducer.answer_ready_checks(before_count=before_count)
        dom_count, dom_text = await read_response_snapshot(self._page)
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
            st = self._reducer.state
            if st.phase == "idle" and st.idle_streak >= self._idle_streak_required:
                return
            if st.phase == "error":
                raise AiRouterError("GEMINI_ERROR", st.error_text or "Gemini error")
            await asyncio.sleep(0.1)
        raise TimeoutError_("Timed out waiting for idle browser state")
