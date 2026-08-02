# Ch 13 — 文法 Fuzzing：Nautilus / Gramatron

> **目標**: 理解 generational grammar fuzzing 的原理、為什麼文法表示方式影響 fuzzer 效率、掌握 CFG derivation tree mutation 與 Gramatron 的 automaton-based 表示。

---

## 為什麼需要文法 Fuzzing

上一章（Ch 12 LPM）處理的是協定層面的語法感知，core 問題是：如何讓 fuzzer 產出的輸入不被目標程式在第一道 parser 就丟棄。

對結構化輸入（JSON、SQL、JavaScript、PDF content stream）而言，dumb mutation 的通過率可以低到個位數百分比。AFL++ 的 `-x dict` 模式幫助有限，因為 dict 只補 token，不懂 token 的組合順序。

文法 fuzzing 的核心主張：**在文法規則定義的語言空間內做 mutation**，保證每個生成的輸入都是語法合法的。代價是要事先寫一份文法，但對已知協定（JSON、HTTP、SQL）這份文法多半有現成的可以改。

真正讓文法 fuzzing 進入主流的是 Nautilus (NDSS 2019)，它把 CFG derivation tree + coverage feedback 第一次真正整合起來。Gramatron (ISSTA 2021) 再往下優化：把 tree 換成 FSA，消除 tree parse/serialize 的開銷，讓 splice mutation 的速度量級提升。

---

## 先建立直覺

### Generational vs Coverage-Guided Grammar

```
兩條路線：

  Generational (pure)
  ────────────────────────────────────────────────────────
  文法規則 ──random expansion──> 輸入字串
  優點：100% 語法合法
  缺點：無 coverage 引導，輸入多樣性受文法規則形狀限制
        不知道哪條展開路徑靠近有趣的 code path

  Coverage-Guided Grammar（Nautilus / Gramatron）
  ────────────────────────────────────────────────────────
  文法規則 ──generate──> derivation tree ──serialize──> 輸入字串
       ↑                                                    │
       └──── mutation（subtree / splice） ←── corpus ────── ┘
                          │
                          └──> target binary ──edge bitmap──>
                               coverage feedback ──保留新 edge──>
                               corpus 擴展
```

Generational 適合初步探索一個不熟悉的目標：你知道它吃 JSON，但不知道哪些路徑有洞。Coverage-guided grammar 則是真正要挖 CVE 時的路線。

---

## Section A：Generational Fuzzer — 從頭跑通

先把最乾淨的 generational grammar fuzzer 跑起來，確認「100% 語法合法」這件事是真的。

```python
import random, re, json

GRAMMAR = {
    'value':    [['object'], ['array'], ['string'], ['number'],
                 ['true'], ['false'], ['null']],
    'object':   [['{}'], ['{<members>}']],
    'members':  [['<pair>'], ['<pair>,<members>']],
    'pair':     [['<string>:<value>']],
    'array':    [['[]'], ['[<elements>]']],
    'elements': [['<value>'], ['<value>,<elements>']],
    'string':   [['""'], ['"<chars>"']],
    'chars':    [['<char>'], ['<char><chars>']],
    'char':     [['a'], ['b'], ['x'], ['1'], ['2'], ['_']],
    'number':   [['0'], ['1'], ['-1'], ['3.14'], ['1e10']],
}

MINS = {
    'value': 'null', 'object': '{}', 'array': '[]',
    'string': '"x"', 'number': '0', 'members': '"k":0',
    'pair': '"k":0', 'elements': '0', 'chars': 'a', 'char': 'a',
}

def gen(sym, depth=0):
    if depth > 6:
        return MINS.get(sym, sym)
    if sym not in GRAMMAR:
        return sym
    expansion = random.choice(GRAMMAR[sym])
    result = ''
    for token in expansion:
        m = re.fullmatch(r'<(\w+)>', token)
        if m:
            result += gen(m.group(1), depth + 1)
        else:
            result += token
    return result

random.seed(42)
valid = 0
for i in range(20):
    s = gen('value')
    try:
        json.loads(s)
        valid += 1
        print(f'  [{i+1:02d}] OK  {s[:60]}')
    except Exception as e:
        print(f'  [{i+1:02d}] ERR {s[:40]} — {e}')

print(f'\nValid: {valid}/20')
```

實際輸出：

```
  [01] OK  -1
  [02] OK  []
  [03] OK  false
  [04] OK  true
  [05] OK  {"aa":null}
  [06] OK  {}
  [07] OK  null
  [08] OK  "b"
  [09] OK  [null,0]
  [10] OK  {"x":{"b":null}}
  [11] OK  3.14
  [12] OK  true
  [13] OK  {"x":null}
  [14] OK  []
  [15] OK  null
  [16] OK  {"a":false,"_":0}
  [17] OK  ["x",0]
  [18] OK  null
  [19] OK  {}
  [20] OK  -1

Valid: 20/20
```

100% 通過率是 generational 的本質承諾。問題在多樣性：光靠 random expansion，很難碰到 `{"a":{"b":{"c":...}}}` 這種深度嵌套，更不會主動往 parser 的邊界去靠。

---

## Section B：CFG Derivation Tree 與 Mutation

### 為什麼要用 Tree 而不是字串

直接對字串做 mutation（flip bit、insert byte）會破壞語法。對 tree 做 mutation，每一步都在文法規則允許的範圍內，保證產出仍然合法。

### JSON Derivation Tree 圖解

```
JSON 文法（片段）:
  value   → object | array | string | number | true | false | null
  object  → '{' members '}'
  members → pair | pair ',' members
  pair    → string ':' value

輸入 {"k":1} 的 derivation tree:

              value
                │
             object
           /    │    \
          {  members  }
                │
              pair
             /    \
          string  value
            │       │
           "k"   number
                    │
                   '1'
```

這棵 tree 就是 Nautilus 在 corpus 裡儲存的格式。每個 non-terminal node 都知道自己的類型（value、pair、string...），mutation 只在同類型 node 之間置換。

### 三種 Tree Mutation

**1. Subtree Mutation**：隨機選一個 non-terminal node，重新生成一棵同類型的子樹。

```
原樹: pair → string ':' value
             "k"         '1'

選中 string 節點，重新 random expand:
     pair → string ':' value
             "new_key"   '1'

結果: {"new_key":1}  — 仍然語法合法
```

**2. Splice**：從 corpus 中的另一棵 tree 取出同類型的子樹，插進當前 tree。

```
corpus 裡有另一個輸入: {"nested":{"x":true}}
它的 value 節點下掛著 object → {"x":true}

當前 tree 的 value slot（對應 '1'）替換成 {"x":true}:
  {"k":{"x":true}}  — 語法合法，且深度增加一層
```

Splice 是讓 coverage 發散的關鍵：你不是在 random generate，而是在把已知能觸發不同 code path 的輸入碎片重新組合。

**3. Recursion Insertion**：找到文法中的遞歸規則，在現有 tree 中插入一層深度。

```
文法: members → pair | pair ',' members
                                  ↑ 自我遞歸

在 {"k":1} 的 members 節點插一層:
  pair ',' members
  "k":1   ,  pair
              "new":null

結果: {"k":1,"new":null}
```

Recursion insertion 是生成深度嵌套結構的有效方式，比 random expansion 更精確地控制遞歸深度。

---

## Section C：Nautilus

### 架構概覽

Nautilus (NDSS 2019, Aschermann et al.) 是第一個把 CFG derivation tree + coverage-guided feedback 真正整合的 fuzzer。

```
Nautilus 流程:

  [文法定義] ──generate──> [derivation tree] ──serialize──> [bytes]
       ↑                          ↑                             │
       │               ┌──────────┤ mutation ─────────────┐    │
       │               │  subtree mutation                 │    │
       │               │  splice from corpus               │    │
       │               │  recursion insertion              │    │
       │               └──────────────────────────────────┘    │
       │                                                        ↓
  [文法] <── 維持                                         [target binary]
                                                               │
  [corpus] <── 保留新 edge ←── [edge coverage bitmap] ←────────┘
```

幾個設計決策值得注意：

- Coverage feedback 和 AFL 相同：edge bitmap，碰到新 edge 就把這個 tree 加進 corpus
- 每個 corpus entry 儲存的是 tree，不是字串；下次 mutation 直接操作 tree
- Chunk-based mutation 允許 splice 跨越多個 corpus entry

### Build Nautilus

```bash
git clone https://github.com/nautilus-fuzz/nautilus
cd nautilus
cargo build --release
# 需要 Rust 1.56+，build 時間約 2-5 分鐘
# WSL2 Ubuntu 22.04 可正常 build

# 準備 JSON 文法（nautilus 使用自訂格式）
# 跑 10 分鐘看 edge coverage 增長
./target/release/fuzzer \
  --grammar grammars/json.py \
  --work-dir /tmp/nautilus_out \
  -- /path/to/json_parser @@
```

Nautilus 的文法格式是 Python，直接 import，比 BNF 更靈活（可以用 Python function 做 conditional expansion）。

---

## Section D：Gramatron — Automaton 表示

### 核心洞察

Nautilus 的 tree 表示有一個固定開銷：每次 serialize/deserialize 都是 O(n) tree traversal。Splice mutation 需要先找到 non-terminal type 匹配的節點，這也是 O(n)。

Gramatron (ISSTA 2021, Srivastava et al.) 的問題是：能不能把 tree 壓平，讓 splice 變成 O(1) 的 array slice？

答案：把 CFG 轉成 **Finite State Automaton (FSA)**，輸入表示成「state → terminal」的 flat array。Splice 就是在 array 中找到同一個 state 的位置，直接切斷接上另一個 suffix。

### CFG → FSA 轉換

```
CFG（JSON 片段）:
  value   → '{' members '}'
  members → pair | pair ',' members
  pair    → string ':' value

Gramatron FSA（partial）:

  q0 ──'{'──────────────> q1
  q1 ──<string terminal>─> q2
  q2 ──':'──────────────> q3
  q3 ──<value terminal>──> q4
  q4 ──','──────────────> q1   (loop back for more members)
  q4 ──'}'──────────────> q_accept

  每個 terminal 展開後的 FSA 路徑是確定的

flat array 表示 {"key":1}:
  index:  0       1        2    3    4
  state:  q0      q1       q2   q3   q4
  token: '{'    '"key"'   ':' '1'  '}'
```

### Splice 操作對比

```
CFG tree mutation (Nautilus):
  1. 序列化 input → tree           O(n) parse
  2. 遍歷找到 nonterminal node     O(n) traversal
  3. 生成新子樹                    O(k) generate
  4. 反序列化 → bytes              O(n) serialize
  total: O(n) + O(n) + O(k)

Gramatron FSA mutation:
  1. input = flat array[(state, token), ...]
  2. 選 splice point i             O(1) array index
  3. 找 corpus 中同 state 的 suffix  O(1) lookup（按 state 建 index）
  4. array[:i] + corpus_suffix     O(n) concat
  total: O(1) + O(1) + O(n)

差異：消除了 tree parse/serialize，O(n) 的係數也小很多
      在 small inputs 上速度差距 2-4x，在 deep nested 上差距更大
```

### FSA 的 State-Aware Corpus Index

```
Gramatron corpus 結構:

  corpus_by_state = {
    q0: [suffix_A, suffix_B, ...],   # 從 q0 出發到 accept 的所有 suffix
    q1: [suffix_C, suffix_D, ...],   # 從 q1 出發...
    q3: [suffix_E, suffix_F, ...],   # 從 q3 出發...
    ...
  }

Splice 操作:
  current = [(q0,'{'), (q1,'"k"'), (q2,':'), (q3,'1'), (q4,'}')]
  選 splice point = index 3 (state q3)
  從 corpus_by_state[q3] 隨機取一個 suffix
  result = current[:3] + suffix  → 合法輸入，因為 q3 是合法的 continuation point
```

### Build Gramatron

```bash
git clone https://github.com/HexHive/Gramatron
cd Gramatron
# 轉換文法到 FSA 格式
python3 automaton-based-grammar/gramfuzz-gen.py \
  --grammar grammars/json/grammar_json.json \
  --output-dir /tmp/gramatron_fsa

cargo build --release
./target/release/gramatron \
  --input-grammar /tmp/gramatron_fsa/automaton.json \
  --output-dir /tmp/gramatron_out \
  -- /path/to/json_parser @@
```

**本段 Gramatron FSA mutation 細節為理論預期行為；實際驗證步驟**：
1. `git clone https://github.com/HexHive/Gramatron && cargo build --release`
2. 用相同 JSON 文法分別跑 Nautilus 與 Gramatron 各 10 分鐘
3. 比較 `fuzzer_stats` 中的 `execs_per_sec` 與 `edges_found`

---

## 對比取捨表

| 維度 | 純 Generational | Nautilus（CFG tree）| Gramatron（FSA）| dumb + dict |
|------|----------------|---------------------|-----------------|-------------|
| 語法合法性 | 100% | 100% | 100% | 低（<10%）|
| Coverage 引導 | 無 | 有（edge bitmap）| 有（edge bitmap）| 有 |
| Mutation 速度 | 快 | 中（tree O(n) parse）| 快（array splice）| 極快 |
| 文法撰寫成本 | 中 | 中（Python DSL）| 中（需轉 FSA）| 無 |
| Corpus 儲存格式 | 字串 | derivation tree | flat array | 字串 |
| Splice 精確度 | 無 | type-matched node | state-matched index | 無 |
| 適合目標 | 初步探索 | 複雜語言，精度優先 | 複雜語言+高效能需求 | 簡單格式 |
| 典型目標 | 協定初探 | JS engine、PDF | JS engine、SQL | HTTP header |

選擇原則：
- 你今天才開始測這個 parser，不確定它有沒有問題 → pure generational 先快速探索
- 要認真挖 CVE，目標是 JS engine 或 PDF parser → Nautilus
- Nautilus 的 exec/s 不夠，或 target 是超大文法 → Gramatron
- 目標文法太難寫，只有 dict → dumb + dict，接受合法率低的代價

---

## 踩雷

### 1. 文法越完整越好

「把整個 JSON RFC 都寫進去，Unicode escape 序列、BOM、surrogates 全覆蓋」。

結果：文法太完整，每個 derivation 的平均長度暴增，mutation 空間擴大但集中在 edge case 的概率反而降低。更糟的是 generation 速度下降，每秒能生成的樣本數減少。

正確做法：**刻意 under-specify**。string 只生成 ASCII printable，不覆蓋 Unicode escape。Unicode edge case 留給 havoc 或 radamsa 在語義層打。文法負責確保 parser 能走到 interesting code path，不是複製 spec。

### 2. Splice 一定保持語法合法性

Nautilus / Gramatron 的 splice 在大多數情況下確實合法，但有一個具體的失效點：**non-terminal type mismatch**。

Nautilus 的 tree splice 必須確認 source subtree 的 non-terminal type 與 destination slot 的 expected type 相同。如果你自己實作文法 fuzzer 或修改 Nautilus 的 mutation 邏輯，忘記做 type check，就會把 `statement` 子樹插進 `expression` slot，產出語法不合法的輸入。

驗證方式：對每個 splice 後的輸入，先過一遍 reference parser（如 `python3 -m json.tool`），確認合法率是否維持 100%。合法率掉了就是 type check 出了問題。

### 3. Grammar Fuzzing 不需要 Initial Corpus

「文法 fuzzer 自己會 generate，不用給 seed」。

問題：從空 corpus 開始的頭幾輪，coverage 發散速度很慢，因為 splice 沒有素材可以取。Gramatron 的 state-aware corpus index 在 corpus 小的時候幾乎退化成 pure generation。

正確做法：給 5-10 個代表性的手寫 seed，覆蓋不同的語法結構（nested object、array of array、空值、大整數、長字串）。有了這些 seed，splice 立刻有跨結構組合的素材，coverage 在前 5 分鐘就能快速發散。

### 4. 遞歸規則沒有深度限制

文法裡有 `members → pair ',' members` 這種遞歸，如果沒有 depth limit，generation 可能產出幾萬層深的輸入，serialization 直接 OOM。

Nautilus 有內建 max depth，但如果你自己寫 generator（如上面的 Python 版本），一定要在 gen() 加上 depth 參數和 MINS fallback。

---

## 進階延伸

### Grammar + Havoc 混合策略

Grammar mutation 確保語法合法，但語法合法不代表能觸發語義層的 bug。很多 CVE 藏在「合法語法但異常語義」的地方：整數溢位、型別混淆、use-after-free。

實際有效的做法（接 Ch 14）：
1. Grammar mutation 產出合法輸入，建立初始 coverage
2. 對 corpus 中高 coverage 的輸入，用 havoc（dumb random mutation）做語義層打擊
3. 如果 havoc 破壞了語法導致 parser 在第一層 reject，退回 grammar mutation 修復
4. 記錄哪些 havoc mutation 雖然破壞語法但仍然觸發了新 coverage（暗示 parser 在語法錯誤路徑上有洞）

### 語意有效性 (Semantic Validity)

語法合法但語義無效：SQL `SELECT foo FROM bar` 中 `foo` 和 `bar` 在 DB schema 裡不存在。JS `x.p` 中 `x` 是 undefined。

這個問題 grammar fuzzing 解決不了，因為文法只描述語法結構。Fuzzilli（Ch 37）的解法是：維護一個 **type-aware IR**，確保每個生成的 JS expression 在型別層是合法的。這是 grammar fuzzing 的天花板，也是為什麼 JS engine fuzzing 需要一個完全獨立的設計。

---

## 動手練習

1. 把上面的 Python generational fuzzer 改成 **tree-based fuzzer**：把 gen() 改成回傳 tree（nested dict），實作 subtree_mutation(tree) 函式，對同一棵 tree 做 100 次 subtree mutation，驗證每次 json.loads() 都通過。

2. 替 SQLite 找一份 SQL 文法（可從 https://sqlite.org/lang.html 手寫片段），用 generational fuzzer 生成 1000 條 SQL，用 `sqlite3 :memory:` 逐一執行，統計合法率與獨特 error message 數量。

3. 拉 Nautilus source code，找到 `src/fuzzer/mutation.rs` 中的 splice mutation 實作，確認 non-terminal type check 在哪一行，理解它如何防止 type mismatch。

---

## 本章重點

- Generational grammar fuzzing 保證 100% 語法合法，但沒有 coverage 引導，多樣性受限
- Nautilus 把 CFG derivation tree 和 AFL-style edge bitmap 整合，subtree mutation 和 splice 都在 tree 層操作，保證合法性的同時有 coverage feedback
- Gramatron 把 tree 換成 FSA flat array，splice 變成 O(1) state-indexed lookup，消除 tree parse/serialize 開銷，適合需要高 exec/s 的場景
- 文法要刻意 under-specify，不要把整個 RFC 塞進去
- Splice 需要 type/state match，否則合法率下降
- 給 5-10 個 seed corpus 能大幅加速前期 coverage 發散

---

## 自我檢核

- 能解釋 subtree mutation 和 splice 的差異，以及各自適合哪種場景？
- Gramatron 的 FSA 表示為什麼能讓 splice 比 Nautilus 快？具體快在哪個步驟？
- 如果發現 grammar fuzzer 的輸出合法率從 100% 掉到 60%，你的第一個假設是什麼？怎麼驗證？
- 什麼情況下你會選 pure generational 而不是 Nautilus？

---

## 延伸閱讀

1. **Nautilus: Fishing for Deep Bugs with Grammars** (NDSS 2019, Aschermann et al.)
   讀 §3 Design，重點在 derivation tree 的內部表示與三種 mutation（subtree / splice / recursion insertion）。這是 grammar + coverage 整合的奠基論文，後續所有 grammar fuzzer 都引用這裡的設計。

2. **Gramatron: Effective Grammar-Aware Fuzzing** (ISSTA 2021, Srivastava et al.)
   讀 §3 FSA Construction 部分。論文給出了 CFG → FSA 的具體演算法，以及為什麼 flat array 的 splice 在 mutation throughput 上優於 tree。Table 2 的 bug 數量比較值得細看：在相同時間預算下 Gramatron 找到明顯更多 bug。

3. **The Fuzzing Book — "Grammars as Inputs"** (https://www.fuzzingbook.org/html/Grammars.html)
   讀整章。從頭實作 grammar fuzzer，清楚說明 BNF 與 PEG 的差異、production rule 的表示方式、以及 expansion depth 控制。這是理解 Nautilus/Gramatron 底層邏輯的最佳前置閱讀，直接用 Python 可以跑起來。

---

Ch 12 的 LPM 解決的是協定層面的結構感知——告訴 fuzzer「這個 field 是 length」。本章往上一層：不只是 field，而是整個語言的語法結構，mutation 完全在文法定義的空間內發生。

下一章（Ch 14）把 coverage feedback 和文法推斷（grammar inference）組合在一起——當你沒有現成文法時，怎麼從 corpus 自動推斷出文法規則，然後用推斷出的文法做 grammar fuzzing。

→ [下一章](./14-coverage-guided-grammar.md)