# Ch 29 — Bit-vector 理論

> 目標：理解 **QF_BV (bit-vector theory)** — 固定寬度整數 + 位元運算 + modular arithmetic，模擬 CPU 指令的語義。BV 是 **hardware verif、binary analysis、compiler 正確性** 的主力。兩條路：bit-blasting 轉 SAT、word-level propagation。現代 solver 都混用。

## BV 的語言

變數：`BitVec n` — 寬度 `n` 的 bit vector，值 `∈ {0, 1, ..., 2^n - 1}`（unsigned 解讀）或 `∈ {-2^(n-1), ..., 2^(n-1) - 1}`（signed）。

運算：

| 類別 | 運算 | 備註 |
|---|---|---|
| Bitwise | `bvand, bvor, bvxor, bvnot` | 逐 bit |
| Shift | `bvshl, bvlshr (logical), bvashr (arith)` | 固定寬度 |
| Arith | `bvadd, bvsub, bvmul, bvudiv, bvsdiv` | **All mod 2^n** |
| Compare | `bvult, bvule, bvslt, bvsle` | Unsigned / signed |
| Extract | `((_ extract i j) x)` | 取 bit j..i |
| Concat | `concat x y` | 連接 |
| Extend | `zero_extend, sign_extend` | 補 bit |
| 等式 | `=` | 全部 bit 相同 |

範例：

```smt2
(set-logic QF_BV)
(declare-const x (_ BitVec 32))
(declare-const y (_ BitVec 32))
(assert (= (bvadd x y) #x00000010))       ; x + y = 16
(assert (bvult x #x00000005))              ; x < 5 unsigned
(check-sat)
```

### Modular arithmetic

**所有運算 mod 2^n**。`(_ BitVec 8)` 上 `0xFF + 0x01 = 0x00`（overflow wraps）。這是 BV 跟 LIA 的根本差異 — LIA 沒 overflow，BV 有。

所以 BV instance 經常捕捉 **整數 overflow bug** — C code 的 `int x; x * 2` 在 `x = 2^30` 時 overflow，SMT 用 BV 抓得到。

## 解法一：Bit-Blasting

**最直接**：每個 `BitVec n` 變成 `n` 個 Boolean 變數、每個 BV 運算變成對應的 **Boolean circuit**。

```
BitVec 32 x   →   32 Boolean vars: x[0], x[1], ..., x[31]

bvadd x y →  full adder circuit:
    x[0], y[0] → s[0], c[1]   (adder)
    x[1], y[1], c[1] → s[1], c[2]
    ...
    結果是 32 個 Boolean vars for sum
```

Add / sub / and / or / xor 都有標準 circuit。Mul 用 shift-and-add (O(n²) gates)。Div 複雜、gates 更多。

### 優點

- **重用 SAT solver 全部基建**：CDCL + preprocess + restart + CDCL 全免費
- **Sound & complete**：SAT solver 給 model、unsat proof
- **簡單實作**

### 缺點

- **大**：32-bit multiply 要 ~1000 gates。100 個 BV 變數的大 formula 輕易到百萬 clause
- **SAT solver 看不到 word-level 結構**：`x + 1 = y + 1` 跟 `x = y` 邏輯等價，但 bit-blast 後兩者完全不同 CNF

### 實務

**Z3 early version** 用 full bit-blasting。**Boolector (2006)** → **cvc5** 的 BV tactic 現在還是 bit-blasting 主力。**工業 hardware verif** 最穩的方法。

## 解法二：Word-Level Propagation

**Bitwuzla** (Niemetz, Preiner 2018) 和 **Bitblasting plus** 類 solver 改走 word-level：

- 保留 BV variable 原貌，不 blast
- Theory solver 做 **interval propagation**：`x ∈ [3, 10]` → `x + 5 ∈ [8, 15]`
- 結合 **SLS (stochastic local search)** 在 BV 上
- Bit-blast 只在 bottom level 做 encoding

### 優點

- **結構保留**：看得到 `x + 1 = y + 1 ⇒ x = y` 這種 invariant
- **某些 instance 碾壓 bit-blast**：尤其包含大量 arithmetic 的

### 缺點

- **演算法複雜**：interval analysis for non-trivial ops (mul, div) 是研究題
- **Completeness 要小心**：combine SLS + interval 不一定 complete

## 跟 CDCL 的整合

Bit-blasting 版本：全部丟 SAT solver，不需要 theory solver interface（只有 pre-processing tactic 把 BV→CNF）。

Word-level 版本：完整 DPLL(T) theory solver interface — assert BV 約束、check interval consistency、propagate、explain。

```cpp
class BVSolver : public TheorySolver {
    IntervalStore intervals;    // x ∈ [lo, hi] per BV var
    Bitblaster bb;              // 部分 bit-blast 需要時
public:
    void assert_lit(BVAtom a, bool v) override {
        intervals.narrow(a, v);
    }
    Result check() override {
        if (intervals.any_empty()) return UNSAT;
        // 某些 atom interval-level 不能決定 → bit-blast 部分 lazy
        if (deep_constraint_detected()) return bb.check();
        return SAT;
    }
};
```

## 範例：bvadd 的 bit-blast

`(bvadd x y) = z` where `x, y, z: BitVec 4`. Ripple-carry adder:

```
s[0] = x[0] XOR y[0]
c[1] = x[0] AND y[0]

s[1] = x[1] XOR y[1] XOR c[1]
c[2] = majority(x[1], y[1], c[1])

s[2] = x[2] XOR y[2] XOR c[2]
c[3] = majority(...)

s[3] = x[3] XOR y[3] XOR c[3]
// c[4] discarded (overflow)

z = s
```

每個 XOR → 4 CNF clauses、每個 AND → 3 clauses、majority → 6 clauses。4-bit adder ≈ 50 clauses、4 碳variable `s[i]`、4 carry `c[i]`。**32-bit 400 clauses、64-bit 800 clauses**。

Mul 更大 — `n × n` 的 array of partial sum + adders、`O(n²)` clauses。

## Signed vs Unsigned

BV 本體是 bit pattern，沒有「signed」或「unsigned」。**同一個 4-bit 0b1111**：

- Unsigned: 15
- Signed (two's complement): -1

運算：

- `bvadd, bvsub, bvmul`: 同一個 bit 運算，語義都適用
- `bvudiv, bvurem`: unsigned division
- `bvsdiv, bvsrem, bvsmod`: signed division, **實作要特別處理負數**
- `bvult` vs `bvslt`: 比較時差很多

Bit-blast 裡 `bvsdiv` 展成「先取正 abs、再 div、最後處理 sign」— 比 `bvudiv` 複雜 10×。

## Extract / Concat 的便利

```
((_ extract 15 0) x)   ← x 的 low 16 bits
(concat x y)           ← x 接在 y 的高位
```

這些讓 BV formula 能捕捉 **bit field 操作**（network packet parse、binary exploit 分析）。現代 solver 對 extract / concat 有 dedicated rewrite 加速。

## BV Preprocessing

BV 有 **專門** preprocessing rule：

- `x + 0 → x`
- `x * 0 → 0`
- `(x & y) & z → x & (y & z)` (associate for further simplification)
- `(concat (_ extract 15 8 x) (_ extract 7 0 x)) = x` (全寬恢復)
- Constant folding: `5 + 3 → 8`

這些是 **word-level rewrite**，在 bit-blast 前做、能縮 CNF 很多。

## 動手練習

1. **Bit-blast 練習**：手動把 `(_ BitVec 2) x, y` 的 `bvadd x y = 3` bit-blast 成 CNF。你會得到 ~10 clause。
2. **Overflow bug**：寫 SMT instance 檢查 `x + 1 > x`（對 BV 32 bit）。答案：不是總對！ `x = 2^31 - 1` 時 overflow。 tool 應該給 `unsat` for `∀x. x + 1 > x`。
3. **BV vs LIA 的差距**：同一個問題分別寫 LIA 和 QF_BV，跑 Z3 比時間。某些 instance BV 快、某些 LIA 快。
4. **Extract/concat 等式**：`concat(extract x[15:8], extract x[7:0]) = x` 是 tautology。驗證。

## 常見誤解

- **「BV 跟 int 一樣」** — 不一樣。BV 有固定寬度 + overflow、int 沒有。
- **「Bit-blasting 永遠 sound and complete」** — 是，但 size blowup 可能讓 SAT solver 卡死。
- **「所有 BV 運算都 O(n)」** — Add O(n)、mul O(n²)、div O(n²) gate count。
- **「32-bit BV 就要 bit-blast 32 個 Boolean」** — 是，而且每個運算產幾百個 intermediate Boolean var。

## 自我檢核

- [ ] 懂 BV 的 modular arithmetic（mod 2^n）
- [ ] 會 bit-blast simple 運算（and, or, not, add）
- [ ] 知道 signed vs unsigned 運算的區別
- [ ] 懂 bit-blasting vs word-level propagation 兩種路線
- [ ] 寫得出 SMT-LIB QF_BV syntax
- [ ] 知道 Boolector / Bitwuzla / cvc5 的 BV tactic 差異

下一章是另一個工業級 theory — **Array**。陣列在程式驗證無所不在（memory 模型、data structure），array theory 的 axioms 看似簡單卻有很多 subtleties。

→ [Ch 30 — Array 理論](./30-arrays.md)
