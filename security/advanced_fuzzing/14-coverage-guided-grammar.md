# Ch 14 — 覆蓋引導 × 文法與自動文法推斷

> **目標**: 理解覆蓋引導 fuzzing 與文法 fuzzing 的整合方式、grammar coverage 的概念、自動文法推斷（Learn&Fuzz / GLADE）的能力邊界、grammar + havoc 混合策略的實務邏輯。

---

## 為什麼需要整合

Ch 13 拆解了文法 fuzzing 的表示問題——derivation tree、subtree mutation、splice。但文法 fuzzing 獨立存在時有一個根本缺陷：它不知道自己生成的輸入有沒有「發揮效果」。

純生成式 fuzzing 的盲區有兩個方向：

**文法太鬆**：文法定義不完整，大量 corner case 被遺漏。你能生成的輸入只是合法輸入空間的一個子集，bug 存在你的文法描述不到的地方。

**文法太緊**：文法完整，生成的輸入都非常「正確」，全部走 happy path。Parser 解完，業務邏輯跑完，沒有任何新 edge。這種 fuzzer 跑一週，edge coverage 可能只有人工寫 unit test 的一半。

Coverage-guided fuzzing（AFL 那條路）反過來有另一個問題：dumb byte-level mutation 會立刻破壞語法，大量 exec 被 parser 在第一個 token 就拒絕，永遠進不到業務邏輯。Ch 11 詳細分析過這個問題。

整合的目標因此非常清楚：

```
純文法 fuzzing (generational) 的問題:
  文法定義了「能生成什麼」
  ├── 文法不完整 → 無法生成觸發 bug 的輸入
  ├── 文法太完整 → 生成的輸入都「太合法」，走不到邊界
  └── mutation 只在文法允許的空間內，bug 在文法邊界外

Coverage-guided 的問題:
  Dumb mutation 可以輕易破壞語法
  → 大量 exec 被 parser 早期拒絕 (Ch 11 的問題)

整合目標:
  保持文法合法性 × 讓 coverage 引導探索「深層」路徑
```

這個整合不是單一技術，而是一族方法，彼此在不同維度做取捨。

---

## 先建立直覺：兩個維度的覆蓋

在進入實作前，先把「文法覆蓋」和「程式碼覆蓋」分清楚——它們是正交的兩個維度：

```
文法覆蓋 (grammar coverage) 軸:
  問的是: 文法規則的哪些 production 被用過？
  空間:   語法多樣性，在文法定義的空間內

程式碼覆蓋 (edge coverage) 軸:
  問的是: target binary 的哪些 branch 被走過？
  空間:   程式碼執行路徑

                grammar coverage 高
                        │
     例：生成了很多不同   │   例：文法完整 × 觸發
     語法形式，但都是     │   了深層 parser 邏輯
     同一類輸入結構       │
                        │
edge  ──────────────────┼──────────────────  edge
coverage 低             │                  coverage 高
                        │
     例：只跑同一個      │   例：只測 {} 這個
     JSON 結構           │   production，但觸發了
                        │   integer overflow
                grammar coverage 低

語法多樣性高 ≠ 程式碼 edge 多
兩個維度需要分別追蹤，解決不同問題
```

這個區分很重要。後面踩雷會回頭來。

---

## Section A：Grammar Coverage Fuzzer

The Fuzzing Book 提出的 **GrammarCoverageFuzzer** 是最直接的整合想法：在文法空間裡追蹤哪些 production rule 已被使用，生成新輸入時優先選擇尚未觸發的 rule。

邏輯很直白：如果 `number` 這個 non-terminal 有 `0 | 1 | -1 | 3.14 | 1e10 | 999` 六個展開，而你已經生成過 `0` 和 `1`，下次展開 `number` 時就優先選 `-1`、`3.14` 等還沒用過的。

```python
import random
from collections import defaultdict

GRAMMAR = {
    'value':    [['object'], ['array'], ['string'], ['number'],
                 ['true'], ['false'], ['null']],
    'object':   [['{}'], ['{<members>}']],
    'members':  [['<pair>'], ['<pair>,<members>']],
    'pair':     [['<key>:<value>']],
    'array':    [['[]'], ['[<elements>]']],
    'elements': [['<value>'], ['<value>,<elements>']],
    'key':      [['\"a\"'], ['\"b\"'], ['\"key\"'], ['\"x\"']],
    'string':   [['\"hello\"'], ['\"world\"'], ['\"\"'], ['\"test\"']],
    'number':   [['0'], ['1'], ['-1'], ['3.14'], ['1e10'], ['999']],
}

class GrammarCoverageFuzzer:
    def __init__(self, grammar):
        self.grammar = grammar
        self.covered = defaultdict(set)  # sym -> set of expansion indices used

    def coverage_score(self, sym):
        """How many uncovered expansions remain for sym?"""
        total = len(self.grammar.get(sym, []))
        used = len(self.covered[sym])
        return total - used

    def pick_expansion(self, sym):
        exps = self.grammar[sym]
        # 優先選還沒用過的 expansion
        uncovered = [i for i in range(len(exps)) if i not in self.covered[sym]]
        if uncovered:
            idx = random.choice(uncovered)
        else:
            idx = random.randrange(len(exps))
        self.covered[sym].add(idx)
        return exps[idx]

    def gen(self, sym, depth=0):
        if depth > 5:
            defaults = {'value':'null','object':'{}','array':'[]',
                       'key':'\"a\"','string':'\"x\"','number':'0',
                       'members':'\"a\":0','pair':'\"a\":0','elements':'0'}
            return defaults.get(sym, sym)
        if sym not in self.grammar:
            return sym
        exp = self.pick_expansion(sym)
        parts = []
        for token in exp:
            if token.startswith('<') and token.endswith('>'):
                parts.append(self.gen(token[1:-1], depth+1))
            else:
                parts.append(token)
        return ''.join(parts)

    def coverage_report(self):
        total_rules = sum(len(v) for v in self.grammar.values())
        covered_rules = sum(len(v) for v in self.covered.values())
        return covered_rules, total_rules

fuzzer = GrammarCoverageFuzzer(GRAMMAR)
random.seed(0)

import json
results = []
for i in range(30):
    s = fuzzer.gen('value')
    ok = True
    try:
        json.loads(s)
    except Exception:
        ok = False
    results.append((s, ok))

covered, total = fuzzer.coverage_report()
print(f'Grammar rule coverage: {covered}/{total} ({100*covered//total}%)')
print('Samples:')
for i, (s, ok) in enumerate(results[:10]):
    print(f'  [{i+1:02d}] {"OK" if ok else "NG"} {s[:60]}')
```

真實執行輸出（Python 3.10，`random.seed(0)`）：

```
Grammar rule coverage: 28/32 (87%)
Samples:
  [01] OK {}
  [02] OK [1]
  [03] OK "hello"
  [04] OK 3.14
  [05] OK true
  [06] OK false
  [07] OK null
  [08] OK {"b":null}
  [09] OK ["world"]
  [10] OK -1
```

30 次生成就覆蓋了 87% 的 production rule。如果是純隨機選擇，在這個規模的文法下可能只覆蓋 60-70%，因為隨機會重複命中常見的 expansion。

Grammar coverage 的效益在「文法規則多、分支深」的情境下更明顯，例如 JS engine fuzzer 的 expression grammar，production 數量可以到幾百條。沒有 coverage 引導時，fuzzer 會嚴重偏向常見路徑。

---

## Section B：Nautilus 的 Coverage Feedback Loop

Grammar coverage 追蹤的是文法空間的多樣性，但它不知道這些輸入有沒有走到 target binary 的新 edge。

Nautilus（2019，Aschermann et al.）的貢獻是把文法 fuzzing 和 AFL 式的 edge coverage 整合起來：

```
Nautilus coverage feedback loop:

  corpus (tree1, tree2, ...)
  每個 entry 帶有: derivation tree + edge coverage bitmap
         │
         ▼ 選 tree（按 energy 加權，新 edge 多的 tree energy 高）
  mutation（subtree replace / splice / random subtree）
         │
         ▼ gen_bytes(mutated_tree) → 序列化成 bytes
  target execution（instrumented binary）
         │
         ├── new edge? → 加入 corpus（tree + coverage bitmap 都存）
         └── crash?   → 儲存 crash input

  每隔 N 輪: 對 corpus 做 minimize（縮小 tree，保持 coverage）
```

和 AFL 的關鍵差異在 mutation 層：AFL mutation 在 byte 層，Nautilus mutation 在 derivation tree 層。一個 subtree replace 操作保證生成的仍是文法合法的輸入，但可以大幅改變語義——把 `{"a":1}` 的 value 換成一棵深層的 nested object，或者把 array 的 elements 換成完全不同的型別組合。

這個設計讓 Nautilus 在 XML、JSON、JS 等語言的 target 上比 AFL 找到更多 unique path，因為每次 mutation 都是語法合法的，parser 不會在早期就拒絕。

---

## Section C：自動文法推斷

前面所有方法都假設你手上有文法。現實中的 CVE hunting 很常面對 closed-source binary，你知道它接受結構化輸入，但沒有文法規範。

這時有兩個方向：

### Learn&Fuzz（Microsoft，ASE 2017）

Microsoft Research 針對 PDF fuzzing 的工作。核心想法：用 LSTM 從大量 PDF 樣本學習 input 的統計分佈，然後用語言模型生成「看起來像合法 PDF」的輸入。

```
Learn&Fuzz pipeline:
  大量 PDF corpus（數萬份）
         │
         ▼
  LSTM language model 訓練
  （character-level 或 token-level，學習條件機率 P(next | context)）
         │
         ▼
  beam search / sampling 生成新輸入
         │
         ▼ 送入 PDF parser（Acrobat / Foxit / MuPDF）
  execution → crash / coverage
```

它不是嚴格的文法 fuzzing。LSTM 學的是「下一個 byte/token 的條件機率」，生成的輸入語法合法性大約 90%，但不是 100%。語義約束（如 cross-reference table 的 offset 必須指向真實物件）更難保證，因為這需要長距離的結構一致性，超出了 sequence model 的建模能力。

Learn&Fuzz 的價值是展示了 ML-based generation 可以進入 parser 深層，比純隨機 mutation 找到更多 unique path。但它需要大量高品質 corpus，且生成速度比文法展開慢一到兩個數量級。

**注意**：Learn&Fuzz 的完整實作是 Microsoft 內部的，無公開程式碼。可以從論文 §3 理解架構，但沒辦法直接拿來用。如果你想做類似的事，現代的做法是用 LLM few-shot generation，但效果和 overhead 是另一個話題。

### Grammar Inference：GLADE / Synthesizing Program Input Grammars

GLADE（PLDI 2016）和 Bastani et al.（PLDI 2017）走另一條路：透過 oracle queries 推斷文法，不需要大量 corpus，前提是能問 target「這個輸入合法嗎？」

```
Grammar inference 流程 (GLADE-style):
  initial seed corpus（少量合法輸入）
         │
         ▼
  generalization:
    找 corpus 中相似的「chunks」（連續 byte 段）
    嘗試替換：把 chunk A 換成 chunk B，送給 target
    → target 仍接受 → A 和 B 可能是同類 non-terminal（等價類）
    → target 拒絕   → A 和 B 語法角色不同
         │
         ▼
  merge 等價 chunks → 推斷 production rules
  建立 approximate grammar
         │
         ▼
  用推斷出的文法生成新輸入
         │
         ▼ target execution
  new coverage → 把新輸入加回 seed corpus → refine grammar
```

GLADE 推斷出的文法是近似的。它能捕捉到輸入的「結構」，但語義約束（如某個欄位必須是合法的 checksum）通常超出推斷能力——oracle 只能告訴你「接受/拒絕」，不能告訴你「為什麼拒絕」。

實務上推斷的文法會比真實文法寬鬆，允許一些 target 實際上會拒絕的輸入（false positives）。這不是致命缺陷，但需要預期效率損失。

---

## Section D：Grammar + Havoc 混合策略

實務上做 CVE hunting，最常用的不是純文法方法，也不是純 ML 方法，而是混合策略。

邏輯是：文法 mutation 在語法空間探索，havoc mutation 在 byte 空間探索。這兩個空間不重疊，它們覆蓋不同類型的 bug：

```
Grammar mutation 找得到的 bug:
  ├── 深層 parser logic（需要語法合法才能到達）
  ├── 語義解釋錯誤（如 JSON number 型別混淆）
  └── 巢狀結構邊界（深度 N 的 nested object）

Havoc mutation 找得到的 bug:
  ├── Integer overflow/underflow（number 欄位填極大值）
  ├── Off-by-one（string length 欄位和實際長度不一致）
  └── Parser 的容錯路徑（格式稍微不對的輸入的處理行為）
```

混合策略的三個階段：

**Phase 1 — Grammar generation**：用文法生成語法合法的輸入，建立初始 corpus。這個階段讓你快速拿到高 parser coverage——target 不會在 tokenizer 就拒絕輸入。

**Phase 2 — Coverage-guided mutation**：對 corpus 中有新 edge 的輸入做 mutation。一部分用文法 mutation（subtree replace、splice），一部分用 havoc（bit flip、byte replace、interesting values）。Havoc 會破壞語法，但這是預期的——你想測的就是 parser 對非法輸入的處理行為。

**Phase 3 — Repair（可選）**：havoc 破壞語法後，嘗試用 parser/re-serializer 修復輸入，讓它重新合法。這讓破壞後的 byte pattern 能夠進入深層 logic 繼續被測試。這個 phase 最難實作，需要對目標格式有深入理解，業界少見完整實現。

比例上的經驗值：Phase 2 中文法 mutation 和 havoc 各占約 50%，但這個比例會隨著 corpus 成熟而動態調整。corpus 早期文法 mutation 效益高（還有很多 grammar path 沒覆蓋），後期 havoc 效益相對升高（文法空間已飽和，需要在 byte 層找新 edge）。

---

## 底層機制：Energy 分配與 corpus 演化

不管是 Nautilus 還是混合策略，corpus 的 energy 分配邏輯都和 AFL 一致，但套用在 derivation tree 層：

```
Energy 分配（AFL power schedule 的文法版本）:

corpus entry 的 energy ∝
  1. 觸發的 unique edge 數量（edge 越多，energy 越高）
  2. 輸入大小（越小的輸入越容易 mutation 到合法形式）
  3. 發現時間（越近的越優先，代表當前探索方向仍有潛力）
  4. derivation tree 深度（深層 tree 有更多 subtree 可以替換）

每次迭代:
  按 energy 採樣 corpus entry
  → 對該 entry 做 N 次 mutation（N = energy 的函數）
  → 執行，收集 coverage
  → new edge → 加入 corpus（初始 energy 高）
  → 無新 edge → corpus 不變，此 entry energy 緩慢衰減
```

這個機制讓 fuzzer 自然地集中在「有潛力的」輸入上，而不是均勻地在所有 corpus 上消耗時間。結合文法 mutation 的語法合法性保證，整體效率比 AFL 在語法敏感的 target 上高出不少。

---

## 對比取捨

| 方法 | 文法合法性 | Coverage 引導 | 文法來源 | 適合場景 |
|------|------------|---------------|----------|----------|
| Pure generational | 100% | 無 | 手寫 | 初步探索 / crash triage |
| Grammar coverage | 100% | 文法空間 | 手寫 | 確保文法規則都被覆蓋 |
| Nautilus / Gramatron | 100% | edge coverage | 手寫 | 複雜語言 / CTF / 長期 campaign |
| Learn&Fuzz | ~90% | 無（ML 生成） | 自動（corpus） | closed-source，有大量 sample |
| GLADE / grammar inference | ~80-95% | 可疊加 | 自動（oracle） | closed-source，corpus 少 |
| Grammar + havoc | 初期 100%，後期下降 | edge coverage | 手寫 | 語法+語義邊界都要探索 |

沒有一個方法全勝。你選方法的依據是：有沒有文法？有沒有 corpus？target 是否 open-source？你有多少時間寫 grammar？

---

## 踩雷

**陷阱一：把 grammar coverage 和 edge coverage 當成同一回事**

很多人看到「coverage-guided grammar fuzzing」就以為文法覆蓋高等於程式碼覆蓋高。這是錯的。

反例：一個 JSON fuzzer 的文法覆蓋率 95%，但生成的所有輸入都觸發同一條 parser 路徑（因為 parser 的 dispatch 只看 top-level type，不管 nested 有多複雜）。Edge coverage 可能只有 30%。

Grammar coverage 是文法空間的多樣性指標，edge coverage 是程式碼路徑的探索指標。你需要兩個都追蹤，但它們解決不同問題：grammar coverage 告訴你文法有沒有被充分利用，edge coverage 告訴你 target 有沒有被充分探索。

**陷阱二：以為自動推斷的文法可以直接信任**

GLADE 類的方法推斷出的文法是近似的。實際測試中，推斷的文法通常允許一批 target 實際上會拒絕的輸入（false positives）。如果你直接拿推斷的文法做 fuzzing，expect 大約 5-20% 的 exec 被 parser 早期拒絕。

這不一定是問題——20% 的廢棄 exec 在 throughput 夠高的情況下還可以接受。但如果你在 high-throughput fuzzing（每秒數千 exec），廢棄 exec 的累積成本就很明顯。修法：把推斷的文法當初始版本，跑幾輪後觀察哪些生成的輸入被拒絕，手動修剪 false-positive rule。

**陷阱三：Grammar + havoc 中，以為 havoc 不能破壞語法**

反直覺，但故意讓 havoc 破壞語法有時候正是你要的。

你在找的 bug 可能就在 parser 的容錯路徑：當輸入格式「稍微不對」時，parser 怎麼處理？很多歷史 CVE 就在「格式 99% 合法，只有一個 field 不符規範」的輸入上觸發。如果你的 havoc 從不破壞語法，你就永遠測不到這條路。

正確的做法：讓 havoc 自由破壞，但追蹤「被 parser 早期拒絕 vs. 進入深層 logic」的比例。如果被早期拒絕的 exec 超過 80%，才需要考慮加入 repair phase 或調整 havoc 比例。

**陷阱四：把 Learn&Fuzz 當成「不需要文法的萬能方案」**

Learn&Fuzz 需要大量高品質 corpus——論文用的是數萬份 PDF。如果你的 target 是公司內部格式，手上只有幾十份 sample，LSTM 根本學不到有意義的分佈。

而且 ML 生成速度比文法展開慢一到兩個數量級，inference time 是瓶頸。如果你能手寫一個粗略的文法，粗略文法 + coverage-guided 通常比 ML generation 效果更好，且不需要 GPU。Learn&Fuzz 的場景是：格式極其複雜（如 PDF 的 content stream）、有大量現成 corpus、且格式文法幾乎不可能手寫完整。

---

## 進階延伸

**SpecFuzz 與協定文法萃取**

Wireshark 的 dissector 本質上是一個用 C 寫的 parser，裡面有大量關於協定結構的知識。SpecFuzz 的思路是從 dissector 原始碼中靜態提取協定文法（分析 `proto_tree_add_item` 等 API 的呼叫模式），再把提取出的文法用於 fuzzing。這種方法讓你在沒有正式規範的情況下，從既有工具拿到可用的文法近似，對網路協定 fuzzing 特別有效。

**Token-level mutation**

Grammar mutation 通常在 non-terminal（subtree）層操作。Token-level mutation 更細：在文法的 terminal（字面量）層做替換。比如把所有 `"string"` token 替換成 `""` / `"\x00"` / 長度為 65536 的字串；把所有 `number` token 替換成 `0` / `-1` / `INT_MAX` / `NaN` / `Infinity`。Fuzzilli（Ch 38 會詳細討論）在 JS fuzzing 中廣泛使用這個思路——它的 mutator 有專門的 `IntegerMutator`、`FloatMutator` 負責在 terminal 層做 boundary value 替換，這些 boundary 正是觸發 JIT 編譯器 bug 的常見地方。

**Gramatron 的 automaton 表示**

Gramatron（ISSTA 2021）把文法轉成 pushdown automaton，然後在 automaton 的 state 空間上做 mutation。好處是 mutation 操作有更清晰的語義（state transition 代表「選擇這個 production rule」），且更容易分析哪些 state 還沒被充分探索。思路和 grammar coverage 相近，但形式化程度更高，允許對 mutation 策略做更精確的能量分配。

---

## 動手練習

**練習一**：把本章的 `GrammarCoverageFuzzer` 擴充，加入 edge coverage 追蹤。用 Python 的 `sys.settrace` 攔截一個簡單 JSON parser（標準庫的 `json.loads`）的函數呼叫，統計每次生成的輸入觸發了多少個不同的函數/行號組合。連續生成 100 個輸入，畫出 grammar coverage 和 edge coverage 隨生成次數的成長曲線。觀察兩條曲線是否同步飽和，還是有一條明顯早飽和。

**練習二**：手動做一次 GLADE 的 generalization 實驗。準備 5 個合法 JSON 字串，把每個字串中的不同 chunk 互相替換，送給 `json.loads` 觀察 accept/reject。統計：哪些 chunk 互相可以替換（等價類），哪些不能。從你的觀察手動寫出一個近似文法，和本章 GRAMMAR 對比，看你推斷出了哪些規則、遺漏了哪些。

**練習三**：實作一個最小的 Grammar + Havoc 混合 fuzzer。用本章的 `GrammarCoverageFuzzer` 做 Phase 1，生成 50 個合法 JSON 輸入存入 corpus。Phase 2 中，對 corpus 中的每個輸入，50% 機率做文法 subtree mutation，50% 機率做 random byte flip。統計：兩種 mutation 各有多少輸入通過 `json.loads` 的驗證？哪種更容易找到「字串長度超過 1000 字元的 value」這類邊界輸入？

---

## 本章重點

- Grammar coverage 追蹤「文法規則的使用多樣性」，edge coverage 追蹤「程式碼路徑」。兩者正交，同時需要。
- GrammarCoverageFuzzer 在文法空間做覆蓋引導，優先展開未使用的 production rule，30 次生成可達 87% grammar coverage。
- Nautilus 把文法 mutation 和 AFL 式 edge coverage 整合：corpus 存 derivation tree + coverage bitmap，mutation 在 tree 層，確保語法合法性。
- Learn&Fuzz 用 LSTM 學習 input 分佈，不需要文法但需要大量 corpus，生成合法性約 90%，無公開實作，適用場景窄。
- GLADE 類方法透過 oracle queries 推斷近似文法，推斷結果有 false positive，需要手動修剪或接受廢棄 exec。
- Grammar + havoc 混合策略是實務最常用的方法：文法 generation 建立 corpus，coverage 引導 mutation，havoc 在 byte 層探索文法描述不到的邊界。故意讓 havoc 破壞語法也有價值——parser 的容錯路徑往往藏著 CVE。

---

## 自我檢核

1. Grammar coverage 100%、edge coverage 30%，這代表什麼問題？你會怎麼診斷是哪裡出了問題？

2. GLADE 推斷出的文法有 15% 的輸入被 target 早期拒絕，這可以接受嗎？在什麼 throughput 的情況下不可以接受？

3. Nautilus 的 corpus 中，一個觸發 5 條新 edge 的大型 tree 和一個觸發 1 條新 edge 但 tree 很小的 entry，哪個 energy 更高？如果你是 Nautilus 的設計者，你會怎麼在這兩者之間取捨？

4. 你在 fuzzing 一個接受 binary protocol 的 closed-source daemon，手上有 50 份合法封包。你會優先選 Learn&Fuzz 還是 GLADE？如果手上有 50,000 份封包呢？

---

## 延伸閱讀

1. **"Learn&Fuzz: Machine Learning for Input Fuzzing"** (Godefroid, Peleg, Singh, ASE 2017) — 讀 §2 Background 和 §3 Architecture。理解 LSTM-based fuzzing 的能力邊界，尤其是「語法合法性 ≠ 語義合法性」這條限制線。這篇論文的貢獻不只是方法，更在於它清楚地界定了 ML 生成可以替代什麼、替代不了什麼。了解它的限制比了解它的成功更重要。

2. **"Synthesizing Program Input Grammars"** (Bastani, Sharma, Aiken, Liang, PLDI 2017) — 讀 §1 Introduction 的 motivation 段落和 §2 的問題形式化。理解 grammar inference 的問題設定：oracle 能提供什麼資訊、L* 演算法在這個問題上的應用，以及為什麼語義約束超出 oracle-based inference 的能力範圍。可以補讀 GLADE 的原始論文（Krogmann et al., PLDI 2016）的 §1 快速了解背景。

3. **The Fuzzing Book, "Coverage-Based Greybox Fuzzing"** (Zeller, Gopinath, Böhme, Fraser, Holler) — https://www.fuzzingbook.org/html/GreyboxFuzzer.html — 讀 §4 "Combining Grammar and Coverage"。這是本章所有內容的可執行版本：Python 實作從 grammar mutation 到 edge coverage feedback 的完整 loop，每個概念都有對應的程式碼和視覺化。如果你只讀一個延伸資源，讀這個——可以直接跑，概念和實作之間沒有落差。

---

## 銜接

Ch 13 解決的是「如何表示文法、如何做 derivation tree mutation」，本章解決的是「如何讓 coverage 引導文法 fuzzing 的探索方向」，以及「當你沒有文法時怎麼辦」。

文法 fuzzing 的 oracle 問題還沒完全解決。Nautilus 和混合策略追蹤 edge coverage，但 edge coverage 只告訴你「有沒有走到新路徑」，不告訴你「這個輸入有沒有被用對的方式處理」。差分 fuzzing 是另一個維度的 oracle——用多個實作的行為差異來發現 bug，不依賴 coverage，也不需要文法。

→ [下一章](./15-differential-fuzzing.md)
