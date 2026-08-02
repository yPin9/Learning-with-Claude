# Ch 7 — 偵測邏輯：IOC vs IOA vs 行為偵測

> 目標：建立偵測邏輯的完整分類框架——理解 IOC、IOA、行為偵測的本質差異、各自的強弱點、以及 Pyramid of Pain 為什麼是防守方思考偵測策略的核心工具。同時誠實面對假陽性與假陰性的本質權衡，不逃避它。

## 為什麼偵測邏輯的選擇攸關生死？

防守方的資源是有限的：SIEM 的 ingestion 費用、分析師的工時、告警的數量都有上限。偵測邏輯決定你在有限資源下能捕捉多少攻擊、同時放過多少噪音。

從攻擊者視角思考：你在做滲透時，哪些行為是「必須做」的（無論如何都要做），哪些是「可以換掉的」（工具、IP、hash 都可以換）？

答案決定了防守方應該把精力放在哪裡。

## IOC（Indicator of Compromise，入侵指標）

### 什麼是 IOC

IOC 是具體的、靜態的、可直接比對的特徵：

- **Hash（MD5/SHA1/SHA256）**：`e3b0c44298fc1c149afbf4c8996fb924...`
- **IP 位址**：`185.220.101.33`
- **域名**：`evil-c2.example.com`
- **URL**：`http://185.220.101.33/malware/payload.exe`
- **Registry key**：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Updater`
- **Mutex 名稱**：`{12345678-1234-1234-1234-123456789abc}`
- **電子郵件標頭**：`X-Mailer: PhishingKit/3.2`

IOC 的本質：**某個已知惡意物件的指紋**。你拿著這個指紋去比對，符合就告警。

### IOC 的問題：為何脆弱

攻擊者換掉 IOC 的成本極低：

```
# 換 hash：重新編譯、加一個空白字元、改 icon → 新的 SHA256
# 換 IP：換一個 VPS，$5/月，10 分鐘完成
# 換域名：新域名幾美元，DNS 傳播幾分鐘
# 換 mutex：改一個字串常數，重新編譯
```

你的 IOC 比對規則在攻擊者改動之後立即失效，而改動的成本幾乎是零。這是 IOC 偵測的根本弱點：**它偵測的是過去的攻擊，不是未來的攻擊**。

更糟的是：很多 IOC 的時效性極短。APT 組織通常每次任務都換基礎設施，你從威脅情報拿到的 C2 IP 在 48 小時後可能已經是別人的合法 CDN 了。

### IOC 的價值在哪

IOC 並非無用，但它的正確定位是：

1. **事後確認**：已知惡意活動的快速比對，確認是否為同一行為者的手法
2. **早期 triage**：事件處理時快速過濾「有無命中已知惡意物件」
3. **情報共享**：STIX/TAXII 格式的 IOC 可以在組織間快速共享，快速部署

IOC 是必要的，但它只能作為偵測策略的最底層，不能當作主要偵測手段。

## IOA（Indicator of Attack，攻擊指標）

### IOC vs IOA 的根本差異

| 維度 | IOC | IOA |
|------|-----|-----|
| 比對對象 | 靜態特徵（hash/IP） | 動作/意圖（行為序列） |
| 時間點 | 事後（after-the-fact） | 即時或近即時 |
| 攻擊者繞過難度 | 低（換工具就繞） | 中至高（需要改變攻擊方式） |
| 誤報率 | 低（精確比對） | 中至高（需要調校） |
| 涵蓋 zero-day | 否 | 部分可以 |

IOA 偵測的是「攻擊者在做什麼」，而非「攻擊者用了什麼工具」。

### IOA 範例

**IOC 思維**：`mimikatz.exe` 的 SHA256 是 `...abc123...`，看到就告警。
→ 攻擊者重新編譯 Mimikatz，hash 變了，不再告警。

**IOA 思維**：`lsass.exe` 的 handle 被帶有 `PROCESS_VM_READ` 的非系統進程開啟，而且來自非預期路徑的可執行檔。
→ 攻擊者無論用什麼工具 dump LSASS，都需要開啟帶有這個 access mask 的 handle，這個行為是必要的。

IOA 的核心問題：**哪些行為對於達成攻擊目標是必要的？**

常見 IOA 範例：

| 攻擊行為 | IOA | 對應 Sysmon Event |
|----------|-----|-------------------|
| Credential dumping | 非 SYSTEM 進程 OpenProcess(lsass) 且 GrantedAccess 包含 `0x10` | Event ID 10 |
| Lateral movement via PsExec | `PsExec` 在遠端機器執行服務 + `\\machine\ADMIN$` 存取 | Event ID 1 + Security 4624 |
| PowerShell download cradle | `powershell.exe` 進行 DNS 查詢後立即建立 TCP 連線到外部 IP | Event ID 22 → Event ID 3 |
| Process hollowing | `CreateProcess(SUSPENDED)` → `WriteProcessMemory` → `ResumeThread` | Event ID 25（Process Tampering） |
| DLL sideloading | 簽名可執行檔從非預期路徑載入未簽名 DLL | Event ID 7 |

## Pyramid of Pain（David Bianco）

Pyramid of Pain 是 2013 年 David Bianco 提出的框架，至今仍是理解偵測層次的最佳工具。

```
         ╔══════════════════════════════╗
         ║      TTPs（Tactics,          ║  ← 攻擊者最難改
         ║  Techniques, Procedures）    ║
         ╚══════════════════════════════╝
       ╔════════════════════════════════════╗
       ║           Tools（工具）             ║
       ╚════════════════════════════════════╝
     ╔════════════════════════════════════════╗
     ║         Network/Host Artifacts         ║
     ╚════════════════════════════════════════╝
   ╔════════════════════════════════════════════╗
   ║            Domain Names（域名）             ║
   ╚════════════════════════════════════════════╝
 ╔════════════════════════════════════════════════╗
 ║            IP Addresses（IP 位址）              ║
 ╚════════════════════════════════════════════════╝
╔══════════════════════════════════════════════════╗
║               Hash Values（雜湊值）              ║  ← 攻擊者最容易改
╚══════════════════════════════════════════════════╝

越高層 → 偵測到對攻擊者越痛苦（被迫改變越多）
```

### 逐層解析：為什麼越高越痛？

**Hash Values（最底層）**
攻擊者改動成本：幾秒。重新編譯、加一個 NOP 指令、修改 PE header 的 timestamp，hash 就變了。你的 hash 黑名單只對完全相同的二進位有效。

**IP Addresses**
攻擊者改動成本：幾分鐘。換個 VPS，或用 cloud CDN（CloudFront、Cloudflare）當跳板，原始 C2 IP 永遠不直接出現。IP blocklist 在 CDN 盛行的今天越來越無效。

**Domain Names**
攻擊者改動成本：幾小時（含 DNS 傳播）。成本略高，但仍然很低。Domain generation algorithm（DGA）讓攻擊者每天產生成千上萬個備用域名，你封掉一個他有幾千個備胎。

**Network/Host Artifacts**
工具留在網路上或主機上的痕跡：特定的 HTTP header 格式、User-Agent 字串、C2 通訊的特定 beacon 間隔、特定的 mutex 名稱、registry key 名稱。
攻擊者改動成本：需要修改工具原始碼，重新編譯，測試，數天到數週。這裡開始痛了。

**Tools（工具）**
攻擊者使用的特定工具：Cobalt Strike、Metasploit、Mimikatz。
偵測工具特徵（工具產生的網路指紋、工具特有的行為模式）讓攻擊者被迫：
(a) 換工具，(b) 修改工具讓它不被識別。
兩者都需要大量時間和技術能力，大幅拉高攻擊成本。

**TTPs（最頂層）**
MITRE ATT&CK 的 Technique 層：`T1003.001 OS Credential Dumping: LSASS Memory`、`T1547.001 Registry Run Keys / Startup Folder`。

攻擊者要繞過 TTP 層的偵測，必須改變攻擊方式本身——換一種達成同樣目標的方法。這通常意味著：
- 研發新的攻擊手法
- 找尋鮮為人知的替代技術
- 花費大量時間測試規避效果

這是攻擊者最難做到的改變。

### 實際應用

**建議**：在有限資源下，把偵測精力優先放在 Artifact 層以上，而非 Hash/IP。

常見錯誤：SOC 99% 的規則都在比對 IP/Hash（因為容易寫），結果攻擊者換個 VPS 就繞過全部規則。

現實的平衡：
- Hash/IP/Domain IOC：自動化比對（威脅情報平台處理），不佔分析師工時
- Artifact 層：Sigma 規則偵測特定工具的行為特徵
- TTP 層：行為偵測規則，需要最多調校但最難繞過

## Indicator 的三種類型

除了 IOC vs IOA 的分類，還有一個維度：indicator 的「計算複雜度」。

### Atomic Indicator

最簡單的 indicator：單一資料點，無需計算。

- Hash `e3b0c44298fc...`
- IP `185.220.101.33`
- Registry key 名稱 `Updater`

可以直接比對，不需要關聯多筆資料。誤報率低，但攻擊者容易繞過。

### Computed Indicator

需要從多個資料點計算或關聯才能產生的 indicator：

- 「連線 beacon 的時間間隔標準差低於 X 秒」（需要時間序列計算）
- 「一台機器在 1 分鐘內對超過 50 個不同目標 IP 發起連線」（需要聚合計數）
- 「某個進程的網路流量 byte 量分佈與已知正常程序顯著不同」（需要統計模型）

Computed indicator 對攻擊者更難繞過（攻擊者需要改變行為模式，不只是換個工具），但誤報率更高，需要更多調校。

### Behavioral Indicator

最複雜的類型：一系列行為的組合，按照特定的順序或邏輯關係發生：

「`Word.exe` 啟動 `cmd.exe`，cmd.exe 執行 `powershell.exe -enc ...`，powershell.exe 建立對外 TCP 連線到非 Office 365 的 IP」

這描述的是 Office 文件 macro 下載 payload 的典型行為鏈。每個單獨步驟可能都是合法的；它們組合在一起才是 indicator。

## 三種偵測策略的取捨

### Signature-based（特徵偵測）

本質：已知惡意特徵的精確比對。

```
if sha256(file) in known_bad_hashes → ALERT
if src_ip in known_c2_ips → ALERT
if regex_match(command_line, r"(?i)-enc[oded]?") → ALERT
```

優點：誤報極低、執行速度快、人類可解釋性高、可快速部署。
缺點：只能偵測已知攻擊、零日攻擊完全無效、攻擊者容易繞過。

### Anomaly-based（異常偵測）

本質：建立「正常」的基線，偵測偏離基線的行為。

機器學習、統計模型：
- 使用者通常在 09:00-18:00 登入，凌晨 3 點的登入是異常
- 這台機器平均每天 DNS 查詢 200 個域名，今天查詢了 10000 個

優點：理論上可偵測 zero-day（只要行為異常）、不依賴預先知道攻擊。
缺點：
- **誤報極高**：正常的行為變化（節假日、業務調整）都可能被視為異常
- 訓練資料若包含攻擊行為，基線會被污染
- 「慢速」攻擊刻意保持在基線內
- 解釋性低：「ML 說有問題」但你不知道為什麼

實際上，純粹依賴異常偵測的 SOC 分析師每天會被淹沒在誤報裡。

### Behavior-based（行為偵測）

本質：偵測攻擊者為了達成目標「必須做」的行為模式，而非特定工具的特徵。

這是目前防守方最有效的偵測策略。它比 signature 更難繞過（因為目標行為本質上是必要的），比純粹 anomaly 的誤報更低（因為有明確的邏輯）。

例子（針對 Kerberoasting）：
```
# 行為：任何帳號在短時間內對多個服務帳號請求 Kerberos Service Ticket
# 邏輯：正常使用者不需要同時連接 10 個服務
Alert when:
  Event ID 4769 (Kerberos Service Ticket request)
  by the same account
  with EncryptionType = 0x17 (RC4, weak, 容易被暴力破解)
  count > 5 within 10 minutes
```

## 假陽性（False Positive）與假陰性（False Negative）的本質

偵測邏輯面對一個無法消滅的權衡：

```
敏感性（Sensitivity）vs 精確性（Precision）

─────────────────────────────────────────────────────
                    │ 是攻擊    │ 不是攻擊
────────────────────┼──────────────────────────────
告警觸發            │ TP（好）  │ FP（假陽性，浪費時間）
告警未觸發          │ FN（假陰性，漏了攻擊）│ TN（好）
─────────────────────────────────────────────────────
```

**規則越嚴格（門檻越高）**：
- FP 降低（更少誤報）
- FN 升高（漏掉更多真實攻擊）

**規則越寬鬆（門檻越低）**：
- FP 升高（分析師被告警淹沒）
- FN 降低（漏掉的攻擊少）

這不是技術問題，是設計決策。你要根據：
- 資產的重要性（生產資料庫 vs 測試環境）
- 攻擊者的能力（APT vs 腳本小子）
- 分析師可以處理的告警量

做出有意識的選擇。

### Alert Fatigue（告警疲勞）的真實影響

大量 FP 不只是浪費時間，它讓分析師習慣點「確認、關閉、下一個」，最終對 TP 也這樣處理。2013 年 Target 被入侵事件中，FireEye 的告警確實有觸發，但 SOC 分析師在疲於應付 FP 的環境下沒有認真跟進。

**FP 太多比 FN 太多更危險**——因為它讓你在有真實威脅時反應遲鈍。

## 偵測邏輯的品質評估

| 評估維度 | 問題 |
|----------|------|
| 可見性 | 這條規則需要哪些 log source？那些 source 一定有嗎？ |
| 精確性 | 在正常環境中，這條規則每週觸發幾次 FP？ |
| 鑑別力 | 觸發這條規則的合法行為有哪些？能進一步區分嗎？ |
| 覆蓋面 | 這條規則能偵測到這個 TTP 的哪些變體？攻擊者最簡單的繞過是什麼？ |
| 可維護性 | 環境改變後（新軟體部署、IP 異動），這條規則需要多少維護？ |

## 踩雷：錯誤直覺 → 正確認識

**1. 「有 IOC 威脅情報訂閱就夠了」**
→ IOC 情報訂閱提供的是 Pyramid of Pain 的最底層——hash 和 IP——攻擊者換起來幾分鐘。情報訂閱是必要的，但不能替代行為偵測。一個沒有行為偵測的 SOC，只要攻擊者用新工具就完全盲目。

**2. 「機器學習的異常偵測比規則更好，因為它能抓 zero-day」**
→ 異常偵測在實際 SOC 的 FP 率通常高到不可接受。它在概念上能抓 zero-day 是真的，但在實務上，分析師處理不完的 FP 讓 TP 也被埋沒。規則型偵測（行為偵測）的可解釋性讓分析師能快速判斷，這在實際運作中更有價值。兩者應互補，而非選一。

**3. 「偵測 TTP 就不需要管 IOC 了」**
→ 兩者的時間點和用途不同。TTP 偵測用於即時偵測；IOC 比對用於快速確認（「這個行為者之前用過的 C2 IP 又出現了？」）和情報溯源。完整的偵測策略需要全部層次。

**4. 「降低 FP 就是好的偵測工程」**
→ 把所有規則的門檻拉高確實可以降低 FP，但同時讓 FN 升高，讓你漏掉真實攻擊。正確目標是：在你能處理的 FP 量之下，把 FN 降到最低。這需要不斷調校，而非追求零 FP。

**5. 「Pyramid of Pain 的意思是 IOC 沒用」**
→ Pyramid of Pain 說的是：偵測越高層的 indicator，對攻擊者越痛苦。不是說底層沒用。SHA256 比對成本幾乎為零，自動化掃描沒理由放棄。問題是不能只依賴底層。

## 進階延伸

### Threat Intelligence 的 Indicator Lifecycle

IOC 有時效性。一個好的威脅情報流程應該包含 indicator 的「老化」機制：

- C2 IP：24–72 小時後可靠性快速下降（IP 可能被重新分配）
- 域名：幾天到幾週（domain 被 sinkhole 或棄置後仍可能比對到）
- TTPs：通常可以保持數月到數年（行為模式改變慢）

**MISP（Malware Information Sharing Platform）** 有 `to_ids` flag 和 `expire` 機制，讓 IOC 自動退場。

### Detection Engineering 作為產品開發

把每一條偵測規則當成一個產品：
- 有明確的「客戶」（這條規則對應哪個分析師流程）
- 有測試（用 Atomic Red Team 確認能觸發）
- 有版本控制（規則改動有 git diff）
- 有回顧（每季度重新評估 FP/FN 率）

這就是 Ch 12 Detection-as-Code 的核心精神。

## 本章重點整理

- IOC（hash/IP/domain）偵測已知惡意特徵，攻擊者改動成本低；IOA 偵測攻擊者的必要行為，改動成本更高。
- Pyramid of Pain：偵測越高層的 indicator，攻擊者繞過的成本越高。TTP 層是頂點。
- Indicator 類型：Atomic（直接比對）、Computed（聚合計算）、Behavioral（行為序列）。
- 三種偵測策略：Signature（精確但易繞）、Anomaly（理論全能但 FP 高）、Behavior（最有效的主力）。
- FP vs FN 是本質權衡，不是可以消滅的問題。Alert fatigue 是比 FN 更常見的失敗模式。
- 完整偵測策略是多層次的：自動化 IOC 比對（底層）+ 行為偵測規則（主力）+ 異常偵測（輔助）。

## 自我檢核

1. 攻擊者用 Mimikatz 的修改版（改 hash）dump LSASS，哪種偵測策略能抓到？哪種不行？
2. 「`powershell.exe` 進行了 DNS 查詢」是 Atomic/Computed/Behavioral indicator 的哪種？為什麼？
3. Pyramid of Pain 的「Network Artifacts」層具體指什麼？舉一個 Cobalt Strike 的例子。
4. 為什麼 FP 太多在實際 SOC 中比 FN 太多更危險？
5. 一條規則的「覆蓋面」和「精確性」通常是什麼關係？如何在設計上做平衡？

## 延伸閱讀

1. **David Bianco, "The Pyramid of Pain"** — [http://detect-respond.blogspot.com/2013/03/the-pyramid-of-pain.html](http://detect-respond.blogspot.com/2013/03/the-pyramid-of-pain.html)
   讀哪：原始部落格文章，簡短，直接
   學什麼：Pyramid of Pain 的原始思維，以及每層的實際操作含義
   關聯：本章的核心框架，在 Ch 10 ATT&CK 對映時再次用到

2. **MITRE ATT&CK — "Techniques" 頁面** — [https://attack.mitre.org/techniques/](https://attack.mitre.org/techniques/)
   讀哪：任意選一個你熟悉的攻擊技術，看它的 "Detection" 欄位
   學什麼：MITRE 官方建議偵測該 TTP 時應該看哪些 data source、什麼樣的行為是 IOA
   關聯：Ch 10 ATT&CK 對映與偵測涵蓋度

3. **"Cyber Kill Chain" — Lockheed Martin** — [https://www.lockheedmartin.com/en-us/capabilities/cyber/cyber-kill-chain.html](https://www.lockheedmartin.com/en-us/capabilities/cyber/cyber-kill-chain.html)
   讀哪：原始白皮書（可 PDF 下載）
   學什麼：Kill Chain 模型如何幫助你在攻擊鏈的不同階段設計偵測點——越早偵測代價越小
   關聯：Ch 2 ATT&CK 與 Kill Chain 的對比

4. **SANS, "Indicators of Compromise and Why They Are Not IOAs"** — 在 SANS 部落格搜尋
   讀哪：搜尋「IOC IOA SANS blue team」，閱讀任一 2020 年後的文章
   學什麼：實際 SOC 環境中如何混合使用兩種 indicator，以及調校的實務經驗
   關聯：Ch 11 SIEM 架構與 detection pipeline

5. **"Detection Engineering Maturity Matrix" — Kyle Bailey** — [https://kyle-bailey.medium.com/](https://kyle-bailey.medium.com/)
   讀哪：Detection Engineering Maturity Matrix 文章
   學什麼：如何評估一個 SOC 的偵測成熟度，從只有 IOC 比對到完整 TTP 偵測的演進路徑
   關聯：Ch 12 Detection-as-Code

---

IOC vs IOA 的理論框架已經清晰。下一步是把這個理論落地：用 Sigma 規則語言把行為偵測邏輯具體寫出來，讓它在任何 SIEM backend 都能執行。

→ [Ch 8 Sigma 規則工程](./08-sigma-rule-engineering.md)
