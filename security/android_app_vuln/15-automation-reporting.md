# Ch 15 — 自動化掃描與報告撰寫

> **目標**：把前面十四章手動挖洞的能力**自動化並產品化**。上半場學工具：**MobSF**（一鍵靜動態掃）、**semgrep / mobsfscan**（寫規則批量抓程式碼層漏洞）、**apkleaks**（挖 secret/URL/endpoint）。下半場學這條工作流真正的終點——**triage 去誤報**（工具吐一堆「疑似」，你要判成「確定/誤報」）與**報告撰寫**（標題／影響／CVSS／重現步驟／PoC／修復，以及 bug bounty 報告要點）。讀完你能把「掃描器輸出一坨」變成「一份能拿去 bug bounty 或評估、別人照著能複現」的報告。

> **環境**：MobSF/mobsfscan/apkleaks 的實跑需 Android 靶場與工具鏈，標「**未實測，理論預期行為**」並給驗證步驟。**semgrep 的 pattern 匹配邏輯與去誤報判斷、CVSS 計算，用 Python 3.12 在本 repo 沙箱實跑**，標「**實際輸出**」。合法邊界：只掃你有權測試的目標；bug bounty 一定先看 scope。

## 為什麼需要這個？

到這裡你已經會手動打十四類洞了。那為什麼還要自動化？三個現實：

- **覆蓋率**：一個 App 幾百個元件、上千個方法，手動逐一看不現實。自動化先把「可疑點」全掃一遍，你的人腦時間留給「驗證與判斷」。
- **重複性**：評估要可重複、可交付。「我掃了、這是結果」比「我憑經驗看了看」更能站得住。MobSF 報告、semgrep 規則就是可重複的證據。
- **但工具會騙你**：這是最關鍵的一點。掃描器的輸出**全是「疑似」**——它看到 `setJavaScriptEnabled(true)` 就報 WebView 風險，但它不知道那個 WebView 載不載入外部 URL、有沒有暴露 JS 介面。**沒 triage 過的掃描結果不是漏洞，是待辦清單。** 直接把 MobSF 報告貼給 bug bounty，會被秒退還扣信譽。

所以這章的重心不是「怎麼按工具的按鈕」（那是最不值錢的部分），而是**怎麼把工具的噪音變成訊號、把訊號寫成一份專業報告**。這才是把前十四章能力變現的最後一哩。

## 先建立直覺：自動化 + 人工的分工

自動化不是取代你，是幫你分流。整條工作流長這樣：

```
   ┌──────────── 自動化（機器做：廣度）─────────────┐
   │                                                │
APK ──► MobSF 靜態 ──► 一堆「疑似」（Manifest/權限/    │
   │    （反編譯+規則）    程式碼模式/secret/憑證）      │
   ├──► semgrep/       ──► 程式碼層規則命中             │
   │    mobsfscan                                    │
   ├──► apkleaks       ──► URL/endpoint/API key/secret │
   │                                                │
   │  （選）MobSF 動態  ──► 執行期行為/流量/檔案落地    │
   └────────────────────────┬───────────────────────┘
                            ▼
         ┌──────── triage（人做：判斷）────────┐
         │  逐條問：這真的可利用嗎？            │
         │   ├─ 確認為真 → 手動做出 PoC        │
         │   ├─ 需條件 → 標「條件成立才可利用」 │
         │   └─ 誤報 → 標 FP + 理由，剔除      │
         └────────────────┬───────────────────┘
                          ▼
         ┌──────── 報告（人做：溝通）──────────┐
         │  標題/影響/CVSS/重現/PoC/修復        │
         │  → 評估報告 或 bug bounty 提交       │
         └─────────────────────────────────────┘
```

**機器負責廣度（掃全）、人負責判斷（去誤報）與溝通（寫報告）**。這條線任何一段偷懶，結果都廢：只掃不 triage = 一堆噪音；triage 完不寫清楚 = 別人無法複現、拿不到 bounty。

## 工具一：MobSF —— 一鍵靜動態掃

MobSF（Mobile Security Framework）是這條線的起點，把「反編譯 + Manifest 分析 + 程式碼規則 + secret 掃描 + 憑證檢查」一次做完，出一份網頁報告。

用法（Docker 起最省事）：

```bash
# 起 MobSF（本機 8000 埠）
docker run -it --rm -p 8000:8000 opensecurity/mobile-security-framework-mobsf:latest
# 瀏覽器開 http://localhost:8000，上傳 APK，等靜態掃完
# 或用 REST API 自動化上傳與取報告
```

> **未實測，理論預期行為**（本 repo 沙箱無法跑 MobSF/Docker Android 分析）。你在自己機器跑，靜態掃完會得到一份含以下區塊的報告，**每一區塊都要當「疑似清單」看，不是結論**：

| MobSF 報告區塊 | 它給你什麼 | 你要做的 triage |
|---|---|---|
| **Manifest Analysis** | exported 元件、debuggable、`allowBackup`、custom permission level | 對照 Ch 3/13：exported 是真暴露還是有 permission 保護？ |
| **Code Analysis** | WebView 設定、crypto 誤用、SQLi 模式、logging | 每條回到對應章節驗：這行程式碼在 reachable path 上嗎？ |
| **Secrets** | 硬編碼字串疑似 key/token | Ch 12：是真 secret 還是測試值/公開 key？ |
| **Network Security** | `network_security_config`、cleartext、pinning | Ch 9：真的允許明文嗎？pinning 是否形同虛設？ |
| **Certificate** | 簽名 scheme、debug 憑證 | Ch 2：用了哪些 scheme？是不是 debug key 簽的？ |

MobSF 也能做**動態分析**（接 AVD/真機，跑 App 觀測流量、檔案操作、API 呼叫），但它需要一台配好的裝置，且對加固/反模擬器 App 常卡住。動態分析我們前面章節多半用 Frida/drozer 手動做更精準，MobSF 動態當「快速初篩」用。

> **MobSF 的定位要擺正**：它是**廣度優先的初篩器**，不是「按一下就出報告」的自動評估工具。它的價值在「一次把攻擊面掃一遍、不漏區塊」，不在「幫你判斷」。判斷永遠是你的活。

## 工具二：semgrep / mobsfscan —— 程式碼層規則掃描

MobSF 內建規則有限。要更精準、可自訂的程式碼層掃描，用 **semgrep**（通用語意 grep，寫 pattern 匹配 AST）與 **mobsfscan**（MobSF 團隊做的行動專用靜態掃，底層也用 semgrep 規則）。

mobsfscan 開箱即用：

```bash
pip install mobsfscan
# 對反編譯出的 Java 原始碼（jadx 輸出）掃
mobsfscan --json -o out.json ./app_java/sources/
```

semgrep 的威力在**你能自己寫規則**。一條抓「WebView 開了 JS 又允許檔案存取」的規則長這樣：

```yaml
# webview-js-file.yml
rules:
  - id: webview-js-and-file-access
    languages: [java]
    severity: WARNING
    message: WebView 同時開啟 JavaScript 與檔案存取，可能導致本地檔案洩漏（Ch 8）
    patterns:
      - pattern: $WS.setJavaScriptEnabled(true);
      - pattern-inside: |
          $WS.setAllowFileAccess(true);
          ...
```

```bash
semgrep --config webview-js-file.yml ./app_java/sources/
```

### semgrep 的匹配邏輯：為什麼它比 grep 準（本 repo 實跑）

semgrep 比 `grep` 強在它懂**語法結構**——`grep "setJavaScriptEnabled(true)"` 會把註解裡的、字串裡的都撈出來（誤報），semgrep 走 AST 只匹配真的程式碼。我用 Python 模擬這個「匹配 + 排除註解」的判斷邏輯，展示它為什麼能少報（**實際輸出**，Python 3.12 實跑）：

```python
# semgrep_demo.py —— 模擬 setJavaScriptEnabled(true) 的匹配 + 排除註解/false 引數
import re
snippets = {
    "vuln_A": 'webView.getSettings().setJavaScriptEnabled(true);',
    "vuln_B": 'ws.setJavaScriptEnabled(true);\nws.setAllowFileAccess(true);',
    "safe_C": 'webView.getSettings().setJavaScriptEnabled(false);',   # 引數 false
    "fp_D":   '// setJavaScriptEnabled(true) is documented here',     # 註解，誤報來源
}
PATTERN = re.compile(r'setJavaScriptEnabled\s*\(\s*true\s*\)')  # 只匹配引數 true
COMMENT = re.compile(r'^\s*(//|\*|/\*)')                        # 排除註解行
for name, code in snippets.items():
    hit = any(PATTERN.search(l) and not COMMENT.match(l) for l in code.splitlines())
    print(f"{name:8} -> {'MATCH(疑似)' if hit else 'no match'}")
```

**實際輸出**：

```
vuln_A   -> MATCH(疑似)
vuln_B   -> MATCH(疑似)
safe_C   -> no match
fp_D     -> no match
```

四行讀出精髓：`vuln_A`/`vuln_B` 命中（真開了 JS）；`safe_C` 因為引數是 `false` 不命中（**semgrep 看引數值，`grep` 不會**）；`fp_D` 在**註解裡**不命中（排除註解）。這就是「語意匹配」少誤報的核心——它不是字串比對，是**「這段程式碼是不是真的做了那件事」**。真的 semgrep 用完整 AST，比這個 regex 模擬更精準，但判斷精神一模一樣：**匹配結構、排除非程式碼、看引數**。

## 工具三：apkleaks —— 挖 secret、URL、endpoint

apkleaks 專掃「藏在 APK 裡的字串型情報」：URL、API endpoint、API key 樣式、AWS/Firebase 之類的雲端 key。

```bash
pip install apkleaks
apkleaks -f target.apk -o apkleaks_out.txt
```

> **未實測，理論預期行為**。它反編譯 APK、用一組正則掃字串，輸出分類的命中清單（URLs、IP、可疑 key pattern）。**它的輸出誤報率高**——會把一堆無害的 URL、SDK 內建的公開 endpoint、格式像 key 但其實是雜湊的字串全撈出來。這正是需要重度 triage 的典型：apkleaks 給你「線索」，不是「漏洞」。

apkleaks 最有價值的用法是**找 endpoint 與 Firebase/S3 之類的後端**——撈到一個 `https://xxx.firebaseio.com`，你就有了一條「去測後端授權」的線（呼應 Ch 12 secret 洩漏，很多真實 bounty 是「App 洩漏了後端 URL + key，直接打後端」）。

## Triage：把「疑似」變「確定」

這是全章最重要的一節。工具吐給你一份混著真洞、需條件、誤報的清單，triage 就是逐條分流。每一條問三個問題：

```
每一條掃描命中，問：
  1. 這段程式碼/設定在「可達路徑」上嗎？
       （reachable？還是 dead code / 從沒被呼叫 / 測試碼）
  2. 觸發它需要什麼前提？
       （攻擊者可控嗎？需要 root/實體接觸/使用者配合嗎？）
  3. 我能做出 PoC 實際觸發嗎？
       （能 → 確定；不能但邏輯上成立 → 標「理論可行、需 X」；完全觸發不了 → FP）
         ↓
   分三堆： [確定，附 PoC]  [條件成立才可利用]  [誤報 FP + 理由]
```

三個判斷原則：

- **可達性優先**：掃描器不知道一段程式碼會不會被執行。一個 `exported=true` 的 Activity 若根本沒被任何 intent-filter 觸發、或內部立刻 `finish()`，那就不是洞。回到 Ch 3/4 手動確認它真的能被外部觸發並做出有意義的事。
- **前提要寫清楚**：很多「洞」需要前提（root、實體接觸、使用者裝了另一個惡意 App、targetSdk 低）。前提不是讓你剔除它，是讓你**在報告裡誠實標注嚴重度**——「需 root 才能利用」的洞嚴重度遠低於「遠端無互動」。
- **能不能做出 PoC 是黃金標準**：掃描器說「可能 SQLi」，你用 drozer 真的注出資料才叫確定（Ch 6）。**沒有 PoC 的漏洞在 bug bounty 幾乎不被接受。** triage 的產出物，最好每條確定項都配一個能重現的 PoC。

**誤報也要記錄**，別默默刪掉——標「FP + 為什麼」。這讓報告可信（審閱者知道你看過、判斷過，不是漏掉），也讓下次掃描能自動排除。

## 報告撰寫：從「我找到洞」到「別人能複現並修」

triage 完的每個確定漏洞，寫成標準結構。這套結構適用評估報告與 bug bounty 提交：

```markdown
## [標題：一句話講清「什麼元件有什麼洞、能導致什麼」]
例：AndroGoat 的 exported ContentProvider 存在 SQL 注入，任意 App 可讀取全部登入資料

### 影響（Impact）
- 一句話說「攻擊者能做到什麼、對誰有什麼後果」
- 例：任何已安裝的 App 無需權限即可讀出使用者名稱與密碼明文

### 嚴重度 / CVSS
- CVSS v3.1 向量 + 分數（下面教怎麼算）
- 例：CVSS 7.5 (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N)

### 重現步驟（Steps to Reproduce）
1. 環境：AVD API 33，裝好 target.apk（版本 x.y）
2. 逐步、可照做的指令（別跳步、別假設讀者知道你腦中的上下文）

### PoC
- 實際能觸發的指令 / drozer 命令 / Frida 腳本 / adb 命令
- 附觸發後的真實輸出（截圖或文字），證明它真的成立

### 修復建議（Remediation）
- 具體、可執行的修法（不是「請加強安全」這種廢話）
- 例：將 provider 改為 exported="false"；查詢改用 parameterized query（selectionArgs）
```

三個寫報告的鐵律：

1. **標題就要能判斷嚴重度**：審閱者一天看幾十份，標題含糊（「一個安全問題」）直接被跳過。標題要 = 元件 + 洞 + 後果。
2. **重現步驟要能被陌生人照做**：你腦中的上下文（哪個 AVD、哪個版本、前置狀態）都要寫出來。「照做重現不出來」的報告在 bounty 平台會被判無效。
3. **修復建議要具體到可執行**：「使用參數化查詢，把 `selection` 的 `+` 拼接改成 `selectionArgs`」是好建議；「加強輸入驗證」是廢話。具體的修法讓報告有交付價值，也讓你顯得專業。

## CVSS：讓嚴重度可比較（本 repo 實跑）

CVSS（Common Vulnerability Scoring System）v3.1 把嚴重度量化成 0–10，讓不同漏洞可比較、讓對方分優先序。它由一組**基礎向量**算出：

- **AV**（Attack Vector）：N 網路 / A 相鄰 / **L 本地** / P 實體。安卓 IPC 漏洞多半是 **L**（同裝置另一 App），deeplink/WebView 遠端誘導是 **N**。
- **AC**（複雜度）L/H、**PR**（所需權限）N/L/H、**UI**（使用者互動）N/R。
- **S**（Scope）U 不變 / C 改變（跳出元件自身安全範圍，如 WebView 拿到系統資源）。
- **C/I/A**（機密/完整/可用性）各 H/L/N。

不用背公式，用官方計算器或腳本算。我把 v3.1 公式實作出來，算兩個本課常見漏洞的分數（**實際輸出**，Python 3.12 實跑，數值與官方 NVD 計算器一致）：

```python
# cvss.py —— CVSS v3.1 Base Score（節錄，完整見官方規範）
import math
def cvss(av,ac,pr,ui,s,c,i,a):
    AV={'N':0.85,'A':0.62,'L':0.55,'P':0.2}[av]; AC={'L':0.77,'H':0.44}[ac]
    PR_=({'N':0.85,'L':0.62,'H':0.27} if s=='U' else {'N':0.85,'L':0.68,'H':0.5})[pr]
    UI={'N':0.85,'R':0.62}[ui]
    C={'H':0.56,'L':0.22,'N':0}[c]; I={'H':0.56,'L':0.22,'N':0}[i]; A={'H':0.56,'L':0.22,'N':0}[a]
    iss=1-(1-C)*(1-I)*(1-A)
    impact=6.42*iss if s=='U' else 7.52*(iss-0.029)-3.25*(iss-0.02)**15
    expl=8.22*AV*AC*PR_*UI
    if impact<=0: return 0.0
    base=min(impact+expl,10) if s=='U' else min(1.08*(impact+expl),10)
    return math.ceil(base*10)/10

# WebView RCE：遠端惡意 deeplink 誘導，跳出 WebView 拿系統能力
print("WebView RCE  (AV:N/AC:L/PR:N/UI:R/S:C/C:H/I:H/A:H) =", cvss('N','L','N','R','C','H','H','H'))
# 硬編碼 secret 洩漏：遠端可讀，僅機密性衝擊
print("Secret 洩漏  (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N) =", cvss('N','L','N','N','U','H','N','N'))
```

**實際輸出**：

```
WebView RCE  (AV:N/AC:L/PR:N/UI:R/S:C/C:H/I:H/A:H) = 9.6
Secret 洩漏  (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N) = 7.5
```

`9.6`（Critical）與 `7.5`（High）——這兩個分數你在官方 NVD CVSS 計算器輸入同樣向量會得到一模一樣的值。**CVSS 的價值不在那個數字精確，在那組向量**：向量把「這洞怎麼被打、影響什麼」結構化地講清楚了。報告裡向量比分數更重要——審閱者看向量就懂你怎麼評的，也能挑戰你（「這真的 UI:N 嗎？不是需要使用者點連結？」）。

> **公式裡的 magic number**（`0.85`/`8.22`/`6.42`/`7.52`/`3.25`/`1.08` 等）不是我瞎湊的，是 **CVSS v3.1 規範明定的權重常數**——AV/AC/PR/UI 的數值對照表、Exploitability 係數 `8.22`、Impact 係數等，全部出自 FIRST 官方規範文件。你不需要記，但要知道它們有出處、不是魔數。

## Bug bounty 報告要點

拿去 bug bounty 平台（HackerOne/Bugcrowd/Google）提交，除了上面的結構，多幾條：

- **先看 scope，再動手**：目標 App 在不在 scope？哪些行為被禁（如社工真實使用者、DoS、測 production）？**越界測試不但報告無效，還可能觸法。** 這是本課從頭到尾的紅線。
- **一份報告一個漏洞**：別把五個洞塞一份。分開提，分開計分、分開賞金。
- **證明影響，不只證明存在**：「這個元件 exported」不夠，要「exported → 我用它讀出了使用者密碼（PoC 附輸出）」。平台按**影響**給賞，不按「你發現一個技術現象」。
- **可複現 > 一切**：附精確版本、環境、可照做的步驟與 PoC。三審員複現不出來，報告直接 N/A。
- **去重與已知**：提交前搜該 App 的 disclosed 報告與 CVE，別交一個已知/已修的（會被判 duplicate）。

## 對比與取捨

| 工具 / 手法 | 強在 | 弱在 | 什麼時候用 |
|---|---|---|---|
| **MobSF** | 廣度、一鍵、報告齊全 | 誤報多、加固/反模擬器卡、判斷仍要人 | 初篩，快速把攻擊面掃一遍 |
| **semgrep（自訂規則）** | 語意匹配、精準、可版本控管規則 | 要自己寫/找規則、只看得到反編譯出的碼 | 針對特定漏洞類別批量精掃 |
| **mobsfscan** | 開箱即用的行動規則集 | 規則固定、覆蓋有限 | semgrep 的懶人版，先跑再說 |
| **apkleaks** | 挖 URL/endpoint/secret 線索 | 誤報極多、只給線索不給洞 | 找後端 endpoint 與硬編碼 secret |
| **手動 + Frida/drozer** | 最精準、能做 PoC、能驗可達性 | 慢、不覆蓋全 | triage 與 PoC 階段的主力 |

**取捨的一句話**：**自動化掃廣度、人工驗深度**。理想流程是 MobSF/semgrep/apkleaks 並行掃出「疑似清單」→ 人工 triage 判可達性與前提 → 對確定項用 Frida/drozer 做 PoC → 寫報告。跳過 triage 直接交掃描結果，是這條線最常見也最致命的偷懶。

## 踩雷集錦

1. **把掃描器輸出當結論交出去**：MobSF 報告是「疑似清單」，直接貼去 bounty 會被秒退並扣信譽。**沒 triage 過的掃描結果不是漏洞。**
2. **報告只證明「存在」不證明「影響」**：「元件 exported」是現象，「exported 導致我讀出密碼」才是漏洞。平台按影響給賞，不按技術現象。
3. **重現步驟省略前置狀態**：你腦中的 AVD 版本、App 版本、前置登入狀態沒寫，審閱者複現不出來，報告判無效。步驟要能被陌生人照做。
4. **CVSS 只填分數不填向量**：分數是結論，向量是推導。少了向量，審閱者無法檢驗你評得對不對，也顯得你在猜。永遠附完整向量。
5. **沒看 scope 就開打**：越界測試報告無效、可能觸法。bug bounty 第一步永遠是讀 scope 與規則，不是開工具。
6. **修復建議寫廢話**：「加強安全性」「注意輸入驗證」等於沒說。要具體到「把字串拼接改成 `selectionArgs` 參數化查詢」這種可執行的層級。

## 進階：再往深一層

- **把掃描接進 CI**：semgrep/mobsfscan 能跑在 CI（每次 build 掃一遍），把「安全評估」左移成「開發時就攔」。寫成 GitHub Action，PR 引入新的 `setJavaScriptEnabled(true)` 就報警——這是攻防合流、把你的攻擊知識變成防禦資產。
- **自訂 semgrep 規則庫**：把本課每一類洞（Ch 3–14）寫成一條 semgrep 規則，沉澱成你自己的行動安全規則庫。下次拿到新 App，一鍵掃出這十幾類的疑似點。這是把「手藝」變「可複用資產」。
- **MobSF REST API 批量掃**：MobSF 有 API，可以寫腳本批量上傳一堆 APK、抓 JSON 報告、自動做初步 triage（例如自動剔除「沒有 exported 元件」的區塊）。評估一整批 App 時省大量時間。
- **triage 的可達性分析可以更嚴謹**：進階做法是用呼叫圖（call graph）分析確認一段程式碼是否真的從入口點可達（reachable），把「這行程式碼存在」升級成「這行程式碼會被執行」。靜態污點分析工具（如 FlowDroid）能做資料流層級的可達性，但成本高，多數評估靠人工 + Frida 動態確認即可。

## 動手練習

1. 對 AndroGoat（或 InsecureBankv2）跑 MobSF 靜態掃，把 Manifest Analysis 與 Code Analysis 的每一條命中列成表，逐條做 triage：標「確定/需條件/誤報」與理由。目標是把一份幾十條的原始報告，收斂成三五條「確定且我能做 PoC」的真漏洞。
2. 針對本課某一類洞（如 WebView `setJavaScriptEnabled(true)` 或 crypto ECB 模式），**自己寫一條 semgrep 規則**，對 jadx 反編譯出的原始碼掃，並刻意在測試碼裡放一個「註解裡的假命中」和一個「引數為 false 的安全用法」，確認你的規則不會誤報它們（呼應本章的匹配邏輯）。
3. 挑你 triage 出的一個確定漏洞，用本章的報告模板寫完整一份：標題、影響、CVSS（附向量，用官方計算器或本章腳本算）、重現步驟、PoC（附真實輸出）、修復建議。寫完給自己一個測試：**把報告給一個沒看過這 App 的人，他能不能照著複現？** 不能就重寫重現步驟。

## 本章重點整理

- 工作流 = **自動化掃廣度（MobSF/semgrep/mobsfscan/apkleaks）→ 人工 triage 去誤報 → 手動 PoC → 寫報告**。任何一段偷懶結果都廢。
- **掃描器輸出全是「疑似」**，不是漏洞。triage 三問：可達嗎？需什麼前提？能做出 PoC 嗎？分成「確定/需條件/誤報」，誤報也要記理由。
- **semgrep 靠語意匹配（看 AST、看引數、排除註解）比 grep 準**（本章 Python 實測：`false` 引數與註解不命中）。
- 報告結構：**標題（元件+洞+後果）／影響／CVSS（附向量）／重現步驟（陌生人可照做）／PoC（附真實輸出）／具體修復建議**。
- **CVSS 向量比分數重要**——它結構化地講清怎麼打、影響什麼（本章實測 WebView RCE 9.6、secret 洩漏 7.5，與官方計算器一致）。
- bug bounty：先看 scope、一報告一洞、證明影響非存在、可複現至上、先去重。

## 自我檢核

- [ ] 不看筆記，能說出這條工作流的三段（自動化廣度 / 人工 triage / 報告）各自誰負責什麼
- [ ] 能解釋為什麼「掃描器輸出不是漏洞」，以及 triage 的三個問題
- [ ] 能說出 semgrep 為什麼比 grep 準（語意/AST、看引數、排除註解）
- [ ] 能默寫報告的六個區塊，並解釋為什麼「標題要含後果」「CVSS 要附向量」「修復要具體」
- [ ] 能為一個本課漏洞給出合理的 CVSS 向量，並解釋 AV 該是 L 還 N
- [ ] 知道 bug bounty 提交前必做的事（看 scope、去重、證明影響、可複現）

## 延伸閱讀

- **[OWASP MASTG — 評估流程與工具](https://mas.owasp.org/MASTG/)**
  - **讀哪裡**：MASTG 的靜態/動態分析方法論、以及每個 MASVS 需求對應的測試案例
  - **和本章的關聯**：這是把「掃 + triage + 報告」升級成業界標準稽核流程的骨架；報告可直接對齊 MASVS 需求編號，這是專業評估報告的樣子
- **[MobSF 官方文件](https://mobsf.github.io/docs/)**
  - **讀哪裡**：靜態分析、動態分析設定、REST API 那幾章
  - **為什麼值得讀**：本章 MobSF 段的權威操作指南；REST API 那節是你做批量自動化的依據
- **[semgrep 官方文件 — 寫規則](https://semgrep.dev/docs/writing-rules/overview)**
  - **讀哪裡**：pattern / pattern-inside / metavariable 的語法；Java 規則範例
  - **和本章的關聯**：本章手寫規則與匹配邏輯的完整版；把本課每類洞寫成規則沉澱規則庫就靠這頁
- **[HackerOne / Bugcrowd 報告撰寫指南](https://docs.hackerone.com/en/articles/8368821-submitting-reports)**
  - **讀哪裡**：如何寫一份高品質報告、嚴重度與 CVSS、重現步驟的要求
  - **為什麼值得讀**：本章報告要點的官方版；bug bounty 平台明確告訴你「什麼報告會被接受、什麼會被退」，照它寫能大幅提高受理率
- **[FIRST — CVSS v3.1 規範](https://www.first.org/cvss/v3-1/specification-document)**
  - **讀哪裡**：Base metrics 定義與計算公式、那些權重常數的出處
  - **前提知識**：讀過本章 CVSS 段，這頁告訴你每個指標怎麼選、公式怎麼來（本章 Python 腳本的權重常數全在這）

你現在有了完整的工具鏈與方法論。下一步是把它**全部串在一個真實靶上跑一遍**——練習 C 給你一個靶 App 與完整流程模板，你要走過偵察、掃描、triage、PoC，最後產出一份完整評估報告。這是從「會每一招」到「能獨立完成一次評估」的跨越。

→ [練習 C：對靶 App 出完整評估報告](./practice-c-full-assessment.md)
