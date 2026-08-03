# Ch 7 — Taint 分析原理：source/sink/sanitizer

> **目標**：把 Ch 4（dataflow/lattice）、Ch 5（IFDS 圖可達性）、Ch 6（points-to 餵邊）收斂到 **taint 分析**這個審計最核心的框架。這章把 taint 拆成 **source / sink / sanitizer / propagation** 四要素並精確定義每一個——taint policy 就是這四者的規格。同時講清 **explicit flow vs implicit flow** 的分野、為什麼多數工具刻意忽略 implicit flow、以及 sanitizer 建模不當怎麼造成漏報/誤報。這套詞彙是後面 Semgrep taint mode（Ch 14）與 CodeQL TaintTracking（Ch 22）的**共同骨架**，先在這章建好。

taint 分析回答一個攻擊視角的問題：**「攻擊者能控制的資料，能不能不經淨化就流到危險操作？」** 這句話拆開，剛好就是四要素——「攻擊者能控制的資料」是 **source**、「危險操作」是 **sink**、「淨化」是 **sanitizer**、「流到」是 **propagation**。整個框架就是在一張 dataflow 圖上，問「有沒有一條從 source 到 sink、中途沒被 sanitizer 切斷的 path」。

## 四要素：taint policy 的規格

```
source ──propagation──► [中途傳遞] ──► sink
                            │
                        sanitizer（切斷這條邊 = 安全）
```

| 要素 | 定義 | 審計對應 | 舉例 |
|---|---|---|---|
| **source（污染源）** | 引入不可信資料的點；其輸出被標為 tainted | 攻擊面入口（Ch 9-10 專講） | `recv()`、`argv`、`req.body`、`getenv` |
| **sink（危險匯集點）** | 若收到 tainted 值就構成漏洞的操作 | 漏洞觸發點 | `system()`、`exec`、SQL query、`memcpy` size |
| **sanitizer（淨化器）** | 讓 tainted 值變回可信的操作；**切斷 taint 邊** | 防禦措施 | `escapeshellarg`、prepared statement、長度檢查 |
| **propagation（傳播規則）** | taint 怎麼從一個值傳到另一個值 | dataflow 的 transfer（Ch 4） | `y = x` 傳、`z = x + y` 任一髒則髒 |

**這四者合起來就是一份 taint policy**。給定 policy，taint 分析是機械的：從每個 source 標 tainted，依 propagation 規則沿 dataflow 圖傳，遇 sanitizer 切斷，看有沒有 tainted 值抵達 sink。**寫 SAST 規則的本質，就是寫這份 policy**——Semgrep 的 `pattern-sources/pattern-sinks/pattern-sanitizers`、CodeQL 的 `isSource/isSink/isSanitizer`，字面上就是這張表的三欄（propagation 多半由 library 內建，你只在特殊情況覆寫）。

## 手動追一份 policy

給一份具體 policy——`source=recv`、`sink=system`、`sanitizer=escape`——對一小段程式手動追 taint。程式（intra-procedural，順序執行）：

```
u = recv()          # source        -> u tainted
v = u               # copy          -> v tainted
w = escape(v)       # sanitizer     -> w clean（taint 被切斷）
system(v)           # sink，收到 v  -> v tainted -> 漏洞
system(w)           # sink，收到 w  -> w clean   -> 安全
```

我把這份 policy 的求值寫成可跑的小引擎（Python 3，真跑）。propagation 規則：assignment 直接傳；call 若非 source/sanitizer，則「任一參數 tainted ⟹ 回傳 tainted」。

```python
SOURCES    = {"recv"}
SANITIZERS = {"escape"}
SINKS      = {"system"}

def eval_taint(prog):
    tainted = {}; findings = []; trace = []
    for stmt in prog:
        if stmt[0] == "assign":
            _, dst, rhs = stmt
            if rhs[0] == "var":
                t = tainted.get(rhs[1], False)
            elif rhs[0] == "call":
                fn, args = rhs[1], rhs[2]
                if   fn in SOURCES:    t = True
                elif fn in SANITIZERS: t = False           # sanitizer 切斷 taint
                else: t = any(tainted.get(a, False) for a in args)  # propagation
            tainted[dst] = t
            trace.append((f"{dst} = {rhs}", dict(tainted)))
        elif stmt[0] == "sink":
            _, fn, args = stmt
            for a in args:
                if tainted.get(a, False): findings.append((fn, a))
            trace.append((f"{fn}({','.join(args)})",
                          {a: tainted.get(a, False) for a in args}))
    return findings, trace

prog = [
    ("assign", "u", ("call", "recv",   [])),
    ("assign", "v", ("var",  "u")),
    ("assign", "w", ("call", "escape", ["v"])),
    ("sink",   "system", ["v"]),
    ("sink",   "system", ["w"]),
]
findings, trace = eval_taint(prog)
for label, snap in trace:
    print(f"  {label:28s} {snap}")
for fn, a in findings:
    print(f"  漏洞：{fn}(...) 收到 tainted 變數 {a}")
```

真跑輸出（照貼，節選）：

```
  u = ('call', 'recv', [])     {'u': True}
  v = ('var', 'u')             {'u': True, 'v': True}
  w = ('call', 'escape', ['v']) {'u': True, 'v': True, 'w': False}
  system(v)                    {'v': True}
  system(w)                    {'w': False}

  漏洞：system(...) 收到 tainted 變數 v
```

`system(v)` 報、`system(w)` 不報——差別只在 `w` 走過 `escape` 這個 sanitizer，taint 邊被切斷。**這就是 taint policy 的全部機制**。真實工具的差別只在：dataflow 圖是跨函式（IFDS）、propagation 靠 points-to（Ch 6）補間接邊、source/sink/sanitizer 集合是你在 query 裡宣告的——骨架完全一樣。

## explicit flow vs implicit flow

上面追的都是 **explicit flow（顯式流）**：tainted 的**資料本身**被賦值/運算傳過去（`v = u`）。但 taint 還能透過控制流洩漏，這叫 **implicit flow（隱式流，control-dependent flow）**：

```c
// explicit：資料直接流
out = secret;

// implicit：沒有資料流過，但 out 的值完全由 secret 決定
if (secret) out = 1; else out = 0;
```

第二段裡，`out` 被賦的是常數 `1` 或 `0`，**沒有任何 tainted 變數流進 `out` 的賦值**——但 `out` 的最終值完全洩漏了 `secret`。這是**控制依賴（control dependence）**造成的資訊流：taint 從「決定走哪個 branch 的條件」洩漏到「branch 內被賦值的變數」。

跑出來看（Python 3，真跑）：

```python
def taint_implicit_ignored(secret_tainted):
    # if secret: out=1 else out=0 —— 工具只看資料賦值，out 是常數 -> clean
    out_tainted = False
    return out_tainted

def taint_implicit_tracked(secret_tainted):
    # 追 control dependence：進入 secret-controlled branch，pc 被污染
    pc_tainted = secret_tainted           # program counter 帶 taint
    out_tainted = pc_tainted              # branch 內賦值繼承 pc 的 taint
    return out_tainted

print("  explicit-only（多數工具）判 out tainted？", taint_implicit_ignored(True))
print("  追 implicit flow          判 out tainted？", taint_implicit_tracked(True))
```

真跑輸出：

```
  explicit-only（多數工具）判 out tainted？ False  <- 漏掉隱式洩漏
  追 implicit flow          判 out tainted？ True  <- 抓到
```

**為什麼多數工具刻意忽略 implicit flow？** 因為追它會**誤報爆炸**。追 control dependence 意味著「任何在 tainted 條件的 branch 裡被賦值的變數都變 tainted」——但真實程式裡幾乎每個 `if` 的條件都多少跟外部輸入有關，於是幾乎所有變數都會被染色，taint set 迅速膨脹到整個程式，警報淹沒到不可用。**這是精度與可用性的取捨**：忽略 implicit flow → taint 保持稀疏、警報可讀，代價是**某些 bug 抓不到**。

**這對審計意味什麼？** 一整類漏洞落在工具的盲區：

- **側信道 / 資訊洩漏**：透過 branch、快取時間、錯誤訊息洩漏 secret 的 bug，多半是 implicit flow，通用 SAST 抓不到。
- **依控制流繞過 sanitizer**：`if (validate(x)) use(x_copy_made_elsewhere)`——如果 sanitize 的是條件、用的是另一份 copy，explicit taint 可能斷。
- 認清這一點你才知道：**taint 工具乾淨不等於安全**，只是「沒有顯式的、你 policy 涵蓋的 flow」。資訊流層級的 bug 得靠人工或專門的 information-flow 分析（見延伸）。

## over-taint vs under-taint：sanitizer 是最常見的翻車點

taint 分析的兩種系統性錯誤：

| | 定義 | 後果 | 常見成因 |
|---|---|---|---|
| **over-taint（過度污染）** | 把不該髒的標髒 | **誤報**淹沒 | source 定太寬、propagation 太粗（field-insensitive）、sanitizer 沒建模 |
| **under-taint（污染不足）** | 該髒的沒標髒 | **漏報**（真 bug 溜走） | sink/source 漏了、propagation 斷邊（alias 沒算，Ch 6）、**假 sanitizer** |

**sanitizer 建模不當是兩頭的主要來源**，值得單獨盯：

- **漏掉真 sanitizer（漏標）**→ over-taint → 誤報。你沒告訴工具 `escapeshellarg` 是 sanitizer，於是它把「其實已淨化」的 flow 當漏洞報。
- **假 sanitizer（誤標一個擋不住的函式為 sanitizer）**→ under-taint → **漏報**，這種最危險。你以為 `escape` 淨化了，但它只跳脫引號、擋不住 `; rm -rf` 這類命令注入——policy 把它當 sanitizer 切斷 taint，於是真漏洞被判安全。

跑一個假 sanitizer 的漏報（Python，真跑）：

```python
prog2 = [
    ("assign", "u", ("call", "recv", [])),
    ("assign", "w", ("call", "escape", ["u"])),  # 誤把 escape 當 sanitizer
    ("sink",   "system", ["w"]),
]
f2, _ = eval_taint(prog2)
print("  policy 判定 findings：", f2 if f2 else "無 -> 漏報（真實 escape 擋不住 system 注入）")
```

真跑輸出：

```
  policy 判定 findings： 無 -> 漏報（真實 escape 擋不住 system 注入）
```

policy 判「安全」，但這其實是命令注入。**sanitizer 的正確性不在 taint 引擎裡，在你腦裡**——引擎只是忠實執行「escape 切斷 taint」這條你給的規格。規格錯了，引擎乾淨地算出錯的答案。這是 Ch 12（false positive triage）與 Ch 15（rule engineering）反覆會碰的痛點。

## 審計視角：這是全課後半的共同詞彙

- **Semgrep taint mode（Ch 14）**：`mode: taint` + `pattern-sources` / `pattern-sinks` / `pattern-sanitizers` 三個 key，字面就是本章的四要素（propagation 由 Semgrep 內建）。
- **CodeQL TaintTracking（Ch 22）**：你實作 `isSource` / `isSink` / `isSanitizer` / `isAdditionalTaintStep`（自訂 propagation），底層跑的是 IFDS 家族的 global dataflow（Ch 5）。
- **先建詞彙的價值**：等你到 Ch 14/22 寫真規則，你不是在學新概念，而是在把本章這四要素翻譯成該工具的語法。**source/sink/sanitizer/propagation 是不變的骨架，工具語法只是方言**。

## 踩雷集錦

**錯誤直覺：「有 sanitizer 就安全。」**
正確認識：只有 sanitizer **正確且完整** 才安全。假 sanitizer（擋不住對應 sink 的攻擊）會讓 policy 判安全但實際有洞——這是 under-taint、直接漏報。而且 sanitizer 是**針對特定 sink** 的：`escapeshellarg` 淨化命令注入，但拿去擋 SQL injection 沒用。看到「這裡有做 escape」別鬆手，要問「這個 escape 擋得住這個 sink 的攻擊嗎」。

**錯誤直覺：「taint 工具跑完乾淨，就沒有這類漏洞。」**
正確認識：乾淨只代表「沒有你 policy 涵蓋的、顯式的 flow」。implicit flow（控制依賴的資訊洩漏）預設被忽略，一整類側信道/資訊洩漏 bug 在盲區。source/sink/sanitizer 沒宣告全也會漏。「工具乾淨」是「在我給的規格下沒找到」，不是「安全」。

**錯誤直覺：「implicit flow 是小眾問題，忽略無所謂。」**
正確認識：對命令注入/SQLi 這類「資料直接進 sink」的 bug，忽略 implicit flow 確實影響小——這也是通用 SAST 敢忽略的原因。但對**保密性**（secret 洩漏、側信道、常數時間比較）漏洞，implicit flow **就是主戰場**，忽略它等於完全放棄這類。取捨要看你在找哪類 bug。

**錯誤直覺：「source 定越廣，抓得越全。」**
正確認識：source 定太寬會 over-taint，誤報淹沒到沒人看，真 bug 被埋在假警報裡——實務上等於漏報（人放棄看）。source 該精確對應「攻擊者實際能控制的入口」（Ch 9-10 的攻擊面建模就是在做這件事），不是「所有外部函式」。精確度不是保守越多越好。

**錯誤直覺：「propagation 規則是工具的事，我不用管。」**
正確認識：預設 propagation 常常不夠。經過你不知道的 helper 函式、經過 `memcpy`/容器操作、經過 alias（Ch 6）——taint 可能斷邊（under-taint 漏報）或亂傳（over-taint 誤報）。CodeQL 讓你寫 `isAdditionalTaintStep`、Semgrep 有 `pattern-propagators` 正是為此。debug 漏報時，「propagation 在哪斷了」是必查項。

## 進階延伸

- **information-flow 與 non-interference**：把 taint 推到極致就是 information-flow control，理論核心是 **non-interference（無干擾）**——high（secret）輸入不得影響 low（public）輸出，這**同時涵蓋 explicit 與 implicit flow**。Denning 的 lattice model、JIF/FlowCaml 這類語言就是在型別層級強制它。審計 secret 洩漏類 bug 時，這是比 taint 更完整的框架，但誤報/標註成本高到少見於通用工具。
- **quantitative information flow**：implicit flow 全有全無太粗，QIF 量化「洩漏了幾 bit」——`if (secret) ...` 洩漏 1 bit，未必值得報。這是「implicit flow 誤報爆炸」的一個理論解方（只報洩漏量大的）。
- **taint 的 sound vs complete**：通用 taint 工具兩者都不是——忽略 implicit flow 使它 unsound（會漏），近似的 propagation/alias 使它 incomplete（會誤報）。這不是 bug 是設計選擇。Ch 8 專講「工具的近似怎麼決定它漏/誤在哪」。

## 本章重點整理

- taint policy = **source / sink / sanitizer / propagation** 四要素的規格。寫 SAST 規則的本質就是寫這份 policy（Semgrep 三個 key、CodeQL 三個 predicate）。
- 機制：source 標髒 → 依 propagation 沿 dataflow 圖傳 → sanitizer 切斷邊 → 看 tainted 值有沒有到 sink。跨函式靠 IFDS（Ch 5）、間接邊靠 points-to（Ch 6）。
- **explicit flow**（資料直接流）vs **implicit flow**（控制依賴洩漏）。多數工具**刻意忽略 implicit flow**（追它誤報爆炸），代價是側信道/資訊洩漏類 bug 落在盲區。
- **over-taint → 誤報、under-taint → 漏報**。**sanitizer 建模不當是兩頭主因**：漏標真 sanitizer → 誤報；**假 sanitizer（誤標擋不住的函式）→ 漏報，最危險**。sanitizer 的正確性在你腦裡，不在引擎裡。
- 「工具跑完乾淨」= 「在我給的 policy 下、僅顯式流、沒找到」，**不等於安全**。

## 自我檢核

- 不看表，說出 taint 四要素各是什麼、各對應審計裡的什麼概念。「寫 SAST 規則 = 寫 taint policy」怎麼理解？
- 用 `if (secret) out=1 else out=0` 解釋 explicit 與 implicit flow 的差別。為什麼多數工具忽略 implicit flow？這讓哪一類漏洞落在盲區？
- over-taint 與 under-taint 各導致誤報還是漏報？sanitizer 建模的兩種錯誤（漏標 / 假 sanitizer）各落在哪一邊、哪個更危險？
- 「這裡有做 escape，所以安全」——用「sanitizer 針對特定 sink」與「假 sanitizer」兩點反駁。
- 為什麼「source 定越廣抓越全」是錯的？它跟 Ch 9-10 的攻擊面建模有什麼關係？
- 「工具跑完乾淨」精確的意思是什麼？把它當「安全」漏掉了哪兩塊（提示：implicit flow、policy 覆蓋度）？

## 延伸閱讀

- **Schwartz, Avgerinos, Brumley, *All You Ever Wanted to Know About Dynamic Taint Analysis and Forward Symbolic Execution (but might have been afraid to ask)*, IEEE S&P 2010**——taint 分析的形式化定義權威，source/sink/propagation 的精確語意、over/under-taint 的討論最清楚。讀 taint policy 形式化那節。前提：本章。優先讀。
- **Denning & Denning, *Certification of Programs for Secure Information Flow*, CACM 1977**——information flow 與 implicit flow 的源頭，lattice model 與為什麼 control dependence 會洩漏。讀 implicit flow 那節，理解本章 `if(secret)` 例子的理論根。前提：本章 + Ch 4 lattice。
- **Sabelfeld & Myers, *Language-Based Information-Flow Security*, IEEE JSAC 2003**——information-flow 的權威 survey，non-interference、explicit/implicit、宣告式解洩漏一條龍。想懂「taint 的完整版長怎樣」讀這篇。前提：本章。
- **Semgrep taint mode 官方文件（`Writing rules > Taint tracking`）** 與 **CodeQL `TaintTracking` library 文件**——把本章四要素對到真實語法。等你到 Ch 14/22 再回頭讀，會發現只是把 source/sink/sanitizer 翻成方言。前提：本章當詞彙表。

四要素齊了，但每個工具**怎麼近似**這套理論、各自在哪犧牲精度、於是各自漏/誤在哪——下一章把 lattice/IFDS/points-to/taint 對映到 Semgrep/CodeQL/Joern/weggli 的實際實作，攤成一張取捨對照表，收束整個 Part 1。

→ [Ch 8 理論怎麼落到工具](./08-theory-to-tools.md)
