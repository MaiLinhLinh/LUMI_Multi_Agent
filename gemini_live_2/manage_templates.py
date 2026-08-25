"""Small CLI for deleting generated reusable layout templates safely.

Run this file from the ``gemini_live_2`` directory.  It always changes the
layout JSON and its catalog record through TemplateCatalog, never separately.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT.parent))

from gemini_live_2.catalogs.templates import (  # noqa: E402
    TemplateCatalog,
    TemplateCatalogError,
    load_template_catalog,
)


_GENERATED_TEMPLATE_ID = re.compile(r"tm[1-9][0-9]*\Z")


def _load_catalog(domain_id: str) -> TemplateCatalog:
    domain_root = PROJECT_ROOT / "domains" / domain_id
    manifest_path = domain_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Không tìm thấy domain: {domain_id}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest.json của domain '{domain_id}' không hợp lệ: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest.json của domain '{domain_id}' phải là object.")
    catalog_path = manifest.get("template_catalog_path")
    if not isinstance(catalog_path, str) or not catalog_path.strip():
        raise ValueError(f"Domain '{domain_id}' không khai báo template_catalog_path.")
    return load_template_catalog(
        catalog_path=domain_root / catalog_path,
        domain_root=domain_root,
        expected_domain_id=domain_id,
    )


def _generated_ids(catalog: TemplateCatalog) -> list[str]:
    return [entry.id for entry in catalog.entries if _GENERATED_TEMPLATE_ID.fullmatch(entry.id)]


def _delete(catalog: TemplateCatalog, template_id: str) -> TemplateCatalog:
    if not _GENERATED_TEMPLATE_ID.fullmatch(template_id):
        raise ValueError("Chỉ được xoá template tự sinh có ID dạng tm1, tm2, ...")
    if not catalog.contains(template_id):
        raise ValueError(f"Không có template '{template_id}' trong catalog domain '{catalog.domain_id}'.")
    updated = catalog.delete_layout_template(template_id)
    print(f"Đã xoá {template_id} khỏi domain '{catalog.domain_id}'.")
    return updated


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Quản lý template layout tự sinh của Lumi.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    delete = commands.add_parser("delete", help="Xoá một template tự sinh, ví dụ tm2.")
    delete.add_argument("domain_id", help="ID domain, ví dụ education.")
    delete.add_argument("template_id", help="ID template tự sinh, ví dụ tm2.")

    clear = commands.add_parser("clear-generated", help="Xoá toàn bộ template tự sinh tmN của một domain.")
    clear.add_argument("domain_id", help="ID domain, ví dụ education.")

    listing = commands.add_parser("list", help="Liệt kê template tự sinh tmN của một domain.")
    listing.add_argument("domain_id", help="ID domain, ví dụ education.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        catalog = _load_catalog(args.domain_id)
        if args.command == "list":
            template_ids = _generated_ids(catalog)
            print("\n".join(template_ids) if template_ids else "Không có template tự sinh.")
            return 0
        if args.command == "delete":
            _delete(catalog, args.template_id)
            return 0
        if args.command == "clear-generated":
            template_ids = _generated_ids(catalog)
            if not template_ids:
                print("Không có template tự sinh để xoá.")
                return 0
            for template_id in template_ids:
                catalog = _delete(catalog, template_id)
            print(f"Đã xoá {len(template_ids)} template tự sinh.")
            return 0
    except (TemplateCatalogError, ValueError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    raise AssertionError(f"Lệnh chưa xử lý: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
