# Ch 7 — Jadx 與 Java 反編譯：原理與限制

> **目標**：搞懂 jadx 到底在做什麼——DEX（bytecode）怎麼被「反編譯」回類 Java 語法，為什麼這個過程是**近似、有損**的（相對於 smali 的一對一無損），jadx-gui 與 CLI 各自何時用，反編譯失敗／出錯時怎麼救，以及 CFR/procyon 這些其他反編譯器什麼時候值得換上場。讀完你會把 jadx 的輸出當成「高品質的線索」而不是「原始碼真相」。

> **環境**：本章以 **jadx 1.4.7**（gui 與 CLI）、AVD 撈出的 APK 為分析對象。反編譯是純檔案解析、不需 Android runtime，但本 repo 沙箱沒有 JVM/jadx，所以 jadx 的實際輸出畫面標「**未實測，理論預期行為**」並附你自己驗證的步驟；bytecode→高階語法「為什麼有損」的概念部分是編譯原理的確定事實，直接論述。

## 為什麼需要這個？

Ch 6 你學會改 smali 重打包，但 smali 難讀——一個十行的 Java 方法反組譯成 smali 可能三四十行，塞滿暫存器編號與型別描述符。要**快速讀懂一個 App 在幹嘛**，你需要的是接近原始碼的東西，這就是 jadx 的價值：它把 DEX 往上推回 Java，讓你像看原始碼一樣讀邏輯、搜字串、追呼叫鏈。

但這裡藏著全安卓逆向最根本的認知陷阱（Ch 1 已埋下伏筆）：**jadx 的輸出不是原始碼，是猜出來的近似**。如果你把它當成「可以編譯回去的真相」，你會在三個地方栽跟頭：改它重編譯（編不過）、相信它顯示的每個細節（有些是反編譯器補的）、以及反編譯出錯時不知道那是 App 的問題還是工具的問題。這一章就是要把「近似到什麼程度、哪裡會騙你」講透。

## 先建立直覺：反組譯 vs 反編譯，差在哪

兩個詞常被混用，但對逆向是天差地別的兩件事：

```
   DEX bytecode
   （暫存器式指令，如 const/4 v0,0x1 / invoke-virtual ...）
        │
        ├──── 反組譯 (disassemble) ────▶ smali
        │       一條 bytecode → 一條 smali          【一對一，無損，可逆】
        │       apktool / baksmali 做這件事
        │       改完組回去 = 原本的 DEX
        │
        └──── 反編譯 (decompile) ──────▶ 類 Java 原始碼
                多條 bytecode → 一個高階結構        【多對一，有損，是「還原」】
                jadx / CFR / procyon 做這件事
                改完編譯回去 ≠ 原本的 DEX（常編不過）
```

核心差別在**資訊量**：

- **反組譯**只是把二進位指令換成文字表示，資訊不增不減——所以無損、可逆。
- **反編譯**要把「一堆低階指令」重建成「高階控制結構」（`if`/`for`/`try`/lambda/類別關係）。但編譯過程**丟掉了**區域變數名、原始的迴圈形狀、某些型別資訊——反編譯器只能**猜**一個合理的高階結構去對應這堆指令。猜得好（jadx 對正常 Java 很準），但本質是重建，不是還原。

一句話記住：**smali 是「翻譯」，jadx 是「重寫」**。翻譯逐字對應，重寫是理解後用自己的話講一遍——大意對，細節不保證一樣。

## 底層機制：bytecode 怎麼被推回 Java

jadx 的反編譯管線大致是這幾階段：

```
 DEX bytecode
     │  ① 解析：讀出每個 method 的指令序列、型別、常數池
     ▼
 中間表示 (IR) + 控制流圖 (CFG)
     │  ② 建 CFG：把指令切成 basic block，畫出跳轉邊
     ▼
 控制流結構化 (structuring)
     │  ③ 把 CFG 的跳轉還原成 if/for/while/switch/try
     │     ← 這步是「猜」的重災區
     ▼
 資料流分析
     │  ④ 暫存器 → 區域變數；型別推導；合併臨時值
     ▼
 類 Java 原始碼輸出
```

每一步都可能引入「與原始碼不同」的結果，其中 **③ 控制流結構化**最關鍵。bytecode 裡沒有 `for` 或 `while` 這種概念——只有條件跳轉（`if-eqz`、`goto`）。反編譯器看到一堆跳轉，要**逆推**它們原本是什麼迴圈。

舉例：原始碼的 `for (int i=0;i<n;i++)` 和 `int i=0; while(i<n){...; i++;}` **編出來的 bytecode 可能完全一樣**。反編譯器只能挑一個顯示——它顯示 `while`，不代表原始碼就是 `while`。這不是 bug，是資訊在編譯時就丟了、無從還原。

再看**型別擦除**：Dalvik 的暫存器沒有靜態型別，一個暫存器這行裝 int、下一段裝 object 引用都可以。jadx 要靠資料流分析推回每個變數的型別。多數情況推得對，但泛型（`List<String>` 的 `<String>`）在 bytecode 層已被擦除成 `List`，jadx 只能顯示 `List`——這是 Ch 8 會深入的擦除問題。

### 為什麼「反編譯出的 Java 常編不過」

即使 jadx 反編譯得很漂亮，那份 Java 也常常**無法重新編譯**，原因：

- **合成成員洩漏**：編譯器生成的 `access$000`、`this$0`、`$VALUES` 這類合成方法／欄位，jadx 會顯示出來，但它們是 `javac` 自動生成的、你不能手寫（會撞名）。
- **不合法但合理的重建**：jadx 為了讓你讀懂，有時產出「語意對但語法非法」的程式碼（例如引用了一個它推不出完整簽名的方法）。它甚至會用 `// jadx: inconsistent code` 之類的註解標出它自己也沒把握的地方。
- **控制流結構化失敗的殘留**：碰到複雜或混淆過的控制流，jadx 結構化不出乾淨的 `if/for`，會退化成 `while(true)` + `switch` + `break label` 的形狀——語意對，但不是人會寫的、也常編不過。

所以再強調一次 Ch 1 的鐵律：**要改邏輯重打包，改 smali（apktool）；jadx 的 Java 只拿來讀**。

### 一個具體例子：同一段 bytecode，jadx 只能「猜一個」

看這段 smali（一對一無損，這是**事實**）：

```smali
    const/4 v0, 0x0          # v0 = 0
    :loop
    if-ge v0, p1, :end       # if (v0 >= p1) goto end
    invoke-static {v0}, Lcom/x/A;->work(I)V
    add-int/lit8 v0, v0, 0x1  # v0 = v0 + 1
    goto :loop
    :end
    return-void
```

這段跳轉，jadx 可能反編譯成 `for`：

```java
for (int i = 0; i < n; i++) { A.work(i); }
```

但**原始碼可能根本是 `while`**：

```java
int i = 0;
while (i < n) { A.work(i); i++; }
```

兩種原始碼編出來的 bytecode **完全相同**——`for` 和 `while` 的差別在編譯時就被抹平了。jadx 顯示 `for` 不代表開發者寫 `for`。這不是 bug，是資訊真的沒了。你要記住：**jadx 給你的迴圈形狀、變數名（`i`）、甚至某些括號結構，都是它從無數個「等價原始碼」裡挑的一個，不是唯一真相**。關鍵處要驗證，就切 smali 看那個無歧義的底層。

## 範例 1：jadx-gui vs CLI，各自何時用

jadx 有兩個前端，同一個反編譯引擎：

**未實測，理論預期行為**：

```bash
# CLI：反編譯整個 APK 到目錄（適合批量、grep、進版本控制）
jadx target.apk -d out_java
#   INFO  - loading ...
#   INFO  - processing ...
#   → out_java/sources/  (Java 檔) + out_java/resources/

# CLI 只反編譯，遇錯繼續（別因單一 class 失敗就整包停）
jadx --show-bad-code target.apk -d out_java
```

```bash
# GUI：互動探索（適合追呼叫鏈、看交叉引用、即時搜尋）
jadx-gui target.apk
```

兩者的分工很清楚：

| 場景 | 用哪個 | 為什麼 |
|---|---|---|
| 全域搜字串／URL／方法名 | GUI（`Ctrl+Shift+F` 全文搜） | 即時、可跳轉，比 grep 產出的檔案好追 |
| 追「誰呼叫了這個方法」 | GUI（右鍵 Find Usage / 交叉引用） | GUI 建了引用圖，一鍵跳到呼叫點 |
| 把反編譯結果進 git / 做 diff | CLI（`-d` 出檔案） | 檔案化才好版本控制、patch-diff |
| 批量處理一堆 APK | CLI（可腳本化） | GUI 沒法自動化 |
| 邊看邊改 smali 對照 | GUI（內建可切 smali 檢視） | 同一個方法可切「Java / smali」對照看 |

> **GUI 的殺手功能是「跳轉+交叉引用」**：逆向的核心動作是「這個可疑字串 → 誰用了它 → 那個方法誰呼叫 → 一路追到入口」。GUI 的 Find Usage（Ctrl+點方法名）讓這條追蹤鏈變成幾次點擊。CLI 的檔案樹要靠 grep 硬追，慢很多。日常探索用 GUI，要檔案化才用 CLI。

## 範例 2：反編譯失敗／出錯時怎麼辦

jadx 對正常 App 很穩，但碰到混淆、加固、或它的結構化演算法 hold 不住的方法時，會產出殘缺或錯誤的程式碼。徵狀與對策：

**徵狀 A：某個方法變成一坨 `// JADX WARNING: ...` 或空殼**

jadx 在它沒把握的地方會插註解，例如：

```java
// JADX WARNING: Removed duplicated region for block: B:12:0x004a
// JADX INFO: Failed to restore original type of variable, use raw type
public Object a(int i) {
    // ... 結構化失敗的殘留
}
```

對策：
- 加 `--show-bad-code`：預設 jadx 會**隱藏**它反編譯失敗的方法（顯示成空），這個旗標強制它把「爛程式碼」也顯示出來——爛歸爛，往往還是看得出邏輯輪廓。
- **回去看 smali**：GUI 切到該方法的 smali 檢視。smali 是一對一無損的，jadx 的 Java 再爛，smali 一定是對的。反編譯失敗時，smali 是你的真相來源。

**徵狀 B：整個 class 反編譯不出來**

通常是混淆（控制流平坦化、字串加密）或 jadx 版本太舊不認新 bytecode。對策：
- 升級 jadx（DEX `039` 版新增的 opcode，舊 jadx 可能不認）。
- 換反編譯器（見下節），不同引擎對混淆的抵抗力不同。

**徵狀 C：jadx 直接 OOM / 卡死**

大 App（幾百 MB DEX）會吃爆記憶體。對策：`jadx -j 4`（限執行緒）、給 JVM 更多堆（`JAVA_OPTS="-Xmx8g" jadx ...`），或用 `--no-imports`／只反編譯特定 class 縮小範圍。

> **反編譯失敗不是死路**：jadx 產不出乾淨 Java，不代表你逆不動。你有兩條退路——smali（永遠可讀）與動態 hook（Ch 13 用 Frida 直接看執行期）。jadx 只是「讓你讀得快」的加速器，不是唯一入口。把它當加速器，它掛了你還有腿走路。

## 範例 3：jadx vs CFR vs procyon——換引擎

jadx 是 Android 專用（直接吃 DEX/APK），但它不是唯一的 Java 反編譯器。有時換一個引擎能反編譯出 jadx 產不出的部分：

| 反編譯器 | 吃什麼 | 特點 | 何時換上場 |
|---|---|---|---|
| **jadx** | DEX/APK 直接 | Android 原生、快、GUI 好用、內建 smali 對照 | 預設首選 |
| **CFR** | `.class`（需先 dex2jar 轉） | 對複雜控制流、新 Java 特性還原強 | jadx 對某方法結構化爛掉時 |
| **procyon** | `.class` | 老牌，某些邊界情況與 CFR 互補 | CFR 也不行時再試 |

換引擎的流程（因為 CFR/procyon 吃 `.class` 不吃 DEX，要先轉）：

**未實測，理論預期行為**：

```bash
# DEX → jar（.class 的集合）
d2j-dex2jar.sh target.apk -o target.jar    # dex2jar 工具

# 用 CFR 反編譯某個 class
java -jar cfr.jar target.jar --outputdir out_cfr
```

> **多引擎互為印證**：碰到關鍵方法 jadx 反編譯得可疑時，用 CFR 反編譯同一個方法對照——兩個獨立引擎給出一致的結構，你才敢相信。不一致時，退回 smali 用一對一的事實裁決。這跟 Ch 1 說的「靜動互相印證」是同一個精神：**不信任何單一工具的輸出，交叉驗證**。

## 對比與取捨

| 維度 | smali（apktool/baksmali） | Java（jadx 反編譯） |
|---|---|---|
| 對應關係 | 一對一、無損 | 多對一、有損（重建） |
| 可讀性 | 低（要熟 bytecode） | 高（接近原始碼） |
| 可否改回重打包 | ✅ 改完組回 = 原 DEX | ❌ 常編不過、對不回 |
| 碰混淆 | 一定產得出（bytecode 就在那） | 可能結構化失敗、產殘缺碼 |
| 適合 | 改邏輯、當真相來源 | 快速讀懂、追呼叫鏈、搜字串 |
| 泛型/lambda | 看得到底層真相（合成方法都在） | 被還原成漂亮但可能失真的語法 |

實務結論：**用 jadx 讀懂、用 smali 動手、用動態確認**。三者分工，不是三選一。

## 踩雷集錦

1. **把 jadx 的 Java 當可編譯原始碼**：想改 jadx 輸出重編譯回去——編不過（合成成員、結構化殘留），就算編過也對不回原 DEX。要改邏輯改 smali。這是全課最根本的錯，Ch 1/5/6 一再強調。
2. **相信 jadx 顯示的每個細節**：泛型參數（`<String>`）、迴圈形狀（`for` vs `while`）、變數名（`i`/`str`）——這些可能是 jadx 補的或猜的，不是原始碼真相。關鍵處回 smali 核對。
3. **方法變空殼就以為逆不了**：jadx 預設隱藏反編譯失敗的方法。加 `--show-bad-code` 看爛碼，或直接看 smali。空殼是 jadx 的能力邊界，不是 App 沒程式碼。
4. **jadx 版本太舊不認新 opcode**：高版本 DEX（`038`/`039`）的新指令舊 jadx 反編譯不出。反編譯結果大量缺失時，先升級 jadx 再懷疑 App。
5. **大 App 直接開 GUI 卡死**：幾百 MB 的 DEX 反編譯很吃記憶體，GUI 全載會 OOM。先 CLI 反編譯特定 package 或調大 `-Xmx`。
6. **只用 jadx 一個引擎**：關鍵方法 jadx 產出可疑就放棄。換 CFR/procyon 或退回 smali，多引擎+smali 交叉驗證才可靠。

## 進階：再往深一層

- **jadx 的 deobfuscation 選項**：`--deobf` 會把混淆的短名（`a`/`b`/`c`）自動重命名成 `p000a.C0001a` 之類穩定的名字，讓你在整個專案裡追蹤同一個被混淆的類。它不能還原原名，但能給每個混淆符號一個**穩定、唯一**的替代名，追呼叫鏈時不會被一堆重複的 `a` 搞混。
- **jadx 的 smali 對照檢視**：GUI 裡每個 class 可切「Java / smali / bytecode」三種檢視。這是驗證「jadx 有沒有騙我」的最快方法——Java 看起來怪，一鍵切 smali 看底層真相。善用它，你就不會被反編譯的近似性坑到。
- **反編譯器的結構化演算法**：控制流結構化是有學術根基的問題（Hex-Rays 的 goto-less 演算法、No More Gotos 論文）。混淆器（Ch 26/27）正是攻擊這個環節——控制流平坦化就是把乾淨的 CFG 打成一個巨大 switch，讓結構化演算法還原不出可讀結構。懂反編譯器怎麼運作，才懂混淆為什麼有效、去混淆要打哪。
- **jadx 也反編譯資源**：jadx 不只出 Java，也把 `resources.arsc`、Manifest 解回可讀（`resources/` 目錄）。所以快速偵察一個 App，jadx 一個工具就能同時看 Java 邏輯 + Manifest + 資源字串，比分別跑 apktool 方便。Ch 9 深入資源逆向時會再對照。

## 動手練習

1. 拿一個 App，同一個方法在 jadx-gui 裡切「Java 檢視」和「smali 檢視」對照看。找一個有迴圈的方法，看 jadx 顯示 `for` 還是 `while`，然後看 smali 的跳轉——體會「jadx 選了一個顯示、但 bytecode 沒這個資訊」。
2. 開 `--show-bad-code`，找一個 jadx 標了 `JADX WARNING` 的方法，讀它的爛碼，再對照 smali——確認「爛碼雖亂但邏輯輪廓還在，smali 才是真相」。
3. 對一個關鍵 class，用 jadx 和（dex2jar + CFR）各反編譯一次，diff 兩份輸出，找出兩個引擎不一致的地方——那些地方就是「反編譯是猜的」最直接的證據。
4. 用 GUI 的 Find Usage：搜一個字串常數（如某 URL），追「誰用了它 → 那個方法誰呼叫」，練習用交叉引用追呼叫鏈——這是逆向最高頻的動作。

## 本章重點整理

- **反組譯（smali）= 翻譯，一對一無損可逆；反編譯（jadx）= 重寫，多對一有損是猜的**。這是全課最根本的區分。
- jadx 的近似性來自**控制流結構化**（`for`/`while` 分不出）與**型別/泛型擦除**；輸出常編不過（合成成員、結構化殘留）。
- **GUI**用於互動探索+交叉引用追呼叫鏈；**CLI**用於批量、檔案化、進 git。
- 反編譯失敗有退路：`--show-bad-code` 看爛碼、回 smali 看真相、換 CFR/procyon 引擎、升級 jadx；**永遠交叉驗證，不信單一工具**。

## 自我檢核

- [ ] 能用自己的話講清楚「反組譯」與「反編譯」的差別，以及為什麼一個無損一個有損
- [ ] 能舉出至少兩個「jadx 顯示的細節其實是猜的、不是原始碼真相」的例子
- [ ] 知道 jadx-gui 和 CLI 各自何時用，特別是 GUI 的交叉引用為什麼重要
- [ ] jadx 某方法反編譯成空殼時，你有至少兩條退路（並知道 smali 為什麼一定對）
- [ ] 知道什麼情況該換 CFR/procyon，以及為什麼要先 dex2jar

## 延伸閱讀

### 工具與原始碼

- **[jadx GitHub repo（README + Wiki）](https://github.com/skylot/jadx)** — skylot
  - **讀哪裡**：README 的 CLI 旗標表（`--show-bad-code`、`--deobf`、`-j`）、Wiki 的使用技巧
  - **和本章的關聯**：本章的失敗排解旗標都在這；issue 區也常有「某 App 反編譯不出」的真實案例與解法
- **[CFR 反編譯器官網](https://www.benf.org/other/cfr/)** — Lee Benfield
  - **讀哪裡**：首頁的能力說明與命令列用法
  - **注意**：CFR 吃 `.class`，Android 場景要先 dex2jar；它對複雜控制流的還原是 jadx 的好補充

### 原理

- **[《No More Gotos》— 控制流結構化論文](https://www.internetsociety.org/sites/default/files/11_4_2.pdf)** — Yakdan et al., NDSS 2015
  - **這篇說什麼**：反編譯器怎麼把無結構的 CFG 還原成 `if/while` 而不留 `goto`
  - **讀哪裡**：問題定義與演算法概述那幾節（數學細節可略）
  - **為什麼值得讀**：懂了結構化的難處，你就懂 jadx 為什麼有時失敗、控制流混淆為什麼有效——這是 Ch 26/27 去混淆的理論地基

### 方法論

- **[OWASP MASTG — Decompiling Java Code](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0018/)** — OWASP
  - **這篇說什麼**：標準化的 Java 反編譯流程與工具選擇
  - **讀哪裡**：反編譯工具對比與注意事項那段
  - **前提知識**：讀過本章「反編譯是近似」的概念，這頁給你對應的標準操作

下一章我們把 jadx 的輸出讀得更深。反編譯出的 Java 有一堆「看起來奇怪」的東西——`$1`/`$2` 匿名類、`access$000` 合成方法、Kotlin 協程變成的狀態機、`when` 的怪異形狀。這些不是反編譯 bug，是編譯器生成程式碼的真實樣貌。讀懂它們，你才不會被自己的工具騙。

→ [Ch 8 讀懂反編譯輸出：匿名類、lambda、協程陷阱](./08-reading-decompiled-output.md)
