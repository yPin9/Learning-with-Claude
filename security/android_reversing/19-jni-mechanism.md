# Ch 19 — JNI 機制：Java 與 native 的邊界

> **目標**：搞懂 Java 世界怎麼呼叫到 native `.so` 裡的 C/C++ 函式——這條邊界叫 **JNI（Java Native Interface）**。你要能回答：一個 `native` 方法怎麼綁到 `.so` 裡的某個函式？綁定有**靜態命名**（`Java_pkg_Class_method`）與**動態註冊**（`RegisterNatives`）兩種，逆向時差別在哪？`JNIEnv*` 是什麼、為什麼每個 native 函式第一個參數都是它？以及最關鍵的：**逆一個把演算法藏進 native 的 App，為什麼第一步常常是「找 RegisterNatives」**。

## 為什麼需要這個？

現代 App 把值錢的東西（簽名演算法、加密金鑰、風控邏輯、授權校驗）往 native 搬，正是因為 Java 層太好逆了——jadx 一開幾乎是原始碼。搬進 `.so` 之後，你面對的是 ARM64 機器碼，門檻陡升。但無論邏輯藏得多深，它**一定要有一個入口讓 Java 呼叫得到**，否則這段 native 程式碼根本跑不起來。這個入口就是 JNI 綁定。

所以 JNI 是 Part 4 的地基。你在 Ch 14 已經用 Frida hook 過 native 函式，但那時我們是「知道函式位址就 hook」；這一章要補的是：**函式位址從哪來、Java 的 `foo()` 到底對應 `.so` 裡哪個 offset**。搞懂綁定機制，你才知道拿到一個陌生 `.so` 要去哪裡下手，而不是對著幾千個函式亂猜。

## 先建立直覺：JNI 是一座雙向的橋

先在腦中建立這張圖。JNI 不只是「Java 呼叫 C」，它是**雙向**的——native 也能回頭呼叫 Java（讀欄位、呼叫方法、new 物件），而且這條回頭路全靠一個叫 `JNIEnv` 的東西：

```
   Java (ART) 這側                     Native (.so) 這側
 ┌──────────────────────┐           ┌───────────────────────────┐
 │ class Native {       │           │ // C/C++                  │
 │   native String      │  ①綁定    │ jstring sign(             │
 │     sign(String s);  │─────────▶ │   JNIEnv* env,            │
 │ }                    │           │   jobject thiz,           │
 │        │ 呼叫 sign()  │           │   jstring s) {           │
 │        ▼             │  ②進入    │   // 這裡做真正的演算法   │
 │   ART 查綁定表 ───────┼─────────▶ │   ...                     │
 │                      │           │   env->NewStringUTF(...); │
 │   ◀──────────────────┼───────────┤   } ③native 用 env 回呼   │
 │   拿到回傳 jstring    │  ④返回    │      Java                 │
 └──────────────────────┘           └───────────────────────────┘
```

四個環節，每個都是逆向的抓手：

1. **綁定**：`native sign` 這個 Java 方法怎麼對應到 `.so` 裡的某段程式碼——靜態命名 or `RegisterNatives`。
2. **進入**：ART 呼叫時，自動塞了兩個隱藏參數（`JNIEnv*`、`jobject`）到 native 函式前面。
3. **回呼**：native 想操作 Java 物件（把 C 字串轉成 `jstring`、讀某個欄位）全透過 `env`。
4. **返回**：native 回傳 `jstring` 等 JNI 型別，ART 轉回 Java 物件。

記住這張圖，下面每一節都是在補它的細節。

## System.loadLibrary：`.so` 是怎麼被載進來的

在任何 native 方法能被呼叫之前，那個 `.so` 必須先被載入進程。Java 側寫法固定：

```java
public class Native {
    static {
        System.loadLibrary("foo");   // 載入 libfoo.so（自動補 lib 前綴、.so 後綴）
    }
    public native String sign(String input);
}
```

`static {}` 是靜態初始化塊，class 第一次被載入時執行一次。`System.loadLibrary("foo")` 做的事：

1. 依 ABI 到 APK 的 `lib/arm64-v8a/`（或 split APK）找 `libfoo.so`。
2. 呼叫底層 `dlopen()` 把它映射進進程位址空間、做重定位（Ch 21 會拆這步）。
3. **執行 `.so` 的 `JNI_OnLoad`（如果有）**——這是逆向的黃金切入點，下一節專講。

> **逆向線索**：`System.loadLibrary("foo")` 這行在 jadx 裡一搜就到，它直接告訴你「native 邏輯在 `libfoo.so`」。這是你從 Java 層追進 native 層的第一個路標。有時字串是動態拼的（`loadLibrary("f"+"oo")`）想擋你靜態搜，那就 Frida hook `System.loadLibrary` 印出真實參數（Ch 14 的手法）。

## 兩種綁定：靜態命名 vs 動態 RegisterNatives

一個 Java `native` 方法要對應到 `.so` 裡某個 C 函式，有兩條路。**這是本章最重要的區分**，直接決定你逆向的下手方式。

### 路 A：靜態命名（按函式名綁定）

如果你把 C 函式命名成一個特定格式，ART 在第一次呼叫該 native 方法時會**按名字去 `.so` 的匯出符號表裡找**。命名規則：

```
Java_<套件名把 . 換成 _>_<類名>_<方法名>
```

例如 `com.example.Native.sign` 對應的 C 函式名是：

```c
// 套件 com.example，類 Native，方法 sign
JNIEXPORT jstring JNICALL
Java_com_example_Native_sign(JNIEnv* env, jobject thiz, jstring input) {
    // ...
}
```

ART 呼叫時透過 `dlsym(handle, "Java_com_example_Native_sign")` 找到位址。**這種綁定的特徵是：函式名直接寫在 `.so` 的動態符號表（`.dynsym`）裡**。逆向時你 `readelf -s libfoo.so | grep Java_` 或在 IDA 的 Exports 視窗搜 `Java_`，一眼就能把 Java 方法對到 native 函式。

> **命名裡的坑**：如果套件/類/方法名含底線 `_`，規則要把 `_` 轉成 `_1`；含 `$`（內部類）轉 `_00024`；Unicode 字元轉 `_0xxxx`。所以 `Java_com_foo_1bar_...` 裡的 `_1` 其實還原回去是 `foo_bar`。逆向看到怪名字別慌，是這套 mangling。另外，如果有**多載**（同名不同參數），還要在後面接 `__` + 參數簽名，例如 `Java_..._sign__Ljava_lang_String_2`。

### 路 B：動態註冊（RegisterNatives，逆向的主戰場）

第二條路是在 `.so` 載入時，由 native 程式碼**主動告訴 ART**：「我這個 Java 方法，對應到我這個函式指標。」用的 API 是 `JNIEnv->RegisterNatives`：

```c
// 真正幹活的函式，名字可以隨便取（甚至沒符號、被 strip）
static jstring real_sign(JNIEnv* env, jobject thiz, jstring input) {
    // ... 演算法藏在這，函式名叫 sub_1234 都行
}

// 綁定表：{Java方法名, JNI簽名, 函式指標}
static const JNINativeMethod methods[] = {
    { "sign", "(Ljava/lang/String;)Ljava/lang/String;", (void*)real_sign },
};

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void* reserved) {
    JNIEnv* env;
    (*vm)->GetEnv(vm, (void**)&env, JNI_VERSION_1_6);
    jclass cls = (*env)->FindClass(env, "com/example/Native");
    (*env)->RegisterNatives(env, cls, methods, 1);   // ← 綁定發生在這
    return JNI_VERSION_1_6;
}
```

**這才是實戰中絕大多數加固/防護 App 的做法**，因為它有兩個對開發者有利、對逆向不利的性質：

- **函式可以完全匿名**：`real_sign` 不需要匯出、可以被 strip，符號表裡看不到任何 `Java_` 或 `sign`。你 `grep Java_` 什麼都搜不到。
- **綁定關係藏在程式碼裡**：Java `sign` 對應哪個位址，寫死在 `methods[]` 陣列與 `RegisterNatives` 的呼叫裡，得你去 `.so` 裡把那次呼叫找出來、把陣列讀出來，才知道對應關係。

所以「**逆一個 native App 的第一步常常是找 RegisterNatives**」——因為它是那張「Java 方法 → native 函式位址」對照表的唯一來源。找到它，你就把幾千個匿名函式裡的關鍵那幾個標定出來了。Ch 22 會教你在 IDA/Ghidra 裡自動化找它。

### 底層機制：綁定表存在 ArtMethod 裡

再往下一層：綁定的最終結果存在哪？每個 Java 方法在 ART 內部都有一個 `ArtMethod` 結構（Part 6 會深挖），native 方法的 `ArtMethod` 裡有個欄位 `entry_point_from_jni_`（不同版本名字略異），存的就是那個 native 函式的位址。

```
Java 方法 "sign"
   │
   ▼
 ArtMethod { ... , entry_point_from_jni_ = 0x7xxxx (指向 .so 裡的 real_sign) }
   │
   ▼
 呼叫時：ART 從這個欄位取位址，塞好 JNIEnv*/jobject，跳過去
```

- **靜態命名**：第一次呼叫時 ART 才 `dlsym` 找名字、填進這欄位（lazy）。
- **RegisterNatives**：`.so` 載入時就主動把位址填進去（eager）。

這解釋了一個 Frida 技巧：你可以直接 hook `RegisterNatives` 這個 ART 函式，把每次註冊的 `(方法名, 函式指標)` 全印出來——不用逆 `.so` 就拿到對照表。Ch 22 的進階段與 Ch 14 都會用到這招。

## JNIEnv：native 回頭操作 Java 的唯一把手

每個 native 函式的**第一個參數永遠是 `JNIEnv* env`**，第二個是 `jobject thiz`（實例方法）或 `jclass`（靜態方法）。這兩個是 ART 幫你塞的隱藏參數，Java 側的方法簽名裡看不到它們。

`JNIEnv` 本質是一張**函式指標表**（C 裡是 `struct JNINativeInterface*`）。native 想做任何跟 Java 有關的事都得透過它：

```c
jstring s      = env->NewStringUTF(env, "hello");        // C 字串 → jstring
const char* c  = env->GetStringUTFChars(env, s, NULL);   // jstring → C 字串
jclass  cls    = env->FindClass(env, "com/example/Foo"); // 找類
jmethodID m    = env->GetMethodID(env, cls, "bar", "()V");// 找方法
env->CallVoidMethod(env, obj, m);                        // 回呼 Java 方法
jfieldID f     = env->GetFieldID(env, cls, "key", "I");  // 找欄位
jint v         = env->GetIntField(env, obj, f);          // 讀欄位
```

**逆向意義**：在反編譯的 native 程式碼裡，你會看到大量 `(*env)->某函式(env, ...)` 或（C++ 寫法）`env->某函式(...)`。這些呼叫是**理解 native 邏輯的路標**：

- 看到 `GetStringUTFChars` → 這函式正在把某個 Java 字串參數拿出來當 C 字串用（多半是要拿去算 hash/加密）。
- 看到 `NewStringUTF` → 它在組回傳的字串（多半是算完的簽名結果）。
- 看到 `FindClass`/`CallXxxMethod` → native 回頭呼叫了某個 Java 方法（可能去拿金鑰、拿設備資訊）。

在 IDA/Ghidra 裡，`JNIEnv*` 是個結構指標，反編譯出來預設是 `off_xxx[偏移]` 這種難讀的形式。Ch 22 會教你**匯入 JNI 結構型別**，讓反編譯器把 `(*env)[0x29C]` 自動顯示成 `->NewStringUTF`——這一步讓 native 逆向的可讀性天差地別。

### jobject / jstring / jclass 到底是什麼

這些 `j` 開頭的型別，在 native 側都是**不透明的指標（opaque handle）**——你不能直接解參考它們去讀 Java 物件的記憶體，必須透過 `env` 的函式。它們本質是 ART 內部物件的間接引用（透過一張 reference table），這樣設計是為了讓 GC 能安全移動物件而不讓 native 持有的指標失效。

| JNI 型別 | Java 對應 | native 側本質 |
|---|---|---|
| `jobject` | 任意 Object | 不透明引用 |
| `jstring` | String | 不透明引用（要 `GetStringUTFChars` 才拿得到內容） |
| `jclass` | Class | 不透明引用 |
| `jint`/`jlong`/`jboolean` | int/long/boolean | 直接就是 C 的整數（值型別，沒包裝） |
| `jbyteArray` | byte[] | 不透明引用（要 `GetByteArrayElements` 拿內容） |

值型別（`jint` 等）直接對應 C 整數，逆向時就是普通 x0/w0 傳參；引用型別（`jstring`/`jobject`）是指標，內容要透過 `env` 取。這個區分在讀反編譯碼時很有用：一個 `jint` 參數你在 ARM64 裡直接看得到值，一個 `jstring` 參數你得追它被 `GetStringUTFChars` 之後才看得到真正的字串。

## 範例：從 Java 追到 native 函式（含失敗路徑）

假設你在 jadx 看到這段：

```java
public class Crypto {
    static { System.loadLibrary("crypto"); }
    public native String encrypt(String plain, int mode);
}
```

**目標**：找到 `encrypt` 對應 `libcrypto.so` 裡哪個函式。

**嘗試 1（先賭靜態命名）**：算出理論函式名 `Java_com_example_Crypto_encrypt`，去查符號表：

```bash
readelf --dyn-syms libcrypto.so | grep -i encrypt
# 或
nm -D libcrypto.so | grep Java_
```

- **成功情況**：列出 `Java_com_example_Crypto_encrypt`——直接拿到位址，去 IDA 跳過去。
- **失敗情況（很常見）**：什麼都沒有。這代表它用 `RegisterNatives` 動態註冊、且函式被 strip 了。別卡住，換路。

**嘗試 2（找 RegisterNatives）**：靜態上，去 `.so` 裡找 `JNI_OnLoad`（這個通常有匯出符號），順著它找 `RegisterNatives` 呼叫，把 `methods[]` 陣列讀出來——Ch 22 詳細示範。

**嘗試 3（動態最快）**：直接 Frida hook `RegisterNatives`，跑一次 App，把註冊表全印出來：

```javascript
// 攔 ART 的 RegisterNatives，印出每個 (方法名 → 函式位址)
var p = Module.findExportByName("libart.so", "_ZN3art3JNI15RegisterNativesEP7_JNIEnvP7_jclassPK15JNINativeMethodi");
Interceptor.attach(p, {
    onEnter: function (args) {
        var cls = args[1], methods = args[2], n = args[3].toInt32();
        for (var i = 0; i < n; i++) {
            var name = methods.add(i * Process.pointerSize * 3).readPointer().readCString();
            var fnptr = methods.add(i * Process.pointerSize * 3 + Process.pointerSize * 2).readPointer();
            console.log("[RegisterNatives] " + name + " -> " + fnptr);
        }
    }
});
```

> **上面這段 Frida 腳本我在本 repo 沙箱無法執行**（沒有 AVD/`libart.so`）：**未實測，理論預期行為**。`RegisterNatives` 的 C++ mangled 名在不同 Android 版本可能不同，跑不通時用 `frida-trace -U -j '*!*RegisterNatives*'` 或列 `libart.so` 匯出符號找正確的 mangled 名。`JNINativeMethod` 是 `{char* name; char* sig; void* fnPtr;}` 三個指標，所以 stride 是 `3 * pointerSize`、fnPtr 在第三個位置——上面的 offset 計算據此而來。你在自己 AVD 上跑，會印出類似 `[RegisterNatives] encrypt -> 0x7abc...` 的對照。

這三條路的順序（靜態命名 → 找 OnLoad → Frida hook）就是逆 native App 定位入口的標準流程。

## 對比與取捨：靜態命名 vs RegisterNatives

| 面向 | 靜態命名 `Java_...` | 動態 `RegisterNatives` |
|---|---|---|
| 綁定時機 | 首次呼叫（lazy `dlsym`） | `.so` 載入時（`JNI_OnLoad` 內） |
| 函式名在符號表 | **有**（可 `grep Java_`） | 可完全匿名、被 strip |
| 逆向定位難度 | 低（符號直接對應） | 高（要找 OnLoad、讀陣列，或 Frida hook） |
| 開發者為何用 | 簡單、不用寫註冊碼 | 藏函式、支援混淆、集中管理 |
| 你的第一手 | `readelf -s` / IDA Exports | 找 `RegisterNatives` / Frida hook |

實戰結論：**先賭靜態命名（一條指令的事）**，搜不到立刻轉「找 RegisterNatives」。加固/防護 App 幾乎清一色 RegisterNatives，所以這招是你 native 逆向的日常。

## 踩雷集錦

1. **只會搜 `Java_` 就以為函式不存在**：搜不到不代表沒有 native 綁定，多半是 `RegisterNatives` 且函式被 strip。轉去找 `JNI_OnLoad` / hook `RegisterNatives`，別以為此路不通。
2. **忘了 native 函式前兩個隱藏參數**：Java 側 `sign(String)` 只有一個參數，但 native 側 `Java_..._sign` 有**三個**（`JNIEnv*`、`jobject`、`jstring`）。逆向時 x0 是 `env`、x1 是 `thiz`、x2 才是你的第一個真參數。數錯參數位置會把整個分析帶偏。
3. **靜態方法 vs 實例方法第二參數不同**：實例 `native` 方法第二參數是 `jobject thiz`；`static native` 方法第二參數是 `jclass`。位置一樣（x1），但語意不同，別把 `jclass` 當成 `this` 去讀實例欄位。
4. **底線 mangling 看不懂**：`Java_com_foo_1bar_baz` 裡的 `_1` 是原名的 `_`，還原是 `com.foo_bar.baz`。逆向看到 `_1`/`_00024`（`$`）別以為是亂碼。
5. **`GetStringUTFChars` 後忘了它可能是複本**：native 拿字串內容有時是複本、有時是直接指標（`isCopy` 參數告訴你），對逆向理解「改這塊記憶體會不會影響 Java 端」有影響——但更常見的坑是分析時忽略了配對的 `ReleaseStringUTFChars`，誤判記憶體生命週期。

## 進階：再往深一層

- **`JNI_OnLoad` 不只用來註冊**：它是 `.so` 載入時第一段執行的 native 程式碼，加固殼常把「解密真正的 payload」「反調試檢測」「初始化 VM 保護」全塞這。所以逆向時 `JNI_OnLoad` 是你要最先讀的函式——它是 native 側的 `main`。
- **`.init_array` 比 `JNI_OnLoad` 更早**：ELF 的 `.init_array`（Ch 21）裡的建構子函式在 `dlopen` 期間、`JNI_OnLoad` **之前**就跑了。反調試/反 Frida 檢測常藏這，趕在你 attach 之前先動手。逆向卡在「還沒到 `JNI_OnLoad` 就被檢測到」時，回頭查 `.init_array`。
- **`CallStaticObjectMethod` 回呼拿金鑰**：進階防護會讓 native 回頭呼叫一個 Java 方法去取金鑰/設備指紋，這樣金鑰不在 native 也不在 Java 靜態碼裡，而是執行期組出來。逆向時看到 native 裡一串 `FindClass`/`GetStaticMethodID`/`CallStatic...`，就是它在回呼 Java——這時 Frida 兩邊都要 hook 才拼得出全貌。
- **JNI 的間接呼叫約定**：C 裡是 `(*env)->Func(env, ...)`（`env` 是指向指標的指標，要兩次解參考再傳自己進去）；C++ 裡是 `env->Func(...)`（編譯器幫你做）。反編譯出來若看到 `(*(*env + 偏移))(env, ...)` 這種雙重解參考，那就是在呼叫某個 `JNIEnv` 函式，用偏移量反查是哪一個。

## 動手練習

1. 拿 Ch 0 撈出的任一含 `lib/` 的 APK，`unzip` 出裡面的 `.so`，用 `readelf --dyn-syms *.so | grep Java_`（或 `nm -D`）看它有沒有靜態命名的 JNI 函式。有的話，挑一個名字，反推它對應的 Java `類.方法`（把 `_` 規則套回去）。
2. 找一個搜不到 `Java_` 的 `.so`（多半是有防護的 App），確認它是 `RegisterNatives` 派——`readelf --dyn-syms *.so | grep -i onload` 看有沒有 `JNI_OnLoad`。這就是你下一章要在 IDA 裡切入的點。
3. 寫一個最小 NDK 專案（一個 `native String hello()`），分別用「靜態命名」和「`RegisterNatives`」兩種方式實作同一個方法，各編一份 `.so`，用 `readelf -s` 對比兩者符號表的差異——親眼看 RegisterNatives 那份為什麼 `grep Java_` 搜不到。

## 本章重點整理

- **JNI 是 Java↔native 的雙向橋**：Java 呼 native、native 用 `JNIEnv*` 回頭操作 Java 物件。
- `System.loadLibrary("foo")` 載入 `libfoo.so`，並執行它的 `JNI_OnLoad`——這是從 Java 追進 native 的第一個路標。
- 綁定有兩種：**靜態命名**（`Java_pkg_Class_method`，符號表可搜）與 **`RegisterNatives`**（函式可匿名/strip，是防護 App 的主流）。
- 逆 native App 的第一步常是**找 RegisterNatives**，因為它是「Java 方法 → native 函式位址」對照表的唯一來源。
- 每個 native 函式前兩個隱藏參數是 `JNIEnv*`（x0）與 `jobject/jclass`（x1），真參數從 x2 起——數參數位置別忘了這兩個。

## 自我檢核

- [ ] 不看筆記，能畫出 Java 呼叫一個 native 方法時，JNIEnv/jobject/真參數各在哪個暫存器
- [ ] 能說出靜態命名與 RegisterNatives 兩種綁定的差別，以及各自在逆向時怎麼定位入口
- [ ] 能解釋為什麼防護 App 偏好 RegisterNatives，以及它讓符號表 `grep Java_` 搜不到的原因
- [ ] 能把 `Java_com_foo_1bar_baz` 這種 mangled 名還原回 Java 的類與方法名
- [ ] 知道 `JNI_OnLoad` 與 `.init_array` 為什麼是加固/反調試最先要讀的兩個 native 進入點

## 延伸閱讀

- **[JNI Specification（Oracle 官方）](https://docs.oracle.com/en/java/javase/17/docs/specs/jni/index.html)**
  - **讀哪裡**：「Resolving Native Method Names」那節（靜態命名 mangling 規則）與 `RegisterNatives`、`JNINativeMethod` 結構定義
  - **和本章的關聯**：本章的命名規則、`methods[]` 結構的權威出處，逆向遇到怪 mangled 名回這查
- **[Android JNI Tips（AOSP 官方）](https://developer.android.com/training/articles/perf-jni)**
  - **讀哪裡**：`JNIEnv` 與 `JavaVM` 的差別、local/global reference、`GetStringUTFChars` 的複本行為
  - **為什麼值得讀**：它從「正向開發者該注意什麼」講 JNI，你逆向時看到的每個陷阱（reference 生命週期、`isCopy`）都對應這裡的規則
- **[Android JNI Reference（jni.h 函式表）](https://developer.android.com/ndk/reference/group/jni)**
  - **讀哪裡**：`JNIEnv` 的完整函式清單；逆向時反查「這個偏移對應哪個 JNI 函式」的對照表來源
  - **和本章的關聯**：Ch 22 匯入 JNI 型別後，反編譯器顯示的 `->NewStringUTF` 等名字就出自這份
- **[Frida — hook RegisterNatives（社群範例）](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 `RegisterNatives`，讀現成腳本怎麼解析 `JNINativeMethod` 陣列
  - **前提知識**：讀過本章的 `RegisterNatives` 段與 Ch 14 的 native hook，這些腳本才看得懂

下一章我們正式進入 native 機器碼的世界。你已經知道入口函式在哪，但打開它是一堆 ARM64 組語——`mov`/`ldr`/`bl`/`ret` 混在一起。Ch 20 補齊「逆向需要的 ARM64」：暫存器、呼叫慣例（哪個暫存器傳參、哪個放回傳值）、常見指令與反編譯 pattern，讓你讀得懂 `.so` 裡到底在算什麼。

→ [Ch 20 ARM64 逆向必備](./20-arm64-for-re.md)
