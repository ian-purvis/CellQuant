"""The output glossary: the in-app copy matches docs/, and the Help menu opens it."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_in_app_glossary_matches_docs():
    docs = (ROOT / "docs" / "OUTPUT_GLOSSARY.md").read_text(encoding="utf-8")
    bundled = (ROOT / "cellquant" / "gui" / "OUTPUT_GLOSSARY.md").read_text(encoding="utf-8")
    assert bundled == docs, "cellquant/gui/OUTPUT_GLOSSARY.md must be a copy of docs/OUTPUT_GLOSSARY.md"


def test_glossary_text_is_found():
    from cellquant.gui.guide import glossary_text

    assert glossary_text().startswith("# CellQuant output glossary")
