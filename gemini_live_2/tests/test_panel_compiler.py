import unittest
from pathlib import Path

from gemini_live_2.catalogs.domains import DomainRegistry
from gemini_live_2.catalogs.resources import SharedResourceRegistry
from gemini_live_2.panel import (
    ChoiceChild,
    DataAlias,
    DataBundle,
    GridRect,
    PanelCompilationError,
    PanelCompiler,
    PlanBlock,
    PresentationPlan,
    SurfaceDocument,
)
from gemini_live_2.tests.runtime_registry import runtime_widget_registry
from gemini_live_2.search import SearchResultStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PanelCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = PanelCompiler(
            runtime_widget_registry(),
            asset_catalog=SharedResourceRegistry(PROJECT_ROOT / "resources").load().assets,
        )
        self.resources = DomainRegistry(PROJECT_ROOT / "domains").load("education")
        self.bundle = DataBundle(
            domain_id="education",
            data={"lesson": {"title": "Dog and cat"}},
            aliases=(DataAlias(id="$title", path=("lesson", "title"), description="Lesson title"),),
        )

    def test_compiles_resolves_aliases_and_generates_anchor_map(self) -> None:
        document = self.compiler.compile_surface_document(
            surface_id="surface-test",
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
        self.assertEqual(document.components[0].props["content"], "Dog and cat")
        self.assertEqual([component.id for component in document.components], ["1", "2", "3"])
        self.assertEqual(set(document.anchor_map), {"a", "b", "c", "d", "e"})
        self.assertEqual(document.anchor_map["a"].component_id, "1")
        self.assertEqual(document.anchor_map["b"].anchor_key, "image")

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
        with self.assertRaisesRegex(PanelCompilationError, "unknown widget_id"):
            self._compile((PlanBlock("chart", GridRect(1, 1, 4, 4), {}),))
        with self.assertRaisesRegex(PanelCompilationError, "unknown data alias"):
            self._compile((PlanBlock("text", GridRect(1, 1, 4, 1), {"content": "$missing"}),))

    def test_image_widget_accepts_svg_icon_asset(self) -> None:
        document = self._compile((
            PlanBlock("image", GridRect(1, 1, 4, 4), {"asset_id": "plus", "label": "+"}),
        ))
        self.assertEqual(document.components[0].props["asset_id"], "plus")

    def test_compiler_materializes_only_a_current_session_remote_image_result(self) -> None:
        store = SearchResultStore()
        result_id = store.put_image(
            session_id="session-a",
            remote_url="https://images.example/pig.png",
            source_url="https://example/pig",
            caption="Một chú heo dễ thương",
        )
        compiler = PanelCompiler(
            runtime_widget_registry(),
            asset_catalog=SharedResourceRegistry(PROJECT_ROOT / "resources").load().assets,
            search_result_store=store,
        )
        document = compiler.compile_surface_document(
            surface_id="surface-test",
            data_bundle=self.bundle,
            domain_resources=self.resources,
            search_session_id="session-a",
            plan=PresentationPlan(domain_id="education", blocks=(
                PlanBlock("image", GridRect(1, 1, 4, 4), {"remote_image_result_id": result_id}),
            )),
        )
        self.assertEqual(document.components[0].props["remote_image_result_id"], result_id)
        self.assertEqual(document.components[0].props["source"]["url"], "https://images.example/pig.png")
        with self.assertRaisesRegex(PanelCompilationError, "search again"):
            compiler.compile_surface_document(
                data_bundle=self.bundle,
                domain_resources=self.resources,
                search_session_id="different-session",
                plan=PresentationPlan(domain_id="education", blocks=(
                    PlanBlock("image", GridRect(1, 1, 4, 4), {"remote_image_result_id": result_id}),
                )),
            )

    def test_compiler_materializes_remote_image_on_flashcard_front(self) -> None:
        store = SearchResultStore()
        result_id = store.put_image(
            session_id="session-a",
            remote_url="https://images.example/coffee.png",
            source_url="https://example/coffee",
            caption="Một tách cà phê nóng",
        )
        compiler = PanelCompiler(
            runtime_widget_registry(),
            asset_catalog=SharedResourceRegistry(PROJECT_ROOT / "resources").load().assets,
            search_result_store=store,
        )
        document = compiler.compile_surface_document(
            surface_id="surface-test",
            data_bundle=self.bundle,
            domain_resources=self.resources,
            search_session_id="session-a",
            plan=PresentationPlan(domain_id="education", blocks=(
                PlanBlock("flashcard", GridRect(1, 1, 8, 6), {
                    "front": {"remote_image_result_id": result_id, "text": "coffee"},
                    "back": {"word": "coffee", "phonetic": "/ˈkɒf.i/", "meaning": "cà phê"},
                }),
            )),
        )
        front = document.components[0].props["front"]
        self.assertEqual(front["remote_image_result_id"], result_id)
        self.assertEqual(front["source"]["url"], "https://images.example/coffee.png")

    def test_image_rejects_raw_provider_url_or_two_image_sources(self) -> None:
        with self.assertRaisesRegex(PanelCompilationError, "unsupported fields"):
            self._compile((PlanBlock("image", GridRect(1, 1, 4, 4), {"url": "https://example/pig.png"}),))
        with self.assertRaisesRegex(PanelCompilationError, "exactly one"):
            self._compile((PlanBlock("image", GridRect(1, 1, 4, 4), {
                "asset_id": "cat", "remote_image_result_id": "img_x",
            }),))

    def test_rejects_a_plan_declared_interaction_that_the_widget_does_not_own(self) -> None:
        with self.assertRaises(PanelCompilationError) as raised:
            self._compile((PlanBlock(
                "image",
                GridRect(1, 1, 4, 4),
                {"asset_id": "cat", "action": "flip"},
            ),))

        feedback = raised.exception.for_plan_agent()
        self.assertEqual(feedback["error_code"], "invalid_widget_contract")
        self.assertEqual(feedback["details"], {"block_index": 1, "widget_id": "image"})
        self.assertIn("unsupported fields", feedback["message"])

    def test_materializes_initial_visibility_into_component_state(self) -> None:
        document = self._compile((
            PlanBlock("image", GridRect(1, 1, 4, 4), {"asset_id": "dog"}),
            PlanBlock(
                "image",
                GridRect(6, 1, 4, 4),
                {"asset_id": "cat"},
                initial_visibility="hidden",
            ),
        ))
        self.assertEqual([component.state["visibility"] for component in document.components], ["visible", "hidden"])

    def test_answer_widget_is_available_to_education_and_has_an_anchor(self) -> None:
        document = self._compile((
            PlanBlock(
                "answer",
                GridRect(1, 1, 3, 2),
                {"value": "3"},
                initial_visibility="hidden",
            ),
        ))
        self.assertEqual(document.components[0].state["visibility"], "hidden")
        self.assertEqual(document.anchor_map["a"].anchor_key, "answer")

    def test_compiles_choice_children_and_creates_one_anchor_for_the_whole_choice(self) -> None:
        document = self._compile((
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
        self.assertEqual(document.components[0].children[0].props["asset_id"], "cat")
        self.assertEqual(document.components[0].children[1].props["content"], "Mèo")
        self.assertEqual([(anchor.component_id, anchor.anchor_key) for anchor in document.anchors], [("1", "choice")])

    def test_compiles_surface_document_with_registry_state_and_document_anchors(self) -> None:
        document = self.compiler.compile_surface_document(
            surface_id="surface-test",
            data_bundle=self.bundle,
            domain_resources=self.resources,
            plan=PresentationPlan(
                domain_id="education",
                blocks=(
                    PlanBlock("text", GridRect(1, 1, 16, 1), {"content": "$title", "role": "title"}),
                    PlanBlock(
                        "choice",
                        GridRect(2, 3, 5, 5),
                        {},
                        initial_state={"selected": True},
                        children=(
                            ChoiceChild("image", {"asset_id": "cat"}),
                            ChoiceChild("text", {"content": "Mèo", "role": "label"}),
                        ),
                    ),
                    PlanBlock(
                        "answer",
                        GridRect(9, 3, 3, 2),
                        {"value": "3"},
                        initial_visibility="hidden",
                    ),
                ),
            ),
        )

        self.assertIsInstance(document, SurfaceDocument)
        self.assertEqual(document.surface_id, "surface-test")
        self.assertEqual(document.revision, 1)
        self.assertEqual(document.components[0].props["content"], "Dog and cat")
        self.assertEqual(document.components[1].state, {"visibility": "visible", "selected": True})
        self.assertEqual(document.components[1].children[0].to_dict(), {"type": "image", "props": {"asset_id": "cat"}})
        self.assertEqual(document.components[2].state, {"visibility": "hidden"})
        self.assertEqual([(anchor.component_id, anchor.anchor_key) for anchor in document.anchors], [
            ("1", "text"), ("2", "choice"), ("3", "answer"),
        ])

    def test_surface_document_rejects_unknown_initial_state_and_invalid_child_asset(self) -> None:
        with self.assertRaisesRegex(PanelCompilationError, "does not allow initial state fields"):
            self.compiler.compile_surface_document(
                data_bundle=self.bundle,
                domain_resources=self.resources,
                plan=PresentationPlan(
                    domain_id="education",
                    blocks=(PlanBlock(
                        "image", GridRect(1, 1, 4, 4), {"asset_id": "dog"},
                        initial_state={"flipped": True},
                    ),),
                ),
            )
        with self.assertRaisesRegex(PanelCompilationError, "unknown asset_id 'missing'"):
            self.compiler.compile_surface_document(
                data_bundle=self.bundle,
                domain_resources=self.resources,
                plan=PresentationPlan(
                    domain_id="education",
                    blocks=(PlanBlock(
                        "choice", GridRect(1, 1, 4, 4), {},
                        children=(ChoiceChild("image", {"asset_id": "missing"}),),
                    ),),
                ),
            )

    def test_rejects_invalid_or_duplicate_choice_children(self) -> None:
        with self.assertRaisesRegex(PanelCompilationError, "must contain at least one child"):
            self._compile((PlanBlock("choice", GridRect(1, 1, 4, 4), {}),))
        with self.assertRaisesRegex(PanelCompilationError, "does not allow child widget ids"):
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
        return self.compiler.compile_surface_document(
            surface_id="surface-test",
            data_bundle=self.bundle,
            domain_resources=self.resources,
            plan=PresentationPlan(domain_id="education", blocks=blocks),
        )


if __name__ == "__main__":
    unittest.main()
