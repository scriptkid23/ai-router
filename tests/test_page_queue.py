import asyncio

import pytest

from ai_router.browser.page_queue import AskJob, PageQueue


@pytest.mark.asyncio
async def test_fifo_order():
    q = PageQueue()
    loop = asyncio.get_running_loop()
    f1 = loop.create_future()
    f2 = loop.create_future()
    await q.put(AskJob("j1", "s1", "p1", "gemini", f1, 120.0))
    await q.put(AskJob("j2", "s2", "p2", "gemini", f2, 120.0))
    j1 = await q.get()
    j2 = await q.get()
    assert j1.job_id == "j1"
    assert j2.job_id == "j2"


@pytest.mark.asyncio
async def test_future_resolve():
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    job = AskJob("j1", "s1", "hello", "gemini", fut, 120.0)
    job.future.set_result("42")
    assert await fut == "42"
