"""Lossless batch recipe patching without a live Qt session."""

from copy import deepcopy

from cellquant.classify import ClassificationRecipe


def patch_recipe_from_marker_edits(canonical: dict, *, name, calibration_group, markers, expected_channel_names):
    """Mirror BatchCoexpressionPanel.recipe_from_table patch semantics."""

    base = deepcopy(canonical)
    prior_by_name = {
        m["name"]: m for m in (base.get("markers") or []) if isinstance(m, dict) and m.get("name")
    }
    patched = []
    for marker in markers:
        item = dict(marker)
        prior = prior_by_name.get(item["name"])
        if prior and prior.get("calibration") is not None:
            item["calibration"] = deepcopy(prior["calibration"])
        patched.append(item)
    base["name"] = name
    base["calibration_group"] = calibration_group
    base["markers"] = patched
    base["expected_channel_names"] = expected_channel_names
    if base.get("queries") is None:
        base.pop("queries", None)
    return ClassificationRecipe(base)


def test_import_apply_save_preserves_custom_query_calibration_and_region_policy():
    original = ClassificationRecipe(
        {
            "schema_version": 1,
            "name": "Custom",
            "calibration_group": "g1",
            "region_policy": "centroid",
            "expected_channel_names": ["C1", "C2"],
            "markers": [
                {
                    "name": "A",
                    "channel": 0,
                    "low": 1,
                    "positive_fraction": 0.5,
                    "calibration": {"review": {"status": "historical"}, "method": "manual"},
                },
                {"name": "B", "channel": 1, "low": 2, "positive_fraction": 0.4},
            ],
            "queries": [
                {
                    "name": "B among A",
                    "positive": ["B"],
                    "negative": [],
                    "denominator_positive": ["A"],
                },
                {
                    "name": "A negative",
                    "positive": [],
                    "negative": ["A"],
                    "denominator_positive": [],
                },
            ],
        }
    )
    fingerprint = original.fingerprint
    # Simulate Apply/Save with no table edits: same markers rebound from canonical.
    markers = [
        {
            "name": m["name"],
            "channel": m["channel"],
            "low": m["low"],
            "high": m.get("high"),
            "positive_fraction": m["positive_fraction"],
            "uncertainty_margin": m.get("uncertainty_margin", 0),
            "compartment": "nucleus",
        }
        for m in original.raw["markers"]
    ]
    rebuilt = patch_recipe_from_marker_edits(
        original.raw,
        name=original.raw["name"],
        calibration_group=original.raw["calibration_group"],
        markers=markers,
        expected_channel_names=original.raw["expected_channel_names"],
    )
    assert rebuilt.fingerprint == fingerprint
    assert rebuilt.raw["region_policy"] == "centroid"
    assert rebuilt.raw["queries"][0]["denominator_positive"] == ["A"]
    assert rebuilt.raw["markers"][0]["calibration"]["method"] == "manual"
