# 練習 A — 用 RV32I 指令測試打穿單週期 core

> **目標**：把 Ch 12 那顆單週期 `core` 從「跑對一支程式」推進到「對每類指令都正確」。你要寫一組**自檢測試（self-checking test）**——涵蓋算術/邏輯/比較/移位/load-store/branch/jump 各類指令，每項算出結果和預期比對，錯就把「第幾項失敗」寫進一個約定的記憶體位址，全過就寫 0。這是 riscv-tests `rv32ui`（RV32I user-level）的精神：用程式自己判斷 pass/fail，不靠人眼看 dump。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。本練習的參考解全部真跑驗證過（含「故意植入 bug 被抓到」的反例）。

## 為什麼要做這個練習？

Ch 12 用費氏數列驗過整顆 core，但那只證明了「這條特定路徑對」。有很多指令和邊界沒被考到：

- SLT/SLTU 的有號無號分野、SRA 的符號延伸——費氏數列沒用到。
- LB/LBU 的 sign/zero extend——沒用到。
- BLT/BGE 遇**負數**的有號比較——費氏數列的 `bne` 只比正數，沒踩到 `$signed()` 那個坑。
- 各種 branch 的 taken/not-taken 兩種走向。

一個 core 可能「費氏數列跑對，但 BLT 遇負數判反」——這種 bug 費氏數列永遠抓不到。**系統性的自檢測試**才能把每類指令、每個容易錯的邊界都打一遍。這也是真實 CPU 開發的日常：改一行 RTL，跑整組 regression test 確認沒破壞任何指令。

而且自檢測試的價值在**可重複、免人工**——你改了 core、重跑一個命令，看 `PASSED` 或 `FAILED at test #N`，立刻知道有沒有壞、壞在哪。這比每次瞪著暫存器 dump 猜哪裡不對高效太多。

## 任務規格

**輸入**：Ch 12 完成的 `core.sv` 及其所有子模組（control_unit/imm_gen/regfile/alu/dmem/branch_unit）。若你還沒做完，先回 Ch 12。

**你要交付兩樣**：

1. **一支自檢測試組 `selftest.S`**（RV32I 組語），至少涵蓋下列每一類，且每類都要包含**會踩到已知坑的邊界 case**：
   - 算術：ADD、SUB（含溢位回繞可選）
   - 邏輯：AND、OR、XOR
   - 比較：SLT（有號，用負數）、SLTU（無號，用大數）
   - 移位：SLL、SRA（用負數驗符號延伸）
   - load/store：SB + LB（有號延伸）、LBU（零延伸）——同一 byte 驗兩種延伸相反
   - 分支：BEQ（taken）、BLT（**用負數**，驗有號）、BLTU（用大數，驗無號）、以及一個 not-taken 案例
   - 跳轉：JAL（存返回位址）+ JALR（返回），驗呼叫往返

2. **一支 C++ testbench `selftest_tb.cpp`**，跑到程式停（PC 自旋不動）或上限 cycle，然後讀約定位址判斷 pass/fail。

**pass/fail 約定**（本練習採用，也貼近 riscv-tests 的 tohost 精神）：

- 用 `mem[0]`（dmem 的 word 0）當「結果暫存器」。
- 程式全部測試通過 → 把 **0** 寫進 `mem[0]`。
- 任一項失敗 → 把**該項的編號**（1, 2, 3...）寫進 `mem[0]` 並停。
- tb 讀 `mem[0]`：0 印 `PASSED`，非 0 印 `FAILED at test #N`。

**驗收標準**：

- 你的 core 跑 `selftest` 得到 `SELFTEST PASSED (mem[0]=0)`。
- **反向驗證**（重要）：故意在 core 植入一個 bug（例如把 branch_unit 的 `$signed()` 拿掉、或把 SRA 改成 SRL），重跑，你的測試組**必須抓到並報出對應的 test #N**。抓不到代表你的測試覆蓋不足——一組永遠 PASS 的測試沒有價值。

## 先建立直覺：自檢測試怎麼「自己判斷對錯」

核心手法：**算出結果，和預期比對，不同就跳去失敗處理**。

```
   test N:
     <做運算，結果放某暫存器 x3>
     addi x4, x0, <預期值>
     bne  x3, x4, failN      ← 不相等就跳去 failN
     <繼續下一個 test>
   ...
   pass:
     sw x0, 0(x0)            ← mem[0] = 0，代表全過
     halt: j halt
   failN:
     addi x3, x0, N          ← 記下失敗編號
     j report
   report:
     sw x3, 0(x0)            ← mem[0] = N
     halt: j halt
```

整支程式就是一串「算→比→不對就跳走」。跑到底沒跳走 = 全過，寫 0；中途跳走 = 記下編號寫進去。tb 只要讀 `mem[0]` 一個值就知道結果。這正是 riscv-tests 的做法（它用 `TEST_CASE` 巨集把「算+比+失敗跳 fail」包起來，失敗時把測試號寫到 `tohost` 位址）。

一個關鍵細節：**預期值怎麼放進暫存器**。小的預期值（能塞進 12-bit 立即數，-2048~2047）用 `addi x4, x0, <值>` 一條搞定。這就是為什麼下面的測試多選小數字當預期——省得用 `lui`+`addi` 兩條組大數。負數也行（`addi x4, x0, -1` 得 0xffffffff）。

## 分段實作建議

別想一次寫完整組。分五步，每步都能單獨驗。

**Step 1：搭出「一個 test + pass/fail 骨架」，先只放一項最簡單的 ADD。**
寫 `_start` 做一次 `add`、比對、`pass` 寫 0、`failN` 寫編號、`report`/`halt` 自旋。先讓這個最小骨架在 core 上跑出 `PASSED`。這步的重點是把 pass/fail 機制和 tb 的「跑到停 + 讀 mem[0]」串通。骨架對了，後面加 test 只是複製貼上。

**Step 2：加「會踩坑」的比較與移位測試（SLT/SLTU/SRA），用負數。**
這幾項是最容易在 ALU 出錯的。SLT 用 `(-1, 1)` 驗有號、SLTU 同輸入驗無號（結果相反）、SRA 用負數（如 `-16 >> 2 = -4`）驗符號延伸。若你的 ALU 有 Ch 9 的 `$signed()` 坑，這步會抓到。

**Step 3：加 load/store 的 sign/zero extend 測試。**
`sb` 一個 `0xff` 到某位址、`lb` 讀回應是 `-1`（0xffffffff）、`lbu` 讀回應是 `255`。同一 byte 兩種延伸結果相反，是 dmem 最容易接反的地方。

**Step 4：加分支的 taken/not-taken 與 BLT 負數。**
BEQ 相等要 taken、一個明確 not-taken 的分支（如 `blt x2,x1` 當 x2<x1 不成立）確認「不跳時走 pc+4」、以及 **BLT 用 `(-1, 1)`** 驗有號分支。這步專打 branch_unit 的 `$signed()` 坑——這正是費氏數列漏掉、而反向驗證要靠它抓 bug 的關鍵。

**Step 5：加 JAL/JALR 呼叫返回，然後收尾接上 pass。**
`jal x7, subr` 跳進子程式、`jalr x0, 0(x7)` 返回、返回後執行一條標記指令確認真的回來了。全部串好後最後跳 `pass`。跑出 `PASSED`，再做反向驗證（植 bug 確認抓得到）。

每步組譯後跑一次，`PASSED` 才往下加。這樣出錯時你知道是剛加的那段有問題，不會一次面對一大坨。

### Step 1 完成長這樣（先把它跑通再往下）

別急著往下讀參考解。Step 1 的骨架只有一個 test，短到能整支貼出來，先讓它在你的 core 上跑出 `PASSED`，把「pass/fail 機制 + tb 讀 mem[0]」這條路徑打通。`skel.S`：

```asm
    .section .text
    .globl _start
_start:
    addi x1, x0, 20
    addi x2, x0, 22
    add  x3, x1, x2          # 42
    addi x4, x0, 42
    bne  x3, x4, fail1       # 不相等就跳去 fail1
pass:
    addi x5, x0, 0
    sw   x0, 0(x5)           # mem[0] = 0 => PASS
    jal  x0, halt
fail1:
    addi x3, x0, 1
    addi x5, x0, 0
    sw   x3, 0(x5)           # mem[0] = 1
halt:
    jal  x0, halt            # 原地自旋，等 tb 收尾
```

配 Ch 12 那支「跑到 PC 自旋就停、讀 mem[0]」的 tb（下面參考解裡的 `selftest_tb.cpp` 直接可用）。組譯灌進去跑：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib \
    -Ttext=0x80000000 -o skel.elf skel.S
riscv64-unknown-elf-objcopy -O binary skel.elf skel.bin
python3 -c '
import struct
d = open("skel.bin","rb").read()
w = [struct.unpack("<I", d[i:i+4])[0] for i in range(0, len(d), 4)]
open("prog.hex","w").write("\n".join("%08x" % x for x in w) + "\n")
'
python3 -c 'open("data.hex","w").write(("00000000\n")*256)'
./obj_dir/Vcore   # 用你 Ch 12 建好的 core
```

真實輸出：

```
SELFTEST PASSED (mem[0]=0)
```

骨架通了，代表 `add`、`bne`、`sw`、`jal` 這條主幹在你的 core 上沒問題，pass/fail 標記也寫得進 mem[0]、tb 讀得到。接下來每加一個 test，就是在 `bne x3,x4,fail1` 之後複製「算→比→跳 failN」的模式，並在下面補一個 `failN` 標籤。骨架不動，只往中間長。

一個立即能做的自我測試：把 `addi x4, x0, 42` 故意改成 `addi x4, x0, 41`（預期值寫錯），重跑應該得 `FAILED at test #1`。確認「錯了會 FAIL、對了才 PASS」這個機制真的在運作——一個永遠 PASS 的框架是騙自己的。

## 參考解

先自己做。卡住再看。這份參考解在 verilator 4.038 上真跑過：正常 core 得 `PASSED`，植入 BLT 有號 bug 後得 `FAILED at test #7`。

<details>
<summary>完整參考解（selftest.S + selftest_tb.cpp + 執行命令 + 真實輸出）</summary>

### selftest.S

```asm
    .section .text
    .globl _start
# 自檢測試：每項算出結果與預期比對，錯就把「失敗編號」寫到 mem[0] 並停。
# 全部通過則把 0 寫到 mem[0]。慣例：mem[0]=0 表示 PASS，非 0 表示第幾項 fail。
_start:
    # ---- test 1: ADD ----
    addi x1, x0, 20
    addi x2, x0, 22
    add  x3, x1, x2          # 42
    addi x4, x0, 42
    bne  x3, x4, fail1
    # ---- test 2: SUB ----
    sub  x3, x2, x1          # 22-20 = 2
    addi x4, x0, 2
    bne  x3, x4, fail2
    # ---- test 3: AND/OR/XOR ----
    addi x1, x0, 0x0F
    addi x2, x0, 0x33
    and  x3, x1, x2          # 0x03
    addi x4, x0, 0x03
    bne  x3, x4, fail3
    or   x3, x1, x2          # 0x3F
    addi x4, x0, 0x3F
    bne  x3, x4, fail3
    xor  x3, x1, x2          # 0x3C
    addi x4, x0, 0x3C
    bne  x3, x4, fail3
    # ---- test 4: SLT (signed) / SLTU (unsigned) ----
    addi x1, x0, -1
    addi x2, x0, 1
    slt  x3, x1, x2          # -1 < 1 => 1
    addi x4, x0, 1
    bne  x3, x4, fail4
    sltu x3, x1, x2          # unsigned big < 1 => 0
    bne  x3, x0, fail4
    # ---- test 5: shifts (SLL / SRA sign) ----
    addi x1, x0, 1
    addi x2, x0, 4
    sll  x3, x1, x2          # 16
    addi x4, x0, 16
    bne  x3, x4, fail5
    addi x1, x0, -16         # 0xfffffff0
    addi x2, x0, 2
    sra  x3, x1, x2          # -4 = 0xfffffffc（算術右移補符號）
    addi x4, x0, -4
    bne  x3, x4, fail5
    # ---- test 6: load/store byte sign vs zero extend ----
    addi x1, x0, -1          # 0xffffffff
    addi x5, x0, 64
    sb   x1, 0(x5)           # mem[64] byte0 = 0xff
    lb   x3, 0(x5)           # sign ext => 0xffffffff = -1
    addi x4, x0, -1
    bne  x3, x4, fail6
    lbu  x3, 0(x5)           # zero ext => 0xff = 255
    addi x4, x0, 255
    bne  x3, x4, fail6
    # ---- test 7: branch taken / not-taken ----
    addi x1, x0, 5
    addi x2, x0, 5
    beq  x1, x2, b_ok        # equal => take
    jal  x0, fail7
b_ok:
    blt  x2, x1, fail7       # 5<5 false => not take, good
    # ---- test 7b: BLT signed with negative（費氏數列漏掉的坑）----
    addi x1, x0, -1          # 0xffffffff
    addi x2, x0, 1
    blt  x1, x2, b7_ok       # signed -1 < 1 => take
    jal  x0, fail7
b7_ok:
    bltu x1, x2, fail7       # unsigned big < 1 => false => not take, good
    # ---- test 8: JAL / JALR call-return ----
    jal  x7, subr            # x7 = 返回位址
    addi x8, x0, 1           # 返回後執行；x8 標記「回來了」
    bne  x8, x8, fail8       # x8==x8 恆真，不跳；確認執行到這
    jal  x0, pass
subr:
    jalr x0, 0(x7)           # 返回到 jal 的下一條

pass:
    addi x5, x0, 0
    sw   x0, 0(x5)           # mem[0] = 0 => PASS
    jal  x0, halt
fail1:
    addi x3, x0, 1
    jal  x0, report
fail2:
    addi x3, x0, 2
    jal  x0, report
fail3:
    addi x3, x0, 3
    jal  x0, report
fail4:
    addi x3, x0, 4
    jal  x0, report
fail5:
    addi x3, x0, 5
    jal  x0, report
fail6:
    addi x3, x0, 6
    jal  x0, report
fail7:
    addi x3, x0, 7
    jal  x0, report
fail8:
    addi x3, x0, 8
    jal  x0, report
report:
    addi x5, x0, 0
    sw   x3, 0(x5)           # mem[0] = 失敗編號
halt:
    jal  x0, halt
```

> 註：test 7 和 7b 都跳 `fail7`（共用編號 7），因為兩者都是「分支條件判斷」的失敗，報 #7 已足以定位到分支邏輯。你也可以拆成 7 和 9 更精細。

### selftest_tb.cpp

```cpp
#include "Vcore.h"
#include "Vcore_core.h"
#include "Vcore_dmem.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vcore *dut;
static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vcore;
    dut->rst = 1; tick(); dut->rst = 0; dut->eval();
    // 跑到程式進入 halt 自旋（PC 連續幾個 cycle 不變）或上限
    uint32_t prev_pc = 0; int stable = 0;
    for (int i = 0; i < 500; i++) {
        tick(); dut->eval();
        if (dut->pc == prev_pc) { if (++stable > 3) break; }
        else stable = 0;
        prev_pc = dut->pc;
    }
    uint32_t result = dut->core->u_dmem->mem[0];
    if (result == 0) printf("SELFTEST PASSED (mem[0]=0)\n");
    else             printf("SELFTEST FAILED at test #%u (mem[0]=%u)\n", result, result);
    delete dut;
    return result == 0 ? 0 : 1;
}
```

### 執行命令

```bash
# 1. 組譯 selftest.S 並轉 hex（Ch 7 流程）
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib \
    -Ttext=0x80000000 -o selftest.elf selftest.S
riscv64-unknown-elf-objcopy -O binary selftest.elf selftest.bin
python3 -c '
import struct
d = open("selftest.bin","rb").read()
w = [struct.unpack("<I", d[i:i+4])[0] for i in range(0, len(d), 4)]
open("prog.hex","w").write("\n".join("%08x" % x for x in w) + "\n")
'
# 2. dmem 初值全 0
python3 -c 'open("data.hex","w").write(("00000000\n")*256)'
# 3. 建置並跑
verilator --cc core.sv control_unit.sv imm_gen.sv regfile.sv alu.sv \
    dmem.sv branch_unit.sv --top-module core --exe selftest_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vcore.mk Vcore
./obj_dir/Vcore
```

### 真實輸出（正常 core）

```
SELFTEST PASSED (mem[0]=0)
```

### 反向驗證：植入 bug 確認測試抓得到

把 branch_unit 的有號比較改成無號（模擬「BLT 忘了 `$signed()`」的坑）：

```bash
# 暫時把 branch_unit.sv 的 $signed(rs1) < $signed(rs2) 改成 rs1 < rs2
sed -i 's/\$signed(rs1) < \$signed(rs2)/rs1 < rs2/' branch_unit.sv
verilator --cc core.sv control_unit.sv imm_gen.sv regfile.sv alu.sv \
    dmem.sv branch_unit.sv --top-module core --exe selftest_tb.cpp --Mdir obj_bad
make -C obj_bad -f Vcore.mk Vcore
./obj_bad/Vcore
```

真實輸出（壞掉的 core）：

```
SELFTEST FAILED at test #7 (mem[0]=7)
```

`test #7` 正是 BLT 負數那項——測試組準確抓到並定位了 bug。這證明測試**有覆蓋到**有號分支的坑（費氏數列抓不到這個）。改回 `$signed()` 後又恢復 `PASSED`。這個「壞了會 FAIL、且指出哪裡」的性質，才是自檢測試的價值。

</details>

## 卡點提示

- **提示 1：`bne x3, x4` 跳去 failN，但 failN 太遠，組譯器報 relocation 錯。** B-type 分支的範圍是 ±4KiB。若你的測試很長、failN 在最後面，`bne x3,x4,fail8` 可能超出範圍。解法：把「比對失敗就跳」拆成「先 `beq` 跳過附近的 `jal failN`」——`beq` 條件反過來跳過一條無條件 `jal`（`jal` 範圍 ±1MiB 大得多）。或把 failN 標籤放靠近測試處。本參考解的測試夠短沒踩到，但你加更多測試時要留意。

- **提示 2：tb 跑完 dump mem[0] 卻是 X 或亂值。** 兩個常見原因：(a) `data.hex` 沒建或行數不足，dmem 初值是 X——先 `python3 -c 'open("data.hex","w").write(("00000000\n")*256)'`。(b) 程式還沒跑到寫 mem[0] 就被 tb 停了——確認 cycle 數夠、或用「PC 自旋偵測」（本 tb 的做法）等程式真的進 halt 才讀。

- **提示 3：反向驗證植了 bug 卻還是 PASS。** 代表你的測試沒覆蓋到那個 bug 影響的路徑。例如把 BLT 改無號但你的測試只用正數比較——正數的有號無號結果相同，抓不到。**每個坑都要用會讓有號/無號結果相反的輸入**（如 `(-1, 1)`）。這也是本練習要你「用負數」的原因。測不出 bug 的測試等於沒測。

- **提示 4：`sw x0, 0(x5)` 寫 mem[0] 但 x5 不是 0。** pass/report 寫 mem[0] 前要確定位址暫存器是 0（`addi x5, x0, 0`）。若前面測試把 x5 改成別的值（本參考解 test 6 用 x5=64 存 byte），寫 mem[0] 前務必重設 `x5=0`，否則 PASS/FAIL 標記寫到錯位址，tb 讀 mem[0] 永遠是舊值。

- **提示 5：JALR 返回後沒接對，程式跑飛。** `jal x7, subr` 後緊接的那條指令位址要正好是 `jalr x0, 0(x7)` 會跳回的地方（pc+4）。若你的 core JALR target 沒清最低位（`& ~1`）或算錯，會跳到別處。先用 Ch 12 範例 3 的 t2 程式確認 JAL/JALR 往返沒問題，再加進 selftest。

## 延伸挑戰

做完基本要求後，這些能把你的驗證推得更深：

- **接真正的 riscv-tests rv32ui**：clone [riscv-tests](https://github.com/riscv-software-src/riscv-tests)，它的 `isa/rv32ui/*.S` 每個檔測一條指令的完整 case（用 `TEST_RR_OP` 之類巨集）。挑戰把 `add.S`、`sub.S`、`sltu.S` 這幾個編出來（要處理它的 `riscv_test.h` 環境與 tohost 慣例），灌進你的 core 跑。這是業界標準測試組，跑通它含金量遠高於自寫的幾項。你會遇到 tohost/fromhost 機制、trap handler 等本課還沒做的東西，要做些裁剪——過程本身很有學習價值。

- **加一個「指令計數器」到 tb**：每 tick 累加、程式停時印出總共執行幾條指令。這是效能分析（Part 3）的起點——之後你能算 CPI（cycles per instruction，單週期恆為 1）、對照 pipeline 版的差異。

- **測 x0 恆 0 的硬體保證**：加一項 `addi x0, x0, 5` 然後讀 x0 應該還是 0（`bne x0, x_zero, failN`）。驗證你的 regfile 真的攔住了對 x0 的寫入——這是 Ch 8 的硬接 0，值得在整合層再確認一次。

- **測 ADD 溢位回繞**：`addi x1, x0, -1`（0xffffffff）+ `addi x2, x0, 1`，`add x3, x1, x2` 應得 0（截 32-bit 回繞，Ch 9 的 ADDwrap）。驗 RV 的靜默溢位語意在 core 裡也對。

- **把測試組變成 regression script**：寫一支 shell script 自動「組譯 → 建置 → 跑 → 檢查 exit code」，每次改 core 就跑它。再進一步：植入一系列已知 bug（SRA→SRL、BLT 去 signed、writeback 漏 jump...），確認每個都被對應 test 抓到——這是在建你自己的「測試覆蓋率」信心。真 CPU 團隊就是這樣守護 RTL 的。

## 本練習重點整理

- 自檢測試的核心手法：**算結果 → 和預期比對 → 不同就跳失敗處理 → 記編號寫進約定位址**。tb 只讀一個值判 pass/fail，免人工看 dump。
- pass/fail 約定：`mem[0]=0` 為 PASS、非 0 為第幾項 fail（貼近 riscv-tests 的 tohost 精神）。
- **覆蓋各類指令 + 會踩坑的邊界**：SLT/SLTU 用負數大數、SRA 用負數、LB/LBU 同 byte 兩種延伸、BLT 用負數（有號分支的坑）、JAL/JALR 往返。
- **反向驗證是驗收關鍵**：植入 bug（如 BLT 去 `$signed()`）必須被測試組抓到並報出 test #N。抓不到代表覆蓋不足——費氏數列就抓不到 BLT 負數坑，這正是要補的。
- 分五步實作：骨架 → 比較/移位 → load/store 延伸 → 分支 → JAL/JALR，每步跑 PASS 才往下。

## 自我檢核

- [ ] 我能說明自檢測試「不靠人眼看 dump」的機制，以及 pass/fail 約定怎麼設計。
- [ ] 我的測試組涵蓋了算術/邏輯/比較/移位/load-store/branch/jump 每一類，且比較和移位用了負數。
- [ ] 我能解釋為什麼費氏數列驗過的 core，仍可能 BLT 負數判反——以及我的測試怎麼補上這個。
- [ ] 我做了反向驗證：植入至少一個 bug，確認測試組報出對應的 test #N。
- [ ] 我知道 B-type 分支範圍 ±4KiB，測試變長時失敗跳轉可能超範圍該怎麼繞。
- [ ] 我能說出至少兩個「測試植了 bug 卻沒抓到」的原因（輸入沒用負數、位址暫存器沒重設）。

## 延伸閱讀

- **[riscv-tests repo](https://github.com/riscv-software-src/riscv-tests) 的 `isa/rv32ui/` 與 `env/`**：官方 RV32I 測試組。先看 `rv32ui/add.S` 理解 `TEST_RR_OP` 巨集怎麼展開成「載入輸入→運算→比對→失敗記號」，再看 `env/p/riscv_test.h` 的 tohost 收尾機制。本練習的自檢手法就是它的簡化版；接它是延伸挑戰的正題。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.4 節末的 datapath 驗證討論**：P&H 談單週期實作正確性時，強調要對「每類指令走的資料路徑」逐一確認——這正是自檢測試分類覆蓋的理論依據。對照你的測試分類是否漏了哪條路徑。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.3 節末的 testbench 討論與第 4 章 SystemVerilog testbench**：Harris 有專門講怎麼為處理器寫 self-checking testbench、怎麼設計測試向量涵蓋邊界。它的「directed test vs random test」討論能幫你想「除了手挑 case，還能怎麼測得更全」。
- **[picorv32 repo](https://github.com/YosysHQ/picorv32) 的 `testbench.v` 與 `firmware/`**：看一個真 core 怎麼被測——它的 firmware 跑一堆 C 程式和 riscv-tests，testbench 監看 tohost/特殊位址判斷結果。對照你的自檢框架，能看出「教學級單指令自檢」到「跑整個 firmware regression」的規模差距，以及真 core 驗證的日常長什麼樣。

打穿這組測試後，你的單週期 RV32I core 不只「跑得動」，而是對每類指令都經過驗證、還有一套能持續守護它的 regression 測試。Part 1 到此完整收尾——你從數位邏輯地基一路做到一顆可驗證的 CPU。接下來 Part 2 會把這顆單週期 core 改造成 pipeline，用你剛建的自檢測試當黃金標準對照，確認改造沒破壞任何指令的正確性。你現在手上這組測試，會是那時最可靠的安全網。

→ [Ch 13 為什麼要 pipeline：throughput vs latency](./13-why-pipeline.md)
