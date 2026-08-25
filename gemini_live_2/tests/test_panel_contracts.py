import unittest

from gemini_live_2.panel.contracts import (
    ActivePanelState,
    AnchorBinding,
    PanelBlock,
    PlanBlock,
    ContractValidationError,
    DataAlias,
    DataBundle,
    GridRect,
    PanelIR,
    PresentationPlan,
    RouteRequest,
)


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

    def test_data_bundle_exposes_only_declared_alias_catalog(self) -> None:
        alias = DataAlias(id="$pet_name", path=("subjects", "0", "name"), description="Ten con vat")
        bundle = DataBundle(domain_id="education", data={"subjects": [{"name": "Cho"}]}, aliases=(alias,))
        self.assertEqual(bundle.alias_catalog, (alias,))

    def test_active_panel_replacement_advances_revision(self) -> None:
        panel = PanelIR(
            panel_id="p1",
            domain_id="education",
            blocks=(sample_panel_block(),),
            anchors=(AnchorBinding("a", "1", "image", "panel.p1.dog", ("highlight",)),),
        )
        state = ActivePanelState(panel_ir=panel)
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
            anchors=(AnchorBinding("a", "1", "image", "panel:p1:block:1:anchor:image", ("highlight",)),),
        )
        updated = panel.with_block_visibility(block_ids={"1"}, visibility="visible")
        self.assertEqual(updated.panel_id, panel.panel_id)
        self.assertEqual(updated.anchors, panel.anchors)
        self.assertEqual(updated.blocks[0].id, "1")
        self.assertEqual(updated.blocks[0].visibility, "visible")

    def test_grid_requires_positive_integers(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "positive"):
            GridRect(col=0, row=1, col_span=1, row_span=1)


if __name__ == "__main__":
    unittest.main()
