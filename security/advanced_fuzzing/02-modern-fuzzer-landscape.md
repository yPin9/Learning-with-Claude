# Ch 2 — 現代 fuzzer 全景

> **目標**：以「目標形態」為主軸重新分類現代 fuzzer，看懂它們的演化脈絡，以及本課各工具在這個地圖上的位置。讀完之後你能對任何一個 fuzzer 說出它的設計維度、它設計來解哪類問題、它的邊界在哪。

---

## 為什麼要先分類再學工具

工具名字本身說不了幾件事。「syzkaller」只告訴你它的作者叫 Dmitry Vyukov；「Fuzzilli」只告訴你有個 fuzz- 前綴。但如果你先問「這個 fuzzer 在哪幾個設計維度上做了什麼選擇」，你就能預測它能解什麼問題、在哪裡失效、和其他工具的關係是什麼。

本章的分類不是按工具名字，而是按**目標形態的四個關鍵維度**。每個維度都有一個軸，fuzzer 在這個軸上的位置決定了它的設計取捨。

---

## 先建立直覺：Fuzzer 的四個設計維度

```
Fuzzer 設計空間（4 個獨立維度）

維度 1：輸入生成策略
  Mutational ─────────────────────────── Generational/Grammar
  （改現有輸入）                          （從規則生成）
  afl++, libFuzzer                        Nautilus, Fuzzilli

維度 2：執行環境
  In-process ─────────────────────────── Out-of-process / Full-system
  （在同個程序 loop）                     （獨立 VM / 全系統快照）
  libFuzzer                               Nyx, kAFL, syzkaller

維度 3：目標狀態
  Stateless ─────────────────────────── Stateful
  （每次執行獨立）                        （執行之間有狀態延續）
  libFuzzer, afl++                        AFLNet, StateAFL, syzkaller

維度 4：Coverage/Feedback 來源
  純覆蓋率 ─────────────────────────── Hybrid（符號輔助 + 覆蓋率）
  （edge bitmap）                         （路徑約束求解）
  afl++, libFuzzer                        SymCC, Driller, QSYM
```

這四個維度是獨立的——任何一個 fuzzer 都可以在四個維度上各有一個位置。理解這點，你就能把任何你沒見過的 fuzzer 快速定位。

---

## 維度 1：Mutational vs Generational/Grammar

### Mutational fuzzing

從一個已有的輸入（seed）出發，做 bit/byte 層級的操作產生新輸入。

優點：對「格式寬鬆」的目標有效，無需任何格式知識，能發現程式設計者沒預料到的輸入。

缺點：對語法嚴格的格式，mutation 後的輸入有很高的「early rejection rate」——被 parser 的第一關擋掉，深層邏輯的 coverage 永遠到不了。

代表：afl++、libFuzzer

### Generational / Grammar-based fuzzing

從一個**形式文法**（BNF、protobuf schema、自定義 AST 規則）出發，**生成**語法合法的輸入。

優點：生成的輸入幾乎 100% 能通過 parser 的第一關，讓 fuzzer 看到更深的業務邏輯。

缺點：文法需要人工撰寫或自動推斷，工作量大；文法如果不準確，生成的輸入可能對某些語意限制無效。

代表：Nautilus（custom grammar）、Fuzzilli（針對 JS 的 FuzzIL 中間語言）

### 中間地帶：結構感知的 mutational fuzzing

libprotobuf-mutator（LPM）走的是一條中間路：它用 protobuf schema 作為**輸入結構的描述**，但 mutation 是在 protobuf 物件樹的層次上做的（修改欄位值、新增/刪除訊息），而不是 bit-level。它比純 mutational 更能保持格式合法性，但不像 grammar fuzzer 那樣從規則生成。

---

## 維度 2：In-process vs Out-of-process / Full-system

### In-process

Fuzzer 和目標程式跑在同一個程序裡。典型例子是 libFuzzer：`LLVMFuzzerTestOneInput` 和 libFuzzer runtime 一起被連結成一個 binary，每次迭代就是一個函式呼叫。

優點：極低的每次迭代開銷（沒有 fork、沒有 exec、沒有 IPC），每秒可以跑幾百萬次。

缺點：目標的 crash 會 crash 整個 fuzzer；目標無法 reset 全域狀態（只有 address sanitizer 這類 reset 才有效）；無法 fuzz kernel 或 VM。

### Out-of-process

Fuzzer 和目標執行在不同程序或 VM 裡，透過 IPC 或 snapshot 機制通訊。afl++ 的 forkserver 是最輕量的 out-of-process，每次迭代 fork 一個子程序；Nyx/kAFL 則是把整個 guest OS 的狀態快照，每次迭代從快照恢復。

優點：目標 crash 不影響 fuzzer；可以 fuzz kernel / hypervisor；可以 reset 更完整的狀態（不只是 user-space 記憶體）。

缺點：每次迭代有 IPC / context switch / snapshot restore 的開銷，每秒迭代數從幾百萬降到幾萬甚至幾千。

### Full-system

整個系統（OS + 目標）都在 fuzzer 的控制範圍內。syzkaller 用 QEMU 跑一個完整的 Linux kernel + userland，透過 SSH 或串口控制 guest 執行 syscall 序列。Nyx 用 QEMU 的 snapshot 機制把 guest 整個快照，每次迭代只需要 1–10ms 的 snapshot restore。

---

## 維度 3：Stateless vs Stateful

### Stateless

每次執行是獨立的，不依賴前一次執行的結果。afl++ 和 libFuzzer 都是 stateless——對一個文字 file parser 這完全足夠，因為 parse 一個 JSON 不需要知道之前 parse 了什麼。

### Stateful

目標的行為依賴於執行歷史（連線歷史、session 狀態、訂閱記錄）。對 stateful 目標，fuzzer 需要模擬或追蹤狀態機，生成的輸入序列（不是單一輸入）才有意義。

代表：AFLNet（以 network message 序列為輸入單位）、StateAFL（自動推斷 server 的 protocol state machine）、syzkaller（生成一個 syscall 序列，每個 syscall 的參數可能依賴前一個的回傳值）

---

## 維度 4：純覆蓋率 vs Hybrid（符號輔助）

### 純覆蓋率 feedback

Fuzzer 只用「哪些 edge 被覆蓋」作為 feedback，mutation 完全是隨機/啟發式的。這是 afl++、libFuzzer 的預設模式。

缺點：遇到「深層魔法值」問題時，coverage 飽和——fuzzer 找到 `if (x == 0xDEADBEEF)` 的 then 分支需要大量迭代（雖然 CmpLog/REDQUEEN 緩解了這點）。

### Hybrid（符號輔助）

在 fuzzer 的某個決策點，把問題轉交給符號執行引擎（Concolic execution）求解路徑約束，得到能觸發特定分支的精確輸入，再交回 fuzzer 繼續 mutation。

代表：Driller（afl++ + angr）、QSYM（afl++ + QSYM 符號執行）、SymCC（clang 插樁的 concolic 執行，效能遠高於 angr）

優點：能突破需要精確值才能觸發的深層條件（magic value、hash check）。

缺點：符號執行引擎的開銷大（比純 fuzzing 慢 10x–100x），path explosion 問題在大程式上嚴重。

---

## Fuzzing 演化史：一張時間軸

這段歷史不是為了考古，而是讓你理解「為什麼當時做了這個選擇、現在我們站在哪裡」。

```
Fuzzing 演化主線（大略年份）

1988  Miller 的最初論文 ── random byte fuzzing, command-line tools
                              │
1998–2006  文法 fuzzer 崛起 ── Peach, SPIKE, Sulley
           (針對網路協定的手工 grammar 描述)
                              │
2013  AFL 誕生 ── lcamtuf 的核心洞見：
      「用 instrumentation 反饋引導 random mutation」
      第一次讓 mutation fuzzing 有了方向感
                              │
2016  libFuzzer 成為主流 ── clang 集成，in-process，極高速
                              │
2017–2019  結構感知時代
      libprotobuf-mutator（2017）── protobuf schema + mutation
      Nautilus（2019）── coverage-guided + grammar
      Fuzzilli（2019）── JS 引擎專用 fuzzing
                              │
2019  afl++ 誕生 ── 整合所有最佳化（CmpLog、REDQUEEN、
      MOpt、CollAFL...）到一個工具，成為 benchmark 基準
                              │
2020–2021  Snapshot / 全系統
      Nyx（2021）── Intel PT + QEMU snapshot，極速 kernel fuzzing
      kAFL（2017 起迭代）── 同系列
                              │
2020–2022  Kernel fuzzing 成熟
      syzkaller（2015 起）成為 Google 生產基礎設施
      syzbot 每天發現數個 kernel bug
                              │
2022–  Hybrid & Directed
      AFLGo（2017）── directed fuzzing，針對特定 patch/目標程式碼
      SymCC（2020）── 比 KLEE/angr 快 3 個數量級的 concolic
      SymQEMU（2021）── 不需源碼的 concolic，在 QEMU 插樁
                              │
2023+  LibAFL 元件化
      LibAFL（Rust，Fioraldi et al.）── 把所有組件拆成
      可組合的 Rust crate，讓你用 20 行 Rust 拼出一個
      針對你的目標的 fuzzer
```

關鍵的幾個轉折點：

1. **AFL 的洞見（2013）**：coverage feedback 讓 mutation 有了方向；在這之前 mutation 是盲目的。
2. **結構感知（2017–2019）**：光有 feedback 不夠，輸入的語法結構也得感知，否則 semantic barrier 擋死深層邏輯。
3. **全系統/Snapshot（2017–2021）**：把 fuzzing 的目標從 user-space binary 擴展到 kernel/hypervisor，代價是速度下降但覆蓋面大幅提升。
4. **元件化（LibAFL, 2022–）**：與其每次從頭 fork afl++，不如把 fuzzer 組件抽象出來，讓工程師直接拼組件。

---

## 大表：本課工具在四個維度的位置

| 工具 | 輸入策略 | 執行環境 | 目標狀態 | Feedback 來源 | 本課 Part |
|------|---------|---------|---------|-------------|---------|
| **libFuzzer** | Mutational（+CmpLog） | In-process | Stateless | Edge coverage | 基礎/Part 3 |
| **afl++** | Mutational（+REDQUEEN） | Out-of-process（forkserver） | Stateless | Edge bitmap | 先修 |
| **LibAFL** | 可組合（任意） | 可組合（任意） | 可組合 | 可組合 | Part 1 |
| **libprotobuf-mutator** | Structural mutation | In-process（+libFuzzer） | Stateless | Edge coverage | Part 2 |
| **Nautilus** | Grammar-based | In-process（+libFuzzer） | Stateless | Edge coverage | Part 2 |
| **AFLNet** | Mutational（message-level） | Out-of-process（network） | Stateful | Edge bitmap + state | Part 3 |
| **StateAFL** | Mutational + state-aware | Out-of-process | Stateful | Edge + protocol state | Part 3 |
| **syzkaller** | Grammar（syzlang）| Full-system（QEMU） | Stateful（syscall seq）| KCOV edge | Part 4 |
| **Nyx / kAFL** | Mutational | Full-system（snapshot）| Reset-per-iter | Intel PT | Part 5 |
| **Fuzzware** | Mutational（+MMIO model） | Full-system（unicorn/QEMU）| Semi-stateful | Edge（unicorn）| Part 6 |
| **Fuzzilli** | Grammar（FuzzIL） | Out-of-process（JS engine）| Stateless | Edge（patched V8/JSC）| Part 7 |
| **SymCC** | Symbolic（exact） | In-process（plugin）| Stateless | Path constraint | Part 8 |
| **AFLGo** | Directed（distance） | Out-of-process | Stateless | Edge + distance | Part 8 |

---

## 底層機制：為什麼「元件化」是現在的趨勢

2022 年以後，fuzzing 領域出現了一個有趣的轉向——研究者開始認為，「造一個新 fuzzer」這件事的門檻太高，很多好的想法因為實作成本而沒有被驗證。LibAFL 試圖解決這個問題，把 fuzzer 拆成獨立的 Rust trait：

```
LibAFL 的組件抽象

┌──────────────────────────────────────────────────────────┐
│  你的 fuzzer = 以下組件的組合                             │
│                                                          │
│  Input          ── 輸入的表示方式（bytes / AST / syscall）│
│  Corpus         ── 如何儲存有價值的輸入                  │
│  Mutator        ── 如何修改輸入                          │
│  Stage          ── mutation 的迭代策略                   │
│  Executor       ── 如何執行目標（in-proc / fork / net）   │
│  Observer       ── 觀察什麼（coverage / return value）   │
│  Feedback       ── 什麼算「有價值」                      │
│  Scheduler      ── 下一個選哪個 corpus seed              │
└──────────────────────────────────────────────────────────┘

換目標 = 換 Executor + 換 Input 型別
換 mutation 策略 = 換 Mutator + 換 Stage
加 grammar = 加一個 grammar-aware Mutator
加 symbolic feedback = 加一個 SymCC Observer
```

這個設計讓「為一個新目標造一個 fuzzer」從「fork afl++ 改 5000 行 C」變成「用現有組件拼，只寫 target-specific 的部分」。Part 1 整個 Part 都圍繞這個設計。

---

## 對比取捨表：In-process vs Full-system

| 指標 | In-process（libFuzzer） | Full-system（Nyx/syzkaller） |
|------|------------------------|---------------------------|
| 每秒執行次數 | 10⁶ 量級 | 10³–10⁴ 量級 |
| Reset 範圍 | User-space 記憶體 | 整個 VM 狀態（包括 kernel）|
| 能 fuzz 的目標 | User-space library / binary | Kernel / hypervisor / firmware |
| 環境需求 | 普通 Linux | KVM + Intel PT（bare-metal）|
| Crash isolation | 差（crash = fuzzer 掛掉，需 ASan） | 好（VM crash 不影響 fuzzer）|

---

## 踩雷集錦

**踩雷 1：用工具名字記分類，記混了**

「syzkaller 是 stateful 的嗎？」這個問題本身有歧義——syzkaller 生成的是 syscall **序列**（sequence），每個 syscall 的參數可能引用前一個 syscall 的回傳值（如 fd），所以在輸入生成層面是 stateful；但每次執行用 QEMU snapshot 完全 reset，所以在執行環境層面是 stateless-per-iter。分類必須說清楚「在哪個維度」。

**踩雷 2：把「Grammar-based」等同於「需要人工寫文法」**

Nautilus 最初的設計是讓用戶寫 BNF 文法。但後來有自動文法推斷工具（如 GRIMOIRE、Glade）可以從一組 valid sample 逆向推斷文法。「Grammar-based = 人工成本高」是 2018 年以前的狀態，不是現在的必然。Part 2 Ch 14 會討論這一演化。

**踩雷 3：以為 Hybrid fuzzing 總是比純 fuzzing 好**

SymCC 比 afl++ 多了路徑約束求解，但它同時也**慢很多**。在一個執行環境已知、目標比較簡單的情況下，加了符號執行反而是負擔——因為你的 fuzzing 迭代速度降了一個數量級，而符號執行帶來的覆蓋率提升不足以補償這個損失。Hybrid 的真正價值在「純 coverage-guided 飽和了之後」。

**踩雷 4：LibAFL 是「比 afl++ 更好的 afl++」**

LibAFL 不是 afl++ 的替代品，它是一個 fuzzer 框架，用來造你自己的 fuzzer。用它造出來的 fuzzer，performance 在某些場景下比 afl++ 好，在另一些場景下不如 afl++（因為 afl++ 多年優化的啟發式在框架化之後不一定完整保留）。LibAFL 的價值是「客製化彈性」，不是「現成開箱效能」。

---

## 動手練習

1. 在本章的大表裡，選三個你沒用過的工具（如 Nautilus、StateAFL、AFLGo），各找一篇介紹它們的論文摘要或 README，確認這個表的分類是否和論文自述一致。有出入的話記下來。

2. 對一個你感興趣的目標（kernel module、TLS library、JS 程式），用四個維度分析：(a) 它最適合的輸入策略是什麼？(b) 最適合的執行環境？(c) 它有狀態嗎？(d) 純覆蓋率 feedback 夠嗎？根據分析，從大表裡選出最合適的工具。

3. LibAFL 的 GitHub repo（`AFLplusplus/LibAFL`）裡有一個 `fuzzers/` 目錄，裡面有多個範例 fuzzer。讀三個最簡單的 `Cargo.toml`，看看每個範例引用了哪些 LibAFL 組件（Observer、Executor、Feedback 等），對照本章的組件分類表。

---

## 本章重點

- 現代 fuzzer 的設計可以用四個維度描述：輸入策略、執行環境、目標狀態、feedback 來源。任何 fuzzer 在每個維度上都有一個位置。
- Fuzzing 演化的主線：盲目 random → coverage-guided mutation → 結構感知 → 全系統 snapshot → 元件化（LibAFL）。
- LibAFL 的核心貢獻是把 fuzzer 組件化，讓「為特定目標造 fuzzer」的工程成本大幅降低。
- 每個工具有它的最適場景；沒有「總是最好的 fuzzer」，只有「最適合這個目標形態的 fuzzer」。

---

## 自我檢核

不翻書回答：

- [ ] LibAFL 和 afl++ 是什麼關係？LibAFL 裡的「Executor」對應 afl++ 的哪個部分？
- [ ] syzkaller 在四個維度上的位置各是什麼？為什麼它在「目標狀態」維度上比較特殊？
- [ ] Fuzzilli 屬於哪個輸入策略維度？它和 afl++ 加 JS dictionary 的本質差異是什麼？
- [ ] Nyx 和 syzkaller 都是 Full-system，它們的主要差別在哪個維度上？
- [ ] SymCC 在 Hybrid 維度上和 Driller（afl++ + angr）有什麼效能差異？原因是什麼？

---

## 延伸閱讀

1. **[LibAFL 論文](https://dl.acm.org/doi/10.1145/3548606.3560602)**（CCS 2022，Fioraldi et al.）——設計哲學那一節（§3）解釋了為什麼要把 fuzzer 組件化、每個組件的抽象選擇；這是 Part 1 整個 Part 的理論基礎，先看完這篇再讀 Part 1 事半功倍。

2. **[The Fuzzing Book 第一章](https://www.fuzzingbook.org/html/Fuzzer.html)**（Zeller et al.）——以 Python 互動方式展示 random fuzzing → mutation fuzzing → grammar fuzzing 的演化，是本章時間軸的可執行版本；如果你覺得時間軸太抽象，來這裡跑幾個 notebook cell。

3. **[SoK: The Progress, Challenges, and Perils of Firmware Security](https://ieeexplore.ieee.org/document/8835340)**（SP 2019，Muench et al.）——針對韌體安全的 SoK，把「rehosting」問題（牆 3）系統化；第三章的分類（static/dynamic/emulation）對應本章 Out-of-process 和 Full-system 的差異；Part 6 開頭前的必讀。

---

分類地圖建好了。接下來深挖 coverage 這個最基礎的 feedback 機制——因為無論選哪個 fuzzer，coverage 都是驅動 fuzzer 前進的引擎，你必須知道它的極限在哪。

→ [下一章：Ch 3 覆蓋率的本質再訪](./03-coverage-feedback-revisited.md)
