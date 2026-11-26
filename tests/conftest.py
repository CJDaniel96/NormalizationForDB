import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from aoi_normalizer.config import load_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def config_file(tmp_path):
    raw = {
        "schema_version": 1,
        "machine_brand": "holly",
        "site": "ZJ",
        "factory": "ZJ",
        "line_id": "Z36",
        "paths": {
            name: f"data/{name}"
            for name in ("input", "output", "csv_backup", "xml_archive", "error", "state", "logs")
        },
        "runtime": {"settle_seconds": 0, "retry_interval_seconds": 2},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture
def config(config_file):
    config = load_config(config_file)
    config.paths.input.mkdir(parents=True)
    return config


@pytest.fixture
def xml_factory():
    def make(*, status="PASS", model="MODEL_A", carrier="CARRIER_1", time="2026/11/26 12:30:00"):
        root = ET.fromstring((FIXTURES / "mixed.xml").read_bytes())
        panel = root.find("Panel")
        panel.set("cModel", model)
        panel.set("Carrier", carrier)
        panel.set("fdate", time)
        for board in list(panel.findall("Board")):
            if (status == "FAIL") != (board.get("Status") == "FAIL"):
                panel.remove(board)
            else:
                board.set("Status", status)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    return make


@pytest.fixture
def put_xml(config, xml_factory):
    def put(relative="result.xml", content=None):
        path = config.paths.input / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(xml_factory() if content is None else content)
        return path

    return put


@pytest.fixture
def clock():
    class Clock:
        value = 1000.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    return Clock()
