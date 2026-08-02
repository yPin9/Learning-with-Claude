# Ch 17 — Registry 鑑識

> 目標：能從離線或記憶體萃取的 Registry hive 中，找出持久化植入、USB 裝置痕跡、使用者活動軌跡，並以 last write time 為時間軸錨點還原攻擊者的操作序列。
>
> 環境：Windows 10/11 鑑識工作站或 REMnux/SIFT VM；工具 RegRipper 3.0、Registry Explorer（Eric Zimmerman 工具集）；hive 來源可以是離線映像或即時 `reg export`。所有輸出標「（示意，依版本/樣本而異）」。

## 為什麼 Registry 是黃金礦脈

Registry（登錄檔）不只是 Windows 設定資料庫——它是作業系統的神經系統，幾乎所有 Windows 子系統的狀態都寫在裡面。從攻擊者的角度，Registry 有兩個致命吸引力：**持久化**和**組態竊取**。從鑑識者的角度，這些寫入都留有 last write time，沒有任何工具能在不修改 timestamp 的前提下悄悄植入一個 key——即使攻擊者手動竄改時間，still 會在 $MFT/$LogFile 裡留痕（上一章談過）。

你在 AD 課或 OSCP 練習裡植入過 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`。現在換個角度：防守方打開 SYSTEM 和 NTUSER.DAT，在幾分鐘內找到你在哪個時間點種了什麼。這就是這章要做的事。

## 先建立直覺：hive 是什麼

Registry 在磁碟上以 **hive 檔案**（二進位格式）儲存，不是資料夾。Windows 把多個 hive 拼接成一棵樹，對外呈現為 `HKEY_LOCAL_MACHINE`（HKLM）等根鍵。

**hive 對應關係：**

| 根鍵 / 路徑 | 磁碟上的實體檔案 | 說明 |
|---|---|---|
| `HKLM\SYSTEM` | `%SystemRoot%\System32\config\SYSTEM` | 服務、驅動、TimeZone、網路介面 |
| `HKLM\SOFTWARE` | `%SystemRoot%\System32\config\SOFTWARE` | 安裝程式、OS 版本、自動啟動 |
| `HKLM\SAM` | `%SystemRoot%\System32\config\SAM` | 本地帳號密碼 hash（即時無法讀，鑑識映像可以）|
| `HKLM\SECURITY` | `%SystemRoot%\System32\config\SECURITY` | LSA secrets、域快取登入 hash |
| `HKCU` / `HKU\<SID>` | `%USERPROFILE%\NTUSER.DAT` | 每個使用者一份；自動啟動、MRU、ShellBags |
| `HKU\<SID>_Classes` | `%USERPROFILE%\AppData\Local\Microsoft\Windows\UsrClass.dat` | COM 登錄、ShellBags（另一份） |

**hive 格式重點：**

- 二進位格式，magic bytes `regf`（檔頭）、`hbin`（資料塊）
- 每個 key 有 **last write time**（FILETIME，100 ns 精度）——這是 Registry 鑑識的時間錨
- 值（value）沒有獨立 timestamp，只有所屬 key 的 last write time
- hive 有 log 檔（`.LOG1`/`.LOG2`），記錄未 commit 的異動，鑑識時必須一起取

**鑑識取得方式：**

1. **離線映像**：`%SystemRoot%\System32\config\*`、`NTUSER.DAT` 直接複製（需 Volume Shadow Copy 或 raw 映像，因為即時系統鎖定）
2. **記憶體萃取**：Volatility 的 `hivelist`/`printkey` plugin 從記憶體 dump 讀取
3. **即時系統**：`reg save HKLM\SYSTEM C:\system.hiv`（需管理員）

## 底層機制：hive 格式與 last write time

每個 key 在 hive 裡是一個 **Named Key（nk record）**，包含：

```
nk record
├── Signature: "nk"
├── Flags (volatile / key flags)
├── Last Write Time: FILETIME (8 bytes, 100-ns intervals since 1601-01-01)
├── Parent Key offset
├── Number of subkeys
├── Number of values
└── Key name (variable length)
```

**last write time 的含義**：

- key 被建立時設定
- key 的**直接**值被新增/修改/刪除時更新
- subkey 的異動**不**影響父 key 的 timestamp

這個細節很重要：如果攻擊者在 `Run` 下加了一個值，`Run` key 的 last write time 會更新，但 `CurrentVersion` 的不會。反推攻擊時間用的是葉節點的 timestamp。

### 工具：Registry Explorer vs RegRipper

| 工具 | 定位 | 用法 |
|---|---|---|
| **Registry Explorer** (Eric Zimmerman) | GUI，互動式瀏覽與搜尋，含 bookmarks 快速跳到已知鑑識位置 | 拖入 hive → 左欄樹狀 → 右欄值；Time 欄可排序 |
| **RegRipper 3.0** | CLI，plugin 驅動，自動萃取預設鑑識興趣點，輸出文字報告 | `rip.exe -r NTUSER.DAT -f ntuser` |
| **RECmd** (Eric Zimmerman) | CLI，支援 batch 設定檔，輸出 CSV | `RECmd.exe -d hives/ --bn BatchExamples/kroll_batch.reb --csv out/` |

## 持久化位置鑑識

這裡是你身為紅隊最熟悉的地盤。每個 persistence key 的 last write time 就是攻擊者植入的時間。

### Run / RunOnce

```
HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce
```

RegRipper 輸出範例（示意，依版本/樣本而異）：

```
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run
LastWrite: 2024-03-15 03:22:11Z

  updater  REG_SZ  C:\ProgramData\svchost32.exe -silent
```

timestamp `03:22:11Z` 就是攻擊者寫入的時間。正常軟體的 Run key 通常在安裝時間附近，深夜 03:22 的值立刻可疑。

**Wow6432Node**：64 位元系統上 32 位元程式寫入的 key 會在 `SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Run`，很多人忽略。

### Services（服務）

```
HKLM\SYSTEM\CurrentControlSet\Services\<ServiceName>
```

關鍵值：

| 值 | 含義 |
|---|---|
| `ImagePath` | 可執行路徑，攻擊者藏 payload 就在這 |
| `Start` | 0=Boot, 1=System, 2=Auto, 3=Demand, 4=Disabled |
| `Type` | 1=Kernel Driver, 16=Win32 Service |
| `Description` | 可偽造，看起來像合法服務 |

Event ID 7045（系統 log）是服務安裝的配對事件，Registry key 的 last write time 和 7045 時間應該吻合——不吻合就代表 log 被竄改或服務被手動建立繞過 SCM。

### Winlogon

```
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon
```

正常值：

```
Shell     REG_SZ  explorer.exe
Userinit  REG_SZ  C:\Windows\system32\userinit.exe,
```

攻擊者的玩法是把 `Userinit` 改成 `userinit.exe, malware.exe,` 或把 `Shell` 換成惡意程式。後面的逗號是 Windows 支援多個值的語法，很多人不知道。

### AppInit_DLLs

```
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows
  AppInit_DLLs  REG_SZ  C:\malicious\inject.dll
  LoadAppInit_DLLs  REG_DWORD  1
```

`LoadAppInit_DLLs` 必須為 1 才生效。現代 Windows（Secure Boot + UEFI）預設 AppInit 被 Code Integrity 阻擋，但在舊機器或禁用 Secure Boot 的環境仍然有效。

### Image File Execution Options（IFEO）

```
HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\<target.exe>
  Debugger  REG_SZ  C:\malware\backdoor.exe
```

這是 debugger hijacking：每次 `target.exe` 啟動，Windows 改為執行 `Debugger` 指定的程式。攻擊者常 target `sethc.exe`（Sticky Keys）或 `utilman.exe` 做後門。

Registry Explorer 的 bookmarks 功能把以上所有位置都預設標好了，進去按 `Bookmarks → All` 就能掃一輪。

## USB 裝置痕跡

```
HKLM\SYSTEM\CurrentControlSet\Enum\USBSTOR
```

每個插過的 USB 儲存裝置在這裡有一個子 key，格式：

```
Disk&Ven_SanDisk&Prod_Ultra&Rev_1.00
  └── <SerialNumber>
        ├── FriendlyName  REG_SZ  "SanDisk Ultra USB Device"
        ├── ClassGUID
        └── ... 參數子 key
```

配合：

```
HKLM\SYSTEM\CurrentControlSet\Enum\USB  (USB 通用，含 hub/HID)
HKLM\SOFTWARE\Microsoft\Windows Portable Devices\Devices  (顯示名稱)
HKLM\SYSTEM\MountedDevices  (磁碟機代號對應)
```

**重建完整 USB 歷史**需要跨四個 hive + $MFT 的 setupapi.dev.log：

1. USBSTOR → 取得廠商、型號、序號
2. `SYSTEM\MountedDevices` → 取得磁碟機代號（D: E: 等）
3. `SOFTWARE\Microsoft\Windows Portable Devices` → 取得使用者可見名稱
4. `%SystemRoot%\INF\setupapi.dev.log` → 取得首次連接時間（hive 的 last write time 只能給最後一次）
5. `NTUSER.DAT\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2` → 使用者帳號掛載紀錄

RegRipper 的 `usbstor.pl` plugin 自動萃取上述資訊。

## 使用者活動痕跡

### UserAssist（程式執行計數）

```
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\UserAssist\{GUID}\Count
```

GUID 依類型不同（捷徑 vs 應用程式），值名是 ROT13 編碼的路徑，值資料包含執行次數、最後執行時間（FILETIME 偏移）。

RegRipper `userassist.pl` 自動解 ROT13 並輸出（示意，依版本/樣本而異）：

```
UEME_RUNPATH:C:\Users\victim\Downloads\evil.exe
  RunCount: 3
  LastRun:  2024-03-15 03:19:42Z
```

攻擊者在 03:19 執行了 evil.exe，03:22 種了 Run key，時間序列一致。

### RecentDocs（最近開啟的文件）

```
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs
```

以副檔名為子 key，值是 MRU（Most Recently Used）序列，binary 資料包含檔名。表示哪些文件被開過，可以 corroborate 釣魚郵件附件。

### TypedPaths（手動輸入的路徑）

```
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths
```

使用者在 Explorer 網址列手動輸入過的路徑，最多保留幾十條。攻擊者瀏覽 `\\attacker-ip\share` 會在這裡留痕。

### ShellBags（資料夾瀏覽歷史）

ShellBags 是 Windows 記錄「使用者曾經在 Explorer 開啟過哪些資料夾，以及視窗設定（大小、排列方式）」的機制，存在：

```
NTUSER.DAT:  HKCU\SOFTWARE\Microsoft\Windows\Shell\BagMRU
             HKCU\SOFTWARE\Microsoft\Windows\Shell\Bags
UsrClass.dat: HKCU\SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\Shell\BagMRU
              HKCU\SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\Shell\Bags
```

ShellBags 最強大的地方：**即使資料夾已刪除，ShellBag 仍然存在**。攻擊者刪了 `C:\staged_payloads\`，但 ShellBag 告訴你它曾存在，且使用者（或攻擊者的 shell session）曾打開它。

Eric Zimmerman 的 **ShellBagsExplorer** 是最好用的工具，會解碼 binary 結構並呈現完整路徑。

## 具體範例：完整鑑識流程

### 範例 1：發現異常的 Run key

```bash
# RegRipper 掃 NTUSER.DAT
rip.pl -r NTUSER.DAT -p run
```

輸出（示意，依版本/樣本而異）：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
LastWrite: 2024-03-15 03:22:11Z
  updater  C:\ProgramData\Microsoft\updater.exe -c http://10.0.0.1:8080/c2
```

可疑點：
- 03:22（深夜）
- `C:\ProgramData` 是非典型 Run key 路徑，合法軟體通常在 `Program Files`
- `-c http://` 明顯是 C2 beacon 參數
- 接下來去 Prefetch / AMCache 確認這個 exe 確實跑過

### 範例 2：USBSTOR 追外洩路徑

情境：懷疑有人把資料複製到 USB 帶走。

RegRipper 輸出（示意，依版本/樣本而異）：

```
HKLM\SYSTEM\CurrentControlSet\Enum\USBSTOR
LastWrite: 2024-03-15 17:33:20Z

Disk&Ven_Kingston&Prod_DataTraveler_3.0&Rev_PMAP
  SN: 001372953BF7A421&0
  FriendlyName: Kingston DataTraveler 3.0 USB Device
  Driver: {4D36E967-...}
```

配合 `MountPoints2`：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2
LastWrite: 2024-03-15 17:33:22Z
  ##Volume#{...}  (對應磁碟機代號)
```

17:33 插入 USB，接下來去 $MFT 和 $UsnJrnl 找同時段的複製動作（上章技術）。

### 範例 3：邊界情境 — 時間戳記被竄改

攻擊者可以用工具把 Registry key 的 last write time 改成過去（例如偽裝成三個月前安裝的合法軟體）。

**怎麼識別？**

- 如果 key 的 parent 有更新的 timestamp，但 child 的 timestamp 更舊，邏輯上不可能（parent write time 會在 child 建立後更新，除非只有子 key 被篡改）
- $MFT 的 hive 檔案本身 change time（$FILE_NAME 的 $MTIME）若與 key 的宣稱時間不符，說明 hive 在那之後被修改
- 配合 Event Log 的時序做 cross-validation

## 對比表格

| 活動類型 | Registry 位置 | 工具 | 時間資訊 |
|---|---|---|---|
| 開機自啟 | Run/RunOnce（HKLM/HKCU） | RegRipper `run.pl` | key last write time |
| 服務安裝 | `SYSTEM\Services\<name>` | RegRipper `services.pl` | key last write time |
| Winlogon 竄改 | `\CurrentVersion\Winlogon` | Registry Explorer | key last write time |
| IFEO hijack | `Image File Execution Options` | RegRipper `ifeo.pl` | key last write time |
| USB 裝置歷史 | `USBSTOR` + `MountedDevices` | RegRipper `usbstor.pl` | last write + setupapi.log |
| 程式執行計數 | UserAssist | RegRipper `userassist.pl` | embedded FILETIME |
| 開啟文件 | RecentDocs | RegRipper `recentdocs.pl` | MRU 順序 + key timestamp |
| 瀏覽資料夾（刪除後） | ShellBags | ShellBagsExplorer | embedded FILETIME |

## 踩雷

1. **直接開正在跑的系統的 hive 用 regedit**：`HKLM\SYSTEM` 等 hive 在 Windows 跑起來時鎖定，`reg export` 只能抓到登錄的視圖（可能含揮發性 key），做鑑識必須用 Shadow Copy 或 raw 映像取離線 hive。

2. **忘記 Wow6432Node**：32 位元應用程式的寫入在 64 位元系統上重導向到 `Wow6432Node`。掃 Run key 只掃了 `HKLM\SOFTWARE\...\Run` 而漏了 `Wow6432Node` 下的同名 key，惡意軟體就這樣溜掉。RegRipper 的 `run.pl` 會同時掃兩個路徑。

3. **只看 HKLM 忘了 HKCU**：很多攻擊者用 HKCU（不需要管理員）植入 Run key，這個 key 在各使用者的 NTUSER.DAT 裡，要每個帳號的 hive 都掃。

4. **ShellBags 只查 NTUSER.DAT**：ShellBags 分散在 NTUSER.DAT 和 UsrClass.dat 兩個 hive。從 Windows Vista 開始，「遠端資料夾」的 ShellBag 主要記錄在 UsrClass.dat，只查 NTUSER.DAT 會漏掉攻擊者瀏覽的網路芳鄰路徑（如 `\\FILESERVER\admin$`）。

5. **把 last write time 當建立時間**：key 每次有直接子值異動就更新。一個 Run key 如果攻擊者分兩次修改，last write time 只反映最後一次。如果你只看到很新的 timestamp，不代表這個 key 是新的，可能是攻擊者更新了值。

## 進階延伸

- **AppCompatFlags**：`HKCU\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store` 記錄使用者「曾點擊運行」某個 exe 的對話框確認，是執行確認的額外佐證。

- **Amcache.hve**：`%SystemRoot%\AppCompat\Programs\Amcache.hve` 嚴格來說不是「系統 Registry」，但也是 hive 格式，記錄執行程式的 SHA-1 hash、PE metadata、安裝時間；Ch 16 的 AMCache 小節討論過，鑑識時和 Run key 搭配用。

- **BAM/DAM**（Background Activity Monitor）：`HKLM\SYSTEM\CurrentControlSet\Services\bam\State\UserSettings\<SID>\` 記錄每個 exe 最後執行時間，Windows 10 以後才有，是 UserAssist 的補充。

- **TypedURLs（IE/Edge Legacy）**：`HKCU\SOFTWARE\Microsoft\Internet Explorer\TypedURLs` 記錄瀏覽器網址列輸入，Edge Chromium 已改位置，但在 IE 時代的攻擊案子很有用。

- **MuiCache**：`HKCU\SOFTWARE\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache` 把 exe 路徑對應到顯示名稱，是程式曾跑過的另一個證據來源，且這個 key 不需要提權就能寫，攻擊者可以假冒（要搭配其他 artifact 確認）。

## 本章重點整理

- Registry hive 是二進位檔；鑑識時需從 Shadow Copy 或 raw 映像取離線 hive，不能用 regedit。
- 每個 key 的 **last write time** 是時間錨點，值本身沒有獨立 timestamp。
- 持久化熱點：Run/RunOnce（HKLM+HKCU+Wow6432Node）、Services、Winlogon、AppInit_DLLs、IFEO。
- USB 歷史：USBSTOR + MountedDevices + MountPoints2 + setupapi.dev.log 四源合一。
- 使用者活動：UserAssist（執行計數/時間）、RecentDocs（開啟文件）、TypedPaths（路徑輸入）、ShellBags（資料夾瀏覽，即使目錄已刪）。
- 工具：Registry Explorer（互動 GUI + bookmarks）、RegRipper（CLI plugin 批次）、RECmd（CSV 輸出整合管線）。

## 自我檢核

- [ ] 我能說出 HKLM\SOFTWARE vs NTUSER.DAT 各對應哪個物理檔案，以及為什麼必須離線取
- [ ] 我能解釋 last write time 的更新觸發條件，以及為什麼它只反映直接值的異動
- [ ] 給我一個 NTUSER.DAT，我能用 RegRipper 找出所有 Run key 的條目，包含 Wow6432Node
- [ ] 我能解釋 ShellBags 為什麼能記錄已刪除資料夾的瀏覽歷史
- [ ] 我能說出 USBSTOR 鑑識需要對照哪四個來源才能拼出完整 USB 歷史
- [ ] 我能識別 Winlogon Userinit 竄改的特徵，以及 IFEO 的攻擊手法
- [ ] 我能說出 timestamp 被竄改時，如何用父子 key 的時間邏輯關係識別

## 延伸閱讀

1. **SANS FOR500 — Windows Forensic Analysis** — 課程 Section 2 & 3 專門講 Registry 鑑識；特別是 Registry 時間線重建與 ShellBags 深挖，是本章的延伸地基，直接到 SANS 課程頁下載試閱材料。

2. **[Eric Zimmerman's Tools documentation](https://ericzimmerman.github.io/)** — Registry Explorer、RECmd、ShellBagsExplorer 的官方說明；學會 RECmd 的 batch 設定檔可以把整個 Registry 鑑識打包成一條命令輸出 CSV，接後續 timeline 用。

3. **[The DFIR Report — 真實案例](https://thedfirreport.com/)** — 搜尋「registry persistence」，看職業 IR 團隊如何在真實 Ransomware 案件中從 Run key + Services 還原攻擊者 TTP，對照本章技術。

4. **Windows Registry Forensics (Carvey, 2nd ed.)** — 最系統的 Registry 鑑識教材；第 3 章的 hive 格式細節與第 5 章的使用者活動 artifact 是本章的底層補充，沒有其他書講得這麼深。

5. **[libregf 與 python-registry](https://github.com/msuhanov/regf)** — 如果想自己解析 hive binary，`msuhanov/regf` 規格文件是最完整的 hive 格式參考，比 Microsoft 官方文件更實用。

---

下一章從 Registry 跳到 Event Log，補齊 Windows 的另一半時間軸——哪些 Event ID 能把登入、進程建立、服務安裝、橫向移動串成攻擊鍊。

→ [Ch 18 Event Log 鑑識](./18-windows-event-log-forensics.md)
