# Ch 38 — 建立 Purple Team 演練循環

> 目標：把整門課學到的技術收斂成一個可重複執行的循環——選技術、執行攻擊、驗偵測、補缺口、再測。這個循環是讓防禦能力持續進步而不只是「感覺有在做事」的機制。

## 為什麼需要演練循環

你現在掌握了：記憶體鑑識、日誌分析、網路鑑識、偵測工程（Sigma/YARA）、威脅情報整合、IR 流程。但這些技術在實戰中有個共同的問題：**你不知道你現在偵測不到什麼**。

「我們有 SIEM、有 EDR、有 SOC，所以我們是安全的」——這個邏輯的問題在於，你沒有系統性地驗證你的偵測是否真的有效。攻擊者不會告訴你他用了什麼技術讓你的偵測沒有觸發。

Red team 解決了「我能不能被打進來」的問題，但傳統 red team engagement 通常每年一次、昂貴且不聚焦在偵測改善上。Purple team 解決的是：**在我的環境裡，攻擊者做了某個具體動作，我的偵測工具看得到嗎？**

Purple team 不是紅隊和藍隊打架，而是兩邊協作：紅隊執行，藍隊觀察，雙方立即對齊結果。

## 建立直覺：閉環的意義

多數 SOC 的問題不是技術不夠，而是沒有回饋機制。

- 買了 EDR，但不知道它能不能抓住特定攻擊
- 寫了 Sigma 規則，但不知道它能不能被繞過
- SIEM 有上千條規則，但不知道哪些真的有效、哪些產生大量 FP 從來沒人看

演練循環強制你把這些不確定性具體化。你選一個 ATT&CK technique，讓紅隊執行，然後問：「這次執行在 SIEM 裡有告警嗎？告警在幾分鐘內被處理？」

如果沒有告警，你學到了：**這個 technique 在我的環境裡目前是盲點**。然後你補規則，再測，直到有效。

這是讓防禦能力可量化、可持續改善的唯一方法。

## Purple Team 演練 vs Red Team Engagement

| 項目 | Red Team Engagement | Purple Team 演練 |
|------|--------------------|--------------------|
| 目標 | 能不能突破防線 | 偵測是否有效 |
| 頻率 | 每年 1–2 次 | 每月或每 Sprint |
| 範疇 | 全鏈（從外部到持久化） | 單一或少數 technique |
| 對抗性 | 高（紅隊隱藏自己） | 低（紅隊配合藍隊） |
| 產出 | 滲透測試報告 | 偵測缺口清單和新規則 |
| 成本 | 高 | 中到低（可內部執行） |

**Adversary Emulation（對手模擬）vs Adversary Simulation（對手仿真）**：

- **Emulation**：嚴格複製特定威脅行為者的真實 TTP，包含工具、時序、目標類型。成本高，通常用於 red team engagement 或進階 purple team。
- **Simulation**：選擇特定 ATT&CK technique 執行，不一定複製特定行為者，重點是測試偵測。成本低，適合日常 purple team 演練。

Atomic Red Team 和 Caldera 主要用於 Simulation；完整的 APT emulation 計畫（如 MITRE 的 APT3 emulation plan）才是 Emulation。

## 工具：Atomic Red Team 與 Caldera

### Atomic Red Team

Red Canary 維護的開源框架。每個「atomic test」對應一個 ATT&CK technique，用最小化的手段重現攻擊行為。

```
# 查看 T1059.001 的 atomic tests
Invoke-AtomicTest T1059.001 -ShowDetailsBrief

# 執行 atomic test（需要 invoke-atomicredteam PowerShell 模組）
Invoke-AtomicTest T1059.001 -TestNumbers 1

# 執行後清理
Invoke-AtomicTest T1059.001 -TestNumbers 1 -Cleanup
```

Atomic Red Team 的優點是：
- 每個 test 都很小，容易理解和追蹤
- 對映明確的 ATT&CK technique ID
- 內建 cleanup，測試完不會留下持久化

缺點：每個 test 是獨立的，不模擬多步驟攻擊鏈。

### MITRE Caldera

Caldera 是更完整的自動化對手模擬框架，能規劃並自動執行多步驟 operation（攻擊計畫）。

架構：
- **Server**：Caldera 主控端，有 Web UI
- **Agent**（sandcat 等）：部署在目標主機上，接收指令
- **Adversary Profile**：預定義的攻擊者 TTP 組合
- **Ability**：單一原子操作，等同 Atomic test

Caldera 能自動選擇下一步攻擊（根據環境偵察結果），也能讓人工指定步驟——後者更適合 purple team 演練，因為你要知道你在測試什麼。

Caldera 有內建的偵測驗證功能：設定預期告警，執行後確認 SIEM 是否有對應記錄。

## 規劃一場演練

### Step 1：選 ATT&CK Technique

用你的威脅模型決定優先順序：

- **行業威脅**：你的行業最常被哪種攻擊者打？他們用什麼 TTP？（查 ATT&CK Groups 頁面）
- **偵測涵蓋度缺口**：Ch 10 建立的涵蓋度矩陣裡，哪些 technique 是紅色（無偵測）？
- **最近的 TI 報告**：Ch 36 學的 intel-driven 方法，最近有針對你的威脅用了什麼新手法？
- **高影響技術**：即使你有偵測，某些技術（credential dumping、lateral movement）的偵測品質值得定期驗證

每次演練選 3–5 個 technique，不要貪多。貪多的結果是每個都只是走過場，沒有深度。

### Step 2：定義「成功」的標準

在執行前，雙方對齊：

```
技術：T1003.001 — LSASS Memory Dump
執行方法：Task Manager GUI / ProcDump / comsvcs.dll MiniDump
預期告警：EDR 告警（LSASS 被非系統程式存取）+ SIEM 規則（Event ID 4656 + LSASS 相關）
成功定義：
  - 所有執行方法在 5 分鐘內產生告警 → 偵測完全有效
  - 部分方法觸發告警 → 記錄哪些方法繞過了，補規則
  - 完全沒有告警 → 嚴重缺口，立即補規則並重新演練
```

不定義成功標準，演練就只是表演。

### Step 3：執行

- **通知**：確保相關的 IT 和基礎設施團隊知道演練時間，避免誤判成真實攻擊觸發 IR 流程
- **記錄時間戳**：攻擊動作的精確時間，方便之後在 SIEM 裡找對應記錄
- **從簡單到複雜**：先跑 Atomic test（最基本的實作），再嘗試變體（混淆、binaries 替換）
- **記錄所有發現**：「成功執行、沒有告警」和「觸發告警、3 分鐘內被處理」都是有價值的資料

### Step 4：評估偵測

在 SIEM/EDR 中查找：

```
# 時間範圍：演練執行前後 10 分鐘
# 主機：演練使用的主機名稱
# 事件：EDR 告警、SIEM Sigma 規則觸發

# 要問的問題：
1. 有沒有告警？
2. 如果有，在執行後幾分鐘出現？（MTTD 的微觀測量）
3. 告警的描述是否正確指出攻擊的性質？
4. 如果沒有，原因是什麼？（規則不存在？規則被繞過？Log 沒傳到 SIEM？）
```

### Step 5：補缺口

按照 Ch 37 Detection Gap 分析的格式：

| Technique | 執行方法 | 偵測結果 | 後續行動 | Owner | Deadline |
|-----------|---------|---------|---------|-------|---------|
| T1003.001 | Task Manager | 觸發 EDR，無 SIEM → 新增 Event 4656 + LSASS Sigma | Detection Eng | 2 週 |
| T1003.001 | comsvcs.dll | 完全未偵測 → 新增 EDR rule + Sigma | Detection Eng | 1 週（高優先） |
| T1003.001 | ProcDump | 觸發，3 分鐘偵測 | 有效，記錄 | — | — |

### Step 6：重測驗證

補完規則之後，**一定要重測**。這是最常被跳過的步驟，也是最關鍵的步驟。規則寫好了不代表它能在生產環境裡正確運作——log 格式可能有差、欄位名稱可能不同、規則的 condition 可能有 typo。

重測用和第一次相同的 Atomic test，確認這次有告警。如果有，記錄「已驗證有效」，關閉工單。如果沒有，繼續分析為什麼。

## 計分與追蹤

purple team 演練的價值部分在於讓改善可見化。如果每次演練的結果只在 Slack 頻道裡聊幾句，管理層看不到，Detection Engineering 的工作也看不見。

### 簡單的計分框架

```
每個 Technique 的評分：
  ✓ 完全偵測（所有執行方法觸發告警，且在 SLA 內）    → 3 分
  △ 部分偵測（部分方法觸發）                          → 1 分
  ✗ 無偵測                                             → 0 分

月度演練得分 = (總分 / 最高可能分數) × 100%

追蹤：
  - 每個 Technique 的歷史得分趨勢
  - 「✗ → ✓」的轉換數量（偵測改善數）
  - 平均從「無偵測」到「有效偵測」的時間
```

這些數字給管理層看時要說明它們意味著什麼，而不只是呈現數字。「本季補了 12 個偵測缺口，其中 3 個是針對金融行業最活躍 APT 的核心 TTP」比「覆蓋率從 67% 提升到 79%」更有說服力。

## 規模化演練：持續改善的文化

單次演練沒有意義，重要的是建立循環。

### 演練頻率建議

```
月度（固定）：
  - 選 3–5 個 technique，聚焦在上個月 TI 報告的 TTP 或上次 IR 的 gap
  - 使用 Atomic Red Team 快速執行
  - 產出：Detection Gap 清單和新規則工單

季度（深度）：
  - 選一個完整的攻擊鏈（Initial Access → Persistence → Lateral Movement → Exfiltration）
  - 使用 Caldera 的 Adversary Profile 或手動鏈式執行
  - 評估不只是「有沒有告警」，而是「分析師從告警到確認事件要多久」
  - 產出：完整的演練報告，含 MTTD 測量和流程改善建議

年度（對抗性）：
  - 傳統的 Red Team Engagement 或第三方進行的對手模擬
  - 驗證年度偵測改善的總體效果
```

### 把演練內建到 Detection-as-Code 流程

Ch 12 學了 Detection-as-Code：每條規則都應該有對應的測試。把 Atomic Red Team 的測試綁定到規則的 CI/CD 管線：

```yaml
# 偵測規則的 GitHub Actions workflow 片段
- name: Validate sigma rule coverage
  run: |
    # 在測試環境 VM 裡執行對應的 Atomic test
    python run_atomic.py --technique T1003.001
    # 等 30 秒讓 log 傳到 SIEM
    sleep 30
    # 查詢 SIEM 是否有對應告警
    python check_siem_alert.py --rule process-access-lsass --window 60
```

這樣每次部署新的 Sigma 規則，自動化測試就驗證它真的能抓住對應的攻擊行為。不是每個環境都能做到這一步，但這是 Detection-as-Code 的終極形態。

## 與課程的銜接：整門課的收斂

這門課從 purple team 框架出發，現在我們回到起點，把所有技術放進演練閉環：

```
Ch 0  Purple Team 框架
   │
   ▼
Part 1 Detection Engineering（Sigma/YARA/Sysmon/ETW/SIEM）
   │          ↓ 寫偵測規則        ↑ 補缺口
Part 2–3 Windows/Linux/網路/雲 DFIR
   │          ↓ 知道攻擊留什麼 artifact
Part 4 Threat Hunting
   │          ↓ 主動搜補漏
Part 5 惡意程式與反鑑識對抗
   │          ↓ 知道攻擊者如何規避偵測
Part 6 營運、情報、整合（Ch 35–38）
   │
   ▼
┌─────────────────────────────────────────────────┐
│ Purple Team 演練閉環                             │
│                                                 │
│  選 ATT&CK → Atomic/Caldera 執行               │
│       ↑                          ↓              │
│  重測驗證                   查 SIEM/EDR         │
│       ↑                          ↓              │
│  補偵測規則 ← Detection Gap 分析               │
└─────────────────────────────────────────────────┘
```

攻擊技術（你在 binary_exploitation、kernel_pwn、web_exploitation 課學的）讓你知道攻擊者在做什麼；防守技術（這門課）讓你知道怎麼抓住他。演練閉環讓你持續縮小兩者之間的落差。

## 踩雷

1. **沒有通知就執行演練**：在生產環境執行 LSASS dump、大量橫向移動的 atomic test，會觸發 EDR 隔離、IR 啟動，搞得雞飛狗跳。演練前一定要有清晰的通知範圍和緊急叫停機制。

2. **演練只測「簡單」的方法**：Atomic Red Team 的預設測試很多是沒有混淆的，真實攻擊者會用 BYOL（Bring Your Own Loader）、混淆、代理工具。定期加入更複雜的變體測試，否則偵測只能抓最基本的實作。

3. **紅隊和藍隊分開行動**：Purple team 不是「紅隊打、藍隊看」，而是「紅隊執行一個動作，立刻和藍隊對話：有沒有看到？如果沒有，為什麼？」這種即時對齊是 purple team 的核心價值，做不到就只是兩個獨立的 team 在同一個環境裡工作。

4. **規則補了不驗證**：Detection gap 填了工單，2 週後 Detection Engineer 交了規則，但沒有人重新執行 atomic test 確認規則真的有效。規則可能有語法錯誤、欄位名稱在你的 SIEM 版本裡不一樣，或者覆蓋的變體不夠。沒有重測，就不算完成。

5. **演練成果不做記錄**：每次演練的結果要文件化：哪些 technique 測了、結果是什麼、補了哪些規則、下次演練用什麼。沒有記錄，6 個月後你不知道你測過什麼、改過什麼，更不知道進步在哪裡。

## 進階延伸

- **MITRE ATT&CK Evaluations**：MITRE 每年對主要 EDR 廠商進行基於真實 APT TTP 的評估，結果公開。讀這些評估報告，學習如何設計嚴格的 detection evaluation。
- **Breach and Attack Simulation（BAS）平台**：Cymulate、AttackIQ、Picus Security 等商業平台提供大規模自動化的 BAS，適合規模較大的 SOC。原理和 Caldera 類似，但有更完整的報告和管理功能。
- **Open Purple Teaming Framework**：Purple Teaming 沒有標準化的流程框架，但 VECTR（漏洞與結果追蹤）是一個免費的平台，專門用來追蹤 purple team 演練的結果、計分和改善歷史。
- **ATT&CK Navigator**：用它標記你的偵測涵蓋度矩陣，每次演練後更新，讓改善過程可視化。

## 本章重點整理

- Purple team 演練是讓防禦能力可量化、可持續改善的閉環機制，不是一次性的活動
- Adversary Emulation 複製真實行為者 TTP，Simulation 聚焦單一 technique，日常演練以 Simulation 為主
- Atomic Red Team 測試單一 technique，Caldera 支援多步驟 operation，兩者互補
- 演練六步驟：選 technique → 定義成功標準 → 執行 → 評估偵測 → 補缺口 → 重測驗證
- 沒有重測的缺口補充是無效的，規則寫了不代表在你的環境裡能運作
- 持續改善的文化要靠記錄、計分、追蹤，讓進步可見化

## 自我檢核

- [ ] 我能說出 purple team 和 red team engagement 的主要差別，以及各自適合什麼場景
- [ ] 我知道 Atomic Red Team 和 Caldera 各自的定位，以及什麼情況下用哪個
- [ ] 我能描述演練閉環的六個步驟，以及哪一步最常被跳過
- [ ] 我能解釋為什麼演練必須包含重測，而不是補完規則就結案
- [ ] 我能把整門課的技術（偵測工程、DFIR、威脅情報、IR 流程）定位在演練閉環的哪個環節

## 延伸閱讀

1. **MITRE ATT&CK — Caldera 官方文件**
   - 讀 Adversary Profiles 和 Operation 設計說明
   - 關聯：本章演練工具的實作基礎，搭配 Atomic Red Team 一起使用
   - [https://caldera.readthedocs.io/](https://caldera.readthedocs.io/)

2. **Atomic Red Team GitHub — Getting Started**
   - 讀 invoke-atomicredteam PowerShell 模組說明和 test 格式規格
   - 關聯：本章 Step 3 執行環節的直接工具，每個 atomic test 對映明確的 ATT&CK technique
   - [https://github.com/redcanaryco/atomic-red-team](https://github.com/redcanaryco/atomic-red-team)

3. **MITRE ATT&CK Evaluations 結果報告**
   - 讀最新一輪的評估結果（選你使用的 EDR 廠商）
   - 關聯：學習如何設計 technique-level 的 detection evaluation，理解主流 EDR 的偵測盲點
   - [https://attackevals.mitre-engenuity.org/](https://attackevals.mitre-engenuity.org/)

4. **SANS "Purple Team" whitepapers（SANS Reading Room）**
   - 搜尋 "purple team exercise" 找 2–3 篇近年的實戰報告
   - 關聯：看真實的 purple team 演練如何規劃、記錄和追蹤改善，補充本章的流程說明

5. **NIST SP 800-115 — Technical Guide to Information Security Testing and Assessment**
   - 讀 Section 4（Examination Techniques）和 Section 5（Target Identification and Analysis）
   - 關聯：演練的正式方法論基礎，對外報告和合規場景中引用

→ [Final Project：完整入侵事件調查](./final-project-full-incident-investigation.md)
