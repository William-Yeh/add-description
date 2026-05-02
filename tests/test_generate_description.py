import json
import subprocess
from pathlib import Path

import httpx
import pytest
from faker import Faker
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import generate_description as gd

SCRIPT = Path(__file__).parent.parent / "skill" / "scripts" / "generate_description.py"
FIXTURES = Path(__file__).parent / "fixtures"


# ── Pure function unit tests (no Ollama needed) ────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("這是一段繁體中文內容，主要討論語言學習方法。", "zh-TW"),
    ("This is a purely English article about software.", "en"),
    ("Mostly English with one 字 CJK character.", "en"),
    ("", "en"),
])
def test_detect_lang(text, expected):
    assert gd.detect_lang(text) == expected


@pytest.mark.parametrize("text,lang,expected", [
    ("摘要：文章內容在此", "zh-TW", "文章內容在此"),
    ("本文摘要：文章內容在此", "zh-TW", "文章內容在此"),
    ("Description: some content here", "en", "some content here"),
    ("Clean content with no prefix.", "en", "Clean content with no prefix."),
])
def test_strip_prefixes(text, lang, expected):
    assert gd.strip_prefixes(text, lang) == expected


def test_truncate_body_strips_ref_definitions():
    body = "Article body.\n[^1]: footnote one\n[^2]: footnote two"
    assert gd.truncate_body(body) == "Article body."


def test_truncate_body_long_body_inserts_marker():
    # Body must exceed BODY_LIMIT to trigger truncation
    body = "A" * (gd.BODY_LIMIT - gd.BODY_TAIL) + "B" * (gd.BODY_TAIL + 100)
    result = gd.truncate_body(body)
    assert gd._BODY_OMISSION_MARKER in result
    assert result.startswith("A")
    assert result.endswith("B")


def test_truncate_body_snaps_head_to_paragraph_break():
    head_limit = gd.BODY_LIMIT - gd.BODY_TAIL
    # Paragraph break near the end of the head region (within HEAD_SNAP_SLACK)
    break_pos = head_limit - gd.HEAD_SNAP_SLACK + 10
    trailing = "Z" * (head_limit - break_pos - 2)
    head = "A" * break_pos + "\n\n" + trailing
    # Body must exceed BODY_LIMIT; pad the tail beyond BODY_TAIL
    tail = "B" * (gd.BODY_TAIL + 100)
    body = head + tail
    result = gd.truncate_body(body)
    before_marker = result.split(gd._BODY_OMISSION_MARKER)[0].rstrip()
    # The trailing 'Z' chars after the paragraph break should not appear in the head
    assert trailing not in before_marker


def test_validate_length_zh_counts_cjk():
    text = "這是中文" * 15  # 60 CJK chars
    count, unit, minimum = gd.validate_length(text, "zh-TW")
    assert count == 60 and unit == "chars" and minimum == gd.MIN_ZH_CHARS


def test_validate_length_zh_ignores_non_cjk():
    count, _, _ = gd.validate_length("Hello 世界 World 你好", "zh-TW")
    assert count == 4


def test_validate_length_en_counts_words():
    text = " ".join(["word"] * 35)
    count, unit, minimum = gd.validate_length(text, "en")
    assert count == 35 and unit == "words" and minimum == gd.MIN_EN_WORDS


@pytest.mark.parametrize("lang,title,body", [
    ("zh-TW", "測試標題", "文章內容"),
    ("en", "Test Title", "Article body"),
])
def test_build_prompts_contains_title_and_body(lang, title, body):
    _, user = gd.build_draft_prompts(lang, title, body)
    assert title in user and body in user


# ── Mocked integration tests (no Ollama needed) ───────────────────────────────

def test_main_returns_description_on_success(mocker, tmp_path, capsys):
    md = tmp_path / "article.md"
    md.write_text("---\ntitle: Test Article\n---\n" + "English sentence. " * 30)
    desc = " ".join(["meaningful"] * 35)

    mocker.patch("generate_description.call_ollama", return_value=desc)
    mocker.patch("sys.argv", ["prog", "--model", "test-model", str(md)])

    gd.main()
    assert capsys.readouterr().out.strip() == desc


def test_main_exits_1_on_short_description(mocker, tmp_path):
    md = tmp_path / "article.md"
    md.write_text("---\ntitle: Test\n---\nContent.")

    mocker.patch("generate_description.call_ollama", return_value="Too short.")
    mocker.patch("sys.argv", ["prog", "--model", "test-model", str(md)])

    with pytest.raises(SystemExit) as exc:
        gd.main()
    assert exc.value.code == 1


def test_main_retries_once_on_title_repetition(mocker, tmp_path):
    md = tmp_path / "article.md"
    md.write_text("---\ntitle: My Article\n---\n" + "English content. " * 30)
    long_desc = " ".join(["word"] * 35)

    mock_call = mocker.patch("generate_description.call_ollama", side_effect=[
        "draft content here",           # call 1: draft pass
        "My Article is about things.",  # call 2: refine → starts with title, triggers retry
        long_desc,                      # call 3: retry refine
    ])

    mocker.patch("sys.argv", ["prog", "--model", "test-model", str(md)])
    gd.main()
    assert mock_call.call_count == 3


def test_main_uses_draft_model_for_pass1_only(mocker, tmp_path):
    md = tmp_path / "article.md"
    md.write_text("---\ntitle: Test Article\n---\n" + "English sentence. " * 30)
    desc = " ".join(["word"] * 35)

    mock_call = mocker.patch("generate_description.call_ollama", side_effect=[
        "draft content",  # pass 1
        desc,             # pass 2
    ])
    mocker.patch("sys.argv", ["prog", "--model", "qwen3:8b", "--draft-model", "taide-model", str(md)])

    gd.main()
    assert mock_call.call_count == 2
    assert mock_call.call_args_list[0].args[0] == "taide-model"
    assert mock_call.call_args_list[1].args[0] == "qwen3:8b"


def test_main_uses_filename_stem_when_no_title(mocker, tmp_path, capsys):
    md = tmp_path / "my-article.md"
    md.write_text("---\ntags: [test]\n---\n" + "English sentence. " * 30)
    desc = " ".join(["word"] * 35)

    mock_call = mocker.patch("generate_description.call_ollama", return_value=desc)
    mocker.patch("sys.argv", ["prog", "--model", "test-model", str(md)])

    gd.main()
    _, _, draft_prompt = mock_call.call_args_list[0].args  # pass 1 carries the title
    assert "my-article" in draft_prompt


# ── Property-based tests (hypothesis) ────────────────────────────────────────

# Reusable strategies
_cjk_chars  = st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF)
_ascii_text = st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=1)

# Faker instances — created once at module level (construction is expensive)
_faker_zh = Faker("zh_TW")
_faker_en = Faker("en_US")


# seed_instance(n) scopes the seed to this instance only, avoiding global Faker RNG mutation
# (Faker.seed() is class-level and breaks test isolation under parallel execution).
@st.composite
def _st_zh_paragraph(draw):
    n = draw(st.integers(min_value=3, max_value=10))
    _faker_zh.seed_instance(n)
    return _faker_zh.paragraph(nb_sentences=n)


@st.composite
def _st_en_paragraph(draw):
    n = draw(st.integers(min_value=5, max_value=15))
    _faker_en.seed_instance(n)
    return _faker_en.paragraph(nb_sentences=n)


@given(st.text())
def test_detect_lang_never_raises(text):
    result = gd.detect_lang(text)
    assert result in ("zh-TW", "en")


@given(st.text(alphabet=_cjk_chars, min_size=20))
def test_detect_lang_all_cjk_is_zh(text):
    assert gd.detect_lang(text) == "zh-TW"


@given(_st_zh_paragraph())
def test_detect_lang_real_zh_tw_paragraphs(text):
    assert gd.detect_lang(text) == "zh-TW"


@given(_st_en_paragraph())
def test_detect_lang_real_en_paragraphs(text):
    assert gd.detect_lang(text) == "en"


@given(_ascii_text)
def test_detect_lang_all_ascii_is_en(text):
    assert gd.detect_lang(text) == "en"


@given(st.text())
def test_truncate_body_never_raises(text):
    gd.truncate_body(text)  # must not raise


@given(st.text(max_size=gd.BODY_LIMIT))
def test_truncate_body_short_body_unchanged(text):
    assume("\n[^" not in text)
    assert gd.truncate_body(text) == text


_MARKER_OVERHEAD = len("\n\n" + gd._BODY_OMISSION_MARKER + "\n\n")


@given(
    ref_def_pos=st.integers(min_value=0, max_value=gd.BODY_LIMIT + 100),
    has_ref_def=st.booleans(),
)
def test_truncate_body_long_body_has_marker(ref_def_pos, has_ref_def):
    # Hypothesis generates two small values; we build the large string in the test
    ref_def_str = "\n[^1]: footnote" if has_ref_def else ""
    body = "a" * ref_def_pos + ref_def_str + "b" * (gd.BODY_LIMIT + 1)
    m = gd._REF_DEF.search(body)
    effective_len = m.start() if m else len(body)
    result = gd.truncate_body(body)
    if effective_len > gd.BODY_LIMIT:
        assert gd._BODY_OMISSION_MARKER in result
    else:
        assert gd._BODY_OMISSION_MARKER not in result


@given(st.integers(min_value=1, max_value=2000))
def test_truncate_body_result_bounded(extra):
    result = gd.truncate_body("a" * (gd.BODY_LIMIT + extra))
    assert len(result) <= gd.BODY_LIMIT + _MARKER_OVERHEAD


@given(st.text())
def test_strip_prefixes_never_longer_than_input(text):
    for lang in ("zh-TW", "en"):
        assert len(gd.strip_prefixes(text, lang)) <= len(text)


@given(st.text(min_size=1))
def test_validate_length_zh_count_matches_cjk_chars(text):
    count, unit, _ = gd.validate_length(text, "zh-TW")
    expected = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    assert count == expected and unit == "chars"


@given(st.text(min_size=1))
def test_validate_length_en_count_matches_word_split(text):
    count, unit, _ = gd.validate_length(text, "en")
    assert count == len(text.split()) and unit == "words"


def test_main_calls_polish_for_zh_tw(mocker, tmp_path):
    md = tmp_path / "article.md"
    md.write_text("---\ntitle: 測試\n---\n" + "中文句子測試內容。" * 20)
    desc = "這是" * 30  # 60 CJK chars — passes minimum

    mocker.patch("generate_description.call_ollama", return_value=desc)
    mock_polish = mocker.patch("generate_description.polish_zh_tw", return_value=desc)
    mocker.patch("sys.argv", ["prog", "--model", "test-model", str(md)])

    gd.main()
    mock_polish.assert_called_once_with(desc)


# ── polish_zh_tw tests ────────────────────────────────────────────────────────

def test_polish_zh_tw_skips_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = gd.polish_zh_tw("軟件工程師使用代碼")
    assert result == "軟件工程師使用代碼"
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_polish_zh_tw_returns_polished_text(monkeypatch, mocker):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    polished = "軟體工程師使用程式碼"
    mock_content = mocker.MagicMock()
    mock_content.text = polished
    mock_msg = mocker.MagicMock()
    mock_msg.content = [mock_content]
    mock_client = mocker.MagicMock()
    mock_client.messages.create.return_value = mock_msg
    mocker.patch("anthropic.Anthropic", return_value=mock_client)

    result = gd.polish_zh_tw("軟件工程師使用代碼")
    assert result == polished


def test_polish_zh_tw_falls_back_on_exception(monkeypatch, mocker, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mocker.patch("anthropic.Anthropic", side_effect=Exception("network error"))

    original = "軟件工程師使用代碼"
    result = gd.polish_zh_tw(original)
    assert result == original
    assert "WARN" in capsys.readouterr().err


# ── call_ollama request shape ─────────────────────────────────────────────────

def _capture_ollama_payload(model: str, mocker) -> bytes:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(200, json={"response": "ok"})

    def fake_post(*args, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(*args, **kwargs)

    mocker.patch.object(gd.httpx, "post", fake_post)
    gd.call_ollama(model, "sys", "user")
    return captured["payload"]


def test_call_ollama_disables_thinking_for_qwen3_5(mocker):
    body = json.loads(_capture_ollama_payload("qwen3.5:9b", mocker))
    assert body["think"] is False


def test_call_ollama_omits_think_flag_for_qwen3(mocker):
    """qwen3:8b emits <think>...</think> inline; we let it run and strip the output."""
    body = json.loads(_capture_ollama_payload("qwen3:8b", mocker))
    assert "think" not in body


# ── Integration tests (require Ollama) ────────────────────────────────────────

@pytest.mark.parametrize("fixture,lang", [
    ("chinese_article.md", "zh-TW"),
    ("english_article.md", "en"),
])
def test_integration_fixture(ollama_running, fixture, lang):
    result = subprocess.run(
        ["uv", "run", str(SCRIPT), "--model", "qwen3:1.7b", str(FIXTURES / fixture)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    count, unit, minimum = gd.validate_length(result.stdout.strip(), lang)
    assert count >= minimum, f"Too short: {count} {unit}\n{result.stdout}"
