# Ch 32 — 生成引擎的架構：從 manifest 到二進位識別

> **目標**：從頭設計一個 syft——理解 source → file tree → cataloger → 正規化這條流水線背後的架構決策；掌握以圖（graph）為基礎的元件資料模型；理解兩份 SBOM 合併時的身份對齊問題；深入沒有 manifest 時如何辨識元件，包含函式級指紋、code clone detection 和 fuzzy hashing。

---

## 為什麼需要這個？

Ch 10 拆解了 syft 的 cataloger 機制，讓你能預測它能看到什麼、看不到什麼。但那一章是「使用者視角」——你知道按哪個按鈕、看什麼輸出。

這一章是「設計者視角」：如果要你從零實作一個 SBOM 生成引擎，你會怎麼設計它的資料模型？當兩條供應鏈匯流時，兩份 SBOM 的合併語意是什麼？當 manifest 完全不存在——靜態連結的 C library、vendor 進去的函式庫、strip 過的 binary——你靠什麼識別元件？

這三個問題是 SBOM 生成領域真正難的部分。工具能幫你處理有 manifest 的情況，但沒有 manifest 的情況才是差距所在。

---

## 先建立直覺

把一個軟體系統想成一本書的彙編（anthology）。書裡有很多篇章，有些章節明確標了作者（manifest），有些是無名氏（vendored/靜態連結），有些是摘錄自其他作品但已和原文混在一起（code clone）。

SBOM 生成引擎的工作，是從這本彙編裡推斷出每一個章節的出處。方法有三個層次，準確度遞減、成本遞增：

```
層次 1：讀目錄（manifest）
  「package.json 說我依賴 express@4.18.2」
  → 100% 準確，成本低，但只看到宣告，不看到實際安裝

層次 2：讀書頁上的版權聲明（OS package DB / 語言 metadata）
  「dpkg status 說 bash 5.2.37 已安裝」
  → 高準確度，成本中，但元件要配合你的格式

層次 3：讀章節的字體和排版（binary 指紋）
  「這個 .so 的 opcode 序列和 OpenSSL 3.0.7 的 libcrypto 有 91% 相似」
  → 概率推斷，成本高，是最後手段也是唯一手段
```

絕大多數工具活在層次 1-2。層次 3 是研究論文的戰場，也是 C/C++ 靜態連結二進位的唯一出路。

---

## 設計一個 syft：流水線與介面

要自行實作一個 SBOM 生成引擎，最核心的問題是：每個子系統之間傳什麼、誰負責什麼決策？

```
          ┌─────────────────────────────────────────┐
          │              Source Resolver            │
          │  輸入: docker image / dir / file / URL  │
          │  輸出: FileTree（統一的虛擬檔案系統）    │
          └────────────────┬────────────────────────┘
                           │ FileTree
                           ▼
          ┌─────────────────────────────────────────┐
          │            Cataloger Registry           │
          │  根據 source type 決定要啟用哪些 cataloger│
          │  每個 cataloger 實作同一個介面:           │
          │    Catalog(resolver FileResolver)        │
          │      → ([]Package, []Relationship, error)│
          └──┬──────────────┬──────────────┬────────┘
             │              │              │
      ┌──────┴──────┐ ┌─────┴──────┐ ┌───┴───────────┐
      │  OS cataloger│ │Lang catalog│ │Binary catalog  │
      │  apk/dpkg/  │ │go.mod/     │ │.go.buildinfo   │
      │  rpm parser  │ │package.json│ │opcode pattern  │
      └──────┬──────┘ └─────┬──────┘ └───┬───────────┘
             │              │             │
             └──────────────┼─────────────┘
                            │ raw []Package
                            ▼
          ┌─────────────────────────────────────────┐
          │              Normalizer                 │
          │  1. 去重 (dedup by name+version+type)   │
          │  2. PURL 合成                            │
          │  3. CPE 推算                             │
          │  4. Relationship 推算（contains/depends）│
          └────────────────┬────────────────────────┘
                           │ PackageGraph
                           ▼
          ┌─────────────────────────────────────────┐
          │             Encoder                     │
          │  PackageGraph → SPDX / CycloneDX / ...  │
          └─────────────────────────────────────────┘
```

關鍵介面設計決策：

**Cataloger 共用 FileResolver 而非直接操作 FileTree**。FileResolver 提供 `FindFilesByGlob(pattern)`、`FileContentsByLocation(loc)`，隔離了「這個 file 實際在哪裡（tar layer / 本地 disk / 壓縮檔內）」的細節。每個 cataloger 只需要說「我要找符合這個 glob 的檔案」，不用管底層是 container image 還是本地目錄。

**Cataloger 輸出 Relationship 而非只輸出 Package**。`os-db-cataloger` 可以輸出「image contains bash」這條關係；`go-module-binary-cataloger` 可以輸出「binary depends-on uuid@v1.6.0」。把關係建模在 cataloger 層，而不是在 normalizer 層猜，準確度高得多。

---

## 圖資料模型：SBOM 不是清單，是圖

把 SBOM 看成清單是錯誤的簡化。清單能說「這些元件存在」，但說不清楚「它們之間的關係是什麼」。SBOM 的核心資料模型應該是一個帶標記的有向圖（labeled directed graph）：

```
節點類型（Node）：
  ComponentNode   ── 一個元件（package、library、binary）
  FileNode        ── 一個檔案（source file、artifact）
  DocumentNode    ── 一份文件（SBOM 本身，作為根節點）

邊類型（Edge）：
  CONTAINS        ── 包含關係（image CONTAINS package）
  DEPENDS_ON      ── 依賴關係（app DEPENDS_ON express@4.18.2）
  DESCRIBES       ── 描述關係（DocumentNode DESCRIBES target）
  GENERATED_FROM  ── 來源關係（artifact GENERATED_FROM source file）
  VARIANT_OF      ── 變體關係（modified OSS VARIANT_OF upstream）

證據邊（Evidence Edge）：
  FOUND_BY        ── 哪個 cataloger 在哪裡找到的（帶 location 屬性）
```

以圖為模型，而不是以清單為模型，有三個重要優點：

**第一，表達「不確定性」**。一個 binary 的 `FOUND_BY` 可以帶上 `confidence: 0.72` 的屬性，表達「我認為這是 OpenSSL 3.0.7，但只有七成把握」。清單模型無法優雅地表達這件事。

**第二，SPDX 和 CycloneDX 的格式差異本質上是在這個圖的不同子集上作序列化**。SPDX 的 `Relationship` section 直接對應 `CONTAINS`/`DEPENDS_ON` 邊；CycloneDX 的 `dependencies` section 也是。兩個格式可以從同一個圖序列化出來。

**第三，SBOM merge 的問題可以轉化為子圖合併問題**。這帶我們進入下一個難題。

---

## 兩份 SBOM 的可合併性：身份對齊問題

考慮一個現實情境：你的 CI pipeline 在 build 時用 `trivy` 生成了一份 SBOM-A，事後用 `syft` 掃 container image 又生成了一份 SBOM-B。你想把它們合併成一份更完整的 SBOM。

乍看這很簡單：取聯集就好了。但真正的問題是：**SBOM-A 裡的 `requests==2.31.0` 和 SBOM-B 裡的 `requests==2.31.0` 是同一個元件嗎？**

不一定，因為：

1. **PURL 不同**：SBOM-A 用 `pkg:pypi/requests@2.31.0`，SBOM-B 用 `pkg:pypi/requests@2.31.0?hash=sha256:abcd`，hash 不同就是不同版本。
2. **定位不同**：SBOM-A 裡的 requests 是 source tree 裡的 requirements.txt 宣告的，SBOM-B 裡的是 image 裡 `/usr/local/lib/python3.11/site-packages/requests-2.32.3.dist-info` 的實際安裝。它們描述的可能是同一個東西，也可能一個是宣告、一個是更新後的安裝版本。
3. **CPE 衝突**：兩份 SBOM 對同一個元件的 CPE 推算結果可能不同，merge 時要取哪個？

**身份對齊（identity alignment）**的完整邏輯如下：

```
merge(SBOM-A, SBOM-B):

1. 嘗試精確匹配（exact match）：
   PURL 完全相同 → 同一個元件，合併 evidence edges

2. 嘗試寬鬆匹配（relaxed match）：
   name + version + type 相同，PURL scheme 相同 → 可能是同一個元件
   → 合併為一個節點，保留兩組 PURL，標記衝突

3. 無法匹配 → 保留兩個獨立節點，建立 SIMILAR_TO 邊

4. Relationship 合併：
   A 說「image CONTAINS requests」
   B 說「image CONTAINS requests」
   → 去重，但保留兩組 FOUND_BY 證據邊

5. 衝突解決策略（需要明確定義）：
   license 欄位衝突 → 取交集？取最嚴格的？
   version 欄位衝突（這不應該發生，但發生了）→ 標記為 CONFLICT，讓消費者決定
```

這正是為什麼 SPDX 3.0 引入了 `ElementCollection` 的概念：不同工具生成的 SBOM 可以被聚合到一個 collection 裡，但保留各自的 Document namespace，讓消費端知道每條資訊的來源。

---

## 底層機制：沒有 manifest 時怎麼辦

這是真正的深水區。當你面對的是：

- 一個 C/C++ 靜態連結的 binary（`libcrypto.a` 已被鏈入，沒有任何 `.so` 依賴）
- vendor 進去的程式碼（源碼直接複製到 repo，沒有 go.mod 記錄）
- strip 過的 binary（debug symbols 和 section headers 被清除）

syft 讀 metadata 的做法對這三種情況幾乎是盲的。研究界用什麼方法填補這個洞？

### 函式級指紋（Function-level Fingerprinting）

關鍵洞察：**一個函式的內部結構，在不同版本的二進位裡有穩定的特徵**。即使 strip 掉 symbol names，函式的控制流圖（CFG）結構、basic block 的 opcode 序列，在相同版本的相同程式庫裡是高度一致的。

ATVHunter（ICSE 2021）就是這個方向的代表工作。它的兩階段識別機制：

```
第一階段（粗粒度 CFG 匹配）：
  1. 從待分析的 binary 提取所有函式的 CFG
  2. 對每個 CFG 計算特徵向量（節點數、邊數、back-edge 數...）
  3. 和 TPL 資料庫裡的 189,545 個函式庫做粗略比對
  4. 召回率優先，篩選出候選函式庫

第二階段（細粒度 opcode 匹配定版本）：
  1. 對每個 basic block 提取 opcode 序列（去掉運算元，只保留操作類型）
  2. 用 bag-of-words 模型計算 basic block 指紋
  3. 在候選函式庫的各個版本裡比對
  4. 找出最接近的版本
```

「去掉運算元，只保留操作類型」這個設計對混淆有抵抗力：即使 code obfuscation 改了暫存器分配（`%eax` → `%rbx`）或記憶體位址，opcode 序列（`MOV, PUSH, CALL, RET`）的結構不變。

結果：90.55% precision、88.79% recall，可以識別到具體版本。資料庫有 3 百萬個版本。

### Code Clone Detection：識別被修改過的重用

但如果 vendored 程式碼不是原封不動的複製，而是經過修改的呢？

CENTRIS（ICSE 2021）的核心觀察：修改過的 OSS 重用（modified reuse）比完全不變的重用（exact reuse）**多 20 倍**。如果你只識別 exact match，你漏掉了大部分的重用。

CENTRIS 的方法是先把 OSS 的「獨特部分」切分出來：

```
1. Component segmentation：
   從 OSS 版本歷史找出每個版本新增的函式（delta）
   去掉在多個 OSS 裡都出現的「通用」函式（如 utility functions）
   只保留「只在這個 OSS 的這個版本出現」的獨特函式

2. 在目標 binary 裡比對這些獨特函式：
   這樣即使只有 30% 的函式被保留（其他被刪改），
   只要獨特函式能配對上，就能識別 OSS 重用

3. 處理巢狀元件（nested components）：
   A 裡面 vendor 了 B，B 裡面又 vendor 了 C
   CENTRIS 能分別識別出這三層
```

規模：10,241 個 GitHub 專案、229,326 版本、800 億行程式碼。準確率 91%，召回率 94%，平均每個 app 分析不到一分鐘。

在 SBOM 生成引擎裡，CENTRIS 這類方法可以作為一個「深度 binary cataloger」——當標準的 OS/語言 cataloger 找不到任何東西，啟動 code clone detection 作為最後手段。代價是需要維護一個龐大的 OSS 指紋資料庫，以及顯著更高的分析時間。

### Fuzzy Hashing：輕量版的相似度計算

如果你不需要 function-level 的精確識別，只需要「這個 binary 和我們知道的哪個 OSS 最像」，TLSH 是一個實用的輕量方案。

TLSH（Trend Micro Locality Sensitive Hash，IEEE Cybercrime and Trustworthy Computing Workshop 2013）的原理：對兩段 binary 計算一個「距離」，而不是判斷它們是否相等。距離 0 = 完全相同，距離愈大愈不同。它用 locality sensitive hash 的特性確保「相似的輸入，距離小」。

```bash
# 在 WSL 安裝 tlsh-tools
sudo apt-get install -y tlsh-tools

# 計算一個 binary 的 TLSH hash
tlsh -f /usr/lib/x86_64-linux-gnu/libssl.so.3

# 比較兩個 binary 的距離
# tlsh 輸出 hash 後，可用 -c 和另一個 hash 比對距離
# 距離 0 = identical，200+ = likely unrelated
```

在實際的 SBOM 生成引擎裡，TLSH 可以作為「第一道篩選」：先用 TLSH 距離快速縮小候選範圍，再用 function-level fingerprint 精確比對。

### Code Property Graph：函式之間的語意關係

Code Property Graph（CPG，IEEE S&P 2014）把 AST（Abstract Syntax Tree）、CFG（Control Flow Graph）和 PDG（Program Dependence Graph）合併成一張圖，讓你能用圖查詢語言找出跨函式的漏洞模式。

雖然 CPG 原本是為了找漏洞而設計，但它的基礎設施對 SBOM 的「元件識別」也有用：當你把一個待分析的 binary 和一個已知 OSS 都建成 CPG，兩個圖之間的子圖同構（subgraph isomorphism）問題可以告訴你「這兩段程式碼在語意上等價」——比 opcode sequence matching 更精確，也更能對抗編譯器最佳化帶來的差異。

代價是建 CPG 的成本極高，不適合大規模批次分析，適合高價值目標的深度調查。

---

## 對比與取捨

| 識別方法 | 適用情境 | 準確度 | 成本 | 能識別修改過的重用？ | 實作難度 |
|---|---|---|---|---|---|
| Manifest parsing（go.mod / package.json）| 有 manifest 的場景 | 極高 | 低 | 否（只看宣告）| 低 |
| OS package DB（apk/dpkg/rpm）| 安裝後的 OS 套件 | 極高 | 低 | 否 | 低 |
| Language metadata（.dist-info / .jar MANIFEST）| 安裝後的語言套件 | 高 | 低 | 否 | 低 |
| Binary build info（.go.buildinfo / .dep-v0）| Go/cargo-auditable Rust binary | 高 | 低 | 否 | 中 |
| binary-classifier（版本字串 pattern）| 任意 binary | 低~中 | 低 | 否 | 低 |
| Fuzzy hashing（TLSH）| 未知 binary 的粗略分類 | 中 | 中 | 部分（取決於修改量）| 中 |
| Function-level fingerprint（ATVHunter 風格）| strip 過的 C/C++ binary | 高 | 高 | 是（opcode 抗混淆）| 高 |
| Code clone detection（CENTRIS 風格）| vendored / modified OSS | 高 | 極高 | 是（專門設計）| 極高 |
| Code Property Graph | 高價值目標深度調查 | 極高 | 極高 | 是 | 極高 |

實務建議：生成引擎應該是一個**優先順序遞降的 fallback chain**。先跑 manifest parsing，找到就停；找不到跑 OS/語言 metadata；還找不到跑 binary build info；最後才考慮 fuzzy hash 或 function-level fingerprint。不要對所有 binary 都跑重量級分析，那是在浪費算力。

---

## 踩雷集錦

**1. strip binary 和「無 metadata binary」是兩回事**

`strip` 移除的是 debug symbols（`.debug_info`、symbol table）。Go 的 `.go.buildinfo` section 不在 debug symbols 裡，`strip -s` 後仍然存在。但 C/C++ 靜態連結的 binary 根本就沒有依賴 metadata section，不是 strip 造成的，而是從來就沒有。

把「syft 掃 stripped Go binary 有輸出」和「syft 掃 stripped C binary 有輸出」混為一談，會讓你對工具的能力邊界有嚴重誤判。

**2. Vendored 程式碼不等於子目錄有 manifest**

常見誤解：vendor 目錄裡有 `go.mod` 或 `package.json`，所以 cataloger 能認到。

實際情況：真正的 vendoring（尤其是 C 生態）通常是把原始碼**複製到專案目錄下**，不帶任何 manifest。沒有 manifest，manifest-based cataloger 就是看不到。更糟的是，vendored 程式碼可能被改過（加了 patch），和上游版本不完全一樣，function-level fingerprint 也可能配對失敗。

**3. SBOM merge 的身份衝突要明確處理，不能偷偷丟棄**

把兩份 SBOM 的 artifact 陣列直接 concat 然後 `uniq` 看似簡單，實際上會：
- 因為工具對同一元件生成的 PURL 微妙差異（trailing slash、hash format）造成假重複（false duplicates）
- 因為不同工具對同一元件報不同版本（一個宣告版本、一個安裝版本）造成不一致

正確做法是保留 provenance（每條資訊的來源），讓消費者看到衝突並作決策，而不是靜默地選一個或丟掉。

**4. binary-classifier-cataloger 的誤報比你想的多**

`binary-classifier-cataloger` 靠版本字串 pattern matching，任何 binary 裡碰巧出現類似 `OpenSSL 1.0.2k` 格式的字串都可能觸發。建置腳本、CI log 被打包進 image、測試資料、文件字串——都是誤報來源。不要把 binary-classifier 的輸出當成確認，而是當成「需要人工驗證的線索」。

**5. 一份 SBOM 能捕捉的依賴深度取決於你的 relationship 模型**

如果你只記錄 direct dependencies，transitive dependencies 的漏洞掃描就會漏。如果你記錄全部 transitive，SBOM 大小可能爆炸，而且「漏洞出現在第三層依賴的某個函式裡，但你的程式碼根本沒呼叫到那個函式」是很常見的情況（這正是 Ch 34 reachability 要解決的問題）。選擇記錄哪幾層關係是一個有意識的設計決策，不是「記越多越好」。

---

## 進階：再往深一層

### OSSPolice 的 hierarchical indexing

OSSPolice（CCS 2017）做的是從 Android app binary 裡識別 OSS 與 license 違規。它的索引設計值得學習：

對 C/C++ 的 OSS 建立兩層索引——
- **粗粒度**：函式名稱的 hash（不 strip 的情況下）+ 特定的唯一字串常數
- **細粒度**：函式的 CFG 特徵

把 60K C/C++ + 77K Java OSS 的這兩層索引放進資料庫後，OSSPolice 分析了 160 萬個 Google Play app，發現超過 4 萬個 GPL/AGPL 違規、超過 10 萬個 app 用了已知有漏洞的 OSS 版本。

對 SBOM 生成引擎的啟示：**hierarchical indexing 是必要的**。先用粗粒度特徵快速過濾，再用細粒度特徵確認版本，才能在合理時間內處理大規模 binary 分析。

### SBOM 生成的增量更新

批次生成 SBOM 的另一個被低估的問題：每次 build 都從零重新掃一遍，太慢。

可以設計一個**增量 cataloger**：對每個 cataloger 維護上次掃描的輸出快照，當 file tree 的 modified time / content hash 沒有變化時，直接重用上次的結果。只有變化的部分重新跑 cataloger。

Go build cache、ccache 的設計思想在這裡完全適用：把 cataloger 的輸出視為 content-addressed 的 cache 項目，輸入（file content hash）不變則輸出不變。

### 圖資料庫作為 SBOM store

當組織有幾千個 repo、每個 repo 有幾份 SBOM 時，把所有 SBOM 存成 JSON 檔案後再做跨 SBOM 查詢（「哪些 service 用了 log4j 2.14.1？」）是難以為繼的。

把 SBOM 的圖資料模型直接存進圖資料庫（Neo4j、Amazon Neptune、JanusGraph）讓這類查詢變成一個 Cypher/Gremlin 查詢：

```cypher
MATCH (c:Component {name: "log4j-core", version: "2.14.1"})
      <-[:DEPENDS_ON*1..]-(s:Service)
RETURN s.name, s.owner
```

這就是 Ch 33 消費平台要解決的問題。SBOM 生成引擎的設計，要考慮後端是不是圖資料庫，或者至少要設計成能以合理代價匯入圖資料庫。

---

## 動手練習

**練習 1：驗證 syft 對 strip C binary 的盲點（WSL）**

```bash
# 在 WSL 裡寫一個依賴 openssl 的 C 程式
cat > /tmp/test_openssl.c << 'EOF'
#include <stdio.h>
#include <openssl/opensslv.h>
int main() {
    printf("OpenSSL version: %s\n", OPENSSL_VERSION_TEXT);
    return 0;
}
EOF

sudo apt-get install -y libssl-dev

# 動態連結後 strip
gcc /tmp/test_openssl.c -o /tmp/test_openssl_dyn -lssl -lcrypto
strip /tmp/test_openssl_dyn

# 用 syft 掃
syft scan file:/tmp/test_openssl_dyn -o table
```

預期觀察：syft 的輸出應該是空的，或者只有 `binary-classifier-cataloger` 靠版本字串 pattern 勉強抓到一條記錄（不可靠）。這就是「manifest 缺失的 C binary 盲區」。

對照：用同樣的方式掃一個 Go binary（即使加了 `-ldflags="-s -w"` strip debug info），`go-module-binary-cataloger` 仍然能從 `.go.buildinfo` 讀出完整依賴清單。C 和 Go 的差異不是 strip 造成的——Go 的 build info section 根本不在 debug symbols 裡。

**練習 2：在紙上設計 SBOM merge 的身份對齊邏輯**

給定以下兩份 SBOM 片段（偽碼表示）：

```
SBOM-A（syft 掃 source dir）：
  Component { purl: "pkg:pypi/requests@2.31.0",
              foundBy: "python-package-cataloger",
              source: "requirements.txt" }

SBOM-B（trivy 掃 image）：
  Component { purl: "pkg:pypi/requests@2.32.3",
              foundBy: "python-egg-package",
              source: "/usr/local/lib/python3.11/site-packages/requests-2.32.3.dist-info" }
```

問題：這兩個是同一個元件嗎？版本不同，但名稱相同。

設計一個 `merge_policy` 函式，要求：
1. 說明你的「身份判斷」依據是什麼（只看 name？看 name+version？看 purl？）
2. 版本衝突時你的策略：丟棄哪個？標記衝突？同時保留？
3. 你的策略對「宣告版本和安裝版本不同」這個現實情況的處理方式

沒有唯一正解，但你的策略必須**可審計**（能事後解釋為什麼做了這個決定）。

**練習 3：閱讀 CENTRIS 論文 Section IV（方法）**

閱讀 CENTRIS（ICSE 2021）的 Section IV「Approach」，然後回答：
1. CENTRIS 定義的 OSS「獨特函式」（unique function）的篩選條件是什麼？
2. 為什麼要去掉「通用」函式？去掉這些函式對 precision/recall 各有什麼影響？
3. 「巢狀元件」（nested component）識別在 CENTRIS 裡如何處理？

---

## 本章重點整理

- SBOM 生成引擎的架構是一個 fallback chain：manifest → OS/語言 metadata → binary build info → fuzzy hash → function-level fingerprint，準確度和成本同步遞升
- 核心介面設計：Cataloger 共用 FileResolver（隔離底層 storage），輸出 Package + Relationship（而非只輸出 Package）
- SBOM 的正確資料模型是帶標記的有向圖：ComponentNode、FileNode、DocumentNode，以及 CONTAINS / DEPENDS_ON / FOUND_BY 等帶屬性的邊
- SBOM merge 的核心難題是身份對齊（identity alignment）：PURL 微妙差異、宣告版本與安裝版本不一致、不同工具的 CPE 推算衝突，都需要明確的衝突解決策略，不能靜默丟棄
- syft 讀 metadata 的做法對三類情境是盲的：C/C++ 靜態連結、vendored 源碼（沒有 manifest）、strip 過的 C binary；Go 是例外——`.go.buildinfo` 不在 debug symbols 段，strip 後仍能讀到
- 深度 binary 識別有三條路：function-level fingerprint（ATVHunter，兩階段 CFG+opcode，precision 90.55%）、code clone detection（CENTRIS，識別修改過的重用，precision 91%）、Code Property Graph（高準確度，成本極高，適合高價值目標）

---

## 自我檢核

- [ ] 我能說出生成引擎的 5 個子系統，以及 Cataloger 為什麼要透過 FileResolver 而非直接操作 FileTree
- [ ] 我能在紙上畫出 SBOM 的圖資料模型（三種節點類型、五種邊類型），並解釋為什麼比清單模型好
- [ ] 我能說出 SBOM merge 時身份對齊的三個主要衝突點，以及各自的處理策略
- [ ] 我能解釋 syft 對 stripped C binary 看不到依賴，但對 stripped Go binary 能看到，原因分別是什麼
- [ ] 我能說出 ATVHunter 的兩階段識別機制，以及「去掉運算元只保留 opcode」對抗混淆的原理
- [ ] 我知道 CENTRIS 和 OSSPolice 處理的問題分別是什麼，兩者解決了哪個不同的挑戰

---

## 精讀論文

**CENTRIS: A Precise and Scalable Approach for Identifying Modified Open-Source Software Reuse**
Woo, Park, Kim, Lee, Oh — ICSE 2021, pp. 860-872

- **核心方法**：把 OSS 切分成「只在此版本出現的獨特函式」，去除跨 OSS 共用的通用函式，用獨特函式的指紋在目標 binary 裡配對，即使部分函式被刪改也能識別；同時處理巢狀元件（A vendor 了 B，B vendor 了 C）。
- **關鍵數字**：10,241 個 GitHub 專案、229,326 版本、800 億行程式碼的資料庫；modified reuse 比 exact reuse 多 20 倍；precision 91%、recall 94%、平均每個 app 不到 1 分鐘。
- **建議讀哪節**：Section IV（Approach）讀完整，特別是 IV-A「Component Segmentation」和 IV-C「Nested Component Handling」。
- **和本章的關聯**：直接回答「vendored/modified OSS 的 binary 識別」這個 syft 做不到的問題；其 component segmentation 的設計是「深度 binary cataloger」的參考藍圖。

---

**Identifying Open-Source License Violation and 1-day Security Risk at Large Scale**（系統名 OSSPolice）
Duan, Bijlani, Xu, Kim, Lee — CCS 2017

- **核心方法**：從 Android app binary 識別使用了哪些 OSS 及版本；hierarchical indexing：先用粗粒度特徵（函式名稱 hash、唯一字串常數）快速過濾，再用細粒度 CFG 特徵確認版本；分別處理 C/C++ native code 和 Java bytecode。
- **關鍵數字**：DB 涵蓋 60K C/C++ + 77K Java OSS；分析 160 萬個 Google Play app；發現 >4 萬個 GPL/AGPL 違規、>10 萬個 app 使用已知漏洞的 OSS 版本。
- **建議讀哪節**：Section III（Design）的 hierarchical indexing 架構，以及 Section V（Evaluation）的 precision/recall breakdown。
- **和本章的關聯**：示範了 hierarchical indexing 在大規模 binary 分析裡的必要性；其「先粗後細」的兩層設計直接對應本章 fallback chain 的設計原則。

---

**ATVHunter: Reliable Version Detection of Third-Party Libraries for Vulnerability Identification in Android Applications**
Zhan, Fan, Chen et al. — ICSE 2021, pp. 1695-1707

- **核心方法**：兩階段識別：第一階段用 CFG 粗粒度特徵找候選 TPL，第二階段用 basic block 的 opcode 序列（去運算元）精確定版本；opcode-only 設計對混淆和編譯器差異有抵抗力。
- **關鍵數字**：DB 189,545 個第三方函式庫、3,006,676 個版本、1,180 條 CVE；precision 90.55%、recall 88.79%；抗混淆測試通過。
- **建議讀哪節**：Section III（Approach）的兩階段設計，以及 Section IV-C（Obfuscation Resistance）。
- **和本章的關聯**：本章「函式級指紋」識別機制的具體實作參考；opcode sequence matching 的設計是對 strip binary 識別的核心技術。

---

**TLSH - A Locality Sensitive Hash**
Oliver, Cheng, Chen — IEEE Cybercrime and Trustworthy Computing Workshop 2013

- **核心方法**：對 binary 計算 locality sensitive hash，讓相似的輸入有相近的 hash 距離；距離 0 = 完全相同，距離愈大差異愈大；計算開銷低，適合大規模初步篩選；和傳統 cryptographic hash（md5、sha256）的差別在於：後者只判斷「相等/不等」，TLSH 判斷「相似程度」。
- **關鍵數字**：原始論文以惡意軟體變種分類為應用場景，後被廣泛用於 binary 相似度分析工具鏈。
- **建議讀哪節**：Section 2（Algorithm Description）了解 hash 構造，Section 4（Experiments）看 false positive rate。
- **和本章的關聯**：本章 fallback chain 裡「fuzzy hashing」層的具體實作；作為 function-level fingerprint 之前的廉價初步篩選，縮小候選範圍。

---

**Modeling and Discovering Vulnerabilities with Code Property Graphs**
Yamaguchi, Golde, Arp, Rieck — IEEE S&P 2014, pp. 590-604

- **核心方法**：把 AST、CFG、PDG 三種程式表示合併成一張屬性圖（Code Property Graph, CPG），然後用圖查詢語言（類 Gremlin）寫「漏洞 pattern」找對應的程式碼片段；一個 pattern 可以跨函式追蹤 taint flow。
- **關鍵數字**：在 Linux kernel 原始碼裡發現多個之前未知的 use-after-free 和 null pointer dereference 漏洞。
- **建議讀哪節**：Section III（Code Property Graphs）讀完整以理解圖結構；Section IV（Traversal-Based Bug Discovery）看如何把漏洞模式寫成圖查詢。
- **和本章的關聯**：CPG 對 SBOM 生成的啟示是「兩個 binary 的 CPG 子圖同構 = 語意等價」，比 opcode matching 更精確也更能對抗編譯器最佳化；本章的「深度識別」段提到 CPG 作為高成本最後手段。

---

## 延伸閱讀

- **[syft source: pkg/cataloger/](https://github.com/anchore/syft/tree/main/syft/pkg/cataloger)**（Anchore GitHub）
  每個 cataloger 的實際實作，`cataloger.go` 裡的 `FinderPatterns` 列出 glob 模式；比閱讀文件更能理解 syft 的能力邊界。

- **[SPDX 3.0 ElementCollection spec](https://spdx.github.io/spdx-spec/v3.0/)**（SPDX GitHub）
  讀「Collection」相關 section，理解 SPDX 3.0 如何用 namespace 隔離不同工具生成的 SBOM fragment，讓 merge 後的 provenance 保持可追溯。

- **[cargo-auditable: How it works](https://github.com/rust-secure-code/cargo-auditable)**
  理解 Rust 生態為什麼需要一個額外工具做 Go 原生就有的事；`.dep-v0` section 的格式設計是嵌入式 SBOM 的另一種形式。

- **[Joern: Open Source Code Analysis Platform](https://github.com/joernio/joern)**
  CPG 的開源實作，支援 C/C++/Java/Python；本章提到的 CPG 識別方法在這裡有可操作的工具。

- **[NTIA Framing SBOM: Myths vs. Facts](https://www.cisa.gov/sites/default/files/publications/SBOM_Myths%20vs%20Facts_Nov2021_0.pdf)**（NTIA/CISA）
  官方承認 SBOM 生成有盲點，正視「沒有工具能 100% 完整」這件事；是本章深水區內容（靜態連結、vendored 程式碼）的政策層對照。

---

SBOM 生成引擎處理的是「能看到什麼」的問題：manifest-based 的高準確度路徑，和 binary 指紋的概率性路徑。下一章切換到消費端——有了 SBOM 之後，怎麼把元件清單和漏洞資料庫關聯起來，以及可達性分析如何幫你過濾掉「有漏洞但你根本不呼叫那個函式」的誤報。

→ [Ch 33 — 消費平台的架構：漏洞關聯與可達性](./33-consumption-platform-correlation.md)
