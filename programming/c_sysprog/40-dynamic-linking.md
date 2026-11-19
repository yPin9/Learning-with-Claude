# Ch 40 — 動態連結：PLT/GOT、LD_PRELOAD 與 dlopen

> **目標**：追蹤一支動態連結的程式從 `execve` 到 `main` 之間 `ld.so` 做了什麼，能診斷「檔案明明存在卻 No such file or directory」與「cannot open shared object file」，會用 `LD_PRELOAD` 與 `--wrap` 攔截 `malloc`，並在 gdb 裡親眼看到 GOT 項目從 `puts@plt+6` 被改寫成 `puts` 的真實位址；最後理解 full RELRO、symbol visibility 與 `dlopen` 外掛架構背後的同一套規則。

> **對應原文**：[你所不知道的 C 語言：動態連結器篇](https://hackmd.io/@sysprog/c-dynamic-linkage) 的「跨越程式語言的交流」「從 execve 到 main」「不改原始程式碼，計算 malloc 與 free 的呼叫次數」「PLT 與 GOT：延遲繫結」「Linkage 決定哪些符號進入動態符號表」「執行時期載入：dlopen 與 dlsym」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、GNU ld 2.38、glibc 2.35、gdb 12.1、bash 5.1。原文環境為 Ubuntu 24.04（glibc 2.39、bash 5.2），差異處會標出。

## 為什麼需要這個？

[Ch 39](./39-static-linking.md) 結尾，我們把 `liby.a` 換成 `liby.so`，連結器就不再複製程式碼，只在執行檔裡記一句「我需要 `liby.so`」。這帶來三個好處：記憶體與磁碟裡只有一份 libc；libc 修了安全漏洞，所有程式下次啟動就自動用到新版；程式可以在執行中才決定要載入哪個外掛。

代價是：**連結的最後一步每次啟動都要重做一遍**，而且由另一個程式——動態連結器 `ld.so`——在你看不到的地方完成。這一步失敗時的錯誤訊息常常很誤導；這一步被「借用」時，又能做到不改程式碼就替換 `malloc`。C 標準完全不區分靜態與動態連結：N1570 5.1.1.2 只說外部參照在第 8 個翻譯階段完成解析，沒說是建置時還是載入時。所以本章大部分內容是 **ELF 與 glibc 的約定**，不是 ISO C 的保證——我們會一路標出這條界線。

## 先建立直覺：C ABI 是程式語言之間的通用語

在進入 `ld.so` 之前，先看動態連結為何如此重要。Java 有 JNI，Python 有 `ctypes`，Rust 有 `extern "C"`，Go 有 cgo——每個語言要呼叫作業系統或既有程式庫，最後都落到 **C 的呼叫慣例與 C 的符號名稱**（沒有名稱修飾，見 Ch 39 的 `extern "C"`）。在 GNU/Linux 上，JNI 的 `System.loadLibrary` 和 `ctypes.CDLL` 最後都呼叫 `dlopen`，再以 `dlsym` 依名稱查函式位址。

先用 Python 呼叫一個 C 共享程式庫，讓你看到這個橋：

```c
/* vlib.c：以 -fvisibility=hidden 建置，只匯出標示 LIB_API 的函式 */
#if defined(__GNUC__) && defined(__ELF__)
#define LIB_API __attribute__((visibility("default")))
#else
#define LIB_API
#endif

int internal_step(int x) { return x * 10; }   /* 沒有 static，但不匯出 */
LIB_API int lib_compute(int x) { return internal_step(x) + 1; }
```

```python
import ctypes
lib = ctypes.CDLL("./libv_hidden.so")
print("lib_compute(4) =", lib.lib_compute(4))
try:
    lib.internal_step
except AttributeError as e:
    print("AttributeError:", e)
```

```console
$ gcc -O2 -fPIC -shared -fvisibility=hidden vlib.c -o libv_hidden.so
$ python3 call.py
lib_compute(4) = 41
AttributeError: ./libv_hidden.so: undefined symbol: internal_step
```

Python 直譯器從沒見過 `vlib.c`，卻能以字串 `"lib_compute"` 找到函式並呼叫——因為它出現在 `.so` 的**動態符號表**裡；`internal_step` 沒出現，就找不到。這張表、找它的人（`ld.so`）、找的順序，就是本章的全部內容。

```
 execve("./prog")
     │  核心讀 program header，看到 PT_INTERP = /lib64/ld-linux-x86-64.so.2
     ▼
 ld.so 開始執行 ──┬─ 依 DT_NEEDED 找程式庫（RPATH / LD_LIBRARY_PATH / RUNPATH / cache / 系統目錄）
                  ├─ 先載入 LD_PRELOAD 指定的物件
                  ├─ 映射每個 .so、做重定位（RELATIVE、GLOB_DAT；JUMP_SLOT 可延遲）
                  ├─ 依相依順序呼叫各 .so 的 init_array
                  └─ 跳到執行檔的 _start ──> __libc_start_main ──> main
 執行中 ──── 第一次呼叫 puts@plt → _dl_runtime_resolve → 改寫 GOT（延遲繫結）
       └── dlopen("plugin.so") → 同一套載入流程再跑一次
```

## 從 execve 到 main：PT_INTERP 與「No such file or directory」之謎

動態連結的執行檔帶一個 `PT_INTERP` program header，記錄 ELF 直譯器（interpreter）的路徑。`execve` 時核心依此**先映射 `ld.so`，把控制權交給它**，而不是直接跳到程式的 `_start`。

製造一個直譯器路徑錯誤的執行檔：

```console
$ printf 'int main(void)\n{\n    return 0;\n}\n' > ok.c
$ gcc ok.c -Wl,--dynamic-linker=/lib64/ld-missing.so.2 -o badinterp
$ readelf -l badinterp | grep interpreter
      [Requesting program interpreter: /lib64/ld-missing.so.2]
$ ls -l badinterp
-rwxr-xr-x 1 ypp ypp 15776 Nov 20 00:32 badinterp
$ ./badinterp; echo "exit=$?"
bash: line 7: ./badinterp: No such file or directory
exit=127
```

檔案明明在、也有執行權限，shell 卻說找不到。`strace` 揭露真相：

```console
$ strace -e trace=execve ./badinterp
execve("./badinterp", ["./badinterp"], 0x7ffce20af900 /* 23 vars */) = -1 ENOENT (No such file or directory)
strace: exec: No such file or directory
+++ exited with 1 +++
```

`execve` 本身以 `ENOENT` 失敗。Linux 核心 `fs/binfmt_elf.c` 的 `load_elf_binary()` 處理 `PT_INTERP` 的程式碼大致是：

```c
        if (elf_ppnt->p_type != PT_INTERP)
            continue;
        ...
        retval = elf_read(bprm->file, elf_interpreter, elf_ppnt->p_filesz,
                          elf_ppnt->p_offset);
        ...
        interpreter = open_exec(elf_interpreter);
        kfree(elf_interpreter);
        retval = PTR_ERR(interpreter);
        if (IS_ERR(interpreter))
            goto out_free_ph;
```

`open_exec()` 打開直譯器失敗時傳回的 `-ENOENT` **原封不動**成為 `execve` 的錯誤碼，跟執行檔本身不存在時一模一樣。不同的呼叫者印出不同的訊息：

```console
$ dash -c ./badinterp
dash: 1: ./badinterp: not found
$ python3 -c 'import os; os.execv("./badinterp", ["x"])'
FileNotFoundError: [Errno 2] No such file or directory
```

bash 5.2（Ubuntu 24.04）改進了訊息，會印 `cannot execute: required file not found`，比較容易聯想到直譯器；本課的 bash 5.1 還是印 `No such file or directory`。

真實世界最常見的情境是**在 glibc 系統編的程式拿到 musl 系統（Alpine 容器）上跑**。實測：

```console
$ gcc -O2 hello.c -o hello_glibc
$ docker run --rm -v "$PWD":/w -w /w alpine:3.20 ./hello_glibc; echo "exit=$?"
sh: ./hello_glibc: not found
exit=127
```

Alpine 沒有 `/lib64/ld-linux-x86-64.so.2`（它的直譯器是 `/lib/ld-musl-x86_64.so.1`）。診斷三步：`readelf -l prog | grep interpreter` 看要求哪個直譯器；`ls` 那個路徑；需要時用 [PatchELF](https://github.com/NixOS/patchelf) 的 `--set-interpreter` 改寫，或直接把直譯器當程式執行：

```console
$ /lib64/ld-linux-x86-64.so.2 ./badinterp; echo "direct exit=$?"
direct exit=0
```

不同架構的執行檔則在 `PT_INTERP` 之前就被 `elf_check_arch()` 拒絕，錯誤是 `ENOEXEC`（Exec format error），不會混淆。

> 核心如何辨識 ELF、建立初始堆疊、把控制權交給 `ld.so` 或 `_start`，在 [Ch 41](./41-c-runtime.md) 的「從 execve 到 _start」一節。

## ld.so 搜尋順序：RPATH、LD_LIBRARY_PATH、RUNPATH、ldconfig

找到 `ld.so` 之後，它要依名稱找每個 `DT_NEEDED` 程式庫。準備兩層相依：`prog` → `liby.so` → `libx.so.1`。

```c
/* x.c */
int x(void)
{
    return 40;
}
```

```c
/* y.c */
int x(void);

int y(void)
{
    return x() + 2;
}
```

```c
/* prog.c */
#include <stdio.h>

int y(void);

int main(void)
{
    printf("y() = %d\n", y());
    return 0;
}
```

### SONAME：建置時期與執行時期的名字不一樣

```console
$ F="-std=c11 -Wall -Wextra -Werror -O2"
$ gcc $F -fPIC -shared x.c -Wl,-soname,libx.so.1 -o libx.so.1.0.0
$ ln -s libx.so.1.0.0 libx.so.1
$ ln -s libx.so.1.0.0 libx.so
$ gcc $F -fPIC -shared y.c -L. -lx -o liby.so
$ readelf -d liby.so | grep NEEDED
 0x0000000000000001 (NEEDED)             Shared library: [libx.so.1]
```

三個名字各司其職：

```
libx.so         ──> 建置時 -lx 找的名字（開發套件 libfoo-dev 提供）
libx.so.1       ──> SONAME，寫進使用者的 DT_NEEDED，執行時 ld.so 找的名字
libx.so.1.0.0   ──> 真正的檔案，帶完整版本
```

修 bug 發佈 `libx.so.1.0.1`，只要把 `libx.so.1` 這個符號連結改指過去（`ldconfig` 掃描系統目錄時會代勞），所有程式不必重連；介面不相容時提高 SONAME 主版本號（`libx.so.2`），新舊版本可以並存。

### 建置時期的搜尋 ≠ 執行時期的搜尋

```console
$ gcc $F prog.c -L. -ly -o prog
/usr/bin/ld: warning: libx.so.1, needed by ./liby.so, not found (try using -rpath or -rpath-link)
/usr/bin/ld: ./liby.so: undefined reference to `x'
collect2: error: ld returned 1 exit status
$ gcc $F prog.c -L. -ly -Wl,-rpath-link,. -o prog
$ ./prog; echo "exit=$?"
./prog: error while loading shared libraries: liby.so: cannot open shared object file: No such file or directory
exit=127
$ LD_LIBRARY_PATH=. ./prog
y() = 42
```

兩個失敗各有來由：

1. GNU ld 建立執行檔時預設檢查共享程式庫的未定義符號，所以要找到間接相依 `libx.so.1`；而找**間接相依**時，GNU ld **不用 `-L` 的目錄**，用的是 `-rpath-link`、`-rpath`、`LD_LIBRARY_PATH` 等（見 GNU ld 手冊 `-rpath-link` 條目）。
2. `-L` 和 `-rpath-link` **只影響建置時期**。執行檔裡只留下 `DT_NEEDED` 記錄的名字，執行時 `ld.so` 不知道 `.` 這個目錄，狀態 127 由 `ld.so` 傳回。

`LD_LIBRARY_PATH` 每次都要設定；`-rpath` 則把搜尋路徑寫進執行檔：

```console
$ gcc $F prog.c -L. -ly -Wl,-rpath,'$ORIGIN' -o prog_rp
$ readelf -d prog_rp | grep -E "NEEDED|RUNPATH"
 0x0000000000000001 (NEEDED)             Shared library: [liby.so]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]
$ ./prog_rp; echo "exit=$?"
./prog_rp: error while loading shared libraries: libx.so.1: cannot open shared object file: No such file or directory
exit=127
```

`$ORIGIN` 在執行時展開成「帶有這項設定的物件所在目錄」，程式與程式庫可以一起搬移。這次 `liby.so` 找到了，`libx.so.1` 卻沒有。用 `LD_DEBUG=libs` 看 `ld.so` 實際搜尋了哪裡（把實驗目錄縮寫成 `.`，`trying file=` 行省略）：

```console
$ LD_DEBUG=libs ./prog_rp 2>&1 | grep -E "find library|search" | sed "s|$PWD|.|g"
      6818:	find library=liby.so [0]; searching
      6818:	 search path=./glibc-hwcaps/x86-64-v3:./glibc-hwcaps/x86-64-v2:./tls/x86_64/x86_64:./tls/x86_64:./tls/x86_64:./tls:./x86_64/x86_64:./x86_64:./x86_64:.		(RUNPATH from file ./prog_rp)
      6818:	find library=libc.so.6 [0]; searching
      6818:	 search path=.		(RUNPATH from file ./prog_rp)
      6818:	 search cache=/etc/ld.so.cache
      6818:	find library=libx.so.1 [0]; searching
      6818:	 search cache=/etc/ld.so.cache
      6818:	 search path=/lib/x86_64-linux-gnu/glibc-hwcaps/x86-64-v3:...:/usr/lib		(system search path)
```

關鍵在最後兩行：`libx.so.1` 是 **`liby.so` 的** `DT_NEEDED`，而 `liby.so` 沒有 `DT_RUNPATH`，所以只查 cache 和系統目錄。**`DT_RUNPATH` 只用於該物件自身的直接相依，不會延伸到間接相依。** 每個目錄下會先試 `glibc-hwcaps/x86-64-v3` 等子目錄（glibc 2.33 加入，讓同一程式庫依 CPU 微架構等級提供不同建置）；本課的 glibc 2.35 還會試舊式的 `tls/`、`x86_64/` 子目錄，原文的 glibc 2.39 已沒有這些。

兩種修法：改用舊式的 `DT_RPATH`（它會沿載入鏈延伸），或讓 `liby.so` 自己帶 `DT_RUNPATH`：

```console
$ gcc $F prog.c -L. -ly -Wl,-rpath,'$ORIGIN' -Wl,--disable-new-dtags -o prog_old
$ readelf -d prog_old | grep RPATH
 0x000000000000000f (RPATH)              Library rpath: [$ORIGIN]
$ ./prog_old
y() = 42
$ gcc $F -fPIC -shared y.c -L. -lx -Wl,-rpath,'$ORIGIN' -o liby.so
$ ./prog_rp
y() = 42
```

### 優先順序：實驗證明 RPATH 壓過 LD_LIBRARY_PATH

依 [ld.so(8)](https://man7.org/linux/man-pages/man8/ld.so.8.html)，名稱不含 `/` 時的搜尋順序：

```
 ① DT_RPATH        （只在請求載入的物件沒有 DT_RUNPATH 時採用；沿載入鏈往上，含執行檔的）
 ② LD_LIBRARY_PATH （secure-execution，例如 setuid 程式，會忽略）
 ③ DT_RUNPATH      （只用於該物件自身的 DT_NEEDED）
 ④ /etc/ld.so.cache（ldconfig 產生）
 ⑤ 系統目錄        （Ubuntu 先找 multiarch 目錄，最後是 /lib、/usr/lib）
     └─ 仍找不到 → cannot open shared object file
```

做一個「假的」`liby.so`（傳回 −1）放在 `alt/`，用 `LD_LIBRARY_PATH` 指過去：

```console
$ LD_LIBRARY_PATH=./alt ./prog_old      # DT_RPATH
y() = 42
$ LD_LIBRARY_PATH=./alt ./prog_rp       # DT_RUNPATH
y() = -1
```

`DT_RPATH` 排在 `LD_LIBRARY_PATH` **之前**，使用者無法用環境變數覆寫寫死的路徑，所以 ld.so(8) 不建議使用它；`DT_RUNPATH` 排在之後，可以被覆寫，代價是不延伸到間接相依。現代 GNU ld 預設產生 `DT_RUNPATH`。`dlopen` 依名稱開啟程式庫時套用同一套順序（RPATH/RUNPATH 取自呼叫 `dlopen` 的物件）。

`ldd` 套用同樣規則顯示解析結果：

```console
$ ldd ./prog_rp | sed "s|$PWD|.|g"
	linux-vdso.so.1 (0x000074224d459000)
	liby.so => ././liby.so (0x000074224d447000)
	libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x000074224d200000)
	libx.so.1 => ././libx.so.1 (0x000074224d439000)
	/lib64/ld-linux-x86-64.so.2 (0x000074224d45b000)
```

**注意**：[ldd(1)](https://man7.org/linux/man-pages/man1/ldd.1.html) 明說它在某些情況下會直接執行目標程式，**不要對不信任的執行檔用 `ldd`**，改用 `readelf -d` 或 `objdump -p`。

## 不改原始碼，攔截 malloc

問題：不改原始碼，怎麼知道程式呼叫了幾次 `malloc` 和 `free`？

### 編譯時期介入的侷限

最直接的想法是在標頭檔寫 `#define malloc(n) counted_malloc(n)`（CS:APP 7.13.1 稱為 compile-time interpositioning）。三個問題：只有重新編譯且看得到巨集的檔案會被追蹤；預先編譯的程式庫（包括 libc 自己、libstdc++ 以 `malloc` 實作的 `operator new`）完全看不到；而且在 ISO C 中**不合法**——引入 `<stdlib.h>` 後 `malloc` 是保留識別字，把它定義成巨集是未定義行為（N1570 7.1.3 §1、§2）。要涵蓋程式庫內部的呼叫，就得在連結階段動手。

### 以 LD_PRELOAD 介入

`LD_PRELOAD` 讓 `ld.so` 在載入任何 `DT_NEEDED` 之前，先載入你指定的物件。我們定義一個與 libc 同名的 `malloc`：

```c
/* mcount.c */
#define _GNU_SOURCE /* glibc 2.35 以前，RTLD_NEXT 需要它 */
#include <dlfcn.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdio.h>
#include <unistd.h>

static atomic_ulong n_malloc, n_free;
static int log_fd = -1;

__attribute__((constructor)) static void init(void)
{
    log_fd = dup(STDERR_FILENO);
}

void *malloc(size_t size)
{
    static void *(*_Atomic real_malloc)(size_t);
    void *(*f)(size_t) = atomic_load(&real_malloc);
    if (!f) {
        f = (void *(*)(size_t)) dlsym(RTLD_NEXT, "malloc");
        if (!f)
            _exit(127);
        atomic_store(&real_malloc, f);
    }
    atomic_fetch_add(&n_malloc, 1);
    return f(size);
}

void free(void *ptr)
{
    static void (*_Atomic real_free)(void *);
    void (*f)(void *) = atomic_load(&real_free);
    if (!f) {
        f = (void (*)(void *)) dlsym(RTLD_NEXT, "free");
        if (!f)
            _exit(127);
        atomic_store(&real_free, f);
    }
    atomic_fetch_add(&n_free, 1);
    f(ptr);
}

__attribute__((destructor)) static void report(void)
{
    char buf[64];
    int n = snprintf(buf, sizeof(buf), "malloc: %lu, free: %lu\n",
                     atomic_load(&n_malloc), atomic_load(&n_free));
    if (n > 0 && (size_t) n < sizeof(buf)) {
        ssize_t r = write(log_fd, buf, (size_t) n);
        (void) r;
    }
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 -fPIC -shared mcount.c -o libmcount.so
$ LD_PRELOAD=./libmcount.so ls / > /dev/null
malloc: 266, free: 101
```

不改、不重編 `ls`，就拿到了計數（數字依 `ls` 版本、locale、目錄內容而異；原文環境是 87/18）。這段程式碼每一行都有理由：

- **`dlsym(RTLD_NEXT, "malloc")`**：從「載入順序中排在目前物件之後」的物件開始找下一個 `malloc`，也就是 libc 的。`RTLD_NEXT` 在 glibc 2.35 以前需要 `_GNU_SOURCE`，把第一行拿掉試試：

  ```console
  mcount_nognu.c:20:39: error: ‘RTLD_NEXT’ undeclared (first use in this function); did you mean ‘RTLD_NOW’?
  ```

  glibc 2.36 起不再需要（POSIX 保留了 `RTLD_` 開頭的名字）。另外 glibc 2.34 起 `dlsym` 位於 `libc.so.6`，不必再 `-ldl`。
- **`void *` 轉函式指標**：ISO C 沒有定義物件指標與函式指標之間的轉換（6.3.2.3 §8 只涵蓋函式指標互轉，前者列在附錄 J.5.7 的常見擴充），POSIX 要求它可行。
- **`_Atomic` 的 `real_malloc`**：多個執行緒可能同時第一次呼叫 `malloc`，一般的 `static` 指標會構成 data race（未定義行為，5.1.2.4 §25）。用 `_Atomic` 後，競爭的執行緒各自呼叫 `dlsym` 拿到相同的值，無害。
- **介入函式內不能呼叫可能配置記憶體的函式**，否則遞迴回到自己。所以計數只做 atomic 累加，報告延到解構函式，用 `snprintf` 寫到堆疊上的緩衝區再直接 `write`。
- **`dup(STDERR_FILENO)`**：`ls` 用 `atexit` 登記的收尾函式會關閉 stderr，而共享程式庫的解構函式排在最後，那時 fd 2 已關。把 `dup` 換成直接用 `STDERR_FILENO` 試試：

  ```console
  $ LD_PRELOAD=./libmcount_nodup.so ls / > /dev/null; echo "(ls done)"
  (ls done)
  ```

  報告**一聲不響地消失了**。

`dlsym` 本身會不會呼叫 `malloc`（造成在 `real_malloc` 還是 NULL 時遞迴）？在 glibc 2.35 上實測不會；但舊版 glibc（2.34 以前）的 `dlsym` 在多執行緒程式中會呼叫 `calloc`。[練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 會在 glibc 2.31 上重現這個無窮遞迴並修好它。

計數同時涵蓋程式本身與 libc 內部的呼叫：

```c
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    for (int i = 0; i < 3; i++)
        free(malloc(16));
    puts("done");
    return 0;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O0 app.c -o app
$ LD_PRELOAD=./libmcount.so ./app
malloc: 4, free: 3
done
$ LD_PRELOAD=./libmcount.so ./app > /dev/null
malloc: 4, free: 3
$ gcc -std=c11 -Wall -Wextra -Werror -O2 app.c -o app2
$ LD_PRELOAD=./libmcount.so ./app2
malloc: 1, free: 0
done
$ objdump -d --no-show-raw-insn app2 | sed -n '/<main>:/,/^$/p'
0000000000001060 <main>:
    1060:	endbr64
    1064:	sub    $0x8,%rsp
    1068:	lea    0xf95(%rip),%rdi        # 2004 <_IO_stdin_used+0x4>
    106f:	call   1050 <puts@plt>
    ...
```

（實驗是在擷取輸出的情況下跑的，stdout 是全緩衝，所以 `done` 在 `exit` 清空緩衝區時才寫出、排在報告之後；在終端機上 `done` 會先出現。）`-O0` 多出的一次 `malloc` 是 `puts` 替 `stdout` 配置的緩衝區（到結束都沒釋放）；導向 `/dev/null` 時照樣配置。`-O2` 時程式自己的三組呼叫**整個消失**：`malloc` 是標準函式庫函式，語意為編譯器所知，這段配置沒有可觀察的行為，依 5.1.2.3 §6 可以刪除。**追蹤器只看得到真的發生的呼叫。**

用 `LD_DEBUG=bindings` 看 `ld.so` 把誰綁到誰：

```console
$ LD_DEBUG=bindings LD_PRELOAD=./libmcount.so ./app 2>&1 | grep -E "symbol .(malloc|free)'" | sed "s|$PWD|.|g" | head -4
      7209:	binding file /lib/x86_64-linux-gnu/libc.so.6 [0] to ./libmcount.so [0]: normal symbol `free' [GLIBC_2.2.5]
      7209:	binding file /lib/x86_64-linux-gnu/libc.so.6 [0] to ./libmcount.so [0]: normal symbol `malloc' [GLIBC_2.2.5]
      7209:	binding file ./app [0] to ./libmcount.so [0]: normal symbol `free' [GLIBC_2.2.5]
      7209:	binding file ./app [0] to ./libmcount.so [0]: normal symbol `malloc' [GLIBC_2.2.5]
```

**連 libc.so.6 自己對 `malloc` 的參照都綁到了 `libmcount.so`。**

### 動態連結器的符號搜尋順序

`ld.so` 解析符號時依**載入順序**搜尋全域範圍：執行檔 → `LD_PRELOAD` 的物件 → 依 `DT_NEEDED` 逐層找到的程式庫，**取第一個找到的定義**。

```
          參照 malloc                   參照 malloc（libc 內部）
  ./app ─────────────┐            ┌───────────── libc.so.6
                     ▼            ▼
             ┌──────────────────────────┐
  搜尋順序：  │ app → libmcount.so → libc │   第一個定義在 libmcount.so
             └──────────────────────────┘
                     │ dlsym(RTLD_NEXT, "malloc")：從 libmcount.so 之後接著找
                     ▼
               libc.so.6 的 malloc
```

在 ISO C 的觀點下，`mcount.c` 本身不合法：整個程式出現兩個 `malloc` 外部定義，違反 6.9 §5 的「恰好一個」。介入能運作，靠的是 ELF 的符號解析規則，以及 glibc 手冊〈Replacing malloc〉**明文支援**以符號介入替換 `malloc`。也因為這條規則對所有符號一視同仁，setuid 程式以 secure-execution 模式執行時，`ld.so` 忽略含斜線的 `LD_PRELOAD` 路徑——否則一般使用者就能替換特權程式的函式。

### 程式庫內部的呼叫不一定能介入

libc 內部的 `malloc` 呼叫能被攔截，是 glibc **刻意**讓它們經過符號解析。一般程式庫呢？

```c
/* libfoo.c */
int helper(void)
{
    return 1;
}

int api(void)
{
    return helper();
}
```

```c
/* over.c */
int helper(void)
{
    return 2;
}
```

```c
/* main.c */
#include <stdio.h>

int api(void);

int main(void)
{
    printf("api() = %d\n", api());
    return 0;
}
```

```console
$ gcc $F -fPIC -shared over.c -o libover.so
$ gcc $F -fPIC -shared libfoo.c -o libfoo.so
$ gcc $F main.c -L. -lfoo -Wl,-rpath,'$ORIGIN' -o m
$ ./m
api() = 1
$ LD_PRELOAD=./libover.so ./m
api() = 2
$ objdump -d --no-show-raw-insn libfoo.so | sed -n '/<api>:/,/^$/p'
0000000000001130 <api>:
    1130:	endbr64
    1134:	jmp    1050 <helper@plt>
```

gcc 讓 `api` 經由 `helper@plt` 呼叫——即使 `helper` 就定義在同一個檔案——所以被 `libover.so` 介入了。換不同建置方式（表中是 `LD_PRELOAD=./libover.so ./m` 的輸出，本課環境實測）：

| 建置 `libfoo.so` 的額外選項 | gcc 11 | clang 14 |
|---|---|---|
| 無 | `api() = 2` | `api() = 1` |
| `-Wl,-Bsymbolic-functions` | `api() = 1` | `api() = 1` |
| `-fno-semantic-interposition` | `api() = 1` | `api() = 1` |
| `-fsemantic-interposition` | `api() = 2` | `api() = 2` |

clang 預設的結果：

```console
$ objdump -d --no-show-raw-insn libfoo.so | sed -n '/<api>:/,/^$/p'   # clang -O2
0000000000001110 <api>:
    1110:	mov    $0x1,%eax
    1115:	ret
```

`helper` 被行內展開，根本沒有呼叫可以攔。三個選項的意義：

- **`-Bsymbolic-functions`**（連結器）：共享程式庫對自身函式的參照在連結時直接綁到自身定義，不經 PLT。Ubuntu 22.04 的 `dpkg-buildflags --get LDFLAGS` 輸出是 `-Wl,-Bsymbolic-functions -flto=auto -ffat-lto-objects -flto=auto -Wl,-z,relro`——**以預設旗標打包的 Ubuntu 套件，程式庫內部對自己函式的呼叫不受 `LD_PRELOAD` 影響**；對其他程式庫函式（如 `malloc`）仍可介入。
- **`-fno-semantic-interposition`**（編譯器）：假設匯出的函式不會被替換，可以內嵌或直接呼叫。gcc 預設保守（`-fsemantic-interposition`），clang 預設較激進。
- clang `-O0` 不內嵌，結果與 gcc 預設相同（實測 `api() = 2`）。

**教訓**：用 `LD_PRELOAD` 寫測試替身或熱修補時，能不能攔到「程式庫內部呼叫」取決於別人怎麼建置那個程式庫。

### 連結時期介入：--wrap

靜態連結的程式沒有 `ld.so` 可借力。GNU ld 的 `--wrap=symbol` 在連結時期做同樣的事：把對 `symbol` 的未定義參照改成 `__wrap_symbol`，把對 `__real_symbol` 的參照改回原本的 `symbol`（CS:APP 7.13.2）：

```c
/* wrap.c */
#include <stddef.h>

void *__real_malloc(size_t size);

unsigned long wrap_count;

void *__wrap_malloc(size_t size)
{
    wrap_count++;
    return __real_malloc(size);
}
```

```c
/* appw.c */
#include <stdio.h>
#include <stdlib.h>

extern unsigned long wrap_count;

int main(void)
{
    char *p = malloc(16);
    if (!p)
        return 1;
    snprintf(p, 16, "%lu", wrap_count);
    puts(p);
    free(p);
    printf("wrap_count = %lu\n", wrap_count);
    return 0;
}
```

```console
$ gcc $F appw.c wrap.c -Wl,--wrap=malloc -o appw && ./appw
1
wrap_count = 1
$ gcc $F -static appw.c wrap.c -Wl,--wrap=malloc -o appws && ./appws
5
wrap_count = 6
```

`--wrap` **只改寫這次連結的輸入檔**中的參照。動態連結時 `puts` 在 `libc.so.6` 內部配置緩衝區，那不在輸入檔中，所以只算到 `main` 那一次。靜態連結時 `libc.a` 的成員也是輸入檔，`main` 之前就已經有 4 次。用 gdb 抓出是誰：

```console
$ gdb -q -batch -ex 'break __wrap_malloc' -ex run -ex 'bt 6' -ex c -ex 'bt 6' ... ./appws_g
#1  0x0000000000480beb in _dl_get_origin ()
#2  0x0000000000454287 in _dl_non_dynamic_init ()
...
#1  0x000000000047d6d1 in _dl_init_paths ()
...
#1  0x000000000047d6ee in _dl_init_paths ()
...
#1  0x000000000048a04f in _dl_find_object_init ()
...
#1  0x000000000040178b in main () at appw.c:9
```

都是靜態 glibc 為了支援靜態程式中的 `dlopen` 而做的初始化（`_dl_get_origin` 記錄執行檔路徑以展開 `$ORIGIN`），[Ch 41](./41-c-runtime.md) 的 strace 會看到對應的 `readlink("/proc/self/exe")`。所以 `main` 配置時計數已是 5，`puts` 配置緩衝區後成為 6。原文在 glibc 2.39 上得到 7/8（多了 libgcc 登記 unwind 資訊的 2 次）——**這類數字隨 glibc 版本變動，不要當成常數**。

| 介入方式 | 時機 | 涵蓋程式庫內部呼叫？ | 適用 |
|---|---|---|---|
| 巨集 `#define` | 編譯 | 否 | 只追自己的程式碼；ISO C 不合法 |
| `--wrap` | 連結 | 只限這次連結的輸入檔（靜態連結時含 libc.a） | 靜態連結、單元測試替身 |
| `LD_PRELOAD` | 載入 | 是，若程式庫內部呼叫經過 PLT | 不重編的追蹤、替換配置器（jemalloc、tcmalloc） |

## PLT 與 GOT：延遲繫結（gdb 實際追一次）

共享程式庫的載入位址到執行期才決定，程式碼頁面又要保持唯讀才能被多個行程共用。所以 PIC 程式碼存取外部符號時多加一層間接（CS:APP 7.12）：

- **GOT（Global Offset Table）**：存放外部變數與函式的實際位址，由 `ld.so` 填入。GOT 在資料區，可寫。
- **PLT（Procedure Linkage Table）**：每個外部函式一小段跳板程式碼，從 GOT 讀目標位址後跳過去。PLT 在程式碼區，唯讀。

```c
/* plt.c */
#include <stdio.h>

int main(void)
{
    puts("first");
    puts("second");
    return 0;
}
```

Ubuntu 的 gcc 對 PIE 預設傳入 `-z now`，並以 `-fcf-protection` 產生帶 `endbr64` 的 PLT。為了觀察**傳統的延遲繫結（lazy binding）**，改用 `-fcf-protection=none -Wl,-z,lazy`：

```console
$ gcc $F -fcf-protection=none -Wl,-z,lazy plt.c -o plt_lazy
$ readelf -W -r plt_lazy | grep puts
0000000000004018  0000000300000007 R_X86_64_JUMP_SLOT     0000000000000000 puts@GLIBC_2.2.5 + 0
$ objdump -d --no-show-raw-insn -j .plt plt_lazy
0000000000001020 <puts@plt-0x10>:
    1020:	push   0x2fe2(%rip)        # 4008 <_GLOBAL_OFFSET_TABLE_+0x8>
    1026:	jmp    *0x2fe4(%rip)        # 4010 <_GLOBAL_OFFSET_TABLE_+0x10>
    102c:	nopl   0x0(%rax)

0000000000001030 <puts@plt>:
    1030:	jmp    *0x2fe2(%rip)        # 4018 <puts@GLIBC_2.2.5>
    1036:	push   $0x0
    103b:	jmp    1020 <_init+0x20>
$ objdump -d --no-show-raw-insn plt_lazy | sed -n '/<main>:/,/^$/p'
0000000000001050 <main>:
    1050:	sub    $0x8,%rsp
    1054:	lea    0xfa9(%rip),%rdi        # 2004 <_IO_stdin_used+0x4>
    105b:	call   1030 <puts@plt>
    1060:	lea    0xfa3(%rip),%rdi        # 200a <_IO_stdin_used+0xa>
    1067:	call   1030 <puts@plt>
    ...
```

`puts` 的 GOT 項目在偏移 `0x4018`（`R_X86_64_JUMP_SLOT` 的 Offset 欄）。gdb 預設關閉 ASLR，PIE 的載入基底是 `0x555555554000`，所以 GOT 項目在 `0x555555558018`。在兩次 `call` 前各停一次（`main+11` = `0x105b`、`main+23` = `0x1067`），中間單步走進第一次呼叫：

```console
$ gdb -q -batch -ex 'break *main+11' -ex 'break *main+23' -ex 'run > /dev/null' \
    -ex 'x/a 0x555555558018' -ex 'x/4a 0x555555558000' \
    -ex stepi -ex 'x/i $pc' -ex stepi -ex 'x/i $pc' -ex stepi -ex 'x/i $pc' \
    -ex stepi -ex 'x/i $pc' -ex stepi -ex 'x/i $pc' \
    -ex continue -ex 'x/a 0x555555558018' ./plt_lazy

Breakpoint 1, 0x000055555555505b in main ()
0x555555558018 <puts@got.plt>:	0x555555555036 <puts@plt+6>
0x555555558000:	0x3df8	0x7ffff7ffe2e0
0x555555558010:	0x7ffff7fd8d50 <_dl_runtime_resolve_xsavec>	0x555555555036 <puts@plt+6>
=> 0x555555555030 <puts@plt>:	jmp    *0x2fe2(%rip)        # 0x555555558018 <puts@got.plt>
=> 0x555555555036 <puts@plt+6>:	push   $0x0
=> 0x55555555503b <puts@plt+11>:	jmp    0x555555555020
=> 0x555555555020:	push   0x2fe2(%rip)        # 0x555555558008
=> 0x555555555026:	jmp    *0x2fe4(%rip)        # 0x555555558010

Breakpoint 2, 0x0000555555555067 in main ()
0x555555558018 <puts@got.plt>:	0x7ffff7c80da0 <__GI__IO_puts>
```

逐行讀：

1. 第一次呼叫前，`GOT[puts]` 指向 **`puts@plt+6`**——也就是 PLT 自己的下一道指令。
2. `GOT[0..2]` 保留給 `ld.so`：`GOT[0]` = `0x3df8`（`_DYNAMIC` 的連結時期位址）、`GOT[1]` = `0x7ffff7ffe2e0`（這個物件的 `link_map`，`ld.so` 用來辨識「是誰在問」）、`GOT[2]` = `_dl_runtime_resolve_xsavec`（解析程序的進入點）。
3. 單步：`jmp *GOT[puts]` 跳回 `puts@plt+6` → `push $0`（**重定位索引**：`.rela.plt` 的第 0 筆）→ 跳到 PLT0 → `push GOT[1]`（link_map）→ `jmp *GOT[2]` 進入 `ld.so`。
4. `_dl_runtime_resolve_xsavec` 保存傳遞引數的暫存器（`xsavec` 是依 CPU 支援的指令挑選的版本），呼叫 glibc `elf/dl-runtime.c` 的 `_dl_fixup`：用 link_map 和重定位索引找到 `R_X86_64_JUMP_SLOT` 項目，搜尋 `puts`，**把位址寫回 GOT**，再傳回跳板還原暫存器後跳往 `puts`。
5. 第二次呼叫前，`GOT[puts]` 已經是 libc 裡 `puts` 的真實位址，`jmp *GOT[puts]` 一步到位。

```
第一次：main ─call─> puts@plt ─jmp *GOT─> puts@plt+6 ─push 0─> PLT0 ─push link_map, jmp *GOT[2]─>
        _dl_runtime_resolve ─> _dl_fixup（查 puts、寫回 GOT[puts]）─> puts
其後：  main ─call─> puts@plt ─jmp *GOT─────────────────────────────────────────> puts
```

**延遲繫結的好處**：從沒被呼叫的函式不必解析（大程式庫可能有上千個）。**代價**：`.got.plt` 在整個執行期間都必須可寫。

x86-64 常見的動態重定位三種：`R_X86_64_JUMP_SLOT`（PLT 對應的 GOT 項目，可延遲）、`R_X86_64_GLOB_DAT`（其他 GOT 項目，如外部變數的位址、被取位址的函式，載入時填）、`R_X86_64_RELATIVE`（只加載入基底，不必搜尋符號；Ch 39 的 `DT_RELR` 壓縮的就是它）。

## full RELRO：讓 GOT 變唯讀

用預設選項建置同一程式：

```console
$ gcc $F plt.c -o plt_now
$ readelf -d plt_now | grep FLAGS
 0x000000000000001e (FLAGS)              BIND_NOW
 0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE
$ objdump -d --no-show-raw-insn -j .plt.sec plt_now
0000000000001050 <puts@plt>:
    1050:	endbr64
    1054:	bnd jmp *0x2f75(%rip)        # 3fd0 <puts@GLIBC_2.2.5>
    105b:	nopl   0x0(%rax,%rax,1)
$ gdb -q -batch -ex 'break main' -ex 'run > /dev/null' -ex 'x/a 0x555555557fd0' ./plt_now
Breakpoint 1, 0x0000555555555060 in main ()
0x555555557fd0 <puts@got.plt>:	0x7ffff7c80da0 <__GI__IO_puts>
```

`-z now` 讓 `ld.so` 在啟動時就解析所有符號（`main` 一開始 GOT 已填好）。搭配 `-z relro`，`ld.so` 重定位完成後以 `mprotect` 把 `PT_GNU_RELRO` 涵蓋的範圍設為唯讀；連結時兩者都指定，連結器才把 PLT 用的 GOT 項目也排進這個範圍——這個組合稱為 **full RELRO**。（`bnd` 前綴就我所知是為了相容 Intel MPX 而留下的 PLT 格式；MPX 已被淘汰，在不支援 MPX 的處理器上這個前綴不影響執行。）

**這有什麼用？** 改寫 GOT 項目是經典的控制流劫持手法：攻擊者若有一個任意寫入的漏洞，把 `GOT[puts]` 改成自己想要的位址，下次呼叫 `puts` 就跳過去。我們在自己的行程裡模擬這個「任意寫入」：

```c
/* gotw.c：在自己的行程裡改寫 puts 的 GOT 項目，觀察 RELRO 的效果 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

extern void *_GLOBAL_OFFSET_TABLE_[];

static int fake_puts(const char *s)
{
    static const char msg[] = "hijacked!\n";
    (void) s;
    return (int) write(1, msg, sizeof msg - 1);
}

int main(void)
{
    puts("before"); /* 先呼叫一次，延遲繫結時 GOT 項目才會填好 */
    void *real = dlsym(RTLD_DEFAULT, "puts");
    void **slot = NULL;
    for (int i = 3; i < 12; i++)            /* GOT[0..2] 保留給 ld.so */
        if (_GLOBAL_OFFSET_TABLE_[i] == real) {
            slot = &_GLOBAL_OFFSET_TABLE_[i];
            break;
        }
    if (!slot) {
        puts("puts slot not found");
        return 1;
    }
    printf("GOT slot for puts: GOT[%td]\n", slot - _GLOBAL_OFFSET_TABLE_);
    fflush(stdout);
    *slot = (void *) fake_puts;             /* 改寫 GOT */
    puts("after");
    return 0;
}
```

（用 `dlsym` 取得 `puts` 的位址而不是直接寫 `(void *) puts`：對 `puts` 取位址會讓編譯器另外產生一個 `GLOB_DAT` 的 GOT 項目，找到的就不是 PLT 用的那一格。）

```console
$ gcc -std=c11 -Wall -Wextra -O0 -Wl,-z,lazy -Wl,-z,norelro gotw.c -o gotw_norelro && ./gotw_norelro
before
GOT slot for puts: GOT[3]
hijacked!
$ gcc -std=c11 -Wall -Wextra -O0 -Wl,-z,lazy gotw.c -o gotw_partial && ./gotw_partial
before
GOT slot for puts: GOT[3]
hijacked!
$ gcc -std=c11 -Wall -Wextra -O0 gotw.c -o gotw_full && ./gotw_full; echo "exit=$?"
before
GOT slot for puts: GOT[3]
Segmentation fault (core dumped)
exit=139
```

三種組態的差別：

| 組態 | 旗標 | `GNU_RELRO` 範圍 | `.got.plt` | 改寫 GOT |
|---|---|---|---|---|
| no RELRO | `-z norelro -z lazy` | 無 | 可寫 | 成功 |
| partial RELRO | `-z relro -z lazy` | `0x3de8`–`0x4000` | 從 `0x4000` 起，**在範圍外** | 成功 |
| full RELRO | `-z relro -z now`（Ubuntu 預設） | `0x3d98`–`0x4000` | 併入 `.got`，**在範圍內** | SIGSEGV |

partial RELRO 只保護 `.got`、`.dynamic` 等，不保護延遲繫結需要改寫的 `.got.plt`。執行時設 `LD_BIND_NOW=1` 只讓解析提早，**不改變**連結時決定的保護範圍。注意 Ubuntu 的 gcc 只對 PIE 執行檔傳入 `-z now`，建立共享程式庫時只有 `-z relro`——下面 `dlopen` 一節會看到它的副作用。

> GOT 改寫、ROP、canary 等攻防的全貌見 [Ch 14 — 函式呼叫與資安](./14-function-call-security.md)。本節的改寫只在自己的行程內示範防護機制的效果。

## Linkage 決定誰進入動態符號表：symbol visibility

能被介入、能被 `dlsym` 找到的前提是符號出現在 **`.dynsym`**，而這從 C 的 linkage 開始（N1570 6.2.2）：`static` 是 internal linkage，只在 `.symtab` 留下 `LOCAL` 符號，`ld.so` 看不到；沒有 `static` 的檔案範圍定義是 external linkage，共享程式庫預設把它們全部放進 `.dynsym`。

ISO C 只有三種 linkage，沒有「在共享程式庫內各檔案共用、但不對外匯出」這一層。gcc/clang 用 **visibility** 屬性補上，對應 ELF 符號的 `st_other` 欄位：

```c
/* syms.c */
static int local(void) { return 0; }
int global(void) { return local(); }
__attribute__((weak)) int weak(void) { return 1; }
__attribute__((visibility("hidden"))) int hidden(void) { return 2; }
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -c syms.c -o syms.o
$ LC_ALL=C readelf -W --syms syms.o | tail -4
     3: 0000000000000000    15 FUNC    LOCAL  DEFAULT    1 local
     4: 000000000000000f    15 FUNC    GLOBAL DEFAULT    1 global
     5: 000000000000001e    15 FUNC    WEAK   DEFAULT    1 weak
     6: 000000000000002d    15 FUNC    GLOBAL HIDDEN     1 hidden
$ gcc -shared -fPIC syms.c -o libsyms.so
$ readelf -W --dyn-syms libsyms.so | grep FUNC
     5: 0000000000001108    15 FUNC    GLOBAL DEFAULT   10 global
     6: 0000000000001117    15 FUNC    WEAK   DEFAULT   10 weak
$ readelf -W --syms libsyms.so | grep -E ' (local|hidden)$'
    10: 00000000000010f9    15 FUNC    LOCAL  DEFAULT   10 local
    14: 0000000000001126    15 FUNC    LOCAL  DEFAULT   10 hidden
```

在目的檔中 `hidden` 仍是 `GLOBAL`（同一個 `.so` 的其他 `.c` 可以呼叫它），連結成共享程式庫後變成 `LOCAL`，與 `static` 的 `local` 一樣不進 `.dynsym`。

大型函式庫的慣例：以 `-fvisibility=hidden` 編譯，只對公開 API 標 `default`（就是本章開頭的 `vlib.c`）。比較兩種建置：

```console
$ gcc -O2 -fPIC -shared vlib.c -o libv_default.so
$ gcc -O2 -fPIC -shared -fvisibility=hidden vlib.c -o libv_hidden.so
$ nm -D --defined-only libv_default.so
0000000000001120 T internal_step
0000000000001130 T lib_compute
$ nm -D --defined-only libv_hidden.so
0000000000001110 T lib_compute
$ objdump -d --no-show-raw-insn libv_default.so | sed -n '/<lib_compute>:/,/^$/p'
0000000000001130 <lib_compute>:
    1130:	endbr64
    1134:	sub    $0x8,%rsp
    1138:	call   1050 <internal_step@plt>
    113d:	add    $0x8,%rsp
    1141:	add    $0x1,%eax
    1144:	ret
$ objdump -d --no-show-raw-insn libv_hidden.so | sed -n '/<lib_compute>:/,/^$/p'
0000000000001110 <lib_compute>:
    1110:	endbr64
    1114:	lea    (%rdi,%rdi,4),%eax
    1117:	lea    0x1(%rax,%rax,1),%eax
    111b:	ret
```

預設可見性下，連同一檔案內的呼叫都經過 PLT（因為 `internal_step` 可能被介入）；hidden 之後，編譯器知道沒人能替換它，直接內嵌成 `x*5*2+1`。收益有三：**匯出符號少**（`ld.so` 啟動時的搜尋與重定位工作少）、**內部呼叫不經 PLT**（可內嵌）、**ABI 只剩明確標示的部分**（內部符號不會和其他程式庫衝突或被介入）。

不改原始碼時，可用**版本腳本（version script）**列出匯出清單：

```console
$ printf '{\n    global: api;\n    local: *;\n};\n' > foo.map
$ gcc -O2 -fPIC -shared -Wl,--version-script=foo.map libfoo.c -o libfoo.so
$ nm -D --defined-only libfoo.so
0000000000001110 T api
$ objdump -d --no-show-raw-insn libfoo.so | sed -n '/<api>:/,/^$/p'
0000000000001110 <api>:
    1110:	endbr64
    1114:	jmp    1100 <helper>
$ LD_PRELOAD=./libover.so ./m
api() = 1
```

`helper` 不再匯出，`api` 直接 `jmp 1100 <helper>`（沒有 `@plt`），`libover.so` 無從介入。Windows 的 DLL 則相反：預設**只**匯出以 `__declspec(dllexport)` 或 `.DEF` 檔明確標示的符號。

### 順帶一提：C99 inline 的 linkage 陷阱

linkage 的另一個常見連結錯誤來自 `inline`。N1570 6.7.4 §7：若某轉譯單元中一個 external linkage 函式的所有宣告都帶 `inline` 而不帶 `extern`，那只是 **inline 定義**，**不提供**外部定義：

```c
inline int twice(int x)
{
    return 2 * x;
}

int main(void)
{
    return twice(21) - 42;
}
```

```console
$ gcc -std=c11 -O0 twice.c -o t0
/usr/bin/ld: /tmp/ccTZF5C7.o: in function `main':
twice.c:(.text+0xe): undefined reference to `twice'
collect2: error: ld returned 1 exit status
$ gcc -std=c11 -O1 twice.c -o t1 && ./t1; echo "rc=$?"
rc=0
```

`-O1` 以上呼叫被內嵌，連結器沒發現定義不存在——**連結成功不代表程式合乎標準**（6.9 §5 仍要求恰好一個外部定義）。解法：在某一個 `.c` 加上 `extern inline int twice(int x);` 提供外部定義，或改用 `static inline`（各版標準語意一致，也不進 `.dynsym`）。gcc 的 `-fgnu89-inline` 下 `inline` 與 `extern inline` 的語意恰好和 C99 對調，混用兩套語意的標頭是常見錯誤來源。

## dlopen / dlsym：外掛架構

`DT_NEEDED` 在啟動時載入，`dlopen`/`dlsym` 把載入延到執行中的任意時刻——外掛架構、JNI、`ctypes` 都建立在它上面（Windows 對應 `LoadLibrary`/`GetProcAddress`）。它們是 POSIX 定義介面、由 C 函式庫提供的函式，**不是系統呼叫**。

```c
/* host.c */
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

int host_version(void)
{
    return 3;
}

int main(int argc, char *argv[])
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s lib.so [now]\n", argv[0]);
        return 2;
    }
    int mode = (argc > 2 && !strcmp(argv[2], "now")) ? RTLD_NOW : RTLD_LAZY;
    void *h = dlopen(argv[1], mode);
    if (!h) {
        fprintf(stderr, "dlopen: %s\n", dlerror());
        return 1;
    }
    int (*entry)(int) = (int (*)(int)) dlsym(h, "entry");
    if (entry)
        printf("entry(21) = %d\n", entry(21));
    else
        printf("dlsym: %s\n", dlerror());
    dlclose(h);
    return 0;
}
```

三個外掛：正常的、參照不存在函式的、回頭參照宿主程式函式的：

```c
/* plugin.c */
#include <stdio.h>

__attribute__((constructor)) static void plugin_init(void)
{
    puts("plugin: loaded");
}

__attribute__((destructor)) static void plugin_fini(void)
{
    puts("plugin: unloaded");
}

int entry(int x)
{
    return x * 2;
}
```

```c
/* broken.c */
void missing_function(void); /* 沒有任何地方定義 */

int entry(int x)
{
    missing_function();
    return x;
}
```

```c
/* ext.c */
int host_version(void);

int entry(int x)
{
    return x * host_version();
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror host.c -o host
$ for p in plugin broken ext; do gcc -std=c11 -Wall -Wextra -Werror -O2 -fPIC -shared $p.c -o $p.so; done
$ ./host ./plugin.so
plugin: loaded
entry(21) = 42
plugin: unloaded
```

`constructor`/`destructor` 經由 `.init_array`/`.fini_array` 執行：前者在程式庫首次載入時、後者在從位址空間移除或行程結束時。`LD_DEBUG=files` 看得到 `ld.so` 的動作：

```console
$ LD_DEBUG=files ./host ./plugin.so 2>&1 | grep -E 'file=|calling' | sed "s|$PWD|.|g"
     10911:	file=libc.so.6 [0];  needed by ./host [0]
     10911:	calling init: /lib64/ld-linux-x86-64.so.2
     10911:	calling init: /lib/x86_64-linux-gnu/libc.so.6
     10911:	file=./plugin.so [0];  dynamically loaded by ./host [0]
     10911:	calling init: ./plugin.so
     10911:	calling fini: ./plugin.so [0]
     10911:	file=./plugin.so [0];  destroying link map
```

### 故意弄壞：四種失敗

**1. 名字不含斜線**：

```console
$ ./host plugin.so; echo "exit=$?"
dlopen: plugin.so: cannot open shared object file: No such file or directory
exit=1
```

不含 `/` 時走前面那套搜尋順序，**不會**找目前目錄。

**2. 找不到符號**：

```console
$ ./host ./noentry.so
dlsym: ./noentry.so: undefined symbol: entry
```

**3. 延遲繫結把錯誤延到呼叫時**：

```console
$ ./host ./broken.so; echo "exit=$?"
./host: symbol lookup error: ./broken.so: undefined symbol: missing_function
exit=127
$ ./host ./broken.so now
dlopen: ./broken.so: undefined symbol: missing_function
$ LD_BIND_NOW=1 ./host ./broken.so
dlopen: ./broken.so: undefined symbol: missing_function
```

`broken.so` 能夠建立，是因為共享程式庫預設允許未定義符號；它也沒有 `BIND_NOW` 旗標（Ubuntu 只對 PIE 傳 `-z now`）。以 `RTLD_LAZY` 載入，`dlopen` 和 `dlsym` 都成功，直到呼叫 `entry` 時 `missing_function` 的 PLT 才經 `_dl_fixup` 解析——找不到，**`ld.so` 以狀態 127 終止整個行程**，宿主程式連處理的機會都沒有。`RTLD_NOW`（或 `LD_BIND_NOW=1`）讓錯誤成為 `dlopen` 的傳回值。根本解法是建立程式庫時就以 `-z defs` 攔下：

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 -fPIC -shared -Wl,-z,defs broken.c -o broken2.so
/usr/bin/ld: /tmp/ccrDtgvk.o: in function `entry':
broken.c:(.text+0xa): undefined reference to `missing_function'
collect2: error: ld returned 1 exit status
```

**4. 外掛找不到宿主的函式**：

```console
$ ./host ./ext.so now
dlopen: ./ext.so: undefined symbol: host_version
$ nm -D --defined-only host | grep host_version || echo "(host_version not in .dynsym)"
(host_version not in .dynsym)
$ gcc -std=c11 -Wall -Wextra -Werror -rdynamic host.c -o host_rd
$ ./host_rd ./ext.so now
entry(21) = 63
$ printf '{ host_version; };\n' > host.list
$ gcc -std=c11 -Wall -Wextra -Werror -Wl,--dynamic-list=host.list host.c -o host_dl
$ ./host_dl ./ext.so now
entry(21) = 63
```

共享程式庫匯出所有預設可見性的 external linkage 定義，**執行檔卻只把建置時有共享程式庫參照的符號放進 `.dynsym`**——建置 `host` 時沒有任何程式庫參照 `host_version`。`-rdynamic`（`-Wl,--export-dynamic`）匯出執行檔所有全域符號；`--dynamic-list` 只加列出的那幾個，更精確。

### 外掛設計的其他注意事項

- **`RTLD_LOCAL` vs `RTLD_GLOBAL`**：glibc 預設 `RTLD_LOCAL`，載入的符號只供同一個 `dlopen` 群組使用；若外掛 A 要用外掛 B 的符號而又沒有 `DT_NEEDED` 記錄，B 要以 `RTLD_GLOBAL` 開啟。
- **C++ 外掛**：`dlsym` 要的是修飾後的名字，慣例是只匯出單一 `extern "C"` 進入點，由它傳回函式表或物件指標。
- **`dlclose` 之後**：仍持有外掛裡的函式指標或字串，就是 use-after-free；`RTLD_NODELETE` 或外掛仍被參照時 `dlclose` 只遞減計數。

## 從實作學習動態連結器

想看完整實作的幾個入口：

- **[min-dl](https://github.com/jserv/min-dl)**（jserv）：以 `mmap` 載入共享程式庫，處理符號表、雜湊表、重定位與 init/fini 陣列；載入時只做 `RELATIVE`，`GLOB_DAT`/`JUMP_SLOT` 由呼叫者解析，另提供回呼式的 PLT 跳板示範延遲解析。本體約一千行，是讀懂本章機制的最佳實作。
- **[dynld](https://github.com/johannst/dynld)**：以 C 與 x86-64 組合語言分章實作，從 no-std 執行檔一路做到能載入一個共享程式庫的動態連結器。
- **glibc 的 `elf/` 目錄**：`rtld.c`（`ld.so` 主程式）、`dl-load.c`（搜尋路徑）、`dl-runtime.c`（`_dl_fixup`）、`sysdeps/x86_64/dl-trampoline.S`（`_dl_runtime_resolve_*`）。本章 gdb 看到的每個名字都在這裡。
- Android 的 Bionic 有自己的連結器（`bionic/linker/`）；早期限制促成了 Chromium 的 crazy linker 與 Meta 的 SoLoader，原文有詳細歷史。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| `DT_RPATH` | 延伸到間接相依；使用者無法覆寫（部署穩定） | 使用者無法覆寫（除錯困難）；ld.so(8) 不建議 | 舊專案；需要強制路徑時 |
| `DT_RUNPATH` | 可被 `LD_LIBRARY_PATH` 覆寫 | 不延伸到間接相依，每個 `.so` 要自己宣告 | 現代預設；搭配 `$ORIGIN` 做可搬移的套件 |
| 延遲繫結 `-z lazy` | 啟動快、不解析沒用到的函式 | `.got.plt` 可寫；缺符號延到呼叫時才炸 | 舊預設；巨型程式庫的啟動時間敏感場景 |
| `-z now` + `-z relro` | full RELRO 保護 GOT；錯誤在啟動時就發現 | 啟動時解析全部符號 | 現代發行版預設（PIE） |
| 預設可見性 | 什麼都能被介入、被 `dlsym` | 匯出太多、內部呼叫走 PLT、ABI 失控 | 小型或內部程式庫 |
| `-fvisibility=hidden` + `LIB_API` | ABI 明確、可內嵌、啟動快 | 要在原始碼標示 | 公開發行的程式庫 |

## 踩雷集錦

1. **「No such file or directory 就是檔案不存在」**：對動態連結的 ELF，也可能是 `PT_INTERP` 指向的直譯器不存在（跨 libc、容器缺 `ld.so`）。先 `readelf -l | grep interpreter`。
2. **「`-L.` 都寫了，為什麼執行時找不到 `.so`」**：`-L` 只影響建置時期。執行時看 `DT_RUNPATH`/`LD_LIBRARY_PATH`/cache/系統目錄。用 `-Wl,-rpath,'$ORIGIN'`，注意 `'$ORIGIN'` 要用單引號防止 shell 展開。
3. **「執行檔設了 RUNPATH，間接相依也會照這個路徑找」**：不會。`DT_RUNPATH` 只套用在該物件自己的 `DT_NEEDED`，每個 `.so` 要自己帶。
4. **「LD_PRELOAD 一定攔得到程式庫內部的呼叫」**：取決於該程式庫是否以 `-Bsymbolic-functions`、`-fno-semantic-interposition`、hidden 或版本腳本建置，以及編譯器是否內嵌。Ubuntu 套件預設 `-Bsymbolic-functions`。
5. **「介入函式裡用 printf 除錯很正常」**：在 `malloc` 介入函式裡呼叫會配置記憶體的函式（`printf` 到 stdout 第一次會配置緩衝區、`fopen`、`dlopen`…）會遞迴回自己。練習 E 會實際看到它 SIGSEGV。
6. **「dlopen 成功就代表外掛沒問題」**：`RTLD_LAZY` 下缺符號要到第一次呼叫才發現，而且直接殺掉整個行程。外掛建置一律加 `-Wl,-z,defs`，載入時考慮 `RTLD_NOW`。
7. **「ldd 只是讀檔，很安全」**：ldd(1) 明說可能執行目標程式。分析不信任的執行檔用 `readelf -d`。

## 進階：再往深一層

**`LD_DEBUG` 是你的透視鏡**。`LD_DEBUG=help` 列出所有類別；最常用的是 `libs`（搜尋路徑）、`bindings`（誰綁到誰）、`files`（載入與 init/fini）、`symbols`（每個符號搜尋了哪些物件）、`statistics`（重定位花的時間）。加 `LD_DEBUG_OUTPUT=file` 可輸出到檔案。

**`-fno-plt`**：讓編譯器直接產生 `call *puts@GOTPCREL(%rip)`，跳過 PLT 跳板，搭配 `-z now` 時少一次間接跳躍；代價是放棄延遲繫結。

**prelink 與它的衰亡**：早年 Red Hat 用 prelink 預先算好每個程式庫的位址、預填 GOT 以加速啟動；ASLR 普及後失去意義，就我所知 glibc 在 2.36 前後移除了相關支援（版本請以 glibc NEWS 為準）。這是「安全機制讓效能最佳化失效」的典型案例。

**面試題型**：「PLT 和 GOT 各在哪個 segment，為什麼這樣分？」（PLT 在唯讀可執行區以共用程式碼，GOT 在可寫資料區讓每個行程各自填位址）；「partial 和 full RELRO 差在哪？」；「如何在不重編的情況下換掉程式的 malloc？」。

> 與既有課重疊：[c_interview Ch 20 — 連結器與符號](../c_interview/20-linker-symbols.md) 有 PLT/GOT 與 `LD_PRELOAD` 的面試整理；[Ch 17 — malloc/free 實作](./17-malloc-internals.md) 是你攔截到的那個 glibc malloc 內部長什麼樣。

## 動手練習

1. 用 `LD_DEBUG=symbols ./plt_lazy 2>&1 | grep puts` 觀察 `puts` 在哪些物件中被搜尋。
2. 在 `plt_lazy` 的 gdb 實驗中，第一次呼叫時 `break _dl_fixup`，用 `bt` 看呼叫鏈，`finish` 之後看傳回值是不是 `puts` 的位址。
3. 把 `mcount.c` 的 `malloc` 加上 `__attribute__((visibility("hidden")))`，預測 `LD_PRELOAD` 跑 `app`（`-O0`）的結果，再實測（原文的結果是 `malloc: 0, free: 3`）。
4. 寫一個外掛載入器，掃描目錄下所有 `*.so`，以 `RTLD_NOW` 載入、取得 `plugin_name()` 並列出；對 `broken.so` 要能優雅地跳過。
5. 對 `/usr/bin/ls` 跑 `readelf -d`、`readelf -lW | grep RELRO`，判斷它是 partial 還是 full RELRO。

## 本章重點整理

- 動態連結 = 把最後一段連結延到載入時（`ld.so`）與首次呼叫時（延遲繫結）；這是 ELF/glibc 的約定，ISO C 不區分。
- `PT_INTERP` 指向的直譯器不存在時，`execve` 回報 `ENOENT`，訊息跟「檔案不存在」一樣。
- 搜尋順序：`DT_RPATH`（無 RUNPATH 時）→ `LD_LIBRARY_PATH` → `DT_RUNPATH`（不延伸到間接相依）→ `ld.so.cache` → 系統目錄。
- 符號解析取載入順序中第一個定義；`LD_PRELOAD` 插在最前面，`dlsym(RTLD_NEXT)` 從下一個物件接著找；`--wrap` 是連結時期的對應手法。
- PLT 跳板經 GOT 間接跳躍；延遲繫結時 GOT 項目先指回 `plt+6`，首次呼叫經 `_dl_runtime_resolve` → `_dl_fixup` 寫回真實位址。
- full RELRO（`-z now -z relro`）把整個 GOT 變唯讀，GOT 改寫攻擊因此失效；partial RELRO 不保護 `.got.plt`。
- visibility/版本腳本決定誰進 `.dynsym`，同時影響介入、`dlsym` 與內嵌；執行檔預設不匯出符號，外掛要回呼宿主需 `-rdynamic` 或 `--dynamic-list`。

## 自我檢核

- [ ] 同事說「我的程式在 Alpine 容器裡 not found，但 `ls` 看得到」，你能在一分鐘內說出最可能的原因與驗證指令嗎？
- [ ] 不看筆記，能畫出延遲繫結時第一次與第二次呼叫 `puts` 的控制流程，並說出 `GOT[1]`、`GOT[2]`、`push $0` 各自的用途？
- [ ] 能解釋 `DT_RPATH` 和 `DT_RUNPATH` 的兩個差異（優先順序、作用範圍），以及為什麼現代預設選後者？
- [ ] 如果面試官問「不改程式碼怎麼統計 malloc 次數」，你能講出三種層級的做法和各自看不到什麼嗎？
- [ ] 能說明為什麼你寫的 `LD_PRELOAD` 測試替身在自己編的程式庫上有效、在 Ubuntu 套件的程式庫上卻無效？
- [ ] 設計一個外掛系統時，你會在建置與載入時加哪些選項，各自防止什麼失敗？

## 延伸閱讀

### 論文 / 長篇技術文件

- **[How To Write Shared Libraries](https://www.akkadia.org/drepper/dsohowto.pdf)** — Ulrich Drepper（前 glibc 維護者，2011 修訂版）
  - **核心貢獻**：從 `ld.so` 的成本模型出發，說明共享程式庫的符號查找、重定位怎麼影響啟動時間，以及 visibility、版本腳本、`-Bsymbolic` 的正確用法。
  - **讀哪裡**：Section 1.5「Startup in the Dynamic Linker」、Section 2.2「Export Control」、Section 3「Maintaining APIs and ABIs」。
  - **和本章的關聯**：本章 visibility 一節的「三個收益」在 Section 2 有量化分析。前提：讀完本章，熟悉 GOT/PLT。

### 部落格 / 技術文章

- **[Position Independent Code (PIC) in shared libraries](https://eli.thegreenplace.net/2011/11/03/position-independent-code-pic-in-shared-libraries/)** — Eli Bendersky（2011）
  - **這篇說什麼**：以 x86 反組譯一步步追 GOT、PLT 與延遲繫結，和本章 gdb 實驗同一路線但有更多圖。
  - **讀哪裡**：整篇；續篇〈Position Independent Code (PIC) in shared libraries on x64〉講 `%rip` 相對定址。
  - **為什麼值得讀**：目前寫得最清楚的 PIC 入門，適合本章讀完後鞏固。
- **[All about Procedure Linkage Table](https://maskray.me/blog/all-about-procedure-linkage-table)** 與 **[ELF interposition and -Bsymbolic](https://maskray.me/blog/elf-interposition-and-bsymbolic)** — Fangrui Song（MaskRay，lld 維護者）
  - **這篇說什麼**：前者涵蓋 PLT 的各種變體（`.plt.sec`、IBT、`-fno-plt`）；後者解釋 symbol interposition 的代價、`-Bsymbolic` 的語意與指標相等性問題。
  - **讀哪裡**：PLT 篇的 x86-64 部分；interposition 篇的前半。
  - **為什麼值得讀**：本章 interposition 表格中 gcc 與 clang 差異的來由。前提：熟悉本章內容。
- **[Tutorial: Function Interposition in Linux](https://jayconrod.com/posts/23/tutorial-function-interposition-in-linux)** — Jay Conrod
  - **這篇說什麼**：比較編譯時、連結時、執行時三種介入，附完整範例。
  - **讀哪裡**：整篇，篇幅短。
  - **為什麼值得讀**：對照本章三種介入方式的最佳補充；CS:APP 7.13 的部落格版本。

### 官方文件 / Spec

- **[ld.so(8)](https://man7.org/linux/man-pages/man8/ld.so.8.html)**
  - **讀哪裡**：「Description」中的搜尋順序、「Environment」中的 `LD_PRELOAD`、`LD_LIBRARY_PATH`、`LD_BIND_NOW`、`LD_DEBUG`，以及 secure-execution 的說明。
- **[glibc 手冊：Replacing malloc](https://sourceware.org/glibc/manual/latest/html_node/Replacing-malloc.html)**
  - **讀哪裡**：整節，列出替換 `malloc` 時必須提供的函式集合，以及內部會配置記憶體的函式。這是練習 E 的規格來源。

### 書籍

- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：7.10–7.13（共享程式庫、PIC、PLT/GOT、函式介入三種做法）。
- **《Linkers and Loaders》** — John R. Levine（1999）
  - **讀哪幾章**：第 10 章 Dynamic Linking and Loading；ELF 細節較舊，但動態連結的設計空間（為什麼需要 PLT、為什麼延遲繫結）講得透徹。

我們一路追到 `ld.so` 跳到執行檔的 `_start`。下一章接手：`_start` 之後、`main` 之前還有誰在工作，`main` 返回之後又是誰把 `return 3` 變成 shell 的 `$?`。

→ [Ch 41 執行階段：從 execve 經 _start 到 main](./41-c-runtime.md)
