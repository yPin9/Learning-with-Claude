# Ch 49 — clangd 與 LSP：compile_commands.json 讓編輯器真的懂 kernel

> **目標**：理解 LSP 的架構與 clangd 為什麼需要 `compile_commands.json`；能在 v6.18 上產生編譯資料庫、處理 gcc 專屬旗標、用 `clangd --check` 驗證設定，並且親手用一個 90 行的 LSP client 驅動 clangd，示範「穿透巨集的 go-to-definition」「依 `.config` 排除死碼的 call hierarchy」，以及它在函式指標面前的極限。
> **鴻溝**：本章主要練**間接鴻溝**與**慣用法鴻溝**（巨集展開、條件編譯造成的「看不出這行到底是什麼」）。

> **環境**：WSL2 Ubuntu 22.04；系統的 clang 14 套件**不含 clangd**，本章用的是從 GitHub `clangd/clangd` releases 下載的官方靜態 binary **clangd 23.1.0**，裝在 `~/.local/opt/clangd_23.1.0`、連結到 `~/.local/bin/clangd`；kernel v6.18 以 gcc 11.4 建在 `~/code_reading/build`（`O=` build，3,000 筆 `compile_commands.json`）。所有 clangd 輸出都是實際執行結果。

## 為什麼需要這個？

[Ch 47](./47-grep-and-regex-search.md) 和 [Ch 48](./48-elixir-cscope-gtags.md) 的工具在同三個地方失敗：

```console
$ global -x mutex_lock | head -3        # 三個候選，哪個生效？
mutex_lock        168 include/linux/mutex.h #define mutex_lock(lock) mutex_lock_nested(lock, 0)
mutex_lock        269 kernel/locking/mutex.c void __sched mutex_lock(struct mutex *lock)
mutex_lock        601 kernel/locking/rtmutex_api.c void __sched mutex_lock(struct mutex *lock)
$ cscope -dL -1 folio_test_dirty         # ## 拼接出來的名字：查無
$ cscope -dL -3 vfs_read | head -1       # 列出一個根本沒編譯的呼叫者
/home/ypp/code_reading/linux/fs/exec.c read_code 832 ssize_t res = vfs_read(file, ...
```

這三個問題的答案都在**前置處理器**裡：`mutex_lock` 是巨集還是函式，取決於 `CONFIG_DEBUG_LOCK_ALLOC`；`folio_test_dirty` 要展開 `FOLIO_TEST_FLAG` 才會出現；`read_code` 在 `#if defined(CONFIG_BINFMT_FLAT)` 裡。語法層工具不跑前置處理器，所以永遠答不出來。

要答出來，工具必須做到編譯器做的事：讀 `.config` 產生的 `autoconf.h`、套用每個檔案的 `-D`/`-I`/`-include` 旗標、展開所有巨集、做型別檢查。換句話說，**工具本身就要是一個編譯器前端**。clangd 就是這樣的工具：它把 clang 的 parser 和語意分析包成一個常駐服務，透過 LSP 回答編輯器的問題。

歷史上，這條路在 2010 年代之前幾乎不可行：每個 IDE 要自己寫 C parser（Eclipse CDT、Visual Studio 的 IntelliSense 各一套），對 GNU C 擴充與 kernel 的巨集密度都支援不好。2016 年微軟提出 LSP，把「懂語言」和「編輯器介面」切開；LLVM 社群隨後做出 clangd。從此任何支援 LSP 的編輯器都能拿到 clang 等級的理解力。

## 先建立直覺

```
   編輯器（LSP client）                          clangd（LSP server）
  ┌──────────────────┐   JSON-RPC over stdio   ┌───────────────────────────────────────┐
  │ neovim / VS Code │ ──── definition? ─────▶ │ 1. 查 compile_commands.json：         │
  │ 或本章的 lsp.py  │ ◀─── include/.../wait.h  │    fs/pipe.c 用什麼旗標編？           │
  │                  │       :626 ───────────  │ 2. 用 clang 前端 parse（含 -D、-I、   │
  └──────────────────┘                          │    autoconf.h → 依 .config 展開）     │
                                                │ 3. 在 AST 上找答案                     │
                                                │ 4. 跨檔案問題查背景索引                │
                                                │    （build/.cache/clangd/index）       │
                                                └───────────────────────────────────────┘
                                                                  ▲
                                         compile_commands.json ───┘  ← 少了它，clangd 是瞎的
```

類比：語法層工具像一個**只看過劇本**的人，劇本裡寫「（依導演指示，此處換成 A 或 B）」，他兩個都記下來；clangd 是**看過正式演出**的人，他知道這一場演的是 A。`compile_commands.json` 就是那場演出的節目單：每一個 `.c` 檔當初是用哪些旗標、在哪個目錄、用什麼編譯器編出來的。

## LSP：協定本身

LSP（Language Server Protocol）是一個 JSON-RPC 協定。client（編輯器）和 server（clangd）透過 stdin/stdout 交換訊息，每則訊息前面有一行 `Content-Length: N` 標頭。核心對話長這樣：

```
client → server   initialize          {rootUri, capabilities}
client → server   initialized         （通知，不等回應）
client → server   textDocument/didOpen {uri, text}           ← 告訴 server「我打開了這個檔」
client → server   textDocument/definition {uri, position}    ← 游標在第 388 行第 13 欄
server → client   [{uri: ".../wait.h", range: {line: 625}}]
client → server   shutdown / exit
```

位置用的是**0 起算**的行與欄。這個協定的關鍵設計是：server 不知道也不在乎 client 是哪個編輯器；client 不知道 server 是用什麼方法得到答案。所以你在 neovim 裡按 `gd`、在 VS Code 裡按 F12、或在本章的 Python 腳本裡送一個 JSON，問的都是同一個問題、得到同一個答案。

## compile_commands.json：clangd 的節目單

### 它長什麼樣

`compile_commands.json` 是一個 JSON 陣列，每個元素描述一個 TU（translation unit，一個 `.c` 檔加上它 include 的全部內容）：

```console
$ python3 -c "import json;d=json.load(open('compile_commands.json'));print(len(d));e=[x for x in d if x['file'].endswith('fs/pipe.c')][0];print(e.keys());print(e['directory'])"
3000
dict_keys(['command', 'directory', 'file'])
/home/ypp/code_reading/build
```

`fs/pipe.c` 那一筆的 `command`（節錄）：

```
gcc -Wp,-MMD,fs/.pipe.o.d -nostdinc -I/home/ypp/code_reading/linux/arch/x86/include
    -I./arch/x86/include/generated -I/home/ypp/code_reading/linux/include -I./include ...
    -include /home/ypp/code_reading/linux/include/linux/kconfig.h
    -include /home/ypp/code_reading/linux/include/linux/compiler_types.h
    -D__KERNEL__ ... -std=gnu11 ... -mrecord-mcount -mfentry -DCC_USING_FENTRY ...
    -fconserve-stack ... -DKBUILD_MODNAME='"pipe"' ... -c -o fs/pipe.o /home/ypp/code_reading/linux/fs/pipe.c
```

三個重點：`-include kconfig.h` 把 `.config` 轉成的 `autoconf.h` 強制 include 進每個檔案（這就是 clangd 知道 `CONFIG_DEBUG_LOCK_ALLOC` 沒開的管道）；`-I./include` 這種相對路徑要配合 `directory` 欄位才解得開；`-mrecord-mcount`、`-fconserve-stack` 這類旗標是 **gcc 專屬**的，後面會處理。

### kernel 怎麼產生它

kernel 不是用 CMake 或 Bear 這類通用工具產生資料庫，而是自己寫了一支腳本，從 Kbuild 的副產品裡撈：每個 `.o` 編譯時，Kbuild 會在旁邊留一個 `.<name>.o.cmd` 檔記錄完整指令（Kbuild 用它判斷旗標有沒有變、要不要重編）。`scripts/clang-tools/gen_compile_commands.py` 讀這些檔案：

```python
_FILENAME_PATTERN = r'^\..*\.cmd$'
_LINE_PATTERN = r'^(saved)?cmd_[^ ]*\.o := (?P<command_prefix>.* )(?P<file_path>[^ ]*\.[cS]) *(;|$)'
_VALID_LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
# The tools/ directory adopts a different build system, and produces .cmd
# files in a different format. Do not support it.
_EXCLUDE_DIRS = ['.git', 'Documentation', 'include', 'tools']
```
（`scripts/clang-tools/gen_compile_commands.py:21-26`（v6.18））

頂層 Makefile 把它包成一個目標：

```make
compile_commands.json: $(srctree)/scripts/clang-tools/gen_compile_commands.py \
	$(if $(KBUILD_EXTMOD),, vmlinux.a $(KBUILD_VMLINUX_LIBS)) \
	$(if $(CONFIG_MODULES), modules.order) FORCE
	$(call if_changed,gen_compile_commands)
```
（`Makefile:2073-2076`（v6.18））

所以標準用法是 build 完之後跑 `make O=../build compile_commands.json`，輸出在 build 目錄。注意它的依賴是 `vmlinux.a` 和 `modules.order`：Makefile 版本只收**實際連結進 vmlinux 或 module 的物件**。直接對目錄跑腳本會收得比較多：

```console
$ time python3 scripts/clang-tools/gen_compile_commands.py -d ../build -o ../scratch/49/cc.json
real	0m1.796s
$ python3 -c "import json;print(len(json.load(open('../scratch/49/cc.json'))))"
3115
```

多出來的 113 筆主要是 `arch/x86/boot`（43 筆，開機解壓縮程式）、`drivers/firmware/efi`（17 筆，EFI stub）、`arch/x86/realmode`（13 筆）——它們有被編譯，但不在 `vmlinux.a` 裡。讀開機流程時你會想要這些；讀一般 kernel 程式碼時，3,000 筆的版本就夠了。

**沒有 build 能不能有資料庫？** 不能。`.cmd` 檔是編譯的副產品，沒編過就沒有。這是 clangd 路線最大的前置成本：你得先能 build（[Ch 0](./00-environment-setup.md) 的環境）。不能 build 的 tree（別的架構、拿不到 config 的廠商 kernel）只能退回 Ch 48 的工具。

## 動手：裝 clangd、跑 `--check`

### 本環境沒有 clangd

```console
$ which clangd clangd-14
$ ls /usr/lib/llvm-14/bin/ | grep -i clangd
$
```

Ubuntu 把 clangd 拆成獨立的 `clangd-14` 套件，這台機器沒有 sudo 裝不了。官方提供 Linux 靜態 binary：

```console
$ curl -sI https://github.com/clangd/clangd/releases/latest | grep -i location
location: https://github.com/clangd/clangd/releases/tag/23.1.0
$ curl -sL -o clangd.zip https://github.com/clangd/clangd/releases/download/23.1.0/clangd-linux-23.1.0.zip
$ python3 -I -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" clangd.zip ~/.local/opt
$ chmod +x ~/.local/opt/clangd_23.1.0/bin/clangd
$ ln -sf ~/.local/opt/clangd_23.1.0/bin/clangd ~/.local/bin/clangd
$ clangd --version
clangd version 23.1.0 (https://github.com/llvm/llvm-project ea7d852a70e8bdfaf601d6626a760f9771b2c4b4)
Features: linux+grpc
Platform: x86_64-unknown-linux-gnu
```

zip 裡除了 `bin/clangd` 還有 `lib/clang/23/include`（clang 內建標頭，例如 `stddef.h`）；clangd 會自動用 `-resource-dir` 指到它。clangd 的版本不必和編譯 kernel 的編譯器一致——它只拿 `compile_commands.json` 的旗標來 parse，不產生程式碼。

### 失敗示範：沒有資料庫

`clangd --check=<file>` 是 clangd 的自我診斷模式：載入一個檔案、建 AST、對每個 token 試跑各種功能，把過程印出來。先故意不給資料庫（指向一個空目錄）：

```console
$ clangd --check=fs/pipe.c --compile-commands-dir=/tmp/emptycdb 2>&1 | grep -E 'fallback|^E\[|All checks' | head
I[11:56:31.135] Generic fallback command is: [/home/ypp/code_reading/linux/fs] /usr/lib/llvm-14/bin/clang -resource-dir=... -- /home/ypp/code_reading/linux/
E[11:56:31.177] [pp_file_not_found] Line 8: 'linux/mm.h' file not found
E[11:56:31.193] [undeclared_var_use] Line 61: use of undeclared identifier 'PIPE_DEF_BUFFERS'
E[11:56:31.193] [typecheck_incomplete_tag] Line 90: incomplete definition of type 'struct pipe_inode_info'
E[11:56:31.193] [-Wimplicit-function-declaration] Line 91: call to undeclared function 'mutex_lock'; ...
...
I[11:56:32.696] All checks completed, 21 errors
```

「Generic fallback command」就是 clangd 在沒有資料庫時的猜測：一個沒有任何 `-I` 的 `clang` 指令。於是第 8 行 `#include <linux/mm.h>` 就找不到，之後每一個 kernel 型別都是「incomplete」、每一個函式都是「undeclared」。**在編輯器裡，這個狀態看起來就是整個檔案滿滿的紅線**，而且 go-to-definition 全部失效。看到這種畫面，第一個要懷疑的永遠是資料庫。

### 有資料庫：剩下的錯誤從哪來

```console
$ time clangd --check=fs/pipe.c --compile-commands-dir=../build 2>&1 | grep -E 'Compile command|^E\[|Built preamble|All checks' | grep -v tweak | cut -c1-150
I[11:55:56.484] Compile command from CDB is: [/home/ypp/code_reading/build] /usr/lib/llvm-14/bin/gcc -Wp,-MMD,fs/.pipe.o.d -nostdinc ...
E[11:55:56.486] [drv_unsupported_opt_for_target] Line 1: unsupported option '-mrecord-mcount' for target 'x86_64-unknown-linux-gnu'
I[11:55:57.253] Built preamble of size 11878648 for file /home/ypp/code_reading/linux/fs/pipe.c version null in 0.76 seconds
I[11:56:05.415] All checks completed, 16 errors
real	0m8.598s
```

「16 errors」看起來嚇人，拆開看：

- **1 個是真的**：`-mrecord-mcount` 是 gcc 的 ftrace 旗標，clang 的 x86_64 target 不支援。
- **15 個是 clangd 的自我測試**：`--check` 會在每個 token 上試跑重構功能（tweak），其中 `SwapBinaryOperands` 在巨集展開出來的運算式上失敗（`E[...] tweak: SwapBinaryOperands ==> FAIL: The new replacement overlaps with an existing replacement.`，位置在 `ARRAY_SIZE(pipe->tmp_page)` 之類的巨集裡）。這是 clangd 重構功能的限制，**不影響讀碼**，上面的指令用 `grep -v tweak` 濾掉了。

AST 有建起來（preamble 11.8 MB、0.76 秒），表示 parse 成功了。`-mrecord-mcount` 這個錯誤不會讓 clangd 罷工，但在編輯器裡它會變成第 1 行的一個紅色診斷，看久了會讓你忽略真正的錯誤。

### clangd 已經偷偷幫你刪了一批旗標

比較資料庫裡的原始指令和 clangd 實際用的指令，會發現 `-mpreferred-stack-boundary=3`、`-fconserve-stack` 這些旗標不見了。clangd 的 log 說明了原因：

```
I[12:06:10.414] Warning: detected unsupported options '-mpreferred-stack-boundary=3, -mindirect-branch=thunk-extern, -mindirect-branch-register, -fno-allow-store-data-races, -fconserve-stack'
```

新版 clangd 會自動移除它不認得的 driver 旗標。如果你拿同一行指令直接餵給 clang 14，看到的才是完整的不相容清單：

```console
$ clang-14 <fs/pipe.c 的完整旗標> -fsyntax-only
clang: error: unknown argument: '-mpreferred-stack-boundary=3'
clang: error: unknown argument: '-mstack-protector-guard-symbol=__ref_stack_chk_guard'
clang: error: unknown argument: '-mindirect-branch=thunk-extern'
clang: error: unknown argument: '-mindirect-branch-register'
clang: error: unknown argument: '-mindirect-branch-cs-prefix'
clang: error: unknown argument: '-mfunction-return=thunk-extern'
clang: error: unknown argument: '-fno-allow-store-data-races'
clang: error: unknown argument: '-fconserve-stack'
clang: error: optimization flag '-falign-jumps=1' is not supported [-Werror,-Wignored-optimization-argument]
clang: error: unsupported option '-mrecord-mcount' for target 'x86_64-pc-linux-gnu'
```

（指令是用 Python 從 `compile_commands.json` 取出 `fs/pipe.c` 那筆、把 `gcc` 換成 `clang-14`、去掉 `-c -o` 之後執行的。）這份清單就是「gcc 編的 kernel 配 clangd」時要處理的旗標。clangd 23 自己會處理大部分；舊版 clangd（例如 Ubuntu 的 clangd-14）沒有這麼聰明，整份清單都會冒出來。

### 用設定檔移除旗標

clangd 的設定檔是 YAML，`CompileFlags.Remove` 列出要刪掉的旗標（支援 `*` 萬用字元）。設定檔可以放在兩個地方：

- **專案設定**：source tree 根目錄的 `.clangd`。kernel 的 `.gitignore` 第 13 行是 `.*`，所以 `.clangd` 不會出現在 `git status` 裡——但它確實寫進了 tree。
- **使用者設定**：`~/.config/clangd/config.yaml`（遵守 `$XDG_CONFIG_HOME`），用 `If: PathMatch` 限定只套用在 kernel 路徑。

本課的 linux tree 不能改，所以用使用者設定。為了不影響這台機器的全域設定，下面把 `XDG_CONFIG_HOME` 指到 scratch 目錄做實驗：

```yaml
# ~/code_reading/scratch/49/xdg/clangd/config.yaml
If:
  PathMatch: /home/ypp/code_reading/linux/.*
CompileFlags:
  CompilationDatabase: /home/ypp/code_reading/build
  Remove:
    - -mrecord-mcount
    - -fconserve-stack
    - -fno-allow-store-data-races
    - -mindirect-branch=*
    - -mindirect-branch-register
    - -mpreferred-stack-boundary=*
    - -mfunction-return=*
    - -falign-jumps=*
  Add:
    - -Wno-unknown-warning-option
Diagnostics:
  UnusedIncludes: None
```

```console
$ XDG_CONFIG_HOME=~/code_reading/scratch/49/xdg clangd --check=fs/pipe.c 2>&1 | grep -E 'config|^E\[|All checks' | grep -v tweak | cut -c1-110
I[11:56:46.464] Loading config file at /home/ypp/code_reading/scratch/49/xdg/clangd/config.yaml
I[11:56:55.067] All checks completed, 15 errors
```

`-mrecord-mcount` 的錯誤消失，剩下的 15 個全是 tweak 自我測試。另外兩個設定值得注意：`CompilationDatabase` 讓你不用每次下 `--compile-commands-dir`；`UnusedIncludes: None` 關掉「這個 include 沒用到」的提示——kernel 的 include 關係太複雜，這個提示在 kernel 裡誤報很多。`-Wno-unknown-warning-option` 讓 clang 不要抱怨 gcc 專屬的 `-W` 選項（例如 `-Wno-alloc-size-larger-than`）。

## 底層機制：用 90 行 Python 當 LSP client

編輯器把 LSP 藏在快捷鍵後面，你看不到它問了什麼、clangd 答了什麼。下面這支腳本把對話攤開：它啟動 clangd、送 `initialize`、`didOpen`，然後依照一個 JSON 查詢清單送 definition、hover、references 與 call hierarchy 請求，把答案印成 `檔案:行號`。

<details>
<summary>lsp.py（完整原始碼，本機實測可用）</summary>

```python
#!/usr/bin/env python3
# Minimal LSP client: drive clangd over stdio, print definition / hover / references / incoming calls.
import json, subprocess, sys, os, time

ROOT = os.path.expanduser('~/code_reading/linux')
CDB  = os.path.expanduser('~/code_reading/build')

def uri(p): return 'file://' + os.path.join(ROOT, p)

class Client:
    def __init__(self, extra):
        self.p = subprocess.Popen([os.path.expanduser('~/.local/bin/clangd'),
                                   '--compile-commands-dir=' + CDB, '--log=' + os.environ.get('LOG','error')] + extra,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.id = 0
    def send(self, method, params, notify=False):
        msg = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notify:
            self.id += 1; msg['id'] = self.id
        body = json.dumps(msg).encode()
        self.p.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
        self.p.stdin.flush()
        return None if notify else self.wait(self.id)
    def read(self):
        n = 0
        while True:
            line = self.p.stdout.readline().strip()
            if not line: break
            if line.lower().startswith(b'content-length:'): n = int(line.split(b':')[1])
        return json.loads(self.p.stdout.read(n))
    def wait(self, i):
        while True:
            m = self.read()
            if m.get('id') == i and 'method' not in m: return m.get('result')
            if 'method' in m and 'id' in m:          # server->client request: ack it
                body = json.dumps({'jsonrpc':'2.0','id':m['id'],'result':None}).encode()
                self.p.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body); self.p.stdin.flush()

def loc(l):
    u = l.get('uri') or l.get('targetUri'); r = l.get('range') or l.get('targetSelectionRange')
    return '%s:%d' % (u.replace('file://' + ROOT + '/', '').replace('file://',''), r['start']['line'] + 1)

def col(path, line, word):
    text = open(os.path.join(ROOT, path)).read().split('\n')[line - 1]
    return text.index(word)

def main():
    bg = '--background-index' if 'bg' in sys.argv else '--background-index=false'
    c = Client([bg])
    c.send('initialize', {'processId': os.getpid(), 'rootUri': 'file://' + ROOT,
                          'capabilities': {'textDocument': {'hover': {'contentFormat': ['plaintext']}}}})
    c.send('initialized', {}, notify=True)
    opened = set()
    def open_(p):
        if p in opened: return
        c.send('textDocument/didOpen', {'textDocument': {'uri': uri(p), 'languageId': 'c', 'version': 1,
               'text': open(os.path.join(ROOT, p)).read()}}, notify=True); opened.add(p)
    qs = json.load(open(sys.argv[1]))
    if 'bg' in sys.argv:                 # CDB is discovered on first didOpen; then give the
        open_(qs[0][1])                  # background index time to load its on-disk shards
        time.sleep(int(os.environ.get('WAIT', '30')))
    for q in qs:
        kind, path, line, word = q
        open_(path)
        pos = {'textDocument': {'uri': uri(path)}, 'position': {'line': line - 1, 'character': col(path, line, word) + 1}}
        print('=== %s  %s:%d  "%s"' % (kind, path, line, word))
        if kind == 'definition':
            for l in c.send('textDocument/definition', pos) or []: print('   ', loc(l))
        elif kind == 'hover':
            h = c.send('textDocument/hover', pos)
            v = h['contents']['value'] if h else '(none)'
            print('\n'.join('    ' + s for s in v.split('\n')[:40]))
        elif kind == 'references':
            pos['context'] = {'includeDeclaration': True}
            for l in c.send('textDocument/references', pos) or []: print('   ', loc(l))
        elif kind == 'incoming':
            items = c.send('textDocument/prepareCallHierarchy', pos) or []
            for it in items:
                for call in c.send('callHierarchy/incomingCalls', {'item': it}) or []:
                    f = call['from']; print('    <- %s  (%s)' % (f['name'], loc(f)))
        elif kind == 'outgoing':
            items = c.send('textDocument/prepareCallHierarchy', pos) or []
            for it in items:
                res = c.send('callHierarchy/outgoingCalls', {'item': it}) or []
                print('    -> ' + ', '.join(sorted(set(x['to']['name'] for x in res))))
    c.send('shutdown', None); c.send('exit', None, notify=True)

main()
```

</details>

查詢清單是 `[種類, 檔案, 行號, 游標要落在的字]`：

```json
[["definition","fs/pipe.c",388,"wait_event_interruptible_exclusive"],
 ["hover","fs/pipe.c",388,"wait_event_interruptible_exclusive"],
 ["definition","fs/pipe.c",282,"mutex_lock"],
 ["definition","fs/pipe.c",1054,"SYSCALL_DEFINE2"],
 ["definition","fs/read_write.c",491,"read_iter"],
 ["references","fs/pipe.c",269,"anon_pipe_read"],
 ["incoming","fs/pipe.c",269,"anon_pipe_read"]]
```

### 結果一：穿透巨集，而且知道 `.config`

```console
$ time python3 -I lsp.py q1.json
=== definition  fs/pipe.c:388  "wait_event_interruptible_exclusive"
    include/linux/wait.h:626
...
=== definition  fs/pipe.c:282  "mutex_lock"
    include/linux/mutex.h:189
=== definition  fs/pipe.c:1054  "SYSCALL_DEFINE2"
    include/linux/syscalls.h:226
...
real	0m1.358s
```

`mutex_lock` 這一筆是本章的核心證據。`include/linux/mutex.h` 裡有兩個 `mutex_lock`：

```c
#define mutex_lock(lock) mutex_lock_nested(lock, 0)          /* 168 行，CONFIG_DEBUG_LOCK_ALLOC 分支 */
...
#else
extern void mutex_lock(struct mutex *lock);                  /* 189 行，非 debug 分支 */
```
（`include/linux/mutex.h:168`、`:188-189`（v6.18））

`~/code_reading/build/.config` 裡 `# CONFIG_DEBUG_LOCK_ALLOC is not set`，所以生效的是 189 行。Global 列出全部 7 個候選，clangd 只回答 189 行——**它真的依照你的 config 做了前置處理**。

### 結果二：hover 給你巨集展開

```
=== hover  fs/pipe.c:388  "wait_event_interruptible_exclusive"
    macro wait_event_interruptible_exclusive

    provided by "linux/wait.h"

    #define wait_event_interruptible_exclusive(wq, condition)             \
    	({                                                            \
    		int __ret = 0;                                        \
    		might_sleep();                                        \
    		if (!(condition))                                     \
    			__ret = __wait_event_interruptible_exclusive( \
    				wq, condition);                       \
    		__ret;                                                \
    	})

    // Expands to

    ({
    	int __ret = 0;
    	do {
    		might_resched();
    	} while (0);
    	if (!(pipe_readable(pipe)))
    		__ret = ({
    			__label__ __out;
    			struct wait_queue_entry __wq_entry;
    			long __ret = 0;
    			init_wait_entry(&__wq_entry, 1 ? 0x01 : 0);
    			for (;;) {
    				long __int = prepare_to_wait_event(
    					&pipe->rd_wait, &__wq_entry,
    					0x00000001);
    				if (pipe_readable(pipe))
    					break;
    ...
```

「Expands to」以下是這一個呼叫點**實際展開**的結果：參數已經代入（`condition` 變成 `pipe_readable(pipe)`、`wq` 變成 `pipe->rd_wait`），巢狀巨集也全部展開（`TASK_INTERRUPTIBLE` 變成 `0x00000001`、`might_sleep()` 在這個 config 下變成 `do { might_resched(); } while (0)`），而且排版成可讀的格式。這等於 [Ch 50](./50-compiler-as-reading-tool.md) 的 `.i` 檔，但只針對游標下的那一個巨集，不用從 8 萬行裡撈。讀到看不懂的巨集時，這是解慣用法鴻溝最快的一招（對照 [Ch 33](./33-wait-queues-completion.md) 對 wait queue 的解說）。

### 結果三：references 與 call hierarchy

```
=== references  fs/pipe.c:269  "anon_pipe_read"
    fs/pipe.c:269
    fs/pipe.c:409
    fs/pipe.c:1259
=== incoming  fs/pipe.c:269  "anon_pipe_read"
    <- fifo_pipe_read  (fs/pipe.c:407)
    <- pipeanon_fops  (fs/pipe.c:884)
```

incoming calls 有個有趣的細節：除了真正的呼叫者 `fifo_pipe_read`，clangd 23 也把 `pipeanon_fops` 列為「來源」——因為 `anon_pipe_read` 在那張表的初始化裡被引用。它指向的 884 行是 `static const struct file_operations pipeanon_fops;` 這個前置宣告（真正的定義在 1257 行）。clangd 能告訴你「這個函式被放進了 `pipeanon_fops`」，**但它不能告訴你誰透過 `pipeanon_fops` 呼叫了它**。

### 跨檔案查詢需要背景索引

上面的查詢只開了一個檔案，所以 references 只看得到 `fs/pipe.c` 內部。要查整個 kernel，clangd 需要背景索引：啟動時加 `--background-index`，它會把資料庫裡的 3,000 個 TU 全部 parse 一遍，把符號存到 `<資料庫目錄>/.cache/clangd/index/`（本例是 `~/code_reading/build/.cache/clangd/index/`，不在 source tree 裡）。

我用一支小腳本啟動 clangd、打開一個檔案觸發索引、每 10 秒數一次索引分片：

```
  10s 1876 shards   20s 2502 shards   30s 2959 shards   60s 3822 shards  120s 4997 shards
 180s 5794 shards  240s 6825 shards  250s 6940 shards  260s 6940 shards  270s 6940 shards
$ du -sh ~/code_reading/build/.cache/clangd/index
103M	/home/ypp/code_reading/build/.cache/clangd/index
```

約 250 秒（`-j=12`）、6,940 個分片（每個 TU 與標頭各一個）、103 MB。這是一次性成本，之後重啟 clangd 會從磁碟載入。一個實作細節：**clangd 在第一個 `didOpen` 之後才發現資料庫、才開始載入索引**（log 裡 `Enqueueing 3000 commands for indexing` 出現在 `didOpen` 之後），所以腳本要先開檔再等。

有了索引之後（`WAIT=40 python3 -I lsp.py q2.json bg`）：

```
=== references  fs/pipe.c:102  "pipe_double_lock"
    fs/pipe.c:102
    fs/splice.c:1743
    fs/splice.c:1866
    include/linux/pipe_fs_i.h:318
=== incoming  fs/read_write.c:552  "vfs_read"
    <- ksys_pread64  (fs/read_write.c:752)
    <- ksys_read  (fs/read_write.c:704)
=== outgoing  fs/read_write.c:552  "vfs_read"
    -> __access_ok, add_rchar, fsnotify_access, get_current, inc_syscr, new_sync_read, rw_verify_area
```

`vfs_read` 的 incoming calls 只有兩個。對照 [Ch 48](./48-elixir-cscope-gtags.md) 的 cscope 有三個，多出來的 `read_code`（`fs/exec.c:832`）在 `#if defined(CONFIG_BINFMT_FLAT) || defined(CONFIG_BINFMT_ELF_FDPIC)` 裡，這個 config 沒編進去。**clangd 給的是「這個 kernel 裡的呼叫者」，cscope 給的是「原始碼裡寫著的呼叫者」**。追 bug、追效能問題時你要的幾乎都是前者。

outgoing calls 也值得對照：`vfs_read` 往下呼叫 `new_sync_read`，但**沒有** `read_iter`——那個呼叫在 `new_sync_read` 裡面，是透過函式指標。

## clangd 解決不了的：函式指標

```
=== definition  fs/read_write.c:491  "read_iter"
    include/linux/fs.h:2274
=== references  fs/read_write.c:491  "read_iter"
    fs/read_write.c:30
    fs/read_write.c:491
    ...（共 54 筆）
    fs/pipe.c:1248
    fs/pipe.c:1259
```

在 `ret = filp->f_op->read_iter(&kiocb, &iter);`（`fs/read_write.c:491`（v6.18））上按 go-to-definition，clangd 帶你到 `struct file_operations` 的成員宣告：

```c
	ssize_t (*read_iter) (struct kiocb *, struct iov_iter *);
```
（`include/linux/fs.h:2274`（v6.18））

這是**正確**的答案：在編譯期，那一行呼叫的就是「`file_operations` 的 `read_iter` 欄位裡存的東西」。存了什麼要到執行期才知道——開的是 pipe 就是 `anon_pipe_read`，開的是 ext4 檔案就是 `ext4_file_read_iter`。references 會列出所有對這個欄位的讀寫（包括 `fs/pipe.c:1259` 那個賦值），等於給你一份**候選清單**，但哪一個會在你關心的情境下被呼叫，靜態分析回答不了。

這就是間接鴻溝的硬核心。處理方法：

- **靜態縮小範圍**：從「你開的是什麼檔案」往回推是哪張 ops table——[Ch 28](./28-ops-tables-indirect-calls.md) 的方法。
- **動態觀察**：直接看執行期跑了什麼——[Ch 51](./51-dynamic-tracing.md) 的 ftrace function_graph、[Ch 52](./52-qemu-gdb-kernel.md) 的 gdb 單步。

## 對比與取捨

| | 語法層（rg / cscope / Global） | clangd |
|---|---|---|
| 前置條件 | 原始碼 | 能 build、`compile_commands.json` |
| `#ifdef` 死碼 | 照樣列出 | 依 `.config` 排除 |
| 同名多定義 | 全部並列 | 只給生效的那個 |
| `##` 拼接的名字 | 找不到 | 找得到（真的展開） |
| 巨集展開 | 看不到 | hover 直接顯示 |
| 函式指標的實際目標 | 不知道 | 不知道（只給欄位與候選） |
| 只看一個 config | 不受限（反而是優點：看得到所有架構與選項） | 受限：資料庫外的檔案只能靠推測 |
| 建索引成本 | 39 秒～2 分鐘 | 約 4 分鐘＋一次完整 build |

最後兩列是 clangd 的代價。讀「x86 defconfig 下 pipe 怎麼運作」，clangd 完勝；讀「這個 driver 在 arm64 上怎麼初始化」或「這個選項關掉時程式怎麼走」，你需要的是語法層工具的「什麼都列出來」。**兩種工具要並用，不是二選一。**

## 踩雷集錦

1. **整個檔案紅線，以為是 clangd 不支援 kernel**：九成是沒有找到 `compile_commands.json`（fallback 指令沒有任何 `-I`）。先跑 `clangd --check=<file>`，看 log 裡是「Compile command from CDB」還是「Generic fallback command」。
2. **把 `--check` 的錯誤總數當成問題數**：`16 errors` 裡 15 個是 tweak 自我測試。用 `grep -v tweak` 看真正的診斷。
3. **把 `.clangd` 放進 tree 根目錄然後忘記**：`.gitignore` 的 `.*` 讓它在 `git status` 裡隱形，之後換 config 或換 build 目錄時，舊設定還在默默生效。用使用者設定加 `If: PathMatch`，或至少記在筆記裡。
4. **期待 clangd 回答「`f_op->read_iter` 會呼叫誰」**：它只會帶你到欄位宣告。這不是 bug，是靜態分析的本質限制。
5. **以為 clangd 能看懂沒編譯的檔案**：不在資料庫裡的檔案（別的架構、沒開的 driver），clangd 會借用相近檔案的指令去推測，結果可能部分正確、部分滿是錯誤。這些檔案要靠語法層工具。
6. **開了背景索引卻馬上查跨檔案 references**：索引要幾分鐘才建完；剛啟動時的結果只涵蓋已開啟的檔案，看起來像「沒有其他呼叫者」。

## 進階：再往深一層

**索引放在哪、怎麼共用。** 背景索引跟著資料庫目錄走（`build/.cache/clangd/`），所以每個 build 目錄各有一份。團隊共用可以看 clangd 的 remote index（需要另外架 `clangd-index-server`，本章未實測）。

**用 clang 編一份 kernel。** 本課的 build 用 gcc，所以要處理 gcc 專屬旗標。如果用 `make LLVM=1` 以 clang 建 kernel，`compile_commands.json` 裡的旗標本來就是 clang 認得的，`Remove:` 清單幾乎可以不用。代價是要多 build 一次，而且 clang 和 gcc 編出來的程式碼不同（讀 [Ch 50](./50-compiler-as-reading-tool.md) 的組語時要注意是哪個編譯器的輸出）。v6.18 的 `Documentation/kbuild/llvm.rst` 有完整說明。

**讓 `--check` 快一點、乾淨一點。** 大檔案的 `--check` 很慢，因為它在每個 token 上試跑功能。clangd 23 有兩個沒列在 `--help` 的選項（`--help-hidden` 也沒列，但實測可用）：`--check-lines=280-300` 只測指定行，`fs/pipe.c` 從 8.6 秒降到 1.2 秒、錯誤數從 16 降到 5；`--check-locations=false` 完全跳過逐 token 測試，0.9 秒結束，錯誤數只剩 1——就是 `-mrecord-mcount` 那一個真的診斷。只想確認設定對不對時，用後者最省事。這兩個是未公開的選項，新版可能改名，以你手上版本實測為準。

**編輯器整合。** 實際讀碼時你不會跑 Python 腳本，而是在編輯器裡按快捷鍵。neovim 的完整設定在本 repo 的 [neovim 課程 Ch 17 LSP 與 clangd](../neovim/17-lsp-and-clangd.md)、[Ch 18 compile_commands.json](../neovim/18-compile-commands.md)、[Ch 19 語意導航](../neovim/19-semantic-navigation.md) 與 [Ch 21 clangd 進階與 macro/ifdef 的坑](../neovim/21-clangd-advanced.md)。

## 動手練習

1. 在 `q1.json` 加一筆 `["hover","fs/pipe.c",282,"mutex_lock"]`，看 hover 對一個「在這個 config 下是函式宣告」的符號顯示什麼；再找一個你的 config 下**是巨集**的鎖操作（例如 `spin_lock`），比較 hover 的差異。
2. 對 `fs/exec.c:832` 的 `vfs_read` 送一個 definition 請求，觀察 clangd 對一個 `#if` 停用區塊裡的程式碼怎麼回應。
3. 把使用者設定的 `Remove:` 清單清空，用 `clangd --check` 確認哪些錯誤會回來；再換成 Ubuntu 的 clangd-14（如果你的機器有）比較。
4. 用背景索引查 `pipe_buf_confirm` 的 incoming calls，然後讀它的定義（`include/linux/pipe_fs_i.h:290`），找出它內部的函式指標呼叫——寫下你要用什麼方法才能知道那個指標在 pipe 的情境下指向誰。

## 本章重點整理

- LSP 把「懂語言」和「編輯器」切開；clangd 是用 clang 前端回答 LSP 請求的 server。
- `compile_commands.json` 告訴 clangd 每個檔案的真實編譯旗標；kernel 用 `gen_compile_commands.py` 從 `.cmd` 檔產生它，前提是先 build。
- gcc 專屬旗標：新版 clangd 自動移除大部分，剩下的（`-mrecord-mcount`）用 `CompileFlags: Remove:` 處理；設定可放在使用者層級，不必寫進 tree。
- clangd 依 `.config` 做前置處理：同名定義只給生效的、`#ifdef` 死碼排除、巨集 hover 直接看展開。
- 函式指標的實際目標仍然要靠 ops table 分析與動態追蹤。

## 自我檢核

- [ ] 不看筆記，畫出編輯器、clangd、`compile_commands.json`、背景索引四者的關係，並說出一個 definition 請求經過哪些步驟。
- [ ] 同事的 neovim 打開 kernel 檔案滿滿紅線，你會請他跑哪個指令、看 log 裡的哪一行？
- [ ] 為什麼 clangd 對 `mutex_lock` 只回一個定義，Global 回七個？如果你想知道 `CONFIG_DEBUG_LOCK_ALLOC=y` 時的行為，該用哪個工具、怎麼做？
- [ ] 解釋 clangd 的 incoming calls 為什麼會把 `pipeanon_fops` 列為 `anon_pipe_read` 的來源，以及這個資訊為什麼還不足以回答「誰會呼叫 `anon_pipe_read`」。
- [ ] 舉出一個讀碼情境，在那個情境下 cscope 比 clangd 更好用。

## 延伸閱讀

- **[Language Server Protocol Specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)** — Microsoft
  - **讀哪裡**：「Base Protocol」（Content-Length 標頭與 JSON-RPC）、`textDocument/definition`、`textDocument/references`、`callHierarchy/incomingCalls` 三個請求的定義。
  - **學到什麼**：本章 `lsp.py` 每一個欄位的出處；position 為什麼是 0 起算、為什麼 hover 要宣告 `contentFormat`。
  - **前提**：會讀 JSON 與 TypeScript 型別宣告。
- **[clangd 官方文件](https://clangd.llvm.org/)**
  - **讀哪裡**：「Getting started」裡關於 `compile_commands.json` 的段落、「Configuration」頁（`CompileFlags`、`If`、`Diagnostics` 的完整欄位）、「Design → Indexing」。
  - **學到什麼**：設定檔的搜尋順序與合併規則、背景索引的儲存位置與更新策略，以及 clangd 對沒有資料庫的檔案怎麼推測指令。
  - **前提**：無。
- **[JSON Compilation Database Format Specification](https://clang.llvm.org/docs/JSONCompilationDatabase.html)** — LLVM 專案
  - **讀哪裡**：整頁（很短）。
  - **學到什麼**：`directory`、`file`、`command`/`arguments` 的精確語意；為什麼相對路徑要以 `directory` 為基準。
  - **前提**：無。
- **v6.18 原始碼 `scripts/clang-tools/gen_compile_commands.py`** 與 **`Documentation/kbuild/llvm.rst`**（docs.kernel.org 上的「Building Linux with Clang/LLVM」）
  - **讀哪裡**：腳本的 `process_line()` 與 `main()`；llvm.rst 的 `LLVM=1` 一節。
  - **學到什麼**：kernel 怎麼從 Kbuild 的 `.cmd` 檔重建編譯指令；如果改用 clang 建 kernel，資料庫和 clangd 的相容性會怎麼改善。
  - **前提**：會讀 Python 與 Makefile。

clangd 讓你看到「編譯器眼中的程式碼」，但它只給答案、不給過程。下一章直接把編譯器的中間產物攤開：展開後的 `.i`、生成的組語、真實的 struct layout、符號表。

→ [Ch 50 把編譯器當讀碼工具：.i、.s、objdump、pahole](./50-compiler-as-reading-tool.md)
