# Ch 28 — 加固加殼原理與分代

> **目標**：把「加固（hardening）/ 加殼（packing）」講到你能拿一個陌生 App，判斷它**有沒有殼、是哪一代殼、哪一家的殼**，並在腦中畫出「真 DEX 是怎麼被藏起來、又會在執行期哪個時刻被還原到記憶體」——因為那個「還原點」就是下一章脫殼的下手處。這章不脫殼（Ch 29 才動手），先把**原理與分代**打穿：一代整包 DEX 加密、二代函式抽取（抽取殼/instruction nabbing）、VMP 殼，各自藏了什麼、留了什麼破綻。

> **環境**：殼的**原理**（DEX 加密、Application 替換、抽取還原點）可在 AVD 上理解，但**多數商用加固殼只支援 ARM、且帶反模擬器檢測，x86_64 AVD 往往跑不起加殼 App**——真要跑加殼樣本多半得 ARM 真機。所以本章的廠商特徵、殼行為描述**基於公開資料與逆向社群共識，標「未實測，理論預期行為」或「就公開資料而言」**；DEX 加密/解密與載入的**邏輯**用 Python 3.12 實跑演示，標「**實際輸出**」。絕不拿沒跑過的殼裝成跑過。

## 為什麼需要這個？

Ch 26/27 的混淆讓程式碼難讀，但它**還在**——DEX 是完整的，你靜態全覽得到。加固不一樣：你 unzip 加殼 APK、jadx 打開，看到的 `classes.dex` 裡**根本沒有 App 的真邏輯**，只有一個殼的載入器（loader）。真正的 DEX 被加密藏在 `assets/` 或某個 `.so` 裡，執行期才解密釋放到記憶體。

這是一個認知斷層。很多人第一次碰加殼 App，jadx 打開只看到幾個 `com.stub.StubApp` 之類的類，就以為「這 App 沒什麼程式碼」或「檔案壞了」——其實是被殼騙了，真程式碼在別處、要脫殼才拿得到。

這章要建立的是**脫殼前的地圖**。脫殼（Ch 29）是動手的活，但你得先知道：這是幾代殼？真 DEX 藏哪？它會在 Application 生命週期的哪個點被還原？知道「還原點」在哪，你才知道 Ch 29 要在哪個時刻去記憶體裡撈。不懂原理直接抄脫殼腳本，換一個殼就束手無策。

## 先建立直覺：殼是「開機先跑我、我再放真身」

加固的核心詭計就一句話：**讓殼的程式碼在 App 真正的程式碼之前先跑，由殼負責解密、載入、把控制權交還給真 App**。它是怎麼搶到「先跑」的位置的？答案在 Ch 2 提過的 **`<application android:name>`**。

```
   加殼 APK 的執行流程（偷天換日）

 系統啟動 App
      │
      ▼
 讀 AndroidManifest → application android:name = "com.stub.StubApp"  ← 殼的類！
      │                                （原本的 Application 被藏起來）
      ▼
 StubApp.attachBaseContext() / onCreate()  ← 殼在這裡最先執行
      │
      ├─ 1. 解密 assets/ 或 .so 裡的真 DEX
      ├─ 2. 用自訂 ClassLoader 把真 DEX 載入記憶體
      ├─ 3. 反射還原「原本真正的 Application」並呼叫它的 onCreate
      ▼
 真 App 開始跑（此刻真 DEX 已在記憶體）  ← 脫殼的下手時機（Ch 29）
```

三個關鍵理解：

1. **殼靠 `Application` 搶執行順序**。`Application` 是 App 進程裡最早被實例化的元件之一（比任何 Activity 早），殼把自己設成 `Application`，就拿到了「第一個跑」的位置。Manifest 裡一個你不認識的 Application 類名（`com.stub.StubApp`、`s.h.e.l.l.S` 之類）是加固的**頭號特徵**。
2. **執行期真 DEX 必然在記憶體**。CPU 不能執行加密的 bytecode——殼再怎麼藏，到了要跑真邏輯那一刻，明文 DEX 一定得躺在記憶體裡。**這是所有脫殼技術的物理基礎**（呼應 Ch 1：加固藏 DEX，但執行期必還原）。殼的強弱，本質是「明文 DEX 在記憶體裡存在的時間有多長、範圍有多完整」。
3. **殼與真 App 用 ClassLoader 縫合**。殼載入真 DEX 後，得讓後續的類載入走到真 DEX——這靠替換或串接 ClassLoader（Ch 35 的題材）。這條縫合線也是脫殼的線索之一。

殼的分代，就是沿著「明文 DEX 在記憶體存在多久、多完整」這條軸演進的——一代把整個明文 DEX 攤在記憶體（好脫），二代只在「用到某函式那一刻」才還原該函式（難脫），VMP 乾脆讓明文 DEX 永遠不出現（最難）。

## 殼的分代：沿「明文暴露程度」演進

### 一代殼：整包 DEX 加密（DEX encryption / whole-dex）

最早、最簡單的一代。做法：把整個原始 `classes.dex` **加密**後藏進 APK（常在 `assets/` 或塞進殼的 `.so`），APK 裡放的 `classes.dex` 只是殼的 loader。執行期，殼一次性解密整個真 DEX，用 `DexClassLoader`（或更底層的 `DexFile` / `InMemoryDexClassLoader`）把它整包載入記憶體。

```
   一代殼的記憶體狀態

 assets/encrypted.dat  ──解密──▶  [完整明文 DEX 攤在記憶體]  ← 一整塊、連續、完整
                                        │
                                   DexClassLoader 載入
                                        │
                                   真 App 跑起來
```

用 Python 演示「整包加密 → 執行期解密 → 明文 DEX 出現在記憶體」的最小模型（**實際輸出**，沿用 Ch 2 的 DEX header 知識）：

```python
import struct, zlib, hashlib

def make_dex(body):                                  # 造一個結構正確的明文 DEX
    h = bytearray(0x70); h[0:8] = b"dex\n035\x00"
    data = bytearray(h + body)
    struct.pack_into("<I", data, 32, len(data))      # file_size
    struct.pack_into("<I", data, 36, 0x70)           # header_size
    struct.pack_into("<I", data, 40, 0x12345678)     # endian_tag
    data[12:32] = hashlib.sha1(bytes(data[32:])).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(bytes(data[12:])) & 0xffffffff)
    return bytes(data)

real_dex = make_dex(b"REAL_APP_LOGIC" + b"\x00"*80)  # 這是真 App 的 DEX
key = 0x5A
encrypted = bytes(b ^ key for b in real_dex)          # 一代殼：整包 XOR 加密後塞進 APK

# APK 裡放的 classes.dex 是殼 loader，真 DEX 是這包 encrypted（看不出是 DEX）
print("加密後前8 byte:", encrypted[:8].hex(), " ← 不是 dex\\n035，靜態掃不到 magic")
# 執行期：殼解密 → 明文 DEX 現形於記憶體
mem = bytes(b ^ key for b in encrypted)
print("解密後前8 byte:", mem[:8], " ← dex magic 回來了，此刻可被 dump")
print("解密後與原始 DEX 相同:", mem == real_dex)
```

```
加密後前8 byte: 5a3e56d16f6f5a5a  ← 不是 dex\n035，靜態掃不到 magic
解密後前8 byte: b'dex\n035\x00'  ← dex magic 回來了，此刻可被 dump
解密後與原始 DEX 相同: True
```

**一代殼的破綻**：明文 DEX 一次性、完整地攤在記憶體，且 magic (`dex\n035`) 完好。脫殼者只要在「解密後、載入時」去記憶體掃 `dex\n035` magic，整包 dump 出來就是完整的真 DEX（Ch 29 的 frida-dexdump 主打這個）。所以一代殼**好脫**——明文暴露最徹底。

### 二代殼：函式抽取（DEX extraction / instruction nabbing / 抽取殼）

一代的破綻是「整包明文都在記憶體」，二代針對此改進：**不整包解密，而是把每個方法的 bytecode（`CodeItem`）挖空，只在該方法真正被執行的那一刻才還原它**。這叫**函式抽取殼 / 抽取殼**（也叫 instruction nabbing、DEX 抽取）。

底層機制要接上 Ch 4/34 的 ART 知識：DEX 裡每個方法的 bytecode 存在 **`CodeItem`** 結構，ART 執行一個方法前會取它的 `CodeItem`。二代殼把靜態 DEX 裡所有 `CodeItem` 的內容**抹掉（填 nop 或清零）**，改在執行期 hook ART 的方法解析/執行路徑，於「某方法即將執行」時才把該方法的真 bytecode 填回去。

```
   二代殼（抽取殼）的記憶體狀態

 靜態 DEX：   [類結構完整][方法 A 的 CodeItem = 空][方法 B = 空]...  ← 抽掉了指令
                                   │
             執行期 hook ART：方法 A 要執行了
                                   │
                            ─── 這一刻才把 A 的真 bytecode 填回 ───▶ [A 有了]
                                   │
                            A 執行完，B 要執行了 → 才填 B
```

**二代殼為什麼難脫**：任何一個時刻，記憶體裡**只有已執行過的方法**是明文，沒跑到的方法還是空的。你在某個時間點整包 dump，會得到一個「只有部分方法有指令、其餘是 nop」的殘缺 DEX——這種 dump 出來的 DEX 打開一堆方法是空的，根本沒法用。

要對付它，脫殼者必須**主動觸發每個方法被還原**——這就是 Ch 29 的 **FART（Frida ART / 主動調用脫殼）** 的核心思想：用反射/ArtMethod 主動呼叫 App 的每一個方法，逼殼把每個方法的 `CodeItem` 都還原一遍，同時攔截這些還原後的 bytecode 拼回完整 DEX。「主動調用」三個字就是為了破二代殼設計的。

> **還原點在哪**：二代殼的還原點是 ART 執行方法的入口——常見 hook 點是 `ArtMethod::Invoke`、解釋器入口、或 `dexFileParse`/`DefineClass`（Ch 29 詳列）。理解「還原發生在方法執行前的那一刻」，你才懂為什麼要主動調用去逼它。

### VMP 殼：Dalvik bytecode 虛擬化（最難）

分代的終點。VMP（Virtual Machine Protection）殼把方法的 Dalvik bytecode **翻譯成一套自訂虛擬機的私有指令**，App 裡帶一個 VM 解釋器來跑這套私有指令。這跟 Ch 27 提的 native VMP 是同一個思想，只是作用在 DEX/Dalvik 層。

```
   VMP 殼

 原始方法的 Dalvik bytecode
        │ 殼把它「編譯」成私有 VM 指令
        ▼
 [私有 VM bytecode]  ←── 執行期由殼帶的 VM 解釋器解讀
        │
        ▼
 真正的行為發生，但「明文 Dalvik bytecode 從頭到尾不出現」
```

**VMP 為什麼最難**：一代/二代殼再怎麼藏，明文 Dalvik bytecode 總有出現在記憶體的一刻（脫殼就抓那一刻）。VMP 的關鍵是**明文 Dalvik bytecode 永遠不出現**——記憶體裡只有私有 VM 指令和 VM 解釋器。你 dump 得到的是 VM bytecode，不是 Dalvik，Ch 29 的 magic 掃描 + dump 那套完全失效。

對付 VMP 只能**逆那台 VM**：逆出 VM 解釋器的 dispatch 迴圈、還原每條私有指令對應的 Dalvik 語意（devirtualization），把 VM bytecode 翻譯回 Dalvik。工程量巨大，是加固對抗的天花板。好消息是 VMP 殼有效能代價（VM 解釋比原生慢），廠商通常**只對最關鍵的幾個方法上 VMP**，其餘用二代殼——所以你多半只需 devirtualize 少數幾個核心方法。

## 主流廠商與特徵（就公開資料而言）

以下廠商特徵基於逆向社群長期公開歸納，**未逐一在本 repo 環境實測**，且各家版本演進快、特徵會變，措辭一律降級。認殼主要靠：Manifest 的 Application 類名、`assets/` 裡的特徵檔、`lib/` 裡的特徵 `.so`。

| 廠商 | 常見特徵 `.so` / 檔案（就公開資料） | 分代傾向 |
|---|---|---|
| **梆梆安全（Bangcle）** | 一般而言帶 `libsecexe.so` / `libsecmain.so`、`assets/` 有加密 blob | 早期一代，後期抽取/VMP |
| **愛加密（Ijiami）** | 一般帶 `libexec.so` / `libexecmain.so`、`assets/ijiami.dat` | 一代 → 抽取 |
| **360 加固保** | 常見 `libjiagu.so`（`jiagu` = 加固拼音）、`libprotectClass.so` | 抽取殼為主，含反調試 |
| **騰訊樂固（Legu）** | 一般帶 `libshell.so` / `libshella-x.x.x.so`、`libtup.so` | 抽取 + 部分 VMP |
| **娜迦 / 通付盾 等** | 各有特徵 `.so`，此處不逐一列 | 混合 |

實務認殼流程（Ch 29 會用到）：

```
1. unzip -l app.apk | grep -E "lib/|assets/"   → 找特徵 so / 加密 blob
2. apktool d 只解 Manifest → 看 application android:name（殼的 Stub 類名）
3. 對照上表特徵 → 猜是哪家、大概哪一代
4. 據此選脫殼策略（一代整包 dump / 二代主動調用 / VMP 逆 VM）
```

> **重要現實**：加固廠商的殼**絕大多數只出 ARM 版本、且帶強反模擬器與反調試檢測**。就公開資料而言，很多加殼 App 在 x86_64 AVD 上根本起不來（偵測到模擬器直接退出、或沒有對應架構的殼 `.so`）。**要實戰脫殼，多半得 ARM64 真機（root + Magisk）**。這門課的脫殼章（Ch 29）會誠實標注哪些步驟需要真機，AVD 上能做的（原理、frida-dexdump 對自製一代殼樣本）會分開講。

## 殼在 Application 載入的完整時序（脫殼的地圖）

把「殼怎麼在 Application 載入時偷天換日」拆到生命週期粒度——這張時序決定你 Ch 29 該在哪個點 hook：

```
進程 fork（Zygote）
   │
   ▼
LoadedApk / makeApplication
   │  實例化 Manifest 指定的 Application = 殼的 StubApp
   ▼
StubApp.attachBaseContext(base)   ◀── 殼最早的落腳點，常在此解密+載入真 DEX
   │   ├─ 解密 assets/.so 裡的真 DEX
   │   ├─ new DexClassLoader/InMemoryDexClassLoader 載入真 DEX
   │   └─ 反射拿到「原本真正的 Application 類名」（藏在殼的元資料）
   ▼
StubApp.onCreate()
   │   └─ 反射 new 真 Application、把它替換進 ActivityThread、呼叫其 attach/onCreate
   ▼
真 Application.onCreate()   ◀── 此刻真 DEX 已在記憶體，真 App 邏輯開始
   │
   ▼
LauncherActivity ...（正常 App 流程）
```

三個脫殼相關的關鍵時刻：

- **`attachBaseContext` / `onCreate`**：一代殼在此把整包明文 DEX 載入記憶體——**這之後是整包 dump 的時機**。
- **每個方法首次執行前**：二代殼在此才還原該方法的 `CodeItem`——所以要**主動調用**逼它全還原。
- **ClassLoader 串接完成後**：真 DEX 已掛到 ClassLoader，你能透過遍歷 ClassLoader → `DexFile` → 記憶體位址找到真 DEX 的落點（Ch 29 的 frida-dexdump 正是走這條）。

## 對比與取捨：三代殼一覽

| | 一代（整包加密） | 二代（函式抽取） | VMP（虛擬化） |
|---|---|---|---|
| 藏什麼 | 整個 DEX 加密 | 每個方法的 CodeItem 挖空 | Dalvik → 私有 VM 指令 |
| 明文 DEX 何時在記憶體 | 載入時**整包完整** | **只有已執行的方法**，逐個還原 | **從不出現**（只有 VM 指令） |
| 脫殼下手處 | 掃記憶體 `dex\n035` 整包 dump | 主動調用逼還原每個方法（FART） | 逆 VM 解釋器、devirtualize |
| 脫殼難度 | 低 | 中—高 | 極高 |
| 破綻 | 完整明文一次暴露 | 需觸發每個方法 | 有效能代價，通常只護核心方法 |
| 代表技術（Ch 29） | frida-dexdump / 記憶體 dump | FART / dump_dex / 主動調用 | 少見於本課，屬研究前沿 |

一條主軸貫穿：**脫殼難度 = 明文 Dalvik bytecode 在記憶體暴露得多完整、多久**。一代暴露最徹底最好脫，VMP 讓明文永不出現最難脫。你判斷一個殼難不難，就問這個問題。

## 踩雷集錦

1. **jadx 打開只看到殼 loader 就以為「沒程式碼」**：真 DEX 被加密藏起來了，你看到的 `com.stub.StubApp` 是殼。這是加固**特徵**不是檔案損壞——去 `assets/`、`lib/` 找加密 blob 和特徵 `.so`，確認是哪家殼。
2. **對二代殼整包 dump 得到殘缺 DEX**：一個時刻只有已執行的方法是明文，dump 出來一堆方法是 nop。二代殼要**主動調用**逼每個方法還原（Ch 29 FART），不能靠單次 dump。
3. **在 x86_64 AVD 上死磕加殼 App**：多數商用殼只出 ARM、帶反模擬器檢測，AVD 上根本起不來或秒退。要脫真實加殼 App，多半得 ARM64 真機。別在 AVD 上耗，先確認架構與反模擬器。
4. **靠 `.so` 名字硬套廠商**：特徵 `.so` 名會隨版本變、也可能被改名。`libjiagu.so` 大概率 360，但別把它當鐵證——結合 Manifest Application 類名、`assets/` blob 綜合判斷，而且措辭上留餘地。
5. **以為脫殼 = 一鍵工具**：frida-dexdump 對一代殼很順，但二代/VMP 需要理解還原點、主動調用、甚至逆 VM。工具是原理的載體，換個殼工具就可能失效——本章的分代與還原點知識才是不變的。
6. **忘了殼還疊了反調試/反 Frida（Ch 30）**：加固殼幾乎都同時上反調試、反注入、反模擬器。你 Frida 一 attach 就被殼檢測到、App 自殺。脫殼常要**先繞反調試/反 Frida**（Ch 30/31）才 attach 得上——脫殼與反調試對抗是綁一起的。

## 進階：再往深一層

- **ClassLoader 熱補與插件化的交界**：殼載入真 DEX 用的 `DexClassLoader`/`InMemoryDexClassLoader`/ClassLoader 串接技術，跟「插件化框架」「熱修復」用的是同一套機制（Ch 35 深挖）。理解 ClassLoader 雙親委派與 `pathList`/`dexElements`，你既能脫殼也能懂熱補——這是同一個底層的攻防兩面。
- **`InMemoryDexClassLoader` 讓殼不落地**：新一點的殼用 `InMemoryDexClassLoader`（Android 8+）直接從記憶體 `ByteBuffer` 載入 DEX，真 DEX 從頭到尾不寫檔案系統。這擋掉了「dump 檔案」的老招，但擋不住「掃記憶體」——因為 DEX 終究要在記憶體裡（Ch 29 因應）。
- **殼的自我完整性與反 dump**：進階殼會監控自己的 `CodeItem` 是否被 dump（例如在方法還原後很快又抹掉、或檢測記憶體被讀取）、hook 掉常見的 dump API、對 dump 出的 DEX 做 anti-analysis（故意破壞非執行必要的欄位，讓 dump 出來的 DEX 結構怪異需修復——Ch 29 的「修復 dump 出的 dex」正因此存在）。
- **ART 版本綁定**：二代抽取殼要 hook ART 的方法解析路徑，而 ART 內部結構（`ArtMethod`、`CodeItem` 佈局、解釋器入口）**每個 Android 版本都可能變**。一個脫殼工具往往只支援特定 Android 版本區間——這也是 FART/脫殼工具要跟著 Android 大版本更新的原因（Ch 34 講 ART 內部時會看到這些結構）。
- **VMP 的 devirtualization 前沿**：對 DEX 層 VMP 的自動去虛擬化是研究熱點，思路類似 Ch 27 native VMP——逆 dispatch 迴圈、建私有指令到 Dalvik 的映射表、符號執行輔助。學界有半自動工具，但通用性有限，實務多半半手工。

## 動手練習

1. 用本章的 Python 片段，把一段「真 DEX」整包 XOR 加密塞進一個 zip 的 `assets/`，再寫個「loader」程式讀出、解密、還原出 `dex\n035` magic。這是**一代殼的最小模型**，讓你親手體會「靜態看是密文、執行期解密後 DEX 現形」。（不需 Android，純 Python/檔案。）
2. 找一個公開的、來源正當的加殼樣本（或用某加固廠商的免費試用加固你自己寫的小 App——需 ARM 真機測），`unzip -l` 看 `lib/`、`assets/`，`apktool d` 只解 Manifest 看 Application 類名。對照本章的廠商特徵表，猜它是哪家、哪一代。先不脫，只練**認殼**。
3. 對著本章的「殼在 Application 載入的完整時序」圖，不看筆記自己重畫一遍，並在每個時刻標上「這裡明文 DEX 存在嗎？一代/二代分別是什麼狀態？」。畫得出來，代表你懂了脫殼的還原點在哪——Ch 29 就是在這些點動手。

## 本章重點整理

- 加固 ≠ 混淆：加固把**真 DEX 藏起來**（靜態看到的是殼 loader），執行期才解密還原到記憶體。
- 殼靠 **`<application android:name>`** 搶「第一個跑」的位置，在 `attachBaseContext`/`onCreate` 解密真 DEX、用 ClassLoader 載入、反射還原真 Application——一個陌生的 Application 類名是加固頭號特徵。
- **分代沿「明文 Dalvik bytecode 在記憶體暴露多完整/多久」演進**：一代整包加密（完整暴露，好脫）→ 二代函式抽取（只有已執行方法明文，要主動調用逼還原）→ VMP（明文永不出現，要逆 VM，最難）。
- 廠商（梆梆/愛加密/360 libjiagu/騰訊樂固）靠特徵 `.so` + Manifest + `assets/` blob 綜合判斷，措辭留餘地；**多數殼只支援 ARM、帶反模擬器，實戰脫殼多半需 ARM64 真機**。
- 執行期真 DEX 必在記憶體（CPU 才能跑）——這是所有脫殼技術的物理基礎，也是 Ch 29 的下手點。

## 自我檢核

- [ ] 拿到一個 App，能判斷有沒有殼，並說出三個判斷依據（Application 類名、特徵 `.so`、`assets/` blob）
- [ ] 能解釋殼為什麼用 `Application` 搶執行順序，以及它在生命週期哪個點解密真 DEX
- [ ] 能講清一代 / 二代 / VMP 殼各自「明文 Dalvik bytecode 在記憶體的狀態」，以及為什麼難度遞增
- [ ] 能說出為什麼對二代殼整包 dump 會得到殘缺 DEX，以及為什麼要「主動調用」
- [ ] 知道為什麼多數加殼 App 在 x86_64 AVD 跑不起來，實戰脫殼通常需要什麼環境
- [ ] 能說出「執行期真 DEX 必在記憶體」這個物理事實，以及它為什麼是脫殼的基礎

## 延伸閱讀

- **[看雪論壇 —— Android 加固與脫殼專區](https://bbs.kanxue.com/)**
  - **讀哪裡**：搜「加固 分代」「抽取殼 原理」「梆梆/愛加密/360/樂固 脫殼」的精華帖
  - **為什麼值得讀**：中文逆向社群對各家殼的分代、特徵、還原點歸納最全最新，本章的廠商特徵多源於此；讀時注意標注日期，殼演進快
- **[FART（Frida ART / 加固脫殼）作者 hanbinglengyue 的 repo 與文章](https://github.com/hanbinglengyue/FART)**
  - **讀哪裡**：README 對「抽取殼」與「主動調用」原理的說明
  - **和本章的關聯**：二代殼「函式抽取」與「主動調用還原」的一手材料，直接接 Ch 29 的脫殼實作；先讀原理再看 Ch 29 動手
- **[OWASP MASTG — Android Anti-Tampering / Packing 偵測](https://mas.owasp.org/MASTG/)**
  - **讀哪裡**：偵測 App 是否加殼、runtime integrity 相關技術
  - **和本章的關聯**：把「認殼」放進標準化測試流程；也從防禦者視角看殼想擋什麼（呼應 Ch 41）
- **[AOSP — ART 與 DEX/CodeItem 結構文件](https://source.android.com/docs/core/runtime)**
  - **讀哪裡**：DEX `code_item`、ClassLoader、`InMemoryDexClassLoader` 相關
  - **為什麼值得讀**：二代抽取殼在 `CodeItem` 層動手、殼用 ClassLoader 載入真 DEX，這些的權威定義在此；Ch 34 深挖 ART 時會再回來
- **[GuardSquare / 各加固廠商官方技術頁](https://www.guardsquare.com/)**
  - **讀哪裡**：DexGuard 的 class encryption、runtime protection 說明
  - **和本章的關聯**：商用加固能力的一手描述，理解「加固」與「混淆」（Ch 26）如何在同一產品裡疊加

下一章我們終於動手——**脫殼**。有了本章的分代地圖和還原點知識，我們會學：怎麼在記憶體裡掃 `dex\n035` magic 整包 dump（打一代殼）、frida-dexdump 怎麼遍歷 ClassLoader 撈 DEX、FART 怎麼用主動調用破二代抽取殼、hook `dexFileParse`/`OpenMemory`/`DefineClass` 攔還原點、以及最後怎麼把 dump 出的殘缺 DEX 修回可用。

→ [Ch 29 脫殼技術](./29-unpacking-techniques.md)
