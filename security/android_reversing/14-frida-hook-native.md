# Ch 14 — Frida hook native 層

> **目標**：把 Frida 從 Java 層打進 `.so`。你要能寫並看懂：用 `Interceptor.attach` 的 `onEnter`/`onLeave` 攔任意 native 函式、用 `Module.getExportByName`/`Module.findBaseAddress` + offset 定位函式、用 `NativePointer` 與 `Memory.read*`/`write*` 讀寫 native 記憶體、用 `NativeFunction` **主動呼叫**一個 native 函式、以及 hook JNI 函式看 Java↔native 的邊界流量。這是把「值錢邏輯搬進 native 擋 Java hook」的 App 撬開的一章。

> **環境**：**Frida 16.x**、Ch 0 的 **x86_64 AVD（Android 13 / API 33，已 root、frida-server 已跑）**。**架構提醒（本章最重要的環境事實）**：你這台 AVD 的 `.so` 是 **x86_64** 編譯的，不是手機的 ARM64。所以本章的暫存器慣例、`args[]` 對應、指標大小都以 **x86_64 System V ABI** 為準——ARM64 的暫存器/呼叫慣例不同，那是 Part 4（Ch 19–25）的主場。好消息是 **Frida 的 native API 本身是跨架構的**（`args[0]` 就是第一個參數，不管底層是 `rdi` 還是 `x0`），你這章學的寫法在 ARM64 上一樣能用，只有「手動讀暫存器」那種底層操作才架構相關。腳本輸出一律標「**未實測，理論預期行為**」+ 你在 AVD 的驗證步驟。

## 為什麼需要這個？

因為現代 App 把最值錢的東西——簽名演算法、加密金鑰、風控、授權校驗——**故意搬進 native `.so`**，正是為了擋只會 Java hook 的人（Ch 1 踩雷 4）。你在 Java 層 hook 到的常常只是個「轉呼叫 native」的空殼：`return nativeSign(data)`。真相在 `.so` 裡。學會 hook native，你才能：在 native 函式入口印出真實參數（Java 層看不到的、已解密的資料）、在出口偷回傳值、甚至繞過整個函式體直接餵假返回、還能**主動呼叫**App 的 native 函式當黑箱用（丟參數進去、拿結果出來，不管它怎麼實作）。這是 Ch 11「不還原演算法、直接偷結果」在 native 層的實現。

## 先建立直覺：native 沒有「方法名」，只有位址

Java 層你有 `com.foo.Bar.check` 這種好認的名字（Ch 13）。native 層世界變了——**CPU 眼裡只有記憶體位址**。一個 `.so` 載入後被映射到某個基底位址（base address），函式散落在 `base + offset` 的各個位置。你 hook native 的第一件事永遠是：**把「我想 hook 的函式」變成一個記憶體位址**。

```
   .so 檔（磁碟上）              載入後（target 進程記憶體）
 ┌──────────────────┐         ┌────────────────────────────────┐
 │ ELF header       │         │ base = 0x7xxxxxxxx000（每次隨機） │
 │ .text (程式碼)   │  載入   │   ├─ 匯出函式 Java_..._sign      │
 │   Java_..._sign  │ ──────▶ │   │   位址 = base + 該符號 offset │
 │   內部函式(無符號)│         │   ├─ 內部函式（沒符號）           │
 │ .symtab / .dynsym│         │   │   位址 = base + 你逆出的 offset│
 └──────────────────┘         └────────────────────────────────┘
        ▲                              ▲
   有匯出符號的：用名字找          沒符號的：base + IDA 裡看到的 offset
   Module.getExportByName          Module.findBaseAddress + add
```

兩條定位路線，對應兩種函式：

- **有匯出符號**（JNI 函式、`.so` 匯出的 API）：用名字直接找位址——`Module.getExportByName("libfoo.so", "Java_com_foo_Bar_sign")`。
- **沒符號的內部函式**（靜態函式、被 strip 掉符號的）：先拿 `.so` 的 base、加上你**在 IDA/Ghidra 逆出來的 offset**——`Module.findBaseAddress("libfoo.so").add(0x1234)`。這裡的 `0x1234` 不是魔法數字，是你在反組譯工具裡看到「該函式相對 .so 起點的偏移」（Ch 22 教怎麼逆出來）。

記住這個「函式 = 位址」的世界觀，後面全部建立在它上面。

## 底層機制：Interceptor 在 native 函式頭做了什麼

Ch 12 提過 Interceptor 靠 trampoline（inline hook）。在 native 這裡它更赤裸：

```
  hook 前：函式開頭幾條真指令
     sign:  push rbp
            mov  rbp, rsp
            ...

  Interceptor.attach 後（Gum 改寫函式開頭）
     sign:  jmp  <trampoline>      ← 開頭被改成跳去 Frida 的 trampoline
            (原指令被搬到別處保存)
                 │
                 ▼
        trampoline:  呼叫你的 onEnter（此時可讀/改參數）
                     執行被搬走的原指令
                     執行函式本體 …
                     回來時呼叫你的 onLeave（此時可讀/改返回值）
```

兩個攔截點，對應兩個時機：

- **`onEnter(args)`**：函式**剛進入、還沒執行本體**時。此時參數在暫存器/堆疊裡，`args[0]`、`args[1]`... 就是第 1、2... 個參數。你能讀它們、也能改它們。
- **`onLeave(retval)`**：函式**執行完、正要返回**時。`retval` 是返回值，你能讀、能改（`retval.replace(...)`）。

`args[N]` 怎麼對應到暫存器？這是架構相關的，但 **Frida 幫你抽象掉了**：

| 參數 | x86_64（你的 AVD） | ARM64（真機/Part 4） | Frida 統一寫法 |
|---|---|---|---|
| 第 1 個 | `rdi` | `x0` | `args[0]` |
| 第 2 個 | `rsi` | `x1` | `args[1]` |
| 第 3 個 | `rdx` | `x2` | `args[2]` |
| 返回值 | `rax` | `x0` | `retval` |

**你寫 `args[0]` 就好，不用管底層是 `rdi` 還是 `x0`**——這就是為什麼本章的腳本在 x86_64 AVD 和 ARM64 真機上都能跑。只有當你要「手動讀某個特定暫存器」（`this.context.rdi`）時才碰到架構差異。

## 核心一：`Interceptor.attach` 攔一個匯出函式

先攔一個有符號的 JNI 函式。假設 App 的簽名邏輯是 `Java_com_example_app_Native_sign(JNIEnv*, jobject, jbyteArray)`：

```javascript
// hook_native_sign.js
Java.perform(function () {
    // 1. 定位函式：有匯出符號，用名字找位址
    var addr = Module.getExportByName("libnative.so", "Java_com_example_app_Native_sign");
    console.log("[*] sign @ " + addr);

    // 2. 掛上攔截
    Interceptor.attach(addr, {
        onEnter: function (args) {
            // JNI 函式的參數：args[0]=JNIEnv*, args[1]=jobject(this),
            //                 args[2]=第一個 Java 傳來的參數(jbyteArray)
            console.log("[sign] onEnter, JNIEnv=" + args[0] + " thiz=" + args[1]);
            // 把 args[2] 這個 NativePointer 存起來，onLeave 還要用
            this.inputPtr = args[2];
        },
        onLeave: function (retval) {
            // retval 是返回值（這裡是 jbyteArray，一個指標）
            console.log("[sign] onLeave, 回傳指標=" + retval);
        }
    });
});
```

逐行為什麼這樣寫：

- **`Module.getExportByName(so, symbol)`**：回傳該符號的 `NativePointer`（位址）。名字錯或 `.so` 沒載入會回 `null`——所以有時要等 `.so` 載入後才 attach（見下面時序）。
- **JNI 函式頭兩個參數固定是 `JNIEnv*` 和 `jobject`**（Ch 19 深講）。所以 App 真正傳的第一個參數從 `args[2]` 開始——這是 hook JNI 函式必記的偏移。
- **`this.inputPtr = args[2]`**：`onEnter` 和 `onLeave` 之間可以用 `this` 傳遞資料（Frida 保證同一次呼叫的 `this` 一致）。想在 `onLeave` 用到入參，就在 `onEnter` 存進 `this`。

## 核心二：`NativePointer` + `Memory.read*` 讀出參數內容

上面只印了「指標」（一個位址），沒印內容。native 的參數常是指標（指向字串、byte 陣列、struct），你得**順著指標把記憶體讀出來**。這是 native hook 和 Java hook 最大的體感差異——Java 給你物件，native 給你位址，你得自己挖。

```javascript
// hook_native_read.js —— 讀出一個 C 字串參數
Java.perform(function () {
    // 假設 libnative.so 有 int check(const char* password)
    var addr = Module.getExportByName("libnative.so", "check");
    Interceptor.attach(addr, {
        onEnter: function (args) {
            // args[0] 是 char*，用 readCString 把 C 字串讀出來
            var pwd = args[0].readCString();
            console.log("[check] password = " + pwd);
        },
        onLeave: function (retval) {
            // 回傳 int，用 toInt32 轉成數字
            console.log("[check] 回傳 = " + retval.toInt32());
        }
    });
});
```

`NativePointer` 上常用的讀法（全是「把這個位址當成某型別的資料讀出來」）：

| 方法 | 讀出什麼 | 用於 |
|---|---|---|
| `.readCString()` | 到 `\0` 為止的 C 字串 | `char*` 字串參數 |
| `.readUtf8String(len)` | UTF-8 字串 | Java 傳來的字串內容 |
| `.readByteArray(n)` | n 個 byte（hexdump 用） | byte 陣列、二進位 buffer |
| `.readU8/U32/U64()` | 1/4/8 byte 整數 | 數字、struct 欄位 |
| `.readPointer()` | 一個指標（8 byte on 64-bit） | 指向指標的指標、struct 裡的指標欄位 |
| `.add(offset)` | 位址 +offset（回新 NativePointer） | 讀 struct 的某欄位、陣列元素 |

**dump 一段記憶體看內容**（不確定型別時最實用）：

```javascript
        onEnter: function (args) {
            // 把 args[0] 指向的 64 byte 印成 hexdump
            console.log(hexdump(args[0], { length: 64, ansi: false }));
        }
```

> **為什麼 `hexdump` 好用**：native 參數常常你一開始不知道是字串、struct 還是加密 blob。先 `hexdump` 印 64 byte 看它長相——看到可讀 ASCII 就當字串讀、看到結構化的就對照 IDA 逆出的 struct 佈局。這是 native hook 的探路標準動作。

## 核心三：改參數、改返回值、繞過整個函式

跟 Java 一樣，native hook 也能改，而且更底層：

```javascript
// hook_native_modify.js
Java.perform(function () {
    var addr = Module.getExportByName("libnative.so", "check");
    Interceptor.attach(addr, {
        onEnter: function (args) {
            // (A) 改參數：把傳進去的 C 字串內容覆寫掉
            //     先確認緩衝區夠大，否則寫爆會崩（見踩雷）
            args[0].writeUtf8String("hijacked");
        },
        onLeave: function (retval) {
            // (B) 改返回值：不管原本算出什麼，一律回 1（假設 1=通過）
            console.log("[check] 原回傳 " + retval.toInt32() + " -> 改成 1");
            retval.replace(ptr(1));   // 用 replace 換掉返回值
        }
    });
});
```

- **`retval.replace(ptr(x))`**：換返回值。`ptr(1)` 把數字 1 包成 NativePointer（返回值在暫存器裡本質是個機器字）。這一招能繞過「native 算出的校驗結果」——不管它內部算什麼，出口一律給你要的值。
- **改參數要小心緩衝區大小**：`writeUtf8String` 寫超過原緩衝區會踩壞相鄰記憶體導致崩潰（native 沒有 Java 的邊界保護）。改字串內容時，新字串別比原的長。

## 核心四：`NativeFunction` 主動呼叫 native 函式

前面都是「被動攔截」——等 App 呼叫函式時插一腳。`NativeFunction` 反過來：**你主動呼叫 App 的 native 函式**，把它當黑箱用。這是 Ch 11「用 App 自己的邏輯幫你算」在 native 的極致——你有簽名函式的位址，就能自己丟資料進去、拿簽名出來，完全不管它演算法。

```javascript
// call_native.js —— 主動呼叫一個 native 加密函式
Java.perform(function () {
    var addr = Module.getExportByName("libnative.so", "encrypt");
    // 用 NativeFunction 把「位址 + 型別簽名」包成可呼叫的 JS 函式
    //   簽名：char* encrypt(char* input)
    //   格式：new NativeFunction(位址, 返回型別, [參數型別...])
    var encrypt = new NativeFunction(addr, 'pointer', ['pointer']);

    // 準備參數：在 native 記憶體配一個 C 字串
    var input = Memory.allocUtf8String("hello");   // 回傳指向該字串的指標

    // 呼叫它，拿回傳指標
    var resultPtr = encrypt(input);
    console.log("[encrypt] 結果 = " + resultPtr.readCString());
});
```

逐行為什麼這樣寫：

- **`new NativeFunction(addr, retType, [argTypes])`**：把一個位址「型別化」成能呼叫的函式。型別字串用 Frida 的：`'pointer'`、`'int'`、`'void'`、`'int64'` 等。**型別簽名必須跟真實函式一致**（你從 IDA 逆出來的），錯了會傳錯參數/讀錯返回，直接崩或垃圾值。
- **`Memory.allocUtf8String("hello")`**：native 函式要指標，你得先在 target 記憶體裡「準備好」資料再把指標傳進去。`alloc*` 系列負責在 target 進程配記憶體。
- **`encrypt(input)`**：像普通 JS 函式一樣呼叫，Frida 幫你把 JS 值轉成 native 呼叫慣例（塞暫存器/堆疊、跳過去、收返回）。

> **為什麼這招強**：練習 B（還原請求簽名）常這樣收尾——你找到簽名的 native 函式後，不用把演算法逆到能自己重寫，直接 `NativeFunction` 呼叫它，餵不同輸入拿輸出，App 的演算法變成你的 oracle。省下逆整個演算法的時間。

## 核心五：沒符號的函式——`findBaseAddress` + offset

內部函式常被 strip 掉符號，`getExportByName` 找不到。這時走「base + offset」：

```javascript
// hook_by_offset.js
Java.perform(function () {
    var base = Module.findBaseAddress("libnative.so");
    if (base === null) {
        console.log("[!] libnative.so 還沒載入");
        return;
    }
    // 0x2340 是你在 IDA/Ghidra 裡看到的「該函式相對 .so 起點的偏移」
    //   —— 不是憑空的數字，是逆向工具裡讀出來的（Ch 22）
    var funcAddr = base.add(0x2340);
    console.log("[*] 目標函式 @ " + funcAddr + " (base " + base + " + 0x2340)");

    Interceptor.attach(funcAddr, {
        onEnter: function (args) {
            console.log("[internal] 被呼叫, args[0]=" + args[0]);
        }
    });
});
```

- **`0x2340` 是唯一需要你「從別處取得」的數字**——它來自你在 IDA/Ghidra 逆 `.so` 時，該函式的位址減去 `.so` 載入基底。沒有反組譯這一步，你不知道要 hook 哪個 offset。這也是為什麼 native 逆向是 Frida native hook 的前置（Part 4）。
- **PIE 與 ASLR**：`.so` 每次載入的 base 是隨機的（ASLR），所以絕不能寫死絕對位址，一定是 `findBaseAddress + offset`。offset 是固定的（相對 .so 起點），base 是執行期才知道的。

> **`.so` 還沒載入怎麼辦**：`findBaseAddress` 回 `null` 代表那個 `.so` 這時還沒被 App 載入（動態載入的庫尤其如此）。解法：hook `dlopen`/`android_dlopen_ext`，在它載入目標 `.so` 後才去 attach。這是 native hook 的時序問題，跟 Ch 12 的 spawn/attach、Ch 13 的類載入時序是同一類問題。

## 核心六：hook JNI 函式看邊界流量

Java↔native 的所有往來都經過 **JNI**（Ch 19 深講）。hook JNI 的關鍵函式，你能看到「Java 世界和 native 世界交換了什麼」。一個高頻用法——攔 `RegisterNatives` 找出「動態註冊」的 native 方法對應哪個位址：

```javascript
// hook_registernatives.js —— 揪出動態註冊的 native 方法
Java.perform(function () {
    // libart.so 匯出 JNI 的 RegisterNatives 實作
    var addr = Module.getExportByName("libart.so", "_ZN3art3JNI15RegisterNativesEP7_JNIEnvP7_jclassPK15JNINativeMethodi");
    // 上面這串是 art::JNI::RegisterNatives(...) 的 C++ mangled 名字
    Interceptor.attach(addr, {
        onEnter: function (args) {
            // 參數：env, jclass, JNINativeMethod* methods, int count
            var methods = args[2];
            var count = args[3].toInt32();
            for (var i = 0; i < count; i++) {
                // JNINativeMethod = { char* name; char* sig; void* fnPtr; }
                //   在 64-bit 上每個欄位 8 byte，整個 struct 24 byte
                var namePtr = methods.add(i * 24 + 0).readPointer();
                var sigPtr  = methods.add(i * 24 + 8).readPointer();
                var fnPtr   = methods.add(i * 24 + 16).readPointer();
                console.log("[RegisterNatives] " + namePtr.readCString() +
                            " " + sigPtr.readCString() + " -> " + fnPtr);
            }
        }
    });
});
```

為什麼要這招：native 方法有兩種綁定方式——「靜態」（函式名叫 `Java_com_foo_...`，`getExportByName` 找得到）和「動態」（用 `RegisterNatives` 在執行期把 Java 方法綁到一個**任意名字的**native 函式）。動態註冊的函式名字可能是 `a1b2c3`，你 `getExportByName("Java_...")` 根本找不到。hook `RegisterNatives` 就能揪出「哪個 Java 方法 → 哪個 native 位址」，這是加固/混淆 App 藏 native 邏輯的常用手法，也是你的破解點。

- **那串 mangled 名字不是魔法**：`_ZN3art3JNI15RegisterNativesE...` 是 C++ 的 name mangling（`art::JNI::RegisterNatives`）。用 `frida-trace` 或 `nm -D libart.so | grep RegisterNatives` 能找到當前系統對應的符號（不同 Android 版本可能略有差異，要以你 AVD 上實際的為準）。
- **`i * 24` 那個 24**：`JNINativeMethod` struct 在 64-bit 上是三個 8-byte 指標 = 24 byte（不是魔法數字，是 struct 佈局算出來的）。x86_64 AVD 是 64-bit，所以 24。

> **驗證步驟**：`frida -U -f com.example.app -l hook_registernatives.js`，App 啟動時若有動態註冊，會印出一串 `name sig -> 位址`。拿到位址後就能用「核心五」的 offset 方式 hook 那個函式。若沒印出東西，代表這個 App 用靜態綁定（直接 `getExportByName` 找 `Java_...`）。

## 對比與取捨

| 你想做 | 用什麼 | 關鍵注意 |
|---|---|---|
| 攔有符號的函式 | `Module.getExportByName` + `Interceptor.attach` | JNI 函式真正參數從 `args[2]` 起 |
| 攔沒符號的內部函式 | `findBaseAddress().add(offset)` | offset 來自 IDA/Ghidra；base 隨機不能寫死 |
| 讀參數內容 | `NativePointer.read*` / `hexdump` | 不確定型別先 hexdump 探路 |
| 改參數 | `write*` | 別寫超過原緩衝區（會崩） |
| 改返回值 | `retval.replace(ptr(x))` | 型別是機器字 |
| 主動呼叫 native | `NativeFunction` + `Memory.alloc*` | 型別簽名必須跟真實函式一致 |
| 揪動態註冊的 native | hook `RegisterNatives` | struct 每項 64-bit 上 24 byte |
| 等 `.so` 載入 | hook `dlopen`/`android_dlopen_ext` | 動態載入的庫要等它進來才 attach |

## 踩雷集錦

1. **JNI 函式從 `args[0]` 讀 App 參數**：錯。JNI 函式頭兩個是 `JNIEnv*`（`args[0]`）和 `jobject`（`args[1]`），App 真正傳的參數從 **`args[2]`** 開始。少算這兩個，你讀到的全是錯的。
2. **寫死絕對位址**：`.so` 有 ASLR，每次 base 不同。永遠 `findBaseAddress + offset`，offset 固定、base 執行期取。寫死位址這次能跑、下次崩。
3. **`NativeFunction` 型別簽名寫錯**：返回/參數型別跟真實函式不符，輕則垃圾值、重則崩。型別要跟你逆出來的一致（`int` vs `pointer` vs `int64` 別混）。
4. **改字串寫超過原緩衝區**：native 沒邊界保護，`writeUtf8String` 寫爆會踩壞相鄰記憶體崩潰。改內容時新值別比原的長；要更長就得自己 `alloc` 新緩衝區再把指標換過去。
5. **`getExportByName` 找不到就以為沒這函式**：可能是（a）動態註冊的（名字不是 `Java_...`，去 hook `RegisterNatives`）、(b) `.so` 還沒載入（回 null，hook `dlopen` 等它）、(c) 被 strip 沒符號（走 offset）。三種情況三種解法。
6. **忘了這台 AVD 是 x86_64**：`this.context.rdi` 這種手動讀暫存器的寫法在 x86_64 上是 `rdi`，ARM64 上是 `x0`——寫死 `rdi` 的腳本搬到真機不會動。能用 `args[N]` 就別手動讀暫存器（Frida 已抽象）。要碰暫存器，先確認架構。
7. **`onEnter` 存的參數在 `onLeave` 讀不到**：跨 `onEnter`/`onLeave` 傳資料要用 `this.xxx`，別用外層變數（多執行緒/重入下會串線）。

## 進階：再往深一層

- **`Interceptor.replace` vs `attach`**：`attach` 是「插一腳、原函式照跑」；`replace` 是「整個換掉原函式」——你提供一個 `NativeCallback`（JS 函式包成 native 函式指標）完全取代它。要「重寫」而非「觀察」一個 native 函式時用 replace。代價是你得自己處理所有參數與返回。
- **`Interceptor.attach` 的 `this.context`**：`onEnter`/`onLeave` 裡的 `this.context` 給你所有暫存器（x86_64 的 `rax/rdi/...`、ARM64 的 `x0/x1/...`）。當 `args[]` 抽象不夠用（比如你要讀一個「靠某暫存器傳的非標準參數」，或函式中途某點的狀態），直接讀 `this.context.rdi`。這是架構相關的深水區。
- **short function / thumb 對齊問題**：極短的函式（開頭指令湊不滿 trampoline 要覆蓋的長度）或 ARM 的 thumb/arm 模式切換，inline hook 會有麻煩。x86_64 AVD 上前者偶爾遇到，ARM64（Part 4）上後者要特別處理（位址最低位表 thumb）。理解 Ch 12 的 trampoline 機制才知道為什麼會這樣。
- **hook 對抗完整性校驗**：`Interceptor.attach` 改了函式開頭的 byte（trampoline 的 jmp）。有 native 完整性校驗的 App 會自己讀 `.text` 算 hash 發現被改（Ch 32）。進階繞法包括校驗時餵回原始 byte、或改用不動 `.text` 的 hardware breakpoint。理解痕跡在哪才知道怎麼藏。

## 動手練習

1. **攔 JNI 印真參數**：找一個有 native 邏輯的 App（或自己用 NDK 寫一個 `stringFromJNI` 變體，把傳入字串加密回傳）。hook 那個 `Java_..._` 函式，從 `args[2]` 讀出 Java 傳來的字串——親眼確認「真正參數從 args[2] 起」。
2. **偷 native 解密結果**：hook 一個 native 解密函式的 `onLeave`，用 `readCString`/`hexdump` 把回傳的明文印出來。體會「不逆演算法、直接偷結果」在 native 層一樣好使。
3. **主動呼叫當 oracle**：用 `NativeFunction` + `Memory.allocUtf8String` 主動呼叫上一題那個函式，餵不同輸入拿輸出——把 App 的 native 函式當成你的計算服務（練習 B 的核心手法預演）。
4. **揪動態註冊**：對一個你懷疑用 `RegisterNatives` 的 App 跑核心六的腳本，看能不能印出 `name -> 位址`。印出來就拿那個位址走 offset hook 它。印不出來代表它用靜態綁定，改用 `getExportByName("Java_...")`。

## 本章重點整理

- **native 世界只有位址**：hook 第一步永遠是「把函式變成位址」——有符號用 `getExportByName`，沒符號用 `findBaseAddress + IDA 逆出的 offset`（offset 固定、base 隨機）。
- **`Interceptor.attach` 的 `onEnter(args)`/`onLeave(retval)`**：入口讀改參數、出口讀改返回；`args[N]` 跨架構統一（x86_64 的 `rdi` = ARM64 的 `x0` = `args[0]`）。**JNI 函式真參數從 `args[2]` 起**。
- **參數多是指標**：用 `NativePointer.read*`/`hexdump` 挖內容（不確定型別先 hexdump）；改內容別寫爆緩衝區。
- **`NativeFunction` 主動呼叫**：把 App 的 native 函式當黑箱 oracle，餵輸入拿輸出、不逆演算法——native 版「直接偷結果」。
- **hook `RegisterNatives`** 揪動態註冊的 native 方法（名字被藏、`getExportByName` 找不到時的破解點）。
- **環境事實**：這台 AVD 的 `.so` 是 x86_64；手動讀暫存器才架構相關，`args[]`/`NativeFunction` 等寫法跨架構通用，ARM64 逆向在 Part 4。

## 自我檢核

- [ ] 能說出 native hook 為什麼第一步是「找位址」，以及有符號/無符號兩種定位法
- [ ] 知道 JNI 函式的 `args[0]`/`args[1]` 是什麼，App 真正參數從第幾個 arg 開始
- [ ] 能寫出讀 native 字串參數、改返回值的 `Interceptor.attach`
- [ ] 能解釋 `NativeFunction` 主動呼叫的價值，以及型別簽名為什麼不能錯
- [ ] 知道 `getExportByName` 回 null 的三種原因與各自解法
- [ ] 記得這台 AVD 是 x86_64、哪些寫法架構相關、ARM64 在哪一 Part

## 延伸閱讀

- **[Frida 官方文件 — JavaScript API：Interceptor / NativePointer / NativeFunction / Memory / Module](https://frida.re/docs/javascript-api/#interceptor)**
  - **讀哪裡**：`Interceptor.attach`/`replace`、`NativePointer` 的 read/write 方法表、`NativeFunction`、`Memory.alloc*`、`Module.getExportByName`/`findBaseAddress` 各節
  - **學什麼**：本章每個 native API 的權威定義、完整型別字串、`this.context` 的欄位
  - **關聯**：本章是這份 API 的實戰導覽，寫腳本查型別/簽名回這裡
- **[Frida CodeShare — native hook 類腳本](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 native / RegisterNatives / hook so 相關，讀它們怎麼定位無符號函式、怎麼處理 dlopen 時序
  - **學什麼**：真實 App 上 native hook 的完整套路（等 .so 載入、揪動態註冊、dump 記憶體）
  - **關聯**：把本章基本功套到真實 `.so`，看社群怎麼解時序與定位難題
- **[HackTricks — Frida native / JNI hooking](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/frida-tutorial/index.html)**
  - **讀哪裡**：native function hooking 與 JNI 相關段落
  - **學什麼**：一線實務攔 native 簽名/加密函式、繞 native root 檢查的模式
  - **關聯**：接 Ch 19（JNI 機制）與練習 B/C（還原搬進 .so 的簽名）
- **[Android JNI 官方規格 — JNI Functions / RegisterNatives](https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/functions.html)**
  - **讀哪裡**：`RegisterNatives` 與 `JNINativeMethod` struct 定義
  - **學什麼**：本章 hook `RegisterNatives` 用到的 struct 佈局（name/sig/fnPtr）的權威來源
  - **關聯**：Ch 19 會把 JNI 邊界講透，這裡先讓你看懂 hook 到的 struct 是什麼

下一章我們把 Frida 推到進階：Stalker（指令級 trace，對付控制流平坦化）、記憶體掃描（找 pattern、找加密金鑰）、以及從記憶體 dump 出東西（脫殼的前奏）。你在 Ch 13/14 學的 hook 是「點」，Stalker 讓你看「線」——整條執行軌跡。

→ [Ch 15 Frida 進階：Stalker、掃描、dump](./15-frida-advanced-stalker.md)
