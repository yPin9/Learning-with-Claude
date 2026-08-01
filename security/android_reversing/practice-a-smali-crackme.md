# 練習 A — 手改 smali 破 crackme

> **目標**：把 [Ch 6](./06-apktool-rebuild.md)（apktool 回編譯）到 [Ch 10](./10-smali-patching.md)（smali patch）學的每一塊拼成一條完整攻擊鏈。你要對一個假想 crackme 做到：解 APK → 定位 `checkPassword()` → 用三種不同手法各繞過一次 → 回編譯重簽 → 裝進 AVD 驗證。三種手法都做過，你就完整走過了「App 層靜態破解」的全流程——這是任何 crackme／簡單 App 破解的第一套動作。

> **環境**：AVD（Android 13 / API 33，x86_64）、apktool 2.9、build-tools 34（zipalign/apksigner）、JDK 11+。本練習的 crackme C/Java 原碼與 smali patch 皆**手寫、語法正確、標明對應**；回編譯裝進 AVD 的實際執行本 repo 沙箱無 Android 工具鏈，標「未實測，理論預期行為」+ 逐步驗證方法。**參考解答藏在最後，先自己試。**

## 背景與動機

Ch 6–10 你各學了一塊：apktool 怎麼一進一出、簽名鏈怎麼接、smali 怎麼讀、四種 patch 手法怎麼用。但真正動手破一個 App，是要把這些**串成一氣**——而串的過程會冒出一堆分開學時不會遇到的問題：patch 對了但回編譯失敗、簽名裝不上、改對了地方卻沒生效、`.locals` 忘了改。

這個練習就是逼你把整條鏈跑通，並且**用三種不同手法破同一個目標**——反轉跳轉、覆蓋返回值、換整個方法體。三種都做，你才會建立「這個場景該用哪招」的判斷，而不是只會一招硬套。打通它，你面對任何「輸入密碼 → 一個 boolean 決定成敗」的 crackme，都有一套可複製的流程。這是肌肉記憶，不是知識——務必自己敲，別看解答抄。

## 任務規格

你會拿到（自己編）一個 crackme APK，行為如下：

- 啟動後有一個輸入框和「驗證」按鈕。
- 你輸入密碼、按驗證，App 呼叫 `checkPassword(String input)`，回傳 `boolean`。
- 回 `true` → 顯示 `Access Granted`（或 log 出 flag）；回 `false` → 顯示 `Wrong Password`。
- `checkPassword` 內部把 input 跟一個硬編碼密碼比對（你**不需要**知道那個密碼是什麼——重點是繞過，不是還原密碼）。

### 你的三關

**第 1 關 — 反轉跳轉**：找到 `login`/`onClick` 裡 `if-eqz`（依 `checkPassword` 結果跳轉）的那條指令，反轉它（`if-eqz`↔`if-nez`）。驗收：輸入**任意錯**密碼，顯示 `Access Granted`。

**第 2 關 — 覆蓋返回值**：在 `checkPassword` 呼叫後、判斷前，插一條 `const/4 v0, 0x1` 把結果強制成 true。驗收：輸入**任何**密碼（含亂打）都顯示 `Access Granted`，且正確密碼也照樣通過（比第 1 關乾淨）。

**第 3 關 — 換整個方法體**：直接把 `checkPassword` 的方法體整段換成 `const/4 v0, 0x1` + `return v0`。驗收：同第 2 關，但這次你根本沒碰呼叫端，改的是被呼叫的方法本身。

三關都要走完整的 `apktool b` → `zipalign` → `apksigner` → `adb install` 鏈並在 AVD 驗證。

## 期望輸出範例

破解成功後，App 畫面顯示（或 logcat 印出）：

```
# 破解前：輸入錯密碼
Wrong Password

# 破解後（任一關）：輸入錯密碼也過
Access Granted
FLAG{smali_patch_works}      ← 若 crackme 在成功分支 log 出 flag
```

回編譯簽名鏈跑通的關鍵訊號（`apksigner verify`）：

```
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
```

`adb install` 回 `Success`，App 打得開、輸入錯密碼卻放行——就成了。

## 如果你卡住了

1. **在 smali 裡找不到 `checkPassword`**：先用 jadx 讀 Java 確認方法名與所在 class，再 `grep -rn "checkPassword" out/smali*/`（**注意 multidex，要 grep 所有 `smali*` 目錄**）。找到 `.smali` 檔再定位方法。
2. **回編譯 `apktool b` 失敗、報 aapt2 資源錯**：這個練習只改 smali、不改資源，解的時候加 `--no-res`（`apktool d --no-res crackme.apk -o out`），繞開整個資源重編譯。純 smali patch 用 `--no-res` 成功率最高（Ch 6）。
3. **裝不上，回 `INSTALL_PARSE_FAILED_NO_CERTIFICATES`**：忘了簽名。`apktool b` 只出未簽名 APK，一定要接 `zipalign`（先）+ `apksigner`（後）。
4. **裝不上，回 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`**：AVD 上已裝原簽名版本，你自簽的簽名不同。先 `adb uninstall <package>` 再裝。
5. **組譯報 `register vN is not valid`**：你用了超過 `.locals` 宣告的暫存器。第 2 關插 `const/4 v0` 若沿用既有 v0 通常不用加；但若你多用暫存器，記得同步加大 `.locals`（Ch 10 第一守則）。
6. **改了但沒生效**：可能改錯 DEX 目錄（multidex，真的方法在 `smali_classes2/`），或改的是呼叫端但邏輯其實在別處。回 jadx 確認資料流：那個 boolean 到底怎麼決定畫面。
7. **`const/4` 塞不進去的數**：本練習只用 `0x0`/`0x1`，都在 `const/4` 範圍（-8~7）內，不會遇到範圍問題。若你自行擴充改數值，才需 `const/16`/`const`。

## 實作步驟建議

### Step 1：編出 crackme 靶（或用現成 CTF crackme）

自己寫一個最小 crackme（參考解答有完整 Java），或找一個開源/CTF 的 password crackme。用 Android Studio 編成 debug APK。**先跑一次確認它正常**：輸對密碼過、輸錯擋——這是偵察，先看清楚敵情。

### Step 2：偵察——jadx 讀懂邏輯

`jadx-gui crackme.apk`，找到 `checkPassword` 和呼叫它的地方（`onClick`/`login`）。看清楚：boolean 結果怎麼流到「顯示 Granted/Wrong」的判斷。記下 class 全名和方法名——待會 smali 要用。

### Step 3：apktool 解出 smali

```bash
apktool d --no-res crackme.apk -o out
grep -rn "checkPassword" out/smali*/       # 定位到 .smali 檔
```

打開那個 `.smali`，對照 Step 2 的 jadx 輸出，把 `checkPassword` 和呼叫端的 smali 看懂。

### Step 4：三關各 patch 一次

每關改一處、各回編譯簽名裝一次（別三關混在一起改）。改完存檔，走 Step 5 的鏈。

### Step 5：回編譯 → 對齊 → 簽 → 裝 → 驗

```bash
apktool b out -o repacked.apk
zipalign -p -f 4 repacked.apk aligned.apk
apksigner sign --ks my.keystore --ks-pass pass:android aligned.apk
adb uninstall com.example.crackme     # 避免簽名衝突
adb install aligned.apk
```

打開 App，輸入**錯**密碼，看它是不是放行。放行就這一關成功。三關各驗一次。

## 完整參考解答

**三關都自己打通再看！** 偷看等於沒練。

<details>
<summary>點開 crackme 靶（Java 原碼）</summary>

一個最小 crackme 的核心（Activity 簡化，只呈現邏輯）：

```java
// com/example/crackme/MainActivity.java
package com.example.crackme;

public class MainActivity extends android.app.Activity {

    // 呼叫端：按鈕點下去跑這段
    public void login(String input) {
        if (checkPassword(input)) {
            android.util.Log.d("CRACKME", "Access Granted\nFLAG{smali_patch_works}");
        } else {
            android.util.Log.d("CRACKME", "Wrong Password");
        }
    }

    // 目標：回 boolean 決定成敗
    public boolean checkPassword(String input) {
        return "s3cr3t_p@ss".equals(input);   // 硬編碼密碼比對
    }
}
```

用 Android Studio 建一個空專案，把上面邏輯接到一個 EditText + Button，編成 debug APK。**你不需要知道 `s3cr3t_p@ss` 是什麼——練習目標是繞過 `checkPassword`，不是還原密碼。**

</details>

<details>
<summary>點開第 1 關參考解（反轉跳轉）</summary>

`login` 反編譯出的 smali（`--no-res` 解出，位於 `out/smali/com/example/crackme/MainActivity.smali`）：

```smali
.method public login(Ljava/lang/String;)V
    .locals 2

    invoke-virtual {p0, p1}, Lcom/example/crackme/MainActivity;->checkPassword(Ljava/lang/String;)Z
    move-result v0                          # v0 = checkPassword 結果 (0/1)

    if-eqz v0, :cond_wrong                  # if (v0 == 0) goto :cond_wrong  ← 錯密碼跳走
    const-string v1, "Access Granted\nFLAG{smali_patch_works}"
    invoke-static {..., v1}, Landroid/util/Log;->d(...)I    # 成功分支
    return-void

    :cond_wrong
    const-string v1, "Wrong Password"
    invoke-static {..., v1}, Landroid/util/Log;->d(...)I    # 失敗分支
    return-void
.end method
```

**patch**：把 `if-eqz` 反轉成 `if-nez`：

```smali
    if-nez v0, :cond_wrong                  # 改一字：if (v0 != 0) goto wrong ← 邏輯反了
```

現在密碼**對**（v0=1）才跳去 wrong，密碼**錯**（v0=0）反而走成功分支。輸入任意錯密碼 → `Access Granted`。

**驗證**：

```bash
adb logcat -s CRACKME
#   D CRACKME: Access Granted
#   D CRACKME: FLAG{smali_patch_works}
```

**副作用**（Ch 10 講過）：正確密碼現在反而顯示 Wrong Password——因為你把對/錯對調了。第 2 關更乾淨。

</details>

<details>
<summary>點開第 2 關參考解（覆蓋返回值）</summary>

同樣改 `login`，但這次不反轉跳轉，而是在 `move-result` 後**插一行**強制覆蓋 v0：

```smali
    invoke-virtual {p0, p1}, Lcom/example/crackme/MainActivity;->checkPassword(Ljava/lang/String;)Z
    move-result v0
    const/4 v0, 0x1                         # ← 插這行：不管 checkPassword 回什麼，v0 強制 = 1

    if-eqz v0, :cond_wrong                  # v0 恆為 1，永不跳，永遠走成功分支
```

`.locals` 原本是 `2`（用了 v0、v1），沒新增暫存器，**不用改**。

**效果**：**任何**輸入（含正確密碼、含亂打）都 `Access Granted`。比第 1 關乾淨——沒有「正確密碼反而失敗」的副作用。這就是 Ch 10 說「繞過驗證用覆蓋比反轉好」的實例。

**驗證**：logcat 同第 1 關，但這次輸對輸錯都過。

</details>

<details>
<summary>點開第 3 關參考解（換整個方法體）</summary>

這次不碰 `login`，改**被呼叫的 `checkPassword` 本身**。原貌：

```smali
.method public checkPassword(Ljava/lang/String;)Z
    .locals 1

    const-string v0, "s3cr3t_p@ss"
    invoke-virtual {v0, p1}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    return v0
.end method
```

**patch**：整個方法體換成「直接回 1」：

```smali
.method public checkPassword(Ljava/lang/String;)Z
    .locals 1

    const/4 v0, 0x1                         # v0 = true
    return v0                               # 直接回 true，內部比對整段繞過
.end method
```

把 `const-string` + `equals` + `move-result` 三行**刪掉**，只留 `const/4 v0, 0x1` + `return v0`。方法簽名 `()Z`（回 boolean）配 `return v0` 型別正確；`.locals 1` 仍夠（只用 v0）。

**效果**：`checkPassword` 對任何輸入都回 true，呼叫端不用改。任何密碼都 `Access Granted`。

**三關對比**：
- 第 1 關改**呼叫端的判斷**（1 個 opcode，有副作用）。
- 第 2 關改**呼叫端的資料**（插 1 行，無副作用）。
- 第 3 關改**被呼叫的方法**（換方法體，最根本——連別處呼叫 `checkPassword` 的地方都一起繞了）。

第 3 關最徹底：如果 App 有多個地方呼叫 `checkPassword`，改方法本身一次全繞；改呼叫端只繞那一處。

</details>

## 測試用例

| 關卡 | 輸入 | 預期 | 驗證點 |
|---|---|---|---|
| 破解前（基準） | 錯密碼 | `Wrong Password` | 確認靶正常，先看清敵情 |
| 破解前（基準） | 對密碼 | `Access Granted` | 確認正常路徑可通 |
| 第 1 關 | 錯密碼 | `Access Granted` | 反轉生效 |
| 第 1 關（副作用） | 對密碼 | `Wrong Password` | 親眼看反轉把對/錯對調 |
| 第 2 關 | 錯密碼 | `Access Granted` | 覆蓋生效 |
| 第 2 關（無副作用） | 對密碼 | `Access Granted` | 對照第 1 關，理解覆蓋更乾淨 |
| 第 3 關 | 任意密碼 | `Access Granted` | 改方法本身，呼叫端沒動也繞 |
| 負向：忘了簽名 | 直接裝 `apktool b` 產物 | `INSTALL_PARSE_FAILED_NO_CERTIFICATES` | 理解簽名是獨立必要步驟 |
| 負向：先簽再對齊 | 簽完再 zipalign | 裝不上/簽名失效 | 理解「先 align 再簽」順序 |

負向用例（故意做錯看它怎麼失敗）和正向一樣重要——它們讓你確認「我以為的流程」和「實際發生的」一致。每個負向都跑一次。

## 延伸挑戰（加分）

- **不改呼叫端也不改 `checkPassword`，改資源**：如果 crackme 把「正確密碼」放在 `res/values/strings.xml`（有些爛設計會這樣），你能不能直接從資源讀出密碼、正大光明輸入正確答案？練習 Ch 9 的資源撈取，體會「有時根本不用 patch」。
- **加一個完整性校驗，再繞過它**：給 crackme 加一段「開機時檢查自己的簽名，非原簽名就閃退」（Ch 32 的前導）。你會發現 patch 完 App 直接崩——因為它偵測到被改。想辦法把那段校驗也 patch 掉（找那個回報「簽名 OK 不 OK」的 boolean 方法，強制回 OK）。這是通往 Ch 32 的橋。
- **把密碼比對搬進 native `.so`**：把 `checkPassword` 的比對邏輯用 JNI 搬進 C（Part 4 的預習）。你會發現 smali 層只剩一個 `native` 方法宣告，patch 不到邏輯——這時 smali patch 就不夠了，得逆 `.so`。體會「為什麼值錢的東西要搬進 native」。
- **改用 Frida 動態繞**：同一個 crackme，不改檔案，用 Frida hook `checkPassword` 讓它回 true（Ch 13 預習）。比較「改 smali（永久、改檔案）」vs「Frida（暫時、不改檔案）」的差別——這正是 Ch 11「為什麼動態贏靜態」要展開的對比。

## 自我檢核

- [ ] 三種手法都能不看解答獨立完成，且能說出各自改的是「判斷／資料／方法本身」哪一層
- [ ] 能解釋第 1 關（反轉）的副作用，以及為什麼第 2 關（覆蓋）沒有這個副作用
- [ ] 能說出第 3 關改 `checkPassword` 本身相對改呼叫端的優勢（多處呼叫一次全繞）
- [ ] 走通了完整的 `apktool b`→`zipalign`→`apksigner`→`install` 鏈，並用負向用例驗證過簽名/順序坑
- [ ] 知道 multidex 時方法可能在 `smali_classes2/`，且改 smali 要顧 `.locals`

三種手法破通，你就完整走過了 App 層靜態破解的第一套動作：讀懂邏輯、定位目標、選對手法、回編譯重簽驗證。但你也會撞到牆——延伸挑戰裡「加完整性校驗就崩」「邏輯搬進 native 就 patch 不到」正是靜態的極限。當 App 有反調試、自校驗、把邏輯藏進執行期才還原的殼，改檔案這條路會越走越窄。下一個 Part 我們換一條腿：動態分析。先講清楚一件事——為什麼面對防護，動態常常贏靜態。

→ [Ch 11 為什麼動態贏靜態](./11-dynamic-beats-static.md)
