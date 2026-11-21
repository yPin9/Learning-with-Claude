# Final Project — 重建一個已公開 kernelCTF submission

> **目標**：挑一篇**已經公開**的 kernelCTF submission（[google/security-research](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf) 裡的任何一篇），把它的 `docs/vulnerability.md` 和 `docs/exploit.md` 讀到真正懂，然後——**不抄它的 `exploit.c`**——在你自己授權的 QEMU 環境裡，從 root cause 開始，獨立重建一條能跑的 exploit，重現它宣稱的穩定度，最後用你自己的話寫出兩份文件。這是全書的畢業考：不是再學一個新技巧，而是證明「前面 51 章 + 6 個練習學到的東西，我能自己接起來，做出一個真實形狀的成品」。讀完這份專案你應該能說：我能看著任何一篇新的 submission，認出它每一步對應本書哪一章，並且知道如果換我來，我要怎麼做。

> **環境與合法性聲明**：所有動手一律在**你自己擁有或明確授權**的環境（[Ch 0](./00-environment-setup.md) 搭的 QEMU、或任何你能隨意摧毀重建的 VM / CTF 靶場）。本專案刻意只重建**已經有上游補丁、漏洞已公開**的 N-day——這類漏洞的細節本來就在公開 repo 裡，重建它不會製造新的 0-day，也**沒有資格**真的投去 kernelCTF（見下方「專案規格」）。本檔提供的是方法論與里程碑，不是任何特定 submission 的逐行解答；凡涉及你自己 kernel 版本的 offset、spray 數量、位址，本書一律標「未實測，依你實測填入」。

## 為什麼用「重建」當作畢業專案

學一門攻擊技術，從「我讀懂了」到「我做得出來」之間有一條很寬的河。你讀完 [Ch 23](./23-dirty-pagetable.md) 會覺得 Dirty Pagetable 的原理很清楚；但真要你**對著一個活的 kernel，從一個 UAF 走到 root**，中間有幾十個「書上沒寫、但你不解決就過不去」的縫隙——哪個 spray 物件在你這版 kernel 的 size 對不對、per-CPU freelist 這次站哪一邊、你以為的 offset 和 `pahole` 查出來差了 8 bytes。這些縫隙才是真正的技能，而它們**只有動手才會浮出來**。

「重建一篇已公開的 submission」是跨這條河最好的橋，理由很簡單：**答案存在，但工作還是得你自己做**。

```
  從零找 0-day：                     重建已公開 submission：
  ─ 不知道有沒有洞                   ─ 確定有洞（已公開）
  ─ 不知道能不能利用                 ─ 確定可利用（有人做到了）
  ─ 卡住了不知道是「此路不通」        ─ 卡住了可以對答案：
    還是「我還不夠強」                  是我哪一步漏了，不是路不通
  → 挫折點太多，初學者會卡死          → 挫折可控，每一關都有驗證標準
```

對答案不等於抄答案。你還是得**自己挑對一篇**（挑錯一篇，環境建不起來就全卡死）、**自己重推 root cause**（光背 `vulnerability.md` 的結論沒用，你要能從 patch 自己推回去）、**自己重找 offset**（寫手的 kernel 和你的 kernel 幾乎不可能完全一樣）、**自己重達穩定度**（這是全程最難、最像真實工作的一段）。公開的 `exploit.c` 是你的「參考解答」——照 [練習 E](./practice-e-nftables-exploit.md) 教你的紀律：**卡死了再看，而且只看你卡住的那一小段**，看完回來自己寫。

這正是 [README](./README.md) 學習方式建議第 2 條（「每個利用技術一定要自己編、自己跑、自己打」）和第 4 條（「讀 kernelCTF submission writeup」）的合體——前者要你動手，後者要你讀真貨，畢業專案把兩者綁成一件事。

## 怎麼挑一篇適合重建的 submission

挑題本身就是一項技能。挑錯題，你會卡在「環境建不起來」而不是「技術學不會」，那是浪費。用這張表篩：

| 篩選維度 | 挑它 | 避開它 | 為什麼 |
|---|---|---|---|
| **漏洞類型** | 你已經練過的類型（UAF、cross-cache、Dirty Pagetable、nf_tables、io_uring） | 你完全沒碰過的子系統 | 畢業專案是「接起來」，不是「再學全新東西」——讓技術是熟的，把精力花在「串起來」 |
| **文件品質** | `vulnerability.md` 和 `exploit.md` 都寫得清楚、分步 | 只有一份、或寫得極簡的 | 你要能從文件重推，文件爛你就只能逆抄 `exploit.c`，失去意義 |
| **目標 kernel** | 你能在 QEMU 裡實際 build 起來的版本（[Ch 0](./00-environment-setup.md)），通常是某個 LTS | 綁死 COS / 特殊硬體 / 極新 rc kernel 的 | 環境重現不了，後面全卡；LTS-based 的舊 submission 最好建 |
| **技術新鮮度** | 技術對應到本書某一章，你能說出「這是 Ch X 那招」 | 用了本書沒涵蓋的冷門原語 | 能對應章節，卡住時知道回去翻哪裡 |
| **mitigation 密度** | 第一次重建選 mitigation 少一點的（舊 LTS target） | 一上來就挑最硬的 Hardened/Mitigation target | 先把「串完一條鏈」走通，再挑戰緩解繞過；別第一次就賭 CFI+SLAB_VIRTUAL 全開 |

幾個「形狀」上適合的方向（照技術與年代挑，不是叫你抄特定某篇——去 repo 裡按這些線索找）：

- **一篇 nf_tables UAF**（對應 [Ch 30](./30-netfilter-nftables.md)、[Ch 50](./50-patch-to-exploit.md)、[練習 E](./practice-e-nftables-exploit.md)）：kernelCTF 最大礦區，writeup 多、模式你最熟，是第一次重建的首選。
- **一篇用 Dirty Pagetable 的**（對應 [Ch 23](./23-dirty-pagetable.md)、[練習 D](./practice-d-crosscache-dirtypagetable.md)）：如果你 cross-cache + 頁表那條鏈想練到底，挑這種；通常還會搭 cross-cache（[Ch 22](./22-cross-cache.md)）。
- **一篇 io_uring 的**（對應 [Ch 31](./31-io-uring.md)）：async ring 的生命週期 UAF 很有代表性，但注意很多目標已把 `IO_URING` 關掉（見 [Ch 49](./49-kernelctf-overview.md) Hardened target），要挑它還開著的年代的 submission。
- **一篇 data-only 收尾的**（對應 [Ch 29](./29-data-only-attack.md)）：現代主流，練「不靠 ROP 提權」最實在；通常最後落在 cred 或 `modprobe_path`（[Ch 16](./16-classic-primitives.md)）。

挑完，做一件事再往下：**把你選的那篇，連同它的目標 kernel 版本、宣稱的穩定度、用到的核心技術，寫成一行筆記**。這行筆記就是你這個專案的「規格書」。

## 專案規格

你的交付物刻意對齊 [Ch 49](./49-kernelctf-overview.md) 講的 kernelCTF PR 檔案結構——因為畢業專案的隱藏目標，就是讓你產出一份「形狀上合格」的 submission，哪怕它不能真的投：

| 交付物 | 規格 | 對應章節 |
|---|---|---|
| `exploit/<target>/exploit.c` | **你自己寫的** static-compiled C，不是抄來的；支援完整提權 | [Ch 49](./49-kernelctf-overview.md)（為什麼是 C）、全書 |
| `exploit/<target>/Makefile` | 至少 `exploit`（編譯）和 `run`（執行）兩個 target | [Ch 49](./49-kernelctf-overview.md) |
| `--vuln-trigger` 模式 | 同一支 exploit 的一個旗標，只觸發漏洞到系統可觀察異常，不跑完整鏈 | [Ch 49](./49-kernelctf-overview.md) |
| `docs/vulnerability.md` | **用你自己的話**寫的 root cause 分析，從 patch 推回去 | [Ch 42](./42-patch-to-rootcause.md) |
| `docs/exploit.md` | **用你自己的話**寫的利用手法：怎麼把漏洞變 root，每一步賭哪層防禦沒開 | [Ch 50](./50-patch-to-exploit.md)、[Ch 26](./26-modern-mitigations.md) |
| 穩定度報告 | 連跑 20 次的成功率 + 平均耗時，照 Ch 49 評分公式算一個分數 | [Ch 45](./45-exploit-stabilization.md)、[Ch 49](./49-kernelctf-overview.md) |

> **不要真的投去 kernelCTF。** 你重建的是一個**已經有上游補丁、已經公開**的漏洞——規則明確要求提交必須是 0-day（無公開揭露、無上游補丁，見 [Ch 49](./49-kernelctf-overview.md)「哪些漏洞沒有資格提交」）。你這份東西天生不合格，它的價值 100% 在「你做出來了」這件事本身，不在獎金。把它當成你 GitHub 上的學習成果、面試作品、或自己的里程碑，不要拿去佔用 kernelCTF 的評估資源或排行榜。

## 建議里程碑

這是一個**跨好幾週**的專案，不是一個下午。切成 M0–M6，每個里程碑有明確的「做到什麼才算過」——過不了就別往下，否則後面會塌。

### M0 — 環境就緒 + 選定 submission

照 [Ch 0](./00-environment-setup.md) 把 QEMU + gdb remote 跑起來，並用上面那張表選定你要重建的 submission，寫下一行規格筆記。

- **做到什麼才算過**：`/dev/` 下的 target、gdb 能 attach、你能說出「我選了哪一篇、它打哪版 kernel、用什麼技術、宣稱多穩」。

### M1 — 讀懂兩份文件，並能用自己的話複述

把 `vulnerability.md` 和 `exploit.md` 讀完，然後**合上文件**，用你自己的話對著空白頁（或對著一個假想聽眾）講一遍：這個洞是什麼、怎麼從洞走到 root。講不順的地方，就是你還沒懂的地方，回去再讀。

- **做到什麼才算過**：你能不看文件，畫出一張從「root cause」到「最終提權原語」的鏈路圖，每一步用 [Ch 10](./10-vulnerability-taxonomy.md) 的漏洞分類詞彙標注。

### M2 — build 出那版脆弱 kernel，用 `--vuln-trigger` 重現 crash

照 submission 標的版本，在 QEMU 裡 build 出**那一版**（通常是某個 LTS 的某個 tag）脆弱 kernel，開 `CONFIG_KASAN`（[Ch 44](./44-debug-toolchain.md)），寫一個最小觸發程式，讓 KASAN 報出和 `vulnerability.md` 描述一致的錯誤。這一步先**不碰提權**，只證明「漏洞在這版上確實活著、我能可重複地踩到它」——這正是 kernelCTF 要求 `--vuln-trigger` 模式的用意。

- **做到什麼才算過**：`dmesg` 裡看到和文件描述吻合的 KASAN report（同一個函式、同一種 bug class），而且你重跑能穩定重現，不是湊一次運氣。

### M3 — 只靠 patch + `vulnerability.md` 重推 root cause（別看 `exploit.c`）

這是整個專案最能逼出理解的一步。找到上游的 **fix commit**（`git log` 配 `Fixes:` tag，見 [Ch 42](./42-patch-to-rootcause.md)），**只看 patch diff 和 `vulnerability.md`**，自己推：這個 patch 補了什麼不變式（invariant）？漏洞是哪個生命週期 / 所有權 / refcount 假設被打破？**此時刻意不要去讀 `exploit.c`**——你要先能獨立說清楚「為什麼會錯」，再去看別人「怎麼利用這個錯」。

- **做到什麼才算過**：你寫出一份 `docs/vulnerability.md` 草稿，內容是**你自己推的**，而且和官方那份的結論一致（允許你表達方式不同，但技術結論要對得上）。

### M4 — 自己重建原語與 leak（卡死才翻 `exploit.c`）

從 M3 的 trigger 出發，自己做：用什麼物件 reclaim 被釋放的記憶體（[Ch 19](./19-spray-objects.md)）、它落在哪個 cache、哪個欄位給你讀、哪個給你寫、怎麼 leak 打 KASLR（[Ch 14](./14-kaslr-infoleak.md)）。**遵守紀律**：先自己想、自己試；真的卡住超過一段合理時間，才翻開 `exploit.md`／`exploit.c`，而且**只看你卡住的那一小段**，看懂馬上合起來自己寫。

- **做到什麼才算過**：你能穩定拿到一個 kernel 位址 leak，並且能說出「這個 leak 來自哪個結構的哪個欄位、怎麼推回 KASLR base」。

### M5 — 提權 + 串完整鏈

把 leak 接到提權。現代 submission 多半走 data-only（改 cred 或 `modprobe_path`，[Ch 29](./29-data-only-attack.md)、[Ch 16](./16-classic-primitives.md)），尤其目標開了 CFI/KCFI（[Ch 28](./28-cfi-kcfi.md)）時——想清楚你這篇賭的是哪層防禦沒開。串成一個「從 `./exploit` 到 root shell」的單一執行檔。

- **做到什麼才算過**：`./exploit`（完整模式）能從普通使用者拿到 `uid=0` 的 shell，哪怕成功率還不高。

### M6 — 穩定化 + 寫兩份文件 + 穩定度報告

這一步把「能跑一次」變成「能重複跑」。照 [Ch 45](./45-exploit-stabilization.md)：pin CPU、grooming 可重複（[Ch 20](./20-heap-grooming.md)）、每一步自我驗證、失敗就重試而不是 panic、用完 cleanup 把 kernel 收回一致狀態。設一個你要達到的目標成功率（建議先訂 70%，對齊 Ch 49 門檻；行有餘力再往 90% 推）。最後寫完你自己的 `docs/vulnerability.md`（M3 的草稿補完）和 `docs/exploit.md`，連跑 20 次做穩定度報告。

- **做到什麼才算過**：連跑 20 次，成功率 ≥ 你訂的目標、單次不超過合理時間上限；兩份文件寫完、別人照著能看懂你的鏈路。

## 評分你自己的成果（自評 rubric）

沒有評審，你得誠實地當自己的評審。對每一項給自己一個等級，並寫一句理由：

| 維度 | 不及格 | 及格 | 優秀 |
|---|---|---|---|
| **root cause 理解** | 只會背文件結論 | 能從 patch 自己推回不變式 | 能說出「如果當初這樣寫就不會錯」，並認出同類 bug 模式 |
| **獨立重建程度** | 幾乎逐行照抄 `exploit.c` | 卡住才看、只看卡住的段、看完自己重寫 | 全程只在少數幾個點對過答案，其餘自推 |
| **穩定度 vs 原作** | 只能偶爾打穿一次 | 達到你訂的目標成功率（如 70%） | 逼近或超過原作宣稱的穩定度，並能解釋你做了哪些 Ch 45 的工程 |
| **writeup 品質** | 流水帳、別人看不懂 | root cause 與 exploit 分開寫、步驟清楚 | 讀起來像一篇能進 repo 的 submission，術語精確 |
| **可移植性** | offset 全寫死，換版本就爛 | 關鍵 offset 參數化、有註解來源 | runtime 偵測版本 / 從 kallsyms 解析，跨版本可移植（[Ch 45](./45-exploit-stabilization.md)） |

**自評的誠實度，比分數本身重要。** 「獨立重建程度」這一欄最容易騙自己——如果你發現自己其實是逆抄，那不丟臉，但要承認，然後挑下一篇時刻意更早合上答案。這本書從頭到尾只有一個對手：你自己上一次的理解深度。

## 常見卡點與怎麼突破

1. **你的 offset 和 writeup 的對不上。** 幾乎必然發生——寫手的 kernel build 和你的不一樣。別硬套，用 `pahole`（[Ch 45](./45-exploit-stabilization.md)）對**你自己這版** kernel 重查 `struct` 佈局，把 offset 當成「每次換環境都要重算的變數」而不是常數。這正是「可移植性」那欄在練的東西。
2. **官方 `exploit.c` 一跑就穩，你的卻 panic。** 多半是你漏了一步 **cleanup**（[Ch 45](./45-exploit-stabilization.md)）——官方那份在用完原語後默默把 freelist / refcount / 被蓋掉的指標修回去了，你沒修，於是後續某次 alloc 踩雷。回去逐行比對「它在提權之後、起 shell 之前還做了什麼」，那段通常就是 cleanup。
3. **重現不出宣稱的穩定度。** 成功率上不去，先查三件事：有沒有 pin CPU（SLUB freelist 是 per-CPU，[Ch 8](./08-slub-allocator.md)/[Ch 45](./45-exploit-stabilization.md)）、grooming 每次是不是從同一個已知狀態開始（[Ch 20](./20-heap-grooming.md)）、是不是每次重跑前環境沒清乾淨（前一次殘留污染下一次佈局）。
4. **目標環境根本 build 不起來。** 你可能挑了一篇綁 COS 或特殊 config 的。退回 M0，改挑一篇 **LTS-based 的舊 submission**——舊、單純、文件齊全的那種，永遠是第一次重建的正確選擇。把「挑到能建的題」本身當成一次成功，而不是失敗。
5. **看了答案就忘了怎麼自己想。** 如果你發現每次卡住都直接翻 `exploit.c`，強制自己加一條規則：翻答案前，先把「我卡在哪、我試過什麼、我猜問題是什麼」寫下來三行。常常寫到第三行你就自己想通了。

## 畢業之後：從重建到原創

重建完一篇，你手上就有了一套**可重複的個人工作流**：挑題 → 讀懂 → 重推 root cause → 重建原語 → 穩定化 → 寫文件。這套流程和真實漏洞研究者的工作流只差一件事——**真實世界沒有人先幫你確認「這裡有洞、而且可利用」**。補上這一步，你就從「重建」走到「原創」：

- 把 [Ch 42](./42-patch-to-rootcause.md) 的方法論用在一個**還很新、剛出爐**的 patch 上，練「讀 patch 判斷可利用性」——這時沒有 `exploit.md` 給你對答案了，那份得你自己寫。
- 跑 Syzkaller（[Ch 43](./43-syzkaller.md)）或讀 syzbot，看**還沒有人寫 exploit** 的 crash report，練「從一個 fuzzer 吐出的 crash 判斷它能不能提權」。
- 持續讀新的 kernelCTF submission——現在你讀每一篇，都能一眼對應到本書的章節地圖，讀的速度和深度會和專案之前完全不同。
- 卡在某個子系統或技術時，回 [README](./README.md) 的**精選資料庫**：pawnyable、lrh2000、smallkirby、Project Zero blog，每一個都是把某條路推得更深的入口。

從重建到原創這一步，可能又是好幾個月。但你已經有橋了——剩下的只是走過去。

## 自我檢核

這是全書真正的「你畢業了嗎」清單。全部打勾，你就不只是讀完了這本書，而是**能用它**：

- [ ] 能不看任何筆記，對一個假想聽眾講清楚你重建那篇的 root cause：哪個不變式被打破、為什麼。
- [ ] 你的 `exploit.c` 是**你自己寫的**——你能誠實說出哪幾個點對過答案、其餘是自推，而且「逆抄」的比例低到你問心無愧。
- [ ] 連跑 20 次達到你訂的目標成功率，並能說出你做了哪些 [Ch 45](./45-exploit-stabilization.md) 的穩定化工程讓它到那個數字。
- [ ] 寫完了 `docs/vulnerability.md` 和 `docs/exploit.md` 兩份獨立文件，而且另一個學過本書的人照著能看懂、能重現。
- [ ] 你的 exploit 關鍵 offset 是參數化或 runtime 偵測的，換一個相近 kernel 版本不會整個爛掉。
- [ ] 現在隨便打開 kernelCTF repo 裡一篇你**沒**重建過的 submission，你能在十分鐘內說出：它是什麼 bug class、用什麼技術、對應本書哪幾章、如果換你來你會從哪裡下手。

最後一條打勾的那一刻，就是這本書對你結束的時刻——因為你已經不需要它當教材了，它變成了你的參考書。

---

你從 [Ch 0](./00-environment-setup.md) 的「QEMU 怎麼把 kernel 跑起來」開始，走過 SLUB 的 freelist、經典 mitigation、現代 heap 利用、CFI 之後的 data-only、kernelCTF 最大的那些礦區、並發與 race、找洞的工具鏈、跨平台差異，最後站在這裡：拿一個真實的、別人做到過的 exploit，自己再做一次。這條路沒有捷徑，你是一個 commit、一張記憶體佈局圖、一次 KASAN report、一次連跑 20 次走過來的。kernel exploitation 是系統安全的天花板——你現在站在上面了。接下來要找的洞、要讀的 writeup、要守的那台機器，都還在外面等你；這本書能給的，到這裡為止。剩下的，去做。

→ [回到課程地圖 README](./README.md)
