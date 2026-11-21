# Ch 16 — 經典利用原語：modprobe_path / core_pattern / poweroff_cmd / cred，任意寫轉提權

> **目標**：把「提權的最後一步」從「只會 `commit_creds`」擴充成一整套工具箱。學完你要能在**只有任意寫、沒有乾淨 RIP 控制**時照樣拿 root——改 `cred`、改 `modprobe_path`、改 `core_pattern`、改 `poweroff_cmd`。這些是 data-only 提權的入門款，也解釋了為什麼現代 exploit 越來越不碰控制流劫持。讀完接練習 B，把 Ch 11–16 串成全 mitigation 下的完整鏈。

> **環境**：Linux 6.6 LTS、x86-64、QEMU，全 mitigation 開。本章多數原語**不需要** leak kernel text（有些甚至不需要 KASLR leak），這點稍後會強調。exploit code 未在本環境實測，標為理論預期行為。

## 為什麼不要只會 commit_creds

[Ch 11–15](./11-stack-overflow.md) 的提權都走「劫 RIP → 呼叫 `commit_creds(prepare_kernel_cred(NULL))` → 回 user」。這條路有兩個越來越痛的前提：

1. 你得有**乾淨的 RIP 控制**——能排 ROP、能正確回程。很多 heap 類 bug（Part 3）給你的是「任意寫一塊記憶體」而**不是**乾淨 RIP。
2. 你得過 **CFI/KCFI**（[Ch 28](./28-cfi-kcfi.md)）——2024 之後間接呼叫會被檢查型別，劫 RIP 去 call `commit_creds` 這種「呼叫目標不合法」的動作會被擋。

這一章的原語全都**繞開控制流**：你只要能把某個值寫到某個 kernel 位址（write-what-where，[Ch 10](./10-vulnerability-taxonomy.md) 的頂級原語），就能提權，不碰 RIP、不過 CFI。這就是「data-only attack」的雛形——Part 4（Ch 29）會把它推到極致，本章先教最經典的四個著力點。

## 先建立直覺：把「寫記憶體」變成「拿 root」

```
  你有的：write-what-where（能把值 V 寫到位址 A）
                    │
        ┌───────────┼───────────┬──────────────┐
        ▼           ▼           ▼              ▼
   改 cred      改 modprobe   改 core_       改 poweroff
   的 uid=0     _path 字串    pattern 字串    _cmd 字串
        │           │           │              │
   當前 process  觸發載入未知   觸發 coredump   觸發關機流程
   直接變 root   模組 → 跑我的  → 跑我的程式     → 跑我的程式
                 程式(root)     (root)          (root)
```

共通模式：kernel 裡有些**全域變數**，內容是「出事時 kernel 會拿去當程式路徑執行」的字串，而且執行時是 **root 權限**。把它改成指向你的腳本，再故意觸發對應事件，kernel 就替你以 root 跑你的東西。這類全域變數是 data-only 提權的金礦。

## 原語一：直接改 cred

最直接。回憶 [Ch 12](./12-control-flow-hijack.md)：提權在資料層面只是「`current->cred` 指向的那份 `struct cred` 的 uid/gid 變 0」。所以有任意寫時，不必呼叫 `commit_creds`，直接找到那份 cred、把它的 uid 欄位寫 0 即可。

難點是**找到 cred 的位址**。幾條路：

- 從 `current`（當前 task_struct）出發：`task_struct` 裡有 `cred`/`real_cred` 指標。若你能 leak `current` 或 task_struct 的位址（[Ch 14](./14-kaslr-infoleak.md) 途徑四），讀出 cred 指標，再寫它的 uid。
- `struct cred` 自己是 `kmalloc` 出來的（在 `cred_jar` 這個專用 cache）。有些攻擊 spray 大量 cred、用 heap 原語直接改中你自己 process 的那份。
- cred 開頭的欄位佈局（`usage` refcount、`uid`、`gid`、`suid`…）是已知的，找到起點後 offset 固定。

```c
/* 概念：有任意寫 write64(addr, val)，且已知自己 cred 的位址 cred_addr */
write64(cred_addr + OFF_UID,  0);   /* uid  = 0 */
write64(cred_addr + OFF_GID?, 0);   /* 視佈局，uid/gid/euid... 一起清 */
/* 回到 user-space 後，當前 process 已是 root（cred 當場生效，不需回程劫持） */
```

> cred 法的好處：**不需要 leak kernel text base**——你需要的是 cred/task_struct 的位址（heap/percpu 類），不是函式位址。對「能任意寫但難 leak text」的題特別好用。`struct cred` 的欄位 offset 隨版本變，以你的 kernel source 為準。

## 原語二：modprobe_path（最受歡迎的一個）

`modprobe_path` 是一個 kernel 全域字串（預設 `"/sbin/modprobe"`），指向「當 kernel 需要載入一個模組時，要執行哪個 user-space 程式」。關鍵機制：當系統嘗試執行一個**未知格式的檔案**（或請求一個沒載入的模組），kernel 會以 **root** 執行 `modprobe_path` 指到的程式。

攻擊三步：

```
1. 改字串：write "/tmp/x" 到 modprobe_path（覆蓋 "/sbin/modprobe"）
2. 準備兩個檔案：
   /tmp/x       → 一個腳本，內容把 /bin/sh 設 suid 或複製 flag，chmod +x
   /tmp/trigger → 一個「未知格式」的可執行檔（例如開頭亂碼的 ELF/隨便 bytes），chmod +x
3. execve("/tmp/trigger")：格式不認得 → kernel 以 root 跑 modprobe_path = /tmp/x
```

```c
/* /tmp/x 內容範例（被 kernel 以 root 執行） */
#!/bin/sh
chmod u+s /bin/bash        # 或 cp /flag /tmp/flag; chmod 777 /tmp/flag
```

```c
/* /tmp/trigger：故意是「無法辨識格式」的檔，觸發 modprobe */
char trigger[] = "\xff\xff\xff\xff";   /* 不是合法 ELF/script，kernel 會找 handler */
/* 寫成檔、chmod +x、execve 它 */
```

`modprobe_path` 法的魅力：

- **不需要任何 leak**——`modprobe_path` 在 kernel `.data`，沒開 KASLR 時位址固定；開 KASLR 時你只需要一個**相對 kernel base 的 offset** 加上你已有的 text base leak（或有些 heap 原語能直接相對定位）。比起要 leak 一串東西的 ROP 路，門檻低很多。
- **不碰控制流**，CFI 完全無關。
- 寫的值是一個短字串，`write-what-where` 一兩次就夠。

> 現代防禦：`modprobe_path` 太好用，於是有 `CONFIG_STATIC_USERMODEHELPER`（把 usermode helper 路徑鎖死、不可寫）這類 hardening。開了它 modprobe_path 法就失效。預設多半沒開，但 kernelCTF 的 mitigation 賽道可能開。遇到失效先查這個 config。

## 原語三：core_pattern

`core_pattern`（`/proc/sys/kernel/core_pattern`，對應 kernel 全域）控制「process crash 產生 coredump 時怎麼處理」。若它以 `|` 開頭，後面的程式會被 kernel 以 **root** 執行、crash 的 core 從 stdin 餵進去。

```
1. 改 core_pattern 為 "|/tmp/x"（覆蓋原值）
2. /tmp/x 是你的 root 腳本
3. 故意讓一個 process crash（例如 abort()）→ kernel 以 root 跑 /tmp/x
```

和 modprobe_path 同類（改字串 → 觸發 → root 執行你的程式），差別只在觸發方式（crash 一個 process vs execve 未知格式）。某些環境 `modprobe_path` 失效時 `core_pattern` 還能用，反之亦然，所以兩個都要會。注意 `core_pattern` 字串較長，要確認你的 write 原語能寫夠長度。

## 原語四：poweroff_cmd

`poweroff_cmd` 是 kernel 全域字串（預設 `"/sbin/poweroff"`），在 `orderly_poweroff()` 流程裡會被以 root 執行。改掉它、再透過某個能觸發 orderly poweroff 的路徑引爆，一樣得到 root 執行。比起 modprobe/core_pattern，它的觸發路徑較少見、較不通用，通常是前兩個都不行時的備胎。知道它存在、模式相同即可。

## 四個原語怎麼選

| 原語 | 需要 leak text？ | 觸發方式 | 常見失效原因 |
|---|---|---|---|
| 改 cred | 否（需 cred/task 位址） | 無，寫完即生效 | 難拿到 cred 位址 |
| modprobe_path | 需 base（算 offset） | execve 未知格式檔 | `STATIC_USERMODEHELPER` |
| core_pattern | 需 base | 觸發 coredump（crash） | coredump 被禁/限制 |
| poweroff_cmd | 需 base | 觸發 orderly_poweroff | 觸發路徑不易構造 |

決策順序大致是：**能拿到 cred 位址就改 cred（最乾淨、不依賴 config）；否則用 modprobe_path（最通用）；它被 config 擋就換 core_pattern；都不行才想 poweroff_cmd 或回去走控制流路線。**

## 踩雷集錦

1. **以為 data-only 就完全不用 leak**——多數情況你仍需要 kernel base 才能算出 `modprobe_path`/`core_pattern` 的執行期位址（它們在 `.data`，隨 text 一起平移）。只有「改 cred」在你能拿到 cred/task 位址時可以不碰 text base。別把「不劫 RIP」誤會成「不用任何 leak」。
2. **modprobe_path 的 trigger 檔寫成合法 ELF**——要觸發 modprobe，trigger 必須是 kernel **認不得格式**的可執行檔（亂碼開頭、chmod +x）。寫成正常 ELF/script 它直接跑起來、不會呼叫 modprobe，原語不觸發。
3. **忘了 trigger / 腳本的權限**——`/tmp/x`（被 root 執行的腳本）和 trigger 檔都要 `chmod +x`；腳本要有正確 shebang。少 `+x` 或少 shebang，kernel 執行失敗，你以為寫錯位址其實是檔案權限。
4. **core_pattern 改了沒確認長度/是否被截斷**——`core_pattern` 字串比 modprobe_path 長，若你的 write 原語一次只能寫 8 bytes，要寫多次且確保中間沒被其他東西改回；沒寫完整個 pipe 命令會壞。
5. **全域變數 offset 照抄別的 build**——`modprobe_path`/`core_pattern`/`poweroff_cmd`/cred 欄位的 offset 都綁 vmlinux build。用 `readelf -s vmlinux | grep modprobe_path`（或 `/proc/kallsyms`）對自己的 build 取 offset，別抄。

## 進階：為什麼這章是 data-only 的起點

把本章四個原語的共通點抽出來：**它們都把「寫一塊資料」直接變成提權，中間不經過劫持控制流。** 這正是 data-only attack 的定義。它們之所以在 2024 之後越來越重要，是因為控制流路線被 CFI/KCFI 越掐越緊——間接呼叫要對型別、ROP gadget 的落點受限。而 data-only 攻擊 kernel 根本分不出「這個寫是合法的還是攻擊」，因為它寫的是合法可寫的全域變數。

本章是入門款（著力點是幾個固定的全域變數）。Part 3 的 Dirty Cred（[Ch 24](./24-dirty-cred.md)）、Part 4 的 data-only 技術集（[Ch 29](./29-data-only-attack.md)）會把這個思路推到：連「固定全域變數」都不需要，直接用 heap 原語把一個低權限物件換成高權限物件、或改 page table（[Ch 23](./23-dirty-pagetable.md)）拿任意實體讀寫。方向一致：**越少碰控制流，越安靜、越能繞現代 mitigation。**

一個實戰建議：準備 exploit 時，先問「這題我有乾淨 RIP 嗎？」。有 → 控制流路線（Ch 11–15）可用，但仍優先考慮 data-only 收尾以避 CFI。只有任意寫 → 直接從本章的工具箱挑。把這兩套都練熟，遇到任何原語都有收尾的路。

## 動手練習

在自有 QEMU 靶上，用一個能提供 write-what-where 的靶（可把 `vulndevice.c` 改成「user 給 addr+val，kernel 直接 `*(unsigned long*)addr = val`」的教學版）：

1. 實作 `modprobe_path` 法：leak base 算出 `modprobe_path` 位址、寫成 `/tmp/x`、備好 trigger 與腳本、execve trigger，拿 root。
2. 換成 `core_pattern` 法重做一次，比較兩者觸發方式。
3. 進階：leak 出自己的 `task_struct`/cred 位址，直接改 cred 的 uid=0，體會「不需要 text base 也能提權」。
4. 把 `CONFIG_STATIC_USERMODEHELPER` 打開重編 kernel，觀察 modprobe_path 法怎麼失效、而改 cred 法仍然有效——理解 config 對原語選擇的影響。

## 本章重點整理

- 只有任意寫、沒有乾淨 RIP 時，用 data-only 原語照樣提權，且繞過 CFI。
- 四個經典著力點：改 cred（uid=0，不需 text base）、modprobe_path、core_pattern、poweroff_cmd（後三者「改字串→觸發→root 執行你的程式」）。
- modprobe_path 最通用但可被 `STATIC_USERMODEHELPER` 擋；cred 法最乾淨但需 cred/task 位址。
- 共通模式是把「寫資料」變成「拿 root」，這是 data-only attack 的雛形，呼應 Ch 23/24/29。
- offset/佈局綁 vmlinux build，必須對自己的 build 取值。

## 自我檢核

- [ ] 能說出為什麼「只有任意寫、沒有 RIP 控制」時本章原語比 Ch 11–15 的控制流路線更適用
- [ ] 能解釋 modprobe_path 法的三步，以及 trigger 檔為什麼必須是「未知格式」
- [ ] 能說出哪個原語不需要 leak kernel text base，為什麼
- [ ] 知道 `CONFIG_STATIC_USERMODEHELPER` 擋掉哪些原語、擋不掉哪個
- [ ] 能解釋為什麼 data-only 原語天生繞過 CFI/KCFI

## 延伸閱讀

- **[modprobe_path 提權技術（pawnyable / 各 CTF writeup）](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：手把手示範 modprobe_path 覆寫與觸發，含 trigger 檔構造。
  - **讀哪裡**：modprobe_path 相關小節；對照本章原語二。
  - **為什麼值得讀**：把「改字串 → 觸發 → root 執行」落到可直接照做的 code 與檔案。
- **[call_usermodehelper / modprobe_path 原始碼（kernel/kmod.c、kernel/umh.c）](https://elixir.bootlin.com/linux/v6.6/source/kernel/umh.c)**
  - **讀哪裡**：`call_usermodehelper` 與 modprobe 觸發路徑；看它何時、以什麼權限執行那個字串。
  - **和本章的關聯**：理解「為什麼是 root 執行」「哪些事件會觸發」，而不是死背步驟。
  - **前提知識**：Ch 3 的 kernel C 執行環境。
- **[struct cred 與 core_pattern 處理（kernel/cred.c、fs/coredump.c）](https://elixir.bootlin.com/linux/v6.6/source/fs/coredump.c)**
  - **讀哪裡**：`coredump.c` 裡對 `core_pattern` 以 `|` 開頭的處理；`cred.c` 的 cred 佈局。
  - **和本章的關聯**：原語一與原語三的機制來源；也幫你確認自己 kernel 版本的欄位 offset。

你現在有了控制流與 data-only 兩套收尾。接下來的練習 B 會把 Ch 11–16 全部在「KASLR + SMEP + SMAP + KPTI + canary 全開」下串成一條完整、可重複的 exploit——這是你第一次打一題「什麼都沒關」的 kernel pwn。

→ [練習 B — 從 stack overflow 到 root shell（全 mitigation 開）](./practice-b-stack-to-root.md)
