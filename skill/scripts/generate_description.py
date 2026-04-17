#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "anthropic",
#   "httpx",
#   "python-frontmatter",
# ]
# ///

import os
import re
import sys
import types
import argparse
from pathlib import Path
from typing import Literal

import httpx
import frontmatter

MIN_ZH_CHARS = 60
MIN_EN_WORDS = 30
MAX_ZH_CHARS = 400
MAX_EN_WORDS = 200
BODY_LIMIT      = 6000
BODY_TAIL       = 2000  # tail chars for first+last strategy; head = BODY_LIMIT - BODY_TAIL
HEAD_SNAP_SLACK = 200   # snap to paragraph break only if within this many chars of head boundary
TAIL_SNAP_SLACK = 100   # skip leading partial line only if it ends within this many chars

Lang = Literal["zh-TW", "en"]

_BODY_OMISSION_MARKER = "[……]"
_ZH_PREFIX  = re.compile(r'^(本文摘要|摘要|描述)[：:]\s*')
_EN_PREFIX  = re.compile(r'^(Description|Here is[^.]*|This article (?:discusses|covers|explores)[^.]*)\s*[:.]\s*', re.IGNORECASE)
_REF_DEF    = re.compile(r'\n\[\^\d+\]:')
_THINK_TAG  = re.compile(r'<think>[\s\S]*?</think>', re.IGNORECASE)


def _is_cjk(c: str) -> bool:
    return "\u4e00" <= c <= "\u9fff"


def truncate_body(body: str) -> str:
    m = _REF_DEF.search(body)
    if m:
        body = body[:m.start()]
    if len(body) <= BODY_LIMIT:
        return body
    head_limit = BODY_LIMIT - BODY_TAIL
    chunk = body[:head_limit]
    head_break = chunk.rfind('\n\n')
    head = chunk[:head_break] if head_break > head_limit - HEAD_SNAP_SLACK else chunk

    raw_tail = body[-BODY_TAIL:]
    tail_break = raw_tail.find('\n')
    tail = raw_tail[tail_break + 1:] if 0 <= tail_break < TAIL_SNAP_SLACK else raw_tail
    return head + "\n\n" + _BODY_OMISSION_MARKER + "\n\n" + tail


def detect_lang(text: str) -> Lang:
    total = cjk = 0
    for c in text:
        if not c.isspace():
            total += 1
            if _is_cjk(c):
                cjk += 1
    if total == 0:
        return "en"
    return "zh-TW" if cjk / total > 0.1 else "en"


# ── Pass 1: content extraction (focus on coverage, relaxed style) ────────────

_ZH_DRAFT_SYSTEM = (
    "你是知識提取助手，使用台灣現代繁體中文風格。"
    "從文章中提取核心概念與論點，直接陳述知識內容；"
    "不描述文章結構，不以「文章」「作者」為主詞。"
)

_ZH_DRAFT_USER = """\
請從以下文章中提取核心論點與知識，整理成詳細段落，供後續改寫為 SEO description 使用。

要求：
- 使用台灣現代繁體中文風格
- 目標長度：約 150–200 字
- 涵蓋所有主要論點，不可只寫前半段
- 帶入文章核心關鍵字
- 直接陳述知識與結論；不使用「文章探討了」「作者指出」「在文章中」等間接句型
- 直接輸出文字，不加引號、不加前綴說明

標題：{title}

內容：
（若內容中出現 {omission}，表示中間段落已省略，開頭與結尾均為原文；請整合兩段資訊，確保涵蓋完整論點。）
{body}\
"""

_EN_DRAFT_SYSTEM = (
    "You are a knowledge extractor. Write in English. "
    "Extract and present core concepts and arguments directly — "
    "do not describe what the article says; do not use 'this article' or 'the author' as subjects."
)

_EN_DRAFT_USER = """\
Extract the core arguments and knowledge from the following article into a detailed paragraph \
for later rewriting as an SEO description.

Requirements:
- Write in English
- Target: ~150–200 words
- Cover all major points, not just the opening
- Include the article's core keywords
- Present knowledge directly; do not use "the article discusses", "the author argues", \
"in this article", or similar meta-commentary
- Output only the extracted text, no quotes, no prefixes

Title: {title}

Content:
(If the content contains {omission}, it marks an omitted middle section; the opening and closing \
are both from the original article. Integrate both parts to ensure full coverage.)
{body}\
"""

# ── Pass 2: style refinement (focus on GEO/direct prose, no article body) ────

_ZH_REFINE_SYSTEM = """\
你是資深技術部落客，熟悉台灣軟體業與工程文化。
用自然的台灣繁體中文寫 SEO 摘要：
- 句子節奏清晰，不堆疊從句
- 不像翻譯稿，不像學術論文
- 避免廢字：透過、進行、相關、處理\
"""

_ZH_REFINE_USER = """\
以下是一段文章摘要草稿，請改寫成精煉的 SEO description：

要求：
- 台灣現代繁體中文，自然口語
- 嚴格禁用一切中介語句型（零容忍）：
  - 以「文章」「本文」「這篇」「本篇」「此文」「該文」為主詞的句子，無論動詞為何
    （例：「文章探討了」「文章指出」「文章以⋯⋯為例」「文章引用」「文章說明」
         「文章介紹」「文章建議」「文章認為」「文章分析」「文章提到」「文章強調」
         「文章將」「文章舉」「文章並」「文章還」）
  - 以「作者」為主詞的句子，無論動詞為何
    （例：「作者認為」「作者指出」「作者以⋯⋯為例」「作者提到」「作者強調」
         「作者說明」「作者分析」「作者建議」「作者引用」「作者舉例」）
  - 其他間接句型：「在文章中」「在本文中」「在這篇」「將幫助」「將探討」「將分析」
    「讀者將」「看這篇」「讓你」「讓讀者」
- 不加行動呼籲（CTA）或誘惑性語言；不使用感嘆號
- 全部改成直述句；優先呈現具體結論與判準，而非描述文章結構
- 每句話獨立可讀，可被 AI 引擎直接截斷引用
- 目標長度：約 100 字
- 直接輸出改寫結果，不加說明、不加引號

草稿：
{draft}\
"""

_EN_REFINE_SYSTEM = """\
You are a senior tech blogger. Rewrite drafts into clean, direct SEO prose:
- Natural conversational English, not academic
- Avoid filler words: utilize, leverage, facilitate, regarding\
"""

_EN_REFINE_USER = """\
Rewrite the following draft into a polished SEO description:

Requirements:
- Natural English, not academic or translated-sounding
- Strictly no meta-language of any form (zero tolerance) — this means no use of \
"this article", "this post", "this piece", "the article", "the post" or \
"the author", "the writer" as the subject, regardless of verb \
(e.g. "explores", "discusses", "covers", "shows", "argues", "explains", "uses", \
"examines", "cites", "suggests", "demonstrates", "presents", "proposes", \
"walks through", "introduces", "highlights", "notes"); \
also ban "in this article/post", "readers will", "you will learn", \
"we discuss", "read this", "this guide"
- No calls-to-action or persuasive hooks; no exclamation marks
- All direct statements; lead with concrete claims and criteria, not structural descriptions
- Each sentence independently quotable by an AI answer engine
- Target: ~100 words
- Output only the rewritten text, no explanation, no quotes

Draft:
{draft}\
"""

_OLLAMA_OPTIONS = types.MappingProxyType({"temperature": 0.5, "repeat_penalty": 1.1})

# ── Pass 3: Taiwan zh-TW polish (Claude API, optional) ───────────────────────

_ZH_POLISH_SYSTEM = """\
你是台灣繁體中文編輯，專門修正技術文字。
輸入：一段技術部落格的文章描述（約100字）。
任務：
1. 將所有簡體字或港式用字改為台灣繁體字
2. 將中國大陸慣用語改為台灣用語
   （例：「軟件」→「軟體」、「硬件」→「硬體」、「調用」→「呼叫」、
        「函數」→「函式」、「實現」→「實作」、「獲取」→「取得」、
        「接口」→「介面」、「字符串」→「字串」、「數據」→「資料」、
        「代碼」→「程式碼」、「線程」→「執行緒」、「進程」→「行程」）
3. 語句自然流暢，符合台灣現代中文書寫習慣
直接輸出修正結果，不加任何說明、不加引號。\
"""

_CLAUDE_POLISH_MODEL = "claude-haiku-4-5"


def polish_zh_tw(text: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("WARN: ANTHROPIC_API_KEY not set — skipping zh-TW polish", file=sys.stderr)
        return text
    try:
        # Deferred: only needed when API key is set; avoids startup cost for English-only runs.
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_CLAUDE_POLISH_MODEL,
            max_tokens=512,
            system=_ZH_POLISH_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"WARN: Claude polish failed ({type(e).__name__}: {e}) — using local output", file=sys.stderr)
        return text


_DRAFT_TEMPLATES: dict[Lang, tuple[str, str]] = {
    "zh-TW": (_ZH_DRAFT_SYSTEM, _ZH_DRAFT_USER),
    "en":    (_EN_DRAFT_SYSTEM, _EN_DRAFT_USER),
}
_REFINE_TEMPLATES: dict[Lang, tuple[str, str]] = {
    "zh-TW": (_ZH_REFINE_SYSTEM, _ZH_REFINE_USER),
    "en":    (_EN_REFINE_SYSTEM, _EN_REFINE_USER),
}


def build_draft_prompts(lang: Lang, title: str, body: str) -> tuple[str, str]:
    sys_, tmpl = _DRAFT_TEMPLATES[lang]
    return sys_, tmpl.format(title=title, body=body, omission=_BODY_OMISSION_MARKER)


def build_refine_prompts(lang: Lang, draft: str) -> tuple[str, str]:
    sys_, tmpl = _REFINE_TEMPLATES[lang]
    return sys_, tmpl.format(draft=draft)


def strip_prefixes(text: str, lang: Lang) -> str:
    pattern = _ZH_PREFIX if lang == "zh-TW" else _EN_PREFIX
    return pattern.sub("", text).strip()


def call_ollama(model: str, system: str, prompt: str) -> str:
    try:
        r = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "system": system, "prompt": prompt, "stream": False,
                  "options": dict(_OLLAMA_OPTIONS)},
            timeout=300.0,
        )
        r.raise_for_status()
        return _THINK_TAG.sub("", r.json()["response"]).strip()
    except httpx.ConnectError:
        print("ABORT: cannot connect to Ollama at localhost:11434", file=sys.stderr)
        sys.exit(1)
    except httpx.ReadTimeout:
        print("ABORT: Ollama request timed out", file=sys.stderr)
        sys.exit(1)
    except httpx.RemoteProtocolError as e:
        print(f"ABORT: Ollama connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"ABORT: Ollama returned {e.response.status_code}", file=sys.stderr)
        sys.exit(1)


def validate_length(text: str, lang: Lang) -> tuple[int, str, int]:
    if lang == "zh-TW":
        count = sum(1 for c in text if _is_cjk(c))
        return count, "chars", MIN_ZH_CHARS
    count = len(text.split())
    return count, "words", MIN_EN_WORDS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()

    post = frontmatter.load(args.file)
    body = truncate_body(post.content)
    lang = detect_lang(body)
    title = str(post.get("title", args.file.stem))

    draft_system, draft_user = build_draft_prompts(lang, title, body)
    draft = call_ollama(args.model, draft_system, draft_user)

    ref_system, ref_user = build_refine_prompts(lang, draft)
    desc = strip_prefixes(call_ollama(args.model, ref_system, ref_user), lang)

    if desc.startswith(title):
        retry_hint = (
            "\n（禁止重複標題，請換個角度切入。）"
            if lang == "zh-TW"
            else "\n(Do not restate the title; approach from a different angle.)"
        )
        desc = strip_prefixes(call_ollama(args.model, ref_system, ref_user + retry_hint), lang)

    if lang == "zh-TW":
        desc = polish_zh_tw(desc)

    count, unit, minimum = validate_length(desc, lang)

    if count < minimum:
        print(f"ABORT: description too short ({count} {unit})", file=sys.stderr)
        sys.exit(1)

    warn_limit = MAX_ZH_CHARS if lang == "zh-TW" else MAX_EN_WORDS
    if count > warn_limit:
        print(f"WARN: description is long ({count} {unit})", file=sys.stderr)

    print(desc)


if __name__ == "__main__":
    main()
