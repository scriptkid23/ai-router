from ai_router.adapters.kimi.selectors import KIMI_NEW_CHAT_URL
from ai_router.adapters.kimi.planner import KimiPlanner
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "kimi", fut, 600.0)


def test_plan_opens_fresh_chat_url():
    cmds = KimiPlanner().plan(make_job())
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
    assert cmds[0].args["url"] == KIMI_NEW_CHAT_URL
    assert "new_chat" not in ops
