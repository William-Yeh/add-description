# ADR-0001: Two-Pass LLM Generation Architecture

Date: 2026-04-18

## Status

Accepted

## Context

The initial implementation used a single LLM call with a combined prompt asking the model to simultaneously extract content, detect key points, and produce polished SEO prose. With small local models (qwen3:1.7b, TAIDE 12B), this produced descriptions that either covered the article well but used academic phrasing ("本文探討...", "This article discusses..."), or were stylistically clean but only reflected the opening paragraphs.

Two additional problems drove the redesign:

1. **Coverage vs. style tension**: A single prompt cannot reliably enforce both "cover all major points" and "write direct, quotable prose" simultaneously. Small models tend to satisfy whichever constraint appears last in the prompt.
2. **Long articles**: A body > 3 000 chars was naively truncated at the head, losing the article's conclusion and later arguments.

## Decision

Split generation into two sequential passes:

**Pass 1 (draft)**: Coverage-focused summarizer. Target ~150–200 words. Relaxed style. Explicitly instructed to integrate head + tail if the `[……]` omission marker is present. Uses a generic summarizer persona.

**Pass 2 (refine)**: GEO-style rewriter. Target ~100 words. Strict rules: no meta-language, direct statements only, each sentence independently quotable, no CTA. Uses a senior tech blogger persona with Taiwan-specific Chinese style guidance.

Body truncation changed from a 3 000-char head-only slice to a 6 000-char first+last strategy (`BODY_LIMIT=6000`, `BODY_TAIL=2000`), with paragraph-break snapping on both ends and an explicit omission marker in the draft prompt.

## Consequences

- **2× API calls per file** — acceptable for a content authoring utility invoked intentionally, not in a hot path.
- **Better quality for CJK content with small models**: pass 1 gives TAIDE/qwen3 a relaxed task; pass 2 applies strict style rules to a clean draft rather than raw markdown.
- **Testability**: both passes are now independently testable. `build_draft_prompts` and `build_refine_prompts` are pure functions; `call_ollama` is the sole I/O boundary.
- **Template maintainability**: prompts live as named module-level constants (`_ZH_DRAFT_USER`, `_EN_REFINE_SYSTEM`, etc.) rather than inline strings, making them easy to read and adjust without touching logic.
- **Retry path simplified**: on title repetition, only the refine pass is retried (not the full pipeline), saving one API call.

---

## Amendment — 2026-04-18

### Problem

In production testing against ~150 real blog posts, TAIDE 12B Q8_0 consistently produced meta-narration despite the explicit prohibition in the refine pass: phrases like "文章探討了", "本文介紹了", "作者指出", "此外，文章還引用了" appeared throughout descriptions. The banned list at the time only covered future-tense forms ("文章將") and a handful of fixed phrases, missing past/present verb forms entirely.

The root cause is training data, not prompt length. TAIDE was trained heavily on formal document summarisation corpora where this style is the norm. Its priors override negative constraints that contradict them. qwen3 (instruction-tuned) follows the same prohibition reliably.

Comparison test on a representative article (9 916 bytes, zh-TW):

| Model | Meta-narration? | Notes |
|---|---|---|
| TAIDE 12B Q8_0 | ❌ present | Ignored prohibition; used "本文介紹了", "讀者" |
| qwen3:1.7b | ✅ clean | Direct prose; slightly thin on keywords |
| qwen3:8b | ✅ clean | Direct prose; concrete keyword coverage; best overall |

### Changes

**Prompt — draft pass**: persona changed from `文章摘要助手` ("article summariser") to `知識提取助手` ("knowledge extractor"), with an explicit prohibition example added to the user prompt. The "summariser" framing was the upstream source seeding meta-narration into the draft, which then leaked through the refine pass.

**Prompt — refine pass**: banned list expanded from a flat enumeration of specific phrases to a structured zero-tolerance rule covering:
- article-as-subject with **any** verb (探討了 / 指出 / 以⋯為例 / 引用 / 說明 / 介紹 / 建議 / 認為 / 分析 / 提到 / 強調 / 舉 / 並 / 還 / 將…)
- author-as-subject with **any** verb (認為 / 指出 / 以⋯為例 / 提到 / 強調 / 說明 / 分析 / 建議 / 引用 / 舉例…)
- location phrases (在文章中 / 在本文中…) and reader-directed hooks (讓你 / 讀者將…)

**`call_ollama`**: added `_THINK_TAG` stripping. qwen3 thinking models emit `<think>…</think>` blocks in the raw `/api/generate` response field; stripping at the I/O boundary keeps all callers clean.

**Model selection** (SKILL.md): switched primary from TAIDE 12B to `qwen3:8b`, fallback to `qwen3:1.7b`. TAIDE remains usable via explicit `--model` override but is no longer the default.

---

## Amendment — 2026-04-19

### Problem

Production testing with qwen3:8b across ~144 zh-TW blog posts revealed a consistent quality gap in the final descriptions: mainland Chinese terminology (軟件, 代碼, 接口, 數據, 調用…) and simplified characters appeared in output despite the refine pass using a Taiwan-style persona. The root cause is model training data — qwen3 was trained on predominantly PRC-origin corpora, so its Chinese defaults to mainland norms even when instructed otherwise via persona.

This is distinct from the meta-narration problem (which qwen3 handles correctly): prohibition-following and dialect consistency are separate capabilities. Prohibition following is a matter of instruction compliance; dialect is a matter of vocabulary prior.

### Decision

Add an optional **pass 3 (polish)** that calls the Claude API (`claude-haiku-4-5`) to correct mainland Chinese terms to Taiwan equivalents and ensure natural Taiwan prose flow. This pass:

- Runs only for zh-TW content
- Is activated when `ANTHROPIC_API_KEY` is set; skips silently otherwise
- Degrades gracefully on API failure (returns local model output unchanged, logs a WARN)

### Rationale for hybrid architecture

- **Passes 1–2 (local)**: Extraction and structure — qwen3 handles these well and reliably follows prohibition rules. Running them locally is free, private, and works offline.
- **Pass 3 (Claude API)**: Dialect correction — a targeted ~100-token-in / ~100-token-out task where Claude's PRC→Taiwan vocabulary substitution is reliable and cheap (~$0.001/file with Haiku). Making passes 1–2 fully Claude-powered would cost ~10× more with no quality gain on the extraction task.
- **Optional by design**: The skill remains fully functional without `ANTHROPIC_API_KEY`. Output quality degrades gracefully rather than failing.

### Consequences

- **3× API calls per file** when `ANTHROPIC_API_KEY` is set (2 Ollama + 1 Claude), otherwise 2×.
- **`validate_length` runs after polish**: The length check now reflects the final polished text. Polish typically increases CJK char count slightly (e.g. 代碼→程式碼 adds one char); no observed failures from the ordering.
- **`anthropic` added to `# dependencies`**: `uv run` always installs it, so the deferred import inside `polish_zh_tw` is a startup-cost optimization rather than a hard conditional.
- **Test coverage**: `polish_zh_tw` is covered by three unit tests (no-key skip, mocked success, exception fallback) plus one integration test that verifies the wiring inside `main()` for zh-TW content.
