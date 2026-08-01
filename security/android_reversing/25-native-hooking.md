# Ch 25 — hook native 進階：inline / PLT hook

> **目標**：把「hook native」從 Ch 14 的「Frida `Interceptor.attach` 一個函式」拆到底層——搞懂 **inline hook**（改函式前幾條指令，跳去你的 trampoline 再跳回來）與 **PLT/GOT hook**（改一個間接跳轉表的指標，換掉整個模組對某函式的呼叫）兩種截然不同的機制，理解 Frida 的 `Interceptor` 底層是哪一種、`Module.enumerateImports/Exports` 怎麼用來定位目標、以及**怎麼 hook 一個被 strip、連符號都沒有的函式**（用 offset）。這章讓你從「會呼叫 Frida API」變成「知道每個 API 底下發生什麼、失敗時知道去哪查」。

> **環境**：Frida 16.x。native hook 要在**真機或 arm64 AVD image** 上驗（x86_64 AVD 的 `.so` 是 x86）。本 repo 沙箱無 Android/Frida，因此所有 hook 的**執行結果標「未實測，理論預期行為」**並附驗證步驟；hook 的**機制原理、指令改寫的計算**是通用事實，如實說明。ARM64 指令與呼叫慣例延續 Ch 20。

## 為什麼需要這個？

Ch 14 你已經會 `Interceptor.attach(addr, {onEnter, onLeave})` 攔一個 native 函式印參數了。那為什麼還要往下挖？三個現實理由：

1. **strip 過的函式沒名字**——現代 `.so` 大量 strip，你想 hook 的核心函式在 IDA 裡叫 `sub_1A40`，沒有符號。`Interceptor.attach(Module.getExportByName(...))` 這種靠名字的寫法**完全用不上**，你得靠 offset 硬定位。不懂底層，這一步就卡死。
2. **hook 失敗時要會 debug**——inline hook 改指令，遇到 PC-relative 指令、太短的函式、或函式正在執行時改寫，會出各種詭異的 crash。不懂 trampoline 怎麼組的，你只會看到「hook 完就 segfault」卻不知為何。
3. **選對 hook 種類**——想攔「這個 `.so` 對 `strcmp` 的所有呼叫」用 PLT hook 一行搞定；想攔「這一個特定函式本身」用 inline hook。用錯種類事倍功半。懂機制才選得對。

這章是 Part 4 native 逆向的收尾武器：Ch 22 靜態看、Ch 23 認演算法、Ch 24 停下來看記憶體，這章讓你**在不停住進程的前提下，攔改任意 native 行為**，然後練習 C 把這些全部用上。

## 先建立直覺：hook 就是「劫持控制流」

所有 hook 的本質是同一件事：**讓程式在該去 A 的時候，先繞到你的 B，你看完/改完再（可能）回 A**。差別只在「劫持發生在控制流的哪個環節」。native 世界有兩個天然的劫持點：

```
   一次函式呼叫的控制流：
   caller ──── bl printf ────▶ [PLT stub] ──jmp──▶ [GOT 裡的 printf 真實位址] ──▶ printf 函式體
                                    ▲                        ▲                         ▲
                                    │                        │                         │
                              (呼叫端)              PLT/GOT hook 劫持這     inline hook 劫持這
                                                    （改表裡的指標）        （改函式頭幾條指令）

   兩種 hook 的根本差異：
   ┌─────────────────┬──────────────────────────┬────────────────────────────┐
   │                 │ PLT/GOT hook             │ inline hook                │
   ├─────────────────┼──────────────────────────┼────────────────────────────┤
   │ 改什麼           │ 一個「跳轉表的指標」       │ 「函式本體」的前幾條指令     │
   │ 影響範圍         │ 只影響「某模組」對它的呼叫 │ 影響「所有人」對這函式的呼叫 │
   │ 能 hook 誰       │ 只能 hook 跨模組匯入的符號 │ 任意函式，含模組內部/strip  │
   │ 需要位址         │ 符號名（查 GOT 條目）     │ 函式起始位址（可用 offset） │
   │ 破壞性           │ 低（只改一個指標）        │ 高（改可執行碼，要處理指令）│
   └─────────────────┴──────────────────────────┴────────────────────────────┘
```

記住這張表你就懂 90%：**PLT/GOT hook 改的是「表」，範圍小、乾淨、但只能攔匯入函式；inline hook 改的是「碼」，能攔任何東西、範圍全局，但要小心處理被覆蓋的指令**。Frida 的 `Interceptor.attach` 底層走的是 inline hook（所以它能 hook 任意位址，包括 strip 的 `sub_xxxx`）。

## PLT/GOT hook：改一個指標換掉整組呼叫

先講較簡單、破壞性較低的 PLT/GOT hook。要懂它先懂 `.so` 怎麼呼叫**別的庫**的函式（例如 `libnative.so` 呼叫 libc 的 `strcmp`）。

**底層機制**：`.so` 編譯時不知道 `strcmp` 會被載到哪（ASLR + 動態連結），所以它不直接 `bl <strcmp絕對位址>`，而是走兩層間接：

```
 libnative.so 內部呼叫 strcmp 的路徑：

  程式碼:  bl  strcmp@plt                    ← 呼叫「PLT stub」，位址編譯時固定
              │
  .plt:    strcmp@plt:                        ← 一小段跳板
              adrp x16, GOT
              ldr  x16, [x16, #strcmp_off]    ← 從 GOT 讀「strcmp 真實位址」
              br   x16                        ← 跳過去
              │
  .got:    [strcmp 條目] = 0x7fxxxx (真實位址)  ← linker 執行期填的「函式指標」
                                                 ↑↑↑ PLT/GOT hook 就改這一格！
```

**GOT（Global Offset Table）裡那一格，就是一個存著「strcmp 真實位址」的指標**。PLT/GOT hook 做的事極簡單：**把那一格的值，從 strcmp 的真實位址，改成你的函式位址**。之後 `libnative.so` 每次 `bl strcmp@plt`，跳板從 GOT 讀到的就是你的函式，控制流被你接管。

**關鍵性質**：
- **範圍限於「這個模組」**——你改的是 `libnative.so` 自己的 GOT，只有它對 `strcmp` 的呼叫被劫持，別的庫（甚至 libc 自己內部）不受影響。要全局攔 `strcmp` 得改每個模組的 GOT。
- **只能 hook「匯入的符號」**——GOT 只有跨模組呼叫才有條目。模組**內部**的函式（`static` 的、自己的 helper）不走 GOT，PLT hook 碰不到，得用 inline hook。
- **極乾淨**——只改一個 8-byte 指標，不動任何可執行碼，沒有指令改寫的風險。反調試也較難偵測（但可以校驗 GOT）。

**Frida 做 PLT/GOT hook（理論預期行為）**——Frida 沒有直接的「GOT hook」高階 API，但 `Interceptor.replace` 配合手動找 GOT 條目可做；更常見的是直接 `Interceptor.attach(Module.getExportByName("libc.so","strcmp"), ...)`，那是 inline hook 到 strcmp 本體（全局生效）。若真要精準只改某模組的 GOT，用 `Module.enumerateImports` 找到條目位址後 `ptr(...).writePointer(myFunc)`：

```javascript
// 找 libnative.so 匯入表裡的 strcmp 條目（GOT slot），改成自己的函式
const imports = Module.enumerateImports("libnative.so");
const strcmpImp = imports.find(i => i.name === "strcmp");
console.log("strcmp GOT slot @ " + strcmpImp.slot + " -> " + strcmpImp.address);
// strcmpImp.slot 就是 GOT 裡存指標的位址；改它即完成 PLT/GOT hook
const myCb = new NativeCallback((a, b) => {
    console.log("[strcmp] " + a.readUtf8String() + " vs " + b.readUtf8String());
    return 0;   // 假裝相等（繞過某些校驗）
}, 'int', ['pointer','pointer']);
ptr(strcmpImp.slot).writePointer(myCb);
```

## enumerateImports / enumerateExports：定位的兩張表

上面用到 `enumerateImports`，這裡把 Frida 定位 native 目標的兩個核心 API 講清楚——它們對應 `.so` 的兩張符號表（Ch 21 的 ELF 知識）：

```
  一個 .so 有兩種對外符號：
  ┌──────────────────────────────────────────────────────────┐
  │ Exports (匯出)：這個 .so 「提供」給別人的函式               │
  │   例：libnative.so 匯出 JNI_OnLoad、Java_com_x_Sign_calc   │
  │   → Module.enumerateExports("libnative.so")               │
  │   → 用來：找「我要 hook 的目標函式」（有符號時）           │
  ├──────────────────────────────────────────────────────────┤
  │ Imports (匯入)：這個 .so 「用到」的外部函式（走 GOT）       │
  │   例：libnative.so 匯入 strcmp、malloc、__android_log_print│
  │   → Module.enumerateImports("libnative.so")               │
  │   → 用來：PLT/GOT hook、看它依賴哪些庫函式（推測行為）      │
  └──────────────────────────────────────────────────────────┘
```

**實用套路（理論預期行為）**：

```javascript
// 列出 libnative.so 匯出的所有函式（找 hook 目標）
Module.enumerateExports("libnative.so").forEach(e => {
    if (e.type === 'function') console.log(e.name + " @ " + e.address);
});
// 快捷取單一匯出的位址
const addr = Module.getExportByName("libnative.so", "Java_com_example_Sign_calc");
Interceptor.attach(addr, { onEnter(a){ console.log("called, arg0=" + a[0]); } });
```

- **enumerateExports** 幫你在**有符號**時秒定位目標（`Java_...` 這種 JNI 函式一定是匯出的，找它最快）。
- **enumerateImports** 幫你(1)做 PLT hook，(2)**推測 `.so` 在幹嘛**——看它匯入了 `AES_encrypt`/`EVP_*` 就知道有 OpenSSL，匯入 `pthread_create` 就知道有多執行緒（可能反調試在別的 thread）。這是 Ch 23 認演算法的旁證來源之一。

> **但 strip 之後 exports 只剩匯出符號**：`static` 的內部函式、被 strip 的 helper 不在 exports 裡。JNI 函式因為要被 ART 找到**必須匯出**所以還在，但真正幹活的內部函式（`sub_1A40`）不在——這就是為什麼需要下一節的 offset hook。

## inline hook：改函式頭幾條指令跳 trampoline

inline hook 是最強、也最需要小心的一種：它直接**改寫函式本體的前幾條指令**，讓它一進來就跳去你的程式碼。它能 hook **任意位址**——匯出的、內部的、strip 的、甚至一個函式中間的某條指令——因為它不靠符號，只靠位址。

**底層機制**——以 ARM64 為例，一條指令 4 byte，跳遠處要用暫存器間接跳：

```
  原函式 target（未 hook）：
    0x1000:  stp x29, x30, [sp,#-16]!    ← 原本的頭幾條指令（prologue）
    0x1004:  mov x29, sp
    0x1008:  ...

  inline hook 後：
    0x1000:  ldr x16, #8         ┐  被覆蓋！改成「跳去 my_hook」
    0x1004:  br  x16             │  (ARM64 遠跳：載入位址再 br)
    0x1008:  <my_hook 的 8 byte 位址>  ┘
        │
        ▼ 跳到你的 hook
    my_hook:
        ... 你的程式碼（看/改參數）...
        ▼ 要放行原函式時，跳去「trampoline」
    trampoline（Frida 動態生成的一小塊碼）：
        stp x29, x30, [sp,#-16]!   ← 「被覆蓋的原指令」搬來這執行
        mov x29, sp                ← 也搬來
        b   0x1008                 ← 再跳回原函式沒被覆蓋的部分
```

**兩個核心難點**，也是 inline hook 會 crash 的根源：

1. **被覆蓋的指令要「搬家」**——你在 `0x1000` 塞了跳轉，原本那幾條 `stp/mov` 被蓋掉了。放行原函式時它們還是得執行，所以 Frida 把它們**複製到 trampoline** 裡先跑，再跳回 `0x1008` 續執行。這叫**指令重定位（relocation）**。

2. **PC-relative 指令搬家會壞**——如果被搬走的指令是 `adr x0, #0x20`（算「相對當前 PC 的位址」）或 `b`/`bl`（相對跳轉），搬到 trampoline 後 PC 變了，算出來的位址就錯了。Frida 的 relocator **必須改寫這些指令**（把相對位址換算成從新位置算的正確值）。這是 inline hook 引擎最難、最容易出 bug 的部分。**函式太短**（不足以放下跳轉指令）或**開頭剛好是複雜的 PC-relative 指令**，都可能讓 hook 失敗或 crash。

**Frida 的 `Interceptor.attach` 就是包好的 inline hook**——它自動處理跳轉組裝、指令重定位、trampoline 生成，你只管給位址和 callback：

```javascript
Interceptor.attach(ptr("0x7xxxx"), {          // 位址可以是任意的，不必有符號
    onEnter(args) { console.log("arg0=" + args[0]); },   // 進入時
    onLeave(ret)  { console.log("ret=" + ret); ret.replace(0); }  // 離開時可改返回值
});
```

`Interceptor.replace` 則是**完全換掉**函式（不放行原函式，除非你自己在裡面呼叫 `NativeFunction` 版的原函式）——適合「我要完全改寫這函式行為」。`attach` 是「攔一刀還放行」，`replace` 是「整個換人」。

## hook strip 掉的函式：用 offset

核心痛點：目標函式 strip 了，IDA 裡叫 `sub_1A40`，沒符號，`getExportByName` 找不到它。解法是**用它相對 `.so` 基址的 offset** 定位——你在 IDA 靜態看到它在 `0x1A40`（相對 image base），執行期真實位址 = **模組載入 base + 0x1A40**。

**做法（理論預期行為）**：

```javascript
// 1. 拿到 .so 在這次執行的載入 base
const base = Module.findBaseAddress("libnative.so");
console.log("libnative.so base = " + base);

// 2. base + IDA 看到的靜態 offset = 真實位址
//    注意 Thumb 模式（32-bit ARM）要 +1，ARM64 不用
const target = base.add(0x1A40);

// 3. 對這個位址 inline hook——完全不需要符號
Interceptor.attach(target, {
    onEnter(args) {
        console.log("sub_1A40 called, x0=" + args[0] + " x1=" + args[1]);
    },
    onLeave(ret) { console.log("sub_1A40 ret=" + ret); }
});
```

**這是 strip 場景的通用鑰匙**：只要你在 IDA 靜態算得出偏移，執行期 `base + offset` 就能 hook 它，符號有沒有無所謂。練習 C 逆一個 `RegisterNatives` 動態註冊的、沒有標準 `Java_...` 名字的 native 方法，就靠這招（或先 hook `RegisterNatives` 拿到函式指標）。

> **關鍵陷阱：offset 的基準與 Thumb**。(1) IDA 顯示的位址可能已含它假設的 image base，要確認你用的是**相對偏移**還是絕對位址——搞混會差一個 base。(2) 32-bit ARM 的 **Thumb** 函式，位址最低位要 **+1** 標記 Thumb 態，忘了會 hook 到錯的解碼模式直接 crash；ARM64 沒這問題。(3) offset 是對「這一版 `.so`」算的，App 更新 `.so` 變了，offset 就失效——offset hook 天生脆弱，換版要重算。

## 對比與取捨：三種 hook 怎麼選

| 場景 | 選哪種 | 為什麼 |
|---|---|---|
| 攔一個有符號的 JNI 函式印參數 | `Interceptor.attach` + `getExportByName` | 最直接，符號在 |
| 攔一個 strip 的內部函式 | `Interceptor.attach` + `base + offset` | inline hook 不靠符號 |
| 攔「某模組對 strcmp 的所有呼叫」 | PLT/GOT hook（改 `import.slot`） | 只影響該模組、乾淨 |
| 攔「全進程對 strcmp 的呼叫」 | `attach(getExportByName("libc.so","strcmp"))` | inline hook 到本體，全局生效 |
| 完全改寫一個函式的行為 | `Interceptor.replace` | 不放行原函式，整個換掉 |
| 只想改一個返回值 | `attach` 的 `onLeave` + `ret.replace()` | 攔一刀改返回，最小侵入 |

**Frida vs 手寫 inline hook 庫（如 Dobby / And64InlineHook）**：Frida 開發快、跨平台、有完整 relocator，但注入明顯（反 Frida 偵測得到，Ch 30）；手寫 inline hook 庫可編進你自己的 `.so`/Xposed 模組裡更隱蔽、但要自己處理指令重定位的地獄細節。逆向分析階段用 Frida，做持久化/隱蔽工具才考慮手寫。

## 踩雷集錦

1. **offset 搞混相對/絕對**：IDA 顯示 `0x1A40` 到底是相對 image base 還是含 base 的絕對位址？用 `base + 相對offset`。差一個 base 直接 hook 到垃圾位址 crash。
2. **32-bit ARM 忘了 Thumb +1**：Thumb 函式位址要 `| 1`。忘了會用 ARM 模式解碼 Thumb 碼，指令全錯、crash。ARM64 無此問題。
3. **PLT hook 只 hook 一個模組卻期待全局**：改 `libnative.so` 的 GOT 只攔它自己的呼叫，別的庫呼叫同一函式不受影響。要全局用 inline hook 到函式本體。
4. **hook 內部 static 函式卻用 PLT**：內部函式不走 GOT，`enumerateImports` 裡沒有，PLT hook 碰不到。用 inline hook + offset。
5. **hook 太短的函式或壞在 PC-relative**：函式短到放不下跳轉、或開頭是 `adr`/`b`/`ldr literal` 這類 PC-relative，inline hook 的指令重定位可能失敗 → hook 完就 crash。換個 hook 點（往後幾條指令）或用別的機制。
6. **App 更新後 offset 全失效**：offset 綁死特定版本的 `.so`。App 一更新，重算所有 offset。有符號的 `attach(getExportByName)` 對版本更穩，能用符號就別用 offset。
7. **在函式執行中途 hook 它自己**：正在跑的函式，你改它前幾條指令，其他 thread 可能剛好執行到被改的區——競態導致偶發 crash。Frida 內部有處理但極端情況仍會出事；hook 時機盡量在函式空閒時。
8. **hook 了 RegisterNatives 註冊的方法卻找不到位址**：這種方法沒有 `Java_...` 匯出符號。先 hook `RegisterNatives`（它的第三參數 `JNINativeMethod*` 陣列裡有函式指標），從中撈出真實位址再 hook。練習 C 會走這條。

## 進階：再往深一層

- **hook RegisterNatives 撈動態註冊的函式指標**：`RegisterNatives(env, clazz, JNINativeMethod* methods, int n)`——第三參數是 `{name, signature, fnPtr}` 陣列。`Interceptor.attach(Module.getExportByName(null,"RegisterNatives"))`（它在 libart 裡），在 `onEnter` 讀 `args[2]` 那個陣列，把每個 native 方法的名字與 `fnPtr` 印出來——**一次拿到所有動態註冊函式的真實位址**。這是逆「函式藏在 RegisterNatives」的標準開局（Ch 19 + 練習 C）。
- **Frida Stalker 做指令級 trace**（Ch 15）：inline hook 攔「函式邊界」，Stalker 能 trace「每一條執行過的指令」——它用動態重編譯（把原碼一塊塊複製、插樁後執行）。逆混淆、找隱藏分支時比 hook 強大得多，但慢、記憶體吃得兇。
- **inline hook 的反制與反反制**：App 可校驗自己函式的前幾 byte（CRC 自己的 `.text`），發現被改（inline hook 留下跳轉痕跡）就報警——這是完整性校驗（Ch 32）打 hook。反反制是 hook 那個校驗函式本身，或用不改碼的 PLT hook / 硬體斷點（Ch 24 的 watchpoint）替代 inline hook。
- **`Interceptor.attach` 的 CpuContext**：`onEnter`/`onLeave` 的 callback 裡 `this.context` 給你完整暫存器（`this.context.x0..x28`、`pc`、`sp`），可以直接讀改任意暫存器，比 `args[]` 更底層——攔非標準呼叫慣例的函式（或函式中途）時必用。
- **NativeCallback 的 ABI 要對**：`Interceptor.replace` 或 GOT hook 塞自己的函式時，`NativeCallback` 的參數/返回型別、呼叫慣例要跟原函式**完全一致**，錯了 stack 就亂、crash。這是把 JS 函式接回 native 世界的邊界，最容易錯。

## 動手練習

> 需真機或 arm64 AVD image + 你有權分析的 App（自寫 native crackme 最佳）。沙箱無法代跑。

1. **enumerate 兩張表**：對一個 `.so` 跑 `Module.enumerateExports` 與 `enumerateImports`，看它匯出哪些 JNI 函式、匯入哪些庫函式，從匯入清單**推測它用了什麼**（有 `EVP_*`？有 `pthread`？）。
2. **符號 hook**：用 `getExportByName` + `Interceptor.attach` 攔一個 `Java_...` 函式，印出參數與返回值。這是 Ch 14 的複習，也是 offset hook 的對照組。
3. **offset hook**：在 IDA 找一個 strip 的內部函式的偏移，用 `Module.findBaseAddress + add(offset)` 定位並 hook，印它的 `x0`。親手體會「沒有符號也能 hook」。
4. **PLT/GOT hook**：用 `enumerateImports` 找某 `.so` 的 `strcmp` GOT slot，`writePointer` 換成你的 `NativeCallback`，讓它永遠回 0（相等），觀察某個字串比對校驗被你繞過。
5. **hook RegisterNatives**：attach `RegisterNatives`，`onEnter` 解析 `args[2]` 的 `JNINativeMethod` 陣列，印出所有動態註冊的方法名與函式指標——為練習 C 熱身。
6. **改返回值**：用 `onLeave` + `ret.replace()` 把一個校驗函式的返回從 false 改 true，看 App 行為變化——第一次用 native hook **改變**而非只**觀察**。

## 本章重點整理

- hook = **劫持控制流**；native 有兩個劫持點：**PLT/GOT**（改跳轉表的指標）與 **inline**（改函式本體的指令）。
- **PLT/GOT hook** 改 GOT 一個指標，只影響**該模組**對**匯入函式**的呼叫，乾淨但範圍受限；**inline hook** 改函式頭幾條指令跳 trampoline，能 hook **任意位址（含 strip/內部）** 但要處理指令重定位、有 crash 風險。
- **Frida `Interceptor.attach` 底層是 inline hook**，所以能 hook 任意位址；`replace` 是整個換掉、`attach` 是攔一刀還放行。
- **`enumerateExports`** 找 hook 目標（有符號時，JNI 函式一定在）；**`enumerateImports`** 做 PLT hook 並推測 `.so` 行為。
- **strip 的函式用 `base + offset` hook**——IDA 靜態偏移 + 執行期模組 base；注意相對/絕對基準、32-bit Thumb +1、換版失效。
- inline hook 兩大難點：**被覆蓋指令要搬去 trampoline**、**PC-relative 指令搬家要改寫**——這是 crash 的主因。
- 動態註冊（`RegisterNatives`）的函式沒標準符號，先 hook `RegisterNatives` 撈函式指標。

## 自我檢核

- [ ] 能畫出一次跨模組函式呼叫的 PLT→GOT→函式體路徑，並指出兩種 hook 各劫持哪裡
- [ ] 能說出 PLT/GOT hook 與 inline hook 在「改什麼、影響範圍、能 hook 誰」三方面的差異
- [ ] 知道 Frida `Interceptor.attach` 底層是哪種 hook，以及為什麼它能 hook strip 的函式
- [ ] 能解釋 inline hook 為什麼要 trampoline、為什麼 PC-relative 指令搬家會壞
- [ ] 能寫出用 `base + offset` hook 一個沒符號函式的 Frida 程式碼，並說出三個 offset 陷阱
- [ ] 知道 `enumerateExports` 與 `enumerateImports` 各拿來做什麼
- [ ] 知道 `RegisterNatives` 註冊的函式為什麼要特別處理，以及怎麼撈它的位址

## 延伸閱讀

- **[Frida JavaScript API — Interceptor / Module](https://frida.re/docs/javascript-api/)** — Frida 官方
  - **讀哪裡**：`Interceptor.attach`/`replace`、`Module.enumerateImports/Exports`、`NativeCallback`、`CpuContext` 那幾節
  - **和本章的關聯**：本章講的機制，官方 API 是唯一權威且更新最快的用法來源
- **[Frida 底層：Stalker & Interceptor 原理](https://frida.re/docs/stalker/)** — Frida 官方
  - **讀哪裡**：Stalker 動態重編譯與 Interceptor trampoline 的說明
  - **為什麼值得讀**：想懂「inline hook 的指令重定位」與「Stalker 的動態重編譯」底層，這是一手來源
- **[Dobby — 一個 inline hook 框架的原始碼](https://github.com/jmpews/Dobby)** — jmpews
  - **讀哪裡**：ARM64 的 `InstructionRelocation`（指令重定位）那部分
  - **和本章的關聯**：想看「被覆蓋指令搬家、PC-relative 改寫」怎麼真正實作，讀它比讀文件深
- **[HackTricks — Frida hooking native](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/frida-tutorial/index.html)**
  - **讀哪裡**：native hook 與 RegisterNatives 那幾段
  - **前提知識**：讀過本章機制，這裡給你更多可複製的實戰腳本

理論齊了：認演算法（Ch 23）、停下來看記憶體（Ch 24）、攔改任意 native 行為（本章）。接下來把三者串成一條真實任務——逆一個把簽名演算法從 Java 搬進 `libsign.so`、還用 `RegisterNatives` 藏起函式的 App，找到它、逆出演算法、寫出能重放的簽名。

→ [練習 C：逆一個把簽名搬進 .so 的 App](./practice-c-native-signature.md)
