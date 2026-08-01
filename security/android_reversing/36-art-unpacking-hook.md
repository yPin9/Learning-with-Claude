# Ch 36 — 從 ART 內部脫殼與 hook

> **目標**：把 Ch 34（`ArtMethod`/entrypoint）與 Ch 35（ClassLoader/動態載入）合起來，攻克 Part 6 的高潮：**為什麼「主動調用」能還原被抽空的方法體**（抽取型加固把 code_item 執行時才填回 `ArtMethod`）、**ArtMethod hook 的原理**（改 entrypoint / 設 native 旗標）、**FART 的主動調用脫殼機制**、以及這套「ART 內部 hook」跟你熟的 **Frida/Xposed hook 的層次差異**。這章要讓你從「用工具脫殼」升級到「懂脫殼工具在對 `ArtMethod` 做什麼」——練習 E 你就要親手寫一個簡化版。

> **環境**：以 **Android 13 / API 33（ART）** 為準。**高度版本敏感**：`ArtMethod` 欄位 offset、`art_quick_to_interpreter_bridge` 名稱、主動調用走的 ART 內部函式，**逐版本變**，文中不硬編 offset，涉及處標明「以某版本為準、要實測」。Frida 腳本手寫、Frida 16.x 語法，執行標「**未實測，理論預期行為**」並附驗證步驟。FART 機制描述基於其公開論文/repo 的公開知識。本 repo 沙箱無 Android/ART/Frida。

## 為什麼需要這個？

Part 5（Ch 28/29）教過脫殼「怎麼操作」，但很多殼——尤其**抽取型（函式抽取 / instant run 型）加固**——你用整體 dump（memory dump 整塊 DEX）脫出來會是**半殘的**：類的骨架在、但**每個方法的方法體（code_item）是空的或假的**。因為這種殼的策略是「平常把方法體抽走藏著，某個方法要執行的前一刻才把真 code_item 填回它的 `ArtMethod`，執行完可能又抹掉」。你整塊 dump 抓到的，是「大部分方法體都還沒被填回」的瞬間，自然殘缺。

要破這種殼，你得**主動觸發每一個方法被執行**（或走到「即將執行」的點），逼殼把每個方法的真 code_item 填回 `ArtMethod`，然後從 `ArtMethod` 把填回的 code_item 一個個 dump 下來拼回完整 DEX。這就是 **FART（Frida-ART / 主動調用脫殼）** 的核心思想。

而這一切的前提，是你懂 Ch 34 的 `ArtMethod`——**主動調用脫殼 = 枚舉 `ArtMethod` → 觸發它 → 從它的 `dex_code_item_offset_` 找到剛填回的 code_item → dump**。不懂 `ArtMethod`，這章就是天書。

一句話：**這章是把「脫殼」從黑盒工具還原成「對 `ArtMethod` 的一系列精確操作」，讓你能對付整體 dump 破不了的抽取型殼。**

## 先建立直覺：抽取型殼藏在哪、主動調用還原什麼

先建立這章的核心心智模型。加固分兩大代（Ch 28 分過）：

```
   整體型加固 (第一代)              抽取型加固 (第二代+)
 ┌─────────────────────┐         ┌──────────────────────────────┐
 │ 整個 DEX 加密, 執行期  │         │ 類骨架留著, 但每個方法的       │
 │ 一次解密載入記憶體     │         │ code_item (方法體) 被抽走藏起  │
 │                     │         │                              │
 │ 脫法: 找到記憶體裡     │         │ 某方法要執行前一刻, 殼才把它的  │
 │ 解密後的整塊 DEX,     │         │ 真 code_item 填回 ArtMethod   │
 │ 一次 dump 下來 ✓      │         │ (執行後可能又抹掉)            │
 └─────────────────────┘         │                              │
                                 │ 脫法: 整體 dump 只抓到空殼方法, │
                                 │ 必須「主動觸發每個方法」逼它     │
                                 │ 填回 code_item, 逐個 dump ★    │
                                 └──────────────────────────────┘
```

**主動調用（active call）** 的意思：你不等 App 自然執行到某方法，而是**從外部主動去「調用」或「觸發」每一個方法**，讓殼以為「這方法要跑了」，把真 code_item 填回它的 `ArtMethod`。填回的那一刻，你從 `ArtMethod` 把 code_item dump 下來。對整個 App 的每個類的每個方法都做一遍，就集齊了所有方法體，拼回完整 DEX。

```
 for 每個 ClassLoader (Ch 35 枚舉):
   for 每個 class:
     for 每個 method (ArtMethod):
        ① 觸發它 (主動調用 / 走到即將執行的點)
        ② 殼把真 code_item 填回這個 ArtMethod
        ③ 從 ArtMethod.dex_code_item_offset_ 讀出 code_item, dump
   拼回完整 DEX
```

這個三重迴圈就是 FART 的骨架。看懂它，這章其他都是細節。

## 底層機制：主動調用怎麼逼出方法體

「主動調用」具體怎麼觸發一個方法、讓殼填回 code_item？有兩個層次的做法，理解它們的差別是這章的關鍵。

### 做法一：真的把方法叫起來（invoke）

最直接：用反射或 ART 內部的 invoke 機制，**真的執行**這個方法一次。殼通常把「填回 code_item」的邏輯掛在方法執行的入口（例如 hook 了 `art_quick_to_interpreter_bridge` 或方法的 entrypoint），所以方法一被叫，殼就填回。

問題：**真執行方法有副作用**——方法可能改狀態、發網路、崩潰（因為你亂傳參數）。對一個 App 的所有方法無差別 invoke，很容易把 App 弄崩或觸發防護。

### 做法二：只走到「即將執行」就攔下來（FART 的精髓）

FART 更聰明：它不必讓方法**完整執行完**，而是攔在「**殼已經把 code_item 填回、但還沒真正跑方法邏輯**」的那一刻。做法是 hook 住 ART 執行方法的**必經之路**——`art_quick_to_interpreter_bridge`（或對應的方法執行入口）。

```
 主動觸發某方法 → ART 準備執行它
        │
        │ 走到 art_quick_to_interpreter_bridge (方法執行必經橋)
        ▼
 ┌────────────────────────────────────────────┐
 │  ★ 你 hook 在這裡 (FART 的攔截點)            │
 │  此刻: 殼已經把 code_item 填回 ArtMethod 了   │
 │  (因為殼也把填回邏輯掛在執行入口, 早你一步跑)  │
 │                                            │
 │  → 從當前 ArtMethod 讀 code_item, dump 出來  │
 │  → dump 完可以選擇讓它繼續跑或攔掉            │
 └────────────────────────────────────────────┘
```

**為什麼 hook `art_quick_to_interpreter_bridge`？** 因為抽取型殼填回的方法體，大多是要走**直譯執行**（殼不希望它被 AOT，那樣就固化落檔了），而直譯執行的必經橋就是這個 bridge。你 hook 在這，每個被觸發的方法經過時，你都能在「code_item 剛填回、即將直譯」的完美時機拿到它。這是 FART「在對的時機、對的地方攔」的核心洞察。

> **未實測，理論預期行為**：`art_quick_to_interpreter_bridge` 是 ART 執行未編譯方法的橋接函式，名稱與位置**隨版本變**（有些版本要 hook 的是 `ExecuteSwitchImpl`、`artQuickToInterpreterBridge`、或 `Interpret` 相關函式）。FART 原版針對特定 Android 版本，跨版本要調整攔截點。你在目標裝置驗證：用 `Module.enumerateSymbols("libart.so")` 找含 `interpreter_bridge`/`Execute`/`Interpret` 的 symbol，確認你這版該 hook 哪個。

### 為什麼「填回時機」是殼與脫殼的核心戰場

抽取型殼跟脫殼者的攻防，全繞著「code_item 什麼時候在 `ArtMethod` 裡是真的」這個時間窗口打。把窗口畫出來就懂了：

```
 時間軸 →
 ┌──────────┬─────────────┬──────────────┬───────────┐
 │ 平常      │ 方法將執行   │ 方法執行中    │ 執行後     │
 │ code_item │ 殼填回真的   │ code_item 是真的│ 殼可能抹掉  │
 │ 是空的/假 │ ★填回瞬間    │              │ 抹回空/加密 │
 └──────────┴─────────────┴──────────────┴───────────┘
       ↑整體dump抓這   ↑FART攔bridge抓這(最佳)  ↑抹太快的殼要搶這之前
       (抓到空殼✗)      (剛填回, 完美✓)
```

- **整體 dump 的失敗**：它在「平常」那格截記憶體，那時大部分方法的 code_item 還是空的，自然殘缺。
- **FART 的勝利**：它把攔截點卡在「方法將執行、殼剛填回」那格——每個方法被觸發、經過 bridge 時，都是它 code_item 最真實的一刻。
- **殼的反制**：把「執行後抹掉」做得越快越好，壓縮你能 dump 的窗口。極端的殼甚至「填回 → 執行單步 → 立刻抹」，讓窗口小到你難搶。

**這就是為什麼脫殼是「時機的藝術」**——不是抓到記憶體就行，是要抓在對的那一格。你選的攔截點越接近「填回瞬間」，脫得越乾淨。

## ArtMethod hook：改 entrypoint 的兩種流派

脫殼要「觸發方法」，hook 要「劫持方法」，兩者都在操作 `ArtMethod`。ART 層 hook（相對於 Frida 的 Java 層 hook）有兩大流派，都圍繞 Ch 34 的 entrypoint：

### 流派一：替換 entrypoint（YAHFA / SandHook 思路）

把目標 `ArtMethod` 的 `entry_point_from_quick_compiled_code_` 改指向你的「hook 方法」的機器碼。之後所有對目標的呼叫，跳 entrypoint 就進了你的 hook。

```
 原本:  呼叫 target → entrypoint → target 的機器碼/bridge

 hook 後: 呼叫 target → entrypoint(已改) → 你的 hook 機器碼
                                              │ 記 log / 改參數 / 改回傳
                                              └ 需要時再調用原 target (backup)
```

關鍵細節：hook 前要**先備份**目標 `ArtMethod`（複製一份），這樣 hook 裡還能調到「原始行為」。YAHFA（Yet Another Hook For Art）就是這流派的代表——它做一個新 `ArtMethod` 當跳板、把原方法的 entrypoint 導向自己的 native trampoline。

### 流派二：設 native 旗標，讓 ART 把它當 JNI 方法

把目標 `ArtMethod` 的 `access_flags_` 加上 `kAccNative`、把 entrypoint 導向一個 JNI 風格的 stub。ART 之後把這方法當 native 方法處理，呼叫時走 JNI 路徑進你的 C 函式。這流派繞開了「機器碼跳板」的一些複雜性，但要處理 JNI 呼叫慣例。

**兩派的共同本質**（呼應 Ch 34）：**hook = 拿到 `ArtMethod` 指標，改它的 entrypoint（和可能的 access_flags），讓執行流進你的程式碼。** 這比改 DEX bytecode 強在——**對已經 AOT/JIT 編成機器碼的方法一樣有效**（因為你改的是執行跳轉點，不是它讀不讀 bytecode）。

### hook 一個方法必須處理的三件事

「改 entrypoint」聽起來一句話，實作要處理三個細節，缺一個 hook 就崩：

```
 hook target 方法
   │
   ① 備份原 ArtMethod (整份複製)
   │     → hook 裡要調「原始行為」時, 用備份的那份去執行, 不然無限遞迴
   │
   ② 把 target 的 entry_point_ 改指向你的 trampoline (機器碼跳板)
   │     → 呼叫慣例要對: 你的跳板要能接收 ART 傳給方法的參數 (含 ArtMethod*/this)
   │
   ③ 處理「這方法可能已被 AOT/JIT 編過」
   │     → 若 target 已有機器碼, 光改 entrypoint 還不夠, 可能有內聯 (inline) 過的
   │       呼叫點直接跳原機器碼, 繞過你的 hook → 要一併去最佳化 (deoptimize)
```

第三點是新手最容易忽略的坑：**ART 可能把一個小方法「內聯」進呼叫它的方法的機器碼裡**。這種情況下，呼叫點根本不經過被內聯方法的 entrypoint——你改了 entrypoint 也攔不到。解法是叫 ART 對相關方法「去最佳化（deoptimize）」，退回直譯執行，讓每次呼叫都乖乖走 entrypoint。YAHFA/SandHook 這類框架的相容性程式碼，很大一塊就在處理內聯與去最佳化。**「我 hook 了但沒攔到」的一個常見原因就是內聯**，別只懷疑 offset。

## 範例一：枚舉方法並讀 code_item 的思路

主動調用脫殼的第一步是枚舉一個類的所有 `ArtMethod`，並準備從中讀 code_item。這裡示範**思路**（**Frida 16.x，未實測，理論預期行為；offset 需目標裝置實測**）：

```javascript
Java.perform(function () {
    // 先切到目標 ClassLoader (Ch 35), 這裡假設已在正確 loader
    var targetClassName = "com.example.protected.Logic";
    var clazz = Java.use(targetClassName);

    // 拿 mirror::Class -> methods_ 陣列, 枚舉每個 ArtMethod
    // 穩健做法: 用 Java 反射拿 Method[], 再對應到 ArtMethod
    var klass = clazz.class;                          // java.lang.Class
    var declaredMethods = klass.getDeclaredMethods(); // Method[]

    console.log("[*] " + targetClassName + " 有 " + declaredMethods.length + " 個方法");
    declaredMethods.forEach(function (m) {
        console.log("    method: " + m.getName());
        // 對每個 method:
        //  ① 從 Method 物件取到底層 ArtMethod* (Frida/ART 內部對應)
        //  ② 觸發它 (主動調用 或 走 interpreter bridge)
        //  ③ 讀 ArtMethod.dex_code_item_offset_ -> 定位 code_item
        //  ④ 從所屬 DexFile 的 base + offset dump code_item bytes
    });
});
```

**這個範例強調的是「枚舉 → 逐方法處理」的框架**，而 ①③④ 的具體實作全部依賴 Ch 34 的 `ArtMethod` 佈局（要在目標裝置實測 offset）。**別把它當可直接跑的腳本**——它是給你「主動調用脫殼的程式結構長什麼樣」的骨架，練習 E 會把這骨架填成可運行的簡化版。

## 範例二：hook interpreter bridge 攔截 code_item（FART 攔截點）

承接主動調用的做法二——hook 執行必經橋，在方法即將直譯時拿 code_item。示範攔截點的**思路**（**Frida 16.x，未實測，理論預期行為；符號名需目標裝置確認**）：

```javascript
Java.perform(function () {
    // 找 ART 執行未編譯方法的必經函式 (版本敏感, 名稱要實測確認)
    var libart = Process.getModuleByName("libart.so");
    var bridge = null;
    libart.enumerateSymbols().forEach(function (sym) {
        // 依你的版本挑對: 可能是 art_quick_to_interpreter_bridge / 
        //   ExecuteSwitchImpl / artQuickToInterpreterBridge 之一
        if (sym.name.indexOf("interpreter_bridge") !== -1 ||
            sym.name.indexOf("ExecuteSwitchImpl") !== -1) {
            console.log("[*] 候選攔截點: " + sym.name + " @ " + sym.address);
            bridge = bridge || sym.address;
        }
    });

    if (bridge) {
        Interceptor.attach(bridge, {
            onEnter: function (args) {
                // 此刻: 某方法即將直譯, 殼已把 code_item 填回它的 ArtMethod
                // args 裡有 ArtMethod* (哪個參數是 ArtMethod 要對照版本 ABI)
                var artMethodPtr = args[0]; // ← 版本相關, 需驗證是不是 ArtMethod*
                // 從 artMethodPtr 讀 dex_code_item_offset_ + 所屬 DexFile base
                //   -> 定位 code_item -> dump 到檔案
                // console.log("[dump] method ArtMethod=" + artMethodPtr);
            }
        });
        console.log("[*] 攔截點已掛, 現在觸發 App 各功能讓方法經過這裡");
    } else {
        console.log("[!] 找不到攔截點, 換符號名重試 (版本問題)");
    }
});
```

**這個範例的三個要點**：

1. **攔截點要動態找、不硬編**：`enumerateSymbols` 掃出候選，依你的版本挑對的那個。這正是「不硬編 offset/名稱」原則的落實。
2. **`args[0]` 是不是 `ArtMethod*` 要驗證**：不同版本、不同攔截函式的參數順序不同，得先確認哪個參數是 `ArtMethod*`（可用已知方法反推）。
3. **dump 時機的完美性**：在這個 `onEnter`，殼已填回 code_item、方法還沒真跑——這是 FART 選這個點的精髓。你只要「觸發 App 各功能（點遍 UI、跑遍流程）」，方法就一個個經過這裡被 dump。

## 範例三（失敗/邊界）：主動調用觸發了殼的防護

主動調用不是萬能，一個常見的失敗：**你無差別 invoke/觸發所有方法，觸發了殼的反脫殼防護或讓 App 崩潰**。

```
 你對所有方法無差別主動調用
        │
        ├─ 某方法一被觸發就 System.exit / 反 dump 檢查 → App 掛掉  ✗
        ├─ 某方法真執行有副作用 (刪檔/發網路/改全域狀態) → 汙染環境 ✗
        └─ 殼偵測「短時間內大量方法被異常觸發」→ 判定被脫殼, 反制 ✗
```

**應對心法**：

1. **優先用「走到即將執行就攔」（做法二）而非「真執行」（做法一）**：只 hook bridge、不真跑方法邏輯，副作用最小。這是為什麼 FART 選 bridge 攔截而非暴力 invoke。
2. **分批、控速**：不要一瞬間觸發全部方法，殼可能偵測頻率。
3. **先脫關鍵類，別貪全量**：你多半只要幾個關鍵類的方法體（加密、簽名、核心邏輯），不必脫 App 每一個方法。縮小範圍降低觸發防護的機率。
4. **對付「執行後抹掉 code_item」的殼**：有些殼填回、執行完立刻把 code_item 抹回空。你的 dump 必須搶在「填回後、抹掉前」——bridge 攔截點正好卡在這個窗口。抹得太快的殼，要更精細地在填回的瞬間攔。

**失敗本身是情報**：主動調用一觸發就崩/被反制，說明這殼有反脫殼設計，你得從「無腦全量」轉向「精準少量 + 更早的攔截點」。

## 走一遍：一個抽取殼從加固到被脫的全程

把前面的機制串成一個完整故事，你就有了脫殼的全局圖。假設一個抽取殼保護 `com.foo.Crypto.encrypt()`：

```
 【加固階段 (打包時)】
   1. 殼工具把 encrypt() 的 code_item 從 DEX 抽出來, 加密存進殼的資料區
   2. 原 DEX 裡 encrypt() 的 code_item 被清空 / 指向假的 stub
   3. 塞入殼的 Application (SO), 接管 App 啟動 (Ch 37 窗口 C)

 【執行階段 (裝置上跑)】
   4. App 啟動, 殼 Application 先跑, hook 住方法執行入口 (interpreter bridge/entrypoint)
   5. 某處呼叫 encrypt() → 走到殼的 hook → 殼解密它的 code_item, 填回 encrypt 的 ArtMethod
   6. encrypt() 真正執行 (此刻 code_item 是真的)

 【脫殼者 (你)】
   7. frida -f 早注入 (Ch 37), 枚舉 ClassLoader 找到真 DEX (Ch 35)
   8. hook 同一個 interpreter bridge, 但你排在殼之後 → 你 onEnter 時 code_item 已被殼填回
   9. 觸發 App 走到 encrypt() → 你的 hook 讀 ArtMethod 的 code_item → dump
   10. 把 dump 的 code_item 填回骨架 DEX 的 encrypt() 位置, 修 header (Ch 4)
   11. baksmali 打開: encrypt() 方法體現形 ✓
```

**這個全程裡藏著一個對稱的美感**：殼和你 hook 的是**同一個執行入口**（interpreter bridge），差別只在「誰的 hook 先跑」——殼先填回，你後 dump。你不是跟殊死搏鬥，你是**搭殼的順風車**：殼辛苦地把真 code_item 填回來給 App 用，你在它填回後、抹掉前那一刻順手拿走。理解「脫殼是利用殼自己的還原動作」，比死記工具操作深一個層次。這也解釋了為什麼殼要做「執行後立刻抹掉」——它在試圖不讓你搭這班順風車。

## 從 dump 到「能打開的 DEX」：後處理三關

dump 出 code_item/DEX bytes 只是脫殼的一半，要變成 baksmali/jadx 打得開的東西，還有三關後處理常被新手忽略：

```
 dump 出的原始 bytes
   │
   ① 對齊: 你 dump 的起點對準 DEX magic (dex\n035) 了嗎?
   │     → 偏一個 byte, 整份都解錯。先在記憶體找 magic 定位真正的 DEX 開頭
   │
   ② header 修復: 改過內容 → checksum(adler32)+signature(SHA-1) 失效 (Ch4)
   │     → 重算這兩欄, 否則 ART/工具驗證直接拒
   │
   ③ 結構修復: map_list 對不上 / cdex 沒轉標準 DEX / debug_info 殘缺
   │     → 拿 map_list 跟實際 offset 對帳 (Ch4); cdex 先轉標準 DEX
   │
   ▼ 能被 baksmali/jadx 打開的完整 DEX
```

三關對應的正是 Ch 4 的知識在脫殼收尾的應用——**Ch 4 手撕 DEX header 不是紙上談兵，就是為了這一刻能修好 dump 產物**。練習 E 會給你一支能實跑的 `fix_dex_header.py` 處理第②關。第①關（對齊）最容易錯：dump 出來 baksmali 報一堆亂碼，先別懷疑複雜的東西，先 `xxd` 看開頭是不是 `64 65 78 0a`（`dex\n`），不是就是起點偏了。

## 對比與取捨：四種 hook/脫殼手段的層次

這是本章要你建立的**全局認知**——你手上這幾種手段各在哪一層、各自的能與不能：

| 手段 | 在哪一層 | 改什麼 | 對 AOT 編過的方法 | 隱蔽性 | 主要用途 |
|---|---|---|---|---|---|
| **Frida Java hook**（`Java.use`） | ART Java API 之上 | 透過 ART 內部替換方法實作 | 有效（Frida 自己處理 entrypoint） | 低（frida-server 好偵測） | 快速觀察/改 Java 行為 |
| **Xposed/LSPosed** | Zygote 層寄生 + ART hook | hook `ArtMethod`（框架封裝） | 有效 | 中（有 root/框架特徵） | 持久化、跨 App、開機即 hook |
| **ART entrypoint hook**（YAHFA/SandHook） | ART 內部，直接改 `ArtMethod` | `entry_point_`/`access_flags_` | 有效（改的就是執行跳轉） | 較高（無 frida-server，可內嵌 App） | 無 root 內嵌 hook、免 Frida |
| **主動調用脫殼**（FART） | ART 內部，讀 `ArtMethod` + hook bridge | 不改行為，只讀 code_item dump | 針對抽取型殼（填回的是待直譯的 code） | 依實作 | **脫抽取型加固** |

**取捨的三句話**：

1. **越往下層（越靠 `ArtMethod`），越通用、越能對付編過的方法、越不依賴外部 server**——但要懂結構、版本敏感、寫起來難。
2. **Frida Java hook 是「上層便利」**：`Java.use` 幫你把 entrypoint 那些細節都包了，快但偵測性高、對 ART 內部黑盒你看不見。
3. **脫殼（FART）跟 hook 是「讀」與「改」的不同目的**，但共用同一套 `ArtMethod` 知識——脫殼是讀 code_item，hook 是改 entrypoint。**這章讓你看清：它們都是 Ch 34 那個 `ArtMethod` 的不同玩法。**

## 踩雷集錦

1. **錯誤直覺：「整體 dump 脫得了所有殼」→ 正確認識**：抽取型殼平常方法體是空的，整體 dump 抓到一堆空方法。必須主動觸發每個方法逼殼填回 code_item，逐個 dump。看到 dump 出來方法體大量為空，就是抽取型殼。
2. **錯誤直覺：「hook 就用 Frida `Java.use` 全搞定」→ 正確認識**：`Java.use` 是上層便利，但有 frida-server 特徵、易被反 Frida 偵測（Ch 30）。要更隱蔽/免 root 內嵌時，得下到 ART entrypoint hook（改 `ArtMethod`）。它們是同一件事的不同層次。
3. **錯誤直覺：「hook `ArtMethod` 隨便找版教學抄 offset」→ 正確認識**：offset 逐版本變（Ch 34 反覆強調）。攔截點函式名（interpreter bridge）也逐版本變。全部要在目標裝置 `enumerateSymbols`/查對應原始碼確認。
4. **錯誤直覺：「主動調用就是把每個方法 invoke 一遍」→ 正確認識**：暴力 invoke 有副作用、易觸發防護、易崩。FART 的精髓是「走到即將執行（bridge）就攔」而非真跑完，副作用最小、時機最準。
5. **錯誤直覺：「code_item 填回就永遠在那」→ 正確認識**：有些殼執行完立刻抹掉 code_item。dump 必須搶「填回後、抹掉前」的窗口——bridge 攔截點正卡這裡。抹太快的殼要更精細攔。

## 進階：再往深一層

- **從 `ArtMethod` 到 DexFile base 的完整定位鏈**：dump code_item 要 `ArtMethod.dex_code_item_offset_`（方法體在 DEX 內的 offset）+ 這方法所屬 `DexFile` 在記憶體的 base。後者要透過 `ArtMethod → declaring_class_ → dex_cache_ → dex_file` 或 Ch 35 的 `DexFile.mCookie` 拿到。這條鏈是脫殼工具「知道去哪 dump」的導航，練習 E 會用到簡化版。
- **重建完整 DEX 的拼裝問題**：dump 出一堆 code_item 後，要把它們填回一個「骨架 DEX」（類結構在、方法體空的那份）的對應位置，重算 header 的 checksum/signature（Ch 4）、修 map_list。FART 系工具的後處理就在做這個「填坑 + 修 header」。
- **FART 的「主動調用」ART 實作**：FART 原版是**改 ART 原始碼重編 ROM**（在 ART 內部加主動調用邏輯），不是純 Frida。純 Frida 版是社群後來的移植，受限於 Frida 能碰到的層次。理解「FART 原版要改 ART」你才懂為什麼它那麼版本綁定。
- **對抗 dump 的殼技巧**：反脫殼會做——填回 code_item 時做完整性混淆、偵測 bridge 被 hook、code_item 用時解密用完加密、把方法拆碎多次填回。這是「殼與脫殼」的軍備競賽，每一招對應一個更精細的 dump 時機/手法。
- **VMP / dex2c 型加固（更難的一代）**：更強的加固把方法體**翻譯成自訂 VM 的指令**或**編譯成 C（dex2c）搬進 `.so`**，DEX 層根本沒有真 code_item 可 dump——主動調用脫殼對它無效。這時要逆的是那個自訂 VM 或 native 程式碼（回到 Part 4）。知道「主動調用脫殼的邊界在哪」很重要：它治抽取型，不治 VMP。

## 動手練習

1. 找一個抽取型加固的樣本（或用開源加固 demo），先用整體 dump 脫一次，`baksmali` 打開看方法體——親眼看到大量方法體為空（`.method` 裡沒指令），確認「整體 dump 對抽取型殼無效」。
2. 用範例二的思路，在 AVD 上 `Module.enumerateSymbols("libart.so")` 找你這版該 hook 的 interpreter bridge / Execute 函式名，記下來——體會「攔截點要實測不能抄」。
3. 對一個普通（無殼）App，用範例一枚舉一個類的所有方法，先只做「枚舉 + 印方法名」，跑通。這是練習 E 的第一步。
4. 讀 FART 的 repo README 與論文摘要，找出它原版「改了 ART 哪些地方加主動調用」——理解純 Frida 移植版為什麼受限、為什麼版本綁定。
5. 對照本章的四手段對比表，不看筆記，自己畫一遍「Frida Java hook / Xposed / ART entrypoint hook / FART」各在哪一層、改/讀什麼——畫得出來代表你把層次關係內化了。

## 本章重點整理

- **抽取型加固**把方法體（code_item）抽走，執行前一刻才填回 `ArtMethod`。整體 dump 抓到空方法，必須**主動調用**逐個逼出 code_item 再 dump。
- **主動調用兩做法**：真 invoke（有副作用、易崩/觸發防護）vs **走到 interpreter bridge 就攔**（FART 精髓，副作用小、時機準）。攔 `art_quick_to_interpreter_bridge`（版本敏感）在「填回後、直譯前」的完美窗口讀 code_item。
- **ART hook = 改 `ArtMethod` 的 entrypoint（YAHFA/SandHook）或設 native 旗標**，對已 AOT/JIT 編過的方法一樣有效（改的是執行跳轉，不是 bytecode）。
- **四手段層次**：Frida Java hook（上層便利、易偵測）→ Xposed（Zygote 持久化）→ ART entrypoint hook（下層通用、免 server）→ FART（讀 code_item 脫抽取型殼）。**全都是 Ch 34 `ArtMethod` 的不同玩法。**
- **主動調用脫殼的邊界**：治抽取型殼；對 VMP/dex2c（方法體變自訂 VM 指令或搬進 `.so`）無效，那要回 Part 4 逆 native。

## 自我檢核

- [ ] 不看筆記，能講出抽取型加固為什麼整體 dump 脫不乾淨，以及主動調用怎麼補救
- [ ] 能畫出主動調用脫殼的三重迴圈（ClassLoader → class → method → 觸發 → dump code_item）
- [ ] 能解釋 FART 為什麼選 hook interpreter bridge、那個攔截點卡在什麼窗口
- [ ] 能說出 ART hook 改 `ArtMethod` 的哪個/哪些欄位，以及為什麼對已編譯方法有效
- [ ] 能講清楚 Frida Java hook / Xposed / ART entrypoint hook / FART 各在哪一層、各自的能與不能
- [ ] 能說出主動調用脫殼治不了哪種加固（VMP/dex2c），以及那時該往哪走

## 延伸閱讀

### FART（一手）

- **[FART 論文與 repo](https://github.com/hanbinglengyue/FART)** — FART 作者
  - **讀哪裡**：README 的原理說明、它改了 ART 哪些函式加主動調用；配套的看雪原理文
  - **為什麼值得讀**：主動調用脫殼的原始出處，本章「攔 bridge、逐方法 dump code_item」的完整版
  - **注意**：原版針對特定 Android 版本，理解它的版本綁定性再看社群移植版

### ART hook 框架（原始碼）

- **[YAHFA](https://github.com/PAGalaxyLab/YAHFA) / [SandHook](https://github.com/asLody/SandHook)** — 社群
  - **讀哪裡**：它們怎麼備份原 `ArtMethod`、怎麼改 entrypoint / 建 trampoline、怎麼處理版本差異
  - **和本章的關聯**：本章「ART entrypoint hook 兩流派」的實作，讀它們的版本適配程式碼理解「offset 為什麼要動態處理」
  - **前提知識**：Ch 34 的 `ArtMethod`/entrypoint，這裡看它怎麼被工程化

### 逆向社群

- **[看雪 — 主動調用脫殼 / ArtMethod hook 系列](https://bbs.kanxue.com/)**（站內搜「FART 主動調用 脫殼 ArtMethod hook」）
  - **這篇說什麼**：中文社群對各代殼、主動調用脫殼、entrypoint hook 的實測拆解
  - **讀哪裡**：找專講抽取型殼原理、FART 移植、code_item dump 拼 DEX 的帖
  - **前提知識**：讀過本章的框架，這些帖給你「實際各家殼與 dump 細節」的案例

### ART 內部（最終仲裁）

- **[art/runtime/interpreter/ 與 entrypoints/](https://cs.android.com/android/platform/superproject/+/master:art/runtime/interpreter/)** — Android Code Search
  - **讀哪裡**：`interpreter.cc`、`ExecuteSwitchImpl`、`art_quick_to_interpreter_bridge` 相關；找你版本真正的執行必經路徑
  - **為什麼值得讀**：本章攔截點的權威依據，**切目標版本 tag** 確認你該 hook 哪個函式

下一章我們把視角拉回進程層面，補齊注入的最後一塊拼圖——Zygote fork+specialize、進程建立、SELinux `untrusted_app` domain 對注入/讀寫的限制、`app_process`。你會理解「你的 hook/脫殼程式碼，到底在哪個時機、以什麼身分、被什麼限制著跑進目標進程」。

→ [Ch 37 Zygote、進程、SELinux 對逆向的影響](./37-zygote-process-selinux.md)
