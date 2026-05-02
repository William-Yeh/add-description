---
name: add-description
description: >
  Use when adding or generating a `description:` field in markdown frontmatter
  for SEO or metadata purposes. Invoke with /add-description.
license: Apache-2.0
compatibility: Requires ollama running locally and uv available in PATH.
allowed-tools: Bash(ollama:*) Bash(uv:*) Read Edit
metadata:
  author: William Yeh <william.pjyeh@gmail.com>
  version: "0.1.0"
---

## Steps

### 1. Parse arguments

Strip `--dry-run` from the argument list and set `DRY_RUN=true` if present.
Remaining tokens are the file path list. If empty, abort:

```
Error: no files specified.
Usage: /add-description [--dry-run] <file> [<file> ...]
```

---

### 2. Check Ollama and select model

Probe local models in priority order. For each row, run
`ollama list 2>/dev/null | grep -qi <regex>`; the first match wins.

| Priority | Model         | Regex                |
|----------|---------------|----------------------|
| 1        | `qwen3.5:9b`  | `qwen3\.5:9b`        |
| 2        | `qwen3:8b`    | `qwen3:8b`           |
| 3        | `qwen3:1.7b`  | (fallback, no probe) |

Set `MODEL` to the first matching name. If `ollama list` itself fails (Ollama not running), abort all files with:
```
Error: Ollama is not running. Start it with: ollama serve
```

Report the selected model before processing.

---

### 3. For each file

#### 3a. Read the file

Read the full file content.

#### 3b. Guard checks (skip if any fail)

| Check | Skip message |
|-------|-------------|
| File has no leading `---` block | `SKIP <file> — no frontmatter found` |
| Frontmatter already contains `description:` | `SKIP <file> — description already exists` |

#### 3c. Generate description via script

Run the generation script, passing the selected model and file path:

```bash
uv run scripts/generate_description.py --model "$MODEL" "$FILE"
```

The script handles language detection, body truncation (first 4 000 + last 2 000 chars
for long articles, with a `[……]` omission marker inserted between the two chunks),
two-pass prompt construction (coverage-first draft → GEO-style refinement, tech-blogger
persona, `temperature=0.5`/`repeat_penalty=1.1`), Ollama API call, post-processing
(prefix stripping, title-repetition retry), length validation, and an optional
Claude API polish pass for zh-TW content (converts mainland Chinese terms and
simplified characters to Taiwan usage — activated when `ANTHROPIC_API_KEY` is set).

To experiment with a heterogeneous draft/refine pipeline, pass `--draft-model <model>`
explicitly. The script uses it for pass 1 only; pass 2 and retry always use `--model`.

- **stdout**: the generated description text
- **stderr**: `ABORT: …` if too short, `WARN: …` if too long
- **exit 0**: proceed to write; **exit 1**: abort this file

Capture stdout as `DESC` and stderr as `SCRIPT_ERR`. If exit code is 1,
log `ABORT <file> — <SCRIPT_ERR>` and move to the next file.

#### 3d. Write or preview

**Dry-run (`DRY_RUN=true`)** — print to stdout only:
```
[DRY-RUN] wiki/sources/foo.md
  model    : hf.co/audreyt/...
  ────────────────────────────────────────
  <generated description text>
  ────────────────────────────────────────
```

**Normal mode** — insert `description:` as the **last key before the closing `---`**
of the frontmatter block using the Edit tool.

Use YAML block scalar style for readability:
```yaml
description: >-
  Generated description text here. Multiple sentences flow as a single
  paragraph when rendered.
```

Use single-line style only if the description contains no special characters
and is under 80 characters.

---

### 4. Print summary table

After all files are processed:

```
STATUS   FILE                                             DETAIL
──────   ──────────────────────────────────────────────   ────────────────────────────
DONE     wiki/sources/foo.md                              zh-TW · 97 chars · qwen3:8b
DONE     wiki/sources/bar.md                              en · 103 words · qwen3:8b
SKIP     wiki/sources/baz.md                              description already exists
ABORT    wiki/sources/qux.md                              too short (22 chars)
[DRY]    wiki/sources/quux.md                             zh-TW · 88 chars · qwen3:1.7b
```

---

## Quality rules (reference)

| Rule | Detail |
|------|--------|
| No title repetition | Regenerate once if description opens with verbatim title |
| Minimum length enforced | Abort file if below threshold — do not write short descriptions |
| Language must match content | zh-TW for CJK-majority files, English otherwise |
| No meta-commentary | Zero tolerance for article/author as subject — `文章探討了` / `本文介紹了` / `作者指出` / `this article explores` / `the author argues` — regardless of verb tense; enforced in both LLM passes |
| No file modification in dry-run | Preview only; the Edit tool must not be called |

---

## Notes

- Do **not** append to `wiki/log.md` — this is a utility, not a knowledge ingest operation.
- If a file has no frontmatter, skip it rather than creating one.
- Glob expansion (`*.md`) is done by the shell before the skill receives arguments;
  pass pre-expanded paths if your shell does not expand them automatically.
