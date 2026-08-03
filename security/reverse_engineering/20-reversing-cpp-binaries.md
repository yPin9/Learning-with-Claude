# Ch 20 — 逆 C++ binary：vtable / RTTI / name mangling

> **目標**：逆 C++ 編出的 binary——從 name mangling 還原類別方法名、從 vtable 結構重建類別的虛擬函式表、從 RTTI 確認繼承關係、認出 `std::string`/`std::vector` 的記憶體佈局。

> **環境**：WSL2 / Linux x86-64，g++ + objdump + c++filt + readelf。

## 為什麼需要這個？

大量的現代 C++ 應用、遊戲引擎、安全工具、瀏覽器（含 V8、JavaScriptCore）都是 C++ binary。相比 C，C++ binary 多了幾個逆向的門檻：

1. **符號 mangling**：`Dog::speak()` 變成 `_ZN3Dog5speakEv`——不熟悉 Itanium ABI mangling 規則，你只看到一串亂碼。
2. **vtable**：虛擬呼叫是 `call *(%rax)` 這樣的間接跳轉，靜態分析看不到目標是誰。
3. **物件佈局**：繼承時 vtable 指標、父類欄位、子類欄位怎麼排列，影響你讀 struct 的方式。
4. **C++ 標準庫容器**：`std::string` 有 SSO（Small String Optimization），`std::vector` 是三指標，認不出來就以為是一堆無意義指標。

本章建立你讀 C++ binary 的完整工具箱。

## 先建立直覺：C++ 物件的記憶體模型

```
Animal 物件在記憶體中（有虛擬函式時）

   低位址
   ┌─────────────────┐ ← this 指標
   │  vptr            │  8 bytes，指向 vtable
   ├─────────────────┤
   │  id (int)        │  4 bytes（Animal 的 data member）
   │  padding         │  4 bytes（對齊 8 bytes）
   └─────────────────┘  = 16 bytes 總大小

   vtable（在 .data.rel.ro 段，只讀）
   ┌──────────────────┐
   │  offset-to-top   │  = 0（頂層繼承）
   │  typeinfo ptr    │  → RTTI 資訊
   │  speak()         │  → Animal::speak  ← vtable[0]
   │  move()          │  → Animal::move   ← vtable[1]
   │  ~Animal()       │  → dtor           ← vtable[2]
   └──────────────────┘
```

`Dog` 繼承 `Animal` 後，`Dog` 物件的佈局和 `Animal` 相同（單一繼承），但 vptr 指向的是 `Dog` 的 vtable（其中 `speak()` entry 被替換成 `Dog::speak`）。

## Name Mangling：Itanium ABI 規則

Linux/x86-64 使用 Itanium C++ ABI 的 mangling 規則。格式：`_Z` + 編碼。

| 符號 | mangled 名 | 解碼規則 |
|---|---|---|
| `make_sound(Animal*)` | `_Z10make_soundP6Animal` | `_Z` + `10`（函式名長度）+ `make_sound` + `P`（pointer）+ `6Animal` |
| `Dog::speak()` | `_ZN3Dog5speakEv` | `_Z` + `N`（namespace/class）+ `3Dog`（長度+名）+ `5speak` + `E`（end）+ `v`（void 參數）|
| `Dog::Dog()` | `_ZN3DogC1Ev` | `C1` = complete constructor |
| `Dog::~Dog()` | `_ZN3DogD1Ev` | `D1` = base object destructor |
| `Dog vtable` | `_ZTV3Dog` | `TV` = vtable |
| `Dog typeinfo` | `_ZTI3Dog` | `TI` = typeinfo（RTTI）|

還原工具：

```bash
$ c++filt _ZN3Dog5speakEv
Dog::speak()

$ c++filt _Z10make_soundP6Animal
make_sound(Animal*)

$ nm /tmp/re_part3/vtable_O0 | grep '_Z' | while read a t s; do echo "$s -> $(c++filt $s)"; done | head -10
_Z10make_soundP6Animal -> make_sound(Animal*)
_ZN3Cat5speakEv -> Cat::speak()
_ZN3Dog5speakEv -> Dog::speak()
_ZN3DogC1Ev -> Dog::Dog()
_ZN6Animal4moveEv -> Animal::move()
_ZN6Animal5speakEv -> Animal::speak()
```

strip 後 mangled 名消失，只剩位址——但 vtable 和 RTTI 仍在 `.data.rel.ro` 和 `.rodata`（下面說明）。

## 真跑：g++ 編 vtable 範例

```cpp
/* /tmp/re_part3/vtable.cpp — 出題 source */
class Animal {
public:
    virtual void speak() { printf("...\n"); }
    virtual void move()  { printf("moving\n"); }
    virtual ~Animal() {}
    int id;
};
class Dog : public Animal {
public:
    void speak() override { printf("Woof!\n"); }
    int breed;
};
class Cat : public Animal {
public:
    void speak() override { printf("Meow!\n"); }
};
void make_sound(Animal *a) { a->speak(); }
```

```bash
$ g++ -O0 -o /tmp/re_part3/vtable_O0 /tmp/re_part3/vtable.cpp
$ /tmp/re_part3/vtable_O0
Woof!
Meow!
sizeof(Animal)=16 sizeof(Dog)=16
```

`sizeof(Animal)=16`：vptr(8) + id(4) + padding(4)。`sizeof(Dog)=16`：Dog 繼承 Animal，加了 `breed(int)` 但佔 Animal 的 padding 空間——16 bytes 夠放得下。

### 從 nm 定位 vtable

```bash
$ nm /tmp/re_part3/vtable_O0 | grep '_ZTV\|_ZTI'
0000000000003d48 V _ZTI3Cat      # Cat typeinfo（RTTI）
0000000000003d60 V _ZTI3Dog      # Dog typeinfo
0000000000003d78 V _ZTI6Animal   # Animal typeinfo
0000000000003cb8 V _ZTV3Cat      # Cat vtable
0000000000003ce8 V _ZTV3Dog      # Dog vtable
0000000000003d18 V _ZTV6Animal   # Animal vtable
```

### 讀 vtable 內容（readelf -x）

```bash
$ readelf -x .data.rel.ro /tmp/re_part3/vtable_O0
```

實際輸出（部分）：

```
Hex dump of section '.data.rel.ro':
  0x00003cb8  00000000 00000000 483d0000 00000000  ........H=......
  0x00003cc8  88130000 00000000 f6120000 00000000  ................
  0x00003cd8  24140000 00000000 52140000 00000000  $.......R.......
```

`0x3cb8` 是 `_ZTV3Cat`（Cat vtable）的開始：
- `offset-to-top = 0`（`00000000 00000000`）
- typeinfo ptr = `0x3d48`（`483d0000 ...`，小端）= `_ZTI3Cat`
- vtable[0] = `0x1388`（`88130000 ...`）= `Cat::speak`
- vtable[1] = `0x12f6`（`f6120000 ...`）= `Animal::move`（Cat 沒 override）
- vtable[2] = `0x1424`（`24140000 ...`）= `Cat::~Cat` D1
- vtable[3] = `0x1452`（`52140000 ...`）= `Cat::~Cat` D0

## 虛擬呼叫的 asm 模式

```bash
$ objdump -d /tmp/re_part3/vtable_O0 | grep -A 15 '<_Z10make_soundP6Animal>:'
```

```asm
00000000000011c9 <_Z10make_soundP6Animal>:
    11c9:  endbr64
    11cd:  push   %rbp
    11ce:  mov    %rsp,%rbp
    11d1:  sub    $0x10,%rsp
    11d5:  mov    %rdi,-0x8(%rbp)      ; Animal *a 存到 stack
    11d9:  mov    -0x8(%rbp),%rax      ; rax = a
    11dd:  mov    (%rax),%rax          ; rax = *a = vptr  ← 載入 vtable 指標
    11e0:  mov    (%rax),%rdx          ; rdx = vtable[0] = speak()  ← 讀函式指標
    11e3:  mov    -0x8(%rbp),%rax      ; rax = a（this）
    11e7:  mov    %rax,%rdi            ; 第一個參數 = this
    11ea:  call   *%rdx               ; 間接呼叫 vtable[0]  ← 虛擬分派！
    11ec:  ret
```

這個三步序列是虛擬呼叫的**不變指紋**：

```
1. mov (%obj), %rax      → 載入 vptr（物件開頭 8 bytes）
2. mov N(%rax), %rdx     → 讀 vtable + N*8（N=0 是第一個虛擬函式）
3. call *%rdx            → 間接呼叫
```

vtable offset 是 `N*8`（64-bit 指標）：`vtable[0]` = offset 0、`vtable[1]` = offset 8、`vtable[2]` = offset 16……

### 多重繼承時 vtable 更複雜

多重繼承（`class C : public A, public B`）的物件佈局有**兩個 vptr**——`C` 的主 vtable 加上 B 的次 vtable。逆向時看到物件裡不止一個像指標的 8 bytes，先查 `_ZTV` 符號或 typeinfo 確認是否多繼承。

## 建構子：vtable 初始化的時機

```bash
$ objdump -d /tmp/re_part3/vtable_O0 | grep -A 15 '<_ZN3DogC1Ev>:'
```

```asm
00000000000013c8 <_ZN3DogC1Ev>:
    13c8:  endbr64
    13cc:  push   %rbp
    13cd:  mov    %rsp,%rbp
    13d0:  sub    $0x10,%rsp
    13d4:  mov    %rdi,-0x8(%rbp)      ; this 指標存 stack
    13d8:  mov    -0x8(%rbp),%rax
    13dc:  mov    %rax,%rdi
    13df:  call   13aa <_ZN6AnimalC1Ev> ; ← 先呼叫 base class ctor
    13e4:  lea    0x290d(%rip),%rdx        # 3cf8 <_ZTV3Dog+0x10>
    13eb:  mov    -0x8(%rbp),%rax
    13ef:  mov    %rdx,(%rax)          ; ← 把 vptr 設為 Dog 的 vtable+0x10
    13f4:  ret
```

建構子的 vtable 初始化發生在**呼叫 base class ctor 之後**——先設 Animal vtable，再設 Dog vtable。這就是為什麼在 ctor 中呼叫虛擬函式，會呼叫到「當下 vptr 指向」的那個函式（不是最終子類的）。

## RTTI：dynamic_cast 和 type_info

```bash
$ nm /tmp/re_part3/vtable_O0 | grep '_ZTI'
0000000000003d48 V _ZTI3Cat
0000000000003d60 V _ZTI3Dog
0000000000003d78 V _ZTI6Animal
```

RTTI 讓你在 strip 的 binary 裡也能確認繼承關係——`_ZTI3Dog` 的記憶體結構包含一個指向 `_ZTI6Animal` 的指標（因為 Dog 繼承 Animal），而 `_ZTVN10__cxxabiv120__si_class_type_infoE` 代表「single-inheritance class type info」。在 `nm` 輸出裡看到：

```bash
U _ZTVN10__cxxabiv117__class_type_infoE@CXXABI_1.3   # 用於無繼承類
U _ZTVN10__cxxabiv120__si_class_type_infoE@CXXABI_1.3 # 用於單一繼承
```

`__si_class_type_info` 說明這個 binary 有單一繼承。

## 標準庫容器的記憶體佈局

### std::string（libstdc++ SSO）

`std::string` 在 libstdc++ 的 SSO 閾值是 15 characters（不含 `\0`）：

```
長字串（> 15 chars）：         短字串（≤ 15 chars，SSO）：
┌─────────────────┐           ┌─────────────────┐
│ ptr → heap       │ 8 bytes  │ buf[15+1]        │ 15+1 bytes
│ size             │ 8 bytes  │ size             │ 8 bytes
│ capacity         │ 8 bytes  │ capacity         │ 8 bytes（最高位 = 0 = SSO flag）
└─────────────────┘           └─────────────────┘
```

逆向時：看到 `mov %rdi,%rax; lea 0x10(%rdi),%rsi; cmp %rsi,%rax; je <local_buf>` ——這是 SSO 路徑判斷（長度 ≤ 15 走 local buf，否則走 heap 指標）。

### std::vector

```
三指標佈局：
┌─────────────────┐
│ _begin           │ 8 bytes，指向 heap 陣列開頭
│ _end             │ 8 bytes，指向最後一個元素的後一格
│ _capacity_end    │ 8 bytes，指向 allocated 記憶體結尾
└─────────────────┘
```

逆向時看到三個連續 8 bytes 的指標，且有 `_end - _begin = size * sizeof(T)` 的計算，就是 `std::vector`。

### Exception（landing pad 概念）

C++ exception 在 binary 裡留下 `.gcc_except_table`（LSDA，language-specific data area）和 unwind table。如果你看到 `.gcc_except_table` 段存在，說明原始碼有 try/catch。逆向時 try block 對應一個 landing pad 地址，catch 的 type 對應一個 typeinfo pointer——從這裡能確認 catch 的型別。

## 對比與取捨

| 場景 | 技術 | 工具 |
|---|---|---|
| 有符號 binary | `c++filt` 還原 mangled 名 | `nm | c++filt` |
| stripped binary，有 RTTI | 從 vtable 位置 + typeinfo 重建類別關係 | `readelf -x .data.rel.ro` |
| stripped binary，無 RTTI（`-fno-rtti`）| 從 vtable 形狀和虛擬呼叫模式推斷 | objdump + 手動分析 |
| 反編譯器（Ghidra/IDA） | F5 後幫你還原虛擬呼叫為函式名（需先定義 class hierarchy） | Ghidra Class Hierarchy 視窗 |

## 踩雷集錦

1. **以為 `call *%rdx` 是函式指標表，沒想到是 vtable**：虛擬呼叫的三步驟（載 vptr → 取 vtable entry → 間接 call）在 `-O2` 下 inline 後可能更精簡，但「`mov (%obj),%rax; call *N(%rax)`」的骨架不變。

2. **vtable 裡的 offset-to-top 和 typeinfo 誤以為是函式指標**：vtable 在函式指標之前有兩個 metadata（offset 和 typeinfo）。真正的函式指標從 `_ZTV + 0x10`（16 bytes）開始——`readelf` 輸出裡第三個 8-byte 才是 vtable[0]。

3. **D0 / D1 / D2 destructor 讓你覺得有三個解構子**：Itanium ABI 的解構子有三種：D0（deleting，負責 delete 自身）、D1（complete object）、D2（base object）。一個 class 可能有兩到三個 `_ZN...D` entry，都是同一個 logical destructor 的不同形式。

4. **-O2 把小 class 的 method inline 掉，vtable 查不到實作**：Dog::speak() 在 `-O2` 下可能直接 inline 到 make_sound 裡，vtable 裡的 entry 仍然存在但實際 code 在呼叫點展開了。靜態分析 vtable 看 Dog 有 speak，但找不到獨立的 `_ZN3Dog5speakEv` 函式——這是正常現象。

5. **std::string 的 SSO 讓你以為字串在 stack 而不在 heap**：短字串（≤ 15 chars）確實在 stack/物件內部，不走 heap。逆向時看到字串資料「長在物件裡面」不要覺得奇怪——這就是 SSO 設計。

## 進階：再往深一層

- **Ghidra 的 C++ Class Analyzer**：Ghidra 有 plugin 能自動從 vtable / RTTI 重建類別層次，產生帶類別名的偽 code。值得在真實目標上試試，但它偶爾會把 offset-to-top 誤判為函式指標（還是要對照 asm 確認）。
- **接 `browser_pwn` 課的 V8 type confusion**：V8 的 Map 物件就是一種 vtable 的等價物；type confusion 漏洞就是「你讓 JIT 以為 vptr 指向 A 的 vtable，但實際是 B 的」——理解了本章的 vtable 機制，`browser_pwn` 的 type confusion 就好懂多了。
- **Android 的 ART virtual dispatch**：Android 的 ART runtime 也有類似 vtable 的 method dispatch table——`android_reversing` 課的 Java virtual call 反編譯，原理和這裡一脈相承。

## 本章重點整理

- **Name mangling** 用 `c++filt` 還原；`_ZN`（class scope）、`_ZTV`（vtable）、`_ZTI`（RTTI typeinfo）是最重要的前綴。
- **虛擬呼叫三步**：`mov (%obj),%rax`（vptr）→ `mov N(%rax),%rdx`（vtable entry）→ `call *%rdx`（間接分派）。
- vtable 在 `.data.rel.ro`，前 16 bytes 是 metadata（offset-to-top + typeinfo），真正的函式指標從 `+0x10` 開始。
- 建構子初始化 vtable 的順序：先呼叫 base ctor，再設 derived vtable——靜態分析 ctor 就能還原初始化順序。
- `std::string` SSO（≤ 15 chars 在物件內）、`std::vector` 三指標（begin/end/cap）是兩個最常見的標準庫佈局。

## 自我檢核

- [ ] 我能用 `c++filt` 還原 `_ZN3Dog5speakEv`，並能解讀 `C1`/`D1`/`D0` 的含義
- [ ] 我能從 objdump 的 `mov (%rax),%rax; mov (%rax),%rdx; call *%rdx` 認出虛擬呼叫
- [ ] 我知道 vtable 的前 16 bytes 是 metadata，函式指標從 `+0x10` 開始
- [ ] 我能從 `nm | grep _ZTV` 找到 vtable 位址，並從 `readelf -x .data.rel.ro` 讀出函式指標
- [ ] 我能解釋 std::string 的 SSO 為什麼讓短字串不走 heap

## 延伸閱讀

1. **Itanium C++ ABI 規格**（[https://itanium-cxx-abi.github.io/cxx-abi/abi.html](https://itanium-cxx-abi.github.io/cxx-abi/abi.html)）
   - 學什麼：vtable 佈局、name mangling 規則、RTTI 結構的官方定義——逆向 C++ 時的第一手參考
   - 前提：能讀英文規格文件

2. **《Practical Reverse Engineering》Ch 3（逆 C++ 物件）** — Dang, Gazet, Bachaalany（Wiley, 2014）
   - 學什麼：大量的 Windows x86 C++ 逆向案例，vtable 追蹤方法和本章類似但加了 MSVC ABI 的差異
   - 前提：本章基礎

3. **OALabs 的 YouTube「Reversing C++ Malware」系列**（搜尋 OALabs C++ reversing）
   - 學什麼：真實惡意程式的 C++ 物件逆向，包括 vtable hook（攻擊方法之一）和防禦偵測
   - 前提：本章 + 基本動態逆向（Part 2）

C++ 的複雜性讓逆向多了幾層，下一章看更新的語言——Rust 和 Go 的 binary 為什麼又是另一種難法。

→ [Ch 21 逆 Rust / Go binary：為什麼更難](./21-reversing-rust-go-binaries.md)
