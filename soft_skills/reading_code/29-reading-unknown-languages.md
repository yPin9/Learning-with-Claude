# Ch 29 — 讀你不會的語言

> **目標**：拆穿「不會這個語言就讀不了它的 code」這個迷思。你要學會的是一套 pattern 遷移法：進到任何陌生語言，先用 80/20 抓五件事，把你腦裡既有的程式概念骨架套上去，再靠命名、型別簽章、測試補語法的縫。本章拿 Go（fzf）當白老鼠，示範怎麼在 20 分鐘內看懂一個你「沒學過」的語言寫的函式在幹嘛。

## 為什麼這章存在

逆向工程師從不會說「這是 ARM64 我不會所以我讀不了」。他換一本 ISA 手冊，認出 `bl`（branch-and-link，就是 call）、`ret`、`ldr`/`str`（load/store），十分鐘後就在追 data flow 了。他認的是**概念**——呼叫、回傳、記憶體讀寫——不是某個助記符的拼法。

讀陌生語言的 source 是同一回事，而且比讀陌生 ISA **簡單得多**：高階語言的概念密度更高、命名更友善、還有型別和測試當地圖。真正卡住你的不是語言本身，是你把「我沒學過 Go/Rust/Zig」誤當成「我讀不懂這段 code」。這兩件事差得很遠。

我們要建立的核心信念是：**每個通用程式語言都有相同的概念骨架**。差別只在語法皮膚。你認骨架，皮膚查一下就好。

## 先給直覺：所有語言共享同一副骨架

不管 C、Go、Rust、Python、Zig、Swift，一段實用程式無非在做這幾件事：

```
   概念骨架（跨語言不變）            你要在陌生語言裡找的「那一件事」
 ┌────────────────────────┐
 │ 1. 進入點 / 執行從哪開始 │  → main？某個 handler？某個 export？
 │ 2. 定義函式與型別       │  → 怎麼宣告 function、struct/class、method
 │ 3. 控制流（分支、迴圈）  │  → if / for / while / match 長什麼樣
 │ 4. 資料結構與集合操作   │  → array/slice/map/list 怎麼建、怎麼走訪
 │ 5. 錯誤處理             │  → exception？回傳 error？Option/Result？
 │ 6. 模組與相依（import） │  → 怎麼分檔、怎麼引入別的套件
 │ 7. 記憶體 / 資源管理    │  → GC？手動？RAII？ownership？defer？
 └────────────────────────┘
```

進一個陌生語言，你不是「從頭學這個語言」，而是**拿這張表去問七個問題**，各問題花不到兩分鐘查語法，你就有了讀懂 90% 一般業務邏輯的地基。剩下 10% 的語言獨門特性（Rust 的 lifetime、Go 的 channel、C++ 的 template）遇到再補，而且多數函式根本用不到那些。

這就是 **80/20**：五到七件事撐起絕大多數 code。我們接下來在真的 Go code 上跑一遍。

## 底層機制：pattern 遷移法怎麼運作

你腦裡對「程式」有一套抽象模型（cognitive schema）——你知道函式會拿參數回傳值、迴圈會重複、集合可以被索引。讀陌生語言時，你的大腦在做的是 **pattern matching**：把眼前這串沒看過的符號，對應到腦裡已有的抽象。

- 看到 `func (h *History) next() string {` ——你不需要「學 Go」。你需要辨識：這是一個**綁在某型別上的方法**（method with receiver），拿一個 `*History`，回傳一個 `string`。C 的世界裡它就是 `char *History_next(History *h)`。認出來了，schema 匹配成功。
- 看到 `if err != nil { return nil, err }` ——這是**回傳式錯誤處理**（error-as-value），對應 C 的「回傳 -1 並設 errno」或「回傳 NULL」。認出來了。

pattern 遷移法的三個支柱，補上語法查不到的語義：

1. **命名**：函式叫 `append`、`current`、`previous`，變數叫 `cursor`、`maxSize`——好命名直接告訴你意圖，跨語言通用。
2. **型別簽章**：`(path string, maxSize int) (*History, error)` 這一行，不用讀函式本體你就知道它「吃一個路徑和上限、吐一個 History 或錯誤」。型別是壓縮過的文件。
3. **測試**：`xxx_test.go`、`test_*.py`、`#[test]` ——測試是**可執行的規格**，它用具體輸入輸出告訴你這函式該做什麼，比任何註解都可信（Ch 30 會強調：註解會騙人，測試不會）。

通用工具在這裡幫大忙：**tree-sitter**（Ch 15）有幾乎所有主流語言的 grammar，`rg` 的結構化查詢和 **LSP**（Ch 13，Go 用 gopls、Rust 用 rust-analyzer）對任何語言給你一樣的「跳定義／找引用」。你的工具鏈不換，只換 grammar/server。

### 五件事的跨語言對照表

把「同一個概念在不同語言長什麼樣」攤成一張表，你會發現皮膚換了、骨架沒變。這張表值得你進新語言時自己填一遍：

| 概念 | C | Go | Rust | Python |
|---|---|---|---|---|
| 定義函式 | `int f(int x){}` | `func f(x int) int {}` | `fn f(x: i32) -> i32 {}` | `def f(x):` |
| 定義聚合型別 | `struct S {…}` | `type S struct {…}` | `struct S {…}` | `class S:` / `@dataclass` |
| 綁在型別上的方法 | 手傳第一參數 | `func (s *S) m()` | `impl S { fn m(&self) }` | `def m(self):` |
| 迴圈 | `for(;;)` / `while` | `for` (唯一) | `for x in …` / `loop` | `for x in …` / `while` |
| 動態陣列 | `T*` + `len` 手管 | `[]T` (slice) | `Vec<T>` | `list` |
| 雜湊表 | 自己寫 / uthash | `map[K]V` | `HashMap<K,V>` | `dict` |
| 錯誤處理 | 回傳 -1 / NULL + errno | `(T, error)` + `if err != nil` | `Result<T,E>` + `?` | `raise` / `try…except` |
| 「沒有值」 | `NULL` | `nil` | `Option<T>` (`None`) | `None` |
| import | `#include` | `import (…)` | `use …` | `import …` / `from … import` |
| 記憶體釋放 | 手動 `free` | GC | ownership/drop | GC |

讀表的方法：**橫著讀是同一個概念的不同拼法**（你認的就是最左欄的概念），**豎著讀是一個語言的性格**（Rust 那欄的 `Result`/`Option`/ownership 就是它跟別人最不一樣、最需要你停下補課的地方）。錯誤處理那一列尤其關鍵——C/Go 的「回傳值帶錯誤」、Rust 的 `Result`、Python 的 exception，是三種不同的 pattern，讀碼時把每個語言歸到正確那一類，你就不會把 Go 的 `if err != nil` 誤讀成「普通分支」，也不會在 Rust code 裡找不到 exception 而困惑。

## 真跑範例：20 分鐘讀懂一段 Go（你沒學過 Go）

環境：`~/reading_code_lab/fzf`，junegunn 的 fzf，Go 寫的，26785 行（`cloc` 實測）。假設你這輩子沒寫過一行 Go。目標函式：`src/history.go` 裡的 `History` 型別和它的方法。

### 第 0 分鐘：先問「這語言長怎樣」——五個 grep

不打開任何教學，先用 grep 對整個 codebase 做語言的 80/20 偵察（以下皆真跑輸出）：

```
$ cd ~/reading_code_lab/fzf
$ rg -n "^func " src/*.go | wc -l          # 函式怎麼定義？
515
$ rg "if err != nil" src/*.go | wc -l      # 錯誤怎麼處理？
122
$ rg -n "^type " src/history.go src/cache.go
src/cache.go:6:type ChunkBitmap [chunkBitWords]uint64
src/cache.go:9:type queryCache map[string]ChunkBitmap
src/cache.go:12:type ChunkCache struct {
src/history.go:10:type History struct {
$ rg -n "^import|^\s+\"" src/history.go     # 相依怎麼引入？
3:import (
4:	"errors"
5:	"os"
6:	"strings"
```

三分鐘不到，五個問題答掉四個：

- **函式**用 `func` 開頭（515 個，這語言就是這樣宣告函式）。
- **型別**用 `type X struct {…}`（跟 C 的 `struct` 幾乎一樣），也能 `type X map[...]...`（type alias / 具名型別）。
- **錯誤處理**是 `if err != nil`——出現 122 次，這就是 Go 的招牌 idiom：**函式回傳 error 值，呼叫端立刻檢查**。沒有 exception。認出這個，你就讀懂了 Go 一半的控制流。
- **import** 是把字串路徑列在 `import ( … )` 區塊裡。

### 第 3 分鐘：讀型別定義，先建資料模型

```go
// History struct represents input history
type History struct {
	path     string
	lines    []string
	modified map[int]string
	maxSize  int
	cursor   int
}
```

你沒學過 Go，但這裡沒有一個欄位是猜不出來的：

- `path string`：一個字串路徑。（Go 把型別寫在名字**後面**，跟 C 相反——這是你需要吸收的第一個語法差異，兩秒鐘的事。）
- `lines []string`：`[]string` 是「string 的 slice」。slice ≈ 動態陣列（C 裡你會用 `char **lines` + `int count`）。所以這是**一行一個字串的歷史清單**。
- `modified map[int]string`：`map[int]string` 是「key 是 int、value 是 string 的雜湊表」。C 裡沒內建，你得自己寫或用 uthash。命名 `modified` 暗示：某些「被改過但還沒寫回檔案」的行，用 index → 新內容 記著。
- `maxSize int`、`cursor int`：上限，跟一個游標位置。

**不讀任何一個方法，光型別你已經有心智模型了**：這是一個「命令列輸入歷史」，存一串行、記一個游標、暫存未落盤的修改、有大小上限。這正是 shell 上按上下鍵翻歷史的那個東西。型別簽章是壓縮的文件，這句話你現在信了。

### 第 8 分鐘：挑一個方法讀本體，驗證心智模型

挑 `next()`（游標往後），因為它短：

```go
func (h *History) next() string {
	if h.cursor < len(h.lines)-1 {
		h.cursor++
	}
	return h.current()
}
```

逐行對應你腦裡的 C：

- `func (h *History) next() string` ——`(h *History)` 是 **receiver**，即「這個方法綁在 `*History` 上」，`h` 是 receiver 變數（等同 C++ 的 `this`、C 裡你手傳的第一個參數）。回傳 `string`。翻成 C：`char *History_next(History *h)`。
- `if h.cursor < len(h.lines)-1` ——`len()` 是內建，取 slice 長度。條件是「游標還沒到最後一行」。
- `h.cursor++` ——游標前進。（`.` 存取欄位，跟 C 一樣；Go 沒有 `->`，指標也用 `.`，這是第二個要吸收的語法差異。）
- `return h.current()` ——回傳目前游標指的內容，`current()` 是同型別另一個方法。

心智模型驗證通過：`next()` 就是「游標往前一格（除非已到底），回傳新位置的內容」。你完全沒查 Go 教學，靠 pattern 遷移就讀懂了。順帶把 `previous()` 也讀了——對稱的，游標往回、下界是 0：

```go
func (h *History) previous() string {
	if h.cursor > 0 {
		h.cursor--
	}
	return h.current()
}
```

### 第 14 分鐘：讀一個「有錯誤處理」的方法，認 Go 的招牌 idiom

```go
func (h *History) append(line string) error {
	// We don't append empty lines
	if len(line) == 0 {
		return nil
	}

	lines := append(h.lines[:len(h.lines)-1], line)
	if len(lines) > h.maxSize {
		lines = lines[len(lines)-h.maxSize:]
	}
	h.lines = append(lines, "")
	return os.WriteFile(h.path, []byte(strings.Join(h.lines, "\n")), 0600)
}
```

三個新語法點，全部能靠 pattern 遷移吃掉：

- `return error`：這方法回傳 `error`。`return nil` 代表「沒錯誤，成功」（`nil` = C 的 NULL）。這就是前面 grep 到 122 次的那個 idiom 的另一半——**函式把錯誤當回傳值往外傳**。
- `h.lines[:len(h.lines)-1]`：**slice 切片**，`a[:n]` 取前 n 個元素（去掉最後一格）。`lines[len(lines)-h.maxSize:]` 取後 `maxSize` 個。這是 Go/Python/Rust 共通的切片語法，C 沒有，但概念你懂：子陣列。
- `os.WriteFile(h.path, []byte(...), 0600)`：把 join 好的字串（`strings.Join` = 用 `\n` 接起來）以權限 `0600` 寫檔。`0600` 你一眼認得——這是 Unix 檔案權限，跨語言不變的知識。

讀完你知道 `append`：跳過空行 → 把新行接到清單（覆蓋掉尾端那個哨兵空字串）→ 超過上限就從頭截斷 → 補回尾端空字串 → 整包寫回檔案。**這是一個帶落盤的環形歷史緩衝**。

### 第 20 分鐘：結案

20 分鐘，零 Go 教學，你讀懂了 `History` 的資料模型和五個方法在幹嘛，還順手辨識了 Go 的三個核心 idiom（`func`/receiver、`if err != nil`、slice）。你對 Go 的「認識」現在足以讀這個 codebase 裡**任何**業務邏輯函式。這就是 pattern 遷移法的投資報酬率。

> 邊界與失敗案例要誠實講：pattern 遷移對「業務邏輯」極有效，對**語言的獨門機制**會踩空。你若在 fzf 裡撞到 `go func() {...}`（啟動 goroutine）、`ch <- v` / `<-ch`（channel 收發），pattern matching 會失敗，因為你腦裡的 C schema 沒有「輕量並發 + CSP 通道」這個 pattern。**這時要停下來，花五分鐘專門補這一個概念**，別硬套。認得出「我撞到語言獨門特性了」本身就是一種技能——它讓你知道何時該從「遷移」切換到「查資料」。

## 換一個更陌生的語言：Rust（同一套方法）

有人會說「Go 太像 C 了才讀得懂」。那換 Rust——它的記憶體模型（ownership/borrow）和你腦裡的 C schema 差最遠，是 pattern 遷移的壓力測試。用 sharkdp 的 hexyl（Rust 寫的十六進位傾印工具，`~/reading_code_lab/hexyl`，1940 行）示範。看 `src/lib.rs` 裡一個把 byte 分類的方法（真跑取出）：

```rust
fn category(self) -> ByteCategory {
    if self.0 == 0x00 {
        ByteCategory::Null
    } else if self.0.is_ascii_graphic() {
        ByteCategory::AsciiPrintable
    } else if self.0.is_ascii_whitespace() {
        ByteCategory::AsciiWhitespace
    } else if self.0.is_ascii() {
        ByteCategory::AsciiOther
    } else {
        ByteCategory::NonAscii
    }
}
```

pattern 遷移全部命中，即使你沒寫過 Rust：

- `fn category(self) -> ByteCategory`：`fn` 是函式關鍵字（對應 Go 的 `func`、C 的宣告）；`self` 是 receiver（跟 Go `(h *History)` 同概念，只是 Rust 把它放參數列第一位）；回傳 `ByteCategory`（一個 enum）。
- `if … else if … else`：控制流跟 C 幾乎一模一樣。
- `self.0`：`.0` 是存取一個 tuple struct 的第 0 個欄位（`struct Byte(u8)` 這種包一個值的型別）。這是 Rust 語法皮膚，兩秒鐘吸收。
- `ByteCategory::Null`：`::` 是路徑分隔（存取 enum 的變體），對應 C++ 的 `::`、Go 的 `.`。
- **注意：Rust 的 `if/else` 區塊是「運算式」**——最後一個沒加分號的值就是回傳值，所以整個 `if…else` 鏈的值直接被 `category` 回傳。這是 Rust 一個要吸收的語義（不只 C 的「陳述句」），但你靠「函式該回傳一個 `ByteCategory`、每個分支剛好落一個變體」也能推出來。

讀懂了：`category` 把一個 byte 分成 Null / 可印 ASCII / ASCII 空白 / 其他 ASCII / 非 ASCII 五類。零 Rust 教學。

再看它的兄弟 `color`，示範 Rust 的招牌控制流 `match`（你 C schema 裡的 `switch` 加強版）：

```rust
fn color(self, color_scheme: ColorScheme) -> &'static [u8] {
    use crate::ByteCategory::*;
    match color_scheme {
        ColorScheme::Default => match self.category() {
            Null => COLOR_NULL.as_bytes(),
            AsciiPrintable => COLOR_ASCII_PRINTABLE.as_bytes(),
            ...
        },
        ...
    }
}
```

`match X { pattern => value, … }` 就是「對 X 做 pattern matching，命中哪個分支就取哪個值」——概念上是 `switch`，但更強（可以配對結構、綁值）。你不需要「學 Rust match」才讀懂這段：命名（`Null`、`AsciiPrintable`）+ 結構（每個 category 對到一個顏色常數）已經告訴你「這函式按 byte 分類挑對應顏色」。

**兩個要停下來補課的 Rust 獨門特性**（pattern 遷移會踩空的地方）：

- `&'static [u8]` 裡的 `'static` 是 **lifetime 標註**——你的 C/Go schema 沒有這個。它說「這個回傳的 byte slice 活得跟整個程式一樣久」。第一次看到 `'a`、`'static` 別硬套，這是 Rust 專屬的 ownership 機制，值得專門花五分鐘補。
- `input.rs` 裡的 `fn read(&mut self, buf: &mut [u8]) -> io::Result<usize>`：`io::Result<usize>` 是 Rust 的錯誤處理——**`Result<T, E>` 是「成功帶 T 或失敗帶 E」的兩態值**，對應 Go 的 `(usize, error)` 回傳對，但包成一個型別。搭配的 `?` 運算子（`let n = reader.read(buf)?;`）是「出錯就提早 return 這個錯」的語法糖。這是 Rust 版的「錯誤即值」——跟 Go 的 `if err != nil` 是**同一個 pattern 的不同皮膚**，認出這個對應關係，你就把 Rust 的錯誤處理接上了你既有的 schema。

結論：Go 和 Rust 語法差很多、記憶體模型差更多，但**你用的是同一套方法**——先問五件事、讀型別簽章建模型、認出「錯誤即值」這種跨語言 pattern、只在撞到獨門特性（lifetime、ownership）時停下補課。這就是 pattern 遷移法的普適性。

## 對比與取捨

| 策略 | 適用 | 成本 | 風險 |
|---|---|---|---|
| **pattern 遷移法**（本章） | 讀懂陌生語言的一般業務邏輯 | 極低，幾分鐘上手 | 撞到語言獨門特性會誤解 |
| **系統性學這個語言** | 你要**寫**這個語言、或長期維護 | 高，數天到數週 | 為了讀一個函式殺雞用牛刀 |
| **只靠 LSP 跳轉不讀語法** | 追型別、找定義 | 低 | 不理解 idiom 會誤判控制流（如把 `if err != nil` 當成普通分支） |
| **餵 AI 翻譯成你會的語言**（Ch 20） | 快速得到大意 | 低 | AI 會漏語言特有語義、可能編故事，要用真跑驗證 |

**實戰選擇**：讀懂「這函式在幹嘛」用 pattern 遷移，撞到獨門特性就針對性補那一個概念。除非你要開始 commit，否則別系統性學整個語言——為了讀而學整套語言是常見的時間黑洞。

## 踩雷集錦

1. **錯誤直覺：「我沒學過這語言，讀了也是白讀」。** → 正確認識：你讀的是概念骨架，不是語法皮膚。業務邏輯的 90% 跨語言同構，你早就會了。真正需要「學」的只有那 10% 獨門特性，且多數函式碰不到。
2. **錯誤直覺：把語法差異當成語義差異。** Go 型別寫在名字後面（`x int` 不是 `int x`）、指標存取也用 `.` 不用 `->`、沒有分號結尾——這些是**皮膚**，兩秒鐘吸收，別讓它們嚇退你。真正要花心思的是 idiom（`if err != nil`、slice、goroutine）背後的**語義**。
3. **錯誤直覺：跳過型別簽章直接讀函式本體。** 型別簽章是壓縮過的文件。`(path string, maxSize int) (*History, error)` 這一行給你的資訊量，抵得過讀十行本體。先讀簽章建模型，再讀本體驗證——順序反了會事倍功半。
4. **不信命名結果硬猜，或太信命名不驗證。** 陌生語言裡好命名是你最快的線索（`cursor`、`maxSize` 直接跨語言溝通），該用；但（見 Ch 30）命名也會騙人，關鍵函式要拿測試或真跑驗證，別把「名字看起來對」當成「行為確實對」。
5. **撞到獨門特性還硬套 C 心智模型。** Rust 的 `?` 運算子、`match`、ownership，Go 的 channel、`defer`，C++ 的 template/RAII——這些沒有 C 對應，硬套會產生自信的誤解。認出「這是我 schema 裡沒有的 pattern」，停下來補，是專業讀者的分寸。

## 進階：再往深一層

- **把「五件事」做成 checklist 卡片**：每進一個新語言，強迫自己填「函式怎麼定義／迴圈怎麼寫／錯誤怎麼處理／模組怎麼組織／記憶體誰管」五格，二十分鐘產出一張該語言的「讀碼速查卡」。這比讀教學快十倍，因為你只抓讀碼需要的部分，跳過寫碼才需要的細節。
- **記憶體模型是最容易誤讀的一格**：C 手動、Go/Java GC、Rust ownership+borrow、C++ RAII。讀 code 時「這塊記憶體誰負責釋放」的答案完全不同，會影響你判斷 UAF（Ch 32）風不風險。讀 Rust 尤其要認 `&`（借用）、`&mut`、`Box`、`Rc`——不懂 ownership 會把安全的 code 讀成有 bug、或把有問題的地方讀成安全。
- **LSP/tree-sitter 是你的語言無關武器**：`gopls`、`rust-analyzer`、`clangd`、`pyright` 給你完全一致的「跳定義／找所有引用／看型別」體驗。你的**方法論不換、鍵位不換，只換後端**。這是 Part 3 那套工具鏈的最大紅利——投資一次，套用到所有語言。
- **AI 當「語言口譯」但要驗證**：把陌生語言片段丟給 LLM 要它「用 C 的話解釋這在幹嘛、標出語言特有語義」很有效（Ch 20），但它會漏 goroutine 這種並發語義、或對 lifetime 編故事。永遠用「真跑一次看輸出」或「讀測試」交叉驗證 AI 的說法。

## 動手練習

1. **對 fzf 填五件事速查卡**：在 `~/reading_code_lab/fzf` 上，用 `rg` 各花兩分鐘回答：函式怎麼定義、迴圈長怎樣（`rg -n "for " src/core.go`）、錯誤怎麼處理、import 怎麼寫、有沒有 `defer`（`rg -n "defer " src/`）。產出一張 Go 速查卡。
2. **20 分鐘讀懂另一個型別**：讀 `src/cache.go` 的 `ChunkCache`。先讀 `type ChunkCache struct` 建模型，再讀它的方法，寫下「這個 cache 快取什麼、key 是什麼」。全程不查 Go 教學。
3. **找語言獨門特性**：`rg -n "go func|<-|chan " src/` 找出 fzf 用 goroutine/channel 的地方。挑一處，這是你 pattern 遷移會踩空的點——花五分鐘專門補「Go channel」這一個概念，然後重讀那段。體會「遷移」與「補課」的切換時機。
4. **換一個語言重來**：clone 一個小 Rust 專案（如 `git clone --depth 1 https://github.com/sharkdp/hexyl`），對它填同一張五件事速查卡。比較 Rust 的錯誤處理（`Result`/`?`）跟 Go 的（`if err != nil`）如何都是「錯誤即值」的變體，而跟 C++ 的 exception 是不同的 pattern。
5. **用測試補語法**：在 fzf 找一個 `_test.go`（如 `src/history_test.go`），讀它的測試案例，看它如何用具體輸入輸出定義 `History` 該有的行為。體會「測試是可執行的規格」。

## 本章重點整理

- 讀陌生語言卡住的不是語言，是你把「沒學過」誤當成「讀不懂」。你認的是概念骨架，不是語法皮膚。
- 每個通用語言共享同一副骨架：進入點、函式/型別定義、控制流、資料結構、錯誤處理、模組、記憶體管理。進新語言就是拿這七件事去問七個問題。
- 80/20：五到七件事撐起 90% 的一般業務邏輯，剩下 10% 是語言獨門特性，遇到再針對性補。
- pattern 遷移法的三支柱補語法的縫：命名（意圖）、型別簽章（壓縮的文件）、測試（可執行的規格）。
- LSP 和 tree-sitter 讓你的工具鏈語言無關：換 grammar/server，方法論不變。
- 分寸：認得出「我撞到 schema 裡沒有的 pattern 了」，該停下補課，別硬套 C 心智模型產生自信的誤解。

## 自我檢核

- [ ] 不看筆記，能不能說出「進一個陌生語言先抓的五到七件事」？
- [ ] 給你一段沒學過的語言的函式簽章，你能不能不讀本體就說出它「吃什麼、吐什麼」？
- [ ] 你能解釋為什麼 Go 的 `if err != nil` 跟 C 的「回傳 -1 設 errno」是同一個 pattern，而跟 C++ exception 是不同 pattern 嗎？
- [ ] 面試官問「給你一個 Rust 專案但你沒寫過 Rust，怎麼讀」，你能不能講出 pattern 遷移法而不是「我先去學 Rust」？
- [ ] 你知道 pattern 遷移法在哪裡會失效（獨門特性），以及怎麼辨識該切換到補課嗎？

## 延伸閱讀

- **《The Programmer's Brain》— Felienne Hermans（Manning, 2021），Ch 2–3。**
  - **讀哪裡**：chunking 與 cognitive schema 兩節。
  - **學到什麼**：本章「pattern 遷移」的認知科學基礎——你讀陌生語言時大腦在做的正是拿既有 schema 去 chunk 陌生輸入。理解機制後你會更刻意地去複用而非重學。
  - **關聯**：直接支撐本章核心論點，也呼應本課 Part 1。

- **[A Tour of Go](https://go.dev/tour/) — Go 官方互動教學。**
  - **讀哪裡**：不用全跑，挑 "Methods and interfaces" 和 "Concurrency" 兩章當**參考手冊**用——當你撞到 receiver 或 channel 時回來查那一節。
  - **學到什麼**：Go 的獨門特性（goroutine、channel、interface）的權威解釋。這正是 pattern 遷移會踩空、需要針對性補課的部分。
  - **關聯**：示範「遇到獨門特性回來查一節」的正確用法，不是從頭學整套。

- **[Rust By Example](https://doc.rust-lang.org/rust-by-example/) — 官方，以可跑範例講語言。**
  - **讀哪裡**：`error_handling`（`Option`/`Result`/`?`）與 `scope`（ownership/borrowing）兩章。
  - **學到什麼**：Rust 兩個最容易讓 C/Go 讀者踩空的 pattern——錯誤處理的三態（`Result`）和記憶體的 ownership。讀懂這兩個，Rust 的一般 code 就對你開放了。
  - **關聯**：對照 Go 的 `if err != nil`，看「錯誤即值」如何在不同語言有不同皮膚。

- **[tree-sitter: List of parsers](https://github.com/tree-sitter/tree-sitter/wiki/List-of-parsers)**
  - **讀哪裡**：掃一遍支援的語言清單，感受它的覆蓋廣度。
  - **學到什麼**：為什麼 tree-sitter 是語言無關讀碼的關鍵基礎設施——幾乎任何語言都有 grammar，你的結構化查詢（Ch 15）到哪都能用。
  - **關聯**：把本章「工具鏈語言無關」的主張落地到具體工具。

搞定了陌生語言，下一個更難的敵人不是你不會的語言，而是你會的語言但別人寫得一團糟——**爛 code 與義大利麵**。那裡命名會騙你、註解會害你、全域變數滿天飛。我們來練防禦性讀法。

→ [Ch 30 讀爛 code / 義大利麵](./30-reading-bad-code.md)
