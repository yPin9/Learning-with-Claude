# Ch 33 — Dalvik 到 ART 的演進

> **目標**：搞懂 Android 的執行引擎從 **Dalvik（JIT 直譯）** 走到 **ART（AOT + JIT 混合）** 到底改了什麼、為什麼要改。你要能回答：`dex2oat` 在幹嘛、`.oat`/`.vdex`/`.art` 這三個檔各裝什麼、profile-guided compilation（依側寫編譯）怎麼運作、為什麼「純 AOT」曇花一現後又退回混合模式。這章是整個 Part 6 的地基——後面談 ART runtime 內部、脫殼、hook，全建在「App 的 DEX 在裝置上被編成什麼、執行時跑的到底是 bytecode 還是機器碼」這個認知上。

> **環境**：本章談的是 **Android 13 / API 33（ART）** 的執行模型。歷史對照涉及 Dalvik（Android 4.4 前）、KitKat 的 ART 實驗、Lollipop 的純 AOT、Nougat（Android 7）起的混合模式。ART 內部行為**隨版本變動大**，關鍵處會標明以哪個版本為準；`.oat`/`.vdex` 的內部格式尤其版本敏感，逆向時要在目標裝置上實測、對照對應版本的 AOSP `art/` 原始碼。本 repo 沙箱無 Android/ART，涉及裝置上檔案的段落會明確標「**未實測，理論預期行為**」。

## 為什麼需要這個？

因為你在 Part 2–5 一直假設「App 的邏輯 = `classes.dex` 裡的 Dalvik bytecode」，但這只對了一半。當這個 DEX 裝進一台 Android 裝置，系統會**在背景把它編譯成原生機器碼**存起來，執行時可能跑的是那份機器碼、而不是你在 jadx 裡看到的 bytecode。你如果不知道這件事，會在三個地方撞牆：

- **脫殼**：很多加固殼的還原時機、抽取殼把方法體填回的位置，都跟「這方法有沒有被 AOT 編譯」「entrypoint 指向直譯器還是機器碼」直接相關（Ch 36 深挖）。不懂編譯流程，你不知道該在哪個時間點 dump。
- **hook**：ART hook 的本質是改 `ArtMethod` 的 entrypoint（Ch 34、36），而 entrypoint 指向哪，取決於這個方法當前是直譯執行、JIT 編譯過、還是 AOT 編譯過。三種狀態改法不同。
- **反調試/完整性**：有些防護會檢查 `.oat` 是否被動過、或利用「首次執行走直譯、之後走 AOT」的時間差來偵測插樁。

一句話：**Dalvik→ART 不是背景知識，是你理解「App 在裝置上到底怎麼被執行」的鑰匙**。而執行方式決定了你的每一個逆向動作打在哪一層。

## 先建立直覺：一份 DEX 的三種命運

先給一個貫穿全章的心智模型。你手上一份 `classes.dex`，裝進 ART 系統後，它裡面的**每一個方法**在執行時，會落在三種狀態之一：

```
              一個 method 被呼叫時, 它現在是哪種狀態?

  ┌──────────────┐   dex2oat 事先編了   ┌──────────────────┐
  │  直譯執行     │ ──────────────────▶ │  AOT 機器碼       │
  │ (interpreter) │                     │ (在 .oat 檔裡)    │
  │  逐條解 dex    │ ◀──────────────────  │  entrypoint 指它  │
  │  bytecode     │   沒編 / 被去最佳化   └──────────────────┘
  └──────────────┘                              ▲
        │  這方法跑很多次, 變熱                    │ JIT 把熱方法
        │  (hot)                                  │ 即時編成機器碼
        ▼                                        │
  ┌──────────────────────────────────────────────┘
  │  JIT 機器碼 (在記憶體, 不落檔)
  └──────────────────────────────────────────
```

三種狀態你要刻進腦子：

1. **直譯（interpreter）**：ART 內建一個直譯器，逐條解 Dalvik bytecode 執行。慢，但不用預先編譯、隨時能跑。剛裝好、還沒被 dex2oat 碰過的 App，一開始就是這樣跑。
2. **AOT（Ahead-Of-Time，事先編譯）**：`dex2oat` 這個工具把 DEX **事先**編成原生機器碼，存進 `.oat` 檔。方法被呼叫時直接跳進機器碼，快。
3. **JIT（Just-In-Time，即時編譯）**：執行期發現某方法被跑很多次（變「熱」），ART 把它**即時**編成機器碼放記憶體，下次呼叫就走機器碼。

**同一個方法在不同時間點可能處於不同狀態**。這個「一份 DEX、每個方法三種命運、還會動態切換」的圖，是理解 ART 一切行為的起點。Dalvik→ART 的演進史，本質就是「這三種狀態怎麼組合」的取捨變遷史。

## 底層機制：Dalvik → ART 的三個時代

Android 執行引擎的演進不是一步到位，是三個階段來回擺盪。看懂這條擺盪線，你才懂今天 ART 為什麼長這樣。

```
 時代       Android 版本        執行策略                     痛點 / 動機
 ───────────────────────────────────────────────────────────────────────
 Dalvik    ~4.4 前            純直譯 + JIT                  執行慢、JIT 每次開機重編、耗電
 (JIT)                        (每次執行時即時編熱方法)       APK 裡是 .dex/.odex

 ART v1    5.0 (Lollipop)     純 AOT                        安裝時全編譯 → 裝機/更新極慢、
 (純 AOT)                     (安裝時 dex2oat 全部編成機器碼)  .oat 檔佔超大空間

 ART v2    7.0 (Nougat) 起     AOT + JIT + 側寫混合           兼顧: 首次直譯/JIT, 依側寫挑熱方法
 (混合)                       (profile-guided, 閒置時編熱點)   閒置充電時才編, 只編常用的
```

### 第一代：Dalvik + JIT——慢在哪

Dalvik 是最初的執行引擎（Ch 4 講的暫存器式 VM 就是它定義的 bytecode）。它的執行策略是**直譯為主、JIT 為輔**：App 跑起來先逐條直譯 Dalvik bytecode，Dalvik 的 JIT（Android 2.2 Froyo 引進）在執行期偵測熱路徑（trace-based，以熱「路徑」為單位而非整個方法），把熱的部分即時編成機器碼。

問題有三：

- **直譯本身慢**：逐條 fetch-decode-dispatch，CPU 慢的年代這是明顯瓶頸。
- **JIT 的成果不落檔**：每次開 App 都要重新偵測熱點、重新編譯，開機/開 App 的「暖機期」一直卡頓。
- **耗電**：執行期一直在編譯，CPU 一直忙，吃電。

Dalvik 在 APK 安裝時會做一次輕量的 `dexopt`，產生 `.odex`（optimized dex），做一些驗證與對齊，但**不是**完整 AOT——主體還是直譯 + 執行期 JIT。

### 第二代：ART 純 AOT——快了但代價巨大

Android 5.0（Lollipop）用 **ART（Android RunTime）** 全面取代 Dalvik，策略走向另一個極端：**純 AOT**。App 安裝的那一刻，`dex2oat` 就把整個 DEX 編成原生機器碼，存成 `.oat` 檔。之後每次執行直接跑機器碼，沒有直譯、沒有執行期 JIT 開銷。

好處立竿見影：App 啟動快、執行流暢、省電（不用邊跑邊編）。但純 AOT 的代價在 Lollipop 上暴露得很痛：

- **安裝/更新極慢**：每裝一個 App、每次系統更新（要重編所有 App），都要把全部方法 AOT 一遍。系統升級後那個「Optimizing app 1 of 200…」的漫長開機畫面，就是這代的產物。
- **空間爆炸**：`.oat` 機器碼比 DEX bytecode 大好幾倍，而且是**全部**方法都編——包括那些一輩子跑不到一次的方法。存儲被吃掉一大塊。

「花大力氣編了一堆從不執行的方法」這個浪費，直接催生了下一代。

### 第三代：混合模式——今天的 ART

Android 7.0（Nougat）起，ART 退回一個聰明的**混合模式（AOT + JIT + profile-guided）**，這也是 Android 13 至今的模型：

```
 App 剛裝好
   │  不做完整 AOT (裝機快)
   ▼
 首次執行 → 直譯 + JIT
   │  JIT 編熱方法, 同時記錄「哪些方法被跑過」到 profile 檔
   ▼
 裝置閒置 + 充電時, 系統跑背景 dex2oat
   │  只 AOT 編譯 profile 裡標記的熱方法 (profile-guided)
   ▼
 之後執行 → 熱方法走 AOT 機器碼, 冷方法仍直譯/按需 JIT
```

三個關鍵設計：

1. **裝機不全編**：安裝時只做驗證，不做完整 AOT，所以裝 App 快了。
2. **執行期記側寫（profile）**：App 跑的時候，ART 把「哪些方法真的被執行了、被執行多少次」記進一個 profile 檔（`/data/misc/profiles/` 底下）。
3. **閒置時 profile-guided 編譯**：裝置充電且閒置時，系統跑 `dex2oat`，但**只編 profile 標記為熱的方法**——常用的路徑編成 AOT，用不到的省下來。

這就是「既要快、又要裝機快、又要省空間省電」的折衷。你逆向時看到的 `.oat` 檔，裡面**未必包含 App 的所有方法**——只有被實際跑過、進了 profile 的那些。這對脫殼很關鍵（Ch 36）。

## dex2oat 與三個產物檔：oat / vdex / art

`dex2oat` 是 ART 的編譯器，把 DEX 編成三個伴生檔。搞清楚這三個檔各裝什麼，是逆向 ART 產物的基本功。

```
        classes.dex (輸入)
             │
             ▼
        ┌─────────┐
        │ dex2oat │ ── 依 profile 決定編哪些方法 (profile-guided)
        └─────────┘
          │    │    │
    ┌─────┘    │    └──────┐
    ▼          ▼           ▼
 .vdex       .oat        .art
 (驗證過      (AOT 機器碼  (啟動堆的
  的 DEX +    + 對應       image: 預先
  快速驗證     ArtMethod    建好的物件/
  資訊)        entrypoint)  類, 加速啟動)
```

以 Android 13 為準，三個檔的分工（版本敏感，內部格式細節要對照對應版本的 AOSP `art/runtime/oat/` 與 `art/dex2oat/`）：

| 檔 | 裝什麼 | 逆向意義 |
|---|---|---|
| **`.vdex`** | 驗證過的 DEX 本體 + 快速驗證資訊（quickening 等） | **這裡面有近乎完整的 DEX**！`vdex` 保留 DEX 是為了跳過重複驗證。逆向/脫殼常從 `.vdex` 直接抽 DEX，繞過記憶體 dump |
| **`.oat`** | AOT 編出的原生機器碼 + 每個編譯方法的 entrypoint 對照 | App 執行時真正跑的機器碼在這；它其實是一個特製的 **ELF** 檔（`.so` 殼包著 oat 資料） |
| **`.art`** | 啟動用的 image（預先實例化好的物件與類，記憶體映射就能用） | 加速啟動；boot image（`boot.art`）裝的是 framework 常用類 |

三個逆向重點：

1. **`.vdex` 是脫殼的一條捷徑**。因為 `vdex` 為了避免重複驗證而**保留了 DEX 本體**，很多情況下你直接從裝置上 pull `.vdex`、用工具（如 `vdexExtractor`）抽出 DEX，就拿到還原的 bytecode 了——不用玩記憶體 dump。前提是殼沒有在 runtime 才把真 DEX 填進來（那種要走 Ch 36 的主動調用）。
2. **`.oat` 是個 ELF**。你 `readelf` 它會發現它是合法 ELF，oat 資料藏在特定 section（`.rodata` 裡的 `oatdata`、`oatexec`）。`oatdump` 工具能把它拆開看每個方法編成什麼機器碼。
3. **這些檔放在哪**：App 的產物通常在 `/data/app/<pkg>-xxx/oat/<arch>/base.odex`（副檔名雖叫 `.odex` 但內容是 oat 格式）、`base.vdex`、`base.art`。boot image 在 `/apex/com.android.art/javalib/<arch>/` 或 `/system/framework/<arch>/`（版本而異）。

> **未實測，理論預期行為**：上述檔案路徑與 `.oat`=ELF 的結構，是 ART 在 Android 12/13 的實際行為，但**確切路徑隨版本、廠商、A/B 分區方案而變**。你在 AVD 上驗證：`adb shell find /data/app -name '*.vdex' 2>/dev/null` 找 vdex，`adb shell ls /data/misc/profiles/cur/0/<pkg>/` 找 profile。`.oat`/`.vdex` 的內部格式定義在 AOSP `art/runtime/oat/oat_file.h`、`art/runtime/vdex_file.h`——**以你目標裝置的 Android 版本對應的 tag 為準，欄位可能因版本不同**。

### compilation filter：dex2oat 可以編多少

`dex2oat` 不是只有「編」或「不編」，它有一個 **compilation filter（編譯篩選等級）** 參數決定編多深。逆向時用 `adb shell dumpsys package <pkg>` 或 `cmd package compile` 能看到/改變一個 App 的編譯狀態：

| filter | 意思 | 執行時行為 |
|---|---|---|
| `verify` | 只驗證，不編機器碼 | 全走直譯 + JIT |
| `speed-profile` | 依 profile 編熱方法（**混合模式預設**） | 熱方法 AOT，冷方法直譯/JIT |
| `speed` | 全編（接近純 AOT） | 幾乎全走機器碼 |
| `everything` | 連能編的都編 | 最激進 |

這個 filter 對逆向的用處：**你可以主動用 `adb shell cmd package compile -m speed -f <pkg>` 強制把某 App 全 AOT 編譯**，讓它的方法都有機器碼——某些脫殼/分析場景會利用這點。反過來，`-m verify` 讓它全走直譯，方便你在直譯器層 hook（Ch 36 會用到「entrypoint 指直譯器 vs 指機器碼」的差異）。

## 範例一：查一個 App 現在編到什麼程度

我們用 ART 自帶的工具鏈觀察一個 App 的編譯狀態。以下指令在 AVD（Android 13）上跑（**指令與輸出格式為代表性，未在本 repo 沙箱實測；ART 版本敏感**）：

```bash
# 看某 App 的 dexopt 狀態 (編譯 filter / 是否 profile-guided)
adb shell dumpsys package com.example.foo | grep -A5 "Dexopt state"
```

代表性輸出：

```
  Dexopt state:
    [com.example.foo]
      path: /data/app/~~ab12==/com.example.foo-xy34==/base.apk
        arm64: [status=speed-profile] [reason=bg-dexopt]
```

`status=speed-profile` 說明它走混合模式（依 profile 編熱方法），`reason=bg-dexopt` 說明這次編譯是背景（閒置充電時）觸發的。這印證了前面「閒置時 profile-guided 編譯」的機制——不是裝機時編的。

強制全編再看一次：

```bash
adb shell cmd package compile -m speed -f com.example.foo
adb shell dumpsys package com.example.foo | grep status
#   arm64: [status=speed] [reason=cmdline]   ← 變成 speed, 幾乎全 AOT
```

`reason=cmdline` 表示是你手動觸發的。**這一招在後面章節有用**：當你想確保某方法有 AOT 機器碼（方便觀察 entrypoint 指向機器碼的情形），就手動 `speed` 一次。

## 範例二：`.oat` 其實是 ELF——用 readelf 驗證

前面說 `.oat` 是個特製 ELF，我們驗證它（**代表性輸出，未實測；oat section 命名以 Android 12/13 為準，版本可能不同**）：

```bash
# 找到 oat/odex 檔並 pull 出來
adb shell find /data/app -name 'base.odex' 2>/dev/null
adb pull /data/app/~~ab12==/com.example.foo-xy34==/oat/arm64/base.odex ./base.odex

readelf -S base.odex | grep -i oat
```

代表性輸出：

```
  [ 5] .rodata           PROGBITS   ... 
       (內含 oatdata: DEX 副本 + oat header + 每個 class/method 的編譯資訊)
  [ 6] .text             PROGBITS   ...
       (內含 oatexec: AOT 編出的原生機器碼)
```

**逆向啟示**：oat 把「原始 DEX（在 oatdata）」和「編譯出的機器碼（在 oatexec）」放在同一個 ELF 裡。這是為什麼 ART 執行時能在「直譯 DEX」和「跑 AOT 機器碼」之間切換——兩份東西它手邊都有。用 AOSP 的 `oatdump --oat-file=base.odex` 能把每個方法的 DEX bytecode 與對應機器碼並排 dump 出來，是逆向 AOT 產物的利器。

### boot image：framework 為什麼「一開機就快」

除了 App 各自的產物，系統還有一份特殊的 AOT 產物——**boot image**（`boot.art` + `boot.oat`）。它把所有 App 都會用到的 framework 核心類（`java.lang.*`、`android.*` 那幾千個）**預先編譯 + 預先實例化**，開機時 Zygote（Ch 3、Ch 37）記憶體映射它就直接可用，不用每台裝置每次開機重編 framework。

```
 boot image (系統預裝/OTA 時產)
   ├ boot.art  ← 預先建好的 framework 物件/類 (映射即用)
   └ boot.oat  ← framework 類的 AOT 機器碼
        │  Zygote 開機時 mmap 進來
        ▼
   每個 App fork 自 Zygote → 免費繼承已就緒的 framework (Ch 3 的同源)
```

**對逆向的意義**：你 hook 一個 framework 類（`System`、`Activity`）時，它的機器碼在 boot image 裡、entrypoint 指向 boot.oat 的 AOT 碼——這是為什麼有些 framework 方法「已經是機器碼」，你改 bytecode 對它無效（Ch 34）。boot image 由系統簽名保護，OTA 後 `odsign`/`otapreopt` 會重建。分析系統層 hook（Xposed hook framework）時，要意識到你動的是 boot image 裡編好的東西。

## 範例三（失敗/邊界）：以為 pull 到 vdex 就一定能抽出真 DEX

一個新手常見的錯誤期待：「vdex 保留了 DEX，那我 pull vdex 就一定能還原任何 App 的邏輯」。**不一定**，這裡有兩個邊界：

1. **加固殼可能讓 vdex 裡的 DEX 是假的/殼的**。抽取型加固（Ch 28）在裝置上執行時才把真方法體填回記憶體裡的 `ArtMethod`，`.vdex`/`.oat` 落檔的是**加殼後的空殼 DEX**。你抽出來的是殼的 stub，真邏輯還在 runtime 才組出來——這正是 Ch 36 主動調用脫殼要解決的問題。

2. **cdex（compact dex）不吃標準工具**。ART 內部可能把 DEX 轉成更省空間的 **cdex（CompactDex）** 格式塞進 vdex，多個 DEX 共用 shared data section（Ch 4 進階提過）。你抽出來若是 cdex，很多只吃標準 DEX 的工具（baksmali 舊版）會報格式錯，要先用工具轉回標準 DEX。

```
 樂觀期待:  vdex ──抽──▶ 完整真 DEX ──jadx──▶ 讀懂邏輯   ✓ (無殼的普通 App)

 現實邊界:  vdex ──抽──▶ 殼的空殼 DEX  (抽取型加固, 真方法體 runtime 才填)  ✗
            vdex ──抽──▶ cdex 格式     (標準工具不吃, 要先轉標準 DEX)        ⚠
```

**心法**：vdex 抽 DEX 是「先試的快路」，成功就省事，失敗（拿到空殼或 cdex）就知道「這 App 有加固/用了 cdex」，該切到 Ch 36 的 runtime 主動調用脫殼。失敗本身就是情報。

## 三種執行狀態各自留下什麼逆向痕跡

把「直譯 / JIT / AOT」三種狀態對逆向者的具體意義攤開，你才知道遇到哪種該用什麼手段：

| 狀態 | 機器碼在哪 | 落不落檔 | 逆向者怎麼利用 | 陷阱 |
|---|---|---|---|---|
| **直譯** | 沒有機器碼，跑 bytecode | — | bytecode 就在 DEX/vdex，最好逆；hook 走 interpreter 路徑最單純 | 加固殼常刻意讓真方法走直譯（不落檔） |
| **JIT** | 進程內 code cache（記憶體） | ✗ 不落檔 | 記憶體 dump 能抓到熱方法的機器碼；但重開就沒了 | 不穩定，同方法不同次執行狀態可能不同 |
| **AOT** | `.oat`（檔案 + 映射記憶體） | ✓ 落檔 | `oatdump` 靜態拆機器碼，最好分析編譯結果 | 改 bytecode 對它無效（它不讀 bytecode 了）——要改 entrypoint（Ch 34/36） |

**一個貫穿全課的判斷**：你要對某方法動手前，先問「它現在是哪種狀態」。要它走直譯好 hook？`cmd package compile -m verify` 逼它別編（回本章的 filter）。要看它 AOT 編成什麼？`cmd package compile -m speed` 逼它全編再 `oatdump`。**能主動控制方法的執行狀態，是 ART 時代逆向者的一個基本槓桿**——這是 Dalvik 時代沒有的操作空間。

## 對比與取捨：三個時代的執行策略

| 面向 | Dalvik + JIT（~4.4） | ART 純 AOT（5.0） | ART 混合（7.0+，今天） |
|---|---|---|---|
| 編譯時機 | 執行期即時（trace JIT） | 安裝時全編 | 首次直譯/JIT + 閒置時 profile-guided AOT |
| 裝機速度 | 快（只 dexopt） | **慢**（全 AOT） | 快（不全編） |
| 啟動/執行速度 | 慢（暖機期直譯） | **快**（全機器碼） | 快（熱方法有 AOT） |
| 空間 | 小 | **大**（全編機器碼） | 中（只編熱方法） |
| 耗電 | 高（一直編） | 低 | 低（閒置才編） |
| 產物落檔 | `.odex`（輕量優化） | `.oat` 全量 | `.vdex`+`.oat`（部分）+`.art` |
| 逆向切入 | 主體是 DEX，好抽 | oat 有全量機器碼可 oatdump | **vdex 抽 DEX** / runtime 主動調用 |

一句話總結：**Dalvik→純 AOT→混合，是「執行快、裝機快、省空間省電」三個目標互相拉扯後找到的平衡點**。今天的 ART 讓「同一個方法的執行狀態是動態的」——這正是 Ch 34–36 一切 hook 與脫殼技術的物理基礎。

## 踩雷集錦

1. **錯誤直覺：「App 執行的就是我 jadx 看到的 DEX bytecode」→ 正確認識**：熱方法可能早被 AOT/JIT 編成機器碼了，執行時跑的是機器碼。你在 DEX 層 hook（改 bytecode）對已編成機器碼的方法可能無效，得改 entrypoint（Ch 36）。
2. **錯誤直覺：「vdex 保留 DEX，任何 App 都能直接抽出真邏輯」→ 正確認識**：抽取型加固落檔的是空殼 DEX，真方法體 runtime 才填；還可能是 cdex 標準工具不吃。抽出來是空殼就是「有加固」的訊號，切 Ch 36。
3. **錯誤直覺：「`.oat` 是純資料檔」→ 正確認識**：`.oat`（副檔名常是 `.odex`）是個合法 **ELF**，oat 資料藏在 section 裡。用 `readelf`/`oatdump` 分析，不要當純二進位硬啃。
4. **錯誤直覺：「編譯狀態是固定的」→ 正確認識**：一個方法可以在直譯/JIT/AOT 之間切換，且能被 `cmd package compile` 手動改。你想觀察特定狀態（全 AOT 或全直譯）時可以主動設 filter。
5. **錯誤直覺：「純 AOT 最好，為什麼要退回混合」→ 正確認識**：純 AOT 的裝機/更新慢到不能忍、空間爆炸（編了一堆從不執行的方法）。混合模式用 profile 只編熱方法，是實測後的工程取捨，不是退步。

## 進階：再往深一層

- **profile 檔的格式與污染**：`/data/misc/profiles/` 下的 profile 記錄「哪些方法熱」。它是二進位格式（`art/libprofile/profile/profile_compilation_info.h`）。安全研究會關注「能不能污染 profile 讓某方法被/不被 AOT 編譯」，藉此影響後續分析或觸發特定執行路徑。
- **cloud profile（雲端側寫）**：Play Store 會下發一份「大眾使用者的熱方法側寫」給你，讓 App 首次啟動前就有一份 profile 可用（Baseline Profiles）。逆向新版 App 時，dexopt 狀態可能一裝就是 speed-profile，因為 profile 是雲端來的。
- **quickening 與去 quickening**：舊版 ART 會把某些 DEX 指令替換成「quickened」變體（如 `invoke-virtual` → `invoke-virtual-quick`，直接用 vtable index）存進 vdex，加速直譯。抽出的 DEX 若含 quickened 指令，得先「去 quickening」還原成標準指令才通用。Android 10 起這機制有變化，版本敏感。
- **JIT code cache 與 OSR**：JIT 編出的機器碼放在進程內的 JIT code cache。ART 還有 **OSR（On-Stack Replacement，棧上替換）**——一個長迴圈跑到一半，能把正在直譯執行的方法「熱替換」成 JIT 機器碼繼續跑。這對「執行到一半狀態會變」的理解是更深一層，也解釋為什麼 hook 時機敏感。
- **dexopt 的觸發者**：背景 dexopt 由 `BackgroundDexOptService`（framework）排程；`cmd package compile` 是手動入口；OTA 更新後 `odsign`/`otapreopt` 重編 boot image。搞清楚誰在什麼時候編，你才能預測某 App 何時會有 AOT 產物。

## 動手練習

1. 在 AVD 挑一個 App，`adb shell dumpsys package <pkg> | grep -A3 "Dexopt state"` 看它現在的 filter 與 reason。然後 `adb shell cmd package compile -m speed -f <pkg>` 強制全編，再看一次，觀察 `status` 從 `speed-profile` 變 `speed`、`reason` 變 `cmdline`。
2. `adb shell find /data/app -name '*.vdex' 2>/dev/null` 找一個 App 的 vdex，pull 出來，用 `vdexExtractor`（或 `vdex-extractor`）抽 DEX，再 jadx 打開——親眼看到「從 vdex 直接還原 DEX」這條快路。找一個有加固的 App 重做，觀察抽出來是不是空殼。
3. pull 一個 `base.odex`，`readelf -S` 確認它是 ELF、找到含 oat 資料的 section。有 AOSP host 工具的話再 `oatdump --oat-file=base.odex --output=dump.txt`，翻 dump 看一個方法的 DEX bytecode 與 AOT 機器碼並排。
4. `adb shell ls -la /data/misc/profiles/cur/0/<pkg>/` 看 profile 檔存不存在、多大。跑幾次 App 再看它變沒變——這是「執行記側寫」的直接證據。

## 本章重點整理

- **一份 DEX 的每個方法有三種執行狀態**：直譯（interpreter）、AOT 機器碼（dex2oat 事先編、在 `.oat`）、JIT 機器碼（執行期即時編、在記憶體）。同一方法會動態切換。
- **演進三時代**：Dalvik+JIT（慢、耗電）→ ART 純 AOT（快但裝機慢、空間爆）→ ART 混合（今天：首次直譯/JIT + 閒置 profile-guided AOT），是三個目標拉扯後的平衡。
- **dex2oat 產三個檔**：`.vdex`（保留 DEX，脫殼捷徑）、`.oat`（AOT 機器碼，其實是 ELF）、`.art`（啟動 image）。編多深由 compilation filter（verify/speed-profile/speed）決定，可 `cmd package compile` 手動改。
- **對逆向的地基意義**：執行時跑的是 bytecode 還是機器碼，決定你的 hook/dump 打在哪一層；vdex 抽 DEX 是快路，抽到空殼/cdex 就是「有加固」的情報，切 Ch 36。

## 自我檢核

- [ ] 不看筆記，能講出一個方法的三種執行狀態，以及它們之間怎麼切換
- [ ] 能說出 Dalvik→純 AOT→混合三個時代各自的策略與被淘汰/採用的動機
- [ ] 能講清楚 `.vdex`/`.oat`/`.art` 各裝什麼，以及為什麼 vdex 是脫殼的一條捷徑
- [ ] 知道 `.oat` 其實是什麼檔案格式、用什麼工具拆它
- [ ] 能解釋 compilation filter 有哪些等級、怎麼手動改一個 App 的編譯狀態、改它對逆向有什麼用
- [ ] 能說出「從 vdex 抽 DEX 抽到空殼」代表什麼、下一步該做什麼

## 延伸閱讀

### 官方文件（一手依據）

- **[ART 與 Dalvik 官方設計文件](https://source.android.com/docs/core/runtime)** — AOSP
  - **讀哪裡**：Runtime 概覽、AOT/JIT 混合、dex2oat 那幾節
  - **和本章的關聯**：本章三時代演進與混合模式的權威出處，官方怎麼描述編譯策略跟本章對照著讀
  - **注意**：這頁會隨版本更新，看的時候留意它描述的是哪個 Android 版本
- **[Configure ART / dexopt](https://source.android.com/docs/core/runtime/configure)** — AOSP
  - **讀哪裡**：compilation filter 各等級、profile-guided 那節
  - **為什麼值得讀**：本章 `speed-profile`/`speed`/`verify` 的定義與觸發時機出自這；你要改 App 編譯狀態時的依據

### 原始碼（最終仲裁）

- **[art/ 原始碼](https://cs.android.com/android/platform/superproject/+/master:art/)** — Android Code Search
  - **讀哪裡**：`art/dex2oat/`（編譯器入口）、`art/runtime/oat/oat_file.h`、`art/runtime/vdex_file.h`（產物格式）
  - **為什麼值得讀**：`.oat`/`.vdex` 的內部格式沒有比原始碼更權威的文件；**務必切到你目標裝置對應的 Android 版本 tag**，欄位版本間會變

### 逆向實戰視角

- **[看雪 ART 系列文章](https://bbs.kanxue.com/)**（站內搜「ART dex2oat oat vdex」）
  - **這篇說什麼**：中文社群對 oat/vdex 格式與脫殼實務的拆解，很多是實測筆記
  - **讀哪裡**：找專講 vdex/oat 結構與 vdexExtractor 用法的帖
  - **前提知識**：讀過本章的三檔分工，這些帖給你「實際 pull 出來怎麼拆」的操作細節
- **[vdexExtractor 專案](https://github.com/anestisb/vdexExtractor)** — anestisb
  - **這篇說什麼**：從 vdex 抽 DEX 的參考實作
  - **讀哪裡**：README 的支援版本表（不同 Android 版本 vdex 格式不同，工具支援度有限）
  - **和本章的關聯**：本章「vdex 抽 DEX 快路」的實作工具，讀它的版本相容說明能理解為什麼 vdex 這麼版本敏感

下一章我們鑽進 ART runtime 內部，把「執行一個方法」這件事拆到 `ArtMethod` 結構、ClassLinker、entrypoint 這些具體物件——你會看到本章講的三種執行狀態，具體是 `ArtMethod` 裡哪個欄位在決定的。

→ [Ch 34 ART runtime 內部](./34-art-runtime-internals.md)
