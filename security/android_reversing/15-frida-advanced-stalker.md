# Ch 15 — Frida 進階：Stalker、掃描、dump

> **目標**：把 Frida 從「hook 一個已知函式」推到「在**未知**的記憶體裡找東西、追東西、撈東西」。你會學到四把重武器：`Stalker`（指令級追蹤，做 code coverage / 追一段程式碼到底走了哪些分支）、`Memory.scan`（在整個進程記憶體裡按 pattern 找位址）、從記憶體 **dump 出真 `.dex` / class / `.so`**（脫殼與還原被藏起來程式碼的基礎）、以及 `Java.enumerateLoadedClasses` 與 `frida-trace` 這兩個「快速偵察」利器。這章是 Ch 13/14（會 hook 已知目標）跟 Part 5 脫殼（要在記憶體裡撈真程式碼）之間的橋。

> **環境**：AVD（Android 13 / API 33，x86_64，Google APIs）、`Frida 16.x`。本章的 Frida 腳本我在本 repo 沙箱**無法執行**（沒有 AVD/Frida），所有腳本都是 Frida 16.x 的標準 API 寫法、逐行解釋、標「**未實測，理論預期行為**」並附你在自己 AVD 上的驗證步驟；純資料結構（DEX header 欄位、pattern 位元組）用 Python 3 實跑，標「**實際輸出**」。

## 為什麼需要這個？

到 Ch 14 為止，你的 Frida 技能是「我**知道**要 hook 哪個類的哪個方法，把它攔下來印參數」。但真實逆向裡，一半的時間你**根本不知道目標在哪**：

- App 加固了，`classes.dex` 在磁碟上是加密的，真 DEX 只在執行期解密到記憶體某處——你要能**在記憶體裡把它找出來 dump 下來**。
- 某個字串（金鑰、URL）你知道值但不知道它在哪個函式被用——你要能**掃記憶體找到它的位址**，再回頭看誰在讀它。
- 一個函式很長、控制流被混淆，你 hook 它進出口只知道「進來了、出去了」，但**中間走了哪條分支、呼叫了誰**你看不到——你要能**指令級追蹤**它。

這章的四個工具正好對應這四種「目標未知」的情境。它們共同的底層前提是：**執行期的記憶體裡，資訊是最全的**（Ch 1 講過，加固殼在執行期一定得把真程式碼還原到記憶體）。Frida 的價值就是給你一支能任意讀寫這塊記憶體、還能在任意位址插程式碼的手術刀。

## 先建立直覺：Frida 手上有整個進程的記憶體

先把心智模型立起來。一個進程的記憶體長這樣，Frida 注入後能碰到的東西標在右邊：

```
        target 進程的虛擬記憶體                    Frida 能做什麼
 ┌────────────────────────────────────┐
 │ ELF: libc.so / libart.so / lib*.so │◀── Module.findExportByName 找函式
 │   .text (機器碼)                   │◀── Stalker 追這裡每一條指令
 │   .rodata (常數/字串)              │◀── Memory.scan 掃這裡找 pattern
 ├────────────────────────────────────┤
 │ heap (malloc 出來的)               │◀── 真 DEX 解密後常落在這；scan + dump
 │   ┌── 一塊 "dex\n035\0..." 的區域  │◀── DEX magic！dump 它就是脫殼
 ├────────────────────────────────────┤
 │ ART 內部結構                       │
 │   已載入的 Class / DexFile 物件    │◀── Java.enumerateLoadedClasses 列出
 ├────────────────────────────────────┤
 │ stack (每個 thread 一個)           │
 └────────────────────────────────────┘
         ▲
         │ Range 資訊從 /proc/self/maps 來
         │ (r-x 可執行、rw- 可讀寫、權限決定能不能掃/dump)
    Process.enumerateRanges('r--') 列出所有可讀區段
```

三個關鍵認知：

1. **記憶體是分段的，每段有讀寫執行權限**。掃字串要掃可讀段（`r--` 以上），追指令要在可執行段（`r-x`），dump DEX 要找可讀段裡的 DEX magic。搞錯權限就掃到一半 crash（讀到不可讀的頁面）。
2. **Frida 用 `/proc/self/maps` 拿到這張記憶體地圖**。`Process.enumerateRanges()` 底層就是讀這個。你在 AVD 上 `adb shell cat /proc/<pid>/maps` 看到的，跟 Frida 看到的是同一張表。
3. **「找東西」和「hook」是兩件事**。前 14 章都在 hook（已知位址插程式碼）；這章的 scan/dump 是**先找到未知位址**，找到之後你才有辦法 hook 或 dump。順序是先找後 hook。

## 工具一：`Java.enumerateLoadedClasses` — 最快的 Java 層偵察

在 hook 之前你得先知道「有哪些類可以 hook」。加固/混淆過的 App，類名可能是 `a.a.a.b`，你 jadx 看到的名字跟執行期載入的名字可能對不上（動態載入、反射、insertDexClassLoader）。`Java.enumerateLoadedClasses` 直接問 ART：**現在這個進程裡到底載入了哪些類？**

```javascript
// list_classes.js —— 列出目前已載入、且類名含關鍵字的類
Java.perform(function () {
    var keyword = "sign";           // 你在找跟簽名有關的類
    Java.enumerateLoadedClasses({
        onMatch: function (name) {  // 每找到一個已載入的類就回呼一次
            if (name.toLowerCase().indexOf(keyword) !== -1) {
                console.log("[class] " + name);
            }
        },
        onComplete: function () {
            console.log("[*] enumeration done");
        }
    });
});
```

逐行的重點：

- `Java.enumerateLoadedClasses` 是**枚舉「已載入」**的類，不是「APK 裡所有」的類。一個類要被 ART 載入（第一次用到時 lazy load）才會出現。所以**你得先操作 App 走到那個功能**，相關的類才會被載入、才掃得到。這是最常見的坑：App 剛啟動就掃，登入相關的類還沒載入，你當然找不到。
- `onMatch` 每個類名回呼一次，`onComplete` 全部枚舉完呼叫一次——這是 Frida 枚舉類 API 的通用形狀（`enumerateRanges`、`enumerateModules` 都長這樣）。
- 大小寫：類名是 case-sensitive 的全限定名（`com.demo.crypto.SignUtil`），所以我用 `toLowerCase()` 做寬鬆比對。

> **未實測，理論預期行為**。在你 AVD 上驗證：`frida -U -f com.demo.app -l list_classes.js`，然後在 App 裡點「登入」讓相關類載入，再回終端看有沒有印出 `[class] com.demo.crypto.SignUtil` 之類。搭配動態載入的加固 App，你會看到一堆 jadx 裡看不到的類名——那些就是執行期才 decrypt 出來的真類。

配套的一招：找到類之後，列出它的所有方法，決定 hook 哪個：

```javascript
// 列出某個類的所有宣告方法（含 overload signature）
var cls = Java.use("com.demo.crypto.SignUtil");
cls.class.getDeclaredMethods().forEach(function (m) {
    console.log(m.toString());   // 印出完整簽名，含參數型別，方便挑 overload
});
```

`getDeclaredMethods()` 走的是 Java 反射（`java.lang.Class` 的方法），印出來的簽名包含參數型別——當一個方法有多個 overload 時，你需要這個資訊才能在 hook 時用 `.overload('java.lang.String', 'int')` 精確指定（Ch 13 講過 overload 的坑）。

## 工具二：`Memory.scan` — 在記憶體裡按 pattern 找位址

`Memory.scan` 解決「我知道要找的位元組長什麼樣，但不知道它在哪」。經典用途：找 DEX magic（脫殼）、找已知的金鑰位元組、找某個特徵碼（做 inline hook 前先定位函式）。

### 底層機制：pattern 語法與掃描範圍

```
Memory.scan(baseAddress, size, pattern, callbacks)
                │          │       │
                │          │       └─ "64 65 78 0a 33 ??" 十六進位，?? = 萬用位元組
                │          └─ 要掃多少 bytes
                └─ 從哪個位址開始掃

pattern 例："64 65 78 0a"  對應 ASCII "dex\n"  ← DEX magic 前 4 byte
            "3? 3? ?? 00"   3 開頭的兩個 nibble + 任意 byte + 0x00
```

先用 Python 確認 DEX magic 的實際位元組（**實際輸出**）：

```python
>>> b"dex\n035\x00".hex(" ")
'64 65 78 0a 30 33 35 00'
```

所以掃「任何版本的 DEX 開頭」的 pattern 是 `64 65 78 0a 3? 3? 3? 00`（`dex\n` + 三個版本數字 nibble 高位固定是 `3`（ASCII `'0'`–`'9'` 是 `0x30`–`0x39`）+ 結尾 `\0`）。這個 `3?` 不是 magic number 亂寫：ASCII 數字 `'0'`(0x30) 到 `'9'`(0x39) 高 nibble 恆為 3、低 nibble 才變，所以 `3?` 精準匹配「一個 ASCII 數字」。

### 範例：掃全進程找 DEX

```javascript
// scan_dex.js —— 掃所有可讀記憶體，找 DEX magic
Java.perform(function () {
    var pattern = "64 65 78 0a 33 ?? ?? 00";   // dex\n + 版本
    // 列出所有「可讀」的記憶體區段，逐段掃
    Process.enumerateRanges('r--').forEach(function (range) {
        Memory.scan(range.base, range.size, pattern, {
            onMatch: function (address, size) {
                console.log("[dex?] found at " + address);
                // 讀 header 裡的 file_size 欄位確認（offset 0x20，小端 u32）
                var fileSize = address.add(0x20).readU32();
                console.log("       declared file_size = " + fileSize);
            },
            onError: function (reason) {
                // 掃到剛好被回收/權限變動的頁面會觸發，略過即可
            },
            onComplete: function () {}
        });
    });
});
```

要點：

- **逐段掃，不是掃整個位址空間**。虛擬位址空間有 128TB，中間絕大多數沒映射，你不能從 0 掃到底——會立刻讀到未映射頁面 crash。正確做法是 `enumerateRanges` 拿到「實際有映射且可讀」的區段清單，只掃這些。
- `onError` 一定要處理。掃描過程中別的 thread 可能在 free/mprotect 記憶體，掃到那一刻頁面權限變了就報錯。忽略單一區段的錯、繼續掃下一段，比整個腳本 crash 好。
- 找到 magic 只是**第一步**，要確認它真的是一個完整 DEX：讀 header 的 `file_size`（offset `0x20`）與 `checksum`（offset `0x8`）。`0x20` 這個 offset 不是猜的，是 DEX header 規格裡 `file_size` 欄位的固定位置（Ch 4 拆過 DEX header）。

> **未實測，理論預期行為**。驗證：`frida -U -f com.demo.app -l scan_dex.js`，操作 App 觸發 DEX 載入，看終端印出 `[dex?] found at 0x...`。加固 App 你會掃到不只一個——磁碟上的殼 DEX、解密後的真 DEX 可能都在。下一個工具教你把它 dump 出來。

## 工具三：從記憶體 dump — 脫殼的核心動作

找到 DEX 位址後，把那塊記憶體讀出來寫成檔案，就是**記憶體 dump**。這是 Part 5 脫殼的最小核心（Ch 29/36 會用主動調用等更強的方法，但 dump 是地基）。

### dump 一個 DEX

```javascript
// dump_dex.js —— 把指定位址的 DEX 存到裝置檔案，再 adb pull 出來
function dumpDex(address) {
    // DEX header: magic(8) checksum(4) sig(20) file_size@0x20(u32)
    var fileSize = address.add(0x20).readU32();
    if (fileSize < 0x70 || fileSize > 0x4000000) {   // 合理性檢查：header 至少 0x70，上限 64MB
        console.log("[!] implausible file_size " + fileSize + ", skip");
        return;
    }
    var bytes = Memory.readByteArray(address, fileSize);   // 讀出整個 DEX
    var path = "/data/local/tmp/dump_" + address + ".dex";
    var f = new File(path, "wb");
    f.write(bytes);
    f.close();
    console.log("[+] dumped " + fileSize + " bytes -> " + path);
}
```

配合工具二的 scan，找到就 dump：

```javascript
Java.perform(function () {
    Process.enumerateRanges('r--').forEach(function (range) {
        Memory.scan(range.base, range.size, "64 65 78 0a 33 ?? ?? 00", {
            onMatch: function (address) { dumpDex(address); },
            onError: function () {},
            onComplete: function () {}
        });
    });
});
```

- `Memory.readByteArray(addr, len)` 回傳一個 `ArrayBuffer`，`File.write` 直接吃它。這是 Frida 把記憶體搬到磁碟的標準組合。
- `file_size` 的**合理性檢查是必要的**：scan 可能誤命中一段剛好含 `dex\n` 位元組但不是真 DEX 的資料，讀出來的 `file_size` 會是個荒謬的值（幾 GB），照著讀就 OOM 或 crash。加上下界 `0x70`（DEX header 大小）與一個合理上界，過濾掉假陽性。
- dump 出來後 `adb pull /data/local/tmp/dump_0x....dex`，丟進 jadx/apktool。這時你手上是**執行期還原的真 DEX**，比磁碟上加密的殼有用得多。

> **未實測，理論預期行為**。實務上這種「無腦 scan+dump」對一代整包加密殼常有效，但二代（函式抽取）dump 出來的 DEX 方法體是空的（`nop` 填充）——那要 Part 5 的主動調用補回方法體。這裡你先掌握「找到 + 讀出 + 落地」這條 dump 主鏈。

### dump 一個 `.so`

native 加固（`.so` 加密、執行期解密）也用同樣思路，只是找的是 ELF magic `7f 45 4c 46`（`\x7fELF`）：

```javascript
// dump 已載入模組的 .so（模組已在記憶體，直接照 module 範圍讀）
var m = Process.getModuleByName("libnative.so");
console.log("base=" + m.base + " size=" + m.size);
var bytes = Memory.readByteArray(m.base, m.size);
var f = new File("/data/local/tmp/libnative_dumped.so", "wb");
f.write(bytes); f.close();
```

差別要講清楚：**dump 已載入模組的 `.so` 出來，它是「記憶體映像」不是「磁碟映像」**。ELF 載入時 section 會按對齊被展開、`.bss` 被清零、GOT 被 relocate 填好位址——所以 dump 出來的 `.so` 用 IDA 開，靜態位址對得上，但直接當檔案跑不起來（section header 可能要修）。這對「靜態逆演算法」夠用（Part 4 的目的），對「重新載入執行」就得修 ELF。這是 dump so 最容易誤會的點。

## 工具四：`Stalker` — 指令級追蹤與 coverage

前三個工具是「找靜態的東西」，`Stalker` 是「追動態的行為」：它能追蹤一個 thread **執行的每一條指令**，回報走了哪些 basic block、call 了誰。這是 Frida 最重的武器，用來：做 code coverage（配合 fuzzing 找沒覆蓋到的分支）、追一個混淆函式實際走的控制流、抓「輸入 X 時到底執行了哪段」。

### 底層機制：Stalker 靠動態重編譯

```
一般執行：       CPU 直接跑 target 的 .text 指令
                 └─ 你只能在函式邊界 hook，看不到中間

Stalker 追蹤：   Stalker 攔截即將執行的每個 basic block，
                 把它「複製一份到自己的緩衝區」並在 block 之間
                 插入你的回呼 (probe)，然後跑複製的那份
                 ┌────────────┐  transform   ┌────────────────────┐
                 │ 原始 block  │ ───────────▶ │ 複製 block + probe │──▶ 跑這份
                 └────────────┘              └────────────────────┘
                 └─ 因此能看到每條指令、每個 block、每個 call
```

代價：**慢**。動態重編譯每個 block、每個 block 之間插回呼，比原生執行慢一到兩個數量級。所以 Stalker 要**只追你關心的那段**（在進入目標函式時 `Stalker.follow`、離開時 `Stalker.unfollow`），不能全程開著。

### 範例：追一個函式走了哪些 basic block（coverage）

```javascript
// stalk_coverage.js —— 只在目標函式執行期間收集它命中的 block 位址
var targetName = "libnative.so";
var targetFn = Module.getExportByName(targetName, "Java_com_demo_Crypto_sign");
var mod = Process.getModuleByName(targetName);

Interceptor.attach(targetFn, {
    onEnter: function () {
        var seen = {};                    // 去重，同一 block 只記一次
        var tid = Process.getCurrentThreadId();
        Stalker.follow(tid, {
            events: { compile: true },    // 每編譯一個新 block 觸發一次（= 覆蓋到的 block）
            onReceive: function (events) {
                var parsed = Stalker.parse(events);
                parsed.forEach(function (ev) {
                    // ev = ['compile', startAddr, endAddr]
                    var start = ev[1];
                    // 只記落在目標模組內的 block，過濾 libc 等無關的
                    if (start.compare(mod.base) >= 0 &&
                        start.compare(mod.base.add(mod.size)) < 0) {
                        var off = start.sub(mod.base);        // 換算成模組內 offset
                        if (!seen[off]) {
                            seen[off] = true;
                            console.log("[block] +0x" + off.toString(16));
                        }
                    }
                });
            }
        });
        this.tid = tid;
    },
    onLeave: function () {
        Stalker.unfollow(this.tid);       // 一定要在離開時停，不然全程超慢
        Stalker.flush();                  // 把緩衝的 events 吐完
    }
});
```

逐點解釋：

- `events: { compile: true }`：Stalker 有多種事件，`compile` 是「每編譯（= 首次執行）一個新 basic block」觸發——正好對應「這次執行覆蓋到的 block」，是做 coverage 最省的選項。另有 `exec`（每條指令，最慢）、`call`/`ret`（函式呼叫）。
- **只在 `onEnter`→`onLeave` 之間 follow**：這是效能生死線。你只追 `sign` 這一個函式，離開立刻 `unfollow`，其他時間 App 全速跑。忘了 `unfollow`，App 會慢到像當機。
- **換算成模組內 offset**（`start.sub(mod.base)`）：ASLR 讓每次載入基址不同，記絕對位址沒意義；記「模組內偏移」才能跨執行比對、才能跟 IDA 裡的靜態位址對上。
- `mod.base` 到 `mod.base + mod.size` 的範圍過濾：不過濾的話，函式呼叫 `libc` 的 `memcpy`、ART 內部函式全被記進來，噪音淹沒你要的資訊。

> **未實測，理論預期行為**。驗證：跑起來後在 App 觸發簽名，終端會刷出 `[block] +0xNNN` 一串——那就是 `sign` 函式這次執行踩過的 basic block。把這些 offset 丟回 IDA，你能在控制流圖上把走過的路徑點亮（這正是 coverage guided fuzzing 的資訊來源）。想追「每一條指令」把 `compile` 換成 `exec`，但會慢非常多，只在極短片段用。

## 工具五：`frida-trace` — 零腳本快速掃過一堆函式

前面都是自己寫腳本。`frida-trace` 是 frida-tools 附的 CLI，**不寫腳本**就能同時 hook 一批函式、自動印進出。適合「我還不知道哪個函式重要，先廣撒網看誰被呼叫」的偵察階段。

```bash
# 追 libnative.so 裡所有名字含 crypt 的 export
frida-trace -U -f com.demo.app -I "libnative.so!*crypt*"

# 追所有 JNI 函式（Java_ 開頭是 JNI 命名慣例）
frida-trace -U -f com.demo.app -I "libnative.so!Java_*"

# 追 Java 方法（-j 是 Java 模式，16.x 支援）
frida-trace -U -f com.demo.app -j "com.demo.crypto.*!*"
```

它的機制：`-I`(include) 用 glob 比對符號，對每個命中的函式**自動生成一個 handler 檔**（`__handlers__/libnative.so/xxx.js`），預設印進出。你可以**編輯那些自動生成的 handler** 加自己的邏輯（印參數、dump 記憶體）——這是 frida-trace 最好用的地方：它幫你把樣板寫好，你只補關鍵那幾行。

- `Java_*` 這個 pattern 很值錢：JNI 函式的命名慣例是 `Java_<類全名底線化>_<方法名>`，所以 `Java_*` 一網打盡所有 native 方法的入口——想知道「App 呼叫了哪些 native 方法」，這一條指令就夠。
- 相對於自己寫 `Interceptor.attach`，frida-trace 的優勢是**批量 + 自動樣板**；劣勢是每個 handler 要細調時不如自己寫的乾淨。偵察用它、精修用手寫，是實務分工。

> **未實測，理論預期行為**。驗證：跑 `frida-trace -U -f com.demo.app -I "libnative.so!Java_*"`，操作 App，終端會即時刷出被呼叫的 JNI 函式名與縮排的進出。看到哪個函式在你點「登入」時被呼叫，那就是下一個要細 hook 的目標。

## 對比與取捨

| 工具 | 解決什麼 | 何時用 | 代價/陷阱 |
|---|---|---|---|
| `Java.enumerateLoadedClasses` | 找「有哪些 Java 類可 hook」 | 混淆/動態載入、名字對不上 | 只列**已載入**的，要先操作 App 觸發載入 |
| `Memory.scan` | 找「已知位元組在記憶體哪」 | 找 DEX/ELF magic、找金鑰特徵 | 只掃可讀段，要處理 `onError`，會有假陽性 |
| 記憶體 dump | 把找到的東西**落地成檔** | 脫殼、撈真 DEX/so | dump 的 so 是記憶體映像，不能直接跑；二代殼方法體是空的 |
| `Stalker` | 追「執行了哪些指令/block」 | coverage、追混淆控制流 | 慢一到兩個數量級，必須只 follow 目標片段 |
| `frida-trace` | 零腳本批量 hook 偵察 | 廣撒網找「誰被呼叫」 | 精修不如手寫；handler 多時輸出很吵 |

一句話定位：**enumerate/scan 是「找位址」，dump 是「取出來」，Stalker 是「追行為」，frida-trace 是「偷懶批量」**。它們常串用：frida-trace 廣撒網找到可疑函式 → enumerate 確認類 → scan 找相關資料 → Stalker 追它的分支 → dump 撈出真程式碼。

## 踩雷集錦

1. **Stalker 忘了 `unfollow`，App 慢到像當機**：Stalker 全程開著等於把整個進程放進動態重編譯器，慢一兩個數量級。鐵律：`onEnter` follow、`onLeave` 立刻 `unfollow`，只追你要的那一小段。
2. **`Memory.scan` 掃整個位址空間直接 crash**：不能從 0 掃到 128TB，中間全是未映射頁面。永遠先 `Process.enumerateRanges('r--')` 拿到有映射的可讀段，逐段掃，並實作 `onError` 吞掉掃描過程中權限變動的錯。
3. **`enumerateLoadedClasses` 掃不到目標類，以為 App 沒這個類**：它只列**已載入**的類。App 剛啟動時登入相關類還沒 lazy load。正解是**先在 App 裡走到那個功能**觸發載入，再枚舉。
4. **dump 出來的 DEX `file_size` 荒謬導致 OOM**：scan 會假陽性命中「剛好含 `dex\n` 但不是 DEX」的資料，其 `file_size` 欄位是垃圾值。dump 前務必做合理性檢查（`0x70 ≤ size ≤ 64MB`），過濾假陽性。
5. **以為 dump 的 `.so` 能直接當檔案跑/重載**：dump 的是**記憶體映像**（section 已展開、GOT 已 relocate、`.bss` 已清零），拿去 IDA 靜態分析位址對得上，但當檔案重新載入會因 section header/relocation 不一致而失敗。要重載得修 ELF。

## 進階：再往深一層

- **Stalker `transform` 改指令**：`Stalker.follow` 的 `transform` 回呼能在複製 block 時**改寫指令**——例如把某條 `b.eq` 條件跳轉的判斷改掉，等於在指令級做動態 patch，不用改磁碟上的 `.so`。這是繞 OLLVM 混淆控制流的高階招（Ch 27 會碰）。
- **coverage 餵給 fuzzer**：把 Stalker 收到的 block offset 集合序列化出來，就是一份 coverage map。配合改變輸入、比對 coverage 差異，你能做出「哪個輸入觸發了新分支」的判斷——這是 Frida-based fuzzing（如 Fuzzilli/frida-fuzz 思路）的資訊來源，Ch 40 自動化會再提。
- **scan 找的不只 magic**：金鑰、憑證 pin 的 SHA256、特定的 log 字串，全能 scan。找到位址後對它下 `Memory` 讀寫斷點（`MemoryAccessMonitor`），能抓「誰在讀這個金鑰」——把「找資料」升級成「找使用者」。
- **`gum` 與 QBDI 的關係**：Stalker 底層是 Frida 的 Gum 引擎做的動態二進位插樁（DBI）。同類技術還有 QBDI、DynamoRIO——原理都是動態重編譯 + 插 probe。懂了 Stalker 的機制，這些工具你一看就懂。

## 動手練習

1. 對一個你自己裝的、來源正當的 App 跑 `frida-trace -U -f <pkg> -I "*!Java_*"`，操作它，記下被呼叫的 JNI 函式清單——體驗「廣撒網偵察」。
2. 寫一支 `list_classes.js`，App 啟動後先枚舉一次類數量，操作幾個功能後再枚舉一次，比較數量差——親眼看到「類是 lazy load 的、操作觸發載入」。
3. 拿 Ch 0 撈出的任一 APK 對應的進程，跑本章的 `scan_dex.js`，看能掃到幾個 DEX magic、`file_size` 是否合理。把 dump 出來的 `.dex` 丟 jadx，跟磁碟上的 `classes.dex` 對照。
4. 選一個短的 native 函式，用 `stalk_coverage.js` 追它一次，數走過幾個 block；換個輸入再追一次，看 block 集合有沒有變——這就是 coverage 對輸入敏感的證明。

## 本章重點整理

- 執行期記憶體資訊最全；這章五個工具都圍繞「在未知記憶體裡找/追/撈」。
- `Java.enumerateLoadedClasses` 列**已載入**的類（要先觸發載入）；`Memory.scan` 按 pattern 找位址（只掃可讀段、處理 error、防假陽性）。
- 記憶體 **dump** 是脫殼地基：找到 DEX/ELF magic → 讀 `file_size` 驗證 → `readByteArray` 寫檔 → `adb pull`。dump 的 `.so` 是記憶體映像，能靜態分析、不能直接重跑。
- `Stalker` 靠動態重編譯做指令級追蹤，能做 coverage/追控制流，但慢，**必須只 follow 目標片段**。
- `frida-trace` 零腳本批量 hook，偵察神器；`Java_*` pattern 一網打盡 JNI 入口。

## 自我檢核

- [ ] 能解釋為什麼 `Memory.scan` 不能掃整個虛擬位址空間，正確做法是什麼
- [ ] 知道 `enumerateLoadedClasses` 掃不到某類的最常見原因，以及怎麼讓它出現
- [ ] 能寫出「掃 DEX magic → 驗證 file_size → dump 落地」的完整流程，並說明合理性檢查為什麼必要
- [ ] 能說出 Stalker 為什麼慢、為什麼一定要 `unfollow`，以及 coverage 為什麼記 offset 而非絕對位址
- [ ] 知道 dump 出來的 `.so` 為什麼不能直接當檔案重跑
- [ ] 能用一句 `frida-trace` 追出一個 App 所有被呼叫的 JNI 函式

## 延伸閱讀

- **[Frida — Stalker API](https://frida.re/docs/javascript-api/#stalker)** — Frida 官方文件
  - **讀哪裡**：`Stalker.follow`/`unfollow`、`events` 種類（compile/exec/call/ret）、`transform`
  - **和本章的關聯**：本章 coverage 範例的權威依據；想做 transform 改指令，這頁是起點
- **[Frida — Memory / MemoryAccessMonitor](https://frida.re/docs/javascript-api/#memory)** — Frida 官方文件
  - **讀哪裡**：`Memory.scan`/`scanSync`、`readByteArray`、`MemoryAccessMonitor`
  - **和本章的關聯**：scan 與 dump 全建在這；進階的「找誰在讀金鑰」用 MemoryAccessMonitor
- **[Frida CodeShare](https://codeshare.frida.re/)** — 社群腳本庫
  - **讀哪裡**：搜 "dump dex" / "dexdump" / "stalker coverage"，讀現成脫殼與 coverage 腳本
  - **為什麼值得讀**：本章的 scan+dump 是精簡版，社群腳本處理了更多邊界（多 DexFile、二代殼），讀原始碼比自己踩坑快
- **[frida-trace 手冊](https://frida.re/docs/frida-trace/)** — Frida 官方
  - **讀哪裡**：`-I`/`-X`(include/exclude)、`-j` Java 模式、handler 自動生成機制
  - **前提知識**：讀過本章 frida-trace 段，這頁補齊所有 glob 與模式選項
- **[OWASP MASTG — Dynamic Analysis (Android)](https://mas.owasp.org/MASTG/techniques/android/)**
  - **讀哪裡**：Dynamic Analysis 與 Memory dump 相關技術
  - **和本章的關聯**：把本章工具放進標準測試方法論的脈絡，理解它們在完整評估流程裡的位置

下一章我們換一種 hook 哲學：Frida 是「即時、每次都要重新 attach」，但如果你想要**每次 App 啟動就自動生效、開機也還在**的持久化 hook 呢？那是 Xposed / LSPosed 的地盤——它把 hook 直接種進 Zygote，讓每個新進程一出生就帶著你的修改。

→ [Ch 16 Xposed / LSPosed：持久化 hook](./16-xposed-lsposed.md)
