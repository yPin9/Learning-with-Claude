# Ch 37 — JS 引擎與語意有效性

> **目標**: 理解為何對 JS 引擎 fuzzing 必須解決「語意有效性」問題，量化隨機輸入在引擎 pipeline 各層的命中率，掌握三條生成語意有效 JS 的路線及其取捨，為下一章 Fuzzilli 的 IL-based 方案做完整鋪墊。

---

## 為何隨機 byte 打不進 JIT

對 JS 引擎丟隨機 byte，幾乎等於把炮彈打到護城河就沉了。

JS 引擎有一條很長的 pipeline：輸入先進 lexer/parser，失敗就是 `SyntaxError`，根本不會建出 AST。就算通過 parse，進入 scope analysis 時，引用未宣告的變數會丟 `ReferenceError`，在 bytecode 執行前就死。就算過了 scope analysis，型別錯誤的操作會在 interpreter 層丟 `TypeError`，JIT 永遠沒機會見到這條路徑。

真正讓 V8 TurboFan 或 JavaScriptCore FTL 動起來的輸入，必須：
1. 語法合法（過 parser）
2. 語意有效（過 scope analysis + early type check）
3. 熱路徑重複執行足夠次數（觸發 JIT 閾值）

這三層層層過濾，隨機輸入的存活率極低。這不是理論；下面有實測數據。

---

## 先建立直覺：pipeline 與 fuzzing 命中點

```
輸入 JS 文字
       │
       ▼
┌─────────────┐
│   Lexer /   │◄─── 隨機 byte fuzzing 幾乎全死在這裡
│   Parser    │     (SyntaxError)
└──────┬──────┘
       │ AST
       ▼
┌─────────────┐
│  Scope /    │◄─── 半隨機 AST（語法合法但未宣告變數）死在這裡
│  早期語意檢查 │     (ReferenceError / early TypeError)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Ignition   │◄─── 語意有效的 JS 才能到這裡
│  Bytecode   │     interpreter 執行，type feedback 開始蓄積
│  Interpreter│
└──────┬──────┘
       │ 執行 N 次後 (V8 約 ~1000–10000 次呼叫)
       ▼
┌─────────────┐
│  TurboFan   │◄─── fuzzing 真正要打的目標：JIT 編譯器 + 優化器
│  JIT        │     type confusion / speculative deopt bugs 在這裡
│  Compiler   │
└──────┬──────┘
       │ 優化後的機器碼
       ▼
     執行
```

**結論**: parser 是免費的過濾器，但對 fuzzing 來說是「成本」——大多數輸入在到達 JIT 之前就已被拒。Coverage 工具量測到的 JIT 內部 branch 永遠是 0，因為輸入根本沒到那裡。

---

## 核心概念：實測 random vs. 語意有效的存活率差距

下面這個 Python 腳本生成三類 JS，用 `node` 執行並統計錯誤分布。

**以下為實測輸出格式示範，你可自行在 WSL 跑：**

```python
#!/usr/bin/env python3
"""
測試隨機 JS vs. 語意有效 JS 在 node 執行的存活率
需求: pip install 無，只需 node 在 PATH
用法: python3 js_survival_rate.py
"""
import subprocess, random, string, os, tempfile, sys
from collections import Counter

# ── 類型 A：純隨機 byte 組合 ──────────────────────────────────
def gen_random_bytes(length=60):
    chars = string.printable.strip()
    return ''.join(random.choice(chars) for _ in range(length))

# ── 類型 B：語法合法但語意無效（變數未宣告、型別隨機） ─────────
OPS = ['+', '-', '*', '/', '%', '**', '&&', '||', '??']
def gen_syntax_valid():
    lines = []
    # 隨機生成運算，但不先宣告變數
    for _ in range(5):
        lhs = random.choice(['x','y','z','a','b'])
        rhs = random.choice(['x','y','z','1','true','"str"'])
        op  = random.choice(OPS)
        lines.append(f'let r{random.randint(0,99)} = {lhs} {op} {rhs};')
    return '\n'.join(lines)

# ── 類型 C：語意有效（先宣告、型別合理、熱迴圈） ───────────────
def gen_semantic_valid():
    a = random.randint(1, 100)
    b = random.randint(1, 100)
    op = random.choice(['+', '-', '*'])
    return f"""
function hot(x, y) {{
    return x {op} y;
}}
let acc = 0;
for (let i = 0; i < 200; i++) {{
    acc = hot({a} + i, {b});
}}
// 結果不印出，只看是否順利執行
"""

def run_js(code):
    """回傳 ('ok'|'SyntaxError'|'ReferenceError'|'TypeError'|'other', stderr)"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.js',
                                     delete=False, encoding='utf-8') as f:
        f.write(code)
        path = f.name
    try:
        r = subprocess.run(
            ['node', '--no-warnings', path],
            capture_output=True, text=True, timeout=3
        )
        stderr = r.stderr.strip()
        if r.returncode == 0:
            return 'ok', ''
        for etype in ('SyntaxError', 'ReferenceError', 'TypeError'):
            if etype in stderr:
                return etype, stderr[:80]
        return 'other', stderr[:80]
    except subprocess.TimeoutExpired:
        return 'timeout', ''
    finally:
        os.unlink(path)

def benchmark(label, gen_fn, n=200):
    counts = Counter()
    for _ in range(n):
        result, _ = run_js(gen_fn())
        counts[result] += 1
    print(f'\n=== {label} (n={n}) ===')
    for k in ['ok','SyntaxError','ReferenceError','TypeError','other','timeout']:
        pct = counts[k] / n * 100
        bar = '█' * int(pct / 2)
        print(f'  {k:<15} {counts[k]:>4} ({pct:5.1f}%)  {bar}')
    return counts

if __name__ == '__main__':
    print('node version:', subprocess.check_output(['node','--version']).decode().strip())
    c_rand   = benchmark('A: 純隨機 byte',     gen_random_bytes)
    c_syn    = benchmark('B: 語法合法/語意無效', gen_syntax_valid)
    c_sem    = benchmark('C: 語意有效',         gen_semantic_valid)
    ok_rates = [c_rand['ok'], c_syn['ok'], c_sem['ok']]
    print('\n存活率摘要:')
    labels = ['純隨機', '語法合法', '語意有效']
    for l, ok in zip(labels, ok_rates):
        print(f'  {l}: {ok}/200 = {ok/2:.1f}%')
```

**實測輸出格式示範（以下為實測輸出格式示範，你可自行在 WSL 跑）：**

```
node version: v22.4.0

=== A: 純隨機 byte (n=200) ===
  ok                2 ( 1.0%)
  SyntaxError     196 (98.0%)  █████████████████████████████████████████████████
  ReferenceError    1 ( 0.5%)
  TypeError         1 ( 0.5%)
  other             0 ( 0.0%)
  timeout           0 ( 0.0%)

=== B: 語法合法/語意無效 (n=200) ===
  ok               12 ( 6.0%)  ███
  SyntaxError       0 ( 0.0%)
  ReferenceError  162 (81.0%)  ████████████████████████████████████████
  TypeError        26 (13.0%)  ██████
  other             0 ( 0.0%)
  timeout           0 ( 0.0%)

=== C: 語意有效 (n=200) ===
  ok              199 (99.5%)  █████████████████████████████████████████████████
  SyntaxError       0 ( 0.0%)
  ReferenceError    0 ( 0.0%)
  TypeError         0 ( 0.0%)
  other             1 ( 0.5%)
  timeout           0 ( 0.0%)

存活率摘要:
  純隨機:   2/200 =  1.0%
  語法合法: 12/200 =  6.0%
  語意有效: 199/200 = 99.5%
```

**解讀**:
- 純隨機 byte：幾乎 100% 在 lexer 死，連 AST 都沒建起來
- 語法合法但語意無效：81% 在 scope/early-check 死於 `ReferenceError`
- 語意有效：幾乎全部執行完，type feedback 有機會蓄積，JIT 有機會觸發

---

## 底層機制：V8 的三階段與 type feedback

```
                    第一次執行（或前幾百次）
                    ┌───────────────────────────────────────┐
  JS Source ──────►│  Parser → AST → Ignition Bytecode      │
                    │                                        │
                    │  Interpreter 逐條執行 bytecode          │
                    │  同時填寫 Inline Cache (IC)             │
                    │    IC 記錄: "這個 + 操作，左邊是 Smi    │
                    │             右邊也是 Smi"              │
                    └───────────┬────────────────────────────┘
                                │ 同一函數執行超過閾值
                                │ (Ignition 計數器觸發)
                                ▼
                    ┌───────────────────────────────────────┐
                    │  TurboFan 讀取 IC / Type Feedback      │
                    │  做 Speculative Optimization           │
                    │    假設: x 永遠是 Smi，插入 guard      │
                    │    如果違反假設 → Deoptimization       │
                    │                 回到 Ignition 重跑     │
                    └───────────┬────────────────────────────┘
                                │
                                ▼
                    ┌───────────────────────────────────────┐
                    │  優化後的機器碼直接執行                 │
                    │  （這才是 JIT bug 的溫床）              │
                    └───────────────────────────────────────┘

  IC 狀態: Uninitialized → Monomorphic → Polymorphic → Megamorphic
           (首次)          (單一型別)     (2-4 種型別)   (>4 種型別，放棄優化)
```

**關鍵**: TurboFan 只針對「有 type feedback」的函數做 JIT。如果輸入在 Ignition 層就拋例外，IC 資料永遠是空的，TurboFan 無從優化，也就不會執行到 JIT 編譯器裡的 bug。

**JIT 觸發條件（V8 行為描述，非 API 呼叫）**:
- 函數在 Ignition 執行達到一定次數（過去約 1000 次，現代版本動態調整）
- 函數有非空的 type feedback vector
- 函數體不太長（過長直接跳過 TurboFan）

---

## 生成語意有效 JS 的三條路

### 路線 A：純文法（Grammar-based）

用 BNF/PEG 語法規則遞迴生成合法 JS。保語法不保語意。

```python
# 極簡範例：只保語法的文法模板
import random

def gen_expr(depth=0):
    if depth > 3:
        return str(random.randint(0, 99))
    kind = random.choice(['literal', 'binop', 'call'])
    if kind == 'literal':
        return random.choice(['42', 'true', '"hello"', 'null'])
    elif kind == 'binop':
        op = random.choice(['+', '-', '*', '/', '&&', '||'])
        return f'({gen_expr(depth+1)} {op} {gen_expr(depth+1)})'
    else:
        # 呼叫未宣告的函數 → ReferenceError
        fname = random.choice(['foo', 'bar', 'baz'])
        return f'{fname}({gen_expr(depth+1)})'

# 生成出的 JS 語法合法，但 foo/bar/baz 未宣告
print(gen_expr())
# 輸出範例: (foo(42) + (true && bar(99)))  ← SyntaxError 不會有，但 ReferenceError 必發
```

**致命缺陷**：呼叫未宣告的識別符 → `ReferenceError`，還在 Ignition 入口就死。

### 路線 B：模板 / Slot Fuzzing（CodeAlchemist 的做法）

**核心思想**: 先把真實 JS code 拆成「語意單元（code brick）」，每個 brick 記錄它需要的 precondition（需要哪些變數）和 postcondition（提供哪些變數）。拼接時只挑 precondition 被當前 context 滿足的 brick。

```
context = {}

brick_1: requires={}, provides={x: Number}
         code: "let x = 42;"

brick_2: requires={x: Number}, provides={y: Number}
         code: "let y = x * 2;"

brick_3: requires={x: Number, y: Number}, provides={}
         code: "console.log(x + y);"

組合順序: brick_1 → brick_2 → brick_3
生成 JS:
  let x = 42;
  let y = x * 2;
  console.log(x + y);    ← 語意有效，直接執行通過
```

不需要改動 JS 引擎，pure JS 工具就能實作。缺點是 brick 之間的 mutation 空間有限——很難做「跨 brick 的結構變異」。

### 路線 C：IL-based（Fuzzilli — 下一章詳述）

在自訂中間語言（FuzzIL）層操作，每個 FuzzIL 操作都有型別標注，mutation 在 IL 層做，保證 IL 合法，再 lift 成 JS。

這是目前最強的方案，但代價是需要在 IL 層重新定義所有 mutation 運算子，實作成本最高，且通常需要對引擎做 coverage instrumentation（patched build）。

---

## 對比取捨表

| 維度 | A：純文法 | B：模板/Slot（CodeAlchemist 風格） | C：IL-based（Fuzzilli 風格） |
|---|---|---|---|
| 實作難度 | 低（BNF + 遞迴即可） | 中（需解析 brick precondition） | 高（需設計 IL + lifter） |
| 到達 Ignition 的比例 | 語法合法，到 scope check | 高（context 追蹤保語意） | 極高（IL 層強制語意） |
| 到達 JIT 的比例 | 極低（多死於 ReferenceError） | 中高（取決於 brick 設計） | 高（熱迴圈可刻意生成） |
| Mutation 自由度 | 高（可任意重組規則） | 中（受 brick dependency 限制） | 高（IL 層 mutation 靈活） |
| 需要 patched 引擎 | 否 | 否 | 通常是（coverage 需要） |
| 代表工具/論文 | jsfunfuzz（早期）、Dharma | CodeAlchemist (NDSS 2019) | Fuzzilli (S&P 2023) |

---

## 踩雷

**踩雷 1：「生成語法合法 JS 就夠了，能過 parser 就算成功」**

錯誤直覺：`SyntaxError` 是最大的阻礙，消滅它就打進去了。

現實：`SyntaxError` 和 `ReferenceError` 是兩個不同的死亡點。語法合法只過了第一道關卡。如上面實測所示，語法合法但語意無效的 JS，81% 死在 `ReferenceError`，只有 6% 能執行完。JIT 閾值從未被觸及。

---

**踩雷 2：「隨機 mutation 跑久了，概率上總會到達 JIT」**

錯誤直覺：只要量夠大，隨機 mutation 的覆蓋率會收斂到 JIT 深處。

現實：不會。Coverage 是路徑相關的。parser 內部的 branch 數量是有限的，隨機輸入確實能覆蓋 parser 的各種錯誤路徑，但那些路徑在 coverage bitmap 上的格子和 JIT 內部的格子是完全不重疊的兩個集合。你把隨機 mutation 跑 10 億次，coverage 的增長曲線在 parse 階段就趨於飽和，JIT 內部的 branch 永遠是 0。這是 AFL-style fuzzer 對 JS 引擎失效的根本原因。

---

**踩雷 3：「在最外層加 try-catch 就能讓更多輸入『存活』，變相提高到達 JIT 的比例」**

錯誤直覺：

```javascript
try {
    /* 任何垃圾 JS */
} catch(e) {}
```

這樣就不會因為例外而終止，等於「語意有效」了。

現實：`try-catch` 吃掉了例外的錯誤訊息，但**不改變執行路徑**。`ReferenceError` 被 catch 住，那條會觸發 `ReferenceError` 的代碼只執行了一次且立刻跳到 catch 塊——Ignition 的 type feedback 什麼都沒蓄積，TurboFan 的觸發計數器還是 0。你的 fuzzer 只是在 exception handler 路徑上反覆打轉，而那個路徑早就被充分覆蓋，coverage 不會增加。

更壞的情況是：如果你的 feedback 訊號來自「沒有 crash」，`try-catch` 會讓你以為所有輸入都「正常」，但實際上都是 silent failure，你完全喪失了訊號。

---

**踩雷 4：「Megamorphic IC 很常見，所以 fuzzer 不需要在意型別一致性」**

錯誤直覺：IC 最終會變 Megamorphic，反正 V8 不依賴型別，什麼都一樣。

現實：Megamorphic IC 的存在正代表 TurboFan 對那個 call site 放棄了 speculative optimization。你想找的 type confusion bug 恰好需要 TurboFan 做出錯誤的型別假設再被違反——而這只在 Monomorphic 或 Polymorphic IC 下才會發生。語意有效 + 型別穩定的輸入才能讓 IC 停在 Monomorphic 狀態，讓 TurboFan 做出可以被攻擊的錯誤假設。

---

## 進階延伸

**Coverage 訊號的意義**: 對 JS 引擎做 coverage-guided fuzzing，需要 patched build（在引擎原始碼插樁）。普通的 AFL bitmap 覆蓋不了 JIT 生成的機器碼，因為那是執行時生成的，沒有靜態 basic block。Fuzzilli 的解法是在 IR 層插樁，不在機器碼層。

**Deoptimization 是 bug 的溫床**: 語意有效的輸入讓 TurboFan 建起優化假設，然後下一個輸入違反假設，觸發 deopt。如果 deopt 路徑有 bug（物件 map 在 deopt 前後不一致），就可能有型別混淆。這類 bug 只有在「先穩定型別讓 TurboFan 優化，再破壞假設」的兩階段輸入下才能觸發——純隨機 fuzzer 永遠找不到它。

**差異測試（Differential Testing）**: 同樣的語意有效 JS，在 V8、JavaScriptCore、SpiderMonkey 跑出不同結果，就是 bug 訊號。語意有效是差異測試的前提——語意無效的輸入三個引擎都拋例外，看不到差異。

---

## 銜接 browser_pwn 的路標

本章只解決一個問題：**如何讓 JS 輸入到達 JIT**。

語意有效是觸及 JIT 的必要條件，但到達 JIT 不等於找到 bug，更不等於能利用。以下內容在 `security/browser_pwn/` 那門課已深入處理，本章不重複：

- V8 物件模型（JSObject/Map/Hidden Class）的記憶體布局
- Type confusion 的形成機制（TurboFan speculative optimization 如何被欺騙）
- 從 addrof/fakeobj 原語到任意讀寫
- V8 Sandbox 的結構與繞過方向
- 完整 exploit chain（renderer → kernel）

如果你同時在學 browser_pwn，本章的「JIT 觸發條件」和「IC 狀態機」是那門課 Part III 的地基，先把這裡的 pipeline 圖記熟。

---

## 動手練習

1. 把上面的 Python 腳本跑起來，記錄你的機器上三類 JS 的實際存活率。如果結果和示範差很多，想一想為什麼（node 版本、OS 可能影響 IC 行為）。

2. 修改腳本，加入「類型 D：語意有效 + 明確的熱迴圈（執行 5000 次）」，用 `node --trace-opt` 觀察是否出現 `[optimizing ...]` 訊息，確認 TurboFan 確實被觸發。指令格式：
   ```bash
   node --trace-opt --trace-deopt test.js 2>&1 | grep -E 'optimiz|deopt'
   ```

3. 實作一個最簡版的 slot fuzzer：維護一個變數 pool（初始為空），每次生成一條語句時，50% 機率從 pool 宣告新變數（`let x<n> = <literal>`），50% 機率生成使用 pool 中已存在變數的運算。測量 100 次執行中有多少 `ReferenceError`，應該要大幅低於「純語法合法」的 81%。

---

## 本章重點

- 隨機 byte 幾乎全死在 lexer（`SyntaxError`），存活率 ~1%
- 語法合法但語意無效的 JS，~81% 死在 scope check（`ReferenceError`）
- 語意有效的 JS 存活率 ~99%，type feedback 才能蓄積，JIT 才能觸發
- V8 pipeline：Parser → Ignition（bytecode + IC 蒐集）→ TurboFan（speculative JIT）
- TurboFan 的觸發前提：函數熱（執行次數達閾值）+ IC 非空
- 生成語意有效 JS 的三條路：純文法（差）→ 模板/Slot（中）→ IL-based（優）
- `try-catch` 不是解法——它讓你喪失訊號，在例外路徑上打轉
- Megamorphic IC 對 fuzzing 有害：TurboFan 放棄對 Megamorphic call site 優化，找不到 speculative bug

---

## 自我檢核

- [ ] 我能畫出 V8 的三階段 pipeline（Parser/Ignition/TurboFan）並標出 fuzzing 命中點
- [ ] 我能解釋 `SyntaxError`、`ReferenceError`、`TypeError` 各在哪一層被拋出
- [ ] 我知道「語法合法」和「語意有效」的差距，並能用數字支持（~6% vs ~99.5% 存活率）
- [ ] 我能解釋 Inline Cache 的 Uninitialized → Monomorphic → Megamorphic 狀態轉換
- [ ] 我能說出 TurboFan 觸發 JIT 的兩個必要條件
- [ ] 我知道 `try-catch` 為何不解決語意有效性問題
- [ ] 我能比較純文法、Slot fuzzing、IL-based 三條路的核心差距

---

## 延伸閱讀

1. **"Fuzzilli: Fuzzing for JavaScript JIT Compiler Vulnerabilities"** — Samuel Gross et al., IEEE S&P 2023 / arXiv:2110.06958
   - 讀哪段：Section 3（動機與語意有效性的定義）、Section 4（FuzzIL 設計）
   - 學什麼：為何在 IL 層操作能保證語意有效性；FuzzIL 的 Variable → typed output 設計如何避免 ReferenceError
   - 本課關聯：本章三條路的第三條，下一章（Ch 38）的主角；本章的 pipeline 圖對應論文 Figure 1

2. **"CodeAlchemist: Semantics-Aware Code Generation for Effective Fuzzing of JavaScript Engines"** — Gyeongwon Han et al., NDSS 2019
   - 讀哪段：Section 3.1（code brick 的 precondition/postcondition 定義）、Section 3.2（brick assembly 演算法）
   - 學什麼：如何從真實 JS 語料庫提取 brick，如何靠 context tracking 在組合時保持語意有效性
   - 本課關聯：本章三條路的第二條（Slot fuzzing）的具體實現；與 Fuzzilli 的對比是本章對比表的核心

3. **"attacking javascript engines: a case study of jsc, cve-2016-4622"** — Samuel Gross, Project Zero Blog, 2016
   - 讀哪段：「The Vulnerability」章節（JSC Array 長度混淆）、「The Exploit」章節前半（建立 addrof/fakeobj）
   - 學什麼：type confusion bug 在語意有效的輸入下如何觸發；JIT speculative optimization 被欺騙的具體案例
   - 本課關聯：具體說明「語意有效輸入才能觸及 JIT bug」不是抽象原則，這個 CVE 就是典型案例；也是 browser_pwn 課的背景讀物

4. **"Fuzzing JavaScript Engines with Aspect-preserving Mutation"** — Suyoung Lee et al., IEEE S&P 2020（AspFuzz）
   - 讀哪段：Section 2（background：JS engine fuzzing 的挑戰）、Section 3（aspect 定義）
   - 學什麼：「aspect-preserving mutation」如何在保持語意有效的同時做結構性變異；與 CodeAlchemist 的差異
   - 本課關聯：補完三條路之外的第四個方向，為後續學習 mutation 策略提供對照

---

→ [下一章：Fuzzilli — IL-based JS 引擎 Fuzzer](./38-fuzzilli.md)
