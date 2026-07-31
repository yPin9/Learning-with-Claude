# Ch 0 — 環境搭建：編 V8、d8 debug build、%DebugPrint、gef

> **目標**：把一套能真的打 V8 的工作台架好——從原始碼編出一個帶除錯功能的 `d8`，理解每個 build flag 在利用流程的哪一段幫到你，並搞懂這門課最要命的一件事：**V8 沒有「一個版本」，你的 exploit 綁死一個 git commit**。這章之後你手上會有一個能執行 JS、能 dump 物件內部、能反組譯 JIT code、能掛 gdb 的 V8。

> **環境**：本課主線是 **WSL2 上的 Ubuntu 24.04、x86-64**，V8 從原始碼編（`is_debug=false` 的 release build + 除錯 intrinsics）。作者實機的環境指紋是 **V8 15.3.0（candidate）、git commit `ab2cad06`（2026-07-31 tip-of-tree）**，16 核 / 15 GB RAM / Linux ext4 檔案系統（**檔案系統這點很重要**，見下文）。下面所有 `d8` 輸出都是在這個版本真的跑出來的。**你的 V8 版本不同時，物件的記憶體佈局、elements kind 常數、sandbox 行為都會不一樣**——這正是本章要你在意的事。

## 為什麼需要這個？

打 glibc heap 時，你至少還能 `apt install` 一個現成的 libc 來對。V8 沒這種好事。

原因有三個，每個都會在後面咬你：

1. **公開的 Chrome 不帶你需要的工具**。你從官網裝的 Chrome 是 release build，`%DebugPrint`（把物件內部 dump 出來的除錯 intrinsic）被關掉、符號被 strip、`--allow-natives-syntax` 這個「開後門」的旗標預設不給用。而這門課從頭到尾都靠這些看 V8 的五臟六腑。你**必須自己編**。

2. **V8 改得比 glibc 快一個數量級**。glibc 一年出一兩個 minor 版；V8 主分支一天幾十個 commit，物件佈局、`Map` 欄位、pointer compression 的細節三個月就可能變樣。網路上 2019 年的 V8 exploit writeup，一半的 offset 直接失效——不是技巧錯，是**環境漂移**。所以我們不說「V8 15」這種粗話，我們釘 git commit。

3. **利用高度依賴「這個 build 是怎麼編的」**。開不開 pointer compression、開不開 sandbox、debug 還是 release——同一個漏洞在不同 build config 下，利用手法可能完全不同。你得先搞懂這些旋鈕各自是什麼，才不會拿著別人 no-sandbox build 的 exploit 在你 sandbox build 上撞牆。

先講一件定調的事：**這門課刻意站在「sandbox 已上線」的現代地面上**。V8 Sandbox（又叫 ubercage）從 2022 年起逐步預設開啟，它把「拿到任意讀寫就能直接改 `TypedArray` 的 backing store pointer 打穿」這條經典路斬斷了（細節留到 [Ch 34](./34-v8-sandbox.md)）。我們主線 build 就**開著 sandbox**，讓你一開始就活在真實世界；需要示範「sandbox 出現前的經典打法」時，會另外編一個 `v8_enable_sandbox=false` 的 build，並在該章明講「這是關掉護欄看古蹟」。

## 先建立直覺：從 JS 到機器碼，你要掌握的工具鏈

在裝任何東西之前，先有一張圖。你手上會有一段惡意 JavaScript，目標是讓 V8 對自己的記憶體產生錯誤認知。這條路上每一段對應一個你這章要架好的能力：

```
        ┌──────────────────────────────────────────────────────────┐
        │          一次 V8 pwn 會用到的觀測與除錯能力               │
        └──────────────────────────────────────────────────────────┘

   寫 exploit.js
        │
        ▼
   d8 --allow-natives-syntax exploit.js   ← 你自己編的 d8（V8 的 REPL/shell）
        │                                    --allow-natives-syntax 開啟 %Foo() 後門
        ▼
   %DebugPrint(obj)  ──────────────────►  印出物件的 Map 指標、elements 指標、
        │                                  型別、in-object 欄位——「透視眼」
        ▼
   %SystemBreak() / gdb attach  ────────►  在關鍵點停下，用 gef 看記憶體
        │                                  x/8gx <addr> 逐 8 byte 看堆
        ▼
   --print-bytecode / --print-opt-code ─►  看 Ignition bytecode、看 TurboFan
        │                                  吐出來的機器碼（JIT 出了什麼）
        ▼
   turbolizer（可選）─────────────────►  用瀏覽器圖形化看 sea-of-nodes IR
```

你不用一次記住全部。這章只做三件事：**把 V8 編出來、把 d8 的除錯開關認一遍、把 gef 掛上去確認能看記憶體**。工具的深入用法散在後面各章（`%DebugPrint` 的欄位怎麼讀在 [Ch 5](./05-map-hidden-class.md)、看 JIT code 在 [Ch 11](./11-optimization-pipeline.md)）。

## 為什麼在 Linux / WSL 編，而且一定要在 Linux 檔案系統上

先擋一個會讓你浪費半天的坑。

V8 的官方 build 工具鏈（`depot_tools` + `gn` + `ninja`）在 Linux 上最順。Windows 原生也能編 Chrome/V8，但要裝 Visual Studio + 一堆 SDK，對「學利用」這件事是純粹的摩擦。**我們用 WSL2 的 Ubuntu**，跟你 `binary_exploitation` 那套環境同源。

關鍵的坑：**原始碼和 build 產物一定要放在 WSL 的 Linux 原生檔案系統（例如 `~/v8build`），不要放在 `/mnt/d/` 或 `/mnt/c/`（Windows 磁碟）**。WSL2 跨 `9p` 協定存取 Windows 檔案有巨大的 I/O 開銷，V8 有數十萬個小檔，放在 `/mnt/` 上 build 會慢到你以為當機。作者實測環境：

```
$ uname -a
Linux DESKTOP-... 6.18.x-microsoft-standard-WSL2 ... x86_64 GNU/Linux
$ nproc
16
$ free -h | head -2
               total        used        free      shared  buff/cache   available
Mem:            15Gi       560Mi        14Gi       3.0Mi       931Mi        14Gi
$ df -h ~ | tail -1
/dev/sdd       1007G  5.1G  951G   1% /          # ← 這是 ext4，不是 /mnt/d
```

規格底線：**磁碟至少留 30 GB**（原始碼約 10+ GB，out 目錄 build 產物又要一截），**RAM 8 GB 起跳**（link 階段吃記憶體，16 核平行 link 更兇），核心越多編越快但也越吃 RAM。RAM 不夠時把 ninja 的平行度調低（`ninja -j4`）。

## Step 1：depot_tools

Google 的專案不用 `apt` 或 `pip` 管相依，用一套自己的工具 `depot_tools`。裡面有 `fetch`（拉原始碼 + 相依）、`gclient`（同步相依樹）、`gn`（產生 build 設定）、`ninja`（實際編譯）。這些工具 V8 官方文件當作前提，你先把它抓下來加進 `PATH`：

```bash
mkdir -p ~/v8build && cd ~/v8build
git clone --depth 1 https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$HOME/v8build/depot_tools:$PATH"   # 建議寫進 ~/.bashrc
```

`--depth 1` 是 shallow clone，只要最新一版、不要整部 git 歷史，省時間省空間。`depot_tools` 第一次被呼叫時會自動 bootstrap 一份自帶的 Python（`vpython3`）和 CIPD client（拿預編好的工具二進位），所以你系統只要有基本的 `git` 和 `python3` 就夠，不用先裝一堆東西。

> **踩雷**：如果你 `PATH` 裡系統的 `git`/`python` 版本很舊，depot_tools 有時會有奇怪的行為。WSL Ubuntu 24.04 內建的 `git 2.4x` / `python 3.12` 完全夠用。另一個常見坑是公司網路的 proxy 擋 `chromium.googlesource.com`——這門課全程都要連得到這個 host。

## Step 2：fetch V8

```bash
cd ~/v8build
fetch --no-history v8
```

`fetch v8` 做的事：clone V8 原始碼，然後跑 `gclient sync` 把 V8 依賴的一大堆第三方東西（build 工具、測試套件、自帶的 clang 編譯器、sysroot……）全部拉齊。**V8 在 Linux 上不是用你系統的 gcc/clang 編的，而是用它自己下載的一份釘定版 clang**——這是為什麼你不用煩惱系統編譯器版本，但也是為什麼第一次 fetch 要下載好幾 GB。

`--no-history` 讓底層 clone 不帶完整 git 歷史，明顯減少下載量和磁碟。代價是你之後要 `git log` 追很久以前的 commit 時歷史不全——但 Part 5 教 patch diffing 時我們會另外針對特定 commit 抓，不影響。

fetch 完成後目錄長這樣（作者實跑輸出）：

```bash
$ ls ~/v8build
depot_tools  v8
$ cd ~/v8build/v8
$ git log --oneline -1
ab2cad06 [turbofan] Disable additive safe integer feedback
$ grep -E 'define V8_(MAJOR|MINOR|BUILD|PATCH)' include/v8-version.h
#define V8_MAJOR_VERSION 15
#define V8_MINOR_VERSION 3
#define V8_BUILD_NUMBER 0
#define V8_PATCH_LEVEL 0
```

也就是 **V8 15.3.0，停在 commit `ab2cad06`**。**把這個版本號和 commit hash 記下來、寫進你的筆記。** 這門課後面每一個 offset、每一個 `%DebugPrint` 的欄位順序，都是對著這個版本說的。哪天你更新了 V8，某個 exploit 突然壞掉，第一件事就是 `git log` 看你現在停在哪個 commit、和寫 exploit 時差了什麼。

> **注意**：`fetch` 之後直接 `gn gen` 可能會抱怨缺系統套件（作者實機就卡在缺 `pkg-config`）。V8 在 Linux 上雖然自帶 clang + sysroot，但仍需要幾個宿主系統的工具/開發檔（`pkg-config`、`libglib2.0-dev`）。缺什麼就 `sudo apt-get install` 什麼；缺很多時直接跑 V8 附的 `./build/install-build-deps.sh`（要 sudo，一次裝齊但比較大包）。

## Step 3：用 gn 產生 build 設定

`gn`（Generate Ninja）把「你想怎麼編」的一堆設定，轉成 `ninja` 看得懂的 build 檔。我們的主線設定：

```bash
cd ~/v8build/v8
gn gen out/x64.release --args='
  is_debug=false
  target_cpu="x64"
  v8_enable_object_print=true
  v8_enable_disassembler=true
  v8_enable_backtrace=true
  symbol_level=1
  v8_enable_sandbox=true
  v8_enable_pointer_compression=true
'
```

成功時 gn 很快（作者實機 383ms）：

```
Done. Made 903 targets from 229 files in 383ms
```

這串 args 沒有一個是隨便填的，逐個講清楚（**別背，理解每個旋鈕控制什麼**）：

| gn arg | 值 | 為什麼這樣選 |
|---|---|---|
| `is_debug` | `false` | 我們要 **release** build。full debug build（`is_debug=true`）慢又大，且行為和真實 Chrome 差更多。release 才貼近你最終要打的目標。 |
| `target_cpu` | `"x64"` | 本課主線 x86-64。 |
| `v8_enable_object_print` | `true` | **這門課的命脈**。開了才有 `%DebugPrint`、`obj.__proto__` 之類的內部 dump，讓你看見 Map / elements 指標。release build 預設關這個，所以一定要手動開。 |
| `v8_enable_disassembler` | `true` | 開了才能 `--print-opt-code` 把 TurboFan 產生的機器碼反組譯出來看。Part 2、Part 4 大量用到。 |
| `v8_enable_backtrace` | `true` | crash 時給比較有用的 stack trace，triage 漏洞時省命。 |
| `symbol_level` | `1` | 保留函式名等符號（`1` 是行號級較輕量，`2` 最完整但更大）。gef 裡看 backtrace 有名字，比一堆裸位址好太多。 |
| `v8_enable_sandbox` | `true` | 主線**開 sandbox**，活在現代真實世界（見前文定調）。 |
| `v8_enable_pointer_compression` | `true` | 現代 V8 預設開；它把 64-bit 指標壓成 32-bit 存（[Ch 4](./04-pointer-compression.md) 專章）。開著才符合你要打的真實目標的記憶體佈局。 |

> **踩雷**：很多舊 writeup 的 gn args 裡沒有 `v8_enable_sandbox`（那時還沒這東西）或明確設 `false`。你如果照抄，編出來的 d8 記憶體佈局和現代 Chrome 不同，練的手法會對不上。**看 writeup 先看它的 build config**，就像 heap 題先看 glibc 版本。

想要一個「關掉護欄看古蹟」的對照 build（後面某些經典章節會用），另外 gen 一個目錄就好，兩個 build 可以並存：

```bash
gn gen out/x64.release.nosbx --args='is_debug=false target_cpu="x64" v8_enable_object_print=true v8_enable_disassembler=true v8_enable_sandbox=false v8_enable_pointer_compression=true'
```

想看某個 arg 到底有哪些可選值、預設是什麼：

```bash
gn args out/x64.release --list --short          # 列全部
gn args out/x64.release --list=v8_enable_sandbox # 查單一個
```

## Step 4：ninja 編譯 d8

```bash
ninja -C out/x64.release d8
```

`-C out/x64.release` 指定 build 目錄，`d8` 是我們要的 target——V8 的獨立 shell/REPL，就是我們整門課的靶機。ninja 會自動吃滿你的核心平行編。作者這個 config 一共約 **2477 個 build 步驟**：

```
ninja: Entering directory `out/x64.release'
[1/2477] ACTION //third_party/partition_alloc/...
[10/2477] CXX obj/build/rust/allocator/...
...
```

（沒看錯，現代 V8 build 裡混了 Rust——`partition_alloc` 等元件有 Rust 部分，ninja 會連 Rust toolchain 一起帶，這些都是 `fetch` 時一併拉下來的，你不用另外裝。）

**第一次編很久**：即使用 V8 自帶的預編 clang，從零編 d8 在 16 核機器上通常也要**十幾分鐘到半小時**（吃 RAM 的是最後的 link 階段）。之後只改幾個檔再編就是增量編譯，很快。RAM 吃緊就 `ninja -C out/x64.release d8 -j6` 降平行度換記憶體。

> **有一個更懶的方式**：V8 提供 `tools/dev/gm.py`（"gm" = go make），一行 `alias gm=~/v8build/v8/tools/dev/gm.py` 之後 `gm x64.release d8` 會自動 gn gen + ninja。但它用的是內建的預設 args，**不含我們要的 `v8_enable_object_print`**，所以這門課用手動 gn 的方式把旗標控制在自己手上。知道 gm.py 存在即可。

編完，冒煙測試（作者實跑輸出）：

```bash
$ ~/v8build/v8/out/x64.release/d8 -e 'print("hello from d8: " + (6*7))'
hello from d8: 42
```

跑得出來，你的靶機就活了。建議 `alias d8=~/v8build/v8/out/x64.release/d8` 省打字。

## Step 5：認識 d8 的除錯開關

`d8` 有一票旗標是這門課的日常。現在先各跑一次認個臉，之後每章會深用。以下都是作者在 V8 15.3.0 上實跑的輸出。

**`--allow-natives-syntax`**：開啟 `%Foo()` 內部函式（intrinsics）。沒有它，`%DebugPrint` 會被當成語法錯誤。看一個 3 元素浮點陣列的內部：

```bash
$ d8 --allow-natives-syntax -e 'let a=[1.1,2.2,3.3]; %DebugPrint(a);'
DebugPrint: 0x2c710104b089: [JSArray]
 - map: 0x2c710100cfc9 <Map[16](PACKED_DOUBLE_ELEMENTS)> [FastProperties]
 - prototype: 0x2c710100c935 <JSArray[0]>
 - elements: 0x2c710104b069 <FixedDoubleArray[3]> [PACKED_DOUBLE_ELEMENTS]
 - length: 3
 - properties: 0x2c71000007e5 <FixedArray[0]>
 - All own properties (excluding elements): {
    0x2c7100000e39: [String] in ReadOnlySpace: #length: ... (const accessor descriptor, attrs: [W__])
 }
 - elements: 0x2c710104b069 <FixedDoubleArray[3]> {
           0: 1.1 (0x3ff199999999999a)
           1: 2.2 (0x400199999999999a)
           2: 3.3 (0x400a666666666666)
 }
0x2c710100cfc9: [Map] in OldSpace
 - type: JS_ARRAY_TYPE
 - instance size: 16
 - elements kind: PACKED_DOUBLE_ELEMENTS
 - back pointer: 0x2c710100cf85 <Map[16](HOLEY_SMI_ELEMENTS)>
   ...（Map 的 transition / descriptor 細節先略，Ch 5 再逐格讀）
```

現在你完全看不懂沒關係——但注意那幾個關鍵字：`map:`、`elements:`、`PACKED_DOUBLE_ELEMENTS`、`length: 3`，還有 elements 裡把 `1.1` 印成原始 8-byte `0x3ff199999999999a`（IEEE 754 double 的位元表示——這在 Part 3 做 `addrof` 時是關鍵，你能用一個 double 陣列「攜帶」任意 64-bit 值）。這些就是 Part 1 要一格一格教你讀的東西。能看到這段輸出，代表你的 `v8_enable_object_print` 開對了。

> 上面那些 `0x2c71...` 位址每次跑都不同（ASLR + GC），別背位址；要看的是**結構**。

**`--print-bytecode`**：看 Ignition 產生的 bytecode（[Ch 9](./09-parser-ignition-bytecode.md) 深入）：

```bash
$ d8 --print-bytecode -e 'function f(x){return x+1}; f(1);'
```

**逼 TurboFan 優化並印出機器碼**（[Ch 11](./11-optimization-pipeline.md) 深入）：

```bash
$ d8 --allow-natives-syntax -e 'function f(x){return x+1};
  %PrepareFunctionForOptimization(f); f(1);
  %OptimizeFunctionOnNextCall(f); f(1);'
```

> **踩雷**：`%OptimizeFunctionOnNextCall(f)` 在新版 V8 **必須先呼叫一次 `%PrepareFunctionForOptimization(f)`**，否則會報錯或不生效。這是新手照舊 writeup 最常撞的牆之一——2019 年的碼片段沒有 `Prepare` 那行。本課 [Ch 12](./12-speculation-deopt.md) 會解釋這對 `Prepare`/`Optimize` 的儀式為什麼存在。

## Step 6：把 gdb / gef 掛上去

看記憶體要靠除錯器。gef（GDB Enhanced Features）你在 `security/gdb` 課已經熟了，這裡直接用：

```bash
# 安裝 gef（若還沒）
bash -c "$(wget https://raw.githubusercontent.com/hugsy/gef/main/scripts/gef.sh -O -)"
```

在 exploit 裡想停下來看記憶體，最順的方式是用 `%SystemBreak()`（等價於送自己一個 SIGTRAP），配合 d8 在 gdb 底下跑。`poc.js`：

```js
let a = [1.1, 2.2, 3.3];
%DebugPrint(a);   // 先印出 a 的位址
%SystemBreak();   // 停進 gdb
```

```bash
$ gdb -q --args ~/v8build/v8/out/x64.release/d8 --allow-natives-syntax poc.js
gef> run
# 執行到 %SystemBreak() 會停下，接著就能 x/16gx <剛印出的位址> 看堆
```

之後 [Ch 3](./03-value-representation.md) 開始，我們會反覆 `%DebugPrint` 拿到一個物件位址、`%SystemBreak()` 停下、在 gef 裡 `x/8gx` 逐 8 byte 把物件的 Map、length、elements 指標一格一格認出來。這一步能跑通，你的透視眼就裝好了。

> **關於 ASLR**：和 userland pwn 一樣，本地除錯時可以 `echo 0 | sudo tee /proc/sys/kernel/randomize_va_space` 關掉 ASLR 讓位址穩定，方便學習階段對照。但 pointer compression 開著時，你在 `%DebugPrint` 看到的那些 `0x2f50...` 其實不是傳統意義的堆位址，而是壓縮過的、相對 isolate root 的表示——行為和傳統 ASLR 不完全一樣，這個差異 [Ch 4](./04-pointer-compression.md) 會講清楚。

## 對比：三種你會遇到的 V8 「版本」

| 你手上的 V8 | 帶 `%DebugPrint`？ | 帶 sandbox？ | 適合幹嘛 |
|---|---|---|---|
| 官網下載的 Chrome | ❌（release，關掉） | ✅ | 最終 real-world 打靶對象，但沒除錯能力 |
| 我們編的 `out/x64.release` | ✅（手動開） | ✅ | **這門課主力**：能透視、又貼近現代真實佈局 |
| `out/x64.release.nosbx` | ✅ | ❌ | 學 sandbox 出現前的經典打法（對照組） |

搞混這三個是新手常態。你在 real Chrome 上重現不出 `%DebugPrint`，不是你錯，是那個 build 根本沒編這功能。

## 踩雷集錦

1. **把原始碼放在 `/mnt/d/`**：build 慢到懷疑人生。V8 幾十萬個小檔在 WSL 的 Windows 檔案系統 mount 上跑，I/O 開銷是 Linux 原生 fs 的好幾倍。一定放 `~/`（ext4）。
2. **以為 V8 有「版本號」就夠了**：說「我用 V8 15」跟說「我用某牌汽車」一樣沒資訊量。物件佈局天天變，**要釘 git commit hash**。這是這門課和 heap 課最大的心態差異。
3. **照抄舊 writeup 的 gn args**：沒有 `v8_enable_sandbox` 或設成 `false` 的舊設定，編出來的東西和現代 Chrome 佈局不同。看 writeup 先看它的 build config 和 V8 版本，如同看 heap writeup 先看 glibc 版本。
4. **release build 忘記開 `v8_enable_object_print`**：`%DebugPrint` 沒反應或報錯，整門課沒法玩。這旗標 release 預設關，一定要手動加。
5. **`%OptimizeFunctionOnNextCall` 前忘了 `%PrepareFunctionForOptimization`**：新版 V8 會噴錯。舊碼片段沒有 Prepare 那行，直接抄會壞。
6. **fetch 完直接 gn 卻沒裝系統相依**：作者實機就卡在缺 `pkg-config`（gn 報 `FileNotFoundError: ... 'pkg-config'`）。缺什麼裝什麼，或跑 `build/install-build-deps.sh`。

## 進階：再往深一層

- **debug build 的額外護欄**：`is_debug=true` 或加 `v8_enable_slow_dchecks=true` 會開啟大量 `DCHECK`（內部一致性檢查）。學習期它能在你把物件搞爛的第一時間就 abort 並告訴你哪裡不一致，對「理解為什麼壞」很有價值；但它也會擋掉一些「release 上其實可利用」的狀態。建議：**學漏洞成因時用帶 dcheck 的 build 看清楚，練最終利用時回到 release**。
- **turbolizer**：Part 2 看 TurboFan 的 sea-of-nodes IR 時，純文字很難讀。V8 附帶 `tools/turbolizer` 是個網頁工具，把 `--trace-turbo` 產生的 `.json` 用互動圖顯示。[Ch 10](./10-turbofan-overview.md) 會教怎麼架。
- **cross-compile 到別的架構**：`target_cpu="arm64"` 可以編 ARM64 的 d8（Android 賽道會用到），但本課主線 x64，這留給你自己延伸。
- **編 Chrome 而非只有 d8**：真正的 renderer exploit 最終要在完整 Chrome 上驗證。編整個 Chrome 是幾十 GB、數小時的工程，本課到 [Ch 38](./38-d8-vs-real-chrome.md) 才討論 d8 和 real Chrome 的差異，屆時再說要不要編。

## 動手練習

1. 把 V8 編出來，跑通 `d8 -e 'print(1+1)'`，並把你的 **V8 版本號 + git commit hash** 抄進一個 `notes.md`。這是你這門課的「環境指紋」。
2. 跑 `d8 --allow-natives-syntax -e 'let a=[1,2,3]; %DebugPrint(a);'`，把輸出貼下來。和本章那個浮點陣列的輸出對比：整數陣列的 elements kind 是 `PACKED_SMI_ELEMENTS` 還是別的？（這正是 [Ch 7](./07-jsarray-elements-kind.md) 的主題，先觀察現象。）
3. 額外編一個 `out/x64.release.nosbx`（關 sandbox），用 `d8 -e 'print(1)'` 確認它也活著。之後對照兩個 build 的行為差異時會用到。

## 本章重點整理

- 打 V8 一定要**自己從原始碼編**，因為除錯能力（`%DebugPrint`、disassembler）和現代佈局（sandbox、pointer compression）都得靠 build flag 開。
- V8 **沒有穩定的「版本」概念**，你的 exploit 綁死一個 **git commit**；釘 commit 是這門課的核心紀律。本課環境指紋：**V8 15.3.0 / commit `ab2cad06`**。
- 主線 build config：`is_debug=false` + `v8_enable_object_print/disassembler/sandbox/pointer_compression=true`，每個旗標都對應一個你需要的能力。
- 原始碼放 Linux 原生檔案系統（`~/`），別放 `/mnt/`。

## 自我檢核

- [ ] 能解釋為什麼不能直接用官網 Chrome 來學（而不只是「因為要編」）
- [ ] 知道 `v8_enable_object_print` 開了給你什麼、關了會怎樣
- [ ] 能說出「釘 V8 版本」和 heap 課「釘 glibc 版本」在心態上的差異與更嚴苛之處
- [ ] 知道 sandbox build 和 no-sandbox build 各自拿來做什麼
- [ ] 手上有一個能跑 `%DebugPrint` 的 d8，且記下了自己的版本指紋

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

### 官方文件

- **[V8 官方 build 文件 — v8.dev/docs/build](https://v8.dev/docs/build)** 及 [source checkout](https://v8.dev/docs/source-code)
  - **讀哪裡**：整篇不長。`depot_tools`、`fetch v8`、`gm.py` 的官方說法在這；本章的 Step 1–4 就是它的實作，加上我們針對「利用學習」調過的 gn args。
  - **和本章的關聯**：當你的 build 出問題（fetch 失敗、gn 報錯），這裡是第一手排錯依據。

- **[GN 參考 — gn.googlesource.com](https://gn.googlesource.com/gn/+/main/docs/reference.md)**
  - **讀哪裡**：`gn args` / `gn gen` 段落。想知道某個 `v8_enable_*` 旗標的完整清單時，`gn args out/... --list` 比翻文件快。
  - **注意**：GN 是通用工具，V8 專屬的旗標定義在 V8 原始碼的 `BUILD.gn` / `gni/` 裡，不在 GN 文件。

### 部落格 / 技術文章

- **[V8 團隊部落格 — v8.dev/blog](https://v8.dev/blog)**
  - **這篇說什麼**：V8 團隊對各子系統的第一手設計說明。現在先不用細讀，但把 blog 首頁加書籤——後面每個 Part 幾乎都有對應的官方長文。
  - **為什麼值得讀**：作者就是寫 V8 的人，權威性無可取代，且比多數二手教材新。

- **[各家 V8 exploit writeup 開頭的「build setup」段（如 doar-e、faraz.faith）](https://doar-e.github.io/)**
  - **這篇說什麼**：真實 exploit 文章通常會先交代「我用哪個 V8 commit、什麼 gn args」。
  - **為什麼值得讀**：養成**看 writeup 先看它環境**的習慣——這正是本章反覆強調的紀律。讀的時候刻意找它釘的 commit 和 build config，對照你自己的。

### 工具

- **[Fuzzilli README — github.com/googleprojectzero/fuzzilli](https://github.com/googleprojectzero/fuzzilli)**
  - **讀哪裡**：現在只要看它「Integrating a new engine」那段提到的 V8 build patch/flag——你會發現 fuzzing 用的 build 又是另一套 config（例如需要 coverage 埋點）。先有個印象，Part 5（[Ch 28](./28-fuzzilli-internals.md)）會正式用。
  - **前提**：本章的 gn/ninja 流程先熟，屆時才看得懂它多加了哪些旗標。

環境架好、版本釘死，你手上有了一個能透視的 d8。下一章先拉高視角：在整個 Chrome 的攻擊面裡，V8（renderer）到底是什麼位置、為什麼它是 client-side RCE 的黃金入口。

→ [Ch 1 — 為什麼 renderer 是攻擊面](./01-why-renderer-attack-surface.md)
