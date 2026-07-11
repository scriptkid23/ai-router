import re

from ai_router.browser.state import StateReducer

STREAM_RE = re.compile(r"StreamGenerate")


def test_answer_not_ready_without_generating_phase():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=("something went wrong",),
    )
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=1, response_text="hi", error_text=None)
    assert r.answer_ready(before_count=0) is False


def test_answer_ready_after_full_cycle():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=2,
        generating_streak_required=1,
        answer_stable_ticks=2,
        stream_quiet_s=0.0,
        error_markers=(),
    )
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    assert r.answer_ready(before_count=0) is True


def test_answer_not_ready_with_stream_end_while_stop_visible():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=6,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=0.0,
        error_markers=(),
    )
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.mark_submitting()
    r.apply_request_finished(
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
    )
    r.apply_stream_end()
    r.apply_dom_tick(generating=True, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=True, response_count=1, response_text="answer", error_text=None)
    checks = r.answer_ready_checks(before_count=0, generating=True)
    assert r.answer_ready(before_count=0, generating=True) is False
    assert checks["stream_end"] is True
    assert checks["stream_quiet"] is False


def test_answer_ready_after_stream_end_and_stop_gone():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=6,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=0.0,
        error_markers=(),
    )
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.mark_submitting()
    r.apply_request_finished(
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
    )
    r.apply_stream_end()
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    checks = r.answer_ready_checks(before_count=0, generating=False)
    assert r.answer_ready(before_count=0, generating=False) is True
    assert checks["stream_end"] is True
    assert checks["stream_quiet"] is True
