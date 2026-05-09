# 練習 B — 用 opt 觀察 Loop Pass Pipeline

> 目標：通過動手觀察 LLVM 的 loop pass pipeline，驗證 Ch 20–26 的理論：迴圈識別、LICM、SCEV、展開、向量化的依賴關係和效果。

## 任務規格

五個觀察任務，每個任務都要記錄「pass 前後 IR 的關鍵差異」和「你的解釋」。

## 任務一：觀察 LCSSA 轉換

```bash
cat > /tmp/task1.c << 'EOF'
int sum_escape(int *a, int n) {
    int last = 0;
    for (int i = 0; i < n; i++) {
        last = a[i];   // last 在迴圈外被 use
    }
    return last;       // 迴圈外使用迴圈內的定義
}
EOF

clang -O0 -S -emit-llvm /tmp/task1.c -o /tmp/t1_base.ll
opt -S -passes="mem2reg,loop-simplify" /tmp/t1_base.ll -o /tmp/t1_simplified.ll
opt -S -passes="mem2reg,loop-simplify,lcssa" /tmp/t1_base.ll -o /tmp/t1_lcssa.ll

diff /tmp/t1_simplified.ll /tmp/t1_lcssa.ll
```

**觀察目標**：
- LCSSA 之後，迴圈出口塊多了哪些 phi 節點？
- 這些 phi 的引數是什麼？有幾個引數？

## 任務二：觀察 LICM 的不變式外提

```bash
cat > /tmp/task2.c << 'EOF'
void scale(float *a, int n, float base, float factor) {
    for (int i = 0; i < n; i++) {
        a[i] = a[i] * (base * factor);   // base*factor 是不變式
    }
}
EOF

clang -O0 -S -emit-llvm /tmp/task2.c -o /tmp/t2_base.ll
opt -S -passes="mem2reg,loop-simplify,lcssa,licm" /tmp/t2_base.ll -o /tmp/t2_licm.ll

diff /tmp/t2_base.ll /tmp/t2_licm.ll
```

**觀察目標**：
- `base * factor` 的計算移到了哪裡？
- 如果把 `factor` 改成通過指針傳入（`float *factor`），LICM 還能外提嗎？為什麼？

## 任務三：觀察 SCEV 的 trip count 計算

```bash
cat > /tmp/task3.c << 'EOF'
int fixed_sum() {
    int s = 0;
    for (int i = 0; i < 100; i++) {   // trip count = 100（靜態已知）
        s += i;
    }
    return s;
}

int var_sum(int n) {
    int s = 0;
    for (int i = 0; i < n; i++) {     // trip count = n（運行時確定）
        s += i;
    }
    return s;
}
EOF

clang -O0 -S -emit-llvm /tmp/task3.c -o /tmp/t3_base.ll
opt -passes="mem2reg,print<scalar-evolution>" /tmp/t3_base.ll -o /dev/null 2>&1 | \
    grep -A5 "Printing analysis"
```

**觀察目標**：
- `i` 的 SCEV 表達式是什麼？
- `fixed_sum` 和 `var_sum` 的 backedge-taken count 有什麼不同？

## 任務四：觀察迴圈展開

```bash
cat > /tmp/task4.c << 'EOF'
int small_loop() {
    int s = 0;
    for (int i = 0; i < 4; i++)   // trip count = 4，期待全展開
        s += i;
    return s;
}

int large_loop(int n) {
    int s = 0;
    for (int i = 0; i < n; i++)   // trip count 未知，期待部分展開
        s += i;
    return s;
}
EOF

clang -O0 -S -emit-llvm /tmp/task4.c -o /tmp/t4_base.ll
opt -S -passes="mem2reg,loop-simplify,lcssa,indvars,loop-unroll" \
    /tmp/t4_base.ll -o /tmp/t4_unrolled.ll

cat /tmp/t4_unrolled.ll
```

**觀察目標**：
- `small_loop` 展開後，迴圈結構還存在嗎？
- 如果 `small_loop` 的迴圈被完全展開，最終結果應該直接是 `return 6`（0+1+2+3=6），SCCP 能進一步把它算出來嗎？

```bash
opt -S -passes="mem2reg,loop-simplify,lcssa,indvars,loop-unroll,sccp,dce" \
    /tmp/t4_base.ll -o /tmp/t4_final.ll
```

## 任務五：觀察向量化診斷

```bash
cat > /tmp/task5.c << 'EOF'
void vec_add(float *c, float *a, float *b, int n) {
    for (int i = 0; i < n; i++)
        c[i] = a[i] + b[i];
}

void no_vec(float *a, int n) {
    for (int i = 1; i < n; i++)
        a[i] = a[i-1] + 1.0f;   // 有跨迭代依賴！
}
EOF

# 向量化診斷
clang -O2 \
    -Rpass=loop-vectorize \
    -Rpass-missed=loop-vectorize \
    -Rpass-analysis=loop-vectorize \
    /tmp/task5.c -o /dev/null 2>&1

# 也可以在 IR 層觀察
clang -O1 -S -emit-llvm /tmp/task5.c -o /tmp/t5_O1.ll
opt -S -passes="loop-vectorize" \
    -pass-remarks-missed=loop-vectorize \
    /tmp/t5_O1.ll -o /tmp/t5_vec.ll 2>&1
```

**觀察目標**：
- `vec_add` 被向量化了嗎？向量因子（VF）是多少？
- `no_vec` 為什麼不能向量化？診斷信息說了什麼？
- 如果給 `vec_add` 的指針加上 `__restrict__`，結果有什麼不同？

## 綜合分析：完整 Pipeline

```bash
cat > /tmp/task_all.c << 'EOF'
float dot_product(float *a, float *b, int n) {
    float sum = 0.0f;
    for (int i = 0; i < n; i++)
        sum += a[i] * b[i];
    return sum;
}
EOF

# 逐步觀察每個 pass 的效果
clang -O0 -S -emit-llvm /tmp/task_all.c -o /tmp/all_O0.ll

for passes in \
    "mem2reg" \
    "mem2reg,loop-simplify,lcssa" \
    "mem2reg,loop-simplify,lcssa,licm" \
    "mem2reg,loop-simplify,lcssa,indvars,loop-unroll" \
    "mem2reg,loop-simplify,lcssa,indvars,loop-vectorize"
do
    echo "=== $passes ==="
    opt -S -passes="$passes" /tmp/all_O0.ll -o - 2>/dev/null | \
        grep -E "(phi|call|fmul|fadd|<.*x.*>)" | head -5
    echo
done
```

記錄每一步的關鍵 IR 變化，理解各 pass 的責任分工。

## 自我檢核

- [ ] 任務一：能辨認 LCSSA phi（一個引數，在迴圈出口）
- [ ] 任務二：LICM 把不變式移到 preheader，指針參數會阻止外提（別名不確定）
- [ ] 任務三：靜態 trip count 是具體數字，動態 trip count 是 `{0,+,1}< >`
- [ ] 任務四：全展開後迴圈結構消失，結合 SCCP 可以直接算出常數結果
- [ ] 任務五：`__restrict__` 消除別名不確定性，使向量化合法

→ [Ch 27 Call Graph 與 SCC](./27-call-graph.md)
