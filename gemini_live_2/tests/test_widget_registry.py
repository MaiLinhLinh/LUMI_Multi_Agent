import unittest

from gemini_live_2.widgets import WidgetPropsError, build_default_widget_registry


class WidgetRegistryTests(unittest.TestCase):

    def test_choice_exposes_its_children_anchor_and_select_event(self) -> None:
        registry = build_default_widget_registry()
        choice = registry.get("choice")
        self.assertEqual(choice.validate({}), {})
        self.assertEqual(choice.anchors_for({})[0].key, "choice")
        self.assertEqual(choice.allowed_child_widget_ids, ("image", "text", "number_display", "object_group"))
        self.assertEqual(choice.interaction_event, "select")
    def setUp(self) -> None:
        self.registry = build_default_widget_registry()

    def test_default_widget_ids(self) -> None:
        self.assertEqual(
            self.registry.widget_ids(),
            ("text", "image", "object_group", "answer", "number_display", "choice"),
        )

    def test_widget_index_is_short_and_respects_domain_allow_list(self) -> None:
        self.assertEqual(
            self.registry.widget_index(("text", "image")),
            (
                {"id": "text", "purpose": "Hiển thị văn bản tự do như tiêu đề, nhãn hoặc nội dung ngắn."},
                {"id": "image", "purpose": "Hiển thị một ảnh hoặc minh hoạ từ Asset Catalog."},
            ),
        )

    def test_public_widget_contract_identifies_asset_backed_props(self) -> None:
        image = self.registry.get("image")
        self.assertEqual(image.widget_id, "image")
        self.assertEqual(image.purpose, "Hiển thị một ảnh hoặc minh hoạ từ Asset Catalog.")
        self.assertEqual(
            image.public_props_contract()["asset_id"],
            {
                "type": "string",
                "required": True,
                "template_value_kind": "binding",
                "description": "ID của asset ảnh sẽ hiển thị.",
                "source": "asset_catalog.id",
            },
        )
        self.assertEqual(
            self.registry.get("object_group").public_props_contract()["count"]["minimum"],
            1,
        )

    def test_widget_props_declare_template_binding_or_structure(self) -> None:
        text_contract = self.registry.get("text").public_props_contract()
        image_contract = self.registry.get("image").public_props_contract()

        self.assertEqual(text_contract["content"]["template_value_kind"], "binding")
        self.assertEqual(text_contract["role"]["template_value_kind"], "structural")
        self.assertEqual(image_contract["asset_id"]["template_value_kind"], "binding")

    def test_text_image_answer_and_number_display_have_visual_anchors(self) -> None:
        self.assertEqual(self.registry.get("text").anchors_for({"content": "Xin chào"})[0].key, "text")
        anchors = self.registry.get("image").anchors_for({"asset_id": "dog", "label": "Chó"})
        self.assertEqual(anchors[0].key, "image")
        self.assertEqual(anchors[0].allowed_effect_ids, ("highlight", "circle"))
        answer = self.registry.get("answer").anchors_for({"value": "3"})
        self.assertEqual(answer[0].key, "answer")
        self.assertEqual(answer[0].allowed_effect_ids, ("highlight", "circle"))
        number = self.registry.get("number_display").anchors_for({"value": "12"})
        self.assertEqual(number[0].key, "number")
        self.assertEqual(number[0].allowed_effect_ids, ("highlight", "circle"))

    def test_object_group_declares_group_and_per_item_anchors(self) -> None:
        anchors = self.registry.get("object_group").anchors_for({"asset_id": "dog", "count": 3})
        self.assertEqual([anchor.key for anchor in anchors], ["group", "item_1", "item_2", "item_3"])

    def test_invalid_widget_props_are_rejected(self) -> None:
        with self.assertRaises(WidgetPropsError):
            self.registry.get("object_group").validate({"asset_id": "dog", "count": 0})
        with self.assertRaises(WidgetPropsError):
            self.registry.get("image").validate({"asset_id": "dog", "unknown": True})
        with self.assertRaises(WidgetPropsError):
            self.registry.get("answer").validate({"value": ""})


if __name__ == "__main__":
    unittest.main()
