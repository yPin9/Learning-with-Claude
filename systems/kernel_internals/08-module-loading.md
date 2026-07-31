# Ch 8 — 模組載入底層：finit_module、符號解析、簽署、initcall

> **目標**：理解 `insmod hello.ko` 那一瞬間，kernel 從一個 syscall 開始，把一份 ELF 檔案 copy 進核心、在 vmalloc 區配一段空間放它、把它引用的 kernel 符號一個個查表填上真實位址、做完重定位、最後呼叫你的 `module_init`——整條路徑你要能在腦中畫出來，並知道符號匯出、GPL-only、簽署、vermagic、refcount 這些機制各卡在哪一步、為什麼卡。

## 為什麼需要這個？

Ch 0 你已經 `insmod` 過 `hello.ko`，看到 `pr_info` 印出訊息。那一步「成功了」，但成功的背後是 kernel 做了一件在別的作業系統看來很激進的事：**把一段外來的機器碼，動態連結進一個已經在跑、擁有最高權限的單一程式（monolithic kernel，見 Ch 1）裡，然後直接跳進去執行**。

這跟 user space 載入 `.so` 完全不是一回事。user space 的動態連結器（`ld.so`）跑在使用者權限、有 MMU 保護、連錯了頂多 segfault 掉一個 process。kernel 模組沒有這層保險：你的模組跟排程器、記憶體管理跑在同一個位址空間、同一個特權層，`.ko` 裡一個填錯的位址就能覆寫核心資料結構、直接 panic 整台機器。

所以 kernel 對「載入一個模組」這件事做的檢查，遠比 `ld.so` 嚴格：

- 這份 ELF 是不是為**這一顆** kernel 編的？（vermagic）
- 它引用的每個 kernel 符號，我到底有沒有匯出給模組用？（`EXPORT_SYMBOL`）
- 它有沒有資格用 GPL-only 的符號？（`MODULE_LICENSE`）
- 這份東西是誰簽的、我信不信？（module signing）
- 現在有沒有別的模組正依賴它，導致我不能卸？（refcount）

這一章就是把 Ch 0 那句「模組真的跑在 kernel 裡了」拆開，看這五道關卡與中間的符號解析、重定位到底怎麼做。搞懂它，你才知道為什麼 `insmod` 會報 `Invalid module format`、`Unknown symbol`、`Key was rejected by service`——這些訊息每一條都對應下面某一步失敗。

## 先建立直覺

先把「模組」這個東西看清楚：一個 `.ko` 就是一個**還沒連結完成的 ELF relocatable object**（`ET_REL`，跟 `gcc -c` 出來的 `.o` 同一類，不是可執行檔）。它裡面有：

- `.text`/`.data`/`.bss`：你的程式碼與資料，但引用外部符號的地方**位址還是空的**
- 一堆 `.rela.*` 重定位表：記錄「哪個位置要填哪個符號的位址」
- `__versions`、`.modinfo` 等特殊 section：放 vermagic、依賴的符號 CRC、`MODULE_LICENSE` 字串、簽章

kernel 載入它，本質上就是**當一個動態連結器**，只是連結的對象是 kernel 自己。這件事跟本 repo `elf_linking` 課教的靜態/動態連結是同一套機制——重定位、符號解析——只是搬進了核心、對象是 running kernel 的符號表（kallsyms）。如果你上過那門課，這裡的 `apply_relocations` 你會覺得眼熟。

一張圖先建立全貌：

```
  user space                          kernel space
 ┌──────────┐   finit_module(fd)     ┌────────────────────────────────────┐
 │  insmod  │ ─────────syscall──────►│  load_module()  kernel/module/main.c│
 │ modprobe │                        │                                     │
 └──────────┘                        │  ① copy ELF 進 kernel（vmalloc）    │
     讀 hello.ko                     │  ② 檢查 vermagic / 簽章             │
     的 fd                           │  ③ layout_and_allocate:            │
                                     │      在「模組區」配最終落腳的記憶體 │
                                     │  ④ simplify_symbols:               │
                                     │      模組引用的 kernel 符號         │
                                     │      → 去 kallsyms 查位址填進符號表 │
                                     │  ⑤ apply_relocations:              │
                                     │      照 .rela 表把位址寫進 .text    │
                                     │  ⑥ do_init_module:                 │
                                     │      呼叫你的 module_init 函式      │
                                     └────────────────────────────────────┘
```

②③④⑤⑥ 任何一步失敗，`load_module` 就把已配的記憶體釋放掉、回一個 `-errno` 給 `finit_module`，`insmod` 就印出對應錯誤。這是整章的骨架，後面每一節都是在展開其中一格。

## 從 insmod 到 syscall：finit_module vs init_module

`insmod` / `modprobe` 本身只是使用者空間的小程式。它們做的事很少：打開 `.ko` 檔、呼叫一個 syscall 把載入工作交給 kernel。歷史上有兩個 syscall：

- **舊的 `init_module(void *module_image, unsigned long len, const char *param)`**：使用者空間得自己把整個 `.ko` 讀進一塊 buffer，再把 buffer 指標與長度傳給 kernel。
- **新的 `finit_module(int fd, const char *param, int flags)`**：使用者空間只傳一個**已開啟的檔案 fd**，由 kernel 自己去讀。

現在的 `insmod`/`modprobe`（kmod 套件）預設走 `finit_module`。為什麼要多一個傳 fd 的版本？兩個理由：

1. **簽章驗證需要「原始檔案」**：module signing 是把簽章附在檔案結尾（見後面），kernel 直接拿到 fd 就能對整份檔案內容做雜湊驗簽，不用信任 user space 幫忙搬運的 buffer。
2. **IMA/檔案完整性策略**可以掛在 fd 上，讓 LSM/IMA（Ch 48 會談 LSM）決定這個檔案能不能被載入。

兩者最後都匯流到同一個核心函式。原始碼在 `kernel/module/main.c`：

- `SYSCALL_DEFINE3(init_module, ...)`：把 user buffer copy 進 kernel，包成一個 `struct load_info`，呼叫 `load_module()`。
- `SYSCALL_DEFINE3(finit_module, ...)`：透過 `kernel_read_file_from_fd()` 把 fd 指向的檔案內容讀進 kernel，同樣包成 `load_info`，呼叫 `load_module()`。

所以真正的主角是 `kernel/module/main.c` 的 **`load_module(struct load_info *info, const char __user *uargs, int flags)`**。這個函式很長（幾百行），是模組載入的總指揮。下面幾節就是照它的執行順序走。

> **6.x 檔案位置的變化**：以前模組載入全塞在單一檔案 `kernel/module.c`。從 5.18 起被拆成 `kernel/module/` 目錄，主流程在 `main.c`，簽章在 `signing.c`，kallsyms 相關在 `kallsyms.c`。本課釘死的 6.12 就是拆分後的佈局，看舊書給的 `kernel/module.c` 路徑要記得換。

## load_module 主流程逐格拆解

### ① copy ELF 進 kernel + 基本 sanity check

無論走哪個 syscall，第一步都是**把整份 ELF 搬進核心可控的記憶體**（`copy_module_from_user` 那條路），得到一個 `struct load_info`，裡面存了 ELF header、各 section header 的指標。

接著 `load_module` 呼叫 `elf_validity_cache_copy()`（6.x 重構後的名字）之類的檢查：確認 ELF magic 對、是 `ET_REL` 型別、section header 表在檔案範圍內、`.modinfo` 與符號表 section 都在。這一步擋掉的是「根本不是合法模組 ELF」的垃圾輸入——`insmod` 一個文字檔會在這裡被回 `-ENOEXEC`。

### ② vermagic 與簽章檢查

搬進來、確認是合法 ELF 之後，`load_module` 會檢查這份模組是不是為當前 kernel 準備的。這一步靠 **vermagic**（version magic）字串，放在模組的 `.modinfo` section，內容長這樣：

```
6.12.0 SMP preempt mod_unload modversions ...
```

它記錄了編這個模組時的：kernel 版本、是否 SMP、preemption 模型、有沒有開 `MODULE_UNLOAD`、有沒有開 `MODVERSIONS` 等**會影響二進位相容性的 config**。`load_module` 呼叫 `check_modinfo()` 把模組的 vermagic 跟 running kernel 自己的 vermagic（`include/linux/vermagic.h` 展開出來的 `VERMAGIC_STRING`）逐字比對。**不一致就回 `-ENOEXEC`**，`insmod` 印 `Invalid module format`。

為什麼要這麼嚴？因為這些 config 會改變 struct 的排版、鎖的實作、preemption 的插點。一個對著「非 preempt kernel」編的模組，如果強行載進 preempt kernel，它對某個 struct 欄位的 offset 可能就錯了——不會馬上 crash，而是幾小時後某個你查不出來的記憶體損毀。vermagic 是拿「載入時直接拒絕」換「執行期詭異 corruption」，這個交換非常划算。這也正是 Ch 0 踩雷第 4 條「`KDIR` 指錯導致 version magic 不符」的底層原因。

簽章檢查（`module_sig_check()`）也在這附近做，放到後面「module signing」一節專講。

### ③ layout_and_allocate：模組住哪裡

檢查過關後，`load_module` 呼叫 **`layout_and_allocate()`**，這一步決定模組**最終落腳的記憶體**。關鍵觀念（直接接 Ch 6 的 vmalloc）：

模組不是配在 `kmalloc` 的線性映射區，而是配在 **module 專屬的虛擬位址區間**，用的是 `module_memory_alloc()`（x86_64 上它底層走 `__vmalloc_node_range`，配在 `MODULES_VADDR`～`MODULES_END` 這段虛擬位址）。為什麼用 vmalloc 式的配置？

- 模組大小不定、可能不小，`vmalloc` 能給大段**虛擬連續**但實體不必連續的記憶體，不會被 buddy allocator 的連續實體頁需求卡住（Ch 17）。
- 模組區有固定的虛擬位址範圍，方便 kallsyms 判斷「這個位址屬於某個模組還是核心本體」。
- x86_64 上模組區刻意放在核心正文附近（在 `-2GB` 定址範圍內），因為 x86 的 relocation 有近距離定址的限制——這點下一節講重定位時會用到。

`layout_and_allocate` 會計算模組所有 section 的佈局：把該執行的 `.text`、該讀寫的 `.data`、只讀的 `.rodata` 分組，算出總大小，呼叫 `module_memory_alloc()` 拿到那塊記憶體，再把各 section 的內容從 `load_info` 的暫存 copy 搬到最終位址。搬完之後，section 在核心裡的**真實虛擬位址**才確定下來——這是下一步符號解析與重定位的前提。

模組配好的記憶體會掛進一個 `struct module`（`include/linux/module.h`），這個結構是模組在核心裡的身分證：名字、狀態、它匯出的符號、它的 refcount、init/exit 函式指標，全在這裡。載入完成後 `/sys/module/<name>/` 與 `/proc/modules` 的內容就是從它讀出來的。

### ④ simplify_symbols：把「未定義符號」變成真位址

這是模組載入最核心的一步，也是「kernel 當連結器」的體現。函式在 `kernel/module/main.c` 的 **`simplify_symbols()`**。

你的模組引用了 `printk`、`kmalloc`、`register_chrdev` 這些 kernel 函式。在 `.ko` 裡，這些是 **undefined symbol**（`st_shndx == SHN_UNDEF`），符號表裡只有名字、沒有位址。`simplify_symbols` 走過模組的符號表，對每個符號按類型處理：

- `SHN_UNDEF`（外部符號，如 `printk`）：呼叫 **`resolve_symbol_wait()` → `find_symbol()`**，拿名字去查 kernel **匯出符號表**（下一節詳述）。查到就把符號的 `st_value` 填成真實核心位址；查不到就回 `-ENOENT`，`insmod` 印 `Unknown symbol in module`。
- `SHN_ABS`：絕對符號，值不動。
- 一般定義在模組自己 section 裡的符號：把 section 的基底位址（③ 拿到的真實位址）加上去，算出最終位址。

`find_symbol` 查的不是 `/proc/kallsyms` 那張「所有符號」表，而是專門的**匯出符號表**：核心本體的 `__ksymtab`（下一節）、加上所有已載入模組匯出的符號。這是關鍵設計——**不是所有 kernel 符號模組都能引用，只有被明確 `EXPORT_SYMBOL` 的才行**。一個模組想呼叫某個沒匯出的內部 static 函式，`find_symbol` 會查不到，直接 `Unknown symbol`。

如果模組 B 依賴模組 A 匯出的符號，`find_symbol` 在 A 的匯出表裡查到後，還會把 A 的 refcount 加一（`ref_module`），這就是「A 被 B 用著，`rmmod A` 會失敗」的來源——留到最後 refcount 一節。

### ⑤ apply_relocations：把位址真正寫進機器碼

符號都有真位址了，但你的 `.text` 裡那些 `call printk` 指令的目標位址欄位還是空的（連結器佔位）。**`apply_relocations()`** 走過模組每個 `.rela.*` section，照重定位表把算好的位址寫進去。

每一筆重定位記錄說的是：「在 `.text` 的 offset X 處，有一個引用符號 S 的位置，請按重定位類型 T 把 S 的位址（可能加上 addend）填進去。」實際填法是架構相依的，在 `arch/x86/kernel/module.c` 的 `apply_relocate_add()`。x86_64 常見兩種類型（一句帶過）：

- `R_X86_64_64`：把符號的 64-bit 絕對位址原樣填入（用在資料指標）。
- `R_X86_64_PC32` / `R_X86_64_PLT32`：填入「目標位址 − 指令位址」的 32-bit 相對偏移（用在 `call`/`jmp`）。

第二種是相對定址，只有 32 bit，能跳的距離是 ±2GB。**這正是 ③ 為什麼模組區要放在核心正文的 ±2GB 範圍內**——否則相對偏移放不下，`apply_relocate_add` 會回 `Overflow`。這是把 relocation 的數學（`elf_linking` 課）跟位址空間佈局（Ch 6/16）綁在一起的一個具體點。

做完重定位，模組的機器碼裡每個外部引用都指向了正確的核心位址，模組在記憶體裡「連結完成」了。這之後 `load_module` 會做收尾：把 `.text` 設成唯讀可執行、`.rodata` 設唯讀（v6.12 是 `module_enable_text_rox`/`module_enable_rodata_ro`，舊名 `module_enable_x`/`module_enable_ro`；防止模組自己或攻擊者事後改寫已載入的模組碼）、註冊 kallsyms、把 `struct module` 掛進全域模組鏈結串列。

### ⑥ do_init_module：呼叫你的 module_init

最後一步，`load_module` 呼叫 **`do_init_module()`**，它會呼叫模組的 init 函式——也就是你用 `module_init(hello_init)` 註冊的那個 `hello_init`。

這裡有一個貫穿本課的觀念要點破：**模組的 init 函式，本質上就是一種「動態 initcall」**。回想 Ch 3，核心自己的初始化是一連串 `initcall`（`core_initcall`、`device_initcall`…），在開機時由 `do_initcalls()` 按等級順序呼叫。編進核心（built-in）的模組，它的 `module_init` 其實會被 `__initcall` 機制收進 initcall 表，開機時就跑掉了——所以 built-in 模組沒有「載入」這回事，它就是開機初始化的一環。

而編成 `.ko` 動態載入的模組，`module_init` 走的是**另一條路**：不進 initcall 表，而是在 `do_init_module` 被載入時才呼叫。同一個 `module_init` 巨集，因為模組是 built-in 還是 loadable，展開成兩種完全不同的機制——這是 kernel 用一個統一介面包住兩種生命週期的典型手法。

`do_init_module` 呼叫 init 函式後看它的回傳值：

- 回 **0**：初始化成功。模組狀態從 `MODULE_STATE_COMING` 轉成 `MODULE_STATE_LIVE`，`load_module` 回 0，`insmod` 成功返回。
- 回**非 0（負 errno）**：初始化失敗（例如你在 init 裡 `register_chrdev` 失敗了回 `-EBUSY`）。`do_init_module` 會把剛載入的模組整個拆掉、釋放記憶體，`insmod` 拿到那個 errno 印錯誤。這就是 Ch 0 動手練習 3「把 init 改成 `return -EINVAL` 會被拒載」的底層——非 0 回傳是模組向核心宣告「我起不來，別留我」的約定。

## 符號匯出與 GPL-only：EXPORT_SYMBOL 的門禁

上面 ④ 說「只有 `EXPORT_SYMBOL` 的符號模組才查得到」。這一節把這個門禁機制講清楚，因為它同時是「模組能用什麼 API」和「GPL 授權」兩件事的交點。

核心裡一個函式要讓模組能呼叫，必須明確匯出：

```c
void my_kernel_service(void) { ... }
EXPORT_SYMBOL(my_kernel_service);        // 任何模組都能用
EXPORT_SYMBOL_GPL(my_kernel_service);    // 只有 GPL 相容授權的模組能用
```

`EXPORT_SYMBOL` 這個巨集（`include/linux/export.h`）做的事：把符號名、位址、CRC 打包成一筆 `struct kernel_symbol`，塞進一個特殊 section `__ksymtab`（GPL-only 的塞進 `__ksymtab_gpl`）。核心開機時這些 section 被組成核心的匯出符號表，`find_symbol`（④）查的就是它。**沒有 `EXPORT_SYMBOL` 的核心函式，符號表裡根本沒有，模組怎麼都連不到。**這是 kernel 刻意維持的界線：核心的「模組 ABI」是被明確劃定的一小塊，不是「所有 non-static 函式」。

`EXPORT_SYMBOL_GPL` 多一層授權門禁。`find_symbol` 查到一個 GPL-only 符號時，會檢查**引用它的模組的授權**——這個授權來自模組的 `MODULE_LICENSE()` 宣告，存在 `.modinfo`。`kernel/module/main.c` 的 `check_modinfo` / `license_is_gpl_compatible()` 判斷這個授權字串是不是 GPL 相容（`"GPL"`、`"GPL v2"`、`"Dual BSD/GPL"` 等算相容；`"Proprietary"` 或**沒宣告** 不相容）。

- 模組**授權相容** → 允許連結 GPL-only 符號。
- 模組**授權不相容或沒宣告** → `find_symbol` 拒絕給它 GPL-only 符號，回 `Unknown symbol`（就算那符號其實存在），而且核心會被 **taint**（`add_taint_module`，設 `TAINT_PROPRIETARY_MODULE`）。

這解釋了 Ch 0 那句「`MODULE_LICENSE("GPL")` 少了它 kernel 會 taint 並拒絕某些符號」。「某些符號」就是 `EXPORT_SYMBOL_GPL` 的那批。很多核心新 API（尤其是深入子系統內部的）都是 GPL-only，這是社群用技術手段表達「用我核心內部 API 的模組，請你也 GPL」的立場。

> **taint 是什麼**：核心維護一個 taint flag 位元組（`/proc/sys/kernel/tainted`），記錄「這顆核心被什麼污染過」——載過閉源模組、載過未簽章模組、發生過 oops、用過 `force` 載入等。它不影響運行，但一旦 kernel 出問題、你去 kernel bug tracker 求救，維護者第一眼看 taint：被閉源模組污染過的核心，社群通常直接請你先復現在乾淨核心上。taint 是「這顆核心的可信度被打了折」的記號。

## Module signing：核心信不信這份 `.ko`

`CONFIG_MODULE_SIG` 打開後，核心可以要求模組**帶合法簽章**才准載入。這一步在 `load_module` 早期由 `kernel/module/signing.c` 的 **`mod_verify_sig()`** 做（透過 `module_sig_check`）。

機制：build kernel 時會產生一對簽章金鑰（預設用核心自帶的臨時 key，或你指定的 key），私鑰簽模組、公鑰編進核心的 keyring。`make modules_install` 時每個 `.ko` 被 `scripts/sign-file` 簽名——簽章與一個固定的 magic 字串 `~Module signature appended~\n` 一起**附在檔案結尾**。載入時核心：

1. 從檔案結尾往回找那個 magic 標記，切出簽章區塊。
2. 對剩下的模組本體算雜湊，用核心 keyring 裡的公鑰驗簽。
3. 驗過 → 正常載入；驗不過或沒簽章 → 看策略。

策略由 `CONFIG_MODULE_SIG_FORCE`（或開機參數 `module.sig_enforce=1`）決定：

- **enforce 模式**：未簽章 / 簽章無效的模組**直接拒絕**，回 `-EKEYREJECTED`，`insmod` 印 `Key was rejected by service`。
- **非 enforce 模式**：允許載入，但核心被 taint（`TAINT_UNSIGNED_MODULE`）。

**這裡跟你在 `windows_kernel_driver` 課學的 driver signing 是同一個問題的兩種答案，值得對照**：

| | Linux module signing | Windows driver signing |
|---|---|---|
| 信任根 | 核心編進去的 keyring（可自訂自簽 key） | 微軟的憑證鏈（WHQL / EV 憑證） |
| 誰能簽 | 你自己（build 你的核心時你就是信任根） | 得向微軟送簽（或自簽測試模式） |
| 未簽後果 | 非 enforce 只 taint；enforce 才拒載 | 現代 x64 預設直接拒載（除非測試模式） |
| 誰強制 | 你的 config / secure boot 策略 | OS 內建、預設開啟 |

差別的根源是治理模型：Linux 把信任根交給「編核心的人」（可能是你自己），所以你能自簽自己的模組；Windows 把信任根收在微軟手上，換來對整個生態的統一管控。

**Secure Boot 會把這件事變強制**（接 `linux_boot` 的 UEFI/Secure Boot 章）：當機器開了 Secure Boot，發行版核心通常會自動進入 `sig_enforce`，因為 Secure Boot 的整條信任鏈（firmware → bootloader → kernel）一路驗簽，最後一環理當延伸到「核心載入的模組」——否則一個能載任意未簽模組的核心，就成了繞過 Secure Boot 的破口。這也是為什麼在開了 Secure Boot 的機器上，你自編的模組 `insmod` 會被拒：你的 key 不在核心信任的 keyring 裡，得用 MOK（Machine Owner Key）把自簽 key 註冊進 firmware 才行。

## 底層機制：它怎麼運作（一次完整載入的資料流）

把前面所有格子串成一條完整的執行流，這是本章要能默畫出來的圖：

```
insmod hello.ko
   │  open("hello.ko") → fd
   ▼
finit_module(fd, "", 0)                          ← syscall 進 kernel
   │  kernel_read_file_from_fd() 讀整份 ELF
   ▼
load_module(info, ...)          kernel/module/main.c
   │
   ├─① elf_validity_check       是不是合法 ET_REL？ ── 不是 → -ENOEXEC
   ├─② module_sig_check         簽章對嗎？(signing.c) ── enforce+無效 → -EKEYREJECTED
   │   check_modinfo/vermagic   版本相符？ ──────────── 不符 → -ENOEXEC "Invalid module format"
   │
   ├─③ layout_and_allocate      module_memory_alloc() 在 MODULES_VADDR 區配記憶體
   │                            把 section 搬到最終虛擬位址（真實位址此刻定案）
   │
   ├─④ simplify_symbols         走符號表：每個 SHN_UNDEF
   │      resolve_symbol_wait      → find_symbol() 查 __ksymtab / 已載入模組
   │        查不到 ────────────────────────────────── → -ENOENT "Unknown symbol"
   │        GPL-only 但授權不符 ───── 拒絕 + taint ──── → "Unknown symbol"
   │        依賴別的模組 A ────────── A.refcount++ (ref_module)
   │
   ├─⑤ apply_relocations        arch/x86/kernel/module.c: apply_relocate_add()
   │      R_X86_64_PC32/PLT32   填相對偏移；超過 ±2GB → Overflow
   │  module_enable_text_rox    .text 設唯讀可執行（舊名 module_enable_x）
   │  module_enable_rodata_ro   .rodata 設唯讀（舊名 module_enable_ro）
   │      掛進全域 module list、註冊 kallsyms
   │
   └─⑥ do_init_module           呼叫 module_init 對應的 hello_init()
          回 0     → MODULE_STATE_LIVE，insmod 成功
          回 !=0   → 整個拆掉、釋放記憶體，insmod 拿到 errno
```

`struct module` 的狀態機貫穿全程：`MODULE_STATE_UNFORMED`（剛配好結構）→ `MODULE_STATE_COMING`（③④⑤ 進行中，其他 CPU 看得到但還不能用）→ `MODULE_STATE_LIVE`（⑥ 成功，可正常使用）→ `MODULE_STATE_GOING`（卸載中）。這個狀態欄位讓多核心下的「A 正在載入時 B 想引用 A 的符號」這種 race 有明確語意。

## 卸載：delete_module 與 refcount

`rmmod hello` 走的是另一個 syscall：**`delete_module(const char *name, int flags)`**，核心裡是 `kernel/module/main.c` 的 `SYSCALL_DEFINE2(delete_module, ...)`。它做的事跟載入相反，但多一道關卡：**refcount 檢查**。

每個模組的 `struct module` 有一個 refcount（`module_get()` 加、`module_put()` 減）。什麼時候會加？

- **模組 B 引用模組 A 的匯出符號**：載入 B 時 ④ 的 `ref_module` 把 A 的 refcount 加一。
- **裝置驅動的裝置正被開啟**：某些子系統會在 `open()` 時 `try_module_get()`，`close()` 時 `module_put()`，確保「還有人在用這個驅動」時它不能被卸。
- **模組間顯式依賴**（`/sys/module/A/holders/` 列出誰在 hold A）。

`delete_module` 呼叫 `try_stop_module`，如果 refcount **不為 0**（`module_refcount()` 大於 0），代表還有人依賴它，**拒絕卸載，回 `-EWOULDBLOCK`**，`rmmod` 印 `Module hello is in use`。這就是「明明我 `rmmod` 了它就是不走」的原因——去查 `/sys/module/<name>/refcnt` 和 `holders/` 看誰卡著它。

refcount 為 0 時，`delete_module` 把狀態轉 `MODULE_STATE_GOING`、呼叫模組的 exit 函式（`module_exit` 註冊的那個）、釋放 `module_memory_alloc` 配的記憶體、從全域 list 拔掉。

> **`rmmod -f`（force）為什麼危險**：`CONFIG_MODULE_FORCE_UNLOAD` 允許無視 refcount 強制卸載。這幾乎總是錯的：refcount > 0 代表「還有程式碼路徑握著這個模組裡的函式指標」，強拆之後那些指標指向已釋放的記憶體，下次被呼叫就是 use-after-free、直接 panic。force unload 只在你**確知** refcount 統計本身有 bug（模組自己漏 `module_put`）時的最後手段，而且會 taint 核心。

## 動手：觀測一次真實載入 + 跨模組符號匯出

環境沿用 Ch 0 的 QEMU + gdb + initramfs。這裡做三件事：看載入後核心怎麼呈現這個模組、做一組 A 匯出／B 使用、用 gdb 停在模組函式裡。

### 1. 載入後看核心怎麼記錄它

用 Ch 0 的 `hello.ko`，在 QEMU 的 shell：

```sh
/ # insmod /hello.ko
/ # cat /proc/modules
hello 12288 0 - Live 0xffffffffc0000000 (O)
#     ↑大小  ↑refcount ↑狀態  ↑載入的虛擬位址（在 MODULES_VADDR 區）(O=out-of-tree/tainted)
```

`0xffffffffc0...` 這個位址落在模組區（`MODULES_VADDR`～`MODULES_END`），對照 ③ `layout_and_allocate` 用 `module_memory_alloc` 配出來的。再看 sysfs 的呈現：

```sh
/ # ls /sys/module/hello/
coresize  holders  initstate  refcnt  sections  taint  ...
/ # cat /sys/module/hello/refcnt      # 0，沒人依賴
/ # cat /sys/module/hello/initstate   # live
/ # ls /sys/module/hello/holders/     # 空的，沒有模組依賴 hello
```

`/sys/module/<name>/` 這整棵目錄就是 `struct module` 欄位的 sysfs 投影（Ch 37 的 device model 會講 sysfs 怎麼把核心結構變成檔案）。

### 2. 模組 A 匯出符號、模組 B 使用

這是驗證 ④ 符號解析與 refcount 的最直接方式。寫兩個模組：

```c
// mod_a.c —— 匯出一個符號
#include <linux/module.h>
#include <linux/kernel.h>

void a_service(void)
{
    pr_info("mod_a: a_service called\n");
}
EXPORT_SYMBOL(a_service);            // 換成 EXPORT_SYMBOL_GPL 再測 GPL 門禁

static int __init a_init(void) { pr_info("mod_a loaded\n"); return 0; }
static void __exit a_exit(void) { pr_info("mod_a unloaded\n"); }
module_init(a_init);
module_exit(a_exit);
MODULE_LICENSE("GPL");
```

```c
// mod_b.c —— 使用 A 匯出的符號
#include <linux/module.h>
#include <linux/kernel.h>

extern void a_service(void);         // 宣告：這是外部（A 匯出的）符號

static int __init b_init(void)
{
    a_service();                     // 呼叫 A 的符號 → 觸發 ④ find_symbol
    return 0;
}
static void __exit b_exit(void) { pr_info("mod_b unloaded\n"); }
module_init(b_init);
module_exit(b_exit);
MODULE_LICENSE("GPL");
```

`Makefile` 一次編兩個：

```makefile
obj-m += mod_a.o mod_b.o
KDIR := /path/to/your/linux
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
```

把 `mod_a.ko`、`mod_b.ko` 放進 initramfs，開機後：

```sh
/ # insmod /mod_a.ko          # A 先載，它把 a_service 放進核心匯出符號表
/ # insmod /mod_b.ko          # B 載入時 find_symbol 查到 a_service
mod_b: (dmesg) a_service called
/ # cat /sys/module/mod_a/refcnt    # 1  ← B 依賴 A，A 的 refcount 被加
/ # ls /sys/module/mod_a/holders/   # mod_b  ← 誰 hold 著 A
/ # rmmod mod_a                     # 失敗！
rmmod: can't unload 'mod_a': Resource temporarily unavailable
/ # rmmod mod_b                     # 先卸 B（B 沒人依賴）
/ # rmmod mod_a                     # 現在 A 的 refcount 回 0，可以卸
```

這一整串把 ④ 符號解析和 refcount 依賴（`delete_module` 的 `-EBUSY`）親手跑出來了。**額外實驗**：把 `mod_a.c` 的 `EXPORT_SYMBOL` 改成 `EXPORT_SYMBOL_GPL`，再把 `mod_b.c` 的 `MODULE_LICENSE("GPL")` 改成 `MODULE_LICENSE("Proprietary")`，重編，`insmod mod_b` 會拿到 `Unknown symbol a_service`——即使 A 明明匯出了。這就是 GPL-only 門禁在動作，你會在 dmesg 看到 taint 訊息。

### 3. gdb 停在模組函式裡

沿用 Ch 0 Step 7 的 gdb 連線。載入模組後，模組的符號**不在** `vmlinux` 裡（它是後來動態配到 `MODULES_VADDR` 區的），gdb 一開始不知道它們的位址。`lx-symbols` 就是解這個的：

```sh
# QEMU 裡：
/ # insmod /mod_a.ko
```

```gdb
# gdb 裡（另一個終端，已 target remote :1234、source vmlinux-gdb.py）：
(gdb) lx-symbols
loading @0xffffffffc0000000: .../mod_a.ko
(gdb) break a_service          # 現在符號有位址了，斷點才設得上
(gdb) continue
# QEMU 裡 insmod mod_b.ko → b_init 呼叫 a_service → gdb 停在這
(gdb) backtrace
#0  a_service ()
#1  b_init ()
#2  do_one_initcall ()         # ← 看到了嗎：模組 init 走的是 initcall 機制
#3  do_init_module ()
```

**注意 backtrace 裡的 `do_one_initcall`**——這就是本章「模組 init 是一種動態 initcall」那句話的實錘：`do_init_module`（⑥）透過 `do_one_initcall` 呼叫你的 init 函式，跟 Ch 3 開機時核心跑 built-in initcall 用的是同一個 `do_one_initcall`。同一個機制，開機時跑一批、`insmod` 時跑一個。`lx-symbols` 的底層原理正是讀 `/sys/module/<name>/sections/` 拿到各 section 的載入位址，再 `add-symbol-file` 告訴 gdb——它讀的就是 ③ 配出來的那些位址。

## 對比與取捨

| 主題 | 選項 A | 選項 B | 取捨 |
|---|---|---|---|
| 載入 syscall | `init_module`（傳 buffer） | `finit_module`（傳 fd） | fd 版讓核心自己讀檔，簽章驗證與 IMA 策略能掛在檔案上；現代 `insmod` 預設走 fd 版 |
| 模組記憶體 | `kmalloc`（線性映射區） | `module_memory_alloc`（vmalloc 式的模組區） | 用模組區：大段不受連續實體頁限制、位址範圍固定便於 kallsyms 判定、且落在 ±2GB 內配合 x86 相對定址 |
| 符號可見性 | 所有 non-static 符號可見 | 只有 `EXPORT_SYMBOL` 的可見 | 明確劃定「模組 ABI」這一小塊，核心內部實作可自由重構不破壞模組 |
| 授權門禁 | 只有 `EXPORT_SYMBOL` | 加 `EXPORT_SYMBOL_GPL` | GPL-only 用技術手段表達授權立場；代價是閉源模組拿不到深層 API |
| 未簽模組 | 拒載（enforce） | 允許但 taint | Secure Boot 場景必須 enforce（否則是繞過信任鏈的破口）；開發機通常非 enforce 圖方便 |
| 卸載 refcount | 嚴格檢查（預設） | `-f` 強制無視 | 強制卸幾乎總導致 UAF；只在確知 refcount 統計有 bug 時當最後手段 |

## 踩雷集錦

1. **`Invalid module format` 以為是檔案壞了**：九成是 vermagic 不符——模組是對「另一顆 kernel」編的（`KDIR` 指錯、或用了發行版 headers 而非你 build 的源碼樹）。`modinfo hello.ko | grep vermagic` 對照 `cat /proc/version` 或 `uname -r`，字串要完全一致。這不是檔案損毀，是核心在保護你不載進 ABI 不相容的模組。

2. **`Unknown symbol` 只想到「符號拼錯」**：先分清是哪種。真的沒這符號 → 拼錯或該符號沒被 `EXPORT_SYMBOL`。符號存在但你載不到 → 很可能是 **GPL-only 符號、你的模組沒宣告相容授權**（`dmesg` 會有 taint 提示）。同一個 `Unknown symbol` 訊息，兩種完全不同的病因。

3. **以為 `MODULE_LICENSE("GPL")` 是法律聲明所以可有可無**：它是**技術開關**。少了它（或宣告非 GPL）核心會 taint 並在 `find_symbol` 拒絕所有 `EXPORT_SYMBOL_GPL` 符號——很多現代 API 都是 GPL-only，你會莫名其妙載不了。這不是形式，是會直接讓你 `insmod` 失敗的東西。

4. **`rmmod` 卡住就狂敲 `rmmod -f`**：refcount > 0 是「還有人握著你的函式指標」，強拆就是 use-after-free 排隊等 panic。正確做法是 `cat /sys/module/<name>/refcnt` 看還剩幾、`ls holders/` 看誰卡著、先卸依賴者（或關掉還開著的裝置節點）。`-f` 是絕望手段不是快捷鍵。

5. **開了 Secure Boot 還想 `insmod` 自編模組**：會被 `-EKEYREJECTED` 拒（`Key was rejected by service`）。不是你模組有問題，是核心在 enforce 模式、你的自簽 key 不在信任 keyring。要嘛用 `mokutil` 把 key 註冊進 MOK、要嘛（開發機）關掉 Secure Boot。別浪費時間查模組本身。

6. **gdb `break my_module_func` 說找不到符號**：模組符號不在 `vmlinux` 裡，是動態載入後才有位址的。`insmod` 之後先在 gdb 跑 `lx-symbols`（它去讀 `/sys/module/*/sections/` 補上位址），斷點才設得上。這是 Ch 0 進階提過、這章真的用到的地方。

## 進階：再往深一層

- **`modprobe` 比 `insmod` 聰明在哪**：`insmod` 只載你指定的那一個檔、不管依賴。`modprobe` 會讀 `/lib/modules/$(uname -r)/modules.dep`（由 `depmod` 掃所有模組的匯出/引用符號產生的依賴圖），自動先載依賴的模組。上面「A 匯出 B 使用」若用 `modprobe mod_b`，它會自動先 `insmod mod_a`。生產環境幾乎只用 `modprobe`；`insmod` 主要用在手動測試單一模組。

- **modversions（CRC 符號版本）**：`CONFIG_MODVERSIONS` 開啟後，每個匯出符號額外帶一個 CRC（對函式原型算出來的），存在模組的 `__versions` section。`simplify_symbols` 除了查位址，還比對 CRC——這樣即使 vermagic 相符，只要某個匯出函式的**原型（參數/回傳型別）變了**，CRC 就對不上、拒絕載入。這讓發行版能在不改 vermagic 的前提下，對 ABI 變動做細粒度把關。是 vermagic（粗粒度 config 相容）之外的第二道 ABI 防線。

- **`request_module()`：核心主動叫使用者空間載模組**：核心某處需要一個還沒載的模組（例如掛載一個檔案系統但對應模組沒載）時，會呼叫 `request_module()`，它透過 usermode helper（`/sbin/modprobe`）從**核心**觸發一次使用者空間的 `modprobe`。這是核心與使用者空間罕見的「反向呼叫」，`bpf`/檔案系統/協定模組的自動載入都靠它。

- **`.init.text` 為什麼載入後就釋放**：模組（和核心本身）標了 `__init` 的函式與 `__initdata` 的資料，只在初始化用一次。`do_init_module` 跑完 init 後會呼叫 `module_enable_ro` 前後把 `.init.*` section 的記憶體釋放掉（`module_arch_freeing_init` / free the init region）——這就是為什麼你不能在非 init 函式裡呼叫 `__init` 標記的函式（載入後那段記憶體已經沒了，呼叫它是 UAF）。Ch 3 談 built-in initcall 的 `.init` 釋放是同一個道理。

- **面試常問**：「模組載入時符號怎麼解析？」——標準答案框架就是本章 ④⑤：`find_symbol` 查 `__ksymtab`（區分 GPL）拿位址，`apply_relocate_add` 照重定位表寫進 `.text`。「為什麼要 `EXPORT_SYMBOL`？」——劃定模組 ABI、讓核心內部可自由重構。「`MODULE_LICENSE` 的作用？」——技術上決定能否用 GPL-only 符號與是否 taint，不只是法律聲明。

## 動手練習

1. **默畫 load_module 流程**：不看筆記，畫出從 `finit_module` 到 `do_init_module` 的六格流程，標出每一格失敗會回什麼 errno、`insmod` 印什麼訊息。畫完對照本章的大圖，漏掉的那格就是你沒真懂的地方。

2. **弄壞 vermagic**：把你的模組編好，用 `modinfo` 看它的 vermagic，然後找一顆版本不同的 kernel（或把 `KDIR` 指到 host 的 `/lib/modules/$(uname -r)/build`）重編一份，拿到 QEMU 裡 `insmod`，確認拿到 `Invalid module format`，並用 `dmesg` 看核心印的 vermagic 不符細節。

3. **跑通 GPL 門禁**：完成「動手」第 2 節的 A/B 模組，然後把 A 改成 `EXPORT_SYMBOL_GPL`、B 改成 `MODULE_LICENSE("Proprietary")`，觀察 `insmod mod_b` 失敗與 taint。再把 B 改回 `"GPL"`，確認又能載。你就親手驗證了 GPL-only 符號的門禁邏輯。

4. **gdb 停進模組看 initcall**：`insmod mod_a` 後在 gdb `lx-symbols`、`break a_service`，從 `mod_b` 觸發它，`backtrace` 看到 `do_one_initcall → do_init_module`。確認你看到的呼叫鏈跟本章說的「模組 init 是動態 initcall」對得上。

5. **製造 refcount 卡住並解開**：載入 A、B（B 依賴 A），`rmmod mod_a` 看它失敗，`cat /sys/module/mod_a/refcnt` 和 `ls holders/` 找出誰卡著，按正確順序卸載。全程不准用 `rmmod -f`。

## 本章重點整理

- `insmod`/`modprobe` → `finit_module`（傳 fd，現代預設）/`init_module`（傳 buffer，舊）→ `kernel/module/main.c` 的 `load_module()`；主流程是 copy ELF → 檢查 vermagic/簽章 → `layout_and_allocate`（配在 vmalloc 式模組區）→ `simplify_symbols`（符號解析）→ `apply_relocations`（重定位）→ `do_init_module`（呼叫 init）。
- 模組本質是未連結完成的 `ET_REL` ELF，核心當連結器：`find_symbol` 只在 `__ksymtab`（`EXPORT_SYMBOL` 劃定的模組 ABI）查位址，`EXPORT_SYMBOL_GPL` 加一層授權門禁——`MODULE_LICENSE` 不相容就拿不到 GPL-only 符號且 taint 核心。
- module signing（`CONFIG_MODULE_SIG`）把簽章附在檔案結尾、載入時用核心 keyring 驗；enforce（Secure Boot 場景）拒載未簽模組，否則只 taint。vermagic 則擋掉「為別顆 kernel 編的」模組。
- `module_init` 對 built-in 模組是開機時的 initcall、對 loadable 模組是 `do_init_module` 載入時呼叫的動態 initcall——同一個巨集兩種生命週期。卸載走 `delete_module`，refcount > 0（有人 hold 著）就拒卸，`rmmod -f` 強拆等於 UAF。

## 自我檢核

- [ ] 不看筆記，能按順序說出 `load_module` 的六個階段，以及每一階段失敗對應的 `insmod` 錯誤訊息（`Invalid module format` / `Unknown symbol` / `Key was rejected` 各卡在哪一步）
- [ ] 能解釋為什麼模組配在 `module_memory_alloc` 的模組區而不是 `kmalloc`，並說出這跟 x86 relocation 的 ±2GB 限制的關係
- [ ] 能說清楚 `EXPORT_SYMBOL` 與 `EXPORT_SYMBOL_GPL` 的差別，以及 `MODULE_LICENSE` 在符號解析時扮演什麼角色（不只是法律聲明）
- [ ] 面試被問「模組載入時 kernel 符號怎麼解析並填進模組程式碼」，能答出 `find_symbol` 查 `__ksymtab` + `apply_relocate_add` 重定位這條線
- [ ] 能解釋「模組 init 是一種動態 initcall」是什麼意思，並說出 gdb backtrace 裡會看到 `do_one_initcall`
- [ ] `rmmod` 卡住時，知道去 `/sys/module/<name>/refcnt` 和 `holders/` 診斷，而不是直接 `rmmod -f`；能說出 `-f` 為什麼危險

## 延伸閱讀

### 官方文件

- **[Documentation/kbuild/modules.rst](https://www.kernel.org/doc/html/latest/kbuild/modules.html)**
  - **讀哪裡**：整篇，尤其「Building External Modules」與 symbol export 相關段落
  - **和本章的關聯**：`obj-m`、`KDIR`、模組如何被 kbuild 編出、符號怎麼跨模組匯出的權威說明；本章「動手」的 Makefile 就是它的最小版

- **[Documentation/admin-guide/module-signing.rst](https://www.kernel.org/doc/html/latest/admin-guide/module-signing.html)**
  - **讀哪裡**：整篇，很聚焦
  - **能學到什麼**：module signing 的金鑰產生、`sign-file`、`sig_enforce` 策略、與 Secure Boot 的關係——本章簽署那節的官方版，要在真實環境搞定自簽模組必讀

### 原始碼

- **[kernel/module/main.c @ v6.12（Bootlin）](https://elixir.bootlin.com/linux/v6.12/source/kernel/module/main.c)**
  - **讀哪裡**：`load_module`、`simplify_symbols`、`do_init_module`、`SYSCALL_DEFINE2(delete_module)` 這幾個函式
  - **為什麼值得讀**：本章每一格都對應這裡的一段程式碼；配 gdb 在 `load_module` 設斷點、`insmod` 你的模組單步走一遍，比讀十遍文字有用
  - **前提**：跟完 Ch 0 的 gdb 環境

### 文章 / 書籍

- **[LWN: "Loadable kernel modules"（The kernel's command-line & module 系列）](https://lwn.net/Kernel/Index/#Modules)**
  - **讀哪裡**：挑 module signing、`EXPORT_SYMBOL_GPL` 爭議、modversions 相關的幾篇
  - **為什麼值得讀**：`EXPORT_SYMBOL_GPL` 的授權立場、簽署機制演進的一手討論，理解「為什麼這樣設計」的最佳來源

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 17 章「Modules」
  - **定位**：模組機制的白話總覽（`EXPORT_SYMBOL`、模組依賴、`module_param`）
  - **注意**：書講的是舊 kernel，`kernel/module.c` 在 6.12 已拆成 `kernel/module/` 目錄，vermagic/簽署細節以本章與官方文件的 6.12 版本為準

模組載入通了，你已經能把自己的程式碼動態塞進 running kernel——這是後面每個「寫模組驗證子系統」章節的基礎。接下來（練習 A）我們用這套能力寫第一個像樣的模組並掛一個自訂 syscall，把 Ch 4（syscall）與這章（模組）合起來用。

→ [練習 A：第一個核心模組 + 自訂 syscall](./practice-a-first-module-syscall.md)
