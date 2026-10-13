"""The install record for the Mac and Linux scripts (packaging/unix).

Same content as the Windows record (packaging/windows/cellquant_env.ps1): which
Cellpose engines are installed, and where. It is kept per computer, not in the
project folder, so a shared project folder works on every computer.

Usage (run with any Python 3):
  env_record.py path
  env_record.py installed            one "engine<TAB>prefix" line per engine whose environment exists
  env_record.py recorded             the same, whether or not the environment still exists
  env_record.py get KEY              default_engine, recommended_engine or parent
  env_record.py save ENGINE PREFIX CELLPOSE_VERSION GPU MODEL_WARNING DEFAULT RECOMMENDED PARENT PROJECT_ROOT
  env_record.py forget               delete the record (before a reinstall)
  env_record.py smoke                read smoke_test.py output on stdin; print "ok<TAB>summary" or "fail<TAB>error"
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE_ORDER = ("cellpose4", "cellpose3")


def record_path() -> Path:
    if sys.platform == "darwin":
        folder = Path.home() / "Library" / "Application Support" / "CellQuant"
    else:
        folder = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "CellQuant"
    return folder / "cellquant_env.json"


def read() -> dict:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def engines(data: dict, existing_only: bool) -> list[tuple[str, str]]:
    table = data.get("engines") or {}
    found = []
    for engine in ENGINE_ORDER:
        prefix = str((table.get(engine) or {}).get("prefix") or "")
        if prefix and (not existing_only or (Path(prefix) / "bin" / "python").exists()):
            found.append((engine, prefix))
    return found


def smoke_summary(text: str) -> str:
    lines = [line for line in text.splitlines() if line.startswith("CELLQUANT_SMOKE ")]
    if not lines:
        tail = "\n".join(text.strip().splitlines()[-15:])
        return f"fail\t{tail or 'The test analysis printed nothing.'}"
    try:
        report = json.loads(lines[-1][len("CELLQUANT_SMOKE ") :])
    except ValueError as exc:
        return f"fail\tUnreadable test report: {exc}"
    if not report.get("ok"):
        return f"fail\t{report.get('error') or 'The test analysis failed.'}"
    gpu = report.get("gpu") or {}
    parts = [f"Test passed: {report.get('test_analysis')}. napari {report.get('napari')}, Qt {report.get('qt')}, default model {report.get('default_model')}."]
    if gpu.get("available"):
        parts.append(f"GPU: {gpu.get('name')}.")
    elif gpu.get("nvidia_gpu"):
        parts.append(f"The NVIDIA GPU ({gpu.get('nvidia_gpu')}) cannot be used: {gpu.get('reason')}")
    threads = report.get("cpu_threads") or {}
    if threads.get("threads") and not threads.get("error"):
        parts.append(f"Cellpose will use {threads['threads']} CPU threads (fastest here).")
    return "ok\t" + " ".join(parts) + "\t" + str(gpu.get("name") or "") + "\t" + str(report.get("default_model") or "")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    data = read()
    if command == "path":
        print(record_path())
    elif command in ("installed", "recorded"):
        for engine, prefix in engines(data, existing_only=command == "installed"):
            print(f"{engine}\t{prefix}")
    elif command == "get" and len(argv) == 2:
        print(data.get(argv[1]) or "")
    elif command == "save" and len(argv) == 10:
        engine, prefix, version, gpu, warning, default, recommended, parent, root = argv[1:]
        table = data.get("engines") or {}
        table[engine] = {
            "prefix": prefix,
            "cellpose_version": version,
            "gpu": gpu or None,
            "model_warning": warning or None,
            "installed_utc": datetime.now(timezone.utc).isoformat(),
        }
        data.update(
            project_root=root,
            parent=parent,
            default_engine=default if default in table else next(iter(table)),
            recommended_engine=recommended,
            engines={key: table[key] for key in ENGINE_ORDER if key in table},
            updated_utc=datetime.now(timezone.utc).isoformat(),
        )
        path = record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    elif command == "forget":
        record_path().unlink(missing_ok=True)
    elif command == "smoke":
        print(smoke_summary(sys.stdin.read()))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
