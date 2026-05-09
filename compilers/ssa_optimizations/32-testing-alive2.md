# Ch 32 — 驗證與測試：Alive2、lit + FileCheck、CSmith

> 目標：掌握 LLVM 測試基礎設施（lit + FileCheck），理解 Alive2 的形式化驗證原理，以及 CSmith 的 differential testing 方法。

## 為什麼 compiler 測試很難

優化的正確性不直觀。一個看起來「顯然正確」的規則可能在邊界情況下錯誤：

```llvm
; 規則：(x / 2) * 2 → x & ~1（去掉最後一位）
; 對無符號整數：正確
; 對有符號整數且 x < 0：錯誤（-3 / 2 = -1 在 C 中，但 (-3) & ~1 = -4）
```

LLVM 有數千條優化規則，手動驗證每條的正確性是不可能的。需要工具。

## FileCheck：模式匹配測試

**FileCheck** 是 LLVM 的測試工具，把「測試腳本」和「期望輸出」寫在同一個文件裡：

```llvm
; RUN: opt -passes="instcombine" -S %s | FileCheck %s

define i32 @test_add_zero(i32 %x) {
; CHECK-LABEL: @test_add_zero(
; CHECK-NEXT:    ret i32 %x
  %r = add i32 %x, 0   ; 預期被消除
  ret i32 %r
}
```

`; RUN:` 說明要執行的命令（`%s` 是當前文件）。
`; CHECK:` 說明輸出中應該包含的行。
`; CHECK-NOT:` 說明輸出中不應該包含的行。
`; CHECK-LABEL:` 找一個標籤，讓後面的 CHECK 從這裡開始匹配（限定範圍）。

```bash
# 手動跑 FileCheck
opt -passes="instcombine" -S test.ll | FileCheck test.ll
# 通過：沒有輸出
# 失敗：印出 "CHECK: expected string not found"
```

### 常用指令

```
; CHECK: %r = add        → 匹配包含這個子字串的行
; CHECK-NEXT: ret        → 必須是緊接著的下一行
; CHECK-NOT: alloca      → 確認沒有這行
; CHECK-DAG: %a          → 可以以任意順序出現的多行
; CHECK-LABEL: @func:    → 找函式標籤，重置匹配位置

; 捕獲變數名（因為 SSA 的 %名 可能不同）
; CHECK: [[VAL:%.*]] = add
; CHECK: ret [[VAL]]
```

## lit：LLVM 的測試框架

**lit**（LLVM Integrated Tester）是跑 LLVM 所有測試的框架，自動找並執行所有有 `; RUN:` 行的測試文件。

```bash
# 跑所有 InstCombine 測試
cd llvm-project/build
./bin/llvm-lit ../llvm/test/Transforms/InstCombine/

# 跑某個具體測試文件
./bin/llvm-lit ../llvm/test/Transforms/InstCombine/and-or-xor.ll

# 詳細輸出（失敗時顯示實際輸出 vs 期望）
./bin/llvm-lit -v ../llvm/test/Transforms/InstCombine/add.ll
```

對 out-of-tree pass（Ch 0 建立的），也可以用 lit 框架：

```bash
# tests/lit.cfg
config.name = 'PracticeA Tests'
config.test_format = lit.formats.ShTest(True)
config.suffixes = ['.ll']
config.substitutions.append(('%plugin',
    '/path/to/PracticeAPass.so'))
```

## Alive2：形式化 IR 等價性驗證

**Alive2**（2020 年後持續維護）是一個基於 SMT solver（Z3）的 LLVM IR 等價性驗證工具。

它能回答：「這個 IR 優化規則，對所有可能的輸入，是否保持語意不變？」

### 線上使用

最簡單的方式：`alive2.llvm.org`

把「優化前的 IR」和「優化後的 IR」貼上去，Alive2 回答「LGTM（語意等價）」或「找到反例（counterexample）」。

### 例子：驗證 `(x & -1) → x`

```llvm
; Source（優化前）
define i32 @src(i32 %x) {
  %r = and i32 %x, -1
  ret i32 %r
}

; Target（優化後）
define i32 @tgt(i32 %x) {
  ret i32 %x
}
```

Alive2 輸出：`Transformation seems to be correct!`

### 例子：發現錯誤規則

```llvm
; Source：錯誤的規則：(x / 2) * 2 → x（對有符號整數）
define i32 @src(i32 %x) {
  %div = sdiv i32 %x, 2
  %mul = mul i32 %div, 2
  ret i32 %mul
}

; Target
define i32 @tgt(i32 %x) {
  ret i32 %x
}
```

Alive2 輸出：

```
ERROR: Source is more defined than target

Example:
  i32 %x = #x00000001 (1)

Source value: 0
Target value: 1
```

反例：`x = 1`，`1 / 2 = 0`，`0 * 2 = 0`，但 target 返回 1。規則**錯誤**。

### 命令行使用

```bash
# 安裝 Alive2
git clone https://github.com/AliveToolkit/alive2
cd alive2
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
      -DLLVM_DIR=/path/to/llvm/build/lib/cmake/llvm
cmake --build build -j$(nproc)

# 驗證 opt 的某個 pass
./build/alive-tv --smt-to=10 \
    --srcTrans=opt_before.ll \
    --tgtTrans=opt_after.ll
```

## CSmith：差分測試

**CSmith** 是一個 C 程式隨機生成器，生成的程式是合法的 C（無 UB）。

**差分測試（Differential Testing）** 的思路：

```
生成隨機 C 程式
→ 用 gcc -O0 跑，記錄輸出
→ 用 clang -O2 跑，記錄輸出
→ 比較兩個輸出是否相同
→ 如果不同：某個編譯器有 bug
```

CSmith 發現了大量 GCC 和 Clang 的優化 bug（2010 年的論文報告了 79 個確認 bug）。

```bash
# 安裝 CSmith
git clone https://github.com/csmith-project/csmith
cd csmith && cmake -S . -B build && cmake --build build -j$(nproc)

# 生成並測試
./build/src/csmith > /tmp/test.c
gcc -O0 /tmp/test.c -o test_ref && ./test_ref > ref.out
clang -O2 /tmp/test.c -o test_opt && ./test_opt > opt.out
diff ref.out opt.out
```

## 測試自己的 Pass

結合三個工具：

1. **FileCheck**：單元測試，驗證特定模式是否出現/消失
2. **Alive2**：驗證優化規則本身的正確性（在加入代碼前先驗證）
3. **CSmith + 差分測試**：壓力測試，用大量隨機程式找漏洞

```bash
# 對自己的 pass 做差分測試腳本
for i in $(seq 1 1000); do
    csmith > /tmp/test_$i.c
    clang -O0 /tmp/test_$i.c -o /tmp/ref_$i 2>/dev/null || continue
    opt -load-pass-plugin $PLUGIN -passes="my-pass" \
        /tmp/test_$i.ll | clang -O0 -x ir - -o /tmp/opt_$i 2>/dev/null || continue
    
    REF=$(/tmp/ref_$i 2>/dev/null)
    OPT=$(/tmp/opt_$i 2>/dev/null)
    
    if [ "$REF" != "$OPT" ]; then
        echo "Mismatch on test_$i!"
        break
    fi
done
```

## 自我檢核

- [ ] FileCheck 語法：`CHECK`、`CHECK-NEXT`、`CHECK-NOT`、`CHECK-LABEL`、`[[VAR:%.*]]`
- [ ] `lit` 是跑 LLVM 測試的框架，找有 `; RUN:` 行的文件
- [ ] Alive2：SMT solver 形式化驗證 IR 等價性，給出反例（counterexample）
- [ ] CSmith：隨機 C 程式生成 + 差分測試，壓力測試優化 pass
- [ ] 測試 pass 的三層：FileCheck（單元）→ Alive2（規則驗證）→ CSmith（壓力）

→ [練習 C：用 Alive2 驗證 Peephole 規則](./practice-c-alive2.md)
