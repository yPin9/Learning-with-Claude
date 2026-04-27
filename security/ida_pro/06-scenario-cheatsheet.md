# Ch 6 — 四大題材速查：CTF / malware / vuln / firmware

> 目標：一頁抵四頁的速查表 — 不同題材遇到 binary 時，第一個該按什麼、第二個按什麼，建立每個題材的反射動線。

把這一章收進書籤。之後真實工作時打開 IDA 前先掃這頁三秒，確認動線。

## 共通 onboarding（任何題材都先做）

```
1. autoanalysis 跑完 (AU: idle)
2. Shift+F12       → 掃 strings
3. Ctrl+S          → 存 IDB（之後 script 跑壞至少有 backup）
4. Shift+F4        → Names window，整體 symbol 掃一眼
5. View → Proximity browser → 看 call graph 密度
```

## 情境 A — CTF reversing

### 目標
快速找 flag 輸出路徑或 check 函式。CTF binary 通常小、無加殼、無反 debug。

### 動線
```
1. Shift+F12 找 "flag{" / "ctf{" / "congrat" / "wrong" / "correct"
2. 雙擊可疑字串 → X (xref) → 跳到 caller
3. 進 caller function，F5 看偽代碼
4. 往 main 走：X 一路到 start
5. 找到 check 函式後：
   - 看 input 對比邏輯（strcmp、自訂 hash、XOR、modular 運算）
   - 有迴圈就找 loop body 的轉換
6. 還原逆運算 → 寫 solver（通常用 Python）
```

### 常用招
| 招 | 鍵 | 為什麼 |
|---|---|---|
| 找字串 | `Shift+F12` | 9 成 CTF 靠字串破 |
| xref 爬 | `X` → `X` → `X` | 往 main 爬找 check |
| 純 disasm 看 loop | Space 切 Graph mode | 看迴圈結構直觀 |
| debug 跳過 check | `F2` 在 jump 前、改暫存器、`F9` | 直接跳過，不用還原邏輯 |

### 踩雷
- 有些 CTF 題故意 strip，`Shift+F12` 掃不到太多。退路是看 `main` 的 `printf("...")` 的 format string（那是 ROData）。
- ASLR 的 CTF 題：IDA 預設 rebase 到 `0x400000`，實際 runtime 不一樣。debug 時開 Process options 確認。

## 情境 B — Malware analysis

### 目標
不執行情況下盡量還原行為：C2 位址、IOC、持久化機制、evasion 技巧。執行要在沙箱。

### 動線
```
1. 先看 Imports (View → Imports)
   - VirtualAlloc + WriteProcessMemory + CreateRemoteThread → injection
   - InternetOpenUrlA / WinHttpSendRequest → C2
   - CryptAcquireContext / CryptEncrypt → 加密 payload 或勒索
   - CreateMutexA → 單一實例（mutex 名字是強 IOC）
2. Shift+F12 找字串
   - URL / IP / registry path
   - 可疑字串可能 XOR / RC4 加密，看起來像亂碼但定長 → Ch 12
3. WinMain / DllMain 進 F5，看 flow
4. 標 anti-debug / anti-vm：
   - IsDebuggerPresent、NtQueryInformationProcess、cpuid、rdtsc diff
5. 重點：
   - TLS callbacks（binary 啟動前就跑）
   - export 函式（DLL 的每個 export 都是潛在入口）
   - resource section（PE `.rsrc`，payload 常藏這）
```

### 常用招
| 招 | 為什麼 |
|---|---|
| Imports 看 API 組合 | API 組合是行為簽名 |
| Navigator band 找紅色區域 | 紅色 = unexplored，packed payload 通常在這 |
| til 載 Win32 SDK | 所有 API prototype 齊全，`Y` 很好填 |
| Structures：`PEB`、`TEB`、`UNICODE_STRING` | 大部分 Win til 都有，直接套 |
| 開 Local Debugger in sandbox VM | 單步走 unpack stub 看 payload 解到哪 |

### 關鍵 Windows struct（Win til 都有）
`_PEB`、`_TEB`、`_UNICODE_STRING`、`_LDR_DATA_TABLE_ENTRY`、`_EPROCESS`（kernel 分析）

看到 `fs:[0x30]` / `gs:[0x60]` 就是 TEB / PEB，`Y` 填型別立刻乾淨。

### 踩雷
- **packed binary 看起來只有 entry + 一堆 junk**：你需要先 unpack，IDA 本身不 unpack（ScyllaHide、PE-sieve、dumping at OEP 另講）。
- **malware 可能對 IDA 的載入行為本身做檢查**：冷門但存在。

## 情境 C — Vulnerability research

### 目標
找 exploitable 的 bug：buffer overflow、UAF、TOCTOU、integer overflow、format string、unsafe deserialization。

### 動線
```
1. 建立 attack surface map
   - Exports（DLL / library）
   - Imports of listen/recv/read/fread/WinHttpReceive → 從網路進的入口
   - 開 Proximity browser 看哪些 function 是入口 → 誰 reachable
2. 列危險 sink：
   - strcpy / strcat / sprintf / gets / memcpy (size 來自外部)
   - alloca / VLA with user size
   - memcpy / memmove with signed size
3. 每個 sink 按 X 反推 caller chain
4. 檢查 size 計算：
   - 是否有 整數 overflow (size * count)
   - 是否有 signed/unsigned 混用
   - 是否有 off-by-one
5. 檢查 lifetime：
   - free 後是否還用（UAF）
   - 兩個 pointer 指向同一塊，其中一個 free 後
6. 檢查 mutation：
   - check 和 use 中間是否有機會 race（TOCTOU）
```

### 常用招
| 招 | 鍵 | 用途 |
|---|---|---|
| X on sink | `X` | 反推所有 caller |
| 查某常數的用途 | `Alt+I` | 找所有 `0x41414141` / magic number |
| 看 stack frame 大小 | `Ctrl+K` | 判斷 overflow 空間 |
| Proximity browser | menu | call graph 看 entry 的可達集 |
| Decompiler 改 size type | `Y` 填 `size_t` / `ssize_t` | signed 混亂 |

### 常見 bug patterns
```c
// Integer overflow → tiny alloc → heap overflow
size_t total = count * size;                    // overflow
buf = malloc(total);
memcpy(buf, src, count * size);                 // write 大於 alloc

// Signed size
ssize_t len = recv(fd, &hdr, sizeof(hdr), 0);
if (len < 0) goto err;
memcpy(out, hdr.data, hdr.length);              // hdr.length 來自網路，可能是負
                                                 // memcpy 把 signed 當 unsigned
// UAF
obj = get();
free(obj);
do_something(obj);                              // oops
```

這些在 pseudocode 配合正確 type 標註後會很明顯。

## 情境 D — Firmware / embedded

### 目標
分析 stripped 的 bin / hex / uImage，搞懂 memory map、peripheral、entry point，找可分析的 function。

### 動線
```
1. 確認架構
   - ARM / ARM64 / MIPS / PowerPC / Xtensa / RISC-V
   - 用 binwalk 看檔案內容組成，或 readelf 如果是 ELF
2. Load 時設定對
   - Processor type
   - Manual load → 設定 ROM / RAM base address
   - 看 vendor datasheet 的 memory map
3. Segments 設好 (Shift+F7)
   - ROM  0x08000000 rx
   - RAM  0x20000000 rw
   - MMIO 0x40000000 rw（各 peripheral）
4. Vector table 放在哪
   - ARM Cortex-M：0x0000_0000 起頭是 SP + Reset handler
   - Reset handler 之後是 NMI / HardFault / ... 還有一堆 IRQ handler
5. 標 vector table entry 為 function
6. 從 Reset handler (F5) 追啟動流程
7. 從 string / peripheral 位址反推 driver：
   - 看到常存取 0x40020000 → 查 datasheet 知道是 GPIOA
   - rename function 為 gpio_init / uart_tx 等
```

### 常用招
| 招 | 用途 |
|---|---|
| Manual load | 設正確 base |
| Alt+S 新增 segment | 補 MMIO / 加載 overlay |
| `O` (convert to offset) | 把 `0x40020018` 變成 `GPIOA_ODR` |
| binwalk + IDA 搭配 | 先 binwalk 找多個 payload 區塊，分開分析 |
| QEMU + Remote GDB | firmware 很難真機 debug，QEMU 是常用跳板 |

### 踩雷
- **Thumb / ARM 混用**：Cortex-M 全 Thumb，某些指令地址要 `|1`。IDA 大多自動處理，但手動 set code 時要對。
- **Segment 沒設對**：peripheral 位址看起來是野位址，xref 全亂。
- **Loader 沒識別出**：丟一包原始 bin，IDA 不知道從哪開始。要 `File → Load additional binary file` 手動載入到指定位址。

## 綜合速查表

| 題材 | 第一個按鍵 | 第一個看的 view | 常用 til |
|---|---|---|---|
| CTF | `Shift+F12` | Strings | (無特殊) |
| Malware (Win) | — | Imports | mssdk_win10 / winxp |
| Malware (Linux) | — | Imports | gnulnx_x64 |
| Vuln research | — | Exports / Imports | (依系統) |
| Firmware | 手動 load | Segments / Vector table | (架構 SDK til) |

## 動手練習

選你最關心的一個題材，找一個 real-world binary（不用太難），用速查頁的動線走完：

1. 記錄你按了哪些鍵、看了哪些 view
2. 哪個步驟最花時間
3. 哪個步驟後來發現是浪費（下次可以跳過）

這份筆記以後會變成你自己的個人化速查，比任何教材都有用。

## 自我檢核

- [ ] 能說出 CTF 題的標準 5 步動線
- [ ] 知道 malware 看 Imports 幹嘛
- [ ] 知道 vuln research 怎麼從 sink 反推
- [ ] 知道 firmware 要手動設 segments
- [ ] 在真實 binary 走過至少一次其中一個動線

下一章進入 **練習 A**：純鍵盤解一個 crackme，把 Ch 2–6 全用上。

→ [練習 A：純鍵盤解一個 crackme](./practice-a-keyboard-only-crackme.md)
