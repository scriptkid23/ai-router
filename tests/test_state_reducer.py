import re
import time

from ai_router.browser.state import StateReducer

STREAM_RE = re.compile(r"StreamGenerate")


def test_idle_after_quiet_dom_ticks():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=0, response_text="", error_text=None)
    assert r.state.phase == "idle"
    assert r.state.idle_streak == 3


def test_generating_when_stop_visible():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    assert r.state.phase == "generating"
    assert r.state.generating_streak == 2


def test_error_on_1095_marker():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=("something went wrong",),
    )
    r.apply_dom_tick(
        generating=False,
        response_count=0,
        response_text="",
        error_text="Something went wrong (1095)",
    )
    assert r.state.phase == "error"


def test_stream_generate_sets_timestamp():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    r.mark_submitting()
    before = time.time()
    r.apply_request_finished(
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
    )
    assert r.state.last_stream_at is not None
    assert r.state.last_stream_at >= before
    assert r.state.saw_generating_this_job is True


def test_stream_end_ignored_without_submit():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is False


def test_stream_end_reset_when_stream_resumes():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=5.0,
        error_markers=(),
    )
    r.mark_submitting()
    url = (
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
    )
    r.apply_request_finished(url)
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is True
    time.sleep(0.01)
    r.apply_request_finished(url)
    assert r.state.saw_stream_end_this_job is False
    assert r.state.stream_ended_at is None


def test_stream_end_requires_stream_after_submit():
    r = StateReducer(
        page_id="test",
        stream_url_res=[STREAM_RE],
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        stream_quiet_s=1.5,
        error_markers=(),
    )
    r.mark_submitting()
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is False
    r.apply_request_finished(
        "https://gemini.google.com/_/BardChatUi/data/"
        "assistant.lamda.BardFrontendService/StreamGenerate"
    )
    r.apply_stream_end()
    assert r.state.saw_stream_end_this_job is True
