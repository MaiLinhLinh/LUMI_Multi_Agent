import pytest

from rag_manager.services.music_player_payload import (
    MusicPlayerPayloadError,
    build_music_player_payload,
)


def _candidate(**overrides):
    candidate = {
        "record_id": "youtube_FN7ALfpGxiI",
        "track_id": "track_noi_nay_co_anh",
        "title": "Nơi Này Có Anh",
        "artists": ["Sơn Tùng M-TP"],
        "video_id": "FN7ALfpGxiI",
        "content_type": "official_mv",
        "version": "official MV",
        "thumbnail_url": (
            "https://i.ytimg.com/vi/FN7ALfpGxiI/hqdefault.jpg"
        ),
        "duration_seconds": 273,
        "release_date": "2017-02-13",
    }
    candidate.update(overrides)
    return candidate


def test_player_payload_contains_metadata_but_no_iframe_or_embed_url() -> None:
    payload = build_music_player_payload(_candidate())

    assert payload == {
        "schema_version": "music.youtube-player.v1",
        "status": "completed",
        "ui_type": "youtube_player",
        "player_action": "play",
        "music": {
            "source_id": "youtube_FN7ALfpGxiI",
            "track_id": "track_noi_nay_co_anh",
            "title": "Nơi Này Có Anh",
            "artist": "Sơn Tùng M-TP",
            "artists": ["Sơn Tùng M-TP"],
            "video_id": "FN7ALfpGxiI",
            "content_type": "official_mv",
            "version": "official MV",
            "duration_seconds": 273,
            "release_date": "2017-02-13",
            "thumbnail_url": (
                "https://i.ytimg.com/vi/FN7ALfpGxiI/hqdefault.jpg"
            ),
        },
    }
    serialized = str(payload).lower()
    assert "iframe" not in serialized
    assert "youtube.com/embed" not in serialized
    assert "youtube-nocookie.com" not in serialized
    assert "src" not in payload


@pytest.mark.parametrize(
    "video_id",
    ["too-short", "123456789012", "bad<script>"],
)
def test_player_payload_rejects_invalid_youtube_video_id(video_id: str) -> None:
    with pytest.raises(MusicPlayerPayloadError) as exc_info:
        build_music_player_payload(_candidate(video_id=video_id))

    assert exc_info.value.code == "invalid_youtube_video_id"


def test_player_payload_ignores_unapproved_thumbnail_origin() -> None:
    payload = build_music_player_payload(
        _candidate(thumbnail_url="https://evil.example/video.jpg")
    )

    assert "thumbnail_url" not in payload["music"]


def test_player_payload_supports_backend_stop_action() -> None:
    payload = build_music_player_payload(_candidate(), player_action="stop")

    assert payload["player_action"] == "stop"
    assert payload["music"]["video_id"] == "FN7ALfpGxiI"

