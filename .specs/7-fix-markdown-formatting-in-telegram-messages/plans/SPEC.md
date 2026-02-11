# SPEC: Fix markdown formatting in Telegram messages

## Goal

Ensure Telegram users never see raw markdown tokens (for example `**`,
`__`, `` ``` ``) in bot messages.

Per requirement, messages must either:

1. Render as valid Telegram formatting (preferred), or
2. Fall back to clean plain text with markdown removed.

## Current behavior and root cause

### Relevant code

- `src/api/app.py`
  - `_markdown_to_telegram_html(text: str) -> str`
  - `_send_telegram_message(...)`

### Observed issue

The API currently converts markdown to HTML and sends with
`parse_mode="HTML"`, but if Telegram returns `400`, it retries with the
**original raw text** and no parse mode:

- This retry can expose raw markdown characters directly to users.
- The converter also does not cover all markdown patterns, so some syntax
  can survive conversion.

## Design decision

Use a **two-layer defense**:

1. Improve markdown-to-HTML conversion for common patterns we generate.
2. Guarantee plain-text fallback strips markdown syntax before resend.

This avoids introducing a new dependency and keeps behavior deterministic.

## Scope

### In scope

- Telegram text message formatting (`sendMessage` path).
- Markdown cleanup/conversion utilities used by Telegram.
- Unit and async tests for conversion and fallback behavior.

### Out of scope

- Chart/image caption markdown behavior (`sendPhoto`) unless bug is
  reproduced there.
- Agent prompt/content strategy changes.
- Non-Telegram channels.

## Implementation plan

### 1) Add a markdown stripping utility for safe fallback

**File:** `src/api/app.py`

Add a helper:

- `_strip_markdown_to_plain_text(text: str) -> str`

Responsibilities:

- Remove/normalize markdown syntax while preserving readable content.
- Keep text semantic structure where possible.

Rules to implement (in order):

- Remove fenced code markers: `` ```lang `` and `` ``` ``.
- Replace inline code `` `value` `` with `value`.
- Convert links `[label](url)` to `label (url)`.
- Remove heading prefixes (`#`, `##`, etc.) but keep heading text.
- Convert list prefixes (`-`, `*`, `+`) to `• `.
- Strip emphasis markers:
  - `**text**`, `__text__`, `*text*`, `_text_` -> `text`
- Remove blockquote marker prefix `> `.
- Collapse excessive blank lines (`3+` -> `2`).
- Trim final output.

This helper is **fallback-safe** and should never return markdown symbols
as structural syntax.

### 2) Harden `_markdown_to_telegram_html`

**File:** `src/api/app.py`

Refine conversion to reduce parse failures and leftover markdown:

- Keep existing HTML escaping approach.
- Add explicit handling for both marker pairs:
  - bold: `**...**` and `__...__`
  - italic: `*...*` and `_..._`
- Preserve current heading/list conversion.
- Ensure all inserted content is escaped with `html_mod.escape`.
- After conversion, run a lightweight cleanup for obvious stray markdown
  markers that should not remain visible.

If conversion cannot safely represent a construct, prefer plain text rather
than passing raw markdown through.

### 3) Update Telegram retry path to use stripped fallback text

**File:** `src/api/app.py`

In `_send_telegram_message(...)`, modify 400 retry behavior:

Current retry:

- `payload["text"] = text`
- `del payload["parse_mode"]`

New retry:

- `fallback_text = _strip_markdown_to_plain_text(text)`
- `payload["text"] = fallback_text or text`
- Remove `parse_mode`
- Retry once

Also add debug logging for first-failure response body/status so we can
verify whether failures are parse-related.

### 4) Add regression tests

**File:** `tests/api/test_lite_telegram.py`

Add tests for formatting behavior:

1. `test_markdown_to_telegram_html_converts_common_markers()`
   - Input includes heading, bullets, `**bold**`, `_italic_`.
   - Assert output contains Telegram HTML tags (`<b>`, `<i>`), list bullets,
     and does **not** contain raw markdown markers.

2. `test_strip_markdown_to_plain_text_removes_markup_tokens()`
   - Input includes mixed markdown forms and unmatched tokens.
   - Assert readable plain output and no `**`, `__`, `` ``` ``, etc.

3. `test_send_telegram_message_400_retry_uses_plain_text_fallback()`
   - Mock `httpx.AsyncClient.post`:
     - first response `400`
     - second response `200`
   - Assert second payload:
     - omits `parse_mode`
     - uses stripped text (no raw markdown tokens).

### 5) Validate with targeted tests

Run:

- `uv run pytest tests/api/test_lite_telegram.py -v`

If available, also run broader API suite for safety:

- `uv run pytest tests/api/ -v`

## Error handling and risk notes

- Telegram `400` can happen for reasons beyond parse mode; retrying with
  plain text remains safe and user-friendly.
- Regex-based markdown handling is intentionally conservative; if a pattern
  is ambiguous, prefer stripping over risky HTML generation.
- Keep conversion helpers private to `app.py` unless later reused.

## Migration / rollout

No DB migration or infra change required.

Rollout steps:

1. Merge code + tests.
2. Deploy API service.
3. Verify live Telegram message samples no longer show raw markdown.
4. Monitor logs for first-pass `400` rate and fallback frequency.

## Acceptance criteria

- Telegram users do not see raw markdown markers in bot replies.
- Common markdown (headings, bold, italic, bullets) renders cleanly.
- On Telegram parse failure, retry sends clean plain text without markdown.
- New tests pass and guard against regression.
