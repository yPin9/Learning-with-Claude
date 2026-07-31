# Ch 39 — renderer 之後：Mojo IPC / site isolation / sandbox escape 全景

> **目標**：給你 full chain 第二節（sandbox escape）的**概念地圖與入口**，而不是逐行實作——那是另一門課的份量。你會建立 Mojo IPC 的全景（renderer 怎麼求 Browser 代勞、interface 攻擊面長什麼樣）、搞懂 site isolation 對攻擊者的實際約束、認清 sandbox escape 的幾種主要漏洞形態，並知道去哪裡讀、讀什麼。最後把這一節接上 [Ch 1](./01-why-renderer-attack-surface.md) 的三節模型，並指路到 `security/kernel_pwn` 完成第三節（LPE），讓你手上有一張完整的 client-to-kernel 版圖。

> **本章為地圖章**：不釘 V8 版本、不逐行實作 Mojo exploit。Mojo/sandbox escape 的具體行為皆屬「未實測，理論預期」，本章給你**入口與所需環境**，讓你自己往下走。d8 沒有 Mojo（[Ch 38](./38-d8-vs-real-chrome.md)），本章的靶天然是真實 Chrome / content_shell。

## 為什麼需要這個？

你走到這裡：一段 JS 餵進 V8，type confusion → 任意讀寫 → renderer 內 code execution。[Ch 38](./38-d8-vs-real-chrome.md) 剛把「這只是 full chain 第一節終點」講死——你人還在沙盒進程裡，seccomp 擋著 `execve`，namespace 讓你看不到系統。那接下來呢？

接下來是**sandbox escape**：從 renderer 內，用你剛拿到的 code exec，發**惡意的 Mojo IPC** 去攻破高權限的 Browser process。這是一門和 V8 pwn **技能樹不同**的功課——它打的不是 JIT 編譯器，是 Chrome 的 C++ IPC 層與各種 broker service。本課的定位（[Ch 1](./01-why-renderer-attack-surface.md) 講明）是專精第一節，所以這章**不教你做一個 Mojo exploit**，而是給你一張地圖：這節的地形長怎樣、有哪幾種洞、你該讀哪些一手資料、需要什麼環境才能自己往下練。把它當成「V8 課的畢業導覽」，不是「Mojo 課的第一章」。

## 先建立直覺：窮光蛋 renderer 怎麼求管家做事

回到 [Ch 1](./01-why-renderer-attack-surface.md) 的城堡比喻。renderer 是零信任、被沙盒鎖住的窮房間，它自己**碰不到檔案、網路、GPU**。但網頁明明能存檔、能連網、能畫 WebGL——這些事全是 renderer **透過 IPC 求 Browser process（管家）代勞**的。Chrome 用的 IPC 機制叫 **Mojo**。

```
   ┌────────────────────────┐                      ┌──────────────────────────┐
   │  Renderer（零信任）      │                      │  Browser process（高權限）│
   │                        │   Mojo message pipe   │                          │
   │  「幫我開這個檔」  ──────┼──────────────────────►│  FileSystem service      │
   │  「幫我連這個網址」──────┼──────────────────────►│  Network service          │
   │  「幫我配置 GPU 資源」────┼──────────────────────►│  GPU service              │
   │                        │                      │  ...幾百個 Mojo interface │
   │  ★你在這拿到 code exec  │                      │  ★攻擊面在這★             │
   └────────────────────────┘                      └──────────────────────────┘
              不受信任的一端                              受信任、會「相信」訊息的一端
```

關鍵洞察：**Browser process 這一端，多多少少要「相信」renderer 傳來的 Mojo 訊息**。它當然會做檢查，但只要有一個 Mojo interface 的實作**檢查不足、或有記憶體 bug**，你這個已經在 renderer 裡拿到 code exec 的攻擊者，就能發一連串精心構造的惡意訊息去攻破 Browser process——逃出沙盒。這就是 sandbox escape 的本質：**濫用「不受信任的一端可以對受信任的一端說話」這個必要的通道**。

## Mojo IPC 全景：你要認得的幾個名詞

要讀懂任何 sandbox escape writeup，先建立這幾個 Mojo 名詞的粗略印象（細節去讀官方 Mojo 文件）：

- **Message pipe**：兩端之間的雙向通道，訊息在上面來回。renderer 和 Browser 之間有一大堆這種 pipe。
- **Interface（`.mojom`）**：Mojo 用一種 IDL（介面描述語言，副檔名 `.mojom`）定義「這條 pipe 上能傳什麼方法、什麼參數」。例如某個 `FileSystem` interface 定義了 `Open(path) -> (file)`。**這些 `.mojom` 定義就是攻擊面的清單**——每一個 renderer 能呼叫的 Browser 端方法，都是一個潛在入口。
- **Remote / Receiver**：一端持有 `Remote`（發起呼叫），另一端是 `Receiver`（實作方法、處理訊息）。sandbox escape 打的是 Browser 端那個 `Receiver` 的 C++ 實作。
- **Interface broker**：renderer 想拿到某個 Browser 端 interface 的 `Remote`，要透過 broker 申請。**「哪些 interface 暴露給 renderer」是一條重要的權限邊界**——縮小這個暴露面本身就是防禦。

所以攻擊者的視角是：我在 renderer 裡有 code exec，我能拿到一堆 Mojo `Remote`，我對著這些 interface 發訊息，尋找 Browser 端 `Receiver` 實作裡的 bug。**這和 V8 pwn 找 TurboFan bug 是完全不同的獵場**——你在讀 Chrome 的 C++ service 程式碼，找 UAF、type confusion、整數溢位、權限檢查缺失。

## Sandbox escape 的主要漏洞形態

sandbox escape 的洞，大類上和你在 `binary_exploitation` 打的 C/C++ bug 是同一家族，只是介面是 Mojo：

1. **Mojo interface 實作的記憶體 bug**：Browser 端某個 `Receiver` 處理訊息時有 UAF / OOB / type confusion。renderer 發惡意參數觸發，拿到 Browser process 內的記憶體破壞。這是最主流的一類。
2. **權限檢查缺失 / 混淆**：某個本不該暴露給 renderer 的 interface 被暴露了，或某個方法沒檢查呼叫者的權限（例如沒驗證 renderer 對應的 site，繞過 site isolation）。這類是**邏輯洞**，不一定要記憶體破壞。
3. **共享記憶體 / handle 濫用**：Mojo 能傳 shared memory buffer 和 OS handle。對這些的處理不當（double-free 一個 handle、把可控大小的 shared buffer 當成固定大小）也是入口。
4. **GPU / 其他 broker process 的洞**：sandbox escape 不一定直接打 Browser。GPU process 權限中等，有時是更軟的中繼跳板：renderer → GPU process（也走類 Mojo/command buffer）→ 再往上。

這四類的共同前提：**你已經在 renderer 裡有 code exec**，能任意構造 Mojo 訊息、能讀寫 renderer 記憶體來準備 payload。這就是為什麼 V8 RCE（本課）是 full chain 的**地基**——沒有它，你連發惡意 Mojo 的資格都沒有。

## Site Isolation：不只是防禦，也是對你的約束

[Ch 1](./01-why-renderer-attack-surface.md) 講過 site isolation 因 Spectre 而生：**每個 site 用獨立 renderer 進程**。對 sandbox escape 的攻擊者，它有兩層意義：

- **約束**：你在某個 site 的 renderer 裡拿到任意讀寫，你能偷的只有這個 site 的資料。想偷別的 site（另一個進程），得先 escape 到 Browser 再說。site isolation 把單一 renderer bug 的破壞半徑夾得很緊。
- **也是攻擊目標**：有些 sandbox escape / 資訊洩漏 bug 的本質就是**繞過 site isolation 的隔離**——例如某個 Mojo 方法沒正確驗證呼叫端的 site，讓 A site 的 renderer 拿到 B site 的資料或能力。這類「site isolation bypass」是一個獨立且熱門的 bug 類別。

理解這條：site isolation 讓「拿到一個 renderer」的價值下降，所以現代 exploit 越來越需要 full chain——這解釋了為什麼廠商肯砸資源做它，也解釋了為什麼你這門 V8 課「只到第一節」在真實世界必須接後面兩節才完整。

## 接上 kernel_pwn：full chain 的第三節

sandbox escape 讓你從 renderer 逃到 **Browser process**——一個高權限進程，能存取檔案、網路、開 process。到這裡，很多攻擊目標（竊資料、持久化）已經達成。但要「完全控制整台電腦」（ring0、繞過 OS 級防護、對抗 EDR），還要第三節：**從 Browser process 打 kernel 漏洞做 LPE（本機提權）**。

```
  [1] Renderer RCE   ← 本課 Ch 3–37（V8 type confusion → 任意讀寫 → code exec）
        │
        ▼
  [2] Sandbox Escape ← 本章給地圖（renderer → 惡意 Mojo → Browser process）
        │
        ▼
  [3] Kernel LPE     ← 你的 security/kernel_pwn 課（Browser → kernel bug → ring0）
```

**第三節你已經有地基**：`security/kernel_pwn` 教的正是「從一個使用者態進程打 kernel 漏洞拿 ring0」——那個「使用者態進程」在這裡就是你 escape 到的 Browser process。你在 kernel_pwn 學的現代 heap 技術（cross-cache、dirty pagetable、USMA）、kernelCTF 那套，直接接上這裡。三門課（`binary_exploitation` 地基 → 本課 renderer → `kernel_pwn` LPE）串起來，就是一條完整、真實的 client-to-kernel 攻擊鏈的知識版圖。

**這也是本課全程反覆強調「你只負責第一節」的收束點**：不是因為第二三節不重要，而是它們各自是一門課的份量，硬塞進來只會兩邊都學不深。你現在有的是「知道全景、知道自己站哪、知道往哪走」——這比「每節都學一半」值錢得多。

## 從這裡往下走：入口與所需環境

如果你真要往 sandbox escape 走（本課不要求，但給你入口），需要的東西和 V8 pwn 差很多：

- **環境**：不能用 d8（它沒有 Mojo，[Ch 38](./38-d8-vs-real-chrome.md)）。你需要 build 完整 Chromium 或至少 `content_shell`（帶多進程 + Mojo + 沙盒）。這比 build V8 重得多——磁碟數十 GB、編譯數小時，且要熟 `content/`、`services/`、`ipc/` 這幾片 Chromium 原始碼。
- **一手材料**：Mojo 官方文件（`//mojo/README.md`）、Chromium IPC security 指南、`.mojom` 檔（攻擊面清單）。以及**讀真實 sandbox escape 的 P0 / Pwn2Own writeup**——這是最快建立形態感的路。
- **心態**：這是「Chrome C++ 逆向 + IPC fuzzing」的世界，不是「JIT 型別推理」的世界。你 V8 課練的 heap grooming、原語構造直覺能平移，但獵物完全不同。

> **未實測，理論預期**：本章不附可跑的 Mojo exploit。要自驗這節，你要 build content_shell、找一個有公開 writeup 的 sandbox escape CVE、對照它讀 Chromium 對應的 `.mojom` 與 `Receiver` 實作。這是「畢業之後的下一門課」，本課只負責把你帶到入口。

## 對比：V8 pwn（本課）vs sandbox escape（下一節）

| 面向 | V8 pwn（第一節，本課） | Sandbox escape（第二節，本章導覽） |
|---|---|---|
| 攻擊介面 | 一段 JavaScript | 一連串 Mojo IPC 訊息 |
| 獵物 | TurboFan/Maglev 的型別推理錯誤 | Browser 端 Mojo `Receiver` 的 C++ bug |
| 靶 | d8 / renderer | Browser process（要真 Chrome/content_shell） |
| bug 家族 | JIT type confusion 為主 | UAF / OOB / 權限檢查缺失 / handle 濫用 |
| 前置條件 | 能跑 JS | **已在 renderer 拿到 code exec**（= 第一節產物） |
| 直覺可平移 | — | heap grooming、原語構造平移；獵物不同 |
| 對應課程 | 本課 Ch 3–37 | 另一門課的份量；本章只給地圖 |

這張表的重點：**第二節把第一節當前置條件**。你這門課的每一分努力，都是在為「有資格發惡意 Mojo」鋪路。看懂這個依賴關係，你就不會覺得「只學第一節」是缺憾——它是整條鏈不可跳過的地基。

## 踩雷集錦

1. **以為 renderer code exec 就能直接打 kernel**：不行。中間隔著 sandbox escape 這整節。seccomp 擋著你直接對 kernel 發大量 syscall，你得先 escape 到 Browser process，才有「使用者態進程打 kernel」的立足點（那才是 kernel_pwn 的起點）。
2. **想用 V8 的獵洞直覺硬套 Mojo**：Mojo 攻擊面是 Chrome 的 C++ IPC 層，找的是 UAF / 權限檢查缺失，不是 JIT 型別推理。heap grooming、原語構造能平移，但「洞長什麼樣、去哪找」是另一套。別把 TurboFan 的經驗當萬能鑰匙。
3. **忽略邏輯洞（權限檢查缺失）**：sandbox escape 不一定要記憶體破壞。一個「本不該暴露給 renderer 的 interface 被暴露」的純邏輯洞，可能就夠 escape 或繞 site isolation。只盯記憶體 bug 會漏掉一整類。
4. **在 d8 上找 sandbox escape**：d8 沒有 Mojo、沒有多進程、沒有 Browser process——它連沙盒都沒有（[Ch 38](./38-d8-vs-real-chrome.md)）。要碰 Mojo 攻擊面，最低也要 content_shell。用 d8 找 escape 是搞錯靶。
5. **低估 site isolation 的存在感**：它同時是防禦（夾住你 renderer bug 的破壞半徑）和攻擊面（bypass 它本身是一類 bug）。做真實 exploit 時，「我這個 renderer 對應哪個 site、能碰哪些 interface」是必須先搞清楚的約束，不是背景細節。
6. **把「full chain 三節」當線性難度遞增**：三節是**不同技能樹**，不是同一技能的三個難度。有人 V8 很強但完全不會 Mojo，反之亦然。三節各自要專門學，本課誠實地只教第一節、把二三節指路清楚，就是這個原因。

## 進階：再往深一層

- **Mojo fuzzing**：就像 V8 有 Fuzzilli（[Ch 28](./28-fuzzilli-internals.md)），Mojo interface 也能 fuzz——Chromium 有針對 Mojo 的 fuzzer，餵半結構化的訊息去戳 `Receiver` 實作。這是真實研究員找 sandbox escape 的主力手段之一，思路和你 Part 5 學的 coverage-guided fuzzing 一脈相承。
- **GPU process 當中繼**：直接打 Browser 有時很硬（它防護最重）。GPU process 權限中等、攻擊面（command buffer、驅動互動）也肥，常被當成「renderer → GPU → Browser」的兩跳跳板。Pwn2Own 的 Chrome full chain 常見這種多跳結構。
- **`.mojom` 當攻擊面地圖**：想系統性看「renderer 能對 Browser 說哪些話」，就去讀 Chromium 裡所有暴露給 renderer 的 `.mojom` 定義。哪些 interface 被 broker 暴露、各方法參數怎麼定義——這份清單就是 sandbox escape 的礦脈圖，和你在 [Ch 2](./02-v8-architecture.md) 畫的 V8 攻擊面地圖是同一種思維，換到 IPC 層。
- **MiraclePtr / 其他 Browser 端硬化**：就像 V8 有 sandbox（[Ch 34](./34-v8-sandbox.md)），Browser process 也在加固——MiraclePtr（`raw_ptr`）讓一大類 UAF 更難利用。理解這些硬化，才知道今天的 sandbox escape 為什麼比五年前貴得多，和 V8 那邊「mitigation 逼攻擊進更窄角落」是同一齣戲。

## 動手練習

1. **畫全景圖**：不看本章，自己畫一張圖：一段惡意 JS 從「網頁」到「kernel ring0」要經過哪三節、每節的攻擊介面是什麼、獵物是什麼、對應哪門課。畫完對照 [Ch 1](./01-why-renderer-attack-surface.md) 的三節模型檢查。這是你這門課「座標感」的期末考。
2. **讀一篇 sandbox escape writeup（只讀架構）**：找一篇 Pwn2Own 或 P0 的 Chrome full chain writeup，**只讀 sandbox escape 那一段的架構**（不用懂 C++ 細節）：它打了哪個 Mojo interface？是記憶體 bug 還邏輯洞？有沒有經過 GPU process？把它 map 到本章「主要漏洞形態」的哪一類。
3. **`.mojom` 探勘**：去 Chromium 原始碼（線上瀏覽即可，不用 build）找幾個 `.mojom` 檔，讀它定義了哪些方法、參數長怎樣。感受「這就是攻擊面清單」——每個 renderer 可呼叫的方法都是一個潛在入口。
4. **接 kernel_pwn 的規劃**：翻你的 `security/kernel_pwn` 筆記，找出「從一個使用者態進程打 kernel」的起手式。想清楚：sandbox escape 交給你的 Browser process，怎麼變成 kernel_pwn 那個「使用者態進程」的起點。把三門課在腦中接起來。

## 本章重點整理

- **sandbox escape = 濫用 renderer 對 Browser 說話的必要通道（Mojo IPC）**：你已在 renderer 有 code exec，發惡意 Mojo 訊息去攻破高權限 Browser process。
- **Mojo 攻擊面 = 暴露給 renderer 的 `.mojom` interface**：獵物是 Browser 端 `Receiver` 的 C++ bug（UAF / OOB / type confusion / 權限檢查缺失 / handle 濫用），是和 V8 pwn 不同的技能樹。
- **site isolation 兩面性**：既夾住單一 renderer bug 的破壞半徑（約束你），本身也是一類可被 bypass 的攻擊面。
- **full chain 三節是三棵技能樹**：本課第一節（V8 RCE）→ 本章導覽第二節（sandbox escape）→ `kernel_pwn` 第三節（LPE）。第二節把第一節當前置條件，所以本課是整條鏈的地基。
- **本章只給地圖與入口**：要往下走需 content_shell/Chromium、讀 Mojo 文件與真實 writeup。這是畢業導覽，不是 Mojo 課的第一章。

## 自我檢核

- [ ] 能用「窮 renderer 求管家代勞」解釋為什麼 Mojo IPC 是 sandbox escape 的主戰場
- [ ] 能說出 Mojo 的 interface / `.mojom` / Remote-Receiver 各是什麼，以及攻擊面在哪一端
- [ ] 能列出 sandbox escape 的至少三種漏洞形態，並知道它們和 `binary_exploitation` 的 bug 家族同源
- [ ] 能講清楚 site isolation 對攻擊者「既是約束又是攻擊面」的兩面性
- [ ] 能把 full chain 三節接起來，說出每節的介面、獵物、對應課程
- [ ] 面試被問「拿到 renderer RCE 之後怎麼打到 kernel」，能講出 escape → Browser → LPE 的完整路徑

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

- **[Mojo 官方文件 — chromium.googlesource.com/chromium/src/+/main/mojo/README.md](https://chromium.googlesource.com/chromium/src/+/main/mojo/README.md)**
  - **這篇說什麼**：Mojo 是什麼、message pipe / interface / Remote-Receiver 怎麼運作、`.mojom` 怎麼寫。
  - **讀哪裡**：概念總覽 + interface 定義那幾節，對照本章「Mojo 全景」。
  - **和本章的關聯**：本章名詞（pipe / interface / broker）的權威定義，往下走的第一份必讀。

- **[Chromium IPC security 指南 — chromium.googlesource.com/chromium/src/+/main/docs/security/mojo.md](https://chromium.googlesource.com/chromium/src/+/main/docs/security/mojo.md)**
  - **這篇說什麼**：從防禦者視角講「Mojo interface 該怎麼寫才安全、常見錯誤是什麼」。
  - **為什麼值得讀**：防禦者列的「常見錯誤」清單，反過來就是攻擊者的 bug 目錄（權限檢查缺失、信任 renderer 傳來的值）。對照本章「主要漏洞形態」。

- **[Project Zero：Chrome sandbox escape / full chain 分析 — googleprojectzero.blogspot.com](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實 sandbox escape 的逆向，打哪個 interface、什麼 bug、怎麼串進 full chain。
  - **讀哪裡**：挑一篇含 sandbox escape 的，讀架構段（動手練習 2 的材料）。建立「這節長什麼樣」的形態感。

- **[Pwn2Own Chrome full chain writeup（各家安全團隊，如 theori / dataflow）— 搜尋「Pwn2Own Chrome renderer to SYSTEM」](https://www.zerodayinitiative.com/blog)**
  - **這篇說什麼**：完整三節鏈的實戰報告，renderer → sandbox escape → LPE 怎麼串。
  - **和本章的關聯**：本章三節模型的真實範例；也讓你看到 GPU process 中繼、多跳結構長怎樣。

你手上現在有整條 client-to-kernel 鏈的地圖，也知道自己這門課負責的第一節做到了極致。最後一章不教新攻擊，教一個貫穿你整個漏洞研究生涯的元技能：**怎麼有系統地讀一篇真實 V8 0-day writeup、怎麼追蹤 V8 安全生態、學完本課的下一步往哪走**。

→ [Ch 40 — 讀 Project Zero / 廠商 writeup 的地圖與下一步](./40-p0-writeup-map-next-steps.md)
