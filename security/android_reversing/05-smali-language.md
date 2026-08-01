# Ch 5 — Smali 語法完整導覽

> **目標**：把 smali 從「一堆看不懂的 `v0`、`invoke-virtual`、`Lcom/x/Y;`」變成你能流暢閱讀、甚至動手改的語言。你要學會：暫存器 `v`/`p` 的分配規則、型別描述符（`I`、`[I`、`J`、`Lcom/x/Y;`）怎麼讀、方法簽名怎麼拼、`.method`/`.locals`/`.registers` 指示符的意義、`const`/`invoke-*`/`move-result`/`if-*`/`return` 這些核心指令、以及 try/catch 長什麼樣。全程給你 smali 片段對照它的 Java——讀完你能把兩邊在腦中互相翻譯。

## 為什麼要學 smali？

因為 **smali 是你唯一能「改」的東西**。Ch 1 反覆強調的那個根本區分：`DEX → smali` 是**一對一無損**的（改完組回去 100% 對應），`DEX → Java`（jadx）是**近似**的（給你讀懂用、常編不過、對不回原 DEX）。所以當你要繞一個 License 檢查、改一個回傳值、插一行 log——你動的是 smali，不是 jadx 的 Java。jadx 幫你「看懂」，smali 讓你「動手」。這兩個工具分工，Ch 6/7 各自深入，這章專攻 smali 這門語言本身。

smali 也是你**驗證自己有沒有真懂 DEX** 的照妖鏡。Ch 4 拆的那些 method_id、type descriptor、`const/4` 指令、暫存器——smali 就是把它們全部寫成人能讀的文字。如果 Ch 4 的暫存器式 VM、型別描述符你只是「看過」，這章對照 Java 一逼，你會立刻知道哪裡沒真懂。

## 先建立直覺：smali 是 bytecode 的「逐字稿」

先破除一個誤解：**smali 不是一種程式語言，它是 Dalvik bytecode 的文字表示**。你不會「用 smali 寫程式」（雖然技術上可以），你是拿到工具（baksmali）把 DEX 反組譯出來的 smali，讀它、改它、再組回去。

打個比方：DEX 是錄音檔（二進位、機器聽的），smali 是那段錄音的**逐字稿**——一個字一個字對應，你在逐字稿上改一個詞，錄音也就跟著改一個詞。這跟「翻譯」（jadx→Java）不同，翻譯會意譯、會漏、會加，對不回原文。

```
   classes.dex  ──baksmali──▶  Foo.smali      (一對一無損, 可改可組回)
   (bytecode)   ◀──smali────                  ← 改這個, 組回去 100% 對應
       │
       └──jadx──▶  Foo.java   (近似翻譯, 讀懂用, 常編不過)
```

所以你讀 smali 的心態不該是「這語言好囉嗦」，而是「這是機器指令的忠實記錄，每一行都有它非在不可的理由」。囉嗦正是因為它無損——Java 的一行 `a.foo(b)` 背後是好幾條 bytecode，smali 老實地一條條列給你。

## 一個檔案的骨架：類宣告與指示符

一個 `.smali` 檔對應一個類。開頭是類層級的**指示符（directive，以 `.` 開頭）**：

```smali
.class public Lcom/example/Foo;          # 這個類的完整名 (型別描述符格式)
.super Ljava/lang/Object;                # 父類
.source "Foo.java"                        # 原始檔名 (可能被混淆砍掉)

.field private mCount:I                   # 一個 int 欄位 mCount

.method public add(II)I                   # 方法: add(int,int) 回傳 int
    .registers 3                          # 這方法用 3 個暫存器
    ...bytecode...
.end method
```

- **`.class`**：類的存取修飾 + 完整型別描述符。`Lcom/example/Foo;` 這格式馬上下一節拆。
- **`.super`**：直接父類。沒寫繼承的 Java 類，這裡是 `Ljava/lang/Object;`。
- **`.field`**：一個成員變數，格式 `名字:型別描述符`（`mCount:I` 就是 `int mCount`）。
- **`.method` … `.end method`**：包住一個方法。方法名後面那串 `(II)I` 是簽名，也馬上拆。

這些指示符對應 Ch 4 的 class_def / field_id / method_id——smali 只是把那些二進位結構寫成文字。

## 型別描述符：讀懂 `I`、`[I`、`Lcom/x/Y;`

這是讀 smali 的第一道關卡，也是最機械、最好背的一關。DEX（因而 smali）用**單字元或 `L...;` 包裹**來表示每個型別。這張表背下來，smali 就通了一半（描述符對應的 Java 型別，本章用 Python 對照過，**實際輸出**）：

```
V -> void
Z -> boolean          ← 注意是 Z 不是 B
B -> byte
S -> short
C -> char
I -> int
J -> long             ← 注意 long 是 J 不是 L
F -> float
D -> double
[I -> int[]           ← 前綴 [ 表示陣列
[[I -> int[][]        ← 兩個 [ 是二維陣列
Ljava/lang/String; -> java.lang.String   ← L...; 包物件, / 取代 .
[Ljava/lang/String; -> String[]          ← 陣列 + 物件
```

三個最容易搞混的坑：

1. **`Z` 是 boolean、`J` 是 long**——不是照字母首字直覺（boolean→B、long→L 都是錯的，B 是 byte、L 是物件前綴）。這兩個新手天天記錯。
2. **物件用 `L套件/類名;`**，套件分隔是 `/` 不是 `.`，結尾一定有分號 `;`。`Ljava/lang/String;` = `java.lang.String`。
3. **陣列在前面加 `[`**，幾維就幾個 `[`。`[I` = `int[]`、`[Ljava/lang/String;` = `String[]`。

**這也是為什麼 Ch 4 的 string pool 裡有一堆 `Lcom/x/Y;` 這種怪字串**——它們就是型別描述符，被當字串存在 pool 裡，type_ids 指向它們。你現在把 Ch 4 和 Ch 5 接上了。

## 方法簽名：`(參數)回傳` 的拼法

方法名後面的簽名把**參數型別描述符串在一起、用括號包住，後面接回傳型別描述符**，中間不加逗號、不加空格：

```
 Java:  public int add(int a, int b)
 smali: .method public add(II)I
                          └┬┘└─ 回傳 I (int)
                           └ 兩個參數: I I (int, int)

 Java:  public String sign(String s, long ts)
 smali: .method public sign(Ljava/lang/String;J)Ljava/lang/String;
                          └───── 參數: String, long ─────┘└ 回傳 String
```

讀簽名的技巧：**括號內從左到右逐個「吃」描述符**——遇到單字元吃一個型別、遇到 `L` 一路吃到 `;`、遇到 `[` 連著後面的一起算陣列。`(Ljava/lang/String;J)` 就是「吃一個 `L...;`（String）、再吃一個 `J`（long）」= 兩個參數。

> **重載方法靠簽名區分**：Java 允許同名不同參數（`add(int,int)` 和 `add(long,long)`）。在 smali/DEX 裡它們是**兩個不同的 method**，靠 `(II)I` vs `(JJ)J` 這個簽名分開——這正是 Ch 4 說的 method_id 靠 proto_idx 區分。**Frida hook 重載方法要寫 `.overload("int","int")`，原因就在這**：光給方法名不夠，得給簽名才能定位到唯一那個 method。

## 暫存器：`v` 與 `p`，以及 `.locals`/`.registers`

Ch 4 說過 Dalvik 是暫存器式 VM，方法裡的計算都對虛擬暫存器操作。smali 裡暫存器有**兩種命名**：

- **`v0`, `v1`, …**：一般（local）暫存器。
- **`p0`, `p1`, …**：參數（parameter）暫存器——方法收到的參數放這。

**關鍵事實：`p` 暫存器只是 `v` 暫存器的別名，它們是同一組暫存器的尾段。** 一個方法宣告了 N 個暫存器，前面幾個是 local（`v0`…），最後幾個放參數（`p0`…）。baksmali 用 `p` 命名純粹是為了讓你一眼看出「這是傳進來的參數」，不用自己數。

還有一個天天踩的坑：**instance method 的 `p0` 是 `this`**，真正的第一個參數從 `p1` 開始。static method 沒有 `this`，`p0` 才是第一個參數。

```
 instance method: public int add(int a, int b)
   p0 = this
   p1 = a          ← 第一個「真」參數
   p2 = b

 static method:   public static int add(int a, int b)
   p0 = a          ← static 沒有 this, p0 就是第一個參數
   p1 = b
```

方法開頭用兩種指示符之一宣告暫存器數量，**兩者二選一、意義不同**：

- **`.registers N`**：這方法**總共**用 N 個暫存器（含參數暫存器）。
- **`.locals N`**：這方法用 N 個**非參數**（local）暫存器；參數暫存器**另外算**、自動加在後面。

換算關係：`.registers = .locals + 參數個數`（instance method 的參數個數含 `this`）。

```
 public int add(int a, int b)   ← instance, 參數含 this 共 3 個 (this,a,b)

 寫法一: .locals 1     → local 有 v0 一個, 參數 p0(this)/p1(a)/p2(b) 另加 → 共 4 個暫存器
 寫法二: .registers 4  → 明講總共 4 個 (v0 + p0/p1/p2)

 兩種寫法描述同一件事。改 smali 加暫存器時, 你動的就是這個數字 —— 算錯會組不回去或跑崩。
```

> **改 smali 最常見的翻車就在暫存器數**：你想插一行 log、需要一個暫存器暫存，就得把 `.locals`/`.registers` 加 1，並且**確認新用的暫存器編號沒撞到參數暫存器**（記住 `p` 是尾段的別名，多開的 local 是 `v` 的前段，不會撞——但若你直接寫 `.registers` 又算錯總數就會撞）。Ch 10 patch 實戰會專門練這個。

## 核心指令：讀懂一段 smali 要認的十來個

smali 有兩百多個 opcode，但**逆向 90% 的時間只碰十幾個**。分族認識：

**搬值 / 賦常數**

```smali
const/4 v0, 0x1              # v0 = 1  (小立即數, Ch 4 拆過它的 4-bit 編碼)
const/16 v1, 0x100          # v1 = 256 (16-bit 立即數)
const-string v2, "secret"   # v2 = 字串 "secret" (引用 string pool)
move v3, v4                  # v3 = v4  (暫存器間搬值)
```

**呼叫方法（invoke 家族）**——最重要的一族：

```smali
invoke-virtual {p0, v1}, Lcom/example/Foo;->add(II)I   # p0.add(v1)  一般實例方法
invoke-static  {v0}, Lcom/example/Util;->hash(I)I      # 靜態方法
invoke-direct  {p0}, Lcom/example/Foo;-><init>()V      # 建構子/private
invoke-super   {p0}, Landroid/app/Activity;->onCreate()V
invoke-interface {v0, v1}, Ljava/util/List;->get(I)Ljava/lang/Object;
```

讀 invoke 的結構：`invoke-種類 {暫存器清單}, L類;->方法名(參數描述符)回傳描述符`。花括號裡是**傳給這個方法的暫存器**——instance method 第一個是接收者（`p0` 就是 `this.xxx`）。這一行你能拆，smali 你就讀得動一大半，因為 App 邏輯就是一連串方法呼叫。

**接返回值（invoke 之後常跟一條）**

```smali
invoke-virtual {p0, v1}, Lcom/example/Foo;->add(II)I
move-result v0              # 把上一個 invoke 的 int 回傳值放進 v0
```

**這是 smali 最反直覺的一點**：invoke **不直接把回傳值放進某個暫存器**，回傳值暫存在一個隱藏位置，你要用緊跟的 `move-result`（回傳基本型別）或 `move-result-object`（回傳物件）把它撈進暫存器。看到 `invoke-*` 後面沒 `move-result` 就代表**回傳值被丟棄**（呼叫這方法只為它的副作用）。

**條件跳轉（if 家族）**

```smali
if-eqz v0, :cond_0          # if (v0 == 0) 跳到標籤 :cond_0
if-nez v0, :cond_1          # if (v0 != 0) 跳
if-eq v0, v1, :cond_2       # if (v0 == v1) 跳
if-ge v0, v1, :cond_3       # if (v0 >= v1) 跳
    ...falls through...      # 條件不成立就往下走
:cond_0
    ...
```

`z` 結尾的（`if-eqz`/`if-nez`）是「跟 0 比」，沒 `z` 的是兩個暫存器互比。`:cond_0` 是跳轉標籤。**繞驗證最常改的就是這裡**——把 `if-eqz`（驗證失敗才跳走）改成 `if-nez`，或直接讓它無條件通過，License/root 檢查就被翻轉了（Ch 10 實戰）。

**返回（return 家族）**

```smali
return-void                 # 無回傳
return v0                   # 回傳 v0 (int/boolean/等基本型別)
return-object v0            # 回傳 v0 (物件)
return-wide v0              # 回傳 v0 (long/double, 佔兩個暫存器)
```

> **`wide` 的坑**：`long` 和 `double` 是 64-bit，佔**兩個連續暫存器**（`v0` 實際佔 `v0`+`v1`）。所以看到 `return-wide v0`、`const-wide v0`、`move-wide` 就要知道它悄悄用掉了 `v0` 和 `v1` 兩個。算暫存器數時漏掉這個會撞暫存器、組不回去。

## 三個 smali↔Java 對照

**範例 1：一個帶條件的方法**

```java
// Java
public boolean check(int x) {
    if (x == 42) {
        return true;
    }
    return false;
}
```

```smali
# smali (instance method: p0=this, p1=x)
.method public check(I)Z
    .registers 3                   # v0 一個 local + p0(this)/p1(x)

    const/16 v0, 0x2a              # v0 = 42
    if-ne p1, v0, :cond_0          # if (x != 42) 跳到 :cond_0
    const/4 v0, 0x1                # v0 = 1 (true)
    return v0
    :cond_0
    const/4 v0, 0x0                # v0 = 0 (false)
    return v0
.end method
```

對照著讀：`if-ne p1, v0, :cond_0` 就是 Java 的 `if (x != 42)` 走 else 分支。**注意 smali 的條件常是「反過來」的**——Java 寫 `if (x == 42) 做A`，smali 常編成「`if (x != 42) 跳過 A`」。這是編譯器把 if/else 攤平成順序 + 跳轉的結果，讀 smali 要習慣這種反相邏輯。

**範例 2：字串拼接與方法呼叫**

```java
// Java
String greet(String name) {
    return "Hi, " + name;
}
```

```smali
.method greet(Ljava/lang/String;)Ljava/lang/String;
    .registers 3                   # p0=this, p1=name, v0=local

    new-instance v0, Ljava/lang/StringBuilder;        # v0 = new StringBuilder()
    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V
    const-string v1, "Hi, "
    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;
    move-result-object v0
    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;
    move-result-object v0
    return-object v0
.end method
```

一行 Java 的 `"Hi, " + name`，smali 展開成一整串 `StringBuilder` 呼叫——因為 Java 的字串 `+` 底層就是編譯成 `StringBuilder.append`。**這是 smali 「囉嗦」的典型**：Java 的語法糖在 smali 裡現出原形。讀多了你會反射性地把「new StringBuilder + append + append + toString」認成字串拼接，不用逐行分析。

**範例 3（邊界/失敗情況）：try/catch**

```java
// Java
int parse(String s) {
    try {
        return Integer.parseInt(s);
    } catch (NumberFormatException e) {
        return -1;
    }
}
```

```smali
.method parse(Ljava/lang/String;)I
    .registers 3

    :try_start_0
    invoke-static {p1}, Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I
    move-result v0
    return v0
    :try_end_0
    .catch Ljava/lang/NumberFormatException; {:try_start_0 .. :try_end_0} :catch_0

    :catch_0
    const/4 v0, -0x1               # v0 = -1
    return v0
.end method
```

try/catch 在 smali 裡是**用標籤標出保護範圍**：`:try_start_0` 到 `:try_end_0` 之間的指令受保護，`.catch 例外型別 {範圍} :handler` 宣告「這範圍內丟出這種例外就跳到 `:catch_0`」。**沒有 Java 那種語法縮排的區塊感，全靠標籤**。這是 Ch 4 `code_item` 裡 `try_items`/`handlers` 的文字化。逆向踩雷：改動 try 範圍內的指令時若破壞了標籤對應，組回去會報 try 範圍錯誤——這是新手 patch try 區塊常翻車的地方。

## 欄位存取：iget/iput 與 sget/sput

前面都是方法呼叫，但 App 大量在讀寫欄位（成員變數、static 常數）。smali 用兩族指令，逆向找「某個開關變數在哪被設」時天天碰：

- **`iget-*` / `iput-*`**：讀/寫**實例欄位**（instance field，屬於某個物件）。
- **`sget-*` / `sput-*`**：讀/寫**靜態欄位**（static field，屬於類）。

後綴跟型別走：`iget`（int/一般）、`iget-object`（物件）、`iget-boolean`、`iget-wide`（long/double）。看一個 getter/setter 對照：

```java
// Java
class Config {
    private boolean mEnabled;
    static String sApiUrl = "https://api.example.com";

    boolean isEnabled() { return mEnabled; }
    void setEnabled(boolean b) { mEnabled = b; }
}
```

```smali
.method isEnabled()Z
    .registers 2                     # p0=this, v0=local
    iget-boolean v0, p0, Lcom/example/Config;->mEnabled:Z   # v0 = this.mEnabled
    return v0
.end method

.method setEnabled(Z)V
    .registers 2                     # p0=this, p1=b
    iput-boolean p1, p0, Lcom/example/Config;->mEnabled:Z   # this.mEnabled = b
    return-void
.end method

# 讀 static 欄位:
    sget-object v0, Lcom/example/Config;->sApiUrl:Ljava/lang/String;   # v0 = Config.sApiUrl
```

讀 `iget-boolean v0, p0, Lcom/example/Config;->mEnabled:Z`：把 `p0`（this）的 `mEnabled:Z` 欄位讀進 `v0`。格式是 `i操作 目標暫存器, 物件暫存器, L類;->欄位名:型別`。**逆向常見動作**：你想知道某個 `isVip`/`isDebug` 開關在哪被改，就全域搜 `iput-boolean` + 那個欄位名，找到寫它的地方；想強制某開關為 true，就把讀它的 `iget` 之後的邏輯 patch 掉，或直接改 setter。sget/sput 同理但針對 static——App 的硬編碼 URL、金鑰常是 static 欄位，搜 `sget-object` + 欄位名就能定位。

## 對比與取捨：smali vs jadx 的 Java

| 面向 | smali | jadx 的 Java |
|---|---|---|
| 與 DEX 的關係 | **一對一無損** | 近似翻譯（有損） |
| 能改回打包嗎 | **能**（baksmali→smali→組回 DEX） | 常編不過、對不回原 DEX |
| 可讀性 | 低（機器指令逐條） | 高（近人類邏輯） |
| 適合 | **改邏輯、patch、精確定位** | 快速讀懂整體邏輯 |
| 混淆抵抗 | 混淆後仍一對一，但難讀 | 混淆後可能反編譯失敗/出錯 |
| 學習曲線 | 陡（要背描述符/暫存器/指令） | 平（會 Java 就會讀） |

實務上的黃金組合：**jadx 讀懂「這在幹嘛」→ 切到 smali 精確定位「要改哪一行」→ 改 smali 組回去**。兩個一起用，不是二選一。只會 jadx 你改不了東西；只會 smali 你讀整體邏輯會很慢。

## 踩雷集錦

1. **錯誤直覺：「Z 是什麼冷門型別，long 應該是 L」→ 正確認識**：`Z`=boolean、`J`=long、`B`=byte、`L`=物件前綴。這四個天天記錯。背熟描述符表是讀 smali 的入場券，沒有捷徑。
2. **錯誤直覺：「p0 就是方法第一個參數」→ 正確認識**：instance method 的 `p0` 是 **`this`**，第一個真參數是 `p1`。static method 才是 `p0` = 第一個參數。搞錯這個，你 hook/改參數會動到錯的暫存器。
3. **錯誤直覺：「invoke 之後回傳值自動在某暫存器」→ 正確認識**：回傳值要用緊跟的 `move-result`/`move-result-object` 撈出來。沒 `move-result` = 回傳值被丟棄。改邏輯時漏掉這條會拿到舊值或崩。
4. **錯誤直覺：「加一行 smali 不用管暫存器數」→ 正確認識**：多用一個暫存器就得改 `.locals`/`.registers`，還要注意 `long`/`double` 佔**兩個**暫存器、`p` 是暫存器尾段別名。算錯數字直接組不回去或執行崩。
5. **錯誤直覺：「smali 的 if 跟 Java 的 if 邏輯一樣」→ 正確認識**：編譯器常把 `if (cond) A` 攤成「`if (!cond) 跳過 A`」——smali 的條件常是 Java 的**反相**。繞驗證時要看清楚它是「成立才跳」還是「不成立才跳」，改錯方向會反效果。

## 進階：再往深一層

- **`range` 版 invoke**：參數超過 5 個時，`invoke-virtual/range {v0 .. v5}` 用連續暫存器範圍代替逐個列。看到 `/range` 就是參數多、暫存器連續傳。
- **`check-cast` 與泛型的真相**：Java 泛型在 bytecode 層被**類型抹除（type erasure）**，smali 裡看到的是一堆 `check-cast Lcom/x/Y;`——泛型的型別資訊在編譯期就沒了，執行期靠 `check-cast` 補強轉型。這是為什麼 jadx 還原泛型常還原不準。
- **`.line` 與 debug 資訊**：沒被混淆的 smali 會有 `.line 42`（對應原始碼行號）和 `.local` / `.param` 註記（區域變數/參數名）——這些來自 Ch 4 的 `debug_info`。混淆會砍掉它們，這也是混淆後 smali 只剩 `p0/v1` 沒有可讀名字的原因。
- **合成方法與 `access$` 橋接**：內部類存取外部類 private 成員，編譯器會生 `access$000` 這種**合成（synthetic）方法**當橋。smali 裡看到 `access$` 開頭、標了 `synthetic` 的方法，是編譯器產物不是原始碼寫的——逆向時知道它是膠水碼，別花時間找它的「原始碼」。Ch 8 讀反編譯輸出會深入這類編譯器產物。

## 動手練習

1. 把本章「型別描述符表」蓋住右邊，只看左邊 `Z`/`J`/`[I`/`[Ljava/lang/String;`/`(Ljava/lang/String;J)V`，逐個默寫出 Java 型別/簽名。錯的做記號重背——描述符不熟，後面 patch 全卡。
2. 拿範例 1 的 `check` 方法，把 `if-ne p1, v0, :cond_0` 想成你要「讓它永遠回 true」該怎麼改（提示：改跳轉條件，或讓它無條件走到 `const/4 v0, 0x1`）。先在紙上改，理解 Ch 10 patch 的思路。
3. 找一個小 APK（或自己寫個 5 行的 Java 類編成 DEX），用 apktool 反出 smali，挑一個方法對照 jadx 的 Java 逐行讀。特別找一處「Java 一行、smali 一大串」的地方（字串拼接、autobox），親眼看語法糖在 smali 現原形。
4. 在某個方法開頭插一行 `const-string v0, "hi"` 並對應把 `.locals`/`.registers` 加 1（注意別撞 `p` 暫存器），用 apktool 組回去看它**能不能成功組**。故意把暫存器數算錯一次，看它怎麼報錯——親手踩一次「暫存器數算錯組不回」的雷。

## 本章重點整理

- **smali 是 DEX bytecode 的一對一無損文字表示**（逐字稿），所以「改邏輯」動 smali、「讀懂邏輯」用 jadx 的 Java——兩者分工。
- **型別描述符**：`Z`=boolean、`J`=long、`I`=int、`L套件/類;`=物件、`[`=陣列；方法簽名 `(參數描述符)回傳描述符` 串接無分隔。背熟這張表是入場券。
- **暫存器 `v`（local）/`p`（參數，是 v 尾段別名）**；instance method 的 **`p0`=this**、第一個真參數是 `p1`；`.registers` = `.locals` + 參數數；`long/double` 佔兩個暫存器。
- **核心指令族**：`const-*`（賦值）、`invoke-*`+`move-result`（呼叫並取回傳值）、`if-*z`/`if-*`（條件跳轉，常是 Java 的反相）、`return-*`；try/catch 靠 `:try_start/:try_end` + `.catch` 標籤界定。

## 自我檢核

- [ ] 不看筆記，能把 `Z`、`J`、`[I`、`Ljava/lang/String;`、`(Ljava/lang/String;J)Z` 翻成對應的 Java 型別/簽名
- [ ] 能解釋 instance method 為什麼 `p0` 是 this、`.registers` 和 `.locals` 差在哪
- [ ] 能講出 `invoke-virtual` 之後為什麼常跟一條 `move-result`，沒跟代表什麼
- [ ] 看到 `if-eqz v0, :cond_0` 能立刻說出它做什麼、以及為什麼 smali 條件常是 Java 的反相
- [ ] 能對照著讀一段 try/catch 的 smali，指出保護範圍和 handler 在哪
- [ ] 能說出為什麼「改邏輯用 smali 不用 jadx 的 Java」，以及改 smali 加暫存器要注意什麼

## 延伸閱讀

### 語言參考（一手）

- **[smali/baksmali 專案 Wiki](https://github.com/JesusFreke/smali/wiki)** — JesusFreke
  - **讀哪裡**：Registers、TypesMethodsAndFields、Instructions 三頁，正好對應本章的暫存器/描述符/指令
  - **為什麼值得讀**：smali 語法的權威來源（smali 就是這專案定義的）。本章是導讀，查精確語法來這
  - **前提知識**：讀過本章，這裡當字典查每個指令的精確語意
- **[Dalvik bytecode 指令集](https://source.android.com/docs/core/runtime/dalvik-bytecode)** — AOSP
  - **讀哪裡**：opcode 總表，對照本章的 `const-*`/`invoke-*`/`if-*`/`return-*` 各族
  - **和本章的關聯**：smali 的每個指令名對應這裡一個 opcode；想知道某個沒講到的指令做什麼，查這

### 實戰對照

- **[HackTricks — Smali](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/smali-changes.html)**
  - **這篇說什麼**：改 smali 的常見場景與片段（改回傳值、繞檢查、加 log）
  - **讀哪裡**：常見修改模式那幾段，正好銜接 Ch 10 patch 實戰
  - **前提知識**：讀過本章的指令族，這頁給你「實際要改哪幾行」的範式
- **[OWASP MASTG — Manipulating Bytecode](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0018/)**
  - **這篇說什麼**：從安全測試視角改 smali 重打包的標準流程
  - **讀哪裡**：smali patching 的步驟與注意事項
  - **和本章的關聯**：本章教你讀寫 smali，這頁把它放進「改完重簽名重裝」的完整測試循環（Ch 6 深入）

下一章我們把「改 smali」變成完整可執行的流程：apktool 怎麼把 APK 反編譯成 smali、你改完之後怎麼回編譯成新 APK、為什麼一定要重簽名（Ch 2 埋的伏筆）、以及重打包全流程的踩雷。學會這套，你就能真正動手改一個 App 並讓它跑起來。

→ [Ch 6 apktool：反編譯、改 smali、回編譯、重簽名](./06-apktool-rebuild.md)
