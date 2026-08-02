# Ch 15 — NTFS 檔案系統 artifacts：$MFT/$UsnJrnl/$LogFile

> 目標：理解 NTFS 核心結構（$MFT record、attribute 種類、時間戳欄位）；能用 MFTECmd 解析 $MFT 和 $UsnJrnl($J)；能識別 timestomping 的特徵（$SI vs $FN 時間戳不一致）；知道 $LogFile 能補什麼、不能補什麼。
>
> 環境：Windows 10/11 NTFS volume；工具：MFTECmd（Eric Zimmermann tools）、analyzeMFT（Python）；artifact 路徑：`C:\$MFT`、`C:\$Extend\$UsnJrnl:$J`、`C:\$LogFile`（需要系統權限或鑑識 image 才能存取）。

## 為什麼檔案系統 artifact 是調查的骨幹

記憶體告訴你「現在」發生了什麼；檔案系統告訴你「曾經」發生了什麼。

攻擊者在目標機器上工作時，幾乎每一步都會觸碰 NTFS：

- 落地工具（dropper 寫 .exe 到 `C:\Windows\Temp\`）
- 橫向移動（複製 credential dump 工具到 share）
- 持久化（在 `C:\ProgramData\` 建立後門檔案）
- 清理痕跡（刪除工具、改時間戳）

這些操作在 NTFS 的 $MFT（主檔案表）、$UsnJrnl（更新序列日誌）、$LogFile（交易日誌）裡留下 artifact，即使檔案已被刪除，記錄仍然可能留存。

## NTFS 的核心結構

### Volume 的基本佈局

NTFS volume 一開始是一個 boot sector（$Boot），記錄 cluster size、MFT 的起始位置等基本參數。整個 volume 被切成 cluster（預設 4KB），所有 metadata 和使用者資料都以 cluster 為單位分配。

NTFS 的 metadata 都是用「特殊檔案」管理，系統保留 MFT 記錄 0–23，其中最重要的幾個：

| MFT Record # | 系統檔案 | 用途 |
|---|---|---|
| 0 | `$MFT` | Master File Table 自身 |
| 1 | `$MFTMirr` | MFT 前 4 條記錄的備份 |
| 2 | `$LogFile` | NTFS 交易日誌（Redo/Undo log） |
| 3 | `$Volume` | Volume 資訊（label、version） |
| 4 | `$AttrDef` | Attribute type 定義 |
| 5 | `.`（root） | 根目錄 |
| 6 | `$Bitmap` | Cluster 使用狀態 bitmap |
| 7 | `$Boot` | Boot sector 備份 |
| 8 | `$BadClus` | 壞 cluster 列表 |
| 9 | `$Secure` | 安全描述子（ACL）資料庫 |
| 11 | `$Extend` | 擴充 metadata 目錄，含 $UsnJrnl |
| 30 | `$Extend\$UsnJrnl` | 更新序列日誌 |

### $MFT（Master File Table）

$MFT 是 NTFS 的核心。每個檔案和目錄都對應一條 MFT record，記錄這個檔案的所有 metadata 和（如果夠小）資料本身。

**MFT record 大小固定為 1024 byte（1 KB）**，結構如下：

```
MFT Record（1024 bytes）
┌─────────────────────────────────────────────────────┐
│ FILE signature（4 bytes）：0x46 0x49 0x4C 0x45 = "FILE" │
│ UpdateSequenceArray offset（2 bytes）                │
│ UpdateSequenceArray size（2 bytes）                  │
│ $LogFile Sequence Number（LSN）（8 bytes）           │
│ Sequence Number（2 bytes）：record 被重用次數        │
│ Reference Count（2 bytes）                           │
│ First Attribute Offset（2 bytes）                    │
│ Flags（2 bytes）：0x01=in use, 0x02=directory        │
│ Used Size of MFT Entry（4 bytes）                    │
│ Allocated Size（4 bytes）                            │
│ File Reference to Base Record（8 bytes）             │
│ Next Attribute ID（2 bytes）                         │
│ (padding)                                            │
├─────────────────────────────────────────────────────┤
│ Attribute 1（變長）                                  │
│ Attribute 2（變長）                                  │
│ ...                                                  │
│ End Marker：0xFF 0xFF 0xFF 0xFF                      │
│ (未使用空間)                                         │
└─────────────────────────────────────────────────────┘
```

### Attribute 的種類

每個 attribute 有一個 type code，常見的：

| Type Code | 名稱 | 內容 |
|---|---|---|
| 0x10（16） | `$STANDARD_INFORMATION`（`$SI`） | 建立/修改/存取/MFT修改時間、flags、owner |
| 0x30（48） | `$FILE_NAME`（`$FN`） | 檔名、父目錄參考、同一套時間戳（另一份） |
| 0x40（64） | `$OBJECT_ID` | 每個檔案的 GUID |
| 0x50（80） | `$SECURITY_DESCRIPTOR` | ACL（通常 redirect 到 $Secure） |
| 0x60（96） | `$VOLUME_NAME` | Volume label（只在 $Volume 裡） |
| 0x80（128） | `$DATA` | 檔案實際資料 |
| 0x90（144） | `$INDEX_ROOT` | B-tree index root（目錄用） |
| 0xA0（160） | `$INDEX_ALLOCATION` | B-tree index 的 non-resident 部分 |
| 0xB0（176） | `$BITMAP` | Index 的 bitmap |

### Resident vs Non-resident Attribute

一個 attribute 如果資料夠小（通常小於 700 bytes），**直接存在 MFT record 裡**（resident）。資料太大就存在 MFT record 外面的 cluster，MFT record 只存一個 data run（cluster 的起始位址和長度列表），這叫 **non-resident**。

`$STANDARD_INFORMATION` 和 `$FILE_NAME` 永遠是 resident（因為它們很小）。`$DATA` 對小檔案是 resident，對大檔案是 non-resident。

這對鑑識的意義：即使磁碟空間被覆寫，MFT record 裡的 resident attribute 的內容（特別是時間戳）仍然可能保存完整，因為 MFT 區域是 NTFS 保留的，不會輕易被覆蓋。

## $STANDARD_INFORMATION vs $FILE_NAME 時間戳

這是最重要的 forensic detail，也是 timestomping 偵測的核心。

每個 NTFS 檔案有**兩份時間戳**，分別存在不同的 attribute 裡。

### 四個時間欄位（兩份 attribute 各有）

每份時間戳都有四個欄位（通常縮寫為 MACB）：

| 縮寫 | 全名 | 意義 |
|---|---|---|
| M | Modified time | 檔案 **$DATA** attribute 的最後修改時間 |
| A | Accessed time | 最後存取時間（現代 Windows 預設不更新，已不可靠） |
| C | Changed time（又稱 $MFT Modified）| MFT record 本身最後被修改的時間（任何 attribute 更動都觸發） |
| B | Birth time（Created） | 檔案建立時間 |

### $STANDARD_INFORMATION（$SI）的時間

存在 type 0x10 的 attribute 裡。**這是 Windows API（`SetFileTime`）可以修改的時間戳**。當你在命令列或 API 用 `SetFileTime` 改時間，改的是這份。

`timestomper.exe`、PowerShell 的 `(Get-Item file).LastWriteTime = ...`、Meterpreter 的 `timestomp` 全都改這份。

### $FILE_NAME（$FN）的時間

存在 type 0x30 的 attribute 裡，**Windows API 無法直接修改**。`$FN` 的時間戳只有在以下情況才被 OS 更新：

- 檔案名稱改變（rename）
- 檔案移到另一個目錄
- 父目錄的時間戳更新觸發的遞迴

平常的檔案讀寫、`SetFileTime`，`$FN` 的時間戳**不會被更新**。

### Timestomping 偵測：$SI vs $FN 不一致

攻擊者用工具改 $SI 的時間戳，但忘了（或沒辦法）改 $FN 的。結果：

```
$SI MACB 時間戳：
  Modified:  2010-01-01 00:00:00 UTC  ← 攻擊者故意改成很舊
  Accessed:  2010-01-01 00:00:00 UTC
  Changed:   2010-01-01 00:00:00 UTC
  Born:      2010-01-01 00:00:00 UTC

$FN MACB 時間戳：
  Modified:  2024-06-15 13:47:22 UTC  ← 真實的建立時間
  Accessed:  2024-06-15 13:47:22 UTC
  Changed:   2024-06-15 13:47:22 UTC
  Born:      2024-06-15 13:47:22 UTC
```

$SI 和 $FN 的 Born（建立時間）超過數秒的差異，是 timestomping 的最強指標。注意「超過數秒」——同一個操作建立的兩份時間戳有幾十毫秒的差異是正常的，但相差數年就是人為篡改。

更進一步：$FN 的 Born time（`2024-06-15 13:47:22`）是攻擊者在受害機器上真實放置檔案的時間，這個時間可以和 $UsnJrnl 記錄、Event Log 交叉驗證。

## $UsnJrnl（Update Sequence Number Journal）

$UsnJrnl 是 NTFS 的「檔案操作日誌」，記錄 volume 上所有的檔案建立、刪除、改名、屬性修改操作。完整路徑是 `C:\$Extend\$UsnJrnl`，資料儲存在它的 `$J` data stream 裡（ADS，Alternate Data Stream）。

### 為什麼它對鑑識很重要

即使攻擊者**刪除了檔案**，$UsnJrnl 裡可能還有這個檔案的操作記錄：

- 「檔案建立」記錄（`FILE_CREATE`）：你知道攻擊者曾經建立了某個檔案
- 「檔案刪除」記錄（`FILE_DELETE`）：你知道攻擊者用完後刪了
- 「重新命名」記錄（`RENAME_OLD_NAME` + `RENAME_NEW_NAME`）：攻擊者把工具改名試圖混淆

### $UsnJrnl 的結構

$J stream 是連續的 USN Record 序列，每條記錄的結構：

```
USN Record（V2，最常見）：
┌───────────────────────────────────────────────┐
│ RecordLength   （4 bytes）                     │
│ MajorVersion   （2 bytes）：通常 2             │
│ MinorVersion   （2 bytes）：通常 0             │
│ FileReferenceNumber （8 bytes）：MFT reference │
│ ParentFileReferenceNumber （8 bytes）          │
│ Usn （8 bytes）：Update Sequence Number        │
│ TimeStamp （8 bytes）：FILETIME format         │
│ Reason （4 bytes）：操作類型 bitmap            │
│ SourceInfo （4 bytes）                         │
│ SecurityId （4 bytes）                         │
│ FileAttributes （4 bytes）                     │
│ FileNameLength （2 bytes）                     │
│ FileNameOffset （2 bytes）                     │
│ FileName（變長，Unicode）                       │
└───────────────────────────────────────────────┘
```

Reason 欄位是 bitmap，常見的值：

| Reason Bit | 代表操作 |
|---|---|
| 0x00000001 | `DATA_OVERWRITE`：資料被覆寫 |
| 0x00000002 | `DATA_EXTEND`：資料延伸 |
| 0x00000100 | `FILE_CREATE`：檔案建立 |
| 0x00000200 | `FILE_DELETE`：檔案標記刪除 |
| 0x00001000 | `RENAME_OLD_NAME`：改名前的舊名 |
| 0x00002000 | `RENAME_NEW_NAME`：改名後的新名 |
| 0x00004000 | `INDEXABLE_CHANGE`：indexing 狀態改變 |
| 0x00008000 | `BASIC_INFO_CHANGE`：屬性改變（時間戳、flag） |
| 0x80000000 | `CLOSE`：file handle 關閉（通常配合其他 reason 出現） |

### $UsnJrnl 的保留期間和限制

$UsnJrnl 是一個環狀緩衝區，預設大小大約 32MB（也可以被管理員更改）。當空間用完，舊的記錄被新的覆蓋。一個繁忙的系統可能只保留幾天到幾週的記錄，空閒系統可能保留更長。

**攻擊者也可以清空 $UsnJrnl**：

```powershell
fsutil usn deletejournal /D C:
```

這個命令清掉整個 journal。如果你發現 $UsnJrnl 是空的，或者 $J stream 的資料量異常少，本身就是一個 anti-forensics 的指標。

## $LogFile（NTFS 交易日誌）

$LogFile 是 NTFS 的 redo/undo log，用於保證 NTFS 操作的原子性（crash recovery）。當電源突然切斷，NTFS 在下次掛載時用 $LogFile 把未完成的 transaction rollback 或 replay。

### 對鑑識的用途和限制

$LogFile 記錄的是 NTFS metadata 操作（MFT record 的修改、directory index 的更新），而不是檔案內容的變化。對鑑識師來說，$LogFile 可以：

- 提供 MFT record 在過去某個時間點的快照（undo log 裡有修改前的狀態）
- 補全 $UsnJrnl 可能已覆蓋掉的舊記錄（$LogFile 循環更慢一些）

但 $LogFile 的格式複雜，不是設計給鑑識用的，解析需要專門工具（例如 LogFileParser）。實戰中，$UsnJrnl 是主要的操作日誌 artifact，$LogFile 是備用、更難啃的選項。

## 工具：MFTECmd 和 analyzeMFT

### MFTECmd（Eric Zimmermann）

MFTECmd 是目前解析 $MFT 和 $UsnJrnl 最實用的工具，輸出 CSV 格式，可以丟進 Timeline Explorer 或 Excel 做 timeline 分析。

**解析 $MFT：**

```powershell
MFTECmd.exe -f "E:\case\$MFT" --csv "E:\output" --csvf mft_output.csv
```

輸出的 CSV 每行是一個 MFT record，欄位包含：

- EntryNumber（MFT record 編號）
- SequenceNumber（重用次數）
- ParentEntryNumber
- InUse（是否還在使用，False = 已刪除但記錄留著）
- FileName
- Extension
- FileSize
- ReferenceCount
- **$SI Created/Modified/Accessed/MFT Modified**（$STANDARD_INFORMATION 的四個時間）
- **$FN Created/Modified/Accessed/MFT Modified**（$FILE_NAME 的四個時間）
- IsDirectory

**解析 $UsnJrnl($J)：**

```powershell
MFTECmd.exe -f "E:\case\$J" --csv "E:\output" --csvf usnjrnl_output.csv
```

輸出每條 USN Record，欄位包含 Timestamp、FileName、Reason、FileAttributes。

**從 live 系統複製 $MFT：**（$MFT 是系統鎖定的檔案，不能直接 copy）

```powershell
# 用 RawCopy 或 Velociraptor artifact 取得
# 或用 FTK Imager 的 "Add Evidence Item > Logical Drive" 取 \$MFT
```

最保險的做法是先做 disk image（E01 或 raw），再從 image 裡提取 $MFT，避免 live 系統的 $MFT 在你複製時被 OS 更新。

### analyzeMFT（Python）

老工具，輸出 CSV，但對 $FN 時間戳的處理有歷史版本問題。用 MFTECmd 為主，analyzeMFT 作為交叉驗證。

```bash
analyzeMFT.py -f $MFT -o output.csv
```

## 具體範例：調查工具落地

### 情境

你拿到一個 Windows 10 的 $MFT dump，懷疑攻擊者在 `2024-06-15 13:00–14:00 UTC` 把工具落地到 `C:\Windows\Temp\`，然後刪除，並做了 timestomping。

### Step 1：MFTECmd 解析 $MFT，篩選 Temp 目錄

```powershell
MFTECmd.exe -f "$MFT" --csv ".\output" --csvf mft.csv
# 開 Timeline Explorer 載入 mft.csv
# 篩選 FullPath 包含 "Windows\Temp"
# 篩選 $SI Created 在 2024-06-15 附近
```

找到幾個 InUse = False 的記錄（已刪除）：

| EntryNumber | FileName | $SI Born | $FN Born | $SI Modified | InUse |
|---|---|---|---|---|---|
| 125432 | svc_update.exe | 2010-01-01 | 2024-06-15 13:47:22 | 2010-01-01 | False |
| 125433 | cmd_output.txt | 2024-06-15 13:47:35 | 2024-06-15 13:47:35 | 2024-06-15 13:48:01 | False |

第一個檔案：$SI Born 是 2010-01-01，$FN Born 是 2024-06-15 13:47:22——典型 timestomping。攻擊者把 $SI 的時間改成很舊試圖隱藏，但沒動 $FN。

### Step 2：$UsnJrnl 確認操作序列

```powershell
MFTECmd.exe -f "$J" --csv ".\output" --csvf usnjrnl.csv
# 篩選 FileName = svc_update.exe
```

（示意輸出，實際依 image/版本而異）：

```
Timestamp（UTC）           UpdateReason              FileName
2024-06-15 13:47:22.100   FILE_CREATE               svc_update.exe
2024-06-15 13:47:22.120   DATA_EXTEND + CLOSE       svc_update.exe
2024-06-15 13:48:55.443   BASIC_INFO_CHANGE + CLOSE svc_update.exe  ← 時間戳被修改
2024-06-15 13:52:11.801   FILE_DELETE + CLOSE       svc_update.exe
```

這個序列還原了攻擊者的操作：13:47 建立檔案 → 13:48 改時間戳（BASIC_INFO_CHANGE）→ 13:52 刪除。$UsnJrnl 讓你重建了整個動作序列，即使檔案已刪除。

`BASIC_INFO_CHANGE` 在 13:48:55 出現是關鍵——這個 reason 代表 $STANDARD_INFORMATION attribute 被更新，也就是 timestomping 發生的時刻。

### Step 3：MFT record 的 $SI Changed（MFT Modified）時間

注意 $STANDARD_INFORMATION 自己的 C time（MFT Modified）：當攻擊者用 timestomper 修改 $SI 的 M/A/C/B 時，$SI 的 C time（MFT record 最後修改時間）**會被更新為修改的當下**。

所以即使 $SI 的 Born 被改成 2010，$SI 的 C time 仍然是 `2024-06-15 13:48:55`。這就是另一個 timestomping 的偵測角度：$SI 的 C time 比其他時間欄位新，說明這個 attribute 最近被修改過。

### 邊界情況：MFT record 編號被重用

MFT record 在檔案刪除後可能被 OS 重新分配給新檔案。重用後，舊的時間戳資料可能被覆寫，你就看不到舊檔案的完整記錄了。Sequence Number 欄位記錄這個 record 被重用了幾次，如果 Sequence Number 和你 $UsnJrnl 記錄裡的 FileReferenceNumber 的高 16 bits 不一致，代表這個 MFT record 已被重用，你看到的時間戳屬於新檔案而不是舊的。

## $MFT record 結構圖

```
Offset  Size  Field
------  ----  -----
0x00    4     Signature ("FILE")
0x04    2     UpdateSequenceArrayOffset
0x06    2     UpdateSequenceArraySize
0x08    8     $LogFile Sequence Number (LSN)
0x10    2     Sequence Number (重用計數)
0x12    2     Reference Count (目錄 link 數)
0x14    2     First Attribute Offset (通常 0x38 或 0x30)
0x16    2     Flags (0x01=in-use, 0x02=directory)
0x18    4     Used Size of MFT Entry
0x1C    4     Allocated Size of MFT Entry
0x20    8     File Reference to Base Record (extension record 用)
0x28    2     Next Attribute ID
0x2A    2     (align padding)
0x2C    4     MFT Record Number (Win XP 後才有)
              ↓
[First Attribute starts here]

$STANDARD_INFORMATION (0x10)：
  Offset  Size  Field
  0x00    8     Created Time (FILETIME)
  0x08    8     Modified Time
  0x10    8     MFT Modified Time (C time)
  0x18    8     Accessed Time
  0x20    4     File Attributes (RO/Hidden/System/Archive...)
  0x24    4     Maximum Versions
  0x28    4     Version Number
  0x2C    4     Class ID
  0x30    4     Owner ID (NTFS 3.1+)
  0x34    4     Security ID
  0x38    8     Quota Charged
  0x40    8     Update Sequence Number (USN)

$FILE_NAME (0x30)：
  Offset  Size  Field
  0x00    8     Parent Directory Reference (MFT ref)
  0x08    8     Created Time
  0x10    8     Modified Time
  0x18    8     MFT Modified Time
  0x20    8     Accessed Time
  0x28    8     Allocated Size
  0x30    8     Real Size
  0x38    4     File Attributes
  0x3C    4     Extended Data / Reparse Point
  0x40    1     File Name Length (in characters)
  0x41    1     File Name Namespace (0=POSIX, 1=Win32, 2=DOS, 3=Win32&DOS)
  0x42    2n    File Name (Unicode)
```

## 工具對照表

| 工具 | 支援的 artifact | 輸出格式 | 優點 |
|---|---|---|---|
| **MFTECmd** | $MFT, $UsnJrnl($J), $LogFile | CSV（可進 Timeline Explorer） | 同時輸出 $SI 和 $FN 時間，timestomping 一目了然 |
| **analyzeMFT** | $MFT | CSV | Python，可改 source，老牌 |
| **NTFS Log Tracker** | $LogFile, $UsnJrnl | CSV, XML | LogFile 解析最完整 |
| **FTK Imager** | 整個 volume（含 $MFT/$UsnJrnl） | E01/raw，並可瀏覽 | 現場採證、取 system files |
| **Velociraptor** | $MFT, $UsnJrnl, Prefetch, ... | JSON → 中央收集 | 遠端批次採集，大規模 IR |
| **Autopsy（with Sleuth Kit）** | E01/raw image | GUI，自動解析 MFT | 整合型鑑識平台，適合完整 case |

## 踩雷清單

1. **$SI 和 $FN 時間戳差幾秒是正常的**：NTFS 在建立檔案時先建 $SI 再建 $FN，兩者時間差幾十毫秒很正常。真正的 timestomping 差距通常是幾個月到幾年。別把幾秒的差異當 IOC，你會被嗆爆。

2. **Accessed time（A time）在現代 Windows 預設不更新**：Windows Vista 之後預設把 NTFS 的 `NtfsDisableLastAccessUpdate` 設為 1，A time 欄位不再即時更新（只在某些情況下更新）。用 A time 判斷「最後存取時間」在現代 Windows 幾乎不可靠，不要當做主要依據。

3. **檔案複製會把 $SI Born time 帶過去，$FN Born time 是複製到新位置的時間**：用 `xcopy` 或 `robocopy /COPY:AT` 複製的檔案，$SI 的 Born time 保留原始，但 $FN 的 Born time 是複製操作的時間。這在 lateral movement（把工具複製到另一台機器的 share）的鑑識裡很容易搞混。

4. **$UsnJrnl 的保留時間有限，不要假設有完整歷史**：在高 I/O 的伺服器上，$UsnJrnl 可能只保留幾小時的記錄。攻擊者可能在 IR 趕到之前就被 journal 自然覆蓋了。這時候要往 Event Log 和其他 artifact 找補。

5. **直接從 live 系統 copy $MFT 拿到的可能是不一致的快照**：$MFT 被 OS 持續更新中，直接 `copy C:\$MFT` 可能拿到中間狀態。用 FTK Imager 的 "Add Evidence Item > Logical Drive" 或專門的 raw copy 工具（RawCopy、Velociraptor），才能取到一致的 snapshot。

## 進階延伸

- **ADS（Alternate Data Streams）**：NTFS 的每個檔案可以有多個 $DATA stream，`file.exe:hidden` 就是一個 ADS。攻擊者用 ADS 隱藏 payload（`notepad.exe > file.txt:payload.exe` 然後 execute `wscript file.txt:payload.exe`）。MFTECmd 的 `--all` 參數會列出 ADS。

- **$INDEX_ROOT 和 $INDEX_ALLOCATION（目錄 slack）**：目錄的 B-tree index 裡，被刪除的檔案名稱可能還在 index 的 slack 空間。Autopsy 的 "Deleted Files" 功能和 Sleuth Kit 的 `icat`/`fls` 可以從這裡復原部分已刪除檔案的名稱。

- **NTFS Sparse File 和 Compressed File**：`$DATA` attribute 可以標記為 sparse（有洞）或 compressed。某些攻擊者用 sparse 技術減少磁碟 footprint。`$MFT` 的 `FileAttributes` 欄位裡的 bit flags 可以識別。

- **Timeline Super-Timeline**：把 $MFT（$SI 和 $FN 兩套時間）、$UsnJrnl、Prefetch、AMCache、Registry 的時間戳全部合併成一條統一 timeline，用 Plaso（log2timeline）做，然後用 Timesketch 查詢。這是大型 IR 的標準做法。

## 本章重點整理

- NTFS 用 $MFT 管理所有檔案；每個 MFT record 1024 bytes，包含 attribute 列表
- 鑑識最重要的兩個 attribute：`$STANDARD_INFORMATION`（$SI，API 可改）和 `$FILE_NAME`（$FN，API 無法直接改）
- 每個 attribute 有四個時間戳（MACB）；$SI 和 $FN 各有一套，共 8 個時間欄位
- Timestomping 的特徵：$SI 和 $FN 的 Born time 差異超過合理範圍（幾秒以上）
- $UsnJrnl($J) 記錄所有檔案操作（建立/刪除/改名/屬性改）；即使檔案刪除後記錄仍可能存在
- `BASIC_INFO_CHANGE` reason 在 $UsnJrnl 裡代表 $SI 屬性被修改，是 timestomping 的記錄
- MFTECmd 是解析 $MFT 和 $UsnJrnl 的首選工具，輸出 CSV 同時包含 $SI 和 $FN 時間
- $LogFile 是 NTFS crash recovery log，對鑑識有補充用途但格式複雜，非主要 artifact
- Resident attribute 的資料直接存在 MFT record 裡，即使磁碟空間被覆寫仍可能保留

## 自我檢核

- [ ] 我能畫出 MFT record 的基本結構，並說出 $STANDARD_INFORMATION 和 $FILE_NAME 各存什麼
- [ ] 我能解釋為什麼攻擊者改不了 $FN 的時間戳，以及這對 timestomping 偵測的意義
- [ ] 我能說出 $UsnJrnl 的 Reason 欄位裡哪個 bit 代表 timestomping，並解釋為什麼
- [ ] 我知道 Accessed time 在現代 Windows 不可靠，不應該把它當主要依據
- [ ] 我能說出 MFTECmd 的基本用法，以及輸出 CSV 有哪些關鍵欄位
- [ ] 我能說出 $UsnJrnl 有哪些侷限（保留時間有限、可被清除）以及如何識別清除的跡象

## 延伸閱讀

1. **《The Art of Memory Forensics》** — 雖然以記憶體為主，但 Appendix 和 Ch 12 有 NTFS 結構的精煉說明，和本章互補。

2. **Brian Carrier, "File System Forensic Analysis"（Addison-Wesley, 2005）**：NTFS 結構的最詳盡技術參考，MFT record、attribute、$UsnJrnl 格式全都有。這本書比《Art of Memory Forensics》更深入 filesystem 細節，讀 Ch 11–13。

3. **SANS FOR500 — Windows Forensic Analysis**：FOR500 的 $MFT 和 timeline analysis 章節是業界標準，SANS Cheat Sheet "NTFS Forensics" 把 $SI/$FN 對比、timestomping 指標整理得非常清楚，貼在桌面旁邊用。

4. **MFTECmd GitHub / Eric Zimmermann 工具套件說明**（[https://ericzimmerman.github.io](https://ericzimmerman.github.io)）：EZ Tools 是 DFIR 的標準工具鏈，Timeline Explorer 搭配 MFTECmd CSV 輸出的用法，官方 GitHub 有 demo 影片和欄位說明。

5. **"Timestomping Detection via NTFS Timestamps" — SANS Reading Room**：多篇 SANS paper 討論 $SI vs $FN 差異的統計分析和偵測閾值，搜尋 "NTFS timestamp forensics timestomping" 可找到。關聯本章的 timestomping 偵測實作。

---

$MFT 和 $UsnJrnl 告訴你「什麼檔案在什麼時候被建立/刪除/改名」。但光知道有個 exe 出現過還不夠——它有沒有真的跑起來？下一章查執行痕跡：Prefetch、AMCache、ShimCache、SRUM，四個 artifact 各自回答「這個程式跑過嗎」的不同面向。

→ [Ch 16 執行痕跡：Prefetch/AMCache/ShimCache/SRUM](./16-windows-execution-artifacts.md)
