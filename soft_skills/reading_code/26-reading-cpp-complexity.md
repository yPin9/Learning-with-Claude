# Ch 26 — 讀懂 C++ 的複雜性

> **目標**：C++ 難讀的根本原因，不是語法複雜，是**很多 code 不在你看得到的地方**。template 要實例化才存在、解構函式在 scope 結尾隱式跑、`a + b` 可能是一次函式呼叫、`auto` 藏起真實型別、ADL 讓函式從別的 namespace 冒出來。這章教你用「編譯器的視角」把這些藏起來的 code 挖出來——真編譯、真 `nm`/`objdump`/gdb，讓「隱形的 code」現形。讀完你有一份「讀陌生 C++ 的求生指南」，看到爆炸的 template error 訊息不會慌，看到一行 `auto x = foo(a, b);` 知道要去問哪三個問題。

## C++ 難讀的根本：code 不在你以為的地方

讀 C 的時候，有一條讓人安心的鐵律：**你看到的就是會執行的**。`a + b` 就是一條加法指令，`}` 就只是 scope 結束不做事，函式呼叫寫 `foo(x)` 你就知道呼叫的是哪個 `foo`。控制流基本上「所見即所得」。

C++ 打破了這條鐵律。同樣一行 code，實際發生的事可能是 source 上完全看不到的：

```cpp
std::string c = a + b;   // 一次函式呼叫（operator+）、一次記憶體配置、
                          // 可能還有多次建構/解構
{
    Guard g("x");         // 一次 Guard::Guard 呼叫
}                         // ← 這個 } 偷偷跑了 Guard::~Guard()！source 上沒有任何字
```

C++ 的複雜性幾乎全部來自這個「隱形 code」現象。所以讀 C++ 的核心技巧是：**訓練自己看到「觸發隱形 code 的語法信號」，然後有辦法把隱形的部分挖出來看。** 這章分兩件事教：（1）每種 C++ 特性藏了什麼、信號是什麼；（2）怎麼用工具讓它現形。

工具面先給結論，後面逐一示範：

| 想看的隱形東西 | 工具 |
|---|---|
| template 實例化成哪些真實型別 | `nm -C`（列符號）、gdb `ptype`/`whatis` |
| `}` 到底跑了哪些解構、`a+b` 呼叫了什麼 | `objdump -d -C`（看真實 call 指令） |
| `auto`/`decltype` 的真實型別 | gdb `ptype`/`whatis`、編譯器 error 誘導、cppinsights.io |
| template/RAII 展開後的等價 C++ | [cppinsights.io](https://cppinsights.io)（線上把隱形 code 攤成明文） |

> 一個心態轉換：讀 C 你信任 source，讀 C++ 你要**不信任 source 的表面**，時時問「這一行背後編譯器替我生成了什麼？」。這聽起來累，但一旦養成，C++ 就從「看不懂的魔法」變成「有規律的自動生成」。

## Template：實例化才存在

template 是 C++ 最違反 C 直覺的地方。一個 template 函式/類別，在你**用它之前根本不產生任何機器碼**。它是個「產生 code 的模板」，編譯器看到你用某個具體型別呼叫它，才「實例化」（instantiate）出那個型別專屬的一份 code。

真跑示範。這個小程式：

```cpp
template <typename T>
T add(T a, T b) { return a + b; }

int main() {
    int x = add<int>(3, 4);
    double y = add<double>(1.5, 2.5);
    // ...
}
```

`add` 是一個 template。編譯後，用 `nm -C`（`-C` = demangle，把 C++ 的 mangled name 還原成人看得懂）看真的產生了哪些符號（真跑輸出）：

```
$ g++ -O0 -g -std=c++17 tmpl.cpp -o tmpl
$ nm -C tmpl | grep " add"
000000000000130d W double add<double>(double, double)
00000000000012f5 W int add<int>(int, int)
```

**兩份**真實的 `add` 出現在 binary 裡：`add<int>` 和 `add<double>`。source 上只寫了一個 `add`，但因為你用了兩種型別，編譯器實例化了兩份獨立的機器碼。（那個 `W` 是 weak symbol，代表這是 template 實例化產生的，多個 TU 可能各生一份、連結時去重。）

**這對讀碼的意義**：

1. **你在 source 看到的 template，不是最終會跑的 code。** 要知道實際跑什麼，得知道它被哪些型別實例化了。`nm -C | grep 函式名` 就能列出全部實例。
2. **一個 template 被 N 種型別用，就有 N 份 code。** 這是 C++ binary 常常很肥的原因（code bloat）。讀效能問題時要意識到這點。
3. **template 裡的錯誤，要到實例化時才爆。** 這帶出下一個惡名昭彰的主題。

### 怎麼讀爆炸的 template error 訊息

C++ 最勸退的體驗，就是用錯一個 STL 容器，噴出三百行、每行五百字元的錯誤。這些訊息看起來像天書，但它有結構，讀它有方法：

**第一，從最後往前讀，不是從前往後。** 錯誤訊息的第一行通常是「最外層的實例化」，真正的錯因往往在**最後幾行**或中間的 `required from here`。編譯器是「一層層實例化下去，在最深處撞到錯，再把整條實例化鏈印出來」。你要找的是鏈底。

**第二，找 `required from here`。** GCC/Clang 會用這個標記串起實例化鏈：「main 要求實例化 A，A 要求實例化 B，B 在這裡出錯」。順著 `required from here` 往下，就走到真正出錯的那一層。

**第三，把長型別名在腦中折疊。** `std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char>>` 就是 `std::string`。`std::map<...>::iterator` 那一大坨就是「map 的迭代器」。讀的時候把這些噪音摺疊成短名，訊息瞬間縮短十倍。

**第四，關鍵字定位。** 在噪音裡搜 `no matching function`、`no known conversion`、`incomplete type`、`static assertion failed`——這幾個是真正的錯因分類，找到它就找到病根。`static assertion failed` 尤其友善，好的 template library（如 fmt、Eigen）會用 `static_assert` 主動給人話錯誤訊息。

> 求生法則：**template error 的長度跟錯誤的嚴重程度無關。** 三百行的錯誤可能只是你少寫一個 `const`。別被長度嚇到，按上面四步定位，通常五分鐘內能找到那一個字。

## RAII：解構函式在 scope 結尾隱式執行

RAII（Resource Acquisition Is Initialization）是 C++ 的靈魂，也是讀碼時**控制流看不到**的最大來源。核心機制：物件離開 scope 時，它的解構函式（destructor）**自動被呼叫**——但 source 上那個 `}` 不寫任何字。

真跑示範。這段：

```cpp
struct Guard {
    const char *name;
    Guard(const char *n) : name(n) { printf("ctor %s\n", name); }
    ~Guard() { printf("dtor %s\n", name); }
};

int main() {
    Guard a("a");
    { Guard b("b"); }     // b 在這個內層 } 就解構
    return 0;
}
```

執行輸出（真跑）：

```
ctor a
ctor b
dtor b      ← 內層 } 觸發，source 上看不到
dtor a      ← main 的 } 觸發，source 上也看不到
```

注意順序：**解構是「後建構的先解構」（LIFO，逆序）**，而且 `b` 在內層 `}` 就死了，`a` 撐到 `main` 結束。這兩件事在 source 上都沒有一個字明示。

用 `objdump` 看編譯器到底插了什麼（真跑輸出，過濾 main 的 call 指令）：

```
$ objdump -d -C --no-show-raw-insn tmpl | sed -n '/<main>:/,/^$/p' | grep call
    call   1284 <Guard::Guard(char const*)>    ← 建構 a
    call   1284 <Guard::Guard(char const*)>    ← 建構 b
    call   12c4 <Guard::~Guard()>              ← 解構 b（內層 }）
    call   12c4 <Guard::~Guard()>              ← 解構 a（main 的 }）
    call   12c4 <Guard::~Guard()>              ← 第三個解構？！
    call   1090 <_Unwind_Resume@plt>
    call   1080 <__stack_chk_fail@plt>
```

**兩次建構、三次解構呼叫。** 前兩個解構是正常路徑（b 在內層、a 在 return）。第三個 `Guard::~Guard()` 配上緊跟的 `_Unwind_Resume`——這是**例外處理路徑（exception unwinding path）**：萬一中間有函式丟出例外，編譯器也得保證已建構的物件被正確解構，所以它額外生成了一條「例外時的清理路徑」。這條 code 在 source 上百分之百看不到，只有 objdump 才看得見。

**這對讀碼的意義（極重要）**：

1. **看到區域物件的宣告，就要在它的 scope 結尾「腦補」一次解構呼叫。** 讀 C++ 函式，每個 `{ }` 區塊結束時，反射性地問「這裡有哪些物件要解構？順序是？」。這是讀 C++ 控制流的必備動作。

2. **解構函式裡可能有重要副作用。** `std::lock_guard` 的解構會**放鎖**、`std::unique_ptr` 的解構會**釋放記憶體**、檔案 RAII wrapper 的解構會**關檔**。你在函式裡找不到 `unlock()`？因為它在 `lock_guard` 的解構裡，藏在 `}`。**「找不到成對的釋放操作」時，第一個懷疑對象就是某個 RAII 物件的解構。**

3. **例外安全的清理路徑是隱形的。** 上面那第三個解構呼叫提醒你：C++ 的控制流不只有正常路徑，還有一整套「例外時往回收拾」的路徑。讀「這函式如果中途丟例外會發生什麼」時，這些隱形清理很關鍵。

## Operator overloading：`a + b` 可能是函式呼叫

C++ 允許替自訂型別定義運算子。所以 `a + b`、`a == b`、`a[i]`、`*p`、`a << b` 在 C++ 裡**都可能是一次函式呼叫**，而不是你以為的內建運算。這是「code 不在你以為的地方」的又一版本。

真跑示範。`std::string` 的 `+`：

```cpp
std::string a = "foo", b = "bar";
std::string c = a + b;      // 看起來像加法，其實是……
```

objdump 看 `a + b` 那一行變成什麼（真跑輸出，過濾）：

```
$ objdump -d -C --no-show-raw-insn op | sed -n '/<main>:/,/^$/p' | grep operator
    call ... <std::__cxx11::basic_string<...> std::operator+<char, ...>(
             std::__cxx11::basic_string<...> const&,
             std::__cxx11::basic_string<...> const&)>
```

`a + b` 編譯成一次 `std::operator+` 的函式呼叫，傳兩個 `string const&` 進去，回傳一個新 `string`。**這一行加號背後是一次函式呼叫加一次堆積記憶體配置**，跟 C 的整數加法完全不是一回事。

**這對讀碼的意義**：

1. **看到運算子作用在非內建型別上，要當它是函式呼叫。** `a + b` 兩邊是 `int`？那是加法。兩邊是 `std::string`/自訂型別？那是 `operator+`，去找它的定義。判斷關鍵是**運算元的型別**。

2. **運算子可能有你想不到的成本。** 上面那個 `+` 配一次記憶體配置。迴圈裡 `result = result + item;` 對 string 是 O(n²) 的災難（每次都配新記憶體、複製全部）。讀效能敏感的 C++，運算子重載是頭號嫌疑。

3. **運算子可能有你想不到的語義。** 有些 library 用 `<<` 做「串流輸出」（`cout << x`）、用 `[]` 做「查表兼插入」（`map[key]` 不存在會**建一個**！）、用 `->` 做智慧指標解引用。看到運算子，先確認它在這個型別上是什麼意思，別套內建直覺。

> `printf` 那行也順帶示範一個優化：我們的 `op.cpp` 裡寫 `printf("%s\n", c.c_str())`，objdump 顯示它被編譯成 `call puts@plt`——編譯器發現「印一個字串加換行」等價於 `puts`，直接換掉了。這是 Ch 28「編譯器做了什麼」的預告：連 C 標準函式呼叫都可能被換成別的。

## ADL、隱式轉換、auto：三個「函式從哪冒出來 / 型別是什麼」的謎

這三個特性都在製造「你看 source 說不出這裡實際發生什麼」的困惑。

**ADL（Argument-Dependent Lookup，實引數依賴查找）**：C++ 找函式時，除了當前 namespace，還會去**引數的型別所在的 namespace** 找。所以 `swap(a, b)` 這行，如果 `a`/`b` 是 `std::vector`，即使你沒寫 `std::`、也沒 `using`，編譯器照樣會找到 `std::swap`——因為引數型別在 `std` namespace。**讀碼困惑**：你在當前檔案、當前 namespace 遍尋不著這個函式的定義，因為它在**引數型別的 namespace** 裡。看到「呼叫一個找不到定義的自由函式，而引數是某 library 的型別」，就去那個 library 的 namespace 找——這就是 ADL。

**隱式轉換（implicit conversion）**：C++ 會自動插入型別轉換。一個 `class Foo { Foo(int); };` 讓 `Foo f = 42;` 合法（`int` 隱式轉成 `Foo`）；函式 `void g(Foo)` 讓 `g(42)` 合法。**讀碼困惑**：你看到 `g(42)` 傳的是 int，但 `g` 收的是 `Foo`——中間偷偷建了個臨時 `Foo` 物件（又是隱形的建構/解構）。看到「傳進去的型別跟參數型別對不上卻能編譯」，懷疑隱式轉換，去看目標型別有沒有「單引數建構子」或 `operator TargetType()`。現代 C++ 用 `explicit` 關鍵字擋掉這種轉換就是為了可讀性。

**`auto` / `decltype`**：`auto x = foo(a, b);` 完全不告訴你 `x` 是什麼型別。方便寫，難讀。**求生工具是 gdb**（真跑）：

```
$ gdb -q ./tmpl
(gdb) ptype a          # a 是前面 Guard a("a");
type = struct Guard {
    const char *name;
  public:
    Guard(const char *);
    ~Guard();
}
(gdb) whatis a
type = Guard
```

`whatis` 給你「這個變數是什麼型別」，`ptype` 給你「這型別展開長什麼樣」（成員、方法都列出來）。讀到一堆 `auto` 看不出型別時，編譯它、gdb 進去 `whatis`/`ptype`，真實型別立刻現形。另一招是**故意寫錯誘導編譯器報型別**：把 `auto x = foo();` 改成 `int x = foo();`，如果型別不對編譯器會噴「cannot convert `真實型別` to int」——那個「真實型別」就是答案。線上工具 [cppinsights.io](https://cppinsights.io) 更直接：貼進去它把 `auto`、range-based for、lambda、template 全展開成明文 C++。

## CRTP 與 lambda 捕獲：兩個一定會遇到的慣用法

**CRTP（Curiously Recurring Template Pattern，奇特遞迴模板模式）**：一個 class 繼承一個「以自己為 template 參數」的 base：`class Derived : public Base<Derived>`。看到這個「自己把自己傳給 base」的怪圈別慌——它是 C++ 做**靜態多型（static polymorphism）**的手法：base 想呼叫 derived 的方法時，因為它知道 derived 的型別（透過 template 參數），可以 `static_cast<Derived*>(this)->method()`，達到虛擬函式的效果但**沒有虛擬函式的執行期開銷**（沒有 vtable、沒有間接呼叫）。讀到 CRTP，心裡翻譯成「這是編譯期綁定版的虛擬函式」，然後去 base 裡找那些 `static_cast<Derived*>(this)`。

**Lambda 捕獲**：`[capture](args){ body }` 這個東西，編譯器把它變成一個**匿名 class**（有 `operator()` 的仿函式）。捕獲清單 `[...]` 決定這個匿名 class 有哪些成員：

- `[x]` 按值捕獲——匿名物件**複製**一份 `x` 存起來。
- `[&x]` 按參考捕獲——匿名物件存 `x` 的**參考**。⚠️ 讀碼警訊：如果這 lambda 活得比 `x` 久（存進 callback、丟到別的執行緒），`x` 死了參考就懸空——**dangling reference**，經典 bug。看到 `[&]` 捕獲 + lambda 被存起來延遲執行，警鈴要響。
- `[=]` 全部按值、`[&]` 全部按參考、`[this]` 捕獲當前物件指標。

讀 lambda 的關鍵永遠是**先看捕獲清單**：它告訴你這個「函式」偷偷帶了哪些狀態、以什麼方式帶（複製還是參考），而生命週期問題全藏在這裡。cppinsights.io 會把 lambda 展開成它背後的匿名 class，一看就懂。

## 對比與取捨

讀 C++ 時，每個特性都在「表達力 ↔ 可讀性」之間做交易，你要知道它拿走了什麼：

| C++ 特性 | 給了什麼 | 讀碼時拿走了什麼 | 怎麼把它挖出來 |
|---|---|---|---|
| template | 泛型、零成本抽象 | 「這 code 存在嗎/是哪一份」的確定性 | `nm -C` 列實例、gdb `ptype` |
| RAII/解構 | 自動資源管理、例外安全 | 控制流可見性（`}` 隱式跑 code） | objdump 看真實 dtor call、腦補 |
| operator overload | 自然語法（`a+b`、`m[k]`） | 「這是運算還是函式呼叫」的分辨 | 看運算元型別、objdump |
| 隱式轉換 | 少寫轉型 | 「型別對不上卻能編譯」的謎 | 找單引數建構子 / `operator T()` |
| `auto` | 少打字、泛型 | 型別可讀性 | gdb `whatis`/`ptype`、誘導報錯、cppinsights |
| ADL | STL 泛型演算法能運作 | 「這函式定義在哪」的可搜性 | 去引數型別的 namespace 找 |
| lambda | 就地寫小函式 | 捕獲的狀態與生命週期 | 先讀捕獲清單、cppinsights 展開 |

一個總的取捨：**C++ 用「編譯期做更多事」換「執行期更快、語法更自然」，代價是「讀者要在腦中模擬編譯器」。** 這門課讀 C 時你信任 source，讀 C++ 時你要多養一層「編譯器視角」。值不值得看場景，但無論如何，讀 C++ 就是得會這一層。

## 踩雷集錦

1. **錯誤直覺：「這一行沒有函式呼叫，所以很快 / 沒副作用」→ 正確：C++ 的 `a+b`、`}`、`m[k]`、隱式轉換都可能藏著函式呼叫、記憶體配置、解構副作用。** 讀 C++ 不能用「所見即所得」的 C 直覺估成本與行為。運算元型別是自訂類別，就當它有隱形 code。

2. **錯誤直覺：「找不到 `unlock()`/`free()`/`close()`，所以這裡漏了釋放」→ 正確：釋放很可能在某個 RAII 物件的解構裡，藏在 scope 結尾的 `}`。** 找不到成對的釋放，第一個懷疑 RAII。反過來，這也是 RAII 的價值——它保證釋放，包括例外路徑。

3. **錯誤直覺：「template error 三百行，一定是我哪裡大錯特錯」→ 正確：長度跟嚴重度無關，常常只是少個 `const` 或型別差一點。** 按「從後往前 / 找 `required from here` / 折疊長型別名 / 搜關鍵字」四步定位，別被長度嚇退。

4. **錯誤直覺：「`[&]` 捕獲很方便，反正都能抓到」→ 正確：按參考捕獲的 lambda 一旦活得比被捕獲的變數久，就是懸空參考。** 看到 `[&]` 捕獲又把 lambda 存起來延遲執行（callback、thread、std::function 成員），是高危險訊號。

5. **錯誤直覺：「我在這個 namespace/檔案找不到這個函式，所以它是編譯器內建或我看漏了」→ 正確：可能是 ADL 從引數型別的 namespace 找來的。** 呼叫一個查無定義的自由函式、而引數是某 library 型別時，去那個 library 的 namespace 找。

6. **錯誤直覺：「`auto` 讓 code 更乾淨，讀起來更好」→ 對寫的人是，對讀的人常常相反。** `auto` 把型別藏起來，讀者要靠工具反推。遇到滿是 `auto` 的陌生 code，別硬猜型別，直接 gdb `whatis` 或 cppinsights。

## 進階：再往深一層

- **cppinsights.io 是讀 C++ 的 X 光機**：它把你貼進去的 C++ 展開成「編譯器眼中的等價明文 C++」——range-based for 變成 iterator 迴圈、lambda 變成匿名 class、`auto` 填上真實型別、隱式的建構/解構全部顯式化、template 實例化攤開。讀任何一段讓你困惑的現代 C++，貼進去看展開，比讀十遍 source 有效。本地版是 clang 的 AST dump（`clang -Xclang -ast-dump`）或 `-fdump-tree-*`，但 cppinsights 的輸出最好讀。

- **name mangling 與 demangle**：C++ 把函式名、參數型別、namespace 全部編碼進符號名（mangling），這樣才能支援重載（同名不同參數）。`nm` 看到的 `_ZN4Guard4GuardEPKc` 是 mangled，`nm -C` 或 `c++filt` 還原成 `Guard::Guard(char const*)`。讀 C++ binary、看連結錯誤（`undefined reference to '_ZN...'`）時，`c++filt` 是必備——把天書變回人話。

- **零成本抽象的真相**：C++ 標榜「你不用的東西不付錢，用的東西手寫也不會更快」。template、`std::sort`、RAII 在 `-O2` 下常常真的編譯成跟手寫 C 一樣好甚至更好的 code（`std::sort` 因為型別已知、能 inline 比較器，常勝過 C 的 `qsort`）。但「零成本」是**執行期**零成本，**編譯期**（編譯變慢、error 變長）和**認知**（讀碼變難）的成本是真實存在的。讀 C++ 效能問題，用 Ch 28 的 objdump 對照，別假設抽象一定有 runtime 開銷、也別假設一定沒有——實測。

- **例外與 RAII 的深水區**：前面 objdump 那個「第三個解構」揭示了例外處理生成的隱形清理路徑。完整的 C++ 例外機制（`.eh_frame`、unwinding tables、`noexcept` 對生成 code 的影響）是一個大坑。讀「這函式例外安全嗎」時要知道：每個可能丟例外的呼叫點之後，編譯器都維護著「到這裡為止有哪些物件已建構、需要解構」的資訊。接你的 binary_exploitation / gdb 課看 unwinding 底層。

## 動手練習

前置：`cd ~/reading_code_lab && git clone --depth 1 https://github.com/fmtlib/fmt`（若還沒 clone），或用本章的自寫小範例。

1. **看 template 實例化**：寫一個 `template<typename T> T add(T,T)`，用 `int`、`double`、`long` 三種型別呼叫，`g++ -g` 編譯後 `nm -C a.out | grep add`。確認產生了三份符號。改成只用一種型別，看符號變一份。

2. **看 RAII 的隱形解構**：用本章的 `Guard` 範例，`g++ -O0 -g tmpl.cpp && ./tmpl` 看解構的 LIFO 順序，再 `objdump -d -C tmpl | sed -n '/<main>:/,/^$/p'`，數 ctor 和 dtor 的 call 各幾次，找出那個例外路徑的多餘 dtor + `_Unwind_Resume`。

3. **看 operator+ 是函式呼叫**：寫 `std::string c = a + b;`，`objdump -d -C` 找到 `std::operator+` 的 call。體會「一個加號是一次函式呼叫加一次記憶體配置」。

4. **用 gdb 反推 auto 型別**：寫幾行含 `auto` 的 code（`auto v = std::vector<int>{1,2,3}; auto it = v.begin();`），`g++ -g` 後 gdb `whatis v`、`whatis it`、`ptype it`。看它把 `auto` 還原成什麼。

5. **cppinsights 展開**：把上一題的 code 貼到 [cppinsights.io](https://cppinsights.io)，看它怎麼把 `auto` 填型別、把 range-based for（如果你寫了）展開成 iterator 迴圈。跟 gdb 的答案對照。

6. **讀 fmt 的一個 error**：故意在 fmt 用錯（例如 `fmt::format("{}", some_non_formattable_type)`），編譯，讀那串 error，找到 fmt 用 `static_assert` 給的人話錯誤訊息。體會好 library 怎麼馴服 template error。

## 本章重點整理

- C++ 難讀的根本：大量 code 不在 source 表面——template 要實例化才存在、`}` 隱式跑解構、`a+b` 可能是函式呼叫、`auto` 藏型別、ADL 讓函式從別處冒出。
- 讀 C++ 的核心技巧：認出「觸發隱形 code 的語法信號」，再用工具讓它現形——`nm -C`（實例）、`objdump -d -C`（真實 call）、gdb `whatis`/`ptype`（型別）、cppinsights.io（展開明文）。
- template：一份 source、N 種型別 = N 份 code；error 訊息從後往前讀、找 `required from here`、折疊長型別名、搜關鍵字。
- RAII：看到區域物件就在 scope 結尾腦補解構；找不到成對釋放第一個懷疑 RAII；例外清理路徑是隱形的。
- operator overload / 隱式轉換 / auto / ADL / lambda 捕獲：每個都在製造「這裡實際發生什麼看不出來」，各有對應的挖掘工具。
- 總取捨：C++ 用「編譯期做更多」換「執行期快、語法自然」，代價是讀者要在腦中模擬編譯器。

## 自我檢核

- [ ] 看到一行 `std::string c = a + b;`，我能不能說出背後至少有一次函式呼叫和一次記憶體配置，並用 objdump 驗證？
- [ ] 給我一個含區域 RAII 物件的函式，我能不能在每個 `}` 標出「這裡隱式解構了誰、順序是什麼」？
- [ ] 噴出三百行 template error，我有沒有一套定位病根的步驟（不是從頭硬讀）？
- [ ] 遇到滿是 `auto` 的陌生 code，我知道用 gdb `whatis`/`ptype` 或 cppinsights 反推型別，而不是硬猜？
- [ ] 我能不能解釋 ADL 為什麼會讓「找不到函式定義」，以及該去哪找？
- [ ] 看到 `[&]` 捕獲又被存起來延遲執行的 lambda，我會不會警覺懸空參考？

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、前提。

- **[C++ Insights（cppinsights.io）](https://cppinsights.io/)**
  - **讀哪裡**：直接把你困惑的 C++ 片段貼進去看展開。先試 range-based for、lambda、`auto`、一個簡單 template 這四類。
  - **學到什麼**：把「編譯器眼中的等價明文 C++」視覺化——所有隱形的建構/解構/型別/實例化全部顯式化。這是讀現代 C++ 最有效的單一工具。
  - **前提**：能讀基本 C++；本章的「隱形 code」概念會讓你更知道要看什麼。

- **[cppreference](https://en.cppreference.com/)**
  - **讀哪裡**：不是從頭讀，是當精準字典查。讀 C++ 遇到不認識的 STL 型別/演算法/語言特性，查它的頁面，看 "Notes" 和範例。特別推薦查 `std::move`、`std::forward`、value categories、ADL 這幾個坑頁。
  - **學到什麼**：每個特性的精確語義。C++ 的魔鬼在細節，cppreference 是最權威的細節來源（比多數教學可靠）。
  - **前提**：中階 C++；查特定主題時對照本章。

- **《Effective Modern C++》— Scott Meyers（O'Reilly, 2014）**
  - **讀哪裡**：讀碼導向的話，優先看 Item 1-6（型別推導、`auto`）、Item 5-6（`auto` 的坑）、Item 31-34（lambda 與捕獲）。
  - **學到什麼**：`auto`/`decltype` 到底怎麼推導型別、lambda 捕獲的生命週期陷阱——正是本章「反推型別」與「lambda 求生」的深度版。
  - **前提**：C++11/14 基礎；配合本章讀 auto 與 lambda 兩節最互補。

- **[Itanium C++ ABI: Name Mangling](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling)**
  - **讀哪裡**：概觀與幾個 mangling 範例即可，不用讀完（很長）。重點理解「符號名怎麼編碼型別與 namespace」。
  - **學到什麼**：`nm`/連結錯誤裡那些 `_ZN...` 天書的解讀規則，以及為什麼 `c++filt` 能還原。讀 C++ binary、除連結錯誤時的底層知識。
  - **前提**：懂 C++ 重載與 namespace；讀本章的 `nm -C` 範例後看最有感。

讀懂了 C++ 把 code 藏進編譯器生成物的把戲，你已經拿到讀高階語言的一把鑰匙。下一章往反方向走——不是往「更抽象」，而是往「更貼近機器」的系統程式與 kernel 慣例：`container_of` 從成員指標回推整個結構、侵入式鏈表、`goto` 清理、`likely`/`unlikely`。這些慣用法在應用層 code 很少見，但你要讀 kernel、driver、高效能 C 就非懂不可。

→ [Ch 27 讀懂 kernel/系統程式慣例](./27-reading-kernel-idioms.md)
