# Browser Pwn 學習筆記：V8 一條到底，從物件模型到 renderer RCE

> 給 userland pwn 已經熟練（穩定解 glibc heap、ROP、format string）、想把 pwn 天梯推到最頂端那一階的人。接在 `security/binary_exploitation` + `security/kernel_pwn` 之後。

這門課只打一個目標：**Google V8**（Chrome 的 JavaScript 引擎）。不碰 JavaScriptCore（Safari）、不碰 SpiderMonkey（Firefox）—— 專一才能挖到底。從 V8 怎麼表示一個 JS 值開始，走過物件模型（Map / elements kind / TypedArray）、執行管線（Ignition bytecode → TurboFan JIT → GC），建立 pwn 界的兩把鑰匙 `addrof` / `fakeobj`，深挖 TurboFan 各類 type confusion，再教你**自己找洞**（讀 commit、patch diffing、跑 Fuzzilli），最後在現代 mitigation（V8 Sandbox、CET/CFI）下把任意讀寫變成 code execution。每章配一個可跑的 d8 範例或 exploit 骨架，不是看圖說故事。

這門課正好卡在你已經走完的兩座山中間又往上一層：`binary_exploitation` 給你 userland 的 leak → 任意寫 → 控 RIP 肌肉記憶，`kernel_pwn` 給你「打一個帶 mitigation 的複雜 C 程式」的耐心 —— V8 就是那個「複雜 C++ 程式」，只是攻擊介面是一段 JavaScript。

## 為什麼學這個？

- **它是 pwn 天梯的頂端**：CTF 圈把 `pwn → heap → kernel → browser` 當難度階梯，browser（尤其 V8）長年在最上面。原因不是單一技巧難，而是你要同時吃下一個真實、龐大、高度優化的 JIT 編譯器的內部模型 —— 沒有前面兩門課的地基，你會在 sea-of-nodes 裡迷路。
- **type confusion 是一整個時代的主戰場**：stack overflow 的時代過去了，現代 client-side RCE 幾乎都長在 JIT 的錯誤假設上。TurboFan 相信一個型別/範圍/map 不會變，攻擊者想辦法讓它變 —— 看懂這條線，你就看懂了 2016 年之後絕大多數瀏覽器 0-day 的骨架。
- **找洞與打洞同步練**：這門課不只教你吃現成漏洞。Part 5 直接帶你 patch diffing（1-day 開發）和跑 Fuzzilli（V8 專用 coverage-guided fuzzer），這是真實漏洞研究員每天在做的事，也是 CTF 之外唯一的變現路徑。
- **mitigation 是活的**：`__malloc_hook` 在 glibc 死掉那套劇情，在 V8 這邊叫 **V8 Sandbox（ubercage）**—— external pointer table 出現後，「任意 R/W → 直接控制 backing store pointer」這條經典路被斬斷。這門課刻意站在 sandbox 已上線的現代，教你它在防什麼、現在的人怎麼繞。

## 先備知識

- **Userland pwn 熟練**：`addrof`/任意讀寫/控 RIP 的直覺要有。不熟先回 `security/binary_exploitation`。
- **C/C++ 讀得動**：V8 是 C++。你不用會寫現代 C++，但要能跟著讀 `src/objects/`、`src/compiler/` 的原始碼。
- **x86-64 組語**：會讀 `gdb disas`，知道 JIT 出來的 machine code 大概長怎樣。
- **JavaScript 基礎**：會寫、知道 prototype、`Array`/`TypedArray` 怎麼用。不用很深。
- **不需要**先懂編譯器理論 —— TurboFan 需要的 IR / 優化概念，Part 2 從零補。讀過 `compilers/compiler_backend` 是加分但非必要。

## 環境

- 本課以 **從原始碼編譯的 V8（含 debug/symbol）**、**x86-64**、**Linux（Ubuntu 22.04 / 24.04 或 WSL2）** 為主線。
- 主要工具：`depot_tools`（`gn` + `ninja`）、`d8`（V8 的 REPL/shell）、`gef`（或 pwndbg）、`turbolizer`（看 TurboFan IR）、`Fuzzilli`。
- **版本會釘定**：V8 的內部佈局（尤其 pointer compression、sandbox、elements kind 常數）改得很快，每章開頭會標明是用哪個 V8 版本/git hash 跑的。你在別的版本重現不出來，先對版本。
- **Ch 0 會把整套環境一次搭好**，包含編譯 flag（`v8_enable_sandbox`、`v8_enable_pointer_compression`、debug vs release）怎麼選。首次編譯磁碟需求 ~30GB、耗時數十分鐘到數小時，Ch 0 會講怎麼縮短。

> **驗證說明（認識論誠實）**：帶 d8 範例、`%DebugPrint` 輸出、exploit 骨架的章節，作者在編好的 V8 上實測後才貼真實輸出。牽涉**完整 Chrome renderer 打靶、真實未修 CVE、Fuzzilli 長時間執行**的段落，會明確標注「**未實測，理論預期**」並給出你自己該怎麼驗證的步驟 —— 這類東西不適合、也不該在教材環境假裝跑過。

## 課程地圖

### Part 0 — 環境與心智模型（Ch 0–2）
- [Ch 0 環境搭建：編 V8、d8、%DebugPrint、gef](./00-environment-setup.md)
- [Ch 1 為什麼 renderer 是攻擊面](./01-why-renderer-attack-surface.md)
- [Ch 2 V8 架構全圖](./02-v8-architecture.md)

### Part 1 — V8 物件模型（地基，Ch 3–8）
- [Ch 3 值的表示：SMI / HeapObject / pointer tagging](./03-value-representation.md)
- [Ch 4 Pointer Compression](./04-pointer-compression.md)
- [Ch 5 Map / hidden class](./05-map-hidden-class.md)
- [Ch 6 Properties 與 Elements](./06-properties-elements.md)
- [Ch 7 JSArray 與 elements kind 轉換](./07-jsarray-elements-kind.md)
- [Ch 8 ArrayBuffer / TypedArray / DataView 與 backing store](./08-arraybuffer-typedarray.md)
- [練習 A：用 %DebugPrint / gef 解剖 V8 物件模型](./practice-a-object-model-dissection.md)

### Part 2 — 執行管線（Ch 9–13）
- [Ch 9 Parser 與 Ignition bytecode](./09-parser-ignition-bytecode.md)
- [Ch 10 TurboFan 概論：sea-of-nodes IR](./10-turbofan-overview.md)
- [Ch 11 優化 pipeline 與 bounds-check elimination](./11-optimization-pipeline.md)
- [Ch 12 Speculation 與 Deoptimization](./12-speculation-deopt.md)
- [Ch 13 GC（Orinoco）與對利用的影響](./13-garbage-collection.md)

### Part 3 — 利用原語（核心，Ch 14–18）
- [Ch 14 第一個 OOB：JSArray 越界](./14-first-oob.md)
- [Ch 15 建立 addrof / fakeobj](./15-addrof-fakeobj.md)
- [Ch 16 從 addrof/fakeobj 到任意讀寫](./16-fake-object-rw.md)
- [Ch 17 TypedArray 攻擊法：劫持 backing store](./17-typedarray-attack.md)
- [Ch 18 「OOB → 任意 R/W」標準流程整合](./18-oob-to-arbitrary-rw.md)
- [練習 B：從 OOB 到任意讀寫](./practice-b-oob-to-rw.md)

### Part 4 — 漏洞類別深挖（Ch 19–25）
- [Ch 19 TurboFan type confusion：CVE-2018-17463](./19-turbofan-type-confusion.md)
- [Ch 20 CheckBounds / redundancy-elimination bug](./20-checkbounds-redundancy-elimination.md)
- [Ch 21 Array.prototype side-effect / species](./21-array-prototype-side-effect.md)
- [Ch 22 Typer / range-analysis bug](./22-typer-range-analysis-bug.md)
- [Ch 23 Element-kind confusion / Map transition bug](./23-element-kind-map-confusion.md)
- [Ch 24 JIT side-effect 系列](./24-jit-side-effect.md)
- [Ch 25 RegExp / JSON / 內建物件的洞](./25-regexp-json-builtins.md)
- [練習 C：TurboFan type confusion CTF 完整解](./practice-c-type-confusion-ctf.md)

### Part 5 — 找洞：patch-diff + fuzzing（Ch 26–31）
- [Ch 26 讀 V8 source 與 commit](./26-reading-v8-source-commits.md)
- [Ch 27 Patch diffing：從 fix 反推 PoC](./27-patch-diffing.md)
- [Ch 28 Fuzzilli 原理](./28-fuzzilli-internals.md)
- [Ch 29 實跑 Fuzzilli 找 crash 與 triage](./29-running-fuzzilli.md)
- [Ch 30 從 crash 判斷可利用性](./30-exploitability-triage.md)
- [Ch 31 OSS-Fuzz / regression test 當漏洞線索](./31-oss-fuzz-regression.md)
- [練習 D：patch-diff 真實 commit 寫出 PoC](./practice-d-patch-diff-poc.md)
- [練習 E：跑 Fuzzilli 找 crash 並 triage](./practice-e-fuzzilli-crash-triage.md)

### Part 6 — 從任意 R/W 到 code exec 與 sandbox（Ch 32–36）
- [Ch 32 任意 R/W 到 code execution](./32-arbitrary-rw-to-code-exec.md)
- [Ch 33 WebAssembly RWX / JIT spray 與消亡史](./33-wasm-rwx-jit-spray.md)
- [Ch 34 V8 Sandbox：ubercage / external pointer table](./34-v8-sandbox.md)
- [Ch 35 繞過 / 在 V8 sandbox 內作業](./35-bypassing-v8-sandbox.md)
- [Ch 36 CET/CFI 之後與 data-only 思路](./36-cfi-cet-data-only.md)
- [練習 F：任意 R/W 到 code execution](./practice-f-rw-to-code-exec.md)

### Part 7 — 真實 exploit 與 CTF（Ch 37–40）
- [Ch 37 CTF V8 題型全解](./37-ctf-v8-challenges.md)
- [Ch 38 d8 與真實 Chrome renderer 的差異](./38-d8-vs-real-chrome.md)
- [Ch 39 renderer 之後：Mojo / sandbox escape 全景](./39-renderer-mojo-sandbox-escape.md)
- [Ch 40 讀 Project Zero writeup 的地圖與下一步](./40-p0-writeup-map-next-steps.md)
- [Final Project：從真實 V8 CVE 到完整 exploit](./final-project-cve-to-exploit.md)

## 學習方式建議

1. **每章都在 d8 裡跑一遍**：這門課的核心資產是一顆你自己編的、帶 debug 功能的 V8。看到 `%DebugPrint(obj)` 就自己打一次，看 map / elements 長什麼樣。光讀不跑，物件模型永遠是抽象的。
2. **故意把它弄壞**：改一個 elements kind、把陣列長度手動蓋掉、讓一個 map transition 發生 —— 觀察 V8 的反應。這門課後半的所有漏洞，本質都是「讓 V8 對自己的物件產生錯誤認知」。
3. **對照原始碼讀**：每章會給 `src/` 下的具體路徑。V8 沒有比它的原始碼更權威的文件；養成「打開對應的 `.cc` / `.tq` 檔」的習慣。
4. **Part 5 一定要親手跑一次**：patch diffing 和 Fuzzilli 不是讀就會的。跑一次 Fuzzilli、triage 一個真實 crash，你對「洞從哪來」的直覺會完全不同。

## 精選資料庫

整門課最值得反覆參照的資源；每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[V8 原始碼](https://chromium.googlesource.com/v8/v8/)**（`src/objects/`、`src/compiler/`、`src/builtins/`）
  - 這門課的最終仲裁。行為和教材不符時，以你編的那個版本的原始碼為準。
- **[V8 官方部落格 v8.dev/blog](https://v8.dev/blog)**
  - V8 團隊自己寫的設計說明。物件模型、elements kind、pointer compression、sandbox 都有官方長文，是二手資料的源頭。

### 推薦部落格 / 系列

- **[Google Project Zero blog](https://googleprojectzero.blogspot.com/)** — Google P0
  - 真實 in-the-wild V8 0-day 的最高品質 writeup 來源。看完這門課就是為了能讀懂這裡的文章。
- **[Jeremy Fetiveau — doar-e / dblog](https://doar-e.github.io/)**（`@__x86`）
  - TurboFan type confusion 利用寫得最清楚的系列之一，多篇是本課 Part 4 的直接對照。
- **[Samuel Groß（saelo）的 Fuzzilli 與 V8 exploit 論文/talk](https://saelo.github.io/)**
  - Fuzzilli 作者本人；Part 5 的 fuzzing 部分幾乎是他工作的導讀。

### 官方文件 / 工具

- **[V8 build 文件（using_git / build gn）](https://v8.dev/docs/build)**
  - Ch 0 的權威依據；depot_tools / gn args 都以這裡為準。
- **[Fuzzilli GitHub](https://github.com/googleprojectzero/fuzzilli)**
  - Part 5 的主工具，README 與 `Docs/` 是 Ch 28–29 的一手材料。

### 讀完本課之後

- **[phrack / 各家 browser exploit 論文](http://phrack.org/)**（進階，把單一技巧推到極致）
- **真實 Chrome full-chain writeup**（renderer → sandbox escape → kernel LPE）——這門課停在 renderer，下一步就是把 `kernel_pwn` 學到的東西接上去，組成完整 chain。

## 這門課刻意不涵蓋

- **JavaScriptCore / SpiderMonkey**：專打 V8，其他引擎的 addrof/fakeobj 思路相通但內部結構不同，不在範圍。
- **完整 Chrome sandbox escape 的實作細節**：Mojo IPC、GPU/network process 逃逸只在 Ch 39 做全景導覽並指路，不逐行實作 —— 那是另一門課的份量。
- **DOM / Blink 漏洞（UAF in renderer 的非 V8 部分）**：本課聚焦 JS 引擎本身，Blink 的 DOM UAF 只在攻擊面章節帶過。
