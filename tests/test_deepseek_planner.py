from ai_router.adapters.deepseek.planner import DeepSeekPlanner
from ai_router.adapters.deepseek.selectors import DEEPSEEK_URL
from ai_router.browser.page_queue import AskJob


def make_job() -> AskJob:
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    return AskJob("1", "sess", "hello", "deepseek", fut, 600.0)


def test_plan_opens_fresh_chat():
    cmds = DeepSeekPlanner().plan(make_job())
    ops = [c.op for c in cmds]
    assert ops == [
        "goto",
        "new_chat",
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]
    assert cmds[0].args["url"] == DEEPSEEK_URL


def test_recovery_plan_also_starts_fresh():
    cmds = DeepSeekPlanner().plan(make_job(), recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[1].op == "new_chat"
