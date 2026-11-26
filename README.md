# AOI XML Normalizer

將 Holly AOI XML 正規化為 OK／NG CSV。正式環境以 Windows + uv 執行；程式本身只使用 Python 標準函式庫，不依賴 pandas、tqdm、tendo 或 CoverType.json。

## Windows 一鍵啟動

1. 安裝 [uv](https://docs.astral.sh/uv/getting-started/installation/)，重新開啟命令提示字元，確認 `uv --version` 可執行。
2. 編輯 `config/zj_z36_holly.json`，確認 input、output、csv_backup、xml_archive、error 路徑。已由原 INI 搬入 ZJ／Z36 的設定，新增的封存目錄位於 input 外。
3. 雙擊 `start.bat`。批次檔會切換至專案目錄，透過 `uv sync --locked --no-dev` 建立或同步 `.venv`，接著持續監控 input。
4. 首次執行可能需要下載 Python 3.12 與建置工具；之後使用既有環境與鎖定版本。請將 `uv.lock` 一起部署。
5. 保持視窗開啟；按 Ctrl+C 停止。關閉視窗會停止監控。開機自啟或登出後執行，請另外以 Windows 工作排程器執行 `start.bat`，並選擇有資料目錄存取權限的帳號。

自訂設定檔也可傳入完整路徑：

```bat
start.bat "C:\AOI Config\line_02.json"
```

Windows 網路共用可使用 JSON 字串 `"//server/share/aoi/input"`。網路磁碟代號與共用資料夾需對執行帳號可見。啟動檔會處理含空白的專案／設定路徑，並啟用 Python UTF-8 模式。

## 執行與開發

```bash
uv sync --locked
uv run --locked aoi-normalizer --config config/local.example.json --check-config
uv run --locked aoi-normalizer --config config/local.example.json --once
uv run --locked aoi-normalizer --config config/local.example.json --watch
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

使用本機範例前，先自行建立 `data/input`，放入 XML。程式不會自動建立 input，避免掛載或路徑設定錯誤時寫入錯誤位置。Windows 正式設定無法直接在 macOS/Linux 使用，請用 `local.example.json`。

省略模式時預設 `--watch`。`--check-config` 只驗證 JSON 與路徑關係，不建立目錄，也不保證網路目錄目前可存取。`--once` 會等待一次穩定觀察期並掃描，不等待所有重試完成；結束碼 0 表示本次掃描無失敗、隔離或待處理項目，1 表示仍有問題或待處理項目，2 表示啟動／設定失敗。

## JSON 設定

相對路徑一律以 JSON 所在資料夾為基準。設定檔允許 UTF-8 BOM，不允許重複鍵、未知欄位或註解。`schema_version` 固定為 1，`machine_brand` 目前只支援 `holly`。

| 欄位 | 用途 |
| --- | --- |
| site / factory / line_id | CSV 的廠區、工廠與產線 |
| paths.input | 遞迴監控根目錄 |
| paths.output / csv_backup | 正式 CSV 與必要備份；兩者成功才封存 XML |
| paths.xml_archive / error | 成功封存／無效資料隔離根目錄 |
| paths.state | 本機持久化工作紀錄，請保留並使用固定位置 |
| paths.logs | 日誌，每份約 5 MB，最多保留 5 份輪替備份 |
| runtime.poll_interval_seconds | 一般掃描間隔，預設 10 秒 |
| runtime.settle_seconds | 大小與修改時間持續不變的觀察時間，預設 10 秒 |
| runtime.retry_interval_seconds | 解析或 I/O 失敗後的重試間隔，預設 30 秒 |
| runtime.max_parse_attempts | 同一路徑、同一內容解析失敗的隔離門檻，預設 3 次 |
| runtime.batch_size / batch_pause_seconds | 每輪最多嘗試處理數量／批次滿載後休息時間，預設 500／2 秒 |
| runtime.exclude_directory_names | 排除的子資料夾名稱，不分大小寫；預設排除舊版 `upload_files` |
| runtime.ok_board_statuses | 明確允許視為 OK 的 Board 狀態，預設 PASS、OK；不分大小寫 |
| runtime.strict_xml_attributes | XML 資料屬性嚴格驗證，預設 false；允許缺少／空白的一般屬性，CSV 對應欄位留空。設 true 恢復嚴格檢查 |

正式、備份、封存、錯誤、狀態及日誌目錄不可互相重疊，也不可等於或包含 input。它們可以位於 input 內，此時掃描會排除整個受管理子目錄；建議部署在 input 外。

## 監控與檔案生命週期

- 啟動時補掃現有 XML，執行中持續遞迴掃描；支援新增的多層子目錄、`.xml`／`.XML` 與中文路徑。
- 不依賴資料夾層數、名稱或 XML 檔案的建立日期。不跟隨子目錄符號連結、Windows junction 或 XML 符號連結。
- 檔案大小及最後修改時間維持不變，才讀取快照。讀取前後及封存前再次驗證來源；若變動就重新等待。穩定時間只是啟發式判斷，上游最佳交付方式為先寫 `.tmp`，關閉檔案後改名為 `.xml`，或約定完成標記。此版不讀取完成標記。
- 有效 XML → 寫出全部 Panel 的正式與備份 CSV → 封存 XML。FAIL 板缺少元件或圖片會視為資料異常，不會默默產生空 NG 資料。
- 解析失敗 → 原處保留，依設定重試；同一內容達門檻 → 移至 error 並將原因寫入日誌與工作紀錄。來源內容變更會建立新的工作紀錄，重新計算次數。
- 網路中斷、權限、寫入／封存失敗或既有輸出內容衝突 → 保留來源，持續重試，不因 I/O 故障將 XML 判定為壞資料。input 暫時消失或子目錄無法掃描也會記錄並重試。
- 封存及錯誤目錄保留相對於 input 的結構。例如 `input/a/b/result.xml` 成功後為 `xml_archive/a/b/result.xml`。同一路徑已有相同內容會復用；內容不同時，檔名追加完整 SHA-256，保留兩份。
- 不刪除來源的其他檔案或空資料夾，不自動清理 XML 封存、工作紀錄或歷史資料夾。若目錄大量累積，應依實際掃描耗時規劃保留期限。
- 同一 Windows 帳號下，同一 input 根目錄僅允許一個處理程序；不同但互相包含的 input 根目錄不可同時部署。鎖由作業系統在程序結束後釋放，不必刪除鎖檔。

CSV 在各目的目錄先寫暫存檔再改為正式檔名。每份工作以來源相對路徑、內容雜湊與輸出相關設定識別；每份正式／備份 CSV 成功後就持久化進度，重啟時跳過已記錄的輸出，再接續封存。請勿清除 `.runtime` 來當作一般重試方法。

多個 CSV、工作紀錄與不同磁碟之間沒有共同交易。若程序在 CSV 發布後、進度寫入前崩潰，下次會先比對已存在內容；若下游已立即取走 CSV，仍可能重新發布。因此下游需利用檔名中的工作識別碼做去重，不能將這個工具視為跨系統 exactly-once 保證。輸出目錄已有不同內容時會停止該檔案處理並重試，日誌會指出衝突路徑，請先調查原因。

## CSV 相容性與刻意修正

- OK 保留舊版 11 欄，NG 保留舊版 31 欄與原始欄名、順序；UTF-8 無 BOM、無索引、LF 換行。空結果仍有標題列。標準 `csv` 模組會正確引用逗號、雙引號與換行。
- 每個 Panel 分別輸出 OK／NG 檔，每份都有正式與備份。輸出資料夾及檔名的時間來自該 Panel 的 `fdate`，修復全 OK 檔案沒有圖片時間與沿用上一份檔案狀態的問題。
- 檔名格式為 `時間_產品_廠區_工廠_產線_設備_Carrier_工作識別碼_p001_OK.csv`（或 NG）。識別欄位會處理 Windows 非法字元並限制長度；不同來源資料夾中的同名 XML 也有不同工作識別碼。
- 圖片路徑接受 `/`、`\\`、磁碟機及 UNC 格式；從圖片檔名的 14 位時間與 `@` 後名稱建立 `日期/時間/時間_Carrier_TB@圖片名`。不再假設日期位於來源目錄的固定層級。
- 預設 `runtime.strict_xml_attributes: false`，一般資料屬性（包含 Component 的 PackageType、Type、PartNo、CompName、MachineDefect、Group_type、is_covered、AI_result、TB，Panel／Board 的識別資料，以及圖片座標／角度）缺少、空字串或純空白時，CSV 對應欄位留空。資料值不會自動補成 0 或字串 None。
- AI 結果仍轉換 NG→0、OK→1 並保留既有 0／1；寬鬆模式保留未知值原文。設 `runtime.strict_xml_attributes: true` 則上述屬性必須非空，未知 AI 結果會重試後隔離。
- 兩種模式都要求有效的 XML 結構、Panel／Board 節點、可解析的 Panel.fdate 與已知 Board.Status；FAIL 板必須有元件及圖片，每個 Image 至少有一個可解析的 PicPath1／PicPath2。缺少分類或路徑所需資料仍會重試後隔離，避免誤判為 OK 或悄悄漏掉 NG。XML 支援有／無預設命名空間。
- FAIL 板仍輸出其所有元件影像，不另依 Component.Status 篩選；`OP_Defect_Code` 仍為 `No Check`，不自行改用 ConfirmDefect。相同 PicPath1／PicPath2 仍保留兩列。這些規則延續原程式，需由業務規格決定是否修改。
- 移除未使用的 CoverType 映射、INI 載入、無效函式及不用的 XML 欄位讀取。正式上線前請以真實 XML 確認狀態值、必填欄位及下游對新檔名的接受程度。

修改 JSON 後需重新啟動程式。驗證模式會納入工作識別，切換模式後可重新處理先前失敗的相同內容；若 XML 已移至 error，請在停止程式後將受影響的 XML 放回 input 的原相對路徑再啟動。保留 `.runtime` 工作紀錄即可，不需清除狀態目錄。

## 程式結構

`config.py` 管理設定驗證；`holly_parser.py` 純解析；`models.py` 定義 CSV 合約；`csv_writer.py` 建立輸出；`storage.py` 管理暫存檔、封存與工作紀錄；`pipeline.py` 控制監控與重試；`file_lock.py` 提供單一執行個體限制；`cli.py` 提供入口與輪替日誌。
