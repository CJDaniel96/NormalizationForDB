from aoi_normalizer.cli import main


def test_check_config_has_no_filesystem_side_effects(config_file):
    assert main(["--config", str(config_file), "--check-config"]) == 0
    assert not (config_file.parent / "data").exists()


def test_once_writes_output_and_returns_success(config_file, config, put_xml):
    put_xml()
    assert main(["--config", str(config_file), "--once"]) == 0
    assert len(list(config.paths.output.rglob("*.csv"))) == 2


def test_once_reports_pending_invalid_data(config_file, config, put_xml):
    put_xml(content=b"<bad")
    assert main(["--config", str(config_file), "--once"]) == 1


def test_invalid_config_returns_startup_error(tmp_path):
    assert main(["--config", str(tmp_path / "missing.json"), "--once"]) == 2
