# Ch 23 — 認出並對抗混淆 / anti-reversing

> **目標**：從防禦/分析視角理解常見混淆手法和 anti-reversing 技術——認出它們是第一步，有系統地繞過或對抗它們是第二步。本章全程維持「分析與防禦理解」定位：理解機制，用於識別惡意行為、繞過保護分析真實目標（受控/自有環境）。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb + strace + strings。

## 為什麼需要這個？

混淆和 anti-reversing 在兩個場景都出現：

1. **惡意程式分析**：malware 使用字串加密、加殼、anti-debug 讓分析員看不到真實行為——這是本章分析視角最重要的應用。接 `malware_analysis` 課。
2. **商業軟體保護**：DRM、授權驗證系統使用各種保護，安全研究員在授權評估或漏洞研究時需要繞過。

了解這些技術的運作，你才能：
- 在靜態分析時**辨識**出「這段 code 是字串解密，等動態跑出來再看」
- 在動態分析時**繞過** anti-debug 繼續調試
- 在報告中**正確描述**一個樣本的保護機制層級

## 先建立直覺：混淆的目標和層次

```
混淆/anti-reversing 的防禦目標

  靜態分析（不跑 binary）       動態分析（跑 binary）
  ──────────────────────       ────────────────────
  字串加密（strings 看不到）     anti-debug（偵測 gdb/ptrace）
  packer/加殼（entropy 高）      anti-VM（偵測虛擬機）
  控制流平坦化（CFG 亂）         timing check（測量 breakpoint 延遲）
  opaque predicate（假分支）     self-modifying code
  反反組譯（錯位指令）            

  應對：動態跑                  應對：patch / 符號執行
```

## Anti-Debug：識別與繞過

### ptrace(PTRACE_TRACEME) 檢查

最常見的 Linux anti-debug：一個 process 只能被一個 tracer attach，所以如果程式自己先 `ptrace(PTRACE_TRACEME)`，再有別人（gdb）來 attach 就會失敗。

真跑範例：

```c
/* /tmp/re_part3/antidebug.c */
#include <sys/ptrace.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static uint8_t enc_msg[] = {0x57^0xAA, 0x45^0xAA, 0x4C^0xAA, 0x43^0xAA,
                             0x4F^0xAA, 0x4D^0xAA, 0x45^0xAA, 0x00^0xAA};
/* "WELCOME" XOR 0xAA — strings 看不到明文 */

static void decrypt(uint8_t *buf, size_t len, uint8_t key) {
    for (size_t i = 0; i < len; i++) buf[i] ^= key;
}

int main(void) {
    if (ptrace(PTRACE_TRACEME, 0, 0, 0) == -1) {
        fprintf(stderr, "Debugger detected! Exiting.\n");
        exit(1);
    }
    decrypt(enc_msg, 7, 0xAA);
    printf("Secret: %s\n", enc_msg);
    return 0;
}
```

```bash
$ gcc -O0 -o /tmp/re_part3/antidebug /tmp/re_part3/antidebug.c

$ /tmp/re_part3/antidebug        # 正常執行
Secret: WELCOME

$ strace -e ptrace /tmp/re_part3/antidebug 2>&1
ptrace(PTRACE_TRACEME) = -1 EPERM (Operation not permitted)
Debugger detected! Exiting.     # ← strace 本身就是一個 tracer，所以 PTRACE_TRACEME 失敗
```

**識別 ptrace anti-debug 的 asm 特徵**：

```asm
; main 開頭
    mov    $0x0,%ecx          ; request=PTRACE_TRACEME=0
    mov    $0x0,%edx
    mov    $0x0,%esi
    mov    $0x0,%edi
    mov    $0x0,%eax
    call   1090 <ptrace@plt>  ; ← 看到 ptrace 呼叫
    cmp    $0xffffffffffffffff,%rax   ; 回傳 -1 = 被 trace
    jne    1252 <main+0x59>          ; ← 沒被 trace 就跳過退出邏輯
```

靜態辨識：`strings binary | grep ptrace` 或 `objdump | grep 'ptrace@plt'`。

**繞過方式 1：patch binary**

把 `jne` 改成 `jmp`（無條件跳轉），或把整個 ptrace 呼叫 nop 掉：

```bash
# 找到 jne 指令的 offset
objdump -d /tmp/re_part3/antidebug | grep -A 2 'cmp.*0xfffff' | head -5
# 輸出：
#   121f:  cmp    $0xffffffffffffffff,%rax
#   1223:  jne    1252 <main+0x59>

# jne = 0x75；改成 jmp = 0xeb（短跳轉保持 offset）
# 用 gdb patch：
```

**繞過方式 2：gdb 設斷點並改 return value**

```gdb
(gdb) break *0x121a         ; 在 call ptrace 後
(gdb) run
(gdb) set $rax = 0          ; 偽造 ptrace 成功（回傳 0 而非 -1）
(gdb) continue
```

**繞過方式 3：LD_PRELOAD hook（最乾淨）**

```c
/* ptrace_bypass.c */
#include <sys/ptrace.h>
long ptrace(int req, ...) { return 0; }  /* 永遠回傳成功 */
```

```bash
$ gcc -shared -fPIC -o ptrace_bypass.so ptrace_bypass.c
$ LD_PRELOAD=./ptrace_bypass.so /tmp/re_part3/antidebug
Secret: WELCOME   # anti-debug 被 hook 掉了
```

### 其他 anti-debug 手法

| 手法 | 識別方式 | 繞過方式 |
|---|---|---|
| `IsDebuggerPresent`（Windows）| `call IsDebuggerPresent; test eax,eax` | patch `xor eax,eax; ret` |
| `/proc/self/status` 讀 TracerPid | `open("/proc/self/status")` + 字串比較 | patch open 或 hook |
| Timing check（rdtsc 差值）| 兩個 `rdtsc` 指令中間的差值 > threshold | patch cmp threshold 為極大值 |
| Exception-based（SIGTRAP 自偵）| `int3` 後的 signal handler 邏輯 | gdb ignore SIGTRAP |

## 字串加密：識別與動態 dump

### 識別加密字串

`strings` 找不到明文字串，但看到：

1. `.rodata` 或 `.data` 裡有一塊 entropy 高的資料（不是可讀文字）
2. 程式開頭有一個函式把這塊資料 XOR 或 RC4 解密到 heap/stack
3. 之後才呼叫 `printf` 等使用這些字串

辨識工具：

```bash
# 計算 section entropy（自動工具）
$ python3 -c "
import math, struct
data = open('/tmp/re_part3/antidebug','rb').read()
counts = [0]*256
for b in data: counts[b] += 1
n = len(data)
h = -sum((c/n)*math.log2(c/n) for c in counts if c)
print(f'Binary entropy: {h:.2f} bits/byte')
"
```

典型 XOR 加密字串在 `.rodata` 裡看起來像「隨機 byte」，entropy 接近 8 bits/byte（而明文 ASCII 字串 entropy 約 4-5 bits）。

在本章範例的 `antidebug.c` 裡：

```bash
$ strings /tmp/re_part3/antidebug | grep -v GLIBC | grep -v '^\.'
# 看不到 "WELCOME"——enc_msg 是加密過的
# 但能看到：
Debugger detected! Exiting.    ← 錯誤訊息沒加密（省事寫法）
Secret: %s                     ← format string 也沒加密
```

這個對比本身就是線索：有些字串加密、有些沒有，說明作者選擇性地保護了重要字串。

### 動態 dump 解密字串

最直接的繞過：讓程式自己解密，然後攔截。

```bash
# 方法 1：strace 看 write/printf
$ strace -e write ./antidebug 2>&1 | grep 'WELCOME\|Secret'
# （需先繞過 anti-debug）

# 方法 2：gdb 在 printf 下斷，讀 rdi（format string）的後面
$ gdb antidebug
(gdb) break printf
(gdb) commands 1
> p (char*)$rsi    # 第二個 printf 參數（字串）
> continue
> end
```

方法 3：Frida hook（接 `android_reversing` / `ios_macos_exploitation` 的 Frida 章）：
```javascript
Interceptor.attach(Module.findExportByName(null, "printf"), {
    onEnter: function(args) {
        console.log("printf:", args[0].readCString(), args[1] ? args[1].readCString() : "");
    }
});
```

## Packer / 加殼：識別與 dump

### 識別 packed binary

Packed binary 的特徵：

1. **Section entropy 高**：`file` 說是 ELF，但 `.text` 的 entropy 接近 8 bits（正常 code 約 5-6 bits）。
2. **Section 名字怪**：UPX 會把 section 改名為 `UPX0`、`UPX1`；自訂 packer 可能叫 `.prot` 或隨機名。
3. **`strings` 幾乎沒有可讀內容**：packed binary 的明文被壓縮/加密了。
4. **Import 極少**：通常只有 `mmap`/`VirtualAlloc` + 少數系統 call，其餘 import 在 unpack 後動態 resolve。

```bash
# UPX 的識別（最常見的開源 packer）
$ strings packed_binary | grep UPX
# 或
$ file packed_binary  # 有些版本 file 能識別 UPX
```

### 動態 dump（unpacked 記憶體）

```
Packed binary 的執行流程

  disk binary（加密/壓縮）
        ↓
  stub（解包器）執行
        ↓
  解包到記憶體
        ↓
  跳到真正的 OEP（Original Entry Point）← 在這裡 dump
        ↓
  真實 code 執行
```

在 gdb 找 OEP（Original Entry Point）：

```gdb
; 在 _start 下 hardware watchpoint，等程式跳到解包後的位址
(gdb) watch *(void**)$rsp   ; 監視 stack 上的返回地址
(gdb) run
; 或者：
(gdb) catch syscall execve   ; 有些 packer 重新 execve 自己
```

更快的方式：在 `read`/`mprotect`/`mmap`（帶 exec 權限）系統呼叫後下斷，dump 剛 unpack 好的記憶體：

```bash
$ strace -e mmap,mprotect,write ./packed_binary 2>&1 | grep 'PROT_EXEC'
# 找到地址和大小，gdb 進去 dump
```

## 控制流平坦化（Control Flow Flattening）

### 識別

控制流平坦化把程式的所有 block 放到一個大 switch，用一個「state 變數」決定下一個 block：

```
正常 CFG：                   平坦化後的 CFG：

  A → B → C                     ┌→ dispatcher ←┐
      ↓               →          │   state == 1 → A
      D → E                      │   state == 2 → B   （state 在每個 block 結束時更新）
                                 │   state == 3 → C
                                 └─ state == 4 → D
```

逆向特徵：
- 一個巨大的 switch（多達幾百個 case）
- 每個 case 結尾會更新一個「magic 整數」（state 變數），然後 jmp 回 switch 頭
- CFG 視覺上看起來像一個輪子，中心是 dispatcher

### 對抗方式

最有效：**符號執行（symbolic execution）** 還原真實 CFG——接 `symex_taint` 課。用 angr 等工具符號執行，讓 state 變數的路徑約束求解，還原出真實的執行路徑，去掉 dispatcher 框架。

手動方式：找到 state 變數的初始值，逐一追蹤每個 case 的前後繼，重建真實 CFG。費時但可行。

## Opaque Predicate：假分支

Opaque predicate 是一個「看起來是條件分支，但實際上永遠走固定一條路」的假分支：

```c
/* 例子：x^2 + x 永遠是偶數（數學恆成立），所以 if 永遠 false */
if ((x*x + x) % 2 == 1) {   /* 永遠 false */
    /* dead code，用來混淆 CFG */
    bogus_function();
}
/* 真正的邏輯 */
real_function();
```

識別方式：這種分支在靜態分析時會讓 Ghidra/IDA 認為有兩條路徑，但動態執行時只走一條——用 coverage-based fuzzing 或動態追蹤看哪些 block 從未執行過，就是 dead code（可能是 opaque predicate 製造的假路徑）。

## 反反組譯（Anti-Disassembly）

### 錯位指令（Junk Byte）

在正常 code 中插入一個「有效 opcode 但指向前一條指令中間」的跳轉：

```asm
jmp _real_target     ; 真正的跳轉
.byte 0xe8           ; 這個 byte 讓 disassembler 誤讀下一條指令
_real_target:
; 真正的指令從這裡開始，但 disassembler 從 0xe8 開始解碼，就錯位了
```

識別：線性反組譯（objdump 的預設模式）會出現「看起來不合理的指令」或「函式中間突然出現 CALL 的 destination 是個奇怪地址」。應對：換用遞迴反組譯（Ghidra/IDA 的預設）或動態追蹤（gdb trace，只看實際執行的指令）。

## 踩雷集錦

1. **認為 packer 都用 UPX**：真實惡意程式更多使用**自訂 packer**——UPX 是常見的但不是唯一的。自訂 packer 的識別要靠 entropy 分析、section 結構、import 數量，而不是特定的 UPX magic。

2. **bypass anti-debug 後忘記字串仍在加密**：繞過了 ptrace 檢查，看到「Secret: WELCOME」，以為逆完了——但如果只是簡單的 XOR 加密，解密函式本身可能就是業務邏輯的一部分，還有更深的邏輯在後面。不要滿足於看到第一個「成功訊息」。

3. **動態 dump 的時機抓錯**：太早 dump（unpack 還沒完成）得到的是部分解包的記憶體；太晚 dump（程式已開始修改記憶體）可能得到修改後的版本。正確時機是在 OEP（原始 entry point）執行前的瞬間。

4. **把控制流平坦化的 state 變數誤以為是加密 key**：看到一個全程被讀寫的「重要整數」，以為是加密演算法的 key——結果是 dispatcher 的 state 變數，它本身沒有密鑰意義。區分方式：看這個變數有沒有直接參與 XOR / 乘法等密碼操作。

5. **LD_PRELOAD bypass 在 setuid binary 無效**：Linux 的 `LD_PRELOAD` 在 setuid/setgid binary 上被忽略（安全設計）。這時要用 gdb patch 或直接 binary patch。

## 進階：再往深一層

- **符號執行去混淆**：angr 的 `SimulationManager` + `blank_state` 可以對控制流平坦化進行符號執行，自動還原真實 CFG——接 `symex_taint` 課的 Ch 7（mini concolic）。
- **加殼分析的工程化**：Pin / DynamoRIO instrumentation 可以在 binary 執行時動態收集所有執行過的 BB（basic block），自動區分「已執行 = 真實 code」和「未執行 = dead code / opaque predicate」——接 Ch 15（DBI）。
- **YARA 規則偵測 anti-debug 手法**：把 ptrace check 的 asm pattern、UPX header、已知字串加密解碼 stub，寫成 YARA 規則，在惡意程式分析中批量偵測——接 `malware_analysis` 課的 YARA 章節。

## 本章重點整理

- **Anti-debug（ptrace）**：識別靠 `objdump | grep ptrace@plt`；繞過靠 `LD_PRELOAD` hook、gdb 改 return value、或 patch `jne` → `jmp`。
- **字串加密**：`strings` 看不到明文 → 有加密；動態 dump 的時機在解密函式執行後。
- **Packer 識別**：高 entropy + 異常 section + 極少 import；繞過靠在 OEP 前 dump 記憶體。
- **控制流平坦化**：大 switch + state 變數 = 被平坦化的 CFG；對抗靠符號執行或動態 coverage。
- **反反組譯**：線性反組譯會被錯位指令騙；用遞迴反組譯或動態 trace 還原真實指令流。

## 自我檢核

- [ ] 我能從 objdump 輸出辨識 ptrace anti-debug 的 asm 模式
- [ ] 我知道 LD_PRELOAD hook 的原理，並能用它繞過 ptrace 檢查
- [ ] 我能從 entropy 和 section 結構辨識一個可能被 pack 的 binary
- [ ] 我理解控制流平坦化的 dispatcher 結構，知道符號執行是對抗它的有效方式
- [ ] 我能解釋為什麼「動態 dump」能繞過字串加密

## 延伸閱讀

1. **《The Art of Unpacking》— Mark Vincent Yason（Black Hat 2007, 免費 PDF）**
   - 學什麼：unpacking 的系統性方法，OEP 定位的多種技術，從 UPX 到自訂 packer
   - 前提：Windows 逆向基礎（但概念通用）

2. **angr 文件：SimulationManager + path exploration**（[https://docs.angr.io/](https://docs.angr.io/)）
   - 學什麼：符號執行去除控制流平坦化的實際做法，有現成的 deobfuscation 案例
   - 前提：`symex_taint` 課基礎 + Python

3. **OpenIOC / YARA 規則庫（惡意程式相關）**（[https://github.com/Yara-Rules/rules](https://github.com/Yara-Rules/rules)）
   - 學什麼：已知惡意程式的 anti-debug / 加密字串 / packer 的 YARA 指紋，活教材
   - 前提：了解 YARA 基本語法

本章以防禦/分析視角貫穿全部技術。下一章跨出 Linux ELF，看一眼 Windows PE 和 ARM64 的逆向差異。

→ [Ch 24 跨平台一瞥：Windows PE / ARM64](./24-cross-platform-pe-arm64.md)
