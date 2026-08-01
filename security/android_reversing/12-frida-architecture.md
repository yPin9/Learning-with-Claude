# Ch 12 — Frida 架構與原理

> **目標**：把 Frida 從「一個能 hook 的工具」還原成「一套跨進程的程式碼注入與執行框架」。你要能回答：你在電腦上敲的那行 `frida -U -f ...`，到底怎麼讓一段 JavaScript 跑進另一台裝置上、另一個進程的位址空間裡？frida-server、agent、gadget 各是什麼、誰載入誰？spawn 和 attach 差在哪、為什麼有些 App 只能用 spawn？搞懂這一章，後面 Ch 13/14 的每個 API 你都知道它跑在哪、代價是什麼、為什麼會被反 Frida 偵測。

> **環境**：以 **Frida 16.x**、Ch 0 建好的 **x86_64 AVD（Android 13 / API 33，可 root）** 為準。frida-server 以 root 在 guest 內跑、frida client 在 host。本章有實際指令示範（`frida-ps`、`frida-trace`），但架構內部（注入時序、GumJS 執行）的細節我在沙箱無法跑，會明確標注哪些是「未實測，理論預期行為」。

## 為什麼需要這個？

因為不懂架構，你就只會照抄別人的腳本，一出錯完全不知道問題在哪一層。attach 不上，是 frida-server 沒跑、還是版本不對、還是被反注入擋了？hook 裝好了但沒觸發，是時序問題（App 在你 attach 前已經跑完那段）、還是 spawn/attach 選錯了？某個 App 一 attach 就閃退，是它偵測到了什麼？這些問題的答案全在「Frida 是怎麼運作的」這張圖裡。搞懂它，你從「腳本使用者」升級成「知道自己在幹嘛的人」——這也是後面對抗反 Frida（Ch 30）的地基。

## 先建立直覺：把你的程式碼「快遞」進別人的進程

Frida 的本質，用一句話講：**它想辦法在目標進程的位址空間裡跑一個 JavaScript 引擎，並讓這個引擎能讀寫該進程的任何記憶體、攔截任何函式。** 你的 hook 腳本，就是餵給這個引擎的 JS。

```
     host（你的電腦）                        guest（AVD, Android 13）
 ┌──────────────────────────┐          ┌────────────────────────────────┐
 │  frida CLI / Python       │          │   frida-server （root 進程）    │
 │   你的 hook.js  ──────────┼──ADB────▶│      │ ptrace attach            │
 │        ▲                  │ TCP 27042│      ▼                         │
 │        │  RPC / message   │◀─────────┼──▶ target App 進程             │
 │        │  （雙向）        │          │   ┌──────────────────────────┐ │
 └──────────────────────────┘          │   │ 注入的 agent (.so)        │ │
                                        │   │   └─ GumJS（QuickJS/V8）  │ │
                                        │   │        跑你的 hook.js     │ │
                                        │   │   └─ Gum（攔截/記憶體引擎）│ │
                                        │   └──────────────────────────┘ │
                                        └────────────────────────────────┘
```

三個角色先分清楚：

- **frida client（host 端）**：你敲指令的地方（`frida` CLI、Python `frida` 模組、frida-tools）。它不做 hook，它負責「把你的腳本送過去、把對面 log 收回來」。
- **frida-server（guest 端）**：以 root 跑的常駐進程（Ch 0 你 push 進去那個）。它是「注入的執行者」——負責把 agent 塞進目標進程。
- **agent（被注入到目標進程裡的那塊）**：一個由 frida-server 動態載入到 target 位址空間的原生程式碼（一個 `.so`），裡面帶著 **GumJS**（JS 引擎 + 綁定）。**你的 hook.js 實際上是在這裡、在 target 進程內部跑的。**

最反直覺、也最關鍵的一點：**你的 JavaScript 不是在你電腦上跑，是在被你分析的那個 App 進程裡跑的。** `console.log` 的字串是從 target 進程一路傳回你 host 的終端。想通這件事，後面很多行為就順了。

## 底層機制一：注入是怎麼發生的（ptrace + 載入 agent so）

frida-server 怎麼把 agent 塞進一個「不是它自己」的進程？這是 Frida 最硬核的一步，也是它需要 root 的原因。核心是 Linux 的 `ptrace`——一個讓進程 A 控制進程 B 的 syscall（Ch 3 講過注入為何要 root，這裡是它的實作面）。

流程（**以下為 Frida 內部機制的理論說明，未在本沙箱實測**）：

```
frida-server                          target App 進程
 │
 │ 1. ptrace(PTRACE_ATTACH, pid)  ─────▶  被暫停，frida-server 成為它的 tracer
 │                                        （這就是為什麼要 root：ptrace 別的
 │                                          App 進程需要特權 / 同 UID）
 │
 │ 2. 讀 target 的 /proc/pid/maps，找到 libc、linker 的位址
 │    在 target 記憶體裡「借」一段空間，寫入一小段 bootstrap 程式碼
 │
 │ 3. 改 target 的暫存器（設 PC 指向 bootstrap），讓它「替我們」
 │    呼叫 dlopen()/linker 載入 agent.so   ─────▶  agent.so 映射進 target
 │
 │ 4. agent.so 的初始化跑起來：啟動一條新執行緒，
 │    在裡面初始化 GumJS，載入你的 hook.js
 │
 │ 5. ptrace(PTRACE_DETACH) 或轉為背景操控，target 恢復執行
 │                                        ─────▶  App 繼續跑，但體內
 │                                                 已經住著你的 agent
```

幾個要點：

- **為什麼要 root**：`ptrace` 一個不屬於你的進程需要特權。frida-server 以 root 跑，才能 attach 任意 App（Ch 0 選 Google APIs image 能 root 正是為此）。如果沒 root，只能用另一條路——gadget（下面講）。
- **注入完 frida-server 就「退居二線」**：真正做 hook、跑 JS 的是 target 進程內的 agent。frida-server 之後主要負責「當通訊中繼」——把你 host 的訊息轉給 agent、把 agent 的 log 轉回你。
- **這一步會留痕跡**：target 的 `/proc/pid/maps` 裡會出現 agent 相關的映射、多了執行緒、可能有 `frida` 字樣的記憶體區段。反 Frida 就是靠掃這些痕跡（Ch 30）。**注入是強力手段，但不隱形。**

> **這是理解「一 attach 就閃退」的鑰匙**：如果 App 有反 Frida，它可能在被 attach 的瞬間（ptrace 讓它暫停/多執行緒出現）或稍後（掃 maps 掃到 agent）偵測到，然後自毀。所以問題不一定在你的腳本——可能你的注入動作本身就觸發了防護。

## 底層機制二：GumJS —— agent 內部的 JS 引擎

agent 裡真正執行你腳本的是 **GumJS**：Frida 的核心引擎 **Gum**（用 C 寫的攔截、記憶體、符號解析引擎）加上一個 **JavaScript runtime** 的綁定。

Frida 的 JS runtime 有兩個選擇：

| Runtime | 特性 | Frida 的取捨 |
|---|---|---|
| **QuickJS**（16.x 預設） | 輕量、啟動快、記憶體小 | 注入負擔小、對目標擾動低——**現代 Frida 預設** |
| **V8** | JIT、執行快、功能全 | 重、佔記憶體，適合大量運算的腳本；需 `--runtime=v8` 指定 |

為什麼預設從 V8 換成 QuickJS？因為 Frida 的 JS 大多是「攔截 + 印 log + 少量邏輯」，不是計算密集型。QuickJS 更小更快啟動，注入進 target 時對它的擾動更小——這跟 Ch 11 講的「動態的觀測代價要盡量輕」是同一個工程取向。你只有在腳本真的要跑重運算（大量 loop、複雜解密）時才需要 `--runtime=v8`。

而 Gum 是底層那顆真正幹活的引擎。你在 JS 寫的 `Interceptor.attach`、`Memory.readByteArray`、`Module.getExportByName`——這些**不是 JS 在做**，是 JS 呼叫到 Gum 的 C 實作。GumJS 就是「把 Gum 的能力包成 JS API」的那層綁定：

```
   你的 hook.js（JS）
        │  Interceptor.attach(addr, {...})
        ▼
   GumJS 綁定層（JS ↔ C 的橋）
        │
        ▼
   Gum（C）：改寫 target 記憶體、插入 trampoline、
            管理攔截、解析符號、讀寫記憶體
        │
        ▼
   target 進程的真實指令 / 記憶體
```

理解這個分層的好處：你會知道**你的 JS 只是「指揮」，真正動 target 記憶體的是底層 C**。所以 hook 的效能瓶頸、被偵測的痕跡，都在 Gum 那層，不在你的 JS 寫得漂不漂亮。

## 底層機制三：三種注入形態——server、gadget、embedded

Frida 有三種把 agent 弄進 target 的方式，對應不同場景：

| 形態 | 怎麼進 target | 需要 root？ | 適合 |
|---|---|---|---|
| **frida-server** | 常駐進程用 ptrace 注入任意 App | ✅ 需要 | **本課主力**（AVD 已 root） |
| **frida-gadget** | 一個 `.so`，你把它塞進 APK 讓 App 自己載入 | ❌ 不用 | 無法 root 的裝置；重打包一次 |
| **embedded/preloaded** | 用 `LD_PRELOAD` 或 patch 讓進程啟動就載入 gadget | 視情況 | 特殊部署 |

**gadget 是「沒 root 也能玩 Frida」的關鍵**：你把 `frida-gadget.so` 塞進目標 APK、改一行 smali 讓 App 啟動時 `System.loadLibrary` 載入它，App 自己就把 agent 帶進了自己的進程——不需要 frida-server、不需要 ptrace、不需要 root。代價是你得重打包重簽名一次（Ch 6）。本課 AVD 已 root，主用 frida-server；但真機無法 root 時，gadget 是你的後路。

> **關鍵區分**：frida-server 是「從外面把 agent 注進去」（要 root，因為 ptrace 別人）；gadget 是「App 自己把 agent 載進來」（不用 root，因為是它自己載入自己的 .so）。同一個 agent 能力，兩種送達路徑。

## 底層機制四：spawn vs attach，以及為什麼有時只能 spawn

你啟動 hook 有兩種模式，差在**你在 App 生命週期的哪個時間點介入**：

```
  attach（附加到已在跑的 App）
     App 已啟動並跑了一段 ──────▶ 你 attach ──▶ 開始 hook
     ▲ 問題：App 啟動時做的事（初始化解密、早期反調試、
       靜態初始化區塊）在你 attach 前就跑完了，你錯過了

  spawn（由 Frida 啟動 App，暫停在最早期）
     Frida 啟動 App 但立刻凍住 ──▶ 你裝好 hook ──▶ resume 才開始跑
     ▲ 優勢：連 App 的第一行程式碼都還沒跑，你的 hook 已就位
```

指令上的差別：

```bash
# spawn：由 frida 啟動並在最早期凍住，hook 裝好才 resume（-f = spawn）
frida -U -f com.example.target -l hook.js
#      -U 用 USB/adb 連線；-f 指定 package 由 frida spawn 它

# attach：附加到已在跑的進程（-n 用名字 / -p 用 pid）
frida -U -n com.example.target -l hook.js
```

**為什麼常常必須 spawn**：很多值錢的東西發生在 App 啟動的最早期——

- 加固殼在 `Application.attachBaseContext`/靜態初始化區塊就解密真 DEX
- 反調試/反 Frida 檢查常在 `onCreate` 之前就跑
- 字串解密表、金鑰在 App 一啟動就初始化好

這些你若用 attach，等你連上時它們早跑完了，hook 撲空。spawn 讓你**趕在第一行程式碼之前**把 hook 就位，才截得到這些早期事件。這是實務上 `-f`（spawn）遠比 attach 常用的原因。

> **spawn 的代價**：spawn 會重啟 App（殺掉現有的、重新啟動）。如果你要分析的是「App 現在的某個運行中狀態」（比如已登入的 session），spawn 會把它洗掉。這種情況才用 attach。**預設用 spawn，需要保留現場才用 attach。**

## 底層機制五：RPC —— host 和 agent 怎麼對話

你的 hook 在 target 進程裡跑，你的控制邏輯常想在 host 端（Python）跑——兩邊怎麼溝通？靠 **RPC** 與 **message 通道**。

- **agent → host**：`console.log(...)` 和 `send({...})` 把資料從 target 進程經 frida-server 傳回你 host。`send` 傳結構化資料（會觸發 host 端的 `on('message')`）。
- **host → agent**：你在 JS 用 `rpc.exports = { ... }` 把函式暴露出來，host 端 Python 就能 `script.exports_sync.yourFunc(arg)` 呼叫它——**在 host 觸發、在 target 進程內執行**。

一個典型的 RPC 骨架（Frida 16.x 寫法，逐段解釋）：

```javascript
// agent.js —— 在 target 進程內跑
rpc.exports = {
    // host 之後可以呼叫 readSecret()，它會在 target 進程內執行
    readSecret: function () {
        // 這裡能讀 target 的記憶體、呼叫它的函式（Ch 13/14 教）
        return "some value read from inside the process";
    }
};
```

```python
# host.py —— 在你電腦上跑
import frida
device = frida.get_usb_device()          # -U：透過 adb 連 guest
pid = device.spawn(["com.example.target"])  # spawn，App 被凍在最早期
session = device.attach(pid)             # 把 agent 注進這個 pid
script = session.create_script(open("agent.js").read())
script.on("message", lambda m, d: print("[msg]", m))  # 收 agent 的 send/log
script.load()                            # 注入並執行 agent.js（含裝好 rpc.exports）
device.resume(pid)                       # 現在才讓 App 真正開始跑
print(script.exports_sync.read_secret()) # host 呼叫，target 內執行，值傳回來
```

逐行為什麼這樣寫：
- `get_usb_device()` 對應 CLI 的 `-U`——透過 adb 找到 guest 上的 frida-server。
- `spawn` 先啟動並凍住（對應 `-f`），`attach` 才真正注入 agent，`resume` 最後放行——這就是 spawn 模式「hook 先就位、App 後開跑」的手動版。
- `script.on("message", ...)` 是 host 收 agent 訊息的入口；`send()`/`console.log` 都走這條回來。
- `exports_sync.read_secret`：JS 的 `readSecret` 駝峰名，在 Python 端變成 `read_secret`（Frida 的命名轉換）。

> **上面的 Python 我在本沙箱無法執行**（沒有 AVD/Frida）。這是 Frida 16.x 的標準 API：`get_usb_device`/`spawn`/`attach`/`create_script`/`resume` 都是現行方法名，`exports_sync` 是 16.x 的同步呼叫介面。你在自己 AVD 上：`python3 host.py`（先確認 frida-server 已跑），會看到 `read_secret` 回傳的值印出來。CLI 版更省事：多數時候 `frida -U -f pkg -l agent.js` 就夠，Python 版是你要在 host 端寫控制邏輯（批量、自動化，Ch 40）時才用。

## 把架構知識變成除錯能力

學架構最實際的回報，是你看到報錯能立刻定位到「是哪一層出事」。把常見錯誤對回本章的圖：

```
   你敲 frida -U -f pkg -l x.js，出錯了。錯在哪一層？
 ─────────────────────────────────────────────────────────────────
  Failed to enumerate processes:              ← client ↔ frida-server
    unable to communicate with remote            這條線斷了
    frida-server                              → server 沒跑 / adb 沒接 / port 沒通

  major versions match                        ← client 與 agent 的通訊協議
                                              → 版本不一致（協議綁在版本上）

  Unable to find process with name ...        ← attach 找不到目標
                                              → App 沒在跑（該用 -f spawn）

  Process terminated（一 attach 就沒了）       ← 注入痕跡被 target 偵測
                                              → 反 Frida（ptrace/maps 被掃到，Ch 30）

  hook 裝好卻沒觸發                            ← 時序：你 attach 前那段已跑完
                                              → 改用 spawn（-f）趕在第一行前就位

  ReferenceError / 腳本內部錯                  ← agent 內 GumJS 執行你的 JS 出錯
                                              → 純粹是你 JS 寫錯，跟注入無關
```

看出重點沒有：**同樣是「跑不起來」，根因可能在完全不同的層**——通訊線、版本、時序、反偵測、或你的 JS。不懂架構的人把這五種都當成「Frida 壞了」瞎試；懂架構的人一看錯誤字樣就知道去修哪一層。這張對照表是你 Ch 13 之後每次卡住的第一個查詢點。

## 對比與取捨

| 維度 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| **注入形態** | frida-server（要 root） | gadget（不用 root，重打包） | 能 root 用 server；不能 root 用 gadget |
| **啟動模式** | spawn（`-f`，凍在最早期） | attach（`-n`/`-p`，附加運行中） | 預設 spawn；要保留現場才 attach |
| **JS runtime** | QuickJS（預設，輕） | V8（`--runtime=v8`，快但重） | 預設 QuickJS；腳本重運算才換 V8 |
| **控制端** | CLI（`frida -l`） | Python 綁定（RPC/自動化） | 手動探索用 CLI；批量/自動化用 Python |

## 踩雷集錦

1. **「以為 hook.js 在我電腦上跑」**：錯。它在 target 進程內部跑，`console.log` 是遠端傳回來的。想通這點，很多「為什麼能讀到 App 記憶體」的疑惑就消失了——因為你的程式碼就住在它體內。
2. **「client 跟 server 版本不用完全一樣」**：錯。Frida 沒有向後相容保證，`major versions match` 錯誤就是版本不一致。兩邊版本號要完全相同（Ch 0 踩雷 2 已警告，這裡從架構角度理解：agent 和 client 的通訊協議綁死在版本上）。
3. **「attach 就好，幹嘛用 spawn」**：很多早期事件（殼解密、反調試、字串表初始化）在 App 啟動最早期就跑完，attach 撲空。預設用 spawn（`-f`）趕在第一行前就位。
4. **「一 attach 就閃退 = 我腳本寫錯」**：不一定。注入動作本身（ptrace、agent 映射進 maps）會留痕跡，反 Frida 可能在你還沒 hook 任何東西時就偵測到並自毀。先懷疑防護，不是先懷疑腳本（Ch 30 專治）。
5. **「沒 root 就不能用 Frida」**：錯。gadget 讓 App 自己載入 agent，不需要 root，代價是重打包一次。root 只是 frida-server（ptrace 注入）這條路的前提。
6. **「AVD 上抓 arm64 的 frida-server」**：x86_64 AVD 要 x86_64 的 frida-server，架構錯了 `exec format error`（Ch 0 踩雷 3）。這也提醒你：agent、gadget 的 `.so` 都得配 target 架構——x86_64 AVD 上是 x86_64。

## 進階：再往深一層

- **Interceptor 底層在幹嘛（trampoline）**：`Interceptor.attach` 不是魔法。Gum 在目標函式開頭改寫幾個 byte，跳到一段它生成的 trampoline，trampoline 先呼叫你的 `onEnter`、再執行被覆蓋的原指令、再跳回去。這是「inline hook」的一種。理解它你就懂為什麼 hook 會被完整性校驗偵測到（函式開頭的 byte 被改了，Ch 32），以及為什麼某些指令對齊/短函式 hook 起來麻煩。Ch 14 會實際用它。
- **frida-server 的通訊埠**：預設 `27042`。frida client 透過 adb 把 host 的 port forward 到 guest 的這個 port（`frida-ps -U` 內部就在做這件事）。反 Frida 的一招是掃這個 port（連 `27042` 有回應就判定有 frida-server），對應的繞法是啟動 server 時換 port（`frida-server -l 0.0.0.0:xxxxx`）。
- **frida-trace 是上層封裝**：`frida-trace -U -i "open" -n com.foo` 這種一行 trace 工具，底層就是幫你自動生成一堆 `Interceptor.attach` 的 agent 腳本。理解架構後你會知道它不是另一個工具，是 Frida 核心能力的糖衣。Ch 15 深入。
- **Stalker —— 指令級 trace**：Gum 還有個 Stalker 引擎，能動態重編譯目標的指令流、逐條 trace 執行（對抗 OLLVM 控制流平坦化很有用）。它比 Interceptor 重得多（擾動大、慢），是 Ch 15 的主題。這裡先知道：Frida 的能力從「hook 一個函式」到「trace 每一條指令」是一個由輕到重的光譜。

## 動手練習

1. **看注入痕跡**：在 AVD 上 `frida -U -f com.android.settings -l -`（空腳本或載入 Ch 0 的 smoke.js），另開 shell `adb shell cat /proc/<pid>/maps | grep -i frida`——親眼看到 agent 相關映射。這就是反 Frida 掃描的目標。
2. **spawn vs attach 的差別**：寫一個 hook 印出某個 App 早期初始化方法（如自訂 `Application.onCreate`）。先用 attach 試（多半撲空），再用 `-f` spawn 試（截得到）。體會「時序」這件事。
3. **手寫 Python RPC 版**：把本章的 `host.py` + `agent.js` 在自己 AVD 跑起來，讓 `readSecret` 回傳一個你在 target 進程內讀到的字串（比如某個系統屬性）。體會「host 呼叫、target 執行、值傳回」的完整迴路——這是 Ch 40 自動化的基礎。

## 本章重點整理

- **Frida 的本質**：在 target 進程內跑一個 JS 引擎（GumJS），讓它能讀寫該進程記憶體、攔截任何函式。**你的 hook.js 在 App 體內跑，不在你電腦上。**
- **注入靠 ptrace**：frida-server 以 root ptrace 目標、載入 agent.so、在裡面起 GumJS——這是它要 root 的原因，也是它留痕跡（可被反 Frida 偵測）的原因。
- **三種送達路徑**：frida-server（要 root，注入任意 App）、gadget（不用 root，App 自載）、embedded；能力相同、路徑不同。
- **spawn vs attach**：spawn（`-f`）趕在第一行前就位、截得到早期事件，是常態；attach 保留運行現場，特殊時才用。
- **QuickJS 是 16.x 預設**（輕、擾動小），V8（`--runtime=v8`）給重運算腳本；**Gum 是真正動 target 記憶體的 C 引擎**，JS 只是指揮。

## 自我檢核

- [ ] 能畫出 client / frida-server / agent 三者的關係，並說出「你的 JS 在哪裡跑」
- [ ] 能解釋 frida-server 為什麼需要 root（跟 ptrace 的關係），以及 gadget 為什麼不用
- [ ] 能說清楚 spawn 和 attach 的差別，並舉一個「必須 spawn」的實例
- [ ] 知道 QuickJS 和 V8 的取捨，以及 Gum 和 GumJS 各是什麼
- [ ] 能解釋為什麼「一 attach 就閃退」可能不是腳本問題，而是注入痕跡被偵測

## 延伸閱讀

- **[Frida 官方文件 — Modes of operation / 架構](https://frida.re/docs/modes/)**
  - **讀哪裡**：injected / embedded / preloaded 三種模式那節，對照本章「三種送達路徑」
  - **學什麼**：官方對 frida-server vs gadget 的定位與部署差異
  - **關聯**：本章的注入形態表就是它的直覺化，讀它補齊部署細節
- **[Frida 官方文件 — Gadget](https://frida.re/docs/gadget/)**
  - **讀哪裡**：整頁，特別是把 gadget 塞進 App 的方式與設定檔
  - **學什麼**：無 root 時的完整替代路徑，接上 Ch 6 的重打包
  - **關聯**：AVD 有 root 主用 server，但真機常無 root，這是你的後路
- **[Frida 官方文件 — JavaScript API（Interceptor / rpc / Script）](https://frida.re/docs/javascript-api/)**
  - **讀哪裡**：先掃 `rpc`、`send`/`recv`、`Interceptor` 三節
  - **學什麼**：本章 RPC 骨架用到的 API 的權威定義與完整選項
  - **關聯**：Ch 13/14 會大量用這份 API，這章先讓你知道它們跑在架構的哪一層
- **[HackTricks — Frida Tutorial](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/frida-tutorial/index.html)**
  - **讀哪裡**：frida-server 部署、spawn/attach、常見報錯排解
  - **學什麼**：一線實務的 Frida 操作與踩坑，補本章的實戰面
  - **關聯**：把本章的架構理解落成可複製的指令，接 Ch 13 動手

下一章我們把 Frida 對準最常見的戰場——Java 層。你會學會 `Java.use`/`.implementation` 怎麼覆寫任意方法、怎麼改參數和返回值、怎麼枚舉已存在的物件實例——把這一章的架構知識變成一支支能跑、能改行為的腳本。

→ [Ch 13 Frida hook Java 層](./13-frida-hook-java.md)
