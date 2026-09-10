from pathlib import Path

from cellquant.config import load_config
from cellquant.survey import default_template_config_path


def test_default_template_is_package_resource_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample_config.yaml").write_text("untrusted: cwd config")
    path = default_template_config_path()
    assert path != tmp_path / "sample_config.yaml"
    assert path.parent.name == "cellquant"
    assert load_config(path).raw["schema_version"] == 1


def test_source_template_matches_packaged_copy():
    root = Path(__file__).resolve().parents[1]
    assert default_template_config_path().read_bytes() == (root / "sample_config.yaml").read_bytes()
