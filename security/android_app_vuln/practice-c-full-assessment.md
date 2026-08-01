# 練習 C — 對靶 App 出完整評估報告

> **目標**：把 Ch 0–15 學的**全部類別**在同一個靶 App 上串成一次**完整評估**——從偵察、自動化掃描、手動驗證、triage 去誤報，到產出一份**別人照著能複現的評估報告**。這不是打一個洞，是**走完一次真實 App 安全評估的流程**。考的是「拿到一個 App，你能不能系統化地把攻擊面掃一遍、把可疑驗成可打、把結果寫成專業報告」。這題是 Final 的前哨——Final 加上加固對抗與更大規模，這題先把「無加固靶的完整評估流程」練扎實。

> **環境與合法邊界**：靶用 **AndroGoat** 或 **InsecureBankv2**（開源刻意埋洞的靶，合法可測）。動態步驟需 **AVD（Android 13 / API 33，可 root）+ drozer + MobSF + Frida + jadx/apktool + semgrep/apkleaks**，標「**未實測，理論預期行為**」並給你自己環境的驗收方法。**CVSS 計算、semgrep 匹配、報告範例的可算部分，在本 repo 沙箱用 Python 3.12 實跑**，標「**實際輸出**」。**只測開源靶，不要拿這套流程去掃你無權測試的 App。**

## 情境設定

你是一名 App 安全評估員，客戶（假想）交給你一個 App 要你做一次完整評估。你選 **AndroGoat**（或 InsecureBankv2）當這次的目標。任務：**在時限內走完一次完整評估，交出一份涵蓋主要漏洞類別的報告 + 每個確定漏洞的 PoC。**

這題的重點不在「打出多炫的洞」，在**流程的完整性與報告的品質**——你能不能證明你系統化地掃過、每個結論有證據、別人能複現。

## 規格：報告要涵蓋哪些類別

一份完整評估至少要**覆蓋這些類別、每類給出「有洞/無洞/未觸及」的結論**（不是每類都要有洞，但每類都要有你檢查過的結論）：

| 類別 | 對應章節 | 你要回答 |
|---|---|---|
| **元件暴露** | Ch 3 | 有哪些 exported 元件？哪些真的能被外部觸發做壞事？ |
| **IPC / Intent** | Ch 4/5 | intent redirection？PendingIntent 劫持？ |
| **ContentProvider** | Ch 6 | SQLi？path traversal？未保護的讀寫？ |
| **Deeplink / WebView** | Ch 7/8 | deeplink 劫持？WebView RCE / 本地檔洩漏？ |
| **網路層** | Ch 9 | 明文流量？pinning 缺失？NSC 誤配？ |
| **儲存 / crypto / secret** | Ch 10/11/12 | 明文儲存敏感資料？crypto 誤用？硬編碼 secret？ |
| **權限** | Ch 13 | custom permission level 設錯？signature 誤用？ |
| **路徑 / 下載** | Ch 14 | zip slip？path traversal？FileProvider 誤配？ |

**規格底線**：報告至少要有一張「類別 × 結論」的總覽表（每類標「發現/未發現/未評估」），以及至少 3 個「確定且附 PoC」的漏洞完整條目。

## 期望輸出

完成後你交出：

1. **一份完整評估報告**（用下方模板），含類別總覽表 + 至少 3 個附 PoC 的確定漏洞條目。
2. **每個確定漏洞的 PoC**（drozer 命令 / adb 命令 / Frida 腳本 / 抓包截圖），附觸發後的真實輸出。
3. **一份 triage 紀錄**：自動化掃描（MobSF/semgrep/apkleaks）的原始命中清單，逐條標「確定/需條件/誤報 + 理由」——證明你去過誤報，不是照抄掃描器。
4. **每個漏洞的 CVSS 向量 + 分數**。

## 卡點預告（先看，少走彎路）

- **卡點 1：MobSF 吐了幾十條，不知從哪下手。** 別逐條硬啃。先按「攻擊面優先序」排：exported 元件 > Provider > deeplink/WebView > 儲存/secret > 其餘。從最可能有高危洞的攻擊面開始 triage。
- **卡點 2：分不清「掃描命中」和「真漏洞」。** 掃描命中是「疑似」。判定為真的唯一標準是**你能做出 PoC 觸發**。做不出 PoC 的，標「需條件」或「誤報」，別當漏洞寫。
- **卡點 3：報告寫成「掃描器輸出的複製貼上」。** 那樣的報告會被客戶/平台退。你的價值在 triage 與 PoC——把「疑似」變「確定 + 證據」，那才是報告的內容。
- **卡點 4：重現步驟自己能跑、別人跑不出來。** 你省略了 AVD 版本、App 版本、前置狀態。重現步驟要寫到「一個沒看過這 App 的人能照做」。
- **卡點 5：每類都想挖到洞，卡在沒洞的類別。** 沒洞就寫「未發現」並附「我怎麼檢查的」。評估的價值也包含「這一塊我查過、是乾淨的」。

## 分步引導（≥5 步）

按這個順序走，這就是一次真實評估的流程。

### Step 1：偵察（recon）—— 先搞懂這是什麼 App

不要一上來就掃。先用 Ch 1/2 的 SOP 建立地圖：

- `unzip -l target.apk` 看檔案：幾個 `classes*.dex`、有哪些 `.so`、`assets/` 有什麼。
- `apktool d` 讀 Manifest：package、入口 Activity、**所有 exported 元件**、權限、custom permission 與其 level（Ch 13）、`debuggable`/`allowBackup`、`network_security_config`。
- 判斷框架（原生/Flutter/RN）與有無加固（AndroGoat/InsecureBankv2 都無加固，好練）。

**產出**：一頁「App 概況 + 攻擊面清單」——列出所有 exported 元件、宣告的權限、值得注意的設定。這是後面所有工作的地圖。

### Step 2：自動化掃描（廣度）

三個工具並行，把「疑似清單」掃出來（Ch 15）：

- **MobSF** 靜態掃：Manifest/Code/Secrets/Network 四大區塊。
- **semgrep / mobsfscan** 對 jadx 反編譯出的原始碼掃程式碼層模式。
- **apkleaks** 挖 URL/endpoint/secret。

**產出**：三份原始掃描輸出。**此刻它們全是「疑似」，一個都還不是漏洞。**

### Step 3：triage（去誤報）—— 把疑似分流

這是評估的核心工序。把 Step 2 的每一條命中，用 Ch 15 的三問分流：

- 這段程式碼/設定**可達**嗎？（reachable？還是 dead code / 測試碼）
- 觸發需要什麼**前提**？（攻擊者可控嗎？需 root/實體接觸嗎？）
- 我**能做出 PoC** 嗎？

分成三堆：`[確定→做 PoC]`、`[需條件→標前提]`、`[誤報→標理由剔除]`。**誤報也記錄理由**，這讓報告可信。

**產出**：一份 triage 表（命中 → 判定 → 理由）。

### Step 4：手動驗證與 PoC（深度）

對 triage 出的「確定」項，用手動工具做出能重現的 PoC：

- exported 元件：`adb shell am start`/drozer `run app.activity.start` 觸發（Ch 3）。
- Provider SQLi/traversal：drozer `scanner.provider.*`、`adb shell content query`（Ch 6）。
- deeplink/WebView：構造惡意 URI 用 `adb shell am start -d`（Ch 7/8）。
- 儲存/secret：`adb shell run-as`（若 debuggable）或 root 拉私有檔看明文（Ch 10/12）。
- 網路：mitmproxy 抓包看明文/pinning（Ch 9）。

每個 PoC 記下**精確指令 + 真實輸出**——這是報告裡的證據。

### Step 5：評估嚴重度（CVSS）

對每個確定漏洞給 CVSS v3.1 向量（Ch 15）。關鍵判斷：

- **AV**：本地另一 App 觸發 = `L`；遠端 deeplink/WebView 誘導 = `N`。
- **UI**：需使用者點連結 = `R`；無需互動 = `N`。
- **C/I/A**：讀出密碼 = C:H；能改資料 = I:H。

用官方 CVSS 計算器或本練習下方的 Python 腳本算分數。

### Step 6：寫報告

用下方模板，把 recon 概況 + 類別總覽表 + 每個確定漏洞的完整條目（標題/影響/CVSS/重現/PoC/修復）組成一份報告。**寫完做一次「陌生人能否複現」的自測。**

## 完整參考解答

先自己走完整個流程。真的卡住再看。這裡給**報告的範例片段**（不是完整報告，是讓你對齊「一個漏洞條目該長什麼樣」），用 AndroGoat 常見洞當例子。

<details>
<summary>參考解答：評估報告範例片段（點開）</summary>

### 類別總覽表（範例）

| 類別 | 結論 | 嚴重度 | 條目編號 |
|---|---|---|---|
| 元件暴露 | 發現：exported Activity 可被外部觸發敏感畫面 | Medium | V-01 |
| ContentProvider | 發現：exported Provider 存在 SQL 注入 | High | V-02 |
| 儲存 | 發現：登入憑證明文存於 SharedPreferences | High | V-03 |
| Deeplink/WebView | 未發現可利用問題（已檢查所有 intent-filter 與 WebView 設定） | — | — |
| 網路層 | 發現：允許明文流量（`cleartextTrafficPermitted=true`） | Medium | V-04 |
| 權限 | 未發現 custom permission 誤配 | — | — |
| 路徑/下載 | 未評估（App 無解壓/下載功能） | — | — |

> 「未發現」與「未評估」是不同的：**未發現 = 我查過、是乾淨的；未評估 = 這功能不存在或超出時限**。兩者都要誠實標。

### 漏洞條目範例：V-02

```markdown
## [V-02] Exported ContentProvider 存在 SQL 注入，任意 App 可讀取全部使用者資料

### 影響
任何已安裝的 App 無需任何權限，即可透過此 Provider 的 selection 參數注入 SQL，
讀出資料庫中的全部使用者名稱與密碼（明文）。

### 嚴重度 / CVSS
CVSS 6.2 (AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N)
（本地另一 App 可觸發、無需權限、洩漏機密性高）

### 重現步驟
1. 環境：AVD Android 13 / API 33，已裝 AndroGoat vX.Y。
2. 用 drozer 或 adb 對 content://<provider>/users 的 selection 注入。

### PoC
adb shell content query --uri "content://com.android.androgoat.provider/users" \
    --where "1=1) UNION SELECT username, password FROM users --"
（觸發後輸出全部 username/password —— 此處貼你實際跑出的 Row 輸出）

### 修復建議
1. 若非跨 App 共享需求，將 provider 改為 android:exported="false"。
2. 查詢改用參數化：query(..., selection="col=?", selectionArgs=new String[]{v})，
   禁止把外部輸入拼進 selection 字串。
```

### triage 表範例（節錄）

| 掃描來源 | 命中 | 判定 | 理由 |
|---|---|---|---|
| MobSF Code | `setJavaScriptEnabled(true)` | **誤報** | 該 WebView 只載入 `file:///android_asset/` 固定頁，不載外部 URL，無 JS 介面暴露 |
| MobSF Manifest | exported provider | **確定→V-02** | drozer 注入成功讀出 users 表 |
| apkleaks | `https://api.example.com` | **需條件** | 是後端 endpoint，但未在 scope、且需後端授權才能測，標記待客戶授權 |
| MobSF Secrets | 一串疑似 key | **誤報** | 追查為 Google Maps API 的公開 key（設計上可公開），非敏感 secret |

triage 表就是你「去過誤報」的證據——它讓報告從「掃描器複製貼上」變成「專業判斷」。

</details>

## CVSS 計算（本 repo 實跑）

對三個 AndroGoat 常見漏洞算 CVSS，你在報告裡填的分數應該跟這一致（**實際輸出**，Python 3.12 實跑，與官方 NVD 計算器相同）：

```python
# cvss.py —— CVSS v3.1 Base Score（權重常數出自 FIRST 官方規範）
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
    return math.ceil((min(impact+expl,10) if s=='U' else min(1.08*(impact+expl),10))*10)/10

print("V-02 Provider SQLi   ", cvss('L','L','N','N','U','H','N','N'))
print("V-03 明文儲存憑證     ", cvss('L','L','N','N','U','H','N','N'))
print("V-01 exported Activity", cvss('L','L','N','N','U','L','L','N'))
```

**實際輸出**：

```
V-02 Provider SQLi    6.2
V-03 明文儲存憑證      6.2
V-01 exported Activity 5.1
```

三個都是本地攻擊面（`AV:L`，另一 App 觸發），所以即使能讀密碼（`C:H`），分數落在 Medium–High 而非 Critical——**這是本地 vs 遠端攻擊面對分數的直接影響**。若同樣的 SQLi 能被遠端 deeplink 誘導觸發（`AV:N`），分數會跳高。報告裡務必附**向量**，讓審閱者看得懂你怎麼評的。

## 評分表

用這張表自評（或同儕互評）。**滿分 100，70 及格代表你完成了一次合格的評估**：

| 維度 | 配分 | 評分要點 |
|---|---|---|
| **偵察完整性** | 15 | 有 App 概況 + 完整 exported 元件/權限清單；不是憑感覺開掃 |
| **掃描覆蓋** | 10 | MobSF + semgrep + apkleaks 都跑了，攻擊面掃全 |
| **triage 品質** | 20 | 每條命中有判定 + 理由；誤報有記錄；不是照抄掃描器 |
| **PoC 可重現** | 20 | 確定漏洞都有能複現的 PoC + 真實輸出；陌生人能照做 |
| **類別覆蓋** | 15 | 規格的八大類都有結論（發現/未發現/未評估） |
| **CVSS 正確** | 10 | 每個漏洞有合理向量 + 分數；AV 判斷正確（本地 vs 遠端） |
| **報告品質** | 10 | 標題含後果、修復具體、結構完整、可交付 |

**加分項（各 +5，封頂 100）**：
- 把 triage 出的規則沉澱成可複用的 semgrep 規則。
- 至少一個漏洞做出「鏈」（如 exported Activity → 觸發敏感操作，或 deeplink → WebView，Ch 7/8）。

## 延伸挑戰

1. **換一個靶重跑**：AndroGoat 做完，換 InsecureBankv2（或 DIVA）再走一遍完整流程。第二次你會發現流程變快、triage 更準——這就是把「評估」變成可重複的肌肉記憶。
2. **把評估自動化一部分**：寫個腳本把 MobSF REST API 的 JSON 輸出自動做初步 triage（例如自動剔除「無 exported 元件」的區塊），把人工時間集中在真需要判斷的地方。
3. **做成鏈**：不只列單點漏洞，找兩個能串起來的（如 exported Activity 接收 Intent → 轉發到內部 WebView → 載入攻擊者 URL，Ch 4+8），在報告裡把鏈畫出來——鏈的影響遠大於單點，也更能拿 bounty。
4. **對齊 MASVS 需求編號**：把每個漏洞對應到 MASVS 的需求（如 MASVS-STORAGE-1、MASVS-PLATFORM-2），讓報告變成標準稽核格式——這是專業評估報告的樣子。

## 自我檢核

- [ ] 不看引導，能說出一次完整評估的流程六步（recon → 掃 → triage → PoC → CVSS → 報告）
- [ ] 我的報告有類別總覽表，八大類都有結論（含誠實的「未發現/未評估」）
- [ ] 我的每個確定漏洞都有能重現的 PoC + 真實輸出，且陌生人能照著做
- [ ] 我有 triage 紀錄，證明我判過誤報、不是照抄掃描器
- [ ] 我的每個漏洞有 CVSS 向量，且我能解釋 AV 為什麼是 L（本地）或 N（遠端）
- [ ] 我的修復建議具體到可執行（不是「加強安全」這種廢話）
- [ ] 我能區分「未發現」（查過乾淨）與「未評估」（沒查/超時限）

走完這題，你已經能獨立完成一次**無加固靶的完整評估**。Final Project 把難度拉滿：一個綜合靶、更全的類別覆蓋、多個 PoC、對齊 MASTG 的正式評估流程，並整合本課 ≥70% 的概念。你在這題練的流程與報告能力，Final 會原封不動用上——差別只在規模與嚴謹度。

→ [Final Project：完整 App 安全評估（MASTG 導向）](./final-project-app-security-assessment.md)
