# 練習 E — 寫一個 ArtMethod-level 主動調用脫殼器（mini FART）

> **目標**：把 Ch 34（`ArtMethod`）、Ch 35（枚舉 ClassLoader 找目標 DEX）、Ch 36（主動調用 / 攔 interpreter bridge dump code_item）串成一個能跑的東西——用 **Frida** 寫一個**簡化版主動調用脫殼器（mini FART）**。你要做到：枚舉目標類的方法 → 主動觸發讓（抽取型殼）把方法體填回 `ArtMethod` → 從 `ArtMethod` 定位並 dump 出 DEX/code_item → 拼回一份能被 baksmali 打開的 DEX。這不是要你造出生產級 FART，是要你**親手走一遍「操作 `ArtMethod` 脫殼」的完整鏈**，把前四章的結構知識變成肌肉記憶。

> **環境**：**Android 13 / API 33 AVD（x86_64，可 root，google_apis）+ Frida 16.x + frida-server（同版本、對應架構）**。目標樣本建議用**自己寫的 mini「抽取型殼」demo** 或開源加固測試 App（合法、可控）。**本練習所有 Frida 腳本手寫、Frida 16.x 語法、未在本 repo 沙箱實測（沙箱無 Android/ART/Frida）——每步都附「在 AVD 上怎麼驗證」**。`ArtMethod` offset / interpreter bridge 符號名**逐版本變**，腳本一律用「動態找 symbol / 查對應版本原始碼」而非硬編，你要在你的 AVD 上實測校準。

> **合法邊界**：只脫你有權分析的目標（自己寫的 demo、開源加固測試 App、CTF、明確授權的評估對象）。這練習的目的是理解脫殼原理與防禦，不是破解他人付費內容。

---

## 任務規格（先讀清楚要做什麼）

你要交付一支 Frida 腳本 `mini_fart.js`（可拆多檔），對一個目標 package 完成：

| 編號 | 需求 | 驗收 |
|---|---|---|
| R1 | **枚舉 ClassLoader**，找出目標 App 實際載入的 DEX（含動態載入的），列出來源 | 印出所有 ClassLoader 與其 `dexElements` 的 DEX 路徑/記憶體來源 |
| R2 | **枚舉目標類的方法**（給定類名清單或整個 loader 的類），拿到每個方法對應的底層 `ArtMethod`/`Method` | 印出每個類的方法數與方法名 |
| R3 | **主動觸發方法**（走「攔 interpreter bridge」或受控 invoke），讓抽取型殼把 code_item 填回 | log 顯示方法被經過/觸發、殼填回 |
| R4 | **dump DEX**：從 `ArtMethod`/`DexFile` 定位 DEX 在記憶體的 base+size，把整塊 DEX bytes 落檔 | 產出 `dump_*.dex` 檔案 |
| R5 | **驗證產物**：dump 出的 DEX 能被 `baksmali`/`jadx` 打開，且**目標方法的方法體不再是空的** | baksmali 反出的 `.method` 有指令、不是空殼 |

**簡化允許**：你不需要處理 VMP/dex2c（超綱）、不需要處理「執行後立刻抹掉」的極端殼（能脫「填回後保留一陣子」的抽取殼即可）、不需要完美拼裝（能 dump 出可打開的 DEX 即算過，header 修復用現成工具）。

---

## 期望輸出（跑起來大概長這樣）

**代表性輸出（未實測，理論預期；你的路徑/數字會不同）**：

```
[*] mini-FART 啟動, target = com.example.packed
[R1] 枚舉 ClassLoader:
     - PathClassLoader  -> /data/app/.../base.apk
     - DexClassLoader   -> /data/data/com.example.packed/files/real.dex   ★動態載入
[R2] 枚舉類 com.example.packed.CoreLogic: 12 個方法
     encrypt, decrypt, sign, checkLicense, ...
[R3] 攔截點 = ExecuteSwitchImpl @ 0x7xxxxx (libart.so)
     觸發 App 功能中... 方法經過 bridge:
       [pass] CoreLogic.encrypt   (code_item 已填回, off=0x3f10)
       [pass] CoreLogic.sign      (code_item 已填回, off=0x41a0)
[R4] dump DEX: base=0x7bxxxx size=0x2e000 -> ./dump_real.dex
[R5] 驗證: baksmali d dump_real.dex -> CoreLogic.encrypt 有 47 條指令 (非空) ✓
[*] 完成
```

跑不出這個沒關係——**下面的分步會帶你一步步逼近**，每步都能單獨驗證。

---

## 卡點預告（你大概會卡在這幾關）

先講在前面，卡住是正常的，對照這裡：

1. **找不到目標類**（`Java.use` 報 `ClassNotFoundException`）→ 你站錯 ClassLoader，回 Ch 35 用 `Java.classFactory.loader` 切到動態 loader。
2. **interpreter bridge symbol 找不到**→ 你這版 ART 的攔截函式名不是你抄的那個。`enumerateSymbols("libart.so")` 掃出候選（`interpreter_bridge`/`ExecuteSwitchImpl`/`Interpret`/`artQuickToInterpreterBridge`），逐個試。
3. **`args[0]` 不是 `ArtMethod*`**→ 攔截函式的參數順序版本相關。用一個已知方法反推哪個 arg 是 `ArtMethod*`。
4. **dump 出來的 DEX 打不開**→ header 的 magic/checksum/map_list 不對（Ch 4）。先確認 dump 的 base 對準 `dex\n035` magic，再用工具修 header。
5. **方法體還是空的**→ 你 dump 得太早（殼還沒填回），或這方法根本沒被觸發到。確認你有讓那個方法「經過 bridge」（觸發對應 UI/流程）。
6. **主動調用把 App 弄崩**→ 別暴力 invoke 全部方法（Ch 36 範例三）。優先「攔 bridge + 手動觸發功能」而非亂 invoke。

---

## 分步實作（≥5 步，每步可獨立驗證）

### Step 1：先跑通「attach + 枚舉 ClassLoader」（對應 R1）

先確認工作台活著、拿到目標的 DEX 全貌。這步只做 Ch 35 範例一的事。

```javascript
// step1_loaders.js
Java.perform(function () {
    console.log("[*] mini-FART Step1: 枚舉 ClassLoader");
    Java.enumerateClassLoaders({
        onMatch: function (loader) {
            console.log("[loader] " + loader);
        },
        onComplete: function () { console.log("[*] done"); }
    });
});
```

跑：`frida -U -f com.example.packed -l step1_loaders.js`（用 `-f` spawn，搶早窗口，Ch 37）。

**怎麼驗證**：輸出裡除了 `PathClassLoader`（載 `base.apk`），若目標是抽取型殼，你多半會看到一個額外的 `DexClassLoader`/`InMemoryDexClassLoader`——那就是真 DEX 的載體。看到它就代表 R1 的線索到手。

---

### Step 2：切到正確 loader，枚舉目標類的方法（對應 R2）

拿到動態 loader 後切過去，枚舉目標類的方法（Ch 35 範例二 + Ch 34 枚舉思路）。

```javascript
// step2_methods.js
Java.perform(function () {
    var TARGET_CLASS = "com.example.packed.CoreLogic";
    var targetLoader = null;

    Java.enumerateClassLoaders({
        onMatch: function (loader) {
            // 粗篩: 找載了真 DEX 的那個 (依你 Step1 看到的特徵改)
            if (loader.toString().indexOf("real.dex") !== -1 ||
                loader.toString().indexOf("InMemoryDex") !== -1) {
                targetLoader = loader;
            }
        },
        onComplete: function () {}
    });

    if (targetLoader) Java.classFactory.loader = targetLoader;  // 切 loader (Ch35)

    var clazz = Java.use(TARGET_CLASS);
    var methods = clazz.class.getDeclaredMethods();
    console.log("[R2] " + TARGET_CLASS + " 有 " + methods.length + " 個方法:");
    methods.forEach(function (m) { console.log("     " + m.getName()); });
});
```

**怎麼驗證**：印出方法清單且沒 `ClassNotFoundException`，代表你站對 loader、拿到方法了。若還是找不到類——回 Step 1 確認你篩 loader 的字串對不對（改成你實際看到的特徵）。

---

### Step 3：找對 interpreter bridge 攔截點（對應 R3 前置）

這步是全練習最版本敏感的一關——找到你這台 AVD 的 ART 執行未編譯方法的必經函式（Ch 36）。

```javascript
// step3_findbridge.js
Java.perform(function () {
    var libart = Process.getModuleByName("libart.so");
    console.log("[*] 掃 libart 找攔截點候選:");
    libart.enumerateSymbols().forEach(function (sym) {
        var n = sym.name;
        if (n.indexOf("interpreter_bridge") !== -1 ||
            n.indexOf("ExecuteSwitchImpl") !== -1 ||
            n.indexOf("ExecuteNterp") !== -1 ||
            (n.indexOf("Interpret") !== -1 && n.indexOf("art") !== -1)) {
            console.log("     候選: " + n + " @ " + sym.address);
        }
    });
});
```

**怎麼驗證**：你會看到一串候選 symbol。Android 13 常見的是 `ExecuteSwitchImpl` / `artQuickToInterpreterBridge` / nterp 相關（**你的版本以實際掃出的為準**）。記下候選，Step 4 逐個試哪個 `onEnter` 的參數含 `ArtMethod*`。

> **誠實提醒**：不同 Android 版本、有沒有啟用 nterp（新直譯器），該 hook 的點不同。**沒有一個「正確答案」能抄**——這步就是要你在自己裝置上實測校準。這正是脫殼工具「版本適配」那部分在做的事。

---

### Step 4：攔 bridge，確認方法經過 + code_item 已填回（對應 R3）

掛上攔截點，觸發 App 功能，看方法一個個經過。

```javascript
// step4_hookbridge.js
Java.perform(function () {
    var libart = Process.getModuleByName("libart.so");
    // 換成你 Step3 挑定的那個 symbol 名
    var BRIDGE_SYM = "_ZN3art11interpreter..."; // ← 填你實測的
    var addr = libart.findExportByName ? libart.findExportByName(BRIDGE_SYM) : null;
    if (!addr) {
        // 用掃描找
        libart.enumerateSymbols().forEach(function (s) {
            if (s.name.indexOf("ExecuteSwitchImpl") !== -1) addr = s.address;
        });
    }
    if (!addr) { console.log("[!] 攔截點沒找到"); return; }

    Interceptor.attach(addr, {
        onEnter: function (args) {
            // ★ 版本相關: 確認哪個 arg 是 ArtMethod* (Step4a 反推)
            var artMethod = args[0];
            // 讀 ArtMethod 的 dex_code_item_offset_ (offset 需實測, 見下)
            // 這裡先只印「有方法經過」, 確認攔截點有效
            console.log("[pass] ArtMethod=" + artMethod);
        }
    });
    console.log("[*] 攔截點已掛, 去 App 點功能觸發方法");
});
```

**怎麼驗證**：掛上後去 App 操作（點按鈕、跑流程），console 應該刷出 `[pass] ArtMethod=0x...`。刷得出來代表**攔截點有效、方法確實經過這裡**。刷不出來——換 Step 3 的其他候選 symbol，或確認你操作的功能真的會呼叫未編譯方法。

**Step 4a（反推哪個 arg 是 ArtMethod*）**：對一個已知方法（如 `CoreLogic.sign`），先用 Frida `Java.use` 拿到它、記下它的 `ArtMethod` 指標（透過已知手段），再比對 `onEnter` 時哪個 `args[i]` 等於它——那個就是 `ArtMethod*` 的位置。

---

### Step 5：從 ArtMethod / DexFile 定位並 dump DEX（對應 R4）

拿到 `ArtMethod`，順「`ArtMethod` → 所屬 DexFile → DEX 記憶體 base+size」把整塊 DEX dump 出來（Ch 36 進階的定位鏈）。**offset 一律實測，不硬編**。

```javascript
// step5_dump.js (核心 dump 邏輯, 思路為主)
function dumpDexFromMemory(dexBase, dexSize, outPath) {
    // 確認 magic 是 "dex\n0" (Ch4)
    var magic = dexBase.readCString(4);
    console.log("[R4] dex magic @ base = " + JSON.stringify(magic));
    var bytes = dexBase.readByteArray(dexSize);
    var f = new File(outPath, "wb");
    f.write(bytes);
    f.close();
    console.log("[R4] dumped " + dexSize + " bytes -> " + outPath);
}

// 從 ArtMethod 拿到 DexFile 的 begin/size 有兩條路:
//  (a) ArtMethod -> declaring_class_ -> dex_cache_ -> dex_file (native)
//  (b) 從 R1 的 DexFile java 物件的 mCookie 反查 native DexFile (Ch35 進階)
// 兩條都要實測 offset。這裡示範拿到 base+size 後的 dump 動作。
```

**怎麼驗證**：產出 `dump_real.dex`，`adb pull` 出來，`xxd dump_real.dex | head` 看開頭是不是 `64 65 78 0a 30 33 35`（`dex\n035`）。是就代表你 dump 到了一份合法 DEX 起點。

---

### Step 6：驗證方法體非空 + 修 header（對應 R5）

dump 出的 DEX 可能 header 校驗欄位不對（Ch 4），先修再驗。

```bash
# 修 header (checksum/signature) — 用現成工具或小腳本重算
python3 fix_dex_header.py dump_real.dex        # 重算 adler32 + SHA-1 (Ch4 邏輯)

# 反出來看目標方法有沒有指令 (非空殼)
baksmali d dump_real.dex -o out_smali
grep -A20 "\.method.*encrypt" out_smali/com/example/packed/CoreLogic.smali
```

**怎麼驗證（R5 驗收）**：`CoreLogic.encrypt` 的 `.method` 區塊裡有實際指令（`const-string`、`invoke-*` 等），**不是空的 `.method ... .end method`**。有指令就代表你**成功把抽取殼填回的方法體 dump 出來了**——脫殼成功。若還是空殼，回 Step 4 確認那個方法真的被觸發過（經過 bridge）。

---

## 完整參考解答

**下面是把上面各步整合的參考骨架。務必理解「它為什麼這樣做」而非照抄——offset/symbol 你必須在自己 AVD 校準。**

<details>
<summary>點開看 mini_fart.js 參考骨架（Frida 16.x，未實測，理論預期行為）</summary>

```javascript
// mini_fart.js — 簡化版主動調用脫殼器 (教學骨架)
// 前提: frida -U -f <pkg> -l mini_fart.js  (spawn 搶早窗口)
// 注意: ARTMETHOD_CODE_ITEM_OFF、BRIDGE 符號名都是「要在你的裝置實測」的佔位

'use strict';

var CONFIG = {
    targetClasses: ["com.example.packed.CoreLogic"], // 要脫的類
    dexOutDir: "/data/data/com.example.packed/files/dump/",
    // ↓↓↓ 全部要在你的 Android 版本實測校準 ↓↓↓
    bridgeSymbolHints: ["ExecuteSwitchImpl", "artQuickToInterpreterBridge",
                        "ExecuteNterpImpl", "interpreter_bridge"],
    // ArtMethod 內 dex_code_item_offset_ 的偏移: 查對應版本 art_method.h 算
    artMethodCodeItemOff: null,   // ← null 代表「你還沒實測填」
};

// ---- R1: 枚舉 ClassLoader, 找動態載入的真 DEX ----
function findTargetLoader(hintSubstr) {
    var found = null;
    Java.enumerateClassLoaders({
        onMatch: function (loader) {
            var s = loader.toString();
            console.log("[R1][loader] " + s);
            if (hintSubstr && s.indexOf(hintSubstr) !== -1) found = loader;
        },
        onComplete: function () {}
    });
    return found;
}

// ---- R3 前置: 找 interpreter bridge 攔截點 ----
function findBridge() {
    var libart = Process.getModuleByName("libart.so");
    var addr = null, name = null;
    libart.enumerateSymbols().forEach(function (sym) {
        CONFIG.bridgeSymbolHints.forEach(function (h) {
            if (!addr && sym.name.indexOf(h) !== -1) { addr = sym.address; name = sym.name; }
        });
    });
    if (addr) console.log("[R3] 攔截點 = " + name + " @ " + addr);
    else console.log("[R3][!] 攔截點沒找到, 調整 bridgeSymbolHints");
    return addr;
}

// ---- R4: 從記憶體 dump 一塊 DEX ----
function dumpDex(base, size, path) {
    try {
        var magic = base.readByteArray(4);   // 期望 64 65 78 0a
        var f = new File(path, "wb");
        f.write(base.readByteArray(size));
        f.close();
        console.log("[R4] dumped " + size + " bytes -> " + path +
                    " (magic=" + hexdump(magic, {length:4, header:false}) + ")");
    } catch (e) {
        console.log("[R4][!] dump 失敗: " + e);
    }
}

// 從 ArtMethod 拿 DexFile 的 begin+size (思路; offset 要實測)
function dexRangeFromArtMethod(artMethodPtr) {
    // 路徑: ArtMethod -> declaring_class_ -> dex_cache_ -> dex_file_ -> begin_/size_
    // 每一跳的 offset 都要查對應版本原始碼 / 動態驗證。
    // 這裡回 null 表示「未校準」, 你實測後填回真正的讀取邏輯。
    return null; // { base: ptr, size: n }
}

// ---- 主流程 ----
Java.perform(function () {
    console.log("[*] mini-FART 啟動");

    // R1
    var loader = findTargetLoader("real.dex") || findTargetLoader("InMemoryDex");
    if (loader) { Java.classFactory.loader = loader; console.log("[R1] 切到動態 loader"); }

    // R2
    CONFIG.targetClasses.forEach(function (cn) {
        try {
            var c = Java.use(cn);
            var ms = c.class.getDeclaredMethods();
            console.log("[R2] " + cn + ": " + ms.length + " 方法");
        } catch (e) { console.log("[R2][!] " + cn + " 找不到: " + e); }
    });

    // R3 + R4: 攔 bridge, 方法經過就 dump 它所屬的 DEX (整塊, 一次即可)
    var bridge = findBridge();
    var dumpedOnce = false;
    if (bridge) {
        Interceptor.attach(bridge, {
            onEnter: function (args) {
                var artMethod = args[0]; // ← 版本相關, 用 Step4a 反推確認
                if (!dumpedOnce) {
                    var range = dexRangeFromArtMethod(artMethod);
                    if (range && range.base) {
                        dumpDex(range.base, range.size,
                                CONFIG.dexOutDir + "dump_real.dex");
                        dumpedOnce = true;
                    }
                }
            }
        });
        console.log("[*] 攔截點已掛。現在到 App 手動觸發各功能, 讓方法經過 bridge。");
        console.log("[*] (別暴力 invoke 全部方法, 會觸發殼防護/崩潰)");
    }
});
```

**這份骨架故意留了兩個 `null`（`artMethodCodeItemOff`、`dexRangeFromArtMethod` 回 null）**——因為那些正是「必須你在目標裝置實測」的部分。填不上這兩個，就代表你還沒真正掌握 Ch 34 的 `ArtMethod` 佈局；填上了，你就真的懂了。這是設計，不是漏寫。

</details>

<details>
<summary>點開看 fix_dex_header.py（重算 checksum/signature，可實跑的純 Python）</summary>

```python
#!/usr/bin/env python3
# fix_dex_header.py — 修 dump 出來的 DEX 的 checksum(adler32) + signature(SHA-1)
# (純演算法, 不需 Android; 對應 Ch4 的 header 校驗)
import sys, struct, zlib, hashlib

def fix(path):
    with open(path, "rb") as f:
        data = bytearray(f.read())
    if data[0:4] != b"dex\n":
        print("[!] 不是 DEX (magic 不對), base 可能沒對準"); return
    # signature = SHA-1(bytes[32:]), 放 offset 12..32
    data[12:32] = hashlib.sha1(bytes(data[32:])).digest()
    # checksum = adler32(bytes[12:]), 放 offset 8..12
    data[8:12] = struct.pack("<I", zlib.adler32(bytes(data[12:])) & 0xffffffff)
    out = path.replace(".dex", "_fixed.dex")
    with open(out, "wb") as f:
        f.write(data)
    print("[+] 修好 ->", out)

if __name__ == "__main__":
    fix(sys.argv[1])
```

這支是**純 Python、能實跑**（不碰 Android），把 Ch 4「改 body 就要重算 header 兩欄」的知識用在 dump 產物上。dump 出來的 DEX 若 header 校驗過不了、工具打不開，跑它修一遍。

</details>

---

## 測試表（逐項打勾驗收）

| # | 測試項 | 怎麼測 | 通過標準 |
|---|---|---|---|
| T1 | 枚舉 ClassLoader 有輸出 | 跑 Step 1 | 列出 ≥1 個 ClassLoader，含 PathClassLoader |
| T2 | 找到動態載入的 DEX | 看 Step 1 輸出 | 出現非 base.apk 的 DexClassLoader/InMemory 來源 |
| T3 | 切 loader 後找得到目標類 | 跑 Step 2 | 印出方法清單，無 ClassNotFoundException |
| T4 | 攔截點有效 | 跑 Step 4，操作 App | console 刷出 `[pass] ArtMethod=...` |
| T5 | 確認 args 裡的 ArtMethod* | Step 4a 反推 | 某 arg 等於已知方法的 ArtMethod 指標 |
| T6 | dump 出合法 DEX 起點 | `xxd dump_real.dex \| head` | 開頭是 `64 65 78 0a 30 33 35` |
| T7 | header 修復後可打開 | 跑 fix_dex_header.py + jadx/baksmali | 工具不報格式錯 |
| T8 | 方法體非空 | baksmali 反目標方法 | `.method` 內有指令，非空殼 |
| T9 | 不觸發殼防護/不崩 | 全程觀察 | App 沒因你的操作 crash/自殺 |

全過 = 你造出了一個能用的 mini-FART。過不了某項——對照上面「卡點預告」的對應編號。

---

## 延伸挑戰（行有餘力）

1. **自動觸發而非手動點**：目前你靠手動操作 App 讓方法經過 bridge。試著用受控 invoke（Ch 36 做法一）自動觸發目標類的無參方法，注意控速、避開有副作用的方法——體會「自動化 vs 觸發防護」的張力。
2. **逐方法 dump code_item 而非整塊 DEX**：進階版不 dump 整塊 DEX，而是每個方法經過 bridge 時，讀它 `ArtMethod.dex_code_item_offset_` 對應的 code_item、單獨 dump，最後填回骨架 DEX 的對應位置——這才是「抽取殼」真正需要的粒度（整塊 dump 只在殼把整份填回時夠用）。
3. **對付 InMemoryDexClassLoader（不落檔）**：找一個用 `InMemoryDexClassLoader` 的目標，透過 `DexFile.mCookie`（Ch 35 進階）反查 native DEX 的記憶體 base，dump 出來——處理「pull 不到檔」的情況。
4. **跨版本適配**：在另一個 Android 版本的 AVD（如 API 30）跑你的腳本，記錄哪些 symbol 名/offset 變了、你怎麼調——親身體會 FART 系工具「版本適配」的工作量。
5. **對抗一個反脫殼 demo**：寫一個「執行完立刻抹掉 code_item」的 mini 殼，再想辦法讓你的 dump 搶在抹掉前——體會殼與脫殼的時機軍備競賽。

---

## 自我檢核

- [ ] 我能不看範例，說出 mini-FART 的四大步（枚舉 loader → 枚舉方法 → 觸發/攔 bridge → dump + 驗證）各對應前面哪一章
- [ ] 我知道為什麼要用 `-f`（spawn）而不是 attach 來跑這個脫殼器（Ch 37 早窗口）
- [ ] 我能解釋「攔 interpreter bridge」比「暴力 invoke 所有方法」好在哪
- [ ] 我親手在我的 AVD 上實測校準過 bridge symbol 名（沒有照抄），並知道為什麼不能硬編
- [ ] 我能說出 dump 出的 DEX 打不開時，先查 header 的哪兩個欄位（Ch 4）
- [ ] 我能判斷「dump 出方法體還是空的」是哪個環節出問題、怎麼回去修
- [ ] 我清楚 mini-FART 治得了抽取型殼、治不了 VMP/dex2c，以及後者該往哪走（Part 4）

---

## 這個練習串起了什麼

回頭看你剛做的事，它把 Part 6 前四章縫成一條可執行的鏈：

- **Ch 34（`ArtMethod`）**：你 dump 的 code_item、你讀的 `dex_code_item_offset_`、你確認的 `args` 裡的 `ArtMethod*`——全是這章的結構。
- **Ch 35（ClassLoader）**：你枚舉 loader、切 `classFactory.loader` 找動態 DEX——這章的機制。
- **Ch 36（主動調用/攔 bridge）**：你的攔截點選擇、「填回後 dump」的時機——這章的原理。
- **Ch 37（進程/時機）**：你用 `-f` spawn 搶早窗口、你的 agent 在 target 進程裡的身分——這章的物理前提。
- **Ch 4（DEX 格式）**：你修 header 的 checksum/signature、你驗 magic——最早那章的知識在這裡收尾。

**你不再是「用某個脫殼工具」，你是「懂脫殼工具在對 `ArtMethod` 做什麼、並能自己寫一個」的人。** 這正是 Part 6 這條「ART 系統內部」主線要把你帶到的地方。

下一章我們把整門課的所有能力收攏成一套方法論——拿到一個完全陌生的 App，從偵察到攻破的標準作業流程（SOP）。

→ [Ch 38 完整逆向方法論：陌生 App 的 SOP](./38-re-methodology.md)
