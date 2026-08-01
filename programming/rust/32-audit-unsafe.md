# Ch 32 — audit unsafe：cargo-geiger/cargo-audit

> **目標**：把 [Ch 31](./31-unsafe-vuln-classes.md) 的「人工判單一段 unsafe」升級成「自動掃整棵依賴樹」的稽核流程。學完你能：（1）用 `cargo audit` 比對 RUSTSEC 資料庫、揪出依賴樹裡的已知漏洞；（2）用 `cargo geiger` 量化依賴樹的 unsafe 用量，決定人工 review 的優先順序；（3）知道 `cargo deny`（政策）、`cargo vet`/`cargo crev`（信任審查）各補什麼洞；（4）人工 review 一段 unsafe 判斷 sound 與否；（5）用 `#![forbid(unsafe_code)]`、`cargo tree`、SBOM 建立可稽核性；（6）把整套流程塞進 CI。

> **環境**：Rust `rustc 1.97.1`（stable）與 nightly，x86-64 Linux（WSL2）。工具版本：`cargo-audit 0.22.2`、`cargo-geiger 0.13.0`，本機真跑（`cargo install` 裝好）。所有工具輸出都是本機對真實依賴（`time`、`dotenv`、`libc`）跑出來的，非推測；標「未實測」處會明說。RUSTSEC 編號在 rustsec.org 查證過。

## 為什麼需要這個？

[Ch 31](./31-unsafe-vuln-classes.md) 教你判斷**一段** unsafe 是否 sound。但 [Ch 30](./30-security-boundary.md) 的信任邊界圖告訴你，真正的攻擊面在**依賴樹**——你的專案可能有上百個 transitive 依賴，每一個都有自己的 unsafe、FFI、`build.rs`。你不可能手動讀完每一個 crate 的每一行 unsafe，那是幾十萬行。

所以稽核策略必須分層：

```
   第一層：已知漏洞    ── cargo audit ──▶ 這棵樹裡有沒有「已經被登記為有漏洞」的 crate?
                                          （比對 RUSTSEC，秒級，必跑，最高 CP 值）
   第二層：unsafe 量測  ── cargo geiger ─▶ 哪些 crate unsafe 用得多? 排出人工 review 優先序
   第三層：政策閘門     ── cargo deny ───▶ 禁止特定 license/來源/重複版本/漏洞，CI 擋 PR
   第四層：信任審查     ── cargo vet ────▶ 「這個 crate 版本有沒有人審過?」建立信任鏈
   第五層：人工 review  ── 你的眼睛 + Ch 31 判準 ─▶ 對高風險 unsafe 逐段判 soundness
```

這一章把這五層工具鏈一個一個講清楚、能裝的真跑給你看輸出。核心心法：**自動化負責「篩」，人工負責「判」。** 工具幫你把幾十萬行縮到「這三個 crate 的這幾段 unsafe 值得看」，剩下的用 Ch 31 的判準人工過。

## 先建立直覺

想像你接手一個 Rust 專案的安全審計。天真的做法是打開 `Cargo.toml`，一個依賴一個依賴去 GitHub 讀原始碼——上百個依賴，你讀到天荒地老，而且讀了也不知道哪個重要。

正確的做法是像分診（triage）：先用便宜的自動掃描把「已經確定有問題的」和「風險高的」篩出來，把稀缺的人工注意力花在刀口上。

```
   幾十萬行依賴程式碼
          │
          ▼  cargo audit（秒級）
   ┌──────────────────────┐
   │ 3 個 crate 有已知漏洞 │ ◀── 先修這個！有 patch 就升版，秒解
   └──────────────────────┘
          │
          ▼  cargo geiger（分鐘級）
   ┌──────────────────────────────┐
   │ 依 unsafe 用量排序的 crate 表 │ ◀── unsafe 多 + 沒名氣 = 優先人工看
   └──────────────────────────────┘
          │
          ▼  人工 review（時級，用 Ch 31 判準）
   ┌──────────────────────────────┐
   │ 對前幾名的 unsafe 逐段判 sound │
   └──────────────────────────────┘
```

這個漏斗的每一層都在**縮小人工要看的量**。`cargo audit` 幾乎零成本、必跑；`cargo geiger` 幫你排序；到你親自讀 code 時，已經是「這幾百行值得看的 unsafe」，不是「幾十萬行全部」。

## 第一層：`cargo audit` —— 比對 RUSTSEC 找已知漏洞

`cargo audit` 讀你的 `Cargo.lock`（精確鎖定的依賴版本），跟 RUSTSEC advisory database（[Ch 31](./31-unsafe-vuln-classes.md) 介紹過）比對，報出「你依賴的某個 crate 的某個版本，登記在案有漏洞」。

安裝與版本：

```
$ cargo install cargo-audit
   Installed package `cargo-audit v0.22.2` (executable `cargo-audit`)
$ cargo audit --version
cargo-audit-audit 0.22.2
```

（本機真跑。）做一個含已知漏洞依賴的專案來看真實輸出。這裡故意 pin 一個舊版 `time`——`time 0.1.x` 有 RUSTSEC-2020-0071（在多執行緒程式裡可能 segfault 的 DoS，[Ch 30](./30-security-boundary.md) 延伸閱讀提過同類）：

```toml
# Cargo.toml 片段
[dependencies]
time = "=0.1.45"
```

跑 `cargo audit`：

```
    Fetching advisory database from `https://github.com/RustSec/advisory-db.git`
      Loaded 1177 security advisories (from /home/ypp/.cargo/advisory-db)
    Updating crates.io index
    Scanning Cargo.lock for vulnerabilities (7 crate dependencies)
Crate:     time
Version:   0.1.45
Title:     Potential segfault in the time crate
Date:      2020-11-18
ID:        RUSTSEC-2020-0071
URL:       https://rustsec.org/advisories/RUSTSEC-2020-0071
Severity:  6.2 (medium)
Solution:  Upgrade to >=0.2.23

error: 1 vulnerability found!
```

（本機真跑，`cargo-audit 0.22.2`，advisory-db 載入 1177 筆。）看這份輸出的每一欄：

- **Crate / Version**：`time 0.1.45`——它精確指到 `Cargo.lock` 裡那個版本。
- **ID**：`RUSTSEC-2020-0071`——advisory 編號，點 URL 看完整成因。
- **Severity**：`6.2 (medium)`——CVSS 分數（有些 advisory 有，有些沒有）。
- **Solution**：`Upgrade to >=0.2.23`——**最有用的一欄**。多數已知漏洞的解法就是升版，秒解。
- 最後 `error: 1 vulnerability found!`，且 exit code 非 0——這就是為什麼它能當 CI 的閘門（下面會用）。

再加一個 `dotenv`（已停止維護），看**警告**（warning，非 vulnerability）長什麼樣：

```
Crate:     time
Version:   0.1.45
Title:     Potential segfault in the time crate
...（同上）...

Crate:     dotenv
Version:   0.15.0
Warning:   unmaintained
Title:     dotenv is Unmaintained
Date:      2021-12-24
ID:        RUSTSEC-2021-0141
URL:       https://rustsec.org/advisories/RUSTSEC-2021-0141

error: 1 vulnerability found!
warning: 1 allowed warning found
```

（本機真跑。）注意 `dotenv` 那筆是 **Warning: unmaintained**，不是 vulnerability。分類差異（[Ch 31](./31-unsafe-vuln-classes.md) 表格提過）在這裡兌現：`unmaintained`/`unsound`/`notice` 這類是 informational warning，預設**不會**讓 exit code 失敗（除非你用 `--deny warnings`）；只有真正的 `vulnerability` 才 `error`。`unmaintained` 的意思是「這 crate 沒人維護了，未來出漏洞不會有人修」——不是現在有洞，是未來風險。

**處理已知漏洞的順序**：

1. 有 patched 版本 → 直接升版（`cargo update -p time --precise 0.2.23` 或改 `Cargo.toml`），最省事。
2. 沒 patched 版本、但你不觸發那條漏洞路徑 → 評估後可用 `--ignore RUSTSEC-XXXX-YYYY` 暫時忽略，但要留紀錄。
3. 沒 patched、又真的觸發 → 換 crate，或自己 fork 修。

`--ignore` 實測（把 `time` 那筆忽略掉，就只剩 `dotenv` 的 warning）：

```
$ cargo audit --ignore RUSTSEC-2020-0071
...
Warning:   unmaintained
Title:     dotenv is Unmaintained
ID:        RUSTSEC-2021-0141
...
warning: 1 allowed warning found
```

（本機真跑——`time` 那筆被忽略，exit 回到成功。）`cargo audit` 還能吐 JSON（`--json`）給 CI/dashboard 吃，結構長這樣（節錄）：

```json
{"database":{"advisory-count":1177,...},
 "vulnerabilities":{"found":true,"count":1,
   "list":[{"advisory":{"id":"RUSTSEC-2020-0071","package":"time",
     "title":"Potential segfault in the time crate",...}}]}}
```

（本機真跑，`cargo audit --json` 節錄。）

> `cargo audit` 只查**已知**漏洞——已經被登記進 RUSTSEC 的。它對「還沒被人發現的 unsound」一無所知。它是「別踩已知的坑」，不是「證明沒坑」。這是它和後面 `cargo geiger` + 人工 review 的分工。

## 第二層：`cargo geiger` —— 量化 unsafe 用量

`cargo audit` 查已知漏洞，但依賴樹裡**還沒被登記**的風險呢？`cargo geiger`（名字取自蓋革計數器——偵測「輻射」= unsafe）掃你的依賴樹，統計每個 crate 用了多少 unsafe。它不判斷 sound/unsound（那要人工），它給你一個**量化的風險地圖**，告訴你「往哪裡看」。

安裝與跑（對一個依賴 `libc` 的專案）：

```
$ cargo install cargo-geiger
   Installed package `cargo-geiger v0.13.0`
$ cargo geiger
```

真實輸出：

```
Metric output format: x/y
    x = unsafe code used by the build
    y = total unsafe code found in the crate

Symbols:
    :) = No `unsafe` usage found, declares #![forbid(unsafe_code)]
    ?  = No `unsafe` usage found, missing #![forbid(unsafe_code)]
    !  = `unsafe` usage found

Functions  Expressions  Impls  Traits  Methods  Dependency

0/0        0/0          0/0    0/0     0/0      ?  geiger_demo 0.1.0
0/93       35/738       2/10   0/0     8/101    !  └── libc 0.2.189

0/93       35/738       2/10   0/0     8/101
```

（本機真跑，`cargo-geiger 0.13.0`，專案只依賴 `libc 0.2.189`。）讀法：

- **`x/y` 格式**：`x` = 這次 build 實際用到的 unsafe、`y` = 該 crate 裡總共有的 unsafe。例如 `libc` 的 Expressions 是 `35/738`——它有 738 個 unsafe expression，這次 build 用到 35 個。
- **符號欄**：`?` = 沒用 unsafe 但**也沒**宣告 `#![forbid(unsafe_code)]`（你的 `geiger_demo` 本身）；`!` = 有 unsafe（`libc`）；`:)` = 沒 unsafe 且明確 forbid（最讓人放心）。
- **五個計數欄**（Functions/Expressions/Impls/Traits/Methods）：unsafe 出現在哪種語法位置。`libc` 這種 FFI binding crate unsafe 天生就多（它整個工作就是包 C），這是**預期**的，不是紅旗。

怎麼用這份表做 triage？看兩個維度的**乘積**：

```
   風險 ≈ unsafe 用量  ×  信任缺口
          （geiger 數字）  （這 crate 多少人在看、多有名）

   libc: unsafe 多，但它是生態核心、無數雙眼睛盯著 → 信任缺口小 → 不急
   某個 300 downloads、你沒聽過、卻有一堆 unsafe 的小 crate → 信任缺口大 → 優先看!
```

`cargo geiger` 給你左邊那個數字；右邊那個「信任缺口」要你自己補（downloads、maintainer、有沒有出過 RUSTSEC）。**unsafe 多不等於危險**（`libc` 就是反例），但「unsafe 多 + 沒人看 + 沒名氣」是最該優先人工 review 的組合。

> 認識論誠實：`cargo geiger` 是**計數**工具，不是**判斷**工具。它數 unsafe 的**數量**，不評估**品質**。一個 crate 可能只有 1 個 unsafe 但那 1 個是 unsound（危險），另一個有 500 個 unsafe 但全 sound（安全）。geiger 幫你排序、縮小範圍，最終的 sound/unsound 判斷還是要人工用 [Ch 31](./31-unsafe-vuln-classes.md) 的判準做。geiger 也有已知限制：它靠掃 token，對 macro 展開出來的 unsafe、`build.rs` 裡的行為可能數不準——把它當「粗略地圖」而非「精確清單」。

## 第三、四層：`cargo deny` 與 `cargo vet`/`cargo crev`

這兩層我在本機**未實測**（`cargo deny` 安裝需另外 build，`cargo vet`/`crev` 需要 import 信任集，離線環境跑不出有意義的輸出），以下說明用途與典型使用方式，標明是理論預期。

### `cargo deny`：政策閘門

`cargo deny` 是「把稽核規則寫成政策、在 CI 強制執行」的工具。你在 `deny.toml` 訂規則，它掃依賴樹、違反就讓 build 失敗。四大類政策：

- **`advisories`**：包 `cargo audit` 的功能（比對 RUSTSEC），可設「發現 vulnerability 就 deny」。
- **`licenses`**：禁止不相容的授權（例如公司政策禁 GPL），或要求所有依賴都在 allowlist 內。
- **`bans`**：禁止特定 crate、禁止**同一個 crate 出現多個版本**（依賴膨脹與重複 unsafe 的來源）、禁止特定來源。
- **`sources`**：限制 crate 只能來自允許的 registry/git（防 typosquat 從奇怪來源拉東西）。

典型 `deny.toml`（示意）：

```toml
[advisories]
# 發現任何 RUSTSEC vulnerability 就讓 CI 失敗
version = 2

[bans]
# 禁止同一 crate 多版本並存（會拖進重複的 unsafe 與體積）
multiple-versions = "deny"

[licenses]
version = 2
allow = ["MIT", "Apache-2.0", "BSD-3-Clause"]  # 只允許這些授權
```

**未實測**，理論預期：`cargo deny check` 會分別跑 advisories/licenses/bans/sources 四項，任一違反就非零退出。它比 `cargo audit` 多的是「license 合規」和「依賴結構政策」——對企業/開源專案的供應鏈治理很重要，audit 只管漏洞。

### `cargo vet` / `cargo crev`：信任審查

`cargo audit`/`geiger` 都是「掃 code」；`cargo vet`（Mozilla 出）和 `cargo crev` 換一個角度——**「這個 crate 版本，有沒有可信的人審查過並簽名背書？」**

概念：

```
   問題：你信任一個 crate，其實是信任「它的作者 + 它的每一次更新」。
   cargo vet 的答案：建立一個「已審查版本」的清單（supply-chain/audits.toml），
                     每筆記錄「某人審過 crate X 的版本 Y，認證它沒惡意/沒 unsound」。
                     可以匯入別的組織（如 Mozilla、Google）公開的審查集，共享信任。
```

`cargo vet` 的流程（**未實測**，理論預期）：第一次 `cargo vet init` 把現有依賴標為「暫時信任」，之後每次新增依賴或升版，`cargo vet` 會要求「這個新版本需要有人審查或匯入他人的審查」，否則 CI 失敗。`cargo crev` 類似但更去中心化，是一個開放的 code review web-of-trust。

這一層補的洞是 `audit`/`geiger` 都補不了的：**供應鏈信任**。一個 crate 可能沒有已知漏洞（audit 過關）、unsafe 也不多（geiger 好看），但它上週被盜帳號投了惡意的 `build.rs`——只有「有人實際審查過這個版本」能抓到。這是 [Ch 30](./30-security-boundary.md) 「線三/線四」（build.rs + 依賴樹）的正面防禦。

## 第五層：人工 review 一段 unsafe

工具篩完，最後靠你的眼睛。示範用 [Ch 31](./31-unsafe-vuln-classes.md) 的三個檢查點 review 兩段 unsafe，一 sound 一 unsound。

### 判 sound：一段 `split_at_mut` 風格的 unsafe

```rust
/// Sound：回傳一個 slice 的兩個不重疊可變半段。
pub fn split_at_mut(v: &mut [u32], mid: usize) -> (&mut [u32], &mut [u32]) {
    let len = v.len();
    assert!(mid <= len); // 強制前提 -> sound
    let ptr = v.as_mut_ptr();
    unsafe {
        // SAFETY: 上面已檢查 mid <= len；兩個 slice 覆蓋 disjoint 的
        // [0,mid) 與 [mid,len)，永不 alias 同一個元素的 &mut。
        (
            std::slice::from_raw_parts_mut(ptr, mid),
            std::slice::from_raw_parts_mut(ptr.add(mid), len - mid),
        )
    }
}
```

套三個檢查點：

1. **依賴什麼前提？** `from_raw_parts_mut` 要求指標有效、長度不越界、且兩個回傳的 `&mut` 不 alias。
2. **前提被強制還是被假設？** `assert!(mid <= len)` **強制**了 `mid` 合法；兩段 `[0,mid)` 與 `[mid,len)` 在數學上不相交，所以兩個 `&mut` 天然不 alias——這是被 API 結構**保證**的，不是假設。
3. **有 `// SAFETY:` 嗎？** 有，且說清楚了「為什麼不 alias」。

結論：**sound**。用 Miri 驗證（跑它的測試）：

```
$ cargo +nightly miri test
test tests::splits ... ok
test result: ok. 1 passed; 0 failed; ...
```

（本機真跑。）Miri 綠——注意這是「這個測試走的路徑沒 UB」，不是 soundness 證明（[Ch 31](./31-unsafe-vuln-classes.md) 踩雷 5），但配合上面的人工論證，我們有信心它 sound。

### 判 unsound：對照 Ch 31 的 `nth`

回顧 [Ch 31](./31-unsafe-vuln-classes.md) 類二那段 `nth`：它用 `get_unchecked(i)` 但**沒有** `assert!(i < len)`。套檢查點：前提是「`i < len`」，但它被**假設**而非強制，也沒有 `// SAFETY:` 交代為什麼 `i` 一定合法。結論：**unsound**——存在 safe 呼叫（`nth(&v, 5)`）觸發 OOB。

**人工 review 的心法**：對每一段 unsafe，找出它的 precondition，然後問「這個 precondition 是被程式碼**強制**成立，還是被**假設**成立？」。強制（assert/型別/結構）= 有機會 sound；假設（靠呼叫者自律，但 API 是 safe 的）= unsound。`// SAFETY:` 註解的存在與品質是最快的第一印象——沒有註解的 unsafe，作者八成沒認真想過前提。

## 可稽核性設計：讓 unsafe 好審

稽核不只是「事後掃」，更是「事前設計得好審」。幾個工具與慣例：

### `#![forbid(unsafe_code)]`：宣告「這個 crate 零 unsafe」

在 crate 根加這一行，編譯器**禁止**任何 unsafe，連 `unsafe {}` 區塊都編不過：

```rust
#![forbid(unsafe_code)]

fn main() {
    let x = 42;
    let p = &x as *const i32;
    let y = unsafe { *p }; // 想解引用裸指標
    println!("{}", y);
}
```

編譯：

```
error: usage of an `unsafe` block
 --> src/main.rs:7:13
  |
7 |     let y = unsafe { *p };
  |             ^^^^^^^^^^^^^
  |
note: the lint level is defined here
 --> src/main.rs:1:11
  |
1 | #![forbid(unsafe_code)]
  |           ^^^^^^^^^^^
```

（本機真跑。）`forbid`（比 `deny` 更強，連 crate 內部都不能用 `#[allow]` 覆蓋）讓一個 crate 對稽核者做出一個**機器可驗證的承諾**：「我這裡沒有 unsafe，你不用審記憶體安全，只要審邏輯。」這正是 `cargo geiger` 輸出裡那個 `:)` 符號的意思。對純邏輯的 crate（parser、資料處理），加這行是很強的可稽核性訊號。

### `cargo tree`：看清依賴結構

稽核前先看清你到底信任了誰。`cargo tree` 印出完整依賴樹：

```
$ cargo tree
audit_demo2 v0.1.0 (/tmp/audit_demo2)
├── dotenv v0.15.0
└── time v0.1.45
    └── libc v0.2.189
```

（本機真跑。）這棵樹告訴你：你直接依賴 `dotenv` 和 `time`，而 `time` 又把 `libc` 拖進來（transitive）。`cargo tree -i <crate>`（invert）能反查「誰把某個可疑 crate 拉進來的」——查到那個問題 crate 的來源路徑時很有用。`cargo tree` 是 SBOM 的基礎（見下）。

### SBOM：軟體物料清單

SBOM（Software Bill of Materials，軟體物料清單）是「這個軟體用了哪些元件、什麼版本」的正式清單，供應鏈安全合規（如美國行政命令 EO 14028）常要求。Rust 生態可用 `cargo cyclonedx`（產 CycloneDX 格式）或 `cargo sbom` 從 `Cargo.lock` 生成。**未實測**（需另裝），理論用途：把你的完整依賴樹（含版本、授權、來源）匯出成標準格式，讓下游/稽核方能自動比對「你用的某版本是不是後來被登記了漏洞」。概念上 SBOM = `cargo tree` + 版本 + 授權 + hash 的機器可讀版。

## 實務：把稽核塞進 CI

單機跑一次沒用，要**每次 PR 自動跑**，讓漏洞進不了 main。一個 GitHub Actions 的最小配置（示意，未在本環境跑 CI，但每條指令都本機驗證過）：

```yaml
# .github/workflows/audit.yml
name: security-audit
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - name: Install cargo-audit
        run: cargo install cargo-audit
      - name: Run cargo audit          # 有已知漏洞就 fail（exit != 0）
        run: cargo audit
      # 選配：Miri 跑測試抓 unsafe UB
      - uses: dtolnay/rust-toolchain@nightly
        with:
          components: miri
      - name: Run Miri on tests
        run: cargo +nightly miri test
```

關鍵：`cargo audit` 發現 vulnerability 會非零退出（前面實測過 `error: 1 vulnerability found!`），CI 就把這個 PR 擋下來。這就是為什麼那個 exit code 重要。

一套務實的分層 CI 策略：

- **每次 PR**：`cargo audit`（快，必跑）+ `cargo deny check`（政策）。
- **定期（每日/每週）排程**：即使沒 PR 也跑 `cargo audit`——因為**新的 advisory 隨時會登記**，你今天沒漏洞不代表明天沒有（`time 0.1.45` 在 2020 年之前也是「乾淨」的）。這是稽核和一般測試最大的不同：**你的程式碼沒變，但世界對它的認知變了。**
- **有 unsafe 的 crate**：CI 加 `cargo +nightly miri test`，讓 [Ch 31](./31-unsafe-vuln-classes.md) 那四類 UB 在 CI 就被抓（前提是測試覆蓋到那些 unsafe 路徑）。
- **上 unsafe 較多的專案**：`cargo geiger` 定期產報告追蹤 unsafe 用量趨勢（突然暴增可能是引入了可疑依賴）。

## 踩雷集錦

1. **「`cargo audit` 過了就安全」**：它只查**已知**（登記進 RUSTSEC）的漏洞。還沒被發現的 unsound、你自己寫的 unsafe bug，它完全看不到。它是「別踩已知的坑」，不是「證明沒坑」。要配 geiger + Miri + 人工 review。

2. **「`cargo geiger` 數字大 = 危險」**：unsafe **數量**不等於**危險**。`libc` 有幾百個 unsafe 但它是生態核心、sound。geiger 是排序工具不是判斷工具——「unsafe 多 + 沒名氣 + 沒人看」才是紅旗，單看數字會錯殺 `libc` 這種正當的 FFI crate。

3. **「audit 一次就好」**：大錯。RUSTSEC 每天有新 advisory 登記。你的 `Cargo.lock` 沒變，但某個依賴可能昨天被登記了漏洞。必須**定期排程**跑 audit，不是一次性。這是供應鏈稽核和普通測試的本質差異。

4. **「`--ignore` 掉煩人的 advisory 就沒事」**：`--ignore` 是「我評估過、暫時接受這個風險」的紀錄，不是「讓它閉嘴」。每個被 ignore 的 RUSTSEC 都該有註解說明為什麼可以接受（不觸發那條路徑？沒 patch 又非換不可？），並定期回頭看有沒有 patch 了。無腦 ignore = 把警報器拆掉。

5. **忘了 `build.rs`/proc-macro 這條線**：`cargo audit` 查的是「依賴有沒有已知漏洞」，`cargo geiger` 數的是 runtime unsafe——兩者都**不太覆蓋** `build.rs`/proc-macro 的編譯期任意執行風險（[Ch 30](./30-security-boundary.md) 線三）。這條線目前主要靠 `cargo vet`/`crev` 的人工審查、和「不隨便加沒名氣的依賴」的紀律。別以為工具全過就代表 build 階段安全。

## 進階：再往深一層

**`cargo audit` 的 advisory-db 是怎麼更新的？** 它 clone `rustsec/advisory-db`（GitHub 上的 git repo，每筆 advisory 是一個 `.md` + TOML metadata），跑 audit 時預設會 fetch 最新。離線環境（如本課某些工具「未實測」的原因）跑不了 fetch，但可以用 `--no-fetch` 吃本機快取的 db——代價是可能漏掉最新 advisory。企業內網常自建 advisory-db mirror。理解這點對「為什麼要定期跑」很關鍵：audit 的價值來自 db 的新鮮度。

**量化整個生態的 unsafe：`cargo-geiger` 的大規模應用。** 有研究拿 geiger 或類似工具掃整個 crates.io，統計「有多少比例的 crate 完全不用 unsafe」「unsafe 集中在哪類 crate（FFI binding、資料結構、no_std）」。結論通常是：絕大多數應用層 crate 零 unsafe（`#![forbid]` 或自然沒有），unsafe 高度集中在少數基礎設施 crate（`libc`、`memmap`、各種 `-sys` binding、lock-free 資料結構）。這對稽核的啟示：**你的 unsafe 風險其實高度集中**——盯緊那幾個基礎 crate（它們通常也是最多人看的），比平均用力有效得多。這正是 [Ch 30](./30-security-boundary.md) 「攻擊面可定位」在依賴樹尺度的體現。

## 動手練習

1. `cargo new` 一個專案，`Cargo.toml` 加 `time = "=0.1.45"`，跑 `cargo generate-lockfile && cargo audit`。確認你看到 RUSTSEC-2020-0071。然後把版本改成 `time = "0.3"`，重跑，確認漏洞消失——體會「升版秒解已知漏洞」。

2. 對同一專案跑 `cargo tree`，畫出依賴樹。再對某個 transitive 依賴跑 `cargo tree -i <crate>`，看它是被誰拉進來的。

3.（若能裝 geiger）對一個有 `libc` 或別的 `-sys` 依賴的專案跑 `cargo geiger`，找出 unsafe 用量最高的 crate。查它的 crates.io downloads——它是「unsafe 多但可信」還是「unsafe 多又沒名氣」？練習做那個「風險 ≈ unsafe 量 × 信任缺口」的判斷。

4. 拿本章「判 sound」那段 `split_at_mut`，故意把 `assert!(mid <= len)` 刪掉，跑 `cargo +nightly miri test`（加一個 `mid > len` 的測試）。看 Miri 怎麼從「綠」變成「抓到 OOB」。這驗證了「強制前提」和「假設前提」的差別。

## 本章重點整理

- 稽核是分層漏斗：`cargo audit`（已知漏洞，比對 RUSTSEC，秒級必跑）→ `cargo geiger`（量化 unsafe，排優先序）→ `cargo deny`（政策閘門）→ `cargo vet`/`crev`（信任審查，補 build.rs/供應鏈那條線）→ 人工 review（用 Ch 31 判準）。**自動化負責篩，人工負責判。**
- `cargo audit` 只查**已知**漏洞，且需**定期**跑（新 advisory 隨時登記，你的 code 沒變但世界對它的認知變了）；發現 vulnerability 非零退出，能當 CI 閘門。
- `cargo geiger` 數 unsafe **數量**不評估**品質**；「unsafe 多 + 沒名氣 + 沒人看」才是紅旗，別錯殺 `libc` 這種正當 FFI crate。
- 可稽核性設計：`#![forbid(unsafe_code)]` 給出機器可驗證的「零 unsafe」承諾、`cargo tree` 看清信任了誰、SBOM 做正式物料清單。人工 review 的心法：找出每段 unsafe 的 precondition，判它是被**強制**還是被**假設**。
- CI 策略：PR 跑 audit+deny、定期排程重跑 audit、有 unsafe 的 crate 加 Miri。

## 自我檢核

- [ ] 不看筆記，能不能說出那個五層漏斗，每層工具查什麼、為什麼順序是這樣（便宜的先篩）？
- [ ] 為什麼「`cargo audit` 過了」不等於「安全」？它具體看不到哪些風險？
- [ ] 為什麼 audit 必須**定期**跑，而不是專案設定好跑一次就算？（用 `time 0.1.45` 的例子解釋。）
- [ ] `cargo geiger` 顯示 `libc` 有幾百個 unsafe，你會不會因此判它危險？為什麼？你還需要哪個維度的資訊才能下判斷？
- [ ] 給你一段有 unsafe 的 safe API，能不能用「precondition 被強制 vs 被假設」這個心法，加上跑 Miri，做出 sound/unsound 的判斷？
- [ ] `cargo audit`/`geiger` 都覆蓋不到 [Ch 30](./30-security-boundary.md) 的哪一條線？那條線靠什麼防？

## 延伸閱讀

### 官方文件 / 工具

- **[The Cargo Book —〈cargo tree〉](https://doc.rust-lang.org/cargo/commands/cargo-tree.html)** — Rust 官方
  - **讀哪裡**：`-i`/`--invert`（反查誰拉進某依賴）與 `-e`/`--edges`（篩選依賴類型，如只看 build-dependencies）兩個 flag。
  - **能學到什麼**：把依賴樹看清楚是所有稽核的第一步；`-e build` 能專門列出 `build.rs` 依賴——[Ch 30](./30-security-boundary.md) 線三的可視化。
  - **前提**：懂 direct vs transitive 依賴的差別。

- **[RustSec —〈cargo-audit〉文件](https://rustsec.org/#tools)** 與 **[rustsec/rustsec GitHub](https://github.com/rustsec/rustsec)**
  - **讀哪裡**：README 的 usage 段（`--ignore`、`--json`、`--deny warnings` 的語意）與 advisory-db 的更新機制說明。
  - **能學到什麼**：本章那些 flag 的完整語意，以及 advisory-db 怎麼組織——理解「為什麼要定期跑」的底層。
  - **前提**：[Ch 31](./31-unsafe-vuln-classes.md) 已介紹 RUSTSEC 資料庫。

- **[cargo-geiger GitHub](https://github.com/geiger-rs/cargo-geiger)** — 官方 repo
  - **讀哪裡**：README 的「output」段（解釋 `x/y` 與符號）與「known limitations」（macro 展開、build.rs 數不準的已知限制）。
  - **能學到什麼**：本章「geiger 是計數不是判斷」那條踩雷的依據；限制段特別重要，避免過度信任它的數字。
  - **前提**：懂 unsafe 出現在哪些語法位置（Ch 17）。

- **[cargo-vet 文件](https://mozilla.github.io/cargo-vet/)** — Mozilla
  - **讀哪裡**：「How it works」與「Importing Audits」兩節。
  - **能學到什麼**：本章「第四層：信任審查」那條 `audit`/`geiger` 補不了的洞——供應鏈信任鏈怎麼建、怎麼跟別的組織共享審查集。
  - **前提**：懂 [Ch 30](./30-security-boundary.md) 線三/線四（build.rs + 依賴樹）為什麼是掃 code 掃不到的風險。

### 部落格 / 文章

- **[〈How to audit and improve your Rust supply chain security〉/ RustSec 團隊相關文章](https://rustsec.org/)** — RustSec / Rust Secure Code WG
  - **這篇說什麼**：從實務角度串起 audit/deny/vet 的整條供應鏈稽核流程，補本章沒展開的「多工具怎麼配合、怎麼進 CI」。
  - **讀哪裡**：整體流程那段；若找不到單篇，直接讀 rustsec.org 首頁的 tools 總覽即可。
  - **為什麼值得讀**：官方 WG 的第一手實務建議，不是二手教程。

### 書籍

- **《Rust for Rustaceans》第 9 章（Unsafe）+ 依賴管理相關段落** — Jon Gjengset（No Starch Press, 2021）
  - **這本書的定位**：中階 Rust 最佳單本書。
  - **讀哪幾章**：第 9 章講怎麼寫 sound 的 unsafe（是「被 audit 的那一方」的視角），和本章「當 auditor」的視角互補——你審別人的 unsafe 前，先懂怎麼寫對的 unsafe。

Part 5 的稽核三部曲（Ch 30 威脅模型 → Ch 31 unsafe 漏洞類 → Ch 32 audit 工具鏈）到此完成。下一章換攻擊/RE 視角：Rust binary 編出來長什麼樣、symbol mangling 怎麼還原、逆向 Rust 和逆向 C++ 差在哪。

→ [Ch 33 逆向 Rust binary：特徵與 mangling](./33-reversing-rust-binary.md)
