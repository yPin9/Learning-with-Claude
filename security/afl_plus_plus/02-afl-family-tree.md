# Ch 2 — AFL 家族樹：從 lcamtuf 的 bitmap 到 AFL++ 的合流

> **目標**：理解 AFL++ 的演化歷史，知道為什麼不直接用原版 AFL，以及 AFL++ 「合流」了哪些研究。

## 為什麼需要家族樹？

2017 年 AFL 停更之後，fuzzing 社群進入了一段「百花齊放但互不相通」的時期。十幾個研究團隊各自改 AFL、各自發 paper、各自釋出工具，但這些改進從來沒有整合在一起。如果你今天想用 power schedule（AFLFast）又想用 magic bytes 突破（LAF-Intel），你得自己把兩個 fork 的 patch 合在一起——而它們通常衝突。

AFL++ 在 2019 年解決的不是技術問題，而是**協調問題**：把五年間分散在各 paper 的 incremental improvements 整合成一個可維護的工具。理解這段歷史，你才能知道 AFL++ 的每個 flag 背後有哪篇論文，以及為什麼設計成現在這樣。

## 先建立直覺

把 AFL 家族想成一條河流：

- **原版 AFL**：泉源，定義了 coverage-guided fuzzing 的基本形狀
- **停更後的 fork**：上游分叉成十幾條支流，每條解決一個特定問題
- **AFL++**：在下游把支流重新匯聚，但不是簡單的 merge——是有架構地整合成可切換的模組

不理解這段歷史，你就不知道 `AFL_LLVM_CMPLOG=1` 在做什麼、為什麼 `-p fast` 比 `-p explore` 更積極、為什麼 LTO mode 要單獨有一個 compiler wrapper。

## 原版 AFL：三個決定性選擇

lcamtuf 在 2013 年發布 AFL（American Fuzzy Lop），在 fuzzing 領域引發了範式轉移（paradigm shift）。原版 AFL 做了三個設計選擇，後來幾乎所有衍生工具都繼承：

**1. Edge coverage bitmap（邊覆蓋位圖）**

把程式的控制流（control flow）抽象成邊（edge）：從基本塊 A 跳到基本塊 B 叫一條邊。用一個 64KB 的 bitmap 記錄「哪些邊被執行過」。每條邊對應 bitmap 的一個 byte，值表示執行次數（bucketed）。

這個設計的妙處在於它夠便宜：bitmap 操作是一條 `mov` 指令，overhead 幾乎為零，但提供的資訊卻足夠指導 mutation。

**2. Forkserver（fork 伺服器）**

不用每次 `execve()` 重啟 target process，而是在 target 內部放一個 forkserver：fuzzer 發信號，forkserver `fork()` 出一個 child，child 執行一個測試用例後退出，forkserver 回報狀態。

`execve()` 的成本在有動態連結的程式上非常高（每次都要重新 load .so、執行 constructors）。forkserver 把這個成本從「每個測試用例」降到「一次」。

**3. Compile-time instrumentation（編譯時插樁）**

在編譯階段用 gcc 插樁，把 bitmap 更新邏輯直接插入 binary，比動態插樁（DynamoRIO、Pin）快一個數量級。

這三個選擇合在一起，讓 AFL 的 execution rate 比當時的競品高出 10 倍以上，這是它迅速成為業界標準的原因。

## 時間線：從 AFL 到 AFL++

```
2013 ── AFL v0.1 (lcamtuf)
        ▪ coverage-guided fuzzing 第一個完整實作
        ▪ bitmap + forkserver + compile-time instrumentation 三位一體

2015 ── AFL 2.x
        ▪ persistent mode 雛形（__AFL_LOOP）
        ▪ LLVM mode 出現（afl-clang-fast）

2016 ── AFLFast (CCS 2016, Böhme et al.)
        ▪ 問題：AFL 在「高頻邊」上浪費太多時間
        ▪ 解法：power schedule（能量分配）——給 rare path 更多 mutation 機會
        ▪ Multi-Armed Bandit 模型引入 fuzzing

        libFuzzer 出現（Google, LLVM 生態）
        ▪ in-process fuzzing，沒有 forkserver
        ▪ 對 sanitizer 整合比 AFL 更方便

2017 ── AFL 停更（lcamtuf 去 Google 後停止維護原版）

        CollAFL (S&P 2018, Gan et al.) [實際 2017-2018 研究]
        ▪ 問題：AFL 的 bitmap hash 有大量 collision
        ▪ 解法：collision-free 的 edge hash scheme

        AFLGo (CCS 2017, Böhme et al.)
        ▪ 問題：想針對特定程式位置做 directed fuzzing
        ▪ 解法：計算 distance，讓 seed 往目標方向 bias

        LAF-Intel (2017, Mounier)
        ▪ 問題：AFL 對 multi-byte 比較無能為力（magic bytes 問題）
        ▪ 解法：把 cmp 指令拆成 single-byte 比較，讓 AFL 能逐步推進

2018 ── 碎片化高峰
        ▪ 10+ active fork：QSYM、TriforceAFL、kAFL、Nautilus...
        ▪ 每個 fork 解決一個問題但互不相容
        ▪ 使用者要組合功能必須自己 cherry-pick patch

        Honggfuzz 成熟
        ▪ Google 出品，feedback-based 但走不同技術路線
        ▪ hardware-assisted coverage（Intel PT）

        REDQUEEN (NDSS 2019, Aschermann et al.) [2018 preprint]
        ▪ 問題：magic bytes 用 taint analysis 解太重
        ▪ 解法：input-to-state correspondence——直接觀察 cmp 的兩個 operand，
                找到 input 中對應的位置並替換，不需要 symbolic execution

2019 ── AFL++ 1.0 (WOOT 2019, Fioraldi et al.)
        ▪ 核心主張：把前五年的 incremental improvements 整合進單一工具
        ▪ 合入：AFLFast power schedule、LAF-Intel（比較拆分）、
                REDQUEEN（CmpLog）、persistent mode 完整支援
        ▪ 新增：custom mutator API、LLVM mode 改進

2020 ── AFL++ 2.x
        ▪ CmpLog 穩定（REDQUEEN 的工程實作）
        ▪ LTO mode（afl-clang-lto）：link-time instrumentation，
          大幅降低 bitmap collision
        ▪ MOpt（CCS 2019, Lyu et al.）整合：用 swarm intelligence
          動態調整 mutation operator 的權重

        AFL++ WOOT 2020 paper（正式學術版本）
        ▪ 完整描述架構，benchmark 展示各模組貢獻

2022 ── AFL++ 4.x
        ▪ 現在的主力版本（4.09c as of 2024）
        ▪ Frida mode 成熟（對 closed-source binary）
        ▪ 持續整合新研究（SnapFuzz 思路的 in-memory fuzzing 改進）
        ▪ 活躍維護：平均每月數十個 commit
```

## AFL++ 合入了哪些研究

以下是 AFL++ 核心功能對應的原始研究：

| AFL++ 功能 | 對應研究 | 解決的問題 |
|-----------|---------|-----------|
| `-p fast` / `-p explore` 等 power schedule | AFLFast (CCS 2016) | rare path 得到更多 mutation 時間 |
| `AFL_LLVM_CMPLOG=1` + `-c 0` | REDQUEEN (NDSS 2019) | magic bytes、checksum 繞過 |
| `AFL_LLVM_LAF_ALL=1` | LAF-Intel (2017) | 比較拆分，輔助 multi-byte 突破 |
| `-p mmopt` | MOpt (CCS 2019) | 動態調整 havoc mutation 的 operator 比例 |
| `afl-clang-lto` | AFL++ 自研 (LTO instrumentation) | 消除 bitmap collision |
| `-Q` QEMU mode | upstream QEMU + AFL patch | 對 binary-only target 插樁 |
| `-O` Frida mode | Frida dynamic instrumentation | closed-source, no recompile |
| custom mutator API | AFL++ 設計 | grammar-aware mutation 介面 |

## 和 libFuzzer、Honggfuzz 的平行發展

這三個工具不是衍生關係，是**平行演化**的競品：

```
AFL (2013)                   libFuzzer (2016)          Honggfuzz (2015)
     │                             │                          │
     │  coverage-guided            │  in-process fuzzing      │  hw coverage
     │  forkserver model           │  LLVM sanitizer          │  Intel PT / 
     │  file-based I/O             │  integration             │  perf events
     │                             │  LibFuzzer corpus        │
     ▼                             ▼                          ▼
AFL++ (2019)              繼續發展中                    繼續發展中
     │
     ▼
LibAFL (2021, Rust)   ← 下一世代，架構更模組化
```

**libFuzzer 的特點**：
- In-process：fuzzer 和 target 在同一個 process，省去 IPC 成本
- 對 ASan/UBSan 整合更自然（因為 in-process）
- 適合有完善 API harness 的 library fuzzing
- 缺點：target crash 會殺死整個 fuzzer process

**Honggfuzz 的特點**：
- 支援 hardware-based coverage（Intel Processor Trace）：不需要插樁就能得到比 bitmap 更精確的 feedback
- 對 persistent mode 支援很早就成熟
- 在某些 server application fuzzing 場景比 AFL++ 好用

**什麼時候選 AFL++ 而非其他**：
- Target 需要 compile-time instrumentation，且你有原始碼
- 想要 CmpLog 突破 magic bytes（libFuzzer 也有類似的 `-use_value_profile`，但不如 AFL++ REDQUEEN 完整）
- 需要細粒度控制 power schedule
- Target 是 binary-only（AFL++ 的 QEMU/Frida mode 比 libFuzzer 的選項豐富）

## 底層機制：為什麼停更的 AFL 不夠用

```
        原版 AFL 的已知問題
        ┌─────────────────────────────────────────┐
        │                                         │
        │  ① bitmap collision（64K bitmap 對大    │
        │    程式不夠，hash 衝突率 15-30%）        │
        │                                         │
        │  ② 所有 seed 能量相等（常見 path 和     │
        │    rare path 得到相同 mutation 次數）    │
        │                                         │
        │  ③ magic bytes 完全無能為力             │
        │    （4-byte 比較要碰到的機率 = 1/2^32） │
        │                                         │
        │  ④ 沒有 plugin 介面，難以整合自訂邏輯   │
        └─────────────────────────────────────────┘

        AFL++ 如何修
        ┌─────────────────────────────────────────┐
        │                                         │
        │  ① LTO instrumentation → collision      │
        │    幾乎為零；PCGUARD 也改善了 hash       │
        │                                         │
        │  ② 6 種 power schedule 可切換；          │
        │    MOpt 動態調整 mutation 比例           │
        │                                         │
        │  ③ CmpLog（REDQUEEN 實作）+              │
        │    LAF-Intel 比較拆分，雙管齊下          │
        │                                         │
        │  ④ custom mutator C API + Python API    │
        └─────────────────────────────────────────┘
```

## 對比：原版 AFL vs AFL++

| 面向 | 原版 AFL | AFL++ |
|------|---------|-------|
| 維護狀態 | 停更（2017） | 活躍維護（月更新） |
| Power schedule | 無（所有 seed 相同） | 6 種可選（fast/explore/exploit/seek/rare/mmopt） |
| Magic bytes 突破 | 無 | CmpLog + LAF-Intel |
| Bitmap collision | 高（15-30% 在大程式） | LTO mode 近零；LLVM mode 改善 |
| Binary-only target | QEMU mode（原始版） | QEMU mode（改進版）+ Frida mode |
| Custom mutator | 無 | C API + Python API + Rust API |
| Compiler wrapper | afl-gcc（基於 gcc plugin） | afl-clang-fast、afl-clang-lto（LLVM based） |
| LTO instrumentation | 無 | afl-clang-lto |
| 記錄 snapshot | 無 | 支援（與 snapshot kernel module 整合） |

## 踩雷集錦

**1. 「AFL++ 就是 AFL 的新版本」**

不是。AFL++ 是獨立的工具，誕生於 AFL 停更兩年後。它的設計目標是「合流多個分支研究」，不是「升級原版 AFL」。兩者在 flag 命名和行為上有不相容之處（例如 `-t` timeout 的預設值不同）。

**2. 文章說「AFL」你要確認說的是哪個**

學術論文中「AFL」通常指原版（lcamtuf 版）。「AFL++」是明確的指稱。很多 2018-2019 年的論文在 baseline 用的是原版 AFL，所以那些比較數字不能直接套到 AFL++ 上。

**3. AFL++ 的 flag 和原版 AFL 有些不相容**

例如原版 AFL 的 `-S sync_dir` 在 AFL++ 裡已被整合進 `-o` 的目錄結構。如果你把針對原版 AFL 寫的腳本直接拿來跑 AFL++，有些 flag 會被忽略或行為不同。

**4. REDQUEEN ≠ CmpLog（嚴格說）**

REDQUEEN 是論文提出的技術（2019, NDSS）。CmpLog 是 AFL++ 的工程實作，沿用了 REDQUEEN 的核心思路（input-to-state correspondence）但有實作差異。AFL++ 官方文件有時會混用這兩個名詞，讀論文和讀 AFL++ 文件要分清楚來源。

**5. AFL++ 的版本號不連續**

AFL++ 從 v1.x 跳到 v2.x 再到 v4.x，沒有 v3.x（被跳過了）。看 changelog 或 paper 引用時注意版本號，不要以為找不到 v3.x 是因為你漏了什麼。

## 進階：AFL++ 合流的工程取捨

把多個研究合進同一個工具，最難的不是功能實作，是**介面設計**：怎麼讓這些功能既能獨立啟用，又能組合使用，同時不讓 code base 爆炸？

AFL++ 的解法是**條件編譯 + 環境變數 + 建構時選項**三層架構：

```bash
# 編譯時決定：是否包含 QEMU mode
make source-only                # 只有 source instrumentation
make binary-only                # 包含 QEMU mode
make all                        # 全部

# 執行時決定：power schedule
afl-fuzz -p fast ...            # AFLFast schedule
afl-fuzz -p explore ...         # 均勻探索

# 環境變數決定：instrumentation 行為
AFL_LLVM_CMPLOG=1 afl-clang-fast target.c -o target   # 啟用 CmpLog
AFL_LLVM_LAF_ALL=1 afl-clang-fast target.c -o target   # 啟用 LAF 拆分
```

這個設計讓 AFL++ 能在「不增加每個測試用例 overhead」的前提下支援多種功能——CmpLog 的 overhead 在「有用到 CmpLog 的輪次」才出現，其他輪次不付出成本。

```c
// src/afl-fuzz.c（簡化）
// AFL++ 在啟動時根據環境決定使用哪個 power schedule
if (getenv("AFL_FAST_CAL"))   fsrv->power_schedule = EXPLORE;
else if (schedule_name)        fsrv->power_schedule = parse_schedule(schedule_name);
else                           fsrv->power_schedule = FAST;
```

## 動手練習

1. **讀 WOOT 2020 paper 的 Table 1**（Fioraldi et al.）：列出 AFL++ 整合的每個技術及來源。對照本章的表格，確認你理解每個行的背景。

2. **比較兩個 binary**：
   ```bash
   # 用原版 AFL compiler wrapper 編譯（如果系統上有裝）
   afl-gcc -o target_afl target.c
   # 用 AFL++ compiler wrapper 編譯
   afl-clang-fast -o target_aflpp target.c
   # 用 objdump 比較插樁的差異
   objdump -d target_afl | grep -A3 "__afl_"
   objdump -d target_aflpp | grep -A3 "__afl_"
   ```

3. **找一篇 2018-2019 年的 fuzzing paper**：看它的 baseline 是原版 AFL 還是 AFL++。想想：如果用現在的 AFL++ 重跑那個實驗，結果會有什麼差異？

## 本章重點整理

- AFL++ 是**合流**，不是升級——它把五年間分散的 fuzzing 研究整合進單一工具，每個功能都有對應的原始 paper
- 原版 AFL 停更後，碎片化的 fork 生態（AFLFast、CollAFL、LAF-Intel 等）各自解決一個問題但互不相容，AFL++ 解決了這個協調問題
- libFuzzer 和 Honggfuzz 是平行競品，不是 AFL 的衍生，在不同場景下各有優勢

## 自我檢核

1. AFLFast 解決了 AFL 的哪個具體問題？它的解法的名字叫什麼？和 Multi-Armed Bandit 有什麼關係？

2. REDQUEEN 的核心技術叫什麼（不是 CmpLog，是論文提出的概念）？它如何在不用 symbolic execution 的前提下突破 magic bytes？

3. 如果你看到一篇 2019 年 fuzzing 論文，baseline 是「AFL」，你能確定它比較的是原版 AFL 嗎？這對解讀實驗結果有什麼影響？

4. AFL++ 的 LTO mode（`afl-clang-lto`）解決了什麼問題？原版 AFL 為什麼沒有這個問題的完整解法？

5. 為什麼 AFL++ 的 custom mutator API 是一個重要設計？如果沒有它，想在 AFL++ 裡做 grammar-aware fuzzing 要怎麼做？

## 延伸閱讀

### 論文

- **[AFL++: Combining Incremental Steps of Fuzzing Research](https://www.usenix.org/conference/woot20/presentation/fioraldi)** — Fioraldi, Maier, Eißfeldt, Heuse, WOOT 2020
  - **核心貢獻**：系統性整合 AFL 生態多年研究，提出可組合的 fuzzer 架構
  - **讀哪裡**：Section 2（各技術背景說明）和 Section 3（每個整合模組的設計決策）
  - **和本章的關聯**：本章時間線的每個節點都在這篇 paper 的 Section 2 有對應描述

- **[Coverage-Based Greybox Fuzzing as Markov Chain (AFLFast)](https://dl.acm.org/doi/10.1145/2976749.2978428)** — Böhme, Pham, Roychoudhury, CCS 2016
  - **核心貢獻**：把 AFL 的 seed 選擇形式化為 Markov chain，推導出 power schedule 的數學基礎
  - **讀哪裡**：Section 3（Markov chain 模型），Section 4（power schedule 推導）
  - **和本章的關聯**：AFL++ 的 `-p fast`/`-p explore` 等 schedule 直接來自這篇

- **[REDQUEEN: Fuzzing with Input-to-State Correspondence](https://www.ndss-symposium.org/ndss-paper/redqueen-fuzzing-with-input-to-state-correspondence/)** — Aschermann, Schumilo, Blazytko, Gawlik, Holz, NDSS 2019
  - **核心貢獻**：不用 taint analysis 也能突破 magic bytes——觀察比較指令的 operand，找到 input 中對應位置替換
  - **讀哪裡**：Section 3（input-to-state correspondence 的定義），Section 4（colorization 技術）
  - **和本章的關聯**：AFL++ 的 CmpLog 是這篇的工程實作

### 部落格 / 技術文章

- **[AFL 原版技術白皮書](https://lcamtuf.coredump.cx/afl/technical_details.txt)** — lcamtuf
  - 原版 AFL 的第一手設計文件，bitmap 設計和 forkserver 的 rationale 都在這裡
  - 讀第一節（coverage feedback）和 forkserver 那節，10 分鐘就能建立基礎直覺

- **[lcamtuf's blog](https://lcamtuf.blogspot.com/)** — Michał Zalewski
  - AFL 作者的個人技術部落格，很多設計決策的背景說明
  - 搜尋「afl-fuzz」標籤找相關文章

### 官方文件

- **[AFL++ GitHub - docs/](https://github.com/AFLplusplus/AFLplusplus/tree/stable/docs)** — 官方文件目錄
  - `INSTALL.md`：建構選項說明
  - `fuzzing_in_depth.md`：深度使用指南（本課 Part 3-4 的實戰參考）
  - `rpc_statsd.md`：多實例監控

→ [Ch 3 — AFL++ 架構總覽](./03-afl-plus-plus-architecture.md)
