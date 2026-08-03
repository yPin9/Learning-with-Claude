# Ch 14 — Semgrep taint mode

> **目標**：把 Ch 13 的 syntactic pattern 升級成資料流分析。Semgrep 的 `mode: taint` 把 Ch 7 的 taint 四要素——source／sink／sanitizer／propagation——變成四個 YAML 欄位，讓引擎自動追「攻擊者控制的資料有沒有不經淨化流到危險操作」。這章對回 Ch 7 理論，講清 taint mode 的**能力邊界**（intra-file、輕量 inter-procedural、alias 很弱，對比 Ch 8 的取捨與 Ch 18 CodeQL 的深度），真跑「含 sanitizer / 不含 sanitizer」兩版程式看命中與被擋，並把 `pattern-propagators` 與 taint labels 點到。
>
> **環境**：Semgrep 1.172.0，WSL Ubuntu 22.04

Ch 13 的 pattern 認「程式碼長什麼樣」，但答不了審計的核心問題：**這個危險呼叫收到的值，是不是攻擊者控制的？** `memcpy(dst, src, n)` 命中了，但 `n` 是常數 64 還是攻擊者從網路讀來的，syntactic pattern 分不出來。要分，得追資料**流**。taint mode 就是 Semgrep 在 AST 上疊了一層輕量 dataflow，把 Ch 7 那張 source/sink/sanitizer 表變成可執行的規格。

## 四要素落成四個欄位

Ch 7 定義的 taint policy 是四要素；Semgrep taint mode 的 YAML 幾乎是字面翻譯：

```
mode: taint
pattern-sources:      ← Ch 7 的 source（污染源，攻擊面入口）
pattern-propagators:  ← Ch 7 的 propagation（taint 怎麼傳，多數靠內建預設）
pattern-sanitizers:   ← Ch 7 的 sanitizer（切斷 taint 邊 = 安全）
pattern-sinks:        ← Ch 7 的 sink（收到 tainted 就是漏洞）
```

| Ch 7 要素 | taint mode 欄位 | 語意 |
|---|---|---|
| source | `pattern-sources` | 哪些 AST 節點的值一出生就是 tainted |
| sink | `pattern-sinks` | tainted 值抵達這裡就報 |
| sanitizer | `pattern-sanitizers` | tainted 值經過這裡變 clean，taint 邊斷 |
| propagation | `pattern-propagators`（多半省略） | 非預設的 taint 傳遞規則（自訂） |

**引擎的工作是機械的**：從每個 source 標 tainted，沿 dataflow 傳（賦值、運算、函式呼叫預設會傳），遇 sanitizer 就把那條路切斷，看有沒有 tainted 值抵達某個 sink。這正是 Ch 7 手動追的那套，只是自動化了。

## 真跑：source → sink，含/不含 sanitizer

拿一條命令注入的 policy——**source = `read_input()`（回傳攻擊者控制的字串）、sink = `system()`、sanitizer = `shell_escape()`**——寫成 rule：

```yaml
rules:
  - id: tainted-input-to-system
    languages: [c]
    severity: ERROR
    message: "attacker-controlled input reaches system() (command injection)"
    mode: taint
    pattern-sources:
      - pattern: read_input()
    pattern-sanitizers:
      - pattern: shell_escape(...)
    pattern-sinks:
      - pattern: system($CMD)
```

**漏洞版**（source 直接流到 sink，沒淨化）：

```c
#include <stdlib.h>
extern char *read_input(void);

void run(void) {
    char *cmd = read_input();   // source
    system(cmd);                // sink: direct tainted flow -> injection
}
```

真跑 `semgrep --quiet --config r-taint.yml taint_vuln.c`，照貼：

```
┌────────────────┐
│ 1 Code Finding │
└────────────────┘
    taint_vuln.c
   ❯❯❱ tainted-input-to-system
          attacker-controlled input reaches system() (command injection)
            6┆ system(cmd);                // sink: direct tainted flow -> injection
```

**安全版**（中間插一個 sanitizer）：

```c
#include <stdlib.h>
extern char *read_input(void);
extern char *shell_escape(char *);

void run(void) {
    char *cmd = read_input();   // source
    cmd = shell_escape(cmd);    // sanitizer
    system(cmd);                // sink: sanitized -> no finding
}
```

真跑同一條 rule，輸出**空的**——沒有任何 finding。sanitizer 把 `cmd` 從 tainted 洗回 clean，抵達 sink 時已經不髒，taint 邊被切斷。

這兩版的對照就是 Ch 7 那張圖的活體：source→propagation→sink 是漏洞，中途插 sanitizer 就切斷。**注意輸出「空」正是我們要的**——taint mode 的價值不只在報得出漏洞版，更在對安全版**不誤報**，這是 syntactic pattern（`system(...)` 一律報）給不了的。

## propagation 自動追多跳

taint 不是只認「source 一步到 sink」。intra-procedural 的傳遞是自動的——賦值、運算都會傳。多跳賦值版：

```c
void run(void) {
    char *a = read_input();  // source
    char *b = a;             // propagate via assignment
    char *c = b;             // and again
    system(c);               // sink reached through 2 hops
}
```

真跑，命中：

```
┌────────────────┐
│ 1 Code Finding │
└────────────────┘
    taint_prop.c
   ❯❯❱ tainted-input-to-system
            8┆ system(c);               // sink reached through 2 hops
```

`a`→`b`→`c` 兩跳賦值，taint 一路傳到 sink。**這就是 taint mode 比 syntactic 強的地方**——它不要求 source 和 sink 寫在同一行，中途經過幾個中間變數都追得到（在 intra-procedural 範圍內）。跨語言也一樣：Python 版把 taint 走過字串 `+` 串接同樣命中：

```python
def run():
    name = input()                        # source
    cmd = "echo " + name                  # taint 走過字串串接
    subprocess.call(cmd, shell=True)      # sink
```

配一條 python taint rule（source=`input()`、sink=`subprocess.call(shell=True)`、sanitizer=`shlex.quote`），真跑命中 sink 那行；把中間換成 `cmd = "echo " + shlex.quote(name)` 的 sanitized 版，輸出**空**。同一套四要素框架，換 `languages` 就換語言。

## 能力邊界：intra-file、輕量 inter-proc、alias 很弱

這是本章最重要的一節——**知道 taint mode 追不到什麼，才不會把「沒報」當成「沒漏洞」**（Ch 8 的頭號誤判）。

```
taint mode 的座標（對比 Ch 8 精度表）
├─ intra-file       ✔ 單檔內追得動
├─ intra-procedural ✔ 函式內多跳賦值/運算都追
├─ inter-procedural ~ 輕量：同檔函式呼叫的 arg→return 有預設傳遞
│                     跨檔、深層呼叫鏈  ✘ 弱/斷
├─ alias / points-to ✘ 很弱：兩個指標指同一塊、out-param 寫回，多半追不到
└─ implicit flow    ✘ 不追（Ch 7：控制流洩漏，主流 SAST 都放棄）
```

**跨檔是硬邊界**：Semgrep taint 主要在單檔範圍工作。source 在 `a.c`、經過 `b.c` 的 helper、sink 在 `c.c`——taint mode 多半斷在檔案邊界。要這種深度 inter-proc + 精確 alias，是 CodeQL global taint 的地盤（Ch 18、Ch 22 用 models-as-data 補 summary）。

**alias 弱**要具體看。out-param 這種「函式把 taint 寫進你傳進去的 buffer」：

```c
extern void fill(char *dst, char *src);   // 把 src 寫進 dst

void run(void) {
    char *a = read_input();   // source
    char *d = NULL;
    fill(d, a);               // taint 從 a 流進 d？
    system(d);                // sink
}
```

即使補上 `pattern-propagators` 宣告 `fill($DST, $SRC)` 把 taint 從 `$SRC` 傳到 `$DST`，這條在測試裡**仍沒命中**——Semgrep 對「透過指標把 taint 寫回一個既有變數」這類 alias 追不穩。這不是你 rule 寫錯，是**工具的近似邊界**（Ch 8 的「alias 很粗」在這裡具體化）。碰到這種洞，換 CodeQL 或人工。

對比之下，**arg→return 的傳遞是預設就有的**：`char *d = wrap(a);` 這種「把 tainted arg 傳進函式、taint 跟著回傳值出來」，就算 `wrap` 是 `extern`（看不到實作），taint mode 也會樂觀地把回傳當 tainted。這是輕量 inter-proc 的甜區——**但也是誤報來源**（`wrap` 其實可能是個 sanitizer，你沒宣告它就照傳）。

## pattern-propagators：什麼時候真的要寫

多數時候 propagation 靠內建預設（賦值傳、運算傳、arg→return 傳），你不用寫 `pattern-propagators`。要自己寫的場景是**非預設的 taint 移動**——最典型是「taint 走過一個容器/包裝，從一個欄位跑到另一個」：

```yaml
pattern-propagators:
  - pattern: $RET = wrap($SRC)
    from: $SRC
    to: $RET
```

`from`/`to` 明講「taint 從哪個 metavariable 移到哪個」。在 `wrap` 已被預設 arg→return 覆蓋的情境下這條是多餘的（測試顯示不寫也命中）；它的真價值在預設追不到的自訂傳遞路徑。**踩雷在反面**：propagator 該寫沒寫，taint 在那個節點斷流，你就漏報——而且漏得無聲無息（見踩雷）。

## taint labels：多來源/多階段污染（點到）

進階場景：不是所有 tainted 都一樣。「來自網路的髒」和「來自檔案的髒」可能觸發不同 sink；或某個漏洞要「先經過解碼、再進 sink」才成立。Semgrep 的 **taint labels（污染標籤）** 讓你給不同 source 貼不同標籤（`label:`），在 sink 用 `requires:` 指定「要帶哪個/哪些標籤才算命中」，還能用 propagator 把一個標籤轉成另一個（模擬「解碼後才危險」的多階段污染）。這把單一 tainted/clean 的二元擴成一套標籤代數。日常規則用不到，但寫「需要多個污染條件同時成立」的精準規則時它是關鍵工具——細節留給官方 taint labels 文件，這裡先知道有這個能力、以及它對應 Ch 7 「不同 source 語意不同」的現實。

## 對比演進：syntactic → taint → global

放進工具譜系收束：

| 手段 | 能回答的問題 | 邊界 |
|---|---|---|
| Ch 13 syntactic | 「程式碼長這樣嗎？」 | 不知道值從哪來 |
| **Ch 14 taint（本章）** | 「這個值是攻擊者控制的嗎？（單檔內）」 | 跨檔斷、alias 弱、不追 implicit |
| Ch 18/22 CodeQL global | 「跨整個 codebase 這個值是攻擊者控制的嗎？」 | 要建 DB、慢、寫 QL 成本高 |

**taint mode 卡在甜蜜點**：比 syntactic 精準太多（能分「攻擊者控制 vs 常數」）、又比 CodeQL 輕量太多（no-build、CI 秒級）。代價是跨檔和 alias 的深度。審計實戰的用法是——**taint mode 快掃粗篩單檔內明顯的 source→sink，深度目標再上 CodeQL/Joern**（Ch 35 funnel）。

## 踩雷集錦

**錯誤直覺：「sanitizer 我隨便宣告一個就擋得住誤報。」**
正確認識：sanitizer 要**涵蓋全**才有效。你宣告了 `shell_escape` 是 sanitizer，但程式碼裡真正的淨化走的是另一個函式 `quote_arg`——taint 沒被你宣告的那個切斷，照樣抵達 sink，你得到**誤報**。反過來，宣告了一個「其實不淨化」的假 sanitizer，會把真 flow 切掉造成**漏報**。sanitizer 清單的完整度與正確度直接決定誤報/漏報比，不是寫一個交差。

**錯誤直覺：「taint mode 能跨檔深追，沒報就是安全。」**
正確認識：Semgrep taint 主要在**單檔**範圍工作，跨檔、深層呼叫鏈多半斷流。source 在 `a.c`、sink 在 `c.c`，中間隔著別的檔——taint mode 大概率漏報。**「taint mode 沒報」只代表「單檔內、它的近似範圍內沒找到」**，不代表沒漏洞。要跨檔深度是 CodeQL 的活（Ch 18/22）。把單檔工具的乾淨當全域保證，是 Ch 8 頭號誤判在 Semgrep 上的具體版。

**錯誤直覺：「propagation 是自動的，我不用管 propagator。」**
正確認識：預設只涵蓋常見傳遞（賦值、運算、arg→return）。碰到**非預設的 taint 移動**（透過自訂容器搬、透過某種 wrapper 換型），預設追不到就**斷流漏報**——而且無聲無息，你不會看到任何錯誤，只是那條真 flow 沒報出來。懷疑漏報時，第一件事查「taint 是不是在某個非標準傳遞的節點斷了」，需要就補 `pattern-propagators`。

**錯誤直覺：「alias/out-param 我寫個 propagator 就能追。」**
正確認識：測試顯示，透過指標把 taint 寫回一個既有變數（out-param 風格），即使補了對應 propagator，Semgrep 也**追不穩**。這是工具的 alias 近似邊界（Ch 8「alias 很粗」），不是 rule 寫法問題——再怎麼調 rule 都補不回來。碰到 out-param/aliasing 主導的洞，正解是換 CodeQL 或人工，不是跟 Semgrep 死磕。

**錯誤直覺：「arg→return 預設會傳，很方便，沒副作用。」**
正確認識：這個「樂觀傳遞」是雙面刃。`x = process(tainted)` 就算 `process` 其實是個淨化函式，只要你沒把它宣告成 sanitizer，taint mode 會照樣把回傳當 tainted → **誤報**。輕量 inter-proc 的甜區同時是誤報溫床。看到「經過某函式後仍報 tainted」時，先確認那函式是不是被你漏標的 sanitizer。

## 進階延伸

- **Semgrep 官方 *Taint tracking* 文件**——`pattern-sources/sinks/sanitizers/propagators` 的完整語意、`focus-metavariable`、`exact`/`by-side-effect` 等旗標的邊界。本章每個欄位的權威定義，寫真規則前通讀一遍。前提：本章 + Ch 7。
- **Semgrep taint labels 文件**——把單一 tainted/clean 擴成標籤代數（`label:` / `requires:`），表達「多來源」「多階段污染」「解碼後才危險」。想寫需要多條件同時成立的精準 taint 規則時的關鍵機制。前提：本章 taint labels 一節。
- **對照 Ch 18/22 CodeQL global taint**：同一個「跨檔 source→sink」漏洞，Semgrep taint 斷在檔案邊界、CodeQL 用 models-as-data 補 summary 追得到。把兩者放一起跑同一個目標，最能體感「輕量 vs 深度」的取捨落在哪。前提：本章 + Ch 8。

## 本章重點整理

- taint mode 把 Ch 7 四要素落成四欄：`pattern-sources`／`pattern-sinks`／`pattern-sanitizers`／`pattern-propagators`（多半省略，靠內建預設）。引擎自動從 source 標 tainted、沿 dataflow 傳、遇 sanitizer 切、看有沒有抵達 sink。
- 真跑對照：漏洞版 `read_input()`→`system()` 報 command injection；插入 `shell_escape()` sanitizer 的安全版**輸出空、不誤報**——這是 syntactic pattern 給不了的精度。
- **能力邊界**：intra-file / intra-procedural 追得動、arg→return 輕量 inter-proc 有預設（但也是誤報源）；**跨檔斷、alias/out-param 很弱、不追 implicit flow**。這些是 Ch 8 的取捨在 Semgrep 上的具體化。
- **「taint mode 沒報」≠「安全」**——它只在單檔近似範圍內找。深度 inter-proc + 精確 alias 是 CodeQL 的地盤（Ch 18/22）。
- sanitizer 清單的**完整度與正確度**直接決定誤報/漏報；propagator 該寫沒寫會**無聲漏報**。taint labels 是「多來源/多階段污染」的進階武器。

## 自我檢核

- 把 Ch 7 的 source/sink/sanitizer/propagation 四要素，各對到 taint mode 哪個 YAML 欄位。哪一個多數時候不用自己寫、為什麼？
- 為什麼「安全版輸出空」比「漏洞版有命中」更能說明 taint mode 的價值？syntactic pattern 在安全版上會怎樣？
- 說出 taint mode 三個追不到的東西（各給一句話為什麼）。「Semgrep taint 沒報這個檔 = 這個檔安全」錯在哪？
- source 在 `net.c`、sink 在 `handler.c`，中間隔一個 helper 檔。taint mode 大概率報還是漏？要追到得換什麼工具、為什麼那個工具追得到？
- 你宣告了 sanitizer 但仍誤報一堆。列出兩個可能原因（一個關於 sanitizer 涵蓋、一個關於 arg→return 樂觀傳遞）。

## 延伸閱讀

- **Semgrep 官方 *Taint tracking* + *Taint labels* 文件**——本章所有欄位與進階標籤機制的權威來源。先讀 taint tracking 打底，要寫多條件規則再讀 labels。前提：本章 + Ch 7。
- **Bennett et al. / Semgrep 團隊關於 taint mode 引擎設計的技術文章**——講 Semgrep 怎麼在不建全域 IR 的前提下做輕量 dataflow、為什麼選擇單檔範圍、arg→return 樂觀傳遞的設計權衡。理解「為什麼它有這些邊界」而非只是「它有這些邊界」。前提：本章能力邊界一節 + Ch 8。
- **Ch 11 跨語言 sink catalog**——taint rule 的 sink 清單品質決定覆蓋率。回去對照各語言真正該當 sink 的 API，才不會 taint rule 只抓到冰山一角。前提：本章 + Ch 11。

taint mode 讓你能寫「單檔內追攻擊者資料流」的規則了。但寫得出一條規則和維護一套規則是兩回事——命名、CWE 對應、autofix、測試怎麼保證規則不退化？下一章從「寫一條」進到「工程化一套」。

→ [Ch 15 Semgrep 規則工程](./15-semgrep-rule-engineering.md)
