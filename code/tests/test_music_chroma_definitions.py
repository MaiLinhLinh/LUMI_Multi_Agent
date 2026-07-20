import json
from pathlib import Path


DEFINITIONS_DIR = Path(__file__).parents[1] / "docs" / "music_chroma"


def _load_json(filename: str):
    return json.loads((DEFINITIONS_DIR / filename).read_text(encoding="utf-8"))


def test_music_chroma_collection_uses_local_cosine_bge_m3_contract() -> None:
    definition = _load_json("collection_config.json")

    assert definition["client"] == {
        "type": "PersistentClient",
        "path": "data/chroma_music",
    }
    assert definition["collection"]["name"] == "music_tracks_v1"
    assert definition["collection"]["configuration"] == {
        "hnsw": {"space": "cosine"}
    }
    assert definition["dense_retrieval"]["embedding_dimensions"] == 1024
    assert definition["lexical_retrieval"]["engine"] == "python_bm25"
    assert definition["fusion"]["method"] == "rrf"


def test_music_chroma_record_is_flat_and_source_scoped() -> None:
    contract = _load_json("record_contract.json")
    metadata = contract["metadata"]

    assert contract["record_model"] == (
        "one_primary_playable_youtube_source_per_track"
    )
    assert contract["embedding"]["dimensions"] == 1024
    assert metadata["flat_only"] is True
    assert {
        "track_id",
        "video_id",
        "content_type",
        "release_date_epoch",
        "embeddable",
        "track_active",
        "source_active",
    } <= set(metadata["required"])
