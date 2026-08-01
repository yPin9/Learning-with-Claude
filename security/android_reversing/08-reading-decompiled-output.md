# Ch 8 — 讀懂反編譯輸出：匿名類、lambda、協程陷阱

> **目標**：反編譯出的 Java 裡有一大堆「看起來很怪」的東西——`$1`/`$2` 匿名類、`access$000` 合成方法、被擦掉的泛型、Kotlin 協程變成的巨型狀態機、`data class` 展開成一堆 `component1()`、`when` 變成鏈式 `if`。這些**不是反編譯 bug，是編譯器生成程式碼的真實樣貌**。這章給你一整組「反編譯輸出 vs 原意」的對照，讓你一眼看穿：這坨怪東西的原始碼本來長什麼樣。

> **環境**：本章的對照示範以 **javac / kotlinc** 的標準編譯行為為準，用 jadx 1.4 反編譯的典型形態呈現。這些是編譯器**確定的**生成規則（不是隨機的），所以概念部分直接論述；具體反編譯畫面本 repo 沙箱無 jadx，標「典型形態」，你可在自己環境用 jadx 對任一 Kotlin App 驗證。smali 片段手寫但語法正確、標明對應 Java。

## 為什麼需要這個？

Ch 7 講了 jadx 的輸出是「近似」。但即使 jadx 反編譯得很準，你看到的東西還是會很陌生——因為**你看到的不是開發者寫的原始碼，是編譯器生成的中間產物再被反編譯回來**。開發者寫 `list.forEach { println(it) }` 一行，編譯器展開成一個匿名類、一個合成方法、一堆膠水碼，jadx 忠實地把這些全反編譯出來給你看。

如果你不知道「這坨 `$1`、`access$000`、`Continuation` 是編譯器生成的」，你會：把時間浪費在讀膠水碼、把合成方法當成 App 的核心邏輯、被 Kotlin 協程的狀態機繞暈、或在泛型被擦除時誤判型別。這章就是給你一副「X 光眼鏡」——看穿膠水，直達開發者的原意。現代 App 幾乎都是 Kotlin，這章的 Kotlin 部分尤其是你天天會撞到的。

## 先建立直覺：你讀的是「編譯器的作文」

開發者的原始碼經過編譯器，會被改寫成 JVM/Dalvik 能執行的形態。這個改寫是**有固定套路**的：

```
   開發者寫的（原意）              編譯器生成的（你反編譯看到的）
 ┌──────────────────────┐      ┌────────────────────────────────┐
 │ 匿名類 / lambda       │─────▶│ Outer$1, Outer$2 (獨立 class)   │
 │ 存取外層 private       │─────▶│ access$000() 合成橋接方法       │
 │ List<String>          │─────▶│ List (泛型被擦除)               │
 │ Kotlin data class     │─────▶│ component1()/copy()/hashCode()  │
 │ Kotlin when           │─────▶│ 鏈式 if-else 或 tableswitch     │
 │ Kotlin 協程 suspend    │─────▶│ 巨型 switch 狀態機 + label      │
 │ Kotlin ?. / !!         │─────▶│ 顯式 null 檢查 + throw          │
 └──────────────────────┘      └────────────────────────────────┘
```

關鍵心智模型：**編譯是「降階」——把高階抽象拆成低階可執行的碎片，過程中生成一堆你沒寫的膠水碼**。反編譯把碎片拼回來給你，但拼不回你原本的高階抽象（那個抽象在編譯時就被拆掉了）。所以你的工作是**逆向認出這些套路**：看到 `$1` 就知道「喔這是個匿名類」、看到 `Continuation` 就知道「喔這是協程」，直接跳過膠水、鎖定真正的邏輯。

## 對照 1：匿名類與 lambda → `$1`、`$2`

**原始碼（開發者寫的）**：

```java
button.setOnClickListener(new View.OnClickListener() {
    @Override
    public void onClick(View v) {
        doLogin();
    }
});
```

**反編譯輸出（典型形態）**：

```java
button.setOnClickListener(new LoginActivity$1(this));
```

外加一個獨立的 class 檔 `LoginActivity$1`：

```java
// LoginActivity$1.java  ← 編譯器生成的，開發者沒寫這個檔
class LoginActivity$1 implements View.OnClickListener {
    final LoginActivity this$0;      // ← 指回外層物件的引用

    LoginActivity$1(LoginActivity loginActivity) {
        this.this$0 = loginActivity;
    }

    public void onClick(View v) {
        this.this$0.doLogin();       // ← 透過 this$0 呼叫外層方法
    }
}
```

**怎麼讀**：
- `外層類名$數字` = 匿名類。`$1` 是外層類裡第一個匿名類，`$2` 是第二個，編號按出現順序。
- `this$0` = 匿名類自動持有的「外層 `this`」引用。匿名類要能存取外層的成員，就靠這個。看到 `this$0.xxx` 就是「呼叫外層的 xxx」。
- **lambda 在較新編譯下可能不生成 `$1` 而是用 `invokedynamic`/合成 `lambda$` 方法**，但 Android 的 d8/r8 通常會把 lambda **desugar 成匿名類**，所以你多半還是看到 `$1` 形態。看到 `lambda$onCreate$0` 這種方法名，就是一個 lambda 被降階成的合成方法。

> **逆向意義**：找「按鈕點了做什麼」「網路回來後做什麼」這類 callback 邏輯，就是去讀 `$1`/`$2` 這些匿名類的 `onClick`/`onResponse`。它們是編號的，不是亂碼——`$1` 只是「第一個匿名類」的意思。

## 對照 2：合成方法 `access$000` → 跨越 private 邊界

**原始碼**：

```java
public class Outer {
    private String secret = "key123";
    private void reveal() { /* ... */ }

    class Inner {
        void use() {
            System.out.println(secret);   // 內部類存取外層 private 欄位
            reveal();                     // 內部類呼叫外層 private 方法
        }
    }
}
```

問題：JVM 層級 `private` 是真的私有，`Inner`（編譯後是獨立 class `Outer$Inner`）**不能**直接碰 `Outer` 的 private。編譯器怎麼辦？**生成合成橋接方法**。

**反編譯輸出（典型形態）**：

```java
// Outer.java 裡多出這些「開發者沒寫」的合成方法：
static /* synthetic */ String access$000(Outer outer) {
    return outer.secret;              // 橋接：讓 Inner 能讀 private secret
}
static /* synthetic */ void access$100(Outer outer) {
    outer.reveal();                   // 橋接：讓 Inner 能呼叫 private reveal
}

// Outer$Inner.java：
void use() {
    System.out.println(Outer.access$000(this.this$0));   // 透過橋接讀 secret
    Outer.access$100(this.this$0);                       // 透過橋接呼叫 reveal
}
```

**怎麼讀**：
- `access$數字` = 編譯器生成的**合成橋接方法**，用來讓內部類跨越 `private` 邊界存取外層成員。它們**不是 App 的業務邏輯**，是膠水。
- `/* synthetic */` 註解就是 jadx 在告訴你「這是編譯器生成的，不是原始碼」。
- 看到 `access$000(x)` 讀作「x 的某個 private 成員」；`access$100(x)` 讀作「呼叫 x 的某個 private 方法」。要找真正的邏輯，穿過這層橋接看它包住的是哪個欄位/方法。

> **逆向意義**：`access$` 是雜訊。你要找 secret 是什麼，別停在 `access$000`，要看它 `return` 的那個真正欄位（`outer.secret`）。合成方法只是編譯器搭的橋，橋的兩端才是重點。

## 對照 3：泛型擦除 → `List` 而不是 `List<String>`

**原始碼（Kotlin/Java 泛型）**：

```java
Map<String, User> users = new HashMap<>();
List<String> names = new ArrayList<>();
```

**反編譯輸出（典型形態）**：

```java
Map users = new HashMap();          // <String, User> 不見了
List names = new ArrayList();       // <String> 不見了
// 存取時可能看到強制轉型：
User u = (User) users.get("alice"); // ← 這個 cast 是型別資訊唯一的殘跡
```

**為什麼**：Java/Kotlin 泛型是**編譯期**的型別檢查機制，編譯完就**擦除**（type erasure）——bytecode 裡 `List<String>` 和 `List<User>` 完全一樣，都是 `List`。泛型參數在 runtime 不存在，所以反編譯也還原不出來。

**怎麼讀**：
- 反編譯看到裸 `List`/`Map`，別以為原始碼沒寫泛型——多半寫了，只是被擦除了。
- **強制轉型 `(User)` 是你唯一的線索**：`(User) map.get(...)` 反推原本是 `Map<?, User>`。編譯器在取值處插入的 cast，洩漏了被擦掉的型別。
- 少數保留：欄位/方法簽名的泛型資訊有時存在 Signature attribute 裡，jadx 可能還原部分。但區域變數的泛型基本沒救。

## 對照 4：Kotlin `data class` → 一堆 `componentN`/`copy`

**原始碼（Kotlin，開發者寫一行）**：

```kotlin
data class User(val name: String, val age: Int)
```

**反編譯輸出（典型形態，一行變幾十行）**：

```java
public final class User {
    private final String name;
    private final int age;

    public User(String name, int age) { this.name = name; this.age = age; }

    public final String getName() { return this.name; }
    public final int getAge() { return this.age; }

    // 以下全是 data class 自動生成的：
    public final String component1() { return this.name; }   // 解構用
    public final int component2() { return this.age; }       // 解構用
    public final User copy(String name, int age) { return new User(name, age); }
    public String toString() { return "User(name=" + this.name + ", age=" + this.age + ")"; }
    public int hashCode() { return (this.name.hashCode() * 31) + this.age; }
    public boolean equals(Object o) { /* 逐欄位比對 */ }
}
```

**怎麼讀**：
- 看到一個 class 有成套的 `component1()`/`component2()`/`copy()`/`toString()`/`hashCode()`/`equals()` → **這是 Kotlin `data class`**。開發者只寫了一行 `data class User(...)`。
- `componentN()` 是給 Kotlin 解構宣告用的（`val (n, a) = user`），你不用管它，看建構子參數就知道欄位。
- **真正的資訊是建構子的參數列表**——那就是 data class 的欄位。其餘全是自動生成的樣板。

## 對照 5：Kotlin `when` → 鏈式 `if` 或 `switch`

**原始碼（Kotlin）**：

```kotlin
when (code) {
    0 -> handleOk()
    1 -> handleRetry()
    else -> handleError()
}
```

**反編譯輸出（典型形態，整數分支常變 tableswitch/if 鏈）**：

```java
switch (code) {
    case 0:  handleOk();    break;
    case 1:  handleRetry(); break;
    default: handleError(); break;
}
// 若 when 的是物件/字串，會變成鏈式 if：
if (Intrinsics.areEqual(s, "ok")) { ... }
else if (Intrinsics.areEqual(s, "retry")) { ... }
else { ... }
```

**怎麼讀**：
- `when` 對整數/enum 通常降階成 `switch`（tableswitch/packed-switch），對字串/物件降階成鏈式 `if` + `Intrinsics.areEqual`。
- `Intrinsics.areEqual(a, b)` 是 Kotlin runtime 的 null-safe equals（等價 `a?.equals(b) ?: (b==null)`）。看到它就知道原本是 Kotlin 的 `==`。
- enum 的 `when` 可能出現一個合成的 `$EnumSwitchMapping$0` 陣列——那是編譯器為了把 enum 常數映射成整數 case 生成的查表，是膠水，別當邏輯。

## 對照 6（重點）：Kotlin 協程 → 巨型狀態機

這是 Kotlin 反編譯**最會把人繞暈**的地方。一個乾淨的 `suspend` 函式，反編譯出來是一個帶 `label` 和 `switch` 的怪物。

**原始碼（Kotlin，開發者寫得很直覺）**：

```kotlin
suspend fun login(user: String): Result {
    val token = api.getToken(user)      // suspend 呼叫（會掛起）
    val profile = api.getProfile(token) // suspend 呼叫（會掛起）
    return Result(token, profile)
}
```

**反編譯輸出（典型形態，一個函式變成狀態機）**：

```java
public final Object login(String user, Continuation<? super Result> continuation) {
    // ① 恢復用的狀態物件
    LoginKt$login$1 cont;
    if (continuation instanceof LoginKt$login$1) {
        cont = (LoginKt$login$1) continuation;
    } else {
        cont = new LoginKt$login$1(this, continuation);
    }
    Object result = cont.result;
    Object suspended = IntrinsicsKt.getCOROUTINE_SUSPENDED();

    switch (cont.label) {              // ② 用 label 記「上次執行到哪」
        case 0:
            ResultKt.throwOnFailure(result);
            cont.L$0 = user;
            cont.label = 1;            // 標記：下次從 case 1 繼續
            Object token = this.api.getToken(user, cont);
            if (token == suspended) return suspended;   // ③ 掛起就 return
            // 沒掛起就 fall through 帶著 token
            break; // (示意)
        case 1:
            ResultKt.throwOnFailure(result);
            user = (String) cont.L$0;
            token = result;            // 恢復後 result 就是上次的回傳值
            cont.label = 2;
            Object profile = this.api.getProfile((String) token, cont);
            if (profile == suspended) return suspended;
            break;
        case 2:
            ResultKt.throwOnFailure(result);
            profile = result;
            return new Result((String) token, profile);
    }
}
```

**怎麼讀**（記住這套模式，協程就不可怕了）：
- **多出一個 `Continuation` 參數** + 回傳 `Object` → 這是 `suspend` 函式被降階的鐵證。
- **`switch (cont.label)`** 是狀態機的核心：`label` 記錄「上次掛起在第幾個 suspend 點」，恢復時跳回對應 `case` 繼續。每個 `case` 對應原始碼的一段（兩個 suspend 呼叫之間）。
- **`if (x == suspended) return suspended;`** 是掛起邏輯：suspend 呼叫還沒完成就 return，等結果好了再被重新呼叫、從下個 `label` 繼續。
- **`cont.L$0`/`L$1`** 是跨掛起點保存的區域變數（掛起後 stack 沒了，得存進 continuation 物件）。
- **還原原意的訣竅**：把每個 `case` 之間的實質呼叫按 label 順序串起來——`case 0` 呼叫 `getToken`、`case 1` 呼叫 `getProfile`、`case 2` 組 `Result`。串起來就是原始碼的三行。**忽略掉 label/suspended/throwOnFailure 這些狀態機膠水，只看每段真正呼叫了什麼**。

> **逆向意義**：協程狀態機看起來嚇人，但套路固定。你要找的邏輯（呼叫了哪個 API、傳什麼參數）都在各 `case` 裡，被膠水包著。認出 `Continuation`+`label`+`switch` 三件套，就知道「這是協程」，然後只讀每段的實質呼叫。現代 App 的網路請求幾乎都在協程裡，這個模式你會天天遇到。

## 對照 7：Kotlin null 安全 → 顯式檢查與 `Intrinsics`

**原始碼（Kotlin）**：

```kotlin
fun greet(name: String) { println(name.length) }   // name 非 null
val x = obj!!.value                                  // !! 斷言非 null
```

**反編譯輸出（典型形態）**：

```java
public final void greet(String name) {
    Intrinsics.checkNotNullParameter(name, "name");  // ← 編譯器插的非 null 檢查
    System.out.println(name.length());
}
// !! 變成：
Intrinsics.checkNotNull(obj);
Object x = obj.value;
```

**怎麼讀**：
- `Intrinsics.checkNotNullParameter(x, "x")` = Kotlin 對**非 null 參數**自動插入的執行期檢查（違反就丟 `NullPointerException`）。看到它就知道原始碼那個參數型別是非 null 的（`String` 而非 `String?`）。
- `Intrinsics.checkNotNull(x)` 對應 Kotlin 的 `!!` 斷言。
- 這些 `Intrinsics.*` 呼叫是**Kotlin 專屬的膠水**——看到它們，你就確定「這是 Kotlin 編譯出來的」，跟純 Java App 一眼就能區分。

## 對比與取捨：認膠水的速查表

| 反編譯看到 | 原意 | 該怎麼對待 |
|---|---|---|
| `Outer$1`, `$2` | 匿名類 / lambda | 讀它的 callback 方法（`onClick` 等） |
| `this$0` | 匿名/內部類持有的外層引用 | `this$0.x` = 外層的 x |
| `access$000` | 合成橋接（跨 private） | 雜訊；看它包住的真正欄位/方法 |
| `lambda$foo$0` | lambda 降階的合成方法 | = 一個 lambda body |
| 裸 `List`/`Map` | 泛型被擦除 | 看取值處的 `(T)` cast 反推型別 |
| `component1/copy/...` 成套 | Kotlin data class | 看建構子參數 = 欄位 |
| `Intrinsics.areEqual` | Kotlin `==` | 原本是 `when`/`if` 的相等比較 |
| `Continuation`+`label`+`switch` | Kotlin 協程 suspend | 按 label 順序串各段實質呼叫 |
| `Intrinsics.checkNotNull*` | Kotlin null 安全 | 膠水；標記這是 Kotlin |
| `$EnumSwitchMapping$0` | enum when 的查表 | 膠水；別當邏輯 |

## 踩雷集錦

1. **把合成方法 `access$000` 當核心邏輯**：花時間分析 `access$000` 本身——它只是橋接，真正的東西是它 return 的欄位。穿過去看被包住的。
2. **看到裸 `List` 以為原始碼沒泛型**：泛型被擦除，不是沒寫。靠取值處的強制轉型反推型別，別假設它是 `List<Object>`。
3. **被協程狀態機嚇退**：`switch(label)` 看起來像天書，其實套路固定。認出 `Continuation`+`label`，只讀各 `case` 的實質呼叫，忽略掛起膠水。這是 Kotlin 逆向最值錢的一招。
4. **把 `$1`/`$2` 的編號當亂碼**：它們是「第 N 個匿名類」的意思，有規律。`Activity$1` 就是那個 Activity 裡第一個匿名類，去讀它的 callback。
5. **忽略 `Intrinsics.*` 的訊號**：這些不只是雜訊——它們**確認了 App 是 Kotlin**，這影響你後面找邏輯的策略（協程、data class 的套路都會出現）。看到 `Intrinsics` 就切換到「Kotlin 讀法」。
6. **把 data class 的 `component1/copy/equals` 逐個讀**：全是自動生成的樣板，看建構子參數就夠了。逐個讀是浪費生命。

## 進階：再往深一層

- **r8 的 desugar 與內聯**：Android 的 r8（release 建構）會做內聯、去除未用碼、把 lambda desugar 成匿名類。所以 release 版反編譯出來的膠水形態可能跟 debug 版不同（更多內聯、更少獨立 `$1`）。認膠水時要知道「同一份原始碼，debug/release 反編譯形態會不一樣」。
- **suspend 的 `invokeSuspend`**：協程更完整的形態裡，狀態機邏輯常搬進一個內部類 `XxxKt$foo$1` 的 `invokeSuspend(Object)` 方法（而非上面簡化的直接在函式裡）。認法一樣：找 `label` + `switch` + `COROUTINE_SUSPENDED`。
- **`when` 的 sealed class 分支**：Kotlin `sealed class` 的 `when` 會變成一串 `instanceof` 檢查（`if (x instanceof Ok) ... else if (x instanceof Err) ...`）。看到對同一物件連續 `instanceof` 不同子類，反推原本是 sealed class 的窮舉 `when`。
- **jadx 的 Kotlin metadata 支援**：新版 jadx 能讀 class 裡的 `@Metadata` annotation（Kotlin 編譯器嵌入的原始資訊），據此還原部分 Kotlin 語法、參數名、甚至 data class 的性質。開 jadx 的 Kotlin 相關選項，反編譯會更貼近 Kotlin 原意——但 metadata 可被混淆器剝掉，剝掉後你就得靠本章的手動認膠水。

## 動手練習

1. 寫一個最小 Kotlin `data class`，kotlinc 編譯成 DEX（或包進小 App），jadx 反編譯，數一數一行原始碼生成了幾個方法——親眼看「一行 → 一坨」。
2. 寫一個有兩個 `suspend` 呼叫的協程函式，反編譯它，對照本章對照 6，把每個 `case` 對回原始碼的哪一行。做過一次，以後看協程狀態機就有肌肉記憶。
3. 找一個真實 Kotlin App，jadx 搜 `Intrinsics.checkNotNullParameter`，隨便挑一個方法，練習「跳過 Intrinsics 膠水、只讀業務邏輯」。
4. 找一個匿名類 `$1`，用 jadx 的 Find Usage 追「誰 new 了它」，確認它是哪個 callback（點擊/回呼/監聽）——把膠水和真正的觸發點連起來。

## 本章重點整理

- 反編譯輸出裡的怪東西**多半是編譯器生成的膠水**（`$1`/`access$`/`component1`/`Continuation`），不是 App 邏輯，也不是反編譯 bug。
- 認膠水的核心套路：`$N`=匿名類、`this$0`=外層引用、`access$`=跨 private 橋接、裸泛型=擦除、成套 `componentN`=data class、`Continuation`+`label`+`switch`=協程。
- **協程狀態機**是 Kotlin 逆向最會繞人的形態：認出三件套，只讀各 `case` 的實質呼叫，忽略掛起膠水。
- `Intrinsics.*` 是「這是 Kotlin」的訊號，看到就切換到 Kotlin 讀法。

## 自我檢核

- [ ] 看到 `Outer$1` / `this$0` / `access$000`，能立刻說出各是什麼、該不該深究
- [ ] 反編譯出裸 `List`，知道怎麼靠強制轉型反推被擦除的泛型型別
- [ ] 看到 `Continuation`+`label`+`switch`，能認出是協程，並知道怎麼把各 `case` 串回原意
- [ ] 看到成套 `component1/copy/equals`，知道是 data class，且知道真資訊在建構子參數
- [ ] 能從 `Intrinsics.*` 判斷 App 是 Kotlin，並說出這對讀法的影響

## 延伸閱讀

### Kotlin 編譯機制

- **[Kotlin 官方文件 — Coroutines 概念](https://kotlinlang.org/docs/coroutines-overview.html)** — JetBrains
  - **讀哪裡**：suspend 函式與 continuation-passing 的概念說明
  - **和本章的關聯**：懂了 CPS（continuation-passing style）你就懂反編譯出的狀態機為什麼長那樣；對照 6 的膠水全源於此
- **[《Deep Dive into Coroutines》/ KotlinConf 演講與文章](https://kotlinlang.org/spec/asynchronous-programming-with-coroutines.html)** — Kotlin spec
  - **讀哪裡**：狀態機轉換那節（label、continuation 物件）
  - **為什麼值得讀**：把「一個 suspend 函式如何被降階成狀態機」講到位，這是本章對照 6 的一手依據

### 反編譯與膠水

- **[jadx GitHub — Kotlin metadata / deobf 相關 issue 與 wiki](https://github.com/skylot/jadx)** — skylot
  - **讀哪裡**：Kotlin metadata 支援與相關選項的說明
  - **和本章的關聯**：jadx 靠 `@Metadata` 還原部分 Kotlin 原意；知道它能還原什麼、混淆後失去什麼
- **[Java Language Spec — Synthetic / Bridge Methods](https://docs.oracle.com/javase/specs/jls/se17/html/index.html)** — Oracle
  - **讀哪裡**：nested class 存取控制與合成成員那節
  - **前提知識**：讀過本章對照 2，這頁給你「編譯器為什麼一定要生成 `access$`」的規範級解釋

下一章我們離開 DEX/Java，轉向 APK 的另一半——資源與 Manifest。硬編碼的 URL、API key、字串常藏在 `resources.arsc` 與 `res/` 裡，而它們是 binary XML/二進位表，unzip 出來一片亂碼。下一章教你解開它們、在資源裡找金鑰、看懂 resource id 的 `0x7f...`。

→ [Ch 9 資源與 Manifest 逆向](./09-resources-manifest-re.md)
