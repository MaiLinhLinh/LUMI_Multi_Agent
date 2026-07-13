import json

from rag_manager.visualization.paths import resolve_asset_path
from rag_manager.visualization.registry import lookup_template
from rag_manager.visualization.renderer import render_template, save_visualization_output


def _load_sample(schema_version: str) -> dict:
    sample_path = resolve_asset_path("schemas", schema_version, "sample.json")
    return json.loads(sample_path.read_text(encoding="utf-8"))


def _weather_template_html() -> str:
    return lookup_template("weather_basic").template_path.read_text(encoding="utf-8")


def _forecast_template_html() -> str:
    return lookup_template("weather_forecast").template_path.read_text(encoding="utf-8")


def test_render_template_renders_current_weather_without_none_or_undefined() -> None:
    envelope = _load_sample("weather.current.v1")
    html = render_template(
        _weather_template_html(),
        answer="Current weather in Ha Noi.",
        data=envelope["data"] | {"source": envelope["source"]},
    )

    assert "Ha Noi" in html
    assert "30" in html
    assert "Current weather in Ha Noi." in html
    assert "<svg" in html
    assert "None" not in html
    assert "undefined" not in html


def test_render_template_renders_combined_forecast_rows() -> None:
    envelope = _load_sample("weather.combined.v1")
    html = render_template(
        _weather_template_html(),
        answer="Combined weather answer.",
        data=envelope["data"] | {"source": envelope["source"]},
    )

    assert "12:00" in html
    assert "Rain 60%" in html
    assert "33" in html


def test_render_template_escapes_answer_and_values() -> None:
    html = render_template(
        "<div>{{ answer }}</div><span>{{ data.location.name }}</span>",
        answer="<script>alert(1)</script>",
        data={"location": {"name": "<Ha Noi>"}},
    )

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;Ha Noi&gt;" in html


def test_render_template_hides_missing_fields() -> None:
    html = render_template(
        "<div>{{ data.current.temperature.current_celsius }}</div>",
        answer="",
        data={},
    )

    assert html == "<div></div>"


def test_render_template_executes_generic_loops_and_escapes_each_item() -> None:
    html = render_template(
        "{% for item in data['items'] %}<p>{{ item }}</p>{% endfor %}",
        answer="",
        data={"items": ["first", "<script>alert(1)</script>"]},
    )

    assert html == "<p>first</p><p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"


def test_forecast_template_renders_one_card_per_day_and_nested_intervals() -> None:
    envelope = _load_sample("weather.forecast.v1")
    first_day = envelope["data"]["forecast"]["days"][0]
    second_day = json.loads(json.dumps(first_day))
    second_day["date"] = "2026-07-12"
    second_day["intervals"][0]["time"] = "15:00"
    second_day["intervals"][0]["condition"]["description"] = "<unsafe>"
    envelope["data"]["forecast"]["days"].append(second_day)

    html = render_template(
        _forecast_template_html(),
        answer="",
        data=envelope["data"] | {"source": envelope["source"]},
    )

    assert html.count('class="daily-card"') == 2
    assert "2026-07-12" in html
    assert "12:00" in html
    assert "15:00" in html
    assert "<unsafe>" not in html
    assert "&lt;unsafe&gt;" in html


def test_save_visualization_output_writes_html(tmp_path) -> None:
    output_path = save_visualization_output("<html></html>", output_dir=tmp_path)

    assert output_path.parent == tmp_path.resolve()
    assert output_path.suffix == ".html"
    assert output_path.read_text(encoding="utf-8") == "<html></html>"
