from ai_router.browser.state import StateReducer


def test_answer_not_ready_without_generating_phase():
    r = StateReducer(
        page_id="test",
        idle_streak_required=3,
        generating_streak_required=2,
        answer_stable_ticks=2,
        error_markers=("something went wrong",),
    )
    for _ in range(3):
        r.apply_dom_tick(generating=False, response_count=1, response_text="hi", error_text=None)
    assert r.answer_ready(before_count=0) is False


def test_answer_ready_after_full_cycle():
    r = StateReducer(
        page_id="test",
        idle_streak_required=2,
        generating_streak_required=1,
        answer_stable_ticks=2,
        error_markers=(),
    )
    r.apply_dom_tick(generating=True, response_count=0, response_text="", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    r.apply_dom_tick(generating=False, response_count=1, response_text="answer", error_text=None)
    assert r.answer_ready(before_count=0) is True
