# Ch 6 — 暖身：用全套心法讀完 FreeBSD wc

> **目標**：第一次把 Ch 1–5 的心法從頭到尾跑完一輪：先讀 man page 建立預期、列問題、找入口、讀資料、讀主迴圈、追邊界，最後把程式碼在 Linux 上編起來實測，並和 GNU `wc` 對照。過程中會從原始碼裡讀出兩個真實的怪行為，並用實驗證實。
> **鴻溝**：本章五道鴻溝都會碰到一點，主要練**目標鴻溝**（每一步都先提問）與**領域鴻溝**（locale、多位元組字元、Capsicum 沙箱、libxo）；移植時碰到的平台差異則是**脈絡鴻溝**的小型版本。

> **環境**：FreeBSD `usr.bin/wc/wc.c`（367 行）與 `wc.1`（211 行），取自 FreeBSD src main 分支的快照（2026-11 取得；檔案內沒有版本標記，行號以這份快照為準）。編譯與測試在 WSL Ubuntu 22.04，gcc 11.4；對照組是 GNU coreutils 8.32 的 `wc`；可用 locale 為 `C`、`C.UTF-8`、`POSIX`。

## 為什麼需要這個？

前五章講的都是單招：提問、選擇性閱讀、找入口、資料結構優先。單招練完，要找一個對象把整套連起來打一次。這個對象要滿足三個條件：

1. **小到能讀完**。kernel 的任何子系統都太大，第一次全套演練會被細節淹沒。
2. **是真的程式碼**，不是教科書範例。真實程式碼有可攜性層、歷史包袱、奇怪的邊角，這些正是讀碼要面對的東西。
3. **行為你已經知道**。你用過 `wc`，對它有預期；讀碼時就能不斷拿「我以為它會怎樣」和「程式碼說它怎樣」比對。

FreeBSD 的 `wc` 完美符合：367 行，從 1980 年的 BSD 一路演化到今天，身上帶著 Capsicum 沙箱、libxo 結構化輸出、多位元組字元處理、`SIGINFO` 進度回報這些真實世界的東西。Spinellis 的原書大量使用 BSD 系統的原始碼當例子，選它當暖身也是向原書致意。

## 先建立直覺

這一章的流程就是一張地圖，之後每讀一個新程式都可以照走：

```
 步驟 0  讀文件（man page）  ─→  寫下預期 E1, E2, ...
   │
 步驟 1  提問              ─→  Q1, Q2, ...（每個問題都要能用程式碼回答）
   │
 步驟 2  找入口 + 全域資料  ─→  main()、全域變數
   │
 步驟 3  資料結構          ─→  狀態有哪些？誰改它？
   │
 步驟 4  主迴圈            ─→  happy path 先走一遍
   │
 步驟 5  邊界              ─→  錯誤、訊號、編碼、檔案結尾
   │
 步驟 6  實測              ─→  編譯、跑、和預期/對照組比對
```

最重要的一條規則：**每一步的產出都要寫下來**。預期寫下來，才能在步驟 6 發現哪個預期被推翻；問題寫下來，才知道什麼時候可以停。

## 步驟 0：先讀 man page，建立預期

讀原始碼之前先讀文件，原因很實際：文件告訴你「作者想讓它做什麼」，原始碼告訴你「它實際做什麼」。兩者的差距就是 bug 或文件過時，而你要先有前者才看得出差距。

`wc.1` 是 mdoc 格式的 troff 原始檔，可以用 `man ./wc.1` 排版後讀，也可以直接讀原始檔。挑幾段關鍵的（`wc.1:45-57`）：

```
.Nm
utility displays the number of lines, words, and bytes contained in each
input
.Ar file ,
or standard input (if no file is specified) to the standard output.
A line is defined as a string of characters delimited by a
.Aq newline
character.
Characters beyond the final
.Aq newline
character will not be included
in the line count.
```

`wc.1:77-106`（節錄）：

```
.It Fl L
Write the length of the line containing the most bytes (default) or characters
(when
.Fl m
is provided)
to standard output.
...
.It Fl c
The number of bytes in each input file
is written to the standard output.
This will cancel out any prior usage of the
.Fl m
option.
...
.It Fl m
The number of characters in each input file is written to the standard output.
If the current locale does not support multibyte characters, this
is equivalent to the
.Fl c
option.
```

`wc.1:129-139`：

```
If
.Nm
receives a
.Dv SIGINFO
(see the
.Cm status
argument for
.Xr stty 1 )
signal, the interim data will be written
to the standard error output in the same format
as the standard completion message.
```

把讀到的東西整理成預期清單：

| 編號 | 預期 | 來源 |
|---|---|---|
| E1 | 沒給選項時等於 `-c -l -w` | `wc.1:117-121` |
| E2 | 輸出順序固定：行、字、位元組、檔名 | `wc.1:115-116` |
| E3 | `-c` 和 `-m` 互相取消，後出現的贏 | `wc.1:92-94`、`104-106` |
| E4 | locale 不支援多位元組時，`-m` 等同 `-c` | `wc.1:100-103` |
| E5 | 「行」= 以換行結尾的字串；最後一個換行之後的字元**不算一行** | `wc.1:51-57` |
| E6 | 「字」= 以 `iswspace(3)` 為真的字元分隔的字串 | `wc.1:59-63` |
| E7 | `-L` 回報最長行的位元組數（或搭配 `-m` 時的字元數）；多檔時 total 是所有檔案中最長的 | `wc.1:77-88` |
| E8 | 收到 `SIGINFO` 時把目前的計數印到 stderr | `wc.1:129-139` |
| E9 | 多個檔案時最後多印一行 total | `wc.1:64-66` |

`SIGINFO` 是 BSD 特有的訊號：在終端機按 Ctrl-T，kernel 會送 `SIGINFO` 給前景行程，很多 BSD 工具（`dd`、`cp`、`wc`）會印出目前進度。**Linux 沒有 `SIGINFO`**——這會在步驟 6 變成一個實際問題。

## 步驟 1：提問

有了預期，列出你想用原始碼回答的問題。好問題的標準是：具體、能被某幾行程式碼回答、回答之後你會知道一件之前不知道的事。

- **Q1**：`-c` 要數位元組，需要把檔案整個讀一遍嗎？有沒有捷徑？
- **Q2**：多位元組字元（例如 UTF-8 的中文字是 3 bytes）如果剛好被 `read()` 的緩衝區邊界切成兩半，怎麼處理？
- **Q3**：遇到不合法的位元組序列（不是合法 UTF-8）怎麼辦？算不算字元？
- **Q4**：`SIGINFO` 是非同步到達的，在訊號處理函式裡直接 `printf` 是不安全的。它怎麼做？
- **Q5**：程式碼裡的 `capsicum`、`casper`、`xo_` 是什麼？拿掉會怎樣？
- **Q6**：E5 說最後一個換行之後的字元不算一行。那 `-L` 呢？最後一行沒有換行時，它的長度算不算？

Q6 是讀 man page 時自然冒出來的懷疑：文件對 `-l` 講了結尾沒換行的情況，對 `-L` 沒講。這種「文件沒講的角落」是最值得帶著去讀原始碼的問題。

## 步驟 2：入口與全域資料

入口是 `main()`（`wc.c:95`）。但依照 [Ch 5](./05-entry-points-and-data-first.md) 的「資料結構優先」，先看 `main()` 上面的全域變數（`wc.c:67-73`）：

```c
static const char *stdin_filename = "stdin";

static fileargs_t *fa;
static uintmax_t tlinect, twordct, tcharct, tlongline;
static bool doline, doword, dochar, domulti, dolongline;
static volatile sig_atomic_t siginfo;
static xo_handle_t *stderr_handle;
```

這七行已經是整個程式的狀態全集：

- `doline` … `dolongline`：五個 bool，對應五個選項 `-l -w -c -m -L`。
- `tlinect` … `tlongline`：四個累計值（`t` = total），給多檔案時的 total 行用（E9）。
- `siginfo`：型別是 `volatile sig_atomic_t`——看到這個型別就知道它是**訊號處理函式和主程式之間的旗標**。`sig_atomic_t` 保證讀寫是原子的，`volatile` 防止編譯器把讀取最佳化掉。這已經部分回答了 Q4。
- `fa`、`stderr_handle`：Capsicum 和 libxo 的 handle，先標記為「Q5 待查」。
- `stdin_filename`：注意它是一個**指標常數**，等一下會看到程式用指標比較（`file != stdin_filename`）而不是字串比較來判斷「這是不是 stdin」。

接著讀 `main()`。它 89 行，分成四段讀。

### 2a：解析選項（`wc.c:101-131`）

```c
	(void) setlocale(LC_CTYPE, "");

	argc = xo_parse_args(argc, argv);
	if (argc < 0)
		exit(EXIT_FAILURE);

	while ((ch = getopt(argc, argv, "clmwL")) != -1)
		switch((char)ch) {
		case 'l':
			doline = true;
			break;
		case 'w':
			doword = true;
			break;
		case 'c':
			dochar = true;
			domulti = false;
			break;
		case 'L':
			dolongline = true;
			break;
		case 'm':
			domulti = true;
			dochar = false;
			break;
		case '?':
		default:
			usage();
		}
	argv += optind;
	argc -= optind;
```

- `setlocale(LC_CTYPE, "")`：依環境變數設定字元分類的 locale。這行決定了之後 `MB_CUR_MAX`、`mbrtowc`、`iswspace` 的行為，是 E4 的根源。
- `case 'c'` 設 `dochar = true; domulti = false;`，`case 'm'` 反過來——E3「互相取消、後出現的贏」就是這四行。預期 E3 驗證通過。
- `argv += optind; argc -= optind;` 是 `getopt` 的標準慣用法：跳過已處理的選項，剩下的 `argv[0..argc-1]` 就是檔名。

### 2b：進入沙箱（`wc.c:133-143`）

```c
	(void)signal(SIGINFO, siginfo_handler);

	fa = fileargs_init(argc, argv, O_RDONLY, 0,
	    cap_rights_init(&rights, CAP_READ, CAP_FSTAT), FA_OPEN);
	if (fa == NULL)
		xo_err(EXIT_FAILURE, "Unable to initialize casper");
	caph_cache_catpages();
	if (caph_limit_stdio() < 0)
		xo_err(EXIT_FAILURE, "Unable to limit stdio");
	if (caph_enter_casper() < 0)
		xo_err(EXIT_FAILURE, "Unable to enter capability mode");
```

這段是 Q5 的核心，也是第一個**領域鴻溝**：不知道 Capsicum 是什麼，這段每個字都認得但不知道在幹嘛。補背景：

- **Capsicum** 是 FreeBSD 的 capability 沙箱機制（2010 年 USENIX Security 論文提出，見延伸閱讀）。行程呼叫 `cap_enter()` 進入「capability mode」之後，就**不能再用路徑名開新檔案**，只能使用手上已經有的 file descriptor，而且每個 fd 能做的操作被 `cap_rights_t` 限制住。
- 問題來了：`wc` 要讀命令列上的檔案，但進了沙箱就不能 `open()`。解法是 **Casper**：一個沙箱外的輔助服務。`fileargs_init()` 先把「允許開哪些檔名、用什麼權限（`CAP_READ`、`CAP_FSTAT`）」登記好，之後在沙箱裡用 `fileargs_open()` 請 Casper 代開。
- `caph_limit_stdio()` 把 stdin/stdout/stderr 的權限也縮到最小；`caph_cache_catpages()` 在進沙箱前先載入訊息目錄（NLS catalog），因為進去之後就開不了那些檔案了。`caph_enter_casper()` 才是真正進入沙箱的那一步。

讀到這裡就夠了。Capsicum 的內部機制和 `wc` 的計數邏輯完全無關——[Ch 4](./04-selective-reading.md) 的黑箱原則：**它對我們的問題（怎麼數）不重要**，把它當成「安全地打開檔案」的黑箱。

### 2c：預設值與主迴圈（`wc.c:145-168`）

```c
	/* Wc's flags are on by default. */
	if (!(doline || doword || dochar || domulti || dolongline))
		doline = doword = dochar = true;

	stderr_handle = xo_create_to_file(stderr, XO_STYLE_TEXT, 0);
	xo_open_container("wc");
	xo_open_list("file");

	errors = 0;
	total = 0;
	if (argc == 0) {
		xo_open_instance("file");
		if (cnt(NULL) != 0)
			++errors;
		xo_close_instance("file");
	} else {
		while (argc--) {
			xo_open_instance("file");
			if (cnt(*argv++) != 0)
				++errors;
			xo_close_instance("file");
			++total;
		}
	}
```

- 「全部都沒設就套預設值」——E1 驗證通過。
- `xo_open_container`、`xo_open_list`、`xo_open_instance` 是 **libxo**：Juniper 開發、FreeBSD 採用的結構化輸出函式庫。同一份程式碼加上 `--libxo json` 就能輸出 JSON，而不改任何計數邏輯。這些 `open/close` 呼叫是在描述輸出的巢狀結構（一個 `wc` 容器、裡面一個 `file` 清單、每個檔案一個 instance）。對計數邏輯來說又是黑箱。
- 真正做事的是 `cnt()`：每個檔案呼叫一次，沒有檔名時傳 `NULL` 代表讀 stdin。
- 注意 `++total` 在迴圈裡**不管 `cnt()` 成功與否都會加**。所以「檔名數 > 1」就會印 total，即使其中一個檔案打不開。記下來，步驟 6 驗證。

### 2d：收尾（`wc.c:170-182`）

```c
	xo_close_list("file");

	if (total > 1) {
		xo_open_container("total");
		show_cnt("total", tlinect, twordct, tcharct, tlongline);
		xo_close_container("total");
	}

	fileargs_free(fa);
	xo_close_container("wc");
	if (xo_finish() < 0)
		xo_err(EXIT_FAILURE, "stdout");
	exit(errors == 0 ? EXIT_SUCCESS : EXIT_FAILURE);
```

E9 驗證通過。`main()` 讀完了，整個程式的骨架是：**解析選項 → 進沙箱 → 對每個檔案呼叫 `cnt()` → 印 total**。所有有趣的東西都在 `cnt()` 裡。

## 步驟 3：資料——wc 沒有 struct，但有狀態

`wc` 沒有定義任何 struct，但「資料結構優先」照樣適用：先找出 `cnt()` 的狀態變數（`wc.c:215-226`）：

```c
	static char buf[MAXBSIZE];
	struct stat sb;
	mbstate_t mbs;
	const char *p;
	uintmax_t linect, wordct, charct, llct, tmpll;
	ssize_t len;
	size_t clen;
	int fd;
	wchar_t wch;
	bool gotsp, warned;

	linect = wordct = charct = llct = tmpll = 0;
```

| 變數 | 角色 |
|---|---|
| `buf[MAXBSIZE]` | `static` 的讀取緩衝區。`static` 讓它不佔 stack；`MAXBSIZE` 是 FreeBSD `<sys/param.h>` 定義的最大檔案系統區塊大小（65536） |
| `linect`, `wordct`, `charct` | 這個檔案的行數、字數、字元（或位元組）數 |
| `llct`, `tmpll` | 「目前最長行」與「目前這一行的長度」——`-L` 的狀態 |
| `gotsp` | 「上一個字元是空白嗎？」——數字數的**兩狀態狀態機** |
| `mbs` | `mbstate_t`：多位元組解碼的**跨呼叫狀態**，Q2 的關鍵 |
| `warned` | 這個檔案是否已經警告過非法序列（只警告一次） |

數字數的邏輯可以先畫成狀態機，讀程式碼時拿來對：

```
              非空白字元 / wordct++
   ┌────────┐ ──────────────────────→ ┌─────────┐
   │ gotsp  │                          │ !gotsp  │
   │ (在空白)│ ←────────────────────── │ (在字裡) │ ── 非空白字元 / 不動作
   └────────┘        空白字元           └─────────┘
       ↑ 初始狀態                           
       └─ 空白字元 / 不動作
```

一個字的計數發生在「從空白轉到非空白」的那一刻。初始狀態是 `gotsp = true`，所以檔案開頭的第一個非空白字元會開啟第一個字。

## 步驟 4：主迴圈——`cnt()` 的三條路

`cnt()` 有 149 行，但它其實是**三條獨立的路徑**，開頭兩個判斷決定走哪一條。先畫出來（這張圖就是 [Ch 4](./04-selective-reading.md) 的「主幹」）：

```
 cnt(file)
   │ 開檔（或用 stdin）
   ▼
 doword || (domulti && MB_CUR_MAX != 1) ?
   │ yes ──────────────────────────────────→ 路徑 3：word（慢，逐字元解碼）
   │ no
   ▼
 !doline && !dolongline ?（只要數位元組）
   │ yes → fstat；是 regular file？
   │         yes → 路徑 1：直接用 st_size（不 read）
   │         no  → 掉到路徑 2
   │ no
   ▼
 路徑 2：slurp（快，逐位元組找 '\n'）
```

### 路徑 1：不讀檔案就知道大小（`wc.c:234-254`）

```c
	if (doword || (domulti && MB_CUR_MAX != 1))
		goto word;
	/*
	 * If all we need is the number of characters and it's a regular file,
	 * just stat it.
	 */
	if (doline == 0 && dolongline == 0) {
		if (fstat(fd, &sb)) {
			xo_warn("%s: fstat", file);
			(void)close(fd);
			return (1);
		}
		if (S_ISREG(sb.st_mode)) {
			reset_siginfo();
			charct = sb.st_size;
			show_cnt(file, linect, wordct, charct, llct);
			tcharct += charct;
			(void)close(fd);
			return (0);
		}
	}
```

**Q1 的答案**：只要數位元組、而且是普通檔案，`wc -c` 根本不讀內容，`fstat` 拿 `st_size` 就回傳了。pipe、終端機這種沒有固定大小的東西才需要讀。這個最佳化在步驟 6 會用 strace 證實。

注意第一個 `if` 的 `goto word`：要數字、或 `-m` 且 locale 是多位元組，就跳到函式最後面的 `word:` label。這是 `goto` 的另一種用法——不是錯誤收尾，而是**跳到另一個實作**。

### 路徑 2：只找換行（`wc.c:261-292`）

```c
	while ((len = read(fd, buf, sizeof(buf))) != 0) {
		if (len < 0) {
			xo_warn("%s: read", file);
			(void)close(fd);
			return (1);
		}
		if (siginfo)
			show_cnt(file, linect, wordct, charct, llct);
		charct += len;
		if (doline || dolongline) {
			for (p = buf; len > 0; --len, ++p) {
				if (*p == '\n') {
					if (tmpll > llct)
						llct = tmpll;
					tmpll = 0;
					++linect;
				} else {
					tmpll++;
				}
			}
		}
	}
	reset_siginfo();
	if (doline)
		tlinect += linect;
	if (dochar)
		tcharct += charct;
	if (dolongline && llct > tlongline)
		tlongline = llct;
	show_cnt(file, linect, wordct, charct, llct);
	(void)close(fd);
	return (0);
```

原始碼上方的註解說得很清楚：數行比數字快得多，因為數字需要 locale 處理。這條路徑逐位元組比對 `'\n'`，不碰任何 locale 函式。

**Q6 的答案就在這裡**：`llct`（最長行）只在遇到 `'\n'` 時更新。如果檔案最後一行沒有換行，那一行的長度累積在 `tmpll` 裡，迴圈結束後**沒有任何一行把 `tmpll` 和 `llct` 比較**。所以預測：一個沒有換行結尾的檔案，`wc -L` 會漏掉最後一行。步驟 6 驗證。

再看最後幾行的累計：`if (dochar) tcharct += charct;`。什麼情況下會在 `dochar == false` 的狀態走進這條路徑、卻又需要累計字元數？回頭看分流條件：`-m` 在 `MB_CUR_MAX == 1`（例如 C locale）時**不會**跳到 word 路徑，而 `-m` 設定的是 `domulti = true, dochar = false`。所以 **`wc -lm` 在 C locale 下走路徑 2，字元數有算、有印，但沒有加進 total**。這是一個從程式碼讀出來的預測，記下來。

### 路徑 3：逐字元解碼（`wc.c:295-359`）

```c
word:	gotsp = true;
	warned = false;
	memset(&mbs, 0, sizeof(mbs));
	while ((len = read(fd, buf, sizeof(buf))) != 0) {
		if (len < 0) {
			xo_warn("%s: read", file);
			(void)close(fd);
			return (1);
		}
		p = buf;
		while (len > 0) {
			if (siginfo)
				show_cnt(file, linect, wordct, charct, llct);
			if (!domulti || MB_CUR_MAX == 1) {
				clen = 1;
				wch = (unsigned char)*p;
			} else if ((clen = mbrtowc(&wch, p, len, &mbs)) == 0) {
				clen = 1;
			} else if (clen == (size_t)-1) {
				if (!warned) {
					errno = EILSEQ;
					xo_warn("%s", file);
					warned = true;
				}
				memset(&mbs, 0, sizeof(mbs));
				clen = 1;
				wch = (unsigned char)*p;
			} else if (clen == (size_t)-2) {
				break;
			}
			charct++;
			if (wch != L'\n')
				tmpll++;
			len -= clen;
			p += clen;
			if (wch == L'\n') {
				if (tmpll > llct)
					llct = tmpll;
				tmpll = 0;
				++linect;
			}
			if (iswspace(wch)) {
				gotsp = true;
			} else if (gotsp) {
				gotsp = false;
				++wordct;
			}
		}
	}
```

最後五行就是步驟 3 畫的狀態機，一字不差。真正需要細讀的是中間那串 `mbrtowc` 的回傳值判斷——這是**領域鴻溝**：不知道 `mbrtowc(3)` 的四種回傳值，這段讀不懂。查 man page 補上：

| `mbrtowc` 回傳 | 意思 | wc 的處理 |
|---|---|---|
| 正數 n | 成功解出一個寬字元，用掉 n bytes | 一般情況，前進 n |
| `0` | 解出的是 NUL 字元 | 當成 1 byte 的字元 |
| `(size_t)-1` | 非法序列，`errno = EILSEQ` | 警告一次、重設解碼狀態、**把這 1 byte 當成一個字元** |
| `(size_t)-2` | 序列不完整：給的 bytes 是某個字元的開頭，但還不夠 | `break` 跳出內層迴圈，去讀下一塊 |

**Q3 的答案**：非法位元組每個算一個字元，而且只警告一次（`warned`）。

**Q2 的答案**藏在 `(size_t)-2` 那一行，而且它的正確性依賴一個不寫在 `wc.c` 裡的事實：C 標準規定 `mbrtowc` 回傳 `-2` 時，**已經把那幾個不完整的 bytes 吃進 `mbs` 狀態裡**。所以 `break` 之後丟掉緩衝區剩下的 bytes 是安全的——它們的資訊已經存在 `mbs` 裡，下一次 `read()` 拿到後半段時，`mbrtowc` 會接著解。這是一個典型的「程式碼看起來像 bug，其實依賴外部契約」的例子：不知道 `mbstate_t` 的語意，你會以為這裡會掉字元。步驟 6 會實際製造一個跨邊界的字元來驗證。

### show_cnt 與 SIGINFO（`wc.c:80-93`、`185-210`）

**Q4**：訊號處理函式本身只做一件事（`wc.c:80-85`）：

```c
static void
siginfo_handler(int sig __unused)
{

	siginfo = 1;
}
```

只設旗標，不印任何東西——這是處理非同步訊號的標準做法，因為 `printf` 不是 async-signal-safe。主迴圈在每次 `read()` 之後（路徑 2）或每個字元（路徑 3）檢查旗標，呼叫 `show_cnt()`。`show_cnt()` 開頭（`wc.c:191-196`）：

```c
	if (!siginfo)
		xop = NULL;
	else {
		xop = stderr_handle;
		siginfo = 0;
	}
```

如果是因為 `SIGINFO` 被呼叫，輸出導向 stderr 並清掉旗標——E8 驗證通過。再看 `reset_siginfo()`（`wc.c:87-93`）：

```c
static void
reset_siginfo(void)
{

	signal(SIGINFO, SIG_DFL);
	siginfo = 0;
}
```

每個檔案處理完都會呼叫它，把 `SIGINFO` 的處理方式**換回預設值**，而整個程式只有 `main()` 開頭裝過一次 handler。推論：**多檔案時，只有第一個檔案處理期間會回應 `SIGINFO`**。在 FreeBSD 上 `SIGINFO` 的預設動作是忽略，所以第二個檔案之後按 Ctrl-T 只是沒反應；但如果移植到一個預設動作是「終止」的訊號上，事情就不一樣了。記下來，步驟 6 會撞到。

這是刻意的設計還是疏忽？光看程式碼回答不了——這是**脈絡鴻溝**，要去翻 FreeBSD 的 commit 歷史（[Ch 37](./37-git-archaeology.md) 的方法）。我們手上只有快照，沒有歷史，所以這題留在未解疑問清單上。

## 步驟 5：把邊界整理成待驗證清單

讀完程式碼，把所有從程式碼推出、但還沒驗證的預測列出來：

| 編號 | 預測 | 依據 |
|---|---|---|
| P1 | `wc -c` 對普通檔案不呼叫 `read()` | `wc.c:240-253` |
| P2 | 跨緩衝區邊界的 UTF-8 字元不會被算錯 | `wc.c:322-323` + `mbrtowc` 契約 |
| P3 | 非法位元組每個算一個字元，只警告一次 | `wc.c:313-321` |
| P4 | 最後一行沒換行時，`-L` 不計入那一行 | `wc.c:271-280`：只在 `'\n'` 時更新 `llct` |
| P5 | C locale 下 `wc -lm` 多檔案時，total 的字元數是 0 | `wc.c:286-287`：`if (dochar)`，但 `-m` 讓 `dochar = false` |
| P6 | 其中一個檔案打不開時，仍印 total，exit status 非 0 | `wc.c:161-167`、`172` |
| P7 | 第一個檔案處理完之後，`SIGINFO` 回到預設動作 | `wc.c:91`、`283` |

## 步驟 6：在 Linux 上編譯並實測

### 第一次編譯：讓錯誤訊息告訴你可攜性層在哪

不改任何東西，直接編：

```
$ gcc -Wall -Ishim -o bsdwc wc.c xo_shim.c
wc.c:81:25: error: expected ‘;’, ‘,’ or ‘)’ before ‘__unused’
   81 | siginfo_handler(int sig __unused)
      |                         ^~~~~~~~
wc.c: In function ‘reset_siginfo’:
wc.c:91:16: error: ‘SIGINFO’ undeclared (first use in this function); did you mean ‘SIGIO’?
   91 |         signal(SIGINFO, SIG_DFL);
      |                ^~~~~~~
      |                SIGIO
...
wc.c: In function ‘cnt’:
wc.c:215:25: error: ‘MAXBSIZE’ undeclared (first use in this function)
  215 |         static char buf[MAXBSIZE];
```

（這是已經準備好 `shim/` 標頭之後的結果；沒有 `shim/` 的話，最先報錯的是找不到 `<sys/capsicum.h>`、`<libxo/xo.h>` 等標頭。）編譯器幫你列出了**所有 FreeBSD 專屬的東西**：`__unused` 屬性巨集、`SIGINFO` 訊號、`MAXBSIZE` 常數，加上 Capsicum、Casper、libxo 三個函式庫。這本身就是一種讀碼技巧：**拿到陌生平台的程式碼，先在你的平台上編一次，錯誤清單就是它的可攜性依賴清單。**

### 策略：不改 wc.c，在外面墊一層

兩種做法：直接修改 `wc.c` 把 FreeBSD 的東西拿掉，或者**不碰 `wc.c`，在外面提供假的標頭和函式**（shim）。選後者，理由是：

- 讀碼時你想讀的是**原汁原味**的程式碼，行號和上游一致；
- 所有平台差異集中在 shim 裡，一眼看得出「為了移植做了哪些假設」；
- 如果之後拿到新版的 `wc.c`，換上去重編就好。

三個巨集用 `-D` 解決：

| FreeBSD 的東西 | Linux 上的替代 | 理由 |
|---|---|---|
| `__unused` | `-D'__unused=__attribute__((unused))'` | FreeBSD `<sys/cdefs.h>` 提供的屬性巨集，意思就是 GCC 的 `unused` 屬性；glibc 沒有這個名字 |
| `SIGINFO` | `-DSIGINFO=SIGUSR1` | Linux（x86）沒有 `SIGINFO`；借用 `SIGUSR1`，可以用 `kill -USR1` 測試 |
| `MAXBSIZE` | `-DMAXBSIZE=65536` | FreeBSD `<sys/param.h>` 的值 |

Capsicum 和 Casper 的 shim 是一組空殼：`cap_rights_init` 什麼都不做、`caph_*` 一律回傳成功、`fileargs_open()` 退化成普通的 `open(2)`。例如 `shim/casper/cap_fileargs.h`：

```c
/* shim: fileargs_* 退化成普通 open(2) */
#pragma once
#include <fcntl.h>
typedef struct { int dummy; } fileargs_t;
#define FA_OPEN 1
static fileargs_t fa_dummy;
static inline fileargs_t *fileargs_init(int argc, char *argv[], int flags,
    int mode, void *rights, int op)
{ (void)argc; (void)argv; (void)flags; (void)mode; (void)rights; (void)op;
  return &fa_dummy; }
static inline int fileargs_open(fileargs_t *fa, const char *name)
{ (void)fa; return open(name, O_RDONLY); }
static inline void fileargs_free(fileargs_t *fa) { (void)fa; }
```

`shim/sys/capsicum.h`、`shim/capsicum_helpers.h`、`shim/libcasper.h` 同理，各三五行。（**這等於把沙箱整個拿掉了**：移植版的 `wc` 沒有任何 capability 限制。這對學習無妨，但要清楚它和 FreeBSD 原版在安全性上不等價。）

libxo 稍微麻煩一點，因為 `show_cnt()` 用的格式字串長這樣：`" {:lines/%7ju/%ju}"`。libxo 的欄位語法是 `{:名字/文字格式/結構化格式}`，shim 要把它改寫回普通的 printf 格式 `" %7ju"`。`xo_shim.c` 的核心：

```c
/* 把 " {:lines/%7ju/%ju}" 這種 libxo 欄位描述改寫成 " %7ju" 再交給 vfprintf */
void xo_emit_h(xo_handle_t *h, const char *fmt, ...)
{
	char out[256], *o = out;
	for (const char *p = fmt; *p; ) {
		if (p[0] == '{' && p[1] == ':') {
			const char *slash = strchr(p, '/');
			const char *end = strchr(p, '}');
			const char *stop = strchr(slash + 1, '/');
			if (stop == NULL || stop > end) stop = end;
			memcpy(o, slash + 1, stop - slash - 1);
			o += stop - slash - 1;
			p = end + 1;
		} else
			*o++ = *p++;
	}
	*o = '\0';
	va_list ap;
	va_start(ap, fmt);
	vfprintf(h ? h : stdout, out, ap);
	va_end(ap);
}
```

`xo_warn`、`xo_err` 轉給 glibc 的 `vwarn`、`verr`；`xo_open_*`/`xo_close_*` 定義成空巨集。這個 shim 只支援 `wc.c` 用到的那一小撮語法，`--libxo json` 不能用。

```
$ gcc -Wall -Ishim -D'__unused=__attribute__((unused))' -DSIGINFO=SIGUSR1 \
      -DMAXBSIZE=65536 -o bsdwc wc.c xo_shim.c
wc.c:33:19: warning: ‘copyright’ defined but not used [-Wunused-const-variable=]
   33 | static const char copyright[] =
      |                   ^~~~~~~~~
$ diff ~/code_reading/freebsd/usr.bin/wc/wc.c wc.c && echo "wc.c unchanged"
wc.c unchanged
```

編過了，剩一個無害的警告（`copyright` 字串是給 `strings(1)` 看的，程式不會用到）。**`wc.c` 一個字都沒改。**

### 測試資料

```
$ printf 'hello world\nfoo  bar baz\n' > a.txt
$ printf '讀碼 心法\n第二行沒有換行' > zh.txt
$ printf 'ok\n\xff\xfe bad\n' > bad.txt
$ printf 'no newline at end' > nonl.txt
```

`zh.txt` 裡每個中文字在 UTF-8 是 3 bytes；第一行「讀碼 心法\n」是 3+3+1+3+3+1 = 14 bytes，第二行 7 個字 21 bytes、沒有換行，總共 35 bytes、13 個字元。`bad.txt` 的 `\xff\xfe` 在 UTF-8 裡永遠不合法。

### 驗證 P1：wc -c 不讀檔案

```
$ strace -e trace=openat,read,fstat,newfstatat ./bsdwc -c a.txt 2>&1 | tail -4
openat(AT_FDCWD, "a.txt", O_RDONLY)     = 3
newfstatat(3, "", {st_mode=S_IFREG|0644, st_size=25, ...}, AT_EMPTY_PATH) = 0
newfstatat(1, "", {st_mode=S_IFIFO|0600, st_size=0, ...}, AT_EMPTY_PATH) = 0
      25 a.txt
$ strace -e trace=openat,read,fstat,newfstatat ./bsdwc -l a.txt 2>&1 | tail -5
openat(AT_FDCWD, "a.txt", O_RDONLY)     = 3
read(3, "hello world\nfoo  bar baz\n", 65536) = 25
read(3, "", 65536)                      = 0
newfstatat(1, "", {st_mode=S_IFIFO|0600, st_size=0, ...}, AT_EMPTY_PATH) = 0
       2 a.txt
```

`-c` 只有 `newfstatat`（glibc 2.35 把 `fstat()` 實作成 `newfstatat(fd, "", ..., AT_EMPTY_PATH)`），沒有任何 `read(3, ...)`；`-l` 則用 65536 bytes 的緩衝區讀了兩次。P1 成立。（最後那個對 fd 1 的 `newfstatat` 是 stdio 在決定 stdout 的緩衝方式，不是 `wc` 的邏輯。）

### 驗證 P2：跨邊界的多位元組字元

造一個檔案：65535 個 `a`，接一個「讀」（3 bytes），再接換行。第一次 `read()` 拿 65536 bytes，「讀」的第一個 byte 剛好在緩衝區最後：

```
$ python3 -c "import sys; sys.stdout.buffer.write(b'a'*65535 + '讀'.encode() + b'\n')" > straddle.txt
$ ./bsdwc -m straddle.txt; wc -m straddle.txt
   65537 straddle.txt
65537 straddle.txt
$ strace -e trace=read ./bsdwc -m straddle.txt 2>&1 | grep 'read(3, "[a\\]'
read(3, "\177ELF\2\1\1\3\0\0\0\0\0\0\0\0\3\0>\0\1\0\0\0P\237\2\0\0\0\0\0"..., 832) = 832
read(3, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"..., 65536) = 65536
read(3, "\256\200\n", 65536)            = 3
```

第一行是動態連結器在載入 libc（fd 3 當時還是 libc 的檔案），可以忽略。第二次讀 `straddle.txt` 的 `read()` 拿到的開頭是 `\256\200`——「讀」（`E8 AE 80`）的後兩個 byte。第一個 byte `\350` 在上一輪被 `mbrtowc` 回傳 `-2` 時吃進了 `mbs`。總數 65537 = 65535 + 1 + 1，正確。P2 成立：那行看起來會掉字元的 `break` 是對的。

### 驗證 P3–P5：和 GNU wc 並排

```
$ for loc in C C.UTF-8; do ... done    # 對每個 locale、每個檔案、每組選項跑兩邊
===== LC_ALL=C
zh.txt        bsd:       1       3      35 zh.txt  | gnu: 1  0 35 zh.txt
zh.txt   -m   bsd:      35 zh.txt  | gnu:35 zh.txt
zh.txt   -L   bsd:      13 zh.txt  | gnu:1 zh.txt
bad.txt       bsd:       2       3      10 bad.txt  | gnu: 2  2 10 bad.txt
bad.txt  -L   bsd:       6 bad.txt  | gnu:4 bad.txt
===== LC_ALL=C.UTF-8
zh.txt        bsd:       1       3      35 zh.txt  | gnu: 1  3 35 zh.txt
zh.txt   -m   bsd:      13 zh.txt  | gnu:13 zh.txt
zh.txt   -L   bsd:      13 zh.txt  | gnu:14 zh.txt
zh.txt   -mL  bsd:      13       5 zh.txt  | gnu:13 14 zh.txt
bad.txt       bsd:       2       3      10 bad.txt  | gnu: 2  2 10 bad.txt
bad.txt  -m   bsd:bsdwc: bad.txt: Invalid or incomplete multibyte or wide character       10 bad.txt  | gnu:8 bad.txt
bad.txt  -L   bsd:       6 bad.txt  | gnu:4 bad.txt
```

（節錄自完整輸出。）逐項解讀：

- **P3（非法位元組）**：UTF-8 下 `bad.txt -m`，BSD 算 10 個字元並警告一次（`EILSEQ` 的訊息是 glibc 的 `strerror` 文字），GNU 算 8 個——GNU 8.32 看起來不把非法位元組算成字元。兩者都是「合理」的選擇，POSIX 對非法序列沒有規定到這麼細。P3 成立。
- **P4（最後一行沒換行）**：UTF-8 下 `zh.txt -L`，BSD 回 13（第一行「讀碼 心法」的 13 bytes），GNU 回 14。BSD 完全沒考慮第二行（21 bytes、7 個字），因為它沒有換行結尾。更直接的測試：

  ```
  $ ./bsdwc -L nonl.txt; wc -L nonl.txt
         0 nonl.txt
  17 nonl.txt
  ```

  一個 17 字元、沒有換行的檔案，BSD 說最長行是 0。P4 成立。
- **`-L` 的定義根本不同**：GNU 的 14 不是 bytes 也不是字元數，而是**顯示寬度**——7 個中文字，每個佔兩欄。C locale 下 GNU 的 `zh.txt -L` 回 1，因為非 ASCII 位元組在 C locale 下是不可列印字元，寬度 0，只剩那個空白算 1 欄。BSD 的 `-L` 是位元組數（或 `-m` 時的字元數），和 man page 說的一致。兩個 `wc -L` 名字一樣、語意不同。
- **字數**：`bad.txt` 的字數 BSD 3、GNU 2；C locale 下 `zh.txt` 的字數 BSD 3、GNU 0。BSD 嚴格照 man page：只要不是 `iswspace` 就算字的一部分。GNU 8.32 的行為看起來是「不可列印字元（包含非法位元組）不構成字」。**這段對 GNU 的解釋是從輸出反推的，本章沒有讀 GNU coreutils 的原始碼**；要確認請讀 coreutils 的 `src/wc.c`。

P5 單獨測：

```
$ LC_ALL=C ./bsdwc -lm a.txt zh.txt; LC_ALL=C wc -lm a.txt zh.txt
       2      25 a.txt
       1      35 zh.txt
       3       0 total
 2 25 a.txt
 1 35 zh.txt
 3 60 total
$ ./bsdwc -lm a.txt zh.txt          # C.UTF-8
       2      25 a.txt
       1      13 zh.txt
       3      38 total
```

**C locale 下 total 的字元數是 0**，GNU 正確地回 60。UTF-8 下沒問題，因為 `-m` 在多位元組 locale 會走路徑 3，那裡的累計寫的是 `if (dochar || domulti)`（`wc.c:353`）。兩條路徑的累計條件不一致，就是這個 bug 的成因。P5 成立——**這是一個靠讀碼找到、再用實驗確認的真實 bug**。我們是在 Linux 移植版上重現的；FreeBSD 的 C locale 同樣是 `MB_CUR_MAX == 1`，理論上原生環境也會發生，但沒有在 FreeBSD 上實測。

### 驗證 P6：打不開的檔案

```
$ ./bsdwc /nonexistent a.txt; echo "rc=$?"
bsdwc: /nonexistent: open: No such file or directory
       2       5      25 a.txt
       2       5      25 total
rc=1
```

只有一個檔案成功，仍然印了 total，exit status 是 1。P6 成立。

### 驗證 P7：SIGINFO（用 SIGUSR1 代替）

先測單一檔案的情況。從一個慢慢吐資料的 pipe 讀，中途送訊號：

```
$ { printf 'a b\n'; sleep 1; printf 'c d e\n'; sleep 2; printf 'f\n'; } | ./bsdwc > out.txt 2> err.txt &
$ sleep 1.5; kill -USR1 $(pgrep -n bsdwc); wait
$ cat out.txt; cat err.txt
       3       6      12
       2       5      10
```

stderr 收到中途的計數（2 行、5 字、10 bytes），stdout 是最終結果。有個細節：訊號在 1.5 秒到達，但 `wc` 正卡在 `read()` 裡；glibc 的 `signal()` 用的是 BSD 語意（`SA_RESTART`），`read()` 被自動重啟、繼續等，直到 3 秒時 `f\n` 到達，路徑 3 處理第一個字元前檢查旗標，才印出中途計數——所以印的是收到 `f` 之前的數字。

再測兩個檔案，在第二個檔案處理期間送訊號：

```
$ { sleep 2; echo x y; } | ./bsdwc a.txt /dev/stdin > o2.txt 2> e2.txt &
$ sleep 1; kill -USR1 $(pgrep -n bsdwc); wait $!; echo "exit status: $?"
bash: line 5: 334342 Broken pipe             { sleep 2; echo x y; }
     334343 User defined signal 1   | ./bsdwc a.txt /dev/stdin > o2.txt 2> e2.txt
exit status: 138
$ cat o2.txt
$
```

`wc` 被 `SIGUSR1` **殺掉了**（138 = 128 + 10，10 是 `SIGUSR1` 的編號），連第一個檔案已經算好的結果都沒印出來（stdout 導向檔案時是全緩衝，還在緩衝區裡就隨行程消失了）。P7 成立，而且展示了移植的陷阱：`reset_siginfo()` 把處理方式換回 `SIG_DFL`，在 FreeBSD 上 `SIGINFO` 的預設動作是忽略，無害；我們用來代替的 `SIGUSR1` 預設動作是終止行程。**同一行程式碼，換一個平台語意就變了**——這正是 shim 方法要明確列出每一個替代的原因。

## 讀完之後：一頁總結

```
wc.c（FreeBSD 快照，367 行）
入口：main()  wc.c:95
結構：解析選項 → Capsicum/Casper 沙箱（黑箱）→ 每檔呼叫 cnt() → total
cnt() 三條路：
  1. 只數 bytes + regular file → fstat，不讀           wc.c:240-254
  2. 數行/最長行（或 C locale 的 -m）→ 逐 byte 找 '\n'  wc.c:261-292
  3. 數字，或多位元組 locale 的 -m → mbrtowc 逐字元     wc.c:295-359
狀態：gotsp 兩狀態機數字；mbstate_t 跨 read() 保存不完整字元
怪行為（皆已實測）：
  - -L 不計最後一行沒換行的部分                        wc.c:271-280
  - C locale 下 -lm 的 total 字元數為 0（累計條件不一致）wc.c:286 vs 353
  - SIGINFO handler 只在第一個檔案期間有效              wc.c:91, 283
未解疑問：
  - reset_siginfo 換回 SIG_DFL 是刻意的嗎？（需要 FreeBSD git 歷史）
  - GNU wc 的字數規則細節（需要讀 coreutils src/wc.c）
```

這份總結的格式就是 [Ch 7](./07-reading-notes-and-diagrams.md) 要正式介紹的讀碼筆記。

## 對比與取捨：BSD wc vs GNU wc

| 面向 | FreeBSD wc | GNU coreutils 8.32 wc |
|---|---|---|
| `-L` 的單位 | 位元組（`-m` 時為字元） | 顯示寬度（中文字算 2 欄） |
| 最後一行無換行時的 `-L` | 不計入 | 計入 |
| 非法位元組（`-m`） | 每個算 1 字元，警告一次 | 不算字元 |
| 字的定義 | 非 `iswspace` 即字元 | 依觀察，不可列印字元不構成字 |
| `-c` 對普通檔案 | `fstat` 取大小，不讀 | 本章未驗證 |
| 安全模型 | Capsicum 沙箱 | 無沙箱 |
| 結構化輸出 | libxo（`--libxo json`） | 無 |

沒有哪一邊「對」：POSIX 規定了 `-c -l -m -w` 的基本語意，`-L` 根本不在 POSIX 裡（`wc.1:189-197` 自己承認它是相容 GNU 的非標準擴充，但兩者的單位其實不同）。讀碼的價值在於：**你現在知道兩邊的差異從哪一行來，而不是只知道「輸出不一樣」**。

## 踩雷集錦

1. **「man page 說的就是程式做的」**：man page 說 `-L` 回報最長行，但沒說最後一行沒換行時怎麼辦；程式碼的答案是「不算」。文件沒寫的角落要靠原始碼回答，文件寫了的也要驗證。
2. **「看起來會掉資料的 `break` 就是 bug」**：`mbrtowc` 回傳 `-2` 後 `break` 丟掉剩餘 bytes，看起來會掉字元，其實資訊已經存在 `mbstate_t` 裡。判斷一段程式碼是不是 bug 之前，先確認它依賴的外部契約（這裡是 C 標準對 `mbrtowc` 的規定）。
3. **「移植時把巨集換掉就好」**：`SIGINFO` → `SIGUSR1` 在語法上完全成立，語意上卻在多檔案時把程式殺掉。替代品的**預設行為**也要對齊，不只是名字。
4. **「兩個工具選項名稱一樣，語意就一樣」**：BSD `-L` 和 GNU `-L` 一個數位元組、一個數顯示寬度。比較兩個實作時，以實測為準。
5. **「拿掉 Capsicum 只是少了一些呼叫」**：shim 讓程式能跑，但也拿掉了它的整個安全模型。學習用沒問題；如果你要把移植版用在處理不可信輸入的地方，它和原版不等價。

## 進階：再往深一層

- **讀 GNU coreutils 的 `src/wc.c`**：用本章同樣的七步驟讀它，回答「GNU 的字數規則到底是什麼」和「GNU 的 `-c` 有沒有 `fstat` 捷徑」。GNU 版本比 BSD 長很多（就我所知較新的版本還有用 SIMD 指令加速數行的分支），是練 [Ch 4](./04-selective-reading.md) 選擇性閱讀的好材料。
- **修掉 P5 的 bug**：最小修改是把 `wc.c:286` 的 `if (dochar)` 改成 `if (dochar || domulti)`，和路徑 3 一致。改完用同樣的測試確認 total 變成 60（我們在移植版上試過：`sed '286s/if (dochar)/if (dochar || domulti)/'` 之後重編，`LC_ALL=C ./bsdwc_fix -lm a.txt zh.txt` 的 total 行變成 `3      60 total`）。想想看：為什麼上游一直沒發現？（提示：多少人會在 C locale 下同時用 `-l` 和 `-m`，而且還看 total？）
- **把 P4 也修掉**：在路徑 2 和路徑 3 的迴圈結束後，各補一次 `if (tmpll > llct) llct = tmpll;`。但要先想清楚：這會改變 `wc -L` 的既有行為，可能有腳本依賴它。這就是為什麼「讀懂一個怪行為」和「決定要不要修」是兩件事。
- **Capsicum 本身**：如果你對沙箱有興趣，FreeBSD 的 `capsicum(4)` man page 和 2010 年的論文是起點；Linux 上概念最接近的是 seccomp 與 Landlock，但設計哲學不同（Capsicum 以 fd 為中心的 capability，seccomp 以 syscall 過濾為中心）。

## 動手練習

1. **重現整章**：在你的 WSL 裡照步驟 6 建 shim、編譯、跑 P1–P7。任何一項結果和本章不同，找出原因（locale？glibc 版本？kernel？）。
2. **換一個程式**：用同樣七步驟讀 `~/code_reading/freebsd/bin/cat/cat.c`。先讀 FreeBSD `cat(1)` 的 man page（可以在 FreeBSD 官網的 man page 查詢系統找到）寫下預期，再讀程式碼。提示：注意 `cat` 有兩個主要函式 `raw_cat` 和 `cook_cat`，想想它們的分流條件和 `wc` 的三條路徑有什麼相似之處。這也是 [練習 A](./practice-a-cold-read-userspace.md) 的暖身。
3. **故意製造邊界**：寫一個測試檔，讓一個**非法**位元組剛好落在 65536 的邊界上（第 65536 個 byte 是 `\xe8`，第 65537 個 byte 是 `a`）。預測 BSD wc `-m` 的輸出和警告訊息，再實測。（提示：`\xe8` 開頭會讓 `mbrtowc` 先回 `-2`，接到 `a` 之後呢？）

<details>
<summary>練習 3 的實測結果與解釋</summary>

```
$ python3 -c "import sys; sys.stdout.buffer.write(b'a'*65535 + b'\xe8' + b'a\n')" > edge.txt
$ ./bsdwc -m edge.txt
bsdwc: edge.txt: Invalid or incomplete multibyte or wide character
   65537 edge.txt
$ python3 -c "import sys; sys.stdout.buffer.write(b'a'*100 + b'\xe8' + b'a\n')" > mid.txt
$ ./bsdwc -m mid.txt
bsdwc: mid.txt: Invalid or incomplete multibyte or wide character
     103 mid.txt
```

`edge.txt` 有 65538 bytes，`wc -m` 回 65537：**那個 `\xe8` 沒被算進去**。過程是：第一輪 `\xe8` 在緩衝區最後，`mbrtowc` 回 `-2`，它被吃進 `mbs`、`break`；第二輪從 `a` 開始，`mbrtowc` 發現「`\xe8` 後面接 `a`」不合法，回 `-1`，`wc` 重設 `mbs`、把**目前這個 byte（`a`）**算成一個字元。`\xe8` 這個 byte 存在 `mbs` 裡，隨著 `memset` 一起消失了。

`mid.txt` 的同一個非法序列不在邊界上，`\xe8` 被當成一個字元算進去（100 + 1 + 1 + 1 = 103）。**同一個非法位元組，算不算字元取決於它落在緩衝區的哪裡**——P3 說的「非法位元組每個算一個字元」只在非邊界的情況成立。這是比 P5 更隱蔽的不一致，只有刻意製造邊界才看得到。

</details>

## 本章重點整理

- 全套流程：讀文件寫預期 → 提問 → 入口與全域資料 → 狀態 → 主迴圈 → 邊界清單 → 實測。每一步的產出都寫下來。
- 先在自己的平台上編一次，錯誤清單就是可攜性依賴清單；用 shim 隔離平台差異，原始碼保持原樣。
- `wc` 的 `cnt()` 是三條路徑：`fstat` 捷徑、逐位元組找換行、`mbrtowc` 逐字元解碼。
- 讀出並實測了三個怪行為：`-L` 漏掉無換行的最後一行、C locale 下 `-lm` 的 total 字元數為 0、`SIGINFO` 只在第一個檔案期間有效。
- 外部契約（`mbrtowc` 的 `-2` 語意、訊號的預設動作）是判斷「這是不是 bug」的必要知識。

## 自我檢核

- [ ] 不看本章，畫出 `cnt()` 的三條路徑分流圖，並說出每條路徑的觸發條件。
- [ ] 用自己的話解釋：為什麼 `mbrtowc` 回傳 `-2` 時 `break` 不會掉字元？如果換成一個沒有 `mbstate_t` 的舊 API，這段程式該怎麼寫？
- [ ] P5 這個 bug 是怎麼從程式碼裡「看」出來的？你讀到哪兩行之間的不一致？下次讀別的程式碼時，這種「兩條平行路徑、條件不一致」的模式要怎麼主動找？
- [ ] 如果同事說「我把 FreeBSD 的工具移植到 Linux，只是把 `SIGINFO` 換成 `SIGUSR1`，不會有問題」，你會問他哪一個問題？
- [ ] 回想你用過的某個命令列工具，它的某個選項在 GNU 和 BSD（或 macOS）上行為不同嗎？你現在知道怎麼找出差異的根源嗎？

## 延伸閱讀

### 論文

- **Capsicum: practical capabilities for UNIX** — Robert N. M. Watson, Jonathan Anderson, Ben Laurie, Kris Kennaway，USENIX Security Symposium（2010）
  - **核心貢獻**：在 UNIX 的 file descriptor 模型上加入 capability 語意，讓既有程式（`tcpdump`、`gzip`、Chromium）能以很小的修改進入沙箱。
  - **讀哪裡**：前三節（動機、設計、capability mode 的語意）；評估與效能部分可以跳過。
  - **和本章的關聯**：解釋了 `wc.c:135-143` 那串呼叫存在的理由——為什麼要先把檔案登記好才能進沙箱。前提：知道 file descriptor 與 `open(2)`。

### 官方文件 / 規格

- **wc — word, line, and byte or character count**，The Open Group Base Specifications（POSIX.1，Issue 7 / IEEE Std 1003.1）
  - **讀哪裡**：DESCRIPTION 與 OPTIONS 兩節，特別是「word」的定義與 `-m` 的說明。
  - **能學到什麼**：哪些行為是標準規定的（`-c -l -m -w`），哪些是實作自由（`-L` 根本不在標準裡、非法序列的處理）。拿來判斷 BSD 和 GNU 的差異誰「違規」：答案是都沒有。
- **mbrtowc(3)**（Linux man-pages，`man 3 mbrtowc`）
  - **讀哪裡**：RETURN VALUE 一節，四種回傳值的定義。
  - **能學到什麼**：本章 Q2、Q3 的外部契約。讀完再回頭看 `wc.c:308-324`，每個分支的意義就清楚了。
- **libxo 文件**（Juniper Networks，GitHub 上的 `Juniper/libxo` 專案）
  - **讀哪裡**：欄位描述語法（field descriptor）的說明，也就是 `{:name/format}` 的部分。
  - **能學到什麼**：`show_cnt()` 那些格式字串的完整語意，以及 `--libxo json` 時輸出長什麼樣子。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 2 章「Basic Programming Elements」與第 11 章「A Complete Example」。
  - **這本書的定位**：第 2 章講基本控制結構的讀法，例子取自 BSD 系統的原始碼，和本章的對象同一類；第 11 章是一次從頭到尾的完整讀碼示範，可以和本章的流程對照，看看本章多了哪些 2003 年還不普遍的手段（strace 驗證、在異平台編譯、shim 隔離）。

你剛剛寫了一頁總結、一張預測清單、一份未解疑問。這些東西有固定的格式，也有更好的畫圖方式——下一章把它們正式化。

→ [Ch 7 讀碼筆記與畫圖：把理解外部化](./07-reading-notes-and-diagrams.md)
