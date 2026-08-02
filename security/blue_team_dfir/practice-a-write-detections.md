# 練習 A — Purple Team 全流程：對已知攻擊寫偵測

> 目標：對 T1003.001（LSASS Credential Dumping）走一次完整的 purple team 流程：描述攻擊手法 → 分析遙測 → 寫 Sigma 規則 → 寫 YARA 規則 → 列假陽性與調校 → 對映 ATT&CK。

## 背景動機

你知道怎麼用 Mimikatz、procdump、comsvcs.dll 轉儲 LSASS。但你知道防守方在這段期間看到什麼嗎？

這個練習逼你換位置思考。不是「我怎麼打」，而是「**我這樣打，防守方的 SIEM 裡會出現什麼 event、哪條規則會命中、哪個地方會漏掉**」。

完成這個練習後，你同時理解攻擊和偵測——這才是 purple team 的意義。

## 任務規格

你要完成以下六個部分，可以用文字說明 + 程式碼混合：

**Part 1**：描述 T1003.001 的三種主要攻擊手法（各 3-5 行）

**Part 2**：分析每種手法在 Windows 會產生什麼遙測（列具體 Event ID、Sysmon Event、欄位）

**Part 3**：寫一條 Sigma 規則，涵蓋至少兩種手法

**Part 4**：寫一條 YARA 規則，偵測 Mimikatz 或 comsvcs dump 的記憶體/磁碟特徵

**Part 5**：列出假陽性來源，並說明調校策略

**Part 6**：ATT&CK 對映——這條規則位於哪個 tactic/technique/sub-technique，需要哪些 data source

## 期望輸出格式

### Part 1 — 攻擊手法描述（示意）

```
手法 1：Mimikatz sekurlsa::logonpasswords
  攻擊者在取得管理員權限後，執行 mimikatz.exe 並呼叫
  sekurlsa::logonpasswords，從 lsass.exe 的記憶體直接
  讀出 plaintext 密碼、NTLM hash、Kerberos ticket。

手法 2：...
```

### Part 3 — Sigma 規則（示意）

```yaml
title: ...
detection:
    selection:
        ...
    condition: selection
```

### Part 4 — YARA 規則（示意）

```yara
rule ... {
    strings:
        ...
    condition:
        ...
}
```

## 如果你卡住了

**卡在 Part 2（不知道產生什麼遙測）**：
- Sysmon Event 10（Process Access）是 LSASS 最關鍵的 event：當任何程序嘗試開啟 lsass.exe 的 handle，Sysmon 記錄 source process、target process、GrantedAccess mask。
- procdump 的 GrantedAccess 通常包含 `0x1fffff`（PROCESS_ALL_ACCESS）或 `0x1010`。
- PowerShell/cmd 的 parent-child 關係：Mimikatz 通常是 cmd.exe 的 child。
- Windows Security Event 4656（A handle to an object was requested）在高 audit 設定下也會記錄。

**卡在 Part 3（Sigma 語法）**：
- 參考 Ch 8 的 Sigma 語法，特別是 `contains|all` 用於要求多個字串同時出現在同一個欄位。
- Sigma 的 `logsource.category: process_access` 是 Sysmon Event 10 的標準類別。
- 試著從 SigmaHQ 的官方規則庫搜尋 `lsass`，看別人怎麼寫，然後用你自己的理解重寫。

**卡在 Part 4（YARA 語法）**：
- 回顧 Ch 9 的規則範本，特別是 `pe.imports()` 查匯入函式、`$s wide` 對 UTF-16 字串。
- Mimikatz 的特徵字串：`"sekurlsa"`, `"kiwi_cmd"`, `"wdigest.dll"`, `"LsaConnectUntrusted"`（這是 sekurlsa 模組呼叫的 API）。
- comsvcs.dll MiniDump 不是 PE 偵測的好對象（它本身是合法 DLL），用 YARA 偵測呼叫它產生的 dump 檔案特徵。

**卡在 Part 5（假陽性分析）**：
- 想想哪些合法工具或工作流程也會存取 lsass.exe——AV、EDR、ProcDump for crash analysis、Windows Error Reporting。
- 哪些 IT admin 操作也會觸發你寫的 Sigma 條件？

## 分段實作建議

### Step 1：研究攻擊手法（30 分鐘）

從你熟悉的攻擊課知識出發，列出 T1003.001 的三種手法：
1. Mimikatz（sekurlsa::logonpasswords）
2. procdump（`procdump -ma lsass.exe`）
3. comsvcs.dll MiniDump（`rundll32 comsvcs.dll MiniDump <PID> out.dmp full`）

對每種手法，問自己：**這個動作在 Windows 裡是怎麼實現的**（API 呼叫、privilege 要求）。這個理解會決定遙測的形狀。

### Step 2：對映遙測（30 分鐘）

用表格整理：

| 手法 | 觸發的 Sysmon Event | 觸發的 Windows Security Event | 關鍵欄位 |
|---|---|---|---|
| Mimikatz | ... | ... | ... |
| procdump | ... | ... | ... |
| comsvcs MiniDump | ... | ... | ... |

填這張表前，想想：
- 哪個 Sysmon event 捕捉「程序 A 存取程序 B 的記憶體」？
- 哪些 GrantedAccess flag 代表「讀取記憶體」？（提示：`0x10`=PROCESS_VM_READ）

### Step 3：寫 Sigma 規則（45 分鐘）

先寫最明顯的情況（procdump 命令列），測試語法後再擴充到 Sysmon Event 10 的 GrantedAccess 條件。

規則要有完整的 `id`（UUID v4）、`status`、`level`、`tags`（包含 `attack.t1003.001`）。

用 `sigma check` 驗證語法（如果有安裝 sigma-cli），或至少肉眼確認格式符合 Sigma spec。

### Step 4：寫 YARA 規則（30 分鐘）

選擇以下其中一個目標：
- 偵測磁碟上的 Mimikatz 二進位（用 `sekurlsa`、`kiwi_cmd`、imphash）
- 偵測 lsass dump 檔案（MiniDump magic bytes：`MDMP` = `4D 44 4D 50`）

提示：lsass dump 檔案開頭 4 bytes 是 `MDMP`（MiniDump format），你可以寫一條抓所有 MiniDump 且大於某個 filesize 的規則，因為 lsass dump 通常 > 50 MB。

### Step 5：假陽性分析與調校（20 分鐘）

列出至少 3 個假陽性來源，並對每個說明調校方法（加例外條件、提高閾值、要求多條件同時命中）。

### Step 6：ATT&CK 對映（10 分鐘）

填完這張表：

| 欄位 | 值 |
|---|---|
| Tactic | ? |
| Technique | T1003 Credential Dumping |
| Sub-technique | T1003.001 LSASS Memory |
| 需要的 Data Source | ? |
| 偵測涵蓋 Layer（0/1/2） | ? |

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考解答</summary>

### Part 1 — 攻擊手法描述

**手法 1：Mimikatz sekurlsa::logonpasswords**

攻擊者在取得 `SeDebugPrivilege` 後，執行 `mimikatz.exe`，呼叫 `sekurlsa::logonpasswords`。Mimikatz 透過 `OpenProcess(PROCESS_ALL_ACCESS, ...)` 開啟 lsass.exe 的 handle，再用 `ReadProcessMemory` 讀取 WDigest、Kerberos、NTLM 等認證提供者在記憶體中存放的明文密碼與 hash。需要管理員或 SYSTEM 權限。

**手法 2：procdump + Minidump**

使用 Sysinternals 的 procdump：`procdump64 -accepteula -ma lsass.exe lsass.dmp`。procdump 呼叫 `MiniDumpWriteDump` API，合法地把 lsass.exe 的完整記憶體寫成 .dmp 檔。攻擊者再把 dump 傳回自己的機器，用 Mimikatz 的 `sekurlsa::minidump lsass.dmp` 離線解析，規避 EDR 的 lsass 記憶體掃描（因為解析在攻擊者機器上進行）。

**手法 3：comsvcs.dll MiniDump export**

`comsvcs.dll` 是 Windows 系統 DLL，其中有一個未記錄的 `MiniDump` export function。攻擊者用 `rundll32.exe C:\Windows\System32\comsvcs.dll MiniDump <lsass PID> lsass.dmp full` 執行，效果和 procdump 相同，但全程使用 Windows 內建工具（LOLBin），規避需要外部工具的偵測規則。

---

### Part 2 — 遙測對映

| 手法 | Sysmon Event | Windows Security Event | 關鍵欄位 |
|---|---|---|---|
| Mimikatz | Event 10（Process Access）：source=mimikatz.exe, target=lsass.exe<br>Event 1（Process Create）：mimikatz.exe 被 spawned | 4656（handle 請求）<br>4688（process creation，需開啟 command line audit） | GrantedAccess=0x1FFFFF 或含 0x10（VM_READ）<br>CallTrace 含 `Dbgcore.dll` |
| procdump | Event 10：source=procdump.exe, target=lsass.exe<br>Event 11（File Create）：.dmp 檔案被建立 | 4656<br>4688：CommandLine 含 `-ma lsass` | GrantedAccess=0x1FFFFF<br>TargetImage=lsass.exe<br>TargetFileName=*.dmp |
| comsvcs MiniDump | Event 10：source=rundll32.exe, target=lsass.exe<br>Event 1：rundll32.exe CommandLine 含 comsvcs + MiniDump | 4656<br>4688 | GrantedAccess 含 0x10<br>CommandLine 含 "comsvcs" AND "MiniDump" AND lsass PID |

---

### Part 3 — Sigma 規則

```yaml
title: LSASS Memory Credential Dumping - Multiple Methods
id: a9b3e27c-1f4d-4b8a-9c2e-7d6f5e3a1b09
status: stable
description: |
    偵測透過多種方式對 lsass.exe 執行記憶體轉儲的行為，涵蓋
    procdump 命令列特徵以及任意程序以高 GrantedAccess 存取 lsass。
references:
    - https://attack.mitre.org/techniques/T1003/001/
    - https://lolbas-project.github.io/lolbas/Libraries/Comsvcs/
author: blue-team-analyst
date: 2025/01/15
tags:
    - attack.credential_access
    - attack.t1003.001
logsource:
    product: windows
    category: process_access
detection:
    # 方法 A：任何程序以 VM_READ|VM_OPERATION 存取 lsass
    selection_lsass_access:
        TargetImage|endswith: '\lsass.exe'
        GrantedAccess|contains:
            - '0x1fffff'
            - '0x1010'
            - '0x143a'
    # 方法 B：排除已知合法工具（allowlist）
    filter_legit:
        SourceImage|endswith:
            - '\MsMpEng.exe'        # Windows Defender
            - '\SenseIR.exe'        # Microsoft Defender for Endpoint
            - '\csrss.exe'          # Windows 核心程序
            - '\werfault.exe'       # Windows Error Reporting
    condition: selection_lsass_access and not filter_legit
---
# 第二條規則：procdump / comsvcs 命令列特徵
title: LSASS Dump via Procdump or comsvcs MiniDump
id: 3c7f2d1e-8a5b-4c9d-b6e3-2f1a9c8d7b04
status: stable
description: 偵測 procdump 針對 lsass 以及 rundll32 呼叫 comsvcs.dll MiniDump。
tags:
    - attack.credential_access
    - attack.t1003.001
logsource:
    product: windows
    category: process_creation
detection:
    selection_procdump:
        Image|endswith:
            - '\procdump.exe'
            - '\procdump64.exe'
        CommandLine|contains|all:
            - 'lsass'
    selection_comsvcs:
        Image|endswith: '\rundll32.exe'
        CommandLine|contains|all:
            - 'comsvcs'
            - 'MiniDump'
    condition: selection_procdump or selection_comsvcs
falsepositives:
    - 合法的 crash dump 收集工具（需要 allowlist）
    - 安全研究人員在測試機上的操作
level: high
```

---

### Part 4 — YARA 規則

```yara
rule Mimikatz_sekurlsa_Strings {
    meta:
        description = "Mimikatz sekurlsa 模組核心字串特徵"
        author      = "blue-team-analyst"
        date        = "2025-01-15"
        reference   = "https://github.com/gentilkiwi/mimikatz"
        tlp         = "WHITE"

    strings:
        // sekurlsa 模組特有字串
        $s1 = "sekurlsa" ascii nocase
        $s2 = "kiwi_cmd" ascii
        $s3 = "wdigest.dll" ascii wide
        $s4 = "kuhl_m_sekurlsa_acquireKeys" ascii

        // Mimikatz 必然匯入的函式
        $imp1 = "LsaConnectUntrusted" ascii
        $imp2 = "LsaLookupAuthenticationPackage" ascii
        $imp3 = "LsaCallAuthenticationPackage" ascii

        // 常見輸出字串
        $out1 = "Username :" ascii wide
        $out2 = "* Username :" ascii wide
        $out3 = "NTLM     :" ascii wide

    condition:
        uint16(0) == 0x5A4D and          // PE magic
        filesize < 10MB and
        (
            (2 of ($s*)) or
            (all of ($imp*)) or
            (2 of ($out*) and 1 of ($s*))
        )
}

rule LSASS_MiniDump_File {
    meta:
        description = "Windows MiniDump 檔案且大小 > 50MB（疑似 LSASS dump）"
        author      = "blue-team-analyst"
        date        = "2025-01-15"

    strings:
        $minidump_magic = { 4D 44 4D 50 }    // "MDMP" — MiniDump 格式 magic

    condition:
        $minidump_magic at 0 and
        filesize > 50MB
}
```

LSASS MiniDump 通常 50–200 MB（取決於系統記憶體），用 filesize 過濾可以大幅降低誤判磁碟上其他 crash dump 的機率。

---

### Part 5 — 假陽性分析與調校

| 假陽性來源 | 為什麼觸發 | 調校方式 |
|---|---|---|
| Windows Defender (MsMpEng.exe) | AV 需要存取 lsass 做 memory scanning | `filter_legit` allowlist 加 MsMpEng.exe |
| Microsoft Defender for Endpoint (SenseIR.exe) | EDR agent 定期讀取 lsass 狀態 | allowlist SenseIR.exe、SenseCE.exe |
| WER / werfault.exe | Windows Error Reporting 在程序 crash 時建 minidump，包括 lsass crash | 加 GrantedAccess 篩選：排除 WER 典型的 `0x0400` |
| IT admin 用 ProcDump 收集 crash report | 合法的 crash dump 工作流程 | 加 CommandLine 條件：只在 CommandLine 含 "lsass" 時告警，而非所有 procdump 執行 |
| Backup agent / ITAM 工具 | 某些 endpoint management 工具開啟 lsass handle 做 process 盤點 | 聯繫廠商確認 binary path，加 allowlist |

**調校策略總結**：
1. 先用 Sigma `filter_legit` 加 SourceImage allowlist 排除已知良性工具
2. 第一週把規則設 shadow mode（只記 log 不告警），收集假陽性清單
3. 加 asset context：C-level 高管機器的 lsass access 比 developer workstation 更值得告警
4. 考慮 GrantedAccess bitmap 精確比對而非 `contains`，減少 bitmask 誤判

---

### Part 6 — ATT&CK 對映

| 欄位 | 值 |
|---|---|
| Tactic | TA0006 Credential Access |
| Technique | T1003 OS Credential Dumping |
| Sub-technique | T1003.001 LSASS Memory |
| 需要的 Data Source | Process: Process Access（Sysmon Event 10）<br>Process: Process Creation（Sysmon Event 1 / Security 4688）<br>File: File Creation（Sysmon Event 11，偵測 .dmp 檔）<br>Command: Command Execution（PowerShell / cmd 記錄） |
| 偵測涵蓋 Layer（0/1/2） | Layer 0：需要 Sysmon 部署（Process Access）<br>Layer 1：有 Sigma 規則（已完成）<br>Layer 2：需要 Atomic Red Team T1003.001 驗證命中 |
| 已知規避手法 | PPL（Protected Process Light）讓一般程序無法開啟 lsass handle<br>Dump 後離線解析（規避 lsass 記憶體掃描）<br>使用 kernel driver bypass PPL（接 windows_kernel_driver 課的知識） |

</details>

## 測試用例

寫完規則後，用以下案例驗證：

| 案例 | Sigma 期望結果 | YARA 期望結果 |
|---|---|---|
| `mimikatz.exe sekurlsa::logonpasswords` | 命中（Sysmon Event 10，GrantedAccess=0x1FFFFF） | 命中（sekurlsa 字串 + LsaConnectUntrusted 匯入） |
| `procdump64 -accepteula -ma lsass.exe lsass.dmp` | 命中（process_creation，CommandLine 含 lsass） | lsass.dmp 命中 MDMP magic + filesize |
| `rundll32 comsvcs.dll MiniDump 672 out.dmp full` | 命中（process_creation，comsvcs + MiniDump） | out.dmp 命中 MDMP magic + filesize |
| `MsMpEng.exe` 正常存取 lsass | 不命中（filter_legit 排除） | 不命中 |
| `werfault.exe` 存取 lsass（GrantedAccess=0x0400） | 不命中（GrantedAccess 不匹配） | 不命中 |
| 一般 64-byte crash dump（Windows App crash） | N/A | 不命中（filesize < 50MB） |

## 延伸挑戰

完成基本版後，試試這些進階題：

**挑戰 1：補 Shadow Copy 清除偵測（T1490）**
Ransomware 部署前幾乎必定清除 VSS snapshot：`vssadmin delete shadows /all /quiet`。寫一條 Sigma 規則偵測這個行為，並想想有哪些合法備份工具也會執行這個命令。

**挑戰 2：YARA 加 PE module 條件**
把 `Mimikatz_sekurlsa_Strings` 那條 YARA 規則改成同時用 `pe.imports()` 確認 `LsaConnectUntrusted` 匯入存在，比對 `pe.imphash()` 是否落在已知 Mimikatz 的 imphash 清單（查 VirusTotal 或 MalwareBazaar 上的 Mimikatz 樣本）。

**挑戰 3：寫 DeTT&CT YAML**
用 Ch 10 的方法，把你的規則整理成 DeTT&CT 的 detection YAML 格式，評分自己的偵測品質（0-4），並說明為什麼給這個分數。

**挑戰 4：找一個規避你規則的方法**
戴回紅隊帽子：如果你知道目標環境用了你剛寫的這兩條規則，你要怎麼 dump lsass 而不觸發它們？（提示：kernel driver / PPL bypass / 讀取 hiberfil.sys 或 Volume Shadow Copy 裡的 lsass 記憶體）

## 自我檢核

完成後你應該能回答：

- [ ] T1003.001 有哪三種主要手法？攻擊者為什麼要用 comsvcs.dll 而不是 procdump？
- [ ] Sysmon Event 10 的哪個欄位直接告訴你「這個程序要讀 lsass 的記憶體」？GrantedAccess 的值是什麼意思？
- [ ] 你的 Sigma 規則用了 `filter_legit`，為什麼這個設計比「只偵測 Mimikatz.exe 檔名」更好？
- [ ] YARA 的 `filesize > 50MB` 條件為什麼重要？不加會怎樣？
- [ ] 你列的假陽性來源裡，哪一個最難排除？為什麼？
- [ ] 如果你的環境沒有部署 Sysmon Event 10，你的 Sigma 規則還有效嗎？

做完這個練習，你有完整的 purple team 閉環：你知道攻擊、知道遙測、有偵測規則、知道盲點在哪。這是 Detection Engineering 的基本功。

→ [Ch 13 Windows 記憶體鑑識入門](./13-windows-memory-forensics-intro.md)
