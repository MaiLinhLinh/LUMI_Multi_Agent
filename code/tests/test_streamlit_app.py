from pathlib import Path

import streamlit_app


def test_assistant_message_prefers_rendered_visualization() -> None:
    message = streamlit_app._assistant_message(
        {
            "weather_status": "completed",
            "final_response": "",
            "visualization_html_path": "D:/tmp/weather.html",
        }
    )

    assert message == {
        "role": "assistant",
        "content": "",
        "html_path": "D:/tmp/weather.html",
    }


def test_clarification_ignores_stale_visualization_path() -> None:
    message = streamlit_app._assistant_message(
        {
            "weather_status": "needs_clarification",
            "final_response": "Bạn muốn xem dự báo thời tiết vào lúc mấy giờ?",
            "visualization_html_path": "D:/tmp/previous_weather.html",
        }
    )

    assert message == {
        "role": "assistant",
        "content": "Bạn muốn xem dự báo thời tiết vào lúc mấy giờ?",
        "html_path": "",
    }


def test_read_rendered_template_reads_renderer_output(tmp_path: Path) -> None:
    html_path = tmp_path / "weather.html"
    html_path.write_text("<html><body>Weather</body></html>", encoding="utf-8")

    assert streamlit_app._read_rendered_template(str(html_path)) == (
        "<html><body>Weather</body></html>"
    )


def test_workflow_history_excludes_html_only_assistant_message() -> None:
    history = streamlit_app._workflow_history(
        [
            {"role": "user", "content": "Thời tiết Hà Nội hôm nay?"},
            {"role": "assistant", "content": "", "html_path": "weather.html"},
            {"role": "user", "content": "Chính xác lúc 9 giờ"},
        ]
    )

    assert history == [
        {"role": "user", "content": "Thời tiết Hà Nội hôm nay?"},
        {"role": "user", "content": "Chính xác lúc 9 giờ"},
    ]
