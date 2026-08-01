# Ch 13 — 自訂 permission 缺陷與簽名權限

> **目標**：搞懂安卓的**自訂 permission（custom permission）**機制怎麼保護元件、`protectionLevel`（normal / dangerous / signature）各自的保護強度差在哪，以及它為什麼是一堆真實漏洞的溫床。核心要打通三個攻擊面：**custom permission 搶註冊**（惡意 App 先安裝、先定義同名 permission，把保護等級降級）、**signature permission 誤用**（以為簽名權限鐵桶，其實邊界很脆）、**用 permission 保護元件卻設錯 level**（宣告了 permission 但等於沒設）。讀完你能拿到一個 App 的 Manifest，一眼判斷它的 permission 保護是真的還是紙糊的。

## 為什麼需要這個？

前面 Ch 3–6 我們打的是 **exported 元件**——開發者根本沒設保護，門大開。但稍微有安全意識的 App 會做一件事：**用 permission 把元件關起來**。`<service android:permission="com.app.PRIVATE"/>`——看起來門鎖上了。

問題是，安卓的 permission 系統**比多數人以為的脆**。它不是一個作業系統核心強制的鐵牆，而是一個**由「誰先定義、定義成什麼等級」決定的、可以被搶、可以被繞、可以被開發者自己設錯**的機制。一個「用 custom permission 保護的元件」，實際保護強度可能是零——取決於三件事：這個 permission 是誰在什麼時候註冊的、`protectionLevel` 設成什麼、以及開發者有沒有搞懂 signature 的真正邊界。

這章就是把「看起來有保護」和「真的有保護」之間的縫隙全部攤開。這是 App 評估裡最容易出「開發者自以為安全」的地方，也是 bug bounty 常見的中高危類別。

## 先建立直覺

安卓的 permission 分兩種來源：

- **系統 permission**：`android.permission.INTERNET`、`CAMERA` 這些，由 framework 定義，等級與語意由 Google 定死。
- **自訂 permission（custom permission）**：App 自己用 `<permission>` 標籤宣告一個新的權限名，然後用它保護自己的元件，或要求別的 App 持有它才能跟自己互動。

一個 custom permission 的定義長這樣：

```xml
<permission
    android:name="com.app.permission.PRIVATE_API"
    android:protectionLevel="signature" />
```

而使用它保護元件長這樣：

```xml
<service android:name=".SyncService"
         android:exported="true"
         android:permission="com.app.permission.PRIVATE_API" />
```

語意是：**任何想 `startService(SyncService)` 的 App，必須持有 `com.app.permission.PRIVATE_API` 這個權限**，否則系統的 `ActivityManagerService` 在派發前就擋下來，回 `SecurityException`。

關鍵在 `protectionLevel`——它決定**誰能拿到這個權限**：

| protectionLevel | 誰能拿到 | 保護強度 |
|---|---|---|
| `normal` | 任何 App 在 Manifest 裡 `<uses-permission>` 宣告一下，**安裝時自動授予** | **幾乎等於沒保護** |
| `dangerous` | 需要**執行期彈窗**問使用者同意（且要在 runtime permission 群組內） | 靠使用者判斷，可被社工 |
| `signature` | **只有用同一把簽名金鑰簽的 App** 才自動拿到 | 較強，但邊界有坑 |

初學者最大的誤解：以為「我宣告了 `android:permission`，元件就安全了」。錯。如果那個 permission 的 `protectionLevel` 是 `normal`，任何惡意 App 只要在自己 Manifest 加一行 `<uses-permission android:name="com.app.permission.PRIVATE_API"/>`，安裝時系統**自動授予**，然後就大搖大擺地呼叫你的「受保護」元件。你設的鎖，鑰匙隨手可得。

## 底層機制：permission 是怎麼被授予與檢查的

要懂這些洞，得先懂 permission 的一生。從定義到檢查，流程是這樣：

```
① 定義階段（安裝時）
   App A 安裝 → PackageManagerService 解析 Manifest
       ├─ 讀到 <permission name="X" protectionLevel="signature">
       └─ 把 X 註冊進系統的「permission 資料庫」
             key = "X"，owner = App A，level = signature
             ⚠️ 先安裝先贏：X 這個名字被 App A 定義的 level 佔住

② 授予階段（安裝時，對每個 App 逐一判定）
   App B 安裝，Manifest 有 <uses-permission name="X">
       PackageManagerService 查 X 的 level：
         ├─ normal    → 直接授予 App B（不問）
         ├─ dangerous → 記為「待使用者同意」，執行期彈窗
         └─ signature → 比對 App B 的簽名 == X 的 owner(App A) 簽名？
                          相同 → 授予 ；不同 → 拒絕

③ 檢查階段（執行期，每次 IPC）
   App B 要 startService(App A 的 SyncService)
       ActivityManagerService 在派發 Intent 前：
         checkPermission("X", App B 的 uid) 通過嗎？
           通過 → 派發 ；不通過 → 丟 SecurityException
```

三個階段，三個攻擊點：

1. **定義階段有「先安裝先贏」**：X 這個 permission 名字的 `protectionLevel`，由**第一個定義它的 App** 決定。這就是搶註冊的根。
2. **授予階段 signature 靠簽名比對**：比的是**簽名憑證**是否相同，不是 package name。這裡的邊界（誰算「同一個開發者」）常被誤解。
3. **檢查階段只認 uid 有沒有那個 permission**：系統不管 App B「怎麼」拿到 permission 的，只認「有沒有」。所以只要授予階段被繞，檢查階段一定放行。

## 攻擊一：custom permission 搶註冊（先安裝搶定義）

這是這章最經典、最反直覺的漏洞。核心事實：**custom permission 的 `protectionLevel` 由第一個定義它的 App 決定，而不是「擁有」它的那個 App。**

### 攻擊場景

受害 App（`com.victim`）這樣保護它的 `AdminService`：

```xml
<!-- com.victim 的 Manifest -->
<permission android:name="com.victim.permission.ADMIN"
            android:protectionLevel="signature" />   <!-- 想用簽名保護 -->

<service android:name=".AdminService" android:exported="true"
         android:permission="com.victim.permission.ADMIN" />
```

開發者的意圖：只有 `com.victim` 自己（同簽名）能呼叫 `AdminService`。看起來對。

但如果**惡意 App 搶在受害 App 之前安裝**，並先定義同名 permission、把等級設成 `normal`：

```xml
<!-- com.attacker 的 Manifest，搶先定義 com.victim.permission.ADMIN -->
<permission android:name="com.victim.permission.ADMIN"
            android:protectionLevel="normal" />    <!-- 降級成 normal！ -->
<uses-permission android:name="com.victim.permission.ADMIN" />
```

流程：

```
時間軸：
  t0  com.attacker 安裝
        → 系統註冊 permission "com.victim.permission.ADMIN"，level = NORMAL（攻擊者定的）
        → attacker 自己 <uses-permission> 這個 normal 權限，安裝時自動拿到
  t1  com.victim 安裝
        → Manifest 也定義 "com.victim.permission.ADMIN" 為 signature
        → 但這個名字「已經被註冊過了」，系統不會用 victim 的 signature 覆蓋
          （名字先到先得，victim 的定義被忽略或維持既有 level）
  t2  攻擊者呼叫 victim 的 AdminService
        → 系統檢查：attacker 有沒有 "com.victim.permission.ADMIN"？有（t0 拿到的）
        → 派發成功，SecurityException 沒觸發 → 受保護元件被打穿
```

**受害 App 以為自己用 signature 保護，實際上這個 permission 在系統裡是 normal**，因為定義權被攻擊者搶走了。這就是搶註冊。

### 這在真實世界成立嗎

成立，但有版本演進，這點必須誠實講：

- **Android 12（API 31）以前**：搶註冊基本可行。先安裝的 App 定義了 permission，後安裝的 App 若定義同名 permission 但簽名不同，其定義**不會**覆蓋，系統維持先定義者的 level。這是一系列 custom permission 降級研究的溫床。
- **Android 12+**：Google 收緊了。若兩個 App 定義同名 permission 但簽名不同，系統會傾向**拒絕安裝後者**或以衝突方式處理，壓縮搶註冊空間。但**已在用的舊系統、以及 targetSdk 較低的組合，行為仍有縫**。

> **未實測，理論預期行為**（本 repo 沙箱無 AVD）。你要在 AVD 上驗證：先裝 attacker（定 normal + uses-permission），看 permission 的 level；再裝 victim，重新 dump 看那個 permission 的 level 有沒有被 victim 的 signature 覆蓋。**驗證關鍵指令**：

```bash
# 看系統裡某 custom permission 目前的 protectionLevel 與 owner
adb shell dumpsys package permissions | grep -A3 "com.victim.permission.ADMIN"
# 看某 App 實際被授予了哪些 permission
adb shell dumpsys package com.attacker | grep -A20 "requested permissions"
```

在 Android 11 及以下的 AVD 上，你會看到 level 停在先定義者（attacker）的 `normal`；在 Android 12+ 上，第二個 App 的安裝可能直接失敗——這個失敗本身就是防線收緊的直接證據。

## 攻擊二：signature permission 的誤用與邊界

開發者常把 `signature` 當萬靈丹：「反正只有我簽名的 App 能拿到，安全。」邊界沒那麼乾淨。

### 邊界 1：signature 比的是「憑證」不是「開發者身分」

`signature` 授予的條件是**申請方的簽名憑證與定義方相同**。這意味著：

- 你所有用**同一把 keystore** 簽的 App 之間，signature permission 是**互通**的。如果你有一個 App 被攻破（例如某個舊 App 有 RCE），攻擊者能在那個 App 的進程裡，去呼叫你另一個 App 用 signature 保護的元件——因為它們同簽名。**signature 不是「只有這個 App」，是「所有同簽名的 App」。**
- 若你把某個小工具 App 外包、用同一把 key 簽，那個外包 App 就自動有權碰你主力 App 的 signature 元件。信任邊界被簽名綁在一起。

### 邊界 2：`signature|privileged` 與系統簽名

`protectionLevel` 可以組合旗標，例如 `signature|privileged`。這裡的坑是：如果一個元件依賴平台簽名保護，而測試裝置（尤其 AVD、或被 root 的裝置）能取得平台簽名或安裝到 `/system/priv-app`，這道保護在受控裝置上形同虛設。評估時要標清楚「這個保護在 stock 裝置上有效，但在 root/客製 ROM 上可繞」。

### 邊界 3：把 permission 當「認證」用

最常見的邏輯誤用：開發者用「呼叫方持有某 signature permission」來當**身分認證**，卻在元件內部又做了會影響其他資料的操作。一旦搶註冊或同簽名旁路成立，這個「認證」就被繞過，後面的操作全暴露。permission 檢查通過**不等於**呼叫方可信——它只證明「呼叫方持有那個權限」，而「持有」的前提在前面兩個攻擊裡已經被打穿了。

## 攻擊三：宣告了 permission 卻等於沒設

即使不談搶註冊，光是設定本身就有一堆讓保護歸零的寫法。

### 坑 A：protectionLevel 沒寫，預設 normal

```xml
<permission android:name="com.app.permission.X" />   <!-- 沒寫 protectionLevel -->
```

`protectionLevel` 省略時**預設是 `normal`**。開發者以為「我定義了個 permission 保護元件，很安全」，實際上任何 App `<uses-permission>` 一下就拿到。這是純設定失誤造成的降級，跟搶註冊無關，也更常見。

### 坑 B：只在 `<permission>` 定義，卻忘了在元件上掛

```xml
<permission android:name="com.app.permission.X" android:protectionLevel="signature"/>
<service android:name=".SyncService" android:exported="true" />
<!-- 忘了寫 android:permission="com.app.permission.X" -->
```

定義了一個很安全的 permission，但元件根本沒引用它。元件還是裸奔的 exported service。定義 permission 跟「用 permission 保護元件」是**兩件事**，少了 `android:permission=` 這一半等於白做。

### 坑 C：Provider 的讀寫權限只設一半

ContentProvider 有 `android:readPermission` 與 `android:writePermission` 兩個獨立設定，還有 `android:permission`（讀寫共用）。常見錯誤：

```xml
<provider android:name=".DataProvider" android:exported="true"
          android:readPermission="com.app.permission.READ_DATA" />
<!-- 沒設 writePermission → 讀受保護，但寫是裸奔的！ -->
```

讀有保護，寫沒保護——攻擊者不能讀，但能 `insert`/`update`/`delete` 竄改資料（呼應 Ch 6 的 Provider 漏洞）。評估 Provider 一定要**分開檢查讀與寫**。

### 坑 D：`grantUriPermissions` 開了臨時授權旁路

Provider 若設 `android:grantUriPermissions="true"`，即使有 read/write permission，別的 App 也能透過 `Intent.FLAG_GRANT_READ_URI_PERMISSION` 拿到針對特定 URI 的臨時授權。若上游把這種帶授權的 Intent 轉發給不可信方（Ch 4 的 intent redirection），permission 保護就被臨時授權旁路了。

## 對比與取捨

| protectionLevel | 攻擊者取得成本 | 適合保護什麼 | 主要風險 |
|---|---|---|---|
| `normal` | 幾乎為零（宣告即得） | 幾乎不該用來保護敏感元件 | 等於沒保護；被誤當防線 |
| `dangerous` | 需騙使用者點同意 | 需使用者知情的資源（如讀簡訊給第三方） | 社工彈窗、使用者亂點 |
| `signature` | 需同簽名（或搶註冊/同簽名旁路） | App 家族內部元件互動 | 搶註冊降級、同簽名連坐、被當認證誤用 |
| `signature\|privileged` | 需平台簽名或 priv-app 位置 | 系統級元件 | root/客製 ROM 上可繞 |

**取捨的一句話**：真正敏感的元件，**第一選擇是不 exported**（`android:exported="false"`，Ch 3）；非要 exported 才用 permission，且必須用 `signature` 以上，並清楚你的簽名邊界包含哪些 App。把「內部 IPC」設計成靠 `normal` permission 保護，是把自己的安全交給「攻擊者有沒有懶得加一行 `<uses-permission>`」。

## 踩雷集錦

1. **以為 `android:permission` 一設就安全，沒看 `protectionLevel`**：`normal` 等級的 permission 保護等於零。評估時第一步是把每個 custom permission 的 level 抓出來，`normal` 的一律當作「元件沒保護」。
2. **把 signature 當「只有這個 App」**：signature 是「所有同簽名 App」。你的簽名家族裡任何一個 App 被攻破，signature 保護的元件全連坐。外包/子品牌 App 若共用 keystore，信任邊界一起破。
3. **只保護 Provider 的讀、忘了保護寫**：`readPermission` 設了、`writePermission` 沒設，攻擊者改資料照樣進。讀寫要分別檢查，別假設設一個就兩個都有。
4. **Android 12+ 就以為搶註冊死透了**：新系統收緊了，但大量使用者還在舊版、以及 targetSdk 低的 App 組合仍有縫。評估報告要標「此問題在 API ≤ 30 的裝置上可利用」，別武斷說「已修復」。
5. **定義 permission 卻沒在元件上引用**：`<permission>` 定義和元件的 `android:permission=` 是兩半，少一半保護就不存在。看到定義了一堆 custom permission，一定要回頭確認元件真的引用了它們。

## 進階：再往深一層

- **`android:protectionLevel` 的完整旗標組合**：除了 base level，還有 `|privileged`、`|development`、`|appop`、`|instant`、`|runtime`、`|knownSigner`（API 31+）等修飾旗標。其中 **`knownSigner`（API 31+）** 是 Google 為了緩解「signature 連坐」給的新武器：可以指定「除了同簽名，還信任這幾個特定簽名憑證」，讓你在不共用 keystore 的情況下建立信任關係。看到 App 用 `knownSigner`，代表開發者對簽名邊界有較成熟的認知。
- **`permission-group` 與 dangerous 的群組授予**：dangerous permission 屬於群組，Android 10 以前授予一個群組成員曾有連帶授予同群組其他成員的傾向，被用來擴大權限。custom dangerous permission 若掛進系統群組，行為更微妙，這是另一條研究線。
- **`checkCallingPermission` vs `checkCallingOrSelfPermission` 的程式碼層陷阱**：元件內部若用 `checkCallingOrSelfPermission`，當呼叫來自自己進程（或透過 `PendingIntent` 以自己身分執行，Ch 5）時會用**自己的**權限判定而非呼叫方的——這是把 permission 檢查寫在程式碼裡時的 confused deputy 變形。逆向時看到 `checkCallingOrSelfPermission` 要警覺。
- **permission tree（`<permission-tree>`）與動態 permission**：App 可以宣告一棵 permission 樹並在執行期動態新增權限（`PackageManager.addPermission`），這擴大了攻擊面分析的範圍，但實務少見。

## 動手練習

1. 找 AndroGoat 或 InsecureBankv2，`apktool d` 後把 Manifest 裡所有 `<permission>` 定義抓出來，逐一標 `protectionLevel`（沒寫的標「預設 normal」）。再把所有 exported 元件的 `android:permission`/`readPermission`/`writePermission` 抓出來，交叉比對：哪些元件宣稱有保護、但用的是 normal 等級（等於沒保護）？哪些 Provider 只保護了讀沒保護寫？產出一張「紙糊保護清單」。
2. **在 AVD 上重現搶註冊**（用 Android 11 或以下的 AVD image 效果最明顯）：寫一個 attacker App 先定義某受害 App 的 custom permission 為 `normal` 並 `<uses-permission>`，裝上；用 `dumpsys package permissions` 確認 level 是 normal。再裝受害 App（或用一個你自建、宣稱 signature 保護某 service 的靶），確認 attacker 能呼叫那個「signature 保護」的 service 而不觸發 `SecurityException`。把 `dumpsys` 前後對比截圖存進報告。
3. 用 `adb shell pm list permissions -g -d` 列出裝置上所有 dangerous permission 與它們的群組，再找一個裝了的第三方 App，用 `dumpsys package <pkg>` 看它實際被授予了哪些、還有哪些是宣告了但沒授予的——建立「宣告 ≠ 授予」的直覺。

## 本章重點整理

- custom permission 的保護強度由 **`protectionLevel`** 決定：`normal` 幾乎等於沒保護、`dangerous` 靠使用者、`signature` 靠簽名比對。省略 `protectionLevel` 預設 `normal`。
- **搶註冊**：custom permission 的 level 由**第一個定義它的 App** 決定。攻擊者先安裝、先定義同名 permission 為 `normal`，就能把受害 App 的 signature 保護降級。Android 12+ 收緊，但舊版/低 targetSdk 仍有縫。
- **signature 不是「只有這個 App」**，是「所有同簽名 App」——簽名家族連坐；且它比的是憑證不是身分，別當認證用。
- 設定層還有一堆讓保護歸零的寫法：level 漏寫、元件沒引用 permission、Provider 只保護讀沒保護寫、`grantUriPermissions` 旁路。
- 評估口訣：**敏感元件優先不 exported；非 exported 不可才用 permission，且至少 signature，並想清楚簽名邊界。**

## 自我檢核

- [ ] 不看筆記，能說出 `normal` / `dangerous` / `signature` 三種 level 各自「誰能拿到這個權限」
- [ ] 能解釋搶註冊為什麼成立（permission level 由誰決定），以及它在 Android 12 前後的差別
- [ ] 能講清楚「signature permission ≠ 只有這個 App」以及簽名家族連坐的風險
- [ ] 拿到一份 Manifest，能找出「宣稱有保護但其實是 normal」「定義了 permission 卻沒在元件引用」「Provider 只保護讀沒保護寫」這三類設定失誤
- [ ] 知道評估報告裡該怎麼誠實標注「此搶註冊問題在 API ≤ 30 可利用」而非武斷說已修復

## 延伸閱讀

- **[Android 開發者文件 — 定義自訂權限](https://developer.android.com/guide/topics/permissions/defining)**
  - **讀哪裡**：`<permission>` 的 `protectionLevel` 各值語意，以及 `knownSigner`（API 31+）那節
  - **和本章的關聯**：這是 `protectionLevel` 語意的權威定義，本章三種等級的「誰能拿到」全出自這頁；`knownSigner` 是緩解 signature 連坐的官方方案
- **[OWASP MASTG — Testing App Permissions (MASVS-PLATFORM)](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/)**
  - **讀哪裡**：Android Platform APIs 下 permission 與 IPC 相關測試案例
  - **和本章的關聯**：把本章的攻擊面對齊 MASVS-PLATFORM 需求編號，報告可直接引用；教你系統化地測「permission 保護是真是假」
- **[Android Security — Permission overriding 風險](https://developer.android.com/topic/security/risks/permission-overriding)**
  - **讀哪裡**：Google 對 custom permission 定義衝突/覆蓋風險的官方說明與緩解建議
  - **為什麼值得讀**：這是搶註冊漏洞從「奇技淫巧」變成 Google 正式承認並收緊的紀錄，讀它你會知道 Android 12+ 到底改了什麼、還剩哪些縫
- **[HackTricks — Android Applications Basics（permissions 段）](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：custom permission 與 protectionLevel 濫用的實戰檢查指令
  - **前提知識**：讀過本章，這頁給你對應的 `dumpsys`/`pm` 具體指令與 PoC 範例

下一章我們換一類完全不同的洞：當 App 解壓縮一個外部來的 zip、或下載一個檔案落地時，那個「檔名/路徑」本身就是攻擊面。zip slip、路徑穿越、下載路徑注入——攻擊者只要能控制一個 entry name，就能把檔案寫到你想不到的地方。我們會用 Python 實際構造惡意 zip 並寫出正規化防禦，親手驗證「一個 `../` 能跑多遠」。

→ [Ch 14 路徑穿越、zip slip 與不安全下載](./14-path-traversal-zipslip.md)
