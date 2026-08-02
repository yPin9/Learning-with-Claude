# Ch 4 — 證據可信度與鑑識報告

> 目標：從「揮發性順序」到「鑑識報告格式」，建立完整的證據可信度思維——知道為什麼每一個採證步驟都必須記錄、為什麼未留存監管鏈的磁碟映像在法庭上一文不值，以及對手的反鑑識技術如何從源頭動搖這一切。

---

## 為什麼「證據可信度（forensic soundness）」是個問題

攻擊者拿到 Domain Admin、撈走資料、離開。事後你有日誌、有磁碟、有記憶體 dump——但若這些證據在採集過程被污染、在傳遞過程沒有記錄，到了法庭、到了合規稽核、甚至到了你自己的事後報告，沒有人有理由相信你的結論。

數位證據有一個根本上的不對稱：**篡改它極其容易，而證明它未被篡改卻需要完整的事前程序**。你在紅隊測試時 `dd if=/dev/mem` 完就走，不在乎這個。但當角色換成藍隊，採到的每一個位元組都必須能回答：「這個位元組，從受害主機到你的分析報告，中間每一步是誰碰了、碰了什麼？」

這章建立的是鑑識工作的地基。後面幾章談的 log source、memory forensics、磁碟分析，都建立在這個地基之上。

---

## 揮發性順序（Order of Volatility）的直覺

先從心智模型開始。電腦的各層儲存，有一條從「最快消失」到「最難消失」的軸線：

```
揮發性 (Volatility)
高 ─────────────────────────────────────────────► 低
│
│  CPU 暫存器/快取   RAM (DRAM)   Swap/Page file   網路狀態
│      <1 ns           ms            秒               秒
│
│  執行中的行程     磁碟 (HDD/SSD)   光碟/磁帶
│       秒              年              數十年
│
▼
消滅速度
```

「揮發性」不是模糊的概念，它有物理意義：

- **CPU 暫存器／快取**：一旦行程切換或電源中斷，register 值立刻被覆寫。你在 GDB 裡見過這個——加一個 watchpoint，下一步就不一樣了。
- **RAM**：電源切斷後數秒到數十秒逐漸 decay（冷啟動攻擊的前提就是 DRAM 電容放電需要時間）。正常關機後幾秒就讀不到了。
- **Swap / Page file**：在磁碟上，但作業系統可能在任何時候覆寫。`pagefile.sys` 或 Linux `/proc/swaps` 指向的分區，內容隨時都在變動。
- **網路連線狀態**：`netstat` 輸出、ARP cache、routing table——只要 session 結束就消失，對手 kill C2 連線後 TCP state machine 30 秒內清空。
- **執行中的行程清單**：行程一旦結束，PID 被回收，指令列 `cmdline`、環境變數 `environ` 都進入 `/proc/<pid>/` 的墓穴。
- **磁碟**：SSD/HDD 斷電後資料保留，但 SSD 的 TRIM/GC 可能在後台悄悄清除已刪除的 block。
- **光碟／磁帶**：最低揮發性，封存後幾乎不會自行改變。

**採集優先順序就是揮發性的倒序**：先捕 RAM 和網路狀態，最後才做磁碟映像。等你做完 `dd` 再回頭想抓記憶體，已經太遲了——如果這台機器重開過，記憶體內容全沒了。

---

## 監管鏈（Chain of Custody）

### 它是什麼、為什麼存在

監管鏈（Chain of Custody，CoC）是一份記錄「這件證據從被採集開始，每次換手都有誰接、何時接、做了什麼」的文件。它的目的只有一個：**讓任何審閱者都能確認，這份證據在你聲稱的每一刻都由可識別的人保管，沒有無法解釋的空窗期**。

美國 Federal Rules of Evidence Rule 901 和臺灣刑事訴訟法第 159 條之一到之五（傳聞例外規則）都要求數位證據必須能夠被驗證其真實性。CoC 是這個驗證的基礎文件之一。

### 表單結構（實際可用格式）

```
DIGITAL EVIDENCE CHAIN OF CUSTODY

案件編號 (Case ID):     IR-2025-0817
證據項目 (Item):        #001 — Samsung 870 EVO 2TB SSD (序號 S4EVNX0R123456)
採集人 (Collected by):  王小明 / 數位鑑識組
採集地點:               台北市信義區某辦公室 Server Room B3
採集時間 (UTC):         2025-08-17 09:42:33Z
採集方式:               Tableau TD3 Forensic Imager + write blocker，輸出 E01

─────────────────────────────────────────────────
日期/時間 (UTC)    | 移交人      | 接收人      | 目的          | 簽名
2025-08-17 09:42  | 現場保全    | 王小明      | 採集          | ___
2025-08-17 11:00  | 王小明      | 鑑識實驗室  | 入庫保存      | ___
2025-08-18 09:00  | 鑑識實驗室  | 李分析師    | 分析作業      | ___
2025-08-18 17:30  | 李分析師    | 鑑識實驗室  | 歸還存檔      | ___
─────────────────────────────────────────────────
SHA-256 (原始媒體): a3f9b2e1d0c87654321fedcba9876543210abcdef1234567890fedcba123456
SHA-256 (映像檔):   a3f9b2e1d0c87654321fedcba9876543210abcdef1234567890fedcba123456
```

空窗期是最大的危險。如果「李分析師接收」和「歸還實驗室」之間有一段時間沒有記錄，辯方律師就有理由主張這段時間內證據遭到修改。

---

## 防寫保護器（Write Blocker）

### 為什麼需要它

當你把一顆硬碟接上分析機器，作業系統**不是** neutral observer。Windows 會嘗試掛載磁區、讀取 NTFS 日誌、更新 `$VOLUME` metadata；Linux 的 `udev` 會觸發 `blkid`，進而讀取分區表並可能更新 `atime`（access time）。這些動作每一個都在修改你打算作為「原始證據」的媒體。

防寫保護器（Write Blocker）坐在分析機器和目標磁碟之間，讓所有「寫入」方向的指令在硬體層被攔截丟棄，同時讓「讀取」指令正常通過。

### 硬體 vs 軟體

| 類型 | 代表產品 | 可信度 | 情境 |
|------|---------|--------|------|
| 硬體 write blocker | Tableau TD3、WiebeTech Forensic UltraDock | 最高，不依賴 OS 設定 | 實驗室採集、法庭證據 |
| 軟體 write blocker | `hdparm -r1`（Linux）、Arsenal Image Mounter | 中，OS kernel 仍在中間 | 緊急現場或遠端 triage |
| 無 write blocker | 直接 mount | 不可接受用於採證 | 無法在法庭上使用 |

硬體 write blocker 之所以更可信，是因為它在 ATA/SATA/NVMe 協議層攔截 write command，不依賴 OS 的 read-only mount flag——OS kernel bug 或 root 程式繞過 mount flag 都無法影響它。

---

## 雜湊完整性驗證（Hash Verification）

### 驗證的邏輯

雜湊函數（Hash function）對同樣的輸入永遠產生同樣的輸出。只要磁碟任何一個位元改變，SHA-256 值就完全不同。因此：

```
採集前   → hash(原始媒體) → H1
採集映像 → hash(映像檔)   → H2
驗證     → H1 == H2? → YES: 映像完整  /  NO: 過程出錯，不可用
```

這個「採集前後都要算」的動作是強制的。如果只有採集後的 hash，你無法證明採集過程沒有修改原始媒體。

### 演算法選擇

- **MD5**：已知碰撞攻擊（collision attack）存在，不適合用來防偽造，但仍被舊工具和舊報告廣泛使用。若對方主張碰撞，你的 MD5 hash 在法庭上站不住腳。
- **SHA-256**：目前推薦標準。無已知實用碰撞攻擊，計算速度可接受。
- **SHA-3**：設計上與 SHA-2 完全不同的 Keccak 架構，量子計算抗性更好，但工具鏈支援還不普遍，法庭接受度也不如 SHA-256。

實務做法：同時記錄 MD5 和 SHA-256，前者為了相容舊文件，後者才是真正的完整性憑據。

### 實際指令：磁碟映像 + 驗證

```bash
# 步驟 1：採集前，在 write blocker 保護下算原始磁碟 hash
# /dev/sdb 是目標磁碟（已接 write blocker）
sha256sum /dev/sdb > /evidence/case-0817/sdb_original.sha256

# 步驟 2：dd 採集映像
# bs=512: 與磁碟 sector size 對齊
# conv=noerror,sync: 壞道不中斷，以 0x00 填補，記錄 offset
dd if=/dev/sdb \
   of=/evidence/case-0817/sdb_001.raw \
   bs=512 \
   conv=noerror,sync \
   status=progress \
   2>/evidence/case-0817/dd_log.txt

# 步驟 3：採集後，算映像檔 hash
sha256sum /evidence/case-0817/sdb_001.raw > /evidence/case-0817/sdb_image.sha256

# 步驟 4：比對
# 兩個 hash 必須完全一致
diff /evidence/case-0817/sdb_original.sha256 /evidence/case-0817/sdb_image.sha256
```

若 `diff` 有輸出，代表映像過程中有位元錯誤（壞道、傳輸錯誤），必須在報告中記錄哪些 sector 受影響，並說明對後續分析的影響。絕對不能刪掉這個記錄。

---

## 鑑識可重現性（Forensic Reproducibility）

可重現性（Reproducibility）的定義很直接：**同一位分析師，對同一份映像，使用同一個工具，執行同樣的指令，必須得到完全一樣的結果**。

這聽起來很廢話，但在數位鑑識中有很多地方會破壞它：

- **工具版本**：`strings` 在 GNU binutils 2.34 和 2.38 的預設 encoding 不同，對同一個 binary 的輸出可能不同。
- **時區設定**：`log2timeline.py` 在不同時區的系統上跑出來的 timestamp 如果沒有明確指定 `-z UTC`，結果會差好幾個小時。
- **隨機性**：某些機器學習型工具有隨機種子問題，每次跑結果略有差異。

報告中的「工具記錄」區塊必須包含：

```
工具名稱:  Volatility 3.2.0.0
指令:      python3 vol.py -f /evidence/mem.raw windows.cmdline.CmdLine
雜湊 (工具):  sha256(vol.py) = 8a3f...
執行時間:  2025-08-17 14:23:11Z
執行環境:  Ubuntu 22.04.3 LTS, Python 3.10.12
輸出檔案:  cmdline_output.txt  sha256 = 1b9e...
```

---

## 法律可採納性（Legal Admissibility）

### 臺灣刑事訴訟法框架

臺灣刑事訴訟法第 159 條規定，被告以外之人在審判外的陳述原則上不得作為證據（傳聞法則）。數位日誌、系統記錄適用第 159 條之 4 的「特信性文書」例外，但前提是該文書是「於通常業務過程中所為」且「有可信之特別情況」。

若系統日誌被人為修改過（即使只是 `atime` 更新），辯護方就可以主張「特別情況不可信」，法官可能排除這份證據。

### Daubert 標準（美國，供對照）

Daubert v. Merrell Dow Pharmaceuticals（1993）確立了法庭對科學鑑定方法的審查標準：

1. 該技術是否可被測試（testable）？
2. 是否經過同儕審閱（peer review）？
3. 已知或潛在的錯誤率？
4. 是否被相關科學社群廣泛接受？

在數位鑑識中，「使用 Autopsy 3.1.3 按標準程序提取 MFT 記錄」滿足 Daubert；「我憑直覺認為這個 timestamp 被修改過」不滿足。

### 什麼讓數位證據被排除

| 問題 | 為什麼被排除 |
|------|------------|
| 無 write blocker 直接接硬碟 | 無法排除採集過程中修改媒體的可能性 |
| hash 不一致 | 映像不是原始媒體的精確複製 |
| CoC 有空窗期 | 無法排除空窗期內的篡改 |
| 工具版本未記錄 | 結果不可重現，無法在法庭上獨立驗證 |
| 事實和推論混在一起 | 分析師的主觀判斷污染了客觀事實 |

---

## 鑑識報告結構

鑑識報告有兩個讀者群，需求完全不同：管理層要知道「發生了什麼、影響多大、要花多少錢」；技術／法律團隊要知道「每一個結論怎麼來的、用了什麼工具、hash 是多少」。把兩群人的需求寫在同一份文件裡，兩群人都看不懂。

標準結構：

```
鑑識分析報告
│
├── 執行摘要（Executive Summary）
│   ─ 針對管理層，1-2 頁
│   ─ 事件概述、時間線、受影響系統
│   ─ 業務影響（資料外洩量、停機時間）
│   ─ 關鍵建議（不超過 5 條）
│   ─ 不寫技術細節
│
├── 技術發現（Technical Findings）
│   ─ 針對 IR 團隊和法務
│   ─ 每個發現：事實（Fact）vs 推論（Inference）明確標籤
│   ─ 採集方法、工具版本、hash 值
│   ─ 原始命令輸出附件
│   ─ 分析方法論說明
│
├── 時間軸重建（Timeline Reconstruction）
│   ─ UTC 為基準，明確說明時區來源
│   ─ 事件 → 證據來源 → 可信度評級
│
├── IoC 彙整（Indicators of Compromise）
│   ─ 可直接餵給 SIEM/EDR 的格式（IP、hash、domain）
│
└── 附件
    ─ 採集記錄（hash 表、工具命令）
    ─ CoC 表單掃描件
    ─ 原始工具輸出
```

### 事實 vs 推論：必須嚴格區分

這是最常被忽略也最危險的地方。

**事實（FACT）**：

> "在 C:\Windows\Temp\svchost32.exe 找到可執行檔，SHA-256 為 3d4a...，建立時間（$STANDARD_INFORMATION）為 2025-08-16 03:12:44Z，$FILE_NAME 時間為 2025-08-16 03:12:44Z。"

**推論（INFERENCE）**：

> "攻擊者在 2025-08-16 凌晨植入了 svchost32.exe 作為持久化後門。"

推論本身沒問題，但必須標記它是推論，並給出推論依據：

> "[INFERENCE] 根據上述事實，以及 VirusTotal 分析顯示 svchost32.exe 符合 Cobalt Strike beacon 特徵（47/72 vendor 標記），推測攻擊者以此作為 C2 植入點。此推論依賴 VT 分析，若 VT 資料有誤則結論需修正。"

辯護方最喜歡的攻擊點，就是報告裡事實和推論混寫的段落。

### 時間軸格式

```
時間 (UTC)           | 來源                        | 事件                              | 可信度
─────────────────────────────────────────────────────────────────────────────────────────
2025-08-15 22:31:07  | Windows Event Log 4625       | jsmith 帳號 RDP 暴力破解 (×327)  | HIGH
2025-08-15 22:43:18  | Windows Event Log 4624       | jsmith 成功登入 (RDP)             | HIGH
2025-08-15 22:44:02  | Prefetch (SVCHOST32.EXE-*.pf)| svchost32.exe 首次執行            | MEDIUM *
2025-08-15 22:44:55  | Security Log 4698            | 新排程工作 "WindowsUpdateV2"      | HIGH
─────────────────────────────────────────────────────────────────────────────────────────
* Prefetch timestamp 可被 timestomping 偽造，與 $LOGFILE 交叉驗證後一致，可信度提升為 MEDIUM
```

---

## 具體案例 1：Windows 10 受害主機正確採集流程

情境：SOC 接到告警，內網 Windows 10 主機 `DESKTOP-0X3A7B9` 疑似遭入侵。主機目前仍在線上。

```
時間 09:00 — 現場確認
  ─ 拍攝螢幕（不碰鍵盤滑鼠），記錄當前螢幕狀態
  ─ 記錄主機序號、MAC address、IP（手寫，不開新工具）

時間 09:05 — 揮發性資料採集（以揮發性由高到低）
  ─ 執行 IRTriage.bat（只讀工具，預先部署到 USB）：
      ipconfig /all   → network_state.txt
      netstat -ano    → netstat.txt
      tasklist /v     → tasklist.txt
      reg query HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run → autoruns.txt
  ─ 記憶體 dump：
      winpmem_mini_x64_rc2.exe physmem.raw
      sha256sum physmem.raw > physmem.sha256

時間 09:40 — 磁碟採集
  ─ 關機（若主機含 BitLocker，先取得 Recovery Key 或 FVEK）
  ─ 接上 Tableau TD3 write blocker
  ─ FTK Imager → Add Evidence → Physical Drive → E01 格式，含 MD5 + SHA-256
  ─ 採集完成後 FTK Imager 顯示驗證通過（採集中自動 hash 比對）

時間 10:45 — 記錄 CoC
  ─ 填寫 CoC 表單：採集人簽名、媒體序號、hash 值、時間
  ─ 映像檔傳輸到鑑識伺服器，網路傳輸後再次 sha256sum 驗證
```

紅隊視角：你在這台機器上跑過的每一支工具，只要在揮發性資料採集的 09:05 之前終止，記憶體裡就沒有你的 cmdline 記錄。但 Prefetch、`$LogFile`、Amcache 這些磁碟上的執行記錄你沒辦法靠「時機」消除——那是後面 Ch 33 反鑑識的題目。

---

## 具體案例 2（失敗案例）：一個 `dir` 毀掉的採證

這是一個實際在資安事件中重演過的錯誤。

**情境**：2023 年某金融機構遭入侵，IT 人員到達現場後，第一個動作是在受害 Windows Server 上打開 CMD，輸入：

```cmd
dir C:\Users\Administrator\Downloads\
```

**發生了什麼**：

NTFS 在 Windows Server 2008 R2 之前預設開啟目錄的 `atime`（last access time）更新。`dir` 指令讀取了目錄內每個檔案的 metadata，導致：

1. `C:\Users\Administrator\Downloads\` 目錄的 `$STANDARD_INFORMATION` 中的 `Last Accessed` timestamp 被更新為操作時間。
2. 目錄下幾個子目錄同樣被更新。
3. 共約 40 個 metadata entry 的 access time 被覆寫。

**鑑識報告的影響**：

- 原本可以用「最後存取時間」建立的使用者活動時間軸出現矛盾——攻擊者在凌晨 2:00 操作，但 atime 卻顯示上班時間的 09:12。
- 辯護方以此主張「timestamp 不可信，整份時間軸需要重新評估」。
- 法務部門無法反駁，因為操作記錄確實顯示 IT 人員碰了這些檔案。

**教訓**：

第一個到達現場的人必須接受 DFIR 基本訓練，知道「不亂碰」規則。若真的需要查看目錄，使用只讀掛載（read-only mount）或鑑識工具（如 FTK Imager CLI `ftkimager /u` 掛載 E01 為 read-only）。對 SSD 上啟用 NTFSCompressionDisabledByDefault 的 Windows 11，即使 `dir` 可能因 Windows 8 後預設關閉 atime update 而安全，但你不能假設所有環境都如此。

---

## 比較表：Live Acquisition vs Dead Acquisition vs Remote Triage

| 維度 | Live Acquisition（在線採集） | Dead Acquisition（離線採集） | Remote Triage（遠端分類） |
|------|---------------------------|---------------------------|------------------------|
| 揮發性資料 | 可採集 RAM、網路狀態 | 無法取得（已消失） | 部分（取決於 agent 預部署） |
| 磁碟完整性 | OS 仍在寫入，有污染風險 | Write blocker 下，最乾淨 | 無法保證（遠端命令也會讀） |
| 法庭可信度 | 中（需詳細記錄採集步驟） | 高（最標準程序） | 低到中（高度依賴工具和記錄） |
| 速度 | 快（不需要關機拆機） | 慢（需要物理接觸） | 最快（無需人員到現場） |
| 適用情境 | 記憶體鑑識、inbound 事件 | 磁碟鑑識、法律案件 | 大規模事件初步排查 |
| 主要工具 | WinPmem、LiME、DumpIt | FTK Imager、dd + write blocker | Velociraptor、osquery |
| 風險 | OS 行為可能干擾採集結果 | 錯過揮發性資料 | 網路傳輸失敗、遠端 agent 不完整 |

在實際事件中，答案通常是**兩者都做**：先 live acquisition 搶揮發性資料，記錄採集動作，然後根據法律需求決定是否做 dead acquisition。

---

## 反鑑識預覽：動搖地基的技術

知道地基怎麼建，才能知道對手怎麼破壞它。

**Timestomping（時間戳偽造）**：攻擊者用 `SetFileTime`（Windows API）把植入的 `evil.exe` 的時間戳改成系統中某個舊檔案的時間，讓它藏在幾年前的舊檔案堆裡。`$STANDARD_INFORMATION` 可以被改，但 `$FILE_NAME` 通常比較難改（需要特殊工具），兩者不一致是重要的 IOC。

**日誌清除（Log Wiping）**：

```cmd
wevtutil cl System
wevtutil cl Security
wevtutil cl Application
```

清空 Windows Event Log，讓你的時間軸出現斷層。但 `wevtutil cl` 本身在還沒被清掉前會產生一個 Event ID 1102（Security log cleared）——這是攻擊者常見的失誤，也是防守方的偵測點。

**MFT 篡改**：直接修改 `$MFT`（NTFS Master File Table），讓某個檔案從目錄樹中消失，但實際資料仍在磁碟上。工具如 `NTFSUndelete` 仍可能找到這些檔案，因為 cluster bitmap 不一定同步被清除。

這些技術的詳細分析和偵測方法在 Ch 33 反鑑識對抗。這裡只需要知道：上面介紹的所有鑑識原則——hash 驗證、時間軸重建、事實與推論分離——都必須把「時間戳可能被偽造」納入考量，標記可信度，而不是盲目相信 metadata。

---

## 錯誤直覺 → 正確認識

**錯誤直覺**：「SHA-256 一樣就代表證據完整，我不需要其他紀錄。」
**正確認識**：Hash 只證明映像是原始磁碟的精確複製，不證明採集程序正確。若採集前沒有 write blocker，磁碟本身可能已被修改——而那次修改在 hash 裡看不出來。

**錯誤直覺**：「Log 被 `wevtutil cl` 清掉了，就沒有攻擊者的蹤跡了。」
**正確認識**：Windows Event Log 有多個備份路徑：SIEM 如果已在事件發生前接走日誌，本地清除無效。`$MFT`、Prefetch、AmCache、NTFS `$LogFile`、Volume Shadow Copy 都可能殘留攻擊痕跡，完整的鑑識不依賴單一來源。

**錯誤直覺**：「記憶體 dump 在拔除電源前做就好了。」
**正確認識**：揮發性資料採集必須在任何磁碟映像之前進行。磁碟採集通常需要關機（dead acquisition），關機的那一刻，記憶體內容就消失了。順序決定你能不能採到 RAM。

**錯誤直覺**：「我的分析結論寫在報告裡，讀報告的人會理解哪些是事實哪些是推論。」
**正確認識**：法律情境中，沒有明確標籤的段落預設被當作「分析師的主觀陳述」而非「客觀事實」，辯護方可以要求分析師在法庭上為每一句話作證。把事實和推論混在一起，等於把辯護的子彈送給對方。

**錯誤直覺**：「現場第一步先看一下目錄結構，確認有沒有可疑檔案。」
**正確認識**：現場的第一步是拍照（螢幕拍照，不觸碰任何輸入裝置）和記錄系統狀態，然後執行預先打包的只讀 triage 工具。任何「先看一下」的動作都可能污染 atime，破壞後續鑑識的可信度。

---

## 進階延伸

### 記憶體雜湊的特殊性

對磁碟雜湊的概念直接套用在記憶體有一個陷阱：記憶體內容在你做 dump 的過程中本身就在改變。DMA（Direct Memory Access）的 atomicity 問題——當你讀取第一個 4KB page 到最後一個 page 之間，OS 可能在其他 core 上繼續執行，改變其中某些 page 的內容。所以記憶體 dump 的 hash 嚴格說只是「dump 開始到結束這段時間採集到的一個快照的 hash」，不同於磁碟 hash 的語義。

工具如 WinPmem 有部分實作會試圖最小化這個視窗（停止所有 CPU 或使用 hyper-V snapshot），但無法完全消除。在報告中需要說明這個限制。

### Secure Hash Algorithm 與量子威脅

SHA-256 的 preimage resistance 在量子電腦下預估剩餘強度為 128 位元（Grover's algorithm 帶來平方根加速），目前仍被認為足夠。若你的案件涉及長期保存（10 年以上）的法庭證據，NIST 建議同時記錄 SHA-3-256，以備將來量子威脅成真時有替代驗證手段。

### E01/EWF 格式 vs raw（dd）

FTK Imager 和 EnCase 常用的 E01（Expert Witness Format）是一個容器格式，內建分塊、壓縮、和每個 chunk 的 CRC32 校驗——壞的 chunk 可以被定位而不影響其他部分。Raw（dd）輸出沒有這些保護，一旦映像檔本身損壞，你只能靠外部 hash 知道壞了，但不知道哪段壞了。

法庭接受度：E01 因為被廣泛使用和有開放的 libewf 文件，法庭接受度高。AFF4（Advanced Forensic Framework 4）是更新的開放標準，支援 multi-volume 和更好的 metadata 嵌入，但工具鏈還不如 E01 成熟。

---

## 本章重點整理

- **揮發性順序**：CPU 暫存器 → RAM → Swap → 網路狀態 → 行程 → 磁碟，採集優先順序是此順序的倒轉。
- **監管鏈（CoC）**：每次換手都要記錄誰、何時、做什麼，空窗期等於替辯護方開門。
- **Write blocker**：硬體 write blocker 是採集磁碟的最低標準；無 write blocker 的採集在法律上幾乎不可用。
- **Hash 驗證**：採集前後都算，MD5 僅用於相容，SHA-256 才是實際憑據，不一致必須記錄而非隱瞞。
- **鑑識可重現性**：工具版本、完整指令、執行環境、輸出 hash——全部要記錄。
- **法律框架**：臺灣刑訴 159-4、美國 Daubert，核心都是「驗證性」，程序有漏洞就可能被排除。
- **事實 vs 推論**：這是鑑識報告最容易犯錯的地方，混在一起寫等於把弱點送出去。
- **反鑑識的存在**：Timestomping、log wiping、MFT 篡改都在對付以上每一個原則，鑑識結論必須標記可信度而非假設 metadata 正確。

---

## 自我檢核

- [ ] 能夠不看筆記說出揮發性順序，並解釋每個層次「揮發」的物理原因
- [ ] 知道監管鏈表單需要記錄哪五類資訊，並說出空窗期的法律含義
- [ ] 能寫出完整的「dd 採集 + SHA-256 前後驗證」bash 指令序列
- [ ] 能夠區分硬體 write blocker 和軟體 write blocker 的可信度差異及原因
- [ ] 能夠舉例說明「事實」和「推論」在鑑識報告中的正確寫法
- [ ] 能夠解釋為什麼在 Windows Server 上執行 `dir` 可能破壞鑑識完整性
- [ ] 知道什麼條件下 Daubert 標準或臺灣刑訴第 159 條可能讓數位證據被排除

---

## 延伸閱讀

1. **SANS FOR508: Advanced Incident Response, Threat Hunting, and Digital Forensics** — SANS 旗艦 DFIR 課程的公開 cheat sheet 和部分教材，包含 Order of Volatility 和 acquisition workflow 的詳細操作手冊。這是業界實務標準的主要來源。

2. **NIST SP 800-86: Guide to Integrating Forensic Techniques into Incident Response** — NIST 官方文件，涵蓋採集程序、CoC 要求、法律注意事項，引用格式受政府和法庭認可。關注 Section 4（Data Collection）。

3. **The DFIR Report: Real Intrusions by Real Attackers** (https://thedfirreport.com) — 真實事件的完整技術報告，每篇都包含 TTP mapping、時間軸重建和 IOC 列表，是理解「真實鑑識報告長什麼樣」的最佳一手資料。

4. **RFC 3227: Guidelines for Evidence Collection and Archiving** — IETF 文件，確立了 Order of Volatility 的正式定義和採集指引，是後來所有 DFIR 教材引用的原始來源。短、精確、必讀。

5. **Carrier, Brian. *File System Forensic Analysis*** — 磁碟鑑識的技術聖經，詳細介紹 NTFS MFT、$FILE_NAME vs $STANDARD_INFORMATION、journal 等實際分析中會碰到的所有底層結構，是 Ch 7 磁碟鑑識的預習讀物。

---

記憶體採集和磁碟採集確立了「我有什麼可以分析」；下一章轉向「我能持續看到什麼」——從 Windows Event Log 到 Sysmon 到 Zeek，把防守側的感知能力從事後採集拉到即時遙測。

→ [Ch 5 遙測從哪來：log source 全景](./05-telemetry-log-sources.md)
