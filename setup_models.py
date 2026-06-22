"""One-time setup: download the offline Argos ru->en translation model.

Requires internet ONCE. After this completes, translation runs fully offline.
EasyOCR downloads its Russian detection/recognition models automatically on
first run, so this script only handles Argos.

    python setup_models.py
"""
from __future__ import annotations

import sys

# Windows consoles default to cp1252, which can't encode characters like the
# arrow in Argos package names. Force UTF-8 output so prints never crash.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    import argostranslate.package as pkg
    import argostranslate.translate as tr

    from_code, to_code = "ru", "en"

    langs = {l.code: l for l in tr.get_installed_languages()}
    if from_code in langs and to_code in langs and \
            langs[from_code].get_translation(langs[to_code]):
        print(f"[setup] {from_code}->{to_code} already installed. Nothing to do.")
        return 0

    print("[setup] updating Argos package index...")
    pkg.update_package_index()
    available = pkg.get_available_packages()

    match = next(
        (p for p in available if p.from_code == from_code and p.to_code == to_code),
        None,
    )
    if match is None:
        print(f"[setup] ERROR: no {from_code}->{to_code} package available.")
        return 1

    print(f"[setup] downloading {from_code}->{to_code} package...")
    path = match.download()
    print("[setup] installing...")
    pkg.install_from_path(path)
    print("[setup] done. Translation now works offline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
