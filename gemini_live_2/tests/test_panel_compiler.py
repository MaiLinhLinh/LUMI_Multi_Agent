import unittest
from pathlib import Path

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.panel import (
    ChoiceChild,
    DataAlias,
    DataBundle,
    GridRect,
    PanelCompilationError,
    PanelCompiler,
    PlanBlock,
    PresentationPlan,
)
from gemini_live_2.widgets import build_default_widget_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PanelCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = PanelCompiler(build_default_widget_registry())
        self.resources = DomainRegistry(PROJECT_ROOT / "domains").load("education")
        self.bundle = DataBundle(
            domain_id="education",
            data={"lesson": {"title": "Dog and cat"}},
            aliases=(DataAlias(id="$title", path=("lesson", "title"), description="Lesson title"),),
        )

    def test_compiles_resolves_aliases_and_generates_anchor_map(self) -> None:
        panel = self.compiler.compile(
            panel_id="panel-test",
            data_bundle=self.bundle,
            domain_resources=self.resources,
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock("text", GridRect(1, 1, 12, 1), {"content": "$title", "role": "title"}),
                    PlanBlock("image", GridRect(1, 2, 5, 5), {"asset_id": "dog", "label": "Dog"}),
                    PlanBlock("object_group", GridRect(7, 2, 5, 5), {"asset_id": "cat", "count": 2}),
                ),
            ),
        )
        self.assertEqual(panel.blocks[0].props["content"], "Dog and cat")
        self.assertEqual([block.id for block in panel.blocks], ["1", "2", "3"])
        self.assertEqual(set(panel.anchor_map), {"a", "b", "c", "d", "e"})
        self.assertEqual(panel.anchor_map["a"].block_id, "1")
        self.assertEqual(panel.anchor_map["b"].anchor_key, "image")

    def test_rejects_out_of_bounds_overlap_and_unknown_asset(self) -> None:
        with self.assertRaisesRegex(PanelCompilationError, "exceeds canvas"):
            self._compile((PlanBlock("text", GridRect(1, 1, 17, 1), {"content": "x"}),))
        with self.assertRaisesRegex(PanelCompilationError, "overlap"):
            self._compile((
                PlanBlock("text", GridRect(1, 1, 4, 2), {"content": "x"}),
                PlanBlock("text", GridRect(3, 2, 4, 2), {"content": "y"}),
            ))
        with self.assertRaisesRegex(PanelCompilationError, "unknown asset_id"):
            self._compile((PlanBlock("image", GridRect(1, 1, 4, 4), {"asset_id": "missing"}),))

    def test_overlap_error_exposes_safe_structured_feedback(self) -> None:
        with self.assertRaises(PanelCompilationError) as raised:
            self._compile((
                PlanBlock("text", GridRect(1, 1, 4, 2), {"content": "x"}),
                PlanBlock("text", GridRect(3, 2, 4, 2), {"content": "y"}),
            ))

        feedback = raised.exception.for_plan_agent()
        self.assertEqual(feedback["error_code"], "grid_overlap")
        self.assertEqual(feedback["details"]["first_block_index"], 1)
        self.assertEqual(feedback["details"]["second_block_index"], 2)
        self.assertEqual(feedback["details"]["overlap_cells"], [{"col": 3, "row": 2}, {"col": 4, "row": 2}])

    def test_rejects_unsupported_widget_and_unknown_alias(self) -> None:
        with self.assertRaisesRegex(PanelCompilationError, "not allowed"):
            self._compile((PlanBlock("chart", GridRect(1, 1, 4, 4), {}),))
        with self.assertRaisesRegex(PanelCompilationError, "unknown data alias"):
            self._compile((PlanBlock("text", GridRect(1, 1, 4, 1), {"content": "$missing"}),))

    def test_image_widget_accepts_svg_icon_asset(self) -> None:
        panel = self._compile((
            PlanBlock("image", GridRect(1, 1, 4, 4), {"asset_id": "plus", "label": "+"}),
        ))
        self.assertEqual(panel.blocks[0].props["asset_id"], "plus")

    def test_materializes_initial_visibility_into_panel_blocks(self) -> None:
        panel = self._compile((
            PlanBlock("image", GridRect(1, 1, 4, 4), {"asset_id": "dog"}),
            PlanBlock(
                "image",
                GridRect(6, 1, 4, 4),
                {"asset_id": "cat"},
                initial_visibility="hidden",
            ),
        ))
        self.assertEqual([block.visibility for block in panel.blocks], ["visible", "hidden"])

    def test_answer_widget_is_available_to_education_and_has_an_anchor(self) -> None:
        panel = self._compile((
            PlanBlock(
                "answer",
                GridRect(1, 1, 3, 2),
                {"value": "3"},
                initial_visibility="hidden",
            ),
        ))
        self.assertEqual(panel.blocks[0].visibility, "hidden")
        self.assertEqual(panel.anchor_map["a"].anchor_key, "answer")

    def test_compiles_choice_children_and_creates_one_anchor_for_the_whole_choice(self) -> None:
        panel = self._compile((
            PlanBlock(
                "choice",
                GridRect(1, 1, 4, 5),
                {},
                children=(
                    ChoiceChild("image", {"asset_id": "cat"}),
                    ChoiceChild("text", {"content": "Mèo", "role": "label"}),
                ),
            ),
        ))
        self.assertEqual(panel.blocks[0].children[0].props["asset_id"], "cat")
        self.assertEqual(panel.blocks[0].children[1].props["content"], "Mèo")
        self.assertEqual([(anchor.block_id, anchor.anchor_key) for anchor in panel.anchors], [("1", "choice")])

    def test_rejects_invalid_or_duplicate_choice_children(self) -> None:
        with self.assertRaisesRegex(PanelCompilationError, "must contain at least one child"):
            self._compile((PlanBlock("choice", GridRect(1, 1, 4, 4), {}),))
        with self.assertRaisesRegex(PanelCompilationError, "not allowed"):
            self._compile((
                PlanBlock(
                    "choice", GridRect(1, 1, 4, 4), {},
                    children=(ChoiceChild("answer", {"value": "3"}),),
                ),
            ))
        with self.assertRaisesRegex(PanelCompilationError, "choice.props must be empty"):
            self._compile((
                PlanBlock("choice", GridRect(1, 1, 4, 4), {"choice_id": "1"}, children=(ChoiceChild("image", {"asset_id": "cat"}),)),
            ))

    def _compile(self, blocks: tuple[PlanBlock, ...]):
        return self.compiler.compile(
            panel_id="panel-test",
            data_bundle=self.bundle,
            domain_resources=self.resources,
            plan=PresentationPlan(domain_id="education", blocks=blocks),
        )


if __name__ == "__main__":
    unittest.main()
