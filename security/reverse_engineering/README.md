# 逆向即讀碼：從 strip binary 系統化重建意圖

> 給讀完 [`reading_code`](../../soft_skills/reading_code/README.md) / [`codebase_case_studies`](../../soft_skills/codebase_case_studies/README.md)、會讀 source，現在想在**沒有 source** 時一樣讀得懂的工程師與安全研究者。

`reading_code` 教你攻堅陌生 source，`codebase_case_studies` 教你累積設計 pattern 字典。這門課走到光譜的極端：**source 一個字都沒有，你只有一團機器碼。** 逆向工程（reverse engineering）就是在這個「無名世界」裡，把編譯器丟掉的意圖——結構、演算法、協定——一塊一塊重建回來。

這是前兩門的鏡像對稱：在 source 裡你認出「這是 reactor event loop」，在 binary 裡你認出「這是 signed 除以 2 的編譯器慣用語」「這是一個 C++ vtable 呼叫」。**逆向的核心能力，是辨識編譯器慣用語（compiler idiom）——binary 版的 pattern 辨識。** 全程用 ground-truth 迴圈（寫→編→strip→逆→對答案）真跑驗證，逆錯當場抓到。

## 為什麼學這個？

- **逆向是安全研究的通用地基**：找漏洞、分析惡意程式、破解保護、patch-diff 還原 CVE、驗證閉源依賴——全都建立在「讀得懂 binary」之上。你有一堆 pwn/RE 專題課（`binary_exploitation`、`android_reversing`、`malware_analysis`…），這門補的是它們共用、卻沒被單獨系統化教的**逆向理解方法論**。
- **它逼你真懂編譯與體系結構**：逆向是編譯的逆運算。看懂 `-O2` binary 逼你理解 inline、strength reduction、向量化、calling convention、ABI——把你的 compiler 課群和 ISA 知識全盤活用。
- **這是一種 pattern 辨識技能**：老手逆向快，是因為一眼認出 compiler idiom、標準庫指紋、資料結構形狀。這門課系統化建立你的 binary pattern 字典。

## 先修知識

- **會讀 source + 讀碼 SOP**（`reading_code` 的偵察/假設驅動/收斂/外化，本課是它的鏡像）
- **C 讀寫 + 基本體系結構**（程度：知道 stack/heap/暫存器/pointer；x86-64 asm 不熟沒關係，Ch 4 從逆向者視角補）
- **命令列 + gcc/gdb**（能編譯、跑、下中斷點）
- 沒有也沒關係的：完整 x86-64 asm、Ghidra/IDA 經驗、任何逆向背景——本課從零建立

## 課程地圖

### Part 0 — 逆向的心智模型與工具（Ch 0–3）
- [Ch 0 環境與 ground-truth 逆向迴圈](./00-environment-and-ground-truth-loop.md)
- [Ch 1 逆向即讀碼：reading_code 的鏡像](./01-reversing-is-reading-code.md)
- [Ch 2 從 source 到 binary：編譯器做了什麼](./02-source-to-binary-what-compiler-does.md)
- [Ch 3 ELF 解剖與載入](./03-elf-anatomy-and-loading.md)

### Part 1 — 靜態逆向：讀反組譯與反編譯（Ch 4–11）
- [Ch 4 x86-64 asm 逆向者視角](./04-x86-64-for-reversers.md)
- [Ch 5 認出控制流：if / loop / switch](./05-recognizing-control-flow.md)
- [Ch 6 認出資料：struct / array / 指標 / 全域](./06-recognizing-data.md)
- [Ch 7 認出函式：prologue / 參數 / inline 痕跡](./07-recognizing-functions.md)
- [Ch 8 讀反編譯器輸出：它的謊言與怎麼騙你](./08-reading-decompiler-output.md)
- [Ch 9 型別與結構還原](./09-type-and-struct-recovery.md)
- [Ch 10 認出編譯器慣用語（compiler idioms）](./10-compiler-idioms.md)
- [Ch 11 認出標準庫與資料結構指紋](./11-recognizing-stdlib-fingerprints.md)
- [練習 A：靜態逆一個 strip crackme](./practice-a-static-reverse-crackme.md)

### Part 2 — 動態逆向：讓 binary 自己招（Ch 12–17）
- [Ch 12 動態逆向心法：觀察勝於推理](./12-dynamic-reversing-mindset.md)
- [Ch 13 gdb 逆向工作流](./13-gdb-reversing-workflow.md)
- [Ch 14 trace 執行：strace / ltrace / 自寫 tracer](./14-tracing-execution.md)
- [Ch 15 動態插樁（DBI）：Frida / Pin / DynamoRIO](./15-dynamic-instrumentation-dbi.md)
- [Ch 16 記憶體與資料流動態追蹤](./16-dynamic-data-flow-tracking.md)
- [Ch 17 靜動結合：假設驅動逆向](./17-combining-static-dynamic.md)
- [練習 B：動態逆一個授權檢查](./practice-b-dynamic-reverse-a-check.md)

### Part 3 — 目標識別：逆出結構（Ch 18–24）
- [Ch 18 逆一個演算法：認出 crypto / hash / 壓縮指紋](./18-reversing-algorithms.md)
- [Ch 19 逆一個檔案格式 / 協定](./19-reversing-file-formats-protocols.md)
- [Ch 20 逆 C++ binary：vtable / RTTI / name mangling](./20-reversing-cpp-binaries.md)
- [Ch 21 逆 Rust / Go binary：為什麼更難](./21-reversing-rust-go-binaries.md)
- [Ch 22 逆靜態連結 / 去符號的大 binary](./22-reversing-stripped-static-binaries.md)
- [Ch 23 認出並對抗混淆 / anti-reversing](./23-obfuscation-anti-reversing.md)
- [Ch 24 跨平台一瞥：Windows PE / ARM64](./24-cross-platform-pe-arm64.md)
- [練習 C：逆一個檔案格式並寫出 parser](./practice-c-reverse-a-format-write-parser.md)

### Part 4 — 工程化與自動化（Ch 25–30）
- [Ch 25 逆向筆記與外化](./25-externalizing-reversing-notes.md)
- [Ch 26 腳本化逆向：Ghidra script / IDAPython / angr](./26-scripting-reversing.md)
- [Ch 27 patch-diff / bindiff：從補丁還原漏洞](./27-patch-diffing.md)
- [Ch 28 二進位相似度與函式指紋](./28-binary-similarity-fingerprinting.md)
- [Ch 29 反編譯到可編譯：lifting 與重建](./29-decompile-to-recompilable-lifting.md)
- [Ch 30 逆向者的 pattern 字典](./30-reversers-pattern-dictionary.md)
- [練習 D：patch-diff 一個真實 CVE 補丁](./practice-d-patch-diff-a-cve.md)

### Part 5 — Capstone（Ch 31–33 + Final）
- [Ch 31 完整攻堅實況：冷逆一個 strip binary](./31-full-attack-live.md)
- [Ch 32 常見誤區與反模式](./32-anti-patterns.md)
- [Ch 33 打造你的逆向 SOP](./33-your-reversing-sop.md)
- [Final Project：冷啟動逆向一個 strip binary](./final-project-cold-reverse-a-binary.md)

## 學習方式建議

1. **每個技巧都用 ground-truth 迴圈練**：寫一小段 C、編譯、strip、逆回去、對答案。逆錯了當場知道——這是把逆向從玄學變技能的關鍵。
2. **兩種優化等級都看**：`-O0` 給你可讀的對照，`-O2`/`-O3` 才是真實 release binary 的樣子。教材兩者都給，你要知道自己在看哪個。
3. **靜態卡住就跑它**：逆向不是純靜態閱讀。推不出來就上 gdb 動態觀察（Part 2）。觀察一次勝過瞪 asm 十分鐘。
4. **建你自己的 idiom 字典**：每認出一個編譯器慣用語（`shr;add;sar` = signed /2、`lea` 當乘法、jump table = switch）就記一張卡。Ch 30 會收斂成完整字典。

## 精選資料庫

每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - Linux/ELF/x86-64 二進位分析最佳入門，工具與原理並重；本課 Part 0–2 主要對照。
- **《Reverse Engineering for Beginners》** — Dennis Yurichev（[免費](https://beginners.re/)）
  - 海量「C 一行 ↔ asm」對照，本課 Ch 4–11 的最佳題庫與字典。

### 工具與參考

- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - 即時 source↔asm 對照，逆向練習神器：想確認某 asm pattern 對應什麼 source，反查它。
- **[Ghidra](https://ghidra-sre.org/) / [Hex-Rays（IDA）](https://hex-rays.com/)**
  - 反編譯器；本課 Ghidra 免費為主，IDA 交叉引用你的 [`ida_pro`](../ida_pro/README.md) 課。

### 讀完本課之後

- 把逆向能力接回你的天梯：patch-diff 找漏洞（→ `browser_pwn`）、逆惡意程式（→ `malware_analysis`）、逆平台（→ `android_reversing` / `ios_macos_exploitation`）、符號執行補靜態（→ `symex_taint`）。
