# 練習 C — 逆一個把簽名搬進 .so 的 App

> **目標**：把 Part 4（[Ch 19 JNI](./19-jni-mechanism.md)–[Ch 25 native hook](./25-native-hooking.md)）學的全部串成一條真實任務。你面對一個假想 App：它把請求簽名演算法**從 Java 搬進 `libsign.so`**，而且用 **`RegisterNatives` 動態註冊**把 native 函式藏起來（沒有標準 `Java_...` 符號）。任務三步走——**(1) 找到那個 native 函式、(2) 逆出簽名演算法（靜態逆或 Frida hook 出中間值）、(3) 在 host 上用 Python 寫出能重放的簽名**。打完這關，你就走完了「Java 追到 native、native 逆出演算法、自己重放」的完整攻擊鏈，這正是練習 B（純 Java 層）的 native 升級版。

> **環境**：需**真機或 arm64 AVD image**（x86_64 AVD 的 `.so` 是 x86，練不到 ARM64）、Frida 16.x、IDA 或 Ghidra、`adb`。target App 為**假想教學靶**（你可自寫一個把簽名放進 `libsign.so` 的 App 當靶，本練習給你靶的規格）。**簽名演算法與重放邏輯的 Python 在本 repo 沙箱實際跑過**，輸出標「**實際輸出**」；Frida hook / IDA 的操作標「**未實測，理論預期行為**」並附驗證步驟——沙箱無 Android/Frida/IDA。

## 背景與動機

練習 B 你逆的是**純 Java 層**的簽名——jadx 讀得到、Frida hook Java 方法就印得出來。但只要 App 開發者稍微上點心，就會把值錢的簽名邏輯**搬進 native**：Java 層只剩一句 `nativeSign(params)`，真正的演算法在 `libsign.so` 裡。這一搬，只會 Java 層的人就投降了——這正是開發者的目的。

而且他們常再加一層：不用標準的 `Java_com_x_Sign_nativeSign` 命名（那種 jadx/Frida 一搜就到），改用 **`RegisterNatives`** 在執行期把 Java 方法綁到一個**匿名的、strip 掉的** native 函式（IDA 裡叫 `sub_XXXX`）。這樣你連「native 函式在哪」都得先挖出來。

這個練習把這兩層防護都放進去，逼你走完整條鏈：**Java 追到 native 邊界 → 挖出被藏起來的函式位址 → 逆演算法 → 重放**。這是真實 App 逆向最常見的形態，也是練習 B 的直接進階。務必自己打——native 逆向是肌肉記憶，看解答抄沒有用。

## 任務規格

### 靶的行為（你要逆的對象）

假想 App `com.example.mtksign`，登入/交易請求都帶一個 `sign` 參數。它的簽名流程：

```
 Java 層 (jadx 看得到，但只是門面)：
   String sign = SignBridge.nativeSign(sortedParamsJson);   // 一句 native 呼叫

 native 層 libsign.so (真正的演算法藏這)：
   - 沒有 Java_com_example_mtksign_SignBridge_nativeSign 符號
   - JNI_OnLoad 裡呼叫 RegisterNatives，把 nativeSign 綁到 sub_1A40
   - sub_1A40 內部：
       1. 把參數依 key 字母序排序、以 & 串接
       2. 尾端接上 "&key=" + 一個硬編碼在 .so 裡的 SECRET
       3. 對整串算 MD5
       4. 回傳 MD5 的十六進位大寫字串
```

**你不知道上面這些**——這是「答案」，你要靠逆向把它挖出來。你手上只有：APK、一個能跑的裝置、你抓到的一組「請求參數 + 對應的 sign」。

### 三個任務

**任務 1 — 找到 native 函式**
- 從 Java 層的 `nativeSign` 追到 native 邊界，挖出 `RegisterNatives` 綁定的**真實函式位址**（`sub_1A40`）。
- 驗收：你能說出「`nativeSign` 對應 `libsign.so` 的哪個 offset」。

**任務 2 — 逆出演算法**
- 兩條路擇一或並用：**(A) 靜態**——IDA 逆 `sub_1A40`，用 Ch 23 的指紋認出它算 MD5、找出硬編碼的 SECRET；**(B) 動態**——Frida hook 出「MD5 前的那串明文 buffer」，直接看到 `排序參數&key=SECRET` 長什麼樣。
- 驗收：你能寫出「sign = MD5(排序參數 + "&key=" + SECRET) 大寫」這條規則，且拿到 SECRET 的值。

**任務 3 — 寫重放**
- 在 host 用 Python 實作簽名，對「你抓到的那組參數」算出 sign，**跟裝置實際產生的 sign 一致**。
- 驗收：你的 Python 對任意新參數都能算出正確 sign（改一個參數，sign 跟著變、且與裝置一致）。

## 期望輸出範例

任務 3 完成後，你的 Python 重放器對一組參數的輸出（**實際輸出**，本練習參考解答在沙箱真跑）：

```
raw  : action=withdraw&amount=500&ts=1722470400&uid=88991&key=MtK_2o24_S1gnK3y
sign : 6F5A5F6674F27DA82F550767747D04FA
replay matches original: True
```

其中 `raw` 是「餵進 MD5 前的完整字串」（任務 2 動態 hook 就是要看到這行），`sign` 是最終簽名。你重算的 `sign` 要跟裝置對這組參數產生的完全一致。

改一個參數，sign 必須跟著變（**實際輸出**）：

```
tampered amount(500->1) -> sign 4EE0B6BEC678041AC7DA646337C5DBEE  (differs from original: True)
```

## 如果你卡住了

1. **jadx 裡 `nativeSign` 是 `native` 方法、看不到內容**：對，native 方法在 jadx 裡只有宣告沒有 body（`public static native String nativeSign(...)`）。這正是訊號——邏輯在 `.so`，往 native 走。
2. **`.so` 裡搜不到 `Java_..._nativeSign`**：因為它是 `RegisterNatives` 動態綁的，沒有標準命名。別再搜函式名——去 hook `RegisterNatives`（見步驟 3），或在 IDA 看 `JNI_OnLoad` 裡呼叫 `RegisterNatives` 傳的 `JNINativeMethod` 陣列。
3. **hook 不到 native 函式（位址不對）**：`.so` 有 ASLR，靜態 offset 要 `Module.findBaseAddress + add(offset)`（Ch 25）。還有 x86_64 AVD 的 `.so` 是 x86——練 ARM64 要用 arm64 image 或真機（Ch 0/20 的老陷阱）。
4. **IDA 逆 `sub_1A40` 看不懂那堆位移異或**：別逐行讀。用 Ch 23 的指紋——掃 `.rodata` 找 `67452301 efcdab89 98badcfe 10325476`（MD5 init），命中就知道是 MD5，不用讀運算。
5. **知道是 MD5 但 SECRET 找不到**：SECRET 常是 `.rodata` 的一個明文字串（IDA Shift+F12 字串窗掃），或被簡單編碼/在別處解密。**最省事的路是動態**——hook MD5 的輸入 buffer，`&key=` 後面那串就是 SECRET，直接現形。
6. **重放算出來的 sign 跟裝置對不上**：99% 是**餵進 MD5 的字串跟裝置不完全一樣**。逐 byte 比對你 Python 拼的 `raw` 跟 hook 到的 `raw`——差別常在：參數排序規則（是不是字母序？大小寫？）、分隔符（`&` 還是別的）、`key=` 的確切寫法、有沒有 trailing 字元、SECRET 有沒有抄錯一個字。動態 hook 到的 `raw` 是**唯一真相**，照它拼。
7. **hook MD5 卻沒觸發**：確認 App 真的走了 native MD5（可能它靜態連結自己的 MD5 實作，不是呼叫 libc/openssl 的 `MD5_Update`）。這時 hook 不到標準符號，改 hook `sub_1A40` 本身、在它算 MD5 前把那串 buffer 讀出來（用 Ch 25 的 offset hook + 讀 `x0`/`x1`）。

## 實作步驟建議

> 以下 Frida/IDA 操作標「未實測，理論預期行為」，附驗證步驟。演算法/重放 Python 標「實際輸出」。

### Step 1 — 偵察：從 Java 追到 native 邊界

jadx 開 APK，搜 `sign`，找到 `SignBridge.nativeSign`。確認它是 `native` 方法（沒有 body），且某處 `System.loadLibrary("sign")` 載入 `libsign.so`。

**驗證步驟**：`unzip -l app.apk | grep libsign` 確認 `.so` 在（注意 ABI 目錄，arm64 在 `lib/arm64-v8a/`）；`adb pull` 出來準備給 IDA。

### Step 2 — 挖出被藏的 native 函式位址（hook RegisterNatives）

native 函式是 `RegisterNatives` 動態綁的，沒有符號。用 Ch 25 的招——hook `RegisterNatives`，讀它第三參數的 `JNINativeMethod` 陣列，把每個方法的 name 與函式指標印出來（**理論預期行為**）：

```javascript
// hook RegisterNatives，撈出動態註冊的 native 函式真實位址
Interceptor.attach(Module.getExportByName(null, "RegisterNatives"), {
    onEnter(args) {
        const env = args[0], clazz = args[1];
        const methods = args[2];          // JNINativeMethod* 陣列
        const count = args[3].toInt32();
        // JNINativeMethod = { char* name; char* sig; void* fnPtr; } 每個 3*ptr
        for (let i = 0; i < count; i++) {
            const m = methods.add(i * Process.pointerSize * 3);
            const name = m.readPointer().readUtf8String();
            const fnPtr = m.add(Process.pointerSize * 2).readPointer();
            console.log(`[RegisterNatives] ${name} -> ${fnPtr}`);
        }
    }
});
```

**你預期會看到（理論預期行為）**：`[RegisterNatives] nativeSign -> 0x7xxxxxxx`。用 `Module.findBaseAddress("libsign.so")` 減掉這個位址，得到靜態 offset（`0x1A40`），跟 IDA 對上。

**驗證步驟**：`frida -U -f com.example.mtksign -l hook_regnative.js`，spawn 模式確保在 `JNI_OnLoad` 跑前掛好 hook（attach 太晚，`RegisterNatives` 早呼叫完了）。看到 `nativeSign -> ...` 就成功。

### Step 3 — 逆演算法（靜態認指紋 + 動態拿明文）

**路 A（靜態）**：IDA 開 `libsign.so`，跳到 `sub_1A40`。用 Ch 23 指紋掃 `.rodata`：search bytes `01 23 45 67`（`0x67452301` 小端）——命中 MD5 init，確認在算 MD5。再 Shift+F12 掃字串，找可疑的 SECRET（像 `MtK_2o24_S1gnK3y` 這種明顯是 key 的字串）。

**路 B（動態，更快）**：hook 住 MD5 的輸入 buffer，直接看餵進去的完整字串（**理論預期行為**）：

```javascript
// 若 App 用 openssl 的 MD5_Update(ctx, data, len)，hook 它讀 data
Interceptor.attach(Module.getExportByName("libsign.so", "MD5_Update"), {
    onEnter(args) {
        const len = args[2].toInt32();
        console.log("[MD5_Update] " + args[1].readUtf8String(len));
    }
});
// 若 MD5 是靜態連結沒符號：改 hook sub_1A40（base+0x1A40），
// 在它算 MD5 前讀出組好的 buffer 指標（哪個暫存器由 IDA 看呼叫慣例決定）
```

**你預期會看到（理論預期行為）**——這一行直接洩漏整個演算法：

```
[MD5_Update] action=withdraw&amount=500&ts=1722470400&uid=88991&key=MtK_2o24_S1gnK3y
```

看到這行你就全懂了：**參數字母序 `&` 串接，尾接 `&key=SECRET`，SECRET = `MtK_2o24_S1gnK3y`**。動態一刀比靜態讀半天快。

**驗證步驟**：觸發一次登入/交易，看 log 印出這串；比對你在裝置抓到的 `sign` 是不是這串的 MD5 大寫。

### Step 4 — 寫重放器（Python，host 上跑）

把逆出來的規則寫成 Python。這段**在沙箱實際跑過（實際輸出）**：

```python
import hashlib

SECRET = "MtK_2o24_S1gnK3y"          # 任務 2 逆出來的硬編碼 key

def make_sign(params: dict) -> tuple[str, str]:
    body = "&".join(f"{k}={params[k]}" for k in sorted(params))   # 字母序 & 串接
    raw = body + "&key=" + SECRET                                 # 尾接 key
    sign = hashlib.md5(raw.encode()).hexdigest().upper()          # MD5 大寫
    return sign, raw

params = {"uid":"88991","ts":"1722470400","action":"withdraw","amount":"500"}
sign, raw = make_sign(params)
print("raw  :", raw)
print("sign :", sign)
```

**實際輸出**：

```
raw  : action=withdraw&amount=500&ts=1722470400&uid=88991&key=MtK_2o24_S1gnK3y
sign : 6F5A5F6674F27DA82F550767747D04FA
```

**驗證步驟**：拿裝置對「同一組參數」實際產生的 sign（從抓包或 hook Java 層 `nativeSign` 的返回值拿），跟你 Python 算的比。一致 → 任務完成，你已經能**離開 App、自己造任意合法簽名**了。改一個參數再算一次，確認裝置也認你新造的 sign。

## 完整參考解答

先自己打，卡住再看。這裡給兩條完整路（動態優先、靜態備援）與最終重放腳本。

<details>
<summary>展開：完整逆向思路 + Frida 腳本 + 重放器（先自己做完再看）</summary>

### 思路總覽

這題的防護是「兩層藏」：**(1) 邏輯搬進 native、(2) 函式用 RegisterNatives 匿名綁定**。對應破法：

```
Java 追到 native 邊界（jadx 看 native 方法 + loadLibrary）
   └─▶ 挖函式位址（hook RegisterNatives 撈 fnPtr）
          └─▶ 逆演算法
                ├─ 動態（快）：hook MD5 前 buffer，一行洩漏整個演算法
                └─ 靜態（穩）：IDA 認 MD5 指紋 + 字串窗撈 SECRET
                       └─▶ host 重放（Python），比對裝置產生的 sign
```

**核心洞察**：這類「排序參數 + 固定 key + 雜湊」的簽名，**動態 hook 雜湊函式的輸入 buffer 幾乎總是最快的一刀**——因為餵進 MD5 前的那串明文，已經把「參數怎麼排、分隔符是什麼、key 接在哪、key 是多少」全部攤開了，你連運算都不用讀。靜態逆是備援（動態被反調試擋住、或 hook 不到符號時用）。

### 動態路：完整 Frida 腳本（理論預期行為）

```javascript
// solve.js — 一次搞定：撈函式位址 + hook 出 MD5 明文
'use strict';

// (1) hook RegisterNatives 撈 nativeSign 的真實位址
Interceptor.attach(Module.getExportByName(null, "RegisterNatives"), {
    onEnter(args) {
        const methods = args[2], count = args[3].toInt32();
        const psz = Process.pointerSize;
        for (let i = 0; i < count; i++) {
            const m = methods.add(i * psz * 3);
            const name  = m.readPointer().readUtf8String();
            const fnPtr = m.add(psz * 2).readPointer();
            if (name === "nativeSign") {
                const base = Module.findBaseAddress("libsign.so");
                console.log(`[+] nativeSign -> ${fnPtr}  (offset 0x${fnPtr.sub(base).toString(16)})`);
            }
        }
    }
});

// (2) hook MD5_Update 讀餵進去的明文（若靜態連結沒符號，改 hook sub_1A40 讀 buffer 指標）
const md5u = Module.findExportByName("libsign.so", "MD5_Update");
if (md5u) {
    Interceptor.attach(md5u, {
        onEnter(args) {
            const len = args[2].toInt32();
            console.log("[MD5_Update] " + args[1].readUtf8String(len));
        }
    });
}
```

跑 `frida -U -f com.example.mtksign -l solve.js`（spawn 模式，確保 hook 在 `JNI_OnLoad` 前掛好）。觸發一次交易，預期 log（理論預期行為）：

```
[+] nativeSign -> 0x7abc001a40  (offset 0x1a40)
[MD5_Update] action=withdraw&amount=500&ts=1722470400&uid=88991&key=MtK_2o24_S1gnK3y
```

第二行就是答案：**字母序參數 `&` 串接 + `&key=MtK_2o24_S1gnK3y` + MD5 大寫**。

### 靜態路（動態被擋時的備援，理論預期行為）

1. IDA 開 `lib/arm64-v8a/libsign.so`，跳 `sub_1A40`（位址從動態或 `JNI_OnLoad` 的 `RegisterNatives` 參數拿）。
2. 掃 `.rodata` 認演算法（Ch 23）：search bytes `01 23 45 67`（`0x67452301` 小端）命中 → MD5。或掃 T-table `78 a4 6a d7`（`0xd76aa478` 小端）二次確認。
3. Shift+F12 開字串窗，找可疑 key 字串 → 命中 `MtK_2o24_S1gnK3y`，交叉引用（X）確認它被 `sub_1A40` 用來拼接。
4. 讀 `sub_1A40` 的拼接邏輯確認「排序 + `&` + `&key=`」規則（這步要讀運算，比動態費工，但不依賴執行）。

### 重放器（Python，實際輸出）

```python
import hashlib

SECRET = "MtK_2o24_S1gnK3y"

def make_sign(params: dict) -> str:
    body = "&".join(f"{k}={params[k]}" for k in sorted(params))
    raw = body + "&key=" + SECRET
    return hashlib.md5(raw.encode()).hexdigest().upper()

params = {"uid":"88991","ts":"1722470400","action":"withdraw","amount":"500"}
print(make_sign(params))
```

**實際輸出**：

```
6F5A5F6674F27DA82F550767747D04FA
```

把它跟裝置對同組參數產生的 sign 比對，一致即通關。這支重放器現在能對**任意**參數造出裝置接受的簽名——你已經把 App 的簽名能力複製到 host 上了。

### 為什麼這樣就贏了

簽名機制的安全性完全押在「SECRET 保密」與「演算法保密」上（security by obscurity）。一旦你動態 hook 出明文 buffer，兩者同時失守——這就是為什麼把演算法搬 native 只是**提高門檻**、不是**真正的安全**。真正的防護要靠伺服器端驗證 + 綁定裝置/時效，讓你重放也過不了（延伸挑戰 4 就在練這個對抗）。

</details>

## 測試表：逐項驗收

| # | 任務 | 怎麼驗 | 通過標準 |
|---|---|---|---|
| 1 | 追到 native 邊界 | jadx 看 `nativeSign` 是 native 方法、找到 `loadLibrary` | 能說出 `.so` 檔名與 ABI |
| 2 | 挖出函式位址 | hook `RegisterNatives` 印出 `nativeSign -> addr` | 拿到 `sub_XXXX` 的 offset |
| 3 | 認出演算法 | IDA 掃到 MD5 init 指紋 / hook 到 MD5 前 buffer | 確認是「MD5(排序參數+&key=SECRET)」 |
| 4 | 拿到 SECRET | `.rodata` 字串 / hook buffer 的 `&key=` 後段 | 得到 `MtK_2o24_S1gnK3y` |
| 5 | 重放一致 | Python 算 vs 裝置實際 sign 比對 | 完全相同 |
| 6 | 篡改可控 | 改一個參數，Python 與裝置各自重算 | 兩邊都變、且互相一致 |
| 7 | 泛化 | 對一組全新參數重放 | 裝置接受你造的 sign |

## 延伸挑戰

1. **SECRET 不是明文**：把靶改成「SECRET 在 `.so` 裡是 XOR 編碼、`sub_1A40` 執行期才解密」。靜態字串窗掃不到——這時只能動態（hook MD5 前 buffer）拿。體會 Ch 23「動態生成常數躲靜態」的實戰。
2. **演算法換成 HMAC-SHA256**：把靶的 MD5 換成 `HMAC-SHA256(排序參數, SECRET)`。你的指紋要改認 SHA-256 init `6a09e667` + HMAC 的 `ipad/opad`（`0x36`/`0x5c` pad），重放器改用 `hmac.new(secret, body, hashlib.sha256)`。
3. **native 反調試**：靶在 `JNI_OnLoad` 加 `ptrace(TRACEME)` 佔位、檢查 `TracerPid`。你的 Frida 一 attach 就閃退——用 Ch 30 的反反調試（先 hook 掉 `ptrace`/`fork`）繞過再逆。
4. **時間戳有效期**：靶的 `ts` 只在 ±60 秒內有效。你的重放器要用「當下時間」生 `ts` 再簽，寫成一個能真的打伺服器的完整重放腳本（合法邊界：只打你自己的測試伺服器）。
5. **inline hook 替代 Frida**：不用 Frida，改用 Ch 25 的手寫 inline hook（Dobby/And64InlineHook 編進你自己的注入 `.so`），hook `sub_1A40` 印 buffer——體會不靠 Frida 的隱蔽路。

## 自我檢核

- [ ] 能解釋為什麼 jadx 裡 `native` 方法沒有 body，以及這代表要往哪走
- [ ] 能說出 `RegisterNatives` 動態註冊的函式為什麼搜不到 `Java_...` 符號，以及怎麼挖出它的位址
- [ ] 能用 Ch 23 的指紋（不讀運算）認出 `sub_1A40` 在算 MD5
- [ ] 知道「靜態找 SECRET」與「動態 hook MD5 前 buffer」各自的優劣，以及為什麼動態常更快
- [ ] 能寫出 host 端的 Python 重放器，並知道「重放對不上」時第一個要比對的是那串 `raw`
- [ ] 走完了 Java→native→逆演算法→重放的完整鏈，能對新參數造出裝置接受的 sign
- [ ] 知道 x86_64 AVD 練不到 ARM64 native、要換 arm64 image 或真機

打通這關，你已經能對付「把簽名搬進 .so + RegisterNatives 藏函式」這種真實 App 的主流防護。但這只是「藏」——下一 Part 我們面對真正的**對抗**：程式碼被混淆成看不懂（OLLVM 控制流平坦化）、DEX 被加密加殼藏起來、反調試反 Frida 主動打你。先從混淆的全譜開始，搞清楚對手有哪些牌。

→ [Ch 26 混淆技術全譜](./26-obfuscation-landscape.md)
