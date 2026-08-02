# Ch 39 — JS 引擎崩潰 triage 與可利用性初判

> **目標**：在 fuzzer 產出幾百個 crash 的情境下，建立一套系統性的分類→最小化→可利用性初判流程，最終輸出「值得深挖」的 crash 清單。讀完之後你能對任意 JS 引擎 crash 說出它屬於哪一類、初步判斷是否值得繼續，以及在哪裡銜接 exploit 開發。

---

## 為什麼需要 triage

Fuzzilli 跑一個週末可以累積幾百個 crash。如果每一個都手動看，大多數時間是在看 OOM 和 DCHECK——它們佔了 crash corpus 的絕大多數，幾乎沒有 security value。

問題不在「有沒有 bug」，在「哪個 bug 值得花接下來三天挖 exploit primitive」。Triage 的目的是把這個決策工業化：讓一套流程幫你把 80% 的垃圾篩掉，剩下的才動腦。

三個層次的浪費：
1. **時間浪費**：OOM 和 assertion failure 看起來像 crash，但不是 security issue。
2. **誤判浪費**：Debug build 的 DCHECK crash 讓人以為找到洞，結果 release build 根本不崩。
3. **去重失敗**：同一個 root cause 觸發三十個 crash 變體，全部保留等於重複工作三十倍。

---

## 先建立直覺：crash 分類 pipeline

```
crash corpus（fuzzer 原始輸出，幾百個 testcase）
        │
        ▼
 [Stage 1: 去重 + 粗分類]
  ─────────────────────────
  計算 stack trace hash
  ├── OOM（heap out of memory）       → 丟棄
  ├── Assertion / DCHECK failure      → 低優先（可能只在 debug build）
  ├── Timeout                         → 丟棄或單獨追
  └── SIGSEGV / SIGABRT / SIGILL      → 保留，進入 Stage 2
        │
        ▼
 [Stage 2: Minimization]
  ─────────────────────────
  delta debugging：二分法去掉 JS statement
  每次跑 d8 確認 crash 還在
  輸出：minimal reproducer（幾行 JS）
        │
        ▼
 [Stage 3: 可利用性初判]
  ─────────────────────────
  crash 地址是否受 JS 控制？
  ├── controlled write / read？          → HIGH VALUE
  ├── type confusion 徵兆（map 混淆）？  → HIGH VALUE
  ├── OOB（ASan 確認 offset 可控）？     → MEDIUM / HIGH
  └── UAF（ASan 確認 allocation 路徑）？ → HIGH VALUE
        │
        ▼
 [交棒]
  → 有 minimal reproducer + crash type 標籤
  → browser_pwn: exploit primitive 開發（addrOf / fakeObj / 任意讀寫）
```

這條 pipeline 可以手動跑，也可以部分自動化。手動理解每個 stage 是先決條件。

---

## 崩潰分類：三大類

### OOM（Out of Memory）

**特徵**：

```
Uncaught RangeError: JavaScript heap out of memory
 #
 # Fatal error in , line 0
 # Fatal JavaScript invalid size error 169220804
```

signal 通常是 SIGABRT（glibc abort on malloc failure）或乾淨的 exit code。stack trace 會看到 `v8::internal::Heap::FatalProcessOutOfMemory`。

**處置**：直接丟棄。OOM 不是 security issue，它只代表 fuzzer 生出了會無限分配記憶體的 JS。

### Benign Assertion / DCHECK

**特徵**：只在 debug build 的 d8 才出現。

```
# Fatal error in ../../src/objects/map.cc, line 123
# Check failed: instance_type() == JS_OBJECT_TYPE.
```

Debug build 的 V8 在每個敏感路徑插了 `DCHECK`（debug-only assertion）。Release build 的同一條路徑沒有這個檢查，會繼續跑——可能正常、可能有 UB、可能崩在別的地方。

**處置**：DCHECK crash 本身不算 security issue。它的 value 是「這裡可能有邏輯錯誤」，但要確認 release build 是否也崩，以及崩的方式是否可利用。

### 真正的 crash：SIGSEGV / SIGABRT（ASan）/ SIGILL

這是 triage 要保留的。

- **SIGSEGV**：非法記憶體存取。分 read fault 和 write fault，write fault 通常更有價值。
- **SIGABRT + ASan 報告**：AddressSanitizer 攔到 `heap-buffer-overflow` 或 `heap-use-after-free`，主動 abort。這是最精確的資訊來源。
- **SIGILL**：非法指令。JIT 產生了非法的機器碼，通常代表 JIT compiler 有嚴重邏輯 bug。

---

## 用 ASan build 的 V8 做精確分類

標準的 V8 release build 在 SIGSEGV 時只給你一個 crash 地址和不完整的 backtrace。ASan build 的 d8 會在越界的那一刻攔截，給出：

1. 精確的 crash 類型（`heap-buffer-overflow` / `heap-use-after-free` / `stack-buffer-overflow`）
2. 越界的方向（read / write）
3. 越界的 offset
4. allocation 的 call stack（物件在哪裡被分配）
5. deallocation 的 call stack（UAF 的情況）

**ASan 輸出範例格式（預期輸出格式示範，實際需要 ASan build 的引擎跑）**：

```
=================================================================
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000001a10 at pc 0x55c1a2b3c4d5 bp 0x7ffd... sp 0x7ffd...
WRITE of size 8 at 0x602000001a10 thread T0
    #0 0x55c1a2b3c4d5 in v8::internal::ElementsAccessor::SetImpl(...)
    #1 0x55c1a2b3c890 in v8::internal::JSArray::SetLength(...)
    #2 0x55c1a2b40123 in v8::internal::Runtime_ArraySetLength(...)
    ...

0x602000001a10 is located 8 bytes to the right of 16-byte region [0x602000001a00,0x602000001a10)
allocated by thread T0 here:
    #0 0x7f8b12345678 in malloc (/usr/lib/x86_64-linux-gnu/libasan.so.5+...)
    #1 0x55c1a2b12345 in v8::internal::Heap::AllocateRaw(...)
    ...
```

建議工作流程：

```bash
# 用 ASan build 的 d8 重現（需要事先編譯 ASan 版本）
# 編譯時加 v8_enable_address_sanitizer = true（GN args）
./d8_asan --allow-natives-syntax testcase.js 2>&1 | head -80
```

**注意**：ASan build 比 release build 慢 2–3 倍，記憶體用量也高很多。Stage 1 用 release build 快速過濾，Stage 3 才用 ASan build 深入分析值得保留的 crash。

---

## Minimization（核心）

### 為什麼要做

fuzzer 生出來的 testcase 可能有幾百行——大量無關的 JS，只是碰巧和觸發 crash 的那幾行一起出現。把 testcase 縮小到最小的好處：

1. 更容易閱讀，看出 root cause
2. 減少 noise，讓後續的 exploit 開發有乾淨的起點
3. 同一個 root cause 不同觸發路徑，縮小後會收斂到相同的 minimal form

### Delta Debugging 原理

核心思路是二分法：把 JS 檔案拆成兩半，測試哪一半還能觸發 crash，保留那一半，反覆迭代。

```
原始 testcase（200 行）
        │
        ├── 前 100 行 → 測試 → 沒 crash
        └── 後 100 行 → 測試 → 有 crash
                │
                ├── 後 50 行 → 測試 → 有 crash
                └── 後 50 行 → 測試 → 沒 crash
                        │
                        ...（繼續縮小）
                        │
                最終：5 行 reproducer
```

### Python minimizer 實作

```python
#!/usr/bin/env python3
"""
js_minimizer.py — delta debugging 風格的 JS testcase 縮減器
用法: python3 js_minimizer.py <input.js> <engine> [engine_args...]
例如: python3 js_minimizer.py crash.js ./d8 --allow-natives-syntax
"""

import subprocess
import sys
import tempfile
import os

def check_crash(engine_cmd: list[str], js_content: str, timeout: int = 10) -> bool:
    """
    回傳 True 表示這段 JS 仍然觸發 crash（非零 exit code）。
    timeout 防止無限迴圈的 testcase 卡死。
    """
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w",
                                     delete=False, encoding="utf-8") as f:
        f.write(js_content)
        tmp_path = f.name

    try:
        result = subprocess.run(
            engine_cmd + [tmp_path],
            capture_output=True,
            timeout=timeout,
        )
        # 非零 exit code 視為 crash
        return result.returncode != 0
    except subprocess.TimeoutExpired:
        return False  # timeout 不算 crash
    finally:
        os.unlink(tmp_path)


def minimize(engine_cmd: list[str], lines: list[str]) -> list[str]:
    """
    delta debugging：二分法去掉不需要的行。
    回傳最小的能觸發 crash 的行集合。
    """
    if len(lines) <= 1:
        return lines

    mid = len(lines) // 2
    first_half = lines[:mid]
    second_half = lines[mid:]

    # 嘗試只保留前半
    if check_crash(engine_cmd, "\n".join(first_half)):
        return minimize(engine_cmd, first_half)

    # 嘗試只保留後半
    if check_crash(engine_cmd, "\n".join(second_half)):
        return minimize(engine_cmd, second_half)

    # 兩半都不夠，必須保留全部；對每一半遞迴縮小
    minimized_first = minimize(engine_cmd, first_half)
    minimized_second = minimize(engine_cmd, second_half)
    return minimized_first + minimized_second


def main():
    if len(sys.argv) < 3:
        print(f"用法: {sys.argv[0]} <input.js> <engine> [engine_args...]")
        sys.exit(1)

    input_file = sys.argv[1]
    engine_cmd = sys.argv[2:]

    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()

    # 先確認原始 testcase 能觸發 crash
    if not check_crash(engine_cmd, content):
        print("錯誤：原始 testcase 在此引擎上沒有觸發 crash")
        sys.exit(1)

    print(f"原始行數：{len(lines)}")
    minimized = minimize(engine_cmd, lines)
    result = "\n".join(minimized)

    out_file = input_file.replace(".js", "_minimal.js")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"最小化後行數：{len(minimized)}")
    print(f"輸出：{out_file}")
    print("--- minimal reproducer ---")
    print(result)


if __name__ == "__main__":
    main()
```

**預期輸出格式示範**（實際執行需要有能觸發 crash 的 testcase 和對應引擎）：

```
原始行數：247
最小化後行數：9
輸出：crash_minimal.js
--- minimal reproducer ---
function f(a) {
  return a[0];
}
let arr = [1.1, 2.2];
%OptimizeFunctionOnNextCall(f);
arr.length = 0;
f(arr);
```

**重要注意事項**：JIT 相關的 crash 經常有 timing 和優化層次的依賴。minimizer 跑完的 reproducer 可能只有 6/10 次能穩定觸發。這是正常現象，不代表 minimizer 有問題。

---

## 可利用性初判框架

有了 minimal reproducer，下一步是判斷「這個 crash 值不值得花時間做 exploit」。

### ASCII 圖：從 crash 地址判斷可利用性

```
crash 地址分析（SIGSEGV / 崩潰位置）
│
├── 0x0000000000000000  → null deref
│   ├── V8 sandbox 之前：搭配特定 layout 仍有利用案例
│   └── V8 sandbox 之後（V8 9.x+）：大幅降低，通常 low value
│
├── 0x4141414141414141  → 受控地址（testcase 可控）
│   → HIGH VALUE：controlled write 或 controlled read
│   → 說明你能把任意值當指標，任意讀寫記憶體
│
├── 0x00007fff????????  → 接近 stack top 的地址
│   → stack overflow / stack OOB
│   → value 視 layout 而定，通常 MEDIUM
│
└── heap addr + 小 offset（如 +0x10, +0x18）
    → OOB（out-of-bounds）
    → 需要進一步看 offset 是否可控：
        可控 offset → HIGH VALUE
        固定 offset → MEDIUM（需要 heap layout groom）
```

### Controlled Write / Controlled Read

在 GDB 或 rr 下重現 crash，看崩潰那一刻的暫存器：

```bash
gdb --args ./d8 --allow-natives-syntax crash_minimal.js
(gdb) run
# 等 SIGSEGV
(gdb) info registers
(gdb) x/10i $rip    # 看崩潰的指令
```

關鍵問題：崩潰指令是 `mov [rax], rbx` 這類，而 `rax` 的值是從你的 JS object 來的嗎？如果能在 JS 層控制 `rax` 的值，就是 controlled write。這是 exploit 的頂級原語。

### Type Confusion 徵兆

Type confusion 是 JIT 引擎 bug 最常見的類型：JIT 認為一個物件是 A 類型，實際上是 B 類型，按 A 的 layout 存取 B 的欄位。

用 `%DebugPrint()` 觀察物件的 map：

```javascript
// 需要 --allow-natives-syntax flag
function f(o) {
  return o.x;
}
let obj = {x: 1.1};
%OptimizeFunctionOnNextCall(f);
let result = f(obj);
%DebugPrint(obj);   // 印出 object 的 map address 和類型資訊
```

執行方式：

```bash
./d8 --allow-natives-syntax --print-opt-code testcase.js
# 或更詳細的 IC 資訊
./d8 --allow-natives-syntax --trace-ic testcase.js 2>&1 | grep -i "type\|map"
```

Type confusion 的徵兆：
- 同一個函數對同類型物件多次呼叫，但 `%DebugPrint` 顯示 map 不一致
- IC (Inline Cache) trace 顯示 deoptimization 的原因是「type mismatch」
- ASan 報告 heap-buffer-overflow，但 offset 對不上該類型應有的欄位

### OOB（Out-of-Bounds）

ASan 報告 `heap-buffer-overflow` 時，最重要的資訊是：

1. **write 還是 read**：write OOB 通常比 read OOB 更有利用價值
2. **offset 大小**：offset = 1 或 2 bytes 的越界比 offset = 0x100 更好利用（更容易控制 heap layout）
3. **offset 是否可控**：如果 offset 來自 JS 可控的 array index 計算，就有機會做任意偏移寫

快速確認 offset 是否可控：修改 testcase，把相關的 array size 或 index 改成不同的值，觀察 crash offset 是否跟著變動。

### UAF（Use-After-Free）

ASan 輸出 `heap-use-after-free` 時，同時給你 allocation 和 deallocation 的 call stack。關鍵問題：

1. **freed 的物件大小**：size 相同的物件可以在 free 後佔據同一塊記憶體（tcache / freelist 替換）
2. **use 的時機**：use 在 free 之後多久？是否有 JS 程式碼可以在 free 之後插入操控？
3. **use 的動作是 read 還是 write**：write 到 freed object 的欄位，等於 write 到替換那塊記憶體的物件，能控制那個物件的行為

UAF 在現代 V8 是高 value crash，V8 的 garbage collector 設計讓 UAF 有對應的 exploit 路徑。

### 「值得深挖」判斷標準（速查）

| crash 類型 | 初判 value | 繼續的條件 |
|---|---|---|
| Controlled write（受控地址寫入） | HIGH | 確認 rax/rdx 來源是 JS 可控的 |
| UAF + write | HIGH | 確認 freed size + 有 JS 可填充的時間窗 |
| Type confusion（map 混淆） | HIGH | 確認 JIT 路徑可穩定觸發 |
| OOB write + offset 可控 | HIGH | 確認 offset 跟著 JS 參數變動 |
| OOB read + 資料外洩 | MEDIUM | 看能否洩漏 heap pointer |
| Null deref（V8 sandbox 後） | LOW | 通常放棄 |
| DCHECK（debug only） | LOW | 先確認 release build 是否也崩 |
| OOM | 丟棄 | — |

**這章做到這裡**。把 crash type 標上去，確認 minimal reproducer 能穩定重現之後，exploit primitive 開發（addrOf / fakeObj / 任意讀寫）是 browser_pwn 的工作，不在這裡展開。

---

## DOM Fuzzer 對比：Domato vs Fuzzilli

Fuzzilli 是 IL-based JS fuzzer，但 JS 引擎的 attack surface 不只是 JavaScript 語言本身，還包括 DOM API、CSS、HTML parser 和 rendering engine。這是兩個不同的工具解決不同的問題。

**Domato（Google Project Zero）**：文法生成 HTML/CSS/JS DOM interaction。不需要 patched 引擎，直接在瀏覽器跑生成的 HTML。覆蓋的是 blink/WebKit/Gecko 的 DOM、layout、rendering 層。

| 維度 | Domato | Fuzzilli |
|---|---|---|
| 目標層 | DOM / rendering / layout | JS JIT compiler / runtime |
| 需要 patched 引擎 | 否（跑真實瀏覽器） | 是（需要 coverage 插樁） |
| Setup 難度 | 低（Python script + 瀏覽器） | 高（編譯 V8、Swift 環境） |
| Crash 類型 | DOM UAF、layout OOB、renderer crash | JIT type confusion、heap OOB、UAF |
| 有效 coverage 語意 | 低（不理解 JS 語意） | 高（IL 保證語意有效） |
| 典型發現 | Blink UAF（renderer process） | V8 JIT type confusion |

這兩個工具不是競爭關係，是互補關係。真正完整的 browser security research 會同時跑兩類 fuzzer。

---

## 踩雷

**踩雷一：「debug build 的 DCHECK crash 就是安全洞」**

DCHECK 只在 V8 debug build 啟用。Release build 的同一條路徑完全沒有這個檢查——引擎直接繼續跑，可能正常、可能有 UB、可能在更晚的地方崩，或者根本不崩。在報告 bug 之前，必須確認 release build 也能重現某種異常行為，否則只是在找 debug build 的 assertion，不是 security issue。

**踩雷二：「crash 地址 0x0 代表 null deref，一定不可利用」**

這在舊版 V8 是錯的。V8 sandbox 之前，null deref 搭配特定的 heap layout 仍然有被利用的案例，原因是 null page 在某些系統上是 mappable 的（mmap 0x0），或者 deref 0x0+offset 實際上碰到的是可控的記憶體。V8 sandbox（約 V8 9.x 開始逐步引入，正式稱為 V8 Sandbox / Pointer Compression Cage）之後，大多數 null deref 確實失去了直接利用價值——但這是前提條件（確認 sandbox 是否啟用、是否 bypass），不是理所當然的結論。

**踩雷三：「minimizer 跑完的 reproducer 一定能穩定觸發 crash」**

JIT 崩潰的觸發常常依賴執行次數、優化 tier 的切換時機，甚至記憶體 layout 的隨機性。minimizer 在縮小過程中每次只執行一次（或少數幾次），通過率 6/10 的 flaky crash 很可能在某次迭代剛好不崩而被錯誤地剔除了那幾行。建議：對每個「要丟棄」的縮小決策跑 3–5 次確認，或在 `check_crash()` 裡加重試邏輯。縮小後的 reproducer 一定要多跑幾次確認穩定度，才算真正的 minimal reproducer。

---

## 進階延伸

**自動化 triage pipeline**

ClusterFuzz（Google 開源，用於 OSS-Fuzz）實作了完整的 crash 去重和分組邏輯。去重的核心是 crash signature：擷取 stack trace 的前 N 個 frame（去掉 allocator 和 signal handler 的 frame），計算 hash。同一 hash 的 crash 視為同一個 root cause。ClusterFuzz 還自動跑 minimizer（bisect-fuzz）並追蹤 regression range（哪個 commit 引入的 bug）。

**Differential triage**

把同一個 crash testcase 拿去跑 V8 / SpiderMonkey / JavaScriptCore 三個引擎。只在一個引擎崩、另外兩個正常，說明這個 testcase 觸發的是引擎特有的 bug（而不是 JS 語意的邊界情況）。只在兩個引擎崩，可能是 spec 解釋歧義。三個都崩，可能是 JS 本身的邊界（或者 fuzzer 產生了非法 JS，三個引擎 handle 方式不同但都有問題）。Differential triage 可以快速縮小 root cause 的搜索範圍。

**從 crash 到 CVE report**

Security researcher 的工作流程：minimal reproducer → 確認 root cause 在 source code 的位置 → 判斷 security impact → 草稿 bug report → 送 vendor（Google Chromium 用 crbug.com，有 security 標籤）→ 等待 patch（通常 90 天 deadline）→ 公開。報告格式通常包含：引擎版本、平台、reproducer JS、crash output（ASan 或 gdb backtrace）、初步的 root cause 分析（哪個 commit / 哪個函數）、security impact 評估。

---

## 動手練習

1. 從你的 Fuzzilli crash corpus 中取 20 個 crash，手動跑 `d8` 確認每個的 signal（SIGSEGV / OOM / DCHECK）。統計三類各佔多少比例。

2. 取一個 SIGSEGV crash（50 行以上），跑上面的 `js_minimizer.py`，確認它能縮到 10 行以下。如果 minimizer 在某步把那幾行剪掉了導致不 crash，加重試邏輯（`check_crash` 跑 3 次取多數決）。

3. 對一個保留的 crash，用 GDB 重現後檢查 `info registers`，找出崩潰指令。判斷 rax / rdx 的值是否來自 JS 層可控的來源（嘗試修改 testcase 的數值，觀察暫存器值是否跟著變）。

4. 安裝 Domato（`git clone https://github.com/googleprojectzero/domato`），用預設文法生成 50 個 HTML testcase，在 Chromium 的 `--enable-logging --no-sandbox` 模式下跑，觀察 crash 類型與 Fuzzilli 的差異。

---

## 本章重點

- Fuzzer 產出的 crash 中，OOM 和 DCHECK 佔大多數，全部丟棄或低優先不虧。
- ASan build 的 d8 比 release build 給出更精確的 crash 類型資訊（OOB / UAF），值得在 Stage 3 使用。
- Minimizer 的核心是 delta debugging 二分法：縮小 testcase 到 minimal reproducer，讓後續分析更乾淨。
- 可利用性初判看四件事：crash 地址是否受控、是否有 type confusion 徵兆、OOB offset 是否可控、UAF 的替換時間窗是否存在。
- DCHECK crash 不等於 security issue；null deref 在 V8 sandbox 之後通常 low value，但不是鐵律。
- Minimizer 產出的 reproducer 對 JIT bug 可能有 flakiness，需要多跑幾次確認穩定度。
- 這章的輸出是「minimal reproducer + crash type 標籤」，exploit primitive 開發在 browser_pwn 課。

---

## 自我檢核

- [ ] 能說出 OOM、DCHECK、SIGSEGV 三類 crash 的處置策略
- [ ] 知道 ASan build 比 release build 在 triage 上多給哪些資訊
- [ ] 理解 delta debugging minimizer 的二分法原理
- [ ] 能用 GDB 在 crash 點檢查暫存器，判斷 crash 地址是否受控
- [ ] 知道 `%DebugPrint()` 需要 `--allow-natives-syntax` flag，以及它能觀察什麼
- [ ] 能區分 type confusion、OOB、UAF 三種 crash 的初判方法
- [ ] 知道 Domato 和 Fuzzilli 覆蓋的 attack surface 層次不同，不是競爭關係
- [ ] 了解 flaky reproducer 的來源，以及如何改善 minimizer 的重試邏輯

---

## 延伸閱讀

- **Domato（2017）** — Ivan Fratric / Google Project Zero — `README.md` 和 `generator.py` 主邏輯 — 學文法規則如何生成 HTML/DOM testcase、為什麼文法 fuzzer 能覆蓋 renderer 而不需要覆蓋引擎 — 本課 Part 7 DOM fuzzer 對比的實作基礎（https://github.com/googleprojectzero/domato）

- **"Exploiting Logic Bugs in JavaScript JIT Engines"（WOOT 2019）** — Samuel Groß (saelo) — Section 3（type confusion primitives）和 Section 4（addrOf / fakeObj 構造）— 學 type confusion 從 crash 到 exploit primitive 的完整路徑，以及為什麼 JIT 的 type confusion 如此高 value — 本課 Ch 39 可利用性初判的 type confusion 徵兆對應這裡的 root cause 分析（https://github.com/saelo/jscpwn / USENIX WOOT 2019 proceedings）

- **ClusterFuzz documentation — Crash Analysis** — Google / chromium.googlesource.com — "Crash analysis" 和 "Minimization" 章節 — 學工業規模的 crash grouping（signature 計算邏輯）、自動 minimizer 的設計、regression 追蹤——這是 Google 處理數百萬 crash 的實際方案 — 本課 Stage 1 去重邏輯的工業化對應（https://google.github.io/clusterfuzz/reference/）

---

## 銜接

Part 7（JS 引擎 fuzzing，Ch 37–39）到這裡結束。你現在有完整的 JS 引擎 fuzzing 流程：從語意有效性為何是引擎 fuzzing 的核心障礙（Ch 37）、Fuzzilli 用 FuzzIL 中介語言保證語意有效的 IL-based 設計（Ch 38），到把 crash 轉化成有標籤的 minimal reproducer 並初判可利用性（Ch 39）。exploit 那一段交棒 browser_pwn。

Part 8 轉向符號輔助 fuzzing。Ch 40 從 coverage-guided fuzzing 的根本侷限出發——路徑爆炸和魔術數字問題——說明為什麼要把符號執行和 fuzzer 結合，以及 hybrid fuzzing 的核心架構（fuzzer 探索 + 符號執行解碰撞）。

→ [Ch 40 hybrid fuzzing 原理](./40-hybrid-fuzzing.md)
