[![CI](https://github.com/William-Yeh/add-description/actions/workflows/ci.yml/badge.svg)](https://github.com/William-Yeh/add-description/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/agentskills.io-compatible-blueviolet)](https://agentskills.io)

# add-description

Add an SEO-friendly `description:` field to markdown frontmatter using a local Ollama model. Language is auto-detected: Taiwan-style Traditional Chinese for CJK-majority files, English otherwise.

## Installation

### Recommended: `npx skills`

```bash
npx skills add William-Yeh/add-description
```

### Manual installation

Copy the skill directory to your agent's skill folder:

| Agent | Directory |
|-------|-----------|
| Claude Code | `~/.claude/skills/` |
| Cursor | `.cursor/skills/` |
| Gemini CLI | `.gemini/skills/` |
| Amp | `.amp/skills/` |
| Roo Code | `.roo/skills/` |
| Copilot | `.github/skills/` |

**Prerequisites:** [Ollama](https://ollama.com) running locally, `uv` available in PATH.

## How it works

Generation runs in up to three passes:

1. **Draft pass** (local Ollama) — extracts core arguments and keywords from the article body (~150–200 words). Long articles are truncated with a first+last strategy (`BODY_LIMIT=6000`, `BODY_TAIL=2000`) so both the opening and closing sections are always visible, bridged by a `[……]` marker.

2. **Refine pass** (local Ollama) — rewrites the draft into a polished ~100-word SEO description. This pass enforces direct-knowledge prose: the model is explicitly forbidden from using the article or its author as a subject — no "this article explores", "the author argues", "文章探討了", "作者指出", or any similar meta-narration regardless of verb tense. The result reads as direct knowledge, quotable by AI answer engines without post-editing.

3. **Polish pass** (Claude API, optional, zh-TW only) — corrects mainland Chinese terms and simplified characters to Taiwan usage (e.g. 軟件→軟體, 代碼→程式碼, 接口→介面). Activated when `ANTHROPIC_API_KEY` is set; skipped silently otherwise, leaving the local model output as-is.

Additional guardrails: title-repetition retry, minimum length check (60 CJK chars / 30 EN words), and optional length warning on over-long output.

## Credits

Inspired by Jacob Mei's blog post "[繁體中文專用 Obsidian 語意搜尋插件 Vault Search](https://jacobmei.com/blog/2026/0404-n6nst4/)": using a local `qwen3:1.7b` model to generate a 50–100 character summary for each note and store it in the frontmatter `description` field.

## Usage

After installing, try these prompts with your agent:

- `Add a description to wiki/sources/my-article.md`
- `Preview what descriptions would be generated for all markdown files in wiki/sources/ without modifying them`
- `Generate SEO descriptions for The-Culture-Map.md and Small-Talk.md`

### Slash command

```
/add-description wiki/sources/my-article.md
/add-description --dry-run wiki/sources/*.md
/add-description wiki/sources/The-Culture-Map.md wiki/synthesis/Small-Talk.md
```
