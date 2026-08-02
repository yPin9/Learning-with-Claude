# Ch 16 — 執行痕跡：Prefetch/AMCache/ShimCache/SRUM

> 目標：能從 Prefetch、AMCache、ShimCache、SRUM 四個 artifact 提取「程式執行」的證據；理解每個 artifact 的底層機制、覆蓋範圍、時間語意和可靠度差異；能做「執行證據」的交叉驗證，避免各自 artifact 的陷阱。
>
> 環境：Windows 10/11；工具：PECmd（Prefetch）、AmcacheParser、AppCompatCacheParser（ShimCache），均來自 Eric Zimmermann 的 EZ Tools；SRUM 用 srum-dump 或 Velociraptor artifact。

## 為什麼「執行痕跡」是獨立的分析層

Ch 15 的 $MFT 和 $UsnJrnl 告訴你「這個 exe 出現過、被建立過、被刪除過」。但出現過不等於跑過。攻擊者可能把工具複製到機器上但還沒來得及執行就被抓到；或者反過來，工具執行完就立刻自刪，$MFT 裡只剩一個刪除記錄。

執行痕跡 artifact 問的問題是：**這個程式有沒有真正執行？什麼時間？執行了幾次？**

這四個 artifact 從不同角度回答這個問題：

| Artifact | 主要問題 | 時間精度 | 保留期 |
|---|---|---|---|
| **Prefetch** | 執行了嗎？幾次？最後/最初何時？ | 秒級 | 最多 128 個 .pf 檔 |
| **AMCache.hve** | 這個 PE 的 hash 是什麼？跑過嗎？ | 秒級 | 持久，除非手動清除 |
| **ShimCache（AppCompatCache）** | 執行過嗎？（語意有版本差異） | 秒級 | 最多 1024 條，重開機才寫入 |
| **SRUM（System Resource Usage Monitor）** | 這個 app 用了多少網路流量/CPU/記憶體？ | 小時級 | 約 30-60 天 |

## Prefetch

### 機制

Windows 的 Prefetch 是一個效能最佳化機制：當一個程式執行時，OS 記錄它在前 10 秒載入了哪些檔案和目錄，下次執行時預先讀進記憶體加速啟動。

副作用：Prefetch 留下了程式執行的證據。

Prefetch 檔案存放位置：`C:\Windows\Prefetch\`，副檔名 `.pf`，格式是：

```
<EXECUTABLE_NAME>-<HASH>.pf
```

Hash 是可執行檔路徑的 hash（不是 PE 的 content hash）。同一個 exe 名稱放在不同路徑，hash 不同，兩個 .pf 都存在。攻擊者把 `mimikatz.exe` 改名成 `svchost.exe` 再跑，Prefetch 也記，但你看到的是 `SVCHOST.EXE-<hash>.pf`，需要看裡面的 loaded files 才能識別。

### Prefetch 的前提條件

- **Prefetch 在 Windows Server 預設關閉**：Server 版 Windows 預設把 `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters\EnablePrefetcher` 設為 0。攻擊者在 Server 上的執行不留 Prefetch，這是已知盲點。
- **SSD 系統有時也關閉**：部分 SSD 配置會關閉 Prefetch 以減少寫入（雖然 Windows 10 以後這個設定已較少預設關閉）。

### .pf 檔案的結構

Prefetch 格式因 Windows 版本不同（XP/Vista/7/8/10/11 各有差異），主要版本：

| Windows 版本 | Format version |
|---|---|
| XP/2003 | Version 17 |
| Vista/7 | Version 23 |
| Win 8/8.1 | Version 26 |
| Win 10/11 | Version 30/31 |

Win 10+ 的 .pf 檔案是壓縮的（MAM 壓縮格式），PECmd 能自動解壓。

主要內容（PECmd 解析後）：

```
Executable Name:          MIMIKATZ.EXE
Hash:                     5E1B7EA2
File Size:                196718
Source File Size (from fs): 1271808
Run count:                4
Last run:                 2024-06-15 13:52:01 UTC+0
Previous run times:
  2024-06-15 13:48:55 UTC+0
  2024-06-15 13:30:12 UTC+0
  2024-06-14 22:17:33 UTC+0

Volume information:
  Volume name:     \VOLUME{...}
  Creation date:   2023-01-10 08:15:00 UTC+0
  Serial number:   ABCD-1234

Directories referenced:
  \VOLUME{...}\WINDOWS\SYSTEM32
  \VOLUME{...}\WINDOWS\TEMP

Files loaded:
  \VOLUME{...}\WINDOWS\SYSTEM32\NTDLL.DLL
  \VOLUME{...}\WINDOWS\SYSTEM32\KERNEL32.DLL
  \VOLUME{...}\WINDOWS\SYSTEM32\LSASS.EXE  ← 注意
  \VOLUME{...}\WINDOWS\TEMP\MIMIKATZ.EXE
```

（示意輸出，實際依 image/版本而異）

「Files loaded」列出的是執行前 10 秒內讀取的所有檔案，包含 DLL。這裡出現 `LSASS.EXE` 代表 mimikatz 讀取了 lsass 的 process memory（`ReadProcessMemory`），和 credential dumping 的動作符合。

### 用 PECmd 解析

```powershell
# 解析單個 .pf 檔
PECmd.exe -f "C:\Windows\Prefetch\MIMIKATZ.EXE-5E1B7EA2.pf"

# 解析整個 Prefetch 目錄，輸出 CSV
PECmd.exe -d "C:\Windows\Prefetch" --csv "E:\output" --csvf prefetch.csv

# 解析（包含時區轉換）
PECmd.exe -d "C:\Windows\Prefetch" --csv "E:\output" --csvf prefetch.csv -q --vss
```

輸出 CSV 的關鍵欄位：
- `ExecutableName`
- `Hash`
- `Size`
- `RunCount`
- `LastRun`
- `PreviousRun0`~`PreviousRun7`（最多保留最近 8 次的 run time）
- `FilesLoaded`（所有載入的檔案，逗號分隔）

### Prefetch 的侷限

- Win 10 的 Prefetch 最多保留 **128 個** .pf 檔。如果進程數超過 128，最舊的被覆蓋。
- Run count 上限是 99（某些版本）。攻擊者跑了 200 次，你看到的還是 99。
- 攻擊者可以**刪除 .pf 檔**。如果 Prefetch 目錄空了或者特定 .pf 不見了，本身就是 anti-forensics 指標（搭配 $UsnJrnl 可以確認 .pf 曾存在）。

## AMCache.hve

### 機制

AMCache（Application Compatibility Cache）儲存執行過的 PE 的 metadata，包含**SHA1 hash**。

位置：`C:\Windows\appcompat\Programs\Amcache.hve`（Windows 8+ 才有，Win 7 是舊版的 RecentFileCache.bcf 格式，已棄用）

AMCache 是一個 registry hive，用標準的 Windows registry 格式儲存。

### AMCache 的重要欄位

AMCache hive 裡的 `Root\InventoryApplicationFile\` 路徑下，每個 PE 有一個 key，key name 是一串 hash，裡面的 value 包含：

| Value Name | 內容 |
|---|---|
| `FileId` | SHA1 hash（前面有兩個 `0000`，去掉才是真正的 SHA1） |
| `ProductName` | PE 的 `VersionInfo` 裡的產品名稱 |
| `CompanyName` | 發行公司 |
| `FileDescription` | 描述 |
| `FileVersion` | 版本字串 |
| `LegalCopyright` | copyright 字串 |
| `LowerCaseLongPath` | 完整路徑（小寫） |
| `LinkDate` | PE 的 compile timestamp（`IMAGE_FILE_HEADER.TimeDateStamp`） |
| `FileSize` | 檔案大小 |
| `ProgramId` | 關聯的 InventoryApplication |

### 使用 AmcacheParser

```powershell
AmcacheParser.exe -f "E:\case\Amcache.hve" --csv "E:\output" --csvf amcache.csv
```

AmcacheParser 輸出多個 CSV（Application、ApplicationFile 等），重點看 `amcache_UnassociatedFileEntries.csv`（直接對應 executable 的記錄）。

輸出（示意輸出，實際依 image/版本而異）：

```
ApplicationName, ProgramId, FileKeyLastWriteTimestamp, SHA1, IsOsComponent, FullPath, Name, FileExtension, LinkDate, ProductName, Size, Language, Version, LongPathHash
svc_update.exe, abc123..., 2024-06-15 13:47:30, 0000a1b2c3d4e5f6..., False, c:\windows\temp\svc_update.exe, svc_update.exe, .exe, 2023-11-20, , 245760, , ,
```

SHA1 欄位值去掉前四個 `0` 就是真正的 SHA1，可以直接查 VirusTotal。即使 `svc_update.exe` 已被刪除，AMCache 記錄仍然在，給你 hash 做 IOC 比對。

### AMCache 的可靠度

AMCache 是「曾經在這台機器上存在過且被 OS 注意到」的最強證據。特別是 SHA1 hash——這是 content hash，檔案改名無法迴避。

**侷限：**
- AMCache 不一定記錄「執行時間」，`FileKeyLastWriteTimestamp` 是 AMCache key 最後寫入的時間，不完全等同首次執行時間。
- 某些 PE 被 OS 掃描（例如 Windows Defender）也可能觸發 AMCache 記錄，不一定是使用者執行了它。不過這個情況相對少見。
- Win 10/11 的 AMCache 格式和 Win 8 略有差異，確認你的 AmcacheParser 版本支援你的 OS。

## ShimCache（AppCompatCache）

### 機制

ShimCache（官方名稱 Application Compatibility Cache）是 Windows 應用程式相容性（App Compat）子系統的快取。OS 在幾個時機記錄程式的 metadata：

- 程式被執行時
- 程式被 OS 的相容性子系統掃描時（例如 explorer.exe 掃描某些目錄時可能觸發，但不一定執行）

位置：`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\AppCompatCache\AppCompatCache`（registry value）

### ShimCache 的時間語意問題

ShimCache 是 DFIR 裡**最常被誤解**的 artifact。它的時間欄位到底代表什麼，因 Windows 版本不同：

| Windows 版本 | ShimCache 時間欄位語意 |
|---|---|
| Windows XP | Last Modified time（$SI Modified）+ 執行 flag |
| Windows Vista/7 | Last Modified time + 執行 flag（InsertFlag）|
| Windows 8/8.1 | Last Modified time，**無執行 flag**，只能確認「存在過」 |
| Windows 10/11 | 同 8/8.1，無執行 flag，只能確認存在 |

Win 10/11 的 ShimCache **沒有 execution flag**。你只能確認「這個路徑上的 exe 曾被 OS 注意到（可能被執行，也可能只是被掃描過）」。**不能用 Win 10 的 ShimCache 作為「確認執行」的獨立證據。**

這是業界常見的誤區，很多報告把 ShimCache 記錄當成「確定執行過」寫進去，但在 Win 10 上這是不正確的。

### ShimCache 的另一個特點：重開機才寫入

ShimCache 的資料**只在重開機或登出時才寫入 registry**。分析 live 系統時，最近執行的程式可能還在記憶體裡的快取，沒有 flush 到 registry。如果你分析的是 memory image，ShimCache 記錄可能比 registry 裡的還新（Volatility 有 `windows.shimcachemem` plugin 可以從記憶體讀取）。

### 使用 AppCompatCacheParser

```powershell
# 從 live 系統的 registry 解析
AppCompatCacheParser.exe -f "E:\case\SYSTEM" --csv "E:\output" --csvf shimcache.csv

# 指定 control set（CCS）
AppCompatCacheParser.exe -f "E:\case\SYSTEM" --csv "E:\output" --csvf shimcache.csv --nl
```

`SYSTEM` hive 的路徑在鑑識 image 裡是 `Windows\System32\config\SYSTEM`。

輸出（示意輸出，實際依 image/版本而異）：

```
ControlSet, CacheEntryPosition, Path, LastModifiedTimeUTC, Executed, Duplicate
1, 0, \??\C:\Windows\System32\svchost.exe, 2024-06-15 08:00:00, Yes, No
1, 1, \??\C:\Windows\Temp\svc_update.exe, 2024-06-15 13:47:20, , No
1, 2, \??\C:\Windows\System32\cmd.exe, 2024-06-15 13:46:58, Yes, No
...
```

`Executed` 欄位在 Win 10 上通常是空的（因為沒有 flag 可以讀）。`LastModifiedTimeUTC` 是 `$SI` 的 Modified time，代表這個 exe 的檔案最後被修改的時間，不是執行時間。

`CacheEntryPosition`：ShimCache 是有序的，位置 0 是最新加入的記錄，數字越大越舊。這個相對順序有時候比絕對時間更有用——告訴你攻擊鏈上各個工具的執行前後順序。

### ShimCache 的容量和保留

最多保留 **1024 條**記錄（Win 10）。超過後，最舊的記錄被覆蓋。在高度活躍的機器上，幾天的記錄就可能滿了。

## SRUM（System Resource Usage Monitor）

### 機制

SRUM 是 Windows 8+ 引入的系統元件，記錄每個應用程式使用了多少系統資源：CPU 時間、記憶體使用量、網路 bytes（sent/received），以小時為粒度（實際上是約每小時 flush 一次，但可能更頻繁）。

位置：`C:\Windows\System32\sru\SRUDB.dat`（ESE（Extensible Storage Engine）格式，和 NTDS.dit 同一個格式）

SRUM 資料保留約 **30–60 天**，比其他 artifact 都長。

### 對鑑識的價值

1. **大量資料傳輸的確認**：攻擊者 exfiltrate 幾百 MB 的資料，SRUM 的網路 bytes 記錄能確認。你知道某個 process 在某個時間窗口傳了多少 bytes 出去，即使 PCAP 沒有，SRUM 有。

2. **長期存在的 C2 beacon**：beacon 每隔幾分鐘心跳一次，累積下來的網路 bytes 很少（幾十 KB 每天）。SRUM 可以確認「這個進程每天都有少量但持續的網路活動」，這是 beacon 的典型 pattern。

3. **程式執行存在的間接確認**：如果一個程式在 SRUM 裡有 CPU/網路記錄，代表它確實執行過（SRUM 不記錄從未執行的程式）。雖然 SRUM 不是執行時間的精確記錄，但它能讓你確認程式有在 SRUM 涵蓋的時間窗口內跑過。

### 使用 srum-dump

```powershell
# 需要先停止 SRUM service（live 系統）或使用鑑識 image
srum-dump.exe -i "E:\case\SRUDB.dat" -t "E:\case\SOFTWARE" -o "E:\output\srum.xlsx"
```

srum-dump 輸出 Excel 格式，有多個 sheet：
- `NetworkConnections`：每個 app 每小時的 BytesSent / BytesReceived
- `NetworkUsage`：類似 NetworkConnections 但 aggregation 不同
- `ApplicationUsage`：CPU time、ForegroundCycleTime、BackgroundCycleTime

也可以用 Velociraptor 的 `Windows.Applications.SRUM.*` artifact 系列遠端收集。

### 讀 SRUM 的方式

由於 SRUDB.dat 是 live 系統上被持續鎖定的 ESE database，直接複製可能得到不完整的快照。建議：

1. 使用 Velociraptor 遠端收集（它知道怎麼正確讀鎖定的 ESE）
2. 用 FTK Imager 做磁碟 image 再提取
3. 停止 `DiagTrack` 和 `WdiServiceHost` 服務後再複製（需要管理員權限，且可能影響系統）

### SRUM 的侷限

- **時間精度只到小時**：SRUM 按小時 bucket 記錄，你知道「在 13:00-14:00 這個小時，Process X 送出了 50MB」，但不知道確切幾點幾分。
- **Process name 可能是 PID 不是完整路徑**：SRUM 記錄的是 User SID + Application ID，Application ID 通常對應一個可執行的路徑，但在進程已退出的情況下，路徑可能解析不到完整名稱。
- **ESE 格式需要特定工具**：不能直接用文字工具讀。srum-dump 或 Python 的 `dissect.esedb` 套件可以解析。

## 執行證據對照表：四個 artifact 比較

| 面向 | Prefetch | AMCache | ShimCache | SRUM |
|---|---|---|---|---|
| **確認執行** | **強**（有 run count） | 中（可能只是被掃描） | 弱（Win 10 無 exec flag） | 中（間接，有 CPU/網路記錄）|
| **執行時間** | **精確**（秒級，最近 8 次） | 記錄的是 key lastwrite，不是執行時間 | 記錄的是 file lastmodified，不是執行時間 | 小時級 |
| **執行次數** | **有**（run count） | 無 | 無 | 無（只有累積資源用量） |
| **PE hash** | 無（是路徑 hash，不是 content hash） | **有**（SHA1） | 無 | 無 |
| **磁碟 footprint** | 每個 exe 一個 .pf 檔 | 一個 hive 檔 | SYSTEM hive 的一個 value | 一個 ESE 資料庫 |
| **刪除後仍有記錄** | .pf 存在就有（但 .pf 可被刪） | 有（hive 記錄不隨 exe 刪除） | 有（registry 記錄不隨 exe 刪除） | 有（資料庫記錄） |
| **Server 預設** | **關閉** | 有 | 有 | 有（Win 8+） |
| **保留期** | ~128 個 .pf 檔 | 持久（手動才清） | 最多 1024 條 | ~30-60 天 |
| **主要侷限** | Server 關閉；.pf 可被刪 | 時間語意不精確 | Win 10 無 exec flag；重開機才寫 | 時間精度差；工具複雜 |
| **解析工具** | PECmd | AmcacheParser | AppCompatCacheParser | srum-dump / Velociraptor |

## 具體範例：確認工具執行的完整鏈

### 情境

懷疑攻擊者在 `2024-06-15 13:45–14:00 UTC` 執行了 credential dumping 工具。檔案已被刪除，現在要靠 artifact 確認。

**Step 1 — Prefetch**

```powershell
PECmd.exe -d "E:\case\Prefetch" --csv "E:\output" --csvf prefetch.csv
# 在 CSV 裡搜尋 ExecutableName 包含 MIMIKATZ 或 DUMP
```

找到 `MIMIKATZ.EXE-5E1B7EA2.pf`：
- LastRun: `2024-06-15 13:52:01 UTC`
- RunCount: 4
- FilesLoaded 包含 `LSASS.EXE`

**確認**：mimikatz 在事件時間點執行了 4 次，最後一次 13:52，且讀取了 lsass。

**Step 2 — AMCache**

```powershell
AmcacheParser.exe -f "E:\case\Amcache.hve" --csv "E:\output" --csvf amcache.csv
# 搜尋 FullPath 包含 mimikatz 或 temp
```

找到記錄：
- FullPath: `c:\windows\temp\mimikatz.exe`
- SHA1: `a1b2c3d4e5f6...`（查 VirusTotal：結果命中 60+/70 引擎）
- FileKeyLastWriteTimestamp: `2024-06-15 13:47:30`

**確認**：拿到 SHA1 做 threat intel 確認，明確是 mimikatz。

**Step 3 — ShimCache**

```powershell
AppCompatCacheParser.exe -f "E:\case\SYSTEM" --csv "E:\output" --csvf shimcache.csv
```

找到 `\??\C:\Windows\Temp\mimikatz.exe`，position 3（代表它是第四新的記錄）。LastModifiedTimeUTC 是工具的 compile 時間，不是執行時間，但 position 3 和 position 0-2 的工具（cmd.exe、powershell.exe、net.exe）組合起來，告訴你攻擊鏈的順序。

**補充資訊**（不是確認執行的主要證據，在 Win 10）：確認 mimikatz 曾出現在這個路徑。

**Step 4 — SRUM**

```powershell
srum-dump.exe -i "E:\case\SRUDB.dat" -t "E:\case\SOFTWARE" -o "E:\output\srum.xlsx"
# 看 NetworkConnections sheet，篩選 2024-06-15 13:00-14:00
```

（示意輸出，實際依 image/版本而異）：某個進程（Application ID 對應到 `mimikatz.exe` 路徑）在 13:00-14:00 的 BytesSent 是 2048 bytes，BytesReceived 是 1024 bytes。這個量很小，不像 exfil——可能只是 mimikatz 連 C2 回報結果。

**綜合結論**：Prefetch 確認執行且有 4 次 run count；AMCache 確認 SHA1 是 mimikatz；ShimCache 確認路徑存在過且在攻擊鏈中的順序；SRUM 確認有少量網路活動。四個 artifact 互相補強，形成完整的執行證據鏈。

## 各 artifact 的 anti-forensics 和對應偵測

| 攻擊者動作 | 影響 | 鑑識對策 |
|---|---|---|
| 刪除 `.pf` 檔 | Prefetch 記錄消失 | $UsnJrnl 找到 .pf 的 FILE_DELETE 記錄；Prefetch 目錄非空但特定 .pf 消失是異常 |
| 停用 Prefetch（改 registry） | 之後的執行不再被記錄 | 但停用動作本身在 registry hive 的 lastwrite 和 Event Log 留下痕跡 |
| 清除 Amcache.hve | AMCache 記錄消失 | Event Log 4688 可能有對應的 process（cleaner 工具）執行；$MFT 記錄 Amcache.hve 的修改時間 |
| 刪除 SRUDB.dat | SRUM 記錄消失 | $MFT/$UsnJrnl 記錄刪除操作；`System` Event Log 可能記錄 SRUM service crash |
| 改名工具再執行 | Prefetch 存 .pf 但用新名字 | 看 .pf 裡的 FilesLoaded（DLL 組合、目錄）可以識別工具真實身份 |

## 踩雷清單

1. **ShimCache 在 Win 10/11 上不能確認「執行」**：Win 10 的 ShimCache 沒有 execution flag，只能確認「存在過」。如果你把 Win 10 ShimCache 記錄寫進報告說「確認執行」，會被專業的對方律師或對手鑑識師打臉。寫「曾出現在系統路徑上」比較安全。

2. **Prefetch 的 LastRun 是「最後一次執行的開始時間」不是結束時間**：LastRun 記錄的是程式啟動的時間點，不是執行完成的時間。短命的工具（一秒就跑完）的 LastRun 和實際操作時間幾乎一致，但長時間執行的程式要注意。

3. **AMCache 的 SHA1 前有 `0000` 要去掉**：AMCache 的 `FileId` value 是 `0000` + 真正的 SHA1，用工具時確認它有自動處理，手動看的時候記得去掉前四個字元再查 VirusTotal。AmcacheParser 的輸出 CSV 通常已處理好，但自己寫腳本的話要注意。

4. **ShimCache 只在重開機/登出時寫入 registry，live 分析要額外看記憶體**：分析 live 系統的 registry 只能看到上次重開機之前的 ShimCache 記錄。最近的執行（上次重開機到現在）只在記憶體裡，要用 `windows.shimcachemem` (Volatility) 或 Velociraptor 的 `Windows.Registry.AppCompatCache` artifact（它知道要讀記憶體）才能拿到。

5. **Prefetch 的 run count 上限是 99（或視版本而定），超過就停在最大值**：你看到 run count 99 不代表只執行了 99 次，可能執行了幾百次。Run count 用來確認「確實執行過」和「大致執行頻率」，但不能作為精確計數。

## 進階延伸

- **Windows Timeline（活動歷史）**：Win 10 的 `C:\Users\<user>\AppData\Local\ConnectedDevicesPlatform\` 下的 `ActivitiesCache.db`（SQLite）記錄使用者的活動歷史，包含開啟的文件、程式、URL，有完整的 timestamp。是 Prefetch 之外另一個執行 artifact，工具用 WxTCmd 解析。

- **LNK 檔（Shell Link）**：使用者用 Explorer 開啟的檔案和程式，在 `C:\Users\<user>\AppData\Roaming\Microsoft\Windows\Recent\` 和 `C:\Users\<user>\AppData\Roaming\Microsoft\Office\Recent\` 留下 .lnk 快捷方式，裡面含有目標路徑、volume serial number、MAC timestamp。即使原始檔案已刪除，LNK 仍保留檔案曾存在過的 metadata。LNKParser 或 LECmd 解析。

- **SuperFetch / ReadyBoost**：Win 7+ 的 SuperFetch 在 `C:\Windows\Prefetch\` 之外還有 `ReadyBoot\` 子目錄和 `AgGlFaultHistory.db` 等。這些是更底層的效能 artifact，在特定場合可以補充 Prefetch 的盲點。

- **Super Timeline 整合**：把 Prefetch（PECmd CSV）、AMCache（AmcacheParser CSV）、ShimCache（AppCompatCacheParser CSV）、SRUM、$MFT（MFTECmd CSV）、$UsnJrnl、Event Log 匯入 Plaso（`log2timeline.py`），生成統一 timeline，用 Timesketch 或 ES/Kibana 做 timeline 查詢。這是大型 IR 的標準作業流程。

## 本章重點整理

- 執行痕跡 artifact 回答「這個程式跑過嗎」，補充 $MFT 只記錄「存在過」的不足
- Prefetch：執行確認最強（run count + 精確 LastRun），但 Server 預設關閉、.pf 可被刪
- AMCache：唯一記錄 PE SHA1 content hash 的 artifact，即使檔案刪除後仍可查 VirusTotal
- ShimCache：Win 10 **沒有 execution flag**，不能當做單獨的執行確認；只在重開機寫入 registry
- SRUM：約 30-60 天的網路/CPU 資源用量記錄，時間精度到小時，識別 exfil 和 beacon 的輔助
- 四個 artifact 要交叉驗證，單一 artifact 都有盲點；Prefetch + AMCache 是最強的執行確認組合
- Anti-forensics 也留下痕跡：刪 .pf 在 $UsnJrnl 有記錄、停用 Prefetch 在 registry 有記錄

## 自我檢核

- [ ] 我能說出 Prefetch .pf 檔名的 hash 是什麼的 hash（路徑 hash，不是 content hash），以及為什麼同名不同路徑的 exe 有不同 .pf
- [ ] 我能解釋 ShimCache 在 Windows 10 上的時間語意問題，以及為什麼不能單獨用它確認執行
- [ ] 我能說出 AMCache SHA1 的正確讀法（去掉 `0000` 前綴），以及它在 threat intel 上的用途
- [ ] 我能說出 Prefetch 在哪種環境預設關閉，以及攻擊者刪了 .pf 後怎麼確認它曾存在
- [ ] 我能說出 SRUM 的時間精度和保留期，以及它在什麼場合比 Prefetch 有用
- [ ] 我能把四個 artifact 串成一個「確認 credential dumping 工具執行」的分析流程

## 延伸閱讀

1. **SANS FOR500 — Windows Forensic Analysis**：FOR500 的 execution artifacts 章節是業界最完整的教材，特別是 ShimCache 的版本差異和時間語意那部分，SANS 的 "Windows Forensic Analysis" Poster 把四個 artifact 的特性整理成一頁速查表。

2. **"Execution Artifacts in Windows" — 13Cubed（YouTube）**：視覺化地展示每個 artifact 的結構和解析過程，對初學者極為友好，看完後回來對照這章的技術細節。

3. **AmcacheParser / PECmd / AppCompatCacheParser GitHub**（[https://ericzimmerman.github.io](https://ericzimmerman.github.io)）：Eric Zimmermann 工具的 release notes 記錄每次更新處理了哪個 Windows 版本的格式變化，遇到解析錯誤先查這裡。

4. **"SRUM Forensics" — Mark Baggett（SANS）**：SRUM 的最完整鑑識參考，詳細說明 ESE 格式、各 table 的欄位語意、以及 exfil 偵測的具體案例。搭配 `srum-dump` 的 GitHub README 一起讀。

5. **《The Art of Memory Forensics》** Ch 17：惡意程式執行的記憶體 artifact 和磁碟 artifact 的交叉分析，把本章（磁碟執行痕跡）和 Ch 13-14（記憶體）連起來的視角。

---

執行痕跡確認了「什麼跑過了」。下一步問：「系統狀態被改了什麼？」——Registry 是 Windows 設定和持久化的核心，攻擊者的 persistence 幾乎都碰 registry。

→ [Ch 17 Registry 鑑識](./17-windows-registry-forensics.md)
