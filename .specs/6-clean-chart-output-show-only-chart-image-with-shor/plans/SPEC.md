# SPEC: Clean Telegram chart output (image + short label only)

## Goal
When a chart is sent in Telegram, the chart message should contain only:
1. the chart image, and
2. a very short caption label.

No long chart explanation/insight text should be attached to the chart message.

---

## Current behavior (problem)
In `src/api/app.py`, chart captions are built from `selected_chart["insight"]` in both chart send paths:
- callback chart flow (`lz:chart:<token>`)
- non-button message flow (auto chart pipeline)

`insight` is often 2–3 sentences, so Telegram chart posts become cluttered.

Also, in non-button mode (`LITE_TELEGRAM_ENABLE_MAP_BUTTONS=false`), the bot sends text summary first and then sends a chart, which can violate “chart-only output” expectations.

---

## Scope
### In scope
- Chart caption generation for Telegram chart sends
- Optional chart-only behavior in non-button mode
- Tests for caption formatting + chart send behavior
- Env/config defaults for short captions

### Out of scope
- Map caption behavior
- Agent/tool chart generation schema changes
- Frontend (Streamlit/Next.js) chart rendering

---

## Functional requirements
1. **Short caption source**
   - Use chart title as primary label (`chart["title"]`), not `insight`.
   - If title is missing/blank, fallback to a compact single-line label derived from chart type (e.g. `"Line chart"`, `"Bar chart"`, default `"Chart"`).

2. **Caption normalization**
   - Collapse whitespace/newlines to single spaces.
   - Strip leading/trailing whitespace.
   - Enforce short length cap (default: 80 chars).
   - Never send a blank caption.

3. **Chart-only output in auto-chart path (non-button mode)**
   - When `LITE_TELEGRAM_ENABLE_MAP_BUTTONS=false` and a chart is successfully selected/rendered/sent:
     - send only the chart photo with short caption
     - do **not** send full summary text beforehand.
   - If chart is unavailable or send fails, fallback to normal text response behavior.

4. **Callback chart flow**
   - Continue sending chart photo in callback flow, but caption must use short-label builder (not insight).

---

## Implementation plan

### 1) Add caption helper in Telegram API flow
**File:** `src/api/app.py`

Add a helper near other Telegram helpers:
- `_fallback_chart_label(chart_type: Any) -> str`
- `_build_telegram_chart_caption(chart: Dict[str, Any]) -> str`

Expected behavior:
- `title = str(chart.get("title") or "").strip()`
- if no title, map chart type to compact label:
  - line/area/scatter/bar/stacked-bar/grouped-bar/pie -> `"<Type> chart"`
  - unknown -> `"Chart"`
- normalize whitespace via regex (`re.sub(r"\s+", " ", label)`)
- truncate to `APISettings.lite_chart_caption_max_chars`
- if empty after truncate, return `"Chart"`

### 2) Update chart send call sites to use helper
**File:** `src/api/app.py`

Replace both existing insight-based caption blocks:
- callback chart branch (around existing `caption = str(selected_chart.get("insight") ...)`)
- non-button message chart branch (same pattern)

with:
- `caption = _build_telegram_chart_caption(selected_chart)`

### 3) Enforce short default cap
**Files:**
- `src/api/config.py`
- `.env.example`

Change default:
- `lite_chart_caption_max_chars`: `1024 -> 80`
- `.env.example`: `LITE_CHART_CAPTION_MAX_CHARS=80`

(keeps env override support if operators need a different value)

### 4) Make non-button flow truly chart-only
**File:** `src/api/app.py`

Refactor `_process_telegram_update` message path ordering:
- Today: send summary text first, then maybe send chart.
- Target:
  1. run agent
  2. if `lite_telegram_enable_map_buttons` is true -> keep existing text + button UX
  3. if buttons are false:
     - attempt chart pipeline first
     - if chart sent successfully: return immediately (no summary text send)
     - if not sent: send summary text as fallback

Implementation detail:
- track `chart_sent = False` in non-button branch
- set `chart_sent = True` only after `_send_telegram_photo` succeeds
- guard summary text send behind `if not chart_sent:`

### 5) Keep logging semantics clear
**File:** `src/api/app.py`

Add/update log fields for observability:
- `chart_caption_source="title|fallback"`
- `chart_caption_length=<int>`
- `text_suppressed_for_chart_only=true|false` (non-button path)

No logging schema migration needed (structured logger can accept new keys).

---

## Test plan

### Unit/behavior tests
**File:** `tests/api/test_lite_telegram.py`

Add tests:
1. `test_build_telegram_chart_caption_prefers_title_and_truncates`
   - long title + configured max length
   - assert normalized + truncated output

2. `test_build_telegram_chart_caption_falls_back_when_title_missing`
   - missing title + known type
   - assert `"Line chart"` (etc)

3. `test_callback_chart_flow_uses_short_caption_not_insight`
   - chart with short title and long insight
   - assert `_send_telegram_photo(..., caption=<title-based short label>)`

4. `test_non_button_mode_chart_only_skips_summary_text`
   - set `APISettings.lite_telegram_enable_map_buttons = False`
   - mock successful chart select/render/send
   - assert `_send_telegram_photo` called once
   - assert `_send_telegram_message` not called for summary text

5. `test_non_button_mode_falls_back_to_text_when_chart_not_sent`
   - set `APISettings.lite_telegram_enable_map_buttons = False`
   - make chart selection fail (or render fail)
   - assert summary text is sent

### Regression checks
- Existing tests for button flow should continue passing:
  - `test_message_flow_sends_buttons_not_auto_chart`
- Existing callback/map tests should remain unchanged.

### Commands
Run:
- `uv run pytest tests/api/test_lite_telegram.py -v`
- optionally full API suite:
  - `uv run pytest tests/api/ -v`

---

## Rollout / compatibility
- Backward compatible at API interface level.
- Behavior change is user-facing for Telegram chart messages:
  - shorter chart captions
  - chart-only output in non-button mode when chart send succeeds
- If needed, operators can increase `LITE_CHART_CAPTION_MAX_CHARS` in env.

---

## Acceptance criteria
- Telegram chart messages no longer use long `insight` text as caption.
- Caption is short, single-line, and non-empty.
- In non-button mode, successful chart responses send only chart image + short label.
- All updated Telegram tests pass.