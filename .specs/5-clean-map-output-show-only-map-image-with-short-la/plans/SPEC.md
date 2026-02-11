# SPEC: Clean map output in Telegram (map image + short label only)

## Goal
When users request a map in Telegram, send only:
1. the map image, and
2. a very short caption label (example: `Tree cover loss - Spain`).

Do **not** use the long assistant summary text as the map caption.

---

## Current behavior (problem)
In `src/api/app.py`, map callback flow currently builds map caption from:
- `context["summary_text"]` (full agent response), then
- truncates with `APISettings.lite_map_caption_max_chars` (default `1024`).

Code path:
- `_process_telegram_update(...)`
- callback branch `action == TELEGRAM_CALLBACK_ACTION_MAP`
- caption logic near existing `map_caption = str(context.get("summary_text") or "")`

This makes map captions verbose/cluttered.

---

## Proposed behavior
For Telegram map sends, caption should be a short label derived from metadata:
- preferred format: `<dataset_name> - <aoi_name>`
- fallback order:
  - dataset + AOI
  - dataset only
  - AOI only
  - `Map`
- sanitize whitespace and trim punctuation
- enforce short max length (configurable)

Examples:
- `Tree cover loss - Spain`
- `Natural lands - Pará`
- `Spain`

---

## Scope
### In scope
- Map callback caption generation (`TELEGRAM_CALLBACK_ACTION_MAP` flow)
- Config default for map caption max chars (shorter default)
- Unit tests for caption behavior and callback map send payload

### Out of scope
- Chart caption behavior
- Telegram text-message formatting behavior
- Map rendering image generation logic (`src/api/map_renderer.py`)

---

## Implementation plan

### 1) Add a dedicated map-caption builder in `src/api/app.py`
Create helper(s) near other Telegram helpers:

- `_build_telegram_map_caption(context: Dict[str, Any]) -> str`
  - read `dataset_name` from `context["dataset"]` if dict
  - read AOI display name from `context["aoi"]` (`name` preferred, then `src_id` fallback)
  - normalize spaces and strip
  - compose concise label with ` - ` delimiter
  - apply hard cap: `APISettings.lite_map_caption_max_chars`
  - final fallback: `"Map"`

Optional small helper:
- `_clean_caption_part(value: Any) -> str`

Notes:
- Do **not** use `summary_text` for map caption.
- Keep helper deterministic and side-effect free.

### 2) Replace existing map caption logic in callback map flow
In `_process_telegram_update(...)` under `action == TELEGRAM_CALLBACK_ACTION_MAP`:

Replace:
- `map_caption = str(context.get("summary_text") or "")`
- truncation block

With:
- `map_caption = _build_telegram_map_caption(context)`

No other flow changes required.

### 3) Tighten config default in `src/api/config.py`
Change default:
- `lite_map_caption_max_chars` from `1024` -> `80` (or 96; pick one and keep consistent)

Recommendation: `80` to match “very short label”.

### 4) Update `.env.example`
Update:
- `LITE_MAP_CAPTION_MAX_CHARS=1024` -> `LITE_MAP_CAPTION_MAX_CHARS=80`

Keep variable name unchanged (no migration needed).

---

## Test plan

### Update existing tests in `tests/api/test_lite_telegram.py`

1. **`test_callback_map_flow_sends_photo`**
   - assert `_send_telegram_photo` called with concise caption
   - example expected caption: `Dataset - BRA` (or `Dataset - <name>` depending fixture)

2. **`test_callback_map_with_missing_tile_url_still_sends_photo`**
   - assert caption still short and derived from metadata, not summary text

3. **`test_callback_map_with_non_dict_dataset_safely_handled`**
   - assert dataset fallback works and caption uses AOI fallback

### Add focused unit tests for caption helper
Add tests (same file or new `tests/api/test_lite_telegram_caption.py`):

- `test_build_telegram_map_caption_prefers_dataset_and_aoi`
- `test_build_telegram_map_caption_fallback_dataset_only`
- `test_build_telegram_map_caption_fallback_aoi_only`
- `test_build_telegram_map_caption_fallback_map_literal`
- `test_build_telegram_map_caption_truncates_to_setting`
- `test_build_telegram_map_caption_ignores_summary_text`

### Suggested test command
- `uv run pytest tests/api/test_lite_telegram.py -v`

Optional full run:
- `uv run pytest tests/ -v`

---

## Acceptance criteria
- Map send in Telegram callback includes image + short caption only.
- Caption no longer contains long assistant summary text.
- Caption format is concise and metadata-based (`dataset - aoi` when available).
- Caption length respects `LITE_MAP_CAPTION_MAX_CHARS` (default short).
- Existing Telegram map callback tests pass and new caption tests pass.

---

## Risks / edge cases
- AOI may not include `name`; fallback to `src_id` required.
- Dataset may be absent or not dict; must not raise.
- Very long dataset names still possible; truncation handles this.
- Hyphen choice (`-` vs `—`) should remain stable for tests and UX.

---

## Rollout notes
- Backward-compatible env var (same key).
- Behavior change is limited to map callback caption text.
- No DB migration, no API contract changes.
