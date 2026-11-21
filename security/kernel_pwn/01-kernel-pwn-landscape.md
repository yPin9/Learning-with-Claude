# Ch 1 — kernel pwn 全景：ring 0/3、威脅模型、攻擊者視角地圖

> **目標**：建立一張「攻擊者怎麼想」的整體地圖——理解 ring 0/3 的權限模型、為什麼 kernel bug 幾乎等於 game over、kernel pwn 的威脅模型落在哪裡，以及本書後續各個 Part 的技術分別對應地圖上哪一段。這章沒有 code，是全書的導航。

## 為什麼需要這一章？

User-space pwn 的目標通常很具體：某個 binary 跑在某個 port 上，吃一個 overflow，拿到 shell。Kernel pwn 的目標模糊得多——「拿到 root」只是地圖上的一個終點，中間要經過好幾段完全不同性質的工作，而不先看清楚整張地圖，初學者常見的卡點是：讀完一個 UAF 利用的 writeup，看懂了每一行，卻不知道**為什麼**作者在這個時間點選擇噴這個物件、劫這個函式指標——因為沒有「這是地圖上第幾步、要解決什麼子問題」的框架。

這章要建立的，就是這個框架。讀完之後，你看任何一篇 kernel exploit writeup，都能先問：「這一步在地圖上對應哪個階段？」

## 先建立直覺：特權環（ring）是什麼

x86 CPU 從 80286 時代開始就有「特權等級」（privilege level）的硬體機制，叫 **ring**，編號 0 到 3，數字越小權限越高：

```
           ring 3 ────────────────────────────┐
          （user mode）                        │
    你平常跑的所有程式：                        │
    浏览器、bash、你写的 exploit                │
                                               │
          ──────── syscall / 中斷 ────────────┤  只有這幾個「門」
                                               │  能從外圈進到內圈
           ring 0 ────────────────────────────┘
          （kernel mode）
    kernel 本身：排程器、記憶體管理、
    檔案系統、網路 stack、所有 driver
```

現代作業系統實務上只用兩圈——ring 0（kernel）和 ring 3（user）。ring 1、2 歷史上是給「比 OS 低、比應用程式高」的中間層（例如某些微核心設計、或者 virtualization 的 guest OS），x86-64 上的 Linux/Windows 都沒用到它們（Intel VT-x 之後，hypervisor 另外用了一個叫 ring -1 的非正式說法，這超出本書範圍，Ch 47 會稍微帶到）。

**關鍵心智模型**：ring 不是「軟體層面的權限檢查」（像 Unix 的 UID 0 vs 非 0），而是**CPU 硬體**直接執行的規則。在 ring 3 執行的程式碼，CPU 從硬體層面直接拒絕它存取某些記憶體、執行某些指令（像 `wrmsr` 寫 model-specific register、或者 `lgdt` 改 descriptor table）。你在 user-space 寫任何 C 程式，CPU 看到你試圖碰這些東西，直接 fault，不管你的程式裡有沒有檢查。

反過來，在 ring 0 執行的程式碼（也就是 kernel），這些硬體限制全部解除。Kernel 想讀任何一個 process 的記憶體、想改任何一個 page table、想執行任何指令——CPU 不會阻止它。**Kernel 的安全性完全建立在「kernel 自己的程式碼邏輯寫得對」這個前提上。**

這就是為什麼 kernel bug 的殺傷力和 user-space bug 不是一個量級——在 user-space，一個程式的 bug 最多搞爆這個程式自己的位址空間（現代 OS 的 process isolation 靠 ring 這個硬體機制保證）；在 kernel-space,一個 bug 搞爆的是**整台機器的信任基礎**。

## 從 Unix 權限模型過渡到 ring 的關係

容易混的兩層權限模型要分清楚：

| 層次 | 機制 | 例子 |
|---|---|---|
| **軟體層（Unix 權限）** | UID/GID、capability、SELinux/AppArmor | 「這個 process 是不是 root (UID 0)」「有沒有 `CAP_SYS_ADMIN`」 |
| **硬體層（CPU ring）** | x86 特權等級 | 「這段程式碼在 ring 0 還是 ring 3 執行」 |

一個以 UID 0（root）跑的 process，依然是在 **ring 3** 執行——它只是在 Unix 權限模型裡被標記成「幾乎什麼都能做」，但它的程式碼本身依然受硬體 ring 限制：想直接讀其他 process 的記憶體、想繞過 kernel 自己改 page table，一樣不行，一樣要靠 `ptrace`、`/proc/<pid>/mem` 之類**由 kernel 提供並檢查過權限的介面**才能做到。

這解釋了為什麼「kernel LPE（local privilege escalation，本機提權）」是獨立於「root 帳號」的攻擊面：就算系統設計得再嚴，不給任何 user 做 root，只要有一個**普通、無特權的 user**能觸發 kernel 裡一個漏洞，他就有辦法讓**kernel 自己**（在 ring 0 執行、不受任何限制）替他做任何事——包括把自己的 process 的 UID 改成 0，或者直接竄改 kernel 的記憶體跳過所有檢查。這正是本書絕大多數技術最終要抵達的終點：commit_creds(prepare_kernel_cred(0))（Ch 12 細講），本質就是「讓 kernel 自己把你 promote 成 root」。

## 為什麼 kernel bug = game over：繞過 sandbox、container、EDR

現代系統疊了很多層隔離，表面上每一層都在「限制一個不信任的程式能做什麼」：

```
   不信任的程式碼
        │
        ▼
┌──────────────────┐
│ seccomp-bpf       │  限制它能呼叫哪些 syscall
├──────────────────┤
│ container         │  限制它看到的 mount namespace、
│ (namespace +      │  pid namespace、network namespace……
│  cgroup)          │
├──────────────────┤
│ EDR / LSM hook    │  監控/攔截可疑行為
│ (SELinux/AppArmor)│
├──────────────────┤
│ 普通 Unix 權限     │  UID/GID/capability
└──────────────────┘
        │
        ▼
   這些全部是 ring 3 軟體層的隔離機制,
   全部靠 kernel 正確實作它們來維持。
```

這四層全部有一個共同前提：**kernel 自己的程式碼邏輯是對的**。Namespace 隔離要靠 kernel 正確區分「這個 process 屬於哪個 namespace」；seccomp 要靠 kernel 正確過濾 syscall；LSM hook 要靠 kernel 在正確的時間點呼叫到正確的檢查函式。如果你能找到 kernel 裡一個記憶體損壞漏洞，把它變成「讓 kernel 執行你選的程式碼」或「讓 kernel 寫你選的資料到你選的位置」，上面四層隔離**全部**同時失效——不是因為你一層一層繞過它們，而是因為你繞到了它們全部共同依賴的那個地基本身。

這也是為什麼雲端供應商、container 平台、瀏覽器沙箱廠商會花巨大資源做 kernel fuzzing（Syzkaller，Ch 43）與 kernel hardening（KSPP，Ch 26/51）——對他們來說，kernel 漏洞不是「某一個應用層的漏洞」，是「整個隔離模型的單點故障」。

## 威脅模型：Local LPE 為主，Remote 次之

Kernel pwn 的威脅模型和 user-space pwn 的「網路服務可以被任何人連」不一樣,主要分兩類：

### Local LPE（本機提權）——本書的主戰場

**前提**：攻擊者已經能在機器上執行**某種程度受限**的程式碼——可能是一個普通 user 帳號的 shell、一個 container 裡的程式、一個瀏覽器 renderer process（已經被瀏覽器自己的 user-space 漏洞攻破，現在想逃出瀏覽器沙箱）。

**目標**：從這個受限環境，利用 kernel 漏洞，拿到比目前權限更高的能力——可能是 root、可能是逃出 container、可能是逃出 seccomp 的限制。

**為什麼這是主戰場**：kernel 的攻擊面（各種 syscall、ioctl、netlink、檢查邏輯）絕大多數**只能從已經在機器上執行的程式碼觸發**。這也是本書幾乎所有 vulnerable module 範例的設計：寫一個 kernel module，開一個 `/dev/xxx`，普通 user 用 `open()` + `ioctl()` 就能跟它互動——完全對應真實世界「本機某個 driver/子系統有漏洞，本機某個低權限 process 能碰到它」的場景。

**kernelCTF**（Ch 49 細講）的題型清一色是這個模型：給你一個跑著目標 kernel 的機器，給你一個普通 user 帳號，目標是拿到 root。

### Remote（遠端攻擊面）——存在但少見，本書點到為止

理論上，如果一台機器開著某個**完全不需要認證就能觸發**的 kernel 攻擊面，攻擊者可以不需要先有本機執行權限。常見例子：

- 網路 stack 本身的漏洞（TCP/IP 處理、netfilter 規則處理）
- 某些檔案系統在處理掛載的不可信映像檔時的漏洞（Ch 34 會談，USB 隨身碟、掛載他人上傳的檔案系統映像都算這類場景）
- 藍牙/Wi-Fi 等無線協定 stack 的漏洞

這類攻擊面存在，但相對稀少、相對難打（網路封包的資料結構通常比 ioctl 傳進來的資料結構更受限、更難精確控制），本書 Part 5（子系統攻擊面）會在談到 netfilter、socket/skb 時點出哪些部分理論上有遠端面，但**全書的 exploit 練習全部走 local 觸發路徑**——這也是為什麼全書假設你有一個本機 shell 可以 `insmod`、`open`、`ioctl` 自己的 vulnerable module。

## 攻擊者視角地圖：從一個 bug 走到穩定 root shell

這是全書真正的核心骨架，把它記熟：

```
┌──────────────┐
│  1. Bug       │  某段 kernel 程式碼邏輯錯了
│  原始漏洞      │  (OOB write / UAF / race / 整數溢位……)
└──────┬───────┘
       │ 利用這個邏輯錯誤,拿到一個「原語」
       ▼
┌──────────────┐
│  2. Primitive │  把 bug 轉成可重複利用的能力
│  利用原語      │  常見原語："任意位址讀/寫"、
│               │  "控制某個 function pointer"、
│               │  "讓某塊記憶體被釋放兩次"
└──────┬───────┘
       │
       ├─────────────────┐
       ▼                  ▼
┌──────────────┐   ┌──────────────┐
│ 3a. Info Leak │   │ 3b. Control/  │
│   取得 kernel  │   │   Data 攻擈   │
│   位址/敏感資料 │   │  劫 RIP 或    │
│  (繞 KASLR)   │   │  改關鍵資料結構│
└──────┬───────┘   └──────┬───────┘
       │                   │
       └─────────┬─────────┘
                  ▼
         ┌──────────────┐
         │ 4. Privesc    │  執行目標:
         │   實際提權     │  commit_creds(prepare_kernel_cred(0))
         │               │  或直接改 cred 結構裡的 uid/gid
         └──────┬───────┘
                  ▼
         ┌──────────────┐
         │ 5. Cleanup /  │  修復被破壞的 kernel 狀態、
         │   穩定性       │  安全返回 userland、避免之後 panic
         └──────────────┘
```

逐段解釋每一步在做什麼、常見手法、對應本書哪個 Part：

### 1. Bug（原始漏洞）

這是一切的起點——某段 kernel 程式碼的邏輯錯誤。Ch 10（漏洞類型學）會把常見的漏洞類型系統性分類：OOB read/write、UAF/double-free、type confusion、refcount 錯誤、整數溢位、race condition（TOCTOU、double-fetch）。不同類型的 bug 能直接給你的「原始能力」差很多——一個 stack overflow 可能直接讓你覆寫 return address；一個 UAF 本身什麼都沒覆寫，但讓你有機會讓另一個無關的物件「誤用」已經被釋放的記憶體。

### 2. Primitive（利用原語）

多數真實世界的 bug 給你的能力都很「原始」、很受限——例如「可以把某塊已經被釋放的 8 bytes 物件改寫成任意值,但只能改一次,而且不知道這塊記憶體現在被誰在用」。**利用工程的大半工作，是把這種受限的原始能力，轉換成更通用、更可控的原語**，常見的目標原語：

- **任意位址讀（AAR, arbitrary address read）**：給一個位址，能讀出那裡的內容。
- **任意位址寫（AAW, arbitrary address write）**：給一個位址和一個值，能把值寫進那裡。
- **控制 function pointer / 控制流**：讓某個之後會被呼叫的函式指標，變成攻擊者選的位址。
- **物件混淆（type confusion）之後的欄位控制**：讓 kernel 把一塊記憶體當成 A 型別讀寫，但它實際上被另一段程式碼當成 B 型別配置與初始化。

Part 3（heap 利用核心）大部分章節在教的,正是「怎麼把一個 UAF/overflow 轉換成上面這幾種原語之一」,透過 heap spray（Ch 19）、heap grooming（Ch 20）精確控制哪個物件落在被覆寫的那塊記憶體上。

### 3a / 3b. Info Leak 與 Control/Data 攻擊

拿到原語之後，通常要做兩件並行的事：

- **Info leak**：現代 kernel 開著 KASLR（Ch 14），你不知道任何 kernel 函式/資料結構實際在哪個位址——這對「劫控制流去跳到某個 kernel 函式」是硬性阻礙，必須先洩漏出至少一個 kernel 位址才能算出 KASLR 的隨機位移（slide）。
- **Control 攻擊**：傳統路線，劫 RIP（直接控制 CPU 接下來執行的指令），走 ROP（因為 SMEP 擋死了「跳去執行 user-space 程式碼」這條更簡單的路，Ch 13）。
- **Data-only 攻擊**：Part 4 後半的主題——不劫任何控制流，純粹改資料（例如直接把某個 `struct cred` 裡的 `uid` 欄位改成 0），在 CFI/KCFI（Ch 28）把控制流劫持越堵越死之後，這條路線的重要性持續上升。

### 4. Privesc（實際提權）

不管走哪條路，最終目標通常收斂到同一組「利用原語」：

- `commit_creds(prepare_kernel_cred(0))`：呼叫這兩個 kernel 函式，把目前 process 的 credential 換成「核心專用、uid/gid 全 0」的 cred——這是 Ch 12 細講的經典提權原語。
- 直接竄改目前 process 的 `struct cred` 裡的欄位（`uid`、`gid`、`cap_effective` 等）——不呼叫任何函式，純資料竄改，對應 Ch 16（經典利用原語）與 Ch 24（Dirty Cred）。
- 竄改其他全域目標達到等效效果：`modprobe_path`（讓 kernel 執行你控制路徑上的程式）、`core_pattern`（crash 時執行你的程式）——這些不直接改 cred，但一樣能繞到「以 root 權限執行任意程式」。

### 5. Cleanup / 穩定性

這一步最常被初學者忽略，卻是 kernelCTF 評分標準明文要求的——exploit 不能只是「這次跑剛好成功」，必須**穩定**成功,而且成功之後要能**安全返回 userland**，不留下一個已經損壞、隨時會 panic 的 kernel 狀態。Ch 45 專門談這個：heap grooming 的穩定化、race retry 策略、怎麼修復被你故意弄壞的資料結構（例如你為了拿 UAF 而釋放了一個物件，拿到想要的效果之後要不要、怎麼把它的引用計數或相關狀態修回一個不會導致之後崩潰的狀態）。

## 地圖不是直線：真實 exploit 經常來回跳

把五步地圖畫成一條直線容易讓人誤會「這是一套固定的流程,照順序走完就結束」。真實情況更接近反覆迭代：拿到一個原語之後去嘗試 info leak，發現這個原語不夠精確、leak 不出需要的東西，退回去重新審視 bug 本身還能不能擠出別的原語；或者 privesc 那一步觸發了一個之前沒注意到的 race window，意外提供了一個更乾淨的 AAW。閱讀 writeup 時常見的「作者嘗試了方法 A，發現某個限制，換成方法 B」的敘事，正是這種來回跳的體現——地圖的五個階段是用來**定位你現在在解決哪一類子問題**，不是用來預期「這次一定會按順序走完」。

## 本書各 Part 落在地圖上的位置

對照上面的五步地圖，本書的 Part 劃分是：

| Part | 章節範圍 | 對應地圖位置 |
|---|---|---|
| Part 0（本 Part） | Ch 0–5 | 環境與基礎,地圖之前的准備 |
| Part 1 | Ch 6–9 | 理解記憶體管理/SLUB，是後面「原語」工程的地基知識 |
| Part 2 | Ch 10–16 | **1. Bug** 分類 + 經典、不靠 heap 的「2→4」直線路徑（stack overflow 直接到 privesc） |
| Part 3 | Ch 17–25 | **2. Primitive**：heap 類 bug 怎麼轉換成可控原語，這是現代 kernel pwn 的主戰場 |
| Part 4 | Ch 26–29 | **3a/3b** 進階：mitigation 更新後 info leak 與 control/data 攻擊怎麼跟著演進 |
| Part 5 | Ch 30–38 | 回到 **1. Bug**，但換成廣度——不同子系統各自的攻擊面長什麼樣 |
| Part 6 | Ch 39–41 | **1. Bug** 的一個特殊類別——race condition，獨立成 Part 因為手法差異大 |
| Part 7 | Ch 42–45 | 怎麼**找到** bug（而不是拿到 bug 之後怎麼打），以及 **5. Cleanup/穩定性** |
| Part 8 | Ch 46–48 | 同一張地圖換到不同架構/場景 |
| Part 9 | Ch 49–51 + Final | 整張地圖在真實賽制裡怎麼跑一遍，以及防禦者視角回頭看這張地圖 |

## Kernel pwn 和其他 pwn 子領域的關係

值得花一點篇幅把 kernel pwn 放進整個 binary exploitation 的座標系裡，否則容易把它當成一個孤立的技能樹。

```
                    Binary Exploitation
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   User-space pwn     Kernel pwn       Browser/VM exploitation
   (CTF 裡最常見的    (本書主題)        (JS engine / V8、
    pwn 題型)                            hypervisor escape)
          │                │                │
          └────────┬───────┴────────┬───────┘
                   共同的底層技能:          各自額外的專門知識:
                   - 記憶體佈局直覺         - user: glibc heap、ret2libc
                   - ROP/控制流劫持          - kernel: SLUB、特權環、mitigation
                   - info leak 思維          - browser: GC、JIT、物件模型
                   - 利用鏈的穩定化
```

四個觀察：

1. **底層技能共通，具體知識不共通**。一個熟練的 user-space pwn 手，面對 kernel pwn 題目，直覺（「這裡應該可以 leak 一個位址」「這個 overflow 應該能蓋到關鍵資料」）大部分能轉移過來，但 glibc heap 的具體技巧（tcache poisoning、house of xxx 系列）在 SLUB 上完全用不上，必須重新學一套（這正是 Part 1 整個存在的理由）。
2. **瀏覽器/VM exploitation 和 kernel pwn 共享「attacker 已經在某個受限 sandbox 裡,目標是逃出去」的結構**，但攻擊對象的型別系統、記憶體管理機制完全不同（JS engine 面對的是高度動態、JIT 編譯的物件模型；kernel 面對的是 C 語言的靜態結構與 SLUB）。本書不涵蓋瀏覽器/VM exploitation，但 Ch 47（容器/虛擬化逃逸邊緣）會簡短帶到 hypervisor escape 和本書技術的交集。
3. **CTF 分類習慣把 kernel pwn 單獨列成一個類別**（常見賽制會標 "pwn - kernel" 或獨立的 "kernel exploitation" 分類），因為題目的技能需求、環境搭建成本（需要一整套 QEMU 環境,不是單純一個 binary）都和一般 user-space pwn 題差異夠大。
4. **kernelCTF 是這個領域的特殊案例**：它不是一次性比賽，是 Google 長期維運的賞金計畫，目標直接是「打穿公開的、真實發行版在跑的 kernel 版本」，獎金依打穿的 kernel 版本新舊與是否需要 0-day 分級。Ch 49 會詳細介紹賽制，這裡先知道它存在、和一般 CTF 一次性比賽的性質不同。

## Remote 攻擊面的具體例子（加深威脅模型的理解）

前面提到 remote 攻擊面存在但稀少，這裡舉兩個具體、真實發生過的類別，讓這個說法不是空話：

- **網路 stack 本身**：TCP/IP 封包的解析、重組邏輯本身跑在 kernel 裡，理論上一台機器只要開著網路介面就暴露了這段程式碼，不需要攻擊者有任何本機帳號。歷史上這類漏洞存在但數量相對少——網路協定的資料結構遠比 ioctl 傳進來的任意結構受限（封包格式要符合協定規範才能被正常解析到足夠深的程式碼路徑），這天然限制了攻擊面的複雜度。
- **處理不可信的檔案系統映像**：掛載一個來路不明的 USB 隨身碟、或者在某些雲端場景掛載使用者上傳的磁碟映像，都會觸發 kernel 的檔案系統 parser（ext4、f2fs、ntfs3……）去解析完全不受信任的資料——這類攻擊面理論上也不需要攻擊者先有本機執行權限（只需要讓目標系統掛載惡意映像），Ch 34 會展開。

這兩類都存在於 Part 5 的廣度覆蓋範圍內，但本書的**動手練習**全部走 local 觸發路徑——原因在於教學上 local 路徑的因果關係更直接（你自己 `ioctl` 自己的漏洞,不需要先搭一套網路/掛載情境），而真正理解了 local 路徑的利用技術後，remote 攻擊面的差異主要在「怎麼把資料送進 kernel」這一步，後面的利用原語工程是共通的。

## 踩雷集錦

1. **錯誤直覺：「kernel exploit 就是找一個大洞，一次性把 shellcode 塞進去跑。」** 正確認識：絕大多數現代 kernel exploit 是上面五步地圖**依序疊加**的工程，一個 bug 很少直接給你 root——它給你一個很受限的原語，你要花更多步驟把它升級。看一篇 kernelCTF submission，常常原始 bug 只佔全文 10%，剩下 90% 是怎麼從那個原語走到穩定 root。

2. **錯誤直覺：「只要普通 user 不是 root，系統就安全。」** 正確認識：Unix 權限（UID）和 CPU ring 是兩層不同的防線,kernel LPE 攻擊的正是「我是普通 user，但我能讓 kernel（在 ring 0、不受任何 UID 檢查限制）替我做事」這條路。Container、沙箱同理——它們提供的隔離只在「kernel 正確實作它們」的前提下有效。

3. **錯誤直覺：「kernel pwn 的威脅模型跟 web/網路服務 pwn 一樣，隨便一個遠端連線就能打。」** 正確認識：kernel 攻擊面中能被完全未認證的遠端連線觸發的部分相對少（網路 stack、特定 driver），本書與絕大多數 kernelCTF 題型假設的是「已經有某種受限本機執行能力」的 local LPE 模型——這個假設本身就是威脅模型的一部分，理解它才知道一個 kernel 漏洞在真實世界裡「打誰」、CVSS 評分為什麼常常不是「Network」而是「Local」。

4. **錯誤直覺：「現代 mitigation（SMEP/SMAP/KASLR/KPTI/CFI）這麼多層，kernel exploit 已經快打不動了。」** 正確認識：每一層 mitigation 確實關閉了一類手法（SMEP 關了 ret2usr 那種「跳回 user-space 執行 shellcode」的捷徑，CFI 大幅收窄了 ROP 的空間），但攻擊面的演進方向從來是「換一種原語」而不是「放棄」——data-only attack（Part 4 後半）正是對 CFI 的直接回應。地圖的五個步驟沒有變,每一步「怎麼做到」隨 mitigation 演進，這正是本書要教的內容。

## 進階：再往深一層

- **把這張地圖套到一篇真實 writeup 上**：找一篇 Google Project Zero 的 Linux kernel exploit blog post，讀的時候明確標出「這一段對應地圖哪一步」——這個練習比抽象記住地圖本身更有效，建議現在就找一篇試試（即使很多細節現在看不懂，標出五個階段的分界點是可以做到的）。
- **思考地圖之外的場景**：本書主打 local LPE，但同一張地圖也適用於 hypervisor escape（guest kernel bug 打穿 host）、瀏覽器 renderer 到 kernel 的兩階段鏈（先打瀏覽器沙箱,再打 kernel）。Ch 47 會稍微帶到容器/虛擬化逃逸的邊緣案例。

## 動手練習

不涉及程式碼，用筆記完成：

1. 找一篇 kernelCTF submission writeup（<https://github.com/google/security-research/tree/master/pocs/linux/kernelctf>，隨便挑一篇你看得懂開頭的），標出文中對應本章五步地圖的段落邊界（不需要看懂細節，只需要辨認「現在作者在講哪一步」）。
2. 寫一段話（3–5 句），解釋給一個只懂 Unix 權限、不懂 CPU ring 的人聽：「為什麼普通 user 的本機 shell，能透過 kernel bug 變成 root。」

## 本章重點整理

- Ring 是 CPU 硬體層的特權機制，和 Unix 的 UID/GID 軟體層權限是兩個不同的東西；root 帳號跑的程式依然在 ring 3。
- Kernel bug 的殺傷力來自「user-space 所有隔離機制（sandbox、container、EDR）全部共同依賴 kernel 自己邏輯正確」這個前提——打穿地基,上面全部一起塌。
- 本書威脅模型以 local LPE 為主：假設攻擊者已經有某種受限的本機執行能力，目標是利用 kernel 漏洞提升到更高權限。
- 攻擊者視角地圖五步：Bug → Primitive → Info Leak / Control-Data → Privesc → Cleanup/穩定性——本書每個 Part 都能對應到地圖上的位置。

## 自我檢核

- [ ] 不看筆記，能解釋 ring 0/3 和 UID 0/非 0 的差別，以及為什麼兩者不能互相替代
- [ ] 能說出「kernel bug = game over」這個說法的具體機制，不是只會背這句話
- [ ] 能畫出（或口述）攻擊者視角地圖的五個步驟，並各舉一個本書會教的技術當例子
- [ ] 如果有人問你「為什麼不直接從 remote 打 kernel」，你能說出 local LPE 模型為什麼是本書（和多數 kernelCTF）的主戰場

## 延伸閱讀

### 書籍

- **《A Guide to Kernel Exploitation: Attacking the Core》** — Enrico Perla & Massimiliano Oldani（Syngress, 2010）
  - **這本書的定位**：涵蓋到 2.6 kernel 時代，技術細節大多過時（那時沒有 SMEP/SMAP/KASLR），但攻擊者思維框架——「從 bug 到 root 要走哪些階段」——至今仍是本章地圖的雛形
  - **讀哪幾章**：第 1、2 章（Kernel-Land 101、A Taxonomy of Kernel Vulnerabilities），和本章內容互補，不需要讀到書的利用技術細節部分

### 部落格 / 系列

- **[Google Project Zero blog — Linux kernel 系列文章](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：每篇針對一個真實 CVE 做完整 root cause + exploit 分析,是業界公認最高品質的 writeup 來源
  - **讀哪裡**：先讀任何一篇標題有 "Linux" 或 "kernel" 的文章的前三段（通常是漏洞背景與影響說明),用來練習本章「標出地圖階段」的練習
  - **前提知識**：目前不需要看懂利用細節，只需要能分辨文章在講 bug 本身還是在講利用過程

### 權威來源 / 比賽

- **[kernelCTF submission repo](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf)**
  - **讀哪裡**：隨便開一篇的 README/writeup
  - **和本章的關聯**：本書 Part 9 與 Final Project 直接以這個 repo 為對象；現在先建立「真正的 kernel exploit writeup 長什麼樣」的印象，不需要看懂內容

有了這張地圖，下一步是補上「怎麼讀 kernel 原始碼」這個貫穿全書的方法論——後面每一章都會要求你去 kernel source tree 裡確認某個函式、某個 struct 的真實定義，而不是只信任書上寫的。

→ [Ch 2 讀 kernel 原始碼的方法](./02-reading-kernel-source.md)
