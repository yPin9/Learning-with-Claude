# Ch 11 — PIC / PIE 與 code model

> 目標：徹底搞懂 `-fPIC` / `-fPIE` / `-mcmodel=` 這三個 flag 在 RISC-V 的實際意義。為什麼 shared library 一定要 PIC、為什麼 modern distro 預設 PIE、為什麼 kernel 用 medany。

## 名詞先釐清

- **PIC (Position-Independent Code)**：**code 不綁定絕對地址**。可以 load 到任何地址跑。shared library (`.so`) 必備。
- **PIE (Position-Independent Executable)**：**整個 executable** 是 PIC，可被 ASLR randomize。
- **非 PIC / 非 PIE**：傳統 static linked executable，code 含絕對地址，load 到固定位置。

**工程師怕的重複概念**：

```
PIC  = code 層的特性（適用 shared library 跟 PIE）
PIE  = executable 層的標記（代表這 binary 的 code 是 PIC）
```

PIE executable 的 `.text` 必然是 PIC。但一個 PIC 的 `.o` 不一定是 PIE （可以作為 `.so` 的一部分）。

## 為什麼 shared library 必須 PIC

如果 `.so` 的 code 含絕對地址：

```
call 0x1000        # 呼叫 libfoo 內部函式 foo（固定地址 0x1000）
```

兩個問題：

1. **多 process 無法共用**：不同 process 的 `libfoo.so` 可能 load 到不同 VA，0x1000 在 A process 有效、在 B process 無效。
2. **要 runtime 改 code**：load 時改寫所有 `call` 指令 → `.text` 要 writable → 違反 RX 原則。

**PIC 的解法**：所有跨越的跳轉走 PC-relative 或 GOT：

```
# intra-library call (跳同 library 內)
auipc ra, %pcrel_hi(foo)
jalr  ra, %pcrel_lo(foo)

# cross-library call (呼叫 libc 的 printf)
auipc t0, %got_pcrel_hi(printf)
ld    t0, %pcrel_lo(1b)(t0)       # load GOT slot
jalr  t0
```

程式碼只讀 PC 跟 GOT。GOT 可寫、`.text` 只讀。

## PIE 的理由：ASLR

傳統 static linked executable 的 code 總是 load 到固定地址（x86-64 是 0x400000）。攻擊者知道這個地址就能計算 return-address 寫入。

PIE 讓 kernel 每次 load 時把整個 binary 放到隨機位置：

```
Run 1: code starts at 0x5612a1234000
Run 2: code starts at 0x57ab29cd5000
Run 3: code starts at 0x58ce34e56000
```

攻擊者不知道地址，ROP / return-to-libc 難度大增。

**代價**：啟動稍慢（要 runtime relocation）、code 稍大（PIC 指令多一條）。

現代 Linux distro（Ubuntu 20+、Fedora 28+）預設 PIE。

## 看 PIE 的證據

```bash
file hello
# ELF 64-bit LSB pie executable, RISC-V, version 1 (SYSV)
```

`pie` 字樣就是。或：

```bash
readelf -h hello | grep Type
# Type: DYN (Shared object file)
```

PIE executable 的 Type 是 `DYN`，跟真的 shared object 同 type。兩者差在：

- 真 `.so` 沒 `PT_INTERP`、`e_entry = 0`（或不用）
- PIE executable 有 `PT_INTERP`、`e_entry` 是 `_start` 的 offset

## 比較三種 build

```bash
# 1. Static (no PIC, no PIE)
gcc -static -no-pie hello.c -o hello.static

# 2. Dynamic but non-PIE
gcc -no-pie hello.c -o hello.dynamic

# 3. PIE (dynamic + position-independent) ← 現代預設
gcc hello.c -o hello.pie

file hello.static hello.dynamic hello.pie
```

輸出：

```
hello.static:  ELF 64-bit LSB executable, statically linked
hello.dynamic: ELF 64-bit LSB executable, dynamically linked
hello.pie:     ELF 64-bit LSB pie executable, dynamically linked
```

## Code model 是什麼

code model 決定 **compiler 產生的 PC-relative code 能覆蓋多遠**。RISC-V 有兩個（標準）：

### `-mcmodel=medlow`

符號地址在 **絕對** `[-2 GiB, +2 GiB)`（低 32-bit signed）。

```asm
lui  a0, %hi(foo)
addi a0, a0, %lo(foo)
```

用 `lui + addi`（絕對地址）存取 symbol。

**適用**：baremetal（你知道自己 load 在低地址）、kernel（設好 VA 在低地址）。

### `-mcmodel=medany`（默認）

符號在 **PC ±2 GiB**。

```asm
auipc a0, %pcrel_hi(foo)
addi  a0, a0, %pcrel_lo(foo)
```

用 `auipc + addi`（PC-relative）。

**適用**：所有 userspace、所有 PIC、所有 PIE。因為 ASLR 會把 code 放到 VA 空間的隨機位置。

## 三個 flag 的交互

```
-fPIC           → 產生的 .o 是 PIC (for .so)
-fPIE           → 產生的 .o 是 PIC, 但只用於 PIE executable
-fno-pic        → 不產生 PIC code (傳統 style)

-pie            → link 時產生 PIE executable
-no-pie         → link 時產生傳統 executable

-mcmodel=medany → 用 PC-relative (for PIC/PIE)
-mcmodel=medlow → 用絕對地址 (for non-PIC/non-PIE)
```

**最常見組合**：

1. **Modern userspace (distro 預設)**：`-fPIE -pie -mcmodel=medany`
2. **Shared library**：`-fPIC -shared -mcmodel=medany`
3. **Kernel**：`-fno-pic -mcmodel=medlow` 或 `-mcmodel=medany`（新一代）
4. **Baremetal MCU**：`-fno-pic -mcmodel=medlow`

## PIC 跟 non-PIC 的 code diff

```c
// hello.c
int g = 42;
int main(void) { return g; }
```

```bash
# non-PIC
gcc -fno-pic -mcmodel=medlow -O2 -S hello.c -o hello.nopic.s

# PIC
gcc -fPIE -mcmodel=medany -O2 -S hello.c -o hello.pic.s
```

`non-PIC` 的 main：

```asm
main:
    lui    a5, %hi(g)
    lw     a0, %lo(g)(a5)
    ret
```

絕對地址、兩條指令。

`PIC` 版：

```asm
main:
    auipc  a5, %pcrel_hi(g)
    lw     a0, %pcrel_lo(1b)(a5)
    ret
```

PC-relative、兩條指令。

**指令數相同、形式不同**。但若是 external symbol（跨 .so），PIC 要透過 GOT 多一層：

```asm
    # access extern foo from .so
    auipc  a5, %got_pcrel_hi(foo)
    ld     a5, %pcrel_lo(1b)(a5)   # load GOT slot
    lw     a0, 0(a5)                 # now read foo
```

**多一次 memory access**。這是 PIC 的 runtime cost。

## 錯用 code model 的典型症狀

### 症狀 1: `relocation truncated to fit: R_RISCV_HI20 against symbol ...`

你用 `-fno-pic -mcmodel=medlow`，但 symbol 位於 `> 2 GiB`。

修法：換 `-mcmodel=medany`。

### 症狀 2: `recompile with -fPIC`

你試圖用 `non-PIC .o` 做 `-shared`。linker 會罵：

```
/usr/bin/ld: a.o: relocation R_RISCV_HI20 against symbol `g' can not be used when making a shared object; recompile with -fPIC
```

修法：加 `-fPIC` 重編。

### 症狀 3: PIE executable 啟動時 segfault

你 `-pie` 但 compile 時忘 `-fPIE` → linker 勉強連起來但 `.text` 裡有絕對地址沒 relocate → runtime 跳飛。

修法：`-fPIE -pie` 成對用。

## Kernel 對 code model 的特殊處理

Linux kernel 通常 map 到高地址（如 `0xffffffff80000000`）。

過去用 `-mcmodel=kernel`（x86 的概念），RISC-V 對應用：

- `-mcmodel=medany` + linker script 確保 kernel 範圍
- 或自訂 `-mcmodel=medany` 延伸 range

Linux RISC-V port 的 makefile 有這些細節。

## Embedded 的 non-PIC 選擇

MCU / baremetal 不需要 PIC：

- 只一個 process、load 位置固定
- 沒 ASLR（也沒必要）
- PIC 多一條 memory access 在 hot loop 中有差

所以 `riscv64-unknown-elf-gcc` 的 baremetal toolchain **預設 `-fno-pic -mcmodel=medlow`**。除非你特別改，不會有 PIC 的 overhead。

## large code model（未來）

RISC-V 目前沒有標準的 `-mcmodel=large`。ARM / x86 有 —— 允許 symbol 距離 PC 超過 ±2 GiB。

對**超大 binary**（幾 GiB 的 code）有用。目前 RISC-V spec 還在討論中（2025）。實務上幾乎沒人碰這個 limit。

## PIC 對 data 的影響

`.data` 本身永遠 RW。PIC 與否差別在「code 如何存取 data」：

- non-PIC: 用絕對地址
- PIC: 用 PC-relative 或 GOT

**所以 `.so` 的 `.data` 每個 process 有自己的 copy**（dynamic linker 做 copy-on-write）。

## copy relocation（x86 有、RISC-V 不建議）

某些平台有 `R_X86_64_COPY`：讓 executable 的 `.bss` 複製 `.so` 的 global variable 初值。

**RISC-V 生態避免用**。太複雜、容易出錯。而且強制 shared variable 必須放 executable 的 `.bss`。

遇到 "copy relocation" 相關訊息多半是移植舊 x86 code 的問題。

## 動手練習

1. 編同一個 hello.c 三種 build（static, no-pie, pie），比較：
   - 三個 ELF 的 Type（readelf -h）
   - `.text` 相同 function 的 objdump 差異
   - binary size（`size hello.*`）
2. 嘗試用 non-PIC `.o` link 成 `.so`（`gcc -shared a.o`）。看 error 訊息，加 `-fPIC` 重做。
3. 查自己 distro 的 `/bin/ls`：`file /bin/ls`、`readelf -h /bin/ls | grep Type`。確認是 PIE。
4. 寫一個 shared library + 呼叫它的 executable，跑 `ldd` 看依賴。
5. 用 `-mcmodel=medlow` 編超大 C++ 程式（含幾個 GB 的 `.rodata`）看會不會出 relocation truncated 錯。

## 常見誤會

1. **「PIC 一定比 non-PIC 慢」**：多 1 條 memory indirection，實務上差 < 5%。現代 CPU prefetcher 很擅長處理這種。
2. **「PIE = PIC」**：PIC 是性質（位置無關），PIE 是「一個 PIC 的可執行檔」。
3. **「-fPIC 一定要 -shared」**：不一定。`-fPIC` 也能做 PIE 或 static PIE。
4. **「baremetal 可以 PIC」**：可以但沒必要。baremetal 不需要 ASLR。
5. **「medany 比 medlow 快」**：不是。code model 選擇是為了 **可以 link**，不是速度。速度基本相同。

## 自我檢核

- [ ] 我能解釋 PIC / PIE / medlow / medany 四個名詞
- [ ] 我能列 `.so` / PIE executable / static binary / baremetal MCU 的 code model 選擇
- [ ] 我能看 objdump 差異判斷 code 是 PIC 還是 non-PIC
- [ ] 我能 debug `recompile with -fPIC` 類錯誤
- [ ] 我知道 copy relocation 是什麼、為什麼 RISC-V 避免用

下一章處理 Thread-Local Storage — `__thread` / `thread_local` 背後的四種 access model。

→ [Ch 12 TLS Model：LE / IE / GD / LD](./12-tls-models.md)
