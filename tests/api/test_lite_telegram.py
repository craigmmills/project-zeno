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
from src.api.config import APISettings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_telegram_settings_and_cache():
    old_secret = APISettings.telegram_webhook_secret
    old_token = APISettings.telegram_bot_token
    old_char_limit = APISettings.lite_telegram_message_char_limit

    APISettings.telegram_webhook_secret = "test-secret"
    APISettings.telegram_bot_token = "test-token"
    APISettings.lite_telegram_message_char_limit = 4096
    _telegram_update_cache.clear()

    yield

    APISettings.telegram_webhook_secret = old_secret
    APISettings.telegram_bot_token = old_token
    APISettings.lite_telegram_message_char_limit = old_char_limit
    _telegram_update_cache.clear()


async def test_telegram_webhook_rejects_invalid_secret(client):
    response = await client.post(
        "/api/lite/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 401


async def test_telegram_webhook_accepts_and_schedules_background_processing(client):
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
        patch("src.api.app._send_telegram_message", new=AsyncMock()) as send_message,
    ):
        await _process_telegram_update(update_id=3003, parsed=parsed)

    send_message.assert_awaited_once_with(
        bot_token="test-token",
        chat_id=2,
        text=TELEGRAM_FRIENDLY_ERROR_TEXT,
        message_thread_id=None,
    )


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
            ]
        }
    )

    with (
        patch("src.api.app.fetch_zeno", new=AsyncMock(return_value=mock_agent)) as fetch,
        patch("src.api.app.get_small_model", side_effect=RuntimeError("skip model rewrite")),
    ):
        result = await _run_lite_agent_for_telegram(
            query="hello",
            thread_id="telegram:77",
            user_id=77,
        )

    assert result == "Short summary"
    fetch.assert_awaited_once_with(channel="lite")
    _, kwargs = mock_agent.ainvoke.await_args
    assert kwargs["config"]["configurable"]["thread_id"] == "telegram:77"
    assert kwargs["config"]["metadata"]["channel"] == "lite"
