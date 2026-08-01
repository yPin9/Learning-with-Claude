# Ch 34 — ART runtime 內部

> **目標**：把「ART 執行一個方法」這件事拆到具體的 C++ 物件層級。你要能畫出並解釋：`ClassLinker` 怎麼把 DEX 的類載進來變成 `mirror::Class`、`ArtMethod`/`ArtField` 這兩個結構裡各有什麼欄位、一次方法呼叫怎麼經過 entrypoint 走到直譯器或機器碼、`entry_point_from_quick_compiled_code_` 這個欄位為什麼是 hook/脫殼的核心戰場。這章是 Part 6 最硬的一章——搞懂它，Ch 35（ClassLoader）、Ch 36（脫殼/hook）對你就是水到渠成，因為那兩章全部的動作，都是在操作本章介紹的這幾個結構。

> **環境**：本章以 **Android 13 / API 33（ART）** 的結構佈局為主要參照。**這是全課最版本敏感的一章**：`ArtMethod`/`ArtField`/`mirror::Class` 的欄位順序、大小、偏移（offset）**每個 Android 版本都可能變**。文中給的是「有哪些欄位、它們的語意」，**不會**給精確 offset——精確 offset 你必須在目標裝置上實測、或對照該版本的 AOSP 原始碼（`art/runtime/art_method.h` 等）。凡涉及 offset/大小的地方會明確標「以 Android 13 為準，版本可能不同，要實測」。本 repo 沙箱無 ART 原始碼，所有結構描述均基於 AOSP 公開原始碼的公開知識，涉及裝置行為處標「**未實測，理論預期行為**」。

## 為什麼需要這個？

因為 Ch 35/36 的每一個技術，本質都是「拿到某個 `ArtMethod` 指標，改它某個欄位」。你如果不知道 `ArtMethod` 長什麼樣、entrypoint 是它的哪個欄位、改了會怎樣，那些技術對你就是背咒語。具體來說：

- **ART hook** = 拿到目標方法的 `ArtMethod`，把它的 `entry_point_from_quick_compiled_code_` 改指向你的程式碼（或改 `access_flags` 讓它走直譯再攔）。不懂這個欄位，你不懂 hook 為什麼這樣做。
- **主動調用脫殼**（Ch 36、練習 E）= 拿到被抽空的方法的 `ArtMethod`，觸發它執行讓殼把方法體填回，再從 `ArtMethod` 找到 `code_item` dump 出來。不懂 `ArtMethod` 怎麼指向 code、你不知道要 dump 哪。
- **Frida 的 `Java.use`** 底層 = 透過 ClassLinker 找到 `mirror::Class`、再從裡面找到 `ArtMethod`。你懂了這條鏈，Frida 那些「找不到類/方法」的錯誤你才知道卡在哪一環。

一句話：**這章是把前面所有「Frida hook」「脫殼」的黑盒，拆成你看得見、摸得到、能自己操作的 C++ 物件。** 之後你不是在用工具，是在理解工具在動哪個結構。

## 先建立直覺：ART 是一堆 C++ 物件在跑 Java

先破除一個幻覺：ART 不是什麼神祕虛擬機黑盒，它就是一個**用 C++ 寫的程式**，裡面用一堆 C++ 結構來「代表」你的 Java 世界。你 Java 裡的每個 class、每個 method、每個 field、每個 object，在 ART 進程的記憶體裡都對應一個具體的 C++ 結構：

```
   你的 Java 世界              ART 進程記憶體裡的 C++ 結構
 ┌────────────────┐          ┌──────────────────────────────┐
 │ class Foo      │ ───────▶ │ mirror::Class  (描述這個類)    │
 │                │          │   ├ methods_ ─▶ ArtMethod[]    │
 │  int x;        │ ───────▶ │   │              (每個方法一個) │
 │                │          │   ├ fields_  ─▶ ArtField[]     │
 │  String sign() │ ───────▶ │   │              (每個欄位一個) │
 │    {...}       │          │   └ ...                        │
 │                │          │                                │
 │ Foo obj = ...  │ ───────▶ │ mirror::Object  (實例, 開頭有   │
 │                │          │   一個指標指回它的 mirror::Class)│
 └────────────────┘          └──────────────────────────────┘
```

三個「代表關係」記住：

- **一個 Java 類 → 一個 `mirror::Class`**：描述這個類有哪些方法、欄位、父類、載入它的 ClassLoader 是誰。
- **一個 Java 方法 → 一個 `ArtMethod`**：描述這個方法的 access flags、屬於哪個類、它的 DEX code 在哪、**執行時該跳去哪（entrypoint）**。
- **一個 Java 物件 → 一個 `mirror::Object`**：實例資料，開頭有個指標（`klass_`）指回它是哪個 `mirror::Class` 的實例。

**你所有的 ART 層逆向，都是在讀寫這些結構的某個欄位。** 這章就是帶你認識這幾個結構、和「執行一個方法」時它們怎麼串起來。

## ClassLinker：把 DEX 的類變成 mirror::Class

一個類不是憑空存在的。DEX 裡的 `class_def`（Ch 4）只是「靜態描述」，要能執行，得有人把它**載入、連結、初始化**成記憶體裡活的 `mirror::Class`。做這件事的是 **`ClassLinker`**（類連結器），它是 ART 裡管「類從 DEX 到可執行」全流程的核心元件。

一個類的生命週期（對應 JVM 的 loading → linking → initializing）：

```
 DEX 裡的 class_def (靜態)
        │
        │  ① Load: ClassLinker 讀 class_def, 建一個 mirror::Class 骨架
        │         (從對應的 ClassLoader 找 DEX, 見 Ch 35)
        ▼
   mirror::Class (status = Loaded)
        │
        │  ② Link: 解析父類/介面, 建 vtable, 佈局欄位,
        │         為每個方法建 ArtMethod、每個欄位建 ArtField
        ▼
   mirror::Class (status = Resolved/Verified)
        │
        │  ③ Initialize: 跑 <clinit> (static 初始化區塊)
        ▼
   mirror::Class (status = Initialized)  ← 現在可以 new 實例、呼叫方法了
```

三個逆向重點：

1. **ClassLinker 維護一張「已載入類」的表**。每個 ClassLoader 有自己的 class table（Ch 35 詳談雙親委派）。`Java.use("com.foo.Bar")`（Frida）底層就是叫 ClassLinker 在正確的 ClassLoader 裡找/載入 `Bar` 這個 `mirror::Class`。找不到就是你熟悉的 `ClassNotFoundException`。
2. **類的 status 是分階段的**。一個類可能載入了但還沒初始化。有些脫殼/hook 要在特定 status 動手（例如趁還沒 verify 前改東西）。
3. **每個方法的 `ArtMethod` 是在 Link 階段建的**。ClassLinker 為類的每個 direct/virtual method 分配一個 `ArtMethod`，這些 `ArtMethod` 連續存在一塊（`mirror::Class` 的 `methods_` 指向這塊陣列）。這個「連續陣列」的佈局在 Ch 36 枚舉方法時很重要。

## ArtMethod：一個方法的全部

`ArtMethod` 是這章的主角，也是整個 Part 6 的核心。它是一個 C++ 結構，**代表一個 Java 方法**。你 hook 一個方法、dump 一個方法、主動調用一個方法，操作的都是它。

以 Android 13 的 `art/runtime/art_method.h` 為參照，`ArtMethod` 的關鍵欄位（**只列語意，不列精確 offset——offset 隨版本變，見下方說明**）：

```
 ArtMethod {
   declaring_class_        ← 這方法屬於哪個 mirror::Class (GC root)
   access_flags_           ← public/private/static/native/abstract... 的旗標
   dex_method_index_       ← 在 DEX 裡的 method_id 索引 (Ch 4)
   dex_code_item_offset_   ← DEX 裡 code_item 的 offset (方法體 bytecode 在哪)
                             ★ 抽取型加固把這個/對應內容抽空, 脫殼要填回
   method_index_           ← 在 vtable / method array 裡的位置
   hotness_count_          ← 被呼叫次數 (JIT 判斷熱不熱用)

   ── PtrSizedFields (指標大小欄位, 通常在結構尾端) ──
   data_ (or dex_cache_resolved_methods / imt)  ← 版本而異的輔助資料
   entry_point_from_quick_compiled_code_
                             ★★★ 執行這方法時跳去哪 (機器碼 or 直譯器 bridge)
                             ★★★ ART hook 就是改這個欄位
 }
```

### 三個「明星欄位」你必須記住

整個 `ArtMethod` 幾十個 bit 的資訊，逆向者天天碰的是這三個：

1. **`access_flags_`**：一堆旗標。除了 Java 語意的 public/static，還有 ART 內部旗標。關鍵的是 **`kAccNative`（是不是 native 方法）** 和 **`kAccCompileDontBother`/`kAccFastInterpreterToInterpreterInvoke`** 這類影響「怎麼執行」的旗標。**hook 的一種做法就是設 native 旗標**，讓 ART 把這方法當 native 處理、跳去你指定的 native 函式（後面詳談）。

2. **`dex_code_item_offset_`**（在較新版本可能整合進其他欄位，版本敏感）：指向這方法在 DEX 裡的 `code_item`——也就是它的 Dalvik bytecode 在哪。**抽取型加固的核心手法就是把這個 offset 對應的 code_item 抽空（或指向假的）**，讓靜態工具抽不到方法體；執行時殼再把真的填回來。脫殼（Ch 36）就是趁填回後從這裡把 code_item dump 出來。

3. **`entry_point_from_quick_compiled_code_`**（下稱 quick entrypoint）：**這是整章最重要的欄位**。它是一個函式指標，決定「呼叫這個方法時，CPU 該跳去執行哪段機器碼」。它可能指向：
   - 這方法的 **AOT/JIT 機器碼**（如果編過）
   - 一個叫 **`art_quick_to_interpreter_bridge`** 的橋接函式（如果沒編、要走直譯器）
   - **你 hook 時塞的自己的機器碼**

**hook 的本質，就是把這個指標改指向你的東西。** 記住這句話，Ch 36 整章都是它的展開。

### 為什麼不給精確 offset

> **這是本章最重要的誠實聲明**：`ArtMethod` 的欄位 **offset 與結構大小逐版本變動**。舉例：Android 8 到 Android 13 之間，ART 為了省記憶體多次調整 `ArtMethod` 的佈局（把某些欄位合併、移進 `PtrSizedFields`、改指標壓縮方案）。網路上任何寫死「`entry_point` 在 offset 0x28」的教學，**只對某個特定版本成立**。
>
> 正確做法有三條路：
> 1. **對照原始碼**：查你目標裝置對應 Android 版本 tag 的 `art/runtime/art_method.h`，數欄位算 offset。
> 2. **用 Frida 動態定位**：透過 ART 匯出的 symbol（如 `ArtMethod::GetEntryPointFromQuickCompiledCode`）或已知的 helper，讓 runtime 自己告訴你，而不是硬編 offset（練習 E 會示範這個思路）。
> 3. **實測掃描**：在裝置上用已知方法的已知屬性反推欄位位置。
>
> **本課所有涉及 `ArtMethod` offset 的地方，一律當成「要在目標上實測/查原始碼」，絕不硬背數字。** 這不是保守，是這個領域的鐵律——硬背的 offset 換台裝置就 crash。

## ArtField：一個欄位的全部

`ArtField` 是 `ArtMethod` 的欄位版親戚，代表一個 Java field。結構簡單得多（欄位沒有「執行」的概念，不需要 entrypoint）。關鍵欄位（**語意，非精確 offset，以 Android 13 為準**）：

```
 ArtField {
   declaring_class_   ← 屬於哪個 mirror::Class
   access_flags_      ← public/private/static/final...
   field_dex_idx_     ← DEX 裡的 field_id 索引 (Ch 4)
   offset_            ← ★ 這個欄位在物件記憶體佈局裡的偏移
 }
```

逆向者對 `ArtField` 的主要用途是 **`offset_`**：它告訴你「一個實例的這個成員變數，存在物件記憶體的哪個位置」。Frida 讀寫一個 field（`obj.someField.value`）底層就是拿 `ArtField.offset_` 去物件記憶體算位址讀寫。你手動在記憶體裡撈一個物件的私有欄位（繞過 getter）時，也是靠這個 offset。

## mirror::Class 與 mirror::Object：類與實例

這兩個是「Java 型別系統」在 ART 記憶體裡的代表。

**`mirror::Class`** 描述一個類的一切（**關鍵欄位語意，Android 13**）：

```
 mirror::Class {
   super_class_       ← 父類的 mirror::Class
   class_loader_      ← 載入這個類的 ClassLoader (Ch 35 的關鍵)
   dex_cache_         ← 這個類所屬 DEX 的解析快取
   methods_           ← ★ 指向 ArtMethod 陣列 (這類所有方法連續存)
   ifields_ / sfields_← 指向 ArtField 陣列 (instance/static 欄位)
   status_            ← Loaded/Resolved/Verified/Initialized
   object_size_       ← 這類的實例佔多大 (new 時配多少記憶體)
   vtable_ / iftable_ ← 虛擬方法表 / 介面方法表 (多型 dispatch 用)
 }
```

**`mirror::Object`** 是實例，開頭固定有一個 `klass_` 指標指回它的 `mirror::Class`：

```
 mirror::Object {
   klass_        ← 指回這是哪個類的實例 (每個 Java 物件開頭都有)
   monitor_      ← 鎖狀態 (synchronized / hashCode 用)
   ── 之後是這個類的 instance fields, 按 ArtField.offset_ 佈局 ──
 }
```

**逆向啟示**：拿到一個物件指標，讀它開頭的 `klass_` 就知道它是什麼型別（型別混淆分析、記憶體掃描找特定物件時常用）；拿到 `mirror::Class` 的 `methods_` 就能枚舉它所有 `ArtMethod`（Ch 36 脫殼枚舉方法的入口）。這條「Object → Class → methods_ → ArtMethod[]」的鏈，是主動調用脫殼的導航路線。

## 底層機制：一次方法呼叫怎麼走到機器碼

現在把上面的結構串起來，看 ART 執行一次方法呼叫的完整路徑——這是理解 hook「改 entrypoint 為什麼有效」的物理基礎。

```
 caller 要呼叫 foo.sign()
   │
   │ ① 拿到 sign 的 ArtMethod (透過 mirror::Class 的 methods_ 找)
   ▼
 ┌───────────────────────────────────────────────────────┐
 │  跳到 ArtMethod.entry_point_from_quick_compiled_code_   │
 │  (這個函式指標決定下面走哪條路)                          │
 └───────────────────────────────────────────────────────┘
        │                          │                       │
   entrypoint 指向               entrypoint 指向          entrypoint 被你
   AOT/JIT 機器碼               interpreter bridge       改成 hook stub
        │                          │                       │
        ▼                          ▼                       ▼
   直接執行編好的               art_quick_to_            執行你的機器碼
   機器碼 (快)                  interpreter_bridge       (ART hook 的原理)
                                    │
                                    ▼
                              逐條直譯 DEX bytecode
                              (從 dex_code_item_offset_
                               找到 code_item, 慢)
```

拆解這條路：

1. **entrypoint 是分岔點**。呼叫方不關心方法是編過還是要直譯——它一律「跳到 entrypoint」。是 entrypoint 這個指標本身決定後續走哪。
2. **沒編過的方法，entrypoint 指向 `art_quick_to_interpreter_bridge`**：這個橋接函式負責「從機器碼世界切進直譯器世界」，設好直譯器需要的環境，再逐條解 bytecode。這是為什麼「直譯執行」也能被「跳 entrypoint」統一——直譯只是 entrypoint 指向了那座橋。
3. **編過的方法，entrypoint 直接指向機器碼**：AOT（在 `.oat`）或 JIT（在記憶體 code cache）編出的機器碼。呼叫直接跳進去，沒有 bridge 開銷。
4. **hook 就是劫持這個分岔點**：把 entrypoint 改指向你的 stub，之後所有對這方法的呼叫都先進你的 stub。你想觀察就記 log 再轉回原 entrypoint，想改行為就直接返回你要的值。**這就是 ART inline hook / entrypoint hook 的全部祕密**（Ch 36 展開實作）。

> **未實測，理論預期行為**：`art_quick_to_interpreter_bridge`、entrypoint 分岔的行為描述，基於 AOSP `art/runtime/entrypoints/` 與 `art/runtime/interpreter/` 的公開實作。橋接函式的確切名稱、entrypoint 欄位的確切位置隨版本變。你在裝置上驗證的方式：用 Frida 讀一個「已 AOT 編譯方法」與一個「沒編過方法」的 entrypoint 值，比對前者落在 oat 的機器碼區、後者落在 interpreter bridge 的位址——這是「三種狀態對應不同 entrypoint」的實證。

## 範例一：oat 方法與 entrypoint 的對應

一個 AOT 編過的方法，它的機器碼在 `.oat` 裡，`ArtMethod.entry_point_from_quick_compiled_code_` 指向那段機器碼在**記憶體映射後**的位址。我們用 `oatdump` 觀察這個對應（**代表性輸出，未實測；欄位以 Android 12/13 oatdump 格式為準，版本可能不同**）：

```bash
adb pull /data/app/.../oat/arm64/base.odex ./base.odex
oatdump --oat-file=base.odex | grep -A8 "Lcom/example/Foo;.*sign"
```

代表性輸出：

```
  2: java.lang.String com.example.Foo.sign(java.lang.String)  (dex_method_idx=1234)
    DEX CODE:
      0x0000: 1a02 ... const-string v2, "SECRET_KEY"
      ...
    OatMethodOffsets (offset=0x00012340)
    OatQuickMethodHeader (...)
    CODE: (code_offset=0x00012360 ...)
      0x...: stp x29, x30, [sp, #-32]!    ← 這方法 AOT 編出的 arm64 機器碼
      0x...: ...
```

**讀懂它**：oatdump 把同一個方法的 **DEX bytecode** 和 **AOT 機器碼** 並排給你。`CODE:` 那段就是這方法被 dex2oat 編出的原生指令，`code_offset` 是它在 oat 裡的位置。當這個 oat 被 ART 映射進進程、這個方法被載入，它的 `ArtMethod.entry_point_from_quick_compiled_code_` 就會指向 `code_offset` 對應的執行期位址。**這就是「entrypoint 指向 AOT 機器碼」的具體長相**——逆向 AOT 產物、確認某方法有沒有被編、編成什麼，oatdump 是主力工具。

## 範例二：從 Frida 讀一個 ArtMethod 的欄位

我們用 Frida 拿到一個方法的 `ArtMethod` 指標並讀它的欄位。**這裡示範思路，不是硬編 offset**——實務要先在目標裝置確認佈局（**未實測，理論預期行為；offset 需在目標裝置實測，以下為結構思路**）：

```javascript
// 思路：Frida 的 Java.use 拿到的方法物件, 底層有辦法取到 ArtMethod 指標
// 較穩健的做法是透過 libart.so 匯出的 ArtMethod 成員函式, 而非硬編 offset
Java.perform(function () {
    var Foo = Java.use("com.example.Foo");
    var signMethod = Foo.sign.overload("java.lang.String");

    // Frida 內部把 Java method 對應到一個 ArtMethod*，可透過
    // signMethod.handle 或 ART API 取得 (版本相關, 需實測)
    console.log("[*] 拿到 sign 的 ArtMethod, 準備讀 entrypoint");

    // 用 libart 匯出的 symbol 讀 entrypoint (比硬編 offset 穩)
    var getEP = Module.findExportByName("libart.so",
        "_ZN3art9ArtMethod36GetEntryPointFromQuickCompiledCodeEv");
    if (getEP) {
        console.log("[*] 找到 GetEntryPointFromQuickCompiledCode symbol");
        // NativeFunction(getEP, 'pointer', ['pointer'])(artMethodPtr)
        // 回傳值就是這方法當前的 quick entrypoint
    } else {
        console.log("[!] symbol 找不到 (被 strip 或 mangling 不同), 需改用 offset 掃描");
    }
});
```

**這個範例的重點不是能不能跑，是三個逆向原則**：

1. **優先用 symbol，不要硬編 offset**：`libart.so` 匯出的 `GetEntryPointFromQuickCompiledCode` 等成員函式（如果沒被 strip）能讓 runtime 自己算 offset，跨版本比硬編穩。
2. **symbol 找不到要有 plan B**：release 的 `libart.so` 可能 strip 掉這些 symbol，這時退回「查對應版本原始碼算 offset + 動態驗證」。
3. **一切都要在目標裝置驗證**：這正是為什麼本章不給你數字——給了就是害你。

## 範例三（失敗/邊界）：native 方法沒有 DEX code_item

一個新手常踩的坑：想 dump 一個方法的 bytecode，結果它是 **native 方法**，根本沒有 DEX code。

```
 你想 dump com.example.Crypto.encrypt() 的 bytecode
   │
   │ 讀它的 ArtMethod.access_flags_
   ▼
 kAccNative 旗標 = 1  ← 這是 native 方法!
   │
   ├─ dex_code_item_offset_ 無效 (native 方法沒有 DEX code_item)
   │
   └─ entry_point_from_quick_compiled_code_ 指向的是
      JNI stub / art_quick_generic_jni_trampoline
      → 真正的邏輯在 .so 裡的 native 函式 (Ch 19/JNI)
```

**這對脫殼很關鍵**：抽取型加固有時把方法「抽成 native」——把 Java 方法標成 native、真邏輯搬到 `.so` 或執行期動態註冊。你想 dump 它的 DEX bytecode 會撲空，因為它現在**沒有** DEX bytecode。看到 `kAccNative` 就要意識到「這方法的邏輯不在 DEX 層」，該切到：檢查它是靜態註冊（`Java_com_example_Crypto_encrypt`）還是動態註冊（`RegisterNatives`），走 Part 4 的 native 逆向。**判斷方法是不是 native，就是讀 `access_flags_` 的 `kAccNative` bit**——這是分流「該在 DEX 層還是 native 層下手」的第一個判斷。

## 對比與取捨：三個結構的角色分工

| 結構 | 代表 | 逆向者最常碰的欄位 | 用在哪 |
|---|---|---|---|
| **`mirror::Class`** | 一個 Java 類 | `methods_`（方法陣列）、`class_loader_`、`status_` | 枚舉方法（脫殼入口）、判斷類的載入狀態、找 ClassLoader（Ch 35） |
| **`ArtMethod`** | 一個 Java 方法 | `entry_point_...`（hook 核心）、`access_flags_`（native 判斷）、`dex_code_item_offset_`（dump 目標） | **hook、主動調用、脫殼**——Part 6 的主戰場 |
| **`ArtField`** | 一個 Java 欄位 | `offset_`（物件內偏移） | 直接讀寫私有欄位、繞 getter |
| **`mirror::Object`** | 一個 Java 實例 | `klass_`（型別）、之後的 instance fields | 記憶體掃描找物件、確認型別、撈欄位值 |

一句話取捨：**`ArtMethod` 是 Part 6 的絕對核心，其他三個是它的上下文**——`mirror::Class` 帶你找到 `ArtMethod`（透過 `methods_`），`mirror::Object` 是你調用方法時的 `this`，`ArtField` 是你順手撈資料的旁支。所有 hook/脫殼技術都收斂到「操作某個 `ArtMethod` 的欄位」。

## 踩雷集錦

1. **錯誤直覺：「照網路教學硬編 entrypoint offset = 0xXX」→ 正確認識**：`ArtMethod` 欄位 offset **逐版本變**。硬編的 offset 換台裝置/換個 Android 版本就讀到垃圾、直接 crash。永遠用 symbol 或查對應版本原始碼算 offset，並在目標實測。
2. **錯誤直覺：「hook 就是改 DEX bytecode」→ 正確認識**：ART 層 hook 改的是 `ArtMethod.entry_point_...`（劫持執行跳轉），不是改 bytecode。已 AOT/JIT 編成機器碼的方法，改 bytecode 根本不影響它執行（它不再讀 bytecode）。
3. **錯誤直覺：「每個方法都有 DEX bytecode 可 dump」→ 正確認識**：native 方法（`access_flags_` 的 `kAccNative`）沒有 DEX code_item，`dex_code_item_offset_` 無效。想 dump 它撲空，因為邏輯在 `.so`。先讀 `kAccNative` 判斷分流。
4. **錯誤直覺：「`mirror::Object` 開頭就是資料」→ 正確認識**：物件開頭是 `klass_`（指回型別）+ `monitor_`，之後才是 instance fields。你直接讀物件開頭當某欄位會讀到型別指標。要用 `ArtField.offset_` 定位真正的欄位。
5. **錯誤直覺：「類載入了就能直接呼叫方法」→ 正確認識**：類有 status（Loaded/Resolved/Verified/Initialized）。沒 Initialize（跑 `<clinit>`）的類，static 欄位還沒初始化。主動調用/hook 時機牽涉 status，Ch 36 會碰到「趁類還沒某狀態時動手」。

## 進階：再往深一層

- **entry_point_from_jni 與 entry_point_from_interpreter**：除了 quick entrypoint，`ArtMethod` 在某些版本還有給 JNI 用的 entrypoint、給直譯器用的 entrypoint（不同版本合併程度不同）。native 方法的呼叫走的是 JNI entrypoint（→ `art_quick_generic_jni_trampoline` 或註冊的 native 函式）。搞清楚一個方法有幾個 entrypoint、各自何時用，是理解「hook native 方法」與「hook Java 方法」為什麼手法不同的關鍵。
- **imt（interface method table）與 vtable**：多型呼叫（`invoke-virtual`/`invoke-interface`）不是直接找 `ArtMethod`，而是透過 `mirror::Class` 的 vtable/iftable 用 index 查。這是為什麼 hook 一個介面方法、或 hook 一個會被覆寫的虛方法時，要考慮「你 hook 的是宣告類的 `ArtMethod` 還是實作類的」——它們是 vtable 裡不同的 slot。
- **CompactDex 與 `ArtMethod` 的 code 定位**：ART 內部用 cdex 時，`ArtMethod` 找 code_item 的路徑跟標準 DEX 不同（經過 vdex 的 dex layout）。脫殼時「從 `ArtMethod` 定位到 bytecode 再 dump」在 cdex 下要多繞一層。
- **指標壓縮與 32/64 位**：`ArtMethod` 裡「指標大小的欄位」在 32-bit 與 64-bit 進程佈局不同，某些版本還有指標壓縮。你 x86_64 AVD 上的佈局跟 arm64 真機不同——這又是一個「offset 不可硬編」的理由。
- **`Runtime::Current()` 與全域入口**：ART 有個全域單例 `Runtime`，透過它能拿到 `ClassLinker`、`Heap`、`Thread` 等。逆向 ART 內部、寫進階脫殼工具時，`Runtime::Current()`（`libart.so` 匯出）是拿到這些子系統的總入口。

## 動手練習

1. 查你目標裝置對應 Android 版本的 `art/runtime/art_method.h`（cs.android.com 切到對應 tag），把 `ArtMethod` 的欄位按順序抄下來，理解為什麼「同一份 code 換版本 offset 就變」。對照 Android 8 和 13 兩個 tag，親眼看它變了什麼。
2. 用 `oatdump --oat-file=base.odex` dump 一個 App，找一個方法，看它的 DEX CODE 與 AOT CODE 並排。找一個 AOT 編過的方法和一個沒編的（DEX code 有但沒 CODE 段），對照差異。
3. 在 AVD 上用 Frida，對一個已知方法，嘗試透過 `libart.so` 的匯出 symbol（`GetEntryPointFromQuickCompiledCode`）讀它的 entrypoint 值。比對「你剛 `cmd package compile -m speed` 全編過」與「還沒編」時 entrypoint 落在哪個記憶體區——實證三種狀態對應不同 entrypoint。
4. 找一個含 native 方法的 App（大多有 `System.loadLibrary`），用 Frida 讀某方法的 `access_flags_`，確認 native 方法的 `kAccNative` bit——親手驗證「怎麼判斷一個方法是不是 native」。

## 本章重點整理

- **ART = 一堆 C++ 結構代表 Java 世界**：Java 類→`mirror::Class`、Java 方法→`ArtMethod`、Java 欄位→`ArtField`、Java 物件→`mirror::Object`。逆向就是讀寫這些結構的欄位。
- **ClassLinker 把 DEX 的 class_def 載入/連結/初始化成 `mirror::Class`**，並為每個方法建 `ArtMethod`（連續存在 `methods_` 陣列）。`Java.use` 底層就是叫它找類。
- **`ArtMethod` 三個明星欄位**：`entry_point_from_quick_compiled_code_`（執行跳去哪，**hook 改這個**）、`access_flags_`（含 `kAccNative`，判斷是不是 native）、`dex_code_item_offset_`（bytecode 在哪，**dump/脫殼的目標**）。
- **一次方法呼叫 = 跳到 entrypoint**，它指向 AOT/JIT 機器碼、或 `interpreter_bridge`（走直譯）、或你的 hook stub。hook 的本質就是劫持這個分岔點。
- **`ArtMethod` 的 offset 逐版本變，絕不硬編**：用 symbol / 查對應版本原始碼 / 動態驗證，一切在目標裝置實測。

## 自我檢核

- [ ] 不看筆記，能畫出 Java 類/方法/欄位/物件對應的四個 C++ 結構，並說出各自代表什麼
- [ ] 能講出 ClassLinker 把一個類從 DEX 變成可執行 `mirror::Class` 的三個階段
- [ ] 能說出 `ArtMethod` 的三個明星欄位各是什麼、各自在 hook/dump/native 判斷裡扮什麼角色
- [ ] 能畫出一次方法呼叫經過 entrypoint 走到機器碼/直譯器/hook stub 的分岔路徑
- [ ] 能解釋為什麼「hook = 改 entrypoint」而不是改 bytecode，以及對已編譯方法改 bytecode 為什麼無效
- [ ] 能講清楚為什麼本章不給精確 offset、正確拿 offset 的三條路是什麼

## 延伸閱讀

### 原始碼（最終仲裁，最重要）

- **[art/runtime/art_method.h](https://cs.android.com/android/platform/superproject/+/master:art/runtime/art_method.h)** — Android Code Search
  - **讀哪裡**：`ArtMethod` class 定義、`access_flags_`/`entry_point_...` 欄位、`GetEntryPointFromQuickCompiledCode` 等成員函式
  - **為什麼值得讀**：`ArtMethod` 沒有比這更權威的文件。**務必切到你目標裝置對應的版本 tag**——這頁就是你算 offset 的依據
  - **注意**：對照兩個版本 tag 看欄位怎麼變，親身理解「offset 不可硬編」
- **[art/runtime/mirror/ (class.h 與 art_field.h)](https://cs.android.com/android/platform/superproject/+/master:art/runtime/mirror/)** — Android Code Search
  - **讀哪裡**：`mirror::Class` 的 `methods_`/`class_loader_`/`status_`；`ArtField` 的 `offset_`
  - **和本章的關聯**：本章 `mirror::Class`/`ArtField` 欄位描述的出處，枚舉方法、讀欄位的依據

### 逆向實戰

- **[看雪 ART 內部 / ArtMethod 系列](https://bbs.kanxue.com/)**（站內搜「ArtMethod entrypoint hook」）
  - **這篇說什麼**：中文社群對 `ArtMethod` 結構與 entrypoint hook 的實測拆解，含不同版本 offset 對照
  - **讀哪裡**：找專講 `ArtMethod` 佈局與版本差異、YAHFA/SandHook 原理的帖
  - **前提知識**：讀過本章的結構語意，這些帖給你「不同版本實際 offset 長怎樣」的實測數據
- **[Frida 官方文件 — Java API](https://frida.re/docs/javascript-api/#java)** — frida.re
  - **這篇說什麼**：Frida 怎麼透過 ART 內部找類/方法（本章結構的上層封裝）
  - **讀哪裡**：`Java.use`/`Java.choose`/method overload 那幾節
  - **和本章的關聯**：本章講的 ClassLinker→`mirror::Class`→`ArtMethod` 鏈，就是 `Java.use` 底層在做的事

### 系統設計

- **[ART 內部設計與 runtime 概覽](https://source.android.com/docs/core/runtime)** — AOSP Runtime 概覽
  - **讀哪裡**：runtime 架構、entrypoint/trampoline 相關描述
  - **和本章的關聯**：把本章的結構放回「整個 ART 怎麼組織」的全局圖

下一章我們往上一層看「類是從哪個 ClassLoader 載進來的」——`PathClassLoader`/`DexClassLoader`/雙親委派，以及熱修復/插件化怎麼利用動態載入 DEX。你會看到本章的 `mirror::Class.class_loader_` 欄位，具體連到哪個 ClassLoader 物件。

→ [Ch 35 ClassLoader 機制與熱補](./35-classloader-hotpatch.md)
