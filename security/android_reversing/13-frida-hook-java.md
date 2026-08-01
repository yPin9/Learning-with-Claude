# Ch 13 — Frida hook Java 層

> **目標**：把 Ch 12 的架構知識落成手上的功夫。你要能寫出並看懂：用 `Java.perform` 進入 Java 世界、`Java.use` 拿到類、`.implementation` 覆寫任意方法、處理 overload（同名多載）、讀改參數與返回值、`Java.choose` 枚舉記憶體裡活著的實例、`.$new` 主動建物件、hook 建構子、列舉類與方法。這一章給你的每支腳本都能在 AVD 上跑，且逐行解釋「為什麼要這樣寫」——因為 Frida 的 Java 橋有一堆非直覺的規矩，不懂規矩你會卡在莫名其妙的錯誤上。

> **環境**：**Frida 16.x**、Ch 0 的 **x86_64 AVD（Android 13 / API 33，已 root、frida-server 已跑）**。所有腳本用 `frida -U -f <pkg> -l x.js` 跑（spawn 模式，Ch 12）。腳本輸出一律標「**未實測，理論預期行為**」——我在沙箱沒有 AVD/Frida，語法是 Frida 16.x 標準寫法，你在自己 AVD 驗。

## 為什麼需要這個？

因為 Java 層是絕大多數 App 邏輯的所在地，也是動態分析投入產出比最高的戰場。登入、簽名、加解密、開關判斷、SSL 設定——這些十有八九先在 Java 層露臉（就算核心在 native，呼叫它的入口也在 Java）。學會 hook Java，你就能：把混淆過的方法印出真實參數（Ch 11 講的「盯行為繞開名字」）、把加密字串的解密結果偷出來、把 `isVip()` 的 `false` 改成 `true`、把某個檢查直接短路。這是 Frida 最先要練熟的一手。

## 先建立直覺：Frida 的 Java 橋在做什麼

Ch 12 說過，你的 JS 在 target 進程內、由 GumJS 執行。但 Java 的方法跑在 **ART**（Android runtime）裡，你的 JS 不是 Java——中間隔著一道橋。Frida 的 `Java` 這個全域物件，就是這道橋：它讓你的 JS 能「找到 ART 裡的類、拿到方法、把方法的實作換成你的 JS 函式」。

```
   你的 hook.js（跑在 target 進程的 GumJS 裡）
        │  Java.use("com.foo.Bar")
        ▼
   Frida 的 Java 橋（GumJS ↔ ART 的綁定）
        │  透過 ART 的內部結構找到類、方法（ArtMethod）
        ▼
   ART runtime（App 的 Java 世界真正跑的地方）
        │
        └─ Bar.check() 被呼叫時 ──▶ 轉去執行你 JS 寫的 implementation
```

兩個必記的規矩，先講清楚，後面所有腳本都建立在它們上：

1. **一切 Java 操作要包在 `Java.perform(fn)` 裡**。因為 Frida 的 JS 執行緒不是 ART 的執行緒；`Java.perform` 確保你的程式碼跑在一個「已附著到 ART VM」的執行緒上下文裡（attach 到 JVM）。不包，你一碰 `Java.use` 就爆 `access violation` 或 `the requested thread is not attached`。
2. **`Java.use("類全名")` 拿到的是「類的包裝」，不是實例**。用它可以覆寫方法（`.implementation`）、建新物件（`.$new`）、呼叫 static 方法。要操作「已經存在的實例」得用 `Java.choose`（下面講）。

## 核心一：`Java.use` + `.implementation` 覆寫方法

最基本、最常用的一招。拿到類，把某方法的 `.implementation` 換成你的 JS 函式——之後 App 每次呼叫該方法，跑的是你的版本。

我們 hook 一個假想的登入校驗方法 `com.example.app.Auth.check(String, String)`，印出帳密、照樣放行：

```javascript
// hook_check.js
Java.perform(function () {
    // 1. 拿到類的包裝（不是實例）
    var Auth = Java.use("com.example.app.Auth");

    // 2. 覆寫 check 方法的實作
    //    參數名隨你取，順序/型別要對應原方法簽名 check(String, String)
    Auth.check.implementation = function (user, pass) {
        console.log("[hook] Auth.check user=" + user + " pass=" + pass);

        // 3. 呼叫「原本的」實作——this 指向當前實例，
        //    this.check(...) 就是被覆寫前的原方法
        var result = this.check(user, pass);

        console.log("[hook] Auth.check 原本回傳 = " + result);
        return result;   // 原值回傳，只觀察不改
    };

    console.log("[*] Auth.check hook 已裝好");
});
```

逐行為什麼這樣寫：

- `Java.use("com.example.app.Auth")`：全類名（含 package）。名字錯了會爆 `ClassNotFoundException`——這也是為什麼你要先靜態（jadx/smali，Ch 5）找到準確的類與方法簽名。
- `Auth.check.implementation = function(...)`：把 `check` 的實作換掉。**參數個數與型別必須對應原方法**，否則觸發不到（Frida 靠簽名匹配）。
- `this.check(user, pass)`：`this` 是「這次呼叫的實例」；`this.check(...)` 呼叫**原始**實作。這是關鍵——覆寫後 `Auth.check` 已是你的版本，只有透過 `this.check` 才能拿回原行為（Ch 0 smoke.js 的 `this.currentTimeMillis()` 就是這個道理）。
- `return result`：這裡原值回傳（觀察）。想改行為就改這個返回值。

跑法與預期（**未實測，理論預期行為**）：

```bash
frida -U -f com.example.app -l hook_check.js
```

```
[*] Auth.check hook 已裝好
[hook] Auth.check user=alice pass=hunter2
[hook] Auth.check 原本回傳 = false
```

**改行為**——把校驗永遠通過，只要把 `return result` 換成 `return true`：

```javascript
    Auth.check.implementation = function (user, pass) {
        console.log("[hook] 強制 check 回傳 true（原本會算，但我們不管）");
        return true;   // 完全不呼叫原實作，直接放行
    };
```

> **驗證步驟**：跑起來後在 App 裡隨便輸入錯的帳密，若能登入成功，代表你的 `return true` 生效。若沒生效，最可能是：類名/方法名錯（回頭 jadx 確認）、或校驗其實在 native 層（這個 Java 方法只是轉呼叫，Ch 14 處理）。

## 核心二：overload —— 同名多載怎麼指定

真實 App 常有同名但參數不同的方法（`login(String)`、`login(String, String)`、`login(String, int)`）。你直接 `.implementation` 會爆錯，因為 Frida 不知道你要覆寫哪一個：

```
Error: check(): has more than one overload, use .overload(<signature>) to choose from:
    .overload('java.lang.String')
    .overload('java.lang.String', 'java.lang.String')
```

這個錯誤其實很貼心——它把所有多載的簽名列給你了。用 `.overload(...)` 指定：

```javascript
// hook_overload.js
Java.perform(function () {
    var Auth = Java.use("com.example.app.Auth");

    // 指定「兩個 String 參數」那個多載
    Auth.check.overload('java.lang.String', 'java.lang.String')
        .implementation = function (user, pass) {
            console.log("[hook] check(String,String) user=" + user);
            return this.check(user, pass);
        };

    // 另一個多載也可以分別 hook
    Auth.check.overload('java.lang.String')
        .implementation = function (token) {
            console.log("[hook] check(String) token=" + token);
            return this.check(token);
        };
});
```

要點：

- **overload 的簽名字串用「Java 型別全名」**：`java.lang.String`、`int`、`boolean`、`[B`（byte 陣列）、`com.foo.Bar`（自訂類）。基本型別用 Java 關鍵字（`int` 不是 `Integer`）。
- **想一次 hook 所有多載**：用 `Auth.check.overloads.forEach(...)` 遍歷（`overloads` 是所有多載的陣列）：

```javascript
    Auth.check.overloads.forEach(function (ovl) {
        ovl.implementation = function () {
            // arguments 是類陣列物件，含這次呼叫的所有參數
            console.log("[hook] check 某多載, 參數個數=" + arguments.length);
            return ovl.apply(this, arguments);   // 用 apply 轉呼叫原實作
        };
    });
```

> **踩雷預告**：`this.check(...)` 在多載情境下也可能爆「ambiguous」，因為 Frida 又不知道你要呼叫哪個原實作。上面 `ovl.apply(this, arguments)` 用「當前這個 overload 物件」呼叫原實作，避開歧義——這是遍歷多載時的標準寫法。

## 核心三：改參數與返回值

hook 的威力在於「不只看、還能改」。三種改法：

```javascript
// hook_modify.js
Java.perform(function () {
    var Msg = Java.use("com.example.app.Message");

    Msg.send.overload('java.lang.String', 'int')
        .implementation = function (text, priority) {
            // (A) 改「傳進去」的參數：把 priority 一律拉到最高
            console.log("[hook] 原 priority=" + priority + " -> 改成 9");
            priority = 9;

            // (B) 呼叫原實作，用改過的參數
            var ret = this.send(text, priority);

            // (C) 改「回傳出來」的值
            console.log("[hook] 原回傳=" + ret + " -> 改成 true");
            return true;
        };
});
```

三個改點對應三個時機（回顧 Ch 12 的 trampoline：`onEnter` 改參數、`onLeave` 改返回值，Java 層這裡合在一個函式裡）：

- **(A) 進入時改參數**：在呼叫 `this.send` 前改 `priority`，App 拿到的是你改後的值。
- **(C) 離開時改返回值**：`return` 你要的值，呼叫端收到的是假的。
- **回傳型別要對**：原方法回傳 `boolean` 你就 `return true/false`；回傳物件就得回一個相容的物件（型別不符會爆或 App 崩）。

**一個實用場景——偷解密結果**（Ch 11 講的「不還原演算法，直接偷結果」）：

```javascript
Java.perform(function () {
    var Crypto = Java.use("com.example.app.CryptoUtil");
    Crypto.decrypt.implementation = function (cipher) {
        var plain = this.decrypt(cipher);   // 讓 App 自己解
        console.log("[decrypt] " + cipher + " => " + plain);  // 明文躺這
        return plain;                        // 照樣回傳，不影響 App
    };
});
```

你完全不管 `decrypt` 內部怎麼實作，只在它 `return` 那刻把明文抓走——這是 Java hook 最高頻的用法之一。

## 核心四：`Java.choose` 枚舉活著的實例

`Java.use` 給你「類」，但有時你要的是「記憶體裡某個**已經存在**的實例」——它裡面有 App 執行到現在累積的狀態（一個已填好的設定物件、一個裝著 session 的 manager）。`Java.choose` 掃 ART 的堆，把某類的所有活實例交給你：

```javascript
// hook_choose.js
Java.perform(function () {
    Java.choose("com.example.app.SessionManager", {
        onMatch: function (instance) {
            // 對每個找到的實例呼叫一次
            console.log("[choose] 找到實例: " + instance);
            // 直接讀它的欄位 / 呼叫它的方法（讀執行期真實狀態）
            console.log("  token = " + instance.getToken());
        },
        onComplete: function () {
            console.log("[choose] 掃描結束");
        }
    });
});
```

要點：

- **`onMatch` 每命中一個實例呼叫一次**，參數是那個活物件——你能直接呼叫它的方法、讀它欄位，看到的是**執行期累積的真實狀態**（不是重新 new 一個空的）。
- **`onComplete` 掃完呼叫一次**，用來收尾。
- **時機很重要**：`Java.choose` 只找「當下堆上活著的」。App 還沒建立那個實例時掃是空的。所以常搭配「先 hook 觸發它建立，再 choose」或在對的時機（某功能用過後）執行。
- **代價**：`Java.choose` 掃整個堆，大 App 上偏慢、有擾動（Ch 11 的觀測代價）。別無腦全類掃。

## 核心五：`.$new` 建物件、hook 建構子

有時你需要**主動建立**一個 Java 物件（餵給某方法、或自己呼叫某邏輯）。用 `.$new(...)`：

```javascript
Java.perform(function () {
    // 主動 new 一個 Java 物件，$new 對應建構子
    var StringBuilder = Java.use("java.lang.StringBuilder");
    var sb = StringBuilder.$new("hello");   // = new StringBuilder("hello")
    sb.append(" frida");
    console.log("[new] " + sb.toString());   // hello frida
});
```

- `.$new(...)` 呼叫建構子建實例；`.$init` 是建構子本身（要 hook 建構子時用它）。
- 型別要對：`$new` 的參數也遵守 overload 規則，多個建構子時用 `.$init.overload(...)`。

**hook 建構子**——攔截物件「被建立的那一刻」，看它帶什麼參數進來（例如攔一個 `URL` 物件的建立看 App 要連哪）：

```javascript
Java.perform(function () {
    var URL = Java.use("java.net.URL");
    // 建構子是 $init，用 overload 指定「單一 String 參數」那個
    URL.$init.overload('java.lang.String').implementation = function (spec) {
        console.log("[URL ctor] App 要建立 URL: " + spec);
        return this.$init(spec);   // 呼叫原建構子，讓物件正常建成
    };
});
```

> **建構子必須呼叫原 `$init`**：你 hook 了建構子後若不呼叫 `this.$init(...)`，物件不會被正確初始化，之後用它一定崩。**觀察建構子 = hook + 印參數 + 照樣呼叫原 `$init`**。

## 核心六：列舉類與方法（不知道類名時）

前面都假設你已知類名。但混淆過的 App 你可能連類叫什麼都不知道。Frida 能在執行期列舉載入的類、找出符合條件的：

```javascript
// enum_classes.js —— 列出所有「類名含 login（不分大小寫）」的已載入類
Java.perform(function () {
    Java.enumerateLoadedClasses({
        onMatch: function (name, handle) {
            if (name.toLowerCase().indexOf("login") !== -1) {
                console.log("[class] " + name);
            }
        },
        onComplete: function () { console.log("[*] 列舉結束"); }
    });
});
```

- **`enumerateLoadedClasses` 只列「已載入」的類**——加固/動態載入的類要等它被載入後才掃得到（又是時序問題，呼應 Ch 12 的 spawn/attach）。
- 找到可疑類後，可以進一步列它的方法：

```javascript
Java.perform(function () {
    var Bar = Java.use("com.example.a.b.Bar");
    // .class.getDeclaredMethods() 走 Java 反射列出方法
    var methods = Bar.class.getDeclaredMethods();
    methods.forEach(function (m) {
        console.log("[method] " + m.toString());
    });
});
```

這在對付混淆時很實用：靜態看到 `a.b.Bar` 一堆 `a()/b()/c()`，執行期列出它們的完整簽名（含參數型別），再挑可疑的 hook。**這就是 Ch 11「動態繞開名字騙術」在 Java 層的具體操作。**

## 對比與取捨

| 你想做 | 用什麼 | 關鍵注意 |
|---|---|---|
| 覆寫/觀察一個方法 | `Java.use` + `.implementation` | 一定包在 `Java.perform`；`this.method()` 呼原實作 |
| 同名多載 | `.overload(簽名)` 或 `.overloads` 遍歷 | 簽名用 Java 型別全名；轉原實作用 `ovl.apply` |
| 操作已存在的實例 | `Java.choose` | 只找當下堆上活的；掃堆有代價，注意時機 |
| 主動建物件 | `.$new(...)` | 遵守 overload 規則 |
| 攔物件被建立 | hook `.$init` | **必須呼叫原 `$init`**，否則物件半殘 |
| 不知類名 | `enumerateLoadedClasses` | 只列已載入的；配合 spawn 等它載入 |
| 改參數 | 在呼叫原方法前改 | 型別要對 |
| 改返回值 | `return` 假值 | 回傳型別要相容，否則崩 |

## 踩雷集錦

1. **沒包 `Java.perform` 就用 `Java.use`**：爆 `access violation` 或 `thread not attached`。所有 Java 操作都要在 `Java.perform(function(){ ... })` 裡——因為要先把 Frida 的執行緒附著到 ART VM。
2. **overload 沒指定就 `.implementation`**：`has more than one overload` 錯。用 `.overload(型別...)` 指定，或 `.overloads` 遍歷。錯誤訊息會列出所有簽名，照抄即可。
3. **`this.method()` 在多載下也 ambiguous**：遍歷多載時用 `ovl.apply(this, arguments)` 呼原實作，別用 `this.method()`（Frida 一樣不知道你要哪個）。
4. **hook 建構子忘了呼原 `$init`**：物件沒初始化，後續用它必崩。建構子 hook 的鐵律：印完參數要 `return this.$init(...)`。
5. **`Java.choose` 掃到空的以為壞了**：實例還沒被建立時掃當然空。先觸發它建立、或在對的時機掃。也別對超大 App 無腦全類 choose，慢且擾動大。
6. **`enumerateLoadedClasses` 找不到類就以為沒有**：它只列「已載入」的。動態載入/加固的類要等載入後才出現——用 spawn 早點就位、或在觸發載入後再列。
7. **改返回值型別不符**：原方法回 `boolean` 你 `return "true"`（字串），或回物件你回 `null` 導致 App NPE。回傳值要跟原型別相容。

## 進階：再往深一層

- **hook 系統類影響全局**：hook `java.lang.String` 或 `java.util.HashMap` 這種到處被用的類，你的 log 會爆量、甚至拖垮 App（每次字串操作都進你的 JS）。要縮範圍：hook 更具體的類，或在 implementation 裡加條件過濾（`if (text.indexOf("sign") !== -1)`）才印。
- **`Java.classFactory` 與多 ClassLoader**：加固/插件化 App 有多個 ClassLoader，`Java.use` 預設用的 loader 可能找不到動態載入的類。進階要用 `Java.enumerateClassLoaders` 找到對的 loader、切 `Java.classFactory.loader`。這是脫殼後 hook 真 DEX 類的常見障礙（Ch 35 ClassLoader、Ch 36 會碰）。
- **`Java.registerClass` 動態造類**：Frida 能在執行期造一個新的 Java 類（實作某介面），塞回 App。用來替換一個 callback、造一個假的 `X509TrustManager` 繞 SSL pinning（Ch 17 的一種手法）。這是 Java 橋能力的上限展示。
- **hook 的痕跡與反制**：`.implementation` 換掉方法會改動 ART 內部的 ArtMethod 結構，反 Frida 可能校驗方法的 entry point（Ch 30/34）。理解 hook 在 ART 層動了什麼，才知道對抗點在哪。

## 動手練習

1. **改一個布林**：找一個你自己寫的 crackme（或練習 A 的），它有個 `boolean` 校驗方法。先 hook 印出它的真實回傳，再改成永遠 `return true`，看能不能繞過。體會「觀察 → 改行為」兩步。
2. **偷解密結果**：寫一個小 App，把某字串加密存、用時解密。hook 那個 decrypt 方法印明文，驗證你「用 App 自己的邏輯幫你解密」——不看它演算法就拿到明文。
3. **對抗混淆**：拿一個開了 R8 混淆的 App（或自己開 minify build 一個），用 `enumerateLoadedClasses` + 列方法找出「登入」相關的類/方法，hook 它印參數，驗證你點登入時它收到你的帳密——名字是 `a.b.c`，行為騙不了你。
4. **枚舉實例讀狀態**：hook 一個 manager 類的建構子讓它建立、然後用 `Java.choose` 找到那個實例、讀它某個欄位。體會 `use`（類）vs `choose`（活實例）的差別。

## 本章重點整理

- **兩條鐵律**：所有 Java 操作包在 `Java.perform` 裡；`Java.use` 給你「類」（覆寫/建物件），`Java.choose` 給你「活實例」（讀執行期狀態）。
- **覆寫**：`.implementation = function(){...}`，`this.method()` 呼原實作；**overload** 用 `.overload(型別...)` 或遍歷 `.overloads`（轉呼叫用 `ovl.apply`）。
- **改行為**：進入時改參數（呼原方法前）、離開時改返回值（`return` 假值），型別要相容。
- **建物件 `.$new`、hook 建構子 `.$init`**（建構子 hook 必須呼原 `$init`）。
- **不知類名**用 `enumerateLoadedClasses` + 列方法——這是動態對抗混淆的具體操作；注意「只列已載入的」的時序限制。

## 自我檢核

- [ ] 能說出為什麼所有 Java 操作要包在 `Java.perform` 裡
- [ ] 能寫出一個覆寫方法、印參數、呼叫原實作、改返回值的完整 hook
- [ ] 碰到 `has more than one overload` 知道怎麼處理，也知道多載下怎麼呼原實作
- [ ] 能講清楚 `Java.use` 和 `Java.choose` 的差別，各在什麼場景用
- [ ] hook 建構子時知道那條鐵律（呼原 `$init`），也知道 `enumerateLoadedClasses` 的時序限制

## 延伸閱讀

- **[Frida 官方文件 — JavaScript API：Java](https://frida.re/docs/javascript-api/#java)**
  - **讀哪裡**：`Java.perform`、`Java.use`、`Java.choose`、`Java.enumerateLoadedClasses`、`overload`、`$new`/`$init` 各小節
  - **學什麼**：本章每個 API 的權威定義、完整參數與邊界（如 `Java.available` 判斷）
  - **關聯**：本章是這份 API 的實戰導覽，寫腳本卡住先回這裡對簽名
- **[Frida CodeShare](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 Java hook / bypass 類腳本，讀它們的 `Java.use`/`overload` 用法
  - **學什麼**：社群怎麼處理真實 App 的多載、多 ClassLoader、系統類 hook——讀原始碼比自己從零摸快
  - **關聯**：把本章基本功套到真實複雜案例，看高手怎麼縮範圍、避坑
- **[HackTricks — Frida Java Hooking](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/frida-tutorial/index.html)**
  - **讀哪裡**：Java hooking 範例段，特別是改返回值/繞校驗的實例
  - **學什麼**：一線滲透最常用的 Java hook 模式（繞 root 檢查、繞登入）
  - **關聯**：本章基本功的實戰應用清單，接 Ch 17 的 pinning bypass
- **[OWASP MASTG — Android Runtime Instrumentation with Frida](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0035/)**
  - **讀哪裡**：用 Frida 做 method hooking 的系統化步驟
  - **學什麼**：把 hook 放進標準測試流程的方法論視角
  - **關聯**：本章的技巧在正式安全測試中的定位，呼應 Ch 11 的方法論

下一章我們跨過 Java 與 native 的邊界，把 hook 打到 `.so` 裡。你會學 `Interceptor.attach` 的 `onEnter`/`onLeave`、怎麼用位址/符號定位 native 函式、怎麼讀寫 native 記憶體、怎麼用 `NativeFunction` 主動呼叫一個 native 函式——把 Java 層截不到的核心演算法，在 native 層攤開。

→ [Ch 14 Frida hook native 層](./14-frida-hook-native.md)
