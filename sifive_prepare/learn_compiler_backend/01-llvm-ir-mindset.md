# Ch 1 — LLVM IR 心法

> 目標：理解 LLVM IR 的設計哲學 —— SSA、型別系統、三種表示形式、為什麼選 phi node 而不是 block argument。讀完你能看著一段 IR 立刻辨認 basic block、type、call pattern。

## LLVM IR 的三種表示

同一份 IR 有三種 representation，內容等價：

```
1. Text (.ll)       人類可讀的 textual 格式
2. Bitcode (.bc)    binary 壓縮格式，.ll 的序列化
3. In-memory        C++ 物件 (Module, Function, Instruction, Value...)
```

用途：

- `.ll`：閱讀、debug、自己手寫測試
- `.bc`：存檔、傳遞（LTO 的 `.o` 裡也是 bitcode）
- in-memory：compile 時的工作表示

轉換：

```bash
clang -emit-llvm -S hello.c -o hello.ll      # 輸出 .ll
clang -emit-llvm -c hello.c -o hello.bc       # 輸出 .bc
llvm-dis hello.bc -o hello.ll                 # bc → ll
llvm-as hello.ll -o hello.bc                  # ll → bc
```

**本課大部分時間看 `.ll`**，因為視覺化易懂。

## 最小例子

C：

```c
int add(int a, int b) { return a + b; }
```

`.ll`：

```llvm
define dso_local i32 @add(i32 %a, i32 %b) {
entry:
  %sum = add nsw i32 %a, %b
  ret i32 %sum
}
```

解構：

- `define`：function 定義
- `dso_local`：symbol visibility
- `i32`：integer 32-bit（LLVM 型別）
- `@add`：global symbol（`@` 前綴）
- `%a, %b`：local values（`%` 前綴）
- `entry:`：basic block label
- `add nsw`：加法指令，`nsw` = "no signed wrap"（undefined behavior if overflow）
- `ret`：return

## SSA：靜態單一賦值

**LLVM IR 是 SSA form**：每個 `%name` 只被 assign 一次。

不是 SSA：

```c
int x = 1;
x = x + 2;       // 同一變數重新賦值
x = x * 3;
```

SSA：

```llvm
%x1 = i32 1
%x2 = add i32 %x1, 2
%x3 = mul i32 %x2, 3
```

每個賦值是新 value name。好處：

- **Def-use chain 清楚**：`%x3` 用了 `%x2`、`%x2` 用了 `%x1`，dataflow 直接看出
- **很多優化簡化**：常數傳播、死碼消除、register allocation 都受益
- **Dominance 直接定義 scope**

## Phi node：SSA 的救星

SSA 的問題：碰到 control flow 合流怎麼辦？

```c
int foo(int cond, int a, int b) {
    int x;
    if (cond) x = a;
    else      x = b;
    return x;     // x 是哪個？
}
```

如果 `x` 是 SSA value，它應該是 `a` 或 `b`？**phi node 解決**：

```llvm
define i32 @foo(i32 %cond, i32 %a, i32 %b) {
entry:
  %t = icmp ne i32 %cond, 0
  br i1 %t, label %then, label %else

then:
  br label %end

else:
  br label %end

end:
  %x = phi i32 [ %a, %then ], [ %b, %else ]
  ret i32 %x
}
```

`phi` 說：「如果從 `%then` 來，我的值是 `%a`；從 `%else` 來，是 `%b`」。

**Phi 是 SSA 的核心 primitive**。看到 `phi` 你知道「這是 control flow 合流的位置」。

### 為什麼不用 block arguments

有些 IR（MLIR、Swift IR）用 block arguments 取代 phi：

```llvm
; hypothetical
end(%x: i32):        ; block 接受 argument
  ret i32 %x
```

兩種等價，但 LLVM 選 phi 是歷史原因。GlobalISel 的 MIR 還是用 phi。

## 型別系統

LLVM IR 強型別：

```llvm
i1    i8    i16    i32    i64    i128     ; 整數，bit 寬度
half  float  double  fp128  x86_fp80       ; 浮點
ptr                                         ; 指標（通用，不帶 pointee type）
[4 x i32]                                   ; 固定長 array
{i32, float}                                ; 結構
<4 x i32>                                   ; 固定長 vector
<vscale x 4 x i32>                          ; scalable vector (RVV / SVE)
void                                        ; 無
```

### pointer 不帶 pointee type (opaque pointer)

LLVM 15 後全面轉 opaque pointer：

```llvm
; 舊 (typed pointer):
%ptr = alloca i32*
store i32 42, i32* %ptr

; 新 (opaque pointer):
%ptr = alloca ptr
store i32 42, ptr %ptr           ; type info 移到 store 指令
```

**好處**：不用一大堆 cast、IR 更乾淨。

### scalable vector 是 RVV 的基礎

`<vscale x 4 x i32>` 表示「長度是 `vscale × 4` 個 i32 的 vector，`vscale` runtime 決定」。

這是 LLVM 對 VLA ISA（RVV / SVE）的表達。Ch 15 會深入。

## 主要指令類型

```llvm
; 算術
%a = add i32 %x, %y           ; 加
%b = sub i32 %x, %y
%c = mul i32 %x, %y
%d = sdiv i32 %x, %y          ; signed div
%e = udiv i32 %x, %y          ; unsigned div

; 邏輯
%f = and i32 %x, %y
%g = or  i32 %x, %y
%h = shl i32 %x, %y           ; shift left
%i = ashr i32 %x, %y          ; arithmetic shift right
%j = lshr i32 %x, %y          ; logical shift right

; 比較
%k = icmp slt i32 %x, %y      ; signed less than → i1
%l = fcmp olt float %x, %y    ; ordered less than

; 記憶體
%m = alloca i32                        ; stack 上配一個 i32
store i32 42, ptr %m                    ; write
%n = load i32, ptr %m                   ; read

; 控制流
br label %next                          ; unconditional
br i1 %cond, label %then, label %else    ; conditional
ret i32 %val
call i32 @foo(i32 %a)                   ; function call
invoke ...                              ; call + exception handling

; 特殊
%o = getelementptr i32, ptr %arr, i32 5 ; &arr[5]
%p = bitcast i32* %x to i8*              ; 型別轉換，不改 bit
%q = phi i32 [ %a, %bb1 ], [ %b, %bb2 ]  ; SSA 合流
```

**`getelementptr`（GEP）** 是 LLVM 最特別的指令。不是 load / 不 produce 值，只算地址：

```llvm
%p = getelementptr i32, ptr %arr, i32 5
; equiv to C: p = &arr[5]
```

## Basic Block

每個 function 由 basic block 組成。每個 block：

1. 有一個 label（entry 點）
2. 含 0 或多個 **非 terminator 指令**
3. 結尾是**一個 terminator**（`ret` / `br` / `switch` / `unreachable` 等）

```llvm
entry:
  %a = add i32 %x, 1
  %b = mul i32 %a, 2
  br label %next         ; terminator

next:
  %c = sub i32 %b, 3
  ret i32 %c             ; terminator
```

Terminator 結束 block。沒 terminator 的 block 是錯的。

## Attribute

Function / parameter / instruction 可以有 attribute：

```llvm
define noinline i32 @foo(i32 noundef %a, ptr readonly %b) #0 {
  ...
}

attributes #0 = { nounwind uwtable "target-cpu"="riscv64" ... }
```

常見 attribute：

- `noinline`：禁止 inline
- `alwaysinline`：強制 inline
- `nounwind`：不會 throw exception
- `readonly` / `readnone`：function 或 parameter 不寫 memory
- `noundef`：保證非 undef

這些 attribute 讓 optimizer 知道更多資訊、產更好 code。

## Global value

```llvm
@x = global i32 42, align 4        ; 有初值
@y = common global i32 0, align 4  ; .bss
@str = private constant [6 x i8] c"hello\00", align 1
```

`@` 前綴 + global address。對應 C 的 global variable。

## Linkage types

```llvm
external   ; 外部定義
linkonce_odr ; inline function 的標準 linkage
private    ; 類似 C 的 static
internal   ; file-scope
available_externally ; 像 extern inline
```

Linkage 影響 symbol 在 `.o` 的 binding。

## 讀一個真實 `.ll`

```c
#include <stdio.h>
int main(void) {
    printf("hello\n");
    return 0;
}
```

`clang -emit-llvm -S -O2 hello.c`：

```llvm
@.str = private unnamed_addr constant [7 x i8] c"hello\0A\00", align 1

; Function Attrs: nofree nounwind
define dso_local noundef i32 @main() local_unnamed_addr #0 {
entry:
  %puts = tail call i32 @puts(ptr nonnull dereferenceable(1) @.str)
  ret i32 0
}

declare i32 @puts(ptr nocapture noundef readonly) local_unnamed_addr #1
```

觀察：

- `@.str` 是 format string（`.rodata`）
- `@main` 是 function
- `@puts` 是 **declaration**（`declare` 而非 `define`）—— 定義在別的地方（libc）
- `tail call`：tail-call optimization hint
- Attribute 多到讓人眼花

## IR 的 hierarchy

LLVM 的 in-memory 物件層次：

```
Module (= 一個 .ll / .bc 檔)
  ├── GlobalVariable*
  ├── Function*
  │     ├── BasicBlock*
  │     │     └── Instruction*
  │     └── Argument*
  └── Metadata / NamedMDNode
```

寫 pass 時你處理這層。Ch 2 會講。

## IR 的精神：高階 enough、低階 enough

LLVM IR 的設計平衡點：

- **夠高階**：type-aware、control-flow clear、SSA 好分析
- **夠低階**：close to machine，可以被多種 target 接手

這個平衡讓 LLVM IR 成為「第三方 frontend（Rust、Swift、Julia）產生的通用目標」+「多 backend（x86、ARM、RISC-V）的 lowering 起點」。

## IR 跟 machine code 的差距

LLVM IR **不是** machine code。幾個關鍵抽象：

- 沒有 register file（用 SSA value）
- 沒有 stack layout（alloca 佔一個 slot）
- 沒有 calling convention 細節（call 是抽象的）
- 沒有 ISA-specific instruction

這些在 SelectionDAG → MIR 的過程中被 lower 掉。Ch 4 之後會看。

## 寫 IR 的 toolchain

寫 test case 常手寫 `.ll`。IR 基本規則：

- 每個 SSA value 用一次（`%a = add ...; %b = add %a, ...`）
- Basic block 結尾必須 terminator
- 指令的 type 必須對齊（`i32` 加 `i32` 產 `i32`）
- `llvm-as` 檢查 syntax：`llvm-as test.ll -o /dev/null`

## 常見誤會

1. **「IR 是 platform-independent」**：大致是，但有 target-specific intrinsic（`@llvm.riscv.vadd.*`）、data layout 可能綁 arch。
2. **「IR 永遠 SSA」**：function scope 是。但 memory 不是（memory 的值可以反覆改）。SSA 只管 `%name` 的 value。
3. **「optimization 後 IR 會變少」**：通常是，但某些 pass（e.g., loop unroll）會變多。大小不是優化指標。
4. **「`load` / `store` 是 memory instruction」**：對，但 opt 可以把很多 local `load/store` 變成 SSA register（`mem2reg` pass）。
5. **「phi 只在 loop 出現」**：任何 control-flow join 都有 phi。`if-else` 也會產生。

## 動手練習

1. 寫 10 行 C，用 `clang -emit-llvm -S -O0` 跟 `-O2` 各產一個 `.ll`，diff 看優化前後差異。
2. 手寫一份 `.ll`（例：接受兩個 `i32` 加起來 return），用 `llvm-as` + `lli` 跑起來。
3. 找一個有 if-else 的 C code，`-O0` 編，找出產生的 phi node，對照 source 理解。
4. 用 `opt -passes=instcombine -S` 跑一份 IR，看 pass 改了什麼。
5. 找一個 `@llvm.memcpy.*` intrinsic call 的 IR，查 LangRef 看它的語意。

## 自我檢核

- [ ] 我能讀一段 `.ll` 並指出 function / basic block / SSA value
- [ ] 我能解釋 SSA 以及 phi node 為什麼存在
- [ ] 我知道 basic block 的結尾一定是 terminator
- [ ] 我能列出常見指令類型（算術、memory、control、GEP、phi）
- [ ] 我知道 LLVM IR 跟 machine code 的抽象差距

下一章看 pass manager —— LLVM 最核心的 infrastructure，所有 optimization / analysis 的 runner。

→ [Ch 2 Pass manager：legacy vs new](./02-pass-manager.md)
