from ai_router.adapters.gemini.planner import GeminiPlanner
from ai_router.adapters.gemini.selectors import GEMINI_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "gemini", fut, 120.0)


def test_plan_opens_fresh_chat_first():
    # stateless invariant: every ask starts its own fresh chat, inside the job
    cmds = GeminiPlanner().plan(make_job())
    ops = [c.op for c in cmds]
    assert ops == [
        "goto",
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]
    assert cmds[0].args["url"] == GEMINI_URL


def test_recovery_plan_also_opens_fresh_chat():
    cmds = GeminiPlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[0].args["url"] == GEMINI_URL
    assert cmds[1].op == "wait_idle"
