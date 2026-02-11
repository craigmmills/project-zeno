from unittest.mock import AsyncMock, patch

import pytest

from src.api.app import (
    TELEGRAM_FRIENDLY_ERROR_TEXT,
    _normalize_map_dataset,
    _parse_telegram_update,
    _process_telegram_update,
    _rewrite_lite_response,
    _run_lite_agent_for_telegram,
    _split_text_for_telegram,
    _telegram_interaction_cache,
    _telegram_update_cache,
)
from src.api.config import APISettings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def reset_telegram_settings_and_cache():
    old_secret = APISettings.telegram_webhook_secret
    old_token = APISettings.telegram_bot_token
    old_char_limit = APISettings.lite_telegram_message_char_limit
    old_enable_charts = APISettings.lite_telegram_enable_charts
    old_enable_buttons = APISettings.lite_telegram_enable_map_buttons
    old_enable_maps = APISettings.lite_telegram_enable_maps
    old_caption_max = APISettings.lite_chart_caption_max_chars
    old_map_caption_max = APISettings.lite_map_caption_max_chars

    APISettings.telegram_webhook_secret = "test-secret"
    APISettings.telegram_bot_token = "test-token"
    APISettings.lite_telegram_message_char_limit = 4096
    APISettings.lite_telegram_enable_charts = True
    APISettings.lite_telegram_enable_map_buttons = True
    APISettings.lite_telegram_enable_maps = True
    APISettings.lite_chart_caption_max_chars = 1024
    APISettings.lite_map_caption_max_chars = 1024

    _telegram_update_cache.clear()
    _telegram_interaction_cache.clear()

    yield

    APISettings.telegram_webhook_secret = old_secret
    APISettings.telegram_bot_token = old_token
    APISettings.lite_telegram_message_char_limit = old_char_limit
    APISettings.lite_telegram_enable_charts = old_enable_charts
    APISettings.lite_telegram_enable_map_buttons = old_enable_buttons
    APISettings.lite_telegram_enable_maps = old_enable_maps
    APISettings.lite_chart_caption_max_chars = old_caption_max
    APISettings.lite_map_caption_max_chars = old_map_caption_max

    _telegram_update_cache.clear()
    _telegram_interaction_cache.clear()


async def test_telegram_webhook_rejects_invalid_secret(client):
    response = await client.post(
        "/api/lite/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    assert response.status_code == 401


async def test_message_flow_sends_buttons_not_auto_chart():
    parsed = {
        "event_type": "message",
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
                return_value={
                    "text": "Result",
                    "charts_data": [chart],
                    "aoi": {"source": "gadm", "src_id": "BRA"},
                    "dataset": {"dataset_name": "Test dataset"},
                }
            ),
        ),
        patch(
            "src.api.app._generate_interaction_token",
            return_value="tok123",
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=100, parsed=parsed)

    send_photo.assert_not_called()
    assert send_message.await_count == 2
    _, kwargs = send_message.await_args_list[-1]
    assert kwargs["reply_markup"]["inline_keyboard"]
    assert "tok123" in str(kwargs["reply_markup"])
    assert _telegram_interaction_cache.get("tok123") is not None


async def test_no_data_response_suppresses_buttons():
    parsed = {
        "event_type": "message",
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": None,
        "text": "show trend",
    }

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(
                return_value={
                    "text": (
                        "Sorry, I couldn't get data for Cambridgeshire "
                        "with the selected filters."
                    ),
                    "charts_data": [],
                    "aoi": None,
                    "dataset": None,
                    "raw_data": {},
                }
            ),
        ),
        patch(
            "src.api.app._generate_interaction_token",
            return_value="tok123",
        ) as token,
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
    ):
        await _process_telegram_update(update_id=104, parsed=parsed)

    token.assert_not_called()
    assert send_message.await_count == 1
    assert all(
        "reply_markup" not in kwargs
        for _, kwargs in send_message.await_args_list
    )
    assert len(_telegram_interaction_cache) == 0


async def test_no_artifact_candidates_suppress_buttons():
    parsed = {
        "event_type": "message",
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": None,
        "text": "show trend",
    }

    with (
        patch("src.api.app._send_telegram_typing", new=AsyncMock()),
        patch(
            "src.api.app._run_lite_agent_for_telegram",
            new=AsyncMock(
                return_value={
                    "text": "Here is a summary without any visual output.",
                    "charts_data": [],
                    "aoi": None,
                    "dataset": None,
                    "raw_data": {},
                }
            ),
        ),
        patch(
            "src.api.app._generate_interaction_token",
            return_value="tok123",
        ) as token,
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
    ):
        await _process_telegram_update(update_id=105, parsed=parsed)

    token.assert_not_called()
    assert send_message.await_count == 1
    assert all(
        "reply_markup" not in kwargs
        for _, kwargs in send_message.await_args_list
    )
    assert len(_telegram_interaction_cache) == 0


async def test_failure_text_with_chart_candidate_still_shows_buttons():
    parsed = {
        "event_type": "message",
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
                return_value={
                    "text": "Sorry, I couldn't get data for one slice.",
                    "charts_data": [chart],
                    "aoi": None,
                    "dataset": None,
                    "raw_data": {},
                }
            ),
        ),
        patch(
            "src.api.app._generate_interaction_token",
            return_value="tok456",
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
    ):
        await _process_telegram_update(update_id=106, parsed=parsed)

    assert send_message.await_count == 2
    _, kwargs = send_message.await_args_list[-1]
    assert kwargs["reply_markup"]["inline_keyboard"]
    assert _telegram_interaction_cache.get("tok456") is not None


async def test_callback_chart_flow_sends_photo():
    token = "charttok"
    chart = {
        "id": "main_chart",
        "type": "line",
        "title": "Trend",
        "insight": "Insight",
        "data": [{"year": 2020, "value": 1}],
        "xAxis": "year",
        "yAxis": "value",
    }
    _telegram_interaction_cache[token] = {
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "query": "trend",
        "summary_text": "summary",
        "aoi": None,
        "dataset": None,
        "charts_data": [chart],
        "created_at": 0,
    }
    parsed = {
        "event_type": "callback",
        "callback_query_id": "cb1",
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "data": f"lz:chart:{token}",
    }

    with (
        patch(
            "src.api.app._send_telegram_callback_answer", new=AsyncMock()
        ) as callback_answer,
        patch(
            "src.api.app.select_best_chart_with_debug",
            return_value=(chart, []),
        ),
        patch("src.api.app.render_chart_png", return_value=b"PNG"),
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=101, parsed=parsed)

    callback_answer.assert_awaited_once()
    send_photo.assert_awaited_once()


async def test_callback_map_flow_sends_photo():
    token = "maptok"
    _telegram_interaction_cache[token] = {
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "query": "map",
        "summary_text": "summary",
        "aoi": {"source": "gadm", "src_id": "BRA"},
        "dataset": {"dataset_name": "Dataset"},
        "charts_data": [],
        "created_at": 0,
    }
    parsed = {
        "event_type": "callback",
        "callback_query_id": "cb2",
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "data": f"lz:map:{token}",
    }

    with (
        patch("src.api.app._send_telegram_callback_answer", new=AsyncMock()),
        patch(
            "src.api.app.render_map_png", new=AsyncMock(return_value=b"PNG")
        ),
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=102, parsed=parsed)

    send_photo.assert_awaited_once()


async def test_callback_map_with_missing_tile_url_still_sends_photo():
    token = "map-no-tiles"
    _telegram_interaction_cache[token] = {
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "query": "map",
        "summary_text": "summary",
        "aoi": {"source": "gadm", "src_id": "BRA"},
        "dataset": {"dataset_name": "Dataset only"},
        "charts_data": [],
        "created_at": 0,
    }
    parsed = {
        "event_type": "callback",
        "callback_query_id": "cb2b",
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "data": f"lz:map:{token}",
    }

    with (
        patch("src.api.app._send_telegram_callback_answer", new=AsyncMock()),
        patch(
            "src.api.app.render_map_png", new=AsyncMock(return_value=b"PNG")
        ),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=1021, parsed=parsed)

    send_photo.assert_awaited_once()
    send_message.assert_not_called()


async def test_callback_map_with_non_dict_dataset_safely_handled():
    token = "map-bad-dataset"
    _telegram_interaction_cache[token] = {
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "query": "map",
        "summary_text": "summary",
        "aoi": {"source": "gadm", "src_id": "BRA"},
        "dataset": "dataset-string",
        "charts_data": [],
        "created_at": 0,
    }
    parsed = {
        "event_type": "callback",
        "callback_query_id": "cb2c",
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "data": f"lz:map:{token}",
    }

    with (
        patch("src.api.app._send_telegram_callback_answer", new=AsyncMock()),
        patch(
            "src.api.app.render_map_png", new=AsyncMock(return_value=b"PNG")
        ) as render_map,
        patch(
            "src.api.app._send_telegram_photo", new=AsyncMock()
        ) as send_photo,
    ):
        await _process_telegram_update(update_id=1022, parsed=parsed)

    send_photo.assert_awaited_once()
    _, kwargs = render_map.await_args
    assert kwargs["dataset"] is None


def test_normalize_map_dataset_keeps_safe_trimmed_fields():
    normalized = _normalize_map_dataset(
        {
            "dataset_name": "  Tree cover loss  ",
            "tile_url": "  https://tiles/{z}/{x}/{y}.png  ",
            "dataset_id": "  umd-glad  ",
            "extra": "drop-me",
        }
    )

    assert normalized == {
        "dataset_name": "Tree cover loss",
        "tile_url": "https://tiles/{z}/{x}/{y}.png",
        "dataset_id": "umd-glad",
    }


def test_normalize_map_dataset_returns_none_for_non_dict():
    assert _normalize_map_dataset("bad") is None


async def test_callback_expired_token_sends_friendly_message():
    parsed = {
        "event_type": "callback",
        "callback_query_id": "cb3",
        "chat_id": 2,
        "message_thread_id": None,
        "user_id": 1,
        "data": "lz:chart:missing-token",
    }

    with (
        patch("src.api.app._send_telegram_callback_answer", new=AsyncMock()),
        patch(
            "src.api.app._send_telegram_message", new=AsyncMock()
        ) as send_message,
    ):
        await _process_telegram_update(update_id=103, parsed=parsed)

    _, kwargs = send_message.await_args
    assert "expired" in kwargs["text"].lower()


async def test_unsupported_callback_payload_is_ignored_by_webhook(client):
    payload = {
        "update_id": 3003,
        "callback_query": {
            "id": "cb-id",
            "from": {"id": 88, "is_bot": False},
            "message": {
                "message_id": 10,
                "chat": {"id": 66, "type": "private"},
            },
            "data": "bad-payload",
        },
    }

    response = await client.post(
        "/api/lite/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["ignored"] == "unsupported_callback_payload"


async def test_telegram_webhook_deduplicates_callback_update_id(client):
    payload = {
        "update_id": 2002,
        "callback_query": {
            "id": "cb-id",
            "from": {"id": 88, "is_bot": False},
            "message": {
                "message_id": 10,
                "chat": {"id": 66, "type": "private"},
            },
            "data": "lz:chart:token123",
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


def test_parse_telegram_update_supports_callback_query():
    parsed = _parse_telegram_update(
        {
            "callback_query": {
                "id": "cb-id",
                "data": "lz:chart:abc",
                "from": {"id": 4},
                "message": {
                    "message_id": 10,
                    "message_thread_id": 7,
                    "chat": {"id": 2},
                },
            }
        }
    )

    assert parsed is not None
    assert parsed["event_type"] == "callback"
    assert parsed["chat_id"] == 2


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


async def test_run_lite_agent_uses_lite_channel_and_returns_context():
    mock_agent = AsyncMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [
                {"type": "ai", "content": "Short summary"},
            ],
            "charts_data": [{"id": "main_chart"}],
            "aoi": {"source": "gadm", "src_id": "BRA"},
            "dataset": {"dataset_name": "Dataset"},
            "raw_data": {"rows": [{"year": 2020, "value": 1}]},
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
    assert result["aoi"] == {"source": "gadm", "src_id": "BRA"}
    assert result["dataset"] == {"dataset_name": "Dataset"}
    assert result["raw_data"] == {"rows": [{"year": 2020, "value": 1}]}
    fetch.assert_awaited_once_with(channel="lite")


async def test_telegram_background_processing_sends_friendly_error_on_failure():
    parsed = {
        "event_type": "message",
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
        await _process_telegram_update(update_id=3004, parsed=parsed)

    send_message.assert_awaited_once_with(
        bot_token="test-token",
        chat_id=2,
        text=TELEGRAM_FRIENDLY_ERROR_TEXT,
        message_thread_id=None,
    )
