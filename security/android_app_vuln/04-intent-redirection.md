# Ch 4 — Intent redirection 與 confused deputy

> **目標**：搞懂一類讓「不 exported 的元件」也能被打進去的洞——**Intent redirection**。當一個 exported 元件收到外來 Intent、卻把裡面夾帶的另一個 Intent 原封不動拿去 `startActivity`/`startService`，攻擊者就能借這個 App 的權限與身份，去存取它自己碰不到的內部元件、檔案、甚至系統資源。你會學到 confused deputy（糊塗代理人）這個貫穿整個安全領域的心智模型，看懂 `getParcelableExtra("intent")` 這行為什麼是紅旗，並構造一條「借道」攻擊鏈。

> **環境**：AVD（Android 13 / API 33，可 root），drozer、`adb`、`jadx`、`apktool`。靶場用 **Pivaa**、**AndroGoat**，以及 Ovaa（OWASP 的 intent redirection 示例）。drozer/adb 執行輸出標「**未實測，理論預期行為**」+驗證步驟；Intent 資料流的推導用文字/ASCII 說明，不需執行環境。

## 為什麼需要這個？

Ch 3 教你打 exported 元件——門開著，直接進。但真實 App 裡值錢的元件（管理後台、內部檔案存取、跨進程的特權操作）幾乎都**不 exported**，門是關的。那怎麼辦？

答案是：**你不自己開門，你讓一個有鑰匙的人幫你開**。Intent redirection 正是這種攻擊。它不打門本身，它找一個「會幫你轉發 Intent」的 exported 元件當跳板——你把「我想開內部那扇門」的指令包成一個 Intent，塞給這個跳板元件，跳板不加思索地拿你的指令去執行，用的是**它自己的權限**。你碰不到的內部元件，跳板碰得到；於是你借了跳板的手。

這是 Android 上最被低估的一類洞。它繞過了 Ch 3 所有的 exported 防護——因為被打的內部元件根本沒 export，開發者以為它安全。Oversecured 靠這一類洞在無數大廠 App（含 Google 自家 App）裡拿過賞金。它也是理解 Ch 5（PendingIntent 劫持）的必要前置——PendingIntent 劫持本質上是 Intent redirection 的一個更陰險的變種。

## 先建立直覺：confused deputy

先講一個跟 Android 無關的老故事，因為這個模型比 Android 本身更重要。

1970 年代有個編譯器服務，跑在高權限下（能寫系統目錄）。它接受使用者傳入「輸出檔案路徑」。有人傳了系統計費檔的路徑當「輸出檔」，編譯器就用**它自己的高權限**把計費檔覆蓋了。使用者自己沒權限寫那個檔，但他**騙有權限的編譯器幫他寫**。編譯器就是那個「糊塗的代理人」——它有權限，卻被低權限者當槍使。

```
   低權限者（攻擊者）          糊塗代理人（有權限的 App）        受保護資源
 ┌──────────────┐          ┌───────────────────────┐        ┌──────────────┐
 │ 我碰不到 X   │          │ 我有權限碰 X          │        │ 內部元件 X   │
 │              │  「幫我  │                       │  用我的│ (not exported)│
 │  請求 ───────┼─對 X 做─▶│ 收到請求，不查是誰    │  權限  │ 內部檔案     │
 │              │  這件事」│ 就照做 ───────────────┼───────▶│ 特權操作     │
 └──────────────┘          └───────────────────────┘        └──────────────┘
                                    ▲
                            漏洞在這：代理人「有權限」+「不驗請求來源與內容」
                            = 攻擊者借它的權限做自己做不到的事
```

**核心**：漏洞不在「資源沒保護」（X 有保護，not exported），而在「代理人有權限、卻盲目替別人執行請求」。Android 的 Intent redirection 就是這個故事的現代版：糊塗代理人 = 那個會轉發 Intent 的 exported 元件，它的權限 = 它能啟動內部元件、能讀 App 私有檔案；攻擊者借的就是這些。

記住這個模型，你在整個資安生涯都會反覆遇到它（SSRF、OAuth redirect、PendingIntent、CSRF 本質上都是 confused deputy）。

## 底層機制：Intent 可以夾帶另一個 Intent

Android 的 Intent 是個容器，`extras` 是個 Bundle，Bundle 裡**可以放任意 Parcelable 物件——包括另一個 Intent**。這是合法且常見的設計（例如「登入後跳回原本想去的頁面」，就把目標 Intent 存在 extra 裡）。問題出在 App 怎麼處理這個內嵌 Intent。

漏洞的資料流：

```
攻擊者 App                     victim 的 exported 元件（proxy）           內部元件
────────────────────────────────────────────────────────────────────────────
                              收到外來 Intent
Intent outer  ───────────────▶ (它 exported，任何 App 能送)
  .setComponent(              │
     victim/.ProxyActivity)   │  Intent inner =
  .putExtra("forward_intent", │     outer.getParcelableExtra("forward_intent")
     Intent inner {           │           │
       .setComponent(         │           │  ← 完全信任這個 inner，不驗它指向哪
        victim/.SecretActivity│           ▼
        (NOT exported!))      │     startActivity(inner)  ────────────────▶ 啟動 SecretActivity
     })                       │                                            （proxy 是同 App，
                              │                                             有權限啟動內部元件！）
```

關鍵在最後一步：`startActivity(inner)` 是由 **victim 的 proxy 元件**發起的。從系統的角度，這是 victim App **自己**在啟動 `SecretActivity`——同 App、同 UID，當然放行，即使 `SecretActivity` 不 exported。攻擊者自己直接 `startActivity` 去打 `SecretActivity` 會被擋（跨 App + not exported），但他讓 proxy 幫他打，就過了。

**三個危害維度**（inner Intent 指向什麼，就借到什麼權限）：

1. **指向內部元件**：啟動 not-exported 的 Activity/Service，繞過 Ch 3 的 exported 防護。
2. **指向內部檔案**（`content://` / `file://` URI + `FLAG_GRANT_READ_URI_PERMISSION`）：讓 proxy 把它自己私有目錄的檔案 URI 授權讀取權給攻擊者——竊取 App 私有檔案。
3. **指向特權操作**：如果 proxy 之後會用 inner Intent 的資料去做敏感事（改設定、發網路請求），攻擊者控制了 inner 就控制了那個操作。

## 範例一：借道啟動不 exported 的內部 Activity

最經典的形態。victim 有個 exported 的 `RedirectActivity`（例如處理 deeplink 後轉跳），它從 extra 取出一個 Intent 直接啟動。

漏洞版 Manifest：

```xml
<!-- proxy：exported，接受外部呼叫 -->
<activity android:name=".RedirectActivity" android:exported="true">
    <intent-filter>
        <action android:name="android.intent.action.VIEW"/>
        <category android:name="android.intent.category.DEFAULT"/>
        <category android:name="android.intent.category.BROWSABLE"/>
        <data android:scheme="myapp" android:host="redirect"/>
    </intent-filter>
</activity>

<!-- 被保護的內部元件：NOT exported，攻擊者直接打不到 -->
<activity android:name=".AdminPanelActivity" android:exported="false"/>
```

漏洞版 `RedirectActivity`（Java，**漏洞點已標**）：

```java
public class RedirectActivity extends AppCompatActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 漏洞點：從外來 Intent 取出內嵌 Intent，不做任何檢查就轉發。
        // getParcelableExtra("...") 取回一個攻擊者完全控制的 Intent。
        Intent forward = getIntent().getParcelableExtra("forward_intent");
        if (forward != null) {
            startActivity(forward);   // 用 victim 的身份啟動 forward 指向的任何元件
        }
        finish();
    }
}
```

攻擊 App（Java，攻擊者這端）：

```java
// inner：我真正想啟動的、victim 的內部元件
Intent inner = new Intent();
inner.setComponent(new ComponentName("com.victim", "com.victim.AdminPanelActivity"));

// outer：送給 exported proxy 的殼，把 inner 夾在 extra 裡
Intent outer = new Intent();
outer.setComponent(new ComponentName("com.victim", "com.victim.RedirectActivity"));
outer.putExtra("forward_intent", inner);
startActivity(outer);   // 攻擊者 App 呼叫 proxy，proxy 幫我打開 AdminPanel
```

**為什麼成功**：`AdminPanelActivity` 不 exported，攻擊者 App 直接 `startActivity(inner)` 會拋 `SecurityException`。但透過 proxy，最後那個 `startActivity(forward)` 是 victim 進程發的，系統視為 App 內部啟動，放行。攻擊者進了本該碰不到的管理面板。

用 drozer 打（**未實測，理論預期行為**）——drozer 送巢狀 Intent 要用它的 extra 語法帶 component，實務上更常直接寫攻擊 App 或用 adb：

```bash
# adb 不方便塞巢狀 Parcelable Intent，這類 PoC 通常寫成小攻擊 App。
# 若 proxy 接受 URI 形式（見範例二），可用 am 帶 --es / -d 構造。
adb shell am start -n com.victim/.RedirectActivity \
    --es forward_uri "myapp://internal/admin"
```

**驗證步驟**：在 AVD 裝 victim 靶（AndroGoat/Pivaa 有類似關卡，或自寫），確認 `AdminPanelActivity` `exported=false`；先用攻擊 App 直接 `startActivity(inner)` 觀察 logcat 應該拋 `SecurityException`（證明它真的碰不到）；再走 proxy 路徑，觀察 AdminPanel 是否被打開（證明借道成功）。這個「直接打失敗、借道成功」的對比，就是 confused deputy 的實證。

## 範例二：借道竊取 App 私有檔案

更值錢的形態：讓 proxy 把它私有目錄的檔案 URI 授權給攻擊者。App 私有目錄（`/data/data/com.victim/`）別的 App 讀不到——但如果 proxy 幫你對一個 `content://com.victim.provider/...` 的 URI 呼叫 `startActivity(inner)` 且 inner 帶 `FLAG_GRANT_READ_URI_PERMISSION`，那個授權是以 victim 的身份發的，攻擊者的接收元件就拿到讀取權。

漏洞版 proxy（**漏洞點已標**）：

```java
Intent forward = getIntent().getParcelableExtra("forward_intent");
// 漏洞點：不僅轉發 inner，還原封保留它的 flags——包含 URI 授權 flag。
// 攻擊者在 inner 裡帶 FLAG_GRANT_READ_URI_PERMISSION 指向 victim 私有檔案 URI，
// proxy 一轉發，就以 victim 身份把讀取權授給了 inner 的目標（攻擊者的元件）。
startActivity(forward);
```

攻擊者的 inner（概念）：

```java
Intent inner = new Intent(Intent.ACTION_VIEW);
inner.setComponent(new ComponentName("com.attacker", "com.attacker.StealActivity"));
inner.setData(Uri.parse("content://com.victim.fileprovider/private/session_token.txt"));
inner.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);   // 借 victim 授權讀取
```

**危害**：攻擊者的 `StealActivity` 被 victim 授權讀取 victim 私有目錄裡的檔案 URI，讀出 session token / 密碼 / 私有資料。這是把 confused deputy 用在檔案存取上——proxy 有權讀自己的私有檔（廢話），攻擊者借它的手把讀取權轉給自己。Ch 6 會從 ContentProvider 的另一端再看 URI 授權濫用。

## 範例三：邊界與失敗案例——什麼樣的轉發是安全的

不是所有「轉發 Intent」都是洞。看這個**安全版**，理解防線畫在哪：

```java
Intent forward = getIntent().getParcelableExtra("forward_intent");
if (forward != null) {
    // 防線 1：白名單——只允許轉發到明確列出的、安全的目標
    ComponentName target = forward.getComponent();
    if (target == null || !ALLOWED_TARGETS.contains(target.getClassName())) {
        finish(); return;   // 不在白名單，拒絕
    }
    // 防線 2：剝掉危險 flags，不讓外來 Intent 帶 URI 授權
    forward.removeFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                      | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
    // 防線 3：強制目標必須是自己 package，不讓它指向別處
    if (!"com.victim".equals(target.getPackageName())) { finish(); return; }
    startActivity(forward);
}
```

**失敗案例（對攻擊者而言）**：碰到上面這種 proxy，你的 inner 指向 `AdminPanelActivity` 會被白名單擋（不在 `ALLOWED_TARGETS`），帶 URI 授權 flag 會被 `removeFlags` 剝掉。這時 redirection 打不動。

**但要小心「假防線」**：很多 App 的檢查是**可繞過的**。例如只檢查 `forward.getComponent().getPackageName()` 是不是自己——但攻擊者可以讓 inner **不設 component、改用 action/data**（隱式 Intent），繞過對 component 的檢查，讓系統自己去 resolve 到一個危險目標。或只用字串 `startsWith` 比對 scheme，被 `myapp://evil@real/...` 這種 URL 解析歧異繞過。審這類防線時，要問「這個檢查涵蓋了所有攻擊者能控制的欄位嗎？component、action、data、flags、category 全都要管」。

## 怎麼在程式碼裡找到這個洞

靜態掃紅旗。用 jadx 反編譯後，搜這些模式：

```
getParcelableExtra(   ← 取出內嵌 Intent 的標誌動作（尤其 key 叫 "intent"/"extra_intent"/"forward"）
getParcelableExtra(..., Intent.class)   ← API 33+ 的新簽名，同樣可疑
```

找到後往下追：這個取出來的 Intent 有沒有**在沒充分檢查的情況下**流進 `startActivity` / `startActivityForResult` / `startService` / `sendBroadcast` / `setResult`？有的話就是候選漏洞點。

drozer 沒有專打 redirection 的一鍵 module，但可以用 `run app.activity.info -a <pkg>` 先框出 exported 的 Activity/Service，再逐個 jadx 看它們有沒有「收 Intent → 轉發 Intent」的模式。**枚舉靠 drozer，判定靠讀碼**。

## 對比與取捨

| | 直接打 exported 元件（Ch 3） | Intent redirection（Ch 4） |
|---|---|---|
| 打的目標 | exported 元件本身 | **不 exported** 的內部元件/檔案 |
| 攻擊者用誰的權限 | 自己的 | **借 victim 的** |
| 需要的跳板 | 不需要 | 需要一個會轉發 Intent 的 exported proxy |
| 防禦繞過 | — | 繞過 exported=false、繞過內部元件的信任假設 |
| 心智模型 | 敲開著的門 | confused deputy（借有鑰匙的人開門） |

| 轉發 Intent 的寫法 | 安全嗎 | 原因 |
|---|---|---|
| 原封 `startActivity(getParcelableExtra("intent"))` | ❌ 危險 | 攻擊者全控 inner |
| 轉發前檢查 component 在白名單 + 剝授權 flag + 限自家 package | ✅ 相對安全 | 涵蓋攻擊者可控欄位 |
| 只檢查 packageName == 自己 | ⚠️ 常可繞 | 隱式 Intent（無 component）繞過 |
| 只用 scheme `startsWith` 比對 | ⚠️ 常可繞 | URL 解析歧異 |

## 踩雷集錦

1. **只看 exported 元件就收工**：以為 `exported=false` 的元件安全。Intent redirection 的整個重點就是打這些「被以為安全」的內部元件。審 App 一定要追「exported 元件會不會把 Intent 轉發進內部」。
2. **看到 `getParcelableExtra("intent")` 就當洞**：那只是紅旗，不是結論。要確認 (a) 取出的 Intent 攻擊者可控、(b) 它流進了 startXxx/setResult、(c) 中間沒有有效的白名單/flag 剝除。三者齊全才是洞。
3. **忽略 flags 的轉發**：redirection 不只轉發「去哪」，也轉發 flags。`FLAG_GRANT_READ_URI_PERMISSION` 被原封轉發，就是範例二的檔案竊取。剝 flag 是防禦的關鍵一環，審計時要看有沒有剝。
4. **把「有 package 檢查」當「安全」**：只比對 packageName 的防線常被隱式 Intent 繞過（inner 不設 component，讓系統 resolve）。有防線不等於防線有效，要驗它涵不涵蓋所有可控欄位。
5. **忘了 `setResult` 也是 redirection 出口**：Activity 用 `setResult(RESULT_OK, forwardIntent)` 把攻擊者控制的 Intent 回傳給呼叫者，若呼叫者信任這個結果 Intent，攻擊面反向存在。Intent 流出的每個出口都要看。

## 進階：再往深一層

- **PendingIntent 是 redirection 的「憑證化」變種**：把「幫我送這個 Intent」包成一個可傳遞的 token，接收方用 victim 的身份觸發它。如果 base Intent 可變（mutable），攻擊者能填充它——這正是 Ch 5 的主題。Intent redirection 你懂了，PendingIntent 劫持就懂了一半。
- **隱式 Intent 的 resolve 歧異**：inner 不設 component 只給 action/data 時，系統按 IntentFilter 匹配去 resolve，結果可能落到攻擊者沒預期（或正好想要）的元件。防禦者用白名單擋 component 時，別忘了隱式 Intent 根本沒 component 可擋——要對隱式 Intent 直接拒絕或強制設 package。
- **`Intent.parseUri` 與 `intent://` scheme**：WebView 或 deeplink 處理中，`Intent.parseUri(url, ...)` 能從一段 URL 字串**造出一個完整 Intent**（含 component、flags、extras）。攻擊者控制 URL 就控制整個 Intent，是 redirection 的一個高危入口（Ch 8 WebView 會再碰）。看到 `parseUri` 要特別警覺。
- **跨 profile / 跨 user 的放大**：在有工作設定檔（work profile）或多使用者的裝置上，某些 redirection 能跨 profile 邊界放大影響。企業 App 場景要留意。

## 動手練習

1. 在 AndroGoat 或 Pivaa 找到「收 Intent 再轉發」的關卡，用 jadx 定位那行 `getParcelableExtra` + `startActivity`。先寫攻擊 App 直接 `startActivity` 打內部元件（觀察 `SecurityException`），再走 proxy 借道（觀察成功）。把兩次的 logcat 存下來當 PoC 證據。
2. 把範例三的「安全版」proxy 抄進你的自寫靶，逐條拿掉一個防線（先拿掉白名單、再拿掉剝 flag），每拿掉一條就重打一次，觀察哪一條防線一旦缺失就被你打穿。這是最直接理解「每條防線各擋什麼」的方法。
3. 用 jadx 對一個你有權分析的真實 App（自己的或開源 App）grep `getParcelableExtra`，人工追每一個結果有沒有流進 startXxx。就算沒找到洞，這個「追資料流」的手感是本章最值錢的產出。

## 本章重點整理

- **Intent redirection = confused deputy 在 Android 的具現**：借一個會轉發 Intent 的 exported proxy，用它的權限去打你自己碰不到的內部元件/檔案。
- 危害維度：借道啟動 **not-exported 元件**、借道**竊取私有檔案**（轉發帶 URI 授權 flag 的 Intent）、借道**觸發特權操作**。
- 紅旗是 **`getParcelableExtra("intent")` → 未充分檢查 → `startActivity`/`setResult`**；三者齊全才是洞。
- 防禦要**涵蓋攻擊者所有可控欄位**：白名單 component + 剝授權 flags + 拒隱式 Intent；只檢查 packageName 或 scheme 字串常可繞。
- 這是 Ch 5 PendingIntent 劫持的直接前置——PendingIntent 是把這套「借身份轉發」憑證化。

## 自我檢核

- [ ] 不看筆記，能用 confused deputy 模型解釋「攻擊者為什麼能打到不 exported 的元件」
- [ ] 能畫出 outer Intent 夾帶 inner Intent、經 proxy `startActivity(inner)` 的資料流
- [ ] 能說出 redirection 的三個危害維度，並各舉一個 inner Intent 該長什麼樣
- [ ] 給你一段轉發 Intent 的防禦程式碼，能判斷它涵不涵蓋所有可控欄位、哪裡可能被繞
- [ ] 能說出為什麼「只檢查 packageName」的防線常被隱式 Intent 繞過

## 延伸閱讀

- **[Oversecured — Interception of Android implicit intents / Intent redirection](https://blog.oversecured.com/)**
  - **讀哪裡**：搜他們談 intent redirection 與 implicit intent 攔截的文章（多篇，含真實大廠 App 案例與賞金）
  - **為什麼是一手參考**：這一類洞的深度研究與最刁鑽的繞過技巧，Oversecured 是業界最系統的來源；本章的攻擊模型直接對應他們的案例
- **[Android 官方 — App security best practices: Intent redirection](https://developer.android.com/privacy-and-security/security-tips#intents)**
  - **讀哪裡**："Use intents to defer permissions" 與處理外來 Intent 的安全建議那段
  - **和本章的關聯**：官方對「轉發外來 Intent」給的防禦準則，就是範例三防線的權威依據
- **[OWASP MASTG — Testing for Vulnerable Implementation of PendingIntent / IPC](https://mas.owasp.org/MASTG/tests/android/MASVS-PLATFORM/)**
  - **讀哪裡**：IPC 與 intent 處理相關的 test case
  - **前提知識**：讀過本章，這裡給你把 redirection 寫進評估報告的標準措辭與嚴重度依據
- **[HackTricks — Android intent injection / redirection](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**："Intent Injection" 與 deeplink 處理那幾段
  - **和本章的關聯**：可複製的 PoC 構造與 `Intent.parseUri` 相關的入口點清單

Intent redirection 是「攻擊者控制一個被 App 轉發的 Intent」。下一章把它推到更陰險的一層：如果被轉發的不是 Intent 本身，而是一張「代表 victim 身份、可以之後再觸發」的憑證——PendingIntent——會怎樣？當這張憑證的內容可以被攻擊者填充，劫持就發生了。

→ [Ch 5 PendingIntent 劫持](./05-pendingintent-hijacking.md)
