# Ch 1 — Fuzzing 三種流派：blackbox / grammar / coverage-guided

> 目標：從「為什麼 random bytes 行不通」出發，說清楚 blackbox、grammar-based、coverage-guided 三種 fuzzing 流派的差異，以及 coverage-guided 為什麼在 2014 年之後變成主流。

## Fuzzing 的一句話定義

把亂七八糟的輸入丟給程式，看它會不會崩潰。就這樣。

但這句話藏了幾個層次：

- **亂七八糟的輸入**怎麼來？隨機產生？變異一份既有樣本？依 grammar 生成？
- **丟給程式**怎麼丟？stdin？檔案？網路封包？in-process function call？
- **看它會不會崩潰**夠嗎？只看 SIGSEGV 是不是太被動？能不能主動「引導」fuzzer 去沒走過的路？

三種流派的差別就在「輸入從哪來」與「要不要回饋」。

## 流派一：Blackbox

**代表**：zzuf、radamsa（它其實也能做 grammar）、早期的 MiniFuzz。

**作法**：拿一份正常樣本（seed），對它做隨機 bit flip、byte swap、truncate、insert random bytes... 一堆啟發式 mutation。把變異過的樣本餵給 target，看會不會崩。target 內部完全不關心。

```
┌──────────┐   mutate    ┌──────────┐    run     ┌─────────┐
│ seed.png │ ─────────▶  │ new.png  │ ────────▶  │ target  │ → crash?
└──────────┘             └──────────┘            └─────────┘
```

**優點**：超簡單、target 不用改一行、適用任何 binary。

**致命傷**：對「有結構的輸入」幾乎無望。隨便變一個 PNG，CRC32 對不上，target 在 parser 的第一關就把你拒絕 — 根本還沒碰到 bug 可能藏著的程式碼。更別提 `if (magic == 0xDEADBEEF)` 這種，亂 flip bit 的機率是 $2^{-32}$。

## 流派二：Grammar-based

**代表**：Peach Fuzzer、Domato（Google，JS engine fuzzing）、Nautilus、Grammarinator。

**作法**：把輸入格式的 grammar 寫出來（BNF 或 XML schema），fuzzer 依 grammar 產生合法或半合法的樣本。

```
grammar:
  json  := object | array
  object := "{" pair ("," pair)* "}"
  pair  := string ":" value
  ...

產出:
  {"a": 1}        合法
  {"a": [[[...]]]}    合法但極端
  {"a": :}        半合法，故意違反
```

**優點**：很快越過 parser 的第一關，觸及深層邏輯。對 JS engine、XML parser、SQL engine 這種強 schema 輸入尤其有效。

**致命傷**：你要**自己寫 grammar**，而真實格式（HTTP、HEVC、protobuf）grammar 規格龐大且充滿 corner case。再來就是，grammar fuzzer 「不知道」target 對某個 production rule 有沒有興趣 — 它只是在 grammar space 裡瞎跑。

## 流派三：Coverage-guided

**代表**：AFL、AFL++、libFuzzer、Honggfuzz。

**作法**：
1. 在 target 編譯時插樁，讓它跑一次後吐出「走過哪些 edge」的 bitmap。
2. Fuzzer mutate 一個 input，執行，看 bitmap 有沒有新的 bit 點亮。
3. 如果有 — 把這個 input 留進 queue，之後繼續 mutate 它的後代。
4. 如果沒有 — 丟掉。

```
┌──────────┐   mutate   ┌──────────┐   run    ┌─────────┐   bitmap
│ queue[i] │ ────────▶  │ new_in   │ ───────▶ │ target  │ ────────┐
└──────────┘            └──────────┘          └─────────┘         │
    ▲                                                              │
    │ new coverage?                                                │
    └──────────────────── yes → add to queue ──────────────────────┘
                              no  → discard
```

這是 2013 年 Michał Zalewski 在 AFL 提出的核心概念，改變了 fuzzing 這一行。

**為什麼這招有效**：它把「盲目 mutate」變成了「爬山」。target 自己在提供 gradient — 每碰一個新分支就是一個訊號，fuzzer 根據訊號決定保留哪些 input。於是 input 會**自動學會**繞過 parser 的 sanity check、填對 magic bytes、甚至猜到簡單的 checksum。

**致命傷**：
- 需要 instrument target，純 binary-only 要靠 QEMU / Frida / Intel PT 等重機制。
- 對 `strcmp(input, "MAGIC")` 這種「一整段都要猜對」的東西還是無力 — bitmap 看不見半對半錯的狀態（這是 Ch 12 CmpLog / RedQueen 要解的）。
- 一次只看一個 target，不像 grammar fuzzer 能做語義檢查。

## 三者對比

| 維度 | Blackbox | Grammar | Coverage-guided |
|---|---|---|---|
| 需要 source | 否 | 否 | 通常是（或用 QEMU mode） |
| 要寫 grammar | 否 | 是 | 否 |
| 繞過 parser | 差 | 好 | 中（bitmap 會引導但慢） |
| 深入 target 邏輯 | 差 | 中 | 好 |
| Magic bytes / checksum | 極差 | 中（grammar 可寫死） | 差（需要 CmpLog 輔助） |
| 建置成本 | 低 | 高 | 中 |
| 代表工具 | zzuf、radamsa | Peach、Domato | AFL++、libFuzzer |

實務上，coverage-guided 是主流，但進階團隊常把三種疊起來用 — 例如用 grammar fuzzer 生 seed corpus，再丟給 coverage-guided 去探索。Nautilus 就是 grammar + coverage 的雜交品種。

## 一個讓直覺打架的數字

假設 target 長這樣：

```c
int main(void) {
    char buf[64];
    read(0, buf, 64);
    if (buf[0] == 'A')
     if (buf[1] == 'B')
      if (buf[2] == 'C')
       if (buf[3] == 'D')
        abort();   // bug!
    return 0;
}
```

**Blackbox**：每 byte 命中正確值的機率 $\frac{1}{256}$，四個都命中 $\frac{1}{256^4} \approx 2^{-32}$。一秒跑一萬次要跑 **五天**。

**Coverage-guided**：
- 隨機跑，某次剛好 `buf[0] == 'A'` — bitmap 多亮一格，這個 input 被留下。
- 在這個 input 上變異，`buf[1]` 某次命中 `'B'` — 又多一格。
- 依此類推。
- 從 $2^{-32}$ 變 $4 \times \frac{1}{256}$，實測大概 **數秒**。

這就是 coverage-guided 的魔法：把指數搜尋空間降成線性。但到了「四 byte 要一次命中」的 `strcmp(buf, "ABCD")` 寫法 — 因為只有全對才有分支，bitmap 看不見中間狀態 — 它又回到 $2^{-32}$。Ch 12 會講這個缺口怎麼補。

## 本書的主角

接下來 17 章只講 coverage-guided，特別是 AFL++ 這個實作。理由：

1. 這是 2014 年後 security 圈真正找得到 bug 的主流方式。
2. 它把 compiler 技術（instrumentation、LLVM pass）和系統技術（forkserver、shared memory、fork/exec 省成本）糅在一起，光是看 AFL++ 原始碼就能把 OS 和 compiler 複習一遍。
3. AFL++ 的實作公開且活躍，比 libFuzzer（綁在 LLVM tree 裡）好追。

如果你對 grammar fuzzer 感興趣，可以回頭看 `custom_mutators/gramatron/`，那是把 grammar idea 嫁接到 AFL++ 上的例子（Ch 14 會碰到）。

## 自我檢核

- [ ] 能解釋為什麼 blackbox fuzzer 對 `if (magic == 0xDEADBEEF)` 這種 code 幾乎無望
- [ ] 能說出 grammar fuzzer 的成本與收益在哪
- [ ] 能用自己的話解釋「coverage-guided 如何把指數搜尋變成線性」
- [ ] 知道 coverage-guided 在什麼情況下會退化回指數級（提示：strcmp）

下一章走歷史 — 看看 AFL 這條路線是怎麼從一個人的 side project 變成今天的 AFL++。

→ [Ch 2 AFL 家譜：從 AFL 到 AFL++ 的分裂與合流](./02-afl-family-tree.md)
