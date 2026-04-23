# Ch 9 — MEMORY / SECTIONS / PROVIDE 的陷阱

> 目標：把 linker script 在實戰中最常見的 10+ 陷阱列出來，每個附症狀 + debug 方法 + 修法。這章比上一章更「戰場」，是你未來 maintain firmware 時會回來查的章。

## 陷阱 1：Orphan section 跑到奇怪地方

**症狀**：C code 用 `__attribute__((section(".mystuff")))`，linker 沒報錯，但 runtime 找不到 symbol 或讀到錯誤資料。

**原因**：linker script 沒 explicit 寫 `.mystuff` → linker 認定 orphan section → 根據 heuristic 插到某個 memory region。通常會印 warning「orphan section placed after」但多數人忽略。

**debug**：

```bash
ld.lld -Map=output.map ...
# 看 output.map 找出 .mystuff 被放哪
```

**修法**：在 linker script 明確寫：

```
.mystuff : {
    KEEP(*(.mystuff))
} > RAM
```

## 陷阱 2：`.got` 跟 `.data` 位置錯

**症狀**：dynamic linked binary 跑不動、segfault 在奇怪地方。

**原因**：某些自寫 linker script 忘了 `.got` / `.got.plt`。這兩個一定要在 writable region。

**修法**：

```
SECTIONS {
    ...
    .got : { *(.got) *(.got.plt) } > RAM
    .data : { *(.data*) } > RAM
    ...
}
```

userspace 的 default linker script 已經處理，多在 baremetal 或 kernel 自寫時踩到。

## 陷阱 3：`.init_array` 沒執行

**症狀**：C++ static 變數的 constructor 沒被呼叫、global `__attribute__((constructor))` function 沒跑。

**原因**：

```c
void __attribute__((constructor)) init_me(void) { ... }
```

這種標記產生 `.init_array` section，**startup code 要遍歷它呼叫**。baremetal 自寫的 startup 如果沒這段 → 全部 constructor 跳過。

**修法**：linker script 要定義邊界 symbol：

```
__init_array_start = .;
KEEP(*(SORT_BY_INIT_PRIORITY(.init_array.*)))
KEEP(*(.init_array))
__init_array_end = .;
```

startup code 遍歷：

```c
typedef void (*init_fn)(void);
extern init_fn __init_array_start[];
extern init_fn __init_array_end[];

for (init_fn *f = __init_array_start; f < __init_array_end; f++)
    (*f)();
```

## 陷阱 4：`KEEP` 沒寫導致 vector 被砍

**症狀**：`-Wl,--gc-sections` 開後，ISR vector table 消失、開機進不了 main。

**原因**：

```
.isr_vector : { *(.isr_vector) }     /* 沒 KEEP */
```

gc-sections 砍沒被引用的 section。Vector 沒 C 程式「呼叫」，linker 當它是 dead code。

**修法**：

```
.isr_vector : { KEEP(*(.isr_vector)) }
```

**記憶法**：任何「hardware 用、但 C code 不 reference」的 section 都要 `KEEP`。

## 陷阱 5：`LENGTH` 超了卻沒錯

**症狀**：flash 只有 512 KiB，你的 binary link 出來 `.text` size 600 KiB，但 linker 沒報錯，燒 Flash 時失敗。

**原因**：linker script 沒寫 `> FLASH`，或寫了 `> FLASH` 但 linker 版本不嚴（較新版應該會報 overflow）。

**修法**：

```
.text : { ... } > FLASH
```

手動加 assertion：

```
ASSERT(. <= ORIGIN(FLASH) + LENGTH(FLASH), "FLASH overflow!")
```

assertion fail 時 linker 報錯。嚴謹的 linker script 都有。

## 陷阱 6：Stack 被蓋

**症狀**：程式跑著跑著變成亂七八糟資料；global variable 的值被不相關 function 改。

**原因**：stack 沒預留、或 stack 放錯位置。stack 向下成長，如果 stack 底下直接是 `.bss`，overflow 就蓋 `.bss`。

**修法**：明確宣告 stack section、放 RAM 尾端、前面留 guard：

```
.stack (NOLOAD) : {
    . = ALIGN(16);
    _stack_bottom = .;
    . += 0x2000;          /* 8 KiB */
    _stack_top = .;
} > RAM

/* 確保 stack 不會蓋到 .bss */
ASSERT(_stack_bottom >= _bss_end, "Stack collides with BSS!")
```

`(NOLOAD)` 讓 linker 不幫這個 section 產 LOAD record（stack 不需要 initialize）。

## 陷阱 7：`PROVIDE` 的微妙差異

```
PROVIDE(end = .);
```

vs

```
end = .;
```

**差異**：後者一定定義，前面看情況定義。

陷阱：libc 可能定義 `end`（heap 結尾）。你的 script 寫 `end = .;` → multiple definition 錯。改 `PROVIDE(end = .)` 就沒事。

**記憶法**：給 C code / libc 可能也定義的符號用 PROVIDE；你獨家的 symbol 用直接 assign。

## 陷阱 8：LMA vs VMA 搞反

**症狀**：全域變數都是垃圾值，好像 `.data` 沒被 initialize。

**原因**：

```
.data : { *(.data*) } > RAM
```

缺 `AT > FLASH`，導致 LMA = RAM。runtime 時 RAM 還沒被填 → 全垃圾。

**修法**：

```
.data : { *(.data*) } > RAM AT > FLASH
```

並且 startup code 有 copy `.data`。

**debug**：`readelf -l` 看 program header。找 `.data` 所在的 PT_LOAD，比較 `VirtAddr` 跟 `PhysAddr`（LMA）是否一致。不一致 = 設對了。

## 陷阱 9：Section ordering 依賴不同 linker

```
.text : {
    *(.text.init)
    *(.text*)
}
```

GNU LD 跟 LLD 處理 section 排序可能略不同。LLD 默認照 linker script 順序；GNU LD 有時會 reorder 做「相同 flags 的 section 合併」等優化。

**debug**：用 Map file 確認實際 layout。兩種 linker 都看一次。

**修法**：關鍵 section 用 `SORT_NONE` 或 `SORT_BY_NAME`：

```
.text : {
    SORT_NONE(*(.text.init))
    *(.text*)
}
```

## 陷阱 10：`CONSTANT(MAXPAGESIZE)` 不符合

**症狀**：部署 binary 到 host 上，有些系統 segfault 或警告「segment alignment too small」。

**原因**：MAXPAGESIZE 跟目標系統 page size 不符。Linux RISC-V page size 可以是 4KiB 或 16KiB（某些 SoC）。

**修法**：

```
. = ALIGN(CONSTANT(MAXPAGESIZE));
```

讓 linker 用平台預設。**不要硬寫 `0x1000`**。

## 陷阱 11：Common vs BSS 放不同位置

**症狀**：某些 uninit 全域變數沒 zero，程式行為詭異。

**原因**：

```
.bss : { *(.bss*) }          /* 沒收 COMMON! */
```

COMMON 是 linker 階段特有的 section（Ch 3 提過）。startup 的 `memset(bss_start, 0, size)` 不會清到 COMMON。

**修法**：

```
.bss : {
    *(.bss*)
    *(COMMON)                /* ← 必須收 */
} > RAM
```

## 陷阱 12：Relax 搞破壞 linker script assumption

**症狀**：某些 section offset 跟 linker script 預期不同。

**原因**：`--relax` 改變 section size。你在 script 裡：

```
. = ORIGIN(FLASH) + 0x1000;   /* 強制從這裡起 */
.data : { ... }
```

可能上游 `.text` 被 relax 縮短，`.data` 就不在 0x1000 處。

**修法**：如果 baremetal 需要精確 layout，用 `--no-relax`：

```
ld -T my.lds --no-relax ...
```

或設計 linker script 適應 relax（多用 ALIGN、少用絕對地址）。

## 陷阱 13：Page alignment 的浪費

**症狀**：binary 比預期大很多。

**原因**：PT_LOAD 之間必須 page-align。如果你 `.text` 700 byte 後接 `.data`、page size 4 KiB → 中間 pad 3300 byte。

**修法**：

- 接受（userspace 沒差）
- 用 `-z max-page-size=0x100` 減小（可能破壞 mmap 假設，僅對 embedded 適用）
- 調 section ordering 讓同 permission 的 section 擠一起

## 陷阱 14：Debug info 跑到 RAM

**症狀**：一部分 `.debug_*` section 被放到 RAM，浪費空間。

**原因**：沒明確寫，orphan section 可能被放 RAM。

**修法**：

```
/DISCARD/ : {
    *(.comment)
    *(.debug_*)        /* 如果不要 debug info */
}
```

`/DISCARD/` 是特殊 output section，裡面的 input section 直接丟掉。

## 陷阱 15：Startup code 的依賴沒滿足

**症狀**：早期 boot log 錯亂、某些 interrupt 不 work。

**原因**：你的 startup code 依賴某個 symbol（例 `_vector_table_start`），但 linker script 沒定義。

**debug**：

```bash
nm final.elf | grep _vector_table_start
# 如果印 U 就是沒 resolve
```

**修法**：linker script 加 `PROVIDE`：

```
PROVIDE(_vector_table_start = ADDR(.isr_vector));
```

## 一份「我寧可保守」的 linker script checklist

對每個 section 問自己：

- [ ] 有 `> REGION` 嗎
- [ ] 需要 LMA ≠ VMA 嗎（`AT > ...`）
- [ ] 有沒有 data 需要 startup copy
- [ ] BSS 收了 COMMON 嗎
- [ ] Vector / table 有 `KEEP` 嗎
- [ ] Stack 有預留 + ASSERT 嗎
- [ ] init_array / fini_array 有包嗎

少任何一項都可能炸。

## 多 linker 相容性

寫 linker script 時注意三個 linker 行為差：

| 議題 | GNU LD | LLD | mold |
|------|--------|-----|------|
| orphan section 放法 | 保守 | 嚴 | 嚴 |
| 默認 relax | 開 | 開 | 開 |
| `SORT_NONE` 尊重 | 完整 | 完整 | 完整 |
| `OVERLAY` | 支援 | 支援 | 部分 |
| 跨 .o 的 section merge | 完整 | 完整 | 完整 |

實務上一份標準 linker script 三個 linker 都能吃。但 edge case 要測。

## Map file 是你的相機

遇到奇怪 layout 問題：

```bash
ld -T my.lds -Map=link.map ...
```

`link.map` 會詳細列：

- 每個 section 放哪
- 每個 input section 屬於哪個 output section
- symbol 的地址

**超長但超有用**。一個有問題的 map file 10 分鐘能幫你定位 99% 的 linker script bug。

## 動手練習

1. 寫一個最小 MCU linker script (FLASH + RAM + .data with AT)，加上所有 ASSERT / PROVIDE。link 一個 hello.c，用 Map file 驗證 layout。
2. 故意造陷阱：`.bss` 不收 `*(COMMON)`，宣告 `int x;`（common），觀察 runtime 時 x 的初值。
3. 寫一個 `__attribute__((constructor))` function，不加 `KEEP` 的 `.init_array`，用 `--gc-sections`。看 constructor 是否被砍。
4. 把同一份 source 用 GNU LD 跟 LLD 各 link，diff 出兩個 binary 的 Map file 差異。
5. 寫一個 linker script bug 自測：`.text` > FLASH, `.data` 忘了 AT > FLASH。跑，觀察 `.data` 裡的變數是否 initialized。

## 常見誤會

1. **「Linker script 寫對就一定 work」**：不夠。要跟 startup code 配合。兩邊的 symbol name + layout 要匹配。
2. **「symbol 有 PROVIDE 就安全」**：PROVIDE 避免 multiple def，但可能 override 你想要的定義。看清楚。
3. **「Map file 只是 debug 用」**：Map file 是 linker script 的診斷工具，應該跟 linker script 版本一起 check in。
4. **「空的 linker script 跟 default 一樣」**：不一樣。空 script 讓 linker 完全靠命令列跟 internal heuristic。絕對寫顯式 script 較好。
5. **「Relax 不會影響 linker script」**：會，如上面陷阱 12。

## 自我檢核

- [ ] 我能列 5 個以上 linker script 常見陷阱
- [ ] 我能用 Map file 診斷 linker 行為
- [ ] 我能寫包含 LMA/VMA 區分的 MCU script
- [ ] 我知道 `/DISCARD/` 的用途
- [ ] 我能解釋 `KEEP` / `PROVIDE` / `PROVIDE_HIDDEN` 的分別

Part 3 完。下一章進 Part 4 dynamic linking 世界 — GOT / PLT / `.dynamic`，每天你的 `ls` 跟 `python` 都走過的路，但你可能從沒看過它們。

→ [Ch 10 動態連結全貌：GOT / PLT / .dynamic](./10-dynamic-linking.md)
