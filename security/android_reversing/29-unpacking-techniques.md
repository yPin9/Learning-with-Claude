# Ch 29 — 脫殼技術

> **目標**：把 Ch 28 的「還原點」地圖變成動手能脫殼的技術。你會學：**記憶體 dump 的原理**（怎麼在進程記憶體裡掃 `dex\n035` magic 整包撈出）、**frida-dexdump** 怎麼遍歷 ClassLoader 撈 DEX、**FART / dump_dex 的主動調用**怎麼破二代抽取殼、hook **`OpenMemory`/`dexFileParse`/`DefineClass`** 攔還原點、以及最關鍵也最常被略過的一步——**修復 dump 出來的殘缺 DEX**，讓它能被 jadx 打開。這章接練習 D（脫殼 + 繞反調試把 App 跑起來）與 Ch 36（從 ART 內部脫殼），是 Part 5 的實戰高潮。

> **環境**：脫殼的**邏輯**（記憶體掃 magic、修 DEX header）在本 repo 用 **Python 3.12 實跑**，標「**實際輸出**」——這些是脫殼工具內部真正做的事。但**實際對加殼 App 執行脫殼**需要跑得起加殼 App 的環境，而多數商用殼**只支援 ARM、帶反模擬器與反 Frida 檢測**，x86_64 AVD 往往起不來（Ch 28 已述）——所以 frida-dexdump/FART 對真實商用殼的行為標「**未實測，理論預期行為**」並給你在 **ARM64 真機（root）** 上的驗證步驟；AVD 上能做的（對自製一代殼樣本 dump）分開講。絕不拿沒跑過的殼結果裝成跑過。

## 為什麼需要這個？

Ch 28 建立了物理事實：**執行期真 DEX 必然在記憶體**（CPU 才能執行）。脫殼就是把這個「必然」變現——趁明文 DEX 在記憶體的那一刻，把它撈出來、修好、拿回一個 jadx 能讀的真 DEX。

這是整個 Part 5「對抗」的收口。前面 Ch 26/27 對付混淆（程式碼難讀但還在），Ch 28 講清殼把 DEX 藏哪、何時還原。到這章，你要真的把藏起來的東西挖回來。脫殼成功，加固這道牆就破了——後面的邏輯分析、native 逆向、協議還原（Part 2–4 的功夫）才有對象。脫殼失敗，你連真程式碼長什麼樣都不知道，一切免談。

而且脫殼考驗的是**理解而非抄工具**：frida-dexdump 對一代殼一鍵搞定，但換二代抽取殼就得懂主動調用、換個 Android 版本就得改 hook 點、dump 出來還常是殘缺的要手修。懂原理，你能跟著殼的演進調整；只會抄腳本，換個殼就卡死。

## 先建立直覺：脫殼三步

不管哪代殼、哪個工具，脫殼都是同一套三步骨架：

```
   ①  找到明文 DEX 在記憶體的時機與位置
        │  （殼在 attachBaseContext/onCreate 解密載入；ART 用到方法時還原）
        ▼
   ②  把它 dump 出來
        │  ├─ 掃記憶體找 dex\n035 magic → 讀 file_size → 整塊 copy 出來
        │  └─ 或遍歷 ClassLoader → DexFile → 拿到 DEX 在記憶體的起始位址
        ▼
   ③  修復 dump 出的 DEX
        │  （over-dump 的尾巴要截、被抹的欄位要補、二代殼殘缺的 CodeItem 要拼）
        ▼
      得到 jadx 能打開的真 DEX
```

三步各自的難點對應不同代的殼：

- **一代殼**：卡在 ①（找時機，`onCreate` 之後就好），②③ 很順——整包完整、magic 完好，掃出來截好就行。
- **二代抽取殼**：卡在 ①②——單次 dump 只拿到已執行的方法，要**主動調用逼每個方法還原**，還原後攔截 `CodeItem` 拼回去。
- **VMP**：這套三步不適用（明文 Dalvik 從不出現），要逆 VM，不在本章範圍。

一條主線貫穿：**dump 的本質是「在對的時刻，把記憶體裡的明文 DEX 位元組 copy 出來」**。工具的差別只在「怎麼找到那塊記憶體、怎麼逼殼把它還原完整」。

## 技術一：記憶體 dump —— 掃 magic 整包撈（打一代殼）

最直接的脫殼：既然明文 DEX 在記憶體、且開頭是 `dex\n035\0` magic，那就**掃描進程記憶體，找到 magic，讀 header 的 `file_size` 欄位，把那一整塊 copy 出來**。這正是 frida-dexdump 的核心動作，也是理解一切 dump 工具的原點。

DEX 在記憶體的可辨識特徵（Ch 2 學過的 header）：

```
offset 0   magic       "dex\n035\0"   ← 掃描的靶點（也有 037/038/039）
offset 8   checksum     adler32
offset 12  signature    SHA-1
offset 32  file_size    ← 讀這個！知道要 copy 多少 byte
offset 36  header_size  0x70
offset 40  endian_tag   0x12345678    ← 可當第二特徵，減少誤判
```

我用 Python 模擬「一塊記憶體裡藏了兩個 DEX + 雜訊」，掃 magic 把它們定位出來——**這就是 dump 工具在進程記憶體裡做的事**（**實際輸出**）：

```python
import struct, zlib, hashlib

def build_dex(body_len=100, version=b"035"):
    magic = b"dex\n" + version + b"\x00"
    data = bytearray(bytearray(0x70) + b"\x00"*body_len)
    data[0:8] = magic
    struct.pack_into("<I", data, 32, len(data))        # file_size
    struct.pack_into("<I", data, 36, 0x70)             # header_size
    struct.pack_into("<I", data, 40, 0x12345678)       # endian_tag
    data[12:32] = hashlib.sha1(bytes(data[32:])).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(bytes(data[12:])) & 0xffffffff)
    return bytes(data)

# 模擬進程記憶體：雜訊 + dex1 + 雜訊 + dex2（不同版本） + 雜訊
blob = b"\x00"*17 + build_dex(64) + b"\x11\x22\x33"*10 + build_dex(128, b"038") + b"\xff"*7

MAGICS = [b"dex\n035\x00", b"dex\n037\x00", b"dex\n038\x00", b"dex\n039\x00"]
i = 0
while i < len(blob) - 8:
    for m in MAGICS:
        if blob[i:i+len(m)] == m:
            size = struct.unpack_from("<I", blob, i+32)[0]   # 讀 file_size
            endian = struct.unpack_from("<I", blob, i+40)[0]
            if endian == 0x12345678:                          # 第二特徵防誤判
                print(f"found DEX @ offset {i:>4}  version={blob[i+4:i+7].decode()}  file_size={size}")
    i += 1
```

```
found DEX @ offset   17  version=035  file_size=176
found DEX @ offset  223  version=038  file_size=240
```

掃到 offset 17 和 223 兩個 DEX，各自讀出 `file_size`，就能把 `blob[17:17+176]` 和 `blob[223:223+240]` copy 出來——**這兩塊就是脫出的 DEX**。真實脫殼裡，`blob` 換成 `/proc/<pid>/maps` 列出的可讀記憶體段（透過 Frida 的 `Process.enumerateRanges` / `Memory.readByteArray`），掃法一模一樣。

> **為什麼 `endian_tag` 當第二特徵重要**：光掃 `dex\n035` 字串可能誤中「剛好資料裡有這幾個 byte」的假陽性。加驗 offset 40 的 `endian_tag == 0x12345678` 和 `header_size == 0x70`，大幅降低誤判——這是工程上讓掃描可靠的小訣竅。

## 技術二：frida-dexdump —— 遍歷 ClassLoader + 掃記憶體

`frida-dexdump`（`pip install frida-dexdump`）是把上面的掃描包成一鍵工具。它做兩件事的組合：

1. **遍歷 ClassLoader 找 DexFile**：殼載入真 DEX 後，DEX 掛在某個 ClassLoader 的 `DexFile` 上。frida-dexdump 用 Frida 遍歷所有 ClassLoader → 拿到 `DexFile` 物件 → 讀出它在記憶體的起始位址與大小。
2. **掃記憶體補漏**：對 ClassLoader 遍歷不到的（例如殼用底層 API 載入、沒掛在標準 ClassLoader 上），退回「掃 `dex\n035` magic」那條路（技術一），把整個進程可讀記憶體掃一遍撈 DEX。

用法（**未實測，理論預期行為**——需跑得起的加殼 App，多為 ARM 真機）：

```bash
# 前提：目標 App 已在裝置上跑、frida-server 已起、能 attach（沒被反 Frida 擋，Ch 30）
frida-dexdump -U -f com.target.app          # spawn 並脫殼
# 或 attach 到已跑的進程：
frida-dexdump -U -n com.target.app
#   輸出：把撈到的 DEX 存成 ./com.target.app/classesN.dex
```

**你在 ARM64 真機的驗證步驟**：(1) root 真機、裝對應版本 frida-server；(2) 目標若有反 Frida，先繞（Ch 30，例如改 frida-server 名、用 gadget 模式）；(3) 跑 `frida-dexdump`，等 App 進到主畫面（讓一代殼的整包 DEX 已載入）再脫；(4) 把 dump 出的 `classes*.dex` 丟 jadx 看是不是真邏輯。若 dump 出的方法大量是 nop → 是二代抽取殼，frida-dexdump 的整包掃不夠，得上主動調用（技術四）。

> **時機很重要**：太早脫（`Application.onCreate` 前）真 DEX 還沒解密載入，掃不到；等 App 跑進主畫面再脫，一代殼的整包 DEX 通常已完整在記憶體。frida-dexdump 允許你 attach 到已跑起來的進程正是為此。

## 技術三：hook 還原點 —— `OpenMemory` / `dexFileParse` / `DefineClass`

掃記憶體是「被動撈」，更精準的是**主動 hook 殼把 DEX 交給 ART 的那一刻**——因為不管殼怎麼解密，它最終都得呼叫 ART 的 DEX 載入 API 把真 DEX 交給 runtime。守在這些 API 上，DEX 一還原你就攔到，位址和大小都是現成的。

關鍵 hook 點（ART native 層，`libart.so` 的匯出/內部符號）：

```
殼解密真 DEX
    │  最終呼叫 ART 的 DEX 載入路徑：
    ▼
DexFile::OpenMemory(base, size, ...)   ◀── 從記憶體 buffer 開 DEX；base/size 直接給你 DEX 位置！
    │        （新版本相關符號：OpenCommon / DexFileLoader::Open）
    ▼
DexFile::dexFileParse / DexFileVerifier  ◀── 解析/驗證 DEX header，此刻 DEX 已完整
    │
    ▼
ClassLinker::DefineClass(...)          ◀── 逐類定義；hook 它能攔到每個被載入的類
```

hook `OpenMemory` 是脫一代殼最漂亮的招——它的參數 `(base, size)` **直接就是明文 DEX 在記憶體的起始位址與長度**，`Memory.readByteArray(base, size)` 存檔即得完整 DEX，連掃 magic 都省了。Frida 概念腳本（**未實測，理論預期行為**；符號名隨 Android 版本變，需先 `Module.enumerateExports('libart.so')` 找對符號）：

```javascript
// hook ART 的 OpenMemory：DEX 一被開啟就 dump（符號名依版本，示意）
var openMem = Module.findExportByName("libart.so",
    "_ZN3art7DexFile10OpenMemoryEPKhjRKNSt3__112basic_stringIcNS3_11char_traitsIcEENS3_9allocatorIcEEEEjPNS_6MemMapEPKNS_10OatDexFileEPS9_");
Interceptor.attach(openMem, {
    onEnter: function (args) {
        var base = args[1];                 // const uint8_t* base
        var size = args[2].toInt32();       // size_t size
        if (size > 0x40) {                  // 過濾太小的
            var dex = Memory.readByteArray(base, size);
            // 存檔（透過 RPC 傳回 host 或寫 /data/local/tmp）
            console.log("[dump] DEX @ " + base + " size=" + size);
        }
    }
});
```

**為什麼這招對二代殼仍不完整**：二代抽取殼呼叫 `OpenMemory` 時，DEX 的**類結構在、但方法的 `CodeItem` 是空的**（指令還沒還原）。你在 `OpenMemory` dump 到的是「有骨架沒血肉」的 DEX。要拿到血肉，得等每個方法被執行時還原——這就要技術四。

> **符號名的坑**：`libart.so` 的 C++ 符號是 mangled 的、且**每個 Android 版本都可能變**。別硬抄別人的符號字串，用 `Module.enumerateExports('libart.so')` 過濾含 `OpenMemory`/`DefineClass`/`DexFile` 的符號，或用 frida 的 `DebugSymbol` 解析。抄死符號名是 hook 還原點最常見的翻車點。

## 技術四：FART 與主動調用 —— 破二代抽取殼

二代抽取殼的死穴（Ch 28）：一個時刻只有**已執行過的方法**有明文 `CodeItem`，沒跑到的還是空的。單次 dump 必然殘缺。**FART（Frida ART / 主動調用脫殼，作者 hanbinglengyue）** 的破法直擊要害：**主動把 App 的每一個方法都呼叫一遍，逼殼把每個方法的 `CodeItem` 都還原，同時攔截還原後的 bytecode，拼回完整 DEX。**

「主動調用」是關鍵詞——不等 App 自然執行到某方法（那樣覆蓋不全），而是脫殼器**遍歷所有類的所有方法，用 ArtMethod 層的 invoke 主動觸發它們**，每觸發一個就逼殼還原一個。

```
   FART 的主動調用流程

 遍歷 ClassLoader → 所有已載入的類
        │
        ▼
 對每個類的每個 Method：
        │  用 ArtMethod::Invoke 主動呼叫（或反射 Method.invoke）
        ▼
 殼攔到「方法要執行」→ 還原該方法的 CodeItem（明文指令填回）
        │
        ▼
 FART 在 ART 執行方法的入口 hook，攔下剛還原的 CodeItem
        │  （記下 method_idx + 真 bytecode）
        ▼
 全部方法跑完 → 收集到所有 CodeItem → 拼回完整 DEX
```

FART 有兩部分：**ART 層的 hook**（改 ART 原始碼或用 Frida，在方法執行入口攔 `CodeItem`）+ **主動調用引擎**（遍歷 + invoke 每個方法）。原版 FART 是**改 AOSP ART 原始碼**編一個特製 ROM/框架（在 `ArtMethod::Invoke` 等處插 dump 邏輯），純度高但要刷機；也有 Frida 版把思路搬到 Frida 上（不用刷機但更受版本與反 Frida 影響）。

**用 FART 的現實**（**未實測，理論預期行為**）：需要對應 Android 版本的 FART 框架/ROM（原版綁特定 AOSP 版本），跑在 root 真機。主動調用可能觸發 App 的副作用（有些方法一呼叫就崩或彈窗），FART 對此有容錯但不完美。dump 完得到每個方法的 `CodeItem`，工具幫你**重組回 DEX**（把 CodeItem 填回被抽空的 DEX 骨架）。

> **主動調用的邊界**：不是每個方法都能安全主動調用——建構子、依賴特定狀態的方法、native 方法，硬 invoke 可能崩潰或無意義。FART 會跳過部分、對崩潰做保護。所以 FART 脫二代殼的產物**可能仍有少數方法沒還原**，需要多跑幾次不同執行路徑、或人工補。這是二代殼比一代難的根本體現。

## 技術五：修復 dump 出的 DEX —— 最常被略過卻最卡人的一步

dump 出來的 DEX 很少能直接用。常見三種「壞」：

1. **over-dump（多撈了尾巴）**：掃 magic 時若沒精確用 `file_size` 截斷、或整段 copy 了記憶體，DEX 後面接了一堆不屬於它的位元組。
2. **header 欄位被殼改壞**：進階殼會故意把 `file_size`、`checksum`、`signature` 等欄位改壞（anti-dump），讓你 dump 出來的 DEX 打不開。
3. **二代殼的 CodeItem 殘缺**：部分方法還是 nop（沒主動調用到）。

前兩種可以程式化修復。核心邏輯：**按真實內容重算 `file_size`、`checksum`、`signature`**（Ch 2 學過這三個欄位）。我用 Python 演示「over-dump 後截斷 + 重算完整性欄位」——**這正是 DEX 修復工具做的事**（**實際輸出**）：

```python
import struct, zlib, hashlib

def build_dex(body_len=100):
    data = bytearray(bytearray(0x70) + b"\x00"*body_len)
    data[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", data, 32, len(data)); struct.pack_into("<I", data, 36, 0x70)
    struct.pack_into("<I", data, 40, 0x12345678)
    data[12:32] = hashlib.sha1(bytes(data[32:])).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(bytes(data[12:])) & 0xffffffff)
    return bytes(data)

orig = build_dex()
dumped = bytearray(orig + b"\xAA"*40)                 # over-dump：尾巴多了 40 byte 垃圾
declared = struct.unpack_from("<I", dumped, 32)[0]    # header 記的真實大小
print("dumped 長度:", len(dumped), " header 記的 file_size:", declared)

fixed = bytearray(dumped[:declared])                              # ① 按 file_size 截斷尾巴
fixed[12:32] = hashlib.sha1(bytes(fixed[32:])).digest()          # ② 重算 signature = SHA-1(bytes[32:])
struct.pack_into("<I", fixed, 8, zlib.adler32(bytes(fixed[12:])) & 0xffffffff)  # ③ 重算 checksum
print("修復後長度:", len(fixed))
print("修復後 checksum 對:", fixed[8:12] == orig[8:12], " signature 對:", fixed[12:32] == orig[12:32])
```

```
dumped 長度: 252  header 記的 file_size: 212
修復後長度: 212
修復後 checksum 對: True  signature 對: True
```

三步——**依 `file_size` 截斷 → 重算 SHA-1 signature → 重算 adler32 checksum**——修出的 DEX 完整性欄位與原始一致，jadx/baksmali 就認得。若殼連 `file_size` 都改壞了，你得從 DEX 的 `map_list`（結尾的區段表，Ch 4 深挖）反推真實大小，或靠工具（如 `DexFixer`、`dexfix`）啟發式修復。二代殼殘缺的 `CodeItem` 則沒法純靠 header 修，得靠技術四把指令補回。

> **這一步為什麼最卡人**：新手常在「dump 出來了但 jadx 打不開/崩潰」卡住，以為脫殼失敗。多半不是沒 dump 到，是 dump 出的 DEX 沒修——over-dump 的尾巴或壞掉的 header。先跑一遍上面的截斷+重算，八成的「打不開」就解決了。

## 對比與取捨：脫殼技術選型

| 技術 | 打哪代殼 | 需要環境 | 產物完整度 | 難度 |
|---|---|---|---|---|
| **記憶體掃 magic dump** | 一代（整包） | Frida，能 attach | 完整（需截斷修復） | 低 |
| **frida-dexdump** | 一代為主 | Frida，多為 ARM 真機 | 一代完整 / 二代殘缺 | 低（一鍵） |
| **hook `OpenMemory`/`dexFileParse`** | 一代 + 二代骨架 | Frida，找對符號 | 一代完整 / 二代缺 CodeItem | 中 |
| **FART / 主動調用（dump_dex）** | 二代抽取殼 | 特製 ROM/框架，root 真機 | 二代較完整（少數方法可能缺） | 高 |
| **逆 VM（devirtualization）** | VMP | 手工 + 符號執行 | 視功力 | 極高 |

選型口訣：**先用 frida-dexdump 一鍵試**（一代秒脫）→ **dump 出來 jadx 打開看方法是不是大量 nop**→ 是（二代抽取殼）就上 **FART/主動調用** → 遇到 VMP（明文 Dalvik 從不出現）才需逆 VM。永遠先修復再判斷「脫得對不對」，別讓沒修的 DEX 誤導你以為脫失敗。

## 踩雷集錦

1. **dump 出來 jadx 打不開就以為脫失敗**：九成是沒修復——over-dump 的尾巴沒截、`checksum`/`signature` 沒重算。先跑截斷+重算三步，再下結論。
2. **在 x86_64 AVD 上脫商用殼**：多數殼只出 ARM、帶反模擬器，AVD 上 App 起不來或秒退。實戰脫殼多半得 ARM64 root 真機。AVD 適合練「對自製一代殼樣本 dump」的原理，不適合真商用殼。
3. **對二代抽取殼整包 dump 得殘缺 DEX**：一個時刻只有已執行方法有指令。看到 dump 出的方法大量 nop，別怪工具——這是二代殼特性，要**主動調用（FART）**逼每個方法還原。
4. **硬抄別人的 `libart.so` 符號名**：ART 符號隨 Android 版本變、是 mangled C++ 名。抄死字串多半 `findExportByName` 回 null。用 `Module.enumerateExports` 動態找含 `OpenMemory`/`DefineClass` 的符號。
5. **太早 dump**：`Application.onCreate` 之前真 DEX 還沒解密。等 App 進主畫面、確認邏輯已跑起來再脫，一代殼此刻整包完整。
6. **忘了反 Frida/反調試會先擋你**（Ch 30/31）：加固殼幾乎都檢測 Frida/調試器，你一 attach 就被殺。脫殼常要**先繞反 Frida**（改 frida-server 名、gadget 注入、hook 檢測點）才 attach 得上——脫殼與反調試對抗是綁一起的，這也是練習 D 把兩者合在一起練的原因。
7. **主動調用引發副作用**：FART 主動 invoke 每個方法，有些方法一呼叫就崩/彈網路請求/改狀態。這是主動調用固有風險，FART 有容錯但不完美，脫二代殼可能要多跑幾次補齊。

## 進階：再往深一層

- **從 ART 內部脫殼（Ch 36）**：本章的 Frida hook 是「從外面守 ART 的 API」。更徹底的是**改 ART 本身**——在 `ArtMethod::Invoke`、`ClassLinker::DefineClass`、解釋器入口直接插 dump 邏輯，編一個特製 ROM。這是 FART 原版的做法，純度最高（殼很難防到 ART 自己的程式碼被改），但要刷機、綁 Android 版本。Ch 34/36 深挖 ART 內部結構（`ArtMethod`、`CodeItem` 佈局）後你才有能力改對地方。
- **`InMemoryDexClassLoader` 的不落地殼**：新殼用 `InMemoryDexClassLoader` 從 `ByteBuffer` 載 DEX，DEX 從不寫檔案——擋掉「找檔案」的老招。但擋不住「掃記憶體」與「hook `OpenMemory`」，因為 DEX 終究要在記憶體、要交給 ART。因應方式不變。
- **anti-dump 對抗**：進階殼會（a）方法還原後很快又抹掉 `CodeItem`（縮短明文窗口，逼你精準卡時機）；（b）故意破壞非執行必要的 DEX 欄位（讓你 dump 出結構怪異的 DEX，`map_list` 對不上）；（c）檢測記憶體被讀取/`/proc/self/maps` 被遍歷。對抗這些要更貼近 ART 內部的 hook（越靠近 ART 執行方法的那一刻，明文越確定存在）+ 更強的 DEX 修復（從 `map_list` 重建欄位）。
- **多 DEX 與 dump 拼接**：現代 App 是 multidex（`classes.dex`+`classes2.dex`+…，Ch 2）。脫殼要撈全所有 DEX 並各自修復、正確命名。frida-dexdump 會 dump 出多個 `classesN.dex`，別漏了非主 DEX——關鍵邏輯可能在 `classes3.dex`。
- **脫殼後的驗證**：dump+修復後，用 jadx 打開確認能反編譯出真邏輯、方法不是空的；再用 `baksmali` 反組譯確認 `map_list`/`CodeItem` 完整。把脫出的 DEX 塞回 apktool 目錄重打包能跑，是「脫得對」的最強證明（接練習 D）。

## 動手練習

1. 用本章的 Python 掃描片段，自己造一塊「記憶體」（雜訊 + 兩三個 DEX），寫程式掃 `dex\n035`/`038` magic、讀 `file_size`、把每個 DEX 切出來存成檔案。這是 frida-dexdump 核心動作的**離線版**，讓你徹底懂「掃 magic dump」在做什麼。（純 Python，不需 Android。）
2. 用本章的修復片段，故意造一個 over-dump（DEX 尾巴接垃圾）+ 一個 header 被改壞（`checksum` 清零）的 DEX，寫程式截斷+重算 `file_size`/`signature`/`checksum` 修好它，用 `baksmali`（若有）確認修復後能反組譯。體會「dump 到 ≠ 能用，中間差一步修復」。
3. （需 ARM64 root 真機）拿一個你自己寫的小 App，用某加固廠商的免費試用加殼，`frida-dexdump` 脫脫看。dump 出的 DEX 丟 jadx：方法有指令 → 一代殼脫成功；大量 nop → 二代抽取殼，去讀 FART 原理準備上主動調用。全程若被反 Frida 擋，先繞（Ch 30）。誠實記錄你卡在哪一步。
4. 對照本章的「脫殼三步」與 Ch 28 的「殼在 Application 載入時序」，把「一代殼在哪個時刻整包完整可 dump」「二代殼為什麼單次 dump 殘缺」用自己的話寫一遍。講得清楚，代表脫殼的原理進腦了。

## 本章重點整理

- 脫殼三步：**① 找明文 DEX 在記憶體的時機/位置 → ② dump 出來 → ③ 修復**。dump 的本質是「在對的時刻把記憶體裡的明文 DEX copy 出來」。
- **記憶體掃 magic**：找 `dex\n035`、讀 `file_size`、驗 `endian_tag`/`header_size` 防誤判，整塊切出——frida-dexdump 的核心動作。
- **hook 還原點**（`OpenMemory`/`dexFileParse`/`DefineClass`）：守在殼把 DEX 交給 ART 的那一刻，`OpenMemory` 的 `(base,size)` 直接給你 DEX 位置；但符號名隨 Android 版本變，要動態找。
- **二代抽取殼**單次 dump 必殘缺（只有已執行方法有 `CodeItem`），**FART 的「主動調用」**遍歷每個方法逼殼還原、攔 `CodeItem` 拼回完整 DEX。
- **修復 dump 出的 DEX** 最常被略過卻最卡人：依 `file_size` 截斷 → 重算 SHA-1 `signature` → 重算 adler32 `checksum`，八成的「jadx 打不開」由此解決。
- 現實：多數商用殼只支援 ARM、帶反模擬器與反 Frida，實戰脫殼多需 ARM64 root 真機，且常要先繞反 Frida（Ch 30）才 attach 得上。

## 自我檢核

- [ ] 不看筆記能講出脫殼三步，並說明每步對一代/二代殼各卡在哪
- [ ] 能解釋「掃記憶體找 DEX」具體掃什麼、怎麼用 `file_size` 截斷、為什麼要驗 `endian_tag`
- [ ] 能說出 hook `OpenMemory` 為什麼對一代殼漂亮、對二代殼為什麼仍缺 `CodeItem`
- [ ] 能講清 FART 的「主動調用」在破什麼、為什麼二代抽取殼需要它
- [ ] 拿到一個 dump 出來但 jadx 打不開的 DEX，知道先做哪三步修復
- [ ] 知道實戰脫殼的環境現實（ARM 真機、反 Frida 要先繞），不會在 AVD 上對商用殼死磕

## 延伸閱讀

- **[frida-dexdump（GitHub, hluwenshan/frida-dexdump）](https://github.com/hluwa/frida-dexdump)**
  - **讀哪裡**：README 的原理段（遍歷 ClassLoader + 掃記憶體 magic）、以及 `agent/` 裡實際掃描的 JS 程式碼
  - **和本章的關聯**：技術一、二的一手實作；讀它的掃描邏輯，對照本章 Python 掃 magic 的片段，你會發現核心動作一模一樣
- **[FART（GitHub, hanbinglengyue/FART）](https://github.com/hanbinglengyue/FART)**
  - **讀哪裡**：README 的「主動調用」原理、以及它改了 ART 哪些位置（`ArtMethod::Invoke` 等）
  - **為什麼值得讀**：破二代抽取殼的權威來源；理解「遍歷每個方法主動 invoke 逼還原 CodeItem」的完整機制，直接接練習 E 的 mini FART
- **[看雪 —— 脫殼實戰系列](https://bbs.kanxue.com/)**
  - **讀哪裡**：搜「frida-dexdump 脫殼」「FART 脫殼」「dex 修復」的實戰帖，多附完整腳本與踩雷
  - **前提知識**：讀過本章三步框架，這些帖給你對應真實殼的具體操作與版本坑；注意標日期，殼與 ART 版本演進快
- **[OWASP MASTG — Reverse Engineering / Anti-Tampering](https://mas.owasp.org/MASTG/)**
  - **讀哪裡**：runtime memory dumping、DEX 相關技術
  - **和本章的關聯**：把記憶體 dump 放進標準化方法論；也從防禦視角看殼想擋 dump 的哪些點
- **[AOSP — DexFile / ClassLinker / DEX format](https://source.android.com/docs/core/runtime/dex-format)**
  - **讀哪裡**：`map_list`、`code_item`、DexFile 載入路徑
  - **為什麼值得讀**：hook `OpenMemory`/修復 DEX header/從 `map_list` 重建欄位，這些的權威定義在此；Ch 34/36 深挖 ART 內部脫殼時會再回來

下一章我們處理脫殼路上一直提到的攔路虎——**反調試、反 Frida、反注入**。加固殼幾乎都同時上這些檢測，你一 attach 就被殺。我們會拆解它們怎麼偵測調試器/Frida/注入（`TracerPid`、埠掃描、maps 掃描、inline hook 檢測），以及怎麼一一繞過，把被防護的 App 真正跑到你手裡——這也正是練習 D 要你「脫殼 + 繞反調試把 App 跑起來」的另一半。

→ [Ch 30 反調試、反 Frida、反注入](./30-anti-debug-anti-frida.md)
