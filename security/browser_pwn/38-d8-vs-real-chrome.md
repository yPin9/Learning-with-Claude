# Ch 38 — d8 與真實 Chrome renderer 的差異：你的 CTF exploit 移植清單

> **目標**：把「一顆 d8」和「真實 Chrome renderer」之間的落差量化清楚。你會知道兩者在 build flag、mitigation 密度、可用 API、進程/沙盒邊界上到底差了什麼，看懂為什麼一個在 d8 上打得漂亮的 CTF exploit **搬到真 Chrome 會直接死**，並拿到一份「d8 exploit → renderer exploit」的移植 checklist。這章把 [Ch 1](./01-why-renderer-attack-surface.md) 的三節模型從「概念」變成「你手上 exploit 要補的具體清單」。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`（sandbox on）、`out/x64.release.nosbx/d8`（off）。本章對比 d8 端可驗證的（shell helper、build flag、`os.system`）真跑；真實 Chrome renderer 行為多為「未實測，理論預期」，並給你自驗步驟與所需環境。

## 為什麼需要這個？

你在 CTF 打的每一題，靶子都是 `d8`——V8 團隊拿來開發、測試、跑 benchmark 的一個**命令列 shell**。它把「V8 引擎」單獨拎出來，剝掉 Chrome 的一切外殼：沒有 DOM、沒有多進程、沒有 renderer 沙盒、沒有 Mojo。這對學習是完美的（你能專心打 V8 內部），但它同時養出一個危險的錯覺：**以為 d8 = renderer**。

不是。d8 是 renderer 的一個**極度簡化、且開了作弊選單的替身**。[Ch 1](./01-why-renderer-attack-surface.md) 講過「在 d8 裡 `system('/bin/sh')` 拿 shell」在真實世界毫無意義，因為那個 shell 被關在沙盒進程裡。這章要把那句話**拆到 build flag 和 API 的粒度**：具體是哪些差異，讓你的 d8 exploit 在真 Chrome 裡失效？你要補什麼才能讓它在真 renderer 裡活？搞懂這張清單，你才算真正理解「V8 RCE 只是 full chain 第一節」這句話的重量。

## 先建立直覺：實驗室的裸引擎 vs 戰場上的整車

```
   ┌─────────────────────────┐          ┌───────────────────────────────────┐
   │  d8（實驗室裸引擎）        │          │  Chrome renderer（戰場整車）        │
   │                          │          │                                    │
   │  V8 引擎                  │          │  V8 引擎（同一顆，但 build 不同）    │
   │  + print/read/os.system  │  ← 作弊   │  + Blink（DOM/HTML/CSS）            │
   │  + --allow-natives-syntax│    選單   │  + WebAssembly / Web API            │
   │  單進程、無沙盒            │          │  多進程 + seccomp + namespace 沙盒   │
   │  no DOM                  │          │  Mojo IPC ↔ Browser process        │
   └─────────────────────────┘          └───────────────────────────────────┘
        你在這裡練 V8 內部                    你的 exploit 最終要在這裡活
        （拿到 code exec 就結束）              （code exec 只是第一節，人還在沙盒）
```

核心差異一句話：**d8 給你一堆真實世界不存在的「作弊道具」，同時拿掉了真實世界最硬的「沙盒牆」**。你的 CTF exploit 建立在作弊道具上、且到「拿 shell」就收工；移植到真 Chrome，作弊道具全部消失、且拿到 code exec 之後才走到牆前面。下面把差異分四類拆開。

## 差異一：build flag —— 同一顆 V8，編法天差地別

d8 和 Chrome 裡的 V8 是同一份原始碼，但**編譯選項（GN args）不同**，導致行為與 mitigation 密度不同。關鍵幾個：

| GN arg | CTF d8 常見 | 真實 Chrome | 對 exploit 的影響 |
|---|---|---|---|
| `v8_enable_sandbox` | 常關（no-sbx 靶）或開 | **開** | 開 → backing store 等指標進 external pointer table，「任意讀寫 → 直接控 raw pointer」被斬（[Ch 34](./34-v8-sandbox.md)） |
| `v8_enable_pointer_compression` | 通常開 | **開** | 你的 R/W 是 32-bit cage 內位移，不是 64-bit 絕對位址（[Ch 4](./04-pointer-compression.md)） |
| `is_debug` / `dcheck_always_on` | CTF 常 release | release | debug build 有 DCHECK 會提早 crash，掩蓋一些「release 才可利用」的狀態 |
| `v8_enable_slow_dchecks` | 關 | 關 | 同上 |
| `enable_os_system` (d8) | **CTF 常開** | 不存在 | 開 → d8 有 `os.system()` 直接開 process，真實世界沒有這東西 |
| `symbol_level` | 常帶 symbol | strip | 你本地能 `%DebugPrint` 看得清清楚，真實 Chrome 沒 natives、沒 symbol |

**本課的雙 d8 就是這條線的活教材**：`out/x64.release`（sandbox on）對齊真實 Chrome 的 mitigation 密度，`out/x64.release.nosbx`（sandbox off）對齊「老 CTF 靶 / 教學簡化」。你在 nosbx 上打通的「任意讀寫 → 控 ArrayBuffer backing store 指標 → 寫任意記憶體」，換到 sandbox on 的 d8 上就會發現 backing store 指標被 external pointer table 包住，這一步直接斷——而**真實 Chrome 站在 sandbox on 這邊**。這也是為什麼本課堅持給你兩顆 d8：讓你親手撞到這條界線，而不是等移植到真 Chrome 才發現。

驗證兩顆 d8 的存在與差異（真跑）：

```bash
# 兩顆都在
~/v8build/v8/out/x64.release/d8      -e 'print(version())'   # sandbox on
~/v8build/v8/out/x64.release.nosbx/d8 -e 'print(version())'  # sandbox off
# 都印 15.3.0 (candidate)
```

## 差異二：mitigation 密度 —— 老 exploit 為什麼在新 Chrome 死

[Ch 1](./01-why-renderer-attack-surface.md) 的歷史演進講過「每一層防禦把攻擊逼進更窄的角落」。真實 Chrome 的 mitigation 是**全開且最新**，而 CTF 靶常常鎖在某個歷史時間點、mitigation 密度較低。這造成一個反覆出現的現象：**一篇 2019 的 exploit writeup，照抄到今天的 Chrome 上每一步都死**。

按時間軸看你會撞到的牆：

- **~2019 前**：沒 pointer compression、沒 sandbox。任意讀寫 = 進程級 64-bit 任意讀寫，直接找 WASM RWX 頁寫 shellcode。\*CTF 2019 oob 的收尾就是這條。
- **~2020**：pointer compression 上線（[Ch 4](./04-pointer-compression.md)）。你的物件指標變成 32-bit cage 內位移，leak 一個絕對位址不再免費，heap 內讀寫被夾在 4GB cage 裡。
- **~2021+**：V8 Sandbox（ubercage）逐步上線（[Ch 34](./34-v8-sandbox.md)）。`ArrayBuffer` backing store 指標、external pointer 全被關進 external pointer table，「拿到 cage 內任意讀寫 → 直接控一個 raw pointer 寫進程任意位址」這條經典路被斬。你需要 [Ch 35](./35-bypassing-v8-sandbox.md) 的思路。
- **~2022+**：WASM RWX 頁被逐步收緊（[Ch 33](./33-wasm-rwx-jit-spray.md)），CFI/CET 相關硬化（[Ch 36](./36-cfi-cet-data-only.md)）讓「劫持一個函式指標跳去 shellcode」更難，data-only 思路上升。

所以「移植一個老 exploit」本質是**把它的每一步對照今天的 mitigation 重寫收尾**。前面拿原語的部分（addrof/fakeobj）思路不變，但「任意讀寫 → code exec」這一段（[Ch 32](./32-arbitrary-rw-to-code-exec.md)）幾乎每隔兩年就要換一套。這也是為什麼本課的重心放在「**現代** mitigation 下怎麼做」，而不是複述 2019 的教程。

> **未實測，理論預期**：具體「某老 exploit 在今天 Chrome 哪一步死」需要你自己抓一個對應版本的 Chrome 實測。自驗方式：用 `chrome://version` 查你機器上 Chrome 的 V8 版本，對照那篇 exploit 的目標版本，逐 mitigation（sandbox/pointer compression/WASM）勾選「這個 mitigation 在目標版本存不存在」。

## 差異三：可用 API —— d8 的作弊道具，renderer 一個都沒有

這是移植時最先撞到、也最容易被低估的差異。你 CTF exploit 裡用得爽的一切 d8 專屬 API，**在真 renderer 裡完全不存在**：

| d8 能力 | 用途（CTF） | 真實 renderer 的替代 |
|---|---|---|
| `print()` | 印 leak、debug | 沒有 stdout。改用 DOM（`document.title=`）或 `fetch` 回傳資料 |
| `read()` / `readline()` | 讀 flag / 讀 stdin | 沒有本地檔案存取（沙盒擋）。要靠 sandbox escape 後才碰得到檔案 |
| `os.system()` | 直接開 shell | **不存在**。renderer 沒有開 process 的能力，seccomp 擋 `execve` |
| `%OptimizeFunctionOnNextCall` 等 natives | 精準控制 tiering | 沒有 `--allow-natives-syntax`。要改成跑熱迴圈自然觸發優化（[Ch 12](./12-speculation-deopt.md)） |
| `%DebugPrint` | 看物件佈局 | 無。只能靠自己的 leak 原語推佈局 |
| `d8.file.execute` | 載入別的 js | 用 `<script>` / `import` |

移植清單裡最重要的兩條：

1. **拿掉所有 `%natives`**。CTF 靠 `%OptimizeFunctionOnNextCall(f)` 精準優化，真實環境要改成「呼叫 `f` 幾萬次讓它自然升到 Maglev/TurboFan」。這改變你的觸發碼結構：你得確保迴圈真的把函式跑熱、且優化在你要的時機發生（可能要處理 OSR，[Ch 2](./02-v8-architecture.md)）。
2. **重寫 I/O 與收尾**。CTF 的終局是 `os.system('/bin/sh')` 或 `read('/flag')`；真實 renderer 的終局是「拿到 code exec 後，發 Mojo IPC 去打 Browser process」（[Ch 39](./39-renderer-mojo-sandbox-escape.md)），或至少在 renderer 內用 DOM/`fetch` 把偷到的資料傳出去。你不能 `print`，要學會用真實 Web API 當你的輸出通道。

## 差異四：進程與沙盒邊界 —— code exec 之後才是真正的起點

d8 是**單進程、無沙盒**。你在 d8 裡拿到 code exec，就是拿到整個進程——`os.system` 隨你開。真實 renderer 是**多進程 + seccomp + namespace 沙盒**（[Ch 1](./01-why-renderer-attack-surface.md) 詳述）。你在 renderer 裡拿到 code exec，只是拿到**一個窮到只剩 code exec 的沙盒進程**：

- **seccomp** 把你能呼叫的 syscall 砍到剩一小撮。`execve`/`open`/`connect` 大多被擋——所以 CTF 的 `execve("/bin/sh")` 直接失效。你能做的是讀寫自己進程的記憶體、發 Mojo IPC。
- **namespace** 讓你看不到主系統的檔案、網路、其他進程。
- **要真的做事**，你得從 renderer 內用你剛拿到的 code exec **發惡意 Mojo IPC 去攻破 Browser process**（sandbox escape），這是 [Ch 39](./39-renderer-mojo-sandbox-escape.md) 的地圖，也是 full chain 的第二節。

**這條差異是最根本的**：它不是「API 換一下」能補的，而是整整多了一節攻擊鏈。CTF 幫你把這節省掉（給 d8 或 no-sandbox 靶），所以你才會有「拿 shell = 打完了」的錯覺。移植到真 Chrome，「拿到 renderer code exec」的那一刻，你其實才走到 [Ch 1](./01-why-renderer-attack-surface.md) 三節模型的第一節終點。

## 動手驗證：d8 的作弊選單有多深

先在本課 d8 上真跑，親眼看到那些「真實 renderer 沒有」的道具確實存在：

```js
// /d/bpwnP7_d8caps.js
print("=== d8 專屬能力清單 ===");
print("print:        " + typeof print);        // renderer: 無
print("read:         " + typeof read);         // renderer: 無
print("readline:     " + typeof readline);     // renderer: 無
print("quit:         " + typeof quit);         // renderer: 無
print("os:           " + typeof os);           // renderer: 無
print("d8.file.read: " + typeof d8.file.read); // renderer: 無
```

在 `out/x64.release/d8` 上真跑（本課環境已驗證同型輸出）：

```
=== d8 專屬能力清單 ===
print:        function
read:         function
readline:     function
quit:         function
os:           object
d8.file.read: function
```

**每一個 `function` 在真實 renderer 裡都是 `undefined`**。把這張清單當成你的移植提醒：exploit 裡每用到一個，就是一筆「移植到真 Chrome 要改寫」的技術債。

`os.system` 要不要另開 flag、`--allow-natives-syntax` 對 natives 的門檻，你可以自己試（真跑，理論預期成功）：

```bash
# natives 要開 flag 才有；不開會 SyntaxError
~/v8build/v8/out/x64.release/d8 --allow-natives-syntax \
  -e 'function f(x){return x+1} %PrepareFunctionForOptimization(f); f(1); %OptimizeFunctionOnNextCall(f); f(1); print("optimized ok")'
```

## 對比表：d8 vs 真實 Chrome renderer

| 面向 | d8（CTF 靶） | 真實 Chrome renderer |
|---|---|---|
| 進程模型 | 單進程 | 多進程（renderer 是其一） |
| 沙盒 | 無 | seccomp + namespace |
| V8 Sandbox | 視 build（本課給兩顆） | 開 |
| pointer compression | 通常開 | 開 |
| DOM / Blink | 無 | 有（另一整片攻擊面） |
| Mojo IPC | 無 | 有（sandbox escape 主戰場） |
| natives（`%...`） | 常開 `--allow-natives-syntax` | 無 |
| I/O | `print`/`read`/`os.system` | DOM / `fetch`，無檔案/process |
| code exec 之後 | = 拿下進程（收工） | = full chain 第一節終點（人還在沙盒） |
| 觸發優化 | `%OptimizeFunctionOnNextCall` | 跑熱迴圈自然觸發 |

## 踩雷集錦

1. **以為 d8 = renderer**：d8 是剝光外殼、開了作弊選單的裸引擎。你的 V8 內部利用（addrof/fakeobj/RW）能平移，但一切 I/O、觸發方式、收尾、沙盒假設都不同。把 d8 當 renderer 是移植失敗的頭號原因。
2. **依賴 `%OptimizeFunctionOnNextCall` 卻沒準備替代**：真實 Chrome 沒有 natives。你的觸發碼如果建立在精準 natives 控制上，移植時整段要重寫成「熱迴圈自然優化」，而自然優化的時機沒那麼好掌控（涉及 tiering 門檻、OSR），這是移植最花時間的一段。
3. **收尾寫死 `os.system`/`execve`**：CTF 能用是因為 d8 無沙盒、seccomp 沒開。真實 renderer 這兩招直接死。你的 exploit「終局」該設計成「拿到穩定任意讀寫 + 控制流劫持」這個中間態，收尾（開 shell / escape）另計，而不是把 `system('/bin/sh')` 焊死在裡面。
4. **忽略 build flag 就跨 d8 打**：本課給你 sandbox on/off 兩顆 d8 就是要你體會這條線。在 nosbx 打通別以為 sandbox on 也通——backing store 指標被 external pointer table 包住那一刻，你的「控 raw pointer」路就斷了。真實 Chrome 站 sandbox on 這邊。
5. **照抄老 writeup 的每一步**：2019 的 exploit 的「任意讀寫 → WASM RWX → shellcode」在現代 Chrome 每一步都要換。前段拿原語思路可留，後段收尾（[Ch 32](./32-arbitrary-rw-to-code-exec.md)）幾乎每兩年換一套。移植 = 對照今天的 mitigation 重寫收尾，不是 copy-paste。
6. **忘了 renderer 還有 DOM/Blink 這片天**：d8 只有 V8。真實 renderer 裡 DOM/Blink 是另一整片攻擊面（本課刻意不打，但你要知道它存在）。反過來，你的 renderer exploit 想做 I/O 反而**必須**用 DOM/`fetch`，因為 d8 那套 `print` 不存在。

## 進階：再往深一層

- **怎麼在本地跑一個「真 renderer」來練移植**：你不必真的打線上 Chrome。可以自己 build 一個 `content_shell`（Chromium 的最小化 renderer 宿主，帶 Blink + 多進程 + 沙盒，但沒有完整 Chrome UI），或用 `--no-sandbox` flag 起一個 renderer 逐步加回沙盒。這是把 d8 exploit 往真 Chrome 移植的最實際練習台。**未實測，理論預期**；自驗需 build Chromium（比 build V8 重得多，數十 GB、數小時），或用官方 `content_shell` 二進位。
- **`--allow-natives-syntax` 之外的觸發控制**：真實環境沒 natives，但你仍能相當程度控制優化：跑固定次數的熱迴圈把函式推過 tiering 門檻、用 `Function.prototype` 技巧、觀察 `performance.now()` 判斷是否已優化。saelo/doar-e 的 real-world writeup 常示範「不用 natives 也能穩定觸發優化」的寫法，值得專門研究。
- **DOM 當 I/O 通道與 heap grooming 工具**：真實 renderer 裡，DOM 物件不只是「另一片攻擊面」，也是你 exploit 的**工具**——用大量 DOM 物件做 heap spray/grooming（[Ch 13](./13-garbage-collection.md) 的 GC 佈局在真實 heap 上更複雜），用 `document`/`fetch` 當輸出。學會把 DOM 當工具，是 d8 選手升級到 renderer 選手的一道坎。
- **不同宿主的 V8：Node.js / Electron / Workers**：[Ch 1](./01-why-renderer-attack-surface.md) 提過 V8 也嵌在 Node、Electron、Cloudflare Workers。這些宿主的「d8 vs renderer」光譜各不同：Node 常常**沒有 renderer 沙盒**（一個 V8 RCE 破壞力反而更大），Electron 介於中間。你這門課的 V8 內部功力對這些宿主同樣值錢，且移植清單更短（少了沙盒那節）。

## 動手練習

1. **作弊道具盤點**：拿你自己寫過的一份 CTF V8 exploit，逐行標出所有「d8 專屬」的呼叫（`print`/`read`/`os.system`/`%natives`）。數一數有幾筆。每一筆寫下「移植到真 Chrome 要換成什麼」。這份清單就是你的移植技術債。
2. **去 natives 化**：把一段依賴 `%OptimizeFunctionOnNextCall` 的觸發碼，改寫成「跑十萬次熱迴圈自然觸發優化」，用 `--trace-opt`（[Ch 2](./02-v8-architecture.md)）確認函式確實升到了 Maglev/TurboFan，且升級時機在你要的位置。體會「沒有 natives 之後控制優化有多微妙」。
3. **雙 d8 撞牆**：在 `out/x64.release.nosbx/d8` 上做一個「控 ArrayBuffer backing store 指標 → 寫任意進程位址」的小實驗（對照 [Ch 17](./17-typedarray-attack.md)），再把同一段搬到 `out/x64.release/d8`（sandbox on）跑，觀察它怎麼死。這就是「移植到真 Chrome」的縮小版預演。
4. **查你機器上 Chrome 的 V8 版本**：開 `chrome://version`，找到 V8 版本號，對照本課的 15.3.0。想一想：一篇針對某個更舊版本的公開 exploit，搬到你這台的 Chrome 上，哪些 mitigation 是新出現、會擋掉它的？

## 本章重點整理

- **d8 是 renderer 的極簡替身**：剝掉 DOM/多進程/沙盒/Mojo，加上 `print`/`read`/`os.system`/natives 一堆真實世界不存在的作弊道具。以為 d8 = renderer 是移植失敗頭號原因。
- **四類差異**：build flag（sandbox/pointer compression/natives）、mitigation 密度（老 exploit 每兩年收尾就過時）、可用 API（d8 道具 renderer 全無）、進程/沙盒邊界（renderer code exec 只是 full chain 第一節終點）。
- **移植清單核心兩條**：拿掉所有 `%natives` 改成熱迴圈自然觸發；重寫 I/O 與收尾（不能 `print`/`os.system`，改用 DOM/`fetch`，終局是發 Mojo 打 Browser 而非開 shell）。
- **本課雙 d8 就是這條線的教材**：nosbx 對齊老 CTF 靶，sandbox on 對齊真實 Chrome。親手在兩顆之間撞牆，勝過移植到真 Chrome 才發現。
- **拿到 renderer code exec ≠ 打完**：真實世界你才走到第一節終點，前面還有 sandbox escape（Ch 39）+ kernel LPE（kernel_pwn）。

## 自我檢核

- [ ] 能列出 d8 有、真實 renderer 沒有的至少五個能力，並說出各自的替代方案
- [ ] 能解釋 `v8_enable_sandbox` 這個 build flag 為什麼讓「控 backing store raw pointer」的老路失效
- [ ] 能講清楚「移植 = 對照今天的 mitigation 重寫收尾」，前段拿原語為何能留、後段為何要換
- [ ] 知道為什麼真實環境要把 `%OptimizeFunctionOnNextCall` 改成熱迴圈自然觸發，以及這帶來什麼麻煩
- [ ] 能說出「renderer code exec」在 [Ch 1](./01-why-renderer-attack-surface.md) 三節模型裡的確切位置（第一節終點，非終局）
- [ ] 面試被問「d8 exploit 和真實 Chrome exploit 差在哪」，能分四類講清楚

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

- **[V8 build 文件（GN args / d8）— v8.dev/docs/build 與 v8.dev/docs/d8](https://v8.dev/docs/build)**
  - **這篇說什麼**：d8 怎麼 build、有哪些 GN args、d8 shell 有哪些 API。
  - **讀哪裡**：GN args 列表（對照本章「差異一」表），以及 d8 內建函式清單（對照「差異三」）。
  - **和本章的關聯**：本章 build flag 與 d8 專屬 API 兩節的權威依據。

- **[Chromium content_shell 與 headless 文件 — chromium.googlesource.com/chromium/src/+/main/content/shell](https://chromium.googlesource.com/chromium/src/+/main/content/shell/)**
  - **這篇說什麼**：content_shell 是帶 Blink + 多進程 + 沙盒的最小 renderer 宿主，比完整 Chrome 輕、比 d8 真。
  - **為什麼值得讀**：這是你「在本地練 d8→renderer 移植」最實際的靶。進階練習 1 的環境依據。

- **[Project Zero：真實 Chrome exploit 分析（含「拿到 renderer RCE 之後怎麼做」）— googleprojectzero.blogspot.com](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實 exploit 怎麼在**沒有 d8 作弊道具**的 renderer 裡做觸發、做 I/O、接下一節。
  - **讀哪裡**：挑一篇 Chrome renderer exploit，專看它「怎麼不用 natives 觸發優化」「怎麼用 Web API 當通道」——這正是你移植要補的。

- **[saelo / doar-e 的 real-world V8 exploit — saelo.github.io / doar-e.github.io](https://saelo.github.io/)**
  - **這篇說什麼**：把 CTF 級技巧升級到真實環境的示範，含「去 natives 化觸發」「現代 mitigation 下的收尾」。
  - **和本章的關聯**：本章「移植清單」的活範例，讀完你會知道那份技術債實際怎麼還。

你現在知道：拿到 renderer code exec 只是第一節終點，人還在沙盒裡。要真正逃出去，得從 renderer 內發惡意 Mojo IPC 去打 Browser process。下一章給你 sandbox escape 的全景地圖與入口——不逐行實作（那是另一門課的份量），但把「往哪走、讀什麼」指清楚，並接上你的 `kernel_pwn` 完成 full chain。

→ [Ch 39 — renderer 之後：Mojo IPC / site isolation / sandbox escape 全景](./39-renderer-mojo-sandbox-escape.md)
