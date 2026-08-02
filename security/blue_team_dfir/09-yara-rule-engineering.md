# Ch 9 — YARA 規則工程

> 目標：學會撰寫能在實戰中命中真實惡意程式的 YARA 規則——從語法結構、PE module、記憶體掃描，到效能與假陽性控制。

## 為什麼需要 YARA？

Sigma 解決的是「log 裡有什麼行為」，YARA 解決的是「**這個二進位/記憶體區塊本身是什麼**」。兩者互補。

Sigma 看不到的東西 YARA 能抓：
- 惡意程式落到磁碟的 payload 本體
- shellcode 注入進程記憶體後的特徵位元組
- 同一個 malware family 共用的 packer stub
- 未執行的惡意文件（Office 巨集、PDF embedded PE）

使用場景三類：
1. **樣本分類**：入庫新樣本時，自動打標「這是 Cobalt Strike」「這是 LockBit」
2. **即時掃描**：AV/EDR 掃磁碟或記憶體中執行中的 process
3. **事件應急**：拿到可疑檔案或 memory dump，跑一批規則快速定性

## 先建立直覺

YARA 規則的邏輯跟你寫 C 的 `if` 一模一樣：**定義一批字串特徵，再寫條件說「至少幾個要同時出現」**。

```
你是不是 Mimikatz？
  → 你的二進位裡有 "sekurlsa" 嗎？
  → 你匯入了 LsaConnectUntrusted 嗎？
  → 你的 PE section 叫 ".data" 但大小是 0 嗎？
三個問題如果答案都是 yes → 打標 Mimikatz
```

這就是 YARA 在做的事。

## 規則結構全景

```yara
rule ExampleMalware {
    meta:
        author      = "analyst"
        date        = "2025-01-15"
        description = "Example packer stub"
        tlp         = "WHITE"
        reference   = "https://example.com/malware-report"

    strings:
        $str1  = "sekurlsa"         ascii nocase
        $str2  = "LsaConnectUntrusted" wide
        $hex1  = { 48 8B 05 ?? ?? ?? ?? 48 85 C0 74 ?? }
        $re1   = /mimi(katz|drv)/i

    condition:
        uint16(0) == 0x5A4D and   // 是 PE
        filesize < 2MB and
        2 of ($str*, $hex1)
}
```

三個區塊：`meta`、`strings`、`condition`，其中 `meta` 是選用的，其餘必填。

### strings 區塊——三種字串類型

**1. 文字字串 (text string)**

```yara
$a = "cmd.exe /c"            // 預設 case-sensitive，ascii
$b = "powershell" nocase     // 不分大小寫
$c = "PasSword"   wide       // UTF-16LE（Windows Unicode），搜 P\x00a\x00s\x00...
$d = "hello"      ascii wide // 同時搜兩種編碼
```

`wide` 是 Windows 二進位裡最常用的修飾詞，因為 Windows API 大量使用 `WCHAR *`（UTF-16LE）。

**2. 十六進位字串 (hex string)**

```yara
$shellcode = { E8 00 00 00 00 5B 81 EB ?? ?? ?? ?? }
//                                   ^^ 單一 wildcard byte
$mov_rax   = { 48 B8 [4-8] C3 }
//                     ^^^^ 4 到 8 個任意 byte（跳過可變長度欄位）
$or_pattern = { ( 90 | 0F 1F 40 00 ) 48 89 }
//               ^^^^^^^^^^^^^^^^^ 二選一
```

hex string 不能加 `nocase` 或 `wide`，因為它本來就是 raw bytes。

**3. 正規表達式 (regex string)**

```yara
$domain = /[a-z0-9]{6,12}\.(top|xyz|club)/   // DGA 域名特徵
$b64    = /[A-Za-z0-9+\/]{40,}={0,2}/         // Base64 payload
```

YARA 的 regex 引擎支援 PCRE 子集，但**不支援** look-behind。複雜 regex 效能差，謹慎使用。

### condition 區塊——常用語法

```yara
// 計數與選擇
all of them                    // 所有字串都要命中
any of ($str*)                 // $str 開頭的任一個
2 of ($a, $b, $c)             // 三個裡面至少兩個
1 of ($hex*)                   // $hex 開頭任一個

// 位置與偏移
$a at 0                        // $a 出現在 offset 0
$a in (0x400..0x600)          // 在這個 offset 範圍內
#a > 3                         // $a 出現超過 3 次（count operator）
@a[0] < @b[0]                  // $a 第一個出現在 $b 第一個之前

// 檔案屬性
filesize < 500KB
filesize > 1MB and filesize < 10MB
uint16(0) == 0x5A4D            // PE magic: "MZ"
uint32(uint32(0x3C)) == 0x4550 // 從 e_lfanew 驗 "PE\x00\x00"
```

## 底層機制

YARA 掃描的核心是 Aho-Corasick 演算法——把所有的字串模式建成一棵自動機，單次 O(n) 掃過整個緩衝區，順帶命中所有匹配。這也是為什麼「字串太短」會造成效能問題：短字串（< 4 bytes）會讓 Aho-Corasick 的 goto 表爆炸，誤中率高、過濾效率差。

YARA 掃描流程：
```
輸入（檔案 / 記憶體區塊）
        │
  ┌─────▼──────┐
  │ 字串掃描   │  Aho-Corasick，找出所有命中與 offset
  └─────┬──────┘
        │ hit list
  ┌─────▼──────┐
  │ condition  │  evaluate（支援 module 呼叫）
  │ evaluation │
  └─────┬──────┘
        │ bool
   命中 / 不命中
```

## PE Module：抓匯入表、section、資源

光看位元組還不夠，`pe` module 讓你查詢 PE 結構欄位：

```yara
import "pe"

rule Mimikatz_Import {
    meta:
        description = "Mimikatz 特有匯入函式組合"

    condition:
        pe.imports("cryptdll.dll", "MD5Init") and
        pe.imports("cryptdll.dll", "MD5Update") and
        pe.imports("ntdll.dll", "NtUnmapViewOfSection")
}
```

`pe.imphash()` 可以抓 Import Hash（imphash），同一個 malware family 編譯出的 imphash 通常相同：

```yara
rule AgentTesla_Imphash {
    condition:
        pe.imphash() == "f34d5f2d4577ed6d9ceec516c1f5a744"
}
```

抓 section 特徵（常見於 packer 分析）：

```yara
import "pe"

rule UPX_Packed {
    meta:
        description = "UPX packer 特徵"
    condition:
        pe.sections[0].name == "UPX0" and
        pe.sections[1].name == "UPX1" and
        pe.number_of_sections == 3
}
```

抓時間戳記是否歸零（惡意程式常清掉）：

```yara
condition:
    pe.timestamp == 0
```

## 掃記憶體——yara -p 與 process scan

YARA 不只能掃檔案，也能掃執行中 process 的記憶體：

```bash
# 掃特定 PID
yara -p 4 my_rules.yar 1234

# 掃所有 process（需要管理員權限）
yara -p 4 my_rules.yar --pid=*

# 掃記憶體 dump（Volatility 匯出的 .dmp 或 procdump）
yara my_rules.yar suspicious.dmp
```

`-p N` 控制平行執行緒數量。掃記憶體時，YARA 會讀取 process 每一個 mapped region，所以可以抓住注入進正常 process 的 shellcode——即使它沒有落到磁碟，也沒有對應的檔案。

**記憶體掃描的陷阱**：運行時的 process 記憶體跟磁碟上的 PE 結構不同，PE header 可能被惡意程式清掉（reflective loading），`pe` module 的條件在記憶體掃描時可能失效。這種情況要改用純 hex/regex 字串規則。

## 三條真實規則範例

### 規則 1：Cobalt Strike Beacon 特徵

Cobalt Strike Beacon 在記憶體中有幾個穩定的字串，是歷版本都不太會變的內部標識：

```yara
rule CobaltStrike_Beacon_Memory {
    meta:
        description  = "Cobalt Strike Beacon 記憶體特徵（多版本通用）"
        author       = "Florian Roth"
        reference    = "https://github.com/Neo23x0/signature-base"
        tlp          = "WHITE"

    strings:
        $s1 = "%s (admin)" ascii
        $s2 = "beacon.dll" ascii
        $s3 = "ReflectiveLoader" ascii
        $s4 = { 69 68 69 68 69 6B }   // "ihihik" — CS 內部魔術字串
        $x1 = "%%COMSPEC%% /b /c start /b /min" ascii
        $x2 = "IEX (New-Object Net.Webclient).DownloadString" ascii nocase

    condition:
        ($s1 and $s2 and $s3) or
        ($s4 and 1 of ($x*))
}
```

這條規則混合了字串與 hex，用 `or` 讓任一組特徵都能命中，提高召回率（recall）。

### 規則 2：Mimikatz sekurlsa 模組

```yara
rule Mimikatz_sekurlsa {
    meta:
        description = "Mimikatz sekurlsa 模組特徵"
        reference   = "https://github.com/gentilkiwi/mimikatz"

    strings:
        $s1 = "sekurlsa" ascii nocase
        $s2 = "kiwi_cmd" ascii
        $s3 = "wdigest.dll" ascii
        $s4 = { 48 8B 05 ?? ?? ?? ?? 48 85 C0 74 }  // 典型 sekurlsa 函式頭
        $import1 = "LsaConnectUntrusted" ascii
        $import2 = "LsaLookupAuthenticationPackage" ascii

    condition:
        uint16(0) == 0x5A4D and
        (
            (2 of ($s*)) or
            (all of ($import*))
        )
}
```

### 規則 3：Office 巨集內嵌 PE（dropper 特徵）

很多 maldoc 把 PE 以 Base64 或 hex 字串藏在 Office 文件裡，在執行期解碼再落地。這條規則抓這個特徵：

```yara
rule MalDoc_Embedded_PE_Base64 {
    meta:
        description = "Office 文件含 Base64 編碼 PE（dropper 常見技巧）"

    strings:
        $mz_b64_1 = "TVqQAAMAAAAEAAAA"  // MZ 開頭 Base64（對齊 offset 0）
        $mz_b64_2 = "TVoAAA"            // 另一種常見 MZ Base64 開頭
        $mz_b64_3 = "TVpAAA"
        $macro1   = "AutoOpen" ascii nocase
        $macro2   = "Shell(" ascii
        $macro3   = "WScript.Shell" ascii

    condition:
        filesize < 5MB and
        1 of ($mz_b64*) and
        1 of ($macro*)
}
```

這條規則故意不要求是 PE 格式（`uint16(0) == 0x5A4D` 不適用 Office 文件），改用 `filesize` 過濾。

## 假陽性與效能控制

### 假陽性來源

| 原因 | 範例 | 對策 |
|---|---|---|
| 字串太通用 | `"cmd.exe"` 幾乎所有 PE 都有 | 加 context，要求字串組合出現 |
| 短 hex 片段 | `{ 48 8B C0 }` 是常見 `mov rax, rax` | 延長至 8 byte 以上含前後文 |
| 合法工具有相同功能 | `LsaConnectUntrusted` 也被 sysadmin 工具呼叫 | 加入排除條件或提高 threshold |
| regex 太寬鬆 | `/[a-z]+\.exe/` | 加長度限制或更精確的字符類 |

### 效能規則

- **字串長度 > 4 bytes**：YARA 對 >= 4 bytes 的字串做 hash table 預篩，短於此長度直接線性掃，效能差一個量級。
- **避免 `nocase` + regex 組合**：YARA 在 case-insensitive regex 模式下無法用 hash 預篩，每個位置都要跑 regex。
- **`filesize` 過濾要放 condition 最前面**：YARA 短路求值，`filesize < 1MB and ...` 對大檔案可以立刻跳過，不跑字串掃描。
- **`for` loops 要謹慎**：`for any i in (0..pe.number_of_sections): (pe.sections[i].name == "UPX0")` 比逐一展開慢，大規模掃描時盡量用 module 的直接查詢。

## 踩雷

1. **`wide` 和 `ascii` 搞混**：Windows 惡意程式的字串 99% 是 `wide`（UTF-16LE），如果只寫 `ascii` 會完全掃不到。遇到命中率莫名其妙低，先加 `wide` 試試。

2. **hex wildcard `??` 跟 jump `[n-m]` 用錯場合**：`??` 是單一 wildcard byte，`[4-8]` 是「跳過 4 到 8 個任意 byte」。把 `[?]` 寫成 `[??]` 是語法錯誤，YARA 會拒絕執行。

3. **記憶體掃描找不到 PE header 特徵**：reflective loader 會清除 DOS header，記憶體中的 beacon `uint16(0) != 0x5A4D`。這種情況把 PE 條件從 condition 移除，改用內部字串特徵。

4. **`pe` module 在掃非 PE 格式（OLE、PDF）時失效**：`pe.imports()` 在非 PE 格式上會回傳 undefined，condition 求值為 false 而不是報錯，容易誤以為規則正常而其實從沒命中過。

5. **規則 ID（rule name）重複**：同一個 `.yar` 檔裡不能有同名規則，跨檔案則需要 `include` 管理。CI 流程裡加 `--fail-on-warnings` 可以抓到這類問題。

## 進階延伸

- **YARA-L**（Google Chronicle 方言）：把 YARA 擴充到 log-level 偵測，語法相似但語意不同。
- **dotnet module**：`pe` module 的延伸，可以查 .NET 組件的類別名稱、命名空間，對 .NET 系 RAT 特別有用。
- **`math` module**：計算 section 的 entropy，entropy > 7.0 通常代表加密/壓縮（packer 特徵）。
- **yaramod**（Python 函式庫）：程式化產生與驗證 YARA 規則，適合把規則生成整合進 CI pipeline。
- **YARA-X**：YARA 作者正在開發的下一代，Rust 重寫，效能更好、語法更嚴格。追蹤 [https://github.com/VirusTotal/yara-x](https://github.com/VirusTotal/yara-x)。

## 本章重點整理

- YARA = 字串特徵 + 布林條件，三區塊：`meta`、`strings`、`condition`
- 三種字串類型：text（支援 `wide`/`nocase`）、hex（支援 wildcard `??` 和 jump `[n-m]`）、regex
- condition 運算子：`all of them`、`N of ($x*)`、`#a > N`、`at`、`in`
- PE module 可查匯入表（`pe.imports()`）、imphash、section 名稱、時間戳
- `yara -p N` 掃 process 記憶體；reflective loader 會破壞 PE header，純字串規則更穩
- 效能：字串 >= 4 bytes、`filesize` 前置過濾、避免 `nocase` regex 組合

## 自我檢核

- [ ] 能從零寫出包含 hex string wildcard 的完整 YARA 規則
- [ ] 知道 `wide` 修飾詞對哪類字串有效、何時必加
- [ ] 能用 `pe` module 查匯入函式與 imphash
- [ ] 知道為什麼記憶體掃描時 PE header 條件可能失效
- [ ] 能說出三個造成假陽性的常見原因與對策
- [ ] 能說出影響 YARA 效能的三個關鍵因素

## 延伸閱讀

1. **YARA 官方文件** [https://yara.readthedocs.io/](https://yara.readthedocs.io/)
   — 所有 module、語法細節、modifier 的權威參考；PE module 的欄位清單在此。

2. **Florian Roth's signature-base** [https://github.com/Neo23x0/signature-base](https://github.com/Neo23x0/signature-base)
   — 數千條實戰 YARA 規則，讀真實規則比讀教材學得快；搭配 `--explain` flag 看命中理由。

3. **"YARA Performance Guidelines"** — Florian Roth, 2015（部落格文章，可搜尋）
   — 效能踩雷大全，每條規則都附 benchmark 數字；本章效能小節的主要來源。

4. **VirusTotal YARA 競賽歷屆規則**（可在 VT blog 找到）
   — 看頂尖 analyst 怎麼在「不能有太多假陽性」的約束下寫規則，學取捨思維。

5. **"Practical Malware Analysis" Ch. 8**（Sikorski & Honig）
   — 惡意程式特徵提取的基礎方法論，和本章配合食用；為什麼某些位元組序列比字串更穩定。

---

下一章我們把偵測規則（Sigma + YARA）對映到 MITRE ATT&CK，量化涵蓋度——以及為什麼「把 Navigator 塗綠」是假安全感。

→ [Ch 10 ATT&CK 對映與偵測涵蓋度](./10-attack-mapping-coverage.md)
