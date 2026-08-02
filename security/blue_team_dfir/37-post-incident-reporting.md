# Ch 37 — 事後報告與 MTTD/MTTR 指標

> 目標：學會寫一份有用的 IR 報告——分清楚事實和推論、向管理層和技術團隊各說各話，以及如何把每次事件轉成偵測改善的具體行動，而不是結案就算。

## 為什麼 IR 報告比你以為的重要

報告是 IR 流程的最後一英里，也是最常被敷衍的部分。「事件已處理，系統恢復正常」這種結案紀錄解決了眼前的問題，但沒有任何學習價值——下次一樣的攻擊進來，你還是得從頭查。

IR 報告有三個受眾，而每個受眾需要完全不同的東西：

- **技術團隊（IR analyst、Detection Engineer）**：要的是完整的攻擊鏈技術細節、IOC、每一個 artifact 的來源、哪些偵測規則有效/沒效
- **管理層（CISO、CTO）**：要的是業務影響、根本原因、修復成本、未來風險
- **法律/合規（GC、Compliance Officer）**：要的是事實時間軸、哪些資料受影響、是否觸發通報義務

用同一份報告服務所有受眾，結果是對技術人員太淺、對管理層太深，兩邊都不滿意。正確做法是一份報告、多個版本：Executive Summary 給上層，完整技術報告內部保留。

## 建立直覺：事實與推論必須分開

IR 報告最常見的品質問題：把推論當事實寫。

| 事實（可舉證） | 推論（需要標示） |
|---------------|-----------------|
| 2025-03-12 03:17 UTC，來自 185.220.101.x 的 SMB 連線到 FILESERVER01 | 攻擊者可能在尋找敏感文件 |
| `lsass.exe` 被 PID 4421（`msftedit.exe`）以 PROCESS_VM_READ 存取 | 攻擊者可能嘗試 dump credentials |
| 勒索程式在 9 台主機上加密了 47,832 個檔案 | 初始存取向量可能是釣魚郵件（未確認） |

事實基於你找到的 artifact；推論基於你的分析和經驗。混在一起有兩個問題：

1. 技術上：讓其他人無法驗證你的分析
2. 法律上：如果牽涉法律程序，推論被當事實引用會造成嚴重問題

推論沒有問題，但要標示「我們評估為（we assess with moderate confidence that...）」或「目前無法確認的是...」。

## 報告結構

### 完整 IR 報告骨架

```
=================================================================
事件調查報告
事件編號：INC-2025-0312-001
分類：機密（僅限 SOC 和管理層）
最終版本日期：2025-03-20
作者：[IR 主導分析師]
審閱：[IR 主管]
=================================================================

## 1. Executive Summary（1–2 頁）
   - 事件性質（勒索、資料外洩、APT、內部威脅...）
   - 時間範圍（入侵到偵測到清除）
   - 受影響的系統與資料
   - 業務影響評估
   - 三項最關鍵的建議行動

## 2. 事件時間軸（Technical Timeline）
   - 每個事件 → 時間戳（UTC）、來源 artifact、關聯系統
   - 推論標示「[推斷]」
   - 圖示：攻擊者在哪個階段做了什麼（對映 PICERL 或 Kill Chain）

## 3. 技術細節
   ### 3.1 初始存取（Initial Access）
   ### 3.2 執行（Execution）
   ### 3.3 持久化（Persistence）
   ### 3.4 橫向移動（Lateral Movement）
   ### 3.5 資料收集與外傳（Collection & Exfiltration）
   ### 3.6 衝擊（Impact）

## 4. IOC（Indicators of Compromise）
   - IP 位址、domain（含觀測時間、context）
   - File hash（MD5/SHA256）、路徑
   - Registry key、計畫工作名稱、服務名稱
   - 每個 IOC 加上：發現時間、發現位置、可信度

## 5. 根本原因分析（Root Cause Analysis）
   - 初始入侵點（patch 沒到位？釣魚？credential 被竊？）
   - 允許攻擊者在環境中停留的因素（偵測失效？分段不夠？）
   - 偵測延遲的原因

## 6. 建議行動（Recommendations）
   - 短期（立即執行，1 週內）：patch、重置憑證、封鎖 IOC
   - 中期（1 個月內）：偵測規則補強、分段調整
   - 長期（季度）：架構改善、訓練計畫

## 7. 附件
   - 完整 artifact 清單（帶 hash 驗證）
   - Sigma/YARA 規則（新增或修改的）
   - 命令記錄（鑑識工具的原始輸出）
=================================================================
```

### Executive Summary 怎麼寫

管理層的 Executive Summary 要做到：

1. **第一句話告訴他業務影響**：不要從技術事件開始，從「這件事對公司意味著什麼」開始
2. **用數字**：「9 台主機、47,832 個檔案、預估 72 小時恢復時間、成本估計 X 萬元」比「大量系統受影響」有用
3. **三個建議行動**：不要給 20 條，管理層要的是優先順序
4. **不要用 jargon**：不寫「T1055 process injection」，寫「攻擊者偽裝成系統程式執行惡意程式碼」

## 指標：MTTD、MTTM、MTTR、Dwell Time

這些指標不是裝飾，是你衡量 SOC 進步的唯一客觀方式。

### 定義

| 指標 | 全名 | 量測的是什麼 |
|------|------|-------------|
| MTTD | Mean Time to Detect | 從入侵發生到 SOC 偵測到的時間 |
| MTTM | Mean Time to Mitigate | 從偵測到初步控制（containment）的時間 |
| MTTR | Mean Time to Recover | 從偵測到完全恢復正常運作的時間 |
| Dwell Time | 停留時間 | 從初始入侵到被偵測到的時間（有時等同 MTTD，但強調攻擊者的視角）|

### 關係

```
入侵發生 ─────────────────────────────▶ 時間軸
    │           │        │              │
    │ ←─ Dwell Time ──▶ │              │
    │ ←────── MTTD ────▶│              │
                         │ ←─ MTTM ──▶ │
                         │ ←──────── MTTR ────────────────▶ │
```

### 指標的意義與濫用

**Dwell Time** 是衡量攻擊者在你的環境裡待了多久才被發現。M-Trends 報告長期顯示全球中位數在 16–24 天，意思是攻擊者平均在你環境裡待了 2–3 週才被偵測到。如果你的 dwell time 遠高於此，說明你的偵測有盲點。

**MTTD 的陷阱**：如果一次事件裡 SIEM 在第 3 天告警，但告警被分析師遺漏，第 15 天才真正啟動 IR，MTTD 應該算 3 天還是 15 天？答案是取決於你的定義，但多數正確的定義是「SOC 確認事件存在的時間點」，不是「告警產生的時間點」。

**MTTR 的陷阱**：「恢復」的定義要非常具體。是所有系統回到線上？還是惡意程式被清除？還是已確認沒有持久化？模糊的定義讓這個指標不可比較。

**指標被管理層濫用**：MTTR 如果是管理層的 KPI，會讓 SOC 傾向「快速」關閉事件而不是「徹底」調查。一個月後同樣的攻擊者用相同方法再進來，MTTR 很漂亮，但你根本沒學到東西。

指標是工具，不是目的。要搭配「每次事件帶來的偵測改善數量」等質性指標一起追蹤。

## Lessons Learned 會議

每次重大事件結束後的 Lessons Learned 會議是把事件轉化成組織改善的關鍵機制，也是最常被跳過的步驟（「都忙著處理事情了哪有時間開會」）。

### 會議要素

- **時間**：事件結案後 1 週內，趁記憶還新鮮
- **與會者**：所有直接參與 IR 的人，加上 IT 和業務代表
- **格式**：不是檢討誰做錯什麼，而是系統性分析流程問題
- **產出**：具體行動項目，有 owner 和 deadline

### 要問的問題

1. 初始偵測是怎麼發生的？（是告警、用戶回報、還是外部通知？）
2. 偵測可以更早發生嗎？如果可以，需要做什麼？
3. IR 流程中有哪些延遲？原因是什麼？
4. 溝通是否順暢？有哪些資訊傳達不及時？
5. 有哪些工具或資源在調查中缺失？
6. 如果這個攻擊者再來，我們現在能抓到嗎？

最後那個問題是最重要的。如果答案是「不確定」，說明你還有工作要做。

## 把事件轉成偵測改善

每次事件都應該產生至少一條新的或改善的偵測規則。這是把 IR 的成本轉化成長期防禦投資的方法。

### Detection Gap 分析

結案後系統性地問：

```
這次攻擊的每個步驟：
   │
   ▼
攻擊者做了 X（ATT&CK Technique Y）
   │
   ├─ 我們有沒有規則偵測 Y？
   │     ├─ 有，而且觸發了 → 記錄「有效偵測」
   │     ├─ 有，但沒觸發 → 分析為什麼失效 → 修規則
   │     └─ 沒有 → 寫新規則
   │
   └─ 是什麼讓攻擊者能做 X？
         → 設定錯誤、patch 缺失、分段不足？
         → 開修復工單
```

Detection Gap 要文件化，追蹤到補規則和驗證為止。

### 範例：勒索軟體事件的偵測改善

事件：REvil 勒索軟體通過 Exchange ProxyShell（CVE-2021-34473）進入，drop webshell，橫向移動到 domain controller，最終部署勒索軟體。

事後 detection gap 分析：

| 攻擊步驟 | ATT&CK | 偵測狀態 | 後續行動 |
|----------|--------|---------|---------|
| ProxyShell 利用（異常 IIS request） | T1190 | 無 → 新增規則：監控 IIS 的 `%windir%\Microsoft.NET\Framework64\` 目錄下的新建檔案 |
| Webshell 創建 | T1505.003 | 有 YARA 規則，但沒掃 Exchange 目錄 → 擴展掃描範圍 |
| Cobalt Strike beacon | T1071.001 | 有，且觸發 → 有效，但升級被延遲 → 改善升級 SLA |
| Lateral movement via SMB | T1021.002 | 有，但 FP 率太高分析師習慣性忽略 → 加 context 降低 FP |
| Rclone 外傳 | T1567.002 | 無 → 新增規則：偵測 `rclone.exe` 執行和異常大量 HTTPS 上傳 |
| 檔案加密 | T1486 | 有，但觸發時已有 9 台主機被加密 → 前置偵測（上述步驟的早期偵測）是關鍵 |

這個表格直接轉成 6 個 GitHub issue，分配給 Detection Engineering 團隊，有 deadline，下個月的 purple team 演練用同樣攻擊鏈驗證修復效果。

## 技術團隊 vs 管理層：溝通的差異

### 技術版報告

- 完整的命令行輸出和 artifact 分析
- Volatility plugin 的輸出、YARA 比對結果
- PCAP 截圖、事件 ID 和 field 值
- ATT&CK technique ID 對映

### 管理層版報告（Executive Summary）

**不要**：「攻擊者使用 T1055（Process Injection）繞過 EDR 的 hook，在 LSASS 進程空間注入 shellcode 並執行 Mimikatz 模組 sekurlsa::logonpasswords 竊取 NTLM hash。」

**要**：「攻擊者成功繞過防毒軟體，竊取了多個管理員帳號的密碼雜湊值，使其能夠在無需原始密碼的情況下存取系統。受影響的帳號需要立即重置。」

內容相同，受眾不同。管理層需要理解「這對業務意味著什麼」，不需要知道 NTLM hash 是什麼。

## 踩雷

1. **結案太快**：攻擊者清除了他的工具不代表他沒有留持久化。在關閉 case 前，持久化掃描和 IOC 搜索要完成。

2. **時間軸用本地時間**：所有 artifact 的時間戳要統一用 UTC，加上來源系統。「下午 3 點」是沒有意義的，因為不知道哪個時區、哪台機器。

3. **IOC 清單沒有 context**：只列 IP 和 hash 沒有價值，要說明每個 IOC 是從哪個 artifact 發現、什麼時間、觀測到什麼行為，才能讓其他人驗證。

4. **Lessons Learned 沒有 owner**：「我們要改善偵測」不是 lessons learned，「Detection Engineering 在 2025-04-30 前新增 Rclone 偵測規則，由 A 負責」才是。沒有 owner 和 deadline 的行動項目會沉沒。

5. **MTTD 只看平均值**：平均 MTTD 4 小時聽起來不錯，但如果有一次事件 dwell time 是 60 天，平均值被那次大量拉高，平均值的意義就有限。要看中位數和分布，特別是長尾。

## 進階延伸

- **Structured Analytic Techniques**：CIA/DIA 發展的情報分析方法，包含「競爭假設分析（ACH）」——在歸因或根本原因分析中評估多個假設，強制列出支持和反駁每個假設的證據，避免確認偏誤。
- **Post-Incident Review 的持續追蹤**：把每次事件產生的 Detection Gap 和建議行動放進偵測工程的 backlog，在季度 review 中確認有多少比例已完成、有多少在下次演練中被驗證。
- **Regulatory Notification Timeline**：GDPR 72 小時、PDPA 72 小時、HIPAA 60 天——不同法規有不同的通報 deadline，IR 時間軸要對照 compliance 要求，這影響結案速度的優先順序。

## 本章重點整理

- IR 報告有三個受眾：技術團隊、管理層、法律合規，要用不同語言說話
- 事實和推論必須明確區分，推論要標示可信度
- 指標 MTTD/MTTM/MTTR/Dwell Time 是衡量進步的工具，但要注意定義不一致和管理層濫用的風險
- Lessons Learned 會議要在事件後 1 週內舉行，產出有 owner 和 deadline 的行動項目
- 每次事件的 Detection Gap 分析要系統化，直接驅動新偵測規則的開發和驗證

## 自我檢核

- [ ] 我能說出 Executive Summary 應該包含哪些元素，以及為什麼要和技術報告分開
- [ ] 我能區分 MTTD、MTTM、MTTR、Dwell Time，以及各自可能被濫用的方式
- [ ] 我能描述 Detection Gap 分析的流程，以及如何把它轉成具體的改善工單
- [ ] 我知道在 IR 報告中「我們評估為」和「確認的事實是」的差別，以及為什麼要分清楚
- [ ] 如果我要主持一場 Lessons Learned 會議，我知道要問哪 5 個核心問題

## 延伸閱讀

1. **NIST SP 800-61r3 — Section 3.4「Post-Incident Activity」**
   - 讀 Lessons Learned 和 Evidence Retention 部分
   - 關聯：本章 lessons learned 流程的政策基礎，也定義了 IR 文件保存要求
   - [https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-61r3.pdf](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-61r3.pdf)

2. **SANS FOR508 — IR Metrics and Reporting**
   - 讀 MTTD/MTTR 定義和 detection gap 分析方法
   - 關聯：本章指標定義的實作細節，搭配 Ch 35 的 SOAR SLA 設計

3. **CISA — Incident Reporting Forms and Guidelines**
   - 讀各行業的通報要求（CISA Cyber Incident Reporting for Critical Infrastructure Act 2022）
   - 關聯：IR 報告的合規面向，特別是通報 deadline 和必要欄位

4. **M-Trends 年度報告（Mandiant）**
   - 讀 Dwell Time 趨勢和攻擊鏈分析
   - 關聯：本章指標的行業 benchmark，讓你知道你的 MTTD 和全球中位數的差距
   - [https://www.mandiant.com/resources/reports/m-trends](https://www.mandiant.com/resources/reports/m-trends)

5. **MITRE ATT&CK — Groups 和 Software 頁面**
   - 讀幾個你認識的 APT group（如 APT29、FIN7）的 TTP 列表
   - 關聯：Detection Gap 分析的 ATT&CK 對映，把 IR 的調查結果連接到 Ch 36 的 TI 整合

→ [Ch 38 建立 Purple Team 演練循環](./38-purple-team-exercise-loop.md)
