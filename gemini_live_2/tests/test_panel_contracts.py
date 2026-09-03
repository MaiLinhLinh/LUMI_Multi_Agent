import unittest

from gemini_live_2.panel.contracts import (
    ActivePanelState,
    AnchorBinding,
    BlockState,
    ChoiceChild,
    CreateSurfacePlan,
    DeleteSurface,
    PatchSurfacePlan,
    PanelBlock,
    PlanBlock,
    ContractValidationError,
    DataAlias,
    DataBundle,
    GridRect,
    PanelIR,
    PresentationPlan,
    RouteRequest,
    SurfaceState,
    SurfaceStructure,
    materialize_panel_ir,
    surface_plan_command_from_dict,
)
from gemini_live_2.widgets import build_default_widget_registry


def sample_plan_block() -> PlanBlock:
    return PlanBlock(
        widget_id="image",
        grid=GridRect(col=1, row=2, col_span=5, row_span=5),
        props={"asset_id": "dog"},
    )


def sample_panel_block(block_id: str = "1") -> PanelBlock:
    return PanelBlock(
        id=block_id,
        widget_id="image",
        grid=GridRect(col=1, row=2, col_span=5, row_span=5),
        props={"asset_id": "dog"},
    )


class PanelContractsTests(unittest.TestCase):
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

    def test_active_panel_replacement_advances_revision(self) -> None:
        panel = PanelIR(
            panel_id="p1",
            domain_id="education",
            blocks=(sample_panel_block(),),
            anchors=(AnchorBinding("a", "1", "image", ("highlight",)),),
        )
        state = ActivePanelState(panel_ir=panel, purpose="So sánh hai con vật")
        updated = state.replace(
            PanelIR(panel_id="p2", domain_id="education", blocks=(sample_panel_block("1"),))
        )
        self.assertEqual((state.revision, updated.revision), (1, 2))
        self.assertEqual(updated.panel_ir.panel_id, "p2")

    def test_panel_ir_visibility_update_preserves_ids_and_anchors(self) -> None:
        panel = PanelIR(
            panel_id="p1",
            domain_id="education",
            blocks=(
                PanelBlock(
                    id="1", widget_id="image", grid=GridRect(1, 1, 2, 2),
                    props={"asset_id": "dog"}, visibility="hidden",
                ),
            ),
            anchors=(AnchorBinding("a", "1", "image", ("highlight",)),),
        )
        updated = panel.with_block_visibility(block_ids={"1"}, visibility="visible")
        self.assertEqual(updated.panel_id, panel.panel_id)
        self.assertEqual(updated.anchors, panel.anchors)
        self.assertEqual(updated.blocks[0].id, "1")
        self.assertEqual(updated.blocks[0].visibility, "visible")

    def test_structure_and_state_materialize_the_existing_panel_ir_contract(self) -> None:
        panel = PanelIR(
            panel_id="p1",
            domain_id="education",
            blocks=(
                PanelBlock(
                    id="1", widget_id="image", grid=GridRect(1, 1, 2, 2),
                    props={"asset_id": "dog"}, visibility="hidden",
                ),
            ),
            anchors=(AnchorBinding("a", "1", "image", ("highlight",)),),
        )
        structure = SurfaceStructure.from_panel_ir(panel)
        state = SurfaceState({"1": BlockState(visibility="visible", selected=True)})

        materialized = materialize_panel_ir(structure=structure, state=state)

        self.assertEqual(materialized.blocks[0].visibility, "visible")
        self.assertEqual(materialized.blocks[0].props, {"asset_id": "dog"})
        self.assertEqual(materialized.anchors, panel.anchors)
        self.assertEqual(state.to_dict()["1"]["selected"], True)
        self.assertEqual(structure.to_dict()["surface_id"], "p1")

    def test_active_panel_converts_legacy_panel_ir_to_separated_structure_and_state(self) -> None:
        panel = PanelIR(
            panel_id="p1",
            domain_id="education",
            blocks=(sample_panel_block(),),
        )
        active = ActivePanelState(panel_ir=panel, purpose="Hiển thị hình chó")

        self.assertEqual(active.structure.surface_id, "p1")
        self.assertEqual(active.state.state_for("1").visibility, "visible")
        self.assertEqual(active.panel_ir, panel)

    def test_grid_requires_positive_integers(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "positive"):
            GridRect(col=0, row=1, col_span=1, row_span=1)

    def test_widget_registry_allows_only_registered_visibility_transitions(self) -> None:
        image = build_default_widget_registry().get("image")
        self.assertEqual(
            image.validate_state_changes(
                current_state=BlockState(visibility="hidden").to_dict(),
                changes={"visibility": "visible"},
            )["visibility"],
            "visible",
        )
        with self.assertRaisesRegex(ValueError, "cannot transition"):
            image.validate_state_changes(
                current_state=BlockState(visibility="visible").to_dict(),
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
                ],
            }
        )

        self.assertIsInstance(command, PatchSurfacePlan)
        self.assertEqual([item["op"] for item in command.to_dict()["operations"]], [
            "add_block", "remove_block", "replace_block", "move_block", "update_props",
        ])
        self.assertEqual(command.to_dict()["operations"][-1]["changes"], {"asset_id": "cat", "label": "MÃ¨o"})

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
