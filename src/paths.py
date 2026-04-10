from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def configure_frozen_environment() -> None:
    if not (getattr(sys, 'frozen', False) and sys.platform == 'darwin'):
        return

    base = Path(sys.executable).resolve().parent.parent / 'Resources'
    os.environ['GI_TYPELIB_PATH'] = str(base / 'lib' / 'girepository-1.0')
    os.environ['GSETTINGS_SCHEMA_DIR'] = str(base / 'share' / 'glib-2.0' / 'schemas')
    os.environ['GTK_PATH'] = str(base / 'lib')
    os.environ['DYLD_LIBRARY_PATH'] = str(base / 'lib')

    bundled_bin = str(base / 'bin')
    if bundled_bin not in os.environ.get('PATH', ''):
        os.environ['PATH'] = f"{bundled_bin}:{os.environ.get('PATH', '')}"


configure_frozen_environment()


def asset_path(name: str) -> Path:
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            base = Path(sys._MEIPASS)
            for candidate in [base / 'data' / name, base / name]:
                if candidate.exists():
                    return candidate

        res_base = Path(sys.executable).resolve().parent.parent / 'Resources'
        for candidate in [res_base / 'data' / name, res_base / name]:
            if candidate.exists():
                return candidate

        return res_base / 'data' / name

    module_dir = Path(__file__).resolve().parent
    source_root = module_dir.parent
    executable_base = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None
    candidates = [
        module_dir / 'data' / name,
        source_root / 'data' / name,
        Path(sys.prefix) / 'share' / 'smb-ab-creator' / 'data' / name,
        Path(sys.prefix) / 'share' / 'smb-ab-creator' / name,
        Path('/app/share/smb-ab-creator/data') / name,
        Path('/app/share/smb-ab-creator') / name,
    ]
    if executable_base is not None:
        candidates.extend([
            executable_base.parent / 'share' / 'smb-ab-creator' / 'data' / name,
            executable_base.parent / 'share' / 'smb-ab-creator' / name,
        ])
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def bundled_tool(name: str) -> str:
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
        candidates = [
            base / 'bin' / name,
            base / '_internal' / 'bin' / name,
            base.parent / 'Resources' / 'bin' / name,
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)

    found = shutil.which(name)
    return found or name
