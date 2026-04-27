# Ch 4 — 靜態連結流程：resolution → relocation → layout

> 目標：把 linker 從「黑盒子」變成你能畫出流程圖的東西。讀完你能分辨「symbol resolve 階段的錯」跟「relocation 階段的錯」，能解釋為什麼 `.o` 之間的 cross-reference 到 linker 這一步才真正綁定。

## Linker 做的四件事

傳統 static linker（`ld`）處理流程：

```
Input: a.o b.o c.o libfoo.a
           │
           ▼
┌─────────────────────────┐
│ 1. Input scanning        │  讀所有 .o / .a，建 symbol table
└─────────────────────────┘
           │
           ▼
┌─────────────────────────┐
│ 2. Symbol resolution     │  把每個 undefined reference 配對到 definition
└─────────────────────────┘
           │
           ▼
┌─────────────────────────┐
│ 3. Section / segment layout│ 決定每個 section 放哪個地址
└─────────────────────────┘
           │
           ▼
┌─────────────────────────┐
│ 4. Relocation            │  把所有 reference 的地方填上正確地址
└─────────────────────────┘
           │
           ▼
         output (a.out / hello / libfoo.so)
```

本章按這個順序走一遍。

## 階段 1 — Input Scanning

linker 讀每個輸入檔：

- `.o` 檔：整個吃下，把所有 section、所有 symbol 加進 table。
- `.a` 檔（archive）：**不整個吃**。只有它裡面的 `.o` 提供「當前未解決的 undefined symbol」才納入。

這個「lazy inclusion」是 `.a` 的核心特性，也是 link order 敏感的根源：

```bash
# libm.a 定義 sin, cos, pow...

gcc main.c -lm
# 1. 掃 main.o：找到 undefined: sin
# 2. 掃 libm.a：sin 在 sin.o，拉 sin.o 進來

gcc -lm main.c
# 1. 掃 libm.a：目前沒 undefined，跳過整包
# 2. 掃 main.o：找到 undefined: sin
# 3. 沒更多輸入 → undefined reference to sin!
```

**結論：所有 `-l` flag 要放引用它的 `.o` 之後**。

### Circular dependency: --start-group / --end-group

如果 `a.a` 引用 `b.a` 的函式、`b.a` 又引用 `a.a` 的函式：

```
a.a provides func_a, uses func_b
b.a provides func_b, uses func_a
```

單次掃描解決不了（掃 a.a 時 func_b 不在 undefined 清單；掃 b.a 後 func_a 還是 undefined）。

解法：

```bash
gcc main.c -Wl,--start-group -la -lb -Wl,--end-group
```

linker 會**重複掃**這組 library 直到沒更多 undefined 被 resolve。這會慢一點，但必要時就得用。

## 階段 2 — Symbol Resolution

把每個 `U` 配到 `T` / `D` 等 defined symbol。

### 衝突處理規則

1. **Strong + Strong**：錯誤（`multiple definition of ...`）
2. **Strong + Weak**：strong 贏
3. **Weak + Weak**：第一個贏（或 largest 贏，取決於 linker）
4. **Common + Strong**：strong 贏
5. **Common + Common**：取最大 size，合併成一個

**Strong** = 有 initializer 的 global variable 或非 inline function。
**Weak** = `__attribute__((weak))` / uninitialized common.

### 一個經典坑

```c
// a.c
int x;                    // uninitialized global (old rule: common)

// b.c
double x;                 // uninitialized global (old rule: common)

// c.c: 使用 x，假設是 int
int main() { printf("%d\n", x); }
```

如果用 `-fcommon`（舊行為），linker 合併 `a.c` 跟 `b.c` 的 `x` 成一個（取大的，double = 8 byte）。`c.c` 以為是 4-byte int，讀前 4 byte → 結果是 double 的低 half bit pattern → 怪數字。

**GCC 10+ 預設 `-fno-common`**，這種 code 直接 link error 逼你改。

## 階段 3 — Section / Segment Layout

linker 的 script（稍後 Ch 8 深入）控制 layout。預設行為：

1. 收集所有 `.o` 的 section
2. 按 type 合併：所有 `.text` 合成一個 output `.text`，所有 `.data` 合成一個 `.data`...
3. 依 linker script 決定順序
4. 按 page size 對齊
5. 決定每個 output section 的 virtual address

典型結果（x86-64 / RISC-V 都類似）：

```
Virtual addr   Section
0x400000       ELF header + program headers
0x400040       .interp
0x400078       .note.*
0x4000c0       .dynsym / .dynstr / .rela.*
0x4005e0       .plt
0x400620       .text        ← code
0x400850       .fini
0x400858       .rodata
               --- page ---
0x401000       .init_array / .fini_array / .dynamic / .got
0x401060       .data
0x401068       .bss (NOBITS)
```

每個 section 被賦予 virtual address。linker 也維護一個 mapping：`.o 的 section offset` → `output 的 virtual address`。

### Section ordering 的奧秘

linker script 的預設把 section 依**權限**分群：

- readonly (text, rodata) 先
- readwrite (data, got) 後
- zero-initialized (bss) 最後

這讓 page boundary 能乾淨切開。

有些 flag 會改 ordering：

- `-ffunction-sections`：每個 function 一個獨立 section → 可以 dead-code elimination
- `-fdata-sections`：每個 variable 一個 section
- `-Wl,--gc-sections`：linker 清掉沒被引用的 section

這三個合用可以大幅縮減 binary size。嵌入式常用。

## 階段 4 — Relocation

**這是 linker 最核心的工作**。每個 `.o` 的 `.text` 裡有很多指令留了「空位」等 linker 填值。例如：

```
# .o 裡的 `.text`:
6e8: 1141             addi   sp,sp,-16
6ea: e406             sd     ra,8(sp)
6ec: 00000517         auipc  a0, 0x0        ← 空位
6f0: 00050513         addi   a0, a0, 0      ← 空位
6f4: 00000097         auipc  ra, 0x0        ← 空位
6f8: 000080e7         jalr   0(ra)           ← 空位
```

這些 `0` 是暫填。**對應的 relocation section 記錄「這裡要填什麼」**：

```bash
$ riscv64-linux-gnu-objdump -r hello.o
RELOCATION RECORDS FOR [.text]:
OFFSET   TYPE              VALUE
000006ec R_RISCV_PCREL_HI20  .rodata+0x0       ← "把 (.rodata起點) 相對 PC 的高 20 bit 填進 0x6ec 的 auipc"
000006ec R_RISCV_RELAX       *ABS*
000006f0 R_RISCV_PCREL_LO12_I .L0              ← "對應 PC-rel HI20 的 low 12 bit 填 addi"
000006f0 R_RISCV_RELAX       *ABS*
000006f4 R_RISCV_CALL        puts             ← "auipc+jalr 配對呼叫 puts"
000006f4 R_RISCV_RELAX       *ABS*
```

每筆 relocation 描述：

- **OFFSET**：在 section 裡哪個位置要改
- **TYPE**：改的方式（不同 type 有不同公式）
- **VALUE**：要用哪個 symbol + addend

linker 到這步已經知道每個 symbol 的**最終 virtual address**，所以它：

1. 對每筆 relocation entry
2. 按 TYPE 的公式算要填的值
3. 把值寫進 OFFSET 指的位置

Ch 5 會細講所有 relocation type。

## Relocation section 的命名

兩種：

- **SHT_REL** (`.rel.<section>`)：沒 addend，x86 用
- **SHT_RELA** (`.rela.<section>`)：有 addend，RISC-V / x86-64 用

RISC-V 永遠用 `.rela.<section>`。所以你會看到：

- `.rela.text` → 對應 `.text` 的 relocation
- `.rela.data` → 對應 `.data` 的
- `.rela.plt` → 給動態連結用

### addend 是什麼

某些 relocation 需要一個常數 offset。例如 `&arr[5]` 比 `&arr[0]` 多 `5 * sizeof(elem)`：

```c
int arr[10];
int *p = &arr[5];    // 指向 arr 起點 + 20 byte (assuming int)
```

compiler 會產生 relocation「symbol=arr, addend=20」。linker 算 `arr` 最終地址再加 20 填進去。

## 一張總結圖

```
  .o files         linker table          output
  ┌─────┐
  │ a.o │─┐        ┌──────────────┐
  └─────┘ │        │ Symbol table │
  ┌─────┐ │  →     │              │ ──┐
  │ b.o │─┼─→      │ Relocs list  │   │
  └─────┘ │        │              │   │
  ┌─────┐ │        │ Section list │   ▼
  │ c.o │─┘        └──────────────┘ ┌──────────┐
  └─────┘                            │ a.out    │
  ┌─────┐                            │ (or .so) │
  │ .a  │──→ (lazy)                  └──────────┘
  └─────┘
```

## 真實流程範例

```
a.c:
  extern int y;
  int x = 10;
  int main() { return x + y; }

b.c:
  int y = 20;
```

編 / link：

```bash
gcc -c a.c -o a.o
gcc -c b.c -o b.o
gcc a.o b.o -o prog
```

### 在 a.o 的 symbol table:

```
num  value  bind    type    ndx   name
0    0      LOCAL   NOTYPE  UND
1    0      LOCAL   FILE    ABS   a.c
2    0      GLOBAL  OBJECT  4     x      ← 定義於 .data (section 4)
3    0      GLOBAL  FUNC    2     main   ← 定義於 .text
4    0      GLOBAL  NOTYPE  UND   y      ← 引用 y，undefined
```

### 在 b.o 的 symbol table:

```
num  value  bind    type    ndx   name
0    ...
2    0      GLOBAL  OBJECT  4     y      ← 定義於 .data
```

### Linker 的動作：

1. **Input scanning**: a.o、b.o 的所有 section + symbol 進 table
2. **Resolution**: a.o 的 UND y → b.o 的 DEF y ✓
3. **Layout**: 把所有 `.text` 合 → 0x400620 起、`.data` 合 → 0x401060 起
   - a.o 的 `x` 在 output `.data` 的 offset 0
   - b.o 的 `y` 在 output `.data` 的 offset 4（因為 x 佔 4 byte）
   - 最終：`x = 0x401060`, `y = 0x401064`
4. **Relocation**: a.o 的 `main` 裡有兩個 reference：
   - `x` 的地址 → 填 `0x401060`
   - `y` 的地址 → 填 `0x401064`
   - 實際填法依 relocation type（RISC-V 用 PCREL_HI20/LO12 pair）

完成！產出 `prog`。

## 靜態 lib 的一個特殊情況

```bash
ar rcs libfoo.a foo.o bar.o baz.o
```

一個 `.a` 就是 `.o` 的 archive。linker 看 `.a` 時：

- 掃 archive index（在 `.a` 的第一個 member `__.SYMDEF`）
- 看 index 找出哪個 `.o` 定義目前的 undefined symbol
- 只納入需要的 `.o`

**這個 lazy 行為讓你的 binary 只含真正用到的 function**。但它也是「不加 --whole-archive 會缺 symbol」的坑源。

## 常見的 Link Error 分類

| 錯誤訊息 | 階段 | 修法 |
|----------|------|------|
| `undefined reference to 'x'` | Resolution | 補 .o 或 -l |
| `multiple definition of 'x'` | Resolution | static 化、或挑一邊刪 |
| `relocation overflow` | Relocation | 換 code model / 換 linker |
| `cannot find -lfoo` | Input | `-L` 加路徑 / 安裝 |
| `section '.my' overlaps '.your'` | Layout | linker script 衝突 |
| `relocation truncated to fit` | Relocation | 同 overflow，換 mcmodel |
| `relocation R_X86_64_PC32 ... can not be used` | Relocation | 加 `-fPIC` |

認得階段就能快速定位。

## 常見坑

1. **拼錯 library 名**：`gcc a.c -lXt` 其實要 `-lXt` 對應 `libXt.so`。換 prefix 會 `cannot find`。
2. **太多 `.o` 互相依賴**：start-group/end-group 救你。
3. **重複 link 同個 `.a`**：不會錯，但浪費時間。現代 linker 有去重。
4. **`.a` 的順序錯**：參考本章上面的例子。
5. **Multiple definition 來自 header 定義變數**：header 只能 `extern`，`.c` 才定義。

## 動手練習

1. 建一個三檔 project（a/b/c.c），刻意讓 a 用 b 定義的函式、b 用 c 的。用不同的 link order 編看會不會錯。
2. 用 `ld --verbose` 看 linker 預設的 linker script（會噴 200 行），認出 `.text` / `.data` 的 layout 規則。
3. 用 `ar tv libfoo.a` 看 archive 裡有幾個 `.o`。用 `nm libfoo.a` 看每個 `.o` 的 symbol。
4. 寫一個 common symbol 測試（用 `-fcommon`），看 linker 如何合併兩個 `int x;`。
5. 用 `-Wl,-Map=map.txt` 產 linker map file，讀它看 linker 如何排列 section。

## 自我檢核

- [ ] 我能畫出 linker 的四階段流程
- [ ] 我能解釋 `.a` 的 lazy inclusion 以及 link order 為什麼敏感
- [ ] 我能分辨「resolution 階段錯」跟「relocation 階段錯」
- [ ] 我知道 weak + strong、common 的解析規則
- [ ] 我能讀 `objdump -r` 的輸出並對照 .text 找到對應位置

下一章深入 relocation 的具體 type —— 每種 type 的語意、RISC-V 專屬的型態、以及為什麼 `R_RISCV_PCREL_LO12` 要指向 label 而不是 symbol（這個解答 `riscv` Ch 3 埋的伏筆）。

→ [Ch 5 Relocation type 總論](./05-relocation-types.md)
