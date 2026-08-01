# Ch 35 — ClassLoader 機制與熱補

> **目標**：搞懂 Android 的類載入體系——`BootClassLoader`/`PathClassLoader`/`DexClassLoader` 各管什麼、雙親委派（parent delegation）怎麼決定「一個類到底從哪個 DEX 載進來」、App 怎麼在執行期動態載入額外的 DEX，以及熱修復（hotfix）與插件化（plugin）怎麼利用這套機制在不重裝 App 的情況下換掉/新增程式碼。你要能回答一個逆向核心問題：**當一個 App 的關鍵邏輯是執行期才動態載入的 DEX，我要怎麼找到它、dump 出來？** 這章把 Ch 34 的 `mirror::Class.class_loader_` 欄位連到具體的 ClassLoader 物件，是理解動態載入類加固/插件化的地基。

> **環境**：本章以 **Android 13 / API 33（ART）** 為準。ClassLoader 是 framework Java 層（`libcore`/`dalvik.system`）機制，相對 `ArtMethod` offset 穩定，但 `DexPathList`/`Element` 等內部欄位名仍**可能因版本微調**，涉及反射欄位處會標明。Frida 腳本手寫、語法為 Frida 16.x，執行標「**未實測，理論預期行為**」並給驗證步驟。本 repo 沙箱無 Android/Frida。

## 為什麼需要這個？

因為現代 App（尤其加固過的、走插件化架構的）的關鍵程式碼，**根本不在你 apktool 反出來的那幾個 `classes*.dex` 裡**。它可能：

- 被加固殼加密藏起來，執行期才解密、用 `DexClassLoader` 動態載入。
- 被拆成一個個「插件」，主 App 只是個殼，真功能執行期才從別的 apk/dex 載入。
- 被熱修復框架換掉——你靜態看到的方法體是舊的，執行期被 patch 成新的。

這三種情況，你只看靜態 DEX 都會**找不到真邏輯**或**找到的是假的/舊的**。要破，你得懂「類是從哪個 ClassLoader 載進來的」——因為所有動態載入的 DEX，最終都掛在某個 ClassLoader 上。**找到那個 ClassLoader，就找到了它動態載入的所有 DEX**，順藤摸瓜就能 dump。這是脫殼（Ch 36）與逆向插件化 App 的共同入口。

一句話：**ClassLoader 是「執行期程式碼從哪來」的總帳本，逆向動態載入的 DEX，第一步就是找到並枚舉這本帳。**

## 先建立直覺：ClassLoader 是「去哪找 class」的策略物件

先給心智模型。當程式需要一個類（`new Foo()`、或呼叫 `Foo` 的 static 方法觸發載入），ART 不會憑空知道 `Foo` 的 bytecode 在哪——它問一個 **ClassLoader**：「請幫我把 `Foo` 這個類載進來」。ClassLoader 就是一個**知道「去哪個 DEX 找類」的策略物件**。

```
   程式碼需要 class Foo
        │
        │ 問「當前的 ClassLoader」: 幫我載 com.example.Foo
        ▼
   ┌──────────────────────────────────────────┐
   │  ClassLoader                              │
   │   ├ parent  ─▶ 先問爸爸有沒有 (雙親委派)   │
   │   └ 自己管的 DEX 清單 (DexPathList)        │
   │        ├ classes.dex                      │
   │        ├ classes2.dex                     │
   │        └ (動態載入的) plugin.dex  ← 逆向重點│
   └──────────────────────────────────────────┘
        │
        ▼
   找到 Foo 的 class_def → 交給 ClassLinker 載成 mirror::Class (Ch 34)
```

三個重點：

1. **每個 ClassLoader 管一組 DEX**（它的 `DexPathList`）。要載類時它在自己管的 DEX 裡找。
2. **ClassLoader 有 parent**，載類時先問 parent（雙親委派，下節詳談）。
3. **動態載入的 DEX 會出現在某個 ClassLoader 的 DEX 清單裡**——這是逆向的鑰匙：枚舉所有 ClassLoader 的 DEX 清單，就能找出執行期偷偷載進來的東西。

**Ch 34 講的 `mirror::Class.class_loader_` 欄位，就是指向載入這個類的 ClassLoader 物件。** 一個類是誰載的，決定了它能看到哪些其他類——這在插件化裡是隔離的關鍵。

## 三個主要 ClassLoader：各管一段

Android 的類載入分工靠三個（實務上主要碰的）ClassLoader，形成一條委派鏈：

```
        BootClassLoader          ← 載入系統 framework 類 (java.*, android.*)
             ▲   parent          (從 boot class path: 那些 boot .art/.oat)
             │
        PathClassLoader          ← 載入 App 自己的 classes*.dex
             ▲   parent          (App 啟動時系統建好的, 管 APK 裡的 DEX)
             │
        DexClassLoader           ← App 執行期自己 new 的, 載「額外的」DEX
                                   (熱修復/插件/加固殼動態載入用這個) ★
```

逐個看：

| ClassLoader | 誰建的 | 管哪些 DEX | 逆向意義 |
|---|---|---|---|
| **`BootClassLoader`** | 系統 | framework 類（`java.lang.*`、`android.*`），來自 boot image | 你 hook 系統類（如 `System`、`Activity`）時，它們由這個載；一般 App 邏輯不在這 |
| **`PathClassLoader`** | 系統（App 啟動時） | App APK 裡的 `classes*.dex` | **App 的主邏輯 ClassLoader**；Frida 的 `Java.use` 預設在這條鏈上找類 |
| **`DexClassLoader`** | **App 自己在執行期 `new`** | 任意路徑的額外 DEX/APK/JAR | ★ **熱修復、插件化、加固殼動態載入真 DEX，都用它**——逆向動態載入的第一嫌疑犯 |

`PathClassLoader` 與 `DexClassLoader` 都繼承自 `BaseDexClassLoader`，差別主要在**能不能指定一個可寫的 optimized 目錄**（歷史上 `DexClassLoader` 可以，用來載外部 DEX；新版兩者差異縮小）。**關鍵記憶點**：App 啟動時系統只給它一個 `PathClassLoader`（管 APK 內的 DEX）；任何「APK 以外」的程式碼要載進來，App 得**自己建一個 `DexClassLoader`**——這個「自己建的 ClassLoader」就是逆向動態載入的路標。

## 底層機制：雙親委派決定「類從哪載」

「雙親委派（parent delegation）」是理解類載入的核心規則。當一個 ClassLoader 被要求載入 `Foo`，它**不先自己找，而是先問 parent**：

```
 loadClass("com.example.Foo")  對某個 ClassLoader
   │
   ▼
 ① 我載過 Foo 了嗎? (findLoadedClass)
   │  載過 → 直接回, 結束
   │  沒載過 ↓
   ▼
 ② 先問 parent: 「你能載 Foo 嗎?」(遞迴往上委派)
   │  parent 載得到 → 用 parent 的, 結束
   │  parent 一路到 BootClassLoader 都載不到 ↓
   ▼
 ③ 才輪到我自己找 (findClass: 在我管的 DEX 裡找)
   │  找到 → 載入
   │  找不到 → ClassNotFoundException
```

為什麼要「先問爸爸」？**為了核心類的唯一性與安全**。假設你的 App 自己塞一個假的 `java.lang.String`，雙親委派保證：載 `String` 時先問到 `BootClassLoader`，它有真的 `String`，就用真的——你的假 `String` 永遠輪不到。這防止核心類被冒充。

**這對逆向有三個直接後果**：

1. **同一個類名可能在不同 ClassLoader 各有一份，是「不同的類」**。ART 判斷「兩個類是否相同」看的是 `(類名, 定義它的 ClassLoader)` 這個組合。這是插件化隔離的基礎——插件 A 和插件 B 各自的 ClassLoader 載同名類，彼此是不同的類、不衝突。
2. **打破雙親委派是插件化/熱修復的常見手法**。有些框架故意讓自己的 ClassLoader **不**先問 parent，而是自己先找（parent-last），這樣就能用自己的類「蓋掉」parent 的同名類——熱修復換方法就靠這個。
3. **Frida `Java.use` 找不到類，常是 ClassLoader 問題**。`Java.use` 預設在 App 的 `PathClassLoader` 那條鏈找。如果目標類是某個 `DexClassLoader` 動態載入的，不在預設鏈上，`Java.use` 就 `ClassNotFoundException`——解法是先找到那個 ClassLoader，用 `Java.classFactory.loader = 那個 loader` 切過去（下面範例會做）。

## DexPathList：ClassLoader 內部裝 DEX 的地方

一個 `BaseDexClassLoader`（`PathClassLoader`/`DexClassLoader` 的父類）內部，真正裝「我管哪些 DEX」的是一個叫 **`pathList`（`DexPathList` 型別）** 的欄位。`DexPathList` 裡有個 **`dexElements` 陣列**，每個 `Element` 包一個 `DexFile`（一個實際的 DEX）：

```
 BaseDexClassLoader
   └ pathList : DexPathList
        └ dexElements : Element[]        ← ★ 逆向 dump 動態 DEX 的目標
             ├ Element{ dexFile → classes.dex }
             ├ Element{ dexFile → classes2.dex }
             └ Element{ dexFile → plugin.dex }   ← 動態載入的就疊在這
```

**這是逆向動態載入 DEX 的技術核心**：

- 熱修復/插件化框架載入新 DEX，做的就是**往某個 ClassLoader 的 `dexElements` 陣列塞新的 `Element`**（或建新 ClassLoader）。
- 你逆向時，只要**反射拿到目標 ClassLoader → `pathList` → `dexElements`**，遍歷每個 `Element` 的 `DexFile`，就枚舉出了「這個 ClassLoader 實際載了哪些 DEX」，包括動態載入的。
- 每個 `DexFile` 內部有指向那份 DEX 在記憶體/檔案的位置的資訊，順著它就能把 DEX dump 出來。

> **未實測，理論預期行為**：`pathList`/`dexElements`/`Element`/`dexFile` 這些欄位名取自 AOSP `libcore/dalvik/src/main/java/dalvik/system/`（`BaseDexClassLoader.java`、`DexPathList.java`、`DexFile.java`）。**欄位名在多數版本穩定，但仍可能因版本微調**（例如某版把 `dexFile` 換名或加中間層）。你在目標裝置驗證：反射 dump 這條鏈時如果 `NoSuchFieldException`，就對照該版本原始碼確認欄位名。

## 熱修復與插件化：同一套機制的兩個用途

熱修復（hotfix，如 Tinker/Robust）和插件化（plugin，如 Shadow/RePlugin）**用的是同一套 ClassLoader 機制，目的不同**：

- **熱修復**：線上 App 有 bug，不想發版重裝，想「打補丁換掉某幾個方法」。做法：下發一個補丁 DEX，想辦法讓 App 執行期**優先載入補丁裡的新版類/方法**，蓋掉舊的。
- **插件化**：把 App 拆成「宿主（host）+ 一堆插件（plugin apk）」，插件不裝進系統、執行期動態載入，實現「不更新主 App 就上新功能」。

兩者的核心技術動作都是「**執行期把新 DEX 掛上某個 ClassLoader，並讓類載入解析到新的**」。看幾種代表思路（逆向時你會遇到這些框架的痕跡）：

```
 熱修復思路 A (Tinker 早期): 全量替換 dexElements
   把補丁 DEX 做成 Element, 插到 App PathClassLoader 的
   dexElements 陣列「最前面」→ 因為找類是按陣列順序找,
   補丁裡的同名類排在前面就先被找到 → 蓋掉舊的
        dexElements = [ patch.dex, classes.dex, classes2.dex ]
                        └ 新版排前面, 先命中

 熱修復思路 B (Robust): 方法級 hook
   不換類, 在每個方法插一個「跳板」, 執行期判斷有沒有補丁,
   有就跳去補丁的實作 (更細粒度, 但要編譯期插樁)

 插件化思路 (Shadow/RePlugin): 每個插件一個 ClassLoader
   宿主為每個插件 new 一個 DexClassLoader 載入該插件 apk,
   用 ClassLoader 隔離不同插件的同名類 (靠雙親委派的
   「類 = 類名 + ClassLoader」特性)
```

**逆向啟示**：無論哪種，「新程式碼」最終都落在某個 ClassLoader 的 `dexElements` 裡。你逆一個熱修復/插件化 App 卡在「靜態看到的方法跟實際行為對不上」時，就要意識到「執行期有東西被掛上來了」——去枚舉所有 ClassLoader 的 DEX，把動態載入的那些 dump 出來，才看得到真行為。

## 範例一：枚舉一個 App 的所有 ClassLoader 與它們的 DEX

我們用 Frida 枚舉進程裡所有 ClassLoader、印出每個載了哪些 DEX——這是逆向動態載入的第一個動作（**Frida 16.x 語法，未實測，理論預期行為**）：

```javascript
Java.perform(function () {
    // enumerateClassLoaders 列出進程裡所有 ClassLoader 實例
    Java.enumerateClassLoaders({
        onMatch: function (loader) {
            console.log("[ClassLoader] " + loader);
            try {
                // 反射拿 pathList → dexElements, 印出每個 DEX 的來源
                var BaseDexClassLoader = Java.use("dalvik.system.BaseDexClassLoader");
                if (BaseDexClassLoader.class.isInstance(loader)) {
                    var pathListField = BaseDexClassLoader.class.getDeclaredField("pathList");
                    pathListField.setAccessible(true);
                    var pathList = pathListField.get(loader);

                    var dexElementsField = pathList.getClass().getDeclaredField("dexElements");
                    dexElementsField.setAccessible(true);
                    var elements = dexElementsField.get(pathList);

                    var len = Java.use("java.lang.reflect.Array").getLength(elements);
                    for (var i = 0; i < len; i++) {
                        var el = Java.use("java.lang.reflect.Array").get(elements, i);
                        console.log("    element[" + i + "] = " + el);
                    }
                }
            } catch (e) {
                console.log("    (無法讀 dexElements: " + e + ")");
            }
        },
        onComplete: function () { console.log("[*] 枚舉完成"); }
    });
});
```

**期望輸出（代表性）**：

```
[ClassLoader] dalvik.system.PathClassLoader[DexPathList[[zip file "/data/app/.../base.apk"]]]
    element[0] = dex file "/data/app/.../base.apk"
[ClassLoader] dalvik.system.DexClassLoader[DexPathList[[dex file "/data/data/com.foo/files/plugin.dex"]]]   ← ★ 動態載入的!
    element[0] = dex file "/data/data/com.foo/files/plugin.dex"
[*] 枚舉完成
```

**讀懂它**：你看到兩個 ClassLoader——一個 `PathClassLoader` 載 APK 的 `base.apk`（正常），另一個 `DexClassLoader` 載了 `/data/data/com.foo/files/plugin.dex`（**執行期動態載入的**！這就是加固殼解密後、或插件化框架載入的真 DEX）。找到這個路徑，你就能 `adb pull` 它、或（如果是記憶體裡的 DEX）走 Ch 36 的記憶體 dump。**「多出來一個你沒預期的 DexClassLoader」就是動態載入的鐵證。**

## 範例二：切換 Frida 的 ClassLoader 找到動態載入的類

承接範例一——你發現目標類在那個 `DexClassLoader` 裡，但 `Java.use("com.foo.plugin.Secret")` 報 `ClassNotFoundException`（因為預設在 `PathClassLoader` 鏈上找不到）。解法是把 Frida 的 classFactory 切到正確的 loader（**Frida 16.x，未實測，理論預期行為**）：

```javascript
Java.perform(function () {
    var targetLoader = null;
    Java.enumerateClassLoaders({
        onMatch: function (loader) {
            // 找那個載了目標類的 loader (用 loader.toString 含 plugin.dex 粗篩)
            if (loader.toString().indexOf("plugin.dex") !== -1) {
                targetLoader = loader;
            }
        },
        onComplete: function () {}
    });

    if (targetLoader !== null) {
        console.log("[*] 找到目標 loader, 切過去");
        Java.classFactory.loader = targetLoader;      // ★ 切 ClassLoader
        var Secret = Java.use("com.foo.plugin.Secret"); // 現在找得到了
        Secret.decrypt.implementation = function (x) {
            var r = this.decrypt(x);
            console.log("[hook] Secret.decrypt(" + x + ") -> " + r);
            return r;
        };
        console.log("[*] hook 裝好");
    } else {
        console.log("[!] 沒找到目標 loader");
    }
});
```

**這個範例教你一個關鍵技能**：`Java.use` 找不到類**不代表類不存在**，很可能是「你站錯 ClassLoader 了」。切到正確的 loader（`Java.classFactory.loader = targetLoader`），動態載入的類就現形。**逆向插件化 App、加固殼解密後的類，這一招是家常便飯。**

## 範例三（失敗/邊界）：DEX 只在記憶體、根本沒落檔

範例一的樂觀情況是動態 DEX 有個檔案路徑能 `adb pull`。但更狠的加固**不落檔**——直接在記憶體裡建 DEX（用 `InMemoryDexClassLoader`，Android 8+ 支援從 `ByteBuffer` 載 DEX），你在 `dexElements` 看到的 `Element` 指向的是一塊**記憶體**而不是檔案：

```
 樂觀:  DexClassLoader → element → dex file "/data/.../plugin.dex"
         └ 有路徑, adb pull 就拿到  ✓

 狠一點: InMemoryDexClassLoader → element → dex 在 ByteBuffer (記憶體)
         └ 沒檔案路徑! 你 pull 不到, 只能從記憶體 dump  ✗→Ch36
```

```javascript
// InMemoryDexClassLoader 的 element 沒有檔案路徑
// element.toString() 可能顯示 "dex file "" (空路徑) 或記憶體位址
// 這時要拿到那個 DexFile / 記憶體區間, 從記憶體 dump 出 DEX
```

**心法**：ClassLoader 枚舉先告訴你「有幾份 DEX、各在哪」。**落檔的**你 `adb pull` 走靜態；**只在記憶體的**（`InMemoryDexClassLoader` 或殼直接 mmap 的）你 pull 不到，得從 `DexFile` 物件反查記憶體位址、把那段 DEX bytes dump 出來——**這就進入 Ch 36 的記憶體 dump / 主動調用領域了**。看到 `InMemoryDexClassLoader` 或空路徑的 element，就知道「這殼不落檔，要動記憶體」。

## 對比與取捨：三種 ClassLoader / 兩種動態載入

| 面向 | `PathClassLoader` | `DexClassLoader` | `InMemoryDexClassLoader` |
|---|---|---|---|
| 誰建 | 系統（App 啟動時） | App 執行期自己 new | App 執行期自己 new（Android 8+） |
| 載入來源 | APK 內的 DEX | 檔案系統上的 DEX/APK/JAR | 記憶體 `ByteBuffer` |
| 逆向難度 | 低（apktool 直接反） | 中（找路徑 → pull → 反） | 高（不落檔，要記憶體 dump） |
| 常見用途 | App 主邏輯 | 熱修復/插件/加固殼載真 DEX | 更隱蔽的加固殼 |
| 逆向切入 | 靜態直接來 | 枚舉 ClassLoader → pull 檔 | 枚舉 → 從 DexFile 記憶體 dump（Ch 36） |

一句話取捨：**加固殼藏 DEX 的隱蔽程度，跟「DEX 落不落檔」直接掛鉤**——落檔（`DexClassLoader` 指檔案）好抓，不落檔（`InMemoryDexClassLoader` / 殼自管記憶體）就得往記憶體 dump 走。而無論哪種，枚舉 ClassLoader 都是找到它的**第一步共同入口**。

## 踩雷集錦

1. **錯誤直覺：「apktool 反出的 `classes*.dex` 就是 App 全部程式碼」→ 正確認識**：加固/插件化 App 的真邏輯常是執行期動態載入的 DEX，不在 APK 裡。要枚舉所有 ClassLoader 的 `dexElements` 才看得到全貌。
2. **錯誤直覺：「`Java.use` 找不到類 = 類不存在」→ 正確認識**：多半是你站在錯的 ClassLoader。動態載入的類在別的 loader 上，`Java.classFactory.loader = 那個 loader` 切過去就找到了。
3. **錯誤直覺：「同名類就是同一個類」→ 正確認識**：ART 判斷類相等看 `(類名, ClassLoader)`。不同 ClassLoader 載的同名類是**不同的類**——這是插件化隔離的基礎，也是為什麼你 hook 的類要對到正確的 loader。
4. **錯誤直覺：「雙親委派是死規則」→ 正確認識**：熱修復/插件化框架故意打破它（parent-last），讓自己的類蓋掉 parent 的。看到 App 有自訂 ClassLoader 且載類行為怪，先懷疑它改了委派順序。
5. **錯誤直覺：「動態 DEX 一定有檔案路徑能 pull」→ 正確認識**：`InMemoryDexClassLoader` 從記憶體載、殼可能自管 mmap，都不落檔。`dexElements` 的 element 顯示空路徑/記憶體位址時，pull 不到，得走記憶體 dump（Ch 36）。

## 進階：再往深一層

- **`DexFile.mCookie` 與 dump 記憶體 DEX**：`DexFile` 物件內部有個 `mCookie`（在新版本是一個 long 陣列或 `Object`），編碼了 native 層 DEX 結構（`DexFile` 的 C++ 端）的位址。逆向記憶體 DEX dump 時，透過 `mCookie` 反查 native `DexFile` 結構、找到 DEX 在記憶體的 begin/size，就能把 bytes dump 出來。這是很多脫殼工具的底層手法之一。
- **`ClassLoader` 注入攻擊**：因為載類看 ClassLoader，安全研究會關注「能不能往 App 的 ClassLoader `dexElements` 塞自己的 DEX」——塞成功等於在 App 進程裡注入了自己的 Java 程式碼（比 Frida 更「原生」）。有些 Xposed 模組、注入框架就是這思路。
- **`LoadedApk` 與 ClassLoader 的建立時機**：App 的 `PathClassLoader` 是 `LoadedApk.getClassLoader()` 在 App 進程初始化時建的（`ActivityThread` 流程）。加固殼常 hook 這個時機、在 App 真正跑起來前替換/包裝 App 的 ClassLoader——這是「殼怎麼接管載類」的關鍵時機點，連到 Ch 37 的 Application 啟動流程。
- **`DelegateLastClassLoader`**：Android 提供的官方「parent-last」ClassLoader（先自己找再問 parent），插件化框架有時直接用它。看到 App 用這個，就知道它刻意要讓自己的類優先。
- **verify 與動態載入的效能**：動態載入的 DEX 首次載入要 verify（Ch 34 的類 status），大量插件會有啟動開銷。有些框架把動態 DEX 也做 dex2oat（產 vdex/oat）加速——這意味著動態 DEX 也可能有 oat 產物落在 `/data/data/<pkg>/` 底下，是另一個 dump 來源。

## 動手練習

1. 寫一個小 App（或找一個插件化 demo），執行期用 `DexClassLoader` 載一個外部 DEX。用範例一的 Frida 腳本枚舉 ClassLoader，親眼看到你的 `DexClassLoader` 和它載的 DEX 路徑出現在列表裡。
2. 對那個動態載入的類，先 `Java.use` 故意觸發 `ClassNotFoundException`，再用範例二切 `Java.classFactory.loader` 到正確 loader，讓 `Java.use` 成功——體會「找不到類 = 站錯 loader」。
3. `adb shell find /data/data/<pkg> -name '*.dex' 2>/dev/null` 找 App 執行期落檔的動態 DEX，pull 出來 jadx 打開，對照它在靜態 APK 裡有沒有——確認「動態 DEX 不在原 APK」。
4. 查 `libcore` 的 `BaseDexClassLoader.java`/`DexPathList.java`（cs.android.com 對應版本 tag），確認 `pathList`/`dexElements`/`Element.dexFile` 欄位名，跟你 Frida 反射用的對齊。換一個舊版本 tag 看欄位有沒有變。

## 本章重點整理

- **ClassLoader 是「去哪找 class」的策略物件**，每個管一組 DEX（`pathList.dexElements`）；`mirror::Class.class_loader_`（Ch 34）就指向它。
- **三個主要 ClassLoader**：`BootClassLoader`（framework 類）、`PathClassLoader`（App 主 DEX）、`DexClassLoader`（**App 執行期載額外 DEX，熱修復/插件/加固殼的載體**）。
- **雙親委派**：載類先問 parent，保證核心類唯一。後果：類相等 = `(類名, ClassLoader)`；熱修復/插件化常打破委派（parent-last）讓自己的類蓋掉舊的；Frida 找不到類多半是站錯 loader。
- **逆向動態載入 DEX 的入口 = 枚舉所有 ClassLoader 的 `dexElements`**：落檔的 `adb pull`，只在記憶體的（`InMemoryDexClassLoader`）走 Ch 36 記憶體 dump。「多出來一個沒預期的 DexClassLoader」就是動態載入的鐵證。

## 自我檢核

- [ ] 不看筆記，能畫出 Boot/Path/Dex 三個 ClassLoader 的委派鏈，並說出各管什麼
- [ ] 能解釋雙親委派的三步流程，以及「先問爸爸」是為了什麼
- [ ] 能講清楚為什麼「同名類在不同 ClassLoader 是不同的類」，以及這在插件化裡的作用
- [ ] 能說出逆向動態載入 DEX 的第一步（枚舉 ClassLoader 的 dexElements），以及落檔 vs 記憶體 DEX 的不同處理
- [ ] 能解釋 `Java.use` 報 `ClassNotFoundException` 時，除了「類不存在」還有什麼可能、怎麼解
- [ ] 能說出熱修復與插件化「用同一套機制」的共同技術動作是什麼

## 延伸閱讀

### 原始碼（一手依據）

- **[libcore dalvik.system ClassLoader 家族](https://cs.android.com/android/platform/superproject/+/master:libcore/dalvik/src/main/java/dalvik/system/)** — Android Code Search
  - **讀哪裡**：`BaseDexClassLoader.java`、`DexPathList.java`、`DexClassLoader.java`、`InMemoryDexClassLoader.java`、`DexFile.java`
  - **為什麼值得讀**：`pathList`/`dexElements`/`mCookie` 這些你反射會用到的欄位，這裡是定義處。**切到目標版本 tag** 確認欄位名
  - **和本章的關聯**：本章 Frida 反射鏈的每個欄位名都出自這幾個檔

### 逆向實戰

- **[看雪 — ClassLoader 與脫殼系列](https://bbs.kanxue.com/)**（站內搜「ClassLoader 脫殼 dexElements InMemoryDexClassLoader」）
  - **這篇說什麼**：中文社群拆解各家加固殼怎麼用 ClassLoader 藏 DEX、怎麼枚舉 dump
  - **讀哪裡**：找專講 DexClassLoader 動態載入偵測、mCookie dump 記憶體 DEX 的帖
  - **前提知識**：讀過本章的 ClassLoader 機制，這些帖給你「實際各家殼長怎樣」的案例
- **[Frida enumerateClassLoaders / classFactory 文件](https://frida.re/docs/javascript-api/#java)** — frida.re
  - **這篇說什麼**：`Java.enumerateClassLoaders`、`Java.classFactory.loader` 的官方用法
  - **讀哪裡**：Java API 的 ClassLoader 相關那幾節
  - **和本章的關聯**：本章範例一、範例二用的 API 出處

### 框架原理

- **[Tinker / Shadow 開源專案](https://github.com/Tencent/tinker)** — 騰訊（Tinker 熱修復）與 [Shadow](https://github.com/Tencent/Shadow)（插件化）
  - **這篇說什麼**：真實生產級熱修復/插件化框架的實作，本章「思路」的完整版
  - **讀哪裡**：Tinker 的 dex patch 載入流程；Shadow 的 plugin ClassLoader 管理
  - **前提知識**：讀過本章的 dexElements 替換與 ClassLoader 隔離，這裡看它們工程上怎麼落地

下一章我們把 Ch 34 的 `ArtMethod` 與本章的動態載入合起來，進入 Part 6 的高潮——從 ART 內部脫殼與 hook：為什麼「主動調用」能還原被抽空的方法體、ArtMethod hook 怎麼改 entrypoint、FART 的主動調用機制，以及它跟 Frida/Xposed hook 的層次差異。

→ [Ch 36 從 ART 內部脫殼與 hook](./36-art-unpacking-hook.md)
