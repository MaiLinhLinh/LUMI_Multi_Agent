import httpx

from rag_manager.services.music_youtube_api import YouTubeMetadataService


def test_youtube_metadata_service_normalizes_source_facts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["part"] == "snippet,contentDetails,status,statistics"
        assert request.url.params["id"] == "AbCdEfGhI12"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "AbCdEfGhI12",
                        "snippet": {
                            "title": "Video title",
                            "channelId": "UC123",
                            "channelTitle": "Official channel",
                            "publishedAt": "2024-03-08T05:00:00Z",
                            "thumbnails": {
                                "high": {
                                    "url": "https://i.ytimg.com/vi/AbCdEfGhI12/hqdefault.jpg"
                                }
                            },
                        },
                        "contentDetails": {"duration": "PT4M45S"},
                        "status": {
                            "privacyStatus": "public",
                            "uploadStatus": "processed",
                            "embeddable": True,
                        },
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeMetadataService(api_key="key", client=client)

    result = service.fetch_videos(["AbCdEfGhI12"])["AbCdEfGhI12"]

    assert result["channel_id"] == "UC123"
    assert result["duration_seconds"] == 285
    assert result["embeddable"] is True
    assert result["source_active"] is True


def test_youtube_metadata_service_batches_at_fifty() -> None:
    batch_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["id"].split(",")
        batch_sizes.append(len(ids))
        return httpx.Response(200, json={"items": []})

    ids = [f"video{i:06d}" for i in range(51)]
    assert all(len(video_id) == 11 for video_id in ids)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeMetadataService(api_key="key", client=client)

    assert service.fetch_videos(ids) == {}
    assert batch_sizes == [50, 1]


def test_youtube_metadata_service_resolves_uploads_and_pages_playlist() -> None:
    channel_id = "UC" + "A" * 22
    page_tokens = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": channel_id,
                            "snippet": {"title": "Official Artist"},
                            "contentDetails": {
                                "relatedPlaylists": {"uploads": "UPLOADS123"}
                            },
                        }
                    ]
                },
            )
        page_token = request.url.params.get("pageToken", "")
        page_tokens.append(page_token)
        if not page_token:
            return httpx.Response(
                200,
                json={
                    "items": [{"contentDetails": {"videoId": "AbCdEfGhI12"}}],
                    "nextPageToken": "page-2",
                },
            )
        return httpx.Response(
            200,
            json={"items": [{"contentDetails": {"videoId": "ZyXwVuTsR98"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeMetadataService(api_key="key", client=client)

    playlists = service.fetch_upload_playlists([channel_id])
    videos = service.fetch_upload_video_ids("UPLOADS123")

    assert playlists[channel_id]["uploads_playlist_id"] == "UPLOADS123"
    assert videos == ["AbCdEfGhI12", "ZyXwVuTsR98"]
    assert page_tokens == ["", "page-2"]
