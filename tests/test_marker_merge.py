"""Step 4 after markers exist: ticking or unticking a channel changes the markers without rebuilding the rest."""

from __future__ import annotations

from cellquant.quicksetup import RULE_MEAN, RULE_PERCENT, marker_recipe, merge_marker_recipe


def _recipe(rule: str = RULE_MEAN) -> dict:
    data = marker_recipe({"measurements": [], "classifications": [], "reports": []}, [(1, "A"), (2, "B")], rule, 40.0)
    return data


def _ids(data: dict, key: str) -> list[str]:
    return [item["id"] for item in data[key]]


def test_a_newly_ticked_channel_gets_a_marker_and_leaves_the_others_alone():
    data = _recipe()
    data["classifications"][0]["threshold"] = 123.0
    data["classifications"][0]["image_thresholds"] = {"img": 5.0}
    data["classifications"][0]["cell_calls"] = {"img": {"3": {"positive": True}}}
    merge = merge_marker_recipe(data, [(1, "A"), (2, "B"), (3, "C")], {1, 2, 3})
    assert merge.added == ["C"] and merge.removed == [] and merge.new_ids == ["c_pos"]
    assert not merge.loses_something
    kept = merge.data["classifications"][0]
    assert kept["threshold"] == 123.0 and kept["image_thresholds"] == {"img": 5.0} and kept["cell_calls"]
    assert _ids(merge.data, "classifications") == ["a_pos", "b_pos", "c_pos"]
    assert _ids(merge.data, "measurements") == ["a_mean", "b_mean", "c_mean"]
    # A result row for the new marker and for each pair with it; the old rows stay.
    rows = [(row["numerator"], row["denominator"]) for row in merge.data["reports"]]
    assert ("a_pos", "all_objects") in rows and ("a_pos AND b_pos", "all_objects") in rows
    assert ("c_pos", "all_objects") in rows and ("a_pos AND c_pos", "all_objects") in rows and ("b_pos AND c_pos", "all_objects") in rows


def test_an_unticked_channel_loses_its_marker_measurements_and_rows_but_not_other_rows():
    data = _recipe(RULE_PERCENT)
    data["reports"].append({"numerator": "b_pos", "denominator": "all_objects"})  # a duplicate quick row: dropped with B
    data["reports"].append({"numerator": "a_pos AND NOT b_pos", "denominator": "a_pos"})  # made by hand, uses B
    data["reports"].append({"numerator": "NOT a_pos", "denominator": "all_objects"})  # made by hand, uses only A
    data["classifications"][1]["cell_calls"] = {"img": {"1": {"positive": False}, "2": {"positive": True}}}
    merge = merge_marker_recipe(data, [(1, "A")], {1, 2})
    assert merge.removed == ["B"] and merge.added == []
    assert _ids(merge.data, "classifications") == ["a_pos"]
    assert _ids(merge.data, "measurements") == ["a_mean", "a_pct"]  # B's mean and percent measurements go
    numerators = [row["numerator"] for row in merge.data["reports"]]
    assert "NOT a_pos" in numerators and "a_pos" in numerators
    assert not any("b_pos" in text for text in numerators)
    assert merge.calls_lost == {"B": 2}
    assert merge.reports_lost == ["A+ and B- among A+"]  # only the row made by hand is worth a question
    assert merge.loses_something


def test_a_marker_on_a_channel_that_is_not_listed_is_never_touched():
    data = _recipe()
    merge = merge_marker_recipe(data, [(2, "B")], {2})  # channel 1 is not one of the boxes
    assert merge.removed == [] and "a_pos" in _ids(merge.data, "classifications")


def test_changing_the_rule_restarts_only_when_asked_and_keeps_calls():
    data = _recipe()
    data["classifications"][0]["image_thresholds"] = {"img": 5.0, "img2": 6.0}
    data["classifications"][0]["cell_calls"] = {"img": {"3": {"positive": True}}}
    same = merge_marker_recipe(data, [(1, "A"), (2, "B")], {1, 2}, RULE_PERCENT, 40.0)  # rule not changed by the user
    assert not same.changes and _ids(same.data, "measurements") == _ids(data, "measurements")
    merge = merge_marker_recipe(data, [(1, "A"), (2, "B")], {1, 2}, RULE_PERCENT, 40.0, reset_rule=True)
    assert merge.reset == ["A", "B"] and merge.own_cutoffs_lost == {"A": 2} and merge.loses_something
    first = merge.data["classifications"][0]
    assert first["id"] == "a_pos" and first["measurement"] == "a_pct" and first["comparison"] == "at_least"
    assert first["threshold"] == 40.0 and "image_thresholds" not in first and first["cell_calls"]
    assert merge.new_ids == ["a_pos", "b_pos"]
    assert set(_ids(merge.data, "measurements")) == {"a_mean", "a_pct", "b_mean", "b_pct"}
    back = merge_marker_recipe(merge.data, [(1, "A"), (2, "B")], {1, 2}, RULE_MEAN, 40.0, reset_rule=True)
    assert set(_ids(back.data, "measurements")) == {"a_mean", "b_mean"}  # the percent measurements go
    assert back.data["classifications"][0]["measurement"] == "a_mean"


def test_new_marker_names_do_not_clash_with_existing_ids():
    data = _recipe()
    merge = merge_marker_recipe(data, [(1, "A"), (2, "B"), (3, "A")], {1, 2, 3})
    assert merge.data["classifications"][-1]["id"] == "a_2_pos"
    assert len(set(_ids(merge.data, "measurements"))) == len(merge.data["measurements"])
