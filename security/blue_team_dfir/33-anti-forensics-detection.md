# Ch 33 — 反鑑識對抗

> 目標：理解攻擊者做反鑑識（anti-forensics）留下的殘跡，以及防守方如何從「清除動作本身」找回證據。核心結論只有一句：log 即時外送 SIEM，端點上再怎麼清都清不掉。

---

## 為什麼需要學反制

攻擊者做反鑑識的目的很明確：讓時間線（timeline）斷裂、讓調查變慢、讓歸因（attribution）失敗。timestomping（時間戳竄改）是為了讓惡意檔案看起來比系統還舊；log 清除是為了消滅 lateral movement 的腳印；secure delete 是為了讓 payload 不可還原。

防守方的反制邏輯也只有一個：**清除動作本身就是痕跡**。

你刪了 log，留下了刪除 log 的 Event ID。你刪了 $UsnJrnl，但 Volume Shadow Copy（VSS）裡的副本還在。你改了時間戳，但 NTFS 有兩組時間，你只能改其中一組。每一個反鑑識操作都是雙刃劍——執行它的代價是創造另一個 artefact。

這章的讀者已經懂得怎麼做攻擊端的反鑑識（來自 binary exploitation、red team 課程）。這裡從防守端把同樣的技術翻面看。

---

## 建立直覺

想像一個犯罪現場：嫌疑人帶走了凶器，擦掉了指紋，燒掉了監視錄影帶。但錄影帶的燃燒痕跡本身告訴警察「有人刻意破壞證據」；鄰居說「昨天 3 點有人在那邊」；消防報告記錄了起火時間。

數位鑑識的情況完全一樣。攻擊者的每個清除動作都在一個平行軌道上留下副本：SIEM、VSS、NTFS 的其他資料流、記憶體 dump。任務不是「恢復被刪掉的原始資料」，而是「從殘跡重建時間線」。

---

## 底層機制

### NTFS 的雙重時間戳系統

NTFS 每個檔案有兩組時間戳，存在不同的 attribute 裡：

- **$STANDARD_INFORMATION（$SI）**：四個時間（Created、Modified、MFT Modified、Accessed）。這組時間是 Win32 API `SetFileTime` 能改的，timestomping 工具全部針對這裡打。
- **$FILE_NAME（$FN）**：同樣四個時間，存在 $FILE_NAME attribute 裡。**只有 kernel 能更新這組**。普通的 timestomping 工具（包括 Metasploit 的 `timestomp` 模組、NirSoft BulkFileChanger）完全碰不到 $FN。

正常狀況下 `$SI.Created >= $FN.Created`（$SI 創建時間晚於或等於 $FN 創建時間，因為 $FN 是在 kernel 建立 directory entry 時寫的，$SI 在那之後同步）。

**偵測規則**：`$SI.Created < $FN.Created` → 攻擊者把 $SI 改成更早的時間，試圖讓檔案看起來很舊。

$UsnJrnl（USN change journal，NTFS 的操作日誌）記錄每次檔案操作的 reason code（FILE_CREATE、DATA_EXTEND、CLOSE 等），**時間戳來自系統時鐘，SetFileTime 影響不到它**。就算 $SI 被改到 2018 年，$UsnJrnl 的對應 record 時間戳仍然是操作當下的真實時間。

### Event Log 的自我記錄

Windows Event Log 在 Security log 被清除時，會在清除完成後立刻寫入 **Event ID 1102**（Security Log Cleared），操作者帳號也記錄在裡面。System log 被清除時寫 **Event ID 104**。

這個機制的設計意圖就是讓清除動作留下記錄。問題在於：如果攻擊者先清 1102 再清 Security log，這個 1102 也消失了。防守方的答案只有一個：**SIEM 即時收 log**。端點 log 一產生就被 Winlogbeat 或 NXLog 送出去，清掉本地的什麼都沒用。

Log gap analysis（間隙分析）：即使沒有 SIEM，你也可以看 log 的時間連續性。Security log 記錄從 14:00 到 14:47 密密麻麻，然後 14:47 到 16:30 完全空白，然後從 16:30 繼續——這個空白本身就是異常，加上 Event ID sequence 跳號（record number 不連續），基本可以確認中間有清除。

### $UsnJrnl 的替代痕跡

`fsutil usn deletejournal /D C:` 或直接截斷 `$Extend\$UsnJrnl`：攻擊者刪掉 USN journal，讓你沒有操作記錄。

殘跡在哪裡？

1. **Journal ID 重置**：USN journal 刪除後重建，journal ID（64-bit identifier）改變。對比前後的 journal ID，斷層就是刪除點。
2. **$LogFile 的 LSN**：NTFS transaction log（$LogFile）裡的 LSN（Log Sequence Number）序列仍有殘跡，但 $LogFile 是 circular buffer，持久性比 $UsnJrnl 短，會被後續操作覆寫。
3. **VSS 裡的 $UsnJrnl**：Volume Shadow Copy 是在 VSS writer 呼叫之前的快照。`fsutil usn deletejournal` 不會影響已存在的 VSS 快照，除非攻擊者也執行 `vssadmin delete shadows /all /quiet`。從 VSS 快照掛載後，用 MFTECmd 直接解析裡面的 `$Extend\$UsnJrnl`，可以還原完整的操作序列。

---

## 實例一：Timestomping 偵測

攻擊者在 2024-08-01 14:22 投放 `svchost32.exe` 到 `C:\Windows\Temp\`，然後用 timestomping 把 $SI 時間改成 2018-03-15 09:00:00，試圖混進系統檔案的時代感。

用 MFTECmd（Eric Zimmerman 工具集）解析 $MFT：

```
MFTECmd.exe -f "C:\$MFT" --csv C:\output --csvf mft_output.csv
```

輸出片段（示意，依樣本而異）：

```
EntryNumber | FileName      | SI_Created          | SI_Modified         | FN_Created          | FN_Modified
------------|---------------|---------------------|---------------------|---------------------|--------------------
84921       | svchost32.exe | 2018-03-15 09:00:00 | 2018-03-15 09:00:00 | 2024-08-01 14:22:11 | 2024-08-01 14:22:11
```

`SI_Created (2018)` 早於 `FN_Created (2024)` → 明確 timestomping。

再對照 $UsnJrnl 的同一個檔案 record（示意，依樣本而異）：

```
Usn          | Timestamp           | Reason                        | FileName
-------------|---------------------|-------------------------------|---------------
9876543200   | 2024-08-01 14:22:09 | FileCreate                    | svchost32.exe
9876543400   | 2024-08-01 14:22:10 | DataExtend | Close             | svchost32.exe
9876543600   | 2024-08-01 14:22:11 | BasicInfoChange | Close         | svchost32.exe
```

最後一筆 `BasicInfoChange` 就是 timestomping 的 $SI attribute 寫入，時間戳仍是真實時間 14:22:11。三個 artefact 互相印證：$SI 是假的，$FN 和 $UsnJrnl 說的是同一個真實時間。

失敗案例：如果攻擊者用了 kernel driver（例如直接寫 $FILE_NAME attribute，透過 NtSetInformationFile 的 undocumented level），$FN 也可以被改。這時候只剩 $UsnJrnl 的 BasicInfoChange record 時間戳。如果 $UsnJrnl 也被清了，而且沒有 VSS，鑑識就真的破功了。進階紅隊會這樣打。防守方對策：讓 SIEM 收到檔案創建時的 Sysmon Event ID 11（FileCreate）——Sysmon 在檔案創建當下就記錄，後續的修改改不了這筆。

---

## 實例二：Event Log 清除鏈

攻擊者在 15:47 清除 Security log，試圖消滅 15:30 的 Event ID 4624（成功登入）。

如果沒有 SIEM，本地 Security log 被清後只剩一筆 1102：

```
Event ID: 1102
Source:   Microsoft-Windows-Eventlog
Time:     2024-08-01 15:47:33
Message:  The audit log was cleared.
          Subject:
            Security ID: DOMAIN\attacker_account
            Account Name: attacker_account
```

4624 消失了，但 gap analysis 告訴你：

1. Security log 最早的 record 時間是 15:47:33（1102 本身）
2. System log 裡 15:30 到 15:47 之間有正常的 kernel event（6005 system start 在 15:28）
3. Sysmon log 還記錄著 15:30:12 的 Process Create（winlogon.exe → cmd.exe）
4. 結論：15:30 到 15:47 的 Security log 被消滅，攻擊者帳號明確

如果有 SIEM（例如 Splunk 接了 Winlogbeat）：

```splunk
index=windows EventCode=4624 Account_Name=attacker_account earliest=15:25 latest=15:50
```

4624 在 SIEM 裡完整存在，端點上的清除什麼用都沒有。

攻擊者更進一步：用 `EvtDeleteRecord`（undocumented API，需要直接操作 .evtx 檔案的二進位結構）只刪特定 record 而不觸發 1102。偵測：.evtx 檔案大小驟降（正常 Security log 是 20MB 設定上限，突然剩 2MB）、record number 跳號（record 5000 直接跳 5100，中間 100 筆消失）、evtx 檔案的 CRC 校驗值異常（Log-MD、Chainsaw 可以偵測）。

---

## 實例三：$UsnJrnl 清除後的鑑識

攻擊者在入侵後執行：

```cmd
fsutil usn deletejournal /D C:
vssadmin delete shadows /all /quiet
```

兩個防線都被打掉了。但環境有定期備份（例如 Windows Server Backup 每天 02:00 備份到網路位置）。

從 02:00 的備份掛載 VSS 快照，或直接從備份磁碟提取 `C:\$Extend\$UsnJrnl`，用 MFTECmd 解析（示意，依樣本而異）：

```
Usn          | Timestamp           | Reason                              | FileName
-------------|---------------------|-------------------------------------|-------------------
12340000     | 2024-08-01 03:14:22 | FileCreate                          | beacon.dll
12340200     | 2024-08-01 03:14:23 | DataExtend | Close                   | beacon.dll
12340400     | 2024-08-01 03:14:24 | BasicInfoChange | Close              | beacon.dll
...
12890000     | 2024-08-01 15:46:11 | FileDelete | Close                   | beacon.dll
12890200     | 2024-08-01 15:46:55 | FileDelete | Close                   | beacon_stage2.dll
12890400     | 2024-08-01 15:47:01 | (journal ends — deletejournal at ~15:47)
```

從備份的 $UsnJrnl 還原了完整操作序列：03:14 投放 beacon.dll，15:46 清理，15:47 刪 journal。時間線重建完成。

**限制**：如果備份也被打掉，或者備份頻率太低（上一次備份在攻擊者進來之前很久），這個方案就失敗。這就是為什麼高成熟度環境用 SIEM 即時收 Sysmon FileCreate/FileDelete（Event ID 11/23），不依賴磁碟備份。

---

## 各反鑑識技術對比表

| 技術 | 操作 | 留下的殘跡 | 偵測難度 | 防守方最強防線 |
|------|------|------------|----------|---------------|
| Timestomping ($SI) | SetFileTime API | $FN 時間、$UsnJrnl BasicInfoChange | 低 | MFTECmd 比對 $SI vs $FN |
| Timestomping ($FN) | kernel driver / exploit | $UsnJrnl timestamp | 中 | Sysmon Event 11 即時記錄 |
| Security log 清除 | wevtutil / ClearEventLog | Event ID 1102、SIEM 已收 | 低（有 SIEM）| SIEM 即時 forward |
| 特定 record 刪除 | EvtDeleteRecord | 檔案大小、record 跳號、CRC | 中 | Chainsaw / Log-MD 掃 .evtx |
| $UsnJrnl 刪除 | fsutil usn deletejournal | journal ID 重置、$LogFile LSN | 中 | VSS 快照、備份 |
| VSS 刪除 | vssadmin delete shadows | 需要 admin，ETW 有記錄 | 中 | SIEM 收 vssadmin 執行 event |
| Secure delete | sdelete / Eraser | $MFT entry 殘留（filename/size）、$UsnJrnl 覆寫序列 | 高（內容不可還原）| 內容丟了，但行為可見 |
| SSD + TRIM | 自動 | 基本無法物理恢復 | 極高 | 放棄磁碟恢復，依賴行為 log |
| 記憶體清零 shellcode | memset + VirtualFree | ETW memory allocation event、EDR on-write hook | 中（窗口短）| EDR 即時掃 MEM_EXECUTE 頁 |
| VM/Sandbox 偵測躲避 | CPUID check、timing | 蜜罐觸發、物理機 sandbox | 中 | 模擬使用者行為的 sandbox |

---

## Secure Delete 與 SSD 的現實

工具多次覆寫（sdelete -p 3）在 HDD 上理論上讓資料不可還原。偵測的重點不在恢復內容，而在行為：

- **$MFT entry 殘留**：檔案被刪除後，$MFT 裡的 entry 只是把 in-use flag 清掉，entry 本身還在。你拿得到 filename、時間戳、file size、parent directory——只是內容沒了。`MFTECmd -f $MFT --csv output` 加 `--dedupe` 就能列出所有已刪除但 entry 殘留的檔案。
- **$UsnJrnl 的覆寫序列**：sdelete 實際上是重複 FILE_CREATE + DATA_EXTEND 然後 FILE_DELETE。$UsnJrnl 裡會看到同一個 inode 被反覆 DataExtend + DataTruncate 的奇怪序列，這個 pattern 本身就是 secure delete 的特徵。
- **SSD 的 TRIM**：SSD 收到 TRIM 命令後，flash cell 直接清零，物理恢復基本不可能。這是 SSD 的正常機制，不是攻擊者做了什麼特別的事。在 SSD 環境，放棄期待「恢復刪除的內容」，把全部注意力放在行為 log（Sysmon、EDR、SIEM）。

---

## 記憶體反鑑識的偵測窗口

攻擊者在記憶體裡跑完 shellcode 後，用 `memset` 清零 shellcode buffer，然後 `VirtualFree` 釋放 MEM_EXECUTE 頁。事後做記憶體 dump，什麼都看不到。

偵測的關鍵是**在寫入時觸發，而不是讀取時**：

- **ETW（Event Tracing for Windows）的 memory allocation event**：`Microsoft-Windows-Kernel-Memory` provider 記錄 VirtualAlloc 的 type（MEM_COMMIT + PAGE_EXECUTE_READWRITE）。攻擊者分配可執行記憶體的當下就被記錄，memset 之後這筆記錄永遠存在 ETW buffer 裡。
- **EDR 的 on-write hook**：AV/EDR 在 `VirtualAlloc(PAGE_EXECUTE_READWRITE)` 或 `VirtualProtect` 改成可執行時掃描記憶體內容。掃描在攻擊者寫入時發生，不等到執行或事後 dump。
- **Process memory snapshot**：Windows Defender 的 Antimalware Scan Interface（AMSI）在 script 執行前掃描，捕捉的是 「執行前一刻」的記憶體，不是事後。

---

## VM/Sandbox 偵測的反制

攻擊者的 malware 用各種 fingerprint 偵測分析環境：

- `CPUID leaf 0x1`：hypervisor bit（bit 31 of ECX）在 VM 裡被設定
- Registry 鍵（`HKLM\SOFTWARE\VMware Inc.\VMware Tools`）
- 特定 process 名（vmtoolsd.exe、VBoxService.exe）
- RDTSC timing attack：在 native 環境 RDTSC 差值是幾個 cycle，在 VM 裡因為 VM-exit overhead 會大很多
- Mouse entropy：sandbox 通常鼠標不動，或只有程式化的機械移動
- Username 是 "admin"、"user"、"sandbox"、"malware" 等

防守方的反制：

1. **物理機 sandbox**：用裸機跑分析，CPUID hypervisor bit 自然不會被設定，RDTSC 正常。
2. **模擬真實使用者行為**：Cuckoo sandbox 有 human simulation plugin，模擬滑鼠移動、瀏覽器開啟、文件點擊。
3. **蜜罐（honeypot）**：讓 malware 以為進了真實環境，觸發 payload，在觸發後分析行為。
4. **移除 VM fingerprint**：把 VMware/VirtualBox 的 registry key、process 名、驅動名都改掉，讓 VM 偽裝成物理機。

---

## 記憶體與 VSS 的進階補充

### Pagefile 與 Hibernation File

攻擊者跑完就清記憶體，但 Windows 的 `pagefile.sys` 和 `hiberfil.sys` 可能在攻擊者有機會清之前就已把記憶體頁交換出去或 hibernate 寫入磁碟。鑑識工具（Volatility、Rekall）可以從這兩個檔案重建當時的記憶體狀態。

這個窗口依賴時機：如果系統在攻擊期間沒有 swap 或 hibernate，這條線索就沒有。

### VSS 的保護策略

VSS 快照在高成熟度環境裡不只是鑑識工具，也是防線：

- 用 Group Policy 禁止非管理員執行 `vssadmin`
- 用 SIEM 監控 `vssadmin delete shadows` 的執行（Sysmon Event ID 1 + command line 過濾）
- 把 VSS 快照複製到攻擊者無法存取的外部位置（offline backup）

攻擊者必須先提權、再刪 VSS、再清 $UsnJrnl，每一步都是額外的 exposure window。

---

## 踩雷

**Event ID 1102 vs 104 搞混**：1102 是 **Security** log 清除，需要 SeSecurityPrivilege（通常是管理員或 audit 權限）。104 是 **System** log 清除，一般管理員就能做。防守方在 Sigma rule 裡兩個都要覆蓋，很多人只寫 1102。

**$FN 早於 $SI 才是異常，不是反過來**：$FN 是 kernel 在 directory entry 建立時寫的，先於 $SI 設定。正常情況是 $SI >= $FN。timestomping 把 $SI 改成更早的時間，讓 $SI < $FN，這才是 flag。第一次看不直覺，背下來。

**SSD + TRIM 讓傳統磁碟恢復完全失效**：`sdelete -p 35`（Gutmann method）在 SSD 上只是在浪費時間，TRIM 本來就把資料清乾淨了。鑑識人員不要對 SSD 上的 secure delete 抱有「或許還能恢復點什麼」的期待，要改成看行為 log。

**VSS 刪除是前置動作，不是事後清理**：進階的攻擊者在開始主要操作之前先刪 VSS（ransomware 也這樣）。如果你看到 `vssadmin delete shadows` 的時間比其他 artefact 都早，這是計畫性清理，不是事後補救。推論攻擊者的手法成熟度。

**memset 清零不等於 EDR 沒看到**：ETW 和 EDR 的 hook 觸發在寫入 MEM_EXECUTE 頁的當下，不是在你讀取記憶體的時候。memset 之前 EDR 已經掃過了。攻擊者唯一的機會是在 EDR hook 還沒建立之前跑（例如 early-boot 階段），或者直接 unhook EDR（但那本身又留下痕跡）。

---

## 本章重點整理

- NTFS 有兩組時間戳：$SI（SetFileTime 可改）和 $FN（只有 kernel 能改）。$SI < $FN 是 timestomping 的明確特徵。
- $UsnJrnl 的時間戳來自系統時鐘，SetFileTime 影響不到，是時間戳真實性的最後防線。
- Event ID 1102（Security log 清除）和 104（System log 清除）是 Windows 自帶的反清除機制，但 SIEM 即時 forward 才是真正的保障。
- Log gap analysis 從時間斷層和 record number 跳號偵測選擇性刪除。
- $UsnJrnl 被刪後，VSS 快照是備用來源；VSS 也被刪後，SIEM 和 Sysmon 是最後防線。
- SSD + TRIM 讓磁碟物理恢復基本失敗，鑑識重心轉移到行為 log。
- 記憶體清零的偵測窗口在寫入時，EDR 和 ETW 的 hook 不依賴事後 dump。
- 每個反鑑識操作都創造新的 artefact；防守方的任務是找「清除動作本身的痕跡」。

---

## 自我檢核

- [ ] 我能說出 $SI 和 $FN 的差別，以及為什麼 $SI < $FN 是異常
- [ ] 我知道用什麼工具比對 $SI vs $FN（MFTECmd）
- [ ] 我能說明 $UsnJrnl 的 BasicInfoChange record 在 timestomping 後的意義
- [ ] 我能解釋 Event ID 1102 和 104 分別對應哪個 log，以及為什麼 SIEM 比本地記錄更可靠
- [ ] 我知道 log gap analysis 的兩個指標（時間斷層、record number 跳號）
- [ ] 我能說明 $UsnJrnl 被刪後的替代痕跡來源（VSS、$LogFile、備份）
- [ ] 我能解釋為什麼 SSD + TRIM 讓磁碟覆寫恢復失效
- [ ] 我知道 memset 清零 shellcode 之前 EDR hook 已經觸發的原理
- [ ] 我能說出 VM/Sandbox 偵測的常見技術和防守方的對應反制
- [ ] 我能正確說出「$SI 早於 $FN 是異常」而不說反

---

## 延伸閱讀

1. **Eric Zimmerman, "MFTECmd Documentation"（GitHub）** — 直接看工具的 README 和 --csv 輸出格式，理解 $MFT 解析的欄位定義。理解 $SI vs $FN 之後，這是實際動手的起點。

2. **Joakim Schicht, "Timestomping" (GitHub: jschicht/SetMace)** — 展示如何用 kernel driver 修改 $FN 時間戳，這是 timestomping 的進階手法。讀完你會知道「$FN 也能改」這件事的技術細節，從而理解為什麼 $UsnJrnl 才是最後的防線。

3. **Florian Roth, "Chainsaw" (GitHub: WithSecureLabs/chainsaw)** — 針對 Windows Event Log 的快速 triage 工具，內建 Sigma rule 引擎，偵測 record 跳號和 CRC 異常。這是 Ch 18 Event Log forensics 的實戰工具延伸。

4. **Black Hat 2012, "Forensic Implications of a Rootkit" (Strzempka)** — 雖然稍舊，但清楚解釋 kernel-level timestomping 和 $FN 修改的可能性，以及當時有限的偵測方案。了解邊界情況的歷史背景。

5. **Volatility Foundation, "Memory Forensics Cheat Sheet"** — 記憶體鑑識的工具參考，特別是從 pagefile.sys 和 hiberfil.sys 重建記憶體狀態的 plugin。補充本章記憶體反鑑識部分的實作細節。

---

→ [Ch 34 偵測工程的盲點](./34-detection-blind-spots.md)
