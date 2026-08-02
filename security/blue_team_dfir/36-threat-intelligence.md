# Ch 36 — 威脅情報整合

> 目標：建立對威脅情報（Threat Intelligence，TI）的正確期待——它能做什麼、不能做什麼，以及如何把 TI 真正落地成偵測規則和狩獵假設，而不是把一堆 IP 清單塞進 SIEM 了事。

## 為什麼需要威脅情報

防守方天生在資訊不對稱的位置。攻擊者知道他要打誰、用什麼工具、打哪個時間點，而你不知道。TI 的作用是縮小這個不對稱——讓你知道「現在有哪些威脅行為者在活動、他們的目標是誰、他們用什麼手法」。

但這是理想。現實中多數組織的「TI 整合」是：訂了幾個 IOC feed，把 IP 和 domain 清單導入 SIEM，然後說「我們有 TI 了」。這種用法把 TI 的戰略價值砍掉了 80%。

真正的 TI 整合要能回答：

- 這個對我的行業或地區有沒有針對性威脅？
- 已知針對我的威脅行為者用什麼 TTP？
- 我現在的偵測涵蓋度對這些 TTP 夠嗎？
- 今天的 IOC feed 有多少是還有效的？

## 建立直覺：TI 的三個層次

TI 不是單一東西，按照受眾和用途分三層：

```
┌─────────────────────────────────────────────────────┐
│ Strategic TI（戰略情報）                              │
│ 受眾：CISO、管理層                                   │
│ 內容：威脅行為者的動機、能力、目標行業趨勢            │
│ 時效：月到年                                          │
│ 形式：報告、簡報                                      │
├─────────────────────────────────────────────────────┤
│ Operational TI（行動情報）                            │
│ 受眾：IR 主管、SOC 經理                               │
│ 內容：某次攻擊活動的時間軸、目標、基礎設施            │
│ 時效：週到月                                          │
│ 形式：campaign 報告、TIP 中的 event                   │
├─────────────────────────────────────────────────────┤
│ Tactical TI（戰術情報）                               │
│ 受眾：SOC 分析師、Detection Engineer                  │
│ 內容：IOC（IP、domain、hash）、TTP、Sigma/YARA 規則   │
│ 時效：小時到週                                        │
│ 形式：STIX bundle、IOC feed、偵測規則                 │
└─────────────────────────────────────────────────────┘
```

多數人只用到 Tactical TI，然後抱怨「TI 沒用」。問題是 Tactical TI 消耗快——一個 IP 被燒掉之後攻擊者換新的，你的 IOC 就過期了。沒有 Operational 和 Strategic TI 提供背景，你不知道要優先看哪些 IOC、某個工具是否和你的威脅模型有關。

## IOC 的品質與時效問題

IOC（Indicators of Compromise）最常見的形式：IP 位址、domain、file hash、URL、email 地址。

### Pyramid of Pain（痛苦金字塔）

這個框架描述了不同類型 IOC 對攻擊者的阻礙程度：

```
                 /\
                /TTP\          最痛（行為層面）
               /------\
              / Tools  \       很痛（工具特徵）
             /----------\
            /  Network   \     中（基礎設施）
           /   Artifacts  \
          /-----------------\
         /  Host Artifacts   \  少（端點 artifact）
        /---------------------\
       /      Domain Names     \  輕（換域名即可）
      /--------------------------\
     /         IP Addresses      \ 最輕（換 IP 即可）
    /------------------------------\
```

**IP 位址和 domain 在金字塔底部**：攻擊者換成本極低，你封鎖之後幾小時就換新的。Hash 也容易規避——改一個 byte，所有 hash 規則全失效。

**TTP（戰術、技術、程序）在頂端**：攻擊者要改變行為模式成本很高，因為涉及培訓、工具重寫、作業流程調整。用 ATT&CK 技術做偵測（行為偵測）比 IOC 持久得多。

### IOC Feed 的常見問題

| 問題 | 說明 |
|------|------|
| 時效過期 | 一個月前的惡意 IP 現在可能已經換手給正常用戶 |
| FP 率高 | 共享 CDN、Cloudflare 節點被錯誤標記 |
| 來源不透明 | 不知道情報從哪來，無法評估可信度 |
| 無背景 | 只有 IP，不知道它和什麼攻擊活動、什麼威脅行為者有關 |
| 重複噪音 | 多個 feed 都包含同樣舊的 IOC，數量看起來多但價值低 |

評估一個 IOC feed 的問題：「這個 IOC 是多久前產生的？來源是什麼？有沒有關聯的背景資訊？」如果都不知道，這個 feed 的可信度存疑。

## TIP 與標準：MISP、STIX/TAXII

### MISP（Malware Information Sharing Platform）

MISP 是開源的威脅情報平台（Threat Intelligence Platform，TIP），核心功能：

- 儲存和共享 IOC、TTP、威脅行為者 profile
- Event 為單位組織情報，每個 event 對應一次攻擊活動或一份報告
- Galaxy：預定義的標籤系統，包含 MITRE ATT&CK、威脅行為者、惡意程式 cluster
- 聯盟功能：多個 MISP 實例互相同步，形成情報共享社群（如 CERT 社群、行業 ISAC）

MISP 的 API 讓你能把情報導出為 SIEM 的查詢、YARA 規則、Sigma 規則，或者把 TheHive 的調查結果推回 MISP 豐富 event。

### STIX/TAXII

- **STIX（Structured Threat Information Expression）**：描述 TI 的 JSON schema。STIX 2.1 定義了多種物件類型：
  - `indicator`：IOC 或行為描述
  - `threat-actor`：威脅行為者 profile
  - `campaign`：攻擊活動
  - `attack-pattern`：TTP（對映 ATT&CK technique ID）
  - `relationship`：物件之間的關係（這個 indicator 屬於這個 campaign）
  
- **TAXII（Trusted Automated eXchange of Indicator Information）**：傳輸 STIX bundle 的協定，定義了 Collection 和 Channel 兩種分發模型。

現代 TIP（商業如 Recorded Future、Mandiant Advantage、MISP 開源版）都支援 STIX/TAXII 介面，讓 TI 的消費和分發標準化。

## ATT&CK 作為 TI 骨架

ATT&CK 不只是偵測涵蓋度的地圖，也是把 TI 轉化成可操作情報的橋樑。

當一份 TI 報告說「APT29 使用 Cobalt Strike 的 named pipe 進行橫向移動」，你需要把這句話翻譯成：

1. ATT&CK 對映：T1021.002（SMB/Windows Admin Shares）、T1059.001（PowerShell）、T1055（Process Injection，named pipe）
2. 我對這些 technique 有偵測嗎？查 Ch 10 的涵蓋度矩陣
3. 沒有的補 Sigma 規則，有的確認規則的品質

這個流程叫 **intel-driven detection**：情報驅動偵測。你不是亂槍打鳥地寫規則，而是根據你的威脅模型（誰會打你、用什麼手法）優先補偵測缺口。

```
TI 報告
   │ 提取 TTP
   ▼
ATT&CK Technique ID
   │ 查涵蓋度矩陣
   ▼
有偵測？
   ├─ 有 → 驗證規則品質（用 Atomic Red Team 跑一下）
   └─ 無 → 寫新的 Sigma/YARA/hunting query
              │
              ▼
           加入測試、部署、監控 FP 率
```

## 歸因：謹慎與陷阱

**歸因（Attribution）**是判斷一次攻擊是誰做的。這是 TI 中最被過度吹捧、也最容易出錯的部分。

### 歸因的技術基礎

常見的歸因依據：

- **TTP 相似度**：和某個已知組的手法高度吻合
- **工具重用**：使用相同的客製化惡意程式或框架
- **基礎設施重疊**：C2 server 和之前已歸因的活動共用 IP 或憑證
- **時區與語言**：編譯時間戳的時區、惡意程式錯誤訊息的語言
- **目標模式**：攻擊目標和已知行為者的歷史目標一致

### 歸因的陷阱

**False flag（假旗幟）操作**：攻擊者故意模仿其他組的手法或植入其語言 artifact，讓你歸因錯誤。2017 年 Fancy Bear 被懷疑在 Olympic Destroyer 操作中植入 Lazarus Group 的程式碼特徵。

**共用工具問題**：Metasploit、Cobalt Strike、Mimikatz 被幾百個不同的威脅行為者使用。看到 Cobalt Strike 說「這是 APT29」是錯的。

**技術指紋的複製**：OSINT 上有大量關於各個 APT 組工具的詳細分析，能力夠的行為者可以刻意複製技術指紋。

**我們的建議**：除非你有多個獨立維度的證據高度吻合，否則用「和 X 組的手法高度相似」代替「這是 X 組」。歸因在法律程序或外交行動中要求極高的確信度，SOC 的日常 IR 中歸因通常不必要，反而會分散注意力。

## 別把 TI 當萬靈丹

TI 的常見誤用：

1. **把 IOC 清單當安全感**：「我們有 10 萬條 IOC 在 SIEM 裡」不代表你更安全，只代表你的 SIEM 要處理更多查詢
2. **照單全收不驗證**：從公開 feed 拿來的 IOC 不經過本地 context 驗證就全部上線，結果封掉了正常業務 IP
3. **把 strategic TI 報告當 tactical 用**：大廠發的 APT 報告讀起來很酷，但裡面的 IOC 很可能已經兩個月前就被攻擊者廢棄了
4. **忽略 TI 的衰減速度**：有個概念叫 TI **time-to-live（TTL）**，IP IOC 的有效期可能只有幾天，TTP 的有效期是幾年。你的 feed 管理要對應這個衰減速度

## 具體範例

### 範例 1：intel-driven detection 的正確流程

你的組織是金融業。Mandiant 發布報告：FIN11（以 Clop 勒索軟體聞名）最近針對金融機構，利用 MOVEit 的漏洞（CVE-2023-34362），並且在 post-exploitation 階段用 WEB shell 建立持久化，再用 Rclone 外傳資料。

你的動作：

1. ATT&CK 對映：T1505.003（Server Software Component: Web Shell）、T1567.002（Exfiltration to Cloud Storage: Exfiltration to Cloud Storage）
2. 確認你有沒有 MOVEit 在環境中 → 有，3 台
3. 查涵蓋度矩陣：Web shell 創建偵測有，但 Rclone 外傳沒有
4. 補 Sigma 規則：偵測 `rclone.exe` 或其 hash，偵測異常的 HTTPS 上傳到 mega.nz/storj 等
5. IOC 部分：把報告中的 C2 IP 和 hash 加入 SIEM，但標記 TTL 為 2 週

這個流程有意義。不是因為 IOC，而是因為 TTP 分析觸發了一條有效的新偵測規則。

### 範例 2：照單全收 IOC 的代價

某 SOC 把一個公開的 Threat Feed 直接匯入 SIEM，啟用「任何連線到清單中 IP 立即告警」。

第三天：120 條高嚴重度告警，全部都是員工連線到某個 SaaS 服務的 CDN 節點，那個節點剛好被錯誤標記在 feed 裡。分析師花了 4 小時確認是 FP。

更糟的是，同一天有一條真實的 C2 beacon 告警，被淹沒在 120 條 FP 裡，沒有被調查。

### 範例 3：歸因錯誤導致應對策略偏差

某公司被入侵後，外部鑑識廠商歸因為「某中國 APT 組」，理由是使用了 PlugX 後門。

基於這個歸因，公司花了 3 個月強化針對中國 APT 的防禦：阻擋中國 IP 範圍、部署針對特定 APT 工具的 YARA 規則。

事後調查：入侵者實際上是一個東歐犯罪組織，他們購買了 PlugX 的源碼並稍加修改。他們的主要目的是安裝後門賣給其他人，不是情報竊取。

結果：公司的防禦重點完全搞錯，真正的攻擊者用的 TTP 反而沒有被偵測。

## 踩雷

1. **用 IOC 的數量評估 TI 計畫的價值**：10 萬條 IOC 不代表 TI 計畫有效，MTTD 才是指標。

2. **TIP 買了但沒整合工作流**：MISP 裝好了，但分析師還是手動查 VirusTotal，TIP 只是資料倉庫而不是作業流程的一部分。

3. **TI 報告讀完就丟**：讀完 Mandiant 或 CrowdStrike 的 APT 報告，要做的事是提取 TTP、對映 ATT&CK、查涵蓋度、補規則。不做這一步，報告就只是消遣讀物。

4. **歸因輕易公開**：內部用 ATT&CK 對映是合理的，但向客戶或管理層說「這是某 APT」需要極高的確信度，說錯了會損害公信力。

5. **忽略 ISAC 的行業情報**：金融業的 FS-ISAC、醫療業的 H-ISAC 提供針對特定行業的情報，品質通常比公開 feed 高，因為是行業內的真實事件分享。

## 進階延伸

- **Diamond Model of Intrusion Analysis**：把攻擊者（Adversary）、基礎設施（Infrastructure）、能力（Capability）、受害者（Victim）四個維度關聯起來分析，提供比 IOC-only 更豐富的分析框架。
- **Threat Actor Profiling**：長期追蹤特定威脅行為者，建立他們的 TTP 演化歷史、基礎設施復用模式。需要足夠的 data 積累，適合行業 CERT 或大型 SOC。
- **Counter-TI / Adversary OPSEC**：研究攻擊者如何主動操作他們的 OPSEC 來混淆歸因——這幫助你理解 TI 的局限性，也幫助你設計更難繞過的偵測。

## 本章重點整理

- TI 分三層：Strategic/Operational/Tactical，大多數組織只用 Tactical 層，效果自然受限
- Pyramid of Pain：IP/hash 最容易換，TTP 最難改，偵測要往金字塔頂端靠
- IOC feed 有時效問題，IP 可能幾天就過期，要設定 TTL 管理
- MISP 是開源 TIP，STIX/TAXII 是標準格式，讓 TI 消費和分發標準化
- intel-driven detection：TI 報告 → ATT&CK 對映 → 涵蓋度查詢 → 補規則，這才是把 TI 落地的正確流程
- 歸因要謹慎，false flag、共用工具、技術指紋複製都會讓歸因錯誤

## 自我檢核

- [ ] 我能說出 TI 三個層次的差別，以及哪個層次的時效最短
- [ ] 我能解釋 Pyramid of Pain，並且說出為什麼 TTP-based 偵測比 IOC-based 偵測更持久
- [ ] 我能描述 intel-driven detection 的完整流程：從 TI 報告到新偵測規則
- [ ] 我知道 STIX 和 TAXII 各自是什麼，以及 MISP 在其中扮演的角色
- [ ] 我能說出歸因的三種常見陷阱，以及為什麼在 IR 中歸因通常不是首要任務

## 延伸閱讀

1. **MITRE ATT&CK 網站 — Threat Intelligence 整合說明**
   - 讀 ATT&CK 的 Groups 和 Software 頁面，看如何把 TI 報告對映到 ATT&CK
   - 關聯：本章 intel-driven detection 的操作方式，直接連接到 Ch 10 涵蓋度矩陣
   - [https://attack.mitre.org/groups/](https://attack.mitre.org/groups/)

2. **MISP 官方文件 — Threat Sharing**
   - 讀 MISP Galaxy 說明和 MISP → TheHive 整合章節
   - 關聯：本章 TIP 實作，與 Ch 35 的 TheHive case management 整合形成完整 SOC 工具鏈
   - [https://www.misp-project.org/documentation/](https://www.misp-project.org/documentation/)

3. **STIX 2.1 規格（OASIS 標準）**
   - 讀 Object 類型定義（indicator、attack-pattern、threat-actor、relationship）
   - 關聯：理解 TI 資料模型，幫助你寫 MISP import script 或評估商業 TIP
   - [https://docs.oasis-open.org/cti/stix/v2.1/stix-v2.1.html](https://docs.oasis-open.org/cti/stix/v2.1/stix-v2.1.html)

4. **"Intelligence-Driven Computer Network Defense" — Hutchins, Cloppert, Amin（Lockheed Martin, 2011）**
   - Kill Chain 的原始論文，說明 TI 如何驅動防禦決策
   - 關聯：intel-driven detection 的理論基礎，搭配本章讀效果最好

5. **Diamond Model of Intrusion Analysis — Caltagirone, Pendergast, Betz**
   - 讀 Section 2 和 3，理解四個維度的關係和 Activity Thread
   - 關聯：補充 ATT&CK 的 TTP 分析視角，對複雜攻擊活動的 TI 分析特別有用

→ [Ch 37 事後報告與 MTTD/MTTR 指標](./37-post-incident-reporting.md)
