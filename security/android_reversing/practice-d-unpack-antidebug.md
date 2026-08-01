# 練習 D — 脫殼 + 繞反調試把 App 跑起來

> **目標**：把 Part 5（Ch 26–32）學的對抗技術**串成一條完整鏈**。你會拿到一個假想的加固 App，它同時裝了三道防線：**輕量殼**（真 DEX 加密藏起來、執行期才解到記憶體）、**ptrace 反調試**（一被 attach 就閃退）、**Frida 檢測**（掃 maps 找 frida 特徵）。任務：**繞反調試 → 繞 Frida 檢測 → 動態脫殼拿到真 DEX → 用 jadx 讀出真邏輯**。這是 Part 5 的期末驗收，考的是「防護疊在一起時，你能不能判斷該先拆哪條、一條條剝開」。

> **環境**：本練習以 **AVD（Android 13 / API 33，x86_64，Google APIs，已 root）+ Frida 16.x + frida-server（x86_64）+ jadx** 為準。**本 repo 建構沙箱是 Windows，沒有 Android/Frida/frida-server**——因此下面所有跟 App 互動的腳本與指令都標「**未實測，理論預期行為**」，並給你在自己 AVD 上的驗證方式。純算法邏輯（判斷脫出的 DEX 是否合法）用 Python 3 實跑，標「**實際輸出**」。

## 情境設定（假想目標 App）

你拿到一個 `target-hardened.apk`，偵察（Ch 1 SOP）發現：

```
target-hardened.apk
├── AndroidManifest.xml
│     application android:name="com.shell.StubApplication"   ← 殼的載入器（強加固訊號）
├── classes.dex          ← 只有殼的 stub 邏輯，真 DEX 不在這（大小異常小）
├── assets/
│     └── encrypted.dat   ← 疑似加密的真 DEX（Ch 2 說 assets 是金礦）
└── lib/x86_64/
      └── libshell.so     ← 殼的 native 部分：解密 DEX + ptrace 反調試 + 掃 maps 反 Frida
```

三道防線的行為（假想，但貼近真實輕量殼）：

- **殼**：`StubApplication.attachBaseContext` 觸發 `libshell.so` 把 `assets/encrypted.dat` 解密成真 DEX，用 `InMemoryDexClassLoader`（或 `DexClassLoader` 寫到私有目錄）載入。真 DEX **只在記憶體/執行期存在**。
- **ptrace 反調試**：`libshell.so` 的 constructor 呼叫 `ptrace(PTRACE_TRACEME)` 自附加；若失敗（表示已被 trace）就 `exit()`。
- **Frida 檢測**：`libshell.so` 起一條執行緒週期性讀 `/proc/self/maps`，掃到 `frida`/`gum-js-loop` 就 `abort()`。

你的最終目標：**拿到解密後的真 DEX，jadx 打開能看到 App 的真實邏輯**（假設真邏輯是一個藏在真 DEX 裡的 `com.app.secret.FlagChecker` 類）。

## 期望輸出

完成後你應該交出：

1. 一支能穩定讓 App **不閃退地跑起來**的 Frida 腳本（同時繞過 ptrace 反調試 + Frida 檢測）。
2. 一份 **dump 出來的真 DEX 檔**（`dumped.dex`），且通過合法性檢查（magic = `dex\n035`、header 完整）。
3. jadx 打開 `dumped.dex`，**能看到 `com.app.secret.FlagChecker`** 這個原本被殼藏起來的類。
4. 一段文字說明：**你判斷「先拆哪條防線」的順序與理由**（這是本練習真正考的東西）。

## 卡點預告（先看，少走彎路）

- **卡點 1：一 attach 就閃退，腳本根本沒機會跑。** 因為 ptrace 反調試/Frida 檢測在 `libshell.so` 的 constructor 跑得**比你 hook 裝好還早**。解法是**早期注入**——spawn 模式 + hook `android_dlopen_ext`，等 `libshell.so` 一載入立刻攔，趕在它的 constructor 前佈置好。
- **卡點 2：繞過了反調試，還是被 Frida 檢測殺。** 兩道防線是獨立的，繞一條不代表另一條放行。要**兩條一起繞**。
- **卡點 3：脫殼脫到的是殼的 stub DEX，不是真 DEX。** 因為你 dump 的時機不對（真 DEX 還沒解密到記憶體）。要 hook 在**殼呼叫 ClassLoader 載入真 DEX 的那一刻**，那時真 DEX 已在記憶體、位址參數就在手上。
- **卡點 4：dump 出來的 DEX 打不開/header 壞。** dump 的起始位址或長度抓錯，多抓/少抓了 byte。用 DEX header 的 `file_size` 欄位確認長度，用 magic 確認起點。

## 分步引導（≥5 步）

按這個順序做，別跳。**順序本身就是本練習的核心考點。**

### Step 1：偵察，確認三道防線各在哪

先別動手繞。用 Ch 1 SOP + Ch 28/29 的殼識別，把三道防線定位清楚：

- 看 Manifest 的 `android:name`（`StubApplication` = 殼載入器入口）。
- `unzip -l` 看 `classes.dex` 大小（異常小 = 真 DEX 被抽走）、`assets/` 有沒有可疑 blob。
- jadx 打開看 `StubApplication`：它在 `attachBaseContext`/`onCreate` 做什麼、`System.loadLibrary` 載入哪個 `.so`。
- 對 `libshell.so`（Ch 22 IDA/Ghidra）掃字串：找 `ptrace`、`/proc/self/maps`、`frida`、`TRACEME`、DEX 相關字串，確認反調試/反 Frida/解密邏輯的存在。

**產出**：一張「三道防線各在 `libshell.so` 哪個函式/時機」的小地圖。

### Step 2：解決注入時機——早期 hook `libshell.so` 的載入

反調試與 Frida 檢測在 `.so` constructor 跑，你必須趕在它前面。用 Frida spawn 模式，hook `android_dlopen_ext`，偵測 `libshell.so` 被載入的那一刻：

- `frida -U -f com.target -l bypass.js`（spawn，App 還沒 resume）。
- 在 `bypass.js` 裡 hook `android_dlopen_ext`，`onLeave` 判斷載入的是不是 `libshell.so`，是的話**立刻**裝上後續的反調試/反 Frida 繞過。

**目的**：把你的繞過佈置在殼的防護程式碼執行**之前**。

### Step 3：繞 ptrace 反調試

在 `libshell.so` 載入後、constructor 前，hook `ptrace`，當 request 是 `PTRACE_TRACEME`(0) 時回 0（假裝成功、不真占位或不真觸發失敗路徑）。若殼是 fork child 來 trace，還要處理 `fork`/`clone`（本練習假設是 TRACEME 版，單 hook `ptrace` 即可）。

### Step 4：繞 Frida 檢測

殼週期性讀 `/proc/self/maps` 掃 frida 特徵。hook 讀 maps 的路徑：攔 `fgets`（或 `read`），把含 `frida`/`gum-js-loop`/`gadget`/`pool-frida` 的行清空，讓殼掃不到。**Step 3 + Step 4 合在同一支腳本**，一起在 Step 2 的早期時機裝上。

此時 App 應該能**穩定跑起來不閃退**——期望輸出 (1) 達成。

### Step 5：動態脫殼——攔真 DEX 載入的那一刻

App 活著了，但你要的是真 DEX。殼會用 `InMemoryDexClassLoader`（或 `DexClassLoader`）載入解密後的真 DEX。hook 這個 ClassLoader 的建構子（或底層 `DexFile.openDexFileNative`/`OpenMemory`），那一刻真 DEX 的記憶體位址 + 長度就在參數裡：

- 拿到 DEX 在記憶體的起始位址與大小（大小可從 DEX header 的 `file_size` 欄位讀，或從 ClassLoader 傳入的 buffer 長度）。
- `Memory.readByteArray(addr, size)` 把整段 dump 出來，寫成 `dumped.dex`。

### Step 6：驗證與分析

- 檢查 `dumped.dex` 的 magic（前 8 byte 是不是 `dex\n035\0`）與 header 完整性。
- jadx 打開 `dumped.dex`，找 `com.app.secret.FlagChecker`——找到就代表脫殼成功。

## 完整參考解答

先自己做。真的卡住再看。

<details>
<summary>參考解答：繞反調試 + 繞 Frida 檢測 + 脫殼腳本（點開）</summary>

### 繞過腳本（Step 2–4 合一）：`bypass.js`

> **未實測，理論預期行為**（本 repo 沙箱無 Android/Frida）。在你的 AVD 上用 `frida -U -f com.target -l bypass.js` 跑。

```javascript
// bypass.js —— 早期注入：libshell.so 一載入就繞 ptrace + Frida 檢測
"use strict";

function installNativeBypass() {
    // ---- (A) 繞 ptrace 反調試：PTRACE_TRACEME 回 0 ----
    const ptracePtr = Module.getExportByName(null, "ptrace");
    if (ptracePtr) {
        Interceptor.attach(ptracePtr, {
            onEnter(args) { this.isTraceme = args[0].toInt32() === 0; }, // PTRACE_TRACEME==0
            onLeave(retval) {
                if (this.isTraceme) {
                    retval.replace(0);
                    console.log("[bypass] ptrace(TRACEME) -> 偽造 0");
                }
            }
        });
    }

    // ---- (B) 繞 Frida 檢測：攔 fgets，清掉 maps 裡含 frida 特徵的行 ----
    const fgetsPtr = Module.getExportByName(null, "fgets");
    if (fgetsPtr) {
        Interceptor.attach(fgetsPtr, {
            onEnter(args) { this.buf = args[0]; },
            onLeave(retval) {
                if (retval.isNull()) return;
                const line = this.buf.readCString() || "";
                if (/frida|gum-js-loop|gadget|pool-frida/i.test(line)) {
                    this.buf.writeUtf8String("\n");   // 清空這行，殼掃不到特徵
                    console.log("[bypass] 濾掉 maps 特徵行");
                }
            }
        });
    }
    console.log("[bypass] native 繞過已安裝");
}

// ---- 早期時機：hook android_dlopen_ext，等 libshell.so 一載入立刻裝繞過 ----
const dlopenExt = Module.getExportByName(null, "android_dlopen_ext");
Interceptor.attach(dlopenExt, {
    onEnter(args) { this.path = args[0].isNull() ? "" : args[0].readCString(); },
    onLeave(retval) {
        if (this.path && this.path.indexOf("libshell.so") !== -1) {
            console.log("[bypass] libshell.so 已載入，安裝繞過（趕在 constructor 前）");
            installNativeBypass();
        }
    }
});
```

> **注意**：`onLeave` 裝繞過其實已經在 `.so` 對映完成之後、但 constructor 通常在 `dlopen` 返回前的更早階段就可能跑。更保險的做法是 hook `call_constructors` 之前的階段，或直接在腳本開頭（spawn 尚未 resume 時）就對整個進程裝上 `ptrace`/`fgets` hook（因為此時任何 `.so` 都還沒跑）。實務上「腳本一開頭就無條件裝 native hook」往往比等 `dlopen` 更穩——因為 spawn 模式下 App 主邏輯尚未 resume。取捨看殼的具體時機。

### 脫殼腳本（Step 5）：附加到 `bypass.js` 尾端

```javascript
// ---- 脫殼：攔 InMemoryDexClassLoader 載入真 DEX 的那一刻 ----
Java.perform(function () {
    // 途徑一：hook InMemoryDexClassLoader 建構子（Android 8+ 記憶體載入）
    try {
        const IMDCL = Java.use("dalvik.system.InMemoryDexClassLoader");
        // 建構子簽名：(ByteBuffer, ClassLoader) 或 (ByteBuffer[], ClassLoader)
        IMDCL.$init.overload("java.nio.ByteBuffer", "java.lang.ClassLoader")
            .implementation = function (bb, parent) {
                dumpByteBuffer(bb, "/data/local/tmp/dumped.dex");
                return this.$init(bb, parent);
            };
    } catch (e) { console.log("[dump] InMemoryDexClassLoader 不可用: " + e); }

    // 途徑二（保底）：hook 底層 DexFile.openInMemoryDexFilesNative / openDexFileNative
    // 不同 Android 版本 API 名不同，實際以你 AVD 的 Android 13 為準去 hook。
});

function dumpByteBuffer(bb, outPath) {
    // 從 DirectByteBuffer 取得底層位址與長度
    const remaining = bb.remaining();
    const bytes = Java.array('byte', []);   // 佔位；實際用下面的讀法
    // 簡潔可靠做法：把 ByteBuffer 內容複製出來
    const arr = [];
    const dup = bb.duplicate();
    while (dup.hasRemaining()) { arr.push(dup.get()); }
    const buf = Memory.alloc(arr.length);
    for (let i = 0; i < arr.length; i++) { buf.add(i).writeS8(arr[i]); }
    const data = Memory.readByteArray(buf, arr.length);
    const f = new File(outPath, "wb");
    f.write(data);
    f.flush(); f.close();
    console.log("[dump] 已寫出 " + arr.length + " bytes -> " + outPath);
    // 驗 magic
    if (arr.length >= 4 &&
        (arr[0]&0xff)===0x64 && (arr[1]&0xff)===0x65 && (arr[2]&0xff)===0x78) {
        console.log("[dump] magic OK: dex...");
    } else {
        console.log("[dump] 警告：magic 不是 dex，dump 時機可能不對");
    }
}
```

### 拉出檔案並分析（host 端）

```bash
adb pull /data/local/tmp/dumped.dex ./dumped.dex
# 驗 magic
xxd dumped.dex | head -1        # 應看到 6465 780a 3033 3500  = "dex\n035\0"
# jadx 打開找真邏輯
jadx dumped.dex -d dumped_out
grep -r "FlagChecker" dumped_out/sources/    # 找到 = 脫殼成功
```

### 「先拆哪條」的判斷與理由（期望輸出 4）

> 順序：**先解決注入時機（Step 2）→ 同時繞 ptrace + Frida 檢測（Step 3/4）→ 才脫殼（Step 5）**。
>
> 理由：反調試/Frida 檢測跑在殼 constructor，比任何 Java hook 都早，**不先解決注入時機，你的脫殼腳本根本沒機會裝上就被殺**。而脫殼（攔 ClassLoader）必須等殼**已經解密真 DEX 並準備載入**——那是 App 存活並跑到一定進度後的事。所以「讓 App 活著」是「脫殼」的前提，順序不能反。這正是本練習的核心：**防線有依賴關係，得先拆擋住你工具的那條。**

</details>

## 驗證脫出的 DEX 是否合法（本 repo 沙箱實跑）

不論你在哪台 AVD 脫殼，dump 出來的 DEX 一定要驗。這段判斷邏輯**在本 repo 沙箱用 Python 3 實跑**（不依賴 Android，只是解析 bytes）：

```python
# verify_dex.py —— 檢查 dump 出來的 DEX 是否合法
import struct, sys

def verify(data: bytes):
    if len(data) < 0x70:
        return "太短，不足一個 DEX header (需 >= 112 bytes)"
    magic = data[0:8]
    if magic[:4] != b"dex\n":
        return f"magic 錯誤: {magic!r}（不是 DEX，dump 時機/位址可能不對）"
    version = magic[4:7].decode(errors="replace")
    file_size = struct.unpack_from("<I", data, 32)[0]   # header offset 32 = file_size
    header_size = struct.unpack_from("<I", data, 36)[0]  # 應為 0x70
    endian = struct.unpack_from("<I", data, 40)[0]       # 應為 0x12345678
    ok = (header_size == 0x70 and endian == 0x12345678 and file_size == len(data))
    return (f"magic=dex ver={version} file_size={file_size} "
            f"實際長度={len(data)} header_size=0x{header_size:x} "
            f"endian=0x{endian:x} -> {'合法' if ok else '可疑(長度/欄位不符)'}")

# 造一個結構正確的最小 DEX header 來示範判斷
demo = bytearray(b"dex\n035\x00" + b"\x00"*24)          # magic + checksum + sig
demo += struct.pack("<I", 0x70)                          # file_size (先佔位，稍後修正)
demo += struct.pack("<I", 0x70)                          # header_size = 0x70
demo += struct.pack("<I", 0x12345678)                    # endian_tag
demo += b"\x00" * (0x70 - len(demo))                     # 補到 header 尾
struct.pack_into("<I", demo, 32, len(demo))              # file_size = 實際長度
print("合法 DEX ->", verify(bytes(demo)))

# 對照：dump 到殼的 stub / 位址抓錯（拿到一段夠長但開頭不是 DEX 的記憶體）
print("錯誤 dump ->", verify(b"ELF-or-garbage-" * 10))   # 150 bytes, magic 不是 dex
```

**實際輸出**（本 repo 沙箱 Python 3 實跑）：

```
合法 DEX -> magic=dex ver=035 file_size=112 實際長度=112 header_size=0x70 endian=0x12345678 -> 合法
錯誤 dump -> magic 錯誤: b'ELF-or-g'（不是 DEX，dump 時機/位址可能不對）
```

把這支當你的「脫殼成敗自動判斷器」：pull 回 `dumped.dex` 後 `python3 verify_dex.py < dumped.dex`（自己改成讀檔），magic/欄位對了才進 jadx。**`file_size` 跟實際長度不符** = 你多抓或少抓了 byte（卡點 4），回去修 dump 的長度。

## 測試表

在你的 AVD 上逐項打勾（本 repo 沙箱只能驗最後兩列的純算法部分）：

| # | 測試項 | 期望結果 | 沙箱可驗？ |
|---|---|---|---|
| 1 | 不掛腳本，`frida -U -f com.target` | App 立刻閃退（三道防線生效） | 否（需 AVD） |
| 2 | 掛 `bypass.js`（僅 ptrace 繞過） | 仍被 Frida 檢測殺（證明兩條獨立） | 否（需 AVD） |
| 3 | 掛完整 `bypass.js`（ptrace + Frida 都繞） | App 穩定停在畫面、不閃退 | 否（需 AVD） |
| 4 | 加脫殼 hook，觸發真 DEX 載入 | log 印出 dump bytes 數 + magic OK | 否（需 AVD） |
| 5 | `adb pull dumped.dex` + `xxd` 看前 8 byte | `6465 780a 3033 3500`（dex\n035） | 否（需真檔） |
| 6 | `verify_dex.py` 檢查合法 DEX | 印「合法」、`file_size == 實際長度` | **是**（實跑） |
| 7 | `verify_dex.py` 檢查錯誤 dump | 印「magic 錯誤」 | **是**（實跑） |
| 8 | jadx 打開 `dumped.dex` | 看得到 `com.app.secret.FlagChecker` | 否（需真檔） |

## 延伸挑戰

1. **殼改成 fork child 反調試**：假設殼不是 `PTRACE_TRACEME`，而是 fork 一個 child 對 parent `PTRACE_ATTACH`。你的單 hook `ptrace` 會失效——想想要多 hook 什麼（`fork`/`clone`？在 child 裡也攔？）才能繞。
2. **殼直呼 syscall 繞 libc**：假設殼用 `syscall(SYS_ptrace, ...)` 和 `syscall(SYS_openat, ...)` 而非 libc wrapper。你掛在 `ptrace`/`fgets` export 上的 hook 全失效——改成 hook `syscall` 或 SVC 攔截，重寫繞過。
3. **殼加完整性校驗（接 Ch 32）**：假設脫殼後殼還會校驗自己的 `.so` 記憶體 `.text`，你的 inline hook 改了記憶體正好被抓。想想怎麼把 Step 3/4 的繞過改成**不改記憶體的手法**（硬體斷點/Stalker），或 hook 掉校驗函式本身。
4. **真 DEX 分多次解密（函式級抽取，二代殼）**：假設不是一次解出整包 DEX，而是每個方法被呼叫時才解密該方法的 bytecode。單點 dump 抓不到全部——你需要 Part 6 的 ArtMethod-level 主動調用脫殼（練習 E 的 mini-FART 就是幹這個）。先想想思路。

## 自我檢核

- [ ] 不看解答，能說出**為什麼要先解決注入時機、再脫殼**（順序的依賴關係）
- [ ] 能解釋反調試與 Frida 檢測「繞一條不代表另一條放行」，兩者獨立
- [ ] 知道脫殼要 hook 在「殼呼叫 ClassLoader 載入真 DEX 的那一刻」，而非隨便一個時機
- [ ] 能用 DEX magic + `file_size` 欄位判斷 dump 出來的 DEX 是否完整（卡點 4）
- [ ] 跑過 `verify_dex.py`，親眼看到合法 DEX 與錯誤 dump 的判斷差異
- [ ] 想過延伸挑戰 1/2 至少一個：fork 反調試或 syscall 直呼會怎麼讓你的繞過失效

這題把 Part 5 的對抗技術串成一條真實的攻擊鏈——**判斷防線依賴、先拆擋路的、再取你要的**。Part 6 我們潛入更底層：ART runtime 內部。搞懂 Dalvik 到 ART 的演進、ArtMethod 的結構，你才能做延伸挑戰 4 那種「函式級主動調用脫殼」，那是脫殼技術的天花板。

→ [Ch 33 Dalvik 到 ART 的演進](./33-dalvik-to-art.md)
