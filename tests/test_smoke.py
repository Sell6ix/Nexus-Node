"""Pytest loads the smoke suites plus a few extra unit checks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smoke_p0 import (  # noqa: E402, F401
    test_daily_isolation,
    test_device_add_parse,
    test_manage_process_confirm_flag,
    test_worker_confirmed_exec,
)
from smoke_p1 import (  # noqa: E402, F401
    test_home_device,
    test_parse_when,
    test_reminder_delivery,
    test_reminder_retries_if_undelivered,
)
from smoke_secrets import (  # noqa: E402, F401
    test_account_encrypt,
    test_secret_box,
    test_user_store_encrypt,
)
from core.tls import ensure_dev_cert
from tools.desktop_tools import _windows_toast_script, _xml_escape


def test_windows_toast_escapes_xml() -> None:
    script = _windows_toast_script('Hi <Nexus>', 'it\'s "ok" & fine')
    assert "&lt;" in script
    assert "&amp;" in script
    assert "ToastNotification" in script
    assert "<Nexus>" not in script
    assert _xml_escape("&") == "&amp;"


def test_dev_tls_cert(tmp_path: Path) -> None:
    cert, key = ensure_dev_cert(tmp_path)
    assert cert.is_file() and key.is_file()
    text = cert.read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in text
    # reuse existing files
    cert2, key2 = ensure_dev_cert(tmp_path)
    assert cert2 == cert and key2 == key
