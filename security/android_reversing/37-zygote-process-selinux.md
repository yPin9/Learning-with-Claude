# Ch 37 — Zygote、進程、SELinux 對逆向的影響

> **目標**：把 Ch 3 建立的安全模型鑽到「進程建立」這一層，回答注入/hook/脫殼的三個時機與限制問題：一個 App 進程從 Zygote **fork + specialize** 出來的每個階段各發生什麼、**你的程式碼能在哪個時機切進去**（早注入 vs 晚注入）、`untrusted_app` 這個 SELinux domain 具體卡住你哪些注入/讀寫動作、以及 `app_process` 在這條鏈裡是什麼。搞懂這章，你就知道「為什麼有些 hook 要趕在 App 邏輯跑之前、為什麼 frida-server 放對地方 root 也可能被 SELinux 擋、為什麼 Xposed 選在 Zygote 動手」。

> **環境**：以 **Android 13 / API 33** 為準。Zygote/進程/SELinux 是系統機制，相對穩定，但 `usap` 池、domain transition 時機等**版本間有調整**，涉及處標明。承接 Ch 3（Zygote/沙箱/SELinux 基礎）、連 Ch 36（hook/脫殼要在對的進程時機）。本 repo 沙箱無 Android，涉及裝置行為處標「**未實測，理論預期行為**」並給驗證步驟。

## 為什麼需要這個？

因為你前面學的所有動態手段——Frida attach、Xposed hook、ART entrypoint hook、主動調用脫殼——**都要先「進到目標 App 進程裡」，而且時機決定成敗**。三個具體痛點：

- **有些 hook 必須趕在 App 邏輯跑之前**。App 的反調試檢查、SSL pinning 初始化、加固殼解密，常在 App 一啟動（`Application.onCreate` 甚至更早）就做完。你 attach 晚了，殼早解完、pinning 早裝好，你 hook 到的是「已成定局」的世界。要在**它做這些之前**切進去，你得懂 App 進程從 Zygote 誕生到執行 App 程式碼之間的時間軸。
- **注入為什麼要 root，root 了為什麼還被擋**。Ch 3 講過 DAC/MAC，這章把 `untrusted_app` domain 對「ptrace 別的 App、寫別人的私有目錄、mmap 可執行記憶體」的具體限制講清楚——這決定你的注入手段哪些行、哪些被 SELinux 打回。
- **Xposed/LSPosed 為什麼選 Zygote**。因為在 Zygote fork 之前種下 hook，之後每個 App 都繼承（Ch 3 提過）。這章補上「Zygote 的哪個時機、怎麼種」的細節。

一句話：**這章是把「你的程式碼怎麼、在何時、以什麼身分、被什麼限制著進到目標進程」講透——這是所有動態逆向的物理前提。**

## 先建立直覺：一個 App 進程的誕生時間軸

先給貫穿全章的心智模型——一個 App 進程從被要求啟動到跑起 App 自己的程式碼，中間這條時間軸上有幾個「可注入的窗口」：

```
 使用者點 App / startActivity
   │
   ▼
 system_server 決定要開 com.foo, 透過 socket 叫 Zygote 孵一個
   │
   ▼
 Zygote.fork()  ──────────────────────────────── 窗口 A: fork 前 (Zygote 內)
   │   子進程 COW 繼承 Zygote 全部 (framework 類/runtime 已載好)   Xposed 種 hook 在這
   ▼
 specialize (子進程裡):
   │  setuid(app_uid) 降權  ────────────────────  ← 過了這行你不再是 root
   │  setgid / 設補充 GID (權限→GID, Ch3)
   │  SELinux domain: → untrusted_app  ─────────  ← 過了這行受 App domain 約束
   │  設 seccomp filter
   ▼
 進入 App 進程主體 (ActivityThread.main)
   │
   ▼
 載入 App 的 APK / 建 ClassLoader (Ch35)  ──────  窗口 B: App 類載入前
   │
   ▼
 Application.attachBaseContext / onCreate  ──────  窗口 C: App 邏輯起點
   │   ★ 加固殼解密、反調試、pinning 常在這附近做
   ▼
 App 正常運行  ─────────────────────────────────  窗口 D: attach 一個已跑的 App
```

四個窗口對應四種注入策略：

- **窗口 A（Zygote 內，fork 前）**：hook 種在 Zygote，所有 App 繼承 → **Xposed/LSPosed 的位置**。最早、最全面，但要動 Zygote（要 root + 框架）。
- **窗口 B/C（App 進程早期）**：趕在殼解密/反調試之前 → **`frida -f`（spawn 模式）** 的價值——spawn 後、resume 前你能先裝好 hook。
- **窗口 D（App 已在跑）**：**`frida -U <name>`（attach 模式）** → 晚了，殼/pinning 可能已生效，適合觀察穩定運行的東西。

**「注入時機」不是細節，是能不能繞過某些防護的關鍵。** 這條時間軸是本章的骨架。

## 底層機制：fork + specialize 的每一步

Ch 3 講了「Zygote fork 出降權子孫」的直覺，這章拆 specialize 每一步做什麼、對逆向的意義。

```
 Zygote (root, 預載好 runtime) 收到「孵 com.foo, uid=10123」
   │
   ▼ fork()  ── 子進程誕生, 此刻還是 root, 繼承 Zygote 一切 (COW)
   │
   ▼ ── 以下都在子進程裡跑 (ZygoteInit / SpecializeCommon) ──
   │
   ① 設 SELinux domain (selinuxContext)
   │    → 從 zygote domain 轉成 untrusted_app  (type_transition / setcon)
   │
   ② setgroups / setresgid  ── 設補充 GID (INTERNET→3003 等, Ch3)
   │
   ③ setresuid(10123)  ── ★ 降權! 過這行子進程不再是 root
   │
   ④ 設 seccomp-bpf filter  ── 限制能發哪些 syscall (Ch3 第三層)
   │
   ⑤ 設 capabilities / 其他隔離
   │
   ▼ 進入 App 主體 (RuntimeInit → ActivityThread.main)
```

四個逆向重點：

1. **降權（③）之前子進程是 root**。這是個理論上的注入窗口，但它在 Zygote 的 specialize 內部、外部很難插手——所以實務上早注入靠的是「在 Zygote fork 前種 hook」（窗口 A），而非搶這個 root 窗口。
2. **domain 轉換（①）之後就是 `untrusted_app`**。過了這步，即使進程曾是 root，它的 SELinux domain 已是普通 App，受 App domain 的策略約束。這解釋「App 進程本身沒有超能力」。
3. **seccomp（④）在這裝上**。App 進程能發的 syscall 白名單在此固定。這是為什麼 `adb shell` 跑得動的 native payload 塞進 App 進程可能 `SIGSYS`（Ch 3 場景 4）。
4. **這一整串是「一次性」的**。App 進程建好後這些身分/限制就定了，你之後注入的程式碼**繼承目標進程的身分與 domain**——你的 Frida agent 跑在 `com.foo` 進程裡，它就是 `untrusted_app`、就是 UID 10123、就受同一套 seccomp。

> **未實測，理論預期行為**：specialize 步驟順序取自 AOSP `frameworks/base/core/java/com/android/internal/os/Zygote.java` 與 native 的 `com_android_internal_os_Zygote.cpp`（`SpecializeCommon`）。**步驟細節、`usap` 池（預先 specialize 的進程池，加速啟動）逐版本有調整**。你驗證：`adb shell cat /proc/<app_pid>/status` 看降權後的 UID、`cat /proc/<app_pid>/attr/current` 看 domain 是 `untrusted_app`（Ch 3 練習做過）。

## app_process：Zygote 與你的注入入口

`app_process` 是個常被忽略但關鍵的角色。它是 `/system/bin/app_process`（64 位是 `app_process64`）——**Zygote 本身就是 `app_process` 啟動的**（init 執行 `app_process` 帶 `--zygote` 參數，就成了 Zygote）。

`app_process` 的逆向意義有二：

1. **它能在命令列跑一個「帶 ART runtime 的 Java 進程」**。你 `app_process` 給它一個 class，它能起一個 ART 環境跑那個 class 的 `main`。**Xposed 早期就是替換/包裝 `app_process`**——讓 Zygote 啟動時先跑 Xposed 的初始化，把 hook 框架種進 Zygote（窗口 A）。這是「在 Zygote 動手」的具體入口。
2. **它是理解「App 進程的執行檔是什麼」的答案**。你 `adb shell ps -A` 看到 App 進程名是 `com.foo`，但它的執行檔（`/proc/<pid>/exe`）其實指向 `app_process`——因為 App 進程是 Zygote（=app_process）fork 出來的，共用同一個執行檔映像，只是把進程名改成了 package name。

```
 init ──exec──▶ app_process --zygote --start-system-server
                    │  (成為 Zygote, 預載 runtime)
                    │
                    ▼ fork + specialize
                 com.foo 進程  (exe 仍是 app_process, 名字改成 com.foo)
```

**Xposed 的持久化本質**：包裝 `app_process`（或用 Riru/Zygisk 這類注入 Zygote 的機制），讓每個 fork 出的 App 進程一誕生就帶著 hook 框架。這就是 Ch 3 說的「在 Zygote 層 hook = 一次 hook 所有 App」的落地方式。

## untrusted_app domain：具體卡你哪些注入動作

Ch 3 說 `untrusted_app` 被 SELinux「綁得很緊」，這章講具體綁哪些、對你的注入手段各是什麼影響：

| 你想做的注入動作 | `untrusted_app` domain 下 | 逆向影響 |
|---|---|---|
| **ptrace 別的 App 進程** | 被 SELinux `neverallow`/策略擋（App 不能 ptrace 別的 App） | 這是為什麼 frida-server **不能**以普通 App 身分跑去 attach 別人——它得以 `shell`/root domain 跑 |
| **mmap 可執行記憶體（RWX）** | 受限（W^X 政策，`execmem`/`execmod` 被管） | inline hook / JIT 出 shellcode 需要可執行記憶體，App domain 對此有限制，某些手法會被擋 |
| **讀別的 App 的 `/data/data`** | 擋（跨 UID + type 是別人的 `app_data_file`） | App 進程內的你，讀不到別 App 的私有檔（除非同 UID/root） |
| **執行 `/data/local/tmp` 的檔** | 受限（App domain 不一定能 exec 那個 type） | 為什麼有些 payload 放 tmp 給 App 進程執行會被擋 |
| **在自己進程內 hook 自己** | 大多可以（同 UID、同進程） | 你注入進去後、在 target 自己進程內改 `ArtMethod`（Ch 36）多半 OK——這是「內嵌 hook」可行的原因 |

**核心分野**：

- **「跨進程注入別人」**（frida-server ptrace target）→ 靠的是 **frida-server 自己以 root/shell domain 跑**，不是 App domain。App domain 不能 ptrace 別人。
- **「注入進去後在 target 進程內動手」**（你的 agent/gadget 已在 target 進程裡改它自己的 `ArtMethod`）→ 這是**同進程操作**，受 target 自己的 `untrusted_app` domain 約束，但「改自己進程的記憶體/方法」大多允許。

這解釋了兩種注入路線的 SELinux 差異：**Frida（外部 root server ptrace 進去）繞的是「跨進程」那道；gadget/內嵌 hook（程式碼已在 target 進程）繞的是「進程內動作」那道。** 你的手段被擋時，先判斷你卡在哪一道。

> **未實測，理論預期行為**：上述 `untrusted_app` 的具體限制取自 AOSP `system/sepolicy/private/untrusted_app*.te` 的 allow/neverallow 規則。**規則逐版本收緊**（新版對 `execmem`、跨 App 存取管得更嚴）。你驗證：動作被擋時 `adb shell dmesg | grep avc`，讀 `scontext=u:r:untrusted_app` 那條 denied，看它想對什麼 type 做什麼被拒（Ch 3 練習做過）。

### 兩條注入路線各撞哪道 SELinux 牆

把「跨進程」與「進程內」兩條路線畫清楚，你就知道被擋時該懷疑哪道牆：

```
 路線 A: Frida (外部 root server ptrace 進去)
   frida-server (root/shell domain)
        │ ptrace(PTRACE_ATTACH, target_pid)   ← 撞「跨進程」那道
        │   → 因為 server 是 root/shell domain, 被允許 ptrace untrusted_app
        ▼
   注入 agent 到 target 進程
        │ 之後 agent 在 target 進程裡跑     ← 身分變成 target (untrusted_app)
        ▼
   agent 想讀別 App 的 /data/data            ← 撞「進程內身分」那道 (被擋)

 路線 B: gadget / 內嵌 hook (程式碼一開始就在 target 進程)
   target 進程自己 dlopen frida-gadget / 內嵌的 hook so
        │ 不需 ptrace 別人 (是自己 load 自己)  ← 繞開「跨進程」那道
        ▼
   在 target 進程內改自己的 ArtMethod         ← 「進程內動自己」大多允許
```

**關鍵洞察**：路線 A 的「能注入」靠的是 **frida-server 的 domain（root/shell）**，不是 target 的 domain；一旦進去，agent 就降級成 target 的 `untrusted_app`，能做的事被 target domain 綁死。路線 B 完全在 target 進程內、以 target 身分，繞開了跨進程 ptrace 那道牆——這是 `debuggable` App 免 root 用 gadget 的原理（Ch 3 提過），也是把 hook so 打包進 App（重打包）能免 root 的原因。**你被擋時先分：是「進不去」（跨進程那道，看 frida-server 的 domain）還是「進去了做不了事」（進程內那道，看 target 的 domain）。**

## 範例一：確認注入進去後你是什麼身分

你 Frida attach 一個 App 後，你的程式碼跑在 target 進程裡，身分就是 target 的身分。驗證這件事（**指令代表性，未實測，理論預期行為**）：

```javascript
// 在 target 進程裡跑, 印出「我現在是誰、什麼 domain」
Java.perform(function () {
    var pid = Process.id;
    console.log("[*] 我跑在 pid=" + pid);
    // 讀自己的 /proc/self/status 看 UID
    var f = new File("/proc/self/status", "r");
    var line;
    while ((line = f.readLine()) !== null) {
        if (line.indexOf("Uid:") === 0) console.log("    " + line.trim());
    }
    f.close();
    // 讀 SELinux domain
    var ctx = new File("/proc/self/attr/current", "r");
    console.log("    domain: " + ctx.readLine().trim());
    ctx.close();
});
```

**期望輸出（代表性）**：

```
[*] 我跑在 pid=4123
    Uid:    10123   10123   10123   10123
    domain: u:r:untrusted_app:s0:c123,c456
```

**讀懂它**：你的 Frida agent 雖然是 root 的 frida-server 注入進來的，但一旦跑在 `com.foo` 進程裡，`Process.id` 是 target 的 pid、`/proc/self` 讀到的 UID 是 10123（App 的）、domain 是 `untrusted_app`。**你在 target 進程裡，就是 target**——這解釋為什麼你的 agent 想從 target 進程去讀別 App 的 `/data/data` 會被擋（你現在是 `untrusted_app`，不是 root）。要跨出去，得回到 frida-server（root）那端做。

## 範例二：spawn vs attach 決定你搶到哪個窗口

同一個 hook，用 spawn（`-f`）和 attach（`-U name`）注入，能不能繞過殼的差別（**概念示範，未實測，理論預期行為**）：

```bash
# 窗口 D (晚): attach 一個已在跑的 App —— 殼早解完、pinning 早裝好
frida -U com.foo -l bypass.js
#   → 你 hook 的時候, App 的反調試/pinning 初始化早跑完了, hook 可能失效

# 窗口 B/C (早): spawn —— App 進程建好但還沒 resume, 你先裝 hook 再放行
frida -U -f com.foo -l bypass.js
#   → -f 會 spawn 並「暫停在很早的點」, 等你的 hook 裝好才 resume
#   → 你能趕在 Application.onCreate / 殼解密 之前 hook 住
```

**這個對比是本章時間軸的實戰落地**：

- **`-f`（spawn）** 讓 Frida 在 App 進程極早期（窗口 B 附近）暫停，你的 hook 在 App 邏輯跑之前就位——**這是繞「一啟動就做的防護」（早期反調試、殼解密、pinning）的關鍵**。
- **`-U name`（attach）** 是 App 已在窗口 D 運行了才接上——對「一次性、早就做完的初始化」你來不及攔。

**心法**：要 hook 的東西如果在 App 啟動早期就定局（殼、pinning、反調試），**用 spawn 搶早窗口**；要觀察的是持續運行的行為（某按鈕的邏輯），attach 就夠。搞不定某個防護時，先問自己「我是不是 attach 太晚了，該改 spawn」。

## 範例三（失敗/邊界）：Zygote 剛啟動時 App 進程還沒生，attach 撲空

一個時機邊界：你想 hook App 最早的初始化，寫了腳本去 attach，但 App 進程**還沒被 Zygote fork 出來**，attach 找不到進程：

```
 你: frida -U com.foo   (attach 模式)
   │
   ▼
 target com.foo 進程存在嗎?
   ├─ 存在 (窗口 D) → attach 成功, 但可能太晚
   └─ 不存在 (App 還沒開/剛被殺) → Failed to attach: 找不到進程  ✗
```

```
Failed to attach: unable to find process with name 'com.foo'
```

**應對**：

1. **要 hook 啟動早期 → 用 `-f` spawn**，讓 Frida 自己把進程孵出來並暫停在早期，而不是等它已經在跑。
2. **要 hook「每次啟動都攔」→ 用 `--gating`/spawn gating** 或走 Xposed（窗口 A，Zygote 層），這樣 App 一 fork 出來就帶 hook，不用你去追它什麼時候啟動。
3. **App 被反調試殺掉又重啟的循環**：有些殼偵測到 attach 就自殺重啟，你 attach 撲空是因為進程一直在換 pid——這時 spawn + 早期繞過反調試才是解，追著 attach 是徒勞。

**失敗是情報**：attach 一直撲空/target 一直換 pid，往往不是你手速慢，是**時機/防護問題**——該換 spawn 或往 Zygote 層走。

## 範例四：加固殼怎麼利用「窗口 C」搶在你前面

抽取殼、反調試的初始化為什麼常在 `Application.onCreate` 附近（窗口 C）？因為那是「App 進程已建好、但 App 業務邏輯還沒跑」的最後一個統一入口——殼要在任何業務碼跑之前把環境布置好（解密 DEX、裝反調試、hook pinning）。

```
 App 進程 (ActivityThread.main)
   │
   ▼ 建 Application 物件 (殼把自己的 Application 塞這, Ch35 的包裝 ClassLoader)
   │
   ▼ Application.attachBaseContext()  ← ★ 殼常在這最早動手 (比 onCreate 還早)
   │     解密真 DEX / 裝反調試 / 反 Frida 檢查
   │
   ▼ Application.onCreate()           ← 殼也可能在這
   │
   ▼ 第一個 Activity ... 業務邏輯開始
```

**這對你的注入時機是直接指令**：殼在 `attachBaseContext`（窗口 C 的最前緣）就動手，你若 attach（窗口 D）當然來不及。你得用 **`frida -f` spawn（窗口 B）搶在 `attachBaseContext` 之前**把繞過 hook 裝好——例如 hook 掉殼的反調試檢查、或在殼解密後、抹掉前 dump（Ch 36）。**「殼在 attachBaseContext 動手，我就要在它之前」——這句話決定了你八成的注入時機選擇。** 用 `frida-trace` 或在 spawn 後 hook `Application.attachBaseContext` 印堆疊，能看到殼具體在這裡做了什麼。

## 對比與取捨：三種注入時機/位置

| 面向 | Zygote 層（窗口 A，Xposed/Zygisk） | spawn 早注入（窗口 B/C，`frida -f`） | attach 晚注入（窗口 D，`frida -U`） |
|---|---|---|---|
| 進去的時機 | App fork 前（最早） | App 進程早期（onCreate 前） | App 已運行 |
| 能繞早期防護嗎 | 能（最全面） | 能（趕在初始化前） | 難（已定局） |
| 持久性 | 所有 App 自動帶（開機即有） | 每次 spawn 手動 | 每次 attach 手動 |
| 需要 | root + 框架（改 Zygote） | root frida-server | root frida-server |
| 隱蔽性 | 中（框架特徵） | 低（frida-server 易偵測） | 低 |
| 適合 | 持久化、跨 App、最早 hook | **繞殼/pinning/反調試** | 觀察穩定運行的邏輯 |

一句話取捨：**注入時機越早越能繞「一啟動就做的防護」，但越早越重（要動 Zygote/框架）**。實務梯度是——先 attach 試（最輕），繞不過早期防護就升 spawn，還要持久化/跨 App 才上 Zygote 層。**別一開始就上最重的，也別死守 attach 硬撞早期防護。**

## 踩雷集錦

1. **錯誤直覺：「注入進去我就是 root」→ 正確認識**：你的 agent 跑在 target 進程裡，身分是 target 的（`untrusted_app`、App 的 UID）。想做 root/跨 App 的事得回 frida-server（root）那端。`Process.id` + `/proc/self` 一驗就懂。
2. **錯誤直覺：「attach 就能 hook 任何東西」→ 正確認識**：attach 是窗口 D（App 已運行），殼解密/pinning/反調試常在啟動早期定局，你 attach 太晚攔不到。要早期就用 `-f` spawn 搶窗口 B/C。
3. **錯誤直覺：「App 進程能 ptrace 別的 App」→ 正確認識**：`untrusted_app` domain 被 SELinux 擋，App 不能 ptrace 別 App。frida-server 能注入是因為它以 root/shell domain 跑，不是 App domain。
4. **錯誤直覺：「Xposed 就是個 hook 工具，跟 Zygote 無關」→ 正確認識**：Xposed 的持久化本質是包裝 `app_process`/注入 Zygote（窗口 A），讓每個 fork 出的 App 帶 hook。它選 Zygote 是因為那是「一次 hook 所有 App」的唯一位置。
5. **錯誤直覺：「attach 撲空是我手速慢」→ 正確認識**：常是時機/防護問題——App 還沒 fork、或反調試偵測到就自殺換 pid。解法是 spawn 或往 Zygote 走，不是追著 attach。

## 進階：再往深一層

- **usap 池（Unspecialized App Process pool）**：為加速啟動，Zygote 預先 fork 一批「還沒 specialize」的進程放池裡，要開 App 時直接抓一個來 specialize。這改變了「fork 時機」——App 進程可能早在你點之前就 fork 好了（只是還沒 specialize 成 com.foo）。逆向極早期 hook 時要意識到這個池的存在。可 `adb shell getprop` 查相關屬性、或看 Zygote 的 usap 設定。
- **Zygisk（Magisk 的 Zygote 注入）**：現代在 Zygote 動手的主流是 Magisk 的 Zygisk 模組介面——它在 specialize 的 `preAppSpecialize`/`postAppSpecialize` 給你 hook 點，讓模組程式碼在「App 降權/轉 domain 前後」跑。這是 LSPosed 現在的注入底座，比改 `app_process` 乾淨。理解它的兩個時機點（specialize 前/後）你才懂模組能在哪個窗口動什麼。
- **isolated_app 的極限沙箱**：Ch 3 提過的 `isolatedProcess`（如 Chrome renderer）跑在 `isolated_app` domain，權限被削到幾乎沒有、UID 臨時分配。注入這種進程比 `untrusted_app` 更難——連平常 App domain 能做的都被再削一層。逆向這類進程要有心理準備。
- **SELinux 對 frida-server 落腳點的講究**：frida-server 放 `/data/local/tmp` 並以 `adb root` 後的 domain 跑是「對的」，因為那個 domain + type 組合被允許 ptrace `untrusted_app`。放錯地方/以錯 domain 跑，root 也可能因 SELinux 而 attach 失敗（Ch 3 場景 2）。這是「放對地方比 root 更重要」在注入上的延伸。
- **`/proc/<pid>/exe` 都指向 app_process 的取證意義**：因為所有 App 進程 exe 都是 `app_process`，你用 exe 路徑分辨不出 App。要靠 `/proc/<pid>/cmdline`（是 package name）或 `/proc/<pid>/status` 的 Name。惡意樣本分析時，「一堆進程 exe 都一樣」是正常的（都是 Zygote 子孫），別誤判。

## 動手練習

1. 在 AVD 上 `frida-ps -U` 找一個沒在跑的 App，先 `frida -U <name>` attach，看它報找不到進程；再 `frida -U -f <package>` spawn，看它成功孵出並暫停——親身體會 attach（窗口 D）與 spawn（窗口 B/C）的時機差。
2. attach 一個 App，用範例一的腳本印出你 agent 的 `Process.id` / UID / domain，確認「你在 target 進程裡就是 `untrusted_app`」。再試從 agent 讀別 App 的 `/data/data/<other>`，看它被擋——理解你的身分限制。
3. `adb shell cat /proc/<zygote_pid>/cmdline`（找 Zygote 的 pid，名字含 `zygote`），確認它是 `app_process`。再看一個 App 進程的 `/proc/<pid>/exe` 指向哪、`/proc/<pid>/cmdline` 是什麼——驗證「exe 都是 app_process、名字靠 cmdline 區分」。
4. 對照本章的四窗口時間軸，不看筆記自己畫一遍，並在每個窗口標「誰用這個窗口（Xposed/spawn/attach）」——畫得出來代表你把注入時機內化了。

## 本章重點整理

- **App 進程 = Zygote fork + specialize 的產物**，specialize 依序：轉 SELinux domain（→`untrusted_app`）、設 GID、**降權 setuid**、裝 seccomp。過了降權/轉 domain，進程就是普通 App、受 App domain 約束。
- **四個注入窗口**：A（Zygote 內 fork 前，Xposed/Zygisk，最早最全）、B/C（App 進程早期，`frida -f` spawn，趕在殼/pinning/反調試前）、D（App 已運行，`frida -U` attach，晚、易錯過早期防護）。**時機決定能不能繞早期防護。**
- **`app_process`** 是 Zygote 的執行檔（`--zygote` 啟動），也是 Xposed 早期包裝以「在 Zygote 種 hook」的入口；所有 App 進程 exe 都是它。
- **`untrusted_app` domain 的分野**：跨進程注入別人（ptrace）App domain 不行，靠 frida-server 以 root/shell domain 做；進程內動自己（改 `ArtMethod`）大多可以。你的 agent 在 target 進程裡就繼承 target 的身分與限制。
- **注入梯度**：先 attach（輕）→ 繞不過早期防護升 spawn → 要持久化/跨 App 才上 Zygote 層。

## 自我檢核

- [ ] 不看筆記，能畫出 App 進程從 Zygote fork 到跑 App 程式碼的時間軸，標出四個注入窗口
- [ ] 能說出 specialize 的關鍵步驟（轉 domain、降權、seccomp），以及過了哪步進程就不再是 root
- [ ] 能解釋 `frida -f`（spawn）與 `frida -U name`（attach）搶到的是哪個窗口、各適合繞/觀察什麼
- [ ] 能講清楚為什麼你的 agent 在 target 進程裡是 `untrusted_app`，以及這限制你不能做什麼
- [ ] 能說出 `app_process` 是什麼、Xposed 為什麼跟它/Zygote 有關
- [ ] 能區分「跨進程注入別人」與「進程內動自己」在 SELinux domain 上的不同限制

## 延伸閱讀

### 原始碼（一手依據）

- **[Zygote specialize 流程](https://cs.android.com/android/platform/superproject/+/master:frameworks/base/core/java/com/android/internal/os/Zygote.java)** — Android Code Search
  - **讀哪裡**：`Zygote.java` 的 fork/specialize 相關，配 native `com_android_internal_os_Zygote.cpp` 的 `SpecializeCommon`
  - **為什麼值得讀**：本章 specialize 步驟順序的權威出處，**切目標版本 tag** 確認你這版的順序與 usap 行為
- **[untrusted_app sepolicy](https://cs.android.com/android/platform/superproject/+/master:system/sepolicy/private/)** — Android Code Search
  - **讀哪裡**：`untrusted_app.te`/`untrusted_app_all.te` 的 allow/neverallow
  - **和本章的關聯**：本章「domain 卡你哪些注入動作」的規則依據；被擋時對照這裡看哪條 neverallow

### 注入框架（原理）

- **[Zygisk / LSPosed 文件](https://github.com/LSPosed/LSPosed)** — LSPosed
  - **這篇說什麼**：現代在 Zygote 注入的主流機制（Zygisk 的 pre/postAppSpecialize hook 點）
  - **讀哪裡**：Zygisk 模組 API 的兩個 specialize 時機點；LSPosed 怎麼用它種 hook
  - **前提知識**：讀過本章的 specialize 時間軸，這裡看「窗口 A」怎麼被工程化

### 系統機制

- **[Android Runtime / Zygote 官方文件](https://source.android.com/docs/core/runtime/zygote)** — AOSP
  - **讀哪裡**：Zygote 啟動、預載、fork 那節
  - **和本章的關聯**：本章 Zygote/app_process 的官方描述，跟 Ch 3 對照著讀
- **[HackTricks — Android Frida spawn vs attach](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **這篇說什麼**：spawn/attach 在滲透實務中的選用與繞早期防護
  - **讀哪裡**：Frida 使用與早期 hook 那段
  - **前提知識**：本章的窗口概念，這頁給你指令化的操作

下一個是練習 E——把 Ch 34（`ArtMethod`）、Ch 35（枚舉 ClassLoader）、Ch 36（主動調用/attrib bridge dump）全部串起來，你要親手用 Frida 寫一個簡化版的 ArtMethod-level 主動調用脫殼器（mini FART）。

→ [練習 E：寫一個 ArtMethod-level 主動調用脫殼器（mini FART）](./practice-e-mini-fart.md)
