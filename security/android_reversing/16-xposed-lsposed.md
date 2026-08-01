# Ch 16 — Xposed / LSPosed：持久化 hook

> **目標**：搞懂另一種 hook 哲學。Frida 是「即時注入、attach 上去才生效、你關掉就沒了」；Xposed 是「把 hook 種進系統，每次目標 App 啟動就自動帶著你的修改，重開機也還在」。你會學到 Xposed 的底層原理（怎麼透過 Zygote 讓 hook 對「每一個之後出生的 App 進程」生效）、LSPosed 這個現代實作（基於 Riru/Zygisk，免改系統分區）、親手寫一個最小 Xposed 模組（`IXposedHookLoadPackage` + `findAndHookMethod`），並在腦中把 Xposed 跟 Frida 的取捨釘清楚。

> **環境**：AVD（Android 13 / API 33，x86_64，Google APIs）、**LSPosed**（Zygisk 模式，需先裝 **Magisk**）、Android Studio 寫模組。本章的模組原始碼可在你的環境編譯安裝，行為描述標「**未實測，理論預期行為**」並附驗證步驟；Java hook 邏輯本身的正確性用純 Java 概念說明，涉及裝置行為的一律不假裝跑過。

## 為什麼需要這個？

Frida 很強，但它有一個結構性限制：**它是「事後 attach」的**。你得先讓 App 跑起來（或 `-f` spawn 它），Frida client 連上去、注入腳本，hook 才生效。這帶來三個實務痛點：

1. **時序**：有些關鍵邏輯在 App **啟動的極早期**就跑完了（`Application.onCreate` 裡的反調試、SDK 初始化的金鑰生成）。等你 attach 上去，早就跑完了——你 hook 得再準也慢一步。
2. **持久化**：你重開機、App 重啟，Frida 的 hook 全沒了，要重新 attach、重跑腳本。想長期改一個 App 的行為（例如永久去廣告、永久解鎖某功能），Frida 不是為此設計的。
3. **穩定性/隱蔽**：Frida 注入會留下明顯痕跡（`frida-server` 進程、特定 port、記憶體特徵），反 Frida 檢測（Ch 30）很容易抓到。

Xposed 從另一個角度解決：它不「事後 attach」，而是**在進程出生的那一刻就已經在裡面了**。因為它把自己種進 **Zygote**——所有 App 進程的母體。每個 App 進程都是從 Zygote fork 出來的，Zygote 被 hook 了，fork 出來的每個孩子天生就帶著 hook。這是「持久化 + 極早期生效」的根本來源。

## 先建立直覺：Zygote 是所有 App 的母體

要懂 Xposed，先懂 Android 怎麼啟動一個 App。**沒有一個 App 是「從零啟動」的**，全都是從一個叫 Zygote 的進程 **fork** 出來的：

```
        開機
         │
    init 啟動 zygote 進程
         │  (載入好整個 ART runtime、預載常用系統類與資源)
         ▼
    ┌─────────────────────────────────────┐
    │  Zygote 進程 (已載入 framework)      │  ← 一切 App 的「母體」
    │  在這裡等著，收到「啟動某 App」請求  │
    └─────────────────────────────────────┘
         │ fork()               │ fork()               │ fork()
         ▼                      ▼                      ▼
   com.demo.app          com.android.chrome      com.foo.bar
   (繼承母體的一切)       (繼承母體的一切)         (繼承母體的一切)
```

**為什麼這樣設計？** fork 是 copy-on-write 的——Zygote 預先載入好整個 ART runtime 和一堆共用系統類，fork 出來的孩子直接共享這些記憶體頁（沒被改寫前不複製），所以 App 啟動又快又省記憶體。這是 Android 效能的關鍵設計。

**對逆向的意義：** 如果你能在 Zygote **fork 之前**改它，那麼**之後 fork 出來的每一個 App 都會繼承你的修改**。Xposed 幹的正是這件事：

```
    ┌─────────────────────────────────────┐
    │  Zygote 進程                        │
    │  ★ Xposed 在這裡插入自己 ★          │  ← hook 進 Zygote 初始化流程
    │  (載入 XposedBridge、註冊模組)      │
    └─────────────────────────────────────┘
         │ fork()  每個孩子都帶著 XposedBridge + 你的模組
         ▼
   com.demo.app  ← 進程一出生，你的 hook 就已經裝好了
```

「進程一出生 hook 就在」正是 Frida「事後 attach」做不到的——這句話是整章的核心。

## 底層機制：Xposed 怎麼 hook 進 Zygote，又怎麼 hook 方法

分兩層看：**第一層是「怎麼進 Zygote」**，**第二層是「進去後怎麼替換一個 Java 方法」**。

### 第一層：進 Zygote

經典 Xposed（Rovo89 的原版，到 Android 8.1）的做法是**替換 `/system/bin/app_process`**（Zygote 的執行檔），在 Zygote 啟動時提前載入 `XposedBridge.jar`。這需要改 `/system` 分區——很硬，且 Android 9+ 的驗證開機（AVB/dm-verity）會擋。

現代做法（**LSPosed**）不改 `/system`，而是走 **Magisk + Zygisk**：

```
經典 Xposed：        改 /system/bin/app_process   ← 動系統分區，會被 AVB 擋
                     (Android ≤ 8.1)

LSPosed (現代)：     Magisk (systemless root)
                       └─ Zygisk (Magisk 內建，注入 Zygote 的框架)
                            └─ LSPosed 以 Zygisk 模組身分注入 Zygote
                                 └─ 在 fork 出的每個目標進程載入你的 Xposed 模組
```

- **Magisk**：systemless root——不改 `/system`，用 overlay/掛載魔法達到 root，避開 AVB。
- **Zygisk**：Magisk 內建的機制，官方支援「在 Zygote 注入程式碼」，正是 LSPosed 需要的注入點。
- **Riru vs Zygisk**：早期 LSPosed 走 **Riru**（另一個獨立的 Zygote 注入框架，靠替換 `libmemtrack.so` 之類的系統庫進 Zygote）；新版走 Magisk 內建的 **Zygisk**，少一層依賴。兩者目的相同：都是「合法地把程式碼注入 Zygote」。

關鍵是：**LSPosed 保留了經典 Xposed 的 API**（`XposedBridge`、`IXposedHookLoadPackage`），所以你按老 Xposed 教學寫的模組，在 LSPosed 上直接能跑。API 沒變，底層注入方式現代化了。

### 第二層：進去後怎麼替換一個方法

Xposed 的 `findAndHookMethod` 底層做的是**方法替換**。ART 裡每個 Java 方法對應一個 `ArtMethod` 結構，裡面有指向該方法機器碼（或解譯入口）的指標。Xposed 把目標方法的 `ArtMethod` 改成指向一個**跳板（hook stub）**：

```
   原本：  someMethod() ──▶ ArtMethod.entry_point ──▶ 原始機器碼

   hook 後：someMethod() ──▶ ArtMethod.entry_point ──▶ Xposed 跳板
                                                        │
                                    ┌───────────────────┤
                                    ▼                   ▼
                            beforeHookedMethod()   （決定要不要）呼叫原方法
                                    │                   │
                                    ▼                   ▼
                             afterHookedMethod()   拿到/改返回值
```

- `beforeHookedMethod`：原方法執行**前**，你能看/改參數、甚至直接 `setResult` 短路掉原方法。
- `afterHookedMethod`：原方法執行**後**，你能看/改返回值。
- 這跟 Frida 的 `Interceptor` 的 `onEnter`/`onLeave`（native）或 `.implementation`（Java）是**同一種思想**（在方法進出口插自己的碼），只是 Xposed 這套是純 Java 層 API、且在編譯期就綁定，執行期由 XposedBridge 在 Zygote 注入時裝好。

> **這裡有個 ART 版本相依的坑**：`ArtMethod` 的記憶體佈局每個 Android 版本都可能變，所以 Xposed/LSPosed 要針對不同 Android 版本適配。這也是為什麼你得用**對應你 AVD Android 版本**的 LSPosed 版本——版本不對，hook 裝不上或 crash。

## 範例一：寫一個最小 Xposed 模組

我們寫一個模組，hook 某 App 的 `checkVip()` 讓它永遠回 `true`（「持久化解鎖 VIP」的最小示範，目標是自己寫的 demo App）。

**Step 1：`build.gradle` 把 Xposed API 設為 `compileOnly`**（只編譯期需要，執行期由 LSPosed 提供，不能打包進去）：

```gradle
repositories { maven { url 'https://api.xposed.info/' } }
dependencies {
    // compileOnly：編譯用得到，但「不」打進 APK，執行期用系統裡 LSPosed 提供的
    compileOnly 'de.robv.android.xposed:api:82'
}
```

`compileOnly` 是**必須**的：如果寫成 `implementation`，XposedBridge 會被打包進你的模組 APK，跟 LSPosed 執行期提供的那份衝突，模組載入失敗。這是新手第一個坑。

**Step 2：`AndroidManifest.xml` 宣告這是 Xposed 模組**：

```xml
<application ...>
    <!-- LSPosed 靠這幾個 meta-data 認出「這是個 Xposed 模組」 -->
    <meta-data android:name="xposedmodule" android:value="true" />
    <meta-data android:name="xposeddescription" android:value="Demo: force VIP" />
    <meta-data android:name="xposedminversion" android:value="82" />
</application>
```

`xposedmodule=true` 是 LSPosed 掃描已安裝 App、判斷「哪些是 Xposed 模組」的依據。少了它，LSPosed 管理介面裡根本看不到你的模組。

**Step 3：宣告入口類**——在 `src/main/assets/xposed_init` 檔裡寫一行你的入口類全名：

```
com.demo.xposedmodule.MainHook
```

這個 `assets/xposed_init` 純文字檔是 Xposed 的「入口宣告」慣例（類比 Java 的 `Main-Class`）。LSPosed 讀這檔知道要載入哪個類。

**Step 4：寫 hook 邏輯**：

```java
package com.demo.xposedmodule;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage.LoadPackageParam;

public class MainHook implements IXposedHookLoadPackage {

    @Override
    public void handleLoadPackage(LoadPackageParam lpparam) throws Throwable {
        // handleLoadPackage 對「每一個」被載入的 App 進程都會呼叫一次。
        // 先用 package name 過濾：只對目標 App 動手，其他 App 直接 return。
        if (!lpparam.packageName.equals("com.demo.targetapp")) {
            return;
        }

        XposedHelpers.findAndHookMethod(
            "com.demo.targetapp.VipManager",  // 目標類全名
            lpparam.classLoader,              // 用「目標 App 的」classLoader 才找得到它的類
            "checkVip",                       // 方法名
            // 之後接參數型別（這裡 checkVip 無參數，所以直接接 callback）
            new XC_MethodHook() {
                @Override
                protected void afterHookedMethod(MethodHookParam param) {
                    // 原方法跑完後，把返回值強制改成 true
                    param.setResult(Boolean.TRUE);
                }
            }
        );
    }
}
```

逐點解釋：

- **`IXposedHookLoadPackage.handleLoadPackage`** 是 Xposed 模組最常用的入口。它對**每個載入的 App 進程**都被呼叫一次（因為 Xposed 在 Zygote，每個 fork 出的孩子都會觸發），所以**第一件事永遠是用 `lpparam.packageName` 過濾**——不然你會對整個系統每個 App 都動手，天下大亂。
- **`lpparam.classLoader` 很關鍵**：目標 App 的類要用**它自己的 classLoader** 才載入得到。用錯 classLoader（例如系統的）會 `ClassNotFoundException`。這是跟 Frida `Java.use` 不同的地方——Frida 幫你處理 classLoader，Xposed 要你自己給對。
- **`param.setResult(Boolean.TRUE)`**：在 `afterHookedMethod` 直接覆蓋返回值。若想連原方法都不執行，改用 `beforeHookedMethod` 裡 `setResult`——一旦在 before 裡 setResult，原方法就被短路不跑了。
- **有參數的方法**：`findAndHookMethod("...", cl, "login", String.class, String.class, new XC_MethodHook(){...})`——方法名後、callback 前，把每個參數型別依序列出（用 `String.class` 或字串 `"java.lang.String"`）。overload 就是靠這串型別區分的。

**Step 5：安裝、在 LSPosed 勾選、重啟目標 App**：

```
1. adb install module.apk
2. 開 LSPosed manager → 模組 → 勾選你的模組 → 勾選作用域 com.demo.targetapp
3. force stop 目標 App，重開 → checkVip() 現在恆回 true
```

> **未實測，理論預期行為**。上述模組原始碼是標準 Xposed API（LSPosed 完全相容）寫法，可在你的環境編譯。在 AVD 驗證：裝 Magisk → 裝 LSPosed（Zygisk 模式）→ 裝這個模組並在 manager 勾選作用域 → 重啟目標 App → 觀察 VIP 功能是否恆開。**注意 LSPosed 改了作用域要 force stop App 才生效**（因為要 App 重新從 Zygote fork 才會帶上新 hook），這是最常見的「改了沒反應」原因。

## 範例二：hook 系統框架層（所有 App 生效）

Xposed 的另一個殺手級用途：**hook 系統框架的類**，一次影響所有 App。例如 hook `PackageManager`，讓所有 App 都「查不到」某個已安裝的 App（隱藏 root 管理器，繞 root 檢測的思路）：

```java
// 不過濾 packageName，或針對 "android" (system_server) 動手
if (lpparam.packageName.equals("android")) {   // system_server 進程
    XposedHelpers.findAndHookMethod(
        "android.app.ApplicationPackageManager",
        lpparam.classLoader,
        "getInstalledApplications", int.class,
        new XC_MethodHook() {
            @Override
            protected void afterHookedMethod(MethodHookParam param) {
                // 從返回的 App 清單裡濾掉 Magisk manager 等
                // （示意：實作要遍歷 param.getResult() 的 List 移除目標）
            }
        });
}
```

這是 Frida 難做到的：Frida 要 hook `system_server` 很麻煩（那是系統核心進程），而 Xposed 天生就在每個進程裡（包含 `system_server`），只要不過濾 packageName 或針對 `"android"` 就能改系統行為。**「一改改全系統」是 Xposed 相對 Frida 的獨特能力**。

> **未實測，理論預期行為**。這類框架層 hook 威力大也危險——改壞 `system_server` 會導致系統反覆重啟（bootloop）。在 AVD 上做（壞了刪快照重建），別在真機上實驗框架 hook。這也是為什麼 Ch 0 建議存乾淨快照。

## 對比與取捨：Xposed vs Frida

| 面向 | Xposed / LSPosed | Frida |
|---|---|---|
| **生效時機** | 進程一出生就在（Zygote 注入） | 事後 attach（或 `-f` spawn 後注入） |
| **持久化** | ✅ 開機/重啟後仍在 | ❌ 關掉就沒，要重新 attach |
| **極早期邏輯** | ✅ 能 hook `Application.onCreate` 之前 | ⚠️ spawn 模式勉強，attach 常已太晚 |
| **改整個系統** | ✅ hook `system_server` 一改全系統 | ⚠️ 很難，Frida 不擅長 |
| **開發迭代速度** | ❌ 慢：改碼→編 APK→裝→重啟 App | ✅ 快：改 `.js` 存檔即時重載 |
| **hook native `.so`** | ⚠️ 弱（主打 Java 層） | ✅ 強（`Interceptor` 直接 hook native） |
| **臨場探索/印參數** | ❌ 笨重 | ✅ REPL 即時，探索神器 |
| **隱蔽性** | 較好（無明顯 server 進程/port） | 較差（frida-server、port 特徵易被檢測） |
| **前置需求** | Magisk + LSPosed（安裝門檻高） | 只要 frida-server（門檻低） |

**心法**：**探索用 Frida、固化用 Xposed**。你先用 Frida 快速迭代、找到「hook 哪個方法、怎麼改」的答案（Frida 改 `.js` 即時重載，比 Xposed 每次重編快十倍）；等邏輯定案、需要**持久化或極早期生效或改全系統**時，再把它寫成 Xposed 模組固化下來。兩者不是競爭，是接力。

## 踩雷集錦

1. **Xposed API 用 `implementation` 打包進 APK**：必須用 `compileOnly`。打包進去會跟 LSPosed 執行期提供的 XposedBridge 衝突，模組直接載入失敗。這是寫第一個模組最常見的死法。
2. **改了作用域/裝了模組卻沒反應**：LSPosed 改設定後**必須 force stop 目標 App**（讓它重新從 Zygote fork 才會帶上 hook）。很多人以為裝了就生效，其實 App 還是舊進程。系統框架層的改動甚至要重啟裝置。
3. **用錯 classLoader 導致 `ClassNotFoundException`**：找目標 App 的類，必須用 `lpparam.classLoader`，不是系統 classLoader。加固 App 的類可能在**動態載入的 classLoader** 裡，連 `lpparam.classLoader` 都找不到——那要先 hook classLoader 的 `loadClass` 等它出現（進階題）。
4. **`xposedmodule=true` 忘了寫，manager 裡看不到模組**：LSPosed 靠 manifest 的 `meta-data` 認模組。少了 `xposedmodule` meta-data，你的 App 就只是個普通 App，不會出現在模組列表。
5. **LSPosed 版本跟 Android 版本不匹配**：`ArtMethod` 佈局隨 Android 版本變，LSPosed 要對應版本才 hook 得上。裝了不對版的 LSPosed，症狀是模組勾了但完全無效、或 App 一開就 crash。用對應 API 33 的 LSPosed 版本。

## 進階：再往深一層

- **`XposedBridge.hookAllMethods` / `hookAllConstructors`**：當一個方法有很多 overload、你懶得一個個列參數型別時，`hookAllMethods(clazz, "methodName", callback)` 一次 hook 所有同名 overload。構造子同理用 `hookAllConstructors`。混淆過的 App 參數型別難確定時特別好用。
- **`XposedHelpers` 反射工具箱**：`callMethod`/`getObjectField`/`setObjectField`/`newInstance`——這些是「在 hook 裡呼叫目標 App 私有方法、讀私有欄位」的瑞士刀，等於幫你把 Java 反射的樣板碼包好。想在 hook 裡拿到某個 private 欄位的值，靠這個。
- **LSPosed 的隱藏能力**：它能對模組設定**作用域**（只對勾選的 App 生效，降低影響面與被檢測風險），還內建對抗部分 Xposed 檢測（App 會查 `de.robv.android.xposed.XposedBridge` 是否存在來偵測 Xposed）。Ch 30 反調試會講 App 怎麼反過來檢測 Xposed、以及 LSPosed 怎麼藏。
- **和 ArtMethod-level 脫殼的連結**：Xposed 的方法替換底層在動 `ArtMethod`，而 Part 6（Ch 36、練習 E mini-FART）的「主動調用脫殼」也是在 `ArtMethod` level 操作。懂了 Xposed 怎麼替換 `ArtMethod` 的 entry point，你就懂了那類脫殼器的一半原理——它們是同一塊 ART 知識的不同應用。

## 動手練習

1. 在 AVD 裝好 Magisk + LSPosed（Zygisk 模式），確認 manager 能開、Zygisk 顯示 enabled——先把持久化 hook 的地基搭好。
2. 照範例一寫一個最小模組，hook 你自己寫的 demo App（或任一你有源碼的 App）的某個回傳 boolean 的方法，把它固定成 `true`。刻意先用 `implementation` 打包一次看它怎麼壞，再改回 `compileOnly`——親眼看過這個坑。
3. 對同一個目標，分別用 **Frida**（改 `.js` 即時重載）和 **Xposed**（改碼重編重啟）達成同一個 hook，計時兩者的迭代速度差——親身體會「探索用 Frida、固化用 Xposed」。
4. 試 hook 一個在 `Application.onCreate` 極早期就跑的邏輯，比較 Frida attach（常抓不到）和 Xposed（進程一出生就在，抓得到）的差別。

## 本章重點整理

- Xposed 靠**注入 Zygote**（所有 App 的 fork 母體）達成「進程一出生 hook 就在」的**持久化 + 極早期生效**——這是它相對 Frida「事後 attach」的根本優勢。
- 現代實作 **LSPosed** 走 **Magisk + Zygisk**（免改 `/system`、避開 AVB），但**保留經典 Xposed API**（`IXposedHookLoadPackage`/`findAndHookMethod`），老教學直接能用。
- 寫模組四要素：`compileOnly` 依賴、manifest `xposedmodule` meta-data、`assets/xposed_init` 入口宣告、`handleLoadPackage` 裡**先過濾 packageName + 用 `lpparam.classLoader`**。
- Xposed 能 hook `system_server` **一改改全系統**，這是 Frida 難做的；但 Frida **迭代快、探索強、native hook 強**。
- 實務分工：**探索用 Frida、固化用 Xposed**——先 Frida 找答案，再 Xposed 持久化。

## 自我檢核

- [ ] 能解釋為什麼 hook 進 Zygote 就能讓「之後每個 App 進程」都帶著 hook
- [ ] 說得出 LSPosed 相對經典 Xposed 的關鍵差異（Magisk/Zygisk、免改 `/system`），以及為什麼 API 沒變
- [ ] 知道寫模組為什麼 Xposed API 一定要 `compileOnly`、`lpparam.classLoader` 為什麼不能用錯
- [ ] 能講出「改了 LSPosed 設定沒反應」的最常見原因（沒 force stop App）
- [ ] 不看表格，能說出 Frida 與 Xposed 各自的三個獨特強項，並說明「探索用 Frida、固化用 Xposed」

## 延伸閱讀

- **[LSPosed 官方 repo](https://github.com/LSPosed/LSPosed)** — GitHub
  - **讀哪裡**：README 的架構說明、Zygisk 模式安裝步驟、與 Riru 的關係
  - **和本章的關聯**：本章「怎麼進 Zygote」的權威現代依據；安裝 LSPosed 照它的文件走
- **[Xposed API 文件（rovo89）](https://api.xposed.info/reference/packages.html)** — Xposed 官方 API doc
  - **讀哪裡**：`IXposedHookLoadPackage`、`XposedHelpers.findAndHookMethod`、`XC_MethodHook`
  - **為什麼值得讀**：LSPosed 完全相容這套 API，這是你寫任何模組的 API 權威；`XposedHelpers` 那頁尤其常翻
- **[LSPosed 模組範例 / 開發者指南](https://github.com/LSPosed/LSPosedModuleSample)** — GitHub
  - **讀哪裡**：整個範例專案的 gradle 設定、manifest、入口宣告
  - **前提知識**：讀過本章範例一，這個 repo 給你一個能直接編譯跑的完整骨架
- **[HackTricks — Xposed](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：Frida/Xposed hooking 那節與 root detection 段
  - **和本章的關聯**：把 Xposed 放進滲透實戰脈絡，並連到「用 Xposed 繞 root/Xposed 檢測」的實作
- **[Magisk 文件 — Zygisk](https://topjohnwu.github.io/Magisk/)** — Magisk 官方
  - **讀哪裡**：Zygisk 一節（Zygote 注入機制）
  - **為什麼值得讀**：LSPosed 的注入地基就是 Zygisk，懂 Zygisk 才懂 LSPosed 為什麼免改系統分區

下一章我們把重心從「改 App 行為」轉到「看 App 跟伺服器說什麼」。想抓 HTTPS 流量，第一道牆就是 **SSL Pinning**——App 只信任它內建的憑證，你的 mitmproxy 憑證它一律不認。下一章拆穿 pinning 的原理，並用 objection/Frida 把它繞掉。

→ [Ch 17 SSL Pinning 與抓包](./17-ssl-pinning-bypass.md)
