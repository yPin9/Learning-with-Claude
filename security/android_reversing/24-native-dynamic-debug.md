# Ch 24 — 動態調試 native

> **目標**：把 native `.so` 從「靜態讀反組譯」升級到「跑起來、下斷點、看暫存器與記憶體、單步 trace」。你要能用 **IDA remote debug（配 `android_server`）**、**lldb 接 Android 進程**、或 **gdbserver** 三條路其中一條，在關鍵 native 函式停下來，親眼看 `x0..x30` 的值、把記憶體裡的 key/明文 dump 出來。這是 Ch 23 的下一步：靜態認出「key 在某個指標」，動態才拿得到那個指標指向的**真實的值**。

> **環境**：native 動態調試需要**真機或 arm64 AVD image** 跑 `android_server`/`lldb-server`/`gdbserver`，本 repo 沙箱沒有 Android/IDA/lldb。因此本章所有調試器的實際操作與輸出**皆標「未實測，理論預期行為」**，並在每段附上**你在自己環境的驗證步驟**。指令語法依 IDA 8.x / lldb 17 / Android NDK r26 的官方行為寫成。**絕不**把沒跑過的輸出裝成跑過的。

## 為什麼需要這個？

Ch 23 教你認出「這函式在算 AES，key 是傳進來的第一個參數指標」。但**指標指向的值是什麼？靜態看不到**——因為 key 常常是執行期才由另一段程式碼算出來（從裝置指紋、時間、伺服器下發的種子動態生成）。你盯著反組譯碼盯到天亮，也看不到那 16 個 byte 到底是多少。

解法只有一個：**讓它跑，在它把 key 準備好、正要餵進 AES 的那一瞬間停住，把記憶體讀出來**。這就是 native 動態調試。它跟 Frida hook（Ch 14）互補——Frida 適合「攔函式印參數」的高層觀測，而調試器適合「停在任意一條指令、逐步看暫存器怎麼變、記憶體怎麼被寫」的精細解剖，尤其是逆混淆過的、沒有清楚函式邊界的程式碼時。

三種攻擊面在這裡交會：Ch 20 的 ARM64（你要看的暫存器 `x0..x30`、`sp`、`pc` 就是那章的知識）、Ch 22 的 IDA（同一個 IDA，從靜態切到 remote debug）、Ch 19 的 JNI（斷點常下在 `JNI_OnLoad` 或某個 native 方法的入口）。

## 先建立直覺：調試器怎麼「停住別人的進程」

先搞懂一件事：**你電腦上的調試器（IDA/lldb）不直接碰手機上的進程**，中間隔一個跑在 Android 上的**調試伺服器（debug stub）**。架構跟 Ch 0 的 Frida 一模一樣——client 在 host、server 在 guest、adb 轉發 port。

```
   你的電腦 (host)                        Android (guest, 真機/arm64 AVD)
 ┌─────────────────────────┐           ┌────────────────────────────────┐
 │  IDA / lldb (client)     │           │  target App 進程                │
 │    ├ 下斷點              │  adb      │    ├ libnative.so (你要逆的)    │
 │    ├ 讀暫存器/記憶體      │◀─forward─▶│    │                           │
 │    └ 單步/continue       │  tcp port │  android_server / lldb-server   │
 │                         │           │   / gdbserver  (root 跑)        │
 └─────────────────────────┘           │    │ 用 ptrace() attach 到進程  │
                                        └────────────────────────────────┘

 底層：debug server 用 Linux 的 ptrace(2) 系統呼叫控制 target
       ── ptrace 讓一個進程可以讀寫另一個進程的暫存器與記憶體、攔它的 signal
```

**最底層是 `ptrace(2)`**：Linux 提供的「一個進程控制另一個進程」的系統呼叫。debug server（不管是 android_server、lldb-server 還是 gdbserver）都是靠 `ptrace(PTRACE_ATTACH, pid, ...)` 掛上 target，之後透過 `PTRACE_GETREGSET` 讀暫存器、`PTRACE_PEEKTEXT`/`process_vm_readv` 讀記憶體、在指令位址寫一個 `brk` 陷阱指令來設斷點。**斷點的本質**就是「把目標位址那條指令換成一個會觸發 SIGTRAP 的陷阱指令，命中時 CPU 陷入核心、核心通知 debug server、server 通知你的 IDA」。

這也解釋了兩件事：(1) **為什麼要 root**——ptrace 別的進程要權限，非 root 的 debug server attach 不上別人的 App（除非 App 自己 `android:debuggable=true`）；(2) **反調試怎麼擋你**（Ch 30）——App 可以自己先 `ptrace(PTRACE_TRACEME)` 佔住那個「只能被一個 tracer 附身」的名額，或檢查 `/proc/self/status` 的 `TracerPid` 欄位是否非 0 來發現你。

## 路線一：IDA remote debug + android_server

IDA 逆向者最順手的路：**同一個 IDA、同一份 `.so` 分析資料庫，從靜態無縫切到動態**。你在靜態視圖標好的函式名、下好的斷點，動態時直接沿用。

**步驟（未實測，理論預期行為）**：

```bash
# 1. android_server 隨 IDA 附贈，在 IDA 安裝目錄 dbgsrv/ 下
#    選對架構：arm64 target 用 android_server64
adb push <IDA>/dbgsrv/android_server64 /data/local/tmp/
adb shell chmod 755 /data/local/tmp/android_server64

# 2. root 跑起來（預設聽 TCP 23946）
adb root
adb shell "/data/local/tmp/android_server64 &"

# 3. host 把 port 轉發過來
adb forward tcp:23946 tcp:23946
```

然後在 IDA：`Debugger → Select debugger → Remote ARM Linux/Android`，設 hostname `127.0.0.1`、port `23946`，`Debugger → Attach to process` 選 target 進程（或用 `Debugger → Process options` 指定 App）。

**你預期會看到（理論預期行為）**：attach 成功後，IDA 進入 debug 佈局——出現暫存器視窗（`X0..X30`、`SP`、`PC`、`CPSR`）、記憶體 hex view、模組列表（列出載入的 `.so` 與各自的 base address）。在你 Ch 23 認出的 AES 函式入口按 F2 下斷點，`Continue`（F9），觸發功能讓 App 跑到那裡——命中時整個 UI 停住，`X0` 顯示第一個參數的值。

> **關鍵細節：ASLR 導致位址對不上**。`.so` 每次載入的 base address 隨機（ASLR）。你在靜態 IDA 看到的函式位址是「相對 image base 的偏移」，動態載入後真實位址 = base + 偏移。IDA remote debug 會自動處理 rebase（它知道模組載到哪），但**如果你手動算位址**（例如 Frida 用 offset），一定要 `模組 base + 靜態偏移`，不能直接用靜態位址。用 `adb shell cat /proc/<pid>/maps | grep libnative` 可查真實 base。

**驗證步驟（在你自己環境）**：起一個你有權分析的 App（自寫 crackme 最佳），照上面接上 IDA，在 `JNI_OnLoad` 下斷點——這是 native 庫載入時**第一個**被呼叫的函式，斷在這裡最保險。命中後看 `X0`（`JNIEnv*`）、`X1`（`JavaVM*` 或 reserved），確認你真的停在 native 入口。

## 路線二：lldb 接 Android（NDK 官方路）

lldb 是 Android NDK 官方調試器，Android Studio 的 native 除錯底層用的就是它。命令列直接用更靈活，且 lldb 的 Python API 適合寫自動化 trace。

**步驟（未實測，理論預期行為）**：

```bash
# 1. lldb-server 在 NDK 裡：
#    <NDK>/toolchains/llvm/prebuilt/<host>/lib/clang/<ver>/lib/linux/aarch64/lldb-server
adb push lldb-server /data/local/tmp/
adb shell chmod 755 /data/local/tmp/lldb-server

# 2. 找到 target pid
adb shell pidof com.example.target        # 假設回 12345

# 3. root 跑 lldb-server，用 platform 模式 attach 到 pid（聽某個 port）
adb root
adb shell "/data/local/tmp/lldb-server platform --listen '*:1234' --server &"
adb forward tcp:1234 tcp:1234
```

host 端開 lldb 連過去（NDK 附的 lldb）：

```
(lldb) platform select remote-android
(lldb) platform connect connect://127.0.0.1:1234
(lldb) attach 12345
```

**常用調試指令（理論預期行為）**：

```
b libnative.so`Java_com_example_Sign_calc    # 在某 native 方法下斷（有符號時）
br set -a 0x7xxxxxxx                          # 在絕對位址下斷（strip 時用 base+offset）
c                                            # continue
register read x0 x1 x2                        # 看前三個參數暫存器
memory read --size 1 --count 16 $x1           # 從 x1 指向處讀 16 byte（例如 AES key）
memory read -f x -c 4 $x0                     # 以 4 個 dword 格式讀
si / ni                                       # 單步進入 / 跳過
bt                                            # 看 call stack
```

**你預期會看到（理論預期行為）**：`register read x1` 印出一個位址（例如 `x1 = 0x7f2a3c0010`），接著 `memory read --size 1 --count 16 0x7f2a3c0010` 印出 16 個 byte——**那就是 AES key 的實際值**，靜態永遠看不到的東西。這一步是整個 native 逆向「還原加密」的臨門一腳。

**驗證步驟（在你自己環境）**：用你的 crackme，在 AES/簽名函式入口下斷，`register read` 找出哪個暫存器是 key 指標（ARM64 呼叫慣例：`x0` 第一參數、`x1` 第二…，Ch 20），`memory read` dump 出來。把 dump 到的 16 byte 拿去 Ch 23 的 Python，用它當 key 解一段密文，能解通就證明你抓對了。

## 路線三：gdbserver（傳統 gdb 路）

gdbserver 是更傳統的路，老教學多用它。新版 NDK 已移除內建 gdb（改推 lldb），但 gdb 生態的 script（peda/gef/pwndbg，你 gdb 課的底子）在這裡能用，逆混淆或做細粒度 trace 時 gef 的視圖很香。

**步驟（未實測，理論預期行為）**：

```bash
# gdbserver 需自備（老 NDK 或自編 aarch64 static 版）
adb push gdbserver /data/local/tmp/
adb shell chmod 755 /data/local/tmp/gdbserver
adb root

# attach 到已跑的 pid，聽 port 1234
adb shell "/data/local/tmp/gdbserver :1234 --attach 12345 &"
adb forward tcp:1234 tcp:1234
```

host 端用 aarch64 的 gdb（或 gdb-multiarch）：

```
(gdb) set architecture aarch64
(gdb) target remote 127.0.0.1:1234
(gdb) info registers x0 x1 x2
(gdb) x/16xb $x1                 # dump x1 指向的 16 byte
(gdb) b *0x7xxxxxxx              # 絕對位址斷點
(gdb) c
```

**取捨**：gdbserver 的優勢是 gef/pwndbg 的漂亮視圖與豐富 script，劣勢是新 NDK 不附、要自備 binary，且對 Android 的支援不如 lldb 官方。**新專案建議 lldb**，除非你重度依賴 gef。

## trace native 執行：看每一步怎麼變

下斷點是「停在某一點」，trace 是「記錄一段執行的每一步」——逆混淆或搞不清資料怎麼流時特別有用。三種粒度：

```
粒度            工具                       看到什麼               成本
──────────────────────────────────────────────────────────────────
單步 si/ni     lldb/gdb 手動或 script     每條指令的暫存器變化    慢，但最細
函式級 trace   Frida Interceptor(Ch14/25) 進出哪些函式+參數       中，適合摸清呼叫關係
指令級 trace   Frida Stalker(Ch15)        跑過的每條指令(可批量)  快、可程式化過濾
```

**lldb script 單步 trace（理論預期行為）**——每步印 pc 與關鍵暫存器：

```python
# lldb Python：從斷點開始單步 N 次，印 pc/x0
def trace(debugger, n=50):
    t = debugger.GetSelectedTarget().GetProcess().GetSelectedThread()
    for _ in range(n):
        f = t.GetFrameAtIndex(0)
        pc = f.GetPC()
        x0 = f.FindRegister("x0").GetValueAsUnsigned()
        print(f"pc=0x{pc:x}  x0=0x{x0:x}")
        t.StepInstruction(False)     # si
```

**取捨提醒**：純調試器單步 trace 幾千條指令會**很慢**（每步都要 host↔guest 來回）。要 trace 大量指令，Ch 15 的 Frida Stalker 在**進程內**收集、批量回傳，快得多。調試器單步適合「幾十到幾百條、要看每步細節」的場合；Stalker 適合「幾萬條、事後過濾」。

**驗證步驟**：在 crackme 的簽名函式入口下斷，跑上面的 `trace(debugger, 30)`，觀察 pc 怎麼在函式內跳、x0 怎麼變。對照 Ch 23 認出的演算法結構（例如 TEA 的 32 輪迴圈），你會看到 pc 在同一段位址反覆跳 32 次——親眼驗證「這就是那個迴圈」。

## 對比與取捨：三條調試路 + Frida

| 手段 | 附著方式 | 強項 | 弱項 | 何時選 |
|---|---|---|---|---|
| **IDA + android_server** | ptrace | 與靜態 IDA 無縫、視圖好 | 要 IDA(商業) | 已用 IDA 做靜態，想無縫切動態 |
| **lldb-server** | ptrace | NDK 官方、Python API、免費 | 指令要熟 | 官方推薦、寫自動化 trace |
| **gdbserver + gef** | ptrace | gef/pwndbg 生態 | 新 NDK 不附、要自備 | 重度依賴 gef 視圖 |
| **Frida**（Ch14/25） | 進程內注入 | 不搶 ptrace、能改行為、快 | 停不到任意指令 | 攔函式印參數、改返回值、大量 trace |

**一個關鍵區別**：調試器（ptrace 類）與 Frida 搶不搶「ptrace 名額」不同——同一時間**一個進程只能被一個 ptracer 附身**。你用 lldb attach 了，Frida 的某些模式或另一個調試器就上不去；反過來 App 自己 `PTRACE_TRACEME` 也會擋掉你。Frida 用注入不靠 ptrace，比較不受這限制（但它有自己的反 Frida 對抗，Ch 30）。實務常**兩手都備**：Frida 攔高層、卡住時上調試器啃細節。

## 踩雷集錦

1. **忘了 root / App 沒 debuggable，attach 失敗**：ptrace 別的進程要權限。確認 `adb root` 成功、debug server 以 root 跑。非 root 只能調試自己 `android:debuggable=true` 的 App。
2. **架構抓錯（32 vs 64）**：`android_server`（32-bit）對 `android_server64`（64-bit）、lldb-server / gdbserver 也分架構。x86_64 AVD 的 `.so` 是 x86，arm64 image 才是 ARM64——**這是 Part 4 的老陷阱**，用錯架構的 server 根本 attach 不上或斷點位址全錯。
3. **用靜態位址下斷、沒加 ASLR base**：`.so` 每次載入 base 隨機。手動下絕對位址斷點要 `真實 base + 靜態偏移`；真實 base 從 `/proc/<pid>/maps` 或調試器的模組列表查。IDA remote 會自動 rebase，手算的別忘加。
4. **App 有反調試，一 attach 就閃退或行為變**：App 檢查 `TracerPid`、`ptrace(TRACEME)` 佔位、偵測斷點指令。Ch 30 專門講繞法；先知道「attach 上去就崩」多半是反調試，不是你操作錯。
5. **斷點下在 native 方法卻沒命中**：可能該方法是 `RegisterNatives` 動態註冊的（Ch 19），符號名不是 `Java_...` 的標準格式，靜態看不到真實位址。要先 hook `RegisterNatives`（Ch 25/練習 C）拿到函式指標，再對那個位址下斷。
6. **debug server 沒在背景跑就 forward**：`adb shell "... &"` 有時 shell 一結束就把它帶走。確認 `adb shell pidof android_server64` 還在，再 `adb forward`。
7. **lldb `platform connect` 前忘了 `platform select remote-android`**：順序錯會連不上或功能不全。先 select 平台再 connect。

## 進階：再往深一層

- **在 `JNI_OnLoad` / linker 早期就斷住**：殼或反調試常在庫一載入就啟動防護。要在防護跑之前停住，得斷在**更早**——`android:debuggable` 配合 `Debug.waitForDebugger`，或用 `frida -f` spawn 模式在進程剛起、`.so` 還沒 `dlopen` 前就掛好。純 attach 太晚，防護早跑完了。
- **硬體斷點（watchpoint）追記憶體被誰寫**：想知道「這個 key buffer 是哪條指令填的」，用 `watchpoint set expression -- <addr>`（lldb）下**資料斷點**——ARM64 有硬體 debug 暫存器支援，命中時停在寫它的那條指令。逆「key 怎麼算出來的」神器。
- **script 化條件斷點自動 dump**：lldb/gdb 都能給斷點掛 script——命中時自動 `memory read` dump key 再 `continue`，不用手動。逆需要跑很多次才觸發的路徑時省大量人力。
- **對抗反 ptrace：先佔名額或改 kernel**：進階繞反調試的一招是自己先 `ptrace(TRACEME)` 或用一個 stub 進程佔住 tracer 名額，讓 App 自己的 `PTRACE_TRACEME` 失敗；更狠的直接在 root 的 kernel 改 `ptrace` 行為或 hook `TracerPid` 的讀取。Ch 30 展開。
- **core dump 事後分析**：跑一次把整個進程記憶體 dump 成 core（`gcore` 或從 `/proc/<pid>/mem` 讀），離線用 IDA/gdb 慢慢看。適合「只能觸發一次、現場稍縱即逝」的狀態。

## 動手練習

> 以下需要真機或 arm64 AVD image + 你有權分析的 App（自寫 native crackme 最佳）。沙箱無法代跑，親手做才有意義。

1. **接上任一調試器**：三條路挑一條（推薦 lldb），把 debug server 推進去、forward、attach 到一個 App，成功看到暫存器視窗。第一次接通比什麼都重要。
2. **斷在 `JNI_OnLoad`**：在 native 庫的 `JNI_OnLoad` 下斷點，命中後看 `x0`（`JNIEnv*`）。這是最保險的 native 入口斷點。
3. **dump 一個 key**：對一個做 AES/簽名的 native 函式（自寫的，你知道 key 是多少）在入口下斷，用 `register read` + `memory read` 把 key dump 出來，跟你原始碼裡的 key 對照，確認抓對了暫存器。
4. **單步驗證迴圈**：對一個 TEA（或任何有明顯迴圈的）函式單步 30 次，觀察 pc 在同一段位址反覆跳，數數是不是 32 輪，印證 Ch 23 認出的結構。
5. **watchpoint 追寫入**：對一個 buffer 下 watchpoint，看是哪條指令寫入它——體會資料斷點怎麼幫你回溯「這個值哪來的」。

## 本章重點整理

- native 動態調試 = **host 的 IDA/lldb/gdb + guest 的 debug server + adb forward**，底層是 `ptrace(2)`：斷點就是「把目標指令換成陷阱、命中陷入核心」。
- 三條路：**IDA+android_server**（與靜態無縫）、**lldb-server**（NDK 官方、免費、可 script）、**gdbserver+gef**（生態好但新 NDK 不附）。都要 **root**、都要**選對架構**。
- 核心動作：入口下斷 → `register read` 找 key/參數指標 → `memory read` dump 出**靜態看不到的真實值**（key/iv/明文）。這是「還原 native 加密」的臨門一腳。
- **ASLR** 使 `.so` base 隨機，手算位址要 `base + 靜態偏移`；base 從 `/proc/<pid>/maps` 查。
- 調試器 vs Frida：調試器停任意指令、看每步細節但慢；Frida 攔函式、能改行為、大量 trace 快。**兩手都備**。
- 本章調試操作皆標「**未實測，理論預期行為**」並附驗證步驟——你要在自己環境跑過才算數。

## 自我檢核

- [ ] 能畫出 native 調試的架構圖（client / debug server / adb / ptrace）
- [ ] 能解釋斷點的底層原理（陷阱指令 + SIGTRAP + ptrace 通知）
- [ ] 能說出三條調試路各自的強弱，以及為什麼都要 root
- [ ] 知道 ASLR 為什麼讓靜態位址下斷失效，以及怎麼算真實位址
- [ ] 能寫出「入口下斷 → 讀暫存器找 key 指標 → dump 記憶體」的 lldb/gdb 指令序列
- [ ] 知道 `RegisterNatives` 動態註冊的函式為什麼可能斷不到，以及怎麼辦
- [ ] 能講清楚調試器與 Frida 的分工，以及「一個進程一個 ptracer」的限制

## 延伸閱讀

- **[Android NDK — Debug your app with LLDB / lldb-server](https://developer.android.com/ndk/guides/lldb)** — Android Developers
  - **讀哪裡**：lldb-server 部署與 `platform connect` 流程
  - **和本章的關聯**：路線二的官方權威依據；lldb 指令細節查它
- **[ptrace(2) man page](https://man7.org/linux/man-pages/man2/ptrace.2.html)** — Linux man-pages
  - **讀哪裡**：`PTRACE_ATTACH`、`PTRACE_GETREGSET`、`PTRACE_TRACEME` 那幾個 request
  - **為什麼值得讀**：所有調試器與大半反調試的底層都是它；懂它才懂斷點與反調試的原理
- **[HackTricks — Android native debugging (IDA / gdbserver)](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：native debugging 與 IDA remote 那幾段
  - **前提知識**：讀過本章架構，這裡給你更多實戰指令與踩雷
- **[LLDB Python Reference](https://lldb.llvm.org/use/python-reference.html)** — LLVM
  - **讀哪裡**：`SBTarget`/`SBProcess`/`SBThread`/`SBFrame` 那幾個類
  - **和本章的關聯**：本章單步 trace 的 script 就用這些 API；寫自動 dump 斷點靠它

能停下來看記憶體之後，下一個進階問題是：怎麼在**不停住進程**的前提下，攔任意一條 native 指令、改它的行為、甚至 hook 一個 strip 掉、連名字都沒有的函式？下一章我們深入 hook 的底層——inline hook 怎麼改前幾條指令跳 trampoline、PLT/GOT hook 怎麼換函式指標、Frida 的 Interceptor 到底在做什麼。

→ [Ch 25 hook native 進階：inline / PLT hook](./25-native-hooking.md)
