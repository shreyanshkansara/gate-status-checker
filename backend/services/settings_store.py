import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_CLOSURE_WINDOW_FUTURE_MIN = 7
DEFAULT_CLOSURE_WINDOW_PAST_MIN = 2
DEFAULT_TRAIN_MERGE_THRESHOLD_MIN = 10

MIN_CLOSURE_WINDOW_MIN = 1
MAX_CLOSURE_WINDOW_MIN = 15

MIN_MERGE_THRESHOLD_MIN = 1
MAX_MERGE_THRESHOLD_MIN = 20


def get_default_settings() -> Dict[str, Any]:
    """Returns default in-memory settings matching existing hardcoded behavior."""
    return {
        "closure_window_future_min": DEFAULT_CLOSURE_WINDOW_FUTURE_MIN,
        "closure_window_past_min": DEFAULT_CLOSURE_WINDOW_PAST_MIN,
        "train_merge_threshold_min": DEFAULT_TRAIN_MERGE_THRESHOLD_MIN,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_numeric_setting(
    field_name: str,
    value: Any,
    min_val: int = 1,
    max_val: int = 15,
) -> Union[int, float]:
    """
    Validates that a numeric setting value is an int or float between min_val and max_val inclusive.
    Explicitly rejects booleans, non-numeric values, and out-of-range numbers.
    Raises ValueError with a specific message if invalid.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be between {min_val} and {max_val}, got {value!r}")

    if value < min_val or value > max_val:
        val_display = int(value) if int(value) == value else value
        raise ValueError(f"{field_name} must be between {min_val} and {max_val}, got {val_display}")

    return int(value) if int(value) == value else value


def load_settings(data_dir: Optional[Union[Path, str]] = None) -> Dict[str, Any]:
    """
    Loads settings from settings.json inside data_dir.
    Falls back to default in-memory settings (never raises) if the file is missing,
    corrupt, or contains out-of-range or non-numeric values.
    """
    if data_dir is None:
        target_dir = Path(__file__).resolve().parent.parent / "data"
    else:
        target_dir = Path(data_dir)

    settings_file = target_dir / "settings.json"

    if not settings_file.exists():
        logger.info("Settings file %s not found. Using defaults.", settings_file)
        return get_default_settings()

    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Settings content must be a JSON object, got {type(data).__name__}")

        future = _validate_numeric_setting(
            "closure_window_future_min",
            data.get("closure_window_future_min"),
            MIN_CLOSURE_WINDOW_MIN,
            MAX_CLOSURE_WINDOW_MIN,
        )
        past = _validate_numeric_setting(
            "closure_window_past_min",
            data.get("closure_window_past_min"),
            MIN_CLOSURE_WINDOW_MIN,
            MAX_CLOSURE_WINDOW_MIN,
        )
        merge = _validate_numeric_setting(
            "train_merge_threshold_min",
            data.get("train_merge_threshold_min", DEFAULT_TRAIN_MERGE_THRESHOLD_MIN),
            MIN_MERGE_THRESHOLD_MIN,
            MAX_MERGE_THRESHOLD_MIN,
        )

        updated_at = data.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at.strip():
            updated_at = datetime.now(timezone.utc).isoformat()

        return {
            "closure_window_future_min": future,
            "closure_window_past_min": past,
            "train_merge_threshold_min": merge,
            "updated_at": updated_at,
        }
    except Exception as exc:
        logger.warning(
            "Error loading settings from %s (%s). Falling back to defaults.",
            settings_file,
            exc,
        )
        return get_default_settings()


_UNSET = object()


def save_settings(
    data_dir: Optional[Union[Path, str]],
    closure_window_future_min: Any = _UNSET,
    closure_window_past_min: Any = _UNSET,
    train_merge_threshold_min: Any = _UNSET,
) -> Dict[str, Any]:
    """
    Validates closure window and merge threshold values within their independent bounds.
    - closure_window_future_min: 1 to 15 inclusive
    - closure_window_past_min: 1 to 15 inclusive
    - train_merge_threshold_min: 1 to 20 inclusive
    If any field is omitted, preserves the current persisted value or default.
    Explicitly passed None, booleans, or out-of-range values raise ValueError.
    Saves atomically to settings.json in data_dir with updated_at timestamp.
    """
    current = load_settings(data_dir)

    if closure_window_future_min is _UNSET:
        closure_window_future_min = current.get("closure_window_future_min", DEFAULT_CLOSURE_WINDOW_FUTURE_MIN)
    if closure_window_past_min is _UNSET:
        closure_window_past_min = current.get("closure_window_past_min", DEFAULT_CLOSURE_WINDOW_PAST_MIN)
    if train_merge_threshold_min is _UNSET:
        train_merge_threshold_min = current.get("train_merge_threshold_min", DEFAULT_TRAIN_MERGE_THRESHOLD_MIN)

    future = _validate_numeric_setting(
        "closure_window_future_min",
        closure_window_future_min,
        MIN_CLOSURE_WINDOW_MIN,
        MAX_CLOSURE_WINDOW_MIN,
    )
    past = _validate_numeric_setting(
        "closure_window_past_min",
        closure_window_past_min,
        MIN_CLOSURE_WINDOW_MIN,
        MAX_CLOSURE_WINDOW_MIN,
    )
    merge = _validate_numeric_setting(
        "train_merge_threshold_min",
        train_merge_threshold_min,
        MIN_MERGE_THRESHOLD_MIN,
        MAX_MERGE_THRESHOLD_MIN,
    )

    if data_dir is None:
        target_dir = Path(__file__).resolve().parent.parent / "data"
    else:
        target_dir = Path(data_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    settings_file = target_dir / "settings.json"

    saved_data = {
        "closure_window_future_min": future,
        "closure_window_past_min": past,
        "train_merge_threshold_min": merge,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    temp_file = target_dir / "settings.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(saved_data, f, indent=2)

    temp_file.replace(settings_file)
    logger.info("Saved settings to %s: %s", settings_file, saved_data)
    return saved_data
