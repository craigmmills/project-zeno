from unittest.mock import AsyncMock, patch

import pytest

from src.api.app import (
    TELEGRAM_FRIENDLY_ERROR_TEXT,
    _process_telegram_update,
    _rewrite_lite_response,
    _run_lite_agent_for_telegram,
    _split_text_for_telegram,
    _telegram_update_cache,
)
from src.api.chart_renderer import ChartRenderError
from src.api.config import APISettings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_telegram_settings_and_cache():
    old_secret = APISettings.telegram_webhook_secret
    old_token = APISettings.telegram_bot_token
    old_char_limit = APISettings.lite_telegram_message_char_limit
    old_enable_charts = APISettings.lite_telegram_enable_charts
    old_caption_max = APISettings.lite_chart_caption_max_chars

    APISettings.telegram_webhook_secret = "test-secret"
    APISettings.telegram_bot_token = "test-token"
    APISettings.lite_telegram_message_char_limit = 4096
    APISettings.lite_telegram_enable_charts = True
    APISettings.lite_chart_caption_max_chars = 1024
    _telegram_update_cache.clear()

    yield

    APISettings.telegram_webhook_secret = old_secret
    APISettings.telegram_bot_token = old_token
    APISettings.lite_telegram_message_char_limit = old_char_limit
    APISettings.lite_telegram_enable_charts = old_enable_charts
    APISettings.lite_chart_caption_max_chars = old_caption_max
    _telegram_update_cache.clear()


async def test_telegram_webhook_rejects_invalid_secret(client):
    response = await client.post(
        "/api/lite/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 401


async def test_telegram_webhook_accepts_and_schedules_background_processing(
    client,
):
    payload = {
        "update_id": 1001,
        "message": {
            "message_id": 10,
            "from": {"id": 777, "is_bot": False},
            "chat": {"id": 555, "type": "private"},
            "text": "Analyze deforestation in Pará",
        },
    }

    def _close_coro(coro):
        coro.close()
        return None

    with patch("src.api.app.asyncio.create_task") as create_task:
        create_task.side_effect = _close_coro
        response = await client.post(
            "/api/lite/telegram/webhook",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["accepted"] is True
    create_task.assert_called_once()


async def test_telegram_webhook_deduplicates_update_id(client):
    payload = {
        "update_id": 2002,
        "message": {
            "message_id": 10,
            "from": {"id": 88, "is_bot": False},
            "chat": {"id": 66, "type": "private"},
            "text": "hello",
        },
    }

    def _close_coro(coro):
        coro.close()
        return None

    with patch("src.api.app.asyncio.create_task") as create_task:
        create_task.side_effect = _close_coro
        first = await client.post(
            "/api/lite/telegram/webhook",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )
        second = await client.post(
            "/api/lite/telegram/webhook",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("dedup") is True
    create_task.assert_called_once()


async def test_telegram_background_processing_sends_friendly_error_on_failure():
    parsed = {
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": None,
        "text": "hello",
    }

    with (
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
    ):
        await _process_telegram_update(update_id=3003, parsed=parsed)

    send_message.assert_awaited_once_with(
        bot_token="test-token",
        chat_id=2,
        text=TELEGRAM_FRIENDLY_ERROR_TEXT,
        message_thread_id=None,
    )


async def test_telegram_sends_photo_when_chart_exists():
    parsed = {
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": 99,
        "text": "show trend",
    }
    chart = {
        "id": "main_chart",
        "type": "line",
        "title": "Trend",
        "insight": "Insight",
        "data": [{"year": 2020, "value": 1}],
        "xAxis": "year",
        "yAxis": "value",
    }

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(
                return_value={"text": "Result", "charts_data": [chart]}
            ),
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
        patch(
            "src.api.app.select_best_chart_with_debug",
            return_value=(chart, []),
        ),
        patch("src.api.app.render_chart_png", return_value=b"\x89PNGmock"),
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=100, parsed=parsed)

    send_message.assert_awaited_once()
    send_photo.assert_awaited_once()


async def test_telegram_text_still_sent_when_render_fails():
    parsed = {
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": None,
        "text": "show trend",
    }
    chart = {
        "id": "main_chart",
        "type": "line",
        "title": "Trend",
        "insight": "Insight",
        "data": [{"year": 2020, "value": 1}],
        "xAxis": "year",
        "yAxis": "value",
    }

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(
                return_value={"text": "Result", "charts_data": [chart]}
            ),
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
        patch(
            "src.api.app.select_best_chart_with_debug",
            return_value=(chart, []),
        ),
        patch(
            "src.api.app.render_chart_png",
            side_effect=ChartRenderError("bad chart"),
        ),
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=101, parsed=parsed)

    send_message.assert_awaited_once()
    send_photo.assert_not_called()


async def test_telegram_caption_truncated_and_message_thread_id_passed():
    APISettings.lite_chart_caption_max_chars = 10

    parsed = {
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": 123,
        "text": "show trend",
    }
    chart = {
        "id": "main_chart",
        "type": "line",
        "title": "Trend",
        "insight": "Very long chart insight that should be truncated",
        "data": [{"year": 2020, "value": 1}],
        "xAxis": "year",
        "yAxis": "value",
    }

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(
                return_value={"text": "Result", "charts_data": [chart]}
            ),
        ),
        patch("src.api.app._send_telegram_message", new=AsyncMock()),
        patch(
            "src.api.app.select_best_chart_with_debug",
            return_value=(chart, []),
        ),
        patch("src.api.app.render_chart_png", return_value=b"\x89PNGmock"),
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=102, parsed=parsed)

    _, kwargs = send_photo.await_args
    assert len(kwargs["caption"]) == 10
    assert kwargs["message_thread_id"] == 123


async def test_telegram_no_charts_does_not_send_photo():
    parsed = {
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": None,
        "text": "show trend",
    }

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(return_value={"text": "Result", "charts_data": []}),
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=103, parsed=parsed)

    send_message.assert_awaited_once()
    send_photo.assert_not_called()


async def test_telegram_send_order_message_before_photo():
    parsed = {
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": None,
        "text": "show trend",
    }
    chart = {
        "id": "main_chart",
        "type": "line",
        "title": "Trend",
        "insight": "Insight",
        "data": [{"year": 2020, "value": 1}],
        "xAxis": "year",
        "yAxis": "value",
    }
    events: list[str] = []

    async def _record_message(**kwargs):
        events.append("message")

    async def _record_photo(**kwargs):
        events.append("photo")

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(
                return_value={"text": "Result", "charts_data": [chart]}
            ),
        ),
        patch(
            "src.api.app._send_telegram_message", side_effect=_record_message
        ),
        patch(
            "src.api.app.select_best_chart_with_debug",
            return_value=(chart, []),
        ),
        patch("src.api.app.render_chart_png", return_value=b"\x89PNGmock"),
        patch("src.api.app._send_telegram_photo", side_effect=_record_photo),
    ):
        await _process_telegram_update(update_id=104, parsed=parsed)

    assert events == ["message", "photo"]


def test_rewrite_lite_response_removes_ui_language_and_caps_words():
    text = "Click the map to continue.\n\n" + "word " * 400
    rewritten = _rewrite_lite_response(text, max_words=20)

    assert "Click the map" not in rewritten
    assert len(rewritten.split()) <= 20


def test_split_text_for_telegram_splits_and_numbers_parts():
    text = "A" * 4500
    parts = _split_text_for_telegram(text, max_chars=1000)

    assert len(parts) > 1
    assert parts[0].startswith("(1/")
    assert all(len(part) <= 1000 for part in parts)


async def test_run_lite_agent_uses_lite_channel_and_thread_id():
    mock_agent = AsyncMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [
                {"type": "ai", "content": "Short summary"},
            ],
            "charts_data": [{"id": "main_chart"}],
        }
    )

    with (
        patch(
            "src.api.app.fetch_zeno", new=AsyncMock(return_value=mock_agent)
        ) as fetch,
        patch(
            "src.api.app.get_small_model",
            side_effect=RuntimeError("skip model rewrite"),
        ),
    ):
        result = await _run_lite_agent_for_telegram(
            query="hello",
            thread_id="telegram:77",
            user_id=77,
        )

    assert result["text"] == "Short summary"
    assert result["charts_data"] == [{"id": "main_chart"}]
    fetch.assert_awaited_once_with(channel="lite")
    _, kwargs = mock_agent.ainvoke.await_args
    assert kwargs["config"]["configurable"]["thread_id"] == "telegram:77"
    assert kwargs["config"]["metadata"]["channel"] == "lite"
