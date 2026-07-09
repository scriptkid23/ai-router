from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Literal

from ai_router.logger import trace

STREAM_GENERATE_RE = re.compile(
    r"assistant\.lamda\.BardFrontendService/StreamGenerate", re.I
)

Phase = Literal["idle", "submitting", "generating", "error", "closed"]


@dataclass
class BrowserState:
    phase: Phase = "idle"
    generating_streak: int = 0
    idle_streak: int = 0
    last_stream_at: float | None = None
    error_text: str | None = None
    response_count: int = 0
    last_response_text: str = ""
    response_stable_streak: int = 0
    saw_generating_this_job: bool = False


class StateReducer:
    def __init__(
        self,
        *,
        page_id: str,
        idle_streak_required: int,
        generating_streak_required: int,
        answer_stable_ticks: int,
        error_markers: tuple[str, ...],
    ) -> None:
        self._page_id = page_id
        self._job_id: str | None = None
        self._idle_required = idle_streak_required
        self._gen_required = generating_streak_required
        self._answer_stable = answer_stable_ticks
        self._error_markers = error_markers
        self.state = BrowserState()

    def set_job(self, job_id: str | None) -> None:
        self._job_id = job_id

    def _set_phase(self, phase: Phase, *, reason: str) -> None:
        if self.state.phase == phase:
            return
        prev = self.state.phase
        self.state.phase = phase
        trace(
            "phase_change",
            page_id=self._page_id,
            job_id=self._job_id,
            from_phase=prev,
            to_phase=phase,
            reason=reason,
            idle_streak=self.state.idle_streak,
            gen_streak=self.state.generating_streak,
        )

    def mark_submitting(self) -> None:
        self._set_phase("submitting", reason="mark_submitting")

    def mark_closed(self) -> None:
        self._set_phase("closed", reason="mark_closed")

    def reset_job_cycle(self) -> None:
        self.state.saw_generating_this_job = False
        self.state.response_stable_streak = 0

    def apply_request_finished(self, url: str) -> None:
        if STREAM_GENERATE_RE.search(url):
            self.state.last_stream_at = time.time()

    def apply_dom_tick(
        self,
        *,
        generating: bool,
        response_count: int,
        response_text: str,
        error_text: str | None,
    ) -> None:
        st = self.state
        if error_text and any(m in error_text.lower() for m in self._error_markers):
            st.error_text = error_text
            self._set_phase("error", reason="dom_error_marker")
            return

        st.response_count = response_count
        if response_text and response_text == st.last_response_text:
            st.response_stable_streak += 1
        else:
            st.last_response_text = response_text
            st.response_stable_streak = 1 if response_text else 0

        if generating:
            st.generating_streak += 1
            st.idle_streak = 0
            self._set_phase("generating", reason="dom_generating")
            if st.generating_streak >= self._gen_required:
                st.saw_generating_this_job = True
        else:
            st.idle_streak += 1
            st.generating_streak = 0
            if st.idle_streak >= self._idle_required:
                self._set_phase("idle", reason="dom_idle")

    def answer_ready_checks(self, *, before_count: int) -> dict[str, bool]:
        st = self.state
        return {
            "saw_generating": st.saw_generating_this_job,
            "new_response": st.response_count > before_count,
            "phase_idle": st.phase == "idle",
            "idle_enough": st.idle_streak >= self._idle_required,
            "stable_enough": st.response_stable_streak >= self._answer_stable,
            "has_text": bool(st.last_response_text),
        }

    def answer_ready(self, *, before_count: int) -> bool:
        return all(self.answer_ready_checks(before_count=before_count).values())
