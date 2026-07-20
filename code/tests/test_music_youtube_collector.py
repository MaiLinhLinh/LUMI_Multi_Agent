import json

import pytest

from rag_manager.services.music_youtube_collector import (
    MusicYouTubeCollectorError,
    OfficialChannel,
    canonicalize_title,
    collect_music_catalog,
    load_official_channels,
    write_collection_outputs,
)
from rag_manager.services.music_catalog_worker import load_music_catalog


CHANNEL_ID = "UC" + "A" * 22


class FakeYouTubeService:
    def fetch_upload_playlists(self, channel_ids):
        assert list(channel_ids) == [CHANNEL_ID]
        return {
            CHANNEL_ID: {
                "channel_id": CHANNEL_ID,
                "channel_name": "Sơn Tùng M-TP Official",
                "uploads_playlist_id": "UPLOADS123",
            }
        }

    def fetch_upload_video_ids(self, playlist_id, *, max_videos=None):
        assert playlist_id == "UPLOADS123"
        assert max_videos is None
        return ["AbCdEfGhI12", "ZyXwVuTsR98", "TeAsErViD01", "NoiNayCoA12"]

    def fetch_videos(self, _video_ids):
        return {
            "AbCdEfGhI12": _video(
                "AbCdEfGhI12",
                "Sơn Tùng M-TP | Lạc Trôi | Official Music Video",
                views=100,
            ),
            "ZyXwVuTsR98": _video(
                "ZyXwVuTsR98",
                "Sơn Tùng M-TP - Lạc Trôi (Official Audio)",
                views=1_000_000,
            ),
            "TeAsErViD01": _video(
                "TeAsErViD01",
                "Sơn Tùng M-TP - Dự án mới Teaser",
                views=2_000_000,
            ),
            "NoiNayCoA12": _video(
                "NoiNayCoA12",
                "Sơn Tùng M-TP - Nơi Này Có Anh",
                views=500_000,
            ),
        }


def _video(video_id, title, *, views):
    return {
        "video_id": video_id,
        "youtube_title": title,
        "channel_id": CHANNEL_ID,
        "channel_name": "Sơn Tùng M-TP Official",
        "published_at": "2024-03-08T05:00:00Z",
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "duration_seconds": 240,
        "embeddable": True,
        "source_active": True,
        "view_count": views,
        "like_count": 10,
        "youtube_tags": ["V-Pop"],
    }


def _channel():
    return OfficialChannel(
        artist="Sơn Tùng M-TP",
        channel_id=CHANNEL_ID,
        language="vi",
        genres=("V-Pop",),
    )


def test_collector_keeps_one_video_and_prioritizes_mv_over_more_popular_audio() -> None:
    catalog, review = collect_music_catalog(
        [_channel()],
        youtube_service=FakeYouTubeService(),
    )

    assert len(catalog["tracks"]) == 2
    lac_troi = next(track for track in catalog["tracks"] if track["title"] == "Lạc Trôi")
    assert len(lac_troi["sources"]) == 1
    assert lac_troi["sources"][0]["video_id"] == "AbCdEfGhI12"
    assert lac_troi["sources"][0]["content_type"] == "official_mv"
    assert lac_troi["release_date_origin"] == "youtube_published_at_proxy"
    selection = next(
        item
        for item in review["channel_reports"][0]["selections"]
        if item["canonical_title"] == "Lạc Trôi"
    )
    assert selection["alternatives"][0]["video_id"] == "ZyXwVuTsR98"
    assert any(
        item["reason"] == "non_song_title"
        for item in review["channel_reports"][0]["rejected"]
    )


def test_collector_marks_unclassified_official_upload_for_review() -> None:
    catalog, review = collect_music_catalog(
        [_channel()],
        youtube_service=FakeYouTubeService(),
    )

    track = next(
        item for item in catalog["tracks"] if item["title"] == "Nơi Này Có Anh"
    )
    assert track["review_required"] is True
    assert review["review_required_count"] == 1


def test_collector_defaults_to_popularity_ranking_and_respects_track_limit() -> None:
    catalog, review = collect_music_catalog(
        [_channel()],
        youtube_service=FakeYouTubeService(),
        max_tracks_per_channel=1,
    )

    assert len(catalog["tracks"]) == 1
    assert catalog["tracks"][0]["title"] == "Nơi Này Có Anh"
    report = review["channel_reports"][0]
    assert report["track_limit"] == 1
    assert report["omitted_by_track_limit"][0]["canonical_title"] == "Lạc Trôi"
    assert review["selection_policy"]["track_ranking"] == (
        "selected_video_view_count_desc"
    )


def test_channel_file_requires_explicit_manual_confirmation(tmp_path) -> None:
    path = tmp_path / "channels.json"
    path.write_text(
        json.dumps(
            {
                "channels_version": "music.youtube-channels.v1",
                "channels": [
                    {
                        "artist": "Artist",
                        "channel_id": CHANNEL_ID,
                        "confirmed_official": False,
                        "language": "vi",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MusicYouTubeCollectorError, match="manually verify"):
        load_official_channels(path)


def test_collection_outputs_refuse_accidental_overwrite(tmp_path) -> None:
    catalog_path = tmp_path / "catalog.json"
    review_path = tmp_path / "review.json"
    catalog_path.write_text("existing", encoding="utf-8")

    with pytest.raises(MusicYouTubeCollectorError, match="Refusing to overwrite"):
        write_collection_outputs(
            catalog={"tracks": []},
            review={},
            catalog_path=catalog_path,
            review_path=review_path,
        )


def test_generated_catalog_is_accepted_by_catalog_worker(tmp_path) -> None:
    catalog, review = collect_music_catalog(
        [_channel()],
        youtube_service=FakeYouTubeService(),
    )
    catalog_path = tmp_path / "catalog.json"
    review_path = tmp_path / "review.json"

    write_collection_outputs(
        catalog=catalog,
        review=review,
        catalog_path=catalog_path,
        review_path=review_path,
    )
    prepared = load_music_catalog(catalog_path)

    assert len(prepared) == 2
    assert all(record.metadata["is_official"] is True for record in prepared)
    assert all(
        record.metadata["release_date_origin"] == "youtube_published_at_proxy"
        for record in prepared
    )


def test_canonical_title_does_not_drop_song_named_video_games() -> None:
    assert canonicalize_title("Video Games", artist="Artist") == "Video Games"
