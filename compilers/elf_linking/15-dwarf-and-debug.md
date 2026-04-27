# Ch 15 — DWARF debug info 與 section 佈局

> 目標：理解 DWARF 如何跟 ELF section 共存、linker 處理 `.debug_*` 時的特殊規則、以及 linker relaxation 對 DWARF 的衝擊。這章不深入 DWARF spec（幾百頁），但給你一個讀得懂 `readelf -wi` 的框架。

## Debug info 的層次

```
Source code  (hello.c)
    │
    │ compile -g
    ▼
DWARF (debug info format)
    │
    │ stored in
    ▼
ELF sections: .debug_info, .debug_line, .debug_abbrev, ...
    │
    │ read by
    ▼
Debugger (gdb, lldb)、Profiler (perf)、Exception handler
```

DWARF 是**格式**。ELF 是**容器**。

## `.debug_*` section 家族

編譯加 `-g` 後會看到一堆 `.debug_*` section：

```bash
$ readelf -S hello | grep debug
  [24] .debug_aranges    PROGBITS    ...   MS
  [25] .debug_info       PROGBITS    ...
  [26] .debug_abbrev     PROGBITS    ...
  [27] .debug_line       PROGBITS    ...   MS
  [28] .debug_str        PROGBITS    ...   MS
  [29] .debug_line_str   PROGBITS    ...
  [30] .debug_loclists   PROGBITS    ...
  [31] .debug_rnglists   PROGBITS    ...
```

常見 section 分工：

| Section | 內容 |
|---------|------|
| `.debug_info` | 主要 DIE tree（type、function、variable）|
| `.debug_abbrev` | 前面的 abbreviation 表 |
| `.debug_line` | 程式碼行號 → 指令地址對應 |
| `.debug_str` | string pool |
| `.debug_aranges` | 地址範圍 → CU 對應 |
| `.debug_ranges` / `.debug_rnglists` | 範圍 lists |
| `.debug_loc` / `.debug_loclists` | variable 的 location expression |
| `.debug_frame` / `.eh_frame` | unwind info（exception / stack trace）|
| `.debug_macro` | macro 資訊（DWARF 5） |
| `.debug_names` | 符號 index（加速 gdb）|

每個 section 都有 spec 定義的結構。DWARF 5 是最新。

## 特殊 flag：MS (Merge + Strings)

`readelf -S` 裡部分 debug section flags 寫 `MS`：

- M (MERGE)：允許 linker merge 相同內容
- S (STRINGS)：內容是 null-terminated 字串

**Linker 會對 string 做 deduplication**。例如 `"main"` 這個字串可能出現在 100 個 `.debug_str` 裡，linker 只保留一份、所有 offset 指向它。

這讓 debug info size 可以大幅壓縮。

## DWARF section 的 linker 處理特點

Linker 對 `.text` / `.data` 的處理是「合併 + relocate」。對 `.debug_*` 有額外規則：

### 1. 不 load 到 memory

`.debug_*` section 沒 `SHF_ALLOC` flag → 不出現在任何 PT_LOAD segment。runtime 不用。

```bash
readelf -S hello | grep debug_info
# [25] .debug_info       PROGBITS    0000000000000000  ...
```

`Address` 欄位全 0 —— 它不在任何 VA。

### 2. 跨 section 的 address reference

DWARF 裡有個 `DW_AT_low_pc` / `DW_AT_high_pc`，指「這個 function 的起始跟結束地址」。這些需要 relocation，linker 要填 `.text` 的最終地址。

```
.debug_info 裡一條 entry:
  DW_TAG_subprogram
    DW_AT_name: "main"
    DW_AT_low_pc: (R_RISCV_64 .text+0x10)     ← relocation
    DW_AT_high_pc: 0x20 (length)
```

linker 處理時用普通 `R_RISCV_64` 等 relocation。

### 3. SUB 類 relocation：算長度

很多 DWARF 欄位需要「地址 A - 地址 B」：

```
length = high_pc - low_pc
```

compile 時兩個地址都未知。用：

```
R_RISCV_ADD64 high_pc_label     # 先加 high
R_RISCV_SUB64 low_pc_label      # 再減 low
```

linker 依序應用。這就是 Ch 5 提到的 `ADD` / `SUB` 系列 relocation。

### 4. Relaxation 對 DWARF 的衝擊

**Relaxation 改了 `.text` size** → `.debug_line`（行號 → 地址表）裡的地址要全部更新。

LLD / GNU LD 的 RISC-V 支援裡有專門的 "DWARF update" pass。bug 多半出在這：relaxation 後 gdb 跳錯行、backtrace 指錯 function。

**2024 年的一個 LLVM bug**：某些情境下 DWARF line 表的 relax 更新不正確，gdb 顯示錯誤行。SiFive 團隊修的。

## `.eh_frame` vs `.debug_frame`

兩個很像的 section：

- **`.eh_frame`**：exception handling 用，**runtime 要 load**（有 `SHF_ALLOC`）。C++ 例外、pthread cancellation 靠這個。
- **`.debug_frame`**：debugger 用，**不 load**。僅 `-g` 時產。

兩者資料幾乎一樣（call frame info），但格式略不同（`.eh_frame` 用 LSDA 等 C++ 特有 info）。

**看 `.eh_frame`**：

```bash
readelf --debug-dump=frames hello
```

裡面是 CFI（Call Frame Information），描述「在某個 PC，stack 長什麼樣，怎麼 unwind」。

## Split DWARF (`-gsplit-dwarf`)

大型 C++ project 的 debug info 可能比 code 大 10 倍。**Split DWARF** 把 debug info 搬到獨立 `.dwo` 檔：

```bash
gcc -g -gsplit-dwarf -c a.c
# 產生 a.o + a.dwo
```

`.o` 裡只剩 skeleton、real info 在 `.dwo`。link 時只 link `.o`，binary 不含 debug（但有指向 `.dwo` 的 reference）。

好處：

- link 快 10 倍（不用處理大 debug info）
- 部署 binary 小
- 需要 debug 時再加載 `.dwo`

Chrome / Firefox 都用這招。RISC-V toolchain 支援。

## DWARF supplementary object (`.dwp`)

`.dwp` = 把一堆 `.dwo` 合成一個檔方便管理：

```bash
dwp -o hello.dwp a.dwo b.dwo c.dwo
```

gdb 自動找這個檔。

## Line table 是 debugger 的心臟

`.debug_line` 存「指令地址 ↔ source 行」的對應。它是 debugger 做 "step by source line"、"break at line 100" 的根據。

`readelf --debug-dump=decodedline` 看：

```
File name                            Line Number   Starting address
hello.c                              1             0x6e8
hello.c                              2             0x6ea
hello.c                              3             0x6ee
hello.c                              4             0x6f2
...
```

**這張表的準確性對 debugger 至關重要**。line 表錯 → break 錯、backtrace 錯、profiler 錯。

## DWARF version 演進

- **DWARF 2**：1993，基礎
- **DWARF 3**：2005，補充
- **DWARF 4**：2010，加 `.debug_aranges` 改進、performance 大改
- **DWARF 5**：2017，加 `.debug_line_str`、改 header 格式、支援 split DWARF

GCC / LLVM 現在預設 DWARF 5（某些系統 5 或 4）。**不同版 tooling 不完全相容**。舊 gdb 不懂 DWARF 5 的新欄位。

查：

```bash
readelf --debug-dump=info hello | head
# Compilation Unit @ offset 0x0:
# Length:        0xa6 (32-bit)
# Version:       5
```

## Strip debug 的方法

```bash
# 完全砍
strip --strip-debug hello

# 只砍 debug、保留 symbol（給 profiler 用）
objcopy --only-keep-debug hello hello.debug
objcopy --strip-debug hello
objcopy --add-gnu-debuglink=hello.debug hello
```

後者保留一個 `.gnu_debuglink` 指向外部 debug file。gdb 自動找。distro 常用：系統 binary 小但 debug 可下載（`*-dbg` package）。

## 一個「DWARF 錯了」的 debug 案例

**症狀**：gdb 在某個 function step 時跳到奇怪行號。

**診斷**：

1. 用 `--no-relax` link → 看問題是否消失 → 確認是 relax 造成
2. 用 `readelf --debug-dump=decodedline` 看 line table
3. 找出「錯行號」的地址，用 `objdump -d` 看該位置實際是什麼指令
4. 對比 relax 後的 code 位移跟 DWARF 沒同步更新

**修法**：

- 升級 linker（新版可能 fix 了）
- 關 relax（損失 size）
- 回報 linker upstream

**這是 SiFive 工程師的日常 bug**。

## 常見誤會

1. **「-g 只影響 debug，不影響性能」**：對 runtime 完全不影響（debug info 不 load）。但 compile / link 時間增加。
2. **「DWARF 跟 ELF 是同一個東西」**：不。DWARF 是格式定義，ELF 是檔案容器。macOS 的 Mach-O、Windows 的 PDB 是另外兩個 debug info 系統。
3. **「.eh_frame 可以 strip」**：不能。runtime 需要（C++ exception）。`.debug_*` 可以 strip，`.eh_frame` 不行。
4. **「debug info 越多越好」**：debug info 會影響 link 時間、binary 大小、tooling 速度。`-g1` 比 `-g3` 輕多了。
5. **「Split DWARF 是給 C++ 用」**：C 也能用。主要受益是大型 project。

## 動手練習

1. 編 `hello -g -O2`，用 `readelf -S` 列 `.debug_*` section，算 debug info 佔總 size 百分比。
2. 用 `readelf --debug-dump=info hello | less` 看 DIE tree。找 `main` 的 subprogram entry。
3. 用 `--no-relax` 跟 default 各編一次 `-g` binary，diff 兩個 `.debug_line` dump，看 relax 對行號表的影響。
4. 試 `-gsplit-dwarf`，產 `.dwo` 檔，看 binary size 差異。
5. 寫 gdb script 讀 backtrace，驗證 inline function 的正確還原。

## 自我檢核

- [ ] 我能列出 5 個以上 `.debug_*` section 的用途
- [ ] 我能解釋 `.eh_frame` 跟 `.debug_frame` 的分別
- [ ] 我知道 linker relax 如何影響 DWARF 以及修復挑戰
- [ ] 我能用 `readelf` / `objdump` 查 DWARF 內容
- [ ] 我了解 Split DWARF 的機制跟收益

Part 5 的一半完成。下一章進 Part 5 的第二半：四個主流 linker 的比較，讓你知道為什麼 mold 這麼紅、gold 為什麼被淘汰。

→ [Ch 16 ld / gold / lld / mold：四個 linker 的取捨](./16-linker-implementations.md)
