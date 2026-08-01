# Ch 22 — IDA / Ghidra 逆 .so

> **目標**：把前三章的知識在真正的反編譯器裡串起來。你要能：把 `.so` 載入 IDA 或 Ghidra、用 F5（反編譯）把 ARM64 組語變成近似 C、識別 JNI 函式、**匯入 JNI 結構型別**讓 `(*env)->` 顯示成 `NewStringUTF` 這種可讀名字、恢復結構體、重命名、用交叉引用（xref）追資料流，最後用 **IDAPython / Ghidra script 自動化找 `RegisterNatives`**——把 Ch 19 說的那張「Java 方法 → native 位址」對照表一鍵撈出來。

> **環境**：本 repo 沙箱**沒有 IDA/Ghidra**，本章的工具操作與反編譯結果全部標「**未實測，理論預期行為**」，並在每處給你在自己環境的驗證步驟。IDAPython/Ghidra script 的邏輯是依官方 API 寫的、可在你機器上跑；反編譯出的具體 C 因目標而異，是代表性示意。**Ghidra 免費**（NSA 開源），沒有 IDA 授權的讀者全程可用 Ghidra 跟著做。

## 為什麼需要這個？

前三章給了你零件：Ch 19 的 JNI 綁定、Ch 20 的 ARM64、Ch 21 的 ELF。但真實逆向不是對著 `readelf` 輸出用腦補——你需要一個能**同時看組語與 C、能點函式跳來跳去、能追誰呼叫誰、能把匿名函式改成有意義名字**的環境。IDA 與 Ghidra 就是這個環境，它們是 native 逆向的主力工作台。

而這章最實用的產出是**自動化找 `RegisterNatives`**。Ch 19 說了它是對照表的唯一來源，但一個大 `.so` 可能有上萬個函式，手動翻 `JNI_OnLoad` 找那次 `RegisterNatives` 呼叫、再手動讀出 `methods[]` 陣列，慢且易錯。寫一段 script 讓工具自動掃出「哪個 Java 方法綁到哪個 native 位址」，是把 native 逆向從「大海撈針」變成「直達目標」的關鍵一步。

## 先建立直覺：反編譯器的工作台長什麼樣

IDA 與 Ghidra 介面不同，但核心的幾個視窗一致，先建立心智模型：

```
 ┌───────────────┬────────────────────────┬──────────────────┐
 │ 函式清單       │  Disassembly (組語)     │  Decompiler (C)  │
 │ (Functions)   │  Ch 20 讀的 ARM64       │  F5 的近似 C     │
 │  sub_1000     │   stp x29,x30,...        │  int sign(...) { │
 │  JNI_OnLoad ◀─┤   ldr w8,[x0]            │    ...           │
 │  Java_..._foo │   bl  GetStringUTF...    │  }               │
 │  ...(上萬個)  │   ...                    │                  │
 ├───────────────┴────────────────────────┴──────────────────┤
 │ Strings (.rodata 字串) │ Xrefs (誰呼叫這/這呼叫誰) │ Imports│
 └────────────────────────────────────────────────────────────┘
```

四個你會一直用的視窗：

- **Functions**：所有函式清單。`Java_...` 與 `JNI_OnLoad`（Ch 19）在這裡認出來。
- **Disassembly + Decompiler**：左邊組語（Ch 20 的技能派上用場）、右邊 F5 出來的 C，**兩邊對照著看**——F5 快讀、組語驗真相。
- **Strings**：`.rodata`（Ch 21）裡的字串。雙擊一個可疑字串（URL/key）→ 看 xref → 直達用到它的函式。
- **Xrefs（交叉引用）**：逆向的核心武器。「誰呼叫了這個函式」「這個字串被哪裡用到」——順著 xref 你能從一個線索追到整條邏輯鏈。

搞懂這四個視窗怎麼互相跳，你就會用反編譯器了。剩下的是熟練度。

## Step 1：載入 `.so`

**IDA**：`File → Open`，選 `libfoo.so`。IDA 認出是 ELF/AArch64（Ch 21 的 header 欄位它自動讀），按預設載入即可。載入後它會自動分析（auto-analysis），跑完才有完整的函式清單與 xref。

**Ghidra**：建一個 Project → `File → Import File` 選 `.so` → 雙擊開啟 → 它問要不要 auto-analyze，選 Yes、用預設 analyzer。

> **未實測，理論預期行為**：載入後你應該在 Functions 視窗看到一串函式，含 `JNI_OnLoad`（若有匯出）。**驗證步驟**：在你自己機器載入任一真實 `.so`，看 Functions 清單非空、能在 Imports 看到 `libc` 的 `malloc`/`memcpy` 等（代表重定位/符號解析成功）。若 Functions 幾乎是空的、只有一大塊 undefined，多半是 Ch 21 說的「section header 被殼刪了」，需要先修復（SoFixer）或手動指定 program header 讓工具重建。

## Step 2：F5 反編譯與識別 JNI 函式

在一個函式上按 **F5**（IDA）或看右側 **Decompiler**（Ghidra），組語變成近似 C。以 Ch 19 的 `native String sign(String)` 為例，靜態命名的函式 `Java_com_example_Native_sign` 反編譯後**理論預期**長這樣：

```c
// 未實測，理論預期行為（代表性反編譯示意）
jstring __fastcall Java_com_example_Native_sign(JNIEnv *env, jobject thiz, jstring input)
{
    const char *s;
    char buf[64];
    s = (*env)->GetStringUTFChars(env, input, 0);   // 取出 Java 字串內容
    // ... 對 s 做運算，結果放 buf ...
    (*env)->ReleaseStringUTFChars(env, input, s);
    return (*env)->NewStringUTF(env, buf);           // 組回傳字串
}
```

把 Ch 19/20 的知識套上去驗證這是對的：**第一參 `env`、第二參 `thiz`、第三參才是真參數 `input`**（x0/x1/x2）；`GetStringUTFChars` 把 `jstring` 轉 C 字串（Ch 19 的路標）；`NewStringUTF` 組回傳。這就是「Java 呼叫 native」在反編譯器裡的樣子。

**識別 JNI 函式的三招**：

1. **看名字**：Functions 清單搜 `Java_`（靜態命名）或 `JNI_OnLoad`——最快。
2. **看簽名特徵**：任何 native 方法第一個參數都是 `JNIEnv*`、第二個是 `jobject`/`jclass`。反編譯出來第一參是個結構指標、且函式內有大量 `(*a1)->...` 呼叫，八成是 JNI 函式。
3. **搜 `RegisterNatives` 反查**（下面 Step 5）：`Java_` 搜不到時的主力。

## Step 3：匯入 JNI 型別——讓 `env->` 顯示函式名

**這是逆 JNI `.so` 最關鍵、也最多人不知道的一步。** 預設反編譯器不認得 `JNIEnv` 結構，會把 `env->NewStringUTF(...)` 顯示成難讀的偏移形式：

```c
// 沒匯入 JNI 型別時（難讀）：
result = (*(a1 + 0x29C))(a1, buf);   // 0x29C 是什麼？不知道
```

`0x29C` 其實是 `JNIEnv` 函式表裡 `NewStringUTF` 的偏移。**匯入 `jni.h` 的型別**後，反編譯器就能把它翻譯成人話：

```c
// 匯入 JNI 型別後（可讀）：
result = (*env)->NewStringUTF(env, buf);
```

做法：

- **IDA**：`File → Load file → Parse C header file`，餵入 NDK 的 `jni.h`（或社群整理好的 JNI 型別檔）。然後把該函式第一個參數的型別設成 `JNIEnv *`（在變數上右鍵 → Set type → `JNIEnv *`）。IDA 就會把 `(*(a1+偏移))` 全部翻成 `(*env)->函式名`。
- **Ghidra**：`File → Parse C Source`，加入 `jni.h`（Ghidra 的 `GDT`/Data Type Manager），再把參數型別設成 `JNIEnv *`。或直接用社群的 [Ghidra JNI 型別封存](https://github.com/Ayrx/JNIAnalyzer) 一鍵匯入。

> **未實測，理論預期行為**：匯入前後同一個函式的可讀性差異巨大。**驗證步驟**：在你機器對一個 JNI 函式，先看未匯入時的 `(*(a1+0x??))` 形式，記下幾個偏移；匯入 `jni.h` 並設 `JNIEnv*` 型別後，同一行應變成具名的 `->GetStringUTFChars`/`->NewStringUTF`。對照 Ch 19 的 JNI 函式表確認偏移對得上（不同 Android 版本 `jni.h` 偏移一致，因為 ABI 穩定）。

## Step 4：重命名、恢復結構體、交叉引用

拿到可讀反編譯後，逆向的日常是**逐步標註、讓程式碼越來越有意義**：

- **重命名（IDA `N`、Ghidra `L`）**：把 `sub_1234` 改成 `real_sign`、把 `v3` 改成 `key_len`。每標一個名字，後面所有引用它的地方都更新——這是「把逆向理解固化下來」的核心動作。名字取得好，一個複雜函式會逐漸自己講出它在幹嘛。
- **恢復結構體**：看到 `*(a1 + 8)`、`*(a1 + 0x10)` 這種對同一指標的固定偏移存取，代表 `a1` 是個結構指標。用 IDA 的 Structures（`Local Types`）或 Ghidra 的 Data Type Manager 定義一個 struct，把偏移對應成具名欄位（`->key`、`->len`），反編譯就從 `*(a1+8)` 變成 `a1->key`。
- **交叉引用（xref，IDA `X`）**：在一個函式/字串上按 `X`，列出「誰用到它」。這是追邏輯鏈的主力：
  - 從 **Strings** 找到可疑字串 → `X` 看誰引用 → 直達關鍵函式。
  - 在關鍵函式上 `X` → 看誰呼叫它 → 往上追到 Java 進來的入口。

> **實戰節奏**：Strings 找地標 → xref 跳到函式 → F5 讀 → 重命名標註 → 再 xref 往外擴。這個「地標→xref→標註」循環是 native 逆向的主旋律。Ch 23 認演算法時，`.rodata` 裡的常數（如 MD5/SHA 初始值）就是最好的 Strings 地標。

## Step 5（重頭戲）：自動化找 RegisterNatives

Ch 19 說了：防護 App 幾乎都用 `RegisterNatives` 動態註冊、函式被 strip，`grep Java_` 搜不到。這時你要在 `JNI_OnLoad` 裡找那次 `RegisterNatives` 呼叫、讀出 `methods[]` 陣列（`{名字, 簽名, 函式指標}` 三元組）。手動翻很累，**寫 script 自動化**。

### 底層機制：RegisterNatives 呼叫在組語裡長什麼樣

```
JNI_OnLoad 內：
   adrp/add x1, methods    ; x1 = methods[] 陣列位址（Ch 20 的 adrp+add pattern）
   mov      w3, #N         ; w3 = 方法數量
   ...
   bl       RegisterNatives ; 呼叫（透過 env 表，實際是間接呼叫）

methods[] 在 .data/.rodata：
   ┌─────────────┬─────────────┬──────────────┐
   │ ptr → "sign"│ ptr → 簽名  │ ptr → 函式   │  ← 每組 3 個指標（24 byte）
   ├─────────────┼─────────────┼──────────────┤
   │ ptr → "enc" │ ptr → 簽名  │ ptr → 函式   │
   └─────────────┴─────────────┴──────────────┘
```

自動化的思路：**找到 `methods[]` 陣列的位址，把它當成連續的 `{char*, char*, void*}` 三元組讀出來**，每組印出「方法名 → 函式位址」。定位陣列位址可以靠 `JNI_OnLoad` 裡 `RegisterNatives` 呼叫前載入 x1 的那個 `adrp+add`，或直接掃 `.data` 找符合三元組模式（前兩個指標指向可讀字串、第三個指向 `.text`）的區塊。

### IDAPython script（骨架）

```python
# 未實測，理論預期行為（依 IDAPython API 寫，可在你機器跑）
# 思路：從一個已知的 methods[] 陣列位址開始，逐 3 個指標解析
import idc, idaapi

def parse_jni_methods(ea, count):
    PTR = 8  # AArch64 指標 8 byte
    for i in range(count):
        base   = ea + i * 3 * PTR
        name_p = idc.get_qword(base)              # char* name
        sig_p  = idc.get_qword(base + PTR)        # char* signature
        fn     = idc.get_qword(base + 2 * PTR)    # void* fnPtr
        name = idc.get_strlit_contents(name_p)
        sig  = idc.get_strlit_contents(sig_p)
        print("%-20s %-40s -> 0x%X" % (
            name.decode() if name else "?",
            sig.decode()  if sig  else "?", fn))
        idc.set_name(fn, "jni_" + (name.decode() if name else "sub"), idc.SN_CHECK)

# 用法：ea 換成你在 JNI_OnLoad 裡找到的 methods[] 位址，count 換成方法數
parse_jni_methods(0x0000ABCD, 3)
```

### Ghidra script（等效邏輯）

```java
// 未實測，理論預期行為（Ghidra Python/Java script 皆可，這裡示意邏輯）
// 從 methods[] 位址逐 3 指標讀：getLong 取指標、再 getDataAt 取字串、setName 命名 fnPtr
// 完整可用版建議直接用社群工具 JNIAnalyzer（下方延伸閱讀），它自動掃 RegisterNatives
```

> **驗證步驟**：在你機器上，先在 `JNI_OnLoad` 用 F5 找到 `RegisterNatives` 呼叫、點進它前面載入的陣列位址（IDA 會標成 `methods` 或某 offset），把那個 `ea` 與方法數餵進上面的 script。跑完應印出類似：
> ```
> sign    (Ljava/lang/String;)Ljava/lang/String;   -> 0x1A2B0
> encrypt (Ljava/lang/String;I)Ljava/lang/String;   -> 0x1B340
> ```
> 並把 `0x1A2B0` 這種匿名函式自動重命名成 `jni_sign`。**這一步就是 Ch 19 那張對照表的自動化產出**——原本上萬個匿名函式裡，關鍵的那幾個現在有名字了。

### 更省事：現成工具

不想自己寫 script，社群有現成的：**JNIAnalyzer**（IDA/Ghidra 外掛）自動掃 `RegisterNatives` 並批量命名、**jni_helper**（Ghidra）自動匯入 JNI 型別 + 找註冊。實務上先跑現成工具、卡住再手寫 script 補。

> **靜態卡住就回動態**：如果 `.so` 混淆到 script 掃不出 `methods[]`（陣列被加密、執行期才組出來），別硬幹靜態——回到 Ch 19 的 Frida hook `RegisterNatives`，執行期它一定得把明文的三元組傳給 ART，你在那時攔就拿到了。靜態自動化與動態 hook 互為備援。

## 對比與取捨：IDA vs Ghidra

| 面向 | IDA (Pro) | Ghidra |
|---|---|---|
| 價格 | 商業授權（貴） | **免費**（NSA 開源） |
| 反編譯品質 | 業界最強（尤其 ARM64） | 很好，複雜函式偶爾略遜 |
| 腳本 | IDAPython（生態成熟） | Python/Java script + headless |
| JNI 型別匯入 | Parse C header | Parse C Source / GDT |
| 動態除錯整合 | 內建 remote debugger（Ch 24） | 需搭外部（gdb/Frida） |
| 學習資源 | 海量教學 | 官方文件 + 成長中社群 |

實務結論：**有 IDA 授權就用 IDA（ARM64 反編譯最強）；沒有就用 Ghidra，全課能跟完、能力不打折。** 兩者概念完全相通（載入→F5→匯型別→重命名→xref→script），學會一個換另一個只是快捷鍵重學。本課示範以「概念 + 兩者對應操作」為主，不綁死工具。

## 踩雷集錦

1. **沒匯入 JNI 型別就硬讀 `(*(a1+0x29C))`**：可讀性差十倍還容易看錯。逆任何 JNI `.so` 第一步就是匯 `jni.h` + 設 `JNIEnv*` 型別，讓 `env->` 顯示函式名。
2. **`grep Java_` 搜不到就以為沒 native 邏輯**：Ch 19 的老坑在工具裡重演。搜不到就去 `JNI_OnLoad` 找 `RegisterNatives`、跑 script 或現成外掛，別放棄。
3. **F5 出來一坨爛泥就相信它是對的**：混淆/OLLVM 碼 F5 常反編譯錯。Ch 20 教你讀組語正是為此——關鍵行回 Disassembly 對照，別讓 F5 帶你進溝裡。
4. **auto-analysis 沒跑完就開始逆**：載入大 `.so` 後 IDA/Ghidra 要跑一陣子分析（建函式、建 xref）。沒跑完 xref 不全、函式邊界可能錯。等左下角進度跑完再動手。
5. **section header 被刪就卡死**：Ch 21 的殼手法。工具載入後只有一塊 undefined，是 section 資訊沒了。先用 SoFixer 修或手動指定 segment，別對著半殘的載入結果硬逆。
6. **重命名/標註不存檔就關掉**：IDA 的 `.idb`/Ghidra 的 project 保存你所有標註。逆到一半沒存，重開等於白做。養成隨手存的習慣。

## 進階：再往深一層

- **headless 批量分析**：IDA 的 `idat -B` 與 Ghidra 的 `analyzeHeadless` 能不開 GUI、對一批 `.so` 跑同一段 script（例如「對每個 `.so` 自動找 RegisterNatives 並匯出對照表」）。Ch 40 的自動化會用到——大規模分析靠這個。
- **FLIRT / 函式簽名庫**：IDA 的 FLIRT 能認出靜態連結進來的 libc/OpenSSL 函式並自動命名。逆到一個 `.so` 塞了整包 OpenSSL 時，FLIRT 幫你把 `sub_xxxx` 標回 `SHA256_Update`，省下大量認演算法的功夫（接 Ch 23）。
- **型別傳播與結構自動恢復**：把一個變數設對型別後，IDA/Ghidra 會沿著資料流傳播（一處設 `JNIEnv*`，相關呼叫全變可讀）。善用這個「設一處、通一片」的特性，比逐行標效率高。
- **與 Frida 對接**：靜態在 IDA 標好函式位址，動態就用那個位址 Frida hook 印參數（Ch 14）。IDA 顯示的是**檔案 offset**，Frida 要的是**執行期位址 = 模組基址 + offset**——換算靠 `Module.findBaseAddress("libfoo.so").add(0x1A2B0)`。這個 offset↔執行期位址的換算是靜動結合的橋。
- **反反編譯（anti-decompilation）**：有些殼故意構造讓 F5 崩潰或產生誤導 C 的碼（假的控制流、濫用例外處理）。認得這類 pattern（Ch 27 OLLVM 去混淆），知道何時該放棄 F5 回去讀組語。

## 動手練習

1. 用 Ghidra（免費）載入一個真實 App 的 `.so`，跑完 auto-analysis，在 Functions 找 `JNI_OnLoad`，F5 讀它——確認你能認出裡面的 `RegisterNatives` 呼叫。
2. 挑一個 JNI 函式，先看未匯入 JNI 型別時的 `(*(a1+0x??))` 形式；匯入 `jni.h`、把第一參設成 `JNIEnv*`，看同一行變成具名的 `->GetStringUTFChars`。親眼感受這一步的威力。
3. 從 `JNI_OnLoad` 找到 `methods[]` 陣列位址，把本章的 script（IDAPython 或 Ghidra 等效）套上去，印出「方法名→函式位址」對照，並確認 IDA/Ghidra 把那幾個匿名函式自動重命名了。
4. 用 Strings 視窗找一個可疑字串（URL/key），按 xref（`X`）跳到用它的函式，重命名 `sub_xxxx` 成有意義的名字——走一遍「地標→xref→標註」循環。
5. 把靜態找到的一個函式 offset，換算成執行期位址（`基址 + offset`），用 Ch 14 的 Frida `Interceptor.attach` hook 它印參數——完成一次靜動結合。

## 本章重點整理

- 反編譯器工作台的核心四視窗：**Functions（找 JNI 入口）、Disassembly+Decompiler（組語與 F5 對照）、Strings（.rodata 地標）、Xrefs（追邏輯鏈）**。
- 逆 JNI `.so` 的關鍵一步是**匯入 `jni.h` 型別 + 把參數設成 `JNIEnv*`**，讓 `(*(a1+0x29C))` 變成可讀的 `env->NewStringUTF`。
- native 函式第一參 `env`、第二參 `thiz`/`jclass`、真參數從第三個起——反編譯結果用 Ch 19/20 的知識驗證。
- **自動化找 `RegisterNatives`**：把 `methods[]` 當連續 `{char*, char*, void*}` 三元組讀，用 IDAPython/Ghidra script 或現成外掛（JNIAnalyzer）一鍵撈出「Java 方法→native 位址」對照並批量命名。
- 靜態卡住（陣列被加密）就回 Ch 19 的 Frida hook `RegisterNatives`——靜態自動化與動態 hook 互為備援。
- **有 IDA 用 IDA，沒有用 Ghidra（免費）**，概念完全相通。

## 自我檢核

- [ ] 能說出反編譯器四個核心視窗各做什麼，以及「地標→xref→標註」的逆向循環
- [ ] 能解釋為什麼要匯入 `jni.h` 型別，不匯入會看到什麼、匯入後變成什麼
- [ ] 拿到一個 `grep Java_` 搜不到的 `.so`，知道下一步去哪找 JNI 綁定
- [ ] 能講清楚 `methods[]` 陣列的結構（幾個指標一組、各是什麼），以及 script 怎麼把它讀成對照表
- [ ] 知道 IDA 顯示的 offset 要怎麼換算成 Frida 用的執行期位址

## 延伸閱讀

- **[Ghidra 官方文件與教學](https://ghidra-sre.org/)** / **[Ghidra GitHub](https://github.com/NationalSecurityAgency/ghidra)**
  - **讀哪裡**：Getting Started、Decompiler、Data Type Manager（匯 `jni.h` 用）、Script Manager
  - **和本章的關聯**：免費工具的權威文件，沒有 IDA 的讀者全程靠它；本章每個操作 Ghidra 都有對應
- **[IDA / IDAPython 官方文件](https://hex-rays.com/products/ida/support/idapython_docs/)**
  - **讀哪裡**：`idc`/`idaapi` 的 `get_qword`/`get_strlit_contents`/`set_name`——本章 script 用到的 API
  - **為什麼值得讀**：寫自動化 script（找 RegisterNatives、批量命名）的 API 參考
- **[JNIAnalyzer（自動找 RegisterNatives 的外掛）](https://github.com/Ayrx/JNIAnalyzer)**
  - **這篇說什麼**：IDA/Ghidra 外掛，自動掃 `RegisterNatives`、匯入 JNI 型別、批量命名
  - **讀哪裡**：README 的使用說明與它掃描 `methods[]` 的邏輯
  - **前提知識**：讀過本章 Step 5，才懂它自動化的是哪個手動流程
- **[Maddie Stone — Reverse Engineering Android Native Libraries（研究者演講/文章）](https://github.com/maddiestone)**
  - **這篇說什麼**：Google 研究者示範逆 Android native 庫的完整流程（JNI、混淆、實戰）
  - **讀哪裡**：JNI 函式識別與 native 逆向方法論那部分
  - **為什麼值得讀**：頂級研究者的實戰視角，把本章的零散技巧串成真實案例的工作流

下一章我們用剛練好的反編譯技能去做一件很值錢的事：**認出 native 裡在跑什麼演算法**。看到一堆 `eor`/位移/常數表，怎麼認出那是 AES、MD5、還是自製的 XOR cipher？認出演算法後，怎麼把金鑰撈出來、把加密還原成能自己重算的 PoC？Ch 23 教你從 `.rodata` 的魔數常數與指令 pattern 反推演算法身分。

→ [Ch 23 native 演算法識別與加密還原](./23-native-algorithm-id.md)
