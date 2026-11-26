"""CSV contracts and data returned by the parser."""

from dataclasses import dataclass
from datetime import datetime

NG_COLUMNS = (
    "Product_Name",
    "Site",
    "Line_ID",
    "StationId",
    "Factory",
    "Machine",
    "Time",
    "BT",
    "Imulti_col",
    "Imulti_row",
    "Carrier_SN",
    "BoardSN",
    "Image_Path",
    "Image_name",
    "Part_Number",
    "CompName",
    "Window_ID",
    "AOI_Defect_Code",
    "OP_Defect_Code",
    "AI_Result",
    "Defect_Center_X",
    "Defect_Center_Y",
    "Defect_Region_X",
    "Defect_Region_Y",
    "Angle",
    "CycleTime",
    "TotalComp",
    "PackageType",
    "Comp_type",
    "Group_type",
    "is_Cover",
)
OK_COLUMNS = (
    "Product_Name",
    "Site",
    "Line_ID",
    "Factory",
    "Machine",
    "Time",
    "BT",
    "Imulti_col",
    "Imulti_row",
    "Carrier_SN",
    "BoardSN",
)

type Record = dict[str, str | int]


class DataError(ValueError):
    """The XML does not satisfy the supported Holly data contract."""


@dataclass(frozen=True)
class PanelResult:
    index: int
    timestamp: datetime
    product_name: str
    carrier: str
    machine: str
    ok_records: list[Record]
    ng_records: list[Record]
