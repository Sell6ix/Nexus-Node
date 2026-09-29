"""Cursor IDE project tools: open workspaces and scaffold folders."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from core.config import load_settings
from core.confirmation import require_confirmation
from core.registry import tool

TemplateType = Literal["python", "node", "empty"]


def _cursor_bin() -> str:
    return load_settings().cursor_cli_path


@tool
def open_project_in_cursor(project_path: str) -> dict[str, Any]:
    """Open a project folder in the Cursor IDE via the Cursor CLI.

    Args:
        project_path: Path to an existing project directory (or file) to open.

    Returns:
        A dict with ok status and the command that was attempted.
    """
    try:
        path = Path(project_path).expanduser().resolve()
        if not path.exists():
            return {"ok": False, "error": f"Path does not exist: {path}"}

        binary = _cursor_bin()
        if shutil.which(binary) is None and binary == "cursor":
            return {
                "ok": False,
                "error": (
                    "Cursor CLI not found on PATH. Install the `cursor` shell command "
                    "from Cursor, or set CURSOR_CLI_PATH."
                ),
                "project_path": str(path),
            }

        result = subprocess.run(
            [binary, str(path)],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
        if result.returncode != 0:
            return {
                "ok": False,
                "error": (result.stderr or result.stdout or "cursor open failed").strip(),
                "project_path": str(path),
            }
        return {
            "ok": True,
            "project_path": str(path),
            "command": [binary, str(path)],
            "output": (result.stdout or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "project_path": project_path}


@tool
@require_confirmation(
    "Create a new project directory structure on disk? This writes new files."
)
def create_project_structure(
    base_path: str,
    template_type: TemplateType = "python",
) -> dict[str, Any]:
    """Scaffold a minimal project directory from a built-in template.

    Dangerous: creates directories and files on disk. Requires confirmation.

    Args:
        base_path: Directory to create (must not already be a non-empty project).
        template_type: One of "python", "node", or "empty".

    Returns:
        A dict listing created paths or an error.
    """
    try:
        root = Path(base_path).expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            return {
                "ok": False,
                "error": f"Refusing to scaffold into non-empty directory: {root}",
            }

        root.mkdir(parents=True, exist_ok=True)
        created: list[str] = [str(root)]

        if template_type == "empty":
            gitkeep = root / ".gitkeep"
            gitkeep.write_text("", encoding="utf-8")
            created.append(str(gitkeep))
        elif template_type == "python":
            (root / "src").mkdir(exist_ok=True)
            created.append(str(root / "src"))
            init_py = root / "src" / "__init__.py"
            init_py.write_text('"""Project package."""\n', encoding="utf-8")
            created.append(str(init_py))
            main_py = root / "main.py"
            main_py.write_text(
                'def main() -> None:\n    print("Hello from scaffolded project")\n\n\n'
                'if __name__ == "__main__":\n    main()\n',
                encoding="utf-8",
            )
            created.append(str(main_py))
            req = root / "requirements.txt"
            req.write_text("# Add dependencies here\n", encoding="utf-8")
            created.append(str(req))
            readme = root / "README.md"
            readme.write_text(f"# {root.name}\n\nScaffolded Python project.\n", encoding="utf-8")
            created.append(str(readme))
        elif template_type == "node":
            package_json = root / "package.json"
            package_json.write_text(
                "{\n"
                f'  "name": "{root.name}",\n'
                '  "version": "0.1.0",\n'
                '  "private": true,\n'
                '  "main": "index.js",\n'
                '  "scripts": {\n'
                '    "start": "node index.js"\n'
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            created.append(str(package_json))
            index_js = root / "index.js"
            index_js.write_text('console.log("Hello from scaffolded project");\n', encoding="utf-8")
            created.append(str(index_js))
            readme = root / "README.md"
            readme.write_text(f"# {root.name}\n\nScaffolded Node project.\n", encoding="utf-8")
            created.append(str(readme))
        else:
            return {"ok": False, "error": f"Unknown template_type: {template_type}"}

        return {
            "ok": True,
            "base_path": str(root),
            "template_type": template_type,
            "created": created,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "base_path": base_path}
