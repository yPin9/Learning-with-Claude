# Ch 35 — 用 Rust 寫資安工具

> **目標**：理解為什麼資安/系統工具鏈正在集體遷移 Rust、掌握用 Rust 型別系統安全處理不可信 binary 輸入的核心模式、寫出一個實用的 ELF/PE 靜態分析工具。學完你能：（1）說清楚 Rust binary parser 和 C binary parser 在漏洞類型上的結構性差異；（2）用 `goblin` crate 讀取 ELF 符號表和 PE section；（3）用 Shannon entropy 快速判別加殼/加密；（4）編譯出不依賴 libc 的靜態 binary 部署到任意環境；（5）避開初學者在 security tooling 裡常踩的五個雷。

> **環境**：stable rustc 1.97，Cargo 1.97。本章 code 範例以 `cargo new` 建立即可在本機跑；不需要特殊環境。goblin 0.9、anyhow 1.x 均為 stable compatible。

## 為什麼需要這個？

工具鏈的遷移不是跟風，是被迫的。

看幾個實際的例子：`ripgrep` 比 GNU grep 快，但它最初打動資安工程師的不是速度，是「處理惡意編碼的 regex 輸入時不會崩潰」。`RustScan`（port scanner）替換 Nmap 前期偵查的原因之一是並發模型更好管、binary 沒有複雜的動態連結依賴。`feroxbuster`（directory fuzzer）在敏感環境下比 `gobuster` 更容易靜態編譯打包。CNCF 生態裡的 `vector`（log pipeline）、`bottlerocket`（AWS 自製 Linux distro）把 Rust 帶進生產基礎設施。

但這些都是表面。更深的原因在**攻擊面**：binary parsing 是 C 語言的漏洞重災區。

Ghostscript 的 PDF parser、ImageMagick 的多格式圖像 parser、各種封包解析器——CVE 榜上年年都有這類東西。根本原因不是 C 工程師不夠聰明，是 C 的程式模型要求你手動管理 offset、長度、邊界，而 parser 的核心工作就是在不可信輸入上做這些計算。手算 + 不可信輸入 = 系統性漏洞來源。

Rust 的型別系統從根本上改變了這個方程式。邊界檢查（bounds check）不是你記不記得寫的問題，是語言的預設行為。`Result<T, E>` 讓 parse 失敗變成型別，而不是 segfault 或靜默 OOB。

---

## 先建立直覺

C 的 binary parser 和 Rust 的 binary parser，在結構上長這樣：

```
   ── C 的 ELF parser（手動 offset + cast）──

   uint8_t *buf = mmap(file, ...);
   Elf64_Ehdr *hdr = (Elf64_Ehdr *)buf;       // buf 夠大嗎？不知道
   uint16_t shnum = hdr->e_shnum;             // 可以是任意大數字
   for (int i = 0; i < shnum; i++) {
       Elf64_Shdr *sh = (Elf64_Shdr *)(buf    // OOB！沒人檢查
           + hdr->e_shoff + i * sizeof(*sh));
       ...
   }
   // 攻擊者控制 e_shnum、e_shoff → 任意讀


   ── Rust 的 ELF parser（goblin，Result + checked）──

   let buf = fs::read(path)?;                 // IO 錯誤是 Result
   let elf = goblin::elf::Elf::parse(&buf)?;  // parse 失敗是 Result
   for sh in &elf.section_headers {           // Vec<SectionHeader>，已 checked
       println!("{}", sh.sh_size);            // 不可能 OOB
   }
   // 攻擊者控制 e_shnum → goblin parse 時就 reject 或 truncate
```

這不是「更小心地寫 C」能解決的問題。`e_shnum` 可以是 65535，C 的迴圈就跑 65535 次、每次都做 pointer 算術——你要在每一次迭代都記得檢查。Rust 的做法是在 `parse()` 時先驗證整個結構，把不合法的狀態拒在型別之外，之後的迭代操作已知安全的 `Vec`。

---

## 工具遷移的三個理由

**理由一：工具本身不被打**

資安工具的特殊性在於它的輸入**天生就是惡意的或至少不可信的**。你的 ELF analyzer 會被拿去分析惡意樣本；你的 port scanner 會從不受信任的網路接收封包；你的 fuzzer 會餵給自己各種畸形輸入。工具本身如果有記憶體漏洞，就變成另一個攻擊面。用 Rust 寫工具是在說「分析惡意樣本的工具本身不應該被惡意樣本打穿」。

**理由二：靜態 binary 易部署**

```bash
cargo build --release --target x86_64-unknown-linux-musl
```

這一行出來的 binary 靜態連結 musl libc，不依賴目標系統的 glibc 版本。把這個 binary scp 進目標環境，不管對方跑的是 Ubuntu 20.04 還是 Debian 12，直接執行。C 工具通常動態連結，在 glibc 版本不同的環境就 `GLIBC_2.34 not found`。

**理由三：cargo 生態加速開發**

你不需要從頭實作 ELF parser：

- `goblin 0.9`：ELF/PE/Mach-O/archive parser，一個 crate 全包，rustc 自己也用
- `nom 8.x`：parser combinator，用來解析任意自訂 binary format
- `object 0.36`：更底層的 COFF/ELF reader，rustc 內部用的那個
- `pcap 2.x`：libpcap 綁定，封包捕獲
- `pnet 0.35`：pure Rust 的 packet construction/parsing

站在這些 crate 的肩膀上，一個實用的 ELF 分析工具可以在 100 行內寫完。

---

## 實例：用 goblin 寫 ELF/PE header parser

先建立專案：

```bash
cargo new binary-analyzer
cd binary-analyzer
```

`Cargo.toml`：

```toml
[package]
name = "binary-analyzer"
version = "0.1.0"
edition = "2021"

[dependencies]
goblin = "0.9"
anyhow = "1"
```

`src/main.rs`：

```rust
use std::fs;
use goblin::Object;

fn entropy(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    let mut freq = [0u64; 256];
    for &b in data {
        freq[b as usize] += 1;
    }
    let len = data.len() as f64;
    freq.iter()
        .filter(|&&c| c > 0)
        .map(|&c| {
            let p = c as f64 / len;
            -p * p.log2()
        })
        .sum()
}

fn analyze(path: &str) -> anyhow::Result<()> {
    let buf = fs::read(path)?;

    match Object::parse(&buf)? {
        Object::Elf(elf) => {
            println!("=== ELF ===");
            println!(
                "sections: {}  symbols: {}",
                elf.section_headers.len(),
                elf.syms.len()
            );

            // 印出所有非空 symbol 名稱
            for sym in elf.syms.iter() {
                if let Some(name) = elf.strtab.get_at(sym.st_name) {
                    if !name.is_empty() {
                        println!("  sym [{:>6}] {}", sym.st_size, name);
                    }
                }
            }

            // 對每個 section 計算 entropy
            println!("\n--- Section entropy ---");
            for sh in &elf.section_headers {
                let name = elf
                    .shdr_strtab
                    .get_at(sh.sh_name as usize)
                    .unwrap_or("<?>"); 

                // 安全取 slice：get() 回傳 Option，不 panic
                let start = sh.sh_offset as usize;
                let end = start.saturating_add(sh.sh_size as usize);
                let data = buf.get(start..end).unwrap_or(&[]);

                let e = entropy(data);
                let flag = if e > 7.0 { " ← HIGH (packed?)" } else { "" };
                println!("  {:<20} size={:>8}  entropy={:.3}{}", name, data.len(), e, flag);
            }
        }

        Object::PE(pe) => {
            println!("=== PE ===");
            println!("sections: {}", pe.sections.len());

            println!("\n--- Section entropy ---");
            for sect in &pe.sections {
                let name = std::str::from_utf8(&sect.name)
                    .unwrap_or("<invalid>")
                    .trim_end_matches('\0');

                // PE section 資料從 pointer_to_raw_data 開始
                let start = sect.pointer_to_raw_data as usize;
                let size = sect.size_of_raw_data as usize;
                let data = buf.get(start..start.saturating_add(size)).unwrap_or(&[]);

                let e = entropy(data);
                let flag = if e > 7.0 { " ← HIGH (packed?)" } else { "" };
                println!("  {:<10} size={:>8}  entropy={:.3}{}", name, data.len(), e, flag);
            }

            // imports
            if let Some(imports) = pe.imports.as_ref().map(|i| i.iter()) {
                println!("\n--- Imports (first 20) ---");
                for (i, import) in imports.take(20).enumerate() {
                    println!("  [{:>2}] {}!{}", i, import.dll, import.name);
                }
            }
        }

        Object::Mach(_) => println!("Mach-O: analysis not shown in this example"),
        Object::Archive(_) => println!("Archive"),
        Object::Unknown(magic) => println!("Unknown magic: {:#x}", magic),
    }

    Ok(())
}

fn main() {
    let path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| {
            // 預設分析自己（Linux 上有效，Windows 用 current_exe）
            std::env::current_exe()
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_else(|_| "/proc/self/exe".into())
        });

    println!("Analyzing: {}\n", path);
    if let Err(e) = analyze(&path) {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}
```

跑起來：

```bash
cargo build --release
./target/release/binary-analyzer /bin/ls        # Linux ELF
./target/release/binary-analyzer notepad.exe    # 隨便找一個 PE
```

### 幾個值得注意的設計決策

**`Object::parse(&buf)` 返回 `Result<Object>`**——parse 失敗是 Rust 型別，不是 C 的 segfault 或靜默 OOB 讀取。攻擊者塞一個格式錯亂的 ELF 進來，你的程式打印 `error: ...` 然後以 exit code 1 結束，不會 crash，更不會被 exploit。

**`elf.syms.iter()` 不需要手算 offset**——goblin 在 `parse()` 時就把所有結構驗好了，iterator 吐出的是已知合法的 `Sym`。這在 C（libelf/libpe）裡是 20+ 年 CVE 的重災區：取 `e_shnum` 後沒有驗上限 → OOB；取 `sh_offset` 後沒有驗不超過 file size → OOB。Rust 的 `Vec<SectionHeader>` 邊界在 parse 時就 checked，之後的迭代是安全的。

**`buf.get(start..end).unwrap_or(&[])`**——這是 Rust 處理不可信 offset 的慣用法。`get()` 回傳 `Option<&[u8]>`；超出範圍回 `None`，`unwrap_or(&[])` 給你空 slice，entropy 回 0，程式繼續。沒有 panic，沒有 OOB。

---

## nom：parser combinator 簡介

`goblin` 知道 ELF/PE/Mach-O 的格式，是「already knows the grammar」的 library。如果你要解析的是自訂 binary 協定（CTF 的 binary format challenge、私有的 network protocol、惡意樣本的加殼 header），就需要 `nom`。

```toml
[dependencies]
nom = "8"
```

一個解析自訂 magic + 長度前綴 payload 的例子：

```rust
use nom::{
    bytes::complete::{tag, take},
    number::complete::le_u32,
    IResult,
};

// binary 格式：magic[4] + payload_len[4] + payload[payload_len]
fn parse_packet(input: &[u8]) -> IResult<&[u8], &[u8]> {
    let (input, _magic) = tag(b"MALP")(input)?;
    let (input, len) = le_u32(input)?;
    let (input, payload) = take(len)(input)?;
    Ok((input, payload))
}

fn main() {
    let raw = b"MALP\x05\x00\x00\x00hello world";
    match parse_packet(raw) {
        Ok((rest, payload)) => {
            println!("payload: {:?}", std::str::from_utf8(payload));
            println!("remaining: {} bytes", rest.len());
        }
        Err(e) => eprintln!("parse error: {e}"),
    }
}
```

nom 的核心特性：所有 combinator 都返回 `IResult<Input, Output>`，對應 `Result<(剩餘輸入, 解析結果), 錯誤>`。`take(len)` 在 `len > input.len()` 時返回 `Err`，不是 OOB。你組合 combinator 的方式就是在描述 grammar，nom 負責把所有邊界檢查埋在 combinator 內部。

goblin 和 nom 的定位差異很清楚：goblin 是「knows ELF/PE/Mach-O」，nom 是「gives you combinators for any binary grammar」。分析標準格式用 goblin，分析私有/自訂格式用 nom。

---

## 實際資安用途：entropy 分析判斷加殼/加密

上面的 `entropy()` 函式實作的是 Shannon entropy（bit 為單位）：

```rust
fn entropy(data: &[u8]) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    let mut freq = [0u64; 256];
    for &b in data {
        freq[b as usize] += 1;
    }
    let len = data.len() as f64;
    freq.iter()
        .filter(|&&c| c > 0)
        .map(|&c| {
            let p = c as f64 / len;
            -p * p.log2()
        })
        .sum()
}
```

數學意義：每個 byte 值出現的機率 p，算 -p * log2(p)，加總。均勻分佈（每個 byte 值等機率出現）時 entropy 趨近 8.0（2^8 = 256 個符號）；全部是同一個 byte 值時 entropy = 0.0。

實際使用的閾值：

| entropy 範圍 | 典型情況 |
|---|---|
| 0.0 – 1.0 | 全零 padding、高度稀疏資料 |
| 1.0 – 5.0 | 一般程式碼/資料（.text/.data section） |
| 5.0 – 7.0 | 壓縮資源、debug info |
| 7.0 – 8.0 | 加密資料、加殼 payload、UPX/混淆器輸出 |

entropy > 7.0 的 section 是惡意樣本初步分類的快速特徵。這是很多 YARA rule 和靜態分析工具的第一道篩選。

---

## 誠實與倫理邊界

這一章教的技術——ELF/PE parser、entropy 分析、靜態 binary 部署——都是雙面刃。

**本課的定位**：

- **授權滲透測試與 CTF**：在有書面授權或明確比賽範圍內使用
- **防禦盤點**：掃自己組織的系統，找暴露的服務、分析自家 binary 是否被竄改
- **研究與惡意樣本分析**：在隔離環境（sandbox、VM）內分析樣本格式，目的是理解威脅、寫偵測規則

**不在本課範圍**：未經授權掃描他人系統、分析後用於攻擊目標。

這不是客套話。台灣《電腦處理個人資料保護法》和《刑法》第 358 條（入侵電腦罪）在「未經授權存取」這點上很明確。工具本身合法，行為的合法性取決於授權。

「分析自己的 binary」和「分析公開 CTF 樣本」這兩個場景，本章的所有 code 都 100% 合法且有益。

---

## 跨平台靜態 binary

**Linux musl 靜態連結**：

```bash
rustup target add x86_64-unknown-linux-musl
cargo build --release --target x86_64-unknown-linux-musl
# 輸出：target/x86_64-unknown-linux-musl/release/binary-analyzer
# 靜態連結，不依賴 glibc
```

用 `file` 確認：

```
binary-analyzer: ELF 64-bit LSB executable, x86-64, statically linked, not stripped
```

**Windows 交叉編譯（Linux host → Windows binary）**：

```bash
rustup target add x86_64-pc-windows-gnu
# 還需要 mingw-w64 工具鏈：apt install gcc-mingw-w64-x86-64
cargo build --release --target x86_64-pc-windows-gnu
```

Windows 原生環境（MSVC toolchain）：

```powershell
rustup target add x86_64-pc-windows-msvc
cargo build --release --target x86_64-pc-windows-msvc
```

**條件編譯處理 OS 差異**：

```rust
fn default_target() -> String {
    #[cfg(target_os = "linux")]
    {
        // Linux 可以讀自己的 /proc/self/exe
        "/proc/self/exe".into()
    }
    #[cfg(not(target_os = "linux"))]
    {
        std::env::current_exe()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "unknown".into())
    }
}
```

`#[cfg(target_os = "...")]` 在**編譯期**選擇分支，不帶執行期開銷。這是 Rust 在跨平台工具裡的標準做法——一份原始碼，不同 target 編出不同行為，而不是在執行期做 OS 偵測。

pentest 工具的部署需求：一個靜態 binary scp/curl 到目標環境，直接執行，不裝 runtime，不帶依賴。musl 靜態連結正好符合這個需求。

---

## 踩雷集錦

**雷一：以為 goblin 是萬能的 safe 盾**

goblin 本身是 safe Rust，parse 失敗是 `Result`，不是 segfault。但你的程式如果在 `fs::read()` 前就 `.unwrap()`：

```rust
// 錯誤示範
let buf = fs::read(path).unwrap();  // 檔案不存在 → panic
```

IO 本身有錯誤（檔案不存在、權限不足、讀到一半 disk error），這些和記憶體安全無關，但一樣可以讓程式非預期終止。正確做法是讓 IO 錯誤走 `?` 或 `anyhow::Result`，不要 `.unwrap()`。

**雷二：用 `&buf[idx..]` 切 slice**

```rust
// 錯誤示範：idx > buf.len() 時 panic
let data = &buf[sh.sh_offset as usize..];

// 正確：get() 回傳 Option，不 panic
let data = buf.get(sh.sh_offset as usize..).unwrap_or(&[]);
```

這不是 UB（Rust 不會讓你 OOB 讀記憶體），但 `Index` trait 越界時會 panic，在分析惡意輸入的工具裡和 crash 沒有實質差別。用 `get()` 把越界轉成 `None`。

**雷三：以為靜態 binary 一定小**

musl 靜態連結會把 Rust std 打進 binary。加上 goblin、anyhow 這類 crate，release build 通常 1–5 MB。如果再加 `nom`、`pcap`，可能到 8–10 MB。這對大部分場景完全可以接受，但如果你要的是幾十 KB 的超小 binary，需要 `no_std` + 手動選 allocator（Ch 22 的內容），不是預設能做到的。

**雷四：entropy 高 = 一定加殼**

entropy > 7.0 的 section 也可能是：
- 合法的 zlib/zstd/lz4 壓縮資源（`.rsrc` section 裡的 icon 就是）
- 正常的加密金鑰儲存區
- 高隨機性的測試資料

反過來也不成立：某些混淆器（XOR 加密 with 固定 key）處理後的 section entropy 仍然可能不到 7.0。entropy 是快速篩選的**第一道特徵**，不是充分條件。高 entropy 值得繼續深挖（對比 import table 是否異常稀少、是否有已知 packer 的 magic bytes），低 entropy 不代表乾淨。

**雷五：跨平台編譯沒有測試 target 工具鏈**

在 Linux 上 cross-compile 到 `x86_64-pc-windows-gnu`，必須有 `mingw-w64` 工具鏈裝好。沒有的話 link 階段會出現類似：

```
error: linker `x86_64-w64-mingw32-gcc` not found
```

解法是 `apt install gcc-mingw-w64-x86-64` 或改用 `cross` 工具（Docker-based cross compilation）。直接在目標 OS 上原生編譯永遠是最省事的選項，cross-compile 在 CI pipeline 才常用。

---

## 進階：再往深一層

**`object` crate**：比 goblin 更底層的 ELF/COFF/Mach-O reader，rustc 和 cargo 本身用的就是這個。如果你需要讀 DWARF debug info、重定位表、或做 binary patching，`object` 的 API 設計在這類操作上比 goblin 更直接。

**`pcap` crate**：libpcap 的 Rust 綁定。`Capture::from_device("eth0")?.open()` 開始捕獲封包，每個封包是 `&[u8]`，再用 `nom` 或 `pnet` 解析協定層。寫自己的流量分析工具的起點。

**`hickory-dns`（前身 trust-dns）**：pure Rust 的 DNS 實作，包括 resolver 和 server。拿來寫 DNS 分析工具（偵測 DNS tunneling、分析 DNS response anomaly）比用 `dig` 再 parse 輸出省事得多。

**`libbpf-rs`**：Rust 包裝的 libbpf，讓你用 Rust 寫 eBPF userspace 程式（probe 管理、map 操作、ring buffer 讀取）。eBPF probe 本身還是要用 C 或受限的 Rust 寫（`aya` crate 走另一條路，pure Rust eBPF），但 userspace 端用 Rust 管理生命週期和錯誤處理比 C 好很多。這是 Ch 38 之後的進階路線。

---

## 延伸閱讀

**goblin crate docs（docs.rs/goblin）**：讀「ELF」那節的 `Elf::parse` 和 `SectionHeader` 結構。特別值得看的是 `strtab` 和 `shdr_strtab` 的 API——它不是 `&str`，是 `Option<&str>`，這個設計決策背後就是「string table 可能包含非 UTF-8 byte」的現實。`Object` enum 的 variant 定義告訴你 goblin 支援哪些格式、每種格式暴露了哪些欄位。

**BurntSushi（Andrew Gallant）的 ripgrep 效能文章**：`blog.burntsushi.net`，搜「ripgrep is faster than」。這篇文章的價值不在速度數字，在於它完整展示了一個 Rust 系統工具從效能設計、SIMD 加速、到「我為什麼選擇 Rust 而不是繼續優化 C」的完整思路。作者是 regex crate 和 memchr crate 的主要作者，他的設計決策在 Rust 工具開發圈有很大影響力。

**nom 官方指南（nom-rs.github.io 或 docs.rs/nom）的 "binary format parsing" 一節**：具體展示如何用 `le_u32`、`be_u16`、`take`、`count` 組合出一個完整的 binary grammar。比一般的 nom 入門教學（通常示範的是文字格式 parsing）更貼近這章的用途。看完之後你能獨立寫出任意私有 binary 協定的 parser，不依賴 goblin 的「已知格式」。

---

→ [Ch 36 Fuzzing Rust：cargo-fuzz/AFL++](./36-fuzzing-rust.md)
