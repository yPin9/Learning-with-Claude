# Ch 1 — 藍隊全貌與 Purple Team 框架

> 目標：在切入任何工具或技術之前，先搞清楚「藍隊」這個詞背後藏著哪些不同角色、他們每天真正在做什麼、彼此怎麼分工，以及為什麼從紅隊視角切入是學習防禦最有效率的方式。

## 為什麼要從這裡開始

你已經知道怎麼打。你會寫 shellcode、會做 heap exploitation、知道 Kerberoasting 怎麼跑、會 SSRF chain 出去打 IMDS。問題不是技術能力不夠，而是你從沒被迫思考「這個動作在對面的 SIEM 裡長什麼樣子」。

大多數人學藍隊的路徑是從工具開始：先學 Splunk 查詢語法，再學 Elastic 的 EQL，再讀一堆告警規則。這條路的問題是你不知道這些規則在防什麼，調 threshold 的時候只能靠感覺，看到 False Positive 也不知道根本原因在哪。

這門課反過來：我們從你已經知道的攻擊手法出發，問「攻擊發生時，資料留在哪、長什麼形狀、什麼時候消失」。這是 Purple Team（紫隊）框架的核心：攻守知識在同一張腦袋裡，不是兩個互不相識的部門在猜對方在做什麼。

另一個問題：藍隊的角色不是一個，是五個，彼此職責、技能需求、時間尺度都不同。在開始學工具之前，先搞清楚這個生態系的分工，才知道自己在學的東西屬於哪一層、服務什麼目的。

## 先建心智模型：藍隊不是一個角色

「藍隊」在實務上至少拆成五種不同的工作，彼此職責、時間尺度、深度廣度都不同：

```
                    ┌─────────────────────────────────────────────────┐
                    │                   企業安全組織                    │
                    └─────────────────────────────────────────────────┘
                                          │
              ┌───────────────────────────┼───────────────────────────┐
              │                           │                           │
    ┌─────────▼──────────┐    ┌───────────▼───────────┐   ┌──────────▼─────────┐
    │  SOC               │    │  Threat Hunting        │   │  Detection Eng.    │
    │  Security Ops Ctr  │    │  主動威脅狩獵            │   │  偵測工程           │
    │                    │    │                        │   │                    │
    │  - 監控告警 24/7    │    │  - 無告警假設入侵        │   │  - 寫告警規則        │
    │  - Triage/分類      │    │  - 假設攻擊者已在裡面    │   │  - 維護 SIEM pipeline│
    │  - 初步應變         │    │  - 自行產生假說+驗證     │   │  - 降低 FP 率        │
    │                    │    │                        │   │                    │
    └────────┬───────────┘    └───────────┬────────────┘   └──────────┬─────────┘
             │                            │                            │
             │ 高嚴重度告警               │ 發現入侵指標               │ 新規則上線
             ▼                            ▼                            │
    ┌─────────────────────────────────────────────┐                   │
    │  IR  Incident Response  事件應變             │◄──────────────────┘
    │                                             │   觸發案例
    │  - 隔離/遏制 (Containment)                  │
    │  - 根因分析 (Root Cause Analysis)            │
    │  - 范圍確認 (Scoping)                        │
    │  - 修復建議                                  │
    └─────────────────┬───────────────────────────┘
                      │ 需要深度鑑識
                      ▼
    ┌─────────────────────────────────────────────┐
    │  DFIR  Digital Forensics & IR               │
    │                                             │
    │  - 磁碟/記憶體映像 (Image) 採集             │
    │  - 時間軸重建 (Timeline Reconstruction)      │
    │  - 惡意程式逆向                              │
    │  - 法律程序保全 (Chain of Custody)           │
    └─────────────────────────────────────────────┘
```

這五個角色的**時間尺度**完全不同：

- SOC：秒到分鐘，告警進來就要決定是不是真的
- IR：小時到天，事件確認後控制傷害範圍
- DFIR：天到週，重建完整攻擊鏈
- Threat Hunting：週到月，一個 campaign 跑完出報告
- Detection Engineering：持續，每次出新 TTP 就要迭代規則

## 五個角色的具體日常工作

### SOC — Security Operations Center（安全運營中心）

SOC 分析師的一天：早上上班，SIEM 佇列裡有 300 個告警。你的工作是把它們分成「真的要處理」和「可以關掉」兩堆，然後把真的要處理的分優先順序。

典型動作：
- 看 Windows Event Log ID 4624（登入成功）+ 4625（登入失敗）的比例，判斷是不是暴力破解
- 把可疑 IP 丟進威脅情報（Threat Intelligence）平台查是不是已知 C2
- 確認告警對應的主機是不是高價值資產（Domain Controller vs 測試機）
- 寫 ticket、升級給 IR

SOC 的核心問題是**廣度**：覆蓋面要夠，但沒有時間深挖每個告警。大量 False Positive 是 SOC 的職業傷害，會導致告警疲勞（Alert Fatigue），讓真正的攻擊埋進雜訊裡。

### IR — Incident Response（事件應變）

IR 工程師接到升級案件後，他的問題是：「這台機器上到底發生了什麼，有沒有橫向移動，範圍有多大？」

典型動作：
- 用 EDR（Endpoint Detection and Response）平台做主機快照查詢
- 跑 Process Tree 找異常父子關係（`winword.exe` → `powershell.exe` 是很典型的 malspam 指標）
- 網路流量分析找 C2 beacon 特徵（固定 interval 的 DNS query、JA3 fingerprint）
- 決定隔離時機：太早隔離攻擊者消失但範圍不清楚，太晚勒索軟體跑完加密

### DFIR — Digital Forensics & Incident Response

DFIR 加上了「鑑識」這一層。IR 可能在 EDR 上點點看，DFIR 要拿到實際的磁碟映像（dd、FTK Imager）和記憶體傾印（WinPmem、LiME），做離線分析。

典型動作：
- Autopsy / Volatility 3 跑完整時間軸，找 `$MFT`、`$LogFile`、`$UsnJrnl` 裡的記錄
- 從 prefetch 檔（`%WINDIR%\Prefetch\*.pf`）重建程式執行歷史
- 記憶體裡找 process injection 的痕跡（`malfind` plugin、PE header 不在磁碟上）
- 如果要進法律程序，Chain of Custody 的文件必須完整，映像要有 MD5/SHA-256

### Threat Hunting（威脅狩獵）

Threat Hunter 的出發點是「假設告警沒有抓到一切」。他不等告警，主動去資料裡找已知 TTP 的蛛絲馬跡。

典型流程：
1. 建立假說：「APT41 的 KEYPLUG 惡意程式用 RC4 over TLS，會有特定的 JA3s 指紋」
2. 在 SIEM / EDR 的歷史資料裡搜尋這個特徵
3. 驗證假說：找到了就升 IR，沒找到就記錄「我們覆蓋了這段時間的這個 TTP」
4. 把有效的查詢轉成長駐告警規則，交給 Detection Engineering

Threat Hunting 的前提是有足夠的資料保留期（Retention）和夠好的資料品質。如果 log 根本沒有 process command line，假說沒辦法驗。

### Detection Engineering（偵測工程）

Detection Engineering 是最接近軟體工程的角色。他們把 Threat Hunter 的查詢、IR 發現的新 TTP、紅隊演練的結果，轉成可維護的告警規則，並且追蹤規則效能（True Positive Rate、False Positive Rate、Coverage）。

典型動作：
- 收到 IR 報告說「攻擊者用 `certutil.exe -urlcache -f` 下載 payload」，寫一條 Sigma 規則監控這個 command line pattern
- 跑 ATT&CK Navigator 找「哪些 Technique 我們完全沒有規則覆蓋」，排優先順序
- 每週跑規則效能報告：哪條規則 TP/FP 比例最差，找原因（log 來源不穩定？環境有特殊合法使用案例？）
- 版本控制所有規則，每次改動有測試案例，CI/CD pipeline 驗證

Detection Engineering 的成果是可量化的：規則數量、ATT&CK 技術覆蓋率、MTTD（Mean Time to Detect）趨勢。這讓它成為整個防禦體系中少數能用工程指標驅動改善的角色。

## 一個關鍵現實：多數組織沒有五個獨立角色

在台灣多數公司，尤其是非 Fortune 500，「藍隊」可能就是三個人全包。同一個人早上做 SOC 工作、下午做 IR、晚上寫 YARA 規則。這沒有問題，但你需要知道：

- 角色混合時最常被犧牲的是 Threat Hunting 和 Lessons Learned，因為它們沒有立即的告警輸入驅動
- 「只要 EDR 廠商有規則就夠了」是中小型組織的致命誤解，廠商規則永遠落後最新 TTP 數週到數月
- 紅隊工程師進小型組織往往需要一人扮演 Detection Engineering + IR 雙角色，這反而是建立全面視角的機會

MSSP（Managed Security Service Provider，管理式安全服務）和 MDR（Managed Detection and Response）是外包這些功能的常見選項，但外包不等於放棄自有能力——你還是需要有人能讀懂廠商的報告、問對的問題、做獨立的驗證。

## 具體案例：一次勒索軟體事件的分工

這是最能說明分工的場景。攻擊者進來、橫向移動、部署 Ransomware，過程中五個角色各自在做什麼：

```
時間軸                角色              動作
─────────────────────────────────────────────────────────────────
T+0   攻擊者用 VPN  SOC               Impossible Travel 告警（北京→台北 1hr）
      帳號 phishing                    → 分析師看了，覺得可能是 VPN，關掉
      → 進入內網

T+2h  攻擊者做      Detection Eng.    （沒有告警，規則空白）
      Kerberoasting

T+6h  橫向到 DC     SOC               4768 Kerberos TGT request 量異常告警
                                      → 升級給 IR

T+7h               IR                確認 DC 被觸及，啟動 Playbook
                                      → 隔離受害主機，保留記憶體快照

T+8h               DFIR              拿到 DC 記憶體傾印
                                      → Volatility `dumpfiles` 找 lsass
                                      → 找到 mimikatz 在記憶體裡的 PE header

T+12h 攻擊者部署   IR + DFIR          確認橫向範圍：23 台主機
      Ransomware                      → 決定全部隔離，保留磁碟映像

T+3d               DFIR              重建完整時間軸：
                                      初始進入點 → Kerberoasting → PtH →
                                      WMI 橫向 → Cobalt Strike beacon →
                                      vssadmin delete shadows →
                                      .lockbit 加密

T+2w  事後         Threat Hunting    有沒有其他機器也有這個 beacon 的 JA3?
                   Detection Eng.    新規則：Kerberoasting 服務帳號流量
                                     新規則：vssadmin delete shadows 偵測
```

注意 T+0 那個關掉的告警：這是真實世界常見的 miss。Impossible Travel 的規則設計問題（太多 VPN 合法使用者），導致分析師訓練成「這種告警都是 FP」然後習慣性關掉。Detection Engineering 的工作就是讓這個規則只在真正可疑時響。

## 反應式 vs 主動式、廣度 vs 深度

| 維度 | SOC | IR | DFIR | Threat Hunting | Detection Eng. |
|------|-----|----|------|----------------|----------------|
| 工作觸發 | 告警進來（反應式） | 升級案件（反應式） | 鑑識需求（反應式） | 自發假說（主動） | 持續迭代（主動） |
| 時間壓力 | 極高（分鐘級） | 高（小時級） | 中（天級） | 低（週月） | 低（持續） |
| 技術深度 | 廣但淺 | 中 | 深 | 深 | 中到深 |
| 覆蓋廣度 | 極廣 | 事件範圍 | 案件範圍 | 假說範圍 | 全面 |
| 紅隊知識需求 | 低 | 中 | 高 | 高 | 高 |
| 輸出 | Ticket / 升級 | 遏制 / 報告 | 時間軸 / 法律文件 | 新假說 / 規則 | 告警規則 |

## 紅隊背景的人在哪裡最有優勢

直白說：Threat Hunting 和 Detection Engineering 是紅隊人進藍隊最有競爭力的入口。

原因：
- Threat Hunter 需要知道攻擊者怎麼想、TTP 的細節，才能建正確的假說。SOC 分析師不知道 Kerberoasting 的 Kerberosable SPN 和 Service Ticket 的比例關係，你知道。
- Detection Engineer 需要知道攻擊者會繞什麼規則、怎麼避開偵測。你寫過繞 AV 的 shellcode、知道 process injection 有哪些變體，這直接轉化成「這個規則可以被這樣繞，要加這個 condition」的能力。
- DFIR 的記憶體分析和惡意程式逆向對有 pwn 底子的人來說學習曲線相對平。`malfind` 找到可疑記憶體區段，接下來要判斷是不是 shellcode，你比純 DFIR 背景的人有感覺。

SOC 的 Tier 1 工作（看告警、查 IP、寫 ticket）紅隊技術優勢最低，但它是理解防禦資料長什麼樣的最快方式。建議把它當做「補 context」的學習過程，而不是職涯目標。

## Purple Team 框架：這門課的切入角度

Purple Team 的定義：紅隊（攻擊）和藍隊（防禦）在同一個演練框架裡協作，而不是完全對抗。

傳統的 Red Team Engagement 結束後交一份報告，Blue Team 拿著報告試著複現 TTP、更新規則。問題是這個週期很長，而且資訊在傳遞過程中失真。

Purple Team 讓這個循環縮短：攻擊者執行一個 TTP，防禦者立刻問「你剛才做了什麼？用什麼工具？哪個帳號？什麼時間？」然後去 SIEM 裡找，如果找不到就找原因——是沒有 log、log 格式不對、還是規則沒有這個 condition？

```
攻擊執行一個 TTP
        │
        ▼
防禦者去找對應的 log / 告警
        │
   ┌────┴────┐
   │  找到了  │     找到 → 確認規則有效 → 下一個 TTP
   └────┬────┘
        │
   找不到
        │
   ┌────▼──────────────────────────┐
   │  診斷：                        │
   │  1. Log 根本沒採集              │ → 修 log 採集配置
   │  2. Log 有但格式不對             │ → 修 parser
   │  3. 有資料但沒有規則             │ → 寫規則
   │  4. 規則有但 threshold 太高      │ → 調參數
   └───────────────────────────────┘
```

這門課把這個框架用在教學上：每個攻擊 TTP 我們都會問「防禦者看到的資料長什麼樣」，每個偵測技術我們都會問「攻擊者有哪些辦法繞」。

這不是把你訓練成「只能防、不能攻」，而是讓你的攻擊知識有了防禦的對映，讓你在任何一邊工作都比只有單邊知識的人看得更深。

## 錯誤直覺 → 正確認識

**「藍隊的工作就是看 dashboard 等告警響」**
→ SOC Tier 1 確實有這個成分，但 Threat Hunting 和 Detection Engineering 是主動產出的角色。把藍隊等同於被動監控是紅隊對藍隊最常見的誤解，結果輕視防禦的複雜度。

**「防禦就是買工具，SIEM 裝好就行了」**
→ SIEM 沒有資料、資料沒有 parser、parser 沒有覆蓋攻擊路徑的告警規則，全部白費。工具是必要條件，不是充分條件。Detection Engineering 的核心價值是把工具變成有效的防禦。

**「紅隊做完 Pentest，Blue Team 修漏洞就好了」**
→ Pentest 找的是漏洞，不是偵測缺口。攻擊者下次用不同的工具打同樣的路徑，修了漏洞但沒有偵測能力，你不知道有沒有人在打你修好的邊界旁邊的其他點。Purple Team 的目標是同時改善偵測，不只修漏洞。

**「DFIR 就是做鑑識報告，跟 IR 是一回事」**
→ IR 的重點是「現在這個事件怎麼收尾」，DFIR 的重點是「完整還原發生了什麼、有沒有法律效力的證據」。IR 可以在 EDR 上查，DFIR 要做 bit-level 的映像保全。在小公司這兩件事往往同一個人做，但技能和思維框架不同。

**「Purple Team 就是紅藍隊一起坐在同一間辦公室」**
→ Purple Team 是一個**協作框架和方法論**，不是組織架構。完全獨立的紅藍隊也可以用 Purple Team 方式運作（PTES、TIBER-EU 都有定義流程）。「大家坐在一起」是形式，測試覆蓋和迭代速度才是目標。

## 進階：從 ATT&CK 到 D3FEND

MITRE ATT&CK 你可能已經用過——它是攻擊技術的分類框架（Technique ID 格式：T1055 = Process Injection）。ATT&CK 讓紅隊和藍隊用同一套語言講話。

MITRE D3FEND 是防禦對應版本：給每個攻擊技術對應「哪些防禦技術可以對抗它」，並且明確列出這些防禦技術依賴哪些 digital artifact。例如 T1003.001（LSASS Memory Dump）對應的 D3FEND artifact 是 `lsass.exe` 的記憶體讀取系統呼叫，防禦技術是 Process Memory API 的監控。

這個對映關係是 Detection Engineering 寫規則的起點：你不是在猜要監控什麼，你是從已知攻擊路徑反推「攻擊必然要操作什麼資源」，然後在那個資源上插偵測點。

## 本章重點整理

- 藍隊包含 SOC、IR、DFIR、Threat Hunting、Detection Engineering 五個不同角色，時間尺度和深度廣度各不相同
- SOC 和 IR 是反應式，Threat Hunting 和 Detection Engineering 是主動式
- SOC 追廣度（覆蓋所有告警），DFIR 追深度（完整還原一個案件）
- 紅隊背景在 Threat Hunting 和 Detection Engineering 有直接優勢，因為需要攻擊者視角建假說和評估繞過風險
- Purple Team 是讓攻防知識互相對映的協作框架，縮短「攻擊發生 → 偵測建立」的週期
- 這門課的核心問題是：攻擊發生時，防禦者看到的資料長什麼樣，如何用這些資料找到攻擊

## 自我檢核

- [ ] 我能不查資料說出 SOC 和 DFIR 的主要工作差異（不是定義，是具體日常任務）
- [ ] 我能描述一個完整事件（如勒索軟體）中五個角色各自在哪個階段介入、做什麼
- [ ] 我理解為什麼「告警疲勞」是 SOC 的核心問題，以及它怎麼讓攻擊得逞
- [ ] 我能解釋 Purple Team 框架如何縮短偵測迭代週期（用圖示說明更好）
- [ ] 我知道自己的紅隊技能在哪個藍隊角色最直接有用，以及原因

## 延伸閱讀

1. **SANS FOR508: Advanced Incident Response, Threat Hunting, and Digital Forensics — Course Outline**
   [https://www.sans.org/cyber-security-courses/advanced-incident-response-threat-hunting-training/](https://www.sans.org/cyber-security-courses/advanced-incident-response-threat-hunting-training/)
   看課綱就能理解 DFIR 和 Threat Hunting 的知識範圍邊界。特別注意他們如何把 Windows Forensic Artifacts 和 Hunting 章節並排，這就是 Purple Team 視角在 curriculum 層面的體現。

2. **The DFIR Report — Real Intrusions by Real Attackers**
   [https://thedfirreport.com/](https://thedfirreport.com/)
   每篇報告都是真實案例的完整還原：初始進入 → 偵察 → 橫向 → 目標行動。配合 ATT&CK Technique ID 閱讀，直接訓練「攻擊動作 ↔ 藍隊可見資料」的對映感。建議從最近的 Cobalt Strike 或 IcedID 系列開始。

3. **MITRE ATT&CK: Getting Started**
   [https://attack.mitre.org/resources/getting-started/](https://attack.mitre.org/resources/getting-started/)
   不是要你背 Technique ID，而是要理解 ATT&CK 的資料模型：Tactic / Technique / Sub-technique / Procedure 的層級關係，以及 Detection 欄位的格式。這是之後整門課的共同語言。

4. **MITRE D3FEND**
   [https://d3fend.mitre.org/](https://d3fend.mitre.org/)
   從攻擊 Technique 出發，找「防禦這個攻擊需要監控什麼 digital artifact」。對 Detection Engineering 的邏輯理解特別有用。選三到五個你熟悉的攻擊技術，看 D3FEND 對應的防禦技術，問自己「這個偵測點我能繞嗎」。

5. **Jared Atkinson: The Detection Maturity Level Model (DML)**
   [https://ryanstillions.blogspot.com/2014/04/the-dml-model_21.html](https://ryanstillions.blogspot.com/2014/04/the-dml-model_21.html)
   一篇 2014 年的舊文，但概念至今仍是偵測策略的基礎：偵測基於 Hash（最脆弱，改一個 byte 就繞）、IP、Domain、Network/Host Artifact（TTPs 層），一路到 Behavior 層的偵測最難繞。理解這個模型是評估任何告警規則品質的框架。

---

下一章把這個框架具體化：從 ATT&CK 的 Tactic / Technique 層級，學會把你的攻擊手法翻譯成防禦者的語言。

→ [Ch 2 攻擊者視角轉防守：MITRE ATT&CK](./02-attacker-to-defender-attack.md)
