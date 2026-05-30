# Ch 1 — Fuzzing 流派
> **目標**：能清楚解釋 blackbox / grammar-based / coverage-guided 三種 fuzzing 流派的差異，以及為什麼 coverage-guided 成為主流，同時知道它的邊界在哪裡。

## 為什麼需要這個？

在有 fuzzing 工具之前，找 parser 的 bug 靠的是手動審計或運氣。1988 年 Barton Miller 把隨機字元餵給 UNIX utilities 發現 25–33% 會 crash——這個結果讓整個安全社群震驚，因為它說明「程式設計師寫的錯誤處理其實沒有他們以為的那麼可靠」。

問題是：純隨機輸入在探索深層邏輯上幾乎沒有效率。如果一個函式需要輸入以 `\x7fELF` 開頭才會進入 ELF parsing 分支，隨機 fuzzer 撞到這個前綴的機率是 1/2^32——在現實時間裡等同零。三十年來，fuzzing 的主要演化方向就是：**如何讓 fuzzer 不靠蠻力也能探索到深層程式碼**。

## 先建立直覺

三種 fuzzing 流派對應三種「在黑暗中找路」的策略：

```
Blackbox fuzzer（dumb fuzzer）
  程式是一個黑盒子。我往裡面丟東西，看門有沒有開。
  不管開沒開，下次還是隨機丟。
  效率：靠運氣。

         [random/mutated input] ──→ [目標程式] ──→ crash? yes/no
                   ↑                                      │
                   └──────────────────────────────────────┘
                   （無 feedback，每次都一樣）

Grammar-based fuzzer
  我知道門鎖的格式——鑰匙必須是這個形狀。
  我根據 grammar 生成「看起來合法」的鑰匙。
  但我不知道門開了之後裡面有沒有其他鎖。

         [grammar spec] ──→ [generator] ──→ [目標程式] ──→ crash?
         （需要人工寫 grammar，生成的輸入格式合法但內容未知）

Coverage-guided fuzzer（greybox）
  我丟一個東西進去，然後偷看：它走了哪條路？
  如果這次走了一條新路，我把這個輸入存起來，以後在它的基礎上繼續變異。
  我不懂程式的全貌（不是 whitebox），但我有 coverage 當路標。

         [mutated input] ──→ [插樁的目標程式] ──→ coverage trace
               ↑                                          │
               └──────────── 如果是新 path，存入 corpus ──┘
               （有 feedback，能學習）
```

---

## Fuzzing 的本質定義

Fuzzing（模糊測試）的核心操作只有一件事：**自動化產生大量輸入，把它們餵給目標程式，觀察是否發生非預期行為（crash、hang、assertion failure、記憶體錯誤）**。

注意兩個關鍵詞：「自動化」和「非預期行為」。Fuzzing 不是在驗證程式符合規格——它是在尋找程式假設不成立的邊界。這個區別很重要，因為它決定了 fuzzing 能找到什麼類型的 bug：任何導致程式行為異常的輸入都是目標，包括緩衝區溢位、整數溢位、空指標解引用、除以零、邏輯錯誤（透過 assert）。

---

## Blackbox Fuzzing：無 Feedback 的蠻力

### 什麼是 Blackbox Fuzzer

Blackbox fuzzer（又稱 dumb fuzzer）把目標程式當成完全不透明的黑盒子。它不在乎程式內部做了什麼，只在乎：丟進去的東西有沒有讓它 crash。

**典型工具**：

- **zzuf**：隨機翻轉 input 的 bits，可以設定翻轉比例（`-r 0.01` = 1% 機率翻轉每個 bit）
- **Radamsa**：更聰明的 blackbox mutator，有一套內建的變異規則（重複段、插入特殊值、截斷等）
- **Spike**：針對網路協議的 dumb fuzzer

```bash
# zzuf 範例：對 file 工具做 blackbox fuzzing
zzuf -r 0.01 -s 0:100 file /bin/ls
# -r 0.01：1% bit flip ratio
# -s 0:100：試 seed 0 到 100（每個 seed 產生一個不同的隨機變異）
```

### 什麼情況 Blackbox 還是夠用

別急著否定 blackbox——它在三種情境下仍然有價值：

1. **目標極其脆弱**：早期的很多 image parser（JPEG、PNG 早期實作）對隨機翻轉非常敏感，1% 翻轉比例能快速找到 crash。
2. **格式結構簡單**：對只有幾個欄位的二進位格式，隨機變異的覆蓋率和 grammar-based 差距不大。
3. **不能改 binary**：有些商業軟體或 BIOS 固件無法重新編譯，blackbox 是唯一選項（或者用 QEMU/Frida mode 的 coverage-guided，但那是第三種流派）。

---

## Grammar-based Fuzzing：用格式知識引導生成

### 什麼是 Grammar-based Fuzzer

Grammar-based fuzzer（又稱 generational fuzzer）的出發點是：**如果我知道輸入格式，我就能生成「格式正確但內容故意奇怪」的輸入**。這比隨機翻轉有效，因為程式不會在 parser 入口就拒絕輸入，能進入更深的處理邏輯。

**典型工具**：

- **Peach Fuzzer**：用 XML 描述檔案格式，然後按格式生成和變異輸入。曾被用來 fuzz 瀏覽器和文件解析器。
- **boofuzz**（Spike 的繼任者）：針對網路協議，用 Python DSL 描述協議結構
- **Dharma**（Mozilla）：基於 grammar 的 JavaScript/DOM fuzzer
- **Domato**（Google Project Zero）：針對 DOM 的生成型 fuzzer

```python
# boofuzz 範例：描述一個簡單的 TLV 協議
from boofuzz import *

session = Session(target=Target(connection=TCPSocketConnection("127.0.0.1", 4444)))

s_initialize("request")
with s_block("header"):
    s_byte(0x01, name="type")         # type field
    s_word(0, name="length")          # length field（會被自動 fuzz）
s_string("AAAA", name="value")        # value field
```

### Grammar-based 解決了什麼，代價是什麼

解決了：能到達 parser 之後的深層邏輯。一個正確格式的 HTTP request 能讓 fuzzer 到達 URL routing、body 解析、業務邏輯層——純隨機輸入根本進不去。

代價：**需要人工寫 grammar**。Grammar 的品質決定 fuzzer 的天花板。如果 grammar 不精確（漏了某些欄位的合法值範圍），fuzzer 永遠不會生成那些值。寫一個完整的瀏覽器 JavaScript grammar 可能要幾個月，而且每次引擎版本更新都需要維護。

---

## Coverage-guided（Greybox）Fuzzing：AFL 的革命

### 什麼是 Coverage-guided Fuzzer

Coverage-guided fuzzer 是 2014 年 AFL（American Fuzzy Lop）帶來的典範轉移。它的核心想法：**不需要知道輸入格式，只需要觀察程式的執行路徑，把走出新路徑的輸入存下來**。

「Greybox」這個名字來自一個類比：
- **Whitebox testing**：知道完整原始碼，做全路徑分析（symbolic execution / taint analysis）
- **Blackbox testing**：什麼都不知道，純 I/O 觀察
- **Greybox testing**：知道一點點——只知道「這個輸入觸發了哪些 edges」，但不做全程序分析

Greybox 的 overhead 比 whitebox 小很多（不做 SMT solving、不做 constraint propagation），比 blackbox 有效很多（有 feedback 能引導探索）。

### AFL 的核心反饋迴路

```
初始 corpus (seeds/)
       │
       ▼
┌─────────────────────┐
│   取出一個種子      │◄───────────────────────────────────┐
└─────────┬───────────┘                                    │
          │                                                 │
          ▼                                                 │
┌─────────────────────┐                                    │
│   Mutation engine   │ (bit flip, byte flip, havoc...)    │
└─────────┬───────────┘                                    │
          │ 生成 test case                                  │
          ▼                                                 │
┌─────────────────────┐                                    │
│   執行插樁後的      │                                    │
│   目標程式          │                                    │
└─────────┬───────────┘                                    │
          │                                                 │
    ┌─────┴──────┐                                         │
    │            │                                         │
    ▼            ▼                                         │
 crash?     新的 coverage edges?                           │
  │              │                                         │
  │              ├── YES → 加入 corpus ────────────────────┘
  │              └── NO  → 丟棄
  │
  └── 存入 crashes/
```

### 為什麼這個設計有效

關鍵洞察：**程式的 bug 通常藏在很少被執行到的程式碼路徑上**。Coverage-guided fuzzer 會主動探索新路徑，而不是反覆執行相同的路徑。每次找到一個新 edge，就相當於「開啟了程式內部的一扇新門」，那扇門後面可能還有更多門。

相比 blackbox fuzzer 的隨機試探，coverage-guided fuzzer 能在沒有格式知識的情況下，逐漸學會「什麼樣的輸入能讓程式走得更深」。

---

## 底層機制：三者的 Throughput 與 Coverage 比較

### 實測數字（引用論文）

Klees et al. 的「Evaluating Fuzz Testing」（CCS 2018）對多個 fuzzer 做了系統性對比。以下數字來自論文 Table 3 和 Table 5（在 LAVA-M benchmark 上）：

| Fuzzer 類型 | 典型 exec/sec | 24h 後 branch coverage | 找到 bug 數 (LAVA-M) |
|------------|--------------|----------------------|---------------------|
| Blackbox (zzuf-like) | 10,000–50,000 | 低（< 20%） | 少（< 50）|
| Grammar-based (Peach) | 100–5,000 | 中（視 grammar 品質） | 中（格式相關 bug 多）|
| Coverage-guided (AFL) | 1,000–10,000 | 高（> 60%） | 多（> 200）|

重要注意：這些數字高度依賴 target 的特性。Grammar-based fuzzer 在需要格式合法的 target（如 PDF 解析器、JavaScript 引擎）上可以超越 coverage-guided。

Böhme et al. 的 AFLFast（CCS 2016）進一步指出：原版 AFL 大部分時間都在重複執行同樣的高頻路徑，`find_new_path` 效率可以透過改進 power schedule 提升 7–35 倍。

### 為什麼 Coverage-guided 成為主流

1. **不需要 grammar**：大多數目標的格式文件不完整，或格式本身太複雜，寫完整 grammar 的成本比 fuzzing 的收益還高。
2. **自動學習**：coverage-guided fuzzer 能自己發現「輸入的哪些位元組對分支有意義」，不需要人工標注。
3. **可擴展**：同一個 fuzzer 可以跑完全不同類型的 target，從圖片解析到網路協議。

---

## Coverage-guided 不是萬能：三大邊界

### 邊界 1：Magic Bytes 和 Checksum

```c
// 這種 code 讓 coverage-guided fuzzer 非常頭痛
if (memcmp(header, "\x89PNG\r\n\x1a\n", 8) != 0) {
    return ERROR_BAD_MAGIC;  // 99.99% 的變異輸入都會在這裡停住
}
```

AFL 的 bit flip 變異很難靠運氣撞到正確的 8 個 magic bytes（機率 1/2^64）。這就是 CmpLog / RedQueen（Ch 15）要解決的問題。

### 邊界 2：Checksum 驗證

```c
uint32_t crc = compute_crc32(data, len);
if (crc != expected_crc) {
    return ERROR_CHECKSUM_FAIL;
}
```

Fuzzer 隨機翻轉 data 後，CRC 就對不上，程式在 parser 之前就 reject 了輸入。解法是在 harness 裡 patch 掉 checksum 檢查（見 Ch 17）。

### 邊界 3：深層狀態機

有些程式有複雜的狀態（例如 TLS handshake 協議），必須先完成幾個步驟才能進入 fuzzing 有意義的狀態。Stateless fuzzer 每次都從頭開始，永遠到不了後面的狀態（見 Ch 21）。

---

## 歷史背景

### 1988–2013：隨機的時代

| 年份 | 事件 |
|------|------|
| 1988 | Barton Miller 的學生在 Unix shell 上做隨機輸入實驗，25–33% 的 UNIX utilities crash |
| 1990 | Miller 等人發表論文「An Empirical Study of the Reliability of UNIX Utilities」，fuzzing 有了學術定義 |
| 2002 | SPIKE 發布，第一個廣泛使用的網路協議 blackbox fuzzer |
| 2007 | Peach Fuzzer 發布，grammar-based fuzzing 開始普及 |
| 2008 | Charlie Miller 的「Fuzz by Numbers」確立了「自動化比手動審計便宜」的論點 |

### 2014：AFL 的典範轉移

Michał Zalewski（lcamtuf）發布 AFL，帶來了三個關鍵創新：
1. **Edge coverage bitmap**：用 64KB 的共享記憶體 bitmap 追蹤分支跳轉，overhead 極低
2. **Forkserver**：避免每次都從頭 exec，大幅提升 throughput
3. **Corpus 最小化**：自動找出觸發同等 coverage 的最小輸入集合

AFL 的出現讓 fuzzing 從「專家工具」變成「工程師工具」。六個月內它找到了 OpenSSL、GnuTLS、libpng 等軟體的大量 CVE。

### 2016 年之後：三足鼎立

| 工具 | 發布 | 特點 |
|------|------|------|
| AFL++ | 2019（AFL 2014） | 社群維護版，整合多項研究成果（CmpLog、LTO、custom mutator） |
| libFuzzer | 2015 | Google 出品，in-process fuzzing，需要寫 `LLVMFuzzerTestOneInput` harness |
| Honggfuzz | 2016 | Google 出品，feedback 機制比 AFL 更細（instruction count 等） |

---

## 對比與取捨

| 特性 | Blackbox | Grammar-based | Coverage-guided |
|------|----------|---------------|-----------------|
| **輸入生成方式** | 隨機 / 固定規則變異 | 根據 grammar 生成 / 變異 | 基於 coverage feedback 變異 corpus |
| **反饋機制** | 無（純 I/O） | 無（或非 coverage） | Edge coverage bitmap（AFL）/ LLVM SanitizerCoverage |
| **Overhead** | 極低（不需插樁） | 低到中（生成有成本） | 低（插樁 ~1-5% 性能損失） |
| **需要格式知識** | 不需要 | 需要（寫 grammar） | 不需要 |
| **需要原始碼** | 不需要 | 不需要 | 需要（或用 QEMU/Frida） |
| **適用場景** | 無源碼 target；格式簡單；快速掃描 | 格式嚴格的 parser（PDF、JS、HTML）；需要深層業務邏輯 | 有源碼的 C/C++ 程式；格式未知；需要系統性探索 |
| **Magic bytes** | 無法突破（純隨機） | 可以（grammar 直接生成） | 難，需要 CmpLog / LAF 輔助 |
| **狀態機** | 無法處理 | 可以（grammar 描述狀態轉換） | 難，需要 snapshot / 特殊 harness |

---

## Grammar-based 和 Coverage-guided 不是互斥的

值得特別說明：Hybrid fuzzer 把兩種方法結合：

- **Nautilus（NDSS 2019）**：先用 grammar 保證輸入格式合法，再在合法範圍內做 coverage-guided mutation
- **Superion（ICSE 2019）**：用 grammar 解析輸入成 AST，在 AST 層面做變異
- AFL++ 的 custom mutator API（Ch 18）讓你可以把任何 grammar-based generator 接進 AFL++ 的 feedback 迴路

這是正確的工程思維：「兩種方法有各自的優勢，結合它們比堅持純粹一種更有效」。

---

## 踩雷集錦

1. **很多人以為 coverage 越高 bug 越多，但實際上** coverage 只是告訴你「哪些程式碼被執行過」，不告訴你「那些程式碼有沒有 bug」。100% branch coverage 不代表沒有 bug，只代表每條分支都被執行過至少一次。Klees et al. (CCS 2018) 的研究明確指出：不同 fuzzer 之間 coverage 相近，但 bug 發現數量差距可達數倍。

2. **很多人以為 fuzzing 只能找記憶體安全 bug（overflow、UAF），但實際上** fuzzing 可以找任何導致「可觀察到的非預期行為」的 bug：整數溢位（透過 `-fsanitize=integer`）、邏輯 bug（透過插入 `assert`）、並發問題（透過 TSan + fuzzing）。工具的邊界是你想找的 bug 是否能被 signal/sanitizer 偵測到，而不是 bug 的類型。

3. **很多人以為 grammar-based 和 coverage-guided 是競爭關係，但實際上** 它們在不同層面解決問題：grammar 解決「如何生成格式合法的輸入」，coverage-guided 解決「如何決定哪些輸入值得繼續探索」。最強的 fuzzer（如 Nautilus）把兩者結合。在選工具時問的問題是「這個 target 的格式合規程度有多重要」，而不是「哪個流派更好」。

4. **很多人以為 AFL 是「whitebox fuzzer」，因為它需要插樁（看到內部資訊），但實際上** AFL 的分類是 **greybox**：它只知道 edge coverage（哪些分支被執行），不做任何 symbolic execution 或 constraint solving（那才是 whitebox）。Greybox 的定義是：有有限的內部資訊（coverage），但不做全程序分析。

5. **很多人以為 1988 年 Miller 的實驗用的是「sophisticated fuzzer」，但實際上** 他用的是 `dd if=/dev/random | command`——字面上的隨機 bytes 餵給 UNIX utilities。找到 bug 的比例（25-33% crash）說明的是那個年代的程式品質，而不是 fuzzing 技術有多厲害。

---

## 進階：再往深一層

Symbolic execution（符號執行）是 whitebox testing 的代表，它能解決 fuzzing 碰到的 magic bytes 問題：

```
Symbolic execution 的方式：
  把 x = input[0..7] 設為「未知符號」
  執行程式時追蹤所有符號表達式
  到達 if (x == MAGIC_BYTES) 時
  → 用 SMT solver（如 Z3）解出：x = "\x89PNG\r\n\x1a\n"
  → 把這個值餵給 fuzzer
```

為什麼不直接用 symbolic execution 取代 fuzzing？因為**路徑爆炸（path explosion）**：每個 if-else 讓狀態空間加倍，真實程式有數百萬個 branch，SMT solver 根本解不完。

這就是「混合 fuzzing（hybrid fuzzing）」出現的原因：fuzzing 快速探索大部分路徑，symbolic execution 只用來解決 fuzzing 卡住的特定 constraint（DRILLER 2016、QSYM 2018）。

AFL++ 的 CmpLog / RedQueen（Ch 15）是一種輕量型的 hybrid：不做完整 symbolic execution，只記錄比較指令的操作數，然後直接把記錄到的值代入輸入。這是以精度換速度的務實設計。

---

## 動手練習

1. 安裝 `zzuf`，對 `file /bin/ls` 做 100 個 seed 的 blackbox fuzzing，記錄有沒有 crash（`zzuf -r 0.01 -s 0:100 file /bin/ls 2>&1 | grep -i "signal\|crash\|killed"`）。
2. 讀 AFL 的 technical_details.txt 的「Coverage measurements」一節（[連結](https://lcamtuf.coredump.cx/afl/technical_details.txt)），找出 AFL 把 edge 表示成 bitmap index 的公式，用自己的話寫下來。
3. 思考練習：如果你要 fuzz 一個 OpenSSL TLS 實作（需要完整 handshake 才能到達 application data 處理邏輯），你會選哪種 fuzzing 流派？為什麼？需要做什麼特殊處理？

---

## 本章重點整理

- Fuzzing 三大流派的核心區別在「反饋機制」：blackbox 沒有、grammar-based 靠格式知識、coverage-guided 靠 edge coverage bitmap。
- Coverage-guided 成為主流的原因不是它最聰明，而是它的 precision/cost 比最好：不需要 grammar、能自動學習、overhead 低、可泛用。
- Coverage-guided 的三大邊界：magic bytes、checksum 驗證、深層狀態機——這些限制決定了後面 Ch 15（CmpLog）、Ch 17（harness 設計）、Ch 21（困難 target）要解決的問題。

---

## 自我檢核

- 不看文件，解釋「greybox fuzzing」的「greybox」指的是什麼，和 whitebox / blackbox 的差異各在哪裡。
- 一個 target 有複雜的 magic header，你用 AFL++ 跑了一小時，`total paths` 增長極慢（停在 5 以下），最可能的原因是什麼？你的下一步是什麼？
- 為什麼 symbolic execution（whitebox）找到的 path 數量比 coverage-guided 少，但它能找到 coverage-guided 找不到的 bug？這說明了什麼？
- Grammar-based fuzzer 和 coverage-guided fuzzer 結合時，各自扮演什麼角色？一個具體的結合方式是什麼？

---

## 延伸閱讀

### 論文

- **[An Empirical Study of the Reliability of UNIX Utilities](https://ftp.cs.wisc.edu/pub/paradyn/technical_papers/fuzz.pdf)** — Miller et al., CACM 1990
  - **核心貢獻**：第一篇系統性研究 fuzzing 有效性的論文，確立了「隨機輸入能找到真實 bug」這個事實
  - **讀哪裡**：Section 3（方法論）和 Section 4（結果），特別是 Table 1 的 crash 比例
  - **和本章的關聯**：這是 blackbox fuzzing 的奠基工作，讀懂它才能理解為什麼後來的人覺得「能做得更好」

- **[Fuzzing: Art, Science, and Engineering](https://arxiv.org/abs/1812.00140)** — Liang et al., IEEE TSE 2018
  - **核心貢獻**：2018 年最完整的 fuzzing survey，把所有 fuzzing 技術分類整理，包含 270+ 篇引用
  - **讀哪裡**：Section 2（taxonomy），先建立分類框架，其他章節按需查閱
  - **和本章的關聯**：本章的三流派分類來自這篇 survey 的框架，讀原文能看到更細的子分類

- **[Evaluating Fuzz Testing](https://dl.acm.org/doi/10.1145/3243734.3243804)** — Klees et al., CCS 2018
  - **核心貢獻**：用嚴格的統計方法指出現有 fuzzing 論文的 benchmark 問題，包括樣本不足、時間太短、coverage 和 bug 發現不對應等
  - **讀哪裡**：Section 4（Pitfalls in Fuzzing Evaluations），直接看他們找到的問題清單
  - **和本章的關聯**：本章引用的 coverage vs bug 數量不對應的論點直接來自這篇

- **[Coverage-based Greybox Fuzzing as Markov Chain (AFLFast)](https://dl.acm.org/doi/10.1145/2976749.2978428)** — Böhme et al., CCS 2016
  - **核心貢獻**：把 AFL 的 seed 選擇行為建模成 Markov Chain，證明 AFL 大量時間在低頻路徑上浪費，提出 power schedule 改進
  - **讀哪裡**：Section 2（AFL 行為分析）和 Section 3（改進的 power schedule 設計）
  - **和本章的關聯**：解釋了為什麼 coverage-guided fuzzing 的「coverage 高不等於效率高」

### 部落格 / 技術文章

- **[The Fuzzing Book — Chapter 1: Fuzzing: Breaking Things with Random Inputs](https://www.fuzzingbook.org/html/Fuzzer.html)** — Zeller et al. (fuzzingbook.org)
  - **這篇說什麼**：用可執行的 Python 程式碼從零實作 blackbox fuzzer，帶出 coverage 引導的基本概念
  - **讀哪裡**：整個 Chapter 1，然後看 Chapter 2（Coverage）的開頭
  - **為什麼值得讀**：程式碼可以直接跑，能親眼看到「隨機輸入有多沒效率」這件事

- **[lcamtuf — Pulling JPEGs out of thin air](https://lcamtuf.blogspot.com/2014/11/pulling-jpegs-out-of-thin-air.html)** — lcamtuf (lcamtuf.blogspot.com, 2014)
  - **這篇說什麼**：AFL 從一個純文字種子開始，不靠任何 JPEG 格式知識，自動演化出有效的 JPEG 結構
  - **讀哪裡**：整篇（很短，有圖），看 corpus 演化的截圖
  - **為什麼值得讀**：這篇是「coverage-guided fuzzer 為什麼不需要 grammar」最直觀的說明

### 官方文件

- **[AFL technical details](https://lcamtuf.coredump.cx/afl/technical_details.txt)**
  - **讀哪裡**：「Coverage measurements」一節（前三分之一），現在先理解 edge coverage 的概念，bitmap 的具體實作 Ch 5 再深入

→ [Ch 2 — AFL 家族樹](./02-afl-family-tree.md)
