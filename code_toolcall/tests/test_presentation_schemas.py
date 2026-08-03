import pytest
from pydantic import ValidationError
from rag_manager.presentation.schemas import PresentationPlan


def test_step_requires_fact_id_and_rejects_untrusted_target_fields():
    with pytest.raises(ValidationError):
        PresentationPlan.model_validate({"steps": [{"narration": "Hi", "effect": "highlight"}]})
    with pytest.raises(ValidationError):
        PresentationPlan.model_validate({"steps": [{"narration": "Hi", "fact_id": "fact", "focus": "overview"}]})
