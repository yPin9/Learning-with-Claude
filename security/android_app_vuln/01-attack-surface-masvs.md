# Ch 1 — App 攻擊面全貌與 MASVS/MASTG 方法論

> **目標**：在動手打任何一個洞之前，先把整張**攻擊面地圖**攤開——一個 App 到底有哪些地方能被攻擊（IPC/元件、deeplink、WebView、儲存、網路、crypto、權限），每一類對應本課哪一章。然後用 OWASP 的 **MASVS**（安全需求標準）+ **MASTG**（測試流程）把「找漏洞」變成一套可重複執行的工作流：recon → 枚舉攻擊面 → 逐類測 → 驗證 PoC → 寫報告。搞懂這章，後面 14 章的每個漏洞你都知道它在地圖上的哪個位置、為什麼要測它。

> **環境**：本章是方法論與地圖，不需要 AVD。只有一段 Manifest 攻擊面枚舉的邏輯，用 **Python 3** 在本機實跑，標「實際輸出」。

## 為什麼需要這個？

android_reversing 教你逆一個 App，那是「往裡看」的技能。找漏洞是另一種思維：**往外看攻擊面**。差別在哪？逆向問「這個 App 怎麼運作」，找漏洞問「一個**外部的攻擊者**——另一個裝在同一台手機上的惡意 App、一個網路中間人、一個能誘導使用者點連結的人——能對這個 App 做什麼」。

新手最容易犯的錯是**亂槍打鳥**：學了 drozer 就到處 `app.activity.start`，看到 WebView 就想 XSS，結果面對一個真實 App 完全沒有系統性——漏了大半攻擊面，卻在沒洞的地方浪費時間。原因是腦中沒有**完整的攻擊面地圖**，也沒有**枚舉的紀律**。

這章給你兩樣東西：一張**攻擊面地圖**（App 能被打的所有面向），和一套**方法論**（OWASP MASVS/MASTG——業界怎麼把「App 安全測試」標準化）。有了地圖你不會漏，有了方法論你能重複、能寫成報告、能跟別人對齊。工具會過時，這兩樣不會。

## 先建立直覺：攻擊面是「信任邊界」被跨越的地方

在攤開地圖之前，先建立最核心的心智模型：**漏洞總是發生在信任邊界（trust boundary）上**。

一個 App 不是孤島。它跟外界有一堆「介面」：接收其他 App 的 Intent、暴露 ContentProvider 給別人查、開 WebView 載入遠端網頁、跟後端 server 通訊、把資料寫進檔案系統。**每一個介面都是一條信任邊界**——邊界的一邊是 App 自己（它信任的），另一邊是它無法控制的外部（其他 App、網路、使用者輸入、檔案系統）。

漏洞的本質，就是 App **錯誤地信任了來自邊界另一邊的東西**：

```
        App 內部 (信任區)                外部 (不可信)
   ┌──────────────────────┐        ┌─────────────────────────┐
   │                      │◀─ 元件 ─┤ 別的 App 發來的 Intent   │  ← 信任了畸形 Intent?
   │                      │◀─ IPC ──┤ 別的 App 對 Provider 的查詢│ ← 信任了 SQL 參數?
   │   App 邏輯 / 資料      │◀─deeplink┤ 使用者點的惡意連結        │ ← 信任了連結帶的資料?
   │                      │◀─WebView─┤ 遠端網頁 / 注入的 JS      │ ← JS 能碰 native?
   │                      │◀─ 網路 ──┤ 中間人改過的回應          │ ← 沒驗證憑證?
   │                      │─ 儲存 ──▶│ 檔案系統 (別的 App 可讀?)  │ ← 存了明文密碼?
   └──────────────────────┘        └─────────────────────────┘
              ▲
         信任邊界：漏洞都長在這條線上
```

記住這個框架，攻擊面地圖就不是一堆零散的漏洞類型，而是「**沿著每一條信任邊界，問 App 有沒有錯誤地信任外部**」。下面的地圖，就是把這些邊界一條條列出來。

## 攻擊面地圖：一個 App 有哪些面能被打

把上面的信任邊界展開成七大類攻擊面，每一類標出本課對應章節：

```
攻擊面                     信任邊界在哪             主要漏洞類型               本課章節
─────────────────────────────────────────────────────────────────────────────────
① 元件 / IPC          別的 App ↔ 你的元件      exported 元件濫用           Ch 2, 3
                                              intent redirection         Ch 4
                                              PendingIntent 劫持          Ch 5
② ContentProvider     別的 App ↔ 你的資料      SQLi / path traversal      Ch 6
                                              openFile 洩漏
③ deeplink / task     使用者點的連結 ↔ App     deeplink 劫持              Ch 7
                                              task hijacking
④ WebView             遠端網頁 ↔ App native    JS bridge RCE             Ch 8
                                              file:// 存取、XSS→原生
⑤ 網路                server ↔ App            明文傳輸、pinning 缺失      Ch 9
                                              network config 誤配
⑥ 儲存 / crypto       檔案系統 ↔ App           不安全儲存                 Ch 10
                                              密碼學誤用                 Ch 11
                                              secret 洩漏                Ch 12
⑦ 權限                權限系統 ↔ App           自訂 permission 缺陷        Ch 13
                                              路徑穿越 / zip slip         Ch 14
─────────────────────────────────────────────────────────────────────────────────
自動化與報告：把上面全部串成流水線 + 寫成評估報告                          Ch 15
```

這張表就是整門課的骨架。**每一列都是一條信任邊界上的一類問題**。你不用記死漏洞名稱，記住「有這七條邊界要檢查」，遇到 App 就沿著邊界一條條問下去。

哪一類最值錢？以 bug bounty 與真實評估的**投報率**排：**① 元件/IPC** 與 **② Provider** 通常最肥（幾乎每個 App 都暴露元件，且很多沒設防），所以本課 Part 2 給它們四章。**④ WebView** 一旦有洞常常是 RCE 級（能鏈成遠端執行程式碼），影響最大但較稀有。**⑥ 儲存/crypto** 最常見但影響通常較輕（要先有本地存取權）。這個「常見度 × 影響」的權衡，決定了你評估時該把時間花在哪。

## OWASP MASVS：安全「需求」的分級標準

MASVS（Mobile Application Security Verification Standard）回答一個問題：「一個 App 要**滿足哪些安全需求**才算安全？」它是**需求清單**，不是測試步驟。把它想成「驗收標準」——你拿它去衡量一個 App 達標沒。

MASVS 把需求分成幾個 **控制群組（control group）**，跟上面的攻擊面地圖高度對應：

| MASVS 群組 | 管什麼 | 對應攻擊面 |
|---|---|---|
| **MASVS-STORAGE** | 敏感資料怎麼存 | ⑥ 儲存 |
| **MASVS-CRYPTO** | 密碼學用得對不對 | ⑥ crypto |
| **MASVS-AUTH** | 認證與授權 | 貫穿（登入、session） |
| **MASVS-NETWORK** | 網路通訊安全 | ⑤ 網路 |
| **MASVS-PLATFORM** | 跟 OS 平台的互動（IPC、WebView、深連結） | ①②③④ |
| **MASVS-CODE** | 程式碼品質與更新（注入、第三方庫） | 貫穿 |
| **MASVS-RESILIENCE** | 抗逆向 / 抗竄改（反調試、加固） | android_reversing 的地盤 |
| **MASVS-PRIVACY** | 隱私（資料最小化、透明） | 合規面 |

**這門課的重點落在 MASVS-PLATFORM（元件/WebView/IPC）、MASVS-STORAGE/CRYPTO、MASVS-NETWORK**——也就是「攻擊者從外部能打的洞」。MASVS-RESILIENCE（反逆向）是 android_reversing 那門課的地盤，這裡不重複。

> **MASVS 的價值在報告，不在測試**：你寫評估報告時，每發現一個洞，標它違反哪條 MASVS 需求（如「明文儲存密碼 → 違反 MASVS-STORAGE-1」）。這讓你的報告有**業界公認的框架**背書，而不是「我覺得這樣不安全」。客戶/廠商看到 MASVS 編號，知道你不是隨口說的。Ch 15 寫報告時會大量引用。

## OWASP MASTG：把「測試」變成可執行的流程

如果 MASVS 是「驗收標準」，MASTG（Mobile Application Security Testing Guide）就是「**怎麼測**」的操作手冊。它針對 MASVS 的每一條需求，給出**具體的測試技術**（test case）：用什麼工具、看什麼、什麼樣算通過/失敗。

MASTG 的結構：

```
MASVS 需求 (要達成什麼)
    │
    ▼
MASTG 測試技術 (怎麼驗它有沒有達成)
    ├─ Static 分析：反編譯看程式碼 / 看 Manifest
    ├─ Dynamic 分析：跑起來、hook、抓包
    └─ 判準：什麼樣的輸出代表有洞
```

舉例，MASVS-NETWORK 要求「用 TLS 且驗證憑證」，MASTG 對應的測試技術會告訴你：反編譯找 `network_security_config`、動態用 mitmproxy 抓包看有沒有明文、用 objection 測 pinning 能不能被繞。**這門課每一章的攻擊，本質都是 MASTG 某個 test case 的實戰版**——我們把 MASTG 的「怎麼測」加上「怎麼利用、怎麼寫 PoC」。

> **MASVS 對 MASTG 的關係，一句話記住**：MASVS 說「要達成 A、B、C」，MASTG 說「用這些方法驗 A、B、C 有沒有達成」。你評估一個 App 時，拿 MASVS 當 checklist（要檢查哪些項），拿 MASTG 當操作手冊（每一項怎麼檢查）。

## 評估工作流：從陌生 App 到一份報告

有了地圖（攻擊面）和標準（MASVS/MASTG），把它們組成一套**可重複的五步工作流**。這是這門課的核心 SOP，final project 就是完整走一遍：

```
1. Recon（偵察）—— 這是什麼 App？
   ├─ MobSF 掃一遍，拿全貌（權限、元件、net config、secret）
   ├─ apktool 解 Manifest，人工讀：package、targetSdk、元件清單
   └─ apkleaks 淘 secret 與端點
        ↓
2. 枚舉攻擊面 —— 沿七條信任邊界列出「可打的點」
   ├─ drozer app.package.attacksurface：列 exported 元件
   ├─ 找 deeplink（intent-filter 的 scheme/host）
   ├─ 找 WebView、找 network_security_config、找本地儲存路徑
   └─ 產出「攻擊面清單」——這一步不打，只列
        ↓
3. 逐類測 —— 對每個攻擊面套 MASTG 的測試技術
   ├─ 每個 exported 元件：能不能濫用？（Ch 3–6）
   ├─ 每個 deeplink：能不能劫持 / 帶惡意資料？（Ch 7）
   ├─ WebView：JS bridge 能不能碰 native？（Ch 8）
   ├─ 網路：有沒有明文 / pinning 缺失？（Ch 9）
   └─ 儲存 / crypto：有沒有明文密碼 / 弱加密？（Ch 10–12）
        ↓
4. 驗證 PoC —— 把「可疑」升級成「可打」
   ├─ 用 drozer / adb / Frida / mitmproxy 真的觸發漏洞
   └─ 沒有 PoC 的「可疑點」不算洞，只算 finding 待驗證
        ↓
5. 報告 —— 每個確認的洞寫成一段
   ├─ 影響 + 重現步驟 + PoC + 對應 MASVS 需求 + 修復建議
   └─ Ch 15 給模板；final 把這些拼成完整評估報告
```

三個關鍵紀律，決定你是「亂打」還是「系統化評估」：

1. **枚舉（步驟 2）與測試（步驟 3）分開**。先把所有可打的點列完，再逐一測。混在一起做會漏——你打著 Activity 就忘了還有 Provider 沒枚舉。
2. **沒 PoC 不算洞（步驟 4）**。「這個 Activity 是 exported，看起來有問題」不是漏洞，是 finding。要能構造出 drozer/adb 指令真的觸發影響，才升級成漏洞。這門課每一章都要求你做到 PoC，不做 PoC 你根本不知道那是不是真洞。
3. **邊測邊記（步驟 5）**。每驗證一個洞，立刻照模板寫一段。等全部測完才回頭寫，細節早忘了。

## 枚舉七條信任邊界：具體看什麼

步驟 2 是整個工作流最容易做得不完整的一步——漏枚舉直接等於漏洞。給你一張「每條信任邊界，靜態看哪裡、動態怎麼確認」的操作對照，評估時逐條打勾：

| 攻擊面 | 靜態看哪裡（Manifest / 反編譯） | 動態怎麼枚舉/確認 |
|---|---|---|
| ① 元件/IPC | Manifest 的 `<activity>/<service>/<receiver>` 的 exported | `drozer app.*.info`、MobSF 元件表 |
| ② Provider | `<provider>` 的 authorities、readPermission/writePermission | `drozer app.provider.finduris`、`scanner.provider.*` |
| ③ deeplink | intent-filter 的 `<data scheme/host/path>`、`android:autoVerify` | adb `am start -d "scheme://..."`、觀察哪個 App 被叫起 |
| ④ WebView | 反編譯搜 `WebView`、`addJavascriptInterface`、`setJavaScriptEnabled`、`loadUrl` | Frida hook `loadUrl`、看載入了什麼 |
| ⑤ 網路 | `network_security_config.xml`、`usesCleartextTraffic`、pinning 程式碼 | mitmproxy 抓包、objection 測 pinning |
| ⑥ 儲存 | 反編譯搜 `SharedPreferences`、`openFileOutput`、`getExternalStorage`、SQLite | `adb shell` 翻 `/data/data/<pkg>/`、objection dump |
| ⑦ 權限 | `<permission>` 自訂宣告的 protectionLevel、`<uses-permission>` | drozer 看 permission、檢查簽名級是否真擋得住 |

這張表的用法：拿到一個 App，**沿著七列從上到下**，靜態欄先掃一遍列出所有點，動態欄留到步驟 3–4 逐一驗。**七列都掃過**，才算枚舉完整。少掃一列，那類洞就永遠找不到——不是因為 App 沒洞，是因為你沒去看。

## 攻擊面枚舉的最小示範（實際跑）

步驟 2「枚舉攻擊面」的第一件事，是從 Manifest 列出**哪些元件是 exported（外部可觸及）**——這決定了攻擊者從別的 App 能碰到哪些元件。exported 的判斷有隱式預設規則（Ch 2 深講），這裡先用 Python 對一段 Manifest 跑一次，讓你看到「枚舉攻擊面」具體是什麼動作：

```python
import xml.etree.ElementTree as ET
ANDROID = "{http://schemas.android.com/apk/res/android}"

manifest = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.target"><application>
  <activity android:name=".Login" android:exported="true"/>
  <activity android:name=".Internal" android:exported="false"/>
  <activity android:name=".Share">        <!-- 沒寫 exported，但有 intent-filter -->
    <intent-filter><action android:name="android.intent.action.SEND"/></intent-filter>
  </activity>
  <activity android:name=".Detail"/>       <!-- 沒寫 exported，也沒 intent-filter -->
</application></manifest>'''

def is_exported(elem):
    raw = elem.get(ANDROID + "exported")
    if raw is not None:                       # 顯式宣告，直接用
        return raw == "true", "explicit"
    has_filter = elem.find("intent-filter") is not None
    return has_filter, "implicit(intent-filter)" if has_filter else "implicit(none)"

app = ET.fromstring(manifest).find("application")
print("== 攻擊面：exported 元件 ==")
for a in app.findall("activity"):
    exported, reason = is_exported(a)
    mark = "[攻擊面]" if exported else "        "
    print(f"{mark} {a.get(ANDROID+'name'):10} exported={str(exported):5} ({reason})")
```

**實際輸出**：

```
== 攻擊面：exported 元件 ==
[攻擊面] .Login     exported=True  (explicit)
         .Internal  exported=False (explicit)
[攻擊面] .Share      exported=True  (implicit(intent-filter))
         .Detail    exported=False (implicit(none))
```

看 `.Share`：**開發者根本沒寫 `exported`，卻因為有 intent-filter 而預設變成 exported**——這是最常見的意外暴露來源，也是 Ch 2 要深挖的核心規則。這段輸出就是「枚舉攻擊面」的縮影：把 Manifest 的元件過一遍，標出哪些是外部可觸及的，這些就是步驟 3 要逐一測的對象。真實評估用 drozer 的 `attacksurface` 或 MobSF 自動做這件事，但**你必須懂它背後這條邏輯**，才知道工具漏報時（例如它沒考慮某個 SDK 版本的預設差異）該怎麼補。

## 誰是攻擊者？先定義威脅模型

「攻擊者能做什麼」取決於**攻擊者是誰、有什麼能力**。同一個洞，對不同攻擊者威脅程度天差地別。評估前先把可能的攻擊者列清楚，你的攻擊面枚舉才有優先序：

| 攻擊者 | 能力（信任邊界在哪一側） | 打得到哪些攻擊面 |
|---|---|---|
| **同機惡意 App**（最常見） | 裝在同一台手機、跑在自己的 UID | 元件/IPC①、Provider②、deeplink③（大宗；不需 root、不需實體接觸） |
| **網路中間人（MITM）** | 控制 App 與 server 之間的網路 | 網路⑤（明文、pinning 缺失） |
| **能誘導點擊的攻擊者** | 讓使用者點一個惡意連結/網頁 | deeplink③、WebView④ |
| **有實體/本地存取** | 拿到解鎖或 root 的裝置 | 儲存⑥（本地檔案、DB、SharedPrefs） |
| **惡意/被入侵的 server** | 控制 App 連的後端 | WebView④（回傳惡意 JS）、網路⑤ |

**最主流、投報率最高的是「同機惡意 App」**——它不需要 root、不需要實體接觸受害者的手機，只要受害者裝了它（偽裝成正常 App），就能對同機其他 App 的 exported 元件、Provider、deeplink 發動攻擊。這就是為什麼元件/IPC 是這門課的重頭戲：它是門檻最低、最真實的攻擊路徑。

反過來，「有實體存取才打得到」的洞（如儲存⑥的很多項）威脅較低——攻擊者都拿到你解鎖的手機了，很多事本來就守不住。評估時要在報告裡標清楚**每個洞需要什麼樣的攻擊者**，這直接決定嚴重度。

## 嚴重度怎麼定：從 finding 到可行動的漏洞

步驟 4「驗證 PoC」把 finding 升級成 vulnerability，但漏洞還要分**嚴重度**才能寫進報告、排修復優先序。三個維度綜合判斷：

```
嚴重度 ≈ 影響（Impact）× 可利用性（Exploitability）× 攻擊者門檻（Prerequisite）

影響：      能拿到什麼？   帳號接管 > 資料洩漏 > 資訊揭露 > 阻斷
可利用性：  多容易觸發？   一條 adb 指令 > 要串幾步 > 要特定條件配合
攻擊者門檻：需要什麼前提？ 同機 App（低）> 網路 MITM（中）> 實體 root（高）
```

舉例對照：

- 「exported Activity 直接繞過登入進到已登入畫面」——影響高（帳號功能）、可利用性高（一條 drozer 指令）、門檻低（同機 App）→ **高危**。
- 「明文儲存了 session token 在 SharedPrefs」——影響高（token）、但門檻高（要先有本地/root 存取）→ **中危**（除非搭配另一個能讀檔的洞，就升級）。
- 「exported 的顯示版本號 Activity」——影響幾乎為零 → 就算可觸及也**不是漏洞**（Ch 1 的 finding vs vulnerability）。

這個「影響 × 可利用性 × 門檻」的三維判斷，是你評估報告嚴重度分級的骨架，Ch 15 會配合 MASVS 等級一起用。**攻擊鏈**是這裡的隱藏放大器：兩個各自中低危的洞串起來（例如「WebView 能載入任意 URL」+「deeplink 能傳 URL 進 WebView」）可能鏈成高危 RCE——這是練習 B 與 final 的重點。

## 這門課怎麼接 android_reversing 的逆向技能

你在 android_reversing 學的每個技能，在這門課都有明確的用武之地：

| android_reversing 技能 | 在本課哪裡用 |
|---|---|
| 解 Manifest（Ch 2）、讀 exported/permission | 步驟 1–2 recon 與枚舉攻擊面的基礎 |
| smali / jadx 讀邏輯 | 步驟 3 看元件收到 Intent 後怎麼處理、找注入點 |
| Frida hook Java（Ch 13） | 步驟 4 驗證 PoC、印執行期參數、繞防護 |
| 抓包 + pinning bypass | Ch 9 網路層、Ch 8 WebView 抓 JS bridge 流量 |
| 脫殼（Part 5） | 目標有加固時，先脫殼拿到真程式碼才能找洞 |

**逆向是這門課的前置能力，不是替代**。差別是視角：逆向問「它怎麼運作」，這門課問「它哪裡能被打」。同一份 smali，逆向工程師讀它是為了理解，漏洞獵人讀它是為了找**沒驗證的輸入、錯誤的信任、暴露的介面**。你會發現讀同一段程式碼，帶著「攻擊面」的問題去讀，看到的東西完全不同。

## 對比與取捨：MASVS vs MASTG，靜態 vs 動態

| 面向 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 框架角色 | **MASVS**（需求標準） | **MASTG**（測試手冊） | 兩者搭配：MASVS 當 checklist，MASTG 當操作手冊 |
| 測試方向 | **靜態**（讀程式碼/Manifest） | **動態**（跑起來打） | 靜態找「可疑點」，動態「驗證」；缺一不可 |
| 找攻擊面 | **手動讀 Manifest** | **MobSF/drozer 自動枚舉** | 自動化快、手動懂原理；先自動掃再手動補 |
| 判定漏洞 | **看起來有問題** | **有 PoC 能觸發** | 只有 B 算數；A 只是 finding |
| 覆蓋策略 | **廣度**（七類全掃一遍） | **深度**（挖透一類） | 評估先廣度確保不漏，再對高價值面深挖 |

實務上都不是二選一，而是**先廣後深、靜動並用**：先靜態快速枚舉全部攻擊面（廣度、不漏），再對高價值的面（元件、WebView）動態深挖到 PoC（深度、確認）。

## 踩雷集錦

1. **沒枚舉就開打**：學了 drozer 就對第一個看到的 Activity 猛打，忘了還有 Provider/Receiver/deeplink 沒列。錯誤直覺「找到一個洞就是成功」——正確：**先把七條信任邊界全枚舉完**（步驟 2），再逐一測。漏枚舉是評估報告最大的黑洞。
2. **把 finding 當漏洞交**：MobSF 說「這 Activity exported」就寫進報告說「有漏洞」。錯誤直覺「工具標紅=漏洞」——正確：exported 只代表「可觸及」，**能不能造成實際危害要靠 PoC 驗**。沒 PoC 的一律標 finding，不標 vulnerability。
3. **只做靜態或只做動態**：光讀 smali 猜這裡有洞，不跑起來驗；或只用 drozer 亂打，不回頭看程式碼確認根因。錯誤直覺「一種夠了」——正確：靜態找可疑點、動態驗證，兩腿走路。
4. **忽略「隱式 exported」**：只看 `exported="true"` 的元件，漏掉那些**沒寫 exported 但有 intent-filter 而預設暴露**的。錯誤直覺「沒寫 exported 就是安全的」——正確：有 intent-filter 的元件預設可能是 exported（Ch 2 詳解），這是最常見的意外暴露，反而最容易漏。
5. **拿 MASVS 當測試步驟**：以為 MASVS 會告訴你「怎麼測」。錯誤直覺「MASVS 是測試指南」——正確：MASVS 是**需求標準**（要達成什麼），**MASTG** 才是測試手冊（怎麼驗）。搞混會找不到具體測試方法。
6. **報告不對應標準**：發現洞只寫「這樣不安全」，沒對應 MASVS 需求。錯誤直覺「說清楚危害就夠」——正確：對應 MASVS 編號讓報告有業界框架背書，可信度天差地別。

## 進階：再往深一層

- **威脅建模（threat modeling）先於枚舉**：更成熟的做法是在 recon 後先做威脅建模——這 App 值錢的資產是什麼（登入 token？支付資訊？）、攻擊者是誰（同機惡意 App？中間人？物理接觸？）。有了威脅模型，你的攻擊面枚舉會**有優先序**，先打通往高價值資產的路徑。final project 會要求你先寫一小段威脅模型。
- **MASVS 的驗證等級（L1/L2 + R）**：MASVS 對不同風險等級的 App 有不同要求——一般 App 過 L1，處理敏感資料的（金融/醫療）要 L2，需要抗逆向的再加 R（Resilience）。評估報告要先確定目標該用哪個等級，才知道拿哪套需求去衡量。銀行 App 沒做 pinning 是嚴重問題，一個計算機 App 沒做 pinning 可能無所謂——等級決定嚴重度。
- **攻擊面會隨 Android 版本變**：exported 的預設規則、PendingIntent 的 mutability 預設、`network_security_config` 的預設信任——這些都隨 targetSdk 版本改變（Android 12 起 exported 必須顯式宣告、Android 12 起 PendingIntent 預設 immutable）。同一段程式碼在不同 targetSdk 下攻擊面不同。評估時第一件事就是看 targetSdk，它決定了很多預設行為。這是 Ch 2–5 反覆出現的主題。
- **自動化的天花板**：MobSF/semgrep 能自動枚舉攻擊面、標出可疑 pattern，但**判斷可利用性、構造 PoC、串攻擊鏈**目前還是人的活。理解自動化在哪裡止步、人從哪裡接手，是這門課想教會你的核心判斷——Ch 15 會把這條線畫清楚。

## 動手練習

1. 拿 Ch 0 你為四個靶場做的攻擊面表，對每個 App 沿著本章的**七條信任邊界**逐條問：它有沒有這條邊界（有沒有 exported 元件？有沒有 WebView？有沒有網路？）。把每個靶場對應到「主要能練哪幾類漏洞」——這就是你這門課的學習路線圖。
2. 挑一個靶場，只做工作流的**步驟 1–2（recon + 枚舉）**，先不打。產出一份「攻擊面清單」：列出所有 exported 元件、deeplink scheme、WebView、儲存路徑。體會「枚舉」跟「攻擊」是兩個分開的動作。
3. 上 OWASP MASTG 網站，找 MASVS-PLATFORM 底下的任一測試技術，讀它「怎麼測」的步驟，對照本章工作流的步驟 3–4，看它跟你即將在 Part 2 學的攻擊怎麼對應。體會「MASTG test case」跟「本課的實戰攻擊」是同一件事的兩種寫法。

## 本章重點整理

- 漏洞總是長在**信任邊界**上——App 錯誤地信任了來自外部（別的 App、網路、使用者輸入、檔案系統）的東西。攻擊面地圖就是把這些邊界一條條列出來。
- 七大攻擊面：**元件/IPC、ContentProvider、deeplink/task、WebView、網路、儲存/crypto、權限**，各對應本課特定章節；投報率最高的通常是元件/IPC 與 Provider。
- **MASVS 是需求標準（要達成什麼）**、**MASTG 是測試手冊（怎麼驗）**；報告對應 MASVS 編號才有業界框架背書。
- 評估工作流五步：**recon → 枚舉攻擊面 → 逐類測 → 驗證 PoC → 報告**；三個紀律是「枚舉與測試分開」「沒 PoC 不算洞」「邊測邊記」。
- 逆向是前置能力，這門課換一個視角讀同一份程式碼：找**沒驗證的輸入、錯誤的信任、暴露的介面**。

## 自我檢核

- [ ] 不看筆記，能說出「漏洞長在信任邊界上」的意思，並舉三條 App 的信任邊界
- [ ] 能列出七大攻擊面，並各對應到本課至少一章
- [ ] 能講清楚 MASVS 與 MASTG 的分工（誰是需求、誰是測試手冊）
- [ ] 能背出評估工作流的五個步驟，並解釋為什麼「枚舉」與「測試」要分開
- [ ] 能解釋「finding」與「vulnerability」的差別，以及為什麼沒 PoC 只算 finding
- [ ] 知道 targetSdk 為什麼會影響一個 App 的攻擊面

## 延伸閱讀

### 方法論（本課骨架）

- **[OWASP MASVS](https://mas.owasp.org/MASVS/)**
  - **讀哪裡**：各 control group（STORAGE/CRYPTO/NETWORK/PLATFORM）的需求清單；驗證等級 L1/L2/R 的說明
  - **和本章的關聯**：本章的攻擊面地圖與 MASVS 群組一一對應；報告寫作（Ch 15）直接引用它的需求編號
- **[OWASP MASTG](https://mas.owasp.org/MASTG/)**
  - **讀哪裡**：Android 的各 test case（尤其 PLATFORM/NETWORK/STORAGE）；每個 case 的 static/dynamic 測試步驟
  - **為什麼值得讀**：這門課每一章的攻擊，本質都是某個 MASTG test case 的實戰版；遇到「這算不算洞、怎麼測」回這裡是最權威的參考

### Android 官方文件

- **[Android 應用程式基礎](https://developer.android.com/guide/components/fundamentals)** — Android Developers
  - **讀哪裡**：App components 與 Intent、Manifest 那節
  - **和本章的關聯**：攻擊面地圖的「元件/IPC」那一整塊建立在這些概念上；Ch 2 會深挖，這裡先建立正向認知
- **[Android 安全最佳實踐](https://developer.android.com/privacy-and-security/security-tips)** — Android Developers
  - **讀哪裡**：IPC、儲存、網路、權限那幾節——它教開發者「怎麼做才安全」
  - **為什麼值得讀**：反過來讀就是「哪裡沒做好就有洞」；理解開發者該做什麼，你才知道他們漏了什麼

### 社群

- **[HackTricks — Android Pentesting](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **這篇說什麼**：把七大攻擊面的實戰檢查清單濃縮成 cheat sheet
  - **讀哪裡**：從 attack surface 概覽到各類攻擊的目錄，對照本章地圖
  - **前提知識**：讀過本章方法論，這頁給你每一類的具體指令

有了地圖與方法論，我們從第一條、也是最肥的一條信任邊界下手：**App 之間的 IPC**。下一章徹底拆解四大元件與 Binder IPC 的安全模型——`exported` 到底怎麼判定、permission 怎麼保護、為什麼一個沒設防的元件等於把大門敞開。這是後面所有元件漏洞（Ch 3–6）的地基。

→ [Ch 2 四大元件與 IPC 安全模型](./02-components-ipc-model.md)
