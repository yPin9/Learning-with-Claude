# Ch 43 — Syzkaller / syzbot：fuzzer 原理、看懂 report、跑自己的 fuzzer

> **目標**：理解 coverage-guided kernel fuzzing 的底層機制（kcov、syzlang、corpus mutation）、能獨立讀懂一份 syzbot report 的每個部分、知道怎麼在自己的 QEMU target 上架一個最小 syzkaller、並能把 syzkaller 生出的 reproducer 當成 exploit 開發的起點。

> **環境**：syzkaller 以 GitHub 上游 `master` 分支近期行為為準（syzkaller 沒有傳統的版本號發布,持續滾動更新,本章描述的是架構層面幾年未變的設計，具體指令旗標請以你 clone 時的 `docs/` 為準）；fuzz target 為 Linux 6.6 LTS、x86-64、QEMU。所有跑指令的段落若標「未實測」，代表本環境沒有可長時間跑 fuzzer 的資源，行為依 syzkaller 官方文件與架構設計重建，請在你自己的機器上驗證。

## 為什麼需要這個？

[Ch 42](./42-patch-to-rootcause.md) 教你從**已經被修好的洞**反推根因——那是 N-day 的路。這一章走另一條路：怎麼找到**還沒有人報告過**的洞，也就是 0-day 開發的起點。

在 syzkaller 出現之前（2015 年由 Google 的 Dmitry Vyukov 釋出），kernel fuzzing 的主流做法是**純隨機測試**：隨便拼出一堆亂數塞進 syscall，跑壞了就記下來。這個做法在 kernel 上效果很差，原因很直接——kernel syscall 的參數結構非常嚴格：一個 `ioctl` 期待的 `cmd` 值只有幾十種合法選項、一個 `fd` 必須先用 `open`/`socket` 拿到才有意義、一段 buffer 的長度字段如果亂填,99% 的機率在**還沒碰到真正的邏輯之前**就被一個 `copy_from_user` 的合法性檢查擋掉。純隨機測試大部分時間在測試 kernel 的輸入驗證，不是在測試驗證之後的邏輯——而漏洞通常藏在驗證之後。

Syzkaller 解決了兩個問題：

1. **結構化生成**——用一套語言（syzlang）描述每個 syscall 的參數長什麼樣、哪些值互相依賴，讓生成的測試案例本身就是「語法合法」的，不會卡在輸入驗證那一層。
2. **coverage feedback**——每跑一個測試案例，用 kcov 記錄這次執行碰到了哪些程式碼位置；如果這次的輸入讓程式走到了**以前沒走過的地方**，就把這個輸入留下來當「種子」繼續變異,否則就丟掉。這讓 fuzzer 自動往「還沒被測過的邏輯」鑽，而不是在同一條路徑上重複打轉。

這套組合讓 syzkaller 從 2015 年至今累計找出數千個 Linux kernel 漏洞（syzbot 的 dashboard 持續在更新這個數字），是目前 mainline kernel 最主要的 bug 來源之一——理解它，你才能看懂現代絕大多數 CVE 的「發現方式」那一欄。

## 先建立直覺

想像你在一個完全黑暗的迷宮裡找出口，手上只有一根會留下痕跡的繩子：

```
純隨機測試：                      coverage-guided fuzzing：
每次都從入口亂走                  每次記住繩子走過的路
┌─────┐                          ┌─────┐
│ ●~~~│  走幾步就撞牆回頭          │ ●───┼──┐  走到沒走過的分岔，
│  ~  │  下次又從頭亂走            │     │  │  優先往那裡繼續延伸
│ ~~~~│  很難探到深處               │  ───┼──┘  繩子覆蓋的範圍
└─────┘                          └─────┘  (= code coverage) 持續擴大
```

kcov 就是那根「留下痕跡的繩子」——它在 kernel 編譯時被插入到幾乎每一個基本區塊（basic block）前，執行到就記一筆「我來過這裡」。syzkaller 的 fuzzer 比對每次執行前後的覆蓋範圍，覆蓋範圍變大的輸入就留下來當種子繼續變異,沒有變大的就丟掉。整個迴圈不需要人工介入，可以一直跑。

## 核心概念：kcov 怎麼記錄覆蓋率

kcov 是這套機制的硬體——它是一個 kernel 編譯時選項（`CONFIG_KCOV=y`），用編譯器插樁（instrumentation）的方式，在每個基本區塊前插入一個對 `__sanitizer_cov_trace_pc` 之類函式的呼叫。使用者態程式透過 `/sys/kernel/debug/kcov` 這個字元裝置來控制它：

```c
// 示意：單個執行緒開啟 kcov 收集（未實測，依 kcov 官方文件架構重建）
int fd = open("/sys/kernel/debug/kcov", O_RDWR);
ioctl(fd, KCOV_INIT_TRACE, COVER_SIZE);
unsigned long *cover = mmap(NULL, COVER_SIZE * sizeof(unsigned long),
                            PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
ioctl(fd, KCOV_ENABLE, KCOV_TRACE_PC);   // 開始記錄這個 task 碰到的 PC
// ... 執行想測試的 syscall ...
ioctl(fd, KCOV_DISABLE, 0);
unsigned long n = cover[0];              // 這段執行命中了多少個不同的 PC
```

重點是 kcov 的記錄範圍是**per-task**——只記錄「這個開了 kcov 的執行緒」這次系統呼叫碰到的程式碼位置，不是整台機器的全域覆蓋率。這讓 syzkaller 能把覆蓋率精確地歸因到「這一個測試案例」，而不是被背景雜訊干擾。

syzkaller 除了記 PC（程式碼位置），也會用 `KCOV_TRACE_CMP` 模式記錄**比較運算元**——例如程式碼裡 `if (magic == 0x1234)` 這種比較，kcov 能把 `0x1234` 這個常數回報給 syzkaller，讓 fuzzer下次直接把輸入裡對應的欄位設成 `0x1234`，繞過「隨機數字永遠湊不中一個 magic number」的困境。這個機制在 AFL 系列的使用者態 fuzzer 裡也有對應物（常叫 laf-intel 或 cmplog），原理相通。

## syzlang：描述 syscall 長什麼樣

coverage feedback 解決了「往哪裡鑽」的問題，但「怎麼生出語法合法的輸入」要靠 syzlang——一套宣告式的 syscall 描述語言，檔案放在 syzkaller repo 的 `sys/linux/*.txt`。

```
# 示意：描述一個自訂 kernel module 的 ioctl 介面
# （對應 Ch 5 建的 vulnerable module，假設它叫 /dev/vulndev）

resource fd_vulndev[fd]

openat$vulndev(fd const[AT_FDCWD], file ptr[in, string["/dev/vulndev"]],
               flags flags[open_flags], mode const[0]) fd_vulndev

# ioctl 的第二個參數是 cmd，第三個參數型別依 cmd 而定
ioctl$VULN_SET_SIZE(fd fd_vulndev, cmd const[0x1000], arg ptr[in, int32])
ioctl$VULN_WRITE_AT(fd fd_vulndev, cmd const[0x1001], arg ptr[in, vuln_req])

vuln_req {
	idx	int32
	val	int64
}
```

這段描述告訴 syzkaller 三件事：

- `openat$vulndev` 回傳一個型別為 `fd_vulndev` 的 **resource**——resource 是 syzkaller 用來表達「這個值是另一個 syscall 的輸出，後面的 syscall 要用它當輸入」的依賴關係。沒有這個宣告，fuzzer 不知道 `ioctl` 的第一個參數要去哪裡找一個合法的 fd，只會亂填一個數字，99% 都是 `EBADF`。
- `const[0x1000]` 把 `cmd` 固定成一個常數——這避免 fuzzer 浪費時間亂猜 cmd 值，而是針對每個已知 cmd 單獨生一條描述，各自配對正確的 `arg` 型別。
- `ptr[in, vuln_req]` 描述這是一個指向 struct 的指標，`in` 表示這個方向是「user 寫、kernel 讀」（對應 `copy_from_user`）；如果是 kernel 寫回使用者,會用 `out`。

syzkaller 看到這些描述後,用 `make generate` 把它們編譯成 Go 程式碼，fuzzer 執行時就依照這些規則生成「語法上合法、依賴關係正確」的測試程式。

> 如果你對「resource/const/ptr」這套描述語言語法細節有疑問，去讀 syzkaller 官方的 `docs/syscall_descriptions_syntax.md`（連結在延伸閱讀）——這套語法本身有版本演進,本章只給你讀懂與寫出基本描述所需的核心概念,具體的進階型別（`len`、`bitfield`、`array` 的邊界語法）請以你 clone 的版本的文件為準。

## 底層機制：corpus、mutation 與整個系統怎麼串起來

```
┌──────────────┐     測試程式(program)      ┌──────────────┐
│ syz-manager  │ ─────────────────────────▶ │  syz-fuzzer   │  (跑在每個 VM 裡)
│ (host, 管理)  │ ◀───────────────────────── │              │
└──────┬───────┘   corpus 更新、crash 回報    └──────┬───────┘
       │                                            │ fork + exec
       │ 管理 VM pool                                ▼
       │ (QEMU/GCE/...)                      ┌──────────────┐
       ▼                                      │ syz-executor  │  (實際執行 syscall)
┌──────────────┐                              │  + kcov 開啟  │
│   corpus.db  │ ← 持久化：目前已知能帶出      └──────┬───────┘
│ （有價值的種子）  新 coverage 的測試程式           │ syscalls
└──────────────┘                                    ▼
                                              ┌──────────────┐
                                              │ 目標 kernel   │
                                              │ (KASAN 開啟)  │
                                              └──────────────┘
```

執行迴圈：

1. `syz-fuzzer` 從目前的 corpus 挑一個種子，做變異（mutation）——常見變異方式包括插入/刪除一個 syscall、把某個欄位的值換成邊界值（0、`-1`、`INT_MAX`）、把兩個程式拼接。
2. 丟給 `syz-executor` 在目標 kernel（通常跑在 QEMU 裡）執行,同時開著 kcov 收集這次執行覆蓋了哪些 PC。
3. 如果這次執行帶出**新的覆蓋率**（命中了之前整個 corpus 都沒碰過的程式碼位置），這個程式就被收進 corpus，成為下一輪變異的種子；沒有新覆蓋率就丟棄。
4. 若這次執行讓 kernel panic/觸發 KASAN report/卡死（hang），`syz-manager` 把 console 輸出與觸發的程式存下來，之後呈報到 syzbot。

**為什麼要在 QEMU 裡跑，而不是直接在 host 上跑？** 因為 fuzzer 本質上就是要讓 kernel crash——如果直接在裸機跑，一次 panic 就讓整台機器掛掉，fuzzer 停擺。QEMU 給你可以重開機、可以快照還原的隔離環境,一次 crash 只是重啟一次虛擬機,管理端自動偵測並重開新的 VM 繼續跑。這也是為什麼 Part 0（[Ch 0](./00-environment-setup.md)）花了一整章把 QEMU + initramfs 的環境建起來——同一套環境,fuzzing 和手動 exploit 開發是共用的。

## 看懂一份 syzbot report

syzbot 是 Google 用 syzkaller 持續對 Linux mainline（以及多個子版本分支）跑 fuzzing 的自動化服務，發現的 bug 會公開在 `syzkaller.appspot.com` 的 dashboard 上，並寄信通知對應的 mailing list。一份 report 一般包含這幾個部分：

```
標題：  [syzbot] [net?] KASAN: use-after-free Read in xxx_func

Hello,
syzbot found the following issue on:
HEAD commit:    <commit hash>          ← 觸發時的 mainline HEAD
git tree:       upstream
console output: <連結>                  ← 完整 boot + crash log
kernel config:  <連結>                  ← 這次測試用的 .config
C reproducer:   <連結>                  ← 獨立可編譯執行的 C 程式
syz reproducer: <連結>                  ← syzlang 格式的程式，需要 syz-execprog 跑

==================================================================
BUG: KASAN: use-after-free in xxx_func+0x1a2/0x3f0
Read of size 8 at addr ffff888... by task syz-executor/1234
...
Call Trace:
 xxx_func+0x1a2/0x3f0
 yyy_syscall_handler+0x...
...
Allocated by task 1234:
 ...
Freed by task 1234:
 ...
==================================================================
```

幾個部分的用途：

- **C reproducer**：最有價值的部分——一段不依賴 syzkaller 框架、能獨立編譯跑起來的 C 程式，直接呼叫觸發 bug 所需的 syscall 序列。syzbot 的目標是對每個報告都盡量生出 C reproducer（並非總能成功，有些 bug 依賴特殊時序，只有 syz 格式的 reproducer）。
- **syz reproducer**：syzkaller DSL 格式，需要搭配 `syz-execprog` 這個工具和對應架構的 `syz-executor` 一起跑，用在 C reproducer 跑不出來、需要更貼近 fuzzer 原始執行環境的情況。
- **Allocated by / Freed by**：KASAN 報告裡的兩段 stack trace,直接告訴你這個物件在哪裡被配置、在哪裡被釋放——對一個 UAF report，這兩段幾乎就是根因分析的一半（細節見 [Ch 44](./44-debug-toolchain.md)）。
- **bisection**：如果 report 下方有 "Bisection: introduced by" 或 "Bisection: fixed by" 的段落，代表 syzbot 自動跑了 `git bisect`（用 C reproducer 當判斷依據）幫你找出了引入或修復的 commit——直接把這個資訊接到 [Ch 42](./42-patch-to-rootcause.md) 的方法論上，省掉自己 bisect 的時間。

## 範例一：寫一份自己 target 的 syzlang 描述

延續 [Ch 5](./05-first-vulnerable-module.md) 建的 `/dev/vulndev` ioctl 靶模組，假設它的介面是：

```c
// 回顧 Ch 5 的 vulnerable module（示意）
#define VULN_SET_SIZE  0x1000
#define VULN_WRITE_AT  0x1001

struct vuln_req {
	int32_t idx;
	int64_t val;
};

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
	switch (cmd) {
	case VULN_SET_SIZE: { ... }
	case VULN_WRITE_AT: {
		struct vuln_req r;
		copy_from_user(&r, (void __user *)arg, sizeof(r));
		buf[r.idx] = r.val;    // BUG: r.idx 沒驗證上下界
		return 0;
	}
	}
}
```

對應的 syzlang 描述檔（存成 `sys/linux/vulndev.txt`，未實測，依 syzkaller `docs/syscall_descriptions.md` 的範例格式重建）：

```
include <linux/fcntl.h>

resource fd_vulndev[fd]

openat$vulndev(fd const[AT_FDCWD], file ptr[in, string["/dev/vulndev"]],
               flags flags[open_flags], mode const[0]) fd_vulndev

vuln_req {
	idx	int32
	val	int64
}

ioctl$VULN_SET_SIZE(fd fd_vulndev, cmd const[0x1000], arg ptr[in, int32])
ioctl$VULN_WRITE_AT(fd fd_vulndev, cmd const[0x1001], arg ptr[in, vuln_req])
```

寫完後（未實測，依官方文件流程重建；在你自己的 syzkaller checkout 裡驗證）：

```bash
make extract TARGETOS=linux SOURCEDIR=$KSRC   # 從 kernel header 擷取相關常數
make generate                                  # 把 .txt 描述編譯成 Go 程式碼
make                                            # 重新編譯 syz-fuzzer / syz-executor
```

跑起來之後，到 syzkaller 內建的 web dashboard（預設監聽在 manager config 裡設定的埠）看 coverage 頁面，確認 `vuln_ioctl` 函式真的被標記成「有被覆蓋到」——這是官方文件明確建議的驗證步驟：新描述寫完不保證語法對、路徑通，一定要靠 coverage 頁面確認 fuzzer 真的能摸到你想測的程式碼，而不是卡在更早的檢查就被拒絕。

## 範例二：架一個最小 syzkaller 設定

對準 Part 0 建好的 QEMU 環境，一份最小 `my.cfg`（syz-manager 的設定檔，未實測，依官方 `docs/linux/setup.md` 架構重建）：

```json
{
	"target": "linux/amd64",
	"http": "127.0.0.1:56741",
	"workdir": "/home/user/syzkaller/workdir",
	"kernel_obj": "/home/user/linux-6.6",
	"image": "/home/user/qemu-image/bullseye.img",
	"sshkey": "/home/user/qemu-image/bullseye.id_rsa",
	"syzkaller": "/home/user/syzkaller",
	"procs": 4,
	"type": "qemu",
	"vm": {
		"count": 4,
		"kernel": "/home/user/linux-6.6/arch/x86/boot/bzImage",
		"cmdline": "net.ifnames=0",
		"cpu": 2,
		"mem": 2048
	}
}
```

這份設定要求目標 kernel build 時至少開啟 `CONFIG_KCOV=y`、`CONFIG_KASAN=y`（讓 crash 能被精確捕捉而不是靜默損壞記憶體後很久才爆炸，詳見 [Ch 44](./44-debug-toolchain.md)）、`CONFIG_DEBUG_FS=y`（kcov 介面掛在 debugfs 底下）。啟動方式是 `./bin/syz-manager -config my.cfg`，manager 會自動透過 QEMU 開 `vm.count` 份虛擬機、每份跑一個 `syz-fuzzer` 實例、透過 SSH 把 fuzzer 和 executor 送進去執行。

## syzlang 常用型別速查

寫描述檔時最常用到的幾種型別,認得它們能讀懂絕大多數 `sys/linux/*.txt`：

| 型別 | 意義 | 範例 |
|---|---|---|
| `const[N]` | 固定常數,fuzzer 不會變異這個值 | `cmd const[0x1000]` |
| `flags[name]` | 從一組預先定義的旗標集合裡選（可能是互斥選一個，也可能是 bitmask 可組合） | `flags flags[open_flags]` |
| `ptr[dir, type]` | 指標，`dir` 是 `in`（user 寫入給 kernel 讀）、`out`（kernel 寫回給 user）或 `inout` | `arg ptr[in, vuln_req]` |
| `len[field]` | 這個整數欄位的值應該等於另一個欄位（通常是陣列）的長度，fuzzer 會自動算，不會亂填 | `size len[data]` |
| `array[type]` | 變長陣列 | `data array[int8]` |
| `string["literal"]` | 固定字串常量,常用在檔案路徑 | `file ptr[in, string["/dev/vulndev"]]` |
| `resource name[base]` | 宣告一個新的 resource 型別,用來表達 syscall 之間的依賴 | `resource fd_vulndev[fd]` |

`len` 型別值得特別注意——它是 syzkaller 避免「fuzzer 亂填長度欄位,結果 99% 都被 kernel 的基本長度檢查擋掉」的機制。沒有正確標注 `len` 關係的描述,fuzzer 會把這個欄位當一般整數隨機生成,大概率生出和實際陣列長度不符的值,浪費大量測試在觸發最外層的合法性檢查上。

## 把 reproducer 當成 exploit 的起點

syzbot 給你的 C reproducer **不是 exploit**，它只是「觸發 crash 的最短路徑」——這一點和 [Ch 10](./10-vulnerability-taxonomy.md) 的原語思維是同一件事：reproducer 證明了 bug 可達,但通常連「這是什麼原語」都沒告訴你，更不用說利用。從 reproducer 走到 exploit 的典型步驟：

1. 用 [Ch 44](./44-debug-toolchain.md) 的 KASAN/其他工具，確認 crash 的精確型態（UAF？OOB？哪個欄位？）。
2. 對照 [Ch 42](./42-patch-to-rootcause.md) 的讀法,如果這個 bug 已經有修復 commit（syzbot 通常會附上，或你自己去 `git log` 對應子系統找），逐行比對 reproducer 的操作序列和 diff 修的那個邏輯缺口是否吻合。
3. 精簡 reproducer——syzkaller 生出的程式常常帶一堆跟觸發無關的雜訊 syscall（因為它是變異出來的,不是人工設計的），手動刪減到最小觸發集合，方便後續開發。
4. 評估原語強度（回 [Ch 42](./42-patch-to-rootcause.md) 的可利用性檢查清單），決定要不要往下開發成完整 exploit。

## 對比與取捨

| 方法 | 找洞能力 | 資源需求 | 輸出 |
|---|---|---|---|
| syzkaller/syzbot（本章） | 能找全新、未知的 0-day；對「淺層」邏輯錯誤效果極好 | 需要持續跑（小時到週級）、多核心/多 VM | crash report + C/syz reproducer，**不含**根因分析與 exploit |
| 讀 patch（[Ch 42](./42-patch-to-rootcause.md)） | 只能找已經被修過的洞（N-day） | 低——讀懂一個 diff 幾分鐘到幾小時 | 根因分析 + 觸發路徑猜測，**不含**自動 reproducer |
| 手讀 source code | 能找到 fuzzer 覆蓋不到的邏輯設計缺陷（權限模型、狀態機） | 最高——需要對子系統有經驗 | 直接的 root cause，但沒有自動驗證 |
| symbolic execution / 其他靜態分析 | 能窮舉特定類別的路徑（如 null deref） | 工具鏈複雜，kernel 規模下 path explosion 嚴重 | 本書不展開，列在延伸閱讀 |

實務上 syzkaller 是找「新鮮度」最高的一條路，但它找到的東西需要後面三種方法論接手做 triage 與利用開發——這正是 Ch 42 到 Ch 45 整個 Part 7 的敘事順序。

## 踩雷集錦

1. **「coverage 到了 100% 就代表找完 bug 了」**。coverage 衡量的是「程式碼有沒有被執行到」，不是「這條路徑上所有可能的輸入組合都被測過」。一段程式碼可能被執行一百萬次,但某個只在特定數值組合下才出現的邊界錯誤,從沒被踩中過。coverage 高只代表 fuzzer 探索得廣，不代表邏輯錯誤被找完。
2. **「C reproducer 一定能穩定重現」**。race condition 類的 bug（[Ch 40](./40-race-conditions.md)）對時序高度敏感,reproducer 在 syzbot 的 VM 環境下能重現,搬到你自己的硬體（CPU 核心數、排程行為不同）未必能一次就中,可能需要重跑幾十到幾千次,或搭配 [Ch 41](./41-widening-race-window.md) 的技巧拉寬窗口。
3. **「reproducer 就等於 exploit」**。reproducer 證明的是「可以讓 kernel 崩潰或讓 KASAN 報告一次違規存取」,這離「能控制 RIP 或竄改 cred」還有很長的距離——很多 reproducer 對應的原語其實很弱（例如一次性、唯讀、位移不可控），回 [Ch 10](./10-vulnerability-taxonomy.md) 的四軸評估完才知道值不值得往下做。
4. **「沒有自動 reproducer 就不算是真正的 bug」**。syzbot 有一類 report 叫 "no repro"——crash 真實發生過（有 console log 證明），但自動化重現腳本沒能復現。這類 bug 不是假警報，只是比較難自動化,通常需要人工介入分析（往往就是最有價值、最少人碰過的那些）。

## 進階：規模化與貢獻回去

- **私有 corpus 與種子匯入**：如果你手上已經有一批已知能觸發有趣行為的測試案例（例如從 Ch 42 反推出的觸發序列轉寫成 syzlang program），可以直接塞進 `corpus.db` 當初始種子，讓 fuzzer 從一個「已經懂一些東西」的起點開始變異，而不是從零探索。
- **針對單一子系統窄化 fuzzing**：預設 syzkaller 描述覆蓋全部 syscall，對一個特定子系統（例如你只關心 io_uring）做深度 fuzzing 時，可以用 `enable_syscalls`/`disable_syscalls` 設定收窄範圍，讓所有運算資源集中在你關心的介面上，提高碰撞到深層邏輯錢的機率。
- **syz-hub 與分散式 fuzzing**：大規模運作（如 Google 內部的 ClusterFuzz 整合）會用多台機器各自跑 manager，透過 syz-hub 同步彼此的 corpus，讓覆蓋率探索不被單機資源限制。
- **把發現回報上游**：如果你在自己的 target 上用這套方法找到了真實、可重現的洞，照 kernel 的[安全漏洞回報流程](https://www.kernel.org/doc/html/latest/process/security-bugs.html)回報，這是整個生態系統得以持續修補的方式——也是本書在 README 強調「藍隊也需要這本書」的具體落地。

## 動手練習

1. 對照 [Ch 5](./05-first-vulnerable-module.md) 的 vulnerable module，照本章範例一的格式，自己寫一份 syzlang 描述檔，涵蓋你模組裡至少兩個不同的 ioctl cmd。
2. （若你有可長時間跑的環境）架好 Ch 0 的 QEMU + 本章範例二的 manager config，跑起來後到 web dashboard 觀察 coverage 頁面,確認你寫的 ioctl 描述真的命中了你的模組程式碼。
3. 到 [syzkaller dashboard](https://syzkaller.appspot.com/upstream) 挑一份狀態為 "open" 的 report，只讀 C reproducer 和 call trace，不看任何人的分析，自己試著回答:這是 Ch 10 哪一類根因？你會怎麼開始往下 triage？
4. 找一份帶 "Bisection: introduced by" 的 report，對照它指出的 commit，練習 Ch 42 的讀法重建根因，驗證跟 syzbot 自動 bisect 的結果吻合。

## 本章重點整理

- Syzkaller 靠兩個機制解決「kernel fuzzing 很難撞中深層邏輯」的問題：syzlang 的結構化生成（避免卡在輸入驗證）+ kcov 的 coverage feedback（引導往未探索的程式碼鑽）。
- kcov 是 per-task 的編譯期插樁機制，syzkaller 靠它判斷一個測試案例有沒有帶來新覆蓋率,決定留或丟。
- syzbot report 的核心資產是 C/syz reproducer、KASAN 的 alloc/free stack、以及（若有）自動 bisection 結果——後者直接接上 Ch 42 的方法論。
- reproducer 只是觸發路徑的證明，不是 exploit；從 reproducer 到 exploit 中間要走 Ch 42（根因分析）與 Ch 44（精確定位）的工序。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼純隨機測試在 kernel syscall 上效果差，coverage-guided fuzzing 解決了哪個具體問題
- [ ] 能說出 kcov 是怎麼知道「這次執行有沒有新覆蓋率」的（per-task、基本區塊插樁）
- [ ] 給一個簡單的 ioctl 介面，能照 syzlang 語法寫出對應的 resource/const/ptr 描述
- [ ] 能列出一份 syzbot report 裡至少四個關鍵部分,並說出各自的用途
- [ ] 能解釋為什麼「reproducer」和「exploit」是兩個不同強度的概念,中間缺了哪些工序

## 延伸閱讀

### 官方文件

- **[syzkaller GitHub repo 的 docs/ 目錄](https://github.com/google/syzkaller/tree/master/docs)**
  - **讀哪裡**：`syscall_descriptions.md`（整體介紹）→ `syscall_descriptions_syntax.md`（語法細節）→ `linux/setup.md`（架設指南）,三份依序讀。
  - **和本章的關聯**：本章的 syzlang 範例與 manager config 都是依這套文件的架構重建；實際跑之前務必對照你 clone 版本的最新文件,語法細節偶有演進。
- **[syzbot 使用說明（docs/syzbot.md）](https://github.com/google/syzkaller/blob/master/docs/syzbot.md)**
  - **讀哪裡**：整篇，特別是 reproducer 格式說明與 `#syz` 指令（`#syz fix`、`#syz dup`、`#syz test`）的用法。
  - **和本章的關聯**：補完本章「看懂 report」一節沒展開的互動機制——如何回覆 syzbot 來確認修復或標記重複。

### 部落格 / 原始設計

- **[Dmitry Vyukov 關於 syzkaller 設計的公開演講與文件](https://github.com/google/syzkaller/blob/master/docs/internals.md)**
  - **這篇說什麼**：syzkaller 作者本人對內部架構（manager/fuzzer/executor 分離、corpus 管理策略）的說明。
  - **讀哪裡**：整篇 `internals.md`，重點在 "Fuzzing" 與 "Code Coverage" 兩節。
  - **為什麼值得讀**：直接來自工具作者，避免二手轉述失真；補完本章「底層機制」一節的細節。

### 論文

- **[Fuzzing: Art, Science, and Engineering](https://arxiv.org/abs/1812.00140)** — Valentin Manès 等（2019 survey）
  - **核心貢獻**：系統性整理 fuzzing 技術的分類（黑盒/灰盒/白盒,coverage-guided 屬於灰盒），把 syzkaller 這類工具放進整個 fuzzing 研究脈絡裡定位。
  - **讀哪裡**：Section 3（Coverage-based fuzzing）對應本章 kcov 機制的學術化描述；其他章節可選讀。
  - **和本章的關聯**：本章講的是 syzkaller 這一個具體實作；這篇論文告訴你這套設計在更大的 fuzzing 技術地圖裡站在哪個位置。

有了 syzkaller 這套找新洞的武器，下一章回頭補上另一半——當你手上已經有一個 crash（不管是 fuzzer 生的還是自己手動 trigger 的），該用哪個工具才能最快把它定位到精確的根因。

→ [Ch 44 — debug 工具鏈：KASAN / KCSAN / KMSAN / UBSAN / lockdep / ftrace / kprobe](./44-debug-toolchain.md)
