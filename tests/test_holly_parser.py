import csv
import io
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from aoi_normalizer.csv_writer import build_artifacts
from aoi_normalizer.holly_parser import parse_holly_xml
from aoi_normalizer.models import DataError

FIXTURES = Path(__file__).parent / "fixtures"


def test_full_csv_contract_matches_golden_files(config):
    panels = parse_holly_xml((FIXTURES / "mixed.xml").read_bytes(), config)
    artifacts = build_artifacts(panels, config, "a" * 64)
    assert len(artifacts) == 4
    for artifact in artifacts:
        status = "ok" if artifact.path.stem.endswith("_OK") else "ng"
        assert artifact.content == (FIXTURES / f"expected_{status}.csv").read_bytes()
        assert artifact.path.parent.name == "2026-11-26"
        assert artifact.path.name.startswith("20261126123000_")


def test_all_ok_after_ng_does_not_reuse_previous_metadata(config, xml_factory):
    parse_holly_xml(xml_factory(status="FAIL"), config)
    result = parse_holly_xml(xml_factory(time="2026/11/27 01:02:03"), config)
    assert len(result[0].ok_records) == 1
    assert not result[0].ng_records
    artifacts = build_artifacts(result, config, "b" * 64)
    assert all(a.path.name.startswith("20261127010203_") for a in artifacts)
    assert len(artifacts[2].content.splitlines()) == 1  # NG header, no data


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "Invalid XML"),
        (b"<Result/>", "no Panel"),
        (b"<Result>", "Invalid XML"),
        (b'<Panel cModel="A" Carrier="C" Machine="AOI" fdate="bad"/>', "fdate"),
    ],
)
def test_invalid_xml(config, content, message):
    with pytest.raises(DataError, match=message):
        parse_holly_xml(content, config)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p.find("Board").set("Status", "PENDING"), "Status"),
        (lambda p: p.remove(p.find("Board")), "no Board"),
        (lambda p: p.find("Board").remove(p.find("Board/Component")), "no Component"),
        (
            lambda p: p.find("Board/Component").remove(p.find("Board/Component/CompImage")),
            "no images",
        ),
        (lambda p: p.find("Board/Component").set("AI_result", "UNKNOWN"), "AI_result"),
        (
            lambda p: p.find("Board/Component/CompImage/Image").set("PicPath1", "invalid.jpg"),
            "filename",
        ),
    ],
)
def test_invalid_business_data_is_not_silently_dropped(config, xml_factory, mutate, message):
    root = ET.fromstring(xml_factory(status="FAIL"))
    mutate(root.find("Panel"))
    with pytest.raises(DataError, match=message):
        parse_holly_xml(ET.tostring(root), config)


def test_multiple_panels_have_independent_files(config, xml_factory):
    root = ET.fromstring(xml_factory())
    root.append(ET.fromstring(xml_factory(model="MODEL_B", time="2026/11/27 00:00:00"))[0])
    panels = parse_holly_xml(ET.tostring(root), config)
    artifacts = build_artifacts(panels, config, "c" * 64)
    assert len(artifacts) == 8
    assert len({a.path for a in artifacts}) == 8
    assert panels[0].ok_records[0]["Product_Name"] == "MODEL_A"
    assert panels[1].ok_records[0]["Product_Name"] == "MODEL_B"
    assert {a.path.parent.name for a in artifacts} == {"2026-11-26", "2026-11-27"}


def test_default_namespace(config, xml_factory):
    content = xml_factory().replace(b"<Result>", b'<Result xmlns="urn:holly">')
    assert len(parse_holly_xml(content, config)[0].ok_records) == 1


def test_csv_quoting_and_windows_filename_sanitizing(config, xml_factory):
    product = '產品, "A"\nB/C:*?'
    panels = parse_holly_xml(xml_factory(model=product), config)
    artifact = build_artifacts(panels, config, "d" * 64)[0]
    rows = list(csv.DictReader(io.StringIO(artifact.content.decode("utf-8"))))
    assert rows[0]["Product_Name"] == product
    assert not any(char in artifact.path.name for char in '<>:"/\\|?*\n')


@pytest.mark.parametrize("ai, expected", [("NG", "0"), ("OK", "1"), ("0", "0"), ("1", "1")])
def test_ai_result_mapping(config, xml_factory, ai, expected):
    root = ET.fromstring(xml_factory(status="FAIL"))
    root.find("Panel/Board/Component").set("AI_result", ai)
    assert parse_holly_xml(ET.tostring(root), config)[0].ng_records[0]["AI_Result"] == expected


def test_one_empty_image_path_is_skipped(config, xml_factory):
    root = ET.fromstring(xml_factory(status="FAIL"))
    root.find("Panel/Board/Component/CompImage/Image").set("PicPath2", "")
    assert len(parse_holly_xml(ET.tostring(root), config)[0].ng_records) == 1
