import json
import pytest
from backend.services.settings_store import (
    load_settings,
    save_settings,
    DEFAULT_CLOSURE_WINDOW_FUTURE_MIN,
    DEFAULT_CLOSURE_WINDOW_PAST_MIN,
    DEFAULT_TRAIN_MERGE_THRESHOLD_MIN,
)


def test_defaults_on_missing_file(tmp_path):
    """When settings.json is missing, load_settings returns defaults without raising."""
    settings = load_settings(tmp_path)
    assert settings["closure_window_future_min"] == DEFAULT_CLOSURE_WINDOW_FUTURE_MIN
    assert settings["closure_window_past_min"] == DEFAULT_CLOSURE_WINDOW_PAST_MIN
    assert settings["train_merge_threshold_min"] == DEFAULT_TRAIN_MERGE_THRESHOLD_MIN
    assert "updated_at" in settings


def test_round_trip_save_and_load(tmp_path):
    """save_settings writes valid values and load_settings reads them back."""
    saved = save_settings(
        tmp_path,
        closure_window_future_min=5,
        closure_window_past_min=7,
        train_merge_threshold_min=12,
    )
    assert saved["closure_window_future_min"] == 5
    assert saved["closure_window_past_min"] == 7
    assert saved["train_merge_threshold_min"] == 12
    assert "updated_at" in saved

    loaded = load_settings(tmp_path)
    assert loaded["closure_window_future_min"] == 5
    assert loaded["closure_window_past_min"] == 7
    assert loaded["train_merge_threshold_min"] == 12
    assert loaded["updated_at"] == saved["updated_at"]


def test_defaults_on_corrupt_json(tmp_path):
    """When settings.json contains malformed JSON, load_settings falls back to defaults."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{corrupted_json: true, invalid", encoding="utf-8")

    loaded = load_settings(tmp_path)
    assert loaded["closure_window_future_min"] == DEFAULT_CLOSURE_WINDOW_FUTURE_MIN
    assert loaded["closure_window_past_min"] == DEFAULT_CLOSURE_WINDOW_PAST_MIN
    assert loaded["train_merge_threshold_min"] == DEFAULT_TRAIN_MERGE_THRESHOLD_MIN


def test_defaults_on_out_of_range_or_invalid_json_values(tmp_path):
    """When settings.json has out-of-range values or non-dict content, load_settings uses defaults."""
    settings_file = tmp_path / "settings.json"

    # Out of range closure_window_future_min (< 1 or > 15)
    settings_file.write_text(
        json.dumps({"closure_window_future_min": 20, "closure_window_past_min": 3, "train_merge_threshold_min": 10}),
        encoding="utf-8",
    )
    loaded = load_settings(tmp_path)
    assert loaded["closure_window_future_min"] == DEFAULT_CLOSURE_WINDOW_FUTURE_MIN

    # Out of range closure_window_past_min
    settings_file.write_text(
        json.dumps({"closure_window_future_min": 2, "closure_window_past_min": 0, "train_merge_threshold_min": 10}),
        encoding="utf-8",
    )
    loaded = load_settings(tmp_path)
    assert loaded["closure_window_past_min"] == DEFAULT_CLOSURE_WINDOW_PAST_MIN

    # Out of range train_merge_threshold_min (< 1 or > 20)
    settings_file.write_text(
        json.dumps({"closure_window_future_min": 2, "closure_window_past_min": 3, "train_merge_threshold_min": 25}),
        encoding="utf-8",
    )
    loaded = load_settings(tmp_path)
    assert loaded["train_merge_threshold_min"] == DEFAULT_TRAIN_MERGE_THRESHOLD_MIN

    # Non-dict JSON
    settings_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    loaded = load_settings(tmp_path)
    assert loaded["closure_window_future_min"] == DEFAULT_CLOSURE_WINDOW_FUTURE_MIN
    assert loaded["train_merge_threshold_min"] == DEFAULT_TRAIN_MERGE_THRESHOLD_MIN


@pytest.mark.parametrize("val", [0, 16, -1, 20, "five", None, True, False])
def test_save_settings_rejects_invalid_future_min(tmp_path, val):
    """Rejection of out-of-range, non-numeric, or boolean inputs for future min."""
    with pytest.raises(ValueError, match="closure_window_future_min must be between 1 and 15"):
        save_settings(tmp_path, closure_window_future_min=val, closure_window_past_min=3)


@pytest.mark.parametrize("val", [0, 16, -1, 20, "five", None, True, False])
def test_save_settings_rejects_invalid_past_min(tmp_path, val):
    """Rejection of out-of-range, non-numeric, or boolean inputs for past min."""
    with pytest.raises(ValueError, match="closure_window_past_min must be between 1 and 15"):
        save_settings(tmp_path, closure_window_future_min=2, closure_window_past_min=val)


@pytest.mark.parametrize("val", [0, 21, -1, 25, "ten", True, False])
def test_save_settings_rejects_invalid_merge_threshold(tmp_path, val):
    """Rejection of out-of-range (0, 21), non-numeric, or boolean inputs for merge threshold."""
    with pytest.raises(ValueError, match="train_merge_threshold_min must be between 1 and 20"):
        save_settings(tmp_path, closure_window_future_min=2, closure_window_past_min=3, train_merge_threshold_min=val)


def test_boundary_values_accepted(tmp_path):
    """Inclusive bounds 1 and 15 for window, and 1 and 20 for merge threshold are accepted."""
    saved_min = save_settings(
        tmp_path,
        closure_window_future_min=1,
        closure_window_past_min=1,
        train_merge_threshold_min=1,
    )
    assert saved_min["closure_window_future_min"] == 1
    assert saved_min["closure_window_past_min"] == 1
    assert saved_min["train_merge_threshold_min"] == 1

    saved_max = save_settings(
        tmp_path,
        closure_window_future_min=15,
        closure_window_past_min=15,
        train_merge_threshold_min=20,
    )
    assert saved_max["closure_window_future_min"] == 15
    assert saved_max["closure_window_past_min"] == 15
    assert saved_max["train_merge_threshold_min"] == 20


def test_independent_field_updates_do_not_reset_other_fields(tmp_path):
    """Updating one or two fields preserves previously saved values for other fields."""
    # 1. Save all three to custom values
    save_settings(
        tmp_path,
        closure_window_future_min=6,
        closure_window_past_min=8,
        train_merge_threshold_min=14,
    )

    # 2. Update only window values (omit merge threshold)
    updated_window = save_settings(
        tmp_path,
        closure_window_future_min=4,
        closure_window_past_min=5,
    )
    assert updated_window["closure_window_future_min"] == 4
    assert updated_window["closure_window_past_min"] == 5
    assert updated_window["train_merge_threshold_min"] == 14  # preserved!

    # 3. Update only merge threshold (omit window values)
    updated_merge = save_settings(
        tmp_path,
        train_merge_threshold_min=18,
    )
    assert updated_merge["closure_window_future_min"] == 4  # preserved!
    assert updated_merge["closure_window_past_min"] == 5   # preserved!
    assert updated_merge["train_merge_threshold_min"] == 18
