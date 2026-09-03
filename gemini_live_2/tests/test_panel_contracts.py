import unittest

from gemini_live_2.panel.contracts import (
    ActivePanelState,
    AnchorBinding,
    ChoiceChild,
    ComponentChild,
    ComponentNode,
    CreateSurfacePlan,
    DeleteSurface,
    PatchSurfacePlan,
    PlanBlock,
    ContractValidationError,
    DataAlias,
    DataBundle,
    GridRect,
    PresentationPlan,
    RouteRequest,
    SurfaceDocument,
    surface_plan_command_from_dict,
)
from gemini_live_2.widgets import build_default_widget_registry


def sample_plan_block() -> PlanBlock:
    return PlanBlock(
        widget_id="image",
        grid=GridRect(col=1, row=2, col_span=5, row_span=5),
        props={"asset_id": "dog"},
    )


def sample_component(component_id: str = "1") -> ComponentNode:
    return ComponentNode(
        id=component_id,
        type="image",
        layout=GridRect(col=1, row=2, col_span=5, row_span=5),
        props={"asset_id": "dog"},
        state={"visibility": "visible"},
    )


class PanelContractsTests(unittest.TestCase):
    def test_surface_document_serializes_components_state_children_and_document_anchors(self) -> None:
        document = SurfaceDocument(
            surface_id="surface-1",
            domain_id="education",
            revision=1,
            components=(
                ComponentNode(
                    id="1",
                    type="choice",
                    layout=GridRect(col=1, row=1, col_span=4, row_span=4),
                    props={"choice_id": "cat"},
                    state={"visibility": "visible", "selected": False},
                    children=(
                        ComponentChild(type="image", props={"asset_id": "cat"}),
                        ComponentChild(type="text", props={"content": "Mèo", "role": "label"}),
                    ),
                ),
            ),
            anchors=(AnchorBinding("a", "1", "choice", ("highlight", "circle")),),
        )

        payload = document.to_dict()
        self.assertEqual(payload["components"][0]["type"], "choice")
        self.assertEqual(payload["components"][0]["state"], {"visibility": "visible", "selected": False})
        self.assertEqual(payload["components"][0]["children"][0]["type"], "image")
        self.assertEqual(payload["anchors"][0]["component_id"], "1")
        self.assertEqual(document.component_map["1"].type, "choice")
        self.assertEqual(document.anchor_map["a"].component_id, "1")

    def test_component_node_requires_layout_and_visibility_state(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "component.layout"):
            ComponentNode(
                id="1", type="image", layout=None, state={"visibility": "visible"}  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ContractValidationError, "state.visibility"):
            ComponentNode(id="1", type="image", layout=GridRect(1, 1, 1, 1), state={})
        with self.assertRaisesRegex(ContractValidationError, "state.visibility"):
            ComponentNode(
                id="1", type="image", layout=GridRect(1, 1, 1, 1), state={"visibility": "pending"}
            )

    def test_surface_document_rejects_duplicate_component_ids_and_unknown_anchor_reference(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "duplicate component ids"):
            SurfaceDocument(
                surface_id="surface-1",
                domain_id="education",
                revision=1,
                components=(sample_component("1"), sample_component("1")),
            )
        with self.assertRaisesRegex(ContractValidationError, "unknown component"):
            SurfaceDocument(
                surface_id="surface-1",
                domain_id="education",
                revision=1,
                components=(sample_component("1"),),
                anchors=(AnchorBinding("a", "missing", "image", ("highlight",)),),
            )

    def test_surface_document_requires_positive_revision(self) -> None:
        for revision in (0, -1, False):
            with self.subTest(revision=revision), self.assertRaisesRegex(ContractValidationError, "revision"):
                SurfaceDocument(
                    surface_id="surface-1",
                    domain_id="education",
                    revision=revision,  # type: ignore[arg-type]
                    components=(sample_component(),),
                )

    def test_route_request_normalizes_and_serializes(self) -> None:
        request = RouteRequest.from_dict({"domain_id": " education ", "intent": " Cho be xem cho "})
        self.assertEqual(request.to_dict(), {"domain_id": "education", "intent": "Cho be xem cho"})

    def test_plan_blocks_do_not_have_agent_created_ids(self) -> None:
        plan = PresentationPlan(domain_id="education", blocks=(sample_plan_block(), sample_plan_block()))
        self.assertEqual([block.widget_id for block in plan.blocks], ["image", "image"])
        self.assertNotIn("id", plan.to_dict()["blocks"][0])

    def test_plan_block_visibility_defaults_to_visible_and_rejects_unknown_values(self) -> None:
        self.assertEqual(sample_plan_block().initial_visibility, "visible")
        self.assertEqual(sample_plan_block().to_dict()["initial_visibility"], "visible")
        with self.assertRaisesRegex(ContractValidationError, "initial_visibility"):
            PlanBlock(
                widget_id="image",
                grid=GridRect(col=1, row=1, col_span=1, row_span=1),
                initial_visibility="pending",
            )

    def test_plan_block_initial_state_is_optional_and_visibility_is_compatible(self) -> None:
        restored = PlanBlock.from_dict({
            "widget_id": "image",
            "grid": {"col": 1, "row": 1, "col_span": 1, "row_span": 1},
            "initial_state": {"visibility": "hidden"},
        })
        self.assertEqual(restored.initial_visibility, "hidden")
        self.assertEqual(restored.initial_state, {"visibility": "hidden"})
        with self.assertRaisesRegex(ContractValidationError, "must match"):
            PlanBlock(
                widget_id="image",
                grid=GridRect(col=1, row=1, col_span=1, row_span=1),
                initial_visibility="hidden",
                initial_state={"visibility": "visible"},
            )

    def test_choice_children_serialize_without_an_inner_grid(self) -> None:
        block = PlanBlock(
            widget_id="choice",
            grid=GridRect(col=1, row=1, col_span=4, row_span=4),
            props={},
            children=(
                ChoiceChild(widget_id="image", props={"asset_id": "cat"}),
                ChoiceChild(widget_id="text", props={"content": "Mèo", "role": "label"}),
            ),
        )
        restored = PlanBlock.from_dict(block.to_dict())
        self.assertEqual(restored.children[0].widget_id, "image")
        self.assertEqual(restored.children[1].props["content"], "Mèo")
        self.assertNotIn("grid", restored.to_dict()["children"][0])

    def test_data_bundle_exposes_only_declared_alias_catalog(self) -> None:
        alias = DataAlias(id="$pet_name", path=("subjects", "0", "name"), description="Ten con vat")
        bundle = DataBundle(domain_id="education", data={"subjects": [{"name": "Cho"}]}, aliases=(alias,))
        self.assertEqual(bundle.alias_catalog, (alias,))

    def test_active_panel_replacement_keeps_surface_document_as_the_only_state(self) -> None:
        state = ActivePanelState(
            document=SurfaceDocument(
                surface_id="p1",
                domain_id="education",
                revision=1,
                components=(sample_component(),),
                anchors=(AnchorBinding("a", "1", "image", ("highlight",)),),
            ),
            purpose="So sánh hai con vật",
        )
        updated = state.replace(
            SurfaceDocument(
                surface_id="p2",
                domain_id="education",
                revision=2,
                components=(sample_component("2"),),
            )
        )
        self.assertEqual((state.revision, updated.revision), (1, 2))
        self.assertEqual(updated.document.surface_id, "p2")
        self.assertEqual(updated.document.components[0].id, "2")

    def test_grid_requires_positive_integers(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "positive"):
            GridRect(col=0, row=1, col_span=1, row_span=1)

    def test_widget_registry_allows_only_registered_visibility_transitions(self) -> None:
        image = build_default_widget_registry().get("image")
        self.assertEqual(
            image.validate_state_changes(
                current_state={"visibility": "hidden"},
                changes={"visibility": "visible"},
            )["visibility"],
            "visible",
        )
        with self.assertRaisesRegex(ValueError, "cannot transition"):
            image.validate_state_changes(
                current_state={"visibility": "visible"},
                changes={"visibility": "visible"},
            )

    def test_create_surface_plan_keeps_the_existing_block_shape(self) -> None:
        command = surface_plan_command_from_dict(
            {
                "action": "create_surface_plan",
                "template_description": "Một ảnh minh hoạ.",
                "surface": {"blocks": [sample_plan_block().to_dict()]},
            }
        )

        self.assertIsInstance(command, CreateSurfacePlan)
        self.assertEqual(command.blocks[0].props["asset_id"], "dog")
        self.assertEqual(command.to_dict()["surface"]["blocks"][0]["widget_id"], "image")

    def test_patch_surface_plan_parses_each_supported_structure_operation(self) -> None:
        command = surface_plan_command_from_dict(
            {
                "action": "patch_surface_plan",
                "surface_id": "s1",
                "base_revision": 3,
                "operations": [
                    {"op": "add_block", "block": sample_plan_block().to_dict()},
                    {"op": "remove_block", "anchor_id": "a"},
                    {"op": "replace_block", "anchor_id": "b", "block": sample_plan_block().to_dict()},
                    {
                        "op": "move_block",
                        "anchor_id": "c",
                        "grid": {"col": 3, "row": 4, "col_span": 2, "row_span": 2},
                    },
                    {
                        "op": "update_props",
                        "anchor_id": "d",
                        "changes": {"asset_id": "cat", "label": "MÃ¨o"},
                    },
                    {
                        "op": "replace_children",
                        "anchor_id": "e",
                        "children": [{"widget_id": "image", "props": {"asset_id": "cat"}}],
                    },
                ],
            }
        )

        self.assertIsInstance(command, PatchSurfacePlan)
        self.assertEqual([item["op"] for item in command.to_dict()["operations"]], [
            "add_block", "remove_block", "replace_block", "move_block", "update_props", "replace_children",
        ])
        self.assertEqual(command.to_dict()["operations"][-2]["changes"], {"asset_id": "cat", "label": "MÃ¨o"})
        self.assertEqual(command.to_dict()["operations"][-1]["children"], [
            {"widget_id": "image", "props": {"asset_id": "cat"}},
        ])

    def test_patch_surface_plan_rejects_unknown_operation_and_empty_prop_changes(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "not supported"):
            surface_plan_command_from_dict(
                {
                    "action": "patch_surface_plan",
                    "surface_id": "s1",
                    "base_revision": 1,
                    "operations": [{"op": "clone_block", "anchor_id": "a"}],
                }
            )
        with self.assertRaisesRegex(ContractValidationError, "non-empty"):
            PatchSurfacePlan.from_dict(
                {
                    "action": "patch_surface_plan",
                    "surface_id": "s1",
                    "base_revision": 1,
                    "operations": [{"op": "update_props", "anchor_id": "a", "changes": {}}],
                }
            )

    def test_surface_commands_require_a_positive_base_revision(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "base_revision"):
            PatchSurfacePlan.from_dict(
                {
                    "action": "patch_surface_plan",
                    "surface_id": "s1",
                    "base_revision": 0,
                    "operations": [{"op": "remove_block", "anchor_id": "a"}],
                }
            )
        with self.assertRaisesRegex(ContractValidationError, "base_revision"):
            DeleteSurface.from_dict({"surface_id": "s1", "base_revision": False})


if __name__ == "__main__":
    unittest.main()
