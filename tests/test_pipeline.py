import csv
import json
from dataclasses import replace
from xml.etree import ElementTree as ET

import pytest

from aoi_normalizer import pipeline as pipeline_module
from aoi_normalizer.models import DataError
from aoi_normalizer.pipeline import Pipeline


def make_pipeline(config, clock):
    return Pipeline(config, clock=clock, wall_clock=clock)


def journals(config):
    return [json.loads(path.read_text()) for path in (config.paths.state / "jobs").glob("*.json")]


def test_recursive_input_is_archived_only_after_all_outputs(config, clock, put_xml):
    path = put_xml("設備 A/日期/batch/deeper/result.XML")
    content = path.read_bytes()
    result = make_pipeline(config, clock).poll()
    assert result.completed == 1
    assert not path.exists()
    assert (config.paths.xml_archive / path.relative_to(config.paths.input)).read_bytes() == content
    output = sorted(config.paths.output.rglob("*.csv"))
    backup = sorted(config.paths.csv_backup.rglob("*.csv"))
    assert len(output) == len(backup) == 2
    assert [p.read_bytes() for p in output] == [p.read_bytes() for p in backup]
    assert journals(config)[0]["phase"] == "completed"


def test_stability_timer_restarts_when_source_changes(config, clock, put_xml, xml_factory):
    config = replace(config, runtime=replace(config.runtime, settle_seconds=10))
    pipeline = make_pipeline(config, clock)
    path = put_xml()
    assert pipeline.poll().pending == 1
    clock.advance(9)
    assert pipeline.poll().pending == 1
    path.write_bytes(xml_factory(model="CHANGED_MODEL"))
    clock.advance(1)
    assert pipeline.poll().pending == 1
    clock.advance(10)
    assert pipeline.poll().completed == 1


def test_new_subdirectories_are_discovered_after_start(config, clock, put_xml):
    pipeline = make_pipeline(config, clock)
    assert pipeline.poll().discovered == 0
    put_xml("new/newer/newest/result.xml")
    assert pipeline.poll().completed == 1


def test_same_filename_in_different_directories_does_not_collide(config, clock, put_xml):
    put_xml("a/same.xml")
    put_xml("b/same.xml")
    assert make_pipeline(config, clock).poll().completed == 2
    assert len(list(config.paths.output.rglob("*.csv"))) == 4
    assert len(list(config.paths.xml_archive.rglob("*.xml"))) == 2


def test_output_failure_preserves_xml_and_retry_progress_survives_restart(
    config, clock, put_xml, monkeypatch
):
    path = put_xml()
    original = pipeline_module.atomic_write

    def fail_backup(target, content):
        if target.is_relative_to(config.paths.csv_backup):
            raise PermissionError("backup disconnected")
        original(target, content)

    monkeypatch.setattr(pipeline_module, "atomic_write", fail_backup)
    assert make_pipeline(config, clock).poll().failed == 1
    assert path.exists()
    assert not config.paths.xml_archive.exists()
    assert not config.paths.error.exists()
    record = journals(config)[0]
    assert record["parse_attempts"] == 0
    assert len(record["published"]) == 1
    first_output = next(config.paths.output.rglob("*.csv"))
    first_output.unlink()  # Simulate a downstream consumer having taken this CSV.

    monkeypatch.setattr(pipeline_module, "atomic_write", original)
    restarted = make_pipeline(config, clock)
    assert restarted.poll().pending == 1
    clock.advance(3)
    assert restarted.poll().completed == 1
    assert not first_output.exists()  # Previously journaled publication is not repeated.
    assert len(list(config.paths.csv_backup.rglob("*.csv"))) == 2


def test_crash_between_csv_publication_and_journal_save_reuses_existing_csv(
    config, clock, put_xml, monkeypatch
):
    path = put_xml()
    pipeline = make_pipeline(config, clock)
    original = pipeline.store.save

    def interrupt_after_publication(record):
        if record["published"]:
            raise KeyboardInterrupt
        original(record)

    monkeypatch.setattr(pipeline.store, "save", interrupt_after_publication)
    with pytest.raises(KeyboardInterrupt):
        pipeline.poll()
    assert path.exists()
    first = next(config.paths.output.rglob("*.csv"))
    modified = first.stat().st_mtime_ns
    assert make_pipeline(config, clock).poll().completed == 1
    assert first.stat().st_mtime_ns == modified


def test_archive_failure_does_not_republish_csv_on_restart(config, clock, put_xml, monkeypatch):
    path = put_xml()
    original = pipeline_module.archive_xml
    monkeypatch.setattr(
        pipeline_module,
        "archive_xml",
        lambda *args: (_ for _ in ()).throw(OSError("archive unavailable")),
    )
    assert make_pipeline(config, clock).poll().failed == 1
    assert path.exists()
    assert journals(config)[0]["phase"] == "outputs_written"
    for csv_file in config.paths.output.rglob("*.csv"):
        csv_file.unlink()
    monkeypatch.setattr(pipeline_module, "archive_xml", original)
    clock.advance(3)
    assert make_pipeline(config, clock).poll().completed == 1
    assert not list(config.paths.output.rglob("*.csv"))


def test_invalid_xml_retries_across_restart_then_preserves_error_structure(config, clock, put_xml):
    path = put_xml("machine/batch/bad.xml", b"<unfinished")
    for attempt in range(1, 4):
        pipeline = make_pipeline(config, clock)
        result = pipeline.poll()
        assert journals(config)[0]["parse_attempts"] == attempt
        if attempt < 3:
            assert result.failed == 1 and path.exists()
            assert pipeline.poll().pending == 1
        else:
            assert result.quarantined == 1
        clock.advance(3)
    assert not path.exists()
    assert (config.paths.error / "machine/batch/bad.xml").read_bytes() == b"<unfinished"
    assert not config.paths.output.exists()


def test_source_content_change_resets_parse_attempts(config, clock, put_xml, xml_factory):
    path = put_xml(content=b"bad")
    pipeline = make_pipeline(config, clock)
    assert pipeline.poll().failed == 1
    path.write_bytes(xml_factory())
    assert pipeline.poll().completed == 1
    assert sorted(record["parse_attempts"] for record in journals(config)) == [0, 1]


def test_reappearing_completed_source_is_not_republished(config, clock, put_xml):
    put_xml()
    assert make_pipeline(config, clock).poll().completed == 1
    for path in config.paths.output.rglob("*.csv"):
        path.unlink()
    put_xml()
    assert make_pipeline(config, clock).poll().completed == 1
    assert not list(config.paths.output.rglob("*.csv"))


def test_archive_collision_keeps_both_versions(config, clock, put_xml, xml_factory):
    original = put_xml()
    first = original.read_bytes()
    assert make_pipeline(config, clock).poll().completed == 1
    put_xml(content=xml_factory(model="SECOND_VERSION"))
    assert make_pipeline(config, clock).poll().completed == 1
    archived = list(config.paths.xml_archive.glob("*.xml"))
    assert len(archived) == 2
    assert (config.paths.xml_archive / "result.xml").read_bytes() == first


def test_legacy_and_managed_subdirectories_are_not_scanned(config, clock, put_xml):
    config = replace(
        config, paths=replace(config.paths, xml_archive=config.paths.input / "archive")
    )
    put_xml("UPLOAD_FILES/old.xml")
    put_xml("archive/archived.xml")
    put_xml("new.xml")
    result = make_pipeline(config, clock).poll()
    assert result.discovered == result.completed == 1
    assert (config.paths.input / "UPLOAD_FILES/old.xml").exists()
    assert (config.paths.input / "archive/archived.xml").exists()


def test_unavailable_input_is_not_created_and_recovers(config, clock, put_xml):
    config.paths.input.rmdir()
    pipeline = make_pipeline(config, clock)
    assert pipeline.poll().scan_errors == 1
    assert not config.paths.input.exists()
    put_xml()
    assert pipeline.poll().completed == 1


def test_batch_limit_does_not_starve_later_files_after_error(config, clock, put_xml, monkeypatch):
    config = replace(config, runtime=replace(config.runtime, batch_size=1))
    put_xml("a.xml")
    put_xml("b.xml")
    original = pipeline_module.atomic_write

    def fail_first(target, content):
        if b"BOARD_OK" in content:
            # Fail only the first attempt, then allow the other source through.
            monkeypatch.setattr(pipeline_module, "atomic_write", original)
            raise OSError("temporary failure")
        original(target, content)

    monkeypatch.setattr(pipeline_module, "atomic_write", fail_first)
    pipeline = make_pipeline(config, clock)
    first = pipeline.poll()
    assert first.failed == 1 and first.batch_full
    second = pipeline.poll()
    assert second.completed == 1 and second.pending == 1


def test_corrupt_journal_never_moves_source(config, clock, put_xml):
    path = put_xml()
    pipeline = make_pipeline(config, clock)
    digest = pipeline_module.content_hash(path.read_bytes())
    job_id = pipeline._job_id(path.relative_to(config.paths.input), digest)
    target = config.paths.state / "jobs" / f"{job_id}.json"
    target.parent.mkdir(parents=True)
    target.write_text("{broken")
    assert pipeline.poll().failed == 1
    assert path.exists()
    assert not config.paths.output.exists()


def test_watch_keeps_polling_and_handles_new_arrivals(config, clock, put_xml, monkeypatch):
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            put_xml("arrived/after/start.xml")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(pipeline_module.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        make_pipeline(config, clock).watch()
    assert len(sleeps) == 2
    assert (config.paths.xml_archive / "arrived/after/start.xml").exists()


def test_once_waits_for_a_stable_snapshot(config, clock, put_xml, monkeypatch):
    config = replace(config, runtime=replace(config.runtime, settle_seconds=10))
    put_xml()
    monkeypatch.setattr(pipeline_module.time, "sleep", clock.advance)
    assert make_pipeline(config, clock).run_once().completed == 1


def test_inaccessible_subdirectory_does_not_stop_other_processing(
    config, clock, put_xml, monkeypatch
):
    path = put_xml()
    original = pipeline_module.os.walk

    def walk(root, *, followlinks, onerror):
        onerror(PermissionError("cannot access one child folder"))
        yield from original(root, followlinks=followlinks, onerror=onerror)

    monkeypatch.setattr(pipeline_module.os, "walk", walk)
    result = make_pipeline(config, clock).poll()
    assert result.scan_errors == 1
    assert result.completed == 1
    assert not path.exists()


def test_input_permission_error_is_retried(config, clock, monkeypatch):
    original = type(config.paths.input).is_dir

    def is_dir(path):
        if path == config.paths.input:
            raise PermissionError("network access denied")
        return original(path)

    monkeypatch.setattr(type(config.paths.input), "is_dir", is_dir)
    assert make_pipeline(config, clock).poll().scan_errors == 1


def test_relaxing_attributes_retries_previous_failure_and_archives_xml(
    config, clock, put_xml, xml_factory
):
    root = ET.fromstring(xml_factory(status="FAIL"))
    root.find("Panel/Board/Component").set("PackageType", "")
    content = ET.tostring(root)
    source = put_xml("machine/batch/empty_package.xml", content)
    strict_config = replace(config, runtime=replace(config.runtime, strict_xml_attributes=True))
    assert make_pipeline(strict_config, clock).poll().failed == 1
    assert source.exists()
    assert not config.paths.output.exists()

    # A validation-mode change is a different output contract, so the old parse
    # failure and retry deadline do not block the same XML under relaxed rules.
    assert make_pipeline(config, clock).poll().completed == 1
    assert not source.exists()
    assert (config.paths.xml_archive / "machine/batch/empty_package.xml").read_bytes() == content
    outputs = list(config.paths.output.rglob("*_NG.csv"))
    assert len(outputs) == 1
    with outputs[0].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert all(row["PackageType"] == "" for row in rows)


@pytest.mark.parametrize("previous_phase", ["quarantining", "quarantined"])
@pytest.mark.parametrize(
    "filename, expected_name",
    [
        ("20261126033959~T~_J1GF15_6_top.jpg", "J1GF15_6_top.jpg"),
        ("20261126015132~T~J101_J8_10_top.jpg", "J101_J8_10_top.jpg"),
    ],
)
def test_parser_fix_reprocesses_previously_rejected_xml(
    config, clock, put_xml, xml_factory, monkeypatch, previous_phase, filename, expected_name
):
    config = replace(config, runtime=replace(config.runtime, max_parse_attempts=1))
    root = ET.fromstring(xml_factory(status="FAIL"))
    image = root.find("Panel/Board/Component/CompImage/Image")
    image.set("PicPath1", filename)
    image.set("PicPath2", "")
    content = ET.tostring(root)
    relative = "machine/batch/tilde.xml"
    source = put_xml(relative, content)
    current_parser = pipeline_module.parse_holly_xml

    def old_parser(content, config):
        raise DataError(f"Invalid image filename: {filename}")

    with monkeypatch.context() as patch:
        patch.setattr(pipeline_module, "parse_holly_xml", old_parser)
        if previous_phase == "quarantining":

            def archive_unavailable(*args):
                raise OSError("error directory unavailable")

            patch.setattr(pipeline_module, "archive_xml", archive_unavailable)
            assert make_pipeline(config, clock).poll().failed == 1
        else:
            assert make_pipeline(config, clock).poll().quarantined == 1
            assert not source.exists()
            source.write_bytes((config.paths.error / relative).read_bytes())

    assert pipeline_module.parse_holly_xml is current_parser
    assert journals(config)[0]["phase"] == previous_phase
    clock.advance(3)
    assert make_pipeline(config, clock).poll().completed == 1
    assert not source.exists()
    assert (config.paths.xml_archive / relative).read_bytes() == content
    with next(config.paths.output.rglob("*_NG.csv")).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["Image_name"] == expected_name
    timestamp = filename[:14]
    assert rows[0]["Image_Path"] == (
        f"2026-11-26/{timestamp}/{timestamp}_CARRIER_1_T@{expected_name}"
    )
    assert journals(config)[0]["phase"] == "completed"


def test_returned_invalid_xml_is_quarantined_again(config, clock, put_xml):
    config = replace(config, runtime=replace(config.runtime, max_parse_attempts=1))
    source = put_xml(content=b"<broken")
    assert make_pipeline(config, clock).poll().quarantined == 1
    source.write_bytes(b"<broken")
    assert make_pipeline(config, clock).poll().quarantined == 1
    assert not source.exists()
    assert not config.paths.output.exists()
    assert journals(config)[0]["parse_attempts"] == 1
