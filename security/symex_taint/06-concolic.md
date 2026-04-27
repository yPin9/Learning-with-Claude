# Ch 6 — Concolic execution：DART 與 CUTE 的真實思路

> 目標：把 concolic 這個字拆透。它不是 "concrete + symbolic" 的音譯縮寫（雖然確實是），而是一個具體的 algorithmic pattern。講完你要能寫出 DART 的 pseudocode。

## 為什麼要有 concolic

Pure symex 的兩個痛點：

1. **environment**：外部 call（網路、檔案、syscall）難 model。symbolic 進去，symbolic 出來，formula 爆炸
2. **path fork 成本高**：每條 branch 兩邊各開一條 state，worklist 爆長

Concolic 的 hack：
- **一次只跑一條 concrete path**，不 fork
- symbolic 只是陪跑，**記下 PC**
- external call 就用 concrete 結果往下走，不 model
- 跑完一條 path，對 PC 做 **negation**，解出新 input，下次用新 input 重跑

核心 insight：**你真的走一次、就真的知道那條 path 存在**。加上 SMT，就知道怎麼修改 input 去走新 path。

## DART 演算法（Godefroid, PLDI 2005）

最初版本非常乾淨：

```
Input:  program P, entry input I₀
Init:   input_queue = {I₀}
        seen_paths  = {}

while input_queue not empty:
    I = input_queue.pop()
    if I 已經走過: continue
    
    (path, PC) = run_concolic(P, I)
    seen_paths.add(path)
    
    # 把 PC 的 clause 逐個 negate，產生新 input
    for i in range(len(PC)):
        partial_PC = PC[0..i-1] ∧ ¬PC[i]
        if SMT.solve(partial_PC) = sat, model m:
            new_input = m
            input_queue.push(new_input)
```

關鍵的 `run_concolic` 做什麼：

```
# P 被 instrumented：每條 instruction 同時更新 concrete state 與 symbolic state
def run_concolic(P, I):
    concrete_state = init(I)
    symbolic_state = init(I as symbolic)
    PC = []
    while not done:
        inst = P.next()
        if is_branch(inst):
            cond_c = evaluate_concrete(inst.cond, concrete_state)
            cond_s = evaluate_symbolic(inst.cond, symbolic_state)
            
            if cond_c:
                PC.append(cond_s)          # 正向紀錄
                take_true_branch()
            else:
                PC.append(Not(cond_s))     # 反向紀錄
                take_false_branch()
        else:
            execute_normally(inst, both_states)
    
    return (path_taken, PC)
```

**注意 PC 的紀錄方式**：根據 concrete 結果決定 `PC.append(cond_s)` 還是 `PC.append(¬cond_s)` — 這樣整條 PC 是你**實際走過的那條 path 的 symbolic 條件**。

## 負面翻轉：怎麼產生新 input

跑完一條 path，你有：

```
PC = [c_1, c_2, c_3, c_4, c_5]
```

要探索新 path，就把 PC 的某一個 clause **反轉**，解 SMT：

```
翻轉 c_5:  new_PC = c_1 ∧ c_2 ∧ c_3 ∧ c_4 ∧ ¬c_5
翻轉 c_4:  new_PC = c_1 ∧ c_2 ∧ c_3 ∧ ¬c_4            # c_5 之後的不管了
翻轉 c_3:  new_PC = c_1 ∧ c_2 ∧ ¬c_3
...
```

每個翻轉都是一個新 SMT query，回來就是新 input。全部加進 queue。

這方式**自然形成 BFS/DFS 的 path exploration**：每次 run 一條，探索鄰居，慢慢往外擴。

## 例子走一次

```c
void f(int a, int b) {
    if (a > 10) {
        if (b < 5) {
            bug();
        }
    }
}
```

I₀ = `(a=0, b=0)`

```
Round 1:
  執行 (a=0, b=0):
    L1: a > 10?  concrete: 0 > 10 → false
        PC.append(Not(α > 10)) == (α ≤ 10)
    回傳
  PC = [α ≤ 10]
  
  翻轉 PC[0]:
    new_PC = Not(α ≤ 10) == α > 10
    solve → α = 11
    new input: (a=11, b=0)

Round 2:
  執行 (a=11, b=0):
    L1: a > 10?  concrete: 11 > 10 → true
        PC.append(α > 10)
    L2: b < 5?   concrete: 0 < 5 → true
        PC.append(β < 5)
    bug()
  PC = [α > 10, β < 5]
  
  ★ 你已經找到 bug ★
  
  翻轉產生更多 input（探索其他 path）:
    翻 PC[1]: α > 10 ∧ Not(β < 5)  →  a=11, b=5
    翻 PC[0]: Not(α > 10)          →  a=0, b=?  (上面已看過 skip)
```

整個演算法**永遠有 concrete value**。它不像 pure symex 那樣維護 2^N 個 state，它就是 loop「跑一次、翻一下、重跑」。

## 為什麼這樣好

### 好處一：external call 隨便 model

pure symex 遇到 `read(fd, buf, n)`：要 model buf 的 symbolic 內容、怎麼變化。

concolic：真的 syscall 去拿 concrete byte，symbolic 那邊就記 `buf[i] = B_i`（B_i 是 symbolic）。後面 branch 如果對 buf 做 `if (buf[0] == 'H')`，concrete 看到 `'X' == 'H' → false`，symbolic 記 `B_0 ≠ 'H'`。

新 input 被求解時，SMT 會給你 `B_0 = 'H'` — 下次 read 你**手動餵**這個 byte（把它寫進 file 或 stdin）。外部世界變成 input 的一部分，被 symex 反向控制。

這個是 SAGE（微軟內部的 fuzzer，Godefroid 帶進 Office）、Driller 等工具的基礎。

### 好處二：path explosion 變成 queue 大小

pure symex 的 active state 可能幾千幾萬。concolic 的 queue 只有 input，一個 input 幾 KB。Queue 長度 10^6 也才幾 GB。**memory profile 完全不同**。

### 好處三：SMT 永遠面對 SAT 的 query

pure symex 的 SMT call 大量是 `check feasibility of this fork`。
concolic 的 SMT call 永遠是 `give me an input for this path`。每次都期待 `sat`、拿 model 就走。

SMT solver 對「找 model」特別快（CDCL heuristics 針對這個優化）。對「證 unsat」慢得多。concolic 偏好的 query profile 恰好是 SMT 擅長的。

## DART 的三大弱點與後續改進

### 弱點 1：Concretization 造成 incomplete

有些 path 你走不到 — 因為外部 call 回傳是 concrete，path 的 feasibility 受限於 concrete 值。

```
int x = external();   // DART 取 x = 5
if (x > 100) bug();   // 走不到了！x 在 symbolic 世界可能是任意值，
                       // 但 concrete 已經鎖定 5
```

這個 case 需要 **symbolic-aware external call**，或讓 external call 回傳 symbolic（部分 pure symex 化）。KLEE 做這個。

### 弱點 2：Loop

loop 讓 PC 變長非常快。PC 有 1000 個 clause 時，每個翻轉都是 1000 次新 SMT call。演算法仍然正確但慢。

後續（CUTE、EXE）加上 **loop bound**、**partial path negation**。

### 弱點 3：搜索順序

DART 原版是 BFS。但有些 bug 埋得很深、離 root 很遠。BFS 跑半天沒到。

後續工具加 **search heuristic**：
- **CREST**：coverage-guided
- **KLEE**：`random-path` 混合 BFS / DFS
- **S2E**：Class-Uniform Path Analysis

這跟 Ch 3 說的 path prioritization 合流。

## CUTE：加上 symbolic pointer

CUTE（Sen, Marinov, Agha, FSE 2005）在 DART 上加了 **symbolic pointer** 支援：

- pointer 的 value 也 symbolic
- 比較 `p == q` 變成 SMT clause
- pointer arithmetic 傳 taint

這讓 linked list、tree 可以被 symex。Java / C++ 的 symex 需要這個。

## SAGE：put it into production

SAGE（Godefroid et al., 2006~2012）把 concolic 搬進 Windows，專門 fuzz 系統 parser。特色：

- **Whitebox fuzzing**：AFL 之前就在跑 coverage-guided
- **Generational search**：一次跑很多 input、並行收集 PC
- 實戰找到 **上千個 MS bugs**（Office、Windows shell、...）

SAGE 是 concolic 第一次證明在真實產品規模 work。它的 paper 是業界典範。

## DART 跟 KLEE 的關係

常見混淆：KLEE 跟 DART 都是 symex，什麼差？

- **DART**：concolic（真的跑），用外部 interpreter / native execution
- **KLEE**：pure symex（不跑），跑在 LLVM interpreter 上

KLEE 比 DART 晚三年出來，作者是同一個學派（Dawson Engler 的學生 Cristian Cadar）。KLEE 的設計是「乾淨的 pure symex」，把 concolic 的外部世界問題用 **POSIX model** 解掉（全部 model 成 symbolic，不靠 concrete）。

工程現實：KLEE 在實務大型 target 也會 degrade 到類似 concolic 的行為 —  loop bounded、memory concretized、部分 external call 退回 concrete。**純不純只是 spectrum，不是 binary**。

## 為什麼你要認識 concolic

- **angr** 本質上是 pure symex + concolic 的 hybrid — 你可以開 concolic 模式（用 `state.options.add(angr.options.SYMBOLIC_CONCOLIC_MODE)`）
- **Triton** 是 concolic 導向（Ch 23 細講）
- **Driller / QSYM / SymCC**（Ch 25）都是 concolic — 因為要接 AFL 的 concrete input
- **fuzzing-assisted** 的 symex 幾乎都是 concolic：你有真實 seed input 時，浪費了不用 concrete 是蠢的

Pure symex 的應用場景其實很窄 —  小 function 的完整覆蓋、unit 測試生成。**真實世界的 symex 工程 99% 是 concolic**。

## 自我檢核

- [ ] 能畫出 DART 的 pseudocode（input queue + path negation）
- [ ] 解釋「concrete 陪跑、symbolic 記 PC」這個設計
- [ ] 知道 concolic 怎麼處理 external call
- [ ] 能分辨 DART、CUTE、SAGE、KLEE 各自的設計定位
- [ ] 理解「pure vs concolic 只是 spectrum」

下一章開始動手 — 用 100 行左右 Python + Z3 寫一個 mini concolic executor，對一個小 bytecode 跑。寫完你對 angr 的 SimState 會有完全不同的感覺。

→ [Ch 7 — 實作：用 Z3 手寫 mini concolic executor](./07-implement-mini-concolic.md)
