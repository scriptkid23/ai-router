from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from ai_router.adapters.gemini.selectors import STREAM_GENERATE_RE
from ai_router.logger import trace

Phase = Literal["idle", "submitting", "generating", "error", "closed"]


@dataclass
class BrowserState:
    phase: Phase = "idle"
    generating_streak: int = 0
    idle_streak: int = 0
    last_stream_at: float | None = None
    stream_ended_at: float | None = None
    saw_stream_end_this_job: bool = False
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
        stream_quiet_s: float,
        error_markers: tuple[str, ...],
    ) -> None:
        self._page_id = page_id
        self._job_id: str | None = None
        self._idle_required = idle_streak_required
        self._gen_required = generating_streak_required
        self._answer_stable = answer_stable_ticks
        self._stream_quiet_s = stream_quiet_s
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
        self.state.saw_stream_end_this_job = False
        self.state.stream_ended_at = None
        self.state.response_stable_streak = 0

    def apply_request_finished(self, url: str) -> None:
        if STREAM_GENERATE_RE.search(url):
            self.state.last_stream_at = time.time()
            self.state.saw_generating_this_job = True

    def apply_stream_end(self, *, url: str = "") -> None:
        st = self.state
        st.saw_stream_end_this_job = True
        st.stream_ended_at = time.time()
        st.saw_generating_this_job = True
        trace(
            "stream_end",
            page_id=self._page_id,
            job_id=self._job_id,
            url=url[:80] if url else None,
        )

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
            if self._stream_end_quiet(st, response_text=response_text):
                st.generating_streak = 0
                st.idle_streak += 1
                if st.idle_streak >= self._idle_required:
                    self._set_phase("idle", reason="stream_end_quiet")
            else:
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

    def _stream_end_quiet(self, st: BrowserState, *, response_text: str) -> bool:
        return (
            st.saw_stream_end_this_job
            and st.stream_ended_at is not None
            and (time.time() - st.stream_ended_at) >= self._stream_quiet_s
            and st.response_stable_streak >= self._answer_stable
            and bool(response_text)
        )

    def answer_ready_checks(self, *, before_count: int) -> dict[str, bool]:
        st = self.state
        idle_ok = st.phase == "idle" and st.idle_streak >= self._idle_required
        stream_quiet_ok = self._stream_end_quiet(st, response_text=st.last_response_text)
        return {
            "saw_generating": st.saw_generating_this_job or st.saw_stream_end_this_job,
            "new_response": st.response_count > before_count,
            "phase_ok": idle_ok or stream_quiet_ok,
            "stable_enough": st.response_stable_streak >= self._answer_stable,
            "has_text": bool(st.last_response_text),
            "stream_end": st.saw_stream_end_this_job,
            "stream_quiet": stream_quiet_ok,
        }

    def answer_ready(self, *, before_count: int) -> bool:
        checks = self.answer_ready_checks(before_count=before_count)
        required = (
            checks["saw_generating"],
            checks["new_response"],
            checks["phase_ok"],
            checks["stable_enough"],
            checks["has_text"],
        )
        return all(required)
