import json

import pytest

from aoi_normalizer.config import ConfigError, load_config


def change(path, edit):
    raw = json.loads(path.read_text())
    edit(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_paths_are_relative_to_config_not_working_directory(config_file, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert load_config(config_file).paths.input == config_file.parent / "data/input"


@pytest.mark.parametrize(
    "edit",
    [
        lambda raw: raw.update(unknown=True),
        lambda raw: raw.pop("site"),
        lambda raw: raw.update(machine_brand="unknown"),
        lambda raw: raw.update(schema_version=True),
        lambda raw: raw["paths"].update(output=raw["paths"]["input"]),
        lambda raw: raw["paths"].update(output="data"),
        lambda raw: raw["paths"].update(error=raw["paths"]["xml_archive"] + "/error"),
        lambda raw: raw["runtime"].update(batch_size=True),
        lambda raw: raw["runtime"].update(max_parse_attempts=0),
        lambda raw: raw["runtime"].update(settle_seconds=-1),
        lambda raw: raw["runtime"].update(poll_interval_seconds=0),
        lambda raw: raw["runtime"].update(retry_interval_seconds=float("nan")),
        lambda raw: raw["runtime"].update(ok_board_statuses=["FAIL"]),
        lambda raw: raw["runtime"].update(ok_board_statuses=[]),
        lambda raw: raw["runtime"].update(exclude_directory_names=["a/b"]),
        lambda raw: raw["runtime"].update(unknown=1),
    ],
)
def test_invalid_config_is_rejected(config_file, edit):
    change(config_file, edit)
    with pytest.raises(ConfigError):
        load_config(config_file)


def test_duplicate_json_keys_are_rejected(config_file):
    config_file.write_text('{"site":"A", "site":"B"}')
    with pytest.raises(ConfigError, match="Duplicate"):
        load_config(config_file)


def test_utf8_bom(config_file):
    config_file.write_bytes(b"\xef\xbb\xbf" + config_file.read_bytes())
    assert load_config(config_file).site == "ZJ"


def test_managed_subdirectories_of_input_are_allowed(config_file):
    change(config_file, lambda raw: raw["paths"].update(xml_archive="data/input/archive"))
    assert load_config(config_file).paths.xml_archive.name == "archive"
