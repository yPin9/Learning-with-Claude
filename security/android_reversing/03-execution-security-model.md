# Ch 3 — 執行與安全模型：Zygote、沙箱、權限、SELinux

> **目標**：搞懂一個 App 裝進系統後，是怎麼被 **Zygote** fork 成進程、被 **UID/GID 沙箱**關進自己的地盤、被 **權限模型**（install-time vs runtime）限制能碰什麼、再被 **SELinux** 用 domain/context 上第二道鎖。這章要回答四個逆向者必問的問題：Frida 為什麼要 root？注入是在突破哪一道牆？App 之間為什麼互相看不到？`/data/data/<pkg>` 為什麼別的 App 進不去？懂了這套模型，你才知道你的每個逆向動作在對抗什麼。

## 為什麼要懂安全模型？

因為你所有的動態手段——attach 進程、注入 `.so`、讀別人的私有目錄、hook 系統呼叫——全部都在**突破 Android 刻意設下的隔離**。你如果不知道這些隔離是怎麼架起來的，就會在「Frida attach 不上」「push 檔案 Permission denied」「明明 root 了還是讀不到某個檔案」這些坑裡瞎猜。

更關鍵的是：Android 的安全模型是**兩層**的。第一層是 Linux 內核原生的 **DAC**（Discretionary Access Control，自主存取控制）——就是 UID/GID + 檔案權限那套 Unix 老規矩。第二層是 **MAC**（Mandatory Access Control，強制存取控制）——SELinux，一套即使你是 root 都繞不過的策略。很多新手以為「root 了就無所不能」，然後撞上 SELinux 的牆一頭霧水。這章就是把這兩層攤開，讓你知道你的 root shell 到底能做什麼、不能做什麼、以及為什麼。

## 先建立直覺：一個 App 進程是怎麼誕生的

先講一個違反直覺但至關重要的事實：**Android 不是每開一個 App 就從頭 `fork+exec` 一個新的 Dalvik/ART runtime**。那樣太慢——每個 App 都要重新載入幾百個核心 framework 類、重新初始化 runtime，開 App 會慢到不能忍。

Android 的解法是 **Zygote（受精卵）**：開機時啟動一個「母進程」，它預先載入好所有 App 都會用到的核心類與資源、把 ART runtime 初始化到「隨時可跑」的狀態，然後**睡著等**。每次要開新 App，系統不從零建，而是叫 Zygote **`fork()` 一份自己**——子進程瞬間繼承了母進程已經載入好的一切（靠 copy-on-write，記憶體還共享著沒真的複製），再把身分改成那個 App、載入 App 自己的 DEX，就成了一個 App 進程。

```
   開機
    │
    ▼
 init ──啟動──▶ Zygote (母進程, UID=root 起步)
                  │  預載: framework 類 / 資源 / ART runtime
                  │  開一個 socket, 睡著等命令
                  │
   使用者點 App    │
    │             │
    ▼             ▼
 system_server ──"孵 com.foo"──▶ Zygote.fork()
                                    │
                                    ▼
                            新進程 (COW 繼承一切)
                                    │  setuid(10123)  ← 掉權成 App 的 UID
                                    │  setgid / SELinux domain 轉換
                                    │  載入 com.foo 的 classes.dex
                                    ▼
                            com.foo 跑起來 (UID=10123, 沙箱內)
```

三個重點刻進腦子：

1. **每個 App 進程都是 Zygote 的子孫**，所以它們的記憶體佈局起點高度一致（這對 Frida/Xposed 這種要在 Zygote 層動手腳的持久化 hook 很重要，Ch 16 會用到）。
2. **fork 完會 `setuid` 掉權**：Zygote 起步是 root，但 fork 出 App 進程後立刻把 UID 降成分配給那個 App 的 UID。App 進程**不是 root**，這是沙箱的地基。
3. **App 的 UID 是它的身分證**：Android 給每個安裝的 App 分一個獨立 UID（從 10000 起算），這個 UID 決定了它能碰什麼檔案、能不能被別人看到。

> Zygote 的內部細節（socket 協議、預載清單、`usap` 池、SELinux domain transition 的時機）留到 Ch 37 深挖。這章給你逆向需要的心智模型就夠——你要記得的是「App 進程 = Zygote 的降權子孫，關在自己 UID 的沙箱裡」。

### fork 而非 exec：對逆向的三個具體後果

「用 `fork()` 複製 Zygote 而不是 `fork()+exec()` 重新啟動」這個決定，不只是效能優化，它在逆向上留下三個可利用的痕跡：

1. **記憶體佈局高度一致**：每個 App 進程都繼承 Zygote 已載入好的 framework 類與 runtime 結構，加上 COW（copy-on-write），這些共用頁在所有 App 進程裡的虛擬位址幾乎一致。這是為什麼跨進程的記憶體 pattern（例如某個 framework 函式的位置）在不同 App 裡能通用——它們同源。
2. **在 Zygote 層 hook = 一次 hook 所有未來的 App**：因為每個 App 都是 fork 出來的，你若能在 Zygote **fork 之前**注入 hook，那份 hook 會被所有子進程繼承。**這正是 Xposed/LSPosed 持久化 hook 的原理**（Ch 16）——它們寄生在 Zygote，之後開的每個 App 自動帶著 hook。相比之下 Frida 是逐進程 attach，App 一重啟就得重來。
3. **ASLR 的一個弱化**：因為 App 進程繼承 Zygote 的位址空間（fork 不重新隨機化已載入部分），某些 framework 程式庫的載入基址在同一次開機的所有 App 裡是**一樣的**。這對記憶體攻擊/pattern scan 是個便利，也是安全研究關注的點。

這三點你現在知道就好，Part 3（Frida vs Xposed 的取捨）與 Ch 16（持久化 hook）會把它們用起來。核心記住：**fork 讓所有 App 同源，同源就有可利用的一致性。**

## 沙箱的地基：UID/GID 就是隔離

Android 的 App 沙箱**不是什麼神祕新技術**，它就是 Unix 用了幾十年的 **UID 檔案權限**。每個 App 裝上去，`PackageManager` 分給它一個唯一的 UID（叫 app UID，範圍從 `AID_APP` = 10000 起）。這個 App 的：

- **進程**以這個 UID 跑
- **私有資料目錄** `/data/data/<pkg>`（等同 `/data/user/0/<pkg>`）**owner 就是這個 UID**，權限設成別人進不去

我們來看這在檔案系統上長什麼樣（代表性，取自 AVD 上 `ls -l /data/data`，**未實測，理論預期行為**——本 repo 沙箱無 Android，格式依實際 AVD 行為寫）：

```
drwx------ 4 u0_a123 u0_a123  ... com.example.foo
drwx------ 4 u0_a124 u0_a124  ... com.example.bar
```

看兩件事：

- **owner 是 `u0_a123`**：這是 `user 0`（主要使用者）的 app 123，對應 UID 10123（`10000 + 123`）。`u0_a124` 就是 UID 10124，另一個 App。
- **權限 `drwx------`**：owner 有 rwx，**group 和 others 什麼都沒有**（`---`）。這一行就是沙箱的全部祕密——`com.example.bar`（UID 10124）想讀 `com.example.foo`（UID 10123）的目錄，Linux 內核在 `open()` 時比對 UID 不符、權限位 others 又是 `---`，直接 `EACCES` 打回。

```
 com.example.bar (UID 10124)              com.example.foo (UID 10123)
       │                                        │
       │  open("/data/data/com.example.foo/…")  │
       ▼                                        ▼
  ┌─────────────────────────────────────────────────┐
  │  Linux VFS 權限檢查:                              │
  │    目錄 owner=10123, mode=rwx------              │
  │    呼叫者 UID=10124 ≠ 10123, others 位=---       │
  │    ⇒ EACCES (Permission denied)                 │
  └─────────────────────────────────────────────────┘
```

**這就是為什麼 App 之間互相看不到彼此的資料**。不需要什麼虛擬機隔離、不需要容器——一個 UID + 一組檔案權限位就搞定了 99% 的隔離。簡單、老派、有效。

### App 的資料到底放哪：internal vs external

逆向常要找 App 存的 token、密碼、快取、資料庫，你得知道它可能落在哪、各自的沙箱強度：

| 位置 | 路徑 | 沙箱強度 | 逆向者關注 |
|---|---|---|---|
| **內部私有** | `/data/data/<pkg>/`（= `/data/user/0/<pkg>/`） | 強（UID + `drwx------`） | SharedPreferences、SQLite db、`files/`、`databases/`——**最肥的目標**，token/密碼常在這 |
| **App 專屬外部** | `/sdcard/Android/data/<pkg>/` | 中（scoped storage 管） | 快取、下載檔；Android 10+ 別的 App 也難碰 |
| **共享外部** | `/sdcard/`（Download、Pictures…） | 弱（有權限就能讀） | App 明碼寫這裡的東西人人可讀，是安全弱點 |

**內部私有目錄是逆向 App 本地儲存的第一站**：`/data/data/<pkg>/shared_prefs/*.xml`（設定與 flag）、`databases/*.db`（SQLite，常存 token/使用者資料）、`files/`（App 自訂檔）。因為它在 UID 沙箱裡，你要 root（或 `run-as` debuggable App）才進得去——這又回到「跨 UID 讀資料要突破 DAC」的同一件事。

Android 10+ 的 **scoped storage（分區儲存）** 收緊了外部儲存：以前 App 有 `WRITE_EXTERNAL_STORAGE` 就能讀整個 `/sdcard/`，現在預設只能碰自己專屬的 `Android/data/<pkg>/` 與透過 MediaStore 的媒體檔。逆向時若發現 App 把敏感資料寫在共享外部（弱沙箱），那本身就是一個值得記錄的安全問題。

### 那 Frida 為什麼要 root？

現在你有基礎能回答這門課最常被問的問題了。你的 `frida-server` 想 attach 到 `com.example.foo`（UID 10123）並把 JavaScript 注入它的進程——注入的技術手段是 `ptrace()`（Ch 12 深挖），而 `ptrace` 一個進程需要你**有權限操作那個進程**。

- 如果 frida-server 以**普通使用者 UID** 跑，它 ptrace 別的 UID 的 App 會被內核擋（`ptrace` 的 `PTRACE_MODE_ATTACH` 檢查會失敗）。
- 如果 frida-server 以 **root** 跑，它跨過了 DAC 這一層（root 的 UID=0 能 ptrace 幾乎任何進程），才注得進去。

```
 frida-server (root, UID=0)
       │  ptrace(PTRACE_ATTACH, pid_of_foo)
       ▼
 ┌──────────────────────────────────┐
 │ 內核: 呼叫者是 root ⇒ DAC 放行     │
 │       (若非 root, 跨 UID ptrace 被擋) │
 └──────────────────────────────────┘
       │  注入 gadget / agent .so
       ▼
 com.example.foo 進程內執行你的 JS
```

**所以 Frida 要 root，本質是因為「跨 UID 注入別人的進程」需要突破 UID 沙箱這道 DAC 牆，而 root 是繞過 DAC 的萬能鑰匙。** 這也是為什麼 Ch 0 一直強調 AVD 要選能 `adb root` 的 Google APIs image——沒 root，frida-server 注不進別的 App。

> **例外：`debuggable` App 不需要 root 就能被 Frida attach 的變體。** 如果 target App 的 Manifest 開了 `android:debuggable="true"`（Ch 2 提過），系統允許 `adb` 透過 `run-as` 以那個 App 的身分跑東西，你可以走 gadget 注入或 `frida-gadget` 塞進 App 而不需要系統 root。這是**同 UID 內操作**（你以 App 自己的身分動它自己），不涉及跨 UID，所以繞開了 root 需求。但正式 App 幾乎都關 debuggable，所以實務上 root 還是主路。

### 從 `/proc` 親眼驗證沙箱

上面的 UID 隔離不是抽象概念，你能在 `/proc` 裡直接看到它落在進程屬性上。每個進程的 `/proc/<pid>/status` 列出它的真實 UID/GID 與補充 group，`/proc/<pid>/attr/current` 列出它的 SELinux context：

```
# adb shell (root) 對一個已跑的 App 進程 (代表性欄位)
$ cat /proc/4123/status
Name:   com.example.foo
Uid:    10123   10123   10123   10123      ← 四個都是 10123, 沒有殘留 root
Gid:    10123   10123   10123   10123
Groups: 3003 9997 20123 50123             ← 補充 GID: 3003=inet(有 INTERNET 權限)
$ cat /proc/4123/attr/current
u:r:untrusted_app:s0:c123,c456            ← SELinux domain = untrusted_app
```

三個逆向線索一次看齊：**Uid 全是 10123**（Zygote fork 後徹底降權、沒殘留 root）、**Groups 含 3003**（有 INTERNET 權限，下一節講權限映射會用到）、**SELinux domain 是 `untrusted_app`**（決定它能對哪類資源做什麼）。你排查一個 App「為什麼碰不到某資源」時，這三行是第一手證據——先確認它的身分（UID）、能力（Groups）、角色（domain），再判斷是哪一層擋了它。

> **未實測，理論預期行為**：上面的 `/proc/<pid>/status` 與 `attr/current` 欄位格式取自 Linux/Android 實際行為（本 repo 沙箱無 Android）。你在 AVD 上 `adb shell ps -A | grep <pkg>` 找到 pid，再 `cat /proc/<pid>/status` 就能看到你自己 App 的這幾行。

## sharedUserId：兩個 App 共用一個 UID

有個例外會打破「一個 App 一個 UID」：如果兩個 App 在 Manifest 宣告了**相同的 `android:sharedUserId`**（而且用**同一把 key 簽名**），系統會讓它們**共用同一個 UID**。共用 UID 的 App 之間，DAC 沙箱形同不存在——它們能互讀對方的私有目錄、能在同進程跑。

這對逆向有兩個意義：

1. **AOSP 系統 App 大量用這招**：一堆 `com.android.*` 系統元件共用 `android.uid.system`（UID 1000）。所以你看到某些系統 App 能互通資料，不是特權魔法，是 sharedUserId。
2. **它是攻擊面**：如果一個 App 用 sharedUserId 跟別人共 UID，你逆向的邊界就不只是它自己——共 UID 的夥伴 App 的資料它都碰得到。

> `sharedUserId` 在新版 Android 已被官方**標記為 deprecated**（因為它讓沙箱邊界變模糊、難維護），但存量 App 與系統元件裡還大量存在，逆向時看到 Manifest 有這欄位要警覺。

### 多使用者與 work profile：UID 的完整結構

前面說 App UID 從 10000 起算，但那只是 `user 0`（主要使用者）的算法。Android 支援多使用者（家庭共用、work profile 分身），完整的 UID 公式是：

```
 UID = user_id × 100000 + app_id
       └ user 0 → 0        └ 10000 起
         user 10 (work) → 1000000
```

所以同一個 App 裝在 work profile（user 10）裡，UID 是 `1000000 + 10123 = 1010123`，資料放在 `/data/user/10/<pkg>` 而不是 `/data/user/0/<pkg>`。這對逆向的意義：**同一個 App 在不同 user 下是不同 UID、不同資料目錄、彼此完全隔離**——你在主 profile 逆一個 App，它在 work profile 的分身資料你碰不到（除非也 root 那邊）。看到 `/data/user/N/` 目錄或 UID 超過 100000 的進程，就要意識到這是多使用者維度，別誤以為是同一個實例。

## 權限模型：install-time vs runtime

UID 沙箱管的是「App 之間」的隔離。但 App 要跟**系統**要能力——上網、讀通訊錄、開相機、讀定位——這靠**權限（Permission）**系統。逆向時你會在 Manifest 裡讀到一堆 `<uses-permission>`，得知道它們分兩種、授予時機完全不同：

| 類型 | 例子 | 何時授予 | 使用者看得到嗎 | 底層機制 |
|---|---|---|---|---|
| **install-time（normal）** | `INTERNET`、`ACCESS_NETWORK_STATE`、`VIBRATE` | 安裝時**自動給**，不問使用者 | 只在商店頁列出 | 多半映射到一個 **Linux GID** |
| **runtime（dangerous）** | `CAMERA`、`READ_CONTACTS`、`ACCESS_FINE_LOCATION` | 執行期**跳框問使用者**（Android 6+） | 明確彈窗 | AppOps + 執行期檢查 |
| **signature** | 系統級、需同 key | 只有同簽名者能拿 | 不問 | 簽名比對 |
| **special** | `SYSTEM_ALERT_WINDOW`、`MANAGE_EXTERNAL_STORAGE` | 需去設定頁手動開 | 特殊設定入口 | 逐項特殊處理 |

底層機制最值得逆向者知道的是 **install-time 權限有一部分是靠 GID 實作的**。例如 `INTERNET` 權限，在底層對應一個叫 `AID_INET`（GID 3003）的 group——有這權限的 App，它的進程會被加進 `inet` 這個補充 group，內核在建 socket 時檢查你在不在 `inet` group，不在就擋。

```
 App 宣告 <uses-permission INTERNET>
       │  安裝時
       ▼
 PackageManager 把 App 進程的補充 GID 加上 3003 (inet)
       │
       │  執行期 App 呼叫 socket(AF_INET, ...)
       ▼
 內核: 檢查呼叫者 GID 含 inet? ── 是 ──▶ 放行建 socket
                                └─ 否 ──▶ EACCES
```

這解釋了一個逆向現象：**你在一個沒宣告 `INTERNET` 的 App 進程裡，就算用 Frida 硬呼叫 socket API 也連不上網**——因為擋你的是內核的 GID 檢查，不是 App 層的邏輯，Frida hook Java 層繞不過它。要讓它能上網，得在 Manifest 加權限重打包（改到 GID 分配），或整個換一個有權限的載體進程。

runtime 權限則不同：它是**執行期**由 framework 的 `checkSelfPermission` / AppOps 檢查的，是**軟體邏輯**。這意味著——**runtime 權限的檢查是可以用 Frida hook 掉的**（hook `ContextImpl.checkPermission` 讓它永遠回 `PERMISSION_GRANTED`）。install-time 的 GID 檢查在內核、hook 不掉；runtime 的權限檢查在 Java framework、hook 得掉。這個區分在你想繞某個權限保護時是關鍵判斷。

> **未實測，理論預期行為**：上述 GID 3003 對應 `INTERNET`、以及 hook `checkSelfPermission` 繞 runtime 權限，是 AOSP `system/core/libcutils/include/private/android_filesystem_config.h` 定義的 AID 與 framework 行為。你在自己 AVD 上驗證：`adb shell cat /proc/<app_pid>/status` 看 `Groups:` 那行有沒有 3003，再對照 App 有沒有宣告 `INTERNET`。

### 權限也守 App 的元件：exported 與自訂 permission

權限不只管「App 能不能碰系統資源」，也管「別的 App 能不能碰**你這個 App 的元件**」（Activity/Service/Provider/Receiver）。這是一個常被忽略但很肥的攻擊面：

- **`android:exported="true"`**：這個元件對外開放，別的 App（甚至 `adb shell am`）能直接叫起它。逆向時在 Manifest 掃 `exported` 的元件，就是在找「不用 root、不用注入，直接從外部戳得到的入口」——很多權限繞過、資料洩漏就藏在一個忘了關 exported 的 Activity 或 ContentProvider。
- **自訂 permission 守元件**：App 可以宣告 `<permission android:name="com.foo.MY_PERM" android:protectionLevel="signature">`，再用它保護某元件——只有同簽名的 App 能呼叫。逆向時看到某元件被自訂 permission 守著，就知道它是「內部元件、對外設防」，要嘛你偽裝成同簽名、要嘛從進程內部繞。

```
 外部 App / adb am ──呼叫──▶ com.foo 的 Activity
                                │
                                ▼  這個 Activity exported 嗎? 有 permission 守嗎?
                    ┌───────────────────────────────┐
                    │ exported=false ⇒ 外部叫不動     │
                    │ exported=true 無守 ⇒ 誰都能叫   │ ← 攻擊面
                    │ 有自訂 signature permission ⇒    │
                    │   只有同簽名 App 能叫           │
                    └───────────────────────────────┘
```

**偵察 SOP（接 Ch 1）裡「看 Manifest」的重點之一就是這個**：列出所有 exported 元件、看哪些沒被 permission 守——那是你不用突破沙箱就能直接互動的表面。Ch 9（Manifest 逆向）會把這套 exported/permission 的分析展開。

## 第二層鎖：SELinux（root 都繞不過的牆）

到這裡你會以為「root = 無所不能」。**錯**。這是新手最大的認知斷層。Android 從 5.0 起強制啟用 **SELinux enforcing 模式**，它是一層**獨立於 UID 的強制存取控制（MAC）**——即使你是 root（UID=0），你的每個動作還要通過 SELinux 策略（sepolicy）的允許，否則照樣被擋。

DAC 和 MAC 的關係是**且（AND）**，不是或：

```
 一個操作 (例如 root 想 open 某檔案)
       │
       ▼
 ┌──────────────────┐   通過   ┌──────────────────┐   通過   ┌──────┐
 │ DAC 檢查          │ ───────▶ │ MAC (SELinux)     │ ───────▶ │ 放行 │
 │ (UID/GID 權限位)  │          │ 檢查 domain 能否   │          └──────┘
 └──────────────────┘          │ 對 type 做此動作   │
       │ 不通過                 └──────────────────┘
       ▼                              │ 不通過 (denied)
    EACCES                            ▼
                              EACCES + audit log (avc: denied)
```

**兩層都放行才放行**。root 讓你過第一層 DAC，但過不了第二層 SELinux。

### SELinux 的三個名詞：context、domain、type

SELinux 給系統裡**每個東西都貼一個標籤**，叫 **security context**，格式是 `user:role:type:level`：

```
 進程的 context:   u:r:untrusted_app:s0:c123,c456
                   │ │ └── domain ──┘ └ MLS level ┘
                   │ └ role
                   └ user
 檔案的 context:   u:object_r:app_data_file:s0
                              └── type ──┘
```

- **domain**：貼在**進程**上的 type（習慣上進程的 type 就叫 domain）。App 進程的 domain 通常是 `untrusted_app`（或細分的 `untrusted_app_27` 之類）。
- **type**：貼在**檔案/資源**上的標籤，例如 App 私有資料是 `app_data_file`。
- **策略規則（allow rule）**長這樣：`allow untrusted_app app_data_file:file { read write open };`——意思是「domain 為 `untrusted_app` 的進程，允許對 type 為 `app_data_file` 的 file 做 read/write/open」。**沒有明確 allow 的，一律拒絕**（default deny）。

這對逆向的**直接衝擊**：

1. **你的 frida-server 放哪、以什麼 domain 跑，決定它能不能注入。** 標準做法是把 frida-server 放 `/data/local/tmp/` 並以 root（`adb root` 後的 shell domain）跑。如果你把它放錯地方、或以受限的 domain 跑，即使是 root 也可能因為 SELinux domain 不允許 `ptrace` 某類進程而 attach 失敗。

2. **`untrusted_app` domain 被 SELinux 綁得很緊**：Android 刻意讓普通 App 的 domain 不能做很多事（不能 ptrace 別的 App、不能亂讀系統檔、不能 exec 某些東西）。這是為什麼**惡意 App 就算拿到某些漏洞，也還被 SELinux 關著**——這層 domain 限制是 App 逃逸的另一道牆。

3. **看到 `avc: denied` 就是 SELinux 擋了你。** 你的動作被莫名擋掉、DAC 明明該過，就 `adb shell dmesg | grep avc` 或看 `logcat`，`avc: denied { <動作> } for ... scontext=... tcontext=... tclass=...` 這行會告訴你「哪個 domain 想對哪個 type 做什麼動作被拒」。這是排查「root 了還被擋」的第一手線索。

> **臨時把 SELinux 關成 permissive**：在能 root 的 AVD 上 `adb shell setenforce 0` 可以把 SELinux 切成 permissive（只記 log 不真的擋），用來確認「到底是不是 SELinux 在擋我」。**這是排錯手段，不是正解**——真機或評估環境你未必能關，而且關了就失去了學習「原本會被擋在哪」的機會。用它來定位問題，別養成依賴。

## 第三層鎖：seccomp 限制能呼叫哪些 syscall

到這裡防禦已經兩層（DAC + MAC），但 Android 還疊了第三層——**seccomp-bpf**。Zygote fork 出 App 後，會給子進程裝上一個 seccomp filter，限制這個進程**能呼叫哪些系統呼叫（syscall）**。不在白名單的 syscall 一發出去，內核直接送 `SIGSYS` 把進程打死。

三層防禦各管一個維度，疊起來是縱深：

```
 一個 App 進程想做一件事
   │
   ▼  ① DAC  : 你是誰 (UID/GID) ── 能不能碰這個檔案?
   │
   ▼  ② MAC  : 你這 domain 被允許做什麼 (SELinux) ── 這動作策略准嗎?
   │
   ▼  ③ seccomp : 你能發哪些 syscall ── 這個 syscall 在白名單嗎?
   │
   ▼  三關全過 ⇒ 執行；任一關擋 ⇒ 失敗 (EACCES / avc denied / SIGSYS)
```

這對 native 逆向的實際影響：**你在 App 進程裡呼叫某個底層 syscall 卻莫名收到 `SIGSYS` / 進程直接死掉**，第一個要懷疑的就是 seccomp——那個 syscall 不在這個進程被允許的清單裡。App 進程的 seccomp 白名單比 shell 寬鬆度不同，這是為什麼有些 payload 在 `adb shell` 跑得動、塞進 App 進程就死。Ch 30（反調試）與 Part 4（native）會再碰到，這裡先把「三層防禦」的完整圖建起來——**很多逆向的「莫名被擋」都是這三層之一在動作，先判斷是哪一層，再對症下藥。**

## 四個具體場景串起來

把上面四層（Zygote/UID/權限/SELinux）套進真實逆向動作：

**場景 1：`adb push frida-server /data/data/com.foo/` 為什麼 Permission denied（即使 root）？**
你 `adb root` 過了 DAC。但 `adb shell` 的 domain 是 `shell`（或 root shell 的 domain），SELinux 策略**不允許 shell domain 寫 `app_data_file`**（App 的私有目錄）。解法：push 到 `/data/local/tmp/`（它的 type 是 shell 能寫的），這就是為什麼所有教學都叫你放這裡。

**場景 2：Frida attach 一個系統進程失敗，attach 普通 App 卻成功。**
普通 App 是 `untrusted_app` domain，你的 root frida-server 有權 ptrace 它。但某些系統進程的 domain 受更嚴的 SELinux 保護（甚至有 `neverallow` 規則禁止任何人 ptrace），root 也 ptrace 不了。這不是 Frida 的 bug，是 MAC 在保護那個進程。

**場景 3（失敗/邊界情況）：你 hook 掉了 App 的 runtime 權限檢查，讓它以為有 `CAMERA` 權限，但相機還是打不開。**
你 hook 的是 Java framework 層的 `checkSelfPermission`（放行了）。但真正開相機時，`cameraserver` 那端**還會再獨立檢查一次**權限（跨進程、透過 AppOps，不在你 hook 的進程裡），而且底層還有 SELinux 管 `untrusted_app` 能不能連 `cameraserver`。**一次 hook 繞不掉「多點檢查 + 跨進程 + MAC」的縱深防禦**——這是新手常犯的錯：以為權限檢查只有一處。要繞這種得同時處理 App 端與 server 端，甚至 hook 系統服務進程。

**場景 4（三層都要判斷）：你把一個 x86_64 shell 跑得動的 native payload 塞進 App 進程，App 直接 crash。**
先排查是哪一層：`logcat` 沒有 `avc: denied`（排除 MAC）、檔案權限也沒問題（排除 DAC），但 crash log 顯示 `SIGSYS`——那就是 **seccomp**：payload 用了某個不在 App 進程白名單的 syscall。這正是「三層防禦各管一維、莫名被擋先判斷是哪一層」的實例。`adb shell` 的 domain 與 seccomp 白名單跟 App 進程不同，所以同一段 code 換進程就死。

這四個場景的共同心法：**別一看到「被擋」就亂猜或狂加 root。先定位是哪一層擋的——DAC（`EACCES` + 檔案權限對不上）、MAC（`avc: denied`）、還是 seccomp（`SIGSYS`）——三種訊號各自明確，對症才有解。**

## 對比與取捨：DAC vs MAC

| 面向 | DAC（UID/GID + 權限位） | MAC（SELinux） |
|---|---|---|
| 誰能改規則 | 檔案 owner 自己可以 `chmod` | 只有系統策略（sepolicy），App/使用者改不了 |
| root 能繞過嗎 | **能**（UID=0 無視權限位） | **不能**（root 也受策略約束） |
| 隔離對象 | App 之間（不同 UID） | 進程 domain 能對哪類資源做哪些動作 |
| 逆向排錯訊號 | `EACCES` + 檔案權限對不上 | `avc: denied` in dmesg/logcat |
| 你能繞的手段 | root / 同 UID / 改 owner | 換 domain（難）/ setenforce 0（測試用） |
| App 層 hook 有效嗎 | 對內核 GID 檢查無效 | 對內核 MAC 檢查無效 |

一句話總結取捨：**DAC 是「你是誰」，MAC 是「你這個角色被允許做什麼」。root 改變了「你是誰」（變成萬能的 0 號），但改變不了 SELinux 眼中「你這個 domain 被允許做什麼」。**

## 踩雷集錦

1. **錯誤直覺：「root 了就無所不能」→ 正確認識**：root 只跨過 DAC 這一層。SELinux（MAC）是獨立的第二層，root 照樣被 sepolicy 約束。看到 `avc: denied` 就是它擋的，不是你 root 沒 root 成功。
2. **錯誤直覺：「App 之間隔離是靠什麼高級虛擬化」→ 正確認識**：99% 的 App 沙箱就是 **Unix UID + 檔案權限位**（`drwx------`，owner 是 App 的 UID）。老派、簡單、有效。懂這個你才知道跨 App 讀資料為什麼要 root。
3. **錯誤直覺：「hook 掉權限檢查就繞過權限了」→ 正確認識**：install-time 權限（如 `INTERNET`）落在**內核 GID 檢查**，App 層 hook 繞不掉；只有 runtime 權限（framework 的 `checkSelfPermission`）是軟體邏輯才 hook 得掉。而且危險權限常有 App 端 + server 端**多點檢查**，一次 hook 不夠。
4. **錯誤直覺：「push 到哪都行，反正我 root」→ 正確認識**：SELinux 管 domain 能寫哪種 type 的檔案。root shell 寫得進 `/data/local/tmp/`（對的 type），但寫不進 App 的 `app_data_file`。放對地方比 root 更重要。
5. **錯誤直覺：「Zygote 只是個開機服務，跟逆向無關」→ 正確認識**：所有 App 進程都是 Zygote 的降權子孫，這是持久化 hook（Xposed/LSPosed 在 Zygote 層注入，Ch 16）與理解 App 進程記憶體佈局一致性的基礎。它不是背景細節，是 App 生命週期的源頭。
6. **錯誤直覺：「被擋了狂加 root / 狂 setenforce 0 就對了」→ 正確認識**：三層防禦訊號各自明確——DAC 是 `EACCES`（檔案權限對不上）、MAC 是 `avc: denied`（SELinux）、seccomp 是 `SIGSYS`（禁用的 syscall）。先讀 log 判斷是哪一層，對症下藥；亂關 SELinux 只是在真機/評估環境未必能用的環境依賴，還讓你不懂原本卡在哪。

## 進階：再往深一層

- **`isolatedProcess` 與更嚴的沙箱**：App 可以宣告某個 Service 跑在 `isolated_app` domain（例如 Chrome 的 renderer），這是一個權限被削到幾乎沒有的極限沙箱——沒有任何權限、UID 是臨時分配的。逆向這類進程時你會發現連平常能做的都做不了，因為 SELinux 把 `isolated_app` 綁得比 `untrusted_app` 還死。這是「縱深防禦」的極致範例。
- **Binder 與 IPC 的信任邊界**：App 被關在 UID 沙箱裡，那它怎麼跟系統服務（PackageManager、location、telephony）要東西？靠 **Binder**——Android 的核心 IPC。Binder 驅動在內核，會把呼叫方的 UID/PID 傳給服務端，讓服務端能用 `Binder.getCallingUid()` 判斷「是誰在呼叫我、有沒有權限」。這是為什麼場景 3 的相機權限在 `cameraserver` 端還能再驗一次——它從 Binder 拿到你的真實 UID，不信任你 App 進程裡的自稱。逆向跨進程的權限繞過、hook 系統服務時，Binder 這條信任邊界是關鍵，Part 3 會用到。
- **SELinux domain transition 的時機**：Zygote fork 出 App 後，是在哪個瞬間從 Zygote 的 domain 轉成 `untrusted_app` 的？這牽涉 `type_transition` 規則與 `setcon`，Ch 37 會拆。理解這個時機，你才懂為什麼在 Zygote 層 hook（早於 transition）能拿到「還沒被關進 App domain」的視窗。
- **權限的 GID 對照表**：`INTERNET`→`inet`(3003)、`WRITE_EXTERNAL_STORAGE`→`sdcard_rw`(1015)、`BLUETOOTH`→`net_bt` 等映射，定義在 AOSP 的 `frameworks/base/data/etc/platform.xml`（`<permission>` → `<group gid>`）。逆向時查這張表能反推「這個 App 的進程被塞了哪些補充 GID、因此能碰哪些內核資源」。
- **`su` 與 Magisk 的 root 是怎麼一回事**：AVD 的 `adb root` 是把 `adbd` 以 root 重啟（emulator/eng build 才允許）；真機沒這待遇，靠 Magisk 這種在 boot image 動手腳、提供一個受控 `su` 的方案來給 root。無論哪種，root 給你的都是「跨過 DAC」的能力，SELinux 那層 Magisk 還得另外裝 sepolicy 補丁放行。這也是為什麼 App 會偵測 root（找 `su` binary、Magisk 特徵）來拒絕在被 root 的裝置上跑——Ch 31 專門講 root 偵測與繞過，你現在先建立「root = 突破 DAC 的鑰匙，但它自己也留下可被偵測的痕跡」的認知。

## 動手練習

1. 在 AVD 上 `adb shell` 進去，`ls -l /data/data`（需要 root）。挑兩個 App，記下它們的 owner（`u0_aXXX`）和權限位。驗證「owner 不同、others 是 `---`」就是沙箱。再 `id` 一下你當前 shell 的身分，理解為什麼沒 root 時你連 `ls /data/data` 都被擋。
2. 找一個宣告 `INTERNET` 的 App 和一個沒宣告的，各 `cat /proc/<pid>/status` 看 `Groups:` 那行，對照有沒有 `3003`。親眼看到「權限 → GID」的映射落在進程屬性上。
3. `adb shell su -c 'cat /some/protected/path'` 故意去讀一個 SELinux 會擋的東西（例如某系統進程的記憶體），看它即使 root 也被 `Permission denied`，然後 `dmesg | grep avc` 找到那條 `avc: denied`，讀懂 `scontext`（你的 domain）/`tcontext`（目標 type）/`tclass`（動作類別）三個欄位。這是你第一次親手讀 SELinux 拒絕日誌。
4. `adb shell getenforce` 確認是 `Enforcing`，`setenforce 0` 改成 `Permissive`，重試練習 3 的動作看它這次過了（只留 log 不擋），再 `setenforce 1` 切回去。體會 MAC 是一道可以「臨時關掉觀察」的獨立牆。
5. 挑一個 App，用 Ch 2 的 apktool 反出它的 `AndroidManifest.xml`，找出所有 `android:exported="true"` 的元件、以及有沒有被自訂 `<permission>` 守著。列一張「不用 root/注入就能從外部戳到的入口」清單——這就是你在做的 App 攻擊面偵察，之後 Ch 9 會把它變成完整流程。
6. 對照本章那張「DAC → MAC → seccomp」三層圖，不看筆記，自己畫一遍，並在每一層旁邊寫上它被擋時的錯誤訊號（`EACCES` / `avc: denied` / `SIGSYS`）。畫得出來，代表你真的把三層防禦內化了。

## 本章重點整理

- **App 進程 = Zygote fork 出的降權子孫**：Zygote 預載 runtime 睡著等，fork 後 `setuid` 掉權成 App 的 UID，再載 App 的 DEX。所有 App 進程同源。
- **沙箱地基是 Unix UID + 檔案權限**：`/data/data/<pkg>` owner 是 App 的 UID、mode `drwx------`，別的 UID 進不去。**Frida 要 root，就是因為跨 UID 注入別人進程需要突破這道 DAC 牆**。
- **權限分 install-time（多半映射內核 GID，如 INTERNET→inet 3003，hook 不掉）與 runtime（framework 軟體檢查，hook 得掉）**；危險權限常多點 + 跨進程檢查，一次 hook 不夠。
- **SELinux 是獨立於 UID 的第二層 MAC，root 也繞不過**：進程有 domain、資源有 type，沒明確 allow 就 deny。被擋看 `avc: denied`；DAC + MAC 兩層都過才放行。

## 自我檢核

- [ ] 不看筆記，能講出一個 App 進程從 Zygote 誕生到降權進沙箱的完整過程
- [ ] 能用「UID + 檔案權限」解釋為什麼 A App 讀不到 B App 的 `/data/data` 目錄
- [ ] 能回答「Frida 為什麼要 root」——注入在突破哪一道牆、root 讓你跨過的是 DAC 還是 MAC
- [ ] 能說出 install-time 與 runtime 權限在**底層機制**上的差別，以及為什麼其中一種 App 層 hook 不掉
- [ ] 能解釋「我 root 了為什麼還被 Permission denied」，並說出去哪裡找 SELinux 的拒絕證據
- [ ] 能講清楚 DAC 與 MAC 是「且」的關係，以及 root 改變了哪一層、改變不了哪一層

## 延伸閱讀

### 官方文件

- **[Android Security Model 概覽](https://source.android.com/docs/security/overview/app-security)** — AOSP
  - **讀哪裡**：App sandbox、UID 分配、permission 那幾節
  - **和本章的關聯**：本章「UID 就是沙箱」的權威出處；官方怎麼描述這套隔離，跟本章對照著讀
- **[Android SELinux 文件](https://source.android.com/docs/security/features/selinux)** — AOSP
  - **讀哪裡**：Concepts（domain/type/context）與 "Implementing SELinux" 開頭；`avc: denied` 怎麼讀那段
  - **為什麼值得讀**：這是你排查「root 了還被擋」的一手依據；SELinux 的每個名詞都在這定義

### 必讀書

- **《Android Security Internals》** — Nikolay Elenkov（No Starch）
  - **讀哪裡**：第 2 章（權限）、第 12 章（SELinux）、進程與 UID 沙箱那幾章
  - **和本章的關聯**：把本章四層（Zygote/UID/權限/SELinux）講到源碼級的權威書。版本偏舊但核心架構沒變
  - **前提知識**：讀過本章建立的框架，這本書填滿每個機制的細節

### 方法論 / 實戰

- **[OWASP MASTG — Android Platform Overview](https://mas.owasp.org/MASTG/0x05a-Platform-Overview/)**
  - **這篇說什麼**：從安全測試視角重講 App 沙箱、權限、IPC
  - **讀哪裡**：Sandbox 與 Permissions 兩節
  - **前提知識**：讀過本章，這頁給你「測試者會怎麼驗證這些隔離」的實作角度
- **[HackTricks — Android Applications Basics](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/android-applications-basics.html)**
  - **這篇說什麼**：UID、權限、SELinux 在滲透實務中的速查
  - **讀哪裡**：sandbox 與 permissions 段落；有可複製的 `adb` 驗證指令
  - **和本章的關聯**：本章動手練習的指令化版本，卡住時來這查

下一章我們鑽進沙箱裡那個 App 進程正在執行的東西——`classes.dex`。我們會逐區塊拆開 DEX 檔案格式、搞懂 Dalvik 這個暫存器式虛擬機跟 JVM 堆疊式的本質差別，還會用 Python 親手解一個 DEX header 的佈局。

→ [Ch 4 Dalvik bytecode 與 DEX 格式深挖](./04-dalvik-dex-format.md)
