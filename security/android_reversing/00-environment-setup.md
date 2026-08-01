# Ch 0 — 環境搭建：AVD、adb、frida-server 與逆向工作台

> **目標**：把一台**可 root 的 AVD**、一套靜態工具鏈（apktool/jadx）、一套動態工具鏈（Frida）從零裝起來，並跑通第一個 end-to-end 冒煙測試：把一個 App 的 APK 拉下來、反編譯、attach Frida 印出一行 log。這章之後，後面每一章你都有地方動手。

> **環境**：本章以 **Android Studio Hedgehog+ 內建 AVD（Android 13 / API 33，x86_64，Google APIs image）**、`adb` (platform-tools 34+)、`Frida 16.x`、`apktool 2.9+`、`jadx 1.4+` 為準。host 是 Windows 11，但所有指令在 macOS / Linux 上等價（差別只在路徑分隔符與執行檔副檔名）。

## 為什麼逆向要先搞環境？

逆向不是「拿一個工具點一點」，是**靜態看 + 動態驗**兩條腿走路，而動態這條腿需要一台你能完全控制的裝置——能 root、能塞進 frida-server、能隨便改隨便崩、崩了三秒重建。真機也行，但真機有三個麻煩：要解 bootloader（有些廠商鎖死）、root 有磚機風險、環境髒（一堆廠商魔改）。

AVD（Android Virtual Device，Google 官方模擬器）把這些問題全繞開：它預設就能 `adb root`（只要你選對 image），壞了刪掉重建，還能開快照秒回滾。代價是它跑在 x86_64 上、native 庫是 x86_64 而不是手機的 ARM64——這個差異很重要，本章最後會專門講。

## 先建立直覺：逆向工作台長什麼樣

```
   你的電腦 (host)                          AVD (guest, Android 13)
 ┌────────────────────────┐              ┌──────────────────────────┐
 │  靜態分析               │              │  target App (被分析對象)  │
 │   apktool ── smali      │              │     │                    │
 │   jadx    ── Java       │   adb (USB/  │     ▼                    │
 │                         │◀── TCP over ─┤  ART runtime             │
 │  動態分析               │    localhost)│     │                    │
 │   frida (client) ───────┼──── 注入 ───▶│  frida-server (root 跑)  │
 │   objection             │              │     │                    │
 │   mitmproxy (抓包)      │◀─── 流量 ────┤  網路 (走 host proxy)    │
 └────────────────────────┘              └──────────────────────────┘
```

三條線你要記住：
- **adb**：你跟 AVD 之間所有控制指令的通道（裝 App、拉檔、開 shell、port forward）。
- **frida-server 在 guest 裡以 root 跑**，你電腦上的 frida client 透過 adb 轉發的 port 跟它對話，把 JavaScript 注入 target 進程。
- **抓包**靠把 AVD 的流量導到你電腦上的 mitmproxy。

搞懂這張圖，後面 40 章都是在這三條線上加細節。

## Step 1：裝 Android SDK 與建立可 root 的 AVD

裝 [Android Studio](https://developer.android.com/studio)（最簡單，SDK + emulator + AVD Manager 一次到位）。裝完後**把 SDK 工具加進 PATH**，你會一直用到 `adb` 與 `emulator`：

```powershell
# Windows PowerShell —— 路徑依你的安裝位置調整
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
$env:Path += ";$env:ANDROID_HOME\platform-tools;$env:ANDROID_HOME\emulator"
adb version
```

代表性輸出（你的版本號可能不同）：

```
Android Debug Bridge version 1.0.41
Version 34.0.5-10900879
```

接著建 AVD。**這一步有一個決定成敗的選擇**：system image 要選 **「Google APIs」**，**不要**選「Google Play」。

| Image 類型 | `adb root` | 能不能 push frida-server 到系統 | 適合 |
|---|---|---|---|
| **Google APIs**（我們要的） | ✅ 可以 | ✅ 可以 | 逆向、動態分析 |
| Google Play | ❌ 被鎖（production build） | ❌ 不行 | 一般 App 開發測試 |
| AOSP（無 GApps） | ✅ 可以 | ✅ 可以 | 純淨環境，但沒 Google 服務 |

> **這是最常見的第一個坑**：很多人隨手建了 Google Play image，然後 `adb root` 回 `adbd cannot run as root in production builds`，卡在這裡以為 AVD 不能 root。不是不能——是你選錯 image 了。刪掉重建，選 Google APIs。

用命令列建（也可以用 Android Studio 的 AVD Manager GUI 點）：

```powershell
# 列出可用 image，找 google_apis（不是 google_apis_playstore）
sdkmanager --list | Select-String "system-images;android-33"
sdkmanager "system-images;android-33;google_apis;x86_64"

# 建立一台叫 re33 的 AVD
avdmanager create avd -n re33 -k "system-images;android-33;google_apis;x86_64" -d pixel_5

# 開機（-writable-system 讓 /system 可寫，之後要改系統 CA 憑證會用到）
emulator -avd re33 -writable-system -no-snapshot-load
```

開起來後，另開一個終端驗證 root：

```powershell
adb root
adb shell whoami
```

代表性輸出：

```
restarting adbd as root
root
```

看到 `root` 就成功了。如果看到 `adbd cannot run as root in production builds`，回去確認 image 是 `google_apis` 不是 `google_apis_playstore`。

> **本段部分未在本文件環境實測**：本 repo 的建構沙箱沒有 Android SDK/emulator，上面的版本輸出是依官方工具的實際行為寫的代表值。你在自己機器跑時，版本號會不同、但流程與關鍵字（`restarting adbd as root` / `root`）一致。後面章節凡是我在 AVD 上真的跑過的，會明講「以下為實際輸出」。

## Step 2：adb 你必須先會的幾招

adb 是逆向的瑞士刀，這幾個指令後面天天用，先練熟：

```bash
adb devices                      # 列出連上的裝置
adb shell                        # 開一個 guest 內的 shell
adb push local.txt /data/local/tmp/   # host → guest 傳檔
adb pull /data/local/tmp/x.dex ./      # guest → host 拉檔
adb install app.apk              # 裝 APK
adb shell pm list packages | grep foo  # 找已裝 App 的 package name
adb shell pm path com.example.foo      # 找某 App 的 APK 檔在裝置上哪裡
adb logcat                       # 看系統/App log（動態分析神器）
adb forward tcp:27042 tcp:27042  # host 的 port 轉發到 guest（Frida 要用）
```

其中 **`pm path` + `pull`** 是你把一個已安裝 App 的 APK 撈出來分析的標準動作：

```bash
adb shell pm path com.android.chrome
# package:/data/app/~~xxxx==/com.android.chrome-yyyy==/base.apk
adb pull /data/app/~~xxxx==/com.android.chrome-yyyy==/base.apk ./chrome.apk
```

> 現代 App 常是 **split APK**（`base.apk` + `split_config.*.apk`），`pm path` 會列出多個路徑。分析主邏輯通常抓 `base.apk` 就夠；native 庫可能在 `split_config.arm64_v8a.apk` 裡，Part 4 會再處理。

## Step 3：靜態工具鏈 —— apktool 與 jadx

兩個工具分工不同，別搞混（Ch 6/7 會深入，這裡先裝起來能跑）：

| 工具 | 輸入 → 輸出 | 用途 | 可回編譯？ |
|---|---|---|---|
| **apktool** | APK → **smali** + 資源 | 改邏輯、重打包 | ✅ 可以 |
| **jadx** | APK/DEX → **Java**（近似） | 讀懂邏輯 | ❌ 只讀 |

裝法（都需要 JDK 11+，先 `java -version` 確認有 Java）：

```bash
# apktool: 下載 wrapper 腳本 + jar，放進 PATH
#   https://apktool.org/docs/install
# jadx: 下載 release zip 解壓，用 bin/jadx 或 bin/jadx-gui
#   https://github.com/skylot/jadx/releases

apktool --version      # 2.9.3
jadx --version         # 1.4.7
```

冒煙測試——把一個 APK 拆開看看：

```bash
# apktool: 反編譯出 smali 與資源
apktool d chrome.apk -o chrome_out
#   會產生 chrome_out/smali*/  chrome_out/res/  chrome_out/AndroidManifest.xml

# jadx: 反編譯成 Java（CLI 版；也可以開 jadx-gui 互動看）
jadx chrome.apk -d chrome_java
#   會產生 chrome_java/sources/  （Java 檔）與 chrome_java/resources/
```

代表性目錄結構（apktool 輸出）：

```
chrome_out/
├── AndroidManifest.xml      # 已還原成可讀 XML（原始是 binary XML）
├── apktool.yml              # apktool 的 metadata（版本、原始檔資訊）
├── res/                     # 資源（layout、string、圖）
├── smali/                   # 主 DEX 的 smali
├── smali_classes2/          # 第二個 DEX（App 大了會分多個）
└── lib/                     # native .so（如果有）
    └── x86_64/
```

## Step 4：動態工具鏈 —— Frida

Frida 分兩半：**你電腦上的 client**（pip 裝）與 **guest 裡的 server**（下載對應架構的 binary 塞進去跑）。**兩邊版本號必須一致**，這是第二個大坑。

```bash
# host 端：裝 client（frida + frida-tools）
pip install frida-tools
frida --version        # 16.5.9  ← 記住這個版本號
```

下載**同版本、對應 AVD 架構**的 frida-server。AVD 是 x86_64，所以抓 `frida-server-16.5.9-android-x86_64`：

```
https://github.com/frida/frida/releases  → 找對應 tag，下載
   frida-server-16.5.9-android-x86_64.xz
```

推進 AVD 並以 root 跑起來：

```bash
# 解壓後推進去
adb push frida-server-16.5.9-android-x86_64 /data/local/tmp/frida-server
adb shell chmod 755 /data/local/tmp/frida-server

# 以 root 在背景跑 server
adb root
adb shell "/data/local/tmp/frida-server &"

# host 端驗證：列出 guest 上正在跑的進程
frida-ps -U | head
```

`frida-ps -U`（`-U` = USB/adb 連線）代表性輸出：

```
 PID  Name
----  ------------------------------
1523  adbd
2891  com.android.systemui
3102  com.google.android.gms
 ...
```

能列出進程，就代表 client↔server 通了。

> **版本不一致的症狀**：`frida-ps -U` 回 `Failed to enumerate processes: unable to communicate with remote frida-server; please ensure that major versions match`。解法就一句：讓 client 跟 server 版本號**完全相同**。Frida 更新快、破壞性改動多，這個坑你會踩不只一次。

## Step 5：End-to-End 冒煙測試

把三條線串起來跑一次，確認工作台真的活著。我們 hook 系統設定 App，攔 `System.currentTimeMillis()` 印出來——這是「Frida 能改 Java 層行為」的最小證明：

```javascript
// smoke.js —— 把 currentTimeMillis 攔下來印 log 並照樣放行
Java.perform(function () {
    var System = Java.use("java.lang.System");
    System.currentTimeMillis.implementation = function () {
        var real = this.currentTimeMillis();   // 呼叫原本的實作
        console.log("[hook] currentTimeMillis() -> " + real);
        return real;                            // 原值回傳，不改行為
    };
    console.log("[*] hook installed");
});
```

跑起來（spawn 一個 App 並注入）：

```bash
frida -U -f com.android.settings -l smoke.js
```

代表性輸出（`-f` 會 spawn 並在 hook 裝好後自動 resume）：

```
     ____
    / _  |   Frida 16.5.9 - A world-class dynamic instrumentation toolkit
   ...
[*] hook installed
[hook] currentTimeMillis() -> 1717430400123
[hook] currentTimeMillis() -> 1717430400456
```

看到 `[hook] currentTimeMillis() -> ...` 一直刷，代表你已經在**別人的進程裡執行你的程式碼**了。這就是整門課動態分析的地基。

> **上面的 smoke.js 我在本 repo 沙箱無法執行**（沒有 AVD/Frida）。腳本語法是 Frida 16.x 的標準 `Java.use` / `.implementation` 寫法，Ch 13 會逐行拆解它為什麼這樣寫、`this.currentTimeMillis()` 為什麼能呼叫原實作。你在自己 AVD 上跑，看到 log 刷出來就對了。

## 對比與取捨：AVD vs 真機 vs 其他

| 方案 | root 難度 | native 架構 | 環境純淨度 | 適合 |
|---|---|---|---|---|
| **AVD (google_apis, x86_64)** | 免（內建 root） | **x86_64** ⚠️ | 乾淨 | 本課主力，App 層/Frida/脫殼 |
| **AVD (google_apis, arm64)** | 免 | ARM64 ✅ | 乾淨 | native ARM64 逆向（host 非 ARM 時慢） |
| 真機（解鎖 + Magisk root） | 高（有磚機風險） | ARM64 ✅ | 髒（廠商魔改） | 反模擬器檢測的 App、真實環境驗證 |
| Genymotion / 第三方模擬器 | 中 | 多為 x86 | 中 | 備選，但反模擬器檢測更容易被識破 |

## 踩雷集錦

1. **選了 Google Play image 卻想 root**：`adb root` 回 `cannot run as root in production builds`。Play image 是 production build，鎖死 root。要 **Google APIs** image，這是最多人卡的第一關。
2. **frida client 與 server 版本不一致**：`major versions match` 錯誤。兩邊版本號要**完全相同**，包含小版本。Frida 沒有向後相容保證。
3. **抓錯 frida-server 架構**：AVD 是 **x86_64**，很多教學預設你在真機所以叫你抓 `arm64`。抓錯架構 server 根本跑不起來（`exec format error`）。用 `adb shell getprop ro.product.cpu.abi` 確認架構再下載。
4. **以為 AVD 的 native 逆向 = ARM64**：**這是本課最重要的環境陷阱**。x86_64 AVD 裡的 `.so` 是 x86_64 編譯的，不是手機上的 ARM64。你在這台 AVD 逆 native 庫，逆到的是 x86_64 組語。Part 4（Ch 19–25）我們要練 ARM64，屆時**改用 arm64 的 AVD image**（`system-images;android-33;google_apis;arm64-v8a`）或真機——在 x86 host 上 arm64 AVD 走全 CPU 模擬（QEMU TCG），慢但能跑。Ch 20 開頭會再提醒切換。
5. **`emulator` 沒加 `-writable-system` 就想改系統檔**：Android 10+ 的 `/system` 預設唯讀，要裝系統級 CA 憑證（Ch 17 抓包）會失敗。開機時加 `-writable-system`，再 `adb remount`。

## 進階：再往深一層

- **快照秒回滾**：`emulator -avd re33 -snapshot clean -no-snapshot-save` 搭配手動存快照，可以在「乾淨已 root + 已放 frida-server」的狀態存一個 snapshot，之後每次分析壞了就秒回，不用重跑整套 Step 1–4。逆惡意樣本時這招是保命符。
- **frida-server 開機自動跑**：手動 `adb shell "... &"` 每次重開機都要重來。進階做法是寫成 Magisk 模組或 init 腳本讓它開機自啟，Ch 16 提 LSPosed 時會碰到類似的持久化思路。
- **無線 adb**：`adb tcpip 5555` + `adb connect <ip>:5555` 讓你不用 USB 線連真機，逆真機時方便。
- **objection**：`pip install objection`，它是架在 Frida 上的互動式工具（`objection -g com.foo explore`），內建一堆常用 hook（SSL pinning bypass、列 activity、dump keystore），Ch 17 會用到。先裝著。

## 動手練習

1. 建一台 `google_apis` x86_64 的 AVD，`adb root` 成功拿到 `root`。故意再建一台 `google_apis_playstore` 的，`adb root` 看它怎麼拒絕你——親眼看過這個錯誤，以後一秒認出。
2. 把 AVD 內建的計算機或設定 App 用 `pm path` 找出 APK 路徑、`adb pull` 出來，`apktool d` 與 `jadx` 各拆一次，比較兩者輸出目錄的差異。
3. 跑通 Step 5 的 smoke test，然後把 `return real;` 改成 `return 0;`，重跑，觀察有沒有 App 因為「時間永遠是 1970」而行為異常——這是你第一次用 hook **改變**而非只是**觀察**程式行為。

## 本章重點整理

- 逆向工作台 = **adb 控制線 + Frida 注入線 + 抓包線**，三條線串起 host 與 AVD。
- AVD 要選 **Google APIs image** 才能 root；Frida **client 與 server 版本必須一致、架構必須對**。
- x86_64 AVD 的 native 庫是 **x86_64 不是 ARM64**——Part 4 練 ARM64 時要換 arm64 image 或真機。
- 靜態 **apktool（smali，可回編譯）+ jadx（Java，只讀）** 分工；動態 **Frida** 讓你在別人進程裡跑程式碼。

## 自我檢核

- [ ] 能解釋為什麼 Google Play image 不能 `adb root`，而 Google APIs 可以
- [ ] 能說出 frida client 連不上 server 的兩個最常見原因，並各給解法
- [ ] 知道你這台 AVD 逆 native `.so` 會逆到什麼架構，以及要練 ARM64 該怎麼辦
- [ ] 不看筆記，能講出 apktool 與 jadx 的差別，以及各自什麼時候用
- [ ] 跑通了 smoke test，親眼看到自己的 JavaScript 在 target 進程裡執行

## 延伸閱讀

### 官方文件

- **[Android Emulator 命令列指南](https://developer.android.com/studio/run/emulator-commandline)**
  - **讀哪裡**：`emulator` 命令列旗標那節；`-writable-system`、`-snapshot`、`-no-snapshot-load` 這些我們用到的旗標都在
  - **和本章的關聯**：Step 1 建 AVD、進階的快照回滾，都是這頁的實際應用
- **[adb 官方文件](https://developer.android.com/tools/adb)**
  - **讀哪裡**：整頁都值得掃一遍，重點看 `forward`/`push`/`pull`/`logcat`
  - **注意**：`pm`（package manager）子命令那節，`pm path` 撈 APK 就靠它

### Frida 官方文件

- **[Frida — Android 快速上手](https://frida.re/docs/android/)**
  - **這篇說什麼**：官方的 frida-server 部署流程，跟本章 Step 4 對應
  - **讀哪裡**：整頁；特別是 frida-server 版本與架構對應那段
  - **為什麼值得讀**：本課動態分析全建在 Frida 上，官方文件是唯一權威且更新最快的來源

### 社群 Cheat Sheet

- **[HackTricks — Android Pentesting](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **這篇說什麼**：AVD/真機/frida/objection 一站式設定與逆向指令
  - **讀哪裡**：開頭的環境設定與 Frida 安裝段
  - **前提知識**：會基本命令列即可；它假設你邊做邊查

下一章我們拉高視角：一個 App 從你按下「安裝」到畫面亮起來，中間 ART 做了什麼？攻擊者可以在這條路徑的哪些點切入？先有這張攻擊地圖，後面每個工具你才知道它在打哪一層。

→ [Ch 1 安卓逆向全貌：攻擊者視角與工作流](./01-android-re-overview.md)
