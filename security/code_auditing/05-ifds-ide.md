# Ch 5 — IFDS/IDE：把 taint 化成圖可達性

> **目標**：Ch 4 的 dataflow 只在單一函式內傳；真實漏洞幾乎都跨函式。這章看 **IFDS（Reps-Horwitz-Sagiv 1995）** 怎麼把一大類程序間 dataflow 問題化約成 **exploded supergraph（爆炸超級圖）上的圖可達性**，用 **tabulation 演算法 + summary edge** 做到既 context-sensitive 又不重算——這是現代 taint 工具（含商業 SAST）底層的主結構，也是「函式 model」的理論根。

跨函式 taint 的暴力解會爆炸。想像 `a = source(); b = id(a); sink(b)`，`id` 又呼叫別的函式……如果每次呼叫都把 callee 的整個 CFG 攤開重算，呼叫巢狀一深就是指數。更麻煩的是 **context-sensitivity（上下文敏感）**：同一個 `id()` 被兩個地方呼叫，你不能讓一個 caller 的污染「漏」到另一個 caller 的 return（這叫 unrealizable path，見下）。IFDS 的整套設計就是為了同時解決這兩件事。

## 先看暴力做為什麼爆炸

程序間分析最天真的做法是 **inline**：把每個 call 換成 callee 的 body 再跑 Ch 4 的 dataflow。問題有二：

- **重複計算**：`id()` 被呼叫 100 次，就攤開算 100 次，即使每次的分析行為一樣。
- **context 混淆**：如果不 inline 而是把所有 caller 的 CFG 用 call/return 邊直接縫起來跑，污染會沿著「A 呼叫 id、從 id return 到 B」這種**根本不會真的發生**的路徑亂流——這叫 **unrealizable path（不可實現路徑）**。call 從 A 進去就該 return 回 A，不能 return 到 B。

```
        call from A ──┐        ┌── return to A   ← 合法
                      ▼        │
                   [ id body ] ┤
                      ▲        │
        call from B ──┘        └── return to B   ← 合法
                                     ╳
        call from A ─────────────────── return to B   ← unrealizable！
```

context-insensitive 的做法會走那條 `╳` 路徑，把 A 的污染算到 B 頭上 → 誤報。IFDS 的 summary edge 機制天生排除這種路徑，這是它 context-sensitive 的來源。

## IFDS 的化約：dataflow fact 變成圖的節點

IFDS 的核心魔術是一個表示上的轉換。它要求問題屬於一類叫 **IFDS 問題**的東西，滿足兩個條件：

- **finite（有限）**：dataflow fact 的集合有限（記 D，例如「哪些變數 tainted」——變數有限所以 fact 有限）。
- **distributive（可分配）**：transfer function 對 join 可分配，`f(x ⊔ y) = f(x) ⊔ f(y)`。**taint、reaching definitions 都是 distributive 的**——一個 fact 的傳播不依賴其他 fact 同時在不在（這正是 Ch 4 結尾埋的 MFP=MOP 條件）。

滿足這兩點，就能把 dataflow 問題**化約成圖可達性**。做法是建 **exploded supergraph**：

- 原本 supergraph（各函式的 CFG 用 call/return 邊縫起來）的每個 statement 節點 `n`，**爆炸**成一排節點 `(n, d)`，`d` 跑遍所有 dataflow fact（外加一個特殊的 `0` fact 代表「無條件成立」）。
- transfer function 變成這些 `(n, d)` 節點之間的邊。「`x` tainted 使 `y` tainted」就是一條從 `(n, x)` 到 `(n', y)` 的邊。

於是：**「fact d 在 statement n 成立」 ⟺ 「在 exploded supergraph 上，從 (start, 0) 能走到 (n, d)」**。dataflow 求解變成純粹的圖可達性（graph reachability）——這就是標題那句「把 taint 化成圖可達性」的字面意思。

```
一個 statement n，fact 集合 D = {0, a, b}，爆炸成三個節點：

   (n, 0)     (n, a)     (n, b)
     │          │          │      ← transfer 變成節點間的邊
   (n',0)     (n',a)     (n',b)

「a=source() 使 a tainted」= 一條 (n,0) → (n', a) 的邊
「b=a 使 b tainted（若 a tainted）」= 一條 (n,a) → (n', b) 的邊
```

`0` fact 那一欄是關鍵：source（無中生有的污染，例如 `a=source()`）畫成從 `(n, 0)` 拉出一條到 `(n', a)` 的邊——「不需要任何前提，a 就被污染」。

## tabulation 演算法與 summary edge

exploded supergraph 可能很大，但 tabulation 演算法用兩個 worklist（path edge 與 summary edge）把它算到多項式時間（$O(E \cdot D^3)$，跟 fact 數三次方相關但**不是指數**）。核心機制是 **summary edge**：

> **summary edge = 一個函式的「輸入 fact → 輸出 fact」摘要**。算過一次 callee 的行為，就記成一條從「call 前的 fact」直達「return 後的 fact」的邊，之後任何 caller 呼叫同一函式，**直接套這條 summary，不再重算 callee 內部**。

以跑例的 `id`：

```c
int id(int p) { return p; }
```

tabulation 算一次得到 `id` 的 summary：**「參數 p tainted ⟹ 回傳值 tainted」**（一條 summary edge：`(call, a) → (afterCall, b)`，若 `a` 傳給 `p`、`b` 收 return）。之後：

```
main:  a=source();  b=id(a);  sink(b);
other: c=source2(); d=id(c);  ...
```

`main` 與 `other` 都呼叫 `id`，**都直接套同一條 summary edge**，`id` 的 body 只被分析一次。這同時給了兩個好處：

- **不重算**：callee 內部只算一次，之後 O(1) 套用。
- **context-sensitive**：summary edge 明確把「這個 call 的輸入」接到「這個 call 之後的輸出」，call 從 `main` 進就 return 回 `main`，unrealizable path 天生被排除。

### ASCII 示意：tabulation 怎麼傳

```
main 側                         id 側（只算一次）
──────────────                  ──────────────────
(a=source(), 0)
   │ 邊：source 產生 a
   ▼
(b=id(a), a) ──call 進入──►  (entry id, p)         ← a 綁到參數 p
                               │ transfer: return p
                               ▼
                             (exit id, ret)
   ◄──return 帶回────────────────┘
(after call, b)              ← ret 綁回 b

一旦算出 (entry id, p) ⇝ (exit id, ret)，就記成 summary edge：
   summary:  (b=id(a), a) ═══════════► (after call, b)
下次任何 caller 呼叫 id，直接走這條 ═══ 雙線，不進 id body。
```

雙線 `═══` 就是 summary edge。tabulation 的兩個 worklist 分別維護「path edge（從函式 entry 到某節點的可達邊）」與「summary edge」，交互推進直到收斂。細節（procedure call / return / normal flow 三種 flow function）留給論文，這裡抓住「**summary edge 讓 callee 只算一次且 context-sensitive**」這個 payoff。

## 可跑的教學 demo（標清楚：非完整 IFDS）

下面這支我**跑過**，但它是**手工示意 tabulation 的 summary edge 複用**，不是完整 IFDS 引擎（沒建完整 exploded supergraph、沒處理一般 flow function）。目的是讓「summary edge 只算一次、被多個 caller 複用」變具體。

```python
# summary of id：參數 p tainted -> 回傳值 tainted（算一次）
def id_summary(arg_tainted):
    p_tainted = arg_tainted
    ret_tainted = p_tainted        # return p
    return ret_tainted

def main_flow():
    trace = []
    tainted = {"a"}                                  # a = source()
    trace.append(("a=source()", set(tainted)))
    b_tainted = id_summary("a" in tainted)           # b = id(a)：套 summary edge
    if b_tainted: tainted |= {"b"}
    trace.append(("b=id(a)  [套用 summary edge]", set(tainted)))
    trace.append(("sink(b)", set(tainted)))
    return trace, ("b" in tainted)

trace, hit = main_flow()
print("=== tabulation：跨函式 taint（套用 id 的 summary edge）===")
for stmt, facts in trace:
    print(f"  {stmt:35s} tainted={sorted(facts)}")
print(f"\nsink(b) 被污染到？ {hit}")

def other_caller():                                  # d = id(c)，複用同一 summary
    return id_summary(True)
print("第二個 caller other()：d = id(c) 直接複用 summary ->", other_caller())
```

真跑輸出（照貼）：

```
=== tabulation：跨函式 taint（套用 id 的 summary edge）===
  a=source()                          tainted=['a']
  b=id(a)  [套用 summary edge]          tainted=['a', 'b']
  sink(b)                             tainted=['a', 'b']

sink(b) 被污染到？ True
第二個 caller other()：d = id(c) 直接複用 summary -> True
```

`id_summary` 只定義一次，`main` 與 `other` 都套它——這就是 summary edge 的複用。真正的 IFDS 引擎（如 Soot 的 Heros、WALA）差別在於它**自動**從 exploded supergraph 用 tabulation 推出這條 summary，而不是我手寫。

## IDE：帶 environment transformer 的推廣

**IDE（Interprocedural Distributive Environment，1996 同一批作者）** 是 IFDS 的推廣。IFDS 的 fact 是「有/無」的二元（一個變數 tainted or not）；IDE 讓每個 fact 額外攜帶一個**值**（透過 environment transformer 沿邊變換），能算「線性常數傳播」這類「不只有無、還帶值」的問題。審計裡少直接手寫 IDE，但知道「IFDS 是 IDE 的特例（值域退化成布林）」能幫你理解為什麼有些工具能在 taint 之上再帶 provenance 或數值資訊。一句帶過即可。

## 審計視角：為什麼這是 SAST 的底層

- **現代 taint 工具多是 IFDS 家族**：需要 precise、context-sensitive、又能規模化的程序間 taint，IFDS 的多項式複雜度 + summary 複用是目前最實用的骨架。商業 SAST（Checkmarx、Fortify 一類）與學術工具（FlowDroid 用 IFDS 做 Android taint）底層都是這套或其變體。
- **summary edge 就是「函式 model」的理論根**：當某函式沒有原始碼（libc、framework API），你沒法對它跑 tabulation 算 summary——於是你**手寫**一條 summary：「`strcpy(dst, src)`：src tainted ⟹ dst tainted」。這正是 CodeQL 的 **global dataflow / models-as-data**（Ch 22、Ch 23）在做的事：用 YAML/data 宣告 API 的 flow summary，等價於手動提供 IFDS 的 summary edge。**你在 Ch 23 寫 `models-as-data` 時，寫的就是 summary edge**——這條橋接記牢。

## 踩雷集錦

**錯誤直覺：「IFDS 能解所有 dataflow 問題。」**
正確認識：只能解 **distributive** 的 IFDS 問題。transfer function 一旦不可分配——例如需要「兩個 fact 同時成立才推出第三個 fact」（relational、需要 fact 之間關聯）的分析——就跳出 IFDS 框架。points-to 的某些精確形式、需要值關聯的分析都不是 IFDS。taint/reaching definitions 剛好是 distributive，所以吃 IFDS；別把它當萬能。

**錯誤直覺：「IFDS 算出來的 taint 是 sound 的。」**
正確認識：IFDS 對它建模的邊是精確的，但它**不會自動處理 aliasing**。`p = &x; *p = tainted;` 之後 `x` 該不該 tainted，取決於外部餵給 IFDS 的 points-to 資訊。points-to 沒算對，IFDS 照樣漏（`x` 沒被標 tainted）。IFDS 是「在給定的邊上做精確可達性」，邊本身的完整性靠 Ch 6 的 points-to。sound 與否在別處。

**錯誤直覺：「summary edge 是那個函式的精確語意。」**
正確認識：summary edge 是**針對這個 dataflow 問題**的摘要，不是函式的完整語意。`id` 的 taint summary 是「p tainted ⟹ ret tainted」，它沒說 `id` 做了什麼算術、有沒有副作用——那些跟當前 taint 問題無關所以不記。換一個 dataflow 問題（例如 nullness），同一函式的 summary 完全不同。summary 是「問題相關的摘要」，別當成函式規格。

**錯誤直覺：「exploded supergraph 很大，所以 IFDS 很慢。」**
正確認識：圖大不代表慢。tabulation 的複雜度是 $O(E \cdot D^3)$，是**多項式**，關鍵在 fact 數 D。IFDS 快的原因正是它把指數的 context 問題壓成多項式的圖可達性 + summary 複用。真正讓它慢/爆的是 D 太大（追蹤太多 fact）或 points-to 太粗導致邊爆炸，不是 supergraph 節點多。

**錯誤直覺：「context-sensitive 靠給每個 call site 複製一份 callee。」**
正確認識：那是 call-string / cloning 的做法，會指數爆。IFDS 的 context-sensitivity 來自 **summary edge 把 call 的輸入精確接回同一 call 的輸出**，不需要複製 callee body。這就是它能又精確又可擴展的核心巧思。

## 進階延伸

- **on-demand / 稀疏 IFDS**：不建整張 exploded supergraph，只從你關心的 sink 反向按需求可達性（demand-driven）。審計時你通常只關心「有沒有污染流到這個 sink」，按需求算比全圖算快得多——這也是為什麼 CodeQL 能對大 codebase 只算你 query 要的 flow。
- **FlowDroid**：Android taint 分析的經典開源系統，直接建在 IFDS（Heros solver）上，是「IFDS 落到真實 taint 工具」的最好讀原始碼範本。
- **flow function 的三種類型**：完整 IFDS 有 normal、call、return（有些版本再加 call-to-return）四種 flow function，分別處理函式內語句、進入 callee、從 callee 回來、以及「跳過 callee 直接接下去」的 local fact。想手刻 IFDS 就得把這四種搞清楚（練習 A 的進階版可以試）。

## 本章重點整理

- 跨函式 taint 暴力做會**指數爆炸 + unrealizable path 誤報**；IFDS 用圖可達性 + summary edge 解決兩者。
- IFDS 適用 **finite + distributive** 的 dataflow 問題（taint、reaching definitions 符合）。它把 dataflow 化約成 **exploded supergraph 上的可達性**：`(n, d)` 可達 ⟺ fact d 在 n 成立。
- **tabulation 演算法**用 path edge + summary edge，$O(E \cdot D^3)$ 多項式時間。**summary edge = 函式的輸入→輸出 fact 摘要，只算一次、被所有 caller 複用**，同時給出 context-sensitivity。
- **IDE** 是 IFDS 帶值（environment transformer）的推廣。
- 審計意義：現代 taint SAST 多是 IFDS 家族；**summary edge 就是「函式 model」的理論根，等價於 CodeQL 的 models-as-data（Ch 23）**。
- IFDS 不處理 aliasing（靠 Ch 6 points-to 餵邊）、不解 non-distributive 問題、summary 是問題相關的摘要而非函式語意。

## 自我檢核

- 說出暴力程序間分析的兩個問題（重算、unrealizable path），以及 summary edge 各怎麼解掉它們。
- IFDS 要求哪兩個條件？「distributive」為什麼跟 Ch 4 的 MFP=MOP 是同一件事？舉一個**不** distributive 因而不能用 IFDS 的分析。
- 用一句話說「(n, d) 可達 ⟺ fact d 在 n 成立」為什麼把 dataflow 變成圖問題。exploded supergraph 裡的 `0` fact 是幹嘛的？
- 為什麼 summary edge 同時給了「不重算」與「context-sensitive」？unrealizable path 為什麼被它天生排除？
- 為什麼說「你在 Ch 23 寫 models-as-data，寫的就是 summary edge」？沒原始碼的 libc 函式，它的 taint summary 從哪來？
- IFDS 的 taint 結果 sound 嗎？aliasing 沒算對時它會漏報還是誤報，責任在 IFDS 還是別的分析？

## 延伸閱讀

- **Reps, Horwitz, Sagiv, *Precise Interprocedural Dataflow Analysis via Graph Reachability*, POPL 1995**——IFDS 原始論文。讀 Section 2–4：IFDS 問題定義、exploded supergraph 建法、tabulation 演算法與 summary edge。前提：本章 + Ch 4 的 distributive 概念。這是全章的源頭，值得逐字讀。
- **Sagiv, Reps, Horwitz, *Precise Interprocedural Dataflow Analysis with Applications to Constant Propagation*, TCS 1996**——IDE 論文，IFDS 帶 environment transformer 的推廣。想理解「帶值的 IFDS」再讀，看 constant propagation 那個例子最直觀。前提：先懂 IFDS。
- **Arzt et al., *FlowDroid: Precise Context, Flow, Field, Object-sensitive... Taint Analysis for Android*, PLDI 2014**——IFDS 落到真實 taint 工具的教科書級案例，開源可讀原始碼。看它怎麼把 Android lifecycle 建成 supergraph、怎麼餵 points-to 給 IFDS。前提：本章。銜接你對「真實 SAST 底層」的想像。
- **Bodden, *Inter-procedural Data-flow Analysis with IFDS/IDE and Soot*, SOAP 2012**——用 Heros/Soot 手把手實作 IFDS 的 tutorial paper，最實用的動手入口。想真的刻一個 IFDS solver 從這篇開始。前提：Java + 本章。

我們反覆說「IFDS 不處理 aliasing，邊的完整性靠 points-to」。那 points-to 到底怎麼算、精度怎麼取捨、Andersen 與 Steensgaard 差在哪——下一章把這塊補上，它是所有 taint 邊 sound 與否的地基。

→ [Ch 6 指標分析](./06-points-to-analysis.md)
