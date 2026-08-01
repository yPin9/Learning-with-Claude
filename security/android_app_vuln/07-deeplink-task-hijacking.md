# Ch 7 — Deeplink / App Link 劫持與 task hijacking

> **目標**：把「一條 URL 打進別人 App」這件事的攻擊面挖穿。你要能區分 **scheme deeplink** 與 **App Link（autoVerify + assetlinks.json）** 的信任模型差在哪、為什麼前者天生可被別的 App 劫持、App Link 的驗證怎麼運作又怎麼被繞過；接著看 deeplink 帶未驗證參數如何變成 open redirect 或內部資源存取；最後補上 **task hijacking（StrandHogg / StrandHogg 2.0）**——用 `taskAffinity` / `singleTask` 把自己的 Activity 疊到目標 App 的 task 上釣帳號。

> **環境**：assetlinks.json 的比對邏輯與 URL host 解析繞過，我用 **Python 3.12** 在本機實跑（純字串/邏輯，不需 Android），標「**實際輸出**」。`adb`／裝置上的 deeplink 觸發與 StrandHogg 疊畫面屬於執行期行為，本 repo 沙箱無 AVD，標「**未實測，理論預期行為**」並附上驗證步驟。deeplink 的攻擊面延續 [Ch 3 exported 元件濫用](./03-exported-components.md) 與 [Ch 4 Intent redirection](./04-intent-redirection.md) 的元件模型。

## 為什麼需要這個？

Deeplink 是現代 App 對外開的一扇門：你點一個網頁連結、掃一個 QR code、收到一封信裡的 `myapp://...`，系統就把你導進某個 App 的某個畫面。開發者為了「無縫體驗」大量開這種門，但每一扇門都是一個**外部可控的輸入點**——而且比一般 exported Activity 更危險，因為觸發它的門檻低到「受害者點一個連結」就成立。

這裡有兩個獨立但常被混為一談的問題：

1. **誰能接這條 URL**：`myapp://` 這種自訂 scheme 沒有任何「所有權」概念，**任何 App 都能宣告自己也接 `myapp://`**。攻擊者寫一個 App 搶註冊你的 scheme，受害者點連結時系統可能把流量導到攻擊者的 App——這叫 **deeplink 劫持**。App Link（`https://` + 網域驗證）就是為了堵這個而生。
2. **URL 裡的參數 App 信不信**：就算 URL 確定進了正確的 App，App 常直接拿 URL 裡的參數去做事——`url=` 餵給 WebView、`redirect=` 拿去跳轉、`file=` 拿去開檔。這是下一章 WebView RCE 鏈的起點（練習 B 就是這條鏈）。

再加一個維度不同的攻擊：**task hijacking**。它不劫持 URL，而是劫持**畫面的視覺歸屬**——攻擊者的 Activity 假裝成你 App 的畫面疊上去，你以為在跟銀行 App 輸密碼，其實在跟攻擊者輸。StrandHogg 系列在 2019–2020 影響過大量真實 App，是這章的壓軸。

## 先建立直覺：一條 URL 怎麼找到它的 App

當系統收到一個要「用 Intent 開啟」的 URL（來自瀏覽器、另一個 App、通知），它做的是 **intent resolution**——拿 URL 的 scheme/host/path 去比對所有已安裝 App 宣告的 `<intent-filter>`，找出誰能接：

```
   一條 URL 進來
   https://shop.example.com/order/42   或   myapp://order/42
            │
            ▼
   ┌──────────────────────────────────────────┐
   │  PackageManager 掃所有 App 的             │
   │  <intent-filter> 找 match 的元件           │
   │    比對 action / scheme / host / path      │
   └──────────────────────────────────────────┘
            │
   ┌────────┴─────────────────────────────────┐
   │  只有 1 個 match  → 直接開它               │
   │  多個 match       → 跳「用哪個 App 開？」  │  ← 劫持的縫隙
   │                     的 chooser（或系統依   │
   │                     優先序自己選）          │
   └──────────────────────────────────────────┘
```

**關鍵就在「多個 match」這個分岔**。自訂 scheme（`myapp://`）沒有網域這種天然唯一擁有者，系統無從判斷誰才是「正版」，所以只要攻擊者的 App 也宣告接 `myapp://`，就會擠進候選清單。運氣好（對攻擊者而言）系統直接選它、或使用者手滑點到它、或攻擊者用一些手段讓自己排前面——URL 就落進攻擊者手裡。

App Link 補的正是這個洞：它強制 URL 必須是 `https://`（有網域），而且系統會去那個網域上抓一份 `assetlinks.json` **驗證這個 App 真的被網域主人授權**。驗證過的 App Link 不會跳 chooser、不會被別的 App 搶——因為別的 App 沒有那個網域的簽章授權。下面逐一拆開。

## 底層機制一：scheme deeplink 為什麼天生可劫持

一個典型的自訂 scheme deeplink 在 Manifest 長這樣：

```xml
<activity android:name=".DeepLinkActivity" android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.VIEW"/>
        <category android:name="android.intent.category.DEFAULT"/>
        <category android:name="android.intent.category.BROWSABLE"/>  <!-- 允許從瀏覽器觸發 -->
        <scheme android:name="myapp"/>
        <host android:name="order"/>
    </intent-filter>
</activity>
```

`BROWSABLE` 這個 category 是重點：它宣告「這條 deeplink 可以從網頁被觸發」。也就是說攻擊者只要讓受害者的瀏覽器載入一個含 `<a href="myapp://order/42">` 或 `window.location = "myapp://..."` 的頁面，就能從外部把 Intent 打進這個 Activity——**受害者連裝什麼惡意 App 都不需要**，一個網頁就夠。

現在看劫持。攻擊者寫一個 App，Manifest 裡宣告**一模一樣的 filter**：

```xml
<!-- 攻擊者的 App -->
<activity android:name=".HijackActivity" android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.VIEW"/>
        <category android:name="android.intent.category.DEFAULT"/>
        <category android:name="android.intent.category.BROWSABLE"/>
        <scheme android:name="myapp"/>   <!-- 搶同一個 scheme -->
        <host android:name="order"/>
    </intent-filter>
</activity>
```

系統無法區分正版與攻擊者——兩者對 `myapp://order/...` 都是合法候選。結果分兩種：

- **系統跳 chooser**：受害者看到「用 A App 還是 B App 開啟」，攻擊者把 App 名稱/圖示偽裝成正版，誘導點擊。
- **系統不跳、直接選**：某些情況（例如攻擊者的 App 設了較高優先序，或使用者曾對某 App 設過「總是」）流量直接進攻擊者 App，受害者毫無感知。

攻擊者拿到這條 deeplink 後能做什麼，取決於 URL 裡帶了什麼——常見的是**竊取跟在 deeplink 後面的敏感資料**：OAuth 授權碼、magic-link token、重設密碼 token。很多 App 的登入流程是「瀏覽器拿到 `myapp://auth?code=XXXX` 再交回 App」，攻擊者搶到這條 deeplink 就等於搶到授權碼。

> **未實測，理論預期行為**：上述兩個 App 同時宣告 `myapp://` 後，`adb shell am start -a android.intent.action.VIEW -d "myapp://order/42"` 觸發時系統的選擇行為，依 Android 版本與使用者過往選擇而異。驗證步驟：在 AVD 裝正版靶 App 與一個自寫的搶註冊 App，用 `adb shell am start -W -a android.intent.action.VIEW -d "myapp://order/42"`，觀察是否跳 chooser 或落到哪個 package。

## 底層機制二：App Link 與 assetlinks.json 的雙向驗證

App Link 用 `https://` URL 並加上 `android:autoVerify="true"`：

```xml
<activity android:name=".DeepLinkActivity" android:exported="true">
    <intent-filter android:autoVerify="true">
        <action android:name="android.intent.action.VIEW"/>
        <category android:name="android.intent.category.DEFAULT"/>
        <category android:name="android.intent.category.BROWSABLE"/>
        <data android:scheme="https" android:host="shop.example.com"/>
    </intent-filter>
</activity>
```

`autoVerify="true"` 觸發**安裝時驗證**：系統會去 `https://shop.example.com/.well-known/assetlinks.json` 抓一份檔案，檢查裡面有沒有授權「這個 package + 這個簽章」處理該網域的所有 URL。驗證通過，這條 App Link 就**獨佔**——別的 App 即使宣告相同 filter 也無法搶（因為它拿不出網域授權），而且不跳 chooser、直接開。

```
   安裝 App 時                          網域伺服器
 ┌──────────────────┐             ┌───────────────────────────────┐
 │ autoVerify=true  │             │ /.well-known/assetlinks.json  │
 │ host=shop.ex.com │──── 抓 ────▶│  [{ package_name, sha256 }]   │
 │ 我的簽章 SHA-256 │◀── 比對 ────│                               │
 └──────────────────┘             └───────────────────────────────┘
        比對通過 → 這條 App Link 獨佔、不跳 chooser、不可被劫持
        比對失敗 → 退化成普通 deeplink（跳 chooser，可被搶）← 這是常見誤配
```

一份 `assetlinks.json` 長這樣：

```json
[{
  "relation": ["delegate_permission/common.handle_all_urls"],
  "target": {
    "namespace": "android_app",
    "package_name": "com.example.target",
    "sha256_cert_fingerprints": [
      "14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5"
    ]
  }
}]
```

驗證的核心是三件事同時成立：**namespace 是 `android_app`、package_name 相符、我的簽章 SHA-256 在 `sha256_cert_fingerprints` 清單裡**。我把這段比對邏輯用 Python 實作並測三種情況（**實際輸出**）：

```python
def verifies(package, cert_sha256, links):
    for e in links:
        t = e.get("target", {})
        if t.get("namespace") != "android_app":            # 必須是 App
            continue
        if t.get("package_name") != package:               # package 必須相符
            continue
        if "delegate_permission/common.handle_all_urls" not in e.get("relation", []):
            continue
        if cert_sha256.upper() in [f.upper() for f in t.get("sha256_cert_fingerprints", [])]:
            return True                                    # 簽章命中才算通過
    return False
```

```
官方簽章驗證通過?   True
攻擊者簽章驗證通過? False
套件名不符驗證通過? False
```

三個結果分別對應：正版 App（package + 簽章都對）通過；攻擊者拿正確 package 名但用**自己的簽章**重打包——簽章對不上，**過不了**；攻擊者換 package 名——也過不了。這就是 App Link 能擋住劫持的數學：**你偽造不出網域主人授權的簽章**。

## 底層機制三：App Link 驗證失敗的「悄悄退化」

App Link 的安全全押在「驗證真的成功」上。但驗證有一堆會失敗的理由，而**驗證失敗不會讓 App 壞掉，只會讓這條 App Link 退化成普通 deeplink**——可跳 chooser、可被劫持。這種「fail open」是實務上最常見的 App Link 漏洞根因：

| 失敗原因 | 後果 |
|---|---|
| `assetlinks.json` 根本沒放 / 404 | 驗證失敗 → 退化成可劫持 deeplink |
| 檔案放對但 `Content-Type` 不是 `application/json` | 部分版本驗證失敗 |
| `sha256_cert_fingerprints` 填錯（填成 debug 簽章、換簽章沒更新） | 正版自己驗不過，退化 |
| 網域有多個子網域，只驗了 `www` 沒驗 `m.` / `app.` | 沒驗到的子網域可被劫持 |
| 走 HTTP 或憑證錯誤，系統抓不到檔案 | 抓取失敗 → 驗證失敗 |
| Manifest 開了 `autoVerify` 但 host 拼錯 | 驗證對不上，退化 |

驗證結果可以在裝置上查（**未實測，理論預期行為**，附驗證步驟）：

```bash
# 查某 App 的 App Link 驗證狀態（Android 12+）
adb shell pm get-app-links com.example.target
#   會列每個 host 的 state：verified / 0（未驗證）/ legacy_failure ...
# Android 11 以前用：
adb shell dumpsys package com.example.target | grep -A5 "Domain verification"
```

> 驗證步驟：裝好靶 App 後跑上面指令，若某 host 顯示非 `verified`，那條 App Link 就退化成可被劫持的普通 deeplink，可再用一個自寫的搶註冊 App 驗證是否跳 chooser。**只在你有權測試的 App 上做**。

## 底層機制四：deeplink 參數未驗證 → open redirect / 內部存取

前三節談「誰接 URL」；這節談「URL 進了正確的 App 之後」。開發者常犯的錯是**直接信任 deeplink 帶進來的參數**。兩類典型：

**(A) open redirect**：deeplink 帶一個 `redirect`/`next`/`url` 參數，App 拿去做跳轉或塞進 WebView。若沒校驗目標網域，攻擊者可把使用者導去釣魚頁，或（配合 WebView）餵惡意頁面：

```java
// 漏洞：deeplink 的 url 參數未經校驗直接 loadUrl —— 這正是練習 B / Ch 8 的入口
Uri data = getIntent().getData();               // myapp://webview?url=...
String next = data.getQueryParameter("url");    // 攻擊者完全可控
webView.loadUrl(next);                           // ← 漏洞點：任意 URL 進 WebView
```

我把這條鏈的解析畫出來（**實際輸出**，示範 Python `urlparse`）：

```
deeplink scheme: myapp   host: webview
被餵進 loadUrl 的 url = https://attacker.example/payload.html
```

**(B) 白名單繞過**：有些 App「有做校驗」，但校驗邏輯本身有洞。最經典的是用 `host.endsWith("trusted.example.com")` 這種後綴比對：

```java
// 漏洞：用 endsWith 判斷可信網域
String host = Uri.parse(next).getHost();
if (host != null && host.endsWith("trusted.example.com")) {   // ← 有洞
    webView.loadUrl(next);
}
```

我用 Python 重現同樣的 `endswith` 邏輯（**實際輸出**）：

```
True   https://trusted.example.com/x            ← 正常放行
True   https://evil-trusted.example.com/x       ← 繞過！攻擊者註冊這個網域即可
False  https://trusted.example.com.evil.com/x
False  https://attacker.com/x
```

`evil-trusted.example.com` 是攻擊者能註冊的獨立網域，卻通過了 `endsWith` 檢查。正確做法是**精確比對 host（`equals`）或比對「主機是不是該網域的子網域」**，不是後綴字串比對。

更陰險的是 **URL 解析歧義**——不同解析器對同一 URL 算出不同 host。看這個 userinfo 陷阱（**實際輸出**）：

```
host=evil.com                    <- https://trusted.example.com@evil.com/
```

`@` 前面的 `trusted.example.com` 是 **userinfo（使用者名稱）不是 host**，真正的 host 是 `@` 後面的 `evil.com`。如果 App 的校驗是「字串裡有沒有 `trusted.example.com`」（`contains`）就會被騙放行，但實際連線去的是 `evil.com`。這類「校驗用的解析器」與「實際發請求用的解析器」不一致的問題，是 open redirect / SSRF 的通用繞過模式。

## 底層機制五：task hijacking（StrandHogg / StrandHogg 2.0）

前面都在打 URL；task hijacking 打的是**畫面歸屬**。要懂它先懂 Android 的 **task 與 back stack**：使用者看到的一連串 Activity 疊成一個 task（一疊卡片），按返回鍵逐張退。`taskAffinity` 決定一個 Activity「想歸屬到哪個 task」，預設是 package 名。

**StrandHogg 1.0（taskAffinity 濫用）** 的核心：攻擊者的 Activity 把 `taskAffinity` 設成**目標 App 的 package 名**，並用 `allowTaskReparenting` / 特定 launch mode，讓自己的 Activity 被歸進目標 App 的 task。當使用者下次從桌面點開目標 App，看到的最上層卻是攻擊者疊上去、長得一模一樣的釣魚畫面：

```xml
<!-- 攻擊者 App：把 affinity 偽裝成目標 -->
<activity android:name=".PhishActivity"
          android:taskAffinity="com.bank.target"       <!-- 冒充目標的 task -->
          android:allowTaskReparenting="true"
          android:excludeFromRecents="true"/>           <!-- 藏起自己不被發現 -->
```

```
   使用者從桌面點「銀行 App」圖示
            │
            ▼
   ┌─────────────────────────────┐
   │ task: com.bank.target        │
   │  ┌────────────────────────┐  │
   │  │ 攻擊者的假登入畫面 ★    │  │ ← 疊在最上層，長得跟銀行一樣
   │  ├────────────────────────┤  │
   │  │ 真正的銀行 Activity     │  │ ← 被蓋在下面
   │  └────────────────────────┘  │
   └─────────────────────────────┘
   使用者以為在跟銀行輸密碼，其實在餵攻擊者
```

**StrandHogg 2.0（CVE-2020-0096）** 更狠：它不靠 `taskAffinity` 這種在 Manifest 留痕跡的靜態設定，而是**在執行期用一連串精心設計的 Intent（帶 `FLAG_ACTIVITY_NEW_TASK` 等 flag）動態把自己插進別人的 task**，可同時攻擊裝置上幾乎所有 App、且靜態掃描更難發現。Google 在 2020 年 5 月的安全公告修掉了它。

| 面向 | StrandHogg 1.0 | StrandHogg 2.0 (CVE-2020-0096) |
|---|---|---|
| 手法 | `taskAffinity` + reparenting（靜態） | 執行期 Intent flag 操縱（動態） |
| 痕跡 | Manifest 有可疑 `taskAffinity` | 執行期才發生，靜態難見 |
| 打擊面 | 需針對特定目標設 affinity | 幾乎可打裝置上所有 App |
| 修復 | 開發者設 `launchMode`／`taskAffinity=""` | 修於 Android 平台（2020-05 patch） |

> **未實測，理論預期行為**：StrandHogg 系列的疊畫面效果需在真實裝置與特定 Android 版本觀察。現代緩解：Android 對 background activity launch 已大幅收緊（Android 10+ 限制背景啟動 Activity），2.0 也已於平台層修復。防禦面（開發者）：主要 Activity 設 `android:launchMode="singleTask"` 或 `android:taskAffinity=""`，並在敏感畫面檢查 `onResume` 時自己是不是 task 根。

## 對比與取捨

| 機制 | 信任來源 | 可否被別的 App 劫持 | 適合裝什麼 |
|---|---|---|---|
| **自訂 scheme deeplink** (`myapp://`) | 無（誰都能宣告） | **可以** | 純 App 內導覽、非敏感 |
| **App Link** (`https://` + autoVerify) | 網域 + 簽章雙向驗證 | 驗證成功則**不可**；失敗則退化成可劫持 | 對外可信入口、承載 token |
| **App Link 但驗證失敗** | 名義上有、實際沒有 | **可以**（悄悄退化） | 這是漏洞，不是設計 |
| **task hijacking** | 攻擊的是畫面歸屬非 URL | 疊畫面釣資料 | 由平台+開發者共同防 |

取捨的核心：**能用 App Link 就別用自訂 scheme 承載任何敏感東西**。授權碼、token、magic link 只要走 `myapp://` 就等於把它暴露在「任何 App 都可能搶接」的環境。而用了 App Link，就得確保 `assetlinks.json` 真的驗過——沒驗過的 App Link 只是「看起來安全」。

## 踩雷集錦

1. **把 App Link 當成「開了 autoVerify 就安全」**：`autoVerify="true"` 只是「請系統去驗」，驗不驗得過是另一回事。`assetlinks.json` 沒放、簽章填錯、子網域漏驗——任何一個都讓它悄悄退化成可劫持 deeplink。永遠用 `pm get-app-links` 確認實際驗證狀態，別看 Manifest 就下結論。
2. **用 `endsWith` / `contains` 校驗可信網域**：`host.endsWith("example.com")` 會放行 `evil-example.com`；`url.contains("example.com")` 會被 `https://example.com@evil.com` 騙。要用精確 host 比對（`equals`）或正規的子網域判斷，別玩字串後綴。
3. **校驗用的 URL parser 跟實際用的不是同一個**：拿 `Uri.parse` 校驗、卻用另一套邏輯（或字串拼接）發請求，兩者對 userinfo / 反斜線 / 編碼的解讀不同，就有繞過縫隙。校驗與使用要基於**同一次解析結果**。
4. **忘了 `BROWSABLE` 讓 deeplink 可從網頁觸發**：加了 `category BROWSABLE` 的 deeplink，攻擊者一個網頁 `window.location="myapp://..."` 就能觸發，門檻極低。敏感 deeplink 要嘛別加 BROWSABLE，要嘛把它當成完全不可信輸入來處理。
5. **敏感 token 走 deeplink 回傳**：OAuth code、重設密碼 token 走 `myapp://auth?code=...` 回 App，一旦 scheme 被劫持就等於送人。OAuth 該用 App Link（`https://`，可驗證）或 PKCE + 系統瀏覽器（Custom Tabs）承載回呼。
6. **task hijacking 只想到靜態 taskAffinity**：StrandHogg 2.0 是執行期動態插入，靜態掃 Manifest 的 `taskAffinity` 掃不到。防禦要在平台版本（2.0 已修）+ 開發者的 `launchMode`／task 根檢查一起做。

## 進階：再往深一層

- **`pm get-app-links` 與 `verify-app-links` 主動觸發驗證**：Android 12+ 可用 `adb shell pm verify-app-links --re-verify com.example.target` 強制重驗，再用 `get-app-links` 看結果。這是評估 App Link 是否真的獨佔的最直接手段，也能看出開發者換簽章後有沒有忘了更新 assetlinks.json。
- **Digital Asset Links 的雙向性**：assetlinks.json 是「網域授權 App」；反過來 App 也可透過 `statementList` 宣告它信任哪些網域（用於 Custom Tabs 免登入等）。兩個方向的信任配置錯了都會出事，Oversecured 有專文拆過真實 App 的 asset link 誤配案例。
- **deeplink → WebView → RCE 的完整鏈**：本章的參數未驗證（機制四）是入口，下一章 WebView 的 `addJavascriptInterface` 是出口，中間串起來就是練習 B 的完整利用鏈。先在這裡記住「deeplink 是那把把任意 URL 塞進 App 內部的鑰匙」。
- **Instant App / App Links 的驗證快取**：系統會快取驗證結果，開發者修好 assetlinks.json 後裝置可能還吃舊快取，導致「伺服器已修但裝置仍退化」。評估時注意重裝或清快取後再驗。

## 動手練習

1. 在 AVD 裝一個有自訂 scheme deeplink 的靶（AndroGoat / DIVA 皆有 deeplink 關卡），用 `adb shell am start -W -a android.intent.action.VIEW -d "<scheme>://..."` 直接觸發，觀察它接不接、把什麼參數帶進去。再從一個 HTML 頁面用 `<a href>` 觸發同一條，體會「網頁即可觸發」。
2. 找靶 App 的 App Link host，用 `adb shell pm get-app-links <package>` 看它的驗證狀態。如果是 `verified` 以外的狀態，想清楚它退化成什麼、可被怎麼利用。
3. 拿本章的 Python 片段，把校驗邏輯改成 `equals`（精確比對）重跑那組 host 測試，確認 `evil-trusted.example.com` 這次被擋下——親手把有洞的校驗改成正確的。
4. 讀一個靶 App 的 deeplink 處理 Activity（jadx），找出它有沒有拿 `getQueryParameter` 的值直接 `loadUrl` / `startActivity` / 跳轉，標出漏洞點。這是練習 B 的暖身。

## 本章重點整理

- **自訂 scheme deeplink 天生可被任何 App 搶註冊劫持**；`BROWSABLE` 讓它連網頁都能觸發，門檻極低——別用它承載 token。
- **App Link = `https://` + `autoVerify` + 網域上的 `assetlinks.json`**，靠「package + 簽章」雙向驗證擋劫持；但**驗證失敗會悄悄退化成可劫持 deeplink**，必須用 `pm get-app-links` 確認真的驗過。
- **deeplink 參數未驗證**是 open redirect / WebView 注入的入口；`endsWith`/`contains` 校驗與「校驗/使用解析器不一致」是最常見的繞過。
- **task hijacking（StrandHogg 1.0/2.0）**劫持的是畫面歸屬：1.0 靠 `taskAffinity` 靜態、2.0（CVE-2020-0096）靠執行期 Intent flag 動態，2.0 已於 2020-05 平台修復。

## 自我檢核

- [ ] 能解釋為什麼 `myapp://` 這種自訂 scheme 天生可被別的 App 劫持，而 App Link 不行
- [ ] 能講清楚 `assetlinks.json` 驗證的三個必要條件，以及攻擊者為什麼過不了
- [ ] 知道「App Link 驗證失敗」的後果不是壞掉，而是退化成可劫持——並知道怎麼查驗證狀態
- [ ] 能說出 `host.endsWith("example.com")` 為什麼可被繞過，正確做法是什麼
- [ ] 能區分 StrandHogg 1.0 與 2.0 的手法差異，以及開發者/平台各自的防禦點

## 延伸閱讀

- **[OWASP MASTG — Testing Deep Links](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/MASTG-TEST-0028/)**
  - **讀哪裡**：deep link 測試流程與 App Link 驗證檢查那節
  - **學什麼**：把「怎麼系統化測 deeplink」變成可重複的清單
  - **關聯**：本章機制一~四的官方測試對照，練習 B 的方法論來源
- **[Android 官方 — Verify Android App Links](https://developer.android.com/training/app-links/verify-android-applinks)**
  - **讀哪裡**：`assetlinks.json` 格式、`autoVerify`、`pm get-app-links` 除錯那節
  - **學什麼**：App Link 驗證的權威流程，理解「退化」的每個失敗點
  - **關聯**：機制二、三的一手依據，動手練習 2 的指令出處
- **[Oversecured — Android deep link and WebView exploitation 系列](https://blog.oversecured.com/)**
  - **讀哪裡**：deep link / intent redirection 相關文章的「參數未驗證如何鏈成利用」段落
  - **學什麼**：真實 App 的 deeplink 誤配如何一路串成 RCE 或帳號接管
  - **關聯**：機制四與練習 B 的實戰版本，本課多章的一手參考
- **[Promon — StrandHogg / StrandHogg 2.0 技術分析](https://promon.co/security-news/strandhogg-2-0/)** 與 **[Android 2020-05 安全公告 (CVE-2020-0096)](https://source.android.com/docs/security/bulletin/2020-05-01)**
  - **讀哪裡**：StrandHogg 的攻擊流程圖與 CVE-2020-0096 的修復說明
  - **學什麼**：task hijacking 的完整手法與平台如何修
  - **關聯**：機制五的一手來源

deeplink 把任意 URL 塞進了 App 內部，下一站就是那個接住 URL 的元件——WebView。它是 App 裡的一個瀏覽器，一旦你能餵它任意頁面、又開了 JS bridge，就能從「載入網頁」升級成「在 App 進程裡執行程式碼」。

→ [Ch 8 WebView 攻擊面](./08-webview-attacks.md)
