# Ch 30 — 反調試、反 Frida、反注入

> **目標**：搞懂 App 用來偵測「你正在逆它」的三大類技術——**反調試**（偵測 ptrace/除錯器附加）、**反 Frida**（偵測記憶體裡的 frida-agent、預設 port、`gum-js-loop`）、**反注入**（掃自己的 `/proc/self/maps` 找不該在的 `.so`），並學會每一種的繞過思路。這章的核心心法是：**這些偵測全都建立在「可觀測的系統狀態」上，而系統狀態你也能改**——偵測與繞過是同一枚硬幣的兩面。

> **環境**：偵測邏輯的示範以 **Python 3** 表達演算法本身（TracerPid 解析、maps 掃描、字串比對）。凡是需要真正在 Android 上以 root 跑 frida-server、或讀 Android 的 `/proc/<pid>/status` 才能重現的，一律標「**未實測，理論預期行為**」並給你在 AVD 上的驗證步驟。本 repo 建構沙箱是 Windows，沒有 Linux `/proc`、沒有 Android。

## 為什麼需要這個？

到這裡你已經會脫殼（Ch 29）、會 Frida hook（Ch 12–15）。但真實世界的加固 App 不會乖乖讓你 attach——你 `frida -U -f com.target` 一下去，App 直接閃退，或彈「偵測到不安全環境」，或更陰的：**假裝正常但暗中把關鍵邏輯改成回傳假資料**。這就是反調試/反 Frida 在做的事。

不懂這些，你會卡在「工具明明沒問題，App 就是一 attach 就死」的鬼打牆裡。懂了，你就知道 App 在 CPU 上一定執行了某段「檢查程式碼」，而那段程式碼你可以定位、可以 hook、可以繞。**反偵測不是玄學，是一段有進入點、有回傳值、可以被攔截的普通函式。** 這章教你把它看穿。

## 先建立直覺：偵測的本質是「觀測系統狀態」

一個 App 想知道「我是不是正在被逆」，它沒有超能力，只能去**讀作業系統暴露出來的狀態**。而在 Linux（Android 底層就是 Linux）上，這些狀態幾乎都攤在 `/proc` 這個虛擬檔案系統裡，或藏在幾個 syscall 的回傳值裡。

```
        App 進程（想偵測自己有沒有被逆）
              │
   ┌──────────┼───────────────────────────┐
   ▼          ▼                            ▼
 反調試      反 Frida                    反注入
   │          │                            │
 「有沒有     「記憶體裡有沒有             「我的 maps 裡有沒有
  除錯器       frida-agent / 開放的         我沒載入過的 .so ？」
  attach 我？」 27042 port / gum 執行緒？」
   │          │                            │
   ▼          ▼                            ▼
 讀 TracerPid  掃 /proc/self/maps 找       比對「我預期載入的 .so」
 呼叫 ptrace   frida 字串、connect 27042    vs「實際 maps 裡的 .so」
 檢 status     掃 D-Bus 特徵
```

三類偵測的共同結構都是：**取一個系統狀態 → 跟「乾淨環境該有的樣子」比對 → 不一致就判定被逆**。所以繞過的共同思路也只有兩條：

1. **讓被觀測的狀態變乾淨**（例如把 frida-agent 藏起來、不開預設 port）。
2. **攔截「取狀態」這個動作本身**（hook `ptrace`、hook 讀 `/proc` 的 `open`/`read`、hook 回傳假值）。

記住這個框架，下面每個具體技術都是它的實例。

## 反調試（一）：ptrace 自附加

Linux 有一條鐵律：**一個進程同時只能被一個 tracer 用 `ptrace` 附加**。除錯器（gdb、Android Studio debugger、也包括某些 Frida 模式）就是靠 `ptrace(PTRACE_ATTACH, ...)` 掛上目標的。App 反過來利用這條鐵律：**我先自己 `ptrace` 我自己**，那位子被我占了，除錯器就 attach 不上（回 `EPERM`）。

底層機制：

```
   正常情況                          自附加防護後
 ┌──────────┐                     ┌──────────┐
 │ 除錯器    │──PTRACE_ATTACH──▶  │ 除錯器    │──PTRACE_ATTACH──▶ EPERM!
 └──────────┘   成功              └──────────┘   失敗（位子被占）
       │                                ▲
       ▼                                │ 已經被自己 trace
   App 進程                        App 進程 fork 出的 child
                                   對 parent 做 PTRACE_ATTACH
                                   （或 parent 對自己 PTRACE_TRACEME）
```

兩種常見寫法：

- **`PTRACE_TRACEME`**：進程呼叫 `ptrace(PTRACE_TRACEME, 0, 0, 0)`，把自己的 tracer 設成父進程。若已經有除錯器附加，這個呼叫會回 `-1`／`errno=EPERM`——App 據此判斷「我被 trace 了」。
- **fork 一個 child 專門 trace parent**：App `fork()`，child 對 parent `PTRACE_ATTACH`。成功了就自己占位、擋掉外部除錯器；child 同時能監控 parent 有無異常。這種「守護子進程」更難繞，因為你得同時處理兩個進程。

用 Python 表達 `PTRACE_TRACEME` 偵測的**判斷邏輯**（Linux 語意；本 repo 沙箱是 Windows 無法實跑 `ptrace`，**以下為理論預期行為**）：

```python
import ctypes, os
libc = ctypes.CDLL("libc.so.6", use_errno=True)   # Android/Linux 上為 libc
PTRACE_TRACEME = 0

def being_debugged():
    r = libc.ptrace(PTRACE_TRACEME, 0, 0, 0)
    if r == -1 and ctypes.get_errno() == 1:        # EPERM = 1
        return True     # 已經有 tracer → 判定被除錯
    return False        # 成功占位 → 目前沒被除錯（但自己現在變成被 trace 狀態）
```

> **未實測（沙箱無 Linux libc/ptrace）**。在 AVD 上驗證：寫個 JNI 小程式呼叫上面的邏輯，先直接跑印出 `False`，再用 `frida -U -f` 以 spawn 模式起它（Frida 的 spawn 會短暫 ptrace），看回傳是否變 `True`。真實加固 App 的這段通常寫在 native `.so` 裡，混在 `JNI_OnLoad` 或 constructor（`__attribute__((constructor))`）中，開機第一時間就跑。

**繞過 ptrace 自附加**——最乾淨的一招是 hook `ptrace` 讓它永遠回成功、且不真的占位：

```javascript
// anti-ptrace-bypass.js —— 讓 App 對自己的 ptrace(TRACEME) 看起來成功、卻不真占位
// 未實測，理論預期行為（需在 AVD + frida-server 上跑）
Interceptor.attach(Module.getExportByName(null, "ptrace"), {
    onEnter(args) {
        // args[0] 是 request；PTRACE_TRACEME == 0
        this.isTraceme = args[0].toInt32() === 0;
    },
    onLeave(retval) {
        if (this.isTraceme) {
            retval.replace(0);   // 假裝成功（回 0）
            console.log("[anti-debug] ptrace(TRACEME) 被攔截，偽造回傳 0");
        }
    }
});
```

驗證步驟：先不掛腳本，`frida -U -f com.target`，看它是否立刻閃退；掛上 `-l anti-ptrace-bypass.js` 再跑一次，若能停在畫面上、log 印出攔截訊息，就成功。**注意**：若 App 是 fork child 來 trace（不是自己 TRACEME），你還得 hook `fork`/`clone` 或在 child 裡也攔，單掛這支未必夠——這就是為什麼 fork-child 版本更硬。

## 反調試（二）：檢查 TracerPid

比 ptrace 更輕量、也更常見的一招：直接讀 `/proc/self/status`，裡面有一行 `TracerPid`。**沒被 trace 時它是 0；被除錯器 attach 時它變成 tracer 的 PID**。App 只要開檔讀這行、parse 出數字、判斷是否非 0 即可。

```
$ cat /proc/self/status | grep TracerPid
TracerPid:      0        ← 乾淨，沒人 trace
TracerPid:      2891     ← 被 PID 2891（除錯器 / frida-server）附加了
```

Python 表達這個判斷（**Android 上為實際可跑**；本 repo 沙箱是 Windows 無 `/proc`，**以下邏輯為理論預期行為**）：

```python
def tracer_pid():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("TracerPid:"):
                return int(line.split()[1])
    return -1

def being_debugged():
    return tracer_pid() != 0     # 非 0 = 有 tracer = 被除錯
```

> **未實測（沙箱是 Windows，無 `/proc/self/status`）**。這段在任何 Linux 桌面上都跑得起來：`cat /proc/self/status` 你會看到 `TracerPid: 0`；用 `strace cat /proc/self/status`（strace 用 ptrace）再看，同一支被 trace 的進程會顯示非 0。在 AVD 上，App 沒被 attach 時 `TracerPid` 是 0，被 frida-server spawn/attach 時會顯示 frida-server 的 PID。

**繞過 TracerPid 檢查**有兩種層次：

- **表層**：hook `open`/`openat`，當路徑是 `/proc/self/status`（或 `/proc/<自己 pid>/status`）時，改讀一份 `TracerPid` 被抹成 0 的假檔。
- **精準**：hook `libc` 的 `fgets`/`read`，偵測讀到的緩衝區含 `TracerPid`，把後面的數字改寫成 `0`。

```javascript
// tracerpid-bypass.js —— 攔 fgets，讀到 TracerPid 那行就竄改成 0
// 未實測，理論預期行為
const fgets = Module.getExportByName(null, "fgets");
Interceptor.attach(fgets, {
    onEnter(args) { this.buf = args[0]; },
    onLeave(retval) {
        if (retval.isNull()) return;
        const line = this.buf.readCString();
        if (line && line.indexOf("TracerPid:") !== -1) {
            this.buf.writeUtf8String("TracerPid:\t0\n");
            console.log("[anti-debug] TracerPid 已竄改為 0");
        }
    }
});
```

驗證：hook 前 App 判定被除錯而閃退；hook 後讀到的永遠是 0，App 以為乾淨。**邊界情況**：有些 App 不用 `fgets` 而用 `read` 一次讀整塊、或直接在 native 裡 `syscall(openat)` 繞過 libc wrapper——那你的 hook 掛在 libc export 上會攔不到，得改 hook syscall 或用 `Interceptor` 掛到更底層。這是攻防升級的典型節奏。

## 反 Frida：三條主要偵測線

Frida 注入後會在目標進程留下**三種可觀測痕跡**，反 Frida 就是針對這三種：

```
① 記憶體特徵     maps 裡多了 frida-agent-64.so
                 anon 區塊命名含 "frida-gum-js-loop"、"pool-frida"
                 記憶體中掃得到 "frida:rpc"、"FridaScriptEngine" 等字串

② 網路特徵       frida-server 預設監聽 TCP 27042（甚至 27043）
                 App 主動 connect("127.0.0.1", 27042)，連得上 = 有 frida-server

③ 行為特徵       Frida 的 D-Bus 交握字串；某些函式被 Interceptor 改寫後
                 開頭的機器碼變成跳板（trampoline），檢查函式序言是否被改
```

**① 掃 maps 找 frida 特徵**是最普遍的。App 讀 `/proc/self/maps`（列出自己所有記憶體對映），逐行找可疑字串。用 Python 表達這個掃描邏輯（**這段在本 repo 沙箱實際可跑**，因為它只是字串比對，不依賴 `/proc`；我們餵一段代表性的 maps 文字）：

```python
# scan_maps.py —— 反 Frida 掃 maps 的核心邏輯（字串比對，實跑）
FRIDA_SIGS = ["frida", "gum-js-loop", "gadget", "pool-frida", "frida-agent"]

sample_maps = """
7f00-7f01 r-xp 00000000 fd:00 111 /data/local/tmp/re.frida.server/frida-agent-64.so
7f02-7f03 r--p 00000000 fd:00 222 /apex/com.android.art/lib64/libart.so
7f04-7f05 rw-p 00000000 00:00 0   [anon:frida-gum-js-loop]
7f06-7f07 r-xp 00000000 fd:00 333 /system/lib64/libc.so
""".strip().splitlines()

for line in sample_maps:
    low = line.lower()
    for sig in FRIDA_SIGS:
        if sig in low:
            print("[anti-frida] 命中特徵:", sig, "->", line.split()[-1])
            break
```

**實際輸出**（本 repo 沙箱 Python 3 實跑）：

```
[anti-frida] 命中特徵: frida -> /data/local/tmp/re.frida.server/frida-agent-64.so
[anti-frida] 命中特徵: gum-js-loop -> [anon:frida-gum-js-loop]
```

清楚看到：預設模式下的 frida-agent 會在 maps 留下 `frida-agent-64.so` 的路徑、以及一個名為 `[anon:frida-gum-js-loop]` 的匿名區塊——兩個都是硬特徵。

**② 掃 27042 port**：frida-server 預設監聽這個 port，App 主動連連看，連得上就判定環境有 frida-server。Python 表達（**本 repo 沙箱可跑 socket connect 邏輯**，但目標 port 在本機通常無人監聽，故示範判斷結構；Android 上語意相同）：

```python
import socket
def frida_port_open(port=27042):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", port))   # 連得上 → 有東西在聽
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
    finally:
        s.close()
# Android 上：frida-server 預設跑起來時這裡回 True
```

> **為什麼是 27042**：這是 Frida 官方寫死的預設 control port（27042 給主 device，27043 給 cluster）。不是 magic 亂數，是 Frida 的預設值——所以繞法之一就是不用預設 port。

**③ 檢函式序言（inline hook 痕跡）**：Frida 的 `Interceptor.attach` 會在被 hook 函式的開頭寫入一段跳板指令（把執行流導向 Frida 的 handler）。App 可以讀某些敏感函式（如 `open`、`strstr`）的前幾個 byte，跟「乾淨的序言該長怎樣」比對，被改了就是被 hook 了。這招專門反制「你 hook 我的偵測函式」。

### 繞過反 Frida：從弱到強

| 偵測線 | 弱繞法 | 強繞法 |
|---|---|---|
| ① maps 有 `frida` 字串 | 改 frida-server 檔名（`frida-server` → `fs`），部分特徵消失 | 用 `frida-gadget` 改名嵌入 / 編譯魔改版 Frida 抹掉 `frida`/`gum` 字串 |
| ① `gum-js-loop` 匿名區塊 | —（改名難） | 魔改 gum 原始碼改執行緒/區塊命名，或 hook 讀 maps 的 `open` 過濾掉該行 |
| ② 27042 port | `frida-server -l 0.0.0.0:12345` 換 port | 改 port + App 若 hardcode 掃 27042 就失效 |
| ③ 序言被改 | —（本來就是你造成的） | 用 hardware breakpoint / Stalker 之類不改記憶體的方式，或先 hook 掉偵測函式 |
| 全部 | objection 的 `--no-pause` 等現成腳本 | **hook 掉偵測函式本身**：找到 App 讀 maps/連 port 的那個函式，直接讓它回「乾淨」 |

最通用的思路仍是**攔截「取狀態」**：hook `open`/`openat`，當路徑是 `/proc/self/maps` 時回傳一份**過濾掉 frida 行**的假 maps。

```javascript
// hide-frida-from-maps.js —— 攔 App 讀 /proc/self/maps，濾掉含 frida/gum 的行
// 未實測，理論預期行為
const openat = Module.getExportByName(null, "openat");
// 實作重點：hook open/openat 判斷路徑，若是 maps 則回一個 fd 指向「已過濾」的暫存內容。
// 更簡單的做法：hook fgets/read，讀到含 "frida"/"gum-js-loop" 的行就跳過或清空。
const fgets = Module.getExportByName(null, "fgets");
Interceptor.attach(fgets, {
    onEnter(args) { this.buf = args[0]; },
    onLeave(retval) {
        if (retval.isNull()) return;
        const line = this.buf.readCString() || "";
        if (/frida|gum-js-loop|gadget|pool-frida/i.test(line)) {
            this.buf.writeUtf8String("\n");   // 清空這行，App 掃不到特徵
        }
    }
});
```

驗證步驟：先讓 App 裸跑（會偵測到 Frida 而退出），再掛此腳本；若能穩定停在畫面、且你另外 hook 偵測函式印出的「maps 命中數」變 0，就成功。**踩雷**：`fgets` 逐行過濾對「一次 `read` 整塊」的 App 無效，得改攔 `read` 並在整塊 buffer 上做過濾，處理跨行邊界更麻煩——這是實務上最常卡的地方。

## 反注入：檢查自己的 maps 有沒有「外來 .so」

反 Frida 是找特定特徵；**反注入**更泛化：App 維護一份「我正常情況該載入哪些 `.so`」的白名單（或至少知道「不該有 `/data/local/tmp` 底下的東西」），掃 `/proc/self/maps`，發現任何來源可疑的 `.so`（尤其路徑在 `/data/local/tmp`、`/data/data/<別的 app>`、或匿名可執行區塊）就判定被注入。

底層機制——注入必然改變 maps：

```
   乾淨進程的 maps                注入後的 maps
 /system/lib64/libc.so         /system/lib64/libc.so
 /apex/.../libart.so           /apex/.../libart.so
 /data/app/.../base.apk!...    /data/app/.../base.apk!...
                          ┌──▶ /data/local/tmp/frida-agent-64.so   ← 多出來！
                          │    [anon:frida-gum-js-loop]  (rwx)      ← 可執行匿名區！
   （只有官方路徑）        └──  （路徑在 tmp / 匿名 rwx = 強烈可疑）
```

兩個特別強的訊號：

1. **路徑在非官方目錄的 `.so`**：正常 App 的 `.so` 只會來自 `/system`、`/apex`、`/vendor`、自己的 APK（`base.apk!/lib/...`）。出現 `/data/local/tmp/*.so` 幾乎鐵定是注入。
2. **`rwx`（可讀可寫可執行）匿名區塊**：正常程式碼區是 `r-x`（不可寫），資料區是 `rw-`（不可執行）。同時可寫又可執行的匿名記憶體是「執行期產生程式碼」的特徵——JIT 引擎（含 Frida 的 gum）會有，正常 App 少見。

Python 表達這個「找外來 .so」的判斷（**字串邏輯，本 repo 沙箱實跑**）：

```python
# detect_injection.py —— 反注入：掃 maps 找非官方路徑的 .so 與 rwx 匿名區
OFFICIAL = ("/system/", "/apex/", "/vendor/", "/data/app/")
sample = """
7f00 r-xp /system/lib64/libc.so
7f01 r-xp /data/app/~~x/base.apk
7f02 r-xp /data/local/tmp/frida-agent-64.so
7f03 rwxp [anon]
""".strip().splitlines()

for line in sample:
    parts = line.split()
    perm, path = parts[1], parts[-1]
    if path.endswith(".so") and not path.startswith(OFFICIAL):
        print("[anti-inject] 可疑 .so（非官方路徑）:", path)
    if "rwx" in perm.replace("p","").replace("s",""):
        print("[anti-inject] 可疑 rwx 匿名區:", path)
```

**實際輸出**（本 repo 沙箱 Python 3 實跑）：

```
[anti-inject] 可疑 .so（非官方路徑）: /data/local/tmp/frida-agent-64.so
[anti-inject] 可疑 rwx 匿名區: [anon]
```

**繞過反注入**跟反 Frida 同源：要嘛讓痕跡消失（把 frida-agent 放進 App 自己的目錄假裝合法、避免 rwx——難），要嘛攔截讀 maps 的動作把可疑行濾掉（同上一節的 `fgets`/`read` hook）。實務上「hook 讀 maps」是最省力的萬用解，因為反調試、反 Frida、反注入**大量共用同一條資訊來源**——`/proc/self/maps`。攔住這條，三類偵測一起瞎掉。

## 對比與取捨：三類偵測與繞過難度

| 偵測技術 | 讀什麼系統狀態 | 偵測強度 | 通用繞法 | 繞過難度 |
|---|---|---|---|---|
| ptrace 自附加（TRACEME） | ptrace syscall 回傳 | 中 | hook `ptrace` 回 0 | 低 |
| ptrace fork-child 守護 | 兩進程互 trace | 高 | 同時處理 parent+child | 高 |
| 檢 TracerPid | `/proc/self/status` | 中 | hook 讀檔改成 0 | 低 |
| 掃 maps 找 frida | `/proc/self/maps` | 中高 | hook 讀 maps 濾行 / 魔改 Frida | 中 |
| 掃 27042 port | socket connect | 低 | 換 port | 低 |
| 檢函式序言被改 | 自身 code 記憶體 | 高 | 不改記憶體的 hook（HW bp/Stalker） | 高 |
| 反注入掃外來 .so | `/proc/self/maps` | 中高 | hook 讀 maps / 藏 .so | 中 |

**取捨心法**：偵測方越把邏輯往「難以攔截的底層」放（native syscall 直呼、不走 libc wrapper、fork 守護進程、硬體斷點檢查），你的繞過就越貴。反過來，只要偵測還走 libc 的 `open`/`fgets`/`ptrace`，一支 Frida `Interceptor` 就能收拾。所以真正硬的 App 會**故意繞過 libc、直接 `syscall()`**，逼你 hook 到 syscall 層或改用不改記憶體的手法。

## 踩雷集錦

1. **只 hook 了 `ptrace` 卻還是被偵測**：App 可能根本沒用 `ptrace`，用的是讀 `TracerPid`；或它 fork 了 child 來 trace，你只攔 parent 的 `ptrace` 沒用。**先定位它到底用哪招**（Frida 掛在 `open`/`ptrace`/`fork` 全印 log，看哪個被呼叫），再對症下藥。別盲目套腳本。
2. **偵測在 `JNI_OnLoad` 或 constructor 裡、跑得比你 attach 還早**：native `.so` 一被 `dlopen` 就跑 constructor，可能在你 spawn 後、hook 裝好前就執行完閃退了。解法：用 Frida 的 **spawn + 早期 instrument**（在 `dlopen`/`android_dlopen_ext` 上設 hook，等目標 `.so` 一載入立刻攔），或 hook `JNI_OnLoad` 本身。
3. **hook `open` 攔不到，因為 App 直接 `syscall(SYS_openat)`**：繞過 libc wrapper 的 App 讓你掛在 export 上的 hook 完全失效。改用 `Interceptor` 掛到 `syscall` 或用 SVC 攔截。這是「libc 層 hook 全套失效」的典型原因。
4. **改了 frida-server 檔名，`gum-js-loop` 還是露餡**：改檔名只清掉 maps 裡的路徑字串，但 gum 的內部執行緒/匿名區命名（`gum-js-loop`、`pool-frida`）沒變。要清這些得**魔改並重編 frida-gum**，不是改檔名能解決的。
5. **繞過後 App「能跑但功能不對」**：有些防護不閃退，改成**靜默降級**（回傳假資料、關掉關鍵功能）。你以為繞過成功，其實它在騙你。驗證繞過是否真成功，要看**功能是否正常**，不能只看「有沒有閃退」。

## 進階：再往深一層

- **反調試藏在 signal handler 裡**：進階手法是自己註冊 `SIGTRAP`/`SIGSEGV` handler，故意觸發斷點指令。沒被 trace 時 handler 正常接到 signal；被除錯器 trace 時 signal 被除錯器攔走、handler 沒被呼叫——App 據此反推被 trace。這比讀 TracerPid 隱蔽得多。
- **時間差偵測（timing check）**：單步除錯會讓程式慢好幾個數量級。App 在關鍵區前後 `clock_gettime`，若耗時遠超正常閾值就判定被單步。繞法是連 `clock_gettime` 也 hook，回傳「看起來正常」的時間差。
- **Frida 的 Stalker / hardware breakpoint 反制序言檢查**：既然「檢查函式開頭 byte 有沒有被改」能抓 inline hook，那就用**不改被 hook 函式記憶體**的手法——ARM64 的硬體斷點（debug 暫存器）或 Frida Stalker 的動態重寫，繞過序言檢查。這是攻防升到 CPU 特性層的體現，Ch 25（native hook 進階）與 Ch 15（Stalker）有伏筆。
- **看雪/OWASP 的對抗清單**：OWASP MASTG 的 anti-tampering/anti-debug 測試案例把這些偵測系統化編號（MASTG-TEST-...），是查「還有哪些偵測我沒想到」的最佳清單。

## 動手練習

1. 在本 repo 沙箱把本章的三段 Python（`scan_maps.py`、`frida_port_open`、`detect_injection.py`）跑一遍，親手改 `sample_maps` 加一行乾淨的 `.so`、再加一行 `/data/local/tmp/xxx.so`，確認偵測邏輯能正確分辨。**目的**：先在無 Android 的環境把「偵測 = 字串比對系統狀態」的本質吃透。
2. （需 AVD）寫一個最小 App，在 `MainActivity.onCreate` 讀 `/proc/self/status` 印出 `TracerPid`。先直接跑（印 0），再用 `frida -U -f` spawn 它（看 `TracerPid` 是否變非 0）。**目的**：親眼見證「被 attach → TracerPid 變非 0」的因果。
3. （需 AVD）拿一個已知有反 Frida 的開源 crackme（或自己在 App 裡加掃 maps 的邏輯），先裸跑看它偵測到 Frida 而退出，再套本章的 `fgets` 過濾腳本，讓它掃不到特徵。**目的**：完成一次「定位偵測 → 攔截取狀態 → 繞過」的完整循環。

## 本章重點整理

- 反調試/反 Frida/反注入的**共同本質**是「讀作業系統暴露的狀態（多半在 `/proc`）→ 跟乾淨環境比對 → 不符就判定被逆」。
- 反調試靠 **ptrace 自附加**（占 tracer 位子）與**檢 TracerPid**（`/proc/self/status`）；繞過 = hook `ptrace` 回 0、或 hook 讀檔把 TracerPid 改 0。
- 反 Frida 針對三種痕跡：**maps 裡的 frida-agent/`gum-js-loop`**、**27042 port**、**被 hook 函式的序言變化**；最通用繞法是攔讀 maps 的 `open`/`fgets` 濾掉特徵。
- 反注入掃 maps 找**非官方路徑的 `.so`** 與 **rwx 匿名區**；因為與反 Frida 共用 `/proc/self/maps`，攔住這條資訊源能一次癱瘓多類偵測。
- 偵測越往「繞過 libc、直呼 syscall、fork 守護、硬體斷點」放，繞過越貴——攻防是持續升級的軍備競賽。

## 自我檢核

- [ ] 不看筆記，能說出反調試/反 Frida/反注入三者的**共同結構**與兩條通用繞法
- [ ] 能解釋 `PTRACE_TRACEME` 為什麼能擋除錯器，以及被 trace 時它的回傳值差異
- [ ] 知道 `TracerPid` 在 `/proc/self/status` 裡、0 與非 0 各代表什麼
- [ ] 能列出 Frida 在 maps 留下的至少兩個硬特徵（檔名 + 匿名區命名）
- [ ] 能解釋為什麼「hook `open` 卻攔不到」——App 直呼 syscall 繞過 libc wrapper
- [ ] 知道「繞過後能跑」不等於「繞過成功」，要驗功能是否正常（防靜默降級）

## 延伸閱讀

- **[OWASP MASTG — Anti-Debugging / Anti-Tampering 測試](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0035/)**
  - **讀哪裡**：Android 的 "Testing Anti-Debugging Detection"、"Anti-Tampering" 系列 TEST 案例
  - **學什麼**：把本章的偵測技術系統化成可勾選的測試清單，含 ptrace/TracerPid/timing 各種變體
  - **關聯**：本章講原理，MASTG 給你「還漏了哪些偵測」的完整檢查表
- **[HackTricks — Frida 偵測與繞過](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/frida-tutorial/index.html)**
  - **讀哪裡**：Frida detection / bypass 那幾段，含 maps 掃描、port 掃描的實際偵測碼
  - **學什麼**：本章 27042、`gum-js-loop`、maps 特徵對應的具體繞過指令
  - **關聯**：本章的繞過思路，這裡給你可複製的 objection/Frida 命令
- **[Frida CodeShare — anti-detection 腳本](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 "anti-debug"、"anti-frida"、"bypass" 的社群腳本，讀原始碼
  - **學什麼**：別人怎麼寫 `ptrace`/`fgets`/maps 過濾的 hook，比自己從零寫快
  - **關聯**：本章的 `.js` 片段是骨架，CodeShare 有處理各種邊界的成熟版本
- **[看雪 — Android 反調試與對抗專題](https://bbs.kanxue.com/)**
  - **讀哪裡**：搜「反調試」「反 Frida」「TracerPid」的技術文；中文社群對 native 層防護的實戰討論最深
  - **學什麼**：signal handler 反調試、syscall 直呼繞 libc、fork 守護進程等進階防護的原始碼級剖析
  - **關聯**：本章「進階」小節提到的手法，看雪有大量真實加固樣本的逆向記錄

反調試/反 Frida 多半是「這個環境安不安全」的通用判斷。下一章我們鑽進一個更專門、也更常獨立出現的偵測——**這台裝置有沒有 root、是不是裝了 Magisk**，以及 SafetyNet/Play Integrity 這種把判斷外包給 Google 的硬體背書機制怎麼運作、怎麼繞。

→ [Ch 31 root / Magisk 檢測與繞過](./31-root-magisk-detection.md)
