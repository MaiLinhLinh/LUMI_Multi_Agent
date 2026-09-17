"""Standalone local Studio for drafting Lumi prompts and domains.

This process never writes inside ``gemini_live_2``.  It stores all edits in
``lumi_studio/workspace`` and starts testable snapshots in that workspace.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles


STUDIO_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = STUDIO_ROOT.parent
PROJECT_ROOT = REPOSITORY_ROOT / "gemini_live_2"
WEB_ROOT = STUDIO_ROOT / "web"
WORKSPACE_ROOT = STUDIO_ROOT / "workspace"
DRAFT_ROOT = WORKSPACE_ROOT / "drafts" / "current"
OVERRIDES_ROOT = DRAFT_ROOT / "overrides"
DRAFT_DOMAINS_ROOT = DRAFT_ROOT / "domains"
SANDBOXES_ROOT = WORKSPACE_ROOT / "sandboxes"
TEST_SANDBOX_PORT = 8005
TLS_ROOT = STUDIO_ROOT / "certs"
TLS_CERT_PATH = Path(os.environ.get("LUMI_STUDIO_TLS_CERT", TLS_ROOT / "lumi-lan-cert.pem"))
TLS_KEY_PATH = Path(os.environ.get("LUMI_STUDIO_TLS_KEY", TLS_ROOT / "lumi-lan-key.pem"))
DOMAIN_ID_RE = re.compile(r"^[a-z]+(?:_[a-z]+)*$")


class StudioError(ValueError):
    """A safe error intended for Studio users."""


@dataclass(frozen=True, slots=True)
class PromptSpec:
    prompt_id: str
    title: str
    owner: str
    trigger: str
    relative_path: str
    constant: str | None
    editable: bool
    kind: str = "python_constant"
    description: str = ""
    origin: str = "project"


CORE_PROMPTS = (
    PromptSpec(
        "gemini_live_route_guidance",
        "Gemini Live route guidance",
        "Gemini Live core",
        "Khi mở Gemini Live; được format động với danh sách domain khả dụng.",
        "live/registry.py",
        None,
        True,
        "python_fstring_return",
        "Core Live guidance. Studio chỉ sửa nội dung f-string của prompt_guidance(), vẫn giữ logic và biến {domains} của core.",
    ),
    PromptSpec(
        "presentation_context_guidance",
        "Presentation context guidance",
        "Gemini Live guide prompt",
        "Nạp một lần khi mở Gemini Live, cùng Core Live guidance.",
        "live/guide_prompt.py",
        "PRESENTATION_CONTEXT_GUIDANCE",
        True,
        description="Quy tắc đọc Stage Map, gọi present_visual và update_surface_state.",
    ),
    PromptSpec(
        "panel_interaction_guidance",
        "Panel interaction guidance",
        "Gemini Live guide prompt",
        "Khi Browser gửi PANEL_INTERACTION_EVENT.",
        "live/guide_prompt.py",
        "PANEL_INTERACTION_GUIDANCE",
        True,
        description="Cách Gemini hiểu event click/select/flip từ panel.",
    ),
    PromptSpec(
        "plan_agent_core",
        "Core Surface Lifecycle Instruction",
        "Plan Agent core",
        "Mỗi lượt Plan Agent; được ghép với prompt kế hoạch của domain.",
        "plan_agent/prompts.py",
        "CORE_SURFACE_LIFECYCLE_INSTRUCTION",
        True,
        description="Quy trình lập Surface Plan và contract JSON đầu ra.",
    ),
)


def _ensure_workspace() -> None:
    for path in (OVERRIDES_ROOT, DRAFT_DOMAINS_ROOT, SANDBOXES_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def _safe_relative(relative_path: str) -> Path:
    path = Path(relative_path.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise StudioError("Đường dẫn draft không hợp lệ.")
    return path


def _source_path(relative_path: str) -> Path:
    path = (PROJECT_ROOT / _safe_relative(relative_path)).resolve()
    try:
        path.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise StudioError("Đường dẫn nguồn nằm ngoài project.") from exc
    if not path.is_file():
        raise StudioError(f"Không tìm thấy file nguồn: {relative_path}")
    return path


def _override_path(relative_path: str) -> Path:
    return OVERRIDES_ROOT / _safe_relative(relative_path)


def _effective_file(relative_path: str) -> Path:
    override = _override_path(relative_path)
    return override if override.is_file() else _source_path(relative_path)


def _draft_domain_source_path(relative_path: str) -> Path:
    path = _safe_relative(relative_path)
    if len(path.parts) < 3 or path.parts[0] != "domains":
        raise StudioError("Prompt domain draft có đường dẫn không hợp lệ.")
    return DRAFT_DOMAINS_ROOT.joinpath(*path.parts[1:])


def _spec_source_path(spec: PromptSpec) -> Path:
    return _draft_domain_source_path(spec.relative_path) if spec.origin == "draft_domain" else _source_path(spec.relative_path)


def _spec_effective_file(spec: PromptSpec) -> Path:
    return _spec_source_path(spec) if spec.origin == "draft_domain" else _effective_file(spec.relative_path)


def _spec_write_path(spec: PromptSpec) -> Path:
    return _spec_source_path(spec) if spec.origin == "draft_domain" else _override_path(spec.relative_path)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _constant_value(source: str, constant: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise StudioError(f"Prompt source không parse được: {exc.msg}") from exc
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(isinstance(target, ast.Name) and target.id == constant for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "strip":
            value = value.func.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value.strip()
    raise StudioError(f"Không tìm thấy string constant {constant}.")


def _replace_constant(source: str, constant: str, content: str) -> str:
    if '"""' in content:
        raise StudioError('Prompt không được chứa chuỗi """.')
    pattern = re.compile(
        rf"(?ms)^(?P<prefix>{re.escape(constant)}\s*=\s*\"\"\").*?(?P<suffix>\"\"\"\.strip\(\))"
    )
    replacement = f'{constant} = """\n{content.strip()}\n""".strip()'
    changed, count = pattern.subn(replacement, source, count=1)
    if count != 1:
        raise StudioError(f"Không thể thay constant {constant} theo format hiện tại.")
    try:
        ast.parse(changed)
    except SyntaxError as exc:
        raise StudioError(f"Bản draft tạo ra Python không hợp lệ: {exc.msg}") from exc
    return changed


def _method_source(source: str, method_name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise StudioError(f"Không tìm thấy method {method_name}.")


def _method_fstring_return_value(source: str, method_name: str) -> str:
    """Extract the editable f-string returned by one prompt-building method."""

    method = _method_source(source, method_name)
    match = re.search(r'(?ms)^\s*return\s+f"""\n?(?P<content>.*?)"""\.strip\(\)', method)
    if match is None:
        raise StudioError(f"Không tìm thấy f-string return trong method {method_name}.")
    return match.group("content").strip()


def _replace_method_fstring_return(source: str, method_name: str, content: str) -> str:
    """Replace only the prompt text, preserving executable method logic."""

    if '"""' in content:
        raise StudioError('Prompt không được chứa chuỗi """.')
    method = _method_source(source, method_name)
    pattern = re.compile(r'(?ms)^(?P<indent>[ \t]*)return\s+f"""\n?.*?"""\.strip\(\)')
    changed_method, count = pattern.subn(
        lambda match: f'{match.group("indent")}return f"""\n{content.strip()}\n{match.group("indent")}""".strip()',
        method,
        count=1,
    )
    if count != 1:
        raise StudioError(f"Không thể thay prompt trong method {method_name}.")
    start = source.find(method)
    if start < 0:  # pragma: no cover - method came from this exact source.
        raise StudioError(f"Không thể ghi method {method_name}.")
    changed = source[:start] + changed_method + source[start + len(method):]
    try:
        ast.parse(changed)
    except SyntaxError as exc:
        raise StudioError(f"Bản draft tạo ra Python không hợp lệ: {exc.msg}") from exc
    return changed


def _domain_specs() -> tuple[PromptSpec, ...]:
    specs: list[PromptSpec] = []
    domains_root = PROJECT_ROOT / "domains"
    sources: list[tuple[Path, str]] = []
    if domains_root.is_dir():
        sources.extend((directory, "project") for directory in domains_root.iterdir() if directory.is_dir())
    if DRAFT_DOMAINS_ROOT.is_dir():
        sources.extend((directory, "draft_domain") for directory in DRAFT_DOMAINS_ROOT.iterdir() if directory.is_dir())
    for directory, origin in sorted(sources, key=lambda item: (item[0].name, item[1])):
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(_read_text(manifest_path))
            domain_id = str(manifest["domain_id"])
            presentation_path = str(manifest["presentation_prompt_path"])
            presentation_constant = str(manifest["presentation_prompt_constant"])
            plan_path = str(manifest["plan_prompt_path"])
            plan_constant = str(manifest["plan_prompt_constant"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        base = f"domains/{directory.name}"
        specs.extend((
            PromptSpec(
                f"domain_{domain_id}_presentation",
                f"{domain_id}: presentation prompt",
                f"Domain: {domain_id}",
                "Lần đầu Gemini gọi route_request cho domain này trong một Live connection; kèm response status=planning.",
                f"{base}/{presentation_path}",
                presentation_constant,
                True,
                description="Phong cách lời nói/trình bày thuộc domain.",
                origin=origin,
            ),
            PromptSpec(
                f"domain_{domain_id}_plan",
                f"{domain_id}: Plan Agent prompt",
                f"Domain: {domain_id}",
                "Mỗi lượt Plan Agent của domain này; được ghép với Plan Agent core.",
                f"{base}/{plan_path}",
                plan_constant,
                True,
                description="Logic nghiệp vụ, phong cách hoặc policy lập kế hoạch thuộc domain.",
                origin=origin,
            ),
        ))
    return tuple(specs)


def _prompt_specs() -> tuple[PromptSpec, ...]:
    return CORE_PROMPTS + _domain_specs()


def _find_spec(prompt_id: str) -> PromptSpec:
    for spec in _prompt_specs():
        if spec.prompt_id == prompt_id:
            return spec
    raise StudioError("Không tìm thấy prompt.")


def _prompt_payload(spec: PromptSpec, include_content: bool = False) -> dict[str, Any]:
    payload = asdict(spec)
    payload["drafted"] = spec.origin == "draft_domain" or _override_path(spec.relative_path).is_file()
    if not include_content:
        return payload
    source_path = _spec_effective_file(spec)
    source = _read_text(source_path)
    if spec.kind == "python_fstring_return":
        payload["content"] = _method_fstring_return_value(source, "prompt_guidance")
        payload["original_content"] = (
            "" if spec.origin == "draft_domain" else _method_fstring_return_value(
                _read_text(_source_path(spec.relative_path)), "prompt_guidance"
            )
        )
    else:
        assert spec.constant is not None
        payload["content"] = _constant_value(source, spec.constant)
        payload["original_content"] = (
            "" if spec.origin == "draft_domain" else _constant_value(
                _read_text(_source_path(spec.relative_path)), spec.constant
            )
        )
    return payload


def _draft_domain_ids() -> tuple[str, ...]:
    if not DRAFT_DOMAINS_ROOT.is_dir():
        return ()
    return tuple(sorted(path.name for path in DRAFT_DOMAINS_ROOT.iterdir() if (path / "manifest.json").is_file()))


def _affected_domain_ids() -> tuple[str, ...]:
    ids = set(_draft_domain_ids())
    if OVERRIDES_ROOT.is_dir():
        for path in OVERRIDES_ROOT.rglob("*"):
            try:
                parts = path.relative_to(OVERRIDES_ROOT).parts
            except ValueError:
                continue
            if len(parts) >= 3 and parts[0] == "domains":
                ids.add(parts[1])
    return tuple(sorted(ids))


def _copy_draft_into(project_copy: Path) -> None:
    if OVERRIDES_ROOT.is_dir():
        for source in OVERRIDES_ROOT.rglob("*"):
            if not source.is_file():
                continue
            target = project_copy / source.relative_to(OVERRIDES_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if DRAFT_DOMAINS_ROOT.is_dir():
        target_root = project_copy / "domains"
        for source in DRAFT_DOMAINS_ROOT.iterdir():
            if not source.is_dir():
                continue
            target = target_root / source.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)


def _copy_source_to(destination: Path) -> None:
    if not PROJECT_ROOT.is_dir():
        raise StudioError("Không tìm thấy gemini_live_2 để tạo sandbox.")
    shutil.copytree(
        PROJECT_ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".git",
            ".lumi_studio",
            ".framework_docx_render",
        ),
    )


def _validate_current_draft() -> dict[str, Any]:
    _ensure_workspace()
    errors: list[dict[str, str]] = []
    for override in OVERRIDES_ROOT.rglob("*.py"):
        try:
            ast.parse(_read_text(override))
        except SyntaxError as exc:
            errors.append({"scope": str(override.relative_to(OVERRIDES_ROOT)), "detail": f"Python syntax: {exc.msg}"})
    affected = _affected_domain_ids()
    if errors or not affected:
        return {"ok": not errors, "checked_domain_ids": list(affected), "errors": errors}

    sys.path.insert(0, str(REPOSITORY_ROOT))
    try:
        from gemini_live_2.catalogs.domains import DomainRegistry, ManifestError
    except Exception as exc:  # pragma: no cover - environment-specific import failure
        return {"ok": False, "checked_domain_ids": list(affected), "errors": [{"scope": "Studio", "detail": str(exc)}]}
    with tempfile.TemporaryDirectory(prefix="lumi-studio-validate-") as temp_dir:
        copied = Path(temp_dir) / "project"
        _copy_source_to(copied)
        _copy_draft_into(copied)
        registry = DomainRegistry(copied / "domains")
        for domain_id in affected:
            try:
                registry.load(domain_id)
            except (ManifestError, ValueError) as exc:
                errors.append({"scope": f"domain:{domain_id}", "detail": str(exc)})
    return {"ok": not errors, "checked_domain_ids": list(affected), "errors": errors}


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _sandbox_tls_files() -> tuple[Path, Path]:
    """Require the certificate used to make Browser microphone access valid."""

    missing = [str(path) for path in (TLS_CERT_PATH, TLS_KEY_PATH) if not path.is_file()]
    if missing:
        raise StudioError(
            "Sandbox test requires HTTPS. Create the LAN certificate first with "
            "scripts/create_lan_tls.ps1; missing: " + ", ".join(missing)
        )
    return TLS_CERT_PATH, TLS_KEY_PATH


def _sandbox_payload(directory: Path) -> dict[str, Any]:
    metadata_path = directory / "metadata.json"
    if not metadata_path.is_file():
        return {"id": directory.name, "status": "unknown"}
    try:
        payload = json.loads(_read_text(metadata_path))
    except json.JSONDecodeError:
        return {"id": directory.name, "status": "invalid_metadata"}
    return payload if isinstance(payload, dict) else {"id": directory.name, "status": "invalid_metadata"}


def _sandbox_list() -> list[dict[str, Any]]:
    _ensure_workspace()
    return [_sandbox_payload(path) for path in sorted(SANDBOXES_ROOT.iterdir(), reverse=True) if path.is_dir()]


def _sandbox_root_from_id(sandbox_id: object) -> Path:
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise StudioError("Sandbox ID không hợp lệ.")
    target = (SANDBOXES_ROOT / sandbox_id).resolve()
    try:
        target.relative_to(SANDBOXES_ROOT.resolve())
    except ValueError as exc:
        raise StudioError("Sandbox ID không hợp lệ.") from exc
    return target


def _pid_is_running(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a reliable existence probe on Windows:
        # it may raise WinError 11 even for a process that Studio started.
        # OpenProcess is the native, non-destructive check for this platform.
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        # Access can be denied for an existing process owned by another user.
        # Treat it as running so Studio never reuses its sandbox unsafely.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_sandbox_process(metadata: dict[str, Any]) -> None:
    """Stop only the server process recorded for this Studio sandbox."""

    pid = metadata.get("pid")
    if _pid_is_running(pid):
        os.kill(pid, getattr(__import__("signal"), "SIGTERM"))
        deadline = time.monotonic() + 4.0
        while _pid_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
    if _pid_is_running(pid):
        raise StudioError("Sandbox cũ chưa dừng được; không thể cập nhật an toàn.")


def _test_sandbox_root() -> tuple[Path, dict[str, Any] | None]:
    """Return the one persistent sandbox used for every Studio test run."""

    for directory in sorted(SANDBOXES_ROOT.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        metadata = _sandbox_payload(directory)
        if metadata.get("port") == TEST_SANDBOX_PORT:
            return directory, metadata
    return SANDBOXES_ROOT / "sandbox_test", None


def _clear_sandbox_project(sandbox_root: Path) -> None:
    project_root = (sandbox_root / "project").resolve()
    try:
        project_root.relative_to(sandbox_root.resolve())
    except ValueError as exc:  # pragma: no cover - fixed path above.
        raise StudioError("Không thể làm mới sandbox ngoài workspace.") from exc
    if project_root.exists():
        shutil.rmtree(project_root)


def _start_sandbox() -> dict[str, Any]:
    validation = _validate_current_draft()
    if not validation["ok"]:
        raise StudioError("Draft chưa hợp lệ; hãy Validate và sửa lỗi trước khi test.")
    _ensure_workspace()
    sandbox_root, previous_metadata = _test_sandbox_root()
    reused = sandbox_root.exists()
    if previous_metadata is not None and _pid_is_running(previous_metadata.get("pid")):
        _stop_sandbox_process(previous_metadata)
    _clear_sandbox_project(sandbox_root)
    sandbox_root.mkdir(parents=True, exist_ok=True)
    if not _port_is_available(TEST_SANDBOX_PORT):
        raise StudioError(f"Port test {TEST_SANDBOX_PORT} đang được dùng bởi process khác.")
    cert_path, key_path = _sandbox_tls_files()
    # Keep the package directory name intact: web_app.py imports
    # ``gemini_live_2.*`` and therefore cannot run from a nameless contents-only copy.
    project_copy = sandbox_root / "project" / "gemini_live_2"
    try:
        project_copy.parent.mkdir(parents=True)
        _copy_source_to(project_copy)
        _copy_draft_into(project_copy)
        log_path = sandbox_root / "server.log"
        command = [
            sys.executable, "-m", "uvicorn", "web_app:app",
            "--host", "0.0.0.0", "--port", str(TEST_SANDBOX_PORT),
            "--ssl-certfile", str(cert_path),
            "--ssl-keyfile", str(key_path),
        ]
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                cwd=project_copy,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        metadata = {
            "id": sandbox_root.name,
            "status": "starting",
            "pid": process.pid,
            "port": TEST_SANDBOX_PORT,
            "scheme": "https",
            # The browser builds the public URL from its own hostname and this
            # port.  Do not persist 127.0.0.1 here: that address only works on
            # the Studio host, not for another device on the LAN.
            "url": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "draft": "current",
            "checked_domain_ids": validation["checked_domain_ids"],
            "mode": "updated" if reused else "created",
        }
        (sandbox_root / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        for _ in range(24):
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    f"https://127.0.0.1:{TEST_SANDBOX_PORT}/api/health",
                    timeout=0.35,
                    context=ssl._create_unverified_context(),
                ):
                    metadata["status"] = "running"
                    break
            except Exception:
                time.sleep(0.25)
        if metadata["status"] != "running":
            metadata["status"] = "failed"
            metadata["detail"] = _read_text(log_path)[-2500:] if log_path.is_file() else "Sandbox process did not start."
        (sandbox_root / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return metadata
    except Exception:
        if sandbox_root.exists() and not (sandbox_root / "metadata.json").exists():
            shutil.rmtree(sandbox_root)
        raise


async def _payload(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception as exc:
        raise StudioError("Body JSON không hợp lệ.") from exc
    if not isinstance(data, dict):
        raise StudioError("Body phải là JSON object.")
    return data


async def studio_home(_: Request) -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


async def api_overview(_: Request) -> JSONResponse:
    _ensure_workspace()
    return JSONResponse({
        "project_root": str(PROJECT_ROOT),
        "workspace_root": str(WORKSPACE_ROOT),
        "draft_name": "current",
        "source_is_read_only": True,
        "test_instruction": "Studio creates a copied sandbox and never overwrites gemini_live_2.",
    })


async def api_prompts(_: Request) -> JSONResponse:
    return JSONResponse({"prompts": [_prompt_payload(spec) for spec in _prompt_specs()]})


async def api_prompt(request: Request) -> JSONResponse:
    return JSONResponse(_prompt_payload(_find_spec(str(request.path_params["prompt_id"])), include_content=True))


async def api_save_prompt(request: Request) -> JSONResponse:
    spec = _find_spec(str(request.path_params["prompt_id"]))
    if not spec.editable:
        raise StudioError("Prompt này chỉ xem được.")
    content = (await _payload(request)).get("content")
    if not isinstance(content, str) or not content.strip():
        raise StudioError("Prompt draft phải là text không rỗng.")
    source = _read_text(_spec_effective_file(spec))
    if spec.kind == "python_constant":
        assert spec.constant is not None
        changed = _replace_constant(source, spec.constant, content)
    elif spec.kind == "python_fstring_return":
        changed = _replace_method_fstring_return(source, "prompt_guidance", content)
    else:  # pragma: no cover - every editable spec is declared above.
        raise StudioError("Studio không hỗ trợ kiểu prompt này.")
    target = _spec_write_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(changed, encoding="utf-8")
    return JSONResponse(_prompt_payload(spec, include_content=True))


async def api_domains(_: Request) -> JSONResponse:
    source_ids = []
    domains_root = PROJECT_ROOT / "domains"
    if domains_root.is_dir():
        source_ids = sorted(path.name for path in domains_root.iterdir() if (path / "manifest.json").is_file())
    return JSONResponse({"source_domain_ids": source_ids, "draft_domain_ids": list(_draft_domain_ids())})


async def api_create_domain(request: Request) -> JSONResponse:
    data = await _payload(request)
    domain_id = str(data.get("domain_id") or "").strip()
    if not DOMAIN_ID_RE.fullmatch(domain_id):
        raise StudioError("Domain ID chỉ dùng chữ thường và dấu gạch dưới, ví dụ history hoặc life_cycle.")
    if (PROJECT_ROOT / "domains" / domain_id).exists() or (DRAFT_DOMAINS_ROOT / domain_id).exists():
        raise StudioError("Domain ID đã tồn tại ở project hoặc draft.")
    presentation_prompt = str(data.get("presentation_prompt") or "").strip()
    plan_prompt = str(data.get("plan_prompt") or "").strip()
    if not presentation_prompt or not plan_prompt:
        raise StudioError("Cần nhập cả prompt trình bày Gemini Live và prompt Plan Agent.")
    root = DRAFT_DOMAINS_ROOT / domain_id
    root.mkdir(parents=True)
    manifest = {
        "domain_id": domain_id,
        "presentation_prompt_path": "prompt.py",
        "presentation_prompt_constant": f"{domain_id.upper()}_PRESENTATION_INSTRUCTION",
        "plan_prompt_path": "plan_prompt.py",
        "plan_prompt_constant": f"{domain_id.upper()}_PLAN_INSTRUCTION",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "prompt.py").write_text(
        f'"""Presentation style owned by the {domain_id} domain."""\n\n'
        f'{manifest["presentation_prompt_constant"]} = """\n{presentation_prompt}\n""".strip()\n',
        encoding="utf-8",
    )
    (root / "plan_prompt.py").write_text(
        f'"""Planning guidance owned by the {domain_id} domain."""\n\n'
        f'{manifest["plan_prompt_constant"]} = """\n{plan_prompt}\n""".strip()\n',
        encoding="utf-8",
    )
    return JSONResponse({"domain_id": domain_id, "draft_path": str(root), "validation": _validate_current_draft()}, status_code=201)


async def api_validate(_: Request) -> JSONResponse:
    return JSONResponse(_validate_current_draft())


async def api_sandboxes(_: Request) -> JSONResponse:
    return JSONResponse({"sandboxes": _sandbox_list()})


async def api_start_sandbox(_: Request) -> JSONResponse:
    return JSONResponse(_start_sandbox(), status_code=201)


async def api_stop_sandbox(request: Request) -> JSONResponse:
    sandbox_id = str(request.path_params["sandbox_id"])
    target = (SANDBOXES_ROOT / sandbox_id).resolve()
    try:
        target.relative_to(SANDBOXES_ROOT.resolve())
    except ValueError as exc:
        raise StudioError("Sandbox ID không hợp lệ.") from exc
    metadata = _sandbox_payload(target)
    pid = metadata.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, getattr(__import__("signal"), "SIGTERM"))
        except ProcessLookupError:
            pass
    metadata["status"] = "stopped"
    metadata["stopped_at"] = datetime.now(timezone.utc).isoformat()
    (target / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse(metadata)


async def studio_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, StudioError):
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"error": "Studio gặp lỗi nội bộ.", "detail": str(exc)}, status_code=500)


app = Starlette(
    debug=True,
    routes=[
        Route("/", studio_home),
        Route("/api/overview", api_overview),
        Route("/api/prompts", api_prompts),
        Route("/api/prompts/{prompt_id}", api_prompt),
        Route("/api/prompts/{prompt_id}", api_save_prompt, methods=["PUT"]),
        Route("/api/domains", api_domains),
        Route("/api/domains", api_create_domain, methods=["POST"]),
        Route("/api/validate", api_validate, methods=["POST"]),
        Route("/api/sandboxes", api_sandboxes),
        Route("/api/sandboxes", api_start_sandbox, methods=["POST"]),
        Route("/api/sandboxes/{sandbox_id}/stop", api_stop_sandbox, methods=["POST"]),
    ],
    exception_handlers={StudioError: studio_error_handler},
)
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


if __name__ == "__main__":
    import uvicorn

    _ensure_workspace()
    uvicorn.run(app, host="0.0.0.0", port=8003)
