import pytest
from pydantic import ValidationError

from rag_manager.agents.music_structured_schema import MusicExtractionResponse
from rag_manager.llm.prompts import MUSIC_PIPELINE_EXTRACTION_SYSTEM_PROMPT


EXPECTED_FIELDS = {
    "action",
    "search_query",
    "title",
    "artist",
    "genre",
    "mood",
    "language",
    "version",
    "sort_by",
    "sort_order",
    "selection_index",
}


def _valid_payload(**overrides):
    payload = {
        "action": "play",
        "search_query": "bài mới nhất của Sơn Tùng",
        "title": None,
        "artist": "Sơn Tùng",
        "genre": None,
        "mood": None,
        "language": None,
        "version": None,
        "sort_by": "release_date",
        "sort_order": "desc",
        "selection_index": None,
    }
    payload.update(overrides)
    return payload


def test_music_extraction_schema_has_exact_required_fields() -> None:
    schema = MusicExtractionResponse.model_json_schema()

    assert set(schema["properties"]) == EXPECTED_FIELDS
    assert set(schema["required"]) == EXPECTED_FIELDS


def test_music_extraction_schema_accepts_latest_artist_request() -> None:
    result = MusicExtractionResponse.model_validate(_valid_payload()).model_dump()

    assert result == _valid_payload()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action", "recommend"),
        ("sort_by", "youtube_views"),
        ("sort_order", "newest"),
        ("selection_index", 0),
    ],
)
def test_music_extraction_schema_rejects_unsupported_control_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        MusicExtractionResponse.model_validate(_valid_payload(**{field: value}))


def test_music_prompt_forbids_catalog_and_embed_hallucination() -> None:
    prompt = MUSIC_PIPELINE_EXTRACTION_SYSTEM_PROMPT

    assert "never guess a song or artist" in prompt.casefold()
    assert "`video_id`" in prompt
    assert "YouTube URL" in prompt
    assert "`where_document`" in prompt
    assert "`$contains`" in prompt
    assert "assistant suggestion" in prompt
