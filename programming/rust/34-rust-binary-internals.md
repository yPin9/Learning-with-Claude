# Ch 34 — Rust binary 內部：組語樣貌

環境：rustc 1.97.1 (8bab26f4f 2026-07-14)，GNU Binutils 2.38，x86-64 Linux (WSL2)

---

## 為什麼需要這個？

你已經能在 Ghidra/Binary Ninja 裡認出「這是 Rust binary」——mangled symbol 格式、panic 字串、`core::panicking` 呼叫鏈都是特徵。但認出來只是第一步，接下來要能**讀懂**那些函式在做什麼。

問題是 Rust 的型別系統跟 C 差很多：`Option<T>` 不是普通 nullable pointer、`&str` 不是 `char*`、`Vec<T>` 不是 `T*`。LLVM 後端又會根據 build profile 大幅改寫 IR——debug 和 release 的組語看起來像兩個不同程式。

這一章的目標是建立從 Rust 型別到組語的直覺映射，讓你拿到一個 Rust binary 時，能快速辨識 fat pointer 傳參、Option niche、bound check、vtable 間接呼叫這幾個關鍵樣式。

---

## 先建立直覺

Rust 的 ABI 沒有公開標準（Rust 只保證 `extern "C"` 函式遵守 C ABI），但在 x86-64 Linux 上，rustc 實際上使用 System V AMD64 ABI 的寄存器分配慣例，搭配自己的結構體拆解規則。幾個核心映射：

| Rust 型別 | 佔幾個 word | ABI 傳遞方式 |
|---|---|---|
| `&str` | 2（ptr + len）| 兩個 register（rdi/rsi 或 rsi/rdx）|
| `&[T]` | 2（ptr + len）| 同上 |
| `str::len()` 呼叫 | 讀第二個 word | 直接取 register，無函式呼叫 |
| `Vec<T>` | 3（ptr + len + cap）| by reference 或拆暫存器 |
| `Option<Box<T>>` | 1（nullable ptr）| niche：ptr=0 代表 None |
| `Option<String>` | 3（nullable ptr + len + cap）| niche：ptr=0 代表 None |
| `Option<i32>` | 8 bytes（discriminant + value）| 無 niche，明確 tag |
| `dyn Trait`（fat ptr）| 2（data ptr + vtable ptr）| 兩個 register |

核心規則：**有 niche 的型別，None 不需要額外 tag**。`String` 的 `ptr` 欄位合法值永遠非零（heap allocation），所以 `Option<String>` 把 `ptr=null` 當作 `None`，省掉一個 discriminant word。這是 Rust layout optimizer 的核心技巧，在組語裡很難用眼睛直接看出，但知道規則後能逆推。

---

## &str / slice：fat pointer 在組語裡的樣貌

`&str` 在記憶體裡是兩個連續的機器字：

```
[ ptr: *const u8 ]  ← 指向 UTF-8 bytes
[ len: usize      ]  ← byte 長度（不含 null terminator）
```

**沒有 null terminator**。這是跟 C 最大的差異。當你在 Ghidra 看到一個函式接收 `&str`，它實際上是兩個參數。

來看真實案例。以下是 `lookup` 函式簽名：

```rust
fn lookup(map: &HashMap<String, String>, key: &str) -> Option<String>
```

Debug 組語開頭：

```
000000000001e310 <_RNvCseCUiVmLUaYH_8src_main6lookup>:
   1e310:	48 83 ec 28          	sub    $0x28,%rsp
   1e314:	48 89 0c 24          	mov    %rcx,(%rsp)
   1e318:	48 89 d0             	mov    %rdx,%rax
   1e31b:	48 8b 14 24          	mov    (%rsp),%rdx
   1e31f:	48 89 44 24 08       	mov    %rax,0x8(%rsp)
   1e324:	48 89 f0             	mov    %rsi,%rax
   1e327:	48 8b 74 24 08       	mov    0x8(%rsp),%rsi
   1e32c:	48 89 44 24 10       	mov    %rax,0x10(%rsp)
   1e331:	48 89 f8             	mov    %rdi,%rax
   1e334:	48 8b 7c 24 10       	mov    0x10(%rsp),%rdi
   1e339:	48 89 44 24 18       	mov    %rax,0x18(%rsp)
   1e33e:	48 89 44 24 20       	mov    %rax,0x20(%rsp)
   1e343:	e8 98 f0 ff ff       	call   1d3e0 <_RINvMs2_...HashMap...3geteE...>
```

傳入的參數分布：
- `rdi` = `map` 的指標（`&HashMap<String, String>`）
- `rsi` = `key` 的 ptr 部分（`*const u8`）
- `rdx` = `key` 的 len 部分（`usize`）

Debug build 不做最佳化，所以你會看到大量 `mov rax, reg` + `mov [rsp+N], rax` 的 spill/reload，這是 debug 模式強制把所有 local 存到 stack 的結果，實際邏輯並不複雜。整個函式在 `call HashMap::get`（已特化為 `get::<str>`）之前把參數重排好，再傳給下一層。

`&[T]` 的結構和 `&str` 完全相同：`(ptr, len)`。差別只是 `ptr` 的型別是 `*const T`，`len` 是元素數量而非 byte 數。傳參方式一樣佔兩個 register。

**逆向識別技巧**：如果你在 Ghidra 看到一個函式接兩個 register 且第二個明顯是 size class 的值，而函式內部用第一個做記憶體存取、用第二個做邊界比較——那幾乎可以確定是 fat pointer 傳參。

---

## Option<T>：niche 過的辨識方法

Rust 的 `Option<T>` 有兩種 layout 模式：

**有 niche（pointer-based types）**：`Option<Box<T>>`、`Option<String>`、`Option<Vec<T>>`、`Option<&T>` 等。因為合法指標永遠非零，所以用 `ptr=null` 代表 `None`，整個 `Option` 的大小等於 `T` 本身的大小，不多一個 byte。

**無 niche（scalar types）**：`Option<i32>`、`Option<u8>` 等。需要明確的 discriminant，通常是在值前面（或後面）加一個 tag byte/word，加上對齊後可能比直覺更大。

以 `Option<String>` 為例，`String` 內部是 `Vec<u8>`，也就是三個 word：`{ ptr, len, cap }`。`Option<String>` 的 layout：
- `None` → `ptr=0`，其餘 word 未定義
- `Some(s)` → `ptr != 0`，後面跟 `len` 和 `cap`

在組語裡，檢查 `Option<String>` 是否為 `None` 的程式碼會是：

```asm
test   rdi, rdi      ; ptr == 0 ?
je     none_branch
```

或者 `cmp rdi, 0` + `je`。不會有 `movzx eax, [rdi+24]` 之類讀 discriminant byte 的操作。

回到 `lookup` 的反組譯，call chain 的意義：

```
   1e343:	call   1d3e0 <HashMap::get::<str>>
   ; rax 回傳 Option<&String>（niche：rax=0 → None）

   1e348:	mov    0x18(%rsp),%rdi
   1e34d:	mov    %rax,%rsi

   1e350:	call   1e9e0 <Option::<&String>::cloned>
   ; 把 Option<&String> 轉成 Option<String>（clone heap data）
```

`HashMap::get` 回傳 `Option<&String>`——這是 niche pointer，`rax=0` 代表 `None`。接著呼叫 `cloned()` 把 `&String` 轉成 owned `String`，最終回傳 `Option<String>`（同樣 niche，ptr 欄位為 0 代表 None）。

`lookup` 的回傳值是 `Option<String>`，依 SysV ABI 的 struct 回傳規則，三個 word 透過 `rdi` 指向的 caller-allocated buffer 回傳（return-value-optimisation buffer 在 stack 上，`rdi` 在函式開始時指向它）。

---

## Vec / String 的記憶體佈局

`Vec<T>` 的 layout 很直觀，但要記住確切的欄位順序：

```
offset 0:   ptr: *mut T    ← heap allocation
offset 8:   len: usize     ← 已存元素數
offset 16:  cap: usize     ← 總容量
```

`String` 就是 `Vec<u8>` 的 newtype wrapper，layout 完全相同。

在 debug binary 裡，`parse_kv` 函式需要維護 `HashMap`、多個 `String`、iterator state，stack frame 會相當大。你會在函式開頭看到較大的 `sub rsp, N`。這是 debug 模式不做 stack coloring 的結果——每個變數都佔獨立的 stack slot，即使生命週期不重疊。

Release build 的 `parse_kv` stack frame 會小很多，因為 LLVM 做了 liveness analysis，重疊使用同一塊 stack 空間。

**逆向時識別 Vec**：在函式 prologue 看到三個連續 8-byte slot 一起初始化（通常是 `mov [rsp+N], rdi; mov [rsp+N+8], rsi; mov [rsp+N+16], rdx`），且後面的程式碼會分別讀這三個 offset，大概率是 `Vec<T>` 的 by-value 傳遞或 stack 上的局部 Vec。

**Vec 的 push 操作在組語裡的樣子**

`vec.push(x)` 在 debug build 裡會展開成：
1. 讀 `len` 和 `cap`（offset 8 和 16）
2. 比較 `len == cap`——如果相等要 reallocate
3. 若有空間：寫入 `ptr[len]`，len++
4. 若無空間：呼叫 `alloc::raw_vec::RawVec::grow_one`（mangled symbol 很長）

所以你在追一個 `Vec::push` 的時候會看到兩條路：一條 fast path（直接寫入）、一條 slow path（realloc）。Rust 用 `__cold` attribute 把 slow path 放到後面，fast path 是直落式的 inline 程式碼。

`String::push_str` 底層就是 `Vec<u8>::extend_from_slice`，pattern 類似但是 byte-level memcpy。

---

## bound check 的組語

這是 Rust 和 C 最明顯的組語差異之一。

原始程式：

```rust
#[inline(never)]
fn sum_first_n(data: &[i32], n: usize) -> i32 {
    let mut total = 0i32;
    for i in 0..n {
        total = total.wrapping_add(data[i]);
    }
    total
}

fn main() {
    let v = vec![10, 20, 30];
    println!("{}", sum_first_n(&v, 3));
}
```

**Debug build**——邊界檢查清晰可見：

```
00000000000145d0 <_RNvCs5rjQWJrqFgh_14slice_noinline11sum_first_n>:
   145d0:	48 83 ec 58          	sub    $0x58,%rsp
   145d4:	48 89 54 24 18       	mov    %rdx,0x18(%rsp)    ; 儲存 slice len
   ...
   1463c:	48 39 c8             	cmp    %rcx,%rax          ; i vs len
   1463f:	72 0b                	jb     1464c              ; i < len → ok
   14641:	eb 20                	jmp    14663              ; i >= len → panic!
   ...
   14663:	48 8b 74 24 28       	mov    0x28(%rsp),%rsi
   14668:	48 8b 7c 24 08       	mov    0x8(%rsp),%rdi
   1466d:	48 8d 15 34 e9 03 00 	lea    0x3e934(%rip),%rdx  ; panic 描述字串
   14674:	ff 15 4e 07 04 00    	call   *0x4074e(%rip)       ; 呼叫 panic handler
```

幾個細節值得注意：

`jb`（jump if below）是**無號數比較**。Rust 的 slice 索引永遠是 `usize`（無號），所以邊界檢查統一用無號比較。這意味著負數 index 在 Rust 根本不存在——`usize` 不可能是負數，如果你強行用 `as usize` 轉換負數，它會變成很大的正數，一樣會觸發 bound check 並 panic。

panic handler 接收的 `rdx` 是指向靜態字串的指標，那個字串包含 source file 路徑、行號、欄號——這是 Rust debug binary 獨有的特徵，在逆向時是非常好用的地標。

**Optimized build**（LLVM 在編譯期確定 `n=3`、`len=3`）：

```
0000000000013cd0 <_RNvCs5rjQWJrqFgh_14slice_noinline11sum_first_n>:
   13cd0:	8b 47 04             	mov    0x4(%rdi),%eax    ; data[1]
   13cd3:	03 07                	add    (%rdi),%eax       ; + data[0]
   13cd5:	03 47 08             	add    0x8(%rdi),%eax    ; + data[2]
   13cd8:	c3                   	ret    
   13cd9:	cc                   	int3   
```

4 行。整個迴圈、bound check、accumulator 全部消掉，剩下三次記憶體讀取直接相加。這是 LLVM 的 loop unrolling + bound check elimination 的結果：當編譯器能靜態證明所有存取都在範圍內，bound check 就徹底消失。

`int3` 是 unreachable 填充，防止 CPU 在 `ret` 後繼續執行跑入下一個函式的程式碼。

**對逆向的意義**：
- Release binary 裡函式看起來異常精簡 → 不要以為是 stub，可能是完整邏輯 unroll 後的樣子
- Debug binary 的 panic 字串是路徑分析的好入口，搜尋 `.rodata` 裡的 `src/` 前綴字串能找到所有 panic 點
- 看到 `jb` + jump-to-panic 的模式 → 這是 Rust 的 slice bound check，不是 C 的越界 bug

**get_unchecked 和 unsafe 的影響**

如果 Rust 程式碼用了 `unsafe { data.get_unchecked(i) }`，bound check 消失，組語直接是：

```asm
mov    eax, [rdi + rcx*4]   ; i * sizeof(i32) = i * 4
```

沒有 `cmp`，沒有 `jb`。在逆向時如果看到 slice 存取卻沒有 bound check——可能是 LLVM 優化掉了，也可能是 `unsafe` 程式碼。如果是後者，那就是潛在的 OOB 漏洞點，值得深追。

區分方法：build debug binary 看看同一個函式有沒有 bound check；如果 debug 也沒有，那是 `unsafe { get_unchecked }` 或等價操作。

---

## monomorphization 的痕跡

泛型函式在 Rust 裡會對每個型別參數組合各生成一份機器碼。這叫 monomorphization，是 Rust 零成本抽象的核心機制，但它的代價是 binary 膨脹和 symbol 爆量。

`nm` 在 debug binary 找 user-code 函式：

```
000000000001e170 t src_main::main
000000000001e310 t src_main::lookup
000000000001e360 t src_main::parse_kv
```

就三個，但 HashMap 的 `get` 呼叫產生的 mangled symbol 是：

```
_RINvMs1_NtCsgQfI1edjipl_9hashbrown3mapINtB6_7HashMapNtNtCscdodAO9FK5_5alloc6string6StringBO_NtNtNtCs2AWtUsOyxgP_3std4hash6random11RandomStateE3geteECseCUiVmLUaYH_8src_main
```

`rustfilt` demangle 後：

```
<hashbrown::map::HashMap<alloc::string::String, alloc::string::String, std::hash::random::RandomState>>::get::<str>
```

這個 symbol 包含了完整的型別資訊：key type `String`、value type `String`、hasher `RandomState`、還有查詢型別 `str`。如果你的 HashMap 換成 `HashMap<i32, String>`，就會生成另一份不同 symbol 的 `get`。

Symbol 數量對比：
- Debug build：1276 symbols
- Release build：1050 symbols

差了 226 個。Release build 透過 inlining 消掉許多小函式（不需要獨立 symbol），但 monomorphized 的特化版本不會消掉——每個型別參數組合的特化份數在兩個 build 裡基本相同。差值主要來自 debug 裡存在的 intrinsic wrapper 和 shim 函式，這些在 release 裡被 inline 進呼叫者。

**實際影響**：在 Ghidra 裡你會看到大量名字相近但型別參數不同的函式。`HashMap::get::<str>` 和 `HashMap::get::<String>` 是兩份不同的機器碼，邏輯相同但 inline 的 hash/eq 實作可能不一樣。不要把兩份分析混淆。

**如何用 nm + rustfilt 快速梳理 symbol**

```bash
# 列出所有 Rust user-code 函式（過濾 _R 前綴）
nm -n binary | grep ' t ' | rustfilt | grep -v 'core::\|alloc::\|std::\|hashbrown::' | head -30

# 列出所有 HashMap 的 monomorphized 版本
nm -n binary | grep 'HashMap' | rustfilt | grep '::get'

# 統計每個 crate 的 symbol 數量（proxy for 程式碼佔比）
nm binary | rustfilt | grep -oP '(?<=^[0-9a-f]+ [tT] )[\w]+(?=::)' | sort | uniq -c | sort -rn
```

`nm -n` 按地址排序，有助於識別哪些函式在 binary 裡相鄰（通常是同一個 module 或 monomorphization group）。

---

## debug vs release 整體對比

| 特性 | debug | release |
|---|---|---|
| bound check | 明確 `cmp` + `jb` + panic 分支 | LLVM 靜態証明後消掉，或保留為 unlikely 分支 |
| 函式 inline | 幾乎不 inline（保留 call frame for backtrace）| 大量 inline，call site 消失 |
| symbol 數量 | 多（本例 1276）| 較少（本例 1050）|
| panic 路徑 | 明顯分支，帶 source path 字串 | DCE 消掉不可達路徑；可達 panic 仍存在 |
| 迴圈結構 | 保留（cmp + jne 回頭跳）| unroll / SIMD / 消掉 |
| stack frame | 大（每個 local 獨立 slot）| 小（liveness-aware slot sharing）|
| 可讀性 | 高，結構清晰對應原始碼 | 低，需要逆向工程師重建意圖 |
| DWARF | 完整（行號、型別、local 變數名）| 通常 stripped 或極度壓縮 |

Rust release binary 的組語密度比 C release binary 高，原因是 monomorphization 讓 LLVM 得到更多內聯資訊，優化空間更大。同樣一個「找 HashMap entry」的操作，Rust 可能生成比手寫 C 更精簡的機器碼。

**工具輔助分析**

在不知道是 debug 還是 release build 的情況下，幾個快速判斷指標：

```bash
# 有沒有 DWARF debug info？
readelf -S binary | grep -c '\.debug'   # debug: ~20 sections; release stripped: 0

# 有沒有 panic 的 source path 字串？
strings binary | grep '\.rs:' | head -5

# 有沒有 backtrace symbol table？
nm binary | grep -c 'rust_begin_short_backtrace'   # debug: 1; release+stripped: 0
```

如果 `strings binary | grep '\.rs:'` 有輸出，那是 debug build 或至少沒有 `-C panic=abort`。這些 source path 字串本身也是很有價值的資訊——直接告訴你這個 binary 是從哪個 crate/repo 編譯的。

---

## trait object vtable 呼叫

當你用 `dyn Trait` 的時候，Rust 生成 fat pointer——兩個 word：

```
[ data ptr: *mut ()     ]  ← 指向實際資料
[ vtable ptr: *const VTable ]  ← 指向靜態 vtable
```

vtable 的 layout：

```
offset 0:   drop_in_place fn ptr
offset 8:   size of T
offset 16:  align of T
offset 24:  method_0 fn ptr
offset 32:  method_1 fn ptr
...
```

典型的 vtable 間接呼叫在組語裡的樣子：

```asm
; rdi = data ptr, rsi = vtable ptr（fat pointer 拆開傳）
mov    rax, [rsi + 24]   ; 取第一個 method 的 fn ptr
call   rax               ; 間接呼叫
```

跟靜態分派的差異：靜態分派直接是 `call 0x1234`（直接跳到已知地址）；vtable 呼叫是 `call rax`（透過 register 的間接跳轉）。在 Ghidra 裡，後者顯示為 `CALL RAX` 或 `CALL qword ptr [RAX + 0x18]`，是識別動態分派的關鍵特徵。

**Rust 沒有 RTTI**。C++ 的 `dynamic_cast` 需要在執行時查型別資訊；Rust 的 vtable 只包含 `drop`、`size`、`align`、以及 trait 定義的 method，沒有型別 ID、沒有繼承鏈資訊。如果你在 Rust binary 裡看到類似 RTTI 的結構，那是 `std::any::Any` 在用的 `TypeId`——是一個編譯期生成的 64-bit hash，不是 C++ RTTI 的 `type_info`。

`downcast_ref::<T>()` 的組語會是比較兩個靜態常數（`TypeId` 的值），不是遍歷繼承鏈。

**在 Ghidra 裡找 vtable**

Rust 的 vtable 是靜態資料，放在 `.rodata` section。你能用這個 pattern 找到它們：在 Ghidra 裡搜尋指向 `drop_in_place` 系列函式的指標，那些指標附近的靜態資料結構就是 vtable。每個 vtable 的前三個 word 一定是：drop fn ptr、size（常數）、align（常數，且是 2 的冪次）。如果你看到一個靜態 array 的第二個 word 是 8（或 16、32），第三個是 1（或 2、4、8），那幾乎可以確定是 vtable 的 size/align 欄位。

逆向時確認 vtable 身份：反解第一個 entry（drop fn ptr）是否是某個 Rust 型別的 drop_in_place，再從 mangled name 推回型別。

---

## 踩雷集錦

**1. 看到 4 行的 sum_first_n 以為是 stub**

Release binary 裡的 `sum_first_n` 只有 4 條指令——3 次記憶體讀取加上 ret。第一反應是「這是 forwarding stub 或 placeholder」。不是。LLVM 把整個迴圈 unroll 加上 bound check elimination，剩下的就是本質工作量。遇到疑似 stub 的函式，先看 cross-reference，確認沒有 call 到其他地方，再考慮它就是完整的實作。

**2. 找 Option 的 discriminant 找不到**

你搜尋 `movzx eax, byte ptr [rdi]`（讀 discriminant byte）沒找到，以為 Option 沒有 discriminant。對 `Option<String>` 這類有 niche 的型別，discriminant 是用 pointer 值是否為零來編碼的——`test rdi, rdi` 就是 discriminant check。對 `Option<i32>` 才會有明確的 tag byte/word。判斷方法：看型別內部是否有合法值空間可以用作 niche。

**3. HashMap::get 有多個 mangled 版本以為有 bug**

在 Ghidra 看到三個 `HashMap::get` 的特化版本，以為是重複符號或 binary 損毀。這是正常的 monomorphization：每個 `(K, V, S, Q)` 組合各一份。你需要確認哪個特化對應你在追的程式碼路徑，看它的型別參數。

**4. panic 路徑在 release 裡消失以為沒有 bound check**

Release 的 `sum_first_n` 沒有 panic 路徑，你以為 Rust release build 不做 bound check。實際情況分兩種：(a) LLVM 靜態證明不會越界，整個 bound check 消掉；(b) bound check 還在，但在不同的 cold 函式路徑裡（可能被放到 binary 很後面）。區分方法：看編譯器能不能靜態知道 index 範圍。如果索引來自使用者輸入，release build 裡仍然有 bound check，只是長相更緊湊。

**5. 以為 &str 是 C 的 char\***

C 背景的工程師第一直覺是 `&str` = `const char*`，但 `&str` 是 fat pointer，函式接收 `&str` 時用兩個 register 傳參。如果你把 `rdi` 當唯一的 string 參數，邏輯會全錯。識別方法：在函式開頭看傳入的 register 數量，如果兩個 register 後面緊接著用第一個做 load、第二個做 bound comparison，就是 fat pointer 的典型樣式。

---

## 進階：再往深一層

**DWARF 型別還原**

Debug binary 帶完整 DWARF，包含 `DW_TAG_structure_type` 描述每個 struct 的欄位和 offset。Ghidra 能解析 DWARF 自動還原部分型別，但 Rust 的 enum（特別是 niche-optimized 的那種）DWARF 描述方式跟 C union 不同，需要手動解讀 `DW_TAG_variant_part`。

**Ghidra Rust 型別還原 plugin**

`ghidra-rust` 這類社群 plugin 能批量 demangle symbol 並嘗試還原型別定義。對大型 Rust binary（十萬行以上的 Rust 專案）手動一個個看 symbol 不現實，plugin 能大幅加速初始分析。侷限是 niche optimization 和 generic 特化的型別推斷常常出錯，仍需人工確認關鍵路徑。

**Panic unwind 在 EH frame 裡的樣子**

Rust 的 panic 預設走 unwinding（除非 `-C panic=abort`）。在 EH frame（`.eh_frame` section）裡，每個可能 panic 的函式都有對應的 FDE（Frame Description Entry），記錄如何回退 stack frame。在 Ghidra 裡你看到的 `__rust_begin_short_backtrace` / `__rust_end_short_backtrace` 就是 Rust runtime 用來截斷 backtrace 顯示的標記函式，不是真正的邏輯——看到就可以跳過。Landing pad（`__gcc_personality_v0` 呼叫）在 release binary 裡如果開了 `-C panic=abort`，整個 unwind 機制消失，binary 顯著變小，且不再有 landing pad 相關的 EH table。

---

## 延伸閱讀

**Rustc dev guide — Code Generation**
`https://rustc-dev-guide.rust-lang.org/codegen.html`
官方文件說明 rustc 如何從 MIR 到 LLVM IR 再到機器碼。讀「MIR lowering」和「monomorphization」兩節，能理解 bound check 和 niche optimization 在哪個 pass 發生、為什麼 release build 的 IR 看起來完全不同。值得讀的不是表面描述，是它指出的 source 路徑——讓你能直接去 `compiler/rustc_codegen_llvm/` 看實際實作。

**Jon Gjengset — Demystifying Monomorphization（YouTube）**
他在 Crust of Rust 系列裡專門講 monomorphization 和 zero-cost abstraction 的實際代價。有實際跑 `cargo build --release` 然後看 binary size 和 compile time 的對比。對理解「為什麼我的 Rust binary 這麼大」很有幫助，而且他的解釋不靠猜測，直接看 LLVM IR。

**Exploring Rust's Enum Layouts — Aria Desires**
`https://faultlore.com/blah/rust-layouts/`
深入分析 Rust enum layout 的各種情況：niche optimization、discriminant 位置、FFI 安全的 enum 設計。對逆向工程師最有用的部分是「如何從 assembly 判斷 enum variant」那一節，直接給出每種情況的組語樣式。比官方 Reference 更有攻擊性，不繞彎子。

---

**Cargo.toml 裡的 profile 設定影響組語的程度**

常見的 security-relevant profile 設定：

```toml
[profile.release]
opt-level = 3        # 預設；2 也很常見
lto = "thin"         # thin LTO：跨 crate inline，symbol 減少明顯
codegen-units = 1    # 強制單一 codegen unit，讓 LLVM 看完整程式
panic = "abort"      # 消掉 unwinding，binary 縮小，但 panic 直接 abort
strip = "symbols"    # 去掉 symbol table，nm 幾乎看不到東西
```

`lto = "thin"` 開啟後，跨 crate 的函式可以被 inline，這時 monomorphized 的 symbol 數量可能進一步減少，因為 inline 後的函式不需要獨立 entry point。`strip = "symbols"` 搭配 `panic = "abort"` 是 production binary 最常見的組合，逆向難度最高。遇到這種 binary，靠 `.rodata` 字串和 vtable 結構推型別是主要手段。

---

→ [Ch 35 用 Rust 寫資安工具](./35-rust-security-tooling.md)
