# Ch 26 — 混淆技術全譜

> **目標**：把「混淆（obfuscation）」這個常被含糊帶過的詞攤成一張全譜——名稱混淆、字串加密、控制流混淆、反射（reflection）調用、資源混淆各自在**哪一層動手、遮蔽了什麼資訊、逆向時卡在哪、怎麼還原**。這章是 Part 5「對抗」的地基：先分清「混淆」（讓程式碼難讀）跟後面幾章的「加固」（把程式碼藏起來）是兩回事，你才不會拿脫殼的招去對付一個只是被 R8 改過名字的 App。

> **環境**：本章的概念在 AVD 上都可實測（apktool 拆 smali、jadx 讀反編譯輸出、Frida hook `String` 建構）。字串解密的邏輯示範用 **Python 3.12** 在本機實跑，標「**實際輸出**」；牽涉真實商用混淆器（DexGuard 等）的具體特徵，基於公開資料敘述，措辭降級為「一般而言」。

## 為什麼需要這個？

你打開 jadx，看到滿螢幕的 `a.a(b.b, c.c(d))`，字串全是亂碼，關鍵函式呼叫繞了三層反射——這時你要能一眼分辨：這是**混淆**還是**加固**？

- 如果是**混淆**：真程式碼就在你眼前，只是被改名、被打亂、被加密。DEX 是完整的，你能靜態全覽，還原是「把可讀性撈回來」的活。
- 如果是**加固**（Ch 28）：你看到的 DEX 根本是個殼，真程式碼在執行期才被解密釋放到記憶體。靜態怎麼看都是空的，得先脫殼（Ch 29）。

分不清這兩者，你會拿錯工具、走錯路。這章先把混淆這一側講透——它是門檻最低、幾乎每個正式 App 都有的第一道防線。搞懂它，你也才懂為什麼開發者要用它、它的極限在哪（提示：混淆從不改變程式的**行為**，這是它一切弱點的根源）。

## 先建立直覺：混淆是「保行為、毀可讀」的變換

先在腦中立一個模型。編譯器把原始碼變成 bytecode 時，做的是**語意保持（semantics-preserving）**的變換——輸入什麼、輸出什麼不變。混淆器也是一種編譯器 pass，它做的同樣是語意保持的變換，只是目標相反：不是為了跑得快，是為了**讓還原出的程式碼盡量難懂**。

```
   原始碼                混淆器的變換               混淆後
 ┌──────────────┐      （語意不變）           ┌──────────────┐
 │ checkLicense │ ── 名稱混淆 ──▶ a.b         │ 行為 100% 相同 │
 │ "SECRET_KEY" │ ── 字串加密 ──▶ decrypt(…)  │ 但人幾乎讀不懂  │
 │ if/for/while │ ── 控制流平坦化 ─▶ switch    │               │
 │ obj.foo()    │ ── 反射 ──▶ invoke("foo")   │               │
 └──────────────┘                             └──────────────┘
         │                                            │
         └──────────── 執行期行為完全一致 ─────────────┘
```

這張圖藏著混淆的**阿基里斯腱**：既然行為不變，那**執行期一定會露餡**。名字混淆了，但那個方法被呼叫時參數與返回值是真的；字串加密了，但用到它的那一刻記憶體裡一定有明文；反射藏了呼叫目標，但 `Method.invoke` 真的執行時 target 是具體的。

所以整個 Part 3（Frida）跟這章是一對：**混淆對付靜態，動態對付混淆**。你在 jadx 卡住的地方，往往 hook 一下就水落石出。這是本章要反覆回到的主軸。

## 混淆的五大類：逐層拆解

### 一、名稱混淆（identifier renaming）——ProGuard / R8

最基礎、最普遍的一招。把有意義的類名、方法名、欄位名 `com.bank.LicenseChecker.verify()` 改成 `a.b.a()`。這是 Android 官方工具鏈自帶的：**R8**（Android Gradle Plugin 3.4+ 的預設，取代舊的 ProGuard）在 release build 時預設就會做 `minifyEnabled true` 的縮減與改名。

它動的是 DEX 裡的 **string_id / type_id / method_id** 表——把符號字串換成 `a`、`b`、`c`。底層機制：

```
DEX string pool（混淆前）          DEX string pool（混淆後）
 ┌────────────────────┐            ┌──────────┐
 │ "LicenseChecker"   │            │ "a"      │
 │ "verifySignature"  │  ── R8 ──▶ │ "b"      │
 │ "com.bank.crypto"  │            │ "a.a"    │   ← 名字重用（同 scope 不衝突就重名）
 └────────────────────┘            └──────────┘
```

名稱混淆有個關鍵性質：**它是有損但可逆記錄的**。R8 改名時會產生一份 **mapping 檔**（`mapping.txt`），記錄「原名 → 混淆名」。開發者留著它是為了把線上 crash 的混淆 stack trace 還原回可讀——而這份檔案一旦到你手上（洩漏、或你就是 App 作者），逆向難度歸零。這是本章「對抗方法」一節的第一招。

`mapping.txt` 長這樣：

```
com.bank.LicenseChecker -> a.b:
    boolean verifySignature(byte[]) -> a
    java.lang.String secretKey -> b
```

有它，jadx 的 **Deobfuscation → Load mapping** 或 `jadx --deobf` 配合可以把名字換回來。沒有它，你只能靠**行為**認出函式（這個 `a.b()` 收 byte[] 回 boolean、被登入流程呼叫 → 大概是驗簽）。

> **名稱混淆的天花板很低**：它不改控制流、不加密資料，只是換名字。邏輯結構、字串（若沒另外加密）、API 呼叫全都在。一個有經驗的人讀被 R8 改過名的程式碼，慢但不難。這也是為什麼「進階混淆」（DexGuard、字串加密、控制流）才是真正的門檻。

### 二、字串加密（string encryption）

名字可以靠上下文猜，但**字串是硬證據**——URL、API 端點、金鑰、錯誤訊息、`Log.d` 的 tag，全是逆向的路標。所以進階混淆器（DexGuard、Allatori 等，R8 本身不做字串加密）會把字串常量加密，執行期才解密。

原本 DEX 裡 `const-string v0, "https://api.bank.com/pay"` 這樣的明文，被改成：

```
const-string v0, "\x8f\x2a\x71..."     ← 密文常量
invoke-static {v0}, La/c/d;->a(String)String   ← 解密函式，執行期還原
move-result-object v0
```

底層就是「密文常量 + 一個解密 stub」。解密演算法通常很輕（XOR、簡單 RC4、查表），因為它要在每次用到字串時跑、不能太慢。我們用 Python 演示最常見的 XOR 型（**實際輸出**）：

```python
def dec(ct, key):                      # 逆向者要還原的就是這個函式
    return bytes(c ^ key[i % len(key)] for i, c in enumerate(ct)).decode()

key = b"\x37"                           # 單 byte XOR key（最常見）
ct  = bytes(b ^ 0x37 for b in b"https://api.bank.com/pay")
print("密文 hex :", ct.hex())
print("解密後  :", dec(ct, key))
```

```
密文 hex : 5f4d4d5c584c1e161e56564d5f5058541e51565b184b56542a
解密後  : https://api.bank.com/pay
```

**逆向字串加密的三條路**：

1. **靜態還原解密器**：解密邏輯本身就在 DEX（那個 `a.c.d.a()`），你逆出它，寫個腳本把所有密文常量批次解回來。
2. **動態 hook 解密函式**：更省事。用 Frida hook 那個解密方法，印出「輸入密文 → 輸出明文」，App 一跑就把所有字串吐給你。
3. **hook `String` 的源頭**：連解密函式都不用找，直接 hook `java.lang.String` 的建構或 `StringBuilder.toString`，凡是執行期生成的字串全攔下。

第 2、3 條正是「動態對付混淆」的體現。範例（Frida，Ch 13 詳講語法）：

```javascript
// hook 那個字串解密函式，把明文全印出來
Java.perform(function () {
    var Dec = Java.use("a.c.d");
    Dec.a.overload('java.lang.String').implementation = function (ct) {
        var pt = this.a(ct);
        console.log("[str] " + pt);        // App 一跑，密文字串全現形
        return pt;
    };
});
```

> **邊界情況**：字串加密最怕「解密函式被搬進 native」。如果解密在 `.so` 裡（Ch 23/27 的題材），Java 層 hook 抓不到，得 hook JNI 邊界或直接逆 native。這是字串加密 × native 混淆的組合拳，商用加固常這樣搭。

### 三、控制流混淆（control-flow obfuscation）

前兩招動的是「名字」和「資料」，這招動的是**程式的骨架**——把清晰的 `if/for/while` 攪成一團讓人（和反編譯器）看不出原本結構。在 Java/DEX 層常見的是**控制流平坦化（control-flow flattening）**與**插入不透明謂詞（opaque predicate）的虛假分支**。

平坦化的核心：把循序執行的一串基本區塊（basic block），改寫成一個 `while(true) { switch(state) {...} }` 的狀態機，靠一個 `state` 變數決定下一塊跳哪——原本一眼可見的執行順序，變成要人腦模擬狀態轉移才能還原。

```
原始控制流（線性/樹狀，好讀）      平坦化後（狀態機，難讀）
   A                              state = 0
   │                              while (true) switch (state):
   B ── if ──▶ C                    case 0: A; state = 1; break
   │           │                    case 1: B; state = cond ? 2 : 3; break
   D ◀─────────┘                    case 2: C; state = 3; break
                                    case 3: D; state = -1; break  // 出口
```

平坦化在 **native（OLLVM）** 上遠比在 DEX 上兇——這是下一章（Ch 27）的主戲，那裡會深挖 dispatcher 識別與 angr 去平坦化。在 DEX 層，商用混淆器也做，但因為 Dalvik bytecode 的限制與 ART 驗證器的存在，通常沒 native 那麼極端。

**不透明謂詞**是搭配招：插入一個「結果恆定但編譯器算不出來」的條件，例如 `if ((x*x + x) % 2 == 0)`（任意整數 `x*x+x` 必為偶數，恆真），把死程式碼掛在恆假分支上，撐大程式碼、誤導反編譯器。逆向時你要能認出「這個條件其實是常數」，把假分支剪掉。

### 四、反射調用（reflection）

直接呼叫 `LicenseChecker.verify()` 在 DEX 裡會留下 `invoke-virtual` 加一個明確的 `method_id`——靜態分析（與 jadx 的交叉引用）一抓一個準。反射把這條線切斷：改用字串在**執行期**才決定要呼叫誰。

```java
// 直接呼叫：靜態可見 target
checker.verify(data);

// 反射：target 藏在字串裡，靜態看不出呼叫了 verify
Class<?> c = Class.forName("a.b");              // 類名是字串
Method m = c.getDeclaredMethod("a", byte[].class); // 方法名是字串
m.invoke(inst, data);
```

反射對逆向的殺傷力在於**破壞呼叫圖（call graph）**：你在 jadx 對 `verify` 按「Find Usages」會找不到呼叫點，因為呼叫是動態組出來的。而且反射常跟字串加密疊用——`"a.b"` 和 `"a"` 本身還是加密的，你得先解密才知道反射的是誰。

**還原反射的動態招**：hook 反射的匯聚點。所有反射呼叫最後都走 `java.lang.reflect.Method.invoke`，hook 它就能攔下「到底是誰呼叫了什麼」：

```javascript
Java.perform(function () {
    var M = Java.use("java.lang.reflect.Method");
    M.invoke.implementation = function (obj, args) {
        console.log("[reflect] " + this.getName() + " on " + (obj ? obj.getClass().getName() : "static"));
        return this.invoke(obj, args);
    };
});
```

這是「動態贏靜態」最漂亮的例子：靜態被反射徹底擋住的呼叫圖，動態一 hook 全部現形。

### 五、資源混淆（resource obfuscation）

前四招針對程式碼，這招針對**資源**。以微信開源的 **AndResGuard** 為代表：把 `res/drawable/icon.png` 改成 `res/a/b.png`、把 `resources.arsc` 裡的資源名縮短。它的原意是**減小 APK 體積**（資源路徑字串變短），但副作用是逆向時資源與程式碼的對應變模糊。

它對逆向的影響其實有限——資源混淆不碰邏輯，你分析程式行為時多半不太依賴資源名。但它是「這個 App 用了 AndResGuard」的特徵訊號（`res/` 下出現大量單字母目錄），偵察時值一眼。

## 對抗方法總表

把五類混淆的還原手段收攏成一張決策表：

| 混淆類型 | 遮蔽了什麼 | 靜態還原 | 動態還原（多半更快） |
|---|---|---|---|
| 名稱混淆 | 符號可讀性 | 有 `mapping.txt` → 秒還原；否則靠行為認 | Frida 印呼叫棧/類名輔助定位 |
| 字串加密 | 字串常量 | 逆解密器 + 批次腳本 | hook 解密函式 / `String` 源頭，全吐 |
| 控制流混淆 | 執行結構 | 手動/工具去平坦化（Ch 27） | trace 執行路徑（Stalker）看真實流程 |
| 反射 | 呼叫圖 | 解密類/方法名字串後補回 | hook `Method.invoke` 攔真實 target |
| 資源混淆 | 資源命名 | 影響小，特徵訊號為主 | 一般不需要 |

一條貫穿全表的原則：**混淆保行為 → 執行期必露餡 → 動態幾乎總能繞**。靜態還原是「徹底但費工」，動態是「快但只看到跑過的路徑」，實務上兩者交替（Ch 1 說的螺旋前進）。

## 踩雷集錦

1. **把混淆當加固**：看到 `a.a.a()` 就喊「加固了脫不了殼」——錯。名稱混淆的 DEX 是**完整的**，根本不用脫殼，載入 mapping 或動態 hook 就能讀。先判斷是混淆還是加固（DEX 是完整邏輯還是殼載入器），再選策略。
2. **手動解密每一個字串**：字串加密的 App 可能有上千個密文常量，一個個逆是折磨。**hook 解密函式讓 App 自己吐**，一次全拿，比手工快兩個數量級。
3. **對反射按 Find Usages 找不到就放棄**：反射本來就切斷靜態呼叫圖。找不到呼叫點不代表沒被呼叫，改用 `Method.invoke` hook。
4. **以為 R8 = 安全**：R8 只做名稱混淆與縮減，**不加密字串、不平坦化控制流**。一個只開了 `minifyEnabled` 的 App，你的金鑰若是明文字串，照樣一搜就到。R8 是「基本衛生」不是「安全防護」。
5. **忽略混淆器留下的指紋**：不同混淆器有特徵（AndResGuard 的單字母 `res/` 目錄、DexGuard 的特定解密 stub 樣式、字串加密的固定 wrapper 呼叫模式）。認出用了哪家，你能查它的已知還原手法，少走彎路。

## 進階：再往深一層

- **DexGuard vs R8**：R8 是 Google 官方、免費、只做基礎混淆與縮減；**DexGuard** 是 GuardSquare 的商用產品，在 R8 之上加字串加密、控制流混淆、反射調用、資源加密、以及跟加固接壤的類加密。一般而言，商用 App 若「字串全加密 + 控制流平坦 + 大量反射」，多半用了 DexGuard 這類商用混淆器而非只有 R8——但這是特徵推斷，不是確證。
- **混淆與加固的交界**：DexGuard 的「class encryption」已經跨到加固的領地——把某些類加密、執行期才解密載入。這時「混淆」和「加固」的界線是模糊的，你可能同時需要本章的動態 hook 和 Ch 29 的脫殼。這也是為什麼這門課把混淆（26/27）放在加固（28/29）前面：先懂難讀，再懂藏起來。
- **反混淆器的軍備競賽**：學界與工具圈有一堆自動去混淆的嘗試（Simplify 這類 DEX 虛擬執行去混淆器、jadx 內建的 deobf）。它們對「規則化的混淆」有效，但商用混淆器會針對這些工具做對抗（例如故意生成讓虛擬執行超時的程式碼）。所以自動工具是輔助，理解原理 + 動態驗證才是根本。
- **Kotlin 讓混淆輸出更亂**：Kotlin 的協程、`when`、data class、inline function 編譯出的 DEX 本身就比 Java 複雜（Ch 8 談過），再疊混淆，jadx 輸出會更難讀。分辨「這團亂是 Kotlin 編譯特徵」還是「這團亂是混淆」，需要 Ch 8 的底子。

## 動手練習

1. 拿一個你自己寫的小 App，`build.gradle` 開 `minifyEnabled true` + `proguardFiles`，build 出 release APK。用 jadx 開它，觀察類名/方法名怎麼變成 `a`/`b`；再去 `build/outputs/mapping/release/mapping.txt` 找出對照，用 jadx 載入 mapping，看名字如何還原。親手走一遍「有 mapping vs 沒 mapping」的難度差。
2. 寫一個把字串 XOR 加密的小函式塞進那個 App，release build 後在 jadx 裡確認字串變密文。然後用 Frida hook 你的解密函式（或 hook `String` 建構），把明文印出來——體會「動態讓字串現形」。
3. 用本章的 Python XOR 片段，自己選一個 key 加密一段 URL，再寫解密還原它。把「字串加密只是輕量可逆變換」這件事用手驗證，破除「加密 = 安全」的錯覺。

## 本章重點整理

- 混淆是**語意保持**的變換：只毀可讀性、不改行為——這是它一切弱點的根源，**執行期必露餡**。
- 五大類：名稱混淆（R8，有 `mapping.txt` 可秒還原）、字串加密（hook 解密函式）、控制流混淆（去平坦化，Ch 27 深挖）、反射（hook `Method.invoke`）、資源混淆（特徵訊號為主）。
- **混淆 ≠ 加固**：混淆的 DEX 是完整邏輯（不用脫殼），加固的 DEX 是殼（要脫）。先分清再選工具。
- R8 只做名稱混淆與縮減，不加密字串；商用混淆器（DexGuard 等）才做字串/控制流/反射，且與加固接壤。
- 對抗總原則：靜態還原徹底但費工，動態 hook 快但只看到跑過的路徑，兩者螺旋交替。

## 自我檢核

- [ ] 拿到一個 `a.a.a()` 滿天飛的 App，能判斷這是混淆還是加固，並說出判斷依據
- [ ] 能解釋為什麼「混淆保行為」導致「動態幾乎總能繞過混淆」
- [ ] 能說出 `mapping.txt` 是什麼、為什麼它的洩漏讓名稱混淆歸零
- [ ] 面對字串加密，能講出三條還原路徑，並說明為什麼 hook 解密函式最省事
- [ ] 能解釋反射為什麼破壞靜態呼叫圖，以及 hook `Method.invoke` 為什麼能還原
- [ ] 知道 R8 做什麼、不做什麼，不會把「開了 minify」當成安全

## 延伸閱讀

- **[R8 / ProGuard 官方文件（Android Developers — Shrink, obfuscate, optimize）](https://developer.android.com/build/shrink-code)**
  - **讀哪裡**：`minifyEnabled`、`mapping.txt` 的產生與用途、`-keep` 規則那節
  - **和本章的關聯**：名稱混淆是官方工具鏈自帶的，這頁是它的權威說明；讀完你會懂開發者這端在做什麼、mapping 從哪來
- **[GuardSquare — DexGuard 技術說明與部落格](https://www.guardsquare.com/dexguard)**
  - **讀哪裡**：字串加密、class encryption、reflection 那幾類保護的介紹
  - **為什麼值得讀**：商用混淆器的能力邊界一手來源；理解「進階混淆」到底加了什麼，也順便看防禦者怎麼想（呼應 Ch 41）
- **[OWASP MASTG — Android Anti-Reversing / Obfuscation](https://mas.owasp.org/MASTG/techniques/android/)**
  - **讀哪裡**：Android 的 "Testing Resilience Against Reverse Engineering" 相關技術，混淆偵測與繞過
  - **和本章的關聯**：把本章的五大類混淆放進標準化測試流程，給你一套可重複的檢查清單
- **[AndResGuard（微信開源）GitHub](https://github.com/shwenzhang/AndResGuard)**
  - **讀哪裡**：README 的原理說明——資源路徑短化與 `resources.arsc` 改寫
  - **和本章的關聯**：資源混淆一節的具體實作；讀它你會認出「單字母 `res/` 目錄」這個指紋

下一章我們把控制流混淆推到最兇的地方——**native 層的 OLLVM**。DEX 層的平坦化還算收斂，OLLVM 的控制流平坦化、虛假控制流、指令替換三招疊起來能讓一個 `.so` 函式的反編譯輸出膨脹十倍。我們會學怎麼識別 dispatcher、用 angr 符號執行去平坦化、以及 D810 這類反混淆工具。

→ [Ch 27 OLLVM 與 native 混淆的去混淆](./27-ollvm-deobfuscation.md)
