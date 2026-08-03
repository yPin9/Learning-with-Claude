# Ch 28 — query 效能與除錯

> **目標**：你已經會寫出**正確**的 query（Ch 21-26），這章教你寫出**跑得動**的 query。在大 database 上，一條寫法不對的 query 會慢到不可用、或直接把記憶體吃爆。你會學到 QL 效能的核心——predicate 的 join order（連接順序）、cartesian product（笛卡兒積）陷阱、binding（綁定）、怎麼避免產生巨大的中間關係——以及 debug 效能的工具：`--tuple-counting`、evaluator log（求值器日誌）、VS Code 的 quick evaluation（逐 predicate 求值）。這是 [Ch 04](./04-dataflow-analysis.md) 那句「精度 vs 可擴展」在 query 層的具體版：你寫的每一條 predicate，都在這個 trade-off 上做選擇。
> **環境**：WSL，`codeql` 2.26.2。本章對本機小 database 真跑並貼輸出。**誠實前提**：我們的靶只有幾十行，query 的**絕對時間**會被 JVM 啟動與編譯的常數項（每次約 15-25 秒）淹沒，看不出戲劇性的快慢差。所以本章示範效能問題的方式是**看中間關係大小與 RA（relational algebra，關係代數）計畫裡的 `CARTESIAN PRODUCT` 字樣**——這才是效能問題在大 database 上爆掉的根因，也是這些工具真正給你看的東西。時間差你得在真實大 database 上才感受得到，我會標明。

## 先破除一個致命誤解：QL 是宣告式，但不是「不用管效能」

QL 是宣告式（declarative）語言：你描述「我要找的東西滿足什麼性質」，不寫「怎麼一步步算」。這很容易讓人以為——「反正引擎會幫我最佳化，我只管寫對就好」。

**這是錯的，而且是最貴的錯。**

QL 引擎確實會最佳化你的 query（重排 join、快取中間結果），但它**不是萬能的**。你寫的 predicate 結構，直接決定了引擎能不能找到好的求值計畫。寫法不對，引擎只能老實地**先算出一個巨大的中間關係、再過濾**——這個巨大的中間關係就是慢與 OOM（out of memory，記憶體耗盡）的來源。

底層原因要從 QL 的求值模型講起。

## 底層機制：QL 怎麼被求值——關係、join、tuple

一條 QL query 被編譯成一連串**關係代數運算**。你可以把每個 predicate 想成一張**關係表**（一組 tuple，元組），query 求值就是對這些表做 **join（連接）**、filter（過濾）、project（投影）。

關鍵概念：

- **relation（關係）/ tuple（元組）**：`from Function f, Function g` 產生的候選集合，是 `(f, g)` 這種 tuple 的集合。一條 predicate 的「大小」就是它含多少 tuple。
- **join order（連接順序）**：多個條件要合併時，引擎決定「先算哪個、後算哪個」。順序天差地別：先用**選擇性強**（結果小）的條件把候選砍小，後面的 join 就便宜；順序反了，中間關係先爆大。
- **binding（綁定）**：一個變數在某條件下是否「已被限定到有限的一組值」。`x = 5` 把 `x` binding 住了；`x < y`（兩個都自由）誰都沒 bound，引擎只能枚舉所有組合。**沒 binding 的自由變數 = 笛卡兒積的溫床**。
- **cartesian product（笛卡兒積）**：兩組各 N、M 個元素的關係，若沒有連接條件把它們關聯起來，join 出來就是 N×M 個 tuple。N、M 一大，N×M 直接爆炸。這是 QL 效能問題的**頭號元兇**。

一句話：**query 慢，通常不是因為「算得多」，而是因為「中間某一步產生了一個本不該那麼大的關係」**。效能除錯的全部重點，就是找出並消滅那個過大的中間關係。

## 真跑：把「慢寫法 vs 快寫法」的中間關係差異挖出來

我在共用靶目錄下建一個小 database（`protodb`，就是練習 D 用的 C 靶，[Ch 20](./20-codeql-databases.md) 的建法），跑兩條**語意相同、寫法不同**的 query，用 evaluator log 把它們的中間關係大小挖出來對照。

兩條 query 都在問「同一個檔案裡的函式配對數」，但一個會產生笛卡兒積、一個不會：

```ql
// pairs_slow.ql —— f 和 g 只靠「同檔」關聯 -> 產生所有同檔配對（中間關係大）
import cpp
from Function f, Function g
where f.getFile() = g.getFile()
select count(f), count(g)
```

```ql
// pairs_fast.ql —— f 和 g 強綁定為同一個 -> 中間關係就是函式數本身（小）
import cpp
from Function f, Function g
where f = g
select count(f), count(g)
```

跑法：`--evaluator-log` 產生求值日誌，`codeql generate log-summary` 轉成可讀摘要，抽出每個中間關係的 `resultSize`：

```bash
export PATH=$HOME/audit-tools/codeql:$PATH
for q in pairs_slow pairs_fast; do
  codeql query run --database=protodb $q.ql --evaluator-log=$q.jsonl >/dev/null 2>&1
  codeql generate log-summary $q.jsonl $q.sum >/dev/null 2>&1
  echo "===== $q：中間關係大小分佈（resultSize × 出現次數）====="
  grep "resultSize" $q.sum | sort -t: -k2 -n | uniq -c | tail -5
done
```

真跑輸出（照貼）：

```
===== pairs_slow：中間關係大小分佈（resultSize × 出現次數）=====
      4   "resultSize" : 279,
      1   "resultSize" : 280,
      1   "resultSize" : 363,
      2   "resultSize" : 629,
      3   "resultSize" : 7116,
===== pairs_fast：中間關係大小分佈（resultSize × 出現次數）=====
      4   "resultSize" : 1,
      2   "resultSize" : 279,
```

**讀懂這組數字**：`pairs_slow` 的求值過程裡出現了 **7116 tuples** 的中間關係——這就是「所有同檔函式的配對」被物化出來的樣子。`pairs_fast` 因為 `f = g` 把兩個變數強綁成一個，最大的中間關係只有 279（大約就是函式總數）。在這個 60 行的靶上，7116 微不足道；但把靶換成一個十萬函式的專案，`f.getFile() = g.getFile()` 這種寫法產生的中間關係會是**十萬乘以每檔平均函式數**——直接 OOM。這就是同樣「正確」的兩條 query，一條在大 database 上跑得動、一條跑不動的根因。

再看更直接的證據：把 `--evaluator-log` 產生的 **RA 求值計畫**裡 `#select` 那條 pipeline 印出來，慢寫法會出現 `CARTESIAN PRODUCT` 字樣：

```
（pairs_slow 的 #select RA pipeline，照貼）
   {1} r1 = JOIN _const#join_rhs WITH `Element::Element.getFile/0#...#nonempty` CARTESIAN PRODUCT OUTPUT Lhs.0
   {2}    | JOIN WITH _const#join_rhs CARTESIAN PRODUCT OUTPUT Lhs.0, Rhs.0
```

**`CARTESIAN PRODUCT` 這四個字就是警報**。它在 RA 計畫裡出現，代表引擎在某一步不得不做無連接條件的全配對。看到它，你就知道有兩個變數之間缺了一個把它們關聯起來的條件。這正是 evaluator log 存在的意義：它讓「哪一步爆了」從猜測變成看得見。

> **關於絕對時間的誠實話**：我也量了 wall-clock，兩條在這個小 db 上都是 15-25 秒級，差異全在 JVM 啟動與 query 編譯的常數項裡，**看不出快慢**。所以本章不拿小 db 的秒數當證據——秒數在小 db 上是噪音。真正可遷移的證據是**中間關係大小**與 **RA 裡的 `CARTESIAN PRODUCT`**，這兩個指標在任何規模的 db 上都指向同一個病根。

## 效能工具箱：怎麼看出 query 慢在哪

盲調（不看任何 profiling 就亂改 query）是效能除錯的最大反模式。QL 給你三件工具，每件回答不同的問題。

### 工具一：`--tuple-counting`

```bash
codeql query run --database=<db> --tuple-counting <query>.ql
```

`--tuple-counting` 讓引擎在求值時記錄**每個 predicate 求值出多少 tuple**。它回答的問題是：「哪個 predicate 產生的中間關係最大？」——那個最大的，通常就是瓶頸。你要找的是「tuple 數異常大、遠超你預期」的那條 predicate。

### 工具二：evaluator log（`--evaluator-log` + `generate log-summary`）

```bash
codeql query run --database=<db> <query>.ql --evaluator-log=eval.jsonl
codeql generate log-summary eval.jsonl summary.txt
```

evaluator log 是最完整的求值紀錄：每個 predicate 的 **RA 計畫**、**resultSize**（中間關係大小）、**millis**（各階段耗時）。上一節我們就是用它挖出 7116 這個數字、挖出 `CARTESIAN PRODUCT` 字樣。它回答的問題是：「求值計畫長什麼樣、哪一步物化了大關係、時間花在哪。」這是效能除錯的**主力工具**。

### 工具三：VS Code 的 quick evaluation（逐 predicate 求值）

在 VS Code 的 CodeQL 擴充裡，你可以對**單一 predicate 或單一運算式**按右鍵 **Quick Evaluation**，只求值那一小塊、立刻看結果與它的大小。它回答的問題是：「我懷疑的這條 predicate，單獨求值出來多大、對不對？」——這是**互動式**縮小問題範圍的利器：與其跑整條 query 猜哪裡慢，不如逐塊 quick-eval，看哪塊的結果集大得離譜。

> quick evaluation 需要 VS Code 擴充，本機 CLI 沒有等價的互動介面（CLI 端最接近的是把可疑 predicate 拆成獨立 query 單跑）。這部分**未在本機驗證**，但概念與 CLI 拆解等價。

### 三件工具的分工

| 工具 | 回答的問題 | 場景 |
|---|---|---|
| `--tuple-counting` | 哪個 predicate 的中間關係最大 | 快速定位瓶頸 predicate |
| evaluator log | RA 計畫 / resultSize / 耗時全貌 | 深度剖析、找 `CARTESIAN PRODUCT` |
| quick evaluation | 這一小塊 predicate 單獨多大 | 互動式縮小範圍（VS Code） |

## 寫得快的核心心法：在源頭 bind，別在最外層 filter

效能問題的病根幾乎都是同一個：**中間關係太大**。而讓中間關係太大的最常見寫法，是**「先產生一大堆候選，最後才 filter」**。快的寫法反過來：**在源頭就用選擇性強的條件把候選限死**。

### 心法一：讓每個變數都有 binding

自由變數（沒被任何條件限到有限集合的變數）是笛卡兒積的來源。確保 query 裡每個變數都被某個**選擇性強**的條件 bound 住。

```ql
// 慢：a、b 只靠「同 enclosing function」關聯，型別過濾在後 -> 大量配對先產生
from Expr a, Expr b
where a.getEnclosingFunction() = b.getEnclosingFunction()
  and a.getType() instanceof PointerType
  and b.getType() instanceof PointerType
select a, b

// 快：先用型別過濾把 a、b 各自的候選砍小，再談關聯
predicate ptrExpr(Expr e) { e.getType() instanceof PointerType }
from Expr a, Expr b
where ptrExpr(a) and ptrExpr(b)
  and a.getEnclosingFunction() = b.getEnclosingFunction()
select a, b
```

把「選擇性強的過濾」（`ptrExpr`）提早，讓引擎先得到小的候選集，再做關聯 join。

### 心法二：在 source 限制，別在最外層

這是心法一的實務版，也直接對應 [Ch 22](./22-codeql-global-taint.md) 的 taint config。看這個 taint 的反例：

```ql
// 慢：source 定義得太寬（所有 read 的回傳），最後才在 select 過濾函式名
predicate isSource(DataFlow::Node n) { n.asExpr() instanceof FunctionCall }
// ... 然後在 select 或 where 最外層才 and node.asExpr().(FunctionCall).getTarget().getName() = "read"
```

```ql
// 快：source 一開始就限死是 read 的相關節點
predicate isSource(DataFlow::Node n) {
  exists(FunctionCall rd |
    rd.getTarget().getName() = "read" and
    n.asDefiningArgument() = rd.getArgument(1))
}
```

taint tracking 的 source/sink 集合**越小越好**——它們是整個 flow 求值的起點與終點，起點大，後面每一步的 flow 探索都跟著大。**別把 source 定義成「所有 XXX」然後指望最外層 filter 收拾**，那等於讓引擎先探索一整片沒用的 flow。

### 心法三：`pragma[inline]` 與其他 annotation

QL 有一組 `pragma` annotation 微調求值。最常用的 `pragma[inline]`：把一個小 predicate **內聯**到呼叫處，避免它被獨立物化成一張中間表——當某個 helper predicate 很小、且它的獨立物化反而妨礙引擎重排 join 時有用。

```ql
pragma[inline]
predicate isDangerousName(string n) { n = "memcpy" or n = "strcpy" or n = "memmove" }
```

`pragma[inline]` 不是萬靈丹，亂加可能更慢。**先用 evaluator log 確認瓶頸在哪，再針對性地加**——這正是下面踩雷區的核心。它是最後手段，不是第一步。

## 命中太多怎麼辦：triage 的工程實務（接 Ch 36）

效能除錯除了「query 跑得慢」，還有一種是「query 跑得動，但命中幾千條」——這在 taint query 撒得太寬時很常見。命中量大本身也是一種效能問題：它拖垮的是**你**（人工 triage 的頻寬），不是引擎。

實務上兩條路並行：

1. **收緊 query 降低命中**（治本）：命中太多常常是 source/sink 太寬或缺 barrier。回到 [Ch 09](./09-source-sink-sanitizer.md) 重新界定 source/sink，加上該有的 sanitizer/barrier——把誤報從**源頭**砍掉，比事後人工篩快得多。
2. **對命中做分層 triage**（治標＋治理）：命中量大時，用 [Ch 36](./36-false-positive-governance.md) 的治理方法——按嚴重度/可達性排序、批次標記已知誤報 pattern、把「確認為誤報的形狀」反饋回 query 變成 barrier。SARIF（[Ch 39](./39-sarif-ecosystem.md)）匯出後接你的追蹤系統，讓 triage 狀態可持久、可協作。

關鍵判斷：**命中太多，先問是不是 query 太寬（治本），再談 triage（治標）**。反過來——放著寬 query 不管、純靠人力篩幾千條——是不可持續的。

## 踩雷集錦

**踩雷 1：predicate 沒 binding，爆笛卡兒積。**
錯誤直覺：「我兩個變數都宣告了，加個 `where` 條件就好。」
正確認識：如果 `where` 裡兩個變數之間**沒有一個把它們關聯起來的等式**（例如只有 `a < b` 這種比較，或各自獨立的過濾），引擎只能枚舉所有 N×M 配對。看到 evaluator log 出現 `CARTESIAN PRODUCT`、或某中間關係大得離譜，先檢查「是不是有兩個變數之間缺了連接條件」。加一個把它們綁起來的等式，或用選擇性強的 predicate 先把各自的候選砍小。

**踩雷 2：以為 QL 是宣告式就不用管效能。**
錯誤直覺：「引擎會最佳化，我只管寫對。」
正確認識：引擎的最佳化受限於你的 predicate 結構。寫成「先產生一大堆、最後 filter」，引擎多半只能照做——它沒辦法無中生有地把你放在最外層的過濾條件「猜」到源頭去。寫對只是及格；在大 database 上跑得動，要你主動把選擇性強的條件往源頭放。正確與高效是**兩件事**，都得管。

**踩雷 3：在最外層 filter，而不是在源頭限制。**
錯誤直覺：「source 我先寫寬一點（所有 function call），反正後面 where 會篩掉。」
正確認識：taint 的 source/sink 集合越大，整個 flow 求值越貴——引擎會先探索一整片你根本不要的 flow，最後才丟掉。把限制條件放進 `isSource`/`isSink` 的定義裡（源頭），別放在 flow 算完後的最外層。這是心法二，也是 taint query 效能的頭號原則。

**踩雷 4：不看 evaluator log 就盲調。**
錯誤直覺：「query 慢，我隨便改改寫法、加個 `pragma[inline]` 試試。」
正確認識：不看 profiling 亂改，運氣好碰對、運氣壞越改越慢，且你根本不知道為什麼。正確流程是**先量後改**：`--tuple-counting` 或 evaluator log 找出「哪個 predicate 的中間關係最大」，針對那一條動手，改完再量一次確認真的變小。`pragma[inline]` 這種手段更要先確認瓶頸位置——亂加只會讓求值計畫更難最佳化。

**踩雷 5：把小 database 上的秒數當效能證據。**
錯誤直覺：「我這條在測試 db 上兩秒就跑完，效能沒問題。」
正確認識：小 db 上的絕對時間被 JVM 啟動與編譯的常數項主導，**看不出 query 本身的擴展性**（本章實測：語意相同的慢/快寫法在小 db 上時間差全是噪音）。效能的真正指標是**中間關係大小**與**求值計畫裡有沒有 `CARTESIAN PRODUCT`**——這兩個在小 db 上就看得出病根，且能預測大 db 上會不會爆。要判斷 query 能不能上大 database（或上 [Ch 27](./27-codeql-mrva.md) 的 MRVA），看這兩個，別看小 db 的秒數。

## 進階延伸

- **讀懂 RA 計畫**：evaluator log 裡的 RA pipeline（`JOIN WITH ... ON ...`、`CARTESIAN PRODUCT`、`OUTPUT`）是引擎實際的執行步驟。學會讀它，你就能精確指出「第幾步物化了多大的關係」，把效能除錯從猜變成診斷。挑一條你寫過的慢 query，把它的 `#select` RA pipeline 印出來逐行讀。
- **quick evaluation 驅動的開發**（VS Code）：養成「寫一段 predicate 就 quick-eval 一次看大小」的習慣，在寫的當下就攔住會爆的中間關係，而不是整條寫完才發現跑不動。這對開發複雜 taint config 特別有效。
- **效能與精度的聯調**：效能與誤報常此消彼長——收緊 source/sink 同時降低命中量與求值成本。把 [Ch 04](./04-dataflow-analysis.md) 的「精度 vs 可擴展」和本章的「中間關係大小」放一起想：你在 query 層做的每個 binding 決策，同時影響**跑多快**和**報多準**。

## 本章重點整理

- **QL 是宣告式，但效能要你管**：引擎的最佳化受限於你的 predicate 結構，寫對只是及格，跑得動要你主動設計。
- **效能問題的病根是「中間關係太大」**：而讓它太大的頭號元兇是**缺連接條件的笛卡兒積**（RA 裡的 `CARTESIAN PRODUCT`）。
- **三件工具各司其職**：`--tuple-counting`（哪個 predicate 最大）、evaluator log（RA 計畫 + resultSize + 耗時，主力）、quick evaluation（互動式逐塊求值，VS Code）。
- **核心心法：在源頭 bind，別在最外層 filter**——每個變數要有選擇性強的 binding，taint 的 source/sink 越小越好，`pragma[inline]` 是先量後用的最後手段。
- **命中太多先問 query 是不是太寬**（治本），再談 triage（治標＋Ch 36 治理）。
- **小 db 的秒數是噪音**：判斷擴展性看中間關係大小與 `CARTESIAN PRODUCT`，不看絕對時間。

## 自我檢核

- 為什麼「QL 是宣告式」不等於「不用管效能」？引擎的最佳化在哪裡受限於你？
- `--tuple-counting`、evaluator log、quick evaluation 三者各回答什麼問題？你在什麼情境下各用哪一個？
- 「在源頭 bind，別在最外層 filter」為什麼能讓 query 變快？拿 taint 的 `isSource` 舉一個具體的慢/快對照。
- **主動回憶**：你寫的一條 taint query 在大 database 上 OOM 了。不看 code 亂改之前，你的診斷步驟依序是什麼？（提示：先量什麼、在 log 裡找什麼字樣、找到瓶頸 predicate 後怎麼改、改完怎麼確認。）
- 為什麼不能拿「在 60 行的測試 db 上兩秒跑完」當「這條 query 效能沒問題」的證據？該看什麼指標才對？

## 延伸閱讀

- **CodeQL 官方文件 "Evaluation of QL programs" / "QL language performance"**（讀「binding」與「join order」兩節）：把本章的 binding、join order、笛卡兒積講到語言規範層級，是理解「引擎為什麼這樣求值」的權威來源。前提：讀完本章對這些詞有直覺後再看，才不會只是背名詞。
- **CodeQL 官方文件 "Debugging queries" 的 tuple counting 與 evaluator log 章節**：本章用到的 `--tuple-counting`、`--evaluator-log`、`generate log-summary` 的完整選項與輸出格式解讀都在這。前提：本機有 CodeQL CLI（我們有），可照著對自己的 query 跑。
- **GitHub Security Lab 部落格關於 query 效能的 writeup**（挑一篇講「某條慢 query 怎麼被優化」的實戰文）：看真實世界怎麼用這些工具把一條跑不動的 query 救回來、優化前後 RA 計畫怎麼變。前提：先能讀懂 RA pipeline（進階延伸第一項）再看收穫最大。

query 寫得又對又快，你就有了一條可以放心撒到大 database、甚至上 MRVA 的武器。接下來練習 D 讓你把整個 Part 的東西——建 database、寫 global taint、抓變體、triage、輸出 SARIF——完整走一遍，這是全課的核心練習。

→ [練習 D：CodeQL variant analysis](./practice-d-codeql-variant-analysis.md)
