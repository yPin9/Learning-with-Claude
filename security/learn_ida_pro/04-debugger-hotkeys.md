# Ch 4 — 動態 debug 情境快捷鍵

> 目標：用 IDA 的 debugger 跑起來 — local / remote、下斷點、conditional bp、watch、看 stack/register，不用換到其他 debugger。

## 為什麼要在 IDA 裡 debug

很多人平常用 x64dbg / gdb / lldb，問：為什麼要用 IDA 的 debugger？

- **你的所有 annotation 都在 IDB**：改過的名字、struct、註解立刻生效，斷下來時上下文完整。
- **switch 回 static 超快**：hit breakpoint 時你還在原本的 IDA 畫面，可以 `F5` 看當前 function 的偽代碼對照執行狀況。
- **Remote debugger 開箱即用**：IDA 附 debugger server（`dbgsrv/`），丟到目標機器開來，本機用 IDA 連過去。firmware / malware 研究實用。
- **和 IDAPython 整合**：可以寫 script 自動下斷點、收集執行 trace、動態 fix up struct。Ch 11 會用到。

但 IDA debugger 不是萬能 — time travel debugging、expression evaluator 的靈活度還是 WinDbg / gdb 贏。我的用法：**靜態為主、動態補洞**，不當主力 debugger。

## Debugger 類型選擇

頂部選單中間有個下拉選 debugger 類型：

```
Local Windows debugger       ← 你在 Windows debug Windows binary
Local Linux debugger         ← Linux 自家
Remote Windows debugger      ← 連到 win32_remote.exe
Remote Linux debugger        ← 連到 linux_server
Remote GDB debugger          ← 連 gdbserver（通用，常給 firmware / QEMU 用）
Bochs debugger               ← Bochs emulator (CPU-level)
```

**Remote GDB debugger 是 firmware 與 QEMU 的標準選法**：QEMU 用 `-s` 開 gdbserver 在 1234 port，IDA Remote GDB 連上去即可。

## 核心執行控制

| 鍵 | 動作 |
|---|---|
| `F9` | Start / Continue |
| `Shift+F9` | Stop / Terminate |
| `Ctrl+F2` | Pause（抓當下執行位置） |
| `F2` | Toggle breakpoint（游標所在位址） |
| `F7` | Step into |
| `F8` | Step over |
| `F4` | Run to cursor（游標位置停下來） |
| `Ctrl+F7` | Run until return（跑到當前 function 結束） |
| `Alt+F9` | 跳過下一次 return（常用來從 syscall / API 回來） |

**F4 Run to cursor 是神鍵**：比 F7 / F8 step 一萬次快得多。看偽代碼看到可疑一行，游標擺過去按 F4，直接到那。

## 斷點進階：conditional、hardware、read/write

下一般 bp：`F2`。想改條件：**右鍵斷點 → Edit breakpoint**（或 `Ctrl+Alt+B` 開 breakpoints 清單）。

### Software bp 的 condition

Condition 欄位填 **IDC 表達式**，例如：

```
EAX == 0x41414141
*(uint32_t*)(RBX + 0x10) > 100
strstr(cstr(EDI), "admin") != 0
```

命中時才真的停下來，其他次自動放過。適合「這個 hot function 被打 1000 次，我只想看其中一次特殊條件」。

### Hardware breakpoint

`Ctrl+Alt+B` → Add → 選 Hardware type。HW bp 可以設成：

- **Execute**：同 software bp，但用 DR0-3 暫存器（限 4 個）。
- **Write**：某記憶體被寫入時斷。偵測 global 被篡改必備。
- **Read**：某記憶體被讀取時斷。追密鑰 / flag 從哪被讀最好用。

Windows / Linux 上都靠 debug register，所以上限 4 個。

## Watch / Locals / Stack

開啟這幾個 view：

- **Debugger → Debugger windows → Watches**：放表達式持續監看。
- **Debugger → Debugger windows → Locals**：顯示當前 function 的 LVAR 值，配合 F5 的偽代碼 LVAR 名稱一起看非常直觀。
- **Debugger → Debugger windows → Stack trace**：call stack。
- **Debugger → Debugger windows → Call stack**：同上（UI 名稱依版本略異）。

`Ctrl+N` 在 Watches 加 expression：

```
*(char*)(RSI)           ← RSI 指的 byte
*(int*)(RBP-0x10)       ← local 變數
ecx                     ← 單純看暫存器
```

**暫存器 view**（`Debugger windows → General registers` 或右側預設顯示）可以直接改值，雙擊某個暫存器輸入新值。patch 測試很方便。

## Remote debug：連 gdbserver 流程

情境：Raspberry Pi 上跑了一個 binary，本機用 IDA 分析。

**目標機：**

```bash
gdbserver :1234 ./suspect_binary arg1 arg2
```

**本機 IDA：**

1. 頂端下拉切 `Remote GDB debugger`。
2. `Debugger → Process options`：
   - Hostname: `192.168.1.50`
   - Port: `1234`
3. `F9` 開始。

IDA 會發 gdb 協定 packet，讀暫存器、memory map、下 bp 等。

### Qemu-user 場景

逆向 ARM binary 但手上沒 ARM 板：

```bash
qemu-arm -g 1234 ./arm_binary
```

同樣 Remote GDB 連 `localhost:1234`，在 IDA 裡看 ARM disasm 並 debug。這是 firmware 研究的常用招。

## Process options：你一定會踩雷的地方

`Debugger → Process options` 設定：

- **Application**：要執行的 binary 路徑（remote 指目標機上的路徑）
- **Input file**：用於對應 IDB 的 binary（本機的）
- **Directory**：working directory
- **Parameters**：command line args
- **Hostname / Port**：remote only
- **Password**：remote only，連 `dbgsrv` 時設定的密碼

**踩雷**：remote debug 時，`Application` 寫的是目標機上的路徑，不是你本機 IDB 檔案的路徑。兩者搞混一次就知道了。

## 動態 fix up struct：debugger + struct 的合作

流程：

1. 靜態看到某 function 操作 `[rbx+0x18]`，猜不出 struct。
2. 在 function 入口 `F2` 下 bp，`F9` 跑起來。
3. 命中時看 `RBX` 指的記憶體：`Debugger → Debugger windows → Dump`，輸入 `RBX`。
4. 看到實際 bytes，推 field 邊界。
5. 在 Local Types 寫出 struct。
6. `F8` step 過幾條指令，再回來比對記憶體變化，驗證 struct 正確。

這個流程 Ch 11 會用 IDAPython 自動化（收集 runtime access pattern）。

## 常見踩雷

- **F9 之後 IDA 整個凍住**：目標程式跑去無限迴圈或等 input。`Ctrl+F2` pause，或切到 terminal 窗給目標 input。
- **Remote debug 連不上**：防火牆、port 錯、或 `dbgsrv` 沒開 verbose log 不知道收到什麼。先 telnet / nc 試 port 通不通。
- **斷點 `F2` 下了但不命中**：
  - PIE / ASLR：你在 file offset 下 bp，實際 runtime 是 rebase 後位址。IDA 9.x 會自動處理 PIE rebase，但如果 `Options → General → Rebase program` 沒啟用可能要手動。
  - HW bp 用完（只有 4 個）。
- **Step into 進了 PLT stub**：`F7` 走進了 `call <plt>` 的 PLT entry，看到一堆無意義 jump。要快速跳出來：`Ctrl+F7` run until return。

## 反 debug 的提醒

題材是 malware / 加殼程式的話，目標通常有 anti-debug：

- `IsDebuggerPresent` / `NtQueryInformationProcess` / PEB.BeingDebugged
- timing check（`rdtsc` 前後差異）
- throw hardware exception 觀察 handler 被吞

IDA 附 **Dbghide** 之類 plugin 可以 patch 掉常見 anti-debug，但這是另一整個主題（不在這門課範圍）。Ch 6 會在 malware 速查頁列個清單。

## 動手練習

1. 拿任何一個你有源碼的 C 小程式，編譯（`gcc -g0 -O0 hello.c`）。
2. IDA 打開編出來的 binary，跑 autoanalysis。
3. 在 `main` 下 bp，`F9` 跑起來。
4. 命中後 `F8` step over 幾步，觀察 register window 的變化。
5. 開 Locals window，對照 pseudocode 的 LVAR 看值。
6. 試一次 conditional bp：在迴圈裡某行下 bp，加條件 `ECX == 5`，看是不是第六次才停。

## 自我檢核

- [ ] 知道 `F7` / `F8` / `F4` / `F9` 的差異，特別是 F4 的威力
- [ ] 能下 conditional breakpoint
- [ ] 知道 hardware bp 的三種型別（exec / write / read）和上限 4 個
- [ ] 懂 remote debug 的基本流程（特別是 gdb remote）
- [ ] 知道 pause / terminate 的差別

下一章回到靜態分析，處理逆向的地獄題：還原 struct 和 type。

→ [Ch 5 Struct / enum / type 還原情境](./05-struct-type-recovery.md)
