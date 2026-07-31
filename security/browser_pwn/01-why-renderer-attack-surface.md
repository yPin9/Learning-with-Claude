# Ch 1 — 為什麼 renderer 是攻擊面：Chrome 多進程與 sandbox 全景

> **目標**：搞懂你在這門課裡「打的到底是什麼」。理解 Chrome 的多進程架構、renderer 為什麼是 client-side RCE 的黃金入口、你在 V8 裡拿到 code execution 之後**還沒到終點**、以及這門課在整條攻擊鏈裡負責哪一段。這章不寫 code，是幫你把後面 40 章掛上正確的座標系。

> **本章為概念章**，不釘 V8 版本；下一章開始回到 d8 實作。

## 為什麼需要這個？

新手打 V8 CTF 題時常有一個誤會：以為「在 d8 裡跑起 `execve("/bin/sh")` 就是把瀏覽器打下來了」。

不是。你在 CTF 拿到的那個 shell，在真實世界裡是**被關在一個沙盒進程裡的 shell**——它連使用者的檔案都讀不到，更別說控制整台電腦。CTF 把靶子簡化成一個 `d8` 或一個 no-sandbox 的 renderer，讓你專心練 V8 內部的利用；但真實的 Chrome exploit 是一條**鏈**，V8 RCE 只是第一節車廂。

你如果不先搞懂這個全景，會犯兩種錯：**高估**（以為 V8 RCE = 拿下電腦，其實還早）或**低估**（以為要學的東西無窮無盡而放棄，其實這門課只負責定義清楚的一段）。這章先把地圖畫出來。

## 先建立直覺：城堡、房間、與上鎖的門

把 Chrome 想成一座城堡，裡面分成好幾個**房間**（進程），房間之間有**上鎖的門**（進程邊界 + 沙盒）：

```
        外面的世界（任何網頁 = 攻擊者可控輸入）
                        │
                        ▼
   ┌───────────────────────────────────────────────────────────┐
   │  Chrome 城堡（多進程）                                       │
   │                                                             │
   │   ┌──────────────┐   上鎖的門   ┌───────────────────────┐  │
   │   │ Renderer 房間 │◄────沙盒────►│  Browser 房間（管家）  │  │
   │   │  (不受信任)   │   IPC/Mojo   │   (高權限，可存檔、    │  │
   │   │              │              │    可開網路、管一切)   │  │
   │   │  ★ V8 在這 ★ │              └───────────────────────┘  │
   │   │  Blink DOM   │                                          │
   │   │  WebAssembly │   ┌──────────┐  ┌──────────┐            │
   │   └──────────────┘   │ GPU 房間 │  │ Network  │            │
   │                      └──────────┘  │  房間    │            │
   │                                    └──────────┘            │
   └───────────────────────────────────────────────────────────┘
                        │
                        ▼（Browser 房間再往下）
              作業系統核心（kernel）── 你的 kernel_pwn 課接這裡
```

攻擊者從「外面的世界」進來：他控制一個網頁，網頁裡的 JavaScript 在 **Renderer 房間**裡執行。Renderer 是**不受信任**的房間——它預設就假設「這裡面跑的東西可能是惡意的」，所以被沙盒鎖住，能碰的系統資源被砍到最少。

**V8 就住在 Renderer 房間裡**。你這門課做的事，是：攻擊者用一段 JS 餵給 V8，利用 V8 的漏洞，在 **Renderer 房間內**拿到任意記憶體讀寫、進而 code execution。

但拿到 Renderer 內的 code execution，你人還在那個上鎖的房間裡。要真的控制使用者的電腦，還得：**打開通往 Browser 房間的門（sandbox escape）**，再從高權限的 Browser 房間**往下鑽進 kernel（LPE，你的 `kernel_pwn` 課）**。這就是「full chain」。

## Chrome 的多進程架構：誰有什麼權限

Chrome（以及所有現代瀏覽器）刻意把自己拆成多個進程，核心動機是**用進程邊界當安全邊界**。各進程的角色與權限：

| 進程 | 信任等級 | 能做什麼 | 跑什麼 |
|---|---|---|---|
| **Browser process** | 高（受信任） | 存取檔案系統、發起網路、管理其他進程、畫視窗框 | Chrome 主邏輯、UI |
| **Renderer process** | **零（完全不信任）** | 幾乎什麼系統資源都碰不到，靠 IPC 求 Browser 代勞 | **V8**、Blink（DOM/HTML/CSS）、WebAssembly |
| **GPU process** | 中 | 存取 GPU 驅動 | 繪圖、合成 |
| **Network process** | 中 | 網路 I/O | HTTP stack、cookie |
| **Utility processes** | 低 | 各種隔離的小工作（音訊、解碼…） | 依需求 |

關鍵設計：**Renderer 處理最危險的東西（來自任意網站、攻擊者完全可控的內容），所以給它最低的權限。** 它想讀一個檔、想連一個網路位址，都不能自己來，必須透過 **IPC（Chrome 用的機制叫 Mojo）** 去請 Browser process 代辦——而 Browser process 會檢查這個請求合不合法。

這就是為什麼「Renderer 內 RCE」和「拿下電腦」中間隔著一道牆：你控制了 Renderer，但 Renderer 本身就是個窮光蛋，你得說服（攻破）Browser process 才能升級權限。

## Renderer 沙盒到底鎖了什麼

「沙盒」不是一個抽象概念，是實打實的作業系統機制。在 Linux 上，Chrome renderer 沙盒主要靠兩層（你在 `binary_exploitation` 的 seccomp 章和 `kernel_pwn` 都見過這些原語）：

1. **seccomp-bpf**：用一個 BPF 過濾器把 renderer 能呼叫的 syscall 砍到剩一小撮。`open`、`connect`、`execve` 這類「能碰外部世界」的 syscall 大多被擋——你在 renderer 裡就算有了 code execution，直接 `execve("/bin/sh")` 也可能被 seccomp 擋下（這也是為什麼 real-world exploit 不能照抄 CTF 的 `system("/bin/sh")`）。
2. **namespace / setuid 沙盒**：把 renderer 丟進獨立的 user/PID/network namespace，讓它看不到、碰不到主系統的資源。

> 如果你對 seccomp-bpf 的過濾器怎麼運作還沒把握，先回看 `security/binary_exploitation` 的 seccomp 章，或 `systems/bpf` 課——這裡的沙盒本質就是那套 BPF 過濾。

所以「在 renderer 裡拿到 shellcode 執行」的意義是：**你能在沙盒進程的位址空間裡跑任意 code**——這已經很強（你能讀寫這個進程的所有記憶體、能發 Mojo IPC 去戳 Browser），但你**還沒逃出沙盒**。

## Site Isolation：連「偷別的網站」都要跨進程

早期 Chrome 一個 renderer 可能同時跑多個網站的內容。2018 年 Spectre/Meltdown（CPU 側通道漏洞）之後，Google 全面推 **Site Isolation**：**每個站台（site）用獨立的 renderer 進程**。

這對攻擊者的意義：就算你在某個 renderer 裡拿到任意讀寫，你能偷的也「只是」當前這個站台的資料——想偷別的站台（例如同時開著的網銀分頁）的記憶體，那個站台在另一個進程裡，你還是得先 sandbox escape。Site Isolation 把「一個 renderer bug 能污染到多廣」這件事夾得更緊。

這也解釋了為什麼現代瀏覽器 exploit 越來越貴、越來越要「full chain」：光一個 V8 bug 的破壞半徑被架構層層限制住了。

## 為什麼偏偏是 V8（而不是 renderer 裡別的東西）

Renderer 房間裡不只 V8，還有 Blink（處理 DOM、HTML、CSS 的引擎）、WebAssembly、各種 Web API。它們都是攻擊面。但這門課專打 V8，理由很硬：

1. **V8 執行的是圖靈完備、攻擊者完全可控的輸入**。網頁的 JavaScript 是攻擊者一個字一個字寫的；V8 要把這段任意程式碼**即時編譯成機器碼並執行**（JIT）。「把不受信任的輸入編譯成 CPU 直接跑的機器碼」——沒有比這更肥的攻擊面了。
2. **JIT 編譯器的正確性極難保證**。TurboFan（V8 的優化編譯器）為了快，會對你的 JS 做大量「假設」（這個變數一定是整數、這個陣列長度不會變…）然後基於假設省略檢查。只要有一個假設能被攻擊者打破，就可能變成型別混淆（type confusion）→ 越界 → 任意讀寫。這是 2016 年後瀏覽器 0-day 的**主礦脈**，也是本課 Part 4 的重頭戲。
3. **V8 的 bug 通常給你非常乾淨的原語**。相比 DOM 的 use-after-free（常常又髒又難穩定），一個好的 V8 type confusion 往往能直接做出穩定的 `addrof`/`fakeobj`（Part 3 的兩把鑰匙），利用起來優雅得多。這也是 CTF 偏愛 V8 題的原因。

Blink 的 DOM UAF、WebAssembly 的漏洞同樣能打，但屬於不同的技能樹。本課聚焦 JS 引擎本身，DOM/Blink 只在攻擊面層次帶過（見「刻意不涵蓋」）。

## 歷史演進：瀏覽器攻擊面怎麼走到「JIT type confusion」這一步

理解「為什麼現在大家都在打 JIT」，要看這條路是怎麼被逼出來的：

- **外掛時代（~2010 前）**：Flash、Java applet、ActiveX 是主戰場。它們是塞進瀏覽器的第三方執行環境，漏洞多如牛毛。廠商的對策是**乾脆把外掛砍掉**——Flash 2020 年正式死亡。攻擊面直接消失一大塊。
- **DOM UAF 時代（~2010s 中）**：外掛沒了，火力轉向瀏覽器自己的 C++ 程式碼，尤其 DOM。物件生命週期管理複雜，use-after-free 滿地都是。廠商對策：沙盒（把 renderer 關起來，UAF 也逃不出去）、更好的記憶體管理、後來的 MiraclePtr/類型隔離 allocator。
- **JIT type confusion 時代（2016+，至今）**：DOM 被逐漸加固後，注意力移到 JS 引擎的 JIT。JIT 的「為了快而做假設」本質上和「安全需要保守檢查」衝突，成為最肥的礦。V8 的 TurboFan、JSC 的 DFG/FTL、SpiderMonkey 的 IonMonkey 都出過大量這類 0-day。廠商對策：JIT 加固、後來的 **V8 Sandbox（ubercage）**——不阻止你觸發 bug，而是限制「拿到任意讀寫之後能造成的傷害」（Ch 34 詳談）。

看懂這條線，你會發現一個規律：**每一層防禦不是消滅攻擊，而是把攻擊逼進下一個更窄、更難的角落**。這和你在 `binary_exploitation` 看到的 NX → ASLR → Canary → CET 是同一個劇本，只是換到瀏覽器的尺度。

## 完整攻擊鏈：V8 RCE 只是第一節

把一個現代 Chrome 0-day 完整攤開，通常長這樣：

```
  [1] Renderer RCE
      攻擊者網頁的 JS → V8 漏洞 → 任意讀寫 → renderer 內 code execution
      ★★★ 這門課 (Ch 3–37) 教的就是這一節 ★★★
                    │
                    ▼
  [2] Sandbox Escape
      在 renderer 內發惡意 Mojo IPC → 攻破 Browser process 的漏洞
      → 逃出 seccomp/namespace 沙盒，拿到高權限進程的 code execution
      （本課 Ch 39 只做全景導覽，不逐行實作——那是另一門課的份量）
                    │
                    ▼
  [3] Kernel LPE / 持久化
      從 Browser process 打 kernel 漏洞 → ring0 → 完全控制
      （這一節就是你的 security/kernel_pwn 課）
```

這門課的定位很明確：**專精第 [1] 節，把它做到極致**。第 [2] 節在 Ch 39 給你地圖和入口，第 [3] 節你已經有 `kernel_pwn` 打底。三門課接起來，就是一條完整的 client-to-kernel chain 的知識版圖。

CTF 的 V8 pwn 題 99% 只考第 [1] 節，而且常常連 sandbox 都幫你關掉（給你 no-sandbox 的 d8 或 patched Chrome），所以你在 CTF 練到的「拿 shell」其實就是「在 renderer 內 code execution」。搞清楚這點，你就不會在真實世界的 exploit 前困惑「為什麼我的 `system('/bin/sh')` 被擋了」。

## 對比：CTF 的 V8 題 vs 真實世界的 Chrome exploit

| 面向 | CTF V8 題 | 真實 Chrome 0-day |
|---|---|---|
| 靶子 | `d8` 或 no-sandbox renderer | 完整 Chrome（sandbox 全開） |
| 漏洞 | 出題者植入或簡化的 bug | 自己 fuzzing/patch-diff 挖到的真 bug |
| 目標 | renderer 內 code execution（拿 flag） | full chain：RCE → sandbox escape → LPE |
| seccomp | 通常關掉，可直接 `execve` | 開著，得繞或走 Mojo |
| 穩定性要求 | 打通一次就好 | 要高可靠、不能 crash 目標 |
| 本課涵蓋 | **全部** | **只到第 [1] 節（+ Ch 39 導覽後續）** |

這張表釘死了你的預期：學完這門課，你能穩定解 CTF 的 V8 題、能讀懂真實 0-day 的第一節、能自己找到 renderer 漏洞——但「把一個 full-chain 0-day 從頭做到控制整台電腦」需要再接 sandbox escape 和 kernel 兩門功課。

## 踩雷集錦

1. **以為 renderer code execution = 拿下電腦**：錯。你人還在沙盒進程裡，能碰的系統資源被 seccomp/namespace 砍光。真實世界還需要 sandbox escape + LPE。CTF 因為幫你關了沙盒，容易養成這個錯覺。
2. **把 CTF 的 `system("/bin/sh")` 當通用終局**：真實 renderer 沙盒下 `execve` 被 seccomp 擋，這招直接失效。CTF 能用是因為靶子刻意放行。真實 exploit 的「終局」是拿到任意讀寫 + 控制流，然後去打下一節，不是開 shell。
3. **以為 V8 是 renderer 裡唯一攻擊面**：Blink DOM、WebAssembly、各種 Web API 都能打。V8 只是**最肥、最乾淨**的那塊，不是唯一。本課專打 V8 是策略選擇，不是因為別的不能打。
4. **忽略 build/sandbox 設定就照抄 exploit**：接續上一章的紀律——真實 exploit 和 CTF exploit 對「sandbox 開沒開」的假設完全不同，位址佈局、能用的 syscall 都不一樣。

## 進階：再往深一層

- **Mojo IPC 是 sandbox escape 的主戰場**：Renderer 求 Browser 代勞的所有請求都走 Mojo。Mojo 介面的實作 bug（type confusion、UAF、權限檢查缺失）就是第 [2] 節的入口。Ch 39 會給你讀 Mojo 攻擊面的起手式。
- **不同瀏覽器的架構大同小異**：Firefox（多進程 + 較新的 sandbox）、Safari（WebContent 進程 + JavaScriptCore）也是同樣的「不受信任 renderer + 受信任 broker」模型。本課的架構直覺可平移，只是引擎內部（JSC vs V8）不同。
- **CPU 側通道（Spectre）改變了架構**：Site Isolation 之所以存在，是因為 Spectre 讓「同進程內讀別的站台記憶體」變可能。這是硬體漏洞倒逼軟體架構的經典案例，值得單獨了解。
- **V8 也被用在瀏覽器之外**：Node.js、Deno、Cloudflare Workers、Electron 都嵌 V8。你在這門課學的 V8 內部與利用，對這些 server-side/桌面環境同樣適用——而且它們常常**沒有 renderer 沙盒**，一個 V8 RCE 的破壞力反而更大。這是 V8 pwn 技能的隱藏價值。

## 動手練習

1. 打開你電腦上的 Chrome，進 `chrome://process-internals/` 或用工作管理員看 Chrome 的進程列表。開幾個不同網站的分頁，觀察進程數量怎麼變（Site Isolation 的效果）。想一想：哪個是 Browser process、哪些是 renderer？
2. 不看本章，用自己的話畫一張圖：一段惡意 JS 從「網頁」到「控制整台電腦」要經過哪幾道邊界，每道邊界的名字是什麼、這門課負責哪一段。
3. 查一則真實的 Chrome full-chain 報告（例如 Pwn2Own 的 writeup 或 Project Zero 的 in-the-wild 分析），找出它的三節分別利用了什麼元件。先不用看懂細節，只要能指認「這是第 [1] 節、這是 sandbox escape、這是 LPE」。

## 本章重點整理

- Chrome 用**多進程 + 沙盒**把「不受信任的 renderer」和「高權限的 Browser」隔開；進程邊界就是安全邊界。
- **V8 住在 renderer**，處理攻擊者完全可控的 JS，JIT 把它編成機器碼——這是最肥的攻擊面。
- 在 V8 拿到 code execution **只是 full chain 的第一節**，人還在沙盒裡；真實世界還需 sandbox escape（Ch 39 導覽）+ kernel LPE（`kernel_pwn` 課）。
- 現代瀏覽器攻擊的主礦脈是 **JIT type confusion**，這是防禦把攻擊逼進的最新角落。

## 自我檢核

- [ ] 能解釋為什麼 renderer 被給「零信任、最低權限」，而 Browser process 高權限
- [ ] 能說出「在 d8 裡拿 shell」和「打下一台裝了 Chrome 的電腦」差在哪幾道牆
- [ ] 能講清楚為什麼 JIT（TurboFan）是特別肥的攻擊面，而不只是「V8 有 bug」
- [ ] 知道這門課負責 full chain 的哪一節、哪些留給 Ch 39 和 `kernel_pwn`
- [ ] 面試被問「瀏覽器 exploit 為什麼要 full chain」，能用多進程+沙盒的架構回答

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

### 官方文件 / 設計文件

- **[Chromium 多進程架構 — chromium.org: Multi-process Architecture](https://www.chromium.org/developers/design-documents/multi-process-architecture/)**
  - **讀哪裡**：整篇，尤其進程角色與 Site Isolation 段落。本章那張「城堡與房間」圖就是它的白話版。
  - **和本章的關聯**：把各進程權限、IPC 邊界講得比本章更細，是理解「為什麼要 sandbox escape」的第一手依據。

- **[Chromium Sandbox 設計文件 — chromium.org: Sandbox](https://chromium.googlesource.com/chromium/src/+/main/docs/design/sandbox.md)** 及 Linux 的 [seccomp-bpf sandbox](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/sandboxing.md)
  - **讀哪裡**：Linux sandboxing 文件的 seccomp-bpf 段落。這解釋了為什麼 renderer 裡 `execve` 會被擋。
  - **前提**：先懂 seccomp-bpf 基本概念（`binary_exploitation` seccomp 章 / `systems/bpf`）。

### 部落格 / 技術文章

- **[Project Zero: in-the-wild 系列與 Chrome exploit 分析 — googleprojectzero.blogspot.com](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實被用於攻擊的 Chrome full-chain 逆向。看它們怎麼把 renderer RCE、sandbox escape、LPE 串起來。
  - **讀哪裡**：先挑一篇有「full chain」字樣的，只讀架構概述段，對照本章的三節模型。細節看不懂正常，那是後面 40 章的事。
  - **為什麼值得讀**：這門課的終極讀者能力，就是讀懂這裡的文章。現在先建立「這是第幾節」的分辨力。

- **[“A Tale of Two Pwnies” / Chrome security team 的 Site Isolation 說明 — security.googleblog.com](https://security.googleblog.com/2018/07/mitigating-spectre-with-site-isolation.html)**
  - **這篇說什麼**：Site Isolation 為什麼因 Spectre 而生、擋住了什麼。
  - **和本章的關聯**：解釋本章「連偷別的網站都要跨進程」那節背後的硬體漏洞動機。

### 書籍 / 進階

- **《The Browser Hacker's Handbook》** — Alcorn et al.（Wiley）
  - **這本書的定位**：偏廣度，涵蓋瀏覽器整體攻擊面（不只記憶體漏洞）。適合本章「全景」層次的補充，不是 V8 內部的深入。
  - **注意**：出版較早，具體技術細節（尤其 JIT、sandbox）已過時，讀它的**架構與攻擊面分類**，不要讀它的 exploit 細節。

有了全景，我們回到 V8 內部。下一章拉開引擎的引擎蓋：一段 JS 從原始碼到執行，在 V8 裡走過 Parser、Ignition、TurboFan、GC 這幾個階段，各自在幹嘛、各自藏著什麼攻擊面。

→ [Ch 2 — V8 架構全圖：Parser → Ignition → TurboFan → GC](./02-v8-architecture.md)
