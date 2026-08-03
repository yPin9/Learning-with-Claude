# Ch 0 — 環境搭建：四把刀一次到位

> **目標**：在一台 Linux（本課用 WSL2 Ubuntu 22.04）上，把 CodeQL、Semgrep、Joern、weggli 四把刀，加上驗證用的 gcc/ASan、ripgrep 全部裝好、跑通一次 smoke test，並建好一個共用的 vuln lab。這章走完，後面每一章的 query 你都能親手重跑。

> **環境**：本章的版本與輸出，全部在 **WSL2 Ubuntu 22.04.3 LTS** 上實跑取得。工具版本釘定：CodeQL 2.26.2、Semgrep 1.172.0、weggli 0.2.4、Joern 4.0.594、OpenJDK 17.0.19、gcc 11.4、ripgrep 13.0。其他 Linux 發行版指令大同小異，套件名可能有差；macOS 多數工具有 brew 版，但本課不逐一驗證 macOS 輸出。

## 為什麼先搞環境

原始碼審計是**動手**的技藝。你可以讀完整門課一行 code 不跑，但那樣學到的是「知道有這回事」，不是「拿到 target 能開工」。這門課的每一章都附了在這套環境上真跑出來的輸出——命中行號、flow path、ASan crash——你要能把它們重跑一遍、改壞看它怎麼變，才算真的學會。

四把刀對環境的要求不一樣，這本身就是第一課：

```
   工具        跑起來需要什麼              裝起來的痛點
   ────        ──────────────            ──────────────
   Semgrep     Python 3                  最輕，pip 一行
   weggli      單一 Rust binary          cargo 編一下，或抓 prebuilt
   CodeQL      JVM + 自帶 toolchain       bundle 幾百 MB，還要能 build target
   Joern       JVM (需 Java 17+)         installer 會拉一堆 Scala 依賴
```

**Semgrep/weggli 幾分鐘搞定，CodeQL/Joern 是 JVM 大傢伙**——這個重量差，之後會直接反映在你「快篩用哪把、深挖用哪把」的選擇上（[Ch 2](./02-static-analysis-landscape.md) 的四工具地圖）。

> 如果你還沒有 WSL2：在 Windows PowerShell（管理員）跑 `wsl --install -d Ubuntu-22.04`，重開機，設好帳密，就有一個 Ubuntu 了。本課所有指令都在這個 Ubuntu 裡跑。

## 先裝地基：Java、Python、build 工具

CodeQL 和 Joern 都要 JVM，Semgrep 要 Python，weggli 要 Rust 的 cargo，驗證漏洞要 gcc + AddressSanitizer。一次裝齊：

```bash
sudo apt-get update
sudo apt-get install -y openjdk-17-jdk python3-pip python3-venv \
                        curl unzip cargo build-essential ripgrep
```

驗證 Java（CodeQL 2.26.2 與 Joern 4.x 都要 Java 17 以上；裝舊版 Java 會在 Joern 啟動時報 `UnsupportedClassVersionError`）：

```bash
$ java -version
openjdk version "17.0.19" 2026-04-21
OpenJDK Runtime Environment (build 17.0.19+7-Ubuntu-0ubuntu122.04)
```

`build-essential` 給你 gcc/make（CodeQL 建 C/C++ database 要真的編譯 target，見 [Ch 20](./20-codeql-databases.md)），`ripgrep`（`rg`）是漏斗最外層的秒級粗篩工具（[Ch 34](./34-structural-search-family.md)）。

## Semgrep：pip 一行

```bash
python3 -m pip install --user semgrep
```

`--user` 裝到 `~/.local/bin`，不用 sudo，也不污染系統 Python。裝完把它加進 PATH（下面統一設）。驗證：

```bash
$ ~/.local/bin/semgrep --version
1.172.0
```

> **踩雷**：`semgrep --test` 這個子命令（跑規則的單元測試，見 [Ch 15](./15-semgrep-rule-engineering.md)）在某些版本傳「目錄」當參數會拋 `IndexError: tuple index out of range`；傳「單一檔案」正常。這不是你規則寫錯，是工具的 bug，記住繞過方式即可。

## weggli：cargo 編一個

weggli 是 Rust 寫的單一 binary，用 cargo 編（第一次會拉依賴、編一兩分鐘）：

```bash
cargo install weggli
```

編好在 `~/.cargo/bin/weggli`。驗證：

```bash
$ ~/.cargo/bin/weggli --version
weggli 0.2.4
```

> 如果你不想裝 Rust toolchain，weggli 的 GitHub releases 也有 prebuilt binary，抓下來 `chmod +x` 就能用。cargo 版的好處是之後要裝別的 Rust 安全工具（ast-grep 等，[Ch 34](./34-structural-search-family.md)）也是同一套。

## CodeQL：抓官方 bundle

CodeQL 分兩種下載：只有 CLI 的，跟「CLI + 標準函式庫 + 預編譯 query」打包好的 **bundle**。**一律抓 bundle**——單抓 CLI 你還得自己 clone、編 QL 標準庫，純找罪受。bundle 在 `codeql-action` 的 releases：

```bash
mkdir -p ~/audit-tools && cd ~/audit-tools
curl -sL -o codeql.tar.gz \
  https://github.com/github/codeql-action/releases/latest/download/codeql-bundle-linux64.tar.gz
tar xzf codeql.tar.gz && rm codeql.tar.gz
```

解開後 CodeQL 在 `~/audit-tools/codeql/codeql`。驗證：

```bash
$ ~/audit-tools/codeql/codeql version
CodeQL command-line toolchain release 2.26.2.
```

> **踩雷**：CodeQL bundle 幾百 MB，`codeql/qlpacks/` 底下是各語言的標準庫與預編譯 query（[Ch 24](./24-codeql-cpp-memory-safety.md)、[Ch 25](./25-codeql-web-languages.md) 會直接跑這些內建 suite）。**別把 `codeql/` 目錄裡的東西刪來省空間**，query 會跑不動。空間不夠就整包放到別的磁碟再軟連結。

> **授權提醒**：CodeQL 對開源專案的分析免費，但對**閉源/商業**用途有授權限制（[Ch 32](./32-joern-vs-codeql.md) 詳談）。這是它和 Apache 授權的 Joern 在接商業 audit 案時的關鍵差別，現在先知道有這條線。

## Joern：跑官方 installer

Joern 有個 install script 會幫你把 Scala/JVM 依賴拉齊：

```bash
cd ~/audit-tools
curl -sL https://github.com/joernio/joern/releases/latest/download/joern-install.sh -o joern-install.sh
chmod +x joern-install.sh
./joern-install.sh --install-dir=$HOME/audit-tools/joern
```

裝完 `joern`、`joern-parse`、`joern-scan` 在 `~/audit-tools/joern/joern-cli/`。本課用的是 **Joern 4.0.594**。

> **踩雷**：`joern --version` 會回 `Warning: Unknown option --version`——這是正常的，Joern CLI 就是不認這個 flag，別以為裝壞了。要確認版本看啟動 banner，或直接 `joern` 進 shell 跑一句查詢（下面 smoke test）。Joern 首次啟動要拉起 JVM，**慢，要等十幾秒**，這是常態不是當機。

## 一次設好 PATH

四把刀散在四個目錄，把它們一次加進 `~/.bashrc`，之後開新 shell 就都在：

```bash
cat >> ~/.bashrc <<'EOF'
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$HOME/audit-tools/codeql:$HOME/audit-tools/joern/joern-cli:$PATH"
EOF
source ~/.bashrc
```

> **踩雷（本課從 Windows 呼叫時最常見）**：如果你像本課作者一樣，是從 Windows 用 `wsl -e bash -lc '...'` 呼叫 WSL 裡的工具——`bash -lc` 是 login shell 會載入 `~/.bashrc`，PATH 才會生效；但某些呼叫方式（非 login、非互動）不載入 `~/.bashrc`，`codeql`/`joern` 就會 `command not found`。保險做法是指令前先手動 `export PATH=...`，或直接用絕對路徑 `~/audit-tools/codeql/codeql`。本課多數章節的實跑就是用絕對路徑，最穩。

## 建共用 vuln lab

後面很多章共用同一個靶檔，現在建好。它是一個教科書等級的堆疊溢位：不可信的 `len` 一路流到 `memcpy` 的 size，中間沒有任何 bound check——這正是一條 taint source → sink 的最小範例：

```bash
mkdir -p ~/audit-lab && cd ~/audit-lab
cat > vuln.c <<'EOF'
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
void handle(int fd) {
    char buf[64];
    int len;
    read(fd, &len, sizeof(len));      // source：len 由對端控制
    char *data = malloc(len);
    read(fd, data, len);
    memcpy(buf, data, len);           // sink：len 未檢查，OOB write
    free(data);
}
int main(){ handle(0); return 0; }
EOF
```

這 12 行之後會被四把刀輪流開刀：weggli 看它的語法形狀、Semgrep/CodeQL/Joern 追 `len` 從 `read` 到 `memcpy` 的資料流。現在先確認每把刀都咬得動它。

## Smoke test：四把刀各咬一口

裝好不等於能用。逐一跑一句最小查詢，確認四把刀都對 `vuln.c` 有反應。

**weggli**（找 size 是變數的 memcpy——`$l` 是 weggli 的 metavariable）：

```bash
$ ~/.cargo/bin/weggli '{ memcpy($d,$s,$l); }' ~/audit-lab/vuln.c
vuln.c:4
void handle(int fd) {
    ...
    memcpy(buf, data, len);           // sink：len 未檢查，OOB write
    ...
}
```

> **踩雷**：weggli 的變數是 `$name`，不是 grep 的正則。我第一次寫成 `{ _ $l; memcpy(_,_,$l); }` 直接 `Query parsing failed`——`$l` 在那個位置沒被正確宣告。weggli 語法自成一格，[Ch 33](./33-weggli.md) 專門講。

**Semgrep**（最小 pattern，找所有 memcpy）：

```bash
$ ~/.local/bin/semgrep --lang=c --pattern 'memcpy(...)' ~/audit-lab/vuln.c
    vuln.c
      10┆ memcpy(buf, data, len);           // sink：len 未檢查，OOB write
Ran 1 rule on 1 file: 1 finding.
```

**CodeQL**（建 database 再查；建 C/C++ db 要真的編譯，小檔約 3 秒）：

```bash
$ cd ~/audit-lab
$ ~/audit-tools/codeql/codeql database create vuln-db \
     --language=cpp --command="gcc -c vuln.c" --overwrite
...
Successfully created database at .../vuln-db.
```

建好後你就能對 `vuln-db` 跑 QL query（語法從 [Ch 18](./18-codeql-model.md) 開始教）。這一步能跑通，代表 extractor + JVM + build 追蹤三者都對了——這是四把刀裡最容易卡的一步，卡住多半是 `gcc` build command 沒涵蓋到檔案。

**Joern**（進 shell 匯入再查，或用 script）：

```bash
$ echo 'importCode("/home/'$USER'/audit-lab/vuln.c"); cpg.call.name("memcpy").location.l' \
    | ~/audit-tools/joern/joern-cli/joern
...
memcpy  vuln.c  10
```

四句都有反應，環境就通了。任何一句卡住，回上面對應那節重裝——**別帶著半殘的環境往下走**，後面每章都要跑東西。

## 對照：為什麼不用線上版 / IDE 外掛就好？

你可能會問：Semgrep 有 Playground、CodeQL 有 VS Code 外掛，幹嘛在命令列裝一堆？

| 方式 | 好處 | 為什麼本課不靠它 |
|---|---|---|
| Semgrep Playground（線上） | 零安裝、即時試 pattern | 只能貼片段、不能跑你的 target、不能上 CI |
| CodeQL VS Code 外掛 | 有 quick-eval、結果視覺化 | 底層還是 CLI；MRVA（[Ch 27](./27-codeql-mrva.md)）才非它不可 |
| 命令列（本課） | 可腳本化、可進 CI、可對真 repo 大規模跑 | —— |

結論：**IDE/線上版適合探索與 debug 單條 query，命令列才是規模化獵殺的形態**。本課以 CLI 為主，該用 IDE 的地方（CodeQL quick eval、MRVA）會明講。

## 踩雷集錦

1. **裝了舊版 Java 導致 CodeQL/Joern 起不來**。錯誤直覺：「有 java 就行」。正確認識：CodeQL 2.26.2 與 Joern 4.x 要 **Java 17+**，裝到 Java 11 會在啟動時噴 `UnsupportedClassVersionError`。用 `java -version` 確認是 17 以上。
2. **只抓 CodeQL CLI 沒抓 bundle**。錯誤直覺：「CLI 就是本體」。正確認識：單 CLI 沒有標準庫和預編譯 query，你連 `import cpp` 都會失敗。一律抓 **bundle**。
3. **PATH 沒設好，`wsl -lc` 下 command not found**。錯誤直覺：「`~/.bashrc` 設了就到處都在」。正確認識：非 login/非互動 shell 不載入 `~/.bashrc`。不確定就用絕對路徑，最穩。
4. **CodeQL 改了 code 卻沒重建 database**。錯誤直覺：「db 會自動跟著原始碼更新」。正確認識：database 是一次性快照（[Ch 20](./20-codeql-databases.md)），原始碼一改就過期，要 `--overwrite` 重建，否則你查的是舊碼。
5. **把 Joern 首次啟動的十幾秒當成當機**。錯誤直覺：「沒反應是掛了，Ctrl-C」。正確認識：JVM 冷啟動就是慢，尤其第一次還要初始化。給它時間，別急著砍。

## 本章重點整理

- 四把刀對環境的要求分兩級：**Semgrep/weggli 輕（Python/單 binary），CodeQL/Joern 重（JVM + 大 bundle/依賴）**，這個重量差之後決定你怎麼分工。
- CodeQL 一律抓 **bundle**（含標準庫與內建 query），且建 C/C++ database 要能真的**編譯 target**——這是它最高的門檻。
- Joern `--version` 報 unknown option、首次啟動慢十幾秒，都是正常現象。
- 共用靶 `~/audit-lab/vuln.c` 是一條最小的 taint source→sink，後面每把刀都會拿它練手。
- 環境沒 smoke test 過就別往下走——四句最小查詢全綠再開工。

## 自我檢核

- [ ]（動手）四把刀的 smoke test 你都親手跑過、都對 `vuln.c` 有反應了嗎？
- [ ]（主動回憶）不看筆記，說出為什麼 CodeQL 要抓 bundle 而不是只抓 CLI。
- [ ]（理解）為什麼 CodeQL 建 C/C++ database 需要 `--command="gcc -c ..."`，而 Semgrep/weggli/Joern 不用能 build 就能跑？這對「能不能審一個編不起來的 target」有什麼影響？（答案的完整版在 [Ch 32](./32-joern-vs-codeql.md) 與 [練習 E](./practice-e-joern-no-build.md)）
- [ ]（理解）Semgrep Playground 很方便，為什麼本課仍以命令列為主？什麼情境你會回頭用 IDE/線上版？
- [ ]（排錯）如果 `wsl -lc 'codeql version'` 報 command not found，但互動 shell 裡好好的，最可能是什麼原因？

## 延伸閱讀

- **[CodeQL CLI 官方文件 — "Getting started with the CodeQL CLI"](https://docs.github.com/en/code-security/codeql-cli)**
  - **讀哪裡**：「Setting up the CodeQL CLI」與「Creating CodeQL databases」兩節，對照本章的 bundle 下載與 `database create`。
  - **和本章的關聯**：本章給你一條能跑的最短路徑，官方文件補齊各種 build 系統（CMake/Gradle）的 database 建法，[Ch 20](./20-codeql-databases.md) 會展開。
  - **前提**：無。
- **[Semgrep 官方文件 — "Getting started"](https://semgrep.dev/docs/getting-started/quickstart)**
  - **讀哪裡**：quickstart 與「Running rules」；配 [Playground](https://semgrep.dev/playground/) 即時試一條 pattern，建立「pattern 長得像原始碼」的手感。
  - **和本章的關聯**：本章只驗證裝好，Semgrep 的 pattern 語法從 [Ch 13](./13-semgrep-syntactic-patterns.md) 正式教。
  - **前提**：無。
- **[Joern 官方文件 — "Installation" 與 "Quickstart"](https://docs.joern.io/)**
  - **讀哪裡**：Installation 確認 Java 需求，Quickstart 看 `importCode` 與第一條 CPGQL 查詢。
  - **和本章的關聯**：補上本章 smoke test 沒展開的 CPG 節點/邊模型，[Ch 29](./29-joern-getting-started.md) 深入。
  - **前提**：知道什麼是 AST/CFG 更好懂（[Ch 3](./03-program-representations-cpg.md)）。
- **[weggli GitHub README](https://github.com/weggli-rs/weggli)**
  - **讀哪裡**：README 的 usage 範例與 `$variable`/`_`/`...` 語法表——這是 [Ch 33](./33-weggli.md) 的預習。
  - **和本章的關聯**：解釋本章 smoke test 那句 `{ memcpy($d,$s,$l); }` 為什麼那樣寫。
  - **前提**：讀得懂 C。

環境齊了、四把刀都咬得動靶。下一章我們退一步，把這門課要做的事——從手讀找洞升級到規模化變體獵殺——的世界觀講清楚，你才知道每把刀在整條獵殺鏈上站哪。

→ [Ch 1 讀碼即逆向 → 審計即規模化](./01-reading-to-auditing.md)
