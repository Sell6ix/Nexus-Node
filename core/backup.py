"""Create zip backups of hub data (excluding live secrets key optional)."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path


SKIP_NAMES = {".DS_Store"}


def build_backup_zip(data_dir: Path, *, include_secrets_key: bool = False) -> bytes:
    root = Path(data_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if not root.is_dir():
            zf.writestr("README.txt", "Empty data dir\n")
        else:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if path.name in SKIP_NAMES:
                    continue
                if path.name == "secrets.key" and not include_secrets_key:
                    continue
                rel = path.relative_to(root).as_posix()
                zf.write(path, arcname=f"data/{rel}")
        zf.writestr(
            "BACKUP_INFO.txt",
            f"Nexus Node backup\ncreated_at={time.time()}\n",
        )
    return buf.getvalue()
