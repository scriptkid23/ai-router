import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_router.browser.events import handle_response
from ai_router.browser.profile import ProviderProfile, ProviderSelectors, StreamDone


@pytest.mark.asyncio
async def test_handle_response_uses_body_when_read_response_bytes():
    captured = {}

    def parse_stream_done(status, body):
        captured["status"] = status
        captured["body"] = body
        return StreamDone(done=True, ok=True)

    profile = ProviderProfile(
        provider_id="kimi",
        stream_url_re=re.compile(r"ChatService/Chat"),
        parse_stream_done=parse_stream_done,
        is_stop_visible=AsyncMock(return_value=False),
        read_response_snapshot=AsyncMock(return_value=(0, "")),
        is_rate_limited=lambda t: False,
        submit_ready=AsyncMock(return_value=True),
        planner=MagicMock(),
        selectors=ProviderSelectors(prompt_input="#in", submit_button="#btn"),
        error_markers=(),
        recoverable_codes=(),
        read_response_bytes=True,
    )
    channel = MagicMock()
    channel.emit = AsyncMock()

    response = MagicMock()
    response.url = "https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat"
    response.status = 200
    response.finished = AsyncMock()
    response.body = AsyncMock(return_value=b"\x00\x00\x00\x00\x05{}")
    response.text = AsyncMock(side_effect=AssertionError("text() must not be called"))

    await handle_response(response, channel, [profile])

    assert captured["status"] == 200
    assert captured["body"] == b"\x00\x00\x00\x00\x05{}"
    channel.emit.assert_awaited_once()
