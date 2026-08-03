# Final Project — 冷啟動攻堅一個你沒看過的 codebase

> **目標**：把整門課 Ch 0–31 的一切——`reading_code` 的 SOP、六個 codebase 的攻堅經驗、Ch 30 的 pattern 字典、Ch 29 的外化實況、Ch 31 的訓練協定——全部用在一個**這門課從沒碰過、你零背景**的中型 C 專案上。限時（建議 3 小時）產出：架構地圖 + 一條關鍵路徑的完整追蹤 + 至少 3 張 pattern 卡（標明哪些是本課學過的 pattern 在新專案重現）+ 一段費曼複述。這是畢業考——不是學新東西，是證明你真的把「冷啟動攻堅」練成了肌肉。

## 背景：為什麼是「完全沒看過」

本課的六個目標，你都不是真正冷讀——每個 Part 都有偵察章帶你、有官方文件先看、有前面章節鋪墊。那是**帶著護欄的攻堅**。

真正的考驗是**拿掉護欄**：面對一個沒有教材帶路、沒有前置章節、你零背景的專案，能不能在三小時內從「完全看不懂」推進到「畫得出地圖、追得完一條路徑、講得出核心機制、認得出重現的 pattern」。

這正是你職涯反覆遇到的場景：onboarding 陌生系統、審一個沒看過的依賴、貢獻一個新開源專案、接手離職同事的爛攤子。**冷啟動能力——在陌生 codebase 快速建立戰場感知、定位、追蹤、驗證——是這門課要給你的核心資產。** Final 就是驗收它。

這個 Final 刻意**不指定專案**。你自己從候選清單挑一個。挑選本身是考核的一部分（偵察從「決定攻哪個」就開始了）。

## 選一個目標：候選清單

候選都是**真實存在、可 clone、中型、經典、且本課沒碰過**的 C 專案（redis 已被 `reading_code` 用過，排除）。四條標準盡量都滿足：中型（2 萬–15 萬行）、活躍、有測試、陌生但你用過/聽過且能 build。

| 專案 | clone | 規模 | 為什麼適合 / 你會撞見哪些本課 pattern |
|---|---|---|---|
| **jq** | `github.com/jqlang/jq` | ~2.7 萬行 | JSON 處理器，**自帶 bytecode VM**（`src/execute.c`）——直接重現 pattern 1/2/3/12。小、乾淨、有測試 |
| **tmux** | `github.com/tmux/tmux` | ~6 萬行 | 終端多工器，**event loop（libevent）+ client/server 架構**——重現 pattern 5，接你的 networking 直覺 |
| **curl** | `github.com/curl/curl` | ~15 萬行 | 傳輸函式庫，**狀態機式的 multi handle + 協定 plugin**——重現 pattern 7，硬但極經典 |
| **musl libc** | `github.com/bminor/musl` | ~10 萬行 | 極簡 libc，**沒有 VM/event loop，是「純函式庫」的乾淨對照**——練「沒有主迴圈的專案怎麼建地圖」 |
| **memcached** | `github.com/memcached/memcached` | ~2 萬行 | KV cache，**event loop + slab allocator**——重現 pattern 4/5，接 networking |
| **wren** | `github.com/wren-lang/wren` | ~2 萬行 | 小巧腳本語言，**stack-based bytecode VM + GC**——重現 pattern 1/2/8，是 Lua 的姊妹對照 |

**六個都真實可 clone**（本課作者實測 `git ls-remote` 全部命中）。**你該挑一個你覺得最陌生、最不舒服的**——舒適圈外才是成長（Ch 31）。

本 Final 的 `<details>` 示範會用 **jq**（作者真 clone、真讀了一小段當範例），但你該挑一個**不同的**——照抄示範等於沒考。

```bash
# 挑好後 clone（不釘 tag，抓最新即可；記下你的 commit hash 寫進 journal）
git clone --depth 1 https://github.com/jqlang/jq   # 換成你挑的
cd jq && git rev-parse --short HEAD                 # 記下來，這是你的 world line
```

## 完整任務規格：四份交付物 + 碼表

限時 3 小時（可分兩天各 90 分鐘）。全程外化（Ch 29）：寫假設、畫圖、記踩雷、講費曼。

### 交付物 1：架構地圖（偵察，~45 分鐘）

一頁以內：

- **規模體檢**：`cloc` 或 `wc -l src/*.c`——多少行、幾個檔、主要子系統。
- **目錄/檔名地圖**：主要模組怎麼切（`ls`、`tree -L 2`）。用檔名的動詞/名詞猜初版 call graph（Ch 28 的偵察技巧）。
- **entry point(s)**：`rg "int main"` 找所有入口，判斷哪個是主程式、其他是什麼（工具/測試）。
- **核心 struct**：這系統圍繞哪 1-3 個 struct 轉？一句話說明各是什麼。
- **build 狀態**：怎麼編、你有沒有編起來。
- **第一印象 + 三個問題**：這專案給你的感覺 + 你最想搞懂的三件事。

### 交付物 2：一條關鍵路徑的完整追蹤（~75 分鐘）

挑一個**使用者可觀察的行為**（一條命令、一次 API 呼叫、一個輸入怎麼變成輸出），從觸發點追到底：

- **觸發點**：使用者做什麼。
- **完整 call chain**：`funcA → funcB → funcC …`，每一跳標「這裡資料變成什麼、為什麼跳這條」。畫成 ASCII 圖。
- **止步線**：碰到子系統邊界（OS syscall、第三方 lib）就停、記 TODO。
- **至少一處動態驗證**：能 build 就 gdb 下斷點看 backtrace / 變數，貼真實輸出；不能就誠實標「未實測、理論預期」+ 寫清楚該怎麼驗。
- **全程外化**：先猜後讀（寫假設）、被 indirection 騙時記踩雷、每讀懂一塊費曼複述一次。

### 交付物 3：至少 3 張 pattern 卡（~30 分鐘）

用 Ch 30 的格式（beacon / 見於 / 遷移到），為這個新專案萃取至少 3 個 pattern。**關鍵要求：標明哪些是本課字典裡已有的 pattern 在這個新專案重現**（複利的證據），哪些是**新 pattern**（你的字典擴充）。每張卡附**真實檔案:行號**佐證（不准憑記憶）。

### 交付物 4：費曼複述（~30 分鐘）

合上所有檔案，對著空氣（或寫成一段）把這個專案的**核心機制**講給「懂程式但沒讀過這專案的人」聽：

- 不用術語黑話（用了就當場解釋）。
- 每個關鍵斷言背後有 code/執行支撐（不是「我猜」）。
- 通過「別人聽完能複述」的測試。

## 里程碑

把 3 小時切成三個檢查點，每個都有明確的「達到了嗎」：

```
   ┌─ M1（~45 分）偵察地圖 ────────────────────────────
   │   達成標準：畫得出模組分層 + 核心 struct + 主 entry，
   │            並寫下 3 個盲猜假設 + 至少驗證 1 個
   │   卡住訊號：45 分還說不出「這專案圍繞哪個 struct 轉」
   │            → 你在讀細節而非建地圖，退回看目錄/檔名
   │
   ├─ M2（~2 小時）追完一條路徑 ────────────────────────
   │   達成標準：一張完整 call chain 圖，每跳標了資料變化，
   │            至少一處動態驗證（或誠實標未實測+驗法）
   │   卡住訊號：追到一半迷路、忘了在追什麼
   │            → 回看任務界定那行字，砍掉岔路、記 TODO
   │
   └─ M3（~3 小時）pattern 萃取 + 費曼 ─────────────────
       達成標準：≥3 張 pattern 卡（標明重現 vs 新），
                費曼複述講得順（卡住處=沒讀透，標記）
       卡住訊號：pattern 卡湊不出 3 張
                → 對照 Ch 30 字典 beacon 欄，多半是你沒認出重現的
```

## 驗收標準（rubric）

給自己打分，每項 0/1/2（0=沒做到、1=部分、2=完整）：

| 面向 | 2 分的樣子 |
|---|---|
| **偵察收斂** | 45 分內畫出可信地圖，明確劃了「不讀」的邊界，沒陷入細節 |
| **假設驅動** | 全程有「先猜後讀」的假設，至少一個被推翻並記成教訓 |
| **路徑完整** | call chain 從觸發追到止步線，每跳標了資料變化，圖清楚 |
| **動態驗證** | 有 gdb/trace 實證（或誠實標未實測+具體驗法），不編假輸出 |
| **pattern 萃取** | ≥3 張卡有真檔案:行號佐證，明確標了「重現 vs 新」 |
| **複利證據** | 至少 1 張卡是本課字典 pattern 在新專案重現，你指出了 beacon |
| **費曼品質** | 講得順、無黑話、每個斷言有 code 支撐、別人聽得懂 |
| **外化紀律** | journal 有假設/圖/踩雷/TODO，過程可回看 |

**14 分以上 = 畢業。** 低於 10 分不是失敗——是告訴你哪個環節還沒成肌肉，回對應章節（偵察→`reading_code` Ch 5 / Ch 28；路徑→Ch 29；pattern→Ch 30）再練一次。

## 這個專案用到本課哪些技巧（對照表）

不管你挑哪個候選，攻堅時會動用的本課技巧：

| 攻堅動作 | 本課出處 |
|---|---|
| 60 分鐘偵察建地圖 | `reading_code` Ch 5、本課 Ch 28 偵察段 |
| 用檔名/目錄猜 call graph | 本課 Ch 28（`exec*.c` vs `node*.c`）|
| 收斂到一條路徑、劃界「不讀」 | `reading_code` Ch 11、本課 Ch 28 |
| 先猜後讀、假設驅動 | 本課 Ch 29 |
| 追 data flow、每跳標資料變化 | `reading_code` Ch 8、本課 Ch 29 |
| 被函式指標/macro indirection 騙 | 本課 Ch 27（macro dispatch）、Ch 28（函式指標）|
| gdb 動態驗證 backtrace | `reading_code` Ch 18、本課 Ch 29 |
| 外化：畫圖 + journal + TODO | `reading_code` Ch 35、本課 Ch 29 |
| 費曼複述測懂沒懂 | `reading_code` Ch 36、本課 Ch 29 |
| 對照 beacon 認出 pattern | 本課 Ch 30 pattern 字典 |
| 認出 VM → 填三欄模板 | 本課 Ch 27 三 VM 對照 |

## `<details>` 示範：攻堅 jq 的一小段（作者真 clone、真讀）

<details>
<summary>展開看作者對 jq 的一段真實攻堅片段（你該挑不同專案，別照抄）</summary>

**環境**：`git clone --depth 1 https://github.com/jqlang/jq`，作者讀的 commit `603db3f`。~2.7 萬行（`cat src/*.c src/*.h | wc -l` ≈ 26838）。

**M1 偵察（真做的）：**

```bash
$ ls src/*.c | head
src/builtin.c  src/bytecode.c  src/compile.c  src/execute.c
src/jv.c  src/jv_parse.c  src/lexer.c  src/main.c  ...
```

檔名先建地圖：`lexer.c`/`parser.c`/`compile.c`/`bytecode.c` = 前端（把 jq 程式編成 bytecode），`execute.c` = **執行 bytecode 的 VM**（beacon 名字命中 pattern 1），`jv.c`/`jv.h` = JSON 值的表示。

盲猜假設（先猜後讀）：
- H1：jq 程式先編成某種 bytecode，再由一個 VM 執行 → `compile.c` + `execute.c`。
- H2：JSON 值大概是 tagged union（pattern 2）→ 看 `jv.h`。

驗 H1，`rg "int main" src/main.c` → `main.c:289`。讀 `main.c` 主流程（`main.c:177-179`）：

```c
/* src/main.c:177 (jq @603db3f) 節選 */
  jq_start(jq, value, flags);
  while (jv_is_valid(result = jq_next(jq))) {
```

**H1 命中**：`jq_next` 是 pull-based——反覆呼叫吐一個結果（這同時命中 pattern 10 火山模型的味道！jq 的 VM 是 pull 的）。

**M2 追路徑：`jq_next` 的 dispatch loop。** 進 `src/execute.c:340`：

```c
/* src/execute.c:340 (jq @603db3f) 節選 */
jv jq_next(jq_state *jq) {
  ...
  while (1) {
    ...
    uint16_t opcode = *pc;
    ...
    pc++;
    switch (opcode) {          /* ← pattern 1！純 switch dispatch */
    ...
    case DUP: {                /* ← pattern：stack machine 的 beacon */
      jv v = stack_pop(jq);
      stack_push(jq, jv_copy(v));
      stack_push(jq, v);
      break;
    }
    ...
```

`stack_pop`/`stack_push` → **jq 是 stack machine**（對照 Ch 27：看 opcode 帶不帶 operand 位置、用不用 stack）。dispatch 是**純 switch**（不像 Lua/CPython 的 computed goto——jq 選了 SQLite 那派的可攜路線）。

**M3 pattern 萃取（jq 的三張卡，全標「重現」+ 真行號）：**

**卡 A — bytecode VM dispatch（pattern 1 重現）**
- beacon：`while(1)` + `uint16_t opcode = *pc; pc++; switch(opcode)`（`execute.c:351-400`）
- 這次的個性（填 Ch 27 三欄）：**stack machine**（`stack_pop`/`stack_push`，`execute.c:424` DUP）、**純 switch** dispatch（不賭 computed goto）、值是 `jv`（見卡 B）。跟 SQLite 同派（可攜優先），跟 Lua/CPython 不同（它們賭 goto）。
- 遷移確認：這是我在 Lua/SQLite/CPython 認過的第四個 VM——照三欄模板，20 分鐘填完。**複利兌現。**

**卡 B — tagged union（pattern 2 重現）**
- beacon：`struct { unsigned char kind_flags; ...; union { struct jv_refcnt* ptr; double number; } u; } jv`（`jv.h:34-43`）
- 個性：小值內嵌（`double number` 直接躺在 union 裡），大值（string/array/object）用 `struct jv_refcnt* ptr` 指向堆上。**這正是 Lua `TValue` 的世界觀**（`kind_flags` = tag、union 內嵌小值）。
- 遷移確認：認出 Lua 的 tagged union 之後，讀 jq 的 `jv` 一眼就懂 `kind_flags` 是 tag、`u` 是 union。

**卡 C — refcount（pattern 3 重現，但混 tagged union）**
- beacon：`jvp_refcnt_inc`/`jvp_refcnt_dec`（`jv.c:63/67`）、`jv_copy` = incref、`jv_free` = decref；註解 `jv.h:46`「All jv_* functions consume (decref) input and produce (incref) output」
- 有趣的變體：jq **不是** CPython 那種「everything boxed」——它是 tagged union（小值內嵌免 refcount）+ 只有堆上的大值走 refcount（`jv_refcnt`）。**這是 pattern 2 和 pattern 3 的混血**，比 Lua（純 GC）和 CPython（純 refcount）都不同——這是一張帶「變體」的卡，字典擴充！

**費曼複述（jq 核心機制，講給沒讀過 jq 的人）：**

> 「jq 執行一個 filter，其實是先把 filter 文字編譯成一串 bytecode（`compile.c`），再由 `execute.c` 的 `jq_next` 這個 stack-based 虛擬機一條一條跑。它是 pull 的：主程式反覆呼叫 `jq_next`，每次吐一個 JSON 結果，吐完為止——所以 jq 天然支援一個輸入產生多個輸出。JSON 值用 `jv` 表示，是個 tagged union：小值（數字、bool）直接內嵌，大值（字串、陣列）指向堆上一個帶 refcount 的物件，靠 `jv_copy`（+1）和 `jv_free`（-1）管生死。所以 jq 的值表示是『Lua 的 tagged union』和『CPython 的 refcount』的混血——小值學 Lua、大值學 CPython。」

**這段攻堅約 40 分鐘**（因為 jq 命中三個我已熟的 pattern，全是填空題不是閱讀理解題）——這就是 Ch 27「第四個 VM 30 分鐘上手」和 Ch 31「複利飛輪」的實證。

**你的任務**：挑一個**不是 jq** 的候選（tmux/curl/musl/memcached/wren），做完整四份交付物。tmux/memcached 會讓你撞見 event loop（pattern 5），musl 會逼你練「沒有主迴圈的函式庫怎麼建地圖」，wren 是另一個 VM（跟 jq/Lua 對照），curl 是狀態機 + plugin（pattern 7）——每個都會重現不同的字典 pattern。

</details>

## 自我檢核

- [ ] 我挑的是本課六個目標之外、我零背景的專案（不是照抄 jq 示範）
- [ ] 我在 45 分鐘內產出了可信的架構地圖，並明確劃了「不讀」的邊界
- [ ] 我全程有「先猜後讀」的假設，至少一個被推翻並記成教訓
- [ ] 我追完了一條路徑，call chain 圖每跳標了資料變化，在止步線停住
- [ ] 我做了動態驗證（gdb backtrace）或誠實標「未實測」+ 寫清楚驗法，沒編假輸出
- [ ] 我萃取了 ≥3 張 pattern 卡，全有真檔案:行號，明確標了「重現 vs 新」
- [ ] 我至少有 1 張卡是本課字典 pattern 在新專案重現，且我指出了 beacon（複利證據）
- [ ] 我的費曼複述講得順、無黑話、每個斷言有 code 支撐
- [ ] 我的 journal 完整（假設/圖/踩雷/TODO），過程可回看

## 做完你站在哪

做完這個 Final，你證明了一件具體的事：**面對一個零背景的中型 codebase，你能在三小時內從「完全看不懂」推進到「畫得出地圖、追得完一條路徑、認得出重現的 pattern、講得出核心機制」。**

這不是「讀過六個名專案」的知識——那會過期。這是一個**可複製的技能**：換任何第七、第八、第N個陌生專案，你都能套同一套動作。你腦裡有了 Ch 30 的 pattern 字典（十二張卡起跳），手上有了 Ch 29 的外化紀律，習慣上有了 Ch 31 的訓練協定讓字典持續長大。

**下一步不是停在這裡，是把 Final 變成常態**（Ch 31）：每季挑一個舒適圈外的硬 codebase，限時攻堅一次，pattern 字典就會持續複利。你和「只讀過自己專案的工程師」的差距，會從這一刻起逐年拉開——因為你不再從零讀任何東西，你認出 pattern、填模板、驗細節。

你畢業了。健身房隨時歡迎你回來——帶下一個更硬的目標。

---

**回到課程地圖**：[README](./README.md) ｜ **前一章**：[Ch 31 打造持續讀碼的訓練習慣](./31-sustained-reading-practice.md)
