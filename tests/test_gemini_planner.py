from ai_router.adapters.gemini.planner import GeminiPlanner
from ai_router.browser.commands import Command
from ai_router.browser.page_queue import AskJob


def test_plan_returns_six_commands():
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    job = AskJob("1", "sess", "hello", "gemini", fut, 120.0)
    cmds = GeminiPlanner().plan(job)
    ops = [c.op for c in cmds]
    assert ops == [
        "wait_idle",
        "clear_input",
        "type",
        "submit",
        "wait_generating",
        "wait_answer",
    ]


def test_recovery_prepends_goto():
    import asyncio

    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    job = AskJob("1", "sess", "hello", "gemini", fut, 120.0)
    cmds = GeminiPlanner().plan(job, recovery=True)
    assert cmds[0].op == "goto"
    assert cmds[1].op == "wait_idle"
