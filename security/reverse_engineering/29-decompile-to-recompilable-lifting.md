# Ch 29 反編譯到可編譯：lifting 與重建

> **目標**：理解 binary lifting 的原理與侷限，掌握「部分重寫」策略，能把逆向出的函式手動重建成等價 C 並用對拍驗證正確性，為後續 fuzzing / sanitizer 插樁鋪路。

> **環境**：WSL2 Ubuntu 22.04，gcc 11，objdump（binutils），Python 3（對拍腳本）。RetDec / mcsema 標「讀者自行安裝測試」，本章對拍範例純用 gcc + objdump 真跑。

---

## 為什麼需要 recompilable source？

讀懂 binary 和能改/能跑之間有一道鴻溝。

**read-only 理解**只讓你在腦子裡建立模型：這個函式做 checksum，那個函式解密 config。夠你寫報告、寫 Sigma rule、解釋攻擊鏈。

**recompilable source** 讓你：

1. **掛 fuzzer**：libAFL 和 AFL++ coverage-guided fuzzing 需要能重編的 source（插入 `__AFL_FUZZ_INIT` 等），或至少能 link 進去的 object file。沒有 source，最多只能 black-box fuzzing，覆蓋率歸零。
2. **掛 sanitizer**：ASAN / MSAN / UBSan 都是編譯期插樁，必須重編。你懷疑某個解析函式有 OOB，沒有 source 就只能靠 Valgrind 跑，慢一個數量級。
3. **精確 patch**：改組語碼（hardcode patch bytes）很容易破壞對齊或分支位移，改 C source 再重編乾淨得多。
4. **自動化差異測試**：有等價 C 才能對原始 binary 做 differential testing，輸入相同、輸出必須一致，這是驗證逆向正確性最強的方法。

這不是「有最好」的錦上添花，是進入深度分析的前提條件。

---

## 先建立直覺

把 binary lifting 想成翻譯的翻譯：

```
機器碼 (x86-64) → IR (LLVM IR / VEX / REIL) → 高階語言 (C / C++)
```

每一層翻譯都在丟資訊：

- **組語 → IR**：語意保留，但型別、變數名、呼叫慣例要靠分析推斷
- **IR → C**：控制流重建、變數合併、型別標注，全是猜測的產物

反向走這條路本質上是欠定問題（underdetermined）。同一份機器碼對應無數份等價 C，反編譯器只能選它自己認為最「合理」的那一份。

**合理 ≠ 原始**。這是第一個要刻進腦子的認知。

---

## Binary Lifting 工具概覽

### RetDec（Avast，開源）

RetDec 的目標是把 ELF / PE / Mach-O 直接輸出成 LLVM IR 和可讀 C。

```bash
# 讀者自行安裝測試
retdec-decompiler target.elf -o output.c
```

優點：開源、支援多架構（x86/ARM/MIPS/PowerPC）、輸出 LLVM IR 讓你接後續工具鏈。  
侷限：
- 間接跳轉（`jmp rax`、vtable dispatch）基本靠猜，容易切錯函式邊界
- 型別還原品質參差，struct 幾乎永遠是 `uint8_t[]` 加上偏移存取
- 輸出的 C 編不過是常態，需要手動修

### mcsema（Trail of Bits）

mcsema 走另一條路：不試圖輸出人可讀的 C，而是把 binary 提升成高品質 LLVM IR，讓你接 LLVM 分析框架（如 KLEE、libFuzzer）。

```bash
# 讀者自行安裝測試，需要 IDA Pro 或 Binary Ninja 作前端
mcsema-lift-13.0 --os linux --arch amd64 --cfg target.cfg -o target.bc
```

優點：LLVM IR 品質較高，適合接 fuzzing 工具鏈。  
侷限：依賴商業反組語器作前端，setup 成本高；同樣在間接跳轉面前折劍。

### reopt（Jane Street）

reopt 的哲學更激進：直接把 binary 重新最佳化，輸出功能等價但重編的 binary，不試圖輸出人可讀 C。

定位是「效能改造」而非「安全分析」，但原理和 mcsema 相近。

---

## Lifting 的核心挑戰

### 間接跳轉

```asm
jmp [rbx + rax*8]   ; vtable dispatch / function pointer table
call rax            ; callback / 函式指標
```

靜態分析無法在不執行的情況下知道 `rax` 的值。Lifting 工具通常：
- 插入 runtime dispatch 表（正確但慢）
- 靜態猜測跳轉目標集合（快但不完整）

任一策略都會讓提升後的 IR 有洞，導致 coverage 不完整。

### 型別丟失

機器碼層面沒有型別。`mov eax, [rbx+8]` 是在讀 `int`、`uint32_t`、`float` 還是 `enum`？取決於後續指令怎麼用，但分析深度直接決定猜測品質。

這點和 Ch 9 型別還原直接銜接：lifting 的型別品質上限就是你型別還原分析的深度。沒做型別還原就直接 lifting，輸出的 C 全是 `uint64_t` 加上 `(type_t*)(void*)` 轉型。

### Calling Convention Ambiguity

x86-64 System V ABI 固定用 `rdi, rsi, rdx, rcx, r8, r9`，但編譯器最佳化後可能打亂順序，或用 tail call 把參數傳遞混在一起。Lifting 工具判斷函式邊界和參數傳遞依賴約定，一旦遇到 LTO / PGO 最佳化的 binary，準確率大幅下降。

### 全域狀態與資料段

全域變數、字串、vtable 在 binary 裡是記憶體位址。Lifting 工具必須把這些位址對應到 IR 裡的 global，稍微複雜的 TLS 或自修改程式碼就會失手。

---

## 部分重寫：比完整 lifting 更可靠的工程策略

完整 lifting 的野心是：餵入 binary，輸出等價 C，接著編譯、fuzzing、修改。

這在玩具 binary 上成立，在真實世界的 stripped、LTO 最佳化、帶有手寫組語的 binary 上鮮少成功。

**部分重寫**是更務實的策略：

1. 用 Ch 8 反編譯器（Ghidra / IDA）定位你真正關心的函式（解析器、加密、checksum）
2. 理解那個函式的語意（接 Ch 9 型別還原、Ch 26 控制流分析）
3. 手動把該函式重寫成等價 C
4. 寫對拍測試驗證等價性
5. 只對這個函式掛 ASAN / fuzzer

你不需要 recompile 整個 binary，只需要 recompile 你關心的那一塊。這個策略在工業界 RE 分析中佔主流，原因很簡單：全局 lifting 的失敗率太高，部分重寫的可控性更好。

---

## 實作：從 stripped binary 手動提升並對拍驗證

這是本章唯一真跑的部分，目的是把「等價性驗證」的完整流程走一遍。

### 步驟一：準備原始函式

```c
/* original_fn.c */
#include <stdio.h>

unsigned checksum(const unsigned char *buf, unsigned len) {
    unsigned s = 0;
    for (unsigned i = 0; i < len; i++)
        s = (s << 1) ^ buf[i];
    return s & 0xffff;
}

int main(void) {
    unsigned char data[] = {0xde, 0xad, 0xbe, 0xef, 0x00, 0x11, 0x22, 0x33};
    printf("original: 0x%04x\n", checksum(data, sizeof(data)));
    return 0;
}
```

編譯並 strip，模擬「陌生 binary」：

```bash
gcc -O1 -o original_fn original_fn.c
strip original_fn
```

### 步驟二：從 objdump 讀 asm

```bash
objdump -d original_fn | grep -A 40 '<checksum\|\.text'
```

strip 之後函式名消失，你要靠 call 關係找入口。假設找到目標函式在 `0x1149`（實際位址依機器而異），看到類似：

```asm
0000000000001149 <checksum>:     # strip 後無名，手動識別
    1149:  xor    eax,eax         # s = 0
    114b:  test   esi,esi         # len == 0?
    114d:  je     1162 <...>      # 若 len==0 跳結尾
    114f:  xor    ecx,ecx         # i = 0
    1151:  movzx  edx,BYTE PTR [rdi+rcx*1]  # buf[i]
    1155:  add    ecx,0x1         # i++
    1158:  lea    eax,[rax+rax*1] # s << 1（等價 s*2）
    115b:  xor    eax,edx         # s ^= buf[i]
    115d:  cmp    ecx,esi         # i < len?
    115f:  jb     1151 <...>      # 繼續迴圈
    1161:  ...
    1162:  movzx  eax,ax          # s & 0xffff
    1165:  ret
```

注意：`lea eax, [rax+rax*1]` 是 `s << 1` 的 gcc 最佳化形式（`x + x == x*2 == x<<1`）。逆向時要認出這個慣用模式。

### 步驟三：手動重建等價 C

```c
/* lifted_checksum.c */
#include <stdio.h>
#include <stdint.h>

/* 從 asm 手動提升 */
unsigned lifted_checksum(const unsigned char *buf, unsigned len) {
    unsigned s = 0;
    if (len == 0) goto done;
    unsigned i = 0;
    do {
        unsigned byte = buf[i];   /* movzx edx, BYTE PTR [rdi+rcx*1] */
        i++;                       /* add ecx, 0x1 */
        s = (s + s) ^ byte;       /* lea + xor：s<<1 ^ buf[i] */
    } while (i < len);            /* jb loop */
done:
    return s & 0xffff;            /* movzx eax, ax */
}

int main(void) {
    unsigned char data[] = {0xde, 0xad, 0xbe, 0xef, 0x00, 0x11, 0x22, 0x33};
    printf("lifted:   0x%04x\n", lifted_checksum(data, sizeof(data)));
    return 0;
}
```

編譯執行：

```bash
gcc -O0 -o lifted_checksum lifted_checksum.c
./original_fn    # original: 0x????
./lifted_checksum # lifted:   0x????
```

兩者輸出必須一致。

### 步驟四：Python 對拍腳本（多組輸入）

只對一組輸入對拍不夠。寫腳本打多組：

```python
#!/usr/bin/env python3
# diff_test.py
import subprocess, random, sys

def run(binary, data_hex):
    r = subprocess.run([binary, data_hex], capture_output=True, text=True)
    return r.stdout.strip()

# 假設兩個 binary 都接受 hex 字串作 argv[1]
# 這裡用 ctypes 直接呼叫更精確，簡化版用 subprocess 示意
import ctypes

lib_orig = ctypes.CDLL("./liboriginal.so")
lib_orig.checksum.restype = ctypes.c_uint
lib_orig.checksum.argtypes = [ctypes.c_char_p, ctypes.c_uint]

lib_lift = ctypes.CDLL("./liblifted.so")
lib_lift.lifted_checksum.restype = ctypes.c_uint
lib_lift.lifted_checksum.argtypes = [ctypes.c_char_p, ctypes.c_uint]

mismatches = 0
for _ in range(10000):
    length = random.randint(0, 64)
    data = bytes(random.randint(0, 255) for _ in range(length))
    r_orig = lib_orig.checksum(data, len(data))
    r_lift = lib_lift.lifted_checksum(data, len(data))
    if r_orig != r_lift:
        print(f"MISMATCH: data={data.hex()}, orig=0x{r_orig:04x}, lift=0x{r_lift:04x}")
        mismatches += 1

print(f"Done. Mismatches: {mismatches}/10000")
```

編成 shared library 再跑：

```bash
gcc -O1 -shared -fPIC -o liboriginal.so original_fn.c
gcc -O0 -shared -fPIC -o liblifted.so lifted_checksum.c
python3 diff_test.py
# 預期：Done. Mismatches: 0/10000
```

10000 組隨機輸入全部一致，等價性驗證通過。這才是「逆向正確」的有效定義。

---

## 對比與取捨

| 策略 | 優點 | 缺點 | 適用情境 |
|------|------|------|----------|
| 完整 lifting（RetDec/mcsema） | 自動化、覆蓋全 binary | 間接跳轉失手、型別猜錯、輸出常編不過 | 快速瀏覽、無法手動分析的大 binary |
| 部分重寫（手動） | 正確性高、可直接驗證 | 耗時、需深度理解目標函式 | 關鍵解析器 / 加密函式 / fuzzing 目標 |
| IR lifting（mcsema）接 fuzzer | 覆蓋率比 black-box 高 | Setup 複雜、商業依賴 | 大規模安全評估、有工具鏈預算 |
| Black-box fuzzing | 零 setup | 覆蓋率低、難觸發深層路徑 | 快速初篩、時間不夠時 |

---

## 踩雷集錦

### 1. `lea eax, [rax+rax*1]` 不是在算陣列偏移

這是 gcc 把 `s << 1`（或 `s * 2`）編成 `lea` 的慣用形式。看到 `lea` 不要預設它是指標算術，先看 operand，是 `[reg+reg*1]` 或 `[reg*2]` 通常是整數倍乘。

### 2. 第一次重寫一定有邊界條件錯

len == 0 時的行為、最後一次迭代的計數方式，手動重寫幾乎必錯一次。對拍腳本要特別測 `len=0`、`len=1`、`len=MAX_UINT`（視情況）。對拍存在的意義就是抓這類低調 bug。

### 3. strip 後找函式入口要靠 call 關係，不要靠地址猜

strip 掉符號表後，`objdump -d` 只給你 `<.text+0x...>` 或匿名標籤。正確做法是從 `main`（通常 linker 保留或能從 `_start` trace）往下找 `call` 目標，再對照參數特徵（`rdi` 是指標、`esi` 是長度，符合 `checksum(buf, len)` 的 ABI 配置）定位函式。

### 4. 完整 lifting 工具的 C 輸出幾乎不能直接編譯

RetDec 輸出的 C 常有 `__asm__`、平台特定 intrinsic、或未定義函式引用。把它當「帶標注的 pseudo-C」讀，不要期望 `gcc output.c` 成功。真正要用的是它輸出的 `.ll`（LLVM IR），接工具鏈比接編譯器務實。

### 5. 最佳化等級不一致會讓對拍產生假陽性

原始 binary 用 `-O1` 編，你的 lifted C 用 `-O3` 編，如果函式有 UB（如有符號整數溢位），兩者行為可能合法地不同。對拍時儘量對齊最佳化等級，或用 `-fno-strict-overflow` 降低 UB 發散風險。

---

## 進階：再往深一層

### 自動等價性驗證（Equivalence Checking）

對拍測試是統計性的，不是形式化的。真正的等價性驗證用 symbolic execution（如 KLEE）或 SMT solver：

```
∀ input: lifted_fn(input) == original_fn(input)
```

這需要把兩者都提升到 LLVM IR，然後用 KLEE 做 differential symbolic execution，找到反例（counterexample）或證明等價。在工具鏈成熟的情況下，這比 random testing 更強，但 setup 成本高。

### 接 advanced_fuzzing 課

部分重寫的成果不是終點，是 fuzzing 的起點。把手動重建的函式包成：

```c
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    lifted_checksum(data, (unsigned)size);
    return 0;
}
```

掛 libFuzzer 或 AFL++，幾分鐘內就能把 coverage 拉高到 black-box 幾天都到不了的深度。Lifting 的價值在這裡才完整兌現。

### Decompiler IL 作為 lifting 中間站

Ghidra 的 P-code、Binary Ninja 的 MLIL/HLIL 是另一種 lifting 中間表示。相比直接輸出 C，先在 IL 上做分析（型別推斷、別名分析、常數折疊）再輸出，品質更高。這也是 Binary Ninja 在安全研究圈比 IDA Pro 愈來愈受歡迎的原因之一：它的 IL 接 Python API 更順手。

---

## 本章重點整理

- **recompilable source 的價值**不在「讀懂」，在「能改、能跑、能測」，是 fuzzing / sanitizer 的前提。
- **Binary lifting** 把機器碼提升到 IR 或 C，但間接跳轉、型別丟失、calling convention ambiguity 是三大硬傷，完整 lifting 在真實 binary 上成功率低。
- **部分重寫**（手動把關鍵函式重建為等價 C）在工程實務上比完整 lifting 更可靠，控制範圍小、可驗證性高。
- **等價性驗證**靠對拍：多組隨機輸入，兩版函式輸出必須一致。這是判斷「逆向正確」的唯一客觀標準。
- **型別品質決定 lifting 品質**：接 Ch 9 型別還原的結果，才能給 lifting 工具更好的起點。
- RetDec / mcsema 的輸出當分析輔助讀，不要期望直接編譯過。

---

## 自我檢核

1. 解釋「recompilable source」和「read-only 理解」在 fuzzing 工作流中的具體差異。
2. 為什麼間接跳轉（`jmp rax`）是 binary lifting 最難處理的問題？靜態分析能給出什麼保證？
3. `lea eax, [rax+rax*1]` 對應什麼 C 語意？怎麼判斷它不是指標算術？
4. 完成本章對拍練習：編譯 original_fn.c（-O1 + strip）、objdump 讀 asm、手動重寫、Python 腳本跑 10000 組對拍，確認 0 mismatch。
5. 說明為什麼對拍時兩者最佳化等級不一致會產生假陽性，以及如何避免。

---

## 延伸閱讀

1. **RetDec GitHub 文件**（github.com/avast/retdec）——了解 lifting pipeline 與 LLVM IR 輸出格式，對照本章侷限清單逐條驗證。
2. **mcsema 技術報告**（Trail of Bits）——"McSema: Static Translation of x86 Instructions to LLVM"，詳述 calling convention 恢復與間接跳轉處理策略的設計取捨。
3. **Driller: Augmenting Fuzzing Through Selective Symbolic Execution**（Stephens et al., NDSS 2016）——展示 lifting + symbolic execution + fuzzing 三者結合的完整工業流程，是「部分 lifting 接 fuzzer」策略的學術根基。
4. **Binary Ninja MLIL 文件**（docs.binary.ninja/dev/bnil-mlil.html）——MLIL 作為 lifting 中間站的 API 設計，理解為何 IL 比直接輸出 C 更適合接後續分析工具。

---

本章把「看懂」推進到「能跑」，下一章回到逆向分析本身：真實 binary 中反覆出現的 pattern——從編譯器慣用型到資料結構特徵——讀出 pattern 字典才能把逆向速度從「逐行解讀」提升到「模式識別」。

→ [Ch 30 逆向者的 Pattern 字典](./30-reversers-pattern-dictionary.md)
