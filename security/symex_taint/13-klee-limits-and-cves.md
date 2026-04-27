# Ch 13 — KLEE 的實戰邊界與 CVE 案例

> 目標：用真實 CVE 看 KLEE 的戰績與極限。講完你要能對一個 target 預判 "KLEE 會不會跑得動"，以及用什麼 tweak 讓它跑得動。

## KLEE 出名的 coreutils 戰役

KLEE 的 OSDI 2008 paper 是 symex 歷史里程碑。作者們把 GNU coreutils 89 個 utility（`ls`、`cat`、`date`、`tr`、...）全部丟 KLEE 跑：

- 總共生成 **3321 個 test**
- **平均 90+% line coverage**（超過 coreutils 自己的 manual test suite）
- 發現 **10+ 個 new bugs**，其中幾個是 10 年以上的 old bug

具體幾個：

- `paste -d\\`：segfault（empty delimiter 引發 OOB）
- `seq 0 1 -1`：infinite loop
- `mkfifo --context=x ...`：double-free
- `pr -e\t -s(空格)`：segfault

這些 bug 用 fuzzing 都能找得到，但 KLEE 在**幾小時內窮舉到**，而且每個 bug 都配有**精確的觸發 input**。

## 為什麼 coreutils 很適合 KLEE

coreutils 的特性恰好是 symex 甜蜜區：

- **小**：每個 utility 幾百~幾千行
- **純 computation**：主要 input 是 command line args + 一個檔案
- **syscall 少且已 model**：read/write/open/stat 都在 POSIX runtime
- **無 threading / network**
- **path 有界**（固定 argv 長度上限）

**換到不符合的 target，KLEE 就沒這麼風光**。

## CVE 實例（跟 KLEE 或 symex 相關）

### CVE-2007-1387（gawk format string）

`gawk` 在 `printf` 的 format string 處理上有 OOB read。KLEE-衍生的工具跑 gawk 找到 — 但要先手動把 `printf` 相關的 format string 代碼 isolate 出來當 harness，不然 full gawk 太大 KLEE 跑不完。

**教訓**：KLEE 不是 push-button，大 target 要先挖 harness。

### CVE-2013-4282（busybox wget OOB）

Busybox 的 wget parser 對 HTTP redirect URL 有 OOB。被一個 KLEE-based tool 找到，用 symbolic URL string 餵進去。但這個 bug 要：

- 限定 URL 長度 < 64（符號 input space 控制）
- Hook 掉 network（不實際連線，只分析 parser）
- 限定 redirect 次數 ≤ 3

最終 KLEE 跑 1 小時找到。fuzzing 也能找但 KLEE 的 input 自帶解釋。

### CVE-2019-17365（Nix sandbox escape）

Ocaml-based sandbox，**不是用 KLEE 找的**，但用類似的 symex 思路手動推 constraint 找到。這說明：**symex 的思維有時比工具本身更有用**。

### CVE-2022-26291（libexpat integer overflow）

Expat parser 對 XML nested depth 沒檢查上限，KLEE / angr 跑起來都會陷進 deep recursion。手動寫個 bound 檢查 harness 後，KLEE 能找到 integer overflow。

### 近年：DARPA CGC

2016 年 DARPA Cyber Grand Challenge 的冠軍 Mayhem 團隊用 **symex + fuzzing** 自動找 bug 並生成 exploit。其中 symex 引擎是 CMU 的 Veritesting（Ch 8 提過），底下跟 KLEE 同源。

Mayhem 在比賽中：
- 分析 100+ 個故意設計的 vulnerable binary
- 自動產生 exploit
- 擊敗所有 human team

這是 symex 自動化漏洞研究的高光時刻。

## KLEE 搞不定的 target 類型

### 類型 1：大型 parser（target > 几十萬 LOC）

例子：libxml2、OpenSSL ASN.1 parser。

- Path 輕易超過 10^10
- Formula 深到 SMT 幾秒才 solve
- Memory 爆

**KLEE 的路**：
1. 把 parser 的 entry function isolate 出來做 harness
2. 限制 input size 到幾十 byte
3. 用 `--max-forks` 限 path 擴展

**更好的路**：用 AFL / libFuzzer 主跑，KLEE 在 stuck 時救。（hybrid fuzzing，Ch 25）

### 類型 2：Crypto 實作

SHA256、AES 這類。

- 每個 round 有 nonlinear bit operation
- Formula 在幾個 round 後就爆
- 實際上 **正確的 crypto 實作對 brute force 免疫，symex 一樣免疫**

**KLEE 能做**：驗證 constant-time 性質（不同 input 走相同 path）、驗證實作等價（兩版 SHA256 對 symbolic input 結果相同）。

**不能做**：crack 密碼、破 hash。

### 類型 3：State machine / protocol

DNS parser、TLS handshake。

- Depth 過深（state 序列長）
- External call 很多（network、時間）
- State explosion

**KLEE 的路**：harness 只跑一個 state transition，不跑整套 handshake。

### 類型 4：Kernel code

Linux kernel driver、filesystem code。

- 需要 kernel context（struct task_struct、VFS）
- 有 lock、interrupt、SMP
- 很多 extern reference

**KLEE 沒辦法**。KernelKLEE 是 2020s 的研究，工程不成熟。用 syzkaller (fuzzing) 取代。

## angr 會贏的情境

KLEE 的最大限制是**需要 source**。下面情境 angr 接管：

- **Binary-only CTF**
- **閉源軟體**
- **Malware 分析**
- **Firmware / IoT**（常無 source）
- **跨架構**（ARM、MIPS binary）

KLEE 有個 **KLEE-Binary** port 嘗試 handle binary、但效果遠不如 angr。binary 做 symex 就用 angr。

## 真實工作流：把 KLEE 壓進開發 pipeline

KLEE 在實務被用得最多的地方其實**不是獨立 bug finding**，而是：

### 1. Test generation

coreutils paper 的應用：對 library function 生成單元測試。

```c
int my_utf8_decode(const char* s, size_t n, uint32_t* cp);

// harness
int main() {
    char s[10];
    uint32_t cp;
    klee_make_symbolic(s, 10, "s");
    size_t n;
    klee_make_symbolic(&n, sizeof(n), "n");
    klee_assume(n <= 10);
    my_utf8_decode(s, n, &cp);
    return 0;
}
```

跑完 KLEE 產生 N 個 test — 對 library 做回歸 test 的好種子。

### 2. Patch verification

兩個 version 的同一個 function，用 KLEE 驗「patch 後 & patch 前，對任意 input，相同 invariant 成立」。

### 3. 定點 bug reproducer

已知某個 CVE 的 crash file 格式複雜、想 minimize — KLEE 可以用 symex 從現有 crashing input 反推最小子集。

## 你自己用 KLEE 的 mental checklist

要不要用 KLEE 解這個 target？回答：

1. **有 source 嗎？** 沒 → angr
2. **LOC > 10 萬？** 是 → 要挖 harness
3. **純 computation？** 是 → KLEE 甜蜜區
4. **有 syscall / file / network？** 多 → 先看 POSIX model 夠不夠
5. **有 threading？** 有 → 放棄 KLEE
6. **有 crypto / 非線性大量運算？** 有 → symex 走不動
7. **Input 有界嗎？**（幾 KB 以內） 是 → 可
8. **想 coverage 還是 bug？** coverage → KLEE；bug → 先試 fuzzer

走完這套 checklist，你知道該不該花時間架 KLEE。

## 一個常見的 "看起來該用 KLEE 但其實不該" 的 case

許多人看到「我要找這個 C library 的 bug」就想開 KLEE。但實際上：

- 如果你有 corpus / 樣本 → 先丟 AFL 一天，幾乎都會先出 bug
- AFL 沒出 → 看 coverage 報告，找卡在哪
- 卡在 magic check → **這才是 KLEE 該上場**

這就是 hybrid fuzzing 的順序。**KLEE 很貴、fuzzing 很便宜，你要讓貴的那個只做便宜那個做不到的事**。

## 心法

KLEE 的 paper 很漂亮，真實世界用起來很挑 target。

不要一看到 symex 就想上 KLEE。先問：
- 這題 AFL 夠嗎？→ 夠就別 KLEE
- 這題需要解 constraint 嗎？→ 需要再 KLEE
- 我有 harness 嗎？→ 沒 harness 不用開始

KLEE 用對了是神器。用錯了就是幾百 GB log 換零收穫。

## 自我檢核

- [ ] 講得出 coreutils paper 的主要成果（coverage、bug 數）
- [ ] 列出 4 個 KLEE 搞不定的 target 類型與 each 的原因
- [ ] 說得出 KLEE 的替代品 — angr 在什麼情境接管
- [ ] 能 walk through「要不要用 KLEE」的 checklist
- [ ] 理解 KLEE 在現代 pipeline 裡常搭 fuzzer 使用

Part 3 結束。下個是 **練習 B**，用 KLEE 對一個故意放洞的小程式找 bug，親手體驗 POSIX runtime 與 test case replay。

→ [練習 B：用 KLEE 找一個故意放漏洞的小程式](./practice-b-klee-find-bug.md)
