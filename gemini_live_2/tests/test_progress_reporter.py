"""Unit tests for tool-result progress formatting."""

from __future__ import annotations

import unittest

from gemini_live_2.live.progress_reporter import ProgressReporter, ToolResultAvailable


def _search_response(**result: str) -> dict[str, object]:
    return {"verified_data": {"data": {"search_results": [result]}}}


class ProgressReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reporter = ProgressReporter()

    def test_web_uses_only_the_first_result_title(self) -> None:
        report = self.reporter.build(ToolResultAvailable(
            plan_run_id="run-a", tool_name="search_web", arguments={"query": "Bác Hồ"},
            response=_search_response(title="Bác Hồ với thiếu nhi", snippet="Không gửi snippet"),
        ))
        self.assertEqual(report and report.message, "Đã tìm được nguồn “Bác Hồ với thiếu nhi”.")

    def test_image_uses_only_the_first_result_caption(self) -> None:
        report = self.reporter.build(ToolResultAvailable(
            plan_run_id="run-a", tool_name="search_image", arguments={"query": "Bác Hồ"},
            response=_search_response(caption="Bác Hồ thăm thiếu nhi", asset_url="Không gửi URL"),
        ))
        self.assertEqual(report and report.message, "Đã tìm được hình ảnh “Bác Hồ thăm thiếu nhi”.")

    def test_describe_widgets_uses_declared_internal_metadata(self) -> None:
        report = self.reporter.build(ToolResultAvailable(
            plan_run_id="run-a", tool_name="describe_widgets", arguments={"widget_ids": ["text"]}, response={"widgets": []},
        ))
        self.assertEqual(report and report.message, "Đã xem các thành phần giao diện cần thiết để chuẩn bị hoạt động.")

    def test_other_tool_or_missing_fact_stays_silent(self) -> None:
        self.assertIsNone(self.reporter.build(ToolResultAvailable(
            plan_run_id="run-a", tool_name="describe_template", arguments={}, response={},
        )))
        self.assertIsNone(self.reporter.build(ToolResultAvailable(
            plan_run_id="run-a", tool_name="search_web", arguments={}, response={"verified_data": {"data": {}}},
        )))


if __name__ == "__main__":
    unittest.main()
