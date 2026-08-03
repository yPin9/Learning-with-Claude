# Ch 15 — 動態插樁（DBI）：Frida / Pin / DynamoRIO

> **目標**：學會可觀察性第三支柱——動態二進位插樁（dynamic binary instrumentation, DBI）：在 binary 執行時**注入你自己的 code**，hook 任意函式看/改參數與回傳、記錄執行覆蓋率、繞過檢查。搞清楚 Frida / Intel Pin / DynamoRIO 三大工具各自的定位，用 Frida 對一個真 binary hook 一個函式印出它的參數與回傳（真跑）。

> **環境**：WSL2 / Linux x86-64，gcc + Frida 16.1.11（`pip install "frida==16.1.11" "frida-tools==12.3.0"`）。本章 Frida hook 是真跑貼上的；Pin/DynamoRIO 標「未實測，理論預期」。

Ch 13 的斷點讓你**停下來看**，Ch 14 的 trace 讓你**側錄邊界**。但兩者都有天花板：斷點是被動的（你停、你看，程式凍住），trace 只看得到既有的 syscall/libcall。這一章的 DBI 打破天花板——**你把自己的 code 塞進目標的執行流裡**，讓它一邊跑一邊執行你的觀測/修改邏輯。這是「觀察」升級成「觀察 + 干預」，也是 Ch 12 心法第 3 條「能改就改」的工業級實現。

## 為什麼需要這個？

gdb 斷點對「hook 一個函式、每次呼叫都印參數」這種需求其實很笨：你得斷、看、continue，重複幾百次；要自動化就得寫 gdb 的 `commands` 或 Python API。而且 gdb 一次只盯一個行程、每次 trap 都很貴。

ltrace 能自動印 libcall 參數，但它**只攔函式庫函式**——程式**自己寫的**函式（那個 `check()`、那個 `transform()`）它一概攔不到。而逆向真正想看的，往往就是這些自寫函式。

DBI 補的正是這塊。用一段腳本，你可以：

- **hook 任意位址的任意函式**（含自寫的、inline 邊界清楚的），每次呼叫自動印出參數、回傳、暫存器狀態——不用手動 continue。
- **改掉函式行為**：讓 `check()` 永遠回 `true`、讓某個授權比對永遠相等——直接在記憶體裡改，不用 patch 檔案。
- **記錄執行覆蓋率**：這次執行碰了哪些 basic block、哪些沒碰——fuzzing 和「找出哪段 code 處理我的輸入」的核心。
- **在函式的正中間注入邏輯**：讀某個中間變數、dump 某塊 buffer、解密後攔截明文。

一句話：**DBI 讓你像寫 plugin 一樣為別人的 binary 加 code**，而且目標不用重編、不用有原始碼。

## 先建立直覺：插樁 vs 斷點 vs trace

三支柱的差別，用「你和目標的關係」來看最清楚：

```
   斷點 (gdb)         程式跑到你設的點 → 凍住 → 你手動看 → 你放行
   （被動、外部）      控制權在你手上來回交接，一次一個點

   trace (strace)     程式全速跑 → 你在邊界上被動側錄
   （被動、旁觀）      只看得到穿過 syscall/libcall 邊界的東西

   插樁 (DBI)         你的 code 被「編織」進目標的指令流 → 一起跑
   （主動、內嵌）      目標每跑到你 hook 的地方，就順便執行你的 code
```

DBI 的關鍵字是**編織（interleave）**：你的觀測/修改 code 不在外面等，而是被插進目標的執行流，變成它的一部分一起跑。所以它快（不用行程間切換來回 trap）、細（能插在任意指令）、強（能改任意東西）。代價是重（要載入一整套 DBI runtime、注入 agent），而且更容易被 anti-debug/anti-hook 偵測（Ch 23）。

## 三大工具的定位

DBI 不是一個工具，是一類工具。逆向常見三個，定位差很多，選錯會事倍功半：

| 工具 | 抽象層級 | 腳本語言 | 甜蜜點 | 對逆向 |
|---|---|---|---|---|
| **Frida** | 函式 hook（高階） | JavaScript | hook 函式看/改參數回傳、跨平台（含 Android/iOS） | 逆向/App 破解首選，上手最快 |
| **Intel Pin** | 指令級（低階） | C++（pintool） | 指令計數、記憶體追蹤、覆蓋率、taint | 學術/精細分析，寫 pintool 較重 |
| **DynamoRIO** | 指令級（低階） | C（client） | 同 Pin，開源、跨平台、常做 fuzzing 後端 | fuzzer/覆蓋率工具的引擎 |

**選擇原則**：

- 你想 **hook 某個函式看它參數、或改它行為**（逆向 crackme、破解授權、看 App 加密前的明文）→ **Frida**。這是逆向最常見的需求，Frida 為它而生，一段 JS 就搞定，還接你的 [`android_reversing`](../../security/android_reversing/README.md) 課（Frida 是安卓動態分析的主力）。
- 你想做**指令級的精細測量**（每條指令跑幾次、每次記憶體存取的位址、動態污點分析、精確覆蓋率）→ **Pin** 或 **DynamoRIO**。這是 `symex_taint`（動態 taint）、`afl_plus_plus`（覆蓋率導向 fuzzing）背後的引擎層。
- 你在**建 fuzzer 或覆蓋率工具**→ **DynamoRIO**（開源、常被拿來當 fuzzer 的插樁後端，如 WinAFL 的 DR 模式）。

本課逆向視角以 **Frida** 為主軸真跑；Pin/DynamoRIO 給定位與概念，它們的深度用法在 `symex_taint`/`afl_plus_plus` 課。

## Frida 實戰：hook 一個函式印出參數與回傳（真跑）

用一個 ground-truth 目標。一個會重複呼叫 `add()` 的小程式：

```c
// hello.c
#include <stdio.h>
int add(int a,int b){ return a+b; }
int main(){ for(int i=0;i<3;i++) printf("add=%d\n", add(i, i*2)); return 0; }
```

```bash
$ gcc -O0 -o hello hello.c
$ objdump -d hello | grep -A1 '<add>:'
0000000000001149 <add>:
    1149:  f3 0f 1e fa   endbr64            ← add 在檔案偏移 0x1149
```

目標：**不改程式、不重編**，用 Frida hook `add()`，每次呼叫印出它的兩個參數和回傳值。逆向常態是**沒有符號**，所以我們用「模組基底 + 偏移」定位 `add`（就算 strip 了 `0x1149` 這個偏移還在）：

```javascript
// hook_add.js
const mod = Process.enumerateModules()[0];   // 第一個模組 = 主程式
const addOff = 0x1149;                        // objdump 得到的 add 偏移（PIE 相對）
const target = mod.base.add(addOff);          // 執行期真實位址 = 基底 + 偏移

Interceptor.attach(target, {
  onEnter(args) {
    // System V AMD64：前兩個整數參數在 rdi / rsi
    this.a = this.context.rdi.toInt32();
    this.b = this.context.rsi.toInt32();
  },
  onLeave(ret) {
    console.log("add(" + this.a + ", " + this.b + ") = " + ret.toInt32());
  }
});
```

`Interceptor.attach` 是 Frida 的核心：在目標位址插入一個 hook，`onEnter` 在函式進入時跑（能讀參數）、`onLeave` 在返回時跑（能讀/改回傳）。真跑：

```bash
$ frida -f ./hello -l hook_add.js
     ____
    / _  |   Frida 16.1.11 - A world-class dynamic instrumentation toolkit
   | (_| |
    > _  |
   . . . .   Connected to Local System (id=local)
Spawning `./hello`...
Spawned `./hello`. Resuming main thread!
[Local::hello ]-> add(0, 0) = 0
add(1, 2) = 3
add(2, 4) = 6
add=0
add=3
add=6
Process terminated
```

**成功**。`add(0,0)=0`、`add(1,2)=3`、`add(2,4)=6` 這三行是**我們的 hook** 印的——Frida 把我們的 JS 編織進 `hello` 的執行流，每次它呼叫 `add`，就順便跑我們的 `onEnter`/`onLeave`。夾在中間的 `add=0`/`add=3`/`add=6` 是程式**自己**的 `printf`。我們用一段 JS，在別人的 binary 裡看到了自寫函式的每次呼叫——這是 ltrace 做不到的（`add` 不是 libc 函式），也比 gdb 手動 continue 優雅太多。

> 真跑時 Frida 程序結束會吐一行 `Fatal Python error: _enter_buffered_busy ... at interpreter shutdown` 的 daemon-thread 警告——這是 Frida CLI 在 stdin 上的收尾問題，**不影響 hook 結果**，忽略即可。

### 從「看」到「改」：讓 add 永遠回 100

DBI 的第二半是干預。在 `onLeave` 裡 `ret.replace()` 就改掉回傳值——這正是「讓 `check()` 永遠回 true」繞過授權的原型：

```javascript
Interceptor.attach(target, {
  onLeave(ret) {
    console.log("原本回傳 " + ret.toInt32() + "，改成 100");
    ret.replace(0x64);   // 100，不管實際算出什麼都回這個
  }
});
```

> **未實測（此變體）**：上面「看」的版本是真跑的；`ret.replace()` 這個改法我沒單獨跑，但 `replace` 是 Frida `InvocationReturnValue` 的標準 API，行為為「覆蓋回傳值」。理論預期：程式每次 `add` 都拿到 100。讀者可自行套用驗證。這一招用在真實逆向就是：hook 授權檢查函式，`onLeave` 裡 `ret.replace(1)`，管你密碼對不對都通過。

### frida-trace：更快的 hook

不想寫腳本，`frida-trace -i "函式名"` 自動生成 hook（真跑）：

```bash
$ frida-trace -i "add" ./hello
Spawning `./hello`...
Started tracing 0 functions. Press Ctrl+C to stop.
add=0
add=3
add=6
Process terminated
```

注意 **`Started tracing 0 functions`**——`add` 是 static 函式、**不在動態符號表**，`frida-trace -i` 靠名字找符號，找不到就 hook 不到（跟 ltrace 攔不到自寫函式同一個原因）。這是個真實踩雷：**`frida-trace -i` 靠符號名，strip/static 函式要改用偏移**（回到上面 `Interceptor.attach(base+offset)` 的手法）。對 libc 函式 `frida-trace -i "strcmp"` 這種就很好用，因為符號在。

## Frida 對逆向的殺手級用途

超出本章 hello 範例，Frida 在真實逆向裡最常做這幾件事：

1. **看加密/雜湊前的明文**：App 把資料加密後才送出，靜態看到的都是密文。hook 加密函式的 `onEnter`，明文就在參數裡——這是逆向 App 協定、破解通訊的標準招（接 `android_reversing`）。
2. **繞過檢查**：hook root 偵測 / 憑證固定（cert pinning）/ 授權檢查函式，`onLeave` 改回傳值讓它永遠通過。
3. **dump 執行期才有的東西**：解密後的字串、動態生成的 key、runtime 才組出來的 URL——hook 到「它剛做好、還沒用掉」的那一刻，`Memory.readByteArray` 抓出來。
4. **摸清呼叫關係**：hook 一批函式印 backtrace（`Thread.backtrace`），看誰呼叫誰，重建控制流。

## Pin / DynamoRIO：指令級的重武器（未實測，概念）

> **未實測，理論預期**：本課環境未裝 Intel Pin / DynamoRIO（Pin 需 Intel 官網下載、DynamoRIO 需另編）。以下是定位與概念，指令為示範性，讀者自行以官方 SDK 重現。

Frida 的抽象層級是「函式」，Pin/DynamoRIO 的抽象層級是「**指令**」。它們讓你在**每一條指令**執行前/後插入 C/C++ callback。這開啟 Frida 做不到（或很慢）的精細分析：

- **指令計數 / profiling**：統計每個 basic block 跑了幾次，找熱點。
- **記憶體存取追蹤**：記錄每一次 load/store 的位址與值——動態污點分析（taint，接 `symex_taint`）的基礎。
- **精確覆蓋率**：這次執行碰了哪些指令/邊，餵給 fuzzer 當回饋（`afl_plus_plus` 的 DBI 模式）。

一個 Pin 的 pintool 概念骨架（示範，非真跑）：

```cpp
// 概念：對每條指令插一個「計數 +1」的 callback（Pin pintool 骨架）
#include "pin.H"
UINT64 icount = 0;
VOID docount() { icount++; }
VOID Instruction(INS ins, VOID *v) {
    // 在每條指令執行前插入 docount()
    INS_InsertCall(ins, IPOINT_BEFORE, (AFUNPTR)docount, IARG_END);
}
VOID Fini(INT32 code, VOID *v) { printf("共執行 %lu 條指令\n", icount); }
int main(int argc, char *argv[]) {
    PIN_Init(argc, argv);
    INS_AddInstrumentFunction(Instruction, 0);
    PIN_AddFiniFunction(Fini, 0);
    PIN_StartProgram();
    return 0;
}
```

DynamoRIO 的 client 概念類似（C API，`dr_insert_clean_call` 插 callback）。逆向者真的動手寫 pintool，通常是為了「精確測量」或「自動化污點/覆蓋率」——一般 hook 需求 Frida 更快。想深挖走 `symex_taint`（taint）和 `afl_plus_plus`（覆蓋率 fuzzing）。

## 對比與取捨：DBI vs 斷點 vs trace

| 需求 | 最佳工具 | 理由 |
|---|---|---|
| hook 一個函式看每次參數/回傳 | Frida | 一段 JS，自動、不用手動 continue |
| 改掉函式行為繞過檢查 | Frida | `onLeave` `ret.replace()` 直接改，不 patch 檔案 |
| 看 libc 函式呼叫（strcmp/fopen） | ltrace（先）或 Frida | ltrace 零 setup；要改行為才上 Frida |
| 精確指令計數 / 記憶體追蹤 / 覆蓋率 | Pin / DynamoRIO | 指令級 callback，Frida 太粗 |
| 只想停在一點慢慢看記憶體 | gdb 斷點 | DBI 殺雞用牛刀 |
| 目標會 anti-hook / 高度混淆 | 看情況 | DBI runtime 更易被偵測，Ch 23 |

心法：**能用 ltrace/gdb 解決的別上 DBI**（DBI 重、易被偵測）；**要 hook 自寫函式、要自動化、要改行為，Frida 是最佳點**；**要指令級精密測量，才動 Pin/DynamoRIO**。

## 踩雷集錦

1. **`frida-trace -i "func"` 對 strip/static 函式回報 `0 functions`**（本章真踩）：`-i` 靠符號名找函式，strip 掉或 static 的函式沒有動態符號。改用 `Interceptor.attach(module.base.add(offset))` 靠偏移 hook。libc 函式（符號在）才適合用 `-i`。
2. **PIE 忘了加基底**：`Interceptor.attach(ptr(0x1149))` 會 attach 到絕對位址 `0x1149`（根本不是你的函式）。PIE 必須 `module.base.add(0x1149)`。跟 Ch 13 gdb 的 PIE 基底問題同源。
3. **從錯的暫存器讀參數**：Frida 的 `args[0]` 對「有正確 ABI 資訊時」好用，但 hook 一個裸位址、Frida 不知道函式簽名時，穩妥做法是照 System V ABI 從 `this.context.rdi/rsi/...` 讀（本章就是這樣）。搞錯 calling convention 就讀到垃圾。
4. **改回傳值改錯地方**：想繞過檢查卻改了呼叫方的暫存器——回傳值在 `rax`，`onLeave` 的 `ret`/`retval` 對的就是它。改中間某個暫存器不會改變「函式回傳什麼」。
5. **DBI 觸發 anti-debug 卻怪工具壞掉**：目標偵測到被插樁（掃 `int3`、比對 code 是否被改、檢查 `/proc/self/maps` 有沒有 Frida agent）就變臉或退出。看到「一 hook 就行為異常」先懷疑 anti-hook（Ch 23），不是工具壞。
6. **Frida 版本 / Python 版本不合**：Frida 17 需要較新的 Python typing（3.11+），在舊環境（如 WSL 的 Python 3.10）會 `ImportError: cannot import name 'NotRequired'`。降到 `frida==16.1.x` + `frida-tools==12.3.x` 相容。這是本章環境的真實坑。

## 進階：再往深一層

- **Stalker——Frida 的指令級追蹤**：Frida 不只能 hook 函式，`Stalker` 能追蹤 thread 執行過的每個 basic block，做覆蓋率/trace，把 Frida 推到接近 Pin/DR 的層級（雖然機制不同）。想在 Frida 裡做覆蓋率導向的東西看它。
- **DBI 的實作原理**：Frida/Pin/DR 底層都是**動態重編譯（dynamic recompilation）**——把目標的 code 一塊塊複製到一個 code cache，複製時把你的插樁 code 織進去，然後執行副本而非原本。這就是為什麼它能「在任意指令插 code」又「不改原始 binary」。理解這層，你會懂為什麼 self-modifying code 和某些 anti-DBI 手法能干擾它。
- **DBI 之於 fuzzing 與符號執行**：覆蓋率回饋（`afl_plus_plus`）、動態污點（`symex_taint`）、concolic 執行——這些進階技術的動態觀測層，底下往往就是 DBI（Pin/DR/Frida-Stalker）。本章的 hook 是入門，那些課是它的工業應用。

## 本章重點整理

- DBI 是可觀察性第三支柱：**在 binary 執行時注入你自己的 code**，把「觀察」升級成「觀察 + 干預」。關鍵字是**編織**——你的 code 被插進目標的指令流一起跑。
- 三大工具定位：**Frida**（函式 hook、JS、逆向/App 破解首選、接 android）、**Pin**（指令級、C++、學術/精細）、**DynamoRIO**（指令級、C、開源、fuzzer 引擎）。逆向 hook 需求選 Frida，指令級測量才動 Pin/DR。
- Frida `Interceptor.attach` 的 `onEnter`/`onLeave` 是核心：看參數、看/改回傳。逆向殺手級用途是看加密前明文、繞過檢查、dump runtime 才有的東西、重建呼叫關係。
- 靠偏移（`module.base.add(off)`）hook 才能對付 strip/static 函式——`frida-trace -i` 靠符號名，對無符號函式失效。

## 自我檢核

- [ ] 我能說出 DBI 和 gdb 斷點、strace/ltrace 的本質差別（編織 vs 被動停/側錄）
- [ ] 我能說出 Frida / Pin / DynamoRIO 各自的抽象層級與甜蜜點，並對一個 hook 需求選對工具
- [ ] 我能寫一個 Frida `Interceptor.attach`，靠模組基底+偏移 hook 一個無符號函式、印出參數
- [ ] 我知道 `onLeave` 改回傳值就能繞過檢查，以及這對應真實逆向的哪種場景
- [ ] 我知道 `frida-trace -i` 為什麼對 static/strip 函式回報 0，以及該怎麼改用偏移

## 延伸閱讀

### 官方文件 / 工具

- **[Frida 官方文件](https://frida.re/docs/home/)**
  - **讀哪裡**：JavaScript API 的 `Interceptor`、`Module`、`Memory`、`Stalker`；「Functions」與「Messages」教學。逆向 hook 的一手參考
  - **前提**：`pip install frida-tools`（本課用 16.1.x 相容舊 Python）
- **[Intel Pin — User Guide](https://www.intel.com/content/www/us/en/developer/articles/tool/pin-a-dynamic-binary-instrumentation-tool.html)** 與 **[DynamoRIO](https://dynamorio.org/)**
  - **讀哪裡**：Pin 的 MyPinTool 範例、Instruction/Trace 插樁；DynamoRIO 的 client API 教學。想做指令級測量才需要

### 書籍 / 課程

- **你自己的 [`android_reversing`](../../security/android_reversing/README.md) 課**
  - **這是什麼**：Frida 在安卓的完整實戰（hook Java/native、繞 root/pinning、脫殼）——本章的 Frida 是通用入門，平台深度在那
- **你自己的 [`symex_taint`](../symex_taint/README.md) 與 [`afl_plus_plus`](../afl_plus_plus/README.md) 課**
  - **這是什麼**：DBI 的工業應用——動態污點分析與覆蓋率導向 fuzzing，Pin/DR 是它們的插樁引擎
- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **讀哪幾章**：Ch 9（binary instrumentation，Pin 實戰）——DBI 原理與 pintool 寫法

DBI 讓我們注入 code 去看去改。下一章把斷點 + watchpoint + DBI 這些能力聚焦到一個問題上：**追一個值/一段輸入怎麼流過整個程式**——輸入進來、變換、比較、落到記憶體哪裡。

→ [Ch 16 記憶體與資料流動態追蹤](./16-dynamic-data-flow-tracking.md)
