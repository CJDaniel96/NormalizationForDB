"""Parse Holly XML without filesystem side effects or cross-file state."""

from datetime import datetime
from pathlib import PureWindowsPath
from xml.etree import ElementTree as ET

from .config import Config
from .models import DataError, PanelResult, Record

TIME_FORMAT = "%Y/%m/%d %H:%M:%S"
AI_RESULTS = {"NG": "0", "OK": "1", "0": "0", "1": "1"}


def _required(element: ET.Element, name: str) -> str:
    value = element.get(name)
    if value is None or not value.strip():
        raise DataError(f"<{element.tag}> is missing {name}")
    return value


def _image_path(raw: str, carrier: str, side: str) -> tuple[str, str]:
    # PureWindowsPath handles both slash styles, including UNC paths, on every OS.
    filename = PureWindowsPath(raw).name
    timestamp = filename.split("_", 1)[0]
    _, separator, image_name = filename.partition("@")
    try:
        if len(timestamp) != 14 or not timestamp.isascii() or not timestamp.isdigit():
            raise ValueError("expected 14-digit image timestamp")
        captured = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    except ValueError as error:
        raise DataError(f"Invalid image filename: {filename}") from error
    if not separator or not image_name:
        raise DataError(f"Image filename must contain @ and an image name: {filename}")
    relative = f"{captured:%Y-%m-%d}/{timestamp}/{timestamp}_{carrier}_{side}@{image_name}"
    return relative, image_name


def _ng_records(board: ET.Element, common: Record) -> list[Record]:
    components = board.findall("Component")
    if not components:
        raise DataError("FAIL board has no Component elements")
    result = []
    for component in components:
        ai_result = _required(component, "AI_result")
        if ai_result not in AI_RESULTS:
            raise DataError(f"Unknown AI_result: {ai_result!r}")
        component_record = common | {
            "Part_Number": _required(component, "PartNo"),
            "CompName": _required(component, "CompName"),
            "Window_ID": -1,
            "AOI_Defect_Code": _required(component, "MachineDefect"),
            "OP_Defect_Code": "No Check",
            "AI_Result": AI_RESULTS[ai_result],
            "PackageType": _required(component, "PackageType"),
            "Comp_type": _required(component, "Type"),
            "Group_type": _required(component, "Group_type"),
            "is_Cover": _required(component, "is_covered"),
        }
        images = component.findall("CompImage/Image")
        if not images:
            raise DataError(f"Component {component_record['CompName']} has no images")
        for image in images:
            coordinates = {
                column: _required(image, attribute)
                for column, attribute in (
                    ("Defect_Center_X", "X1"),
                    ("Defect_Center_Y", "Y1"),
                    ("Defect_Region_X", "X2"),
                    ("Defect_Region_Y", "Y2"),
                    ("Angle", "Angle"),
                )
            }
            paths = [image.get(key, "") for key in ("PicPath1", "PicPath2")]
            paths = [path for path in paths if path.strip() and path.strip().lower() != "none"]
            if not paths:
                raise DataError("Image has neither PicPath1 nor PicPath2")
            for path in paths:
                relative, name = _image_path(
                    path, str(common["Carrier_SN"]), _required(component, "TB")
                )
                result.append(
                    component_record
                    | coordinates
                    | {
                        "Image_Path": relative,
                        "Image_name": name,
                    }
                )
    return result


def parse_holly_xml(content: bytes, config: Config) -> list[PanelResult]:
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, LookupError) as error:
        raise DataError(f"Invalid XML: {error}") from error
    # Accommodate exports with a default XML namespace.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    panels = list(root.iter("Panel"))
    if not panels:
        raise DataError("XML contains no Panel elements")
    results = []
    for index, panel in enumerate(panels, start=1):
        product = _required(panel, "cModel")
        carrier = _required(panel, "Carrier")
        machine = _required(panel, "Machine")
        try:
            timestamp = datetime.strptime(_required(panel, "fdate"), TIME_FORMAT)
        except ValueError as error:
            raise DataError(f"Invalid Panel fdate: {panel.get('fdate')!r}") from error
        boards = panel.findall("Board")
        if not boards:
            raise DataError(f"Panel {index} has no Board elements")
        ok_records, ng_records = [], []
        for board in boards:
            common = {
                "Product_Name": product,
                "Site": config.site,
                "Line_ID": config.line_id,
                "Factory": config.factory,
                "Machine": machine,
                "Time": timestamp.strftime(TIME_FORMAT),
                "BT": _required(board, "TopBtm"),
                "Imulti_col": _required(board, "imulti"),
                "Imulti_row": -1,
                "Carrier_SN": carrier,
                "BoardSN": _required(board, "BoardSN"),
            }
            status = _required(board, "Status").upper()
            if status in config.runtime.ok_board_statuses:
                ok_records.append(common)
            elif status == "FAIL":
                common |= {
                    "StationId": _required(panel, "StationId"),
                    "CycleTime": _required(panel, "CycleTime"),
                    "TotalComp": _required(board, "TotalComp"),
                }
                ng_records.extend(_ng_records(board, common))
            else:
                raise DataError(f"Unknown Board Status: {status!r}")
        results.append(
            PanelResult(index, timestamp, product, carrier, machine, ok_records, ng_records)
        )
    return results
