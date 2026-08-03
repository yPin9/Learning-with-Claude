# Ch 34 — 結構搜尋家族：comby/ast-grep

> **目標**：weggli 只吃 C/C++。真實審計你會碰到 Go 服務、Rust 元件、Java 後端、一坨 JS——這章補齊 weggli 之外的結構搜尋工具生態：**ast-grep**（tree-sitter、多語言、能改寫）、**comby**（語言無關、改寫最強）、**ripgrep**（純正則但極快，第一道粗篩）。你要學會的不是「多背幾個工具」，而是**分工判斷**：什麼問題一條 rg 就秒殺、什麼要 ast-grep 的結構、什麼時候別動用 CodeQL 這種重砲。同時認清一個常被搞混的點——**ast-grep 不是「多語言 weggli」**，語意深度差很多。
> **環境**：weggli 0.2.4，WSL Ubuntu 22.04；ast-grep/comby 於本機**未安裝**，相關輸出標「未實測，理論預期」並附安裝指令。

上一章 weggli 是把專精的刀——C/C++、深、快，但語言鎖死。審計現場很少是純 C。你掃一個雲原生專案，可能同時有 Go 的 API server、Rust 的 sidecar、前端 TS、幾個 Python 腳本。這時你需要**能跨語言做結構匹配**的工具，或者——很多時候——**根本不需要結構，一條快正則就夠了**。這章給你這張分工地圖。

## 四把工具的定位

先把家族擺清楚。它們不是競爭關係，是**不同深度/廣度的取捨**：

- **ripgrep（`rg`）**：純正則、不懂語法，但**極快**（Rust、記憶體映射、平行）。它是第一道粗篩——「這個危險函式全 repo 用在哪」這種問題，rg 毫秒級回答，不用開任何重工具。缺點：被格式/註解/字串騙（跟 grep 同病），抓不準巢狀結構。
- **weggli**：C/C++ 專精、懂 AST、單函式結構匹配。上一章的主角。廣度換深度。
- **ast-grep（`ast-grep` / 舊別名 `sg`）**：**tree-sitter based，多語言**（C/C++/Go/Rust/Java/JS/TS/Python… 凡 tree-sitter 有 grammar 的都行）。用 pattern（或 YAML rule）做結構匹配，**而且能改寫（rewrite）**。定位：跨語言的結構 linter/搜尋/codemod。
- **comby**：**語言無關**的結構搜尋與**改寫**。它不靠每語言的完整 AST，而是靠一套「懂括號/引號/註解平衡」的輕量結構解析——所以它幾乎什麼語言都能吃，**改寫能力最強、最穩**，是做大規模機械式修補的首選。

### 一張對照表

| 維度 | ripgrep (`rg`) | weggli | ast-grep | comby |
|---|---|---|---|---|
| 匹配基礎 | 純正則（文字） | C/C++ AST（tree-sitter） | 多語言 AST（tree-sitter） | 語言無關的結構（括號/引號平衡） |
| 語言 | 任意文字 | **只 C/C++** | 多語言（tree-sitter grammar） | 幾乎任意（含未知語言） |
| 懂註解/字串？ | 否 | 是 | 是 | 部分（懂引號/註解平衡） |
| 跨函式 dataflow？ | 否 | 否 | 否 | 否 |
| 語意深度 | 無 | **深（C/C++ 專屬 helper：型別位、參數展開）** | 中（結構準，但無 C 專屬語意 helper） | 淺（結構模板，非完整 AST） |
| **改寫（rewrite）** | 否（只搜） | 否（只搜） | **是** | **是（最強）** |
| 規則格式 | CLI 正則 | CLI pattern | CLI pattern + YAML rule | CLI 模板 `:[hole]` |
| 速度 | 最快 | 很快 | 快 | 中等 |
| 典型角色 | 秒級文字粗篩 | C/C++ 結構縮面 | 多語言結構 lint/codemod | 大規模機械改寫 |

**四者都不追跨函式 dataflow**——那是 CodeQL/Joern 的地盤。這張表全在漏斗的「快而粗～中層結構」那一段（Ch 35 會把整條漏斗串起來）。

## ripgrep：一定能跑的第一道粗篩

rg 幾乎一定裝了（`/usr/bin/rg`，13.0.0）。它不懂結構，但在**「先看看有沒有、在哪」**這種第一問上無可取代——快到你根本不會考慮開別的工具。

實跑：一次撈全 repo 的危險函式呼叫點（`~/audit-lab/weggli-lab/samples.c`）：

```
$ rg -n '\b(strcpy|strcat|sprintf|gets|memcpy)\s*\(' samples.c
7:    memcpy(dst, src, n);
12:    memcpy(dst, src, sizeof(dst));
39:    strcpy(name, src);
42:/* comment trick: this memcpy(x,y,z) is in a comment and split
46:    memcpy(d,
```

一條正則、毫秒級，把所有危險函式呼叫點都撈出來了。**但注意它的病**：

- 第 42 行**命中了註解裡的假 memcpy**——rg 不懂那是註解。
- 第 46 行只看到 `memcpy(d,` 開頭，**不知道這個呼叫的引數跨三行、size 是 strlen**——rg 沒有結構概念。

這正是 rg 的定位：**快而粗，接受雜訊**。你用它做「有沒有、大概在哪」的第一刀，接受它會命中註解、看不穿多行。真正要「這個 memcpy 的第三個引數是不是變數」這種結構問題，就往下交給 weggli/ast-grep。rg 的速度換掉了精度——這在漏斗頂端是對的取捨。

rg 掃大 repo 有多快？libgit2 的 `src/`（367 萬 bytes）掃一條 `strcpy` 正則：

```
$ rg --stats -c '\bstrcpy\s*\(' src
...
0.025070 seconds spent searching
```

**25 毫秒。** rg 就是拿來「不假思索先撈一把」的。

## ast-grep：多語言結構搜尋 + 改寫（未安裝，理論預期）

> **注意**：本機**未安裝** ast-grep（WSL 裡 `sg` 其實是系統的 `newgrp`，不是 ast-grep）。以下 pattern 與輸出為**理論預期，未實測**。安裝方式：
> ```
> npm i -g @ast-grep/cli      # 或 cargo install ast-grep --locked
> ast-grep --version          # 裝好後執行檔叫 ast-grep（別名 sg 可能與 newgrp 撞名）
> ```

ast-grep 的賣點：**tree-sitter 撐起多語言、pattern 就是一段帶洞的目標語言 code、還能改寫**。它的 meta-variable 用 `$NAME`（單一節點）、`$$$`（多個節點，類似 weggli 的 `...`）。

理論預期——找 Go 裡的 `exec.Command`（命令注入 sink 候選）：

```
# 理論預期，未實測
$ ast-grep --lang go --pattern 'exec.Command($CMD, $$$ARGS)' ./src
```

理論預期——找 C 的 memcpy（跟 weggli 對照）：

```
# 理論預期，未實測
$ ast-grep --lang c --pattern 'memcpy($DST, $SRC, $N)' samples.c
```

**改寫**才是 ast-grep 相對 weggli 的獨門——`--rewrite`（或 YAML rule 的 `fix`）能批次改。理論預期把所有 `strcpy(a, b)` 改成 `strlcpy(a, b, sizeof(a))`：

```yaml
# rule.yml — 理論預期，未實測
id: strcpy-to-strlcpy
language: c
rule:
  pattern: strcpy($DST, $SRC)
fix: strlcpy($DST, $SRC, sizeof($DST))
```
```
$ ast-grep scan --rule rule.yml --update-all ./src   # 理論預期
```

ast-grep 還能用 YAML 寫**帶約束的複合 rule**（`inside`/`has`/`not`/`all`/`any`），比 CLI pattern 表達力強——這是它當「結構 linter」時的主力，能寫進 CI 當 policy gate。

### 關鍵：ast-grep ≠ 多語言 weggli

最常見的誤解。ast-grep 和 weggli 都基於 tree-sitter、都做結構匹配，但**語意深度不同**：

- weggli 有**C/C++ 專屬的語意 helper**——它懂「型別位」（`_*` 匹配任意型別）、懂函式參數的展開、對 C 的宣告/呼叫結構有專門處理。它為 C/C++ 漏洞獵捕**深度優化**。
- ast-grep 是**通用的多語言結構匹配**——它準、它快、它跨語言，但沒有 weggli 那種「for C 漏洞獵人」的專屬語意。同一個 C pattern，weggli 可能有更貼近漏洞語意的表達方式。

換句話說：**ast-grep 廣但通用，weggli 窄但為 C 漏洞而生**。掃 C/C++ 找記憶體 bug，weggli 通常更趁手；掃 Go/Rust/多語言、或要改寫，才是 ast-grep 的主場。把 ast-grep 當「weggli 的多語言版」會高估它在 C 上的語意深度、低估 weggli 在 C 上的專精。

## comby：改寫最強、語言無關（未安裝，理論預期）

> **注意**：本機**未安裝** comby。以下為**理論預期，未實測**。安裝方式：
> ```
> bash <(curl -sL get.comby.dev)          # 官方安裝腳本
> # 或 apt / brew：見 comby.dev
> comby -version
> ```

comby 的哲學跟 ast-grep 不同：它**不建每語言的完整 AST**，而是用一套「懂括號 `()[]{}`、引號、註解平衡」的輕量結構解析。代價是語意較淺，好處是**幾乎什麼語言都能吃**（連它沒內建 grammar 的語言，靠括號平衡也能做基本結構匹配），而且**改寫又穩又強**——這是 comby 最出名的用途。

comby 的洞叫 `:[name]`（match 一段平衡的結構）、`:[[name]]`（match 一個識別子）。

理論預期——把所有 `strcpy(dst, src)` 改成 `strlcpy(dst, src, sizeof(dst))`：

```
# 理論預期，未實測
$ comby 'strcpy(:[dst], :[src])' 'strlcpy(:[dst], :[src], sizeof(:[dst]))' .c -i
```
`-i` 是原地改寫（in-place）。`:[dst]` 匹配第一個引數（含巢狀括號都平衡處理），改寫時原樣塞回。

理論預期——找「malloc 後緊接著就用、中間沒東西」的結構：

```
# 理論預期，未實測
$ comby ':[[p]] = malloc(:[n]);
:[[p]](:[args])' .c
```

comby 因為懂括號平衡，處理巢狀引數、多行呼叫比正則穩得多；但它**不懂型別、不懂作用域、不懂 C 語意**——它比 rg 準（懂結構平衡），比 ast-grep/weggli 淺（無真 AST 語意）。它的甜蜜點是**大規模、機械式的批次改寫**：全 repo 統一改一個 API、加一個參數、換一個函式名。

## 何時「不動用重工具」

這章最值錢的判斷不是「怎麼用這些工具」，是「**什麼時候不該開 CodeQL/Joern**」。審計新手最常見的浪費是**一上來就對整 repo 建 CodeQL database**——建庫幾分鐘、查詢再幾分鐘，只為回答一個 `rg` 三秒能答的問題。

判斷準則，由輕到重：

| 你的問題 | 該用 | 別用 |
|---|---|---|
| 「這個危險函式全 repo 用在哪？」 | `rg`（毫秒） | 別開 CodeQL |
| 「哪些 memcpy 的 size 是變數 / dst 是固定 buffer？」 | weggli（C/C++）/ ast-grep（多語言）（秒級） | 別建 database |
| 「這些命中裡，size 有沒有從網路 read 流過來？」 | **這才輪到** CodeQL/Joern（跨函式 taint） | — |
| 「全 repo 把 `strcpy` 換成 `strlcpy`」 | comby / ast-grep 改寫 | 手改 800 處 / 別開 taint 分析 |

原則：**問題不需要 dataflow，就別開追 dataflow 的工具**。「用在哪」「長什麼結構」是搜尋問題，rg/weggli/ast-grep 秒殺。只有「值不值錢/來源可不可控」這種要跨函式追污染的問題，才值得付 CodeQL 的建庫成本。這正是漏斗方向的縮影——便宜工具在前砍掉 99%，貴工具只花在最後 1%（Ch 35 完整展開）。

## 改寫用途：對接大規模修補

搜尋工具找到 bug，改寫工具**批量修** bug。這是 comby/ast-grep 相對 weggli/rg（純搜尋）的獨門價值：

- **統一淘汰危險 API**：全 repo `strcpy → strlcpy`、`sprintf → snprintf`、`gets → fgets`。一條 comby/ast-grep rule 改幾百處，比手改可靠。
- **補加缺失的檢查/參數**：給某個 API 統一加一個 bounds 參數。
- **CVE 修補的規模化**：一個 bug 有幾十個變體（variant，Ch 43），確認修法後用改寫工具一次全補。

但改寫的鐵律在下面踩雷——**沒測過的批次改寫是災難**。

## 踩雷集錦

**錯誤直覺：「結構問題我用正則硬解就好，不用學結構工具。」**
正確認識：正則對**巢狀平衡結構**根本無能。「找引數平衡的函式呼叫」「匹配到對應的右括號」——正則的括號計數是出了名的做不到（巢狀括號需要的是上下文無關文法，正則是正規語言，表達力不夠）。你用正則抓 `foo(a, bar(b, c), d)` 的第一個引數，遇到內層 `bar(...)` 的逗號就爆。rg 適合「有沒有、在哪」的粗篩；一旦問題是「這個結構長怎樣、引數對不對」，就得上懂括號平衡的 comby 或懂 AST 的 ast-grep/weggli。用正則解結構問題是把兩小時的活拖成兩天還做不對。

**錯誤直覺：「ast-grep 就是多語言版 weggli，掃 C 也一樣好用。」**
正確認識：兩者語意深度不同。weggli 有 C/C++ 專屬 helper（型別位 `_*`、參數展開、C 宣告/呼叫的專門處理），為 C 漏洞獵捕深度優化；ast-grep 是通用多語言結構匹配，準但沒有那層 C 專屬語意。掃 C 找記憶體 bug，weggli 通常更趁手；ast-grep 的主場是**多語言**和**改寫**。把它當「C 上的 weggli 替代品」會高估它的 C 語意深度。

**錯誤直覺：「改寫工具很聰明，寫完 rule 直接全 repo 批改就行。」**
正確認識：**沒在小樣本上 dry-run 驗證過的批次改寫是災難**。comby/ast-grep 的改寫是機械的——pattern 稍寬就會改到不該改的地方（改到註解裡的、改到字串常數裡的、改到語意不同的同名呼叫），而且巢狀/多行的邊界很容易吃錯。標準流程永遠是：**先不加 `-i`/`--update-all` 看 diff → 小目錄試改 → review 每一處 → 才全 repo 跑**，最後必須編譯 + 測試回歸。我沒安裝這兩個工具，正是提醒你：連我示範的 rule 都標「未實測」，你自己的 rule 更沒有理由不先測。「工具沒測就批次改」是把一個小修補變成一次大規模引入 bug。

**錯誤直覺：「有了這些快工具，CodeQL/Joern 就用不上了。」**
正確認識：這家族**全都不追跨函式 dataflow**。rg/weggli/ast-grep/comby 回答的是「哪裡有這個結構」，**回答不了**「這個 size 是不是從不可信來源流過來的」。當你的判斷需要「來源可不可控」「污染有沒有被 sanitize」——那是跨函式 taint，只有 CodeQL/Joern 做得到。這些快工具是漏斗上層的縮面刀，不是深工具的替代。搞反了會漏掉所有需要 flow 才能確認的真 bug。

**錯誤直覺：「`sg` 就是 ast-grep，直接跑。」**
正確認識：在很多 Linux 系統（包含本課的 WSL）`sg` 是系統內建的 `newgrp`（切換群組），**不是** ast-grep。裝了 ast-grep 後執行檔通常叫 `ast-grep`，`sg` 別名可能與 `newgrp` 撞名而失效。跑之前先 `which ast-grep` 確認、`ast-grep --version` 驗證，別對著 `newgrp` 打 pattern 一頭霧水。

## 進階延伸

- **ast-grep 的 YAML rule 進 CI**：ast-grep 的 `scan` + YAML rule（含 `inside`/`has`/`not` 這類 relational rule）能當**多語言結構 linter** 寫進 CI，對每個 PR 擋掉「新增的危險結構」。這比 Semgrep 輕、比 CodeQL 快，適合「結構層 policy」的守門（跟 Ch 17 Semgrep CI、Ch 39 SARIF 生態接得起來）。
- **comby 的語言無關性在冷門格式上的價值**：碰到沒有現成 tree-sitter grammar 的配置格式、DSL、老語言，comby 靠括號/引號平衡仍能做基本結構匹配與改寫——這是它相對 ast-grep（依賴 grammar）的獨特生存空間。審計異質系統時很有用。
- **改寫工具做 codemod 之外的「反向」用途**：改寫 pattern 也能拿來**做偵測**——把「安全的寫法」當 rewrite target，看哪些地方改寫後有變化，變化的就是「還沒用安全寫法」的點。這是把 codemod 工具當 linter 用的一個巧招。
- **工具選擇的元判斷**：真正的成本不是「跑工具」，是「解讀輸出的人時」。選工具的準則是**最小化你要人眼看的雜訊**——能用結構砍掉的雜訊就別留給人。rg 快但雜訊多、weggli/ast-grep 慢一點但雜訊少，取捨點在「命中量會不會淹死你」。這條元判斷貫穿整個漏斗。

## 本章重點整理

- 結構搜尋家族四把刀，全在漏斗的「快而粗～中層結構」段、**全都不追跨函式 dataflow**：`rg`（純正則、最快、第一道粗篩，會被註解/多行騙）、weggli（C/C++ 深）、ast-grep（多語言 tree-sitter + 改寫）、comby（語言無關 + 改寫最強）。
- rg 實跑證明其定位：一條正則毫秒撈全 repo 危險函式，但命中註解、看不穿多行——**快而粗、接受雜訊**是它對的取捨。
- ast-grep / comby 本機未安裝，pattern 與改寫範例標「理論預期」並附安裝指令（`npm i -g @ast-grep/cli`；`bash <(curl -sL get.comby.dev)`）。
- **ast-grep ≠ 多語言 weggli**：ast-grep 廣而通用，weggli 窄而為 C 漏洞深度優化，語意深度有別。
- 最值錢的判斷是「**不動用重工具**」：「用在哪/什麼結構」用 rg/weggli/ast-grep 秒殺，別為此建 CodeQL database；只有「來源可不可控」這種要 dataflow 的問題才值得付深工具成本。
- 改寫（comby/ast-grep）能對接大規模修補（統一淘汰危險 API、規模化補 CVE variant），但**沒 dry-run 驗證過的批次改寫是災難**。

## 自我檢核

- 給定問題「這個危險函式全 repo 用在哪」，你會用哪把刀、為什麼不開 CodeQL？換成「這個 size 是不是從網路流過來的」，答案為什麼變？
- rg 掃 `samples.c` 命中了第 42 行的註解和只有半個呼叫的第 46 行——這暴露 rg 的什麼本質限制？什麼工具能修正這兩個問題？
- 為什麼正則抓不準「函式呼叫的第一個引數」（當引數本身含巢狀括號時）？這是什麼層級的表達力問題？
- ast-grep 和 weggli 都基於 tree-sitter、都做結構匹配，差在哪？掃 C 記憶體 bug 你優先選誰、為什麼？
- 你要全 repo 把 `strcpy` 改成 `strlcpy`——描述一個安全的批次改寫流程（在直接 `-i` 之前你會做哪幾步）？為什麼不能寫完 rule 就直接全跑？

## 延伸閱讀

- **ast-grep 官網（ast-grep.github.io）的 Pattern Syntax 與 Rule 文件**——`$VAR`/`$$$`、YAML rule 的 `inside`/`has`/`not`/`fix` 全在。用法：裝好後把本章的 C/Go 理論 pattern 真跑一遍驗證，再試 YAML rule + `--update-all` 改寫（先 dry-run）。前提：本章 + 對 tree-sitter 有概念。
- **comby 官網（comby.dev）的 Overview 與 Syntax Reference**——`:[hole]`/`:[[id]]` 語法、in-place 改寫、支援語言清單。用法：裝好後拿一個小 C 檔試 `strcpy → strlcpy` 改寫，先不加 `-i` 看 diff，體會「語言無關的括號平衡」怎麼運作。前提：本章。
- **ripgrep 的 `man rg` / `--help`（尤其 `--pcre2`、`-t`/`--type`、`--stats`）**——rg 當粗篩要用好正則與檔案類型過濾。用法：把「撈危險函式族」的正則練熟，配 `-t c` 只掃 C 檔、`--stats` 看掃了多快。前提：正則基礎。
- **本課 Ch 22《CodeQL 全域 taint》**——這家族的「上界」。用法：讀完就懂為什麼本章的工具都到「結構」為止、為什麼「來源可控性」要留給 taint 工具。前提：Part 4。這條界定了「不動用重工具」判斷的另一邊。

你現在手上有一整排刀——從 rg 的毫秒粗篩、weggli/ast-grep 的結構縮面、comby 的改寫，到你早就會的 CodeQL/Joern 深查。問題是：它們怎麼**接力**成一套流程？下一章把四把刀組成一個漏斗，示範「找 unchecked memcpy」怎麼從幾百萬行一路收斂到幾個真 bug。

→ [Ch 35 組合拳漏斗](./35-funnel-combining-tools.md)
