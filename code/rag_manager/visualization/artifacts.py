"""Request-scoped staged artifact storage for generated visualization assets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from rag_manager.visualization.paths import resolve_output_dir
from rag_manager.visualization.validator import validate_security, validate_template_syntax


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    request_id: str
    kind: str
    status: str
    content: dict[str, Any]
    metadata: dict[str, Any]


class ArtifactStore:
    """Filesystem-backed, request-scoped artifact store.

    Artifacts are never promoted to the packaged registry automatically.
    """

    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.root = resolve_output_dir(output_dir) / "staged_artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def stage_artifact(self, content: dict[str, Any], metadata: dict[str, Any]) -> Artifact:
        if not isinstance(content, dict) or not isinstance(metadata, dict):
            raise ArtifactError("Artifact content and metadata must be objects.")
        request_id = _safe_id(metadata.get("request_id"), "request")
        kind = metadata.get("kind")
        if kind not in {"base_template", "component", "complete_template"}:
            raise ArtifactError("Unsupported artifact kind.")
        artifact_id = f"art_{request_id}_{kind}_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        artifact_metadata = {
            **metadata,
            "artifact_id": artifact_id,
            "request_id": request_id,
            "kind": kind,
            "source": metadata.get("source", "llm3"),
            "status": "staged",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
        directory = self.root / artifact_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "metadata.json").write_text(json.dumps(artifact_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return Artifact(artifact_id, request_id, kind, "staged", content, artifact_metadata)

    def get_artifact(self, artifact_id: str) -> Artifact:
        directory = self.root / _safe_id(artifact_id, "artifact")
        try:
            metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            content = json.loads((directory / "content.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"Unknown artifact: {artifact_id}") from exc
        return Artifact(artifact_id, metadata["request_id"], metadata["kind"], metadata["status"], content, metadata)

    def validate_artifact(self, artifact_id: str) -> Artifact:
        artifact = self.get_artifact(artifact_id)
        content = artifact.content
        if artifact.kind == "component":
            html = content.get("component_html")
            if not isinstance(html, str) or not html.strip():
                raise ArtifactError("Generated component HTML is empty.")
            validate_template_syntax(html)
            validate_security(html)
            metadata = content.get("metadata", {})
            if not isinstance(metadata, dict) or not isinstance(metadata.get("supported_slots"), list):
                raise ArtifactError("Generated component metadata must declare supported_slots.")
        elif artifact.kind in {"base_template", "complete_template"}:
            html = content.get("template_html") or content.get("base_html")
            if not isinstance(html, str) or not html.strip():
                raise ArtifactError("Generated template HTML is empty.")
            # A staged base is intentionally allowed to contain slot
            # placeholders; the assembled output must not contain them.
            if artifact.kind == "complete_template":
                validate_template_syntax(html)
            validate_security(html)
        return self._set_status(artifact, "validated")

    def mark_used_by_llm4(self, artifact_id: str) -> Artifact:
        return self._set_status(self.get_artifact(artifact_id), "used_by_llm4")

    def mark_assembled(self, artifact_id: str) -> Artifact:
        return self._set_status(self.get_artifact(artifact_id), "assembled")

    def expire_artifact(self, artifact_id: str) -> Artifact:
        return self._set_status(self.get_artifact(artifact_id), "expired")

    def reject_artifact(self, artifact_id: str, reason: str) -> Artifact:
        artifact = self.get_artifact(artifact_id)
        return self._set_status(artifact, "rejected", reason=reason)

    def _set_status(self, artifact: Artifact, status: str, *, reason: str | None = None) -> Artifact:
        metadata = {**artifact.metadata, "status": status}
        if reason:
            metadata["reason"] = reason
        directory = self.root / artifact.artifact_id
        (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return Artifact(artifact.artifact_id, artifact.request_id, artifact.kind, status, artifact.content, metadata)


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ArtifactError(f"Invalid {label} id.")
    return value
