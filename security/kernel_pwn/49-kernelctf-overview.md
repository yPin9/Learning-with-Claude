# Ch 49 — kernelCTF 賽制與流程：環境、穩定性要求、submission 流程

> **目標**：搞懂 Google kernelCTF 到底是什麼、為什麼它比大多數 CTF kernel pwn 題更接近真實世界的 1-day 開發、它的目標環境與難度分層怎麼設計、submission 要交什麼、怎麼拿 flag、錢怎麼算。讀完這章,你要能看懂 [kernelCTF submission repo](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf) 裡任何一篇 writeup 的結構,也知道自己如果要投一篇,該準備什麼。

> **環境與時效性聲明**：kernelCTF 的賽制細節（目標環境、獎金數字、穩定性門檫）由 Google 持續調整,歷史上已經有多次重大改版。本章內容以 **2026 年 11 月**查證官方 [kernelCTF rules 頁面](https://google.github.io/security-research/kernelctf/rules.html) 的結果為準,查證時該頁面標注規則**自 2026-09-04 生效**。這份規則本身在你讀到這章的時候,很可能又已經修改——**具體數字、賽道名稱、穩定性門檫,一律以官方 rules 頁面當下的內容為準**,本章的角色是幫你讀懂規則文件在講什麼、為什麼這樣設計,不是取代它。

## 為什麼需要這一章

你在這本書前面 48 章學的每一個利用技術——SLUB grooming、cross-cache、Dirty Pagetable、data-only attack——都是為了打穿一個**真實、當下存在、有真金白銀獎勵**的目標而存在的。多數 user-space pwn CTF 的題目是出題者刻意埋的 toy vulnerability;kernelCTF 不一樣——它獎勵的是對 Linux kernel **真實 0-day 或尚未公開 N-day 漏洞**寫出能穩定復現的利用程式,目標是**上游真正在跑的 Linux kernel**（或 Google 自家 Container-Optimized OS 使用的 kernel 配置)。這意味著你在這個賽制裡磨出來的每一個技能,都直接對應到「如果我今天在真實世界發現一個 kernel 漏洞,我該怎麼把它變成一個能交出去的 exploit」這個問題——這正是 [README](./README.md) 把它定為全書 capstone 的原因。

kernelCTF 不是一場「限時幾小時,解開就好」的傳統 CTF。它是一個**長期運行的漏洞獎勵計畫（VRP，Vulnerability Reward Program）**,用「CTF」這個名字,是因為它借用了 CTF 的 flag 機制和公開排行榜文化,把枯燥的漏洞獎勵流程包裝成一個有競爭感、有排行榜的遊戲——但底層驅動的,是 Google 想透過真金白銀,逼研究者把**理論上存在的漏洞變成經過驗證的、可復現的利用**,而不是停在「這裡看起來有問題」的猜測階段。

## 從 kCTF 到 kernelCTF：一段簡短的歷史

kernelCTF 不是憑空冒出來的計畫。它的前身是 **kCTF**——Google 2020 年為了測試自家 **gVisor**（一個用 user-space 攔截 syscall 來隔離容器的沙箱技術)與 GKE（Google Kubernetes Engine）節點的 kernel 攻擊面而設立的漏洞獎勵賽。早期 kCTF 的題目範圍侵限在「容器逃逸」這個特定場景——研究者要從一個受限的容器環境裡,找路徑打穿底層 kernel。2021 到 2022 年間,Google 數度擴大獎金規模與目標範圍(從 2022 年的部落格文章可以看到單次 0-day 獎金一路往上調),2023 年正式把範圍從「容器逃逸」擴大成「對著當下真實運行的上游 kernel 開放提交」,並改名為 **kernelCTF**,這才是本章主要討論的版本。

知道這段歷史有兩個實際用處：第一,早期(2020–2022)的 kCTF 文獻用的獎金結構、目標環境和本章完全不同,讀到年代久遠的部落格文章時不要套用本章的數字;第二,這個計畫本身的命名與範圍**持續在演進**——如果 kernelCTF 這個名字本身有一天又改了,順著「Google kernel 漏洞獎勵計畫」這條脈絡去找,通常找得到當下的正確入口。

## 先建立直覺：三種「交作業」的方式,只有一種真的有用

```
方式 A —— 傳統漏洞獎勵計畫（VRP）：
  研究者：「我發現一個 bug,理論上能提權」
  廠商  ：「謝謝回報,但你證明得了嗎?」
  研究者：「……」
  → 廠商很難知道這個 bug 到底有多嚴重,獎金評估很主觀

方式 B —— 傳統 CTF：
  出題者：精心設計一個 vulnerable binary,塞進人工漏洞
  選手  ：解開拿 flag
  → 練到的技巧很扎實,但目標是假的,漏洞是人為埋的,
     解完換不到「這能不能打真實世界」的信心

方式 C —— kernelCTF：
  研究者：找到一個真實 kernel 的 0-day（或尚未公開的 N-day）
  研究者：寫出一個會員正打一個活著的 kernel 伺服器的 exploit
  伺服器：自動跑這個 exploit,成功拿到 root 才給 flag
          而且要求「跑 20 次至少 N 次成功」才算數
  → 漏洞的嚴重性不是靠嘴巴描述,是靠「打不打得穿、穩不穩」直接證明
```

這張圖的重點是方式 C 把「這個漏洞有多嚴重」這個本來很主觀的問題,變成一個**可自動化、可驗證的客觀分數**——這也是為什麼 kernelCTF 的 submission 幾乎都是**預先編譯好的 C 二進位檔**,而不是一段 Python PoC：伺服器要能在完全不信任提交者的前提下,自動跑這個東西、量測它、不需要人工介入判斷「這個漏洞是不是真的有這麼強」。

### 完整時間線:從找到漏洞到拿到獎金

把前面兩個階段的流程(本章後面會展開細節)先畫成一張時間線,建立全局感：

```
找到 0-day
   │
   ▼
上傳 exploit 壓縮檔 ──▶ 評估伺服器跑過 ──▶ 拿到 flag
   │                                          │
   ▼                                          ▼
填表單佔位,留編輯連結                  (flag 綁定這份提交的雜湊,
   │                                     無法憑空編造)
   ▼
贏得時段 ──▶ 7 天內回報 security@kernel.org
   │
   ▼
（等待,可能數週到數月）
   │
   ▼
上游補丁發布
   │
   ├─▶ 7 天內：回表單補齊技術細節
   │
   └─▶ 90 天內：對 security-research repo 開 PR
                   │
                   ▼
        GitHub Actions 自動檢查 + 確認漏洞吻合 ──▶ 發放一半獎金
                   │
                   ▼
            人工審查 + 合併 ──▶ 發放另一半獎金
```

這條時間線拉得相當長——從「找到漏洞」到「拿到全部獎金」,中間穿插了「等上游修補」這個完全不受研究者控制的等待期,短則幾週,長則數月。這也是為什麼 kernelCTF 的研究者社群普遍把「同時手上壓著好幾個候選漏洞,輪流推進」當作常態工作模式,而不是一次只處理一個。

## kernelCTF 的核心機制：目標環境、提交、評分、flag

### 目標環境：從「三條賽道」到「單一 Hardened 目標」

kernelCTF 從 2023 年啟動以來,長期維持過**三個目標環境（歷史上常被稱為三條「賽道」）**,這個結構在很長一段時間裡是理解 kernelCTF、也是讀早期 submission writeup 必備的背景知識：

| 歷史賽道（2023–2026 初期間) | 核心環境特徵 | 定位 |
|---|---|---|
| **LTS** | 追蹤當下最新的 Linux LTS（long-term support）kernel,每 2–4 週更新一次版本,每個版本只接受一次成功提交 | 最接近「真實世界剛發布的 N-day 窗口」——你要在廠商升級前,對著當下真正在跑的版本打穿它 |
| **COS（Container-Optimized OS）** | Google 自家 Container-Optimized OS 用的 kernel 配置與加固參數 | 模擬 GCP 上真實容器環境的攻擊面,獎金依是否使用 `user_ns`/`io_uring` 等高風險子系統而有差異（刻意鼓勵研究者找「不靠這些重型原語」的利用路徑） |
| **Mitigation** | 開最多現代緩解機制（為了測試緩解機制本身夠不夠硬)的目標 | 最難打的一條線,base reward 之外疊加「穩定性加成」與「0-day 加成」,鼓勵研究者挑戰最新緩解 |

**截至本章查證時間點（2026-11),上述三條賽道的分層已經被官方規則取代**——Google 把獎勵結構簡化成**單一目標 "Hardened"**：一個基於 Linux 7.2-rc5（x86-64)、套用 COS kernel config 再疊加額外加固的目標,啟用 kCFI + IBT、`SLAB_VIRTUAL`、`KMALLOC_PARTITION_TYPED`、關閉 `SLAB_MERGE_DEFAULT`,並且直接**關閉**掉幾個歷史上最容易出漏洞的高風險子系統（`IO_URING`、`USER_NS`、`TLS`、`CRYPTO_USER_API_AEAD`、`AF_UNIX_OOB`,並預設 `BPF_UNPRIV_DEFAULT_OFF=y`)。三條賽道、三套獎金結構、各自的 bonus 類別——在這次改版裡全部被取消,換成**單一固定獎金**。

查證時能找到的歷史資料顯示,舊 LTS 賽道的獎金結構大致是「base reward + 穩定性加成 + 0-day 加成」疊加而成——某個版本的 rules 存檔列出 base reward 約 $21,337,穩定性加成(要求 90% 成功率)約 $10,000,0-day 加成約 $20,000,三者疊加起來的量級就落在新制單一獎金 $71,337 附近;COS 賽道則記錄過「不使用 `user_ns`/`io_uring` 則全額 $21,000,否則減半」的規則。**這些歷史數字僅供理解規模量級,不保證是任何特定時間點的精確值**——舊版規則文件本身隨時間多次調整,這裡轉述的是查證時能找到的片段記錄,真正要考證某個特定年份的確切金額,應該去找 Google Security Blog 當年的公告原文或 Wayback Machine 的存檔頁面,而不是依賴這本書。

為什麼要講這段「已經過時」的歷史?因為 Project Zero blog、本書引用的 pawnyable、lrh2000 等教材,以及 kernelCTF submission repo 裡大量既有的 writeup,**都是在舊三賽道結構下寫的**——讀那些資料時,如果不知道「LTS/COS/Mitigation」這套語言曾經存在過、各自代表什麼,你會看不懂寫作當時的上下文(例如某篇 writeup 會寫「這是打 Mitigation instance,拿到 stability bonus」,這些詞在新規則下已經消失)。理解歷史結構,是讀懂這本書累積的既有文獻的前提。

### 當前目標的穩定性要求

不論賽制怎麼改版,kernelCTF 一路貫穿的核心精神沒變：**光打穿一次不算贏,你要證明這是可重複的**。查證時的規則要求：

- **跑 20 次,成功率至少 70%**（單次執行超過 5 分鐘直接算失敗,不計入成功)。
- 評分公式：`分數 = 2 × 穩定性(%) + max(100 − 平均耗時(秒), 0)`,滿分 300（100% 穩定 + 0 秒耗時的理論極限),及格門檫是 140 分（對應剛好 70% 穩定、平均約 100 秒)。
- Exploit 必須支援一個 `--vuln-trigger` 旗標,能跳過非必要步驟（例如 KASLR 的 info-leak 預取),單純把漏洞本身觸發到讓系統當掉——這是用來驗證「漏洞在舊版本（查證時為 LTS 6.12）上依然新鮮、還沒被修掉」的快速檢查,**不要求**這個模式下完成完整的提權鏈。

這套「跑 N 次、算成功率、設時間上限」的機制,直接對應你在 [Ch 45](./45-exploit-stabilization.md) 會學到的 exploit 穩定化工程——kernelCTF 的評分公式,本質上就是把那一章教的「heap grooming 要可重複、race 要有重試機制、要避免 panic」這幾件事,變成一個會真金白銀打你分數的硬指標。

拿評分公式算一個具體例子,幫你把抽象的門檫數字換算成直覺：

| 20 次執行結果 | 成功次數 | 成功率 | 平均耗時(僅計成功次數) | 分數 | 是否過門檫(140) |
|---|---|---|---|---|---|
| 18 成功、2 次超過 5 分鐘 | 18 | 90% | 40 秒 | `2×90 + max(100−40,0)` = 180 + 60 = 240 | 過 |
| 14 成功、6 次失敗 | 14 | 70% | 110 秒(超過 100 秒) | `2×70 + max(100−110,0)` = 140 + 0 = 140 | 剛好過(查證時的及格線) |
| 12 成功、8 次失敗 | 12 | 60% | 30 秒 | `2×60 + max(100−30,0)` = 120 + 70 = 190 | **不過**(穩定性低於 70% 門檫,不論耗時多短) |

最後一列值得特別注意：穩定性門檫(70%)和分數門檫(140 分)是**兩個獨立的關卡**——即使你把耗時壓到接近 0 秒讓分數算出來很高,只要成功率本身低於硬性門檫,查證時的規則依然會判定不合格。這呼應踩雷集錦會提到的一個常見誤解。

> **數字會變,方法不會**：70% 這個門檫、$71,337 這個獎金數字,都是查證當下的規則。你讀到這本書的時候,它們極有可能已經不同——但「要求可重複的成功率、要求有時間上限、要求覆蓋足夠多次執行」這個**設計哲學**,是 kernelCTF 從創立至今一路貫穿的,不太可能變。讀規則文件時,先抓這個哲學,再去記當下的具體數字。

### Submission 流程：兩個階段

kernelCTF 的提交流程刻意分成**漏洞公開前**和**漏洞公開後**兩個階段,目的是保護 0-day 不被提前洩漏,同時仍然給研究者一個「先佔位」的機制：

**第一階段（漏洞補丁發布之前)：**

1. 把編譯好的 `exploit` 二進位檔、原始碼、build 腳本打包成 `.tar.gz`——這份壓縮檔**就是你實際提交去跑的東西**,打包之後不要再改內容。
2. 把這個壓縮檔上傳到 kernelCTF 的評估伺服器,伺服器跑過之後回你一個 **flag**——這個 flag 的內容本質是這份壓縮檔雜湊值的編碼,等於「伺服器證明它真的跑過你這份、而且沒被竄改過的提交」。
3. 把拿到的 flag 填進官方表單提交,保留表單的編輯連結（之後第二階段要回來補資料)。
4. 到公開的排行榜（spreadsheet）確認自己的提交有被記錄到。
5. 如果你贏得了這個時段的提交資格,必須在 **7 天內**把這個漏洞回報給 `security@kernel.org`（也就是上游 kernel 的官方安全信箱——這一步不是選項,是規則要求,直接確保 kernelCTF 促成的是真正的漏洞修補,不是讓 0-day 停留在私人獎金流程裡)。
6. 確保之後正式發出的修補 patch 裡,`Reported-By` 欄位有你的名字（除非你同時也是修補者本人)。
7. 等待補丁真正進到主線或 stable tree。

**第二階段(補丁發布之後):**

1. 在補丁公開後 **7 天內**,回到第一階段保留的表單連結,補齊漏洞的技術細節。
2. **90 天內**對 `security-research` repo 開一個 Pull Request,內容是完整的 exploit 與技術說明。
3. PR 的 GitHub Actions 自動檢查通過、且確認這個 exploit 真的打中你聲稱的那個漏洞之後,**先拿到一半獎金**;人工審查通過並合併之後,拿到**另一半**。

這個「先佔位、後補完整細節」的設計,解決了一個現實問題:一個真正新鮮的 0-day,從「我發現它」到「我能寫出一個穩定利用」常常要花上數週,如果規則要求「先交完整 exploit 才能佔位」,會逼研究者在漏洞還沒公開、最有價值的那段時間裡拖著不報——反過來鼓勵大家盡早回報給 kernel 官方,寬限期內再補完整的技術細節。

### PR 的檔案結構

一篇完整的 kernelCTF submission(對應到你最後要交的 [Final Project](./final-project-kernelctf-rebuild.md))在 repo 裡的位置是 `pocs/linux/kernelctf/<CVE編號>_<目標名稱>/`（如果同一個目標有多篇提交,用 `_2`、`_3` 這類後綴區分),裡面固定要有:

```
pocs/linux/kernelctf/CVE-XXXX-YYYYY_hardened-v1-7.x/
├── original.tar.gz              ← 第一階段上傳的那份原始提交(存證用)
├── metadata.json                 ← schema v3,記錄 CVE 編號、目標、提交時間等結構化資訊
├── docs/
│   ├── vulnerability.md          ← 漏洞成因的技術說明(root cause 分析)
│   └── exploit.md                ← 利用手法的技術說明(怎麼把漏洞變成 root)
└── exploit/hardened-v1-7.x/
    ├── exploit.c
    ├── exploit                   ← 預先編譯好的二進位檔(伺服器直接執行,不重新編譯)
    └── Makefile                  ← 至少提供 exploit 和 run 兩個 target
```

這個結構值得特別記住——它幾乎就是你寫一份合格的漏洞分析報告該有的骨架:**漏洞成因**和**利用手法**分開寫成兩份獨立文件,這個切分本身就在教你"root cause 分析"和"exploit 工程"是兩件不同的事,值得分開講清楚,不要混在一篇流水帳裡。[Ch 42](./42-patch-to-rootcause.md) 教的「讀 patch 找 root cause」方法論,產出就應該長得像這裡的 `vulnerability.md`。

`metadata.json`（schema v3）裡大致會記錄這類結構化欄位,實際欄位名稱請以 repo 裡既有 submission 的範例為準,這裡只示意形狀：

```json
{
  "submission_date": "2026-03-14",
  "vulnerability": {
    "cve": "CVE-2026-XXXXX",
    "patch_commit": "<上游修補 commit 的 hash>",
    "vulnerable_since": "<引入漏洞的 commit 的 hash>",
    "bug_class": "use-after-free"
  },
  "exploits": [
    {
      "target": "hardened-v1-7.x",
      "stability": "95%",
      "uses_user_namespace": false
    }
  ]
}
```

這份 JSON 存在的理由,是讓整個 submission repo**可以被程式化地統計**——前面引用的 Google Security Blog 回顧文章裡那些「攻擊面分布」統計圖,正是靠彙整所有 submission 的 `metadata.json` 算出來的,不是人工一篇篇數的。

### Hardened 目標疊了哪些緩解,各自對應本書哪一章

查證時 Hardened 目標疊加的緩解機制,本書前面幾章都個別拆解過,這裡把它們對起來,讓你知道「打這個目標要準備哪些知識」：

| 緩解機制 | 對應章節 | 它擋住什麼 |
|---|---|---|
| kCFI + IBT（間接分支追蹤) | [Ch 28](./28-cfi-kcfi.md) | 限制 indirect call/jump 只能跳到合法的函式進入點,逼你從「控制流劫持」轉向「data-only」 |
| `SLAB_VIRTUAL` | [Ch 26](./26-modern-mitigations.md) | 讓 SLUB object 的虛擬位址不再直接對應實體記憶體佔用方式,干擾依賴「猜 heap 布局」的技巧 |
| `KMALLOC_PARTITION_TYPED` | [Ch 27](./27-random-kmalloc-caches.md) | 依型別把 kmalloc 配置切進不同分區,讓 heap spray 很難再「隨便找個同大小的物件就能噴中目標 cache」 |
| 關閉 `SLAB_MERGE_DEFAULT` | [Ch 9](./09-slub-hardening.md) | 避免大小相近但型別不同的 cache 被自動合併,降低 cross-cache 類攻擊([Ch 22](./22-cross-cache.md))的可操作空間 |
| 關閉 `IO_URING`/`USER_NS`/`TLS` 等子系統 | [Ch 30](./30-netfilter-nftables.md)、[Ch 31](./31-io-uring.md)、[Ch 36](./36-user-namespace.md) | 直接拿掉歷史上出洞密度最高的幾個攻擊面,不是加固這些子系統,是不給你碰 |

從這張表可以看出 Hardened 目標的加固策略是**雙軌並行**:一部分是「讓傳統技巧更難生效」(kCFI、SLAB 系列),另一部分是「直接關掉容易出事的子系統」——後者意味著打這個目標的研究者,勢必要往本書 Part 5 裡那些**還留著**的子系統(ksmbd、GPU-DRM、檔案系統、TTY/keyring 家族——[Ch 34](./34-filesystem-attack-surface.md)、[Ch 35](./35-tty-keyring-watchqueue.md)、[Ch 37](./37-other-subsystems.md))裡找漏洞,而不是繼續啃已經被關掉的 netfilter/io_uring。

### 哪些漏洞沒有資格提交

規則對「這個漏洞算不算合格提交」也有明確邊界,幾個常見的判斷點：

- **必須是 0-day**:提交當下,這個漏洞不能已經有上游公開補丁、也不能已經被任何形式公開揭露(部落格、推文、conference talk 都算)。
- **同一根因只認第一個提交**:如果兩個人各自獨立找到「表面上是不同 bug,但修補 commit 其實是同一個根因」的漏洞,規則會判定這是重複,只有先提交的人算合格——這逼研究者在寫 `docs/vulnerability.md` 時,要把根因分析寫得足夠精確,方便判定重複性。
- **不能已經回報給其他管道**:如果這個漏洞已經透過其他 bug bounty(例如 Pwn2Own 這類比賽)回報過,通常不再符合這裡的「0-day」資格——唯一允許的例外是你同時也依規則要求把它報給了 `security@kernel.org`,這是流程本身要求的步驟,不算「已回報給別的管道」。
- **提交時段與頻率限制**:查證時規則對每個研究者在同一個時段內能用掉的伺服器評估次數有上限(例如每個時段最多 10 次評估),目的是避免有人用暴力嘗試去「賭」穩定性測試的隨機性過關。

### Flag 機制

和傳統 CTF「flag 藏在一個檔案裡,拿到就貼上去」不同,kernelCTF 的 flag 是**伺服器端生成、綁定到你那份提交內容的雜湊**——你把編譯好的 exploit 壓縮檔上傳給評估系統,系統實際跑過、確認它真的拿到 root 之後,才回傳一個編碼了這份檔案雜湊的 flag。這個設計擋掉了一種常見的 CTF 作弊路徑:你**不可能**憑空編一個 flag 交上去,因為 flag 本身就是「你的檔案真的被跑過一次」的密碼學證明,拿不到就是還沒讓系統真的吃過你的 exploit。

### 為什麼獎金這樣設計

查證當下的獎金是**單一固定金額 $71,337**(有趣的數字選擇延續了 Google VRP 一路以來喜歡用「leet」風格數字的傳統),拆成兩半分階段發放,且不再有歷史上「穩定性加成」「0-day 加成」這類疊加獎金——這和前面講的「目標環境已簡化成單一 Hardened target」是同一次改版的一部分:既然目標環境已經統一到最難的那一級,也就不需要用獎金分層去區分「打哪條賽道比較難」。獎金可以在多名研究者之間拆分(團隊合作常見的場景),但每個人都要先在 bughunters.google.com 註冊才能收到付款。

## 為什麼 submission 幾乎都是 static-compiled C

這不是研究者的習慣問題,是規則**結構性**逼出來的結果,三個理由疊加:

1. **伺服器不信任你的建置環境**。評估系統收到的是一個**已經編譯好的二進位檔**,直接執行,不會替你重新編譯——如果你交一段 Python 腳本,伺服器要幫你準備一個帶著正確 Python 版本、正確第三方套件的執行環境,這本身就是一個攻擊面(任意程式碼在評估伺服器上跑,還能挾帶套件依賴),規則直接把這條路堵死。
2. **穩定性測試要求極致的執行可控性**。pwntools 這類工具背後依賴大量 Python runtime 行為(垃圾回收時機、socket buffering、signal handling)在高頻率重複執行(20 次,每次都要全新啟動一個 exploit 流程)下,這些不可控因子會直接吃掉你的穩定性分數——一個 static 編譯、不依賴動態連結庫版本差異的 C 二進位檔,執行行為最接近「每次都一樣」。
3. **exploit 本身大量直接操作 syscall、namespace、ptrace 這類需要精準控制的底層介面**。[Ch 36](./36-user-namespace.md) 的 user namespace 操作、[Ch 45](./45-exploit-stabilization.md) 的 race retry 邏輯,都需要對 syscall 回傳值、errno、timing 做極細粒度的控制——pwntools 這類為「連一個 remote pwn 服務、收發 payload」設計的工具,在這個場景裡完全用不上,直接用 C 寫 raw syscall 反而更直覺。

一個合格的 submission 入口大致要處理這類命令列介面(以下是骨架示意,**未實測,理論預期行為**——實際的 flag 解析邏輯要看你自己的 exploit 需要哪些步驟):

```c
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
        int vuln_trigger_only = 0;

        for (int i = 1; i < argc; i++) {
                if (strcmp(argv[i], "--vuln-trigger") == 0)
                        vuln_trigger_only = 1;
        }

        trigger_vulnerability();            /* 不管哪種模式都要先踩到漏洞 */

        if (vuln_trigger_only) {
                /* 只需要證明漏洞還活著、能讓系統出現可觀察的異常即可返回,
                   不需要跑完整提權鏈——這是 vuln-test 模式的用途 */
                return 0;
        }

        establish_kernel_rw_primitive();     /* 完整模式才繼續往下打 */
        escalate_to_root();
        spawn_root_shell();
        return 0;
}
```

這段骨架的重點不是程式碼本身,是它體現的設計要求：**同一份 exploit 要同時支援「只驗證漏洞新鮮度」和「完整提權」兩種模式**,而不是寫兩份獨立的程式——這也呼應為什麼規則特別要求 `--vuln-trigger` 必須是同一支 exploit 的一個選項,而不是另外交一份單獨的 PoC。

## 對比與取捨

| 維度 | 傳統 CTF kernel pwn 題 | kernelCTF |
|---|---|---|
| 漏洞來源 | 出題者人工埋入 | 真實上游 kernel 的 0-day/尚未公開 N-day |
| 目標環境 | 單次題目自訂,解完就結束 | 持續運行、版本持續更新的真實目標 |
| 驗收標準 | 拿到一次 flag 就算解出 | 要求高成功率(查證時 70%/20 次)、有時間上限 |
| 交付物 | 通常只要 exploit 腳本 | exploit + root cause 分析 + exploit 技術文件,三件缺一不可 |
| 語言彈性 | Python/pwntools 很常見 | 幾乎強制是預先編譯的 C 二進位檔 |
| 回報義務 | 無(題目本身不是真實漏洞) | 強制在 7 天內回報 `security@kernel.org`,促成真實修補 |
| 學到的技能遷移性 | 熟練個別利用技術 | 直接對應真實世界漏洞研究者的完整工作流 |

再往外比較,kernelCTF 和其他知名的漏洞獎勵/競賽形式也不是同一種動物,放在一起對照能看得更清楚各自的定位：

| 維度 | 一般廠商 bug bounty | Pwn2Own 類競賽 | kernelCTF |
|---|---|---|---|
| 時間限制 | 通常沒有嚴格時限 | 現場限時(常見數十分鐘到數小時) | 長期運行,但單一漏洞有明確的回報/公開時限 |
| 驗收方式 | 廠商人工審核報告 | 現場評審即時見證一次成功攻擊 | 伺服器自動化、要求多次重複驗證成功率 |
| 目標穩定性 | 不特別要求 | 通常只需要現場成功一次 | 明確要求查證時 70%/20 次的門檫 |
| 典型產出格式 | 文字報告 | 現場演示 + 事後報告 | 可重現編譯、可直接執行的完整 exploit 原始碼 |

把這張表和前一張放在一起看,能看出 kernelCTF 刻意站在「比賽現場演示」和「純文字報告」兩個極端的中間——它要的不是「表演一次就好」,也不是「光靠嘴巴描述嚴重性」,而是「交出一份能被重複驗證、能被完整復現的工程成品」。這正是它被本書選為 capstone 的理由。

## 踩雷集錦

1. **錯誤直覺：「我讀過的某篇 writeup 說這是打 Mitigation instance,我照著打新目標應該一樣。」** 正確認識：賽制細節會改版,舊 writeup 裡提到的「LTS/COS/Mitigation」這套語言,在查證當下已經被單一 Hardened 目標取代——讀歷史 writeup 時要先確認它寫作的時間點對應哪一代規則,環境的 kernel config、啟用的緩解機制可能完全不同,不能直接照搬 exploit 細節到新目標上。

2. **錯誤直覺：「只要我能打穿一次,flag 就該是我的。」** 正確認識：kernelCTF 要求的是**可重複的高成功率**(查證時 70%/20 次,且有單次執行時間上限)——一個只能靠運氣打穿一次的 exploit,完全不符合提交門檫。這直接呼應 [Ch 41](./41-widening-race-window.md)/[Ch 45](./45-exploit-stabilization.md) 教的那些「拉長 race window」「exploit 穩定化」技巧的真實價值——它們不是加分項,是能不能及格的硬門檫。

3. **錯誤直覺：「我可以用 Python 快速寫一版 PoC 交上去,反正邏輯對就好。」** 正確認識：評估伺服器要求的是**預先編譯好、可直接執行的二進位檔**,而且規則明確要求用 C(通常是單一 `.c` 檔,也可拆成多檔),這不是風格建議是硬性規定——你用別的語言寫完 PoC 驗證邏輯沒問題之後,還是得重寫或移植成 C。

4. **錯誤直覺：「`--vuln-trigger` 模式能跑通,代表我的漏洞和完整提權鏈都沒問題。」** 正確認識：這個模式查證時的定位只是「驗證漏洞在舊版本上依然新鮮、能讓系統當掉」的快速檢查,**不要求也不代表**完整的提權鏈已經可行——完整鏈的穩定性是另一套獨立的評分,兩者不能互相替代。

5. **錯誤直覺：「只要我沒公開 PoC,這個漏洞永遠算我的 0-day。」** 正確認識：規則要求提交的必須是「0-day」——沒有上游補丁、沒有公開揭露,但這個狀態有明確的時間壓力：贏得時段後 7 天內必須回報給 `security@kernel.org`,之後補丁一旦發出,你有 90 天完成完整 PR 並公開技術細節——拖過這些期限,或者讓同一個根因被別人先一步公開/修補,你的提交資格就會出問題(規則明確排除「同一根因的重複修補」這類情況算合格提交)。

6. **錯誤直覺：「我先在 Pwn2Own 之類的比賽上賣這個漏洞,打完之後再拿同一個洞去交 kernelCTF。」** 正確認識：規則明確排除「已經回報給其他獎勵管道」的漏洞——kernelCTF 要的是「這條流程本身促成了修補」,如果這個洞已經透過別的管道走完揭露流程,它對 kernelCTF 而言就不再是合格的 0-day 提交對象,兩邊的獎金不能疊加拿。

7. **錯誤直覺：「找到 0-day 之後,我可以慢慢打磨 exploit,反正只要補丁沒出就一直是我的。」** 正確認識：贏得某個提交時段的資格後,規則要求 **7 天內**必須把漏洞回報給 `security@kernel.org`——這個時限不是「exploit 要在 7 天內做完」,是「回報漏洞本身」要在 7 天內完成,打磨 exploit 到可提交的穩定度,是補丁發出後額外給的 90 天視窗——把這兩個時限搞混,容易誤判自己還有多少時間可以拖。

## 進階：再往深一層

- **kvmctf**：Google 同一套理念延伸到 KVM hypervisor 的姐妹計畫,目標是 guest-to-host 的虛擬化逃逸——如果你對 [Ch 47](./47-container-vm-escape.md) 的虛擬化逃逸議題有興趣,kvmctf 的 rules 頁面(`google.github.io/security-research/kvmctf/rules`)值得對照讀,看看同一套「可重複驗證」的哲學在不同攻擊面上怎麼變形。
- **讀 metadata.json schema 的演進歷史**:這份 schema 從 v1 到查證時的 v3,每次改版反映的是 Google 內部想從 submission 裡多萃取什麼結構化資訊(例如更精確地標注漏洞類型、CWE 分類)——比對不同版本的 schema,能看出整個計畫想往什麼方向演進。
- **評分公式的最佳化空間**:查證時的公式是 `2 × 穩定性% + max(100 − 平均秒數, 0)`,意味著「把平均耗時壓到 0 秒附近」和「把穩定性推到 100%」兩者對分數的邊際貢獻不是線性等價的(穩定性每多 1% 貢獻固定的 2 分;耗時只要壓到 100 秒以內,每少 1 秒才多 1 分,超過 100 秒之後再怎麼慢都不再扣分)——這是個值得你自己動手驗證的小數學題:在固定的工程投入下,先衝穩定性還是先衝速度,邊際效益更高?
- **減少攻擊面本身也是一種「加固」**:查證時的 Hardened 目標直接關掉 `IO_URING`/`USER_NS` 等整個子系統,而不是單純疊加 mitigation——這代表 Google 選擇的加固策略之一是「攻擊面能關就直接關」,不只是「開著但加更多檢查」。對照 [Ch 36](./36-user-namespace.md) 講的 user namespace 怎麼放大攻擊面,再看這個關閉決策,能體會到「attack surface reduction」和「exploit mitigation」是兩條互補但不同的防禦思路,[Ch 51](./51-defense-detection.md) 會把這條思路講完整。
- **留意 style guide**:repo 裡附的 [style guide](https://google.github.io/security-research/kernelctf/style_guide.html) 規定了 `docs/vulnerability.md`/`docs/exploit.md` 該有的寫法與用詞慣例——即使你暫時沒有打算真的提交,照著這份風格指南練習寫一次漏洞分析報告,是訓練「把一個漏洞的技術細節講清楚給審查者看」這個能力的捷徑,這個能力和寫 exploit 本身一樣重要,卻很少有教材單獨練。

## 動手練習

1. 打開 [kernelCTF submission repo](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf),挑三篇不同年份的 writeup,分別記下它們對應的目標環境名稱(可能是舊的 LTS/COS/Mitigation,也可能是新的 Hardened)——確認你能從檔案結構和 `metadata.json` 認出它們對應哪一代規則。
2. 讀其中一篇的 `docs/vulnerability.md` 和 `docs/exploit.md`,畫出這篇漏洞從「root cause」到「最終提權原語」的完整鏈路圖(用你在 [Ch 10](./10-vulnerability-taxonomy.md) 學到的漏洞分類詞彙標注每一步)。
3. 讀當下官方 rules 頁面(不要只看本章轉述的版本),列出三個本章沒提到、但你覺得對實際提交很重要的細節——這個練習的目的是逼你習慣「規則會變,你要自己去讀權威來源」這個現實,而不是依賴任何一本書(包括這本)的轉述。
4. 照著 [style guide](https://google.github.io/security-research/kernelctf/style_guide.html) 的格式要求,為你在 [練習 F](./practice-f-race-exploit.md) 或任何一個你已經寫過的 vulnerable module exploit,試寫一份 `docs/vulnerability.md` 草稿(不需要真的提交,目的是練習把「漏洞成因」用規範化的格式講清楚)——比較看看這份草稿和你原本習慣寫的「解題心得」式 writeup,差在哪裡。

## 本章重點整理

- kernelCTF 是把「真實 kernel 0-day 的漏洞獎勵」包裝成「打穿一個活著的伺服器拿 flag」的長期運行計畫,核心價值在於用**可自動化驗證的成功率**取代主觀的漏洞嚴重性評估。
- 目標環境的分層結構會隨規則改版(本章查證時從歷史的 LTS/COS/Mitigation 三賽道,簡化成單一 Hardened 目標)——讀歷史文獻時要先確認寫作時間點對應哪一代規則。
- Submission 要求遠不只是「exploit 能跑」:穩定性門檫(查證時 70%/20 次)、漏洞成因分析文件、利用手法文件,三者缺一不可,且有明確的回報與公開時限。
- static-compiled C 幾乎是強制要求,根源是伺服器端「不重新編譯、直接執行」的評估模式,加上穩定性測試對執行可控性的極致要求。
- 具體數字(獎金、門檫)會變,但「要求可重複驗證、強制促成真實修補」這個設計哲學,是理解這個賽制的穩定核心。

## 自我檢核

- [ ] 不看筆記,能解釋 kernelCTF 和傳統 CTF kernel pwn 題在「漏洞來源」與「驗收標準」上的根本差異
- [ ] 能說出為什麼 kernelCTF 的 submission 幾乎都是 static-compiled C,而不是「因為規則隨便規定」
- [ ] 能解釋「先回報 7 天內、補丁後 90 天內公開」這個兩階段流程,分別在保護什麼、鼓勵什麼
- [ ] 如果有人問你「kernelCTF 的 LTS/COS/Mitigation 賽道是什麼」,你能說出這是歷史結構、現狀是什麼、以及去哪裡確認當下真正的規則

## 延伸閱讀

### 官方文件 / 權威來源

- **[kernelCTF rules](https://google.github.io/security-research/kernelctf/rules.html)**
  - **讀哪裡**：整頁,特別是 Target、Requirements、Reward、Submission Process 幾節
  - **和本章的關聯**：本章幾乎每個數字與流程細節都來自這份文件在查證時間點的內容——**這是本章存在的唯一理由不是取代它、而是幫你讀懂它**,任何本章轉述和這份文件當下內容不一致時,以這份文件為準
  - **前提知識**：無,但讀之前建議先讀完本章一次,熟悉本章給的詞彙框架,再去對照官方文字會更快抓到重點

- **[kernelCTF FAQ](https://google.github.io/security-research/kernelctf/faq.html)**
  - **讀哪裡**：整頁都短,一次讀完
  - **和本章的關聯**：補充本章沒展開的邊角案例問答(例如團隊合作怎麼分錢、同一漏洞被多人獨立發現怎麼處理)

- **["Learnings from kCTF VRP's 42nd Linux kernel exploit submission"](https://security.googleblog.com/2023/06/learnings-from-kctf-vrps-42-linux.html)** — Google Security Blog（2023）
  - **這篇說什麼**：Google 官方回顧 kernelCTF 運行一段時間後,從大量真實提交裡觀察到的攻擊面趨勢與緩解機制演進——是理解「為什麼 netfilter/io_uring 會成為 kernelCTF 最大礦區」([Ch 30](./30-netfilter-nftables.md)、[Ch 31](./31-io-uring.md))的第一手官方視角
  - **讀哪裡**：整篇,特別是統計攻擊面分布的段落
  - **前提知識**：建議先讀完 [Ch 10](./10-vulnerability-taxonomy.md) 的漏洞分類學,再讀這篇統計時才看得懂分類依據

- **[kernelCTF style guide](https://google.github.io/security-research/kernelctf/style_guide.html)**
  - **讀哪裡**：整頁,特別是 `docs/vulnerability.md`/`docs/exploit.md` 該有的章節結構範例
  - **和本章的關聯**：本章「PR 的檔案結構」一節只講了骨架,這份文件是實際動筆寫這兩份文件時真正要對照的規範——動手練習第 4 題直接用得上

### 延伸比較

- **[kvmctf rules](https://google.github.io/security-research/kvmctf/rules.html)**
  - **讀哪裡**：和 kernelCTF rules 對照著讀,特別是 Target 與 Reward 兩節的差異
  - **和本章的關聯**：同一套設計哲學應用在 KVM hypervisor 攻擊面上的變形,幫助你確認本章歸納的「kernelCTF 設計哲學」是否真的是通用原則,還是只是這一個計畫的特例

- **["Trick or treat: Paying leets and sweets for Linux 0-days"](https://security.googleblog.com/2021/11/trick-treat-paying-leets-and-sweets-for.html)** 與 **["Roses are red, violets are blue, giving out $50k today, if you know what to do"](https://security.googleblog.com/2022/02/roses-are-red-violets-are-blue-giving.html)** — Google Security Blog（2021、2022）
  - **這篇說什麼**：這兩篇是 kernelCTF 前身 kCTF 在擴大獎金規模與目標範圍過程中的官方公告,記錄了「為什麼要把獎金一路加碼」的思考過程(例如意識到低獎金吸引不到頂尖研究者去啃真正難打的漏洞)
  - **讀哪裡**：整篇都短
  - **為什麼值得讀**：幫助理解本章「從 kCTF 到 kernelCTF」一節提到的歷史演進,不是靜態事實,而是 Google 一路根據實際投稿品質調整策略的過程——前提知識：無,但建議先讀完本章一次再看,會更容易注意到哪些機制從那時延續到現在

讀懂了賽制,下一章把視角收回到技術本身——給你一個真實 patch,從讀懂它修了什麼、到寫出一個能交去 kernelCTF 的 exploit,完整走一遍 N-day 流程。

→ [Ch 50 從 patch 到 exploit：N-day 完整 walkthrough](./50-patch-to-exploit.md)
