# Ch 47 — 從 crash 到 CVE

> **目標：** 掌握把 fuzzer 找到的 crash 轉化成 CVE 的完整工作流：crash reproduce 與 minimize、root cause 分析（ASan + gdb）、可利用性判斷、responsible disclosure 報告撰寫、CVE 申請流程、embargo 機制與 bug bounty。本章是整門課的收尾，也是「fuzzing 作為 CVE hunting 工具」的最後一哩路。

## 為什麼 crash 和 CVE 之間有距離

fuzzer 找到 crash 很容易，但 crash 距離 CVE 還有幾個關卡：

1. **reproduce**：fuzzer 的 crash input 在你的環境能穩定重現嗎？
2. **minimize**：能把 input 縮到最小嗎？（方便報告，也方便分析）
3. **root cause**：bug 的根本原因是什麼？哪一行？哪種 bug 類型？
4. **severity**：這個 bug 在實際攻擊中有多危險？是 DoS、資訊洩露，還是 RCE？
5. **disclosure**：怎麼告訴廠商？格式要怎麼寫？embargo 要談多久？
6. **CVE**：CVE 怎麼申請？誰來核發？

每一步都需要判斷力，不是技術問題就是溝通問題。

## 先建立直覺：triage 流水線

```
fuzzer output
  ├── crash-abc123      ← reproducer input
  ├── crash-def456
  └── crash-ghi789

               │
               ▼ Step 1: 去重（是否同一個 bug？）

  unique crashes（stack trace dedup）
  ├── UNIQUE: heap-buffer-overflow in parser_read (3 crashes)
  └── UNIQUE: use-after-free in session_free (1 crash)

               │
               ▼ Step 2: reproduce + minimize

  for each unique crash:
  ├── 能穩定重現？（排除 race condition / ASLR 幸運）
  └── minimize input（testcase-minimizer / AFL's tmin / libfuzzer -minimize_crash）

               │
               ▼ Step 3: root cause analysis

  ASan report → 哪種 bug type？
  gdb → 哪一行？呼叫棧？控制流？
  審 source → 為什麼？

               │
               ▼ Step 4: severity assessment

  可利用性分析
  CVSS score 估計

               │
               ▼ Step 5: report + disclosure

  寫 bug report（reproducer + root cause + impact）
  聯絡廠商 → embargo 談判 → fix 驗證 → public disclosure
  申請 CVE
```

## Step 1：reproduce 與 crash minimize

### reproduce

```bash
# 用 ASan binary 直接跑 reproducer
./fuzz_target_asan crash-abc123

# 預期輸出：
# ==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
# ...

# 如果不能穩定重現：
# 1. 確認 binary 是同一個版本（commit hash）
# 2. 關閉 ASLR
echo 0 | sudo tee /proc/sys/kernel/randomize_va_space

# 3. 固定 CPU frequency（避免 timing-sensitive race）
sudo cpupower frequency-set -g performance
```

### crash minimize（libFuzzer）

```bash
# libFuzzer 內建 minimize
./fuzz_target_asan -minimize_crash=1 \
    -max_total_time=60 \
    -exact_artifact_path=minimized_crash \
    crash-abc123

# 輸出：
# CRASH_MIN: minimizing crash input: 4096 bytes
# CRASH_MIN: successfully minimized crash to 128 bytes
# artifact_prefix='./'; Test unit written to minimized_crash
```

### crash minimize（afl-tmin）

**本段未實測，為理論預期行為**（環境無 AFL++）。驗證步驟：

```bash
# afl-tmin：對 AFL++ instrumented binary 做 delta-debugging 式 minimize
afl-tmin \
    -i crash-abc123 \       # 輸入 crash input
    -o minimized_crash \    # 輸出 minimized input
    -- ./target_afl @@       # 目標 binary

# 預期輸出：
# afl-tmin 2.68c by <lcamtuf@google.com>
# [*] Stage #0: Removing blocks of data...
# [*] Stage #1: Cloning bytes...
# [*] Stage #2: Bit flip (4-bit)...
# [+] Block minimization done, 4096 -> 23 bytes.
```

minimize 的目的：
- 讓 root cause 分析更容易（少資料 → 少干擾）
- 報告給廠商時更清楚（reproducer 是 `printf('\x41' * 23)` 而不是一個 4096 bytes 的 binary blob）
- 幫助 bisect（小 input 更容易在舊版本 reproduce）

## Step 2：ASan 報告解讀

一個典型的 heap-buffer-overflow ASan 報告結構：

```
==12345==ERROR: AddressSanitizer: heap-buffer-overflow
READ of size 4 at 0x602000000050 thread T0
    #0 0x555555593f21 in parse_int myproject/parser.c:142:5
    #1 0x555555591c40 in parse_value myproject/parser.c:287:9
    #2 0x555555590b88 in parse_document myproject/parser.c:451:12
    #3 0x555555590000 in fuzz_parse fuzz/fuzz_parse.cc:18:5

0x602000000050 is located 0 bytes to the right of 4-byte region
  [0x602000000048, 0x60200000004c)
allocated by thread T0 here:
    #0 0x7ffff7a1c234 in malloc ...
    #1 0x555555593abc in alloc_token myproject/parser.c:89:12
    #2 0x555555591c40 in parse_value myproject/parser.c:275:9

Shadow bytes around the buggy address:
  0x0c047fff7fb0: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
  0x0c047fff7fc0: fa fa 00 00 fa fa 04 fa fa fa fa fa fa fa fa fa
                                         ^^
                                         fa = heap redzone（越界了）
```

解讀方法：

| 欄位 | 意義 |
|------|------|
| `READ of size 4` | 讀取 4 bytes，如果是 WRITE 則更危險 |
| `0 bytes to the right of 4-byte region` | 正好越過邊界 1 個元素，off-by-one |
| `allocated by ... alloc_token` | 越界的 buffer 是 `alloc_token` 分配的 |
| stack frame `parse_int:142` | 真正觸發越界的那一行 |

常見 bug type 及其初步 severity 判斷：

| ASan 報告類型 | 初步判斷 |
|--------------|---------|
| heap-buffer-overflow WRITE | 高（可能 overwrite metadata） |
| heap-buffer-overflow READ | 中（資訊洩露；偶爾可升級） |
| stack-buffer-overflow | 視 stack canary 狀態 |
| use-after-free WRITE | 高（UAF + 寫） |
| use-after-free READ | 中高（可能洩露 heap pointer） |
| double-free | 中高（heap metadata corruption） |
| null-dereference | 低（通常是 DoS） |
| global-buffer-overflow | 視 overflow 方向 |

## Step 3：gdb root cause 分析

ASan 給了你 bug 的位置，gdb 幫你理解「為什麼」：

```bash
# 用非 ASan binary 跑（ASan binary 有特殊記憶體佈局，gdb 比較難讀）
# 但先確認 crash 能在非 ASan 版本重現
./fuzz_target_debug minimized_crash

# 進 gdb
gdb ./fuzz_target_debug

(gdb) run minimized_crash
# 等到 SIGSEGV 或 crash

# 看 stack
(gdb) bt
# #0  0x0000555555593f21 in parse_int (buf=0x6020000000a8, len=3) at parser.c:142
# #1  0x0000555555591c40 in parse_value (ctx=0x...) at parser.c:287

# 看問題那一行的局部變數
(gdb) frame 0
(gdb) info locals
# buf    = 0x6020000000a8 "abc"
# len    = 3
# index  = 4   ← 這裡！index 已經超過 len 了

# 看問題位置的 source
(gdb) list parser.c:140,145
# 140:  int val = 0;
# 141:  for (int i = 0; i < len; i++) {
# 142:      val = val * 10 + (buf[i] - '0');
# 143:  }
# 144:  // 問題：len 是 caller 傳的，但 buf 只有 3 bytes
# 145:  return buf[len];  ← off-by-one：index == len 已越界
```

root cause 分析需要回答：

1. **Bug type**：off-by-one / type confusion / integer overflow / missing NULL check...
2. **觸發路徑**：哪個 code path 才能走到這行？需要什麼條件？
3. **Controllability**：fuzzer input 能控制哪些 offset/size/content？這決定可利用性。

## Step 4：可利用性判斷

這是整個流程裡最需要判斷力的環節。以下是一個粗略的評估框架（不是每個 bug 都要做完整的 exploit PoC，但要能估計出 ceiling）：

```
可利用性評估 checklist：

堆 buffer overflow（最常見）：
  □ overflow 的方向（讀 or 寫）
  □ overflow 的大小（能控制多少 bytes？）
  □ overflow 的內容（能控制寫入的值嗎？）
  □ 相鄰 chunk 是什麼？（metadata、function pointer、其他 object？）
  □ 是否有 ASLR / PIE？（位址隨機化，但可能洩露）
  □ 是否有 heap metadata corruption？

Use-after-free：
  □ 釋放後多久被重用？（同步 vs 非同步）
  □ 同一個 allocation size 的哪些物件可以被 heap feng shui 放進去？
  □ free 後的物件有 function pointer / vtable 嗎？

對應 severity（粗略）：
  └── 能控制 PC（instruction pointer）→ Critical（RCE）
  └── 能 arbitrary write → High（可升級為 RCE）
  └── 能 arbitrary read → Medium-High（資訊洩露，可能輔助 RCE）
  └── DoS only → Low-Medium
```

**Project Zero 的實際案例：CVE-2020-1971（OpenSSL DSA_do_verify UAF）**

Project Zero 的 Tavis Ormandy 在 2020 年用 fuzzing 找到 OpenSSL 的一個 use-after-free。分析過程：

1. fuzzer crash：ASan 報告 heap-use-after-free in `DSA_do_verify`
2. minimize：reproducer 縮到幾十 bytes 的 malformed DSA signature
3. root cause：`DSA_do_verify` 在某個錯誤路徑上 free 了 `r` 和 `s`，但在函式返回後 caller 還會用它們
4. 可利用性：UAF 在 OpenSSL 內部對象上，但這個 code path 只在特定 verify 失敗時觸發，可利用性有限（評為 DoS 等級）；但因為 OpenSSL 的影響範圍，仍是 High severity

這個案例說明：就算可利用性評為 Low-Medium，**影響範圍** 也可以讓它成為 Critical-priority fix。

## Step 5：撰寫 Responsible Disclosure 報告

### 找到聯絡管道

優先順序：
1. `security@<project>.org` 或 `<project>-security@googlegroups.com`（正式安全聯絡）
2. 專案 SECURITY.md 裡列的聯絡方式
3. GitHub Security Advisories（private 報告功能）
4. 個人聯絡（maintainer 的 public email）

不要用：公開 issue tracker（除非廠商沒有 private channel）、社群 Discord/Slack。

### 報告格式

```
Subject: [SECURITY] heap-buffer-overflow in parse_value() — CVE candidate

Summary:
A heap-buffer-overflow vulnerability was found in libfoo version X.Y.Z
using coverage-guided fuzzing. A crafted input can trigger an out-of-bounds
read in parse_value() at parser.c:142, leading to information disclosure
or potentially arbitrary code execution.

Affected versions:
libfoo <= 2.3.1 (tested: 2.3.0, 2.3.1)
Fixed in: not yet

Reproducer:
Environment: Ubuntu 22.04, clang 14, ASan enabled
Build:
  git clone https://github.com/example/libfoo.git
  cd libfoo && git checkout abc1234
  clang -fsanitize=address,fuzzer fuzz/fuzz_parse.cc -o fuzz_parse ./libfoo.a

Reproduce:
  printf '\x41\x42\x00\xff\x03' > minimized_crash
  ./fuzz_parse minimized_crash

Output:
  ==12345==ERROR: AddressSanitizer: heap-buffer-overflow
  READ of size 4 at 0x6020... thread T0
  [完整 ASan 輸出貼在這]

Root cause:
In parser.c:142, the loop bound `len` is controlled by user input
without validation against the allocated buffer size. When a crafted
input provides len=N but the buffer is only N-1 bytes, the final
array access buf[N] reads one byte past the allocation.

Impact assessment:
This is a READ overflow. Under normal (non-ASan) conditions, this
reads adjacent heap memory, potentially leaking sensitive data.
Exploitation for code execution would require additional primitives
(e.g., information leak to defeat ASLR). CVSS v3.1 base score
estimate: 7.5 (AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N).

Suggested fix:
Add bounds check before the loop:
  if (len > allocated_size) return ERROR_INVALID_INPUT;

Timeline:
2026-08-01: Discovered during fuzzing campaign
2026-08-01: Sending this report
Proposed embargo: 90 days (by 2026-10-30)

Researcher:
[Your name / handle]
[Contact email]
[Optional: GPG key fingerprint for encrypted communication]
```

## Step 6：embargo 與 disclosure 時間線

**90-day policy** 是 Google Project Zero 建立的事實標準：

```
Day 0: 報告送出
         │
         ▼
Day 1-7: 廠商確認收到（若無回應，第 7 天 follow up）
         │
         ▼
Day 7-30: 廠商確認 bug，開始 fix
         │
         ▼
Day 60-80: 廠商預計發布 patch 時間
         │
         ▼
Day 90: Embargo 結束，無論廠商是否 patch 完畢，public disclosure
         （Project Zero 的標準；某些研究者給更長的 grace period）
         │
         ├── 廠商已 patch → 同步發布 advisory
         └── 廠商未 patch → 仍然 public（full disclosure）
                             目的：給用戶知情權和防禦機會
```

**grace period 談判**：廠商有時會要求延長 embargo，Project Zero 的政策是：

- 如果廠商在 Day 84 發布了 patch，Project Zero 給 7 天 grace period（到 Day 97 才 public）
- 更長的延伸需要強烈理由（例如已知有 active exploitation，延長能保護更多用戶）
- 無限延伸不接受

**何時不遵守 90 天**：如果 bug 在 embargo 期間被第三方獨立發現並 public，或者廠商確認已有 in-the-wild exploitation，可以提前 public。

## Step 7：申請 CVE

**CVE Numbering Authorities（CNA）**：

| 情況 | 聯絡誰 |
|------|--------|
| 廠商是 CNA（大多數大型開源專案、主要商業廠商） | 由廠商直接核發 |
| 廠商不是 CNA | 聯絡 MITRE（`cve@mitre.org`）或 GitHub Security Advisories（若在 GitHub 上） |
| 想自己申請 | 填 MITRE 的 CVE Request 表單 |

申請 CVE 需要的資訊：
- 漏洞描述（一段，說明 bug 類型和影響）
- 受影響的軟體與版本
- 參考連結（fix commit / advisory URL）

CVE 核發通常在 patch 發布前後，disclosure 和 CVE 可以同步進行。

**CVE ID 長什麼樣：**
```
CVE-2024-12345
 │    │    │
 │    │    └── 序號（MITRE 分配）
 │    └── 年份（通常是報告年，不是 patch 年）
 └── 前綴
```

## Bug Bounty

如果目標有 bug bounty program（HackerOne、Bugcrowd、廠商自辦）：

1. **先看 scope**：哪些 product / component 在 scope 內？哪些 bug type 付款？
2. **先看 known issue list**：不要報已知的 bug
3. **報告格式**：大多數 program 有自己的模板，按它寫
4. **付款金額範例**（2026 市場行情，各家差異大）：
   - RCE in production 系統：$10,000–$100,000+
   - 重要 crypto library 的 memory corruption：$5,000–$30,000
   - DoS：通常 $500–$2,000
   - Open source library（非 SaaS）：通常沒有直接 bounty，但可能有 GitHub 的 security advisory reward

**重要**：在 bug bounty 情況下，**不要在聯絡廠商之前 public**。Full disclosure before patch = 違反 bounty 條款，通常會失去獎金且被 ban。

## 一個完整案例流程（Project Zero 風格）

以 2019 年 Project Zero 找到的 Whatsapp 漏洞（CVE-2019-3568）為參考框架，梳理整個流程：

```
Day 0:  fuzzing 找到 crash（RTCP packet parsing 的 heap buffer overflow）
Day 0:  ASan 確認 bug type，minimize reproducer
Day 1:  root cause 分析（stack trace → source → RTCP parse loop）
Day 2:  可利用性評估（over-the-wire 可觸發，RCE 潛力）
Day 2:  撰寫 bug report
Day 3:  寄給 Facebook security team（security@fb.com）
Day 4:  Facebook 確認收到，開始 triage
Day 10: Facebook 確認為 Critical，開始 patch
Day 49: Facebook 發布 patch（WhatsApp 2.19.134）
Day 49: 協商公開時間
Day 60: Public disclosure（Project Zero issue tracker）
同時:   MITRE 核發 CVE-2019-3568
```

這個案例的特點：廠商反應快（49 天就 patch），主動提前 public（未到 90 天 deadline）。

## 踩雷

**踩雷 1：crash 不等於安全漏洞，不要每個 crash 都報 CVE**
錯誤直覺：「fuzzer 找到 crash，我就申請 CVE！」
正確：crash 分很多種——null pointer dereference 在 CLI 工具裡通常是 bug 但不是安全漏洞（攻擊者需要本地執行 binary）；只有在攻擊者能觸發的情境下才是安全漏洞。先評估 threat model：攻擊者是誰、怎麼觸發、影響是什麼。DoS-only 的 crash 在很多 bug bounty program 裡不付款，也不一定值得申請 CVE（雖然 MITRE 不強制要求「可利用」，只要有安全影響就可以申請）。

**踩雷 2：在廠商 patch 之前就 public**
錯誤直覺：「我在 Twitter 上說『在某個大型開源庫找到 RCE』，又沒貼 PoC，應該沒問題。」
正確：任何能讓有能力的攻擊者重現的資訊（模糊描述 + crash type + 受影響版本）都可能讓 bug 被獨立重現。在 patch 發布前 public（無論是否完整 PoC）在道德上有爭議，在有 bug bounty 的情況下通常直接取消獎金。

**踩雷 3：只看 ASan 的 bug type，不評估實際可利用性**
錯誤直覺：「ASan 說是 heap-buffer-overflow WRITE，一定是 Critical。」
正確：一個 1-byte overflow into heap padding（不影響 metadata）在現代 glibc 環境下幾乎不可利用；一個看起來是 READ 的越界但洩露了 heap pointer 可能是繞過 ASLR 的關鍵原語。CVSS score 的 Impact 欄位（Confidentiality/Integrity/Availability）需要真實的可利用性評估，不能機械地從 bug type 對應。

**踩雷 4：報告裡沒有 build 步驟**
錯誤直覺：「廠商知道自己的 code 怎麼 build，我只要說 crash type 和 crash input 就夠了。」
正確：廠商的 security team 不一定是熟悉 fuzzing 環境的工程師，他們可能需要從零 reproduce。一份好的 bug report 要包含：精確的 git commit hash、完整的 build 指令（含 compiler 版本）、minimized crash input 的完整 hex dump 或 base64、reproduce 的完整指令。把 reproduction time 從「一週」縮到「10 分鐘」，修 bug 的優先級就會高很多。

## 進階延伸

- **CVSS v4.0**：2023 年發布的新版評分系統，比 v3.1 多了 Supplemental Metrics（Automatable、Recovery、Safety 等），更適合描述現代供應鏈漏洞。在申請 CVE 時，廠商越來越常用 v4.0。
- **VEX（Vulnerability Exploitability eXchange）**：供應鏈安全裡的新格式，廠商用 VEX 文件聲明「這個 CVE 在我的 product 裡不可利用（因為 code path 不可達）」，避免用戶因 false positive 花費大量 patch 成本。CISA 在推廣這個格式。
- **Full disclosure vs coordinated disclosure 的歷史爭議**：1990 年代 Mudge 和 L0pht 的 full disclosure 運動、2000 年代 vulnerability research 的商業化（Zero-Day Initiative）、到今天 90-day policy 的成為事實標準，這段歷史解釋了現在的 responsible disclosure 體系是怎麼形成的。David Litchfield 和 HD Moore 的公開爭論是很好的切入點。

## 動手練習

1. 選一個 OSS-Fuzz 的 public bug（`https://bugs.chromium.org/p/oss-fuzz/issues/list`），找一個 Fixed 狀態的 heap-buffer-overflow，讀它的 ASan stack trace，練習：（a）找出 crash 的那一行；（b）根據報告判斷 severity；（c）在 GitHub 上找對應的 fix commit。
2. 對你在 Ch 44 或其他章節跑出的 crash（如果有的話），完整走過 minimize → ASan 解讀 → gdb root cause 三個步驟，寫一份 500 字以內的 bug report（格式參照本章範例）。
3. 去 HackerOne 或 Bugcrowd 找一個你熟悉的開源 library 的 bug bounty program，閱讀它的 scope 頁面，列出：（a）哪些 bug type 有獎金；（b）RCE 和 DoS 的獎金差距；（c）他們要求的 report 格式與本章範例的差異。

## 本章重點

- Crash → CVE 要走完六步：reproduce、minimize、root cause、severity、disclosure、CVE 申請
- ASan 報告是 root cause 的起點，gdb 是補充，source code 是終點
- 可利用性判斷不能機械地從 bug type 對應——要看 controllability、記憶體佈局、exploit primitives
- Responsible disclosure 的黃金標準是 90-day policy，embargo 中提前 public 是大忌
- Bug report 的品質決定廠商的 fix 速度：minimized input + build steps + 完整 ASan output = 廠商的時間成本最低

## 自我檢核

- [ ] 我能說出 crash triage 的五個步驟及其順序
- [ ] 我能從 ASan 報告讀出 bug type、crash 位置、分配位置三個關鍵資訊
- [ ] 我能列出可利用性評估的三個關鍵問題（overflow 大小、內容可控性、相鄰 chunk 是什麼）
- [ ] 我知道 responsible disclosure 的 90-day policy 是什麼，以及何時可以縮短 embargo
- [ ] 我能說出 CVE 申請的兩個管道（廠商 CNA / MITRE 直接申請）
- [ ] 我能寫出一份包含 summary / reproducer / root cause / impact 的基本 bug report

## 延伸閱讀

1. **[Project Zero: Exploiting the Linux kernel via packet sockets](https://googleprojectzero.blogspot.com/2017/05/exploiting-linux-kernel-via-packet.html)** — Project Zero 2017
   讀哪段：整篇，特別是「The Bug」和「From Bug to Exploit」兩節；學什麼：看一個真實 fuzzing 找到的 kernel crash 如何走完 root cause → exploit primitive 的完整流程，是「可利用性評估」最好的一手範本。關聯：本章 Step 4 可利用性判斷的實際應用。

2. **[A Year of Responsible Disclosure](https://www.google.com/about/appsecurity/research/)** — Google Project Zero 2014（原始 announcement）+ 後續 blog
   讀哪段：Project Zero 的 90-day policy 說明文件，以及 [Project Zero's disclosure policy FAQ](https://googleprojectzero.blogspot.com/2015/02/feedback-and-data-driven-updates-to.html)；學什麼：90-day policy 的完整邏輯——為什麼選 90 天、grace period 如何運作、full disclosure 的倫理基礎。關聯：本章 Step 6 embargo 與 disclosure。

3. **[CVSS v4.0 Specification](https://www.first.org/cvss/v4-0/)** — FIRST.org 官方
   讀哪段：Section 2（Base Metrics）和 Appendix A（Examples）；學什麼：如何正確填 CVSS score 的每個欄位，特別是 Attack Vector（AV）和 Impact 的判斷方法——這兩個欄位決定 score 最多 80% 的差異。關聯：本章 Step 4 可利用性判斷後如何換算成 CVSS score。

→ [Final Project：真實開源目標端到端 fuzzing campaign](./final-project-real-target-campaign.md)
