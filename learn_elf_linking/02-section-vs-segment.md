# Ch 2 — Section vs Segment：為什麼要分兩套

> 目標：徹底分清 ELF 的「section view」跟「segment view」。這兩者在教材裡常被混為一談，但它們服務不同目的、給不同程式看。看完你永遠不會再問「為什麼一個 ELF 要兩套 header」。

## 一句話版本

- **Section** = **linker / debugger 看的**，細顆粒（可有幾十個）
- **Segment** = **kernel loader / dynamic linker 看的**，粗顆粒（通常 4–9 個）

相同 bytes、兩種視角。linker 輸出時從 section 重新 pack 成 segment。

## 為什麼要兩套

想像 linker 剛收到一堆 `.o` 檔：

- 每個 `.o` 有自己的 `.text`、`.data`、`.rodata`、`.debug_info`、`.eh_frame` ...
- linker 要做的事：**把相同名字的 section 合併、重新 layout、產 executable**

linker 在乎的是「**section 的屬性**」：

- `.text` 要合進一起
- `.rodata` 要合進一起
- `.bss` 要一起算大小
- `.debug_info` 合但最後可能 strip 掉

這是 linker view。

另一個需求：**kernel 要 load 這個 executable**，它需要知道：

- 哪幾塊 byte 要 load 到 memory、load 到哪個 virtual address
- 哪幾塊要 read-only、哪幾塊要 read-write、哪幾塊要 executable
- 哪些 byte 不用 copy（`.bss`）

kernel **不在乎** `.text` vs `.rodata` 的分別 —— 反正都是 read-only + executable（或 read-only）。它只在乎「這幾塊一起 mmap、RWX flag 是什麼」。

所以 linker 把一堆 section **按權限分組**成 segment：

```
.text .rodata .eh_frame .plt .init  → 一個 R-X segment
.got .data .bss                      → 一個 RW- segment
```

**Segment 是 section 的權限分組**。

## 視覺化：同一份 ELF 的兩個 view

```
Section view (linker):                Segment view (loader):

┌──────────────┐  offset 0x238       ┌──────────────┐
│  .interp      │                    │              │
├──────────────┤                    │              │
│  .note...    │                    │              │
├──────────────┤                    │              │
│  .hash       │                    │              │
├──────────────┤                    │              │
│  .dynsym     │                    │  PT_LOAD #1  │ R--
├──────────────┤                    │  (read-only) │
│  .dynstr     │                    │              │
├──────────────┤                    │              │
│  .rela.dyn   │                    │              │
├──────────────┤                    │              │
│  .rela.plt   │                    │              │
├──────────────┤                    │              │
│  .plt        │                    │              │
├──────────────┤                    │  PT_LOAD #2  │ R-X
│  .text       │                    │  (exec)      │
├──────────────┤                    │              │
│  .fini       │                    │              │
├──────────────┤                    │              │
│  .rodata     │                    │              │
├──────────────┤                    │              │
│  .eh_frame   │                    │              │
├══════════════┤  page 對齊           ├══════════════┤
│  .init_array │                    │              │
├──────────────┤                    │              │
│  .fini_array │                    │              │
├──────────────┤                    │  PT_LOAD #3  │ RW-
│  .dynamic    │                    │  (data)      │
├──────────────┤                    │              │
│  .got        │                    │              │
├──────────────┤                    │              │
│  .data       │                    │              │
├──────────────┤                    │              │
│  .bss        │ (NOBITS)           │              │
└──────────────┘                    └──────────────┘
```

同一串 byte，左邊顯示「linker 如何切塊」，右邊顯示「loader 如何 mmap」。

## 關鍵觀察：segment 是 section 的 union

**一個 segment 涵蓋多個 section，但一個 section 只會對到（通常）一個 segment**。

`readelf -l` 的最後會印 "Section to Segment mapping"：

```
 Section to Segment mapping:
  Segment Sections...
   00
   01     .interp
   02     .interp .note.gnu.build-id .hash .gnu.hash .dynsym .dynstr ...
   03     .init_array .fini_array .dynamic .got .data .bss
   04     .dynamic
   ...
```

看 segment 02 涵蓋十幾個 section：linker 把它們全塞進一個 R-X 的 PT_LOAD。

## 為什麼會 section 跨 segment

有時一個 section 出現在兩個 segment 裡。例如 `.dynamic` 既在 `PT_LOAD #3`（要 load 到 memory）也在 `PT_DYNAMIC`（指出它的位置讓 dynamic linker 找）。

這很正常 —— segment 是「map view」，不同 map 可以指向相同 byte range。

## 一個沒 segment 的世界：`.o` 檔

```bash
riscv64-linux-gnu-readelf -l hello.o
# There are no program headers in this file.
```

`.o` 沒有 program header。因為它是 **relocatable**，還沒決定地址、不知道怎麼 load。它只需要 section view，給 linker 處理。

**這是「為什麼要分兩套」的直接證據**：如果兩者必然綁定，`.o` 就不該能單獨存在。

## Section 的 address（`sh_addr`）

Section header 的 `sh_addr` 欄位：

- **在 `.o` 裡通常是 0**：因為還沒 relocate、還沒決定 virtual address。
- **在 executable / shared object 裡**：是該 section 要 load 到的 virtual address。這個地址必須跟對應 segment 的 `p_vaddr + offset_in_segment` 一致。

linker 的責任之一：**讓每個 section 的 `sh_addr` 算對，且跟 segment 的地址對齊**。

## Segment 的 alignment 與 page size

`p_align` 通常是 `0x1000`（4 KiB，典型 page size）。兩個不同權限的 `PT_LOAD` 之間必須 **page-align**，因為 MMU 以 page 為單位設 permission。

**這就是 binary 裡常常看到「中間一塊空白」的原因**：code segment 結束到 data segment 開始之間要補 0 到下一個 page。

```
0x600: .text 結束
0x1000: .data 開始    ← 中間 0x600~0x1000 是 pad
```

**大 binary 加上 `-Wl,-z,max-page-size=0x10000`** 就會看到 pad 多一位，因為 align 到 64 KiB。

## 一些特殊 segment

### PT_INTERP

指向 `.interp` section，裡面存動態 linker 的 path：

```bash
$ riscv64-linux-gnu-readelf -x .interp hello
Hex dump of section '.interp':
  0x00000238 2f6c6962 2f6c642d 6c696e75 782d7269 /lib/ld-linux-ri
  0x00000248 73637636 342d6c70 36346400          scv64-lp64d.
```

**kernel exec 一個 ELF 時，如果看到 `PT_INTERP`，先執行這個 interpreter（通常是 `ld-linux-*.so`），再由它 load 真正的程式**。沒 PT_INTERP 的 ELF 是 static executable，kernel 直接跑。

### PT_DYNAMIC

指向 `.dynamic` section。dynamic linker 看這裡知道「我要 load 哪些 `.so`」、「符號表在哪」等。

### PT_GNU_STACK

這是個虛擬的 segment（沒實際 byte），只為了表達 **stack 的 RWX 屬性**。典型值是 RW- —— 表示 stack 不可執行（NX 保護）。如果這個變成 RWX（`-z execstack`）會觸發 security 警告。

### PT_GNU_RELRO

"Relocation Read-Only"。告訴 dynamic linker：某塊 memory 一旦 relocate 完就改成 read-only。保護 `.got.plt` 等被攻擊目標。Ch 13 會講。

### PT_TLS

TLS template。Ch 12 會深入。

## 一個具體例子

編一個小程式看兩個 view：

```c
// hello.c
#include <stdio.h>
static const char greeting[] = "hello\n";
int global_var = 42;
static int bss_var;

int main(void) {
    printf("%s", greeting);
    return global_var + bss_var;
}
```

```bash
riscv64-linux-gnu-gcc -o hello hello.c
riscv64-linux-gnu-readelf -S hello | grep -E "\.text|\.rodata|\.data|\.bss"
riscv64-linux-gnu-readelf -l hello
```

你應該看到：

- `.text`、`.rodata`（greeting 在這）、`.eh_frame` 被 pack 進一個 `PT_LOAD` R-X
- `.data`（global_var 在這）、`.bss`（bss_var 在這）、`.got` 被 pack 進另一個 `PT_LOAD` RW-

**把這三種 variable 放對位置是 compiler + linker 合作的結果**。

## Stripping：砍 section 不砍 segment

```bash
riscv64-linux-gnu-strip hello
riscv64-linux-gnu-readelf -h hello | grep -E "section headers"
# Number of section headers: 0      ← 沒 section header 了
```

strip 可以把 section header 全砍 —— 因為 runtime 不需要它們。Segment 資訊保留（不能砍，loader 要用）。

stripped binary 還能跑，但 debugger / nm 失去幾乎所有能力。

## 讀 ELF 的順序（給寫 parser 的人）

寫一個 ELF loader / parser 的建議：

1. 讀 ELF header（64 byte，固定位置 0）
2. 檢查 magic、class、endian
3. **如果要 load**：去 `e_phoff` 讀 program headers、跑所有 `PT_LOAD`
4. **如果要 link / debug**：去 `e_shoff` 讀 section headers、找需要的 section

**兩條路徑互不依賴**。loader 不需要讀 section header、linker 也可以不讀 program header（對 `.o` 來說更是必然）。

## `readelf` vs `objdump -s`：兩個角度的印法

- `readelf -S file`：按 section 列，印每個 section 的 header
- `readelf -l file`：按 segment 列，印每個 segment 的 header
- `objdump -s file`：按 section 列，印每個 section 的 **hex dump**（不是 header）

要 debug section 內容用 `objdump -s` 或 `readelf -x <name>`。要看 metadata 用 `readelf -S / -l`。

## 常見誤會

1. **「Section 就是 C 的 `__attribute__((section("x")))`」**：部分對。你宣告的 section 最終會出現在 ELF 的 section 表，但 linker 可能合進別的 section、或 map 到某個 segment。
2. **「沒 section header 的 binary 不合法」**：合法。strip 過的 binary 可以只有 program header。
3. **「Segment 跟 section 大小一樣」**：不。一個 segment 通常涵蓋多個 section。
4. **「`.bss` 是 section 不是 segment」**：`.bss` 是 section，但它**在** RW-的 `PT_LOAD` segment 裡。它用 `p_memsz > p_filesz` 的差值表達。
5. **「runtime 會用到 section header」**：基本不會。runtime 依賴 segment + `.dynamic`。唯一例外：`/proc/self/exe` 讀 section 做 self-introspection。

## 動手練習

1. 用 `readelf -S hello | wc -l` 數 section 數，`readelf -l hello | grep PT_LOAD | wc -l` 數 segment 數。算 ratio（通常 10:1 以上）。
2. 用 `objcopy --only-keep-debug hello hello.dbg && strip hello` 把 debug 分離。查 `readelf -S hello.dbg` 跟 `hello` 的 section 差異。
3. 找出你系統的 `/bin/ls`，`readelf -l /bin/ls` 看它的 segments、對比一個自己編的 hello world。
4. 寫 C 程式宣告一個 `__attribute__((section(".mydata"))) int x = 7;`。build 後 `readelf -S` 找出 `.mydata`、看它被 pack 到哪個 segment。
5. 用 `riscv64-linux-gnu-strip -s hello -o hello.stripped`，比較大小。找出砍掉的是哪些 section（用 `readelf -S` 前後對比）。

## 自我檢核

- [ ] 我能用一句話區別 section 跟 segment
- [ ] 我能解釋為什麼 `.o` 沒 program header、executable 兩個都有
- [ ] 我能指出 `.bss` 在 section 中與在 segment 中的表達差異
- [ ] 我知道 `PT_INTERP` / `PT_DYNAMIC` / `PT_GNU_STACK` 的用途
- [ ] 我能解釋為什麼 stripped binary 不影響執行

下一章看 ELF 最核心的資訊之一：symbol table。學完你能分辨 T / D / U / W 狀態、能用 `nm` 定位 undefined reference。

→ [Ch 3 Symbol Table 與 String Table](./03-symbol-and-string-table.md)
