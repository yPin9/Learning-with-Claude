# Ch 10 — Smali patch 實戰：繞校驗與改邏輯

> **目標**：把 Ch 6 的「改一個 boolean」升級成真正的 smali patch 實戰。你會學會四類最常用的改法——**反轉條件跳轉繞過驗證**、**改 boolean 返回值破開關**、**插 log 追執行期資料**、**破 VIP／試用限制**——每一類都給 before→after 的 smali 對照，並把「改完為什麼會壞（暫存器、`.locals`、型別）」的地雷全排掉。這是 Part 2 的實戰高潮。

> **環境**：本章的 smali 片段**手寫但語法正確**，每段都標明對應的 Java（讓你對得起來）。回編譯裝進 AVD 的部分需 apktool/apksigner（Ch 6 的鏈），標「未實測，理論預期行為」+ 驗證步驟。smali 語法遵循 baksmali/apktool 2.9 的輸出格式。假設你已讀過 Ch 5（smali 語法）與 Ch 6（回編譯流程）。

## 為什麼需要這個？

因為到最後，改邏輯就是改 smali。動態 hook（Frida，Part 3）強在「不改檔案、執行期改行為」，但它需要每次都跑 Frida、target 每次都得能被 attach。而 smali patch 是**永久的**——改完重打包，這個 App 從此就是你要的行為，不需要 Frida、不需要 root、給誰裝都一樣。破解一個離線 crackme、做一個「預設解鎖」的分析版、繞過一個純本地的完整性檢查——這些場景 smali patch 比 Frida 直接。

而且**讀懂怎麼 patch，反過來就讀懂了怎麼防**（Ch 41 的防禦視角）。你知道攻擊者會反轉哪個 `if`、改哪個返回值，你才知道防禦該把校驗放哪、怎麼讓單點 patch 失效。這章是攻防同一枚硬幣的攻擊面。

## 先建立直覺：patch 的三種「切法」

改 smali 邏輯，本質上只有三種切入點：

```
   一個方法的執行流：
        參數進來
          │
     ┌────▼────┐
     │ 做一堆事 │
     └────┬────┘
          │
     ┌────▼─────────┐   ① 改「判斷」：反轉 if-eqz / 改比較
     │ if (校驗通過) │◀──   讓走錯的分支變走對的分支
     └──┬────────┬──┘
        │通過     │失敗
        ▼         ▼
     真邏輯    擋你/報錯
        │
     ┌──▼──────┐        ② 改「返回值」：讓方法直接回你要的值
     │ return x │◀────      const + return，繞過整個方法內部
     └─────────┘

     ③ 插「觀測」：在任意點 invoke Log.d 印出暫存器
        不改邏輯，只是看執行期的值（配合定位）
```

三種切法的取捨：
- **改判斷**（反轉跳轉）：外科手術式，只動一條指令，最小侵入。適合「校驗結果對了就放行」的場景。
- **改返回值**：釜底抽薪，讓整個方法回你要的值，不管它內部算什麼。適合「一個方法回 true/false 決定命運」的場景。
- **插 log**：不改邏輯，先看清楚執行期發生什麼，再決定怎麼改。是 patch 前的偵察。

實戰常常三種混用：先插 log 看清楚 → 決定改判斷還是改返回值 → 動刀。

## 底層地雷：改 smali 前必須懂的三件事

在給範例前，先把「改完為什麼會壞」的三大來源講清楚，否則你會一直卡在組譯錯誤。

**① `.locals` / `.registers` 必須夠用**

方法開頭的 `.locals N`（或 `.registers M`）宣告用幾個暫存器。你的 patch 若用到新暫存器，超過宣告數就報 `register vN is not valid`。

```smali
.method public check()Z
    .locals 1          # ← 只宣告 1 個區域暫存器 v0
    ...
.end method
```

如果你的 patch 要用 `v1`，得先把 `.locals 1` 改成 `.locals 2`。**改 smali 第一守則：加了暫存器就同步加 `.locals`**。

**② 型別描述符要對**

smali 的型別是單字母/描述符：`Z`=boolean、`I`=int、`V`=void、`Ljava/lang/String;`=String 物件。返回指令要配對型別：`return v0`（回物件/int）、`return-void`（回 void）、`return-wide`（回 long/double）。型別配錯組譯不過或 runtime 崩。

**③ `const/4` 的範圍**

`const/4 vX, lit` 只能塞 **-8 到 7** 的 4-bit 值（所以 `0x0`、`0x1` 可以）。要塞更大的數用 `const/16`（16-bit）或 `const`（32-bit）。用 `const/4 v0, 0x100` 會組譯失敗——超範圍。

記住這三點，下面的範例才不會一改就爛。

## 範例 1：反轉條件跳轉——繞過密碼驗證

最經典的 patch。假設有個登入檢查，Java 邏輯：

```java
public void login(String pwd) {
    if (checkPassword(pwd)) {
        grantAccess();          // 密碼對 → 放行
    } else {
        showError();            // 密碼錯 → 擋
    }
}
```

反編譯出的 smali（對應上面）：

```smali
.method public login(Ljava/lang/String;)V
    .locals 1

    invoke-virtual {p0, p1}, Lcom/example/Login;->checkPassword(Ljava/lang/String;)Z
    move-result v0                  # v0 = checkPassword 的返回值 (0/1)

    if-eqz v0, :cond_fail           # if (v0 == 0) goto :cond_fail  ← 密碼錯就跳走
    invoke-virtual {p0}, Lcom/example/Login;->grantAccess()V   # 放行
    return-void

    :cond_fail
    invoke-virtual {p0}, Lcom/example/Login;->showError()V     # 擋
    return-void
.end method
```

`if-eqz v0, :cond_fail` 讀作「if v0 == zero（false）goto cond_fail」。密碼錯（`v0=0`）就跳到 `:cond_fail` 擋你。

**patch 法 A：反轉跳轉條件**（`if-eqz` → `if-nez`）

```smali
    if-nez v0, :cond_fail           # 改成：if (v0 != 0) goto fail ← 邏輯反了
```

現在變成「密碼**對**（`v0=1`）才跳去 `:cond_fail`（擋），密碼錯反而放行」。你輸入**錯**密碼就能進——繞過了驗證。`if-eqz`（等於零跳）↔ `if-nez`（不等零跳）是一對，反轉一個字就翻轉整個判斷。

**patch 法 B：直接讓校驗結果恆為真**（更穩）

反轉跳轉有個副作用：正確密碼反而進不去。更乾淨的做法是**直接把 `checkPassword` 的結果覆蓋成 1**：

```smali
    invoke-virtual {p0, p1}, Lcom/example/Login;->checkPassword(Ljava/lang/String;)Z
    move-result v0
    const/4 v0, 0x1                 # ← 插這行：不管 checkPassword 回什麼，v0 強制 = 1

    if-eqz v0, :cond_fail           # v0 恆為 1，永遠不跳，永遠放行
```

這樣**任何密碼**（含正確的）都放行——比反轉跳轉更符合「繞過」的意圖。注意 `.locals` 已是 1（用 v0），沒新增暫存器，不用改。

> **反轉 vs 覆蓋，怎麼選？** 反轉跳轉改動最小（一個 opcode），但會把「對」和「錯」對調——正常輸入反而失敗。覆蓋返回值（插 `const/4 v0, 0x1`）讓所有輸入都通過，更符合「繞過驗證」的目標。實戰多半用覆蓋。反轉適合「我要的就是走另一條分支」的特定場景。

## 範例 2：改 boolean 返回值——破 VIP 開關

假設 VIP 判斷集中在一個方法：

```java
public boolean isVip() {
    return this.user.getLevel() >= 3 && checkLicense();   // 一堆條件
}
```

不管它內部多複雜，你要的就是它**永遠回 true**。smali 原貌：

```smali
.method public isVip()Z
    .locals 2

    iget-object v0, p0, Lcom/example/App;->user:Lcom/example/User;
    invoke-virtual {v0}, Lcom/example/User;->getLevel()I
    move-result v0
    const/4 v1, 0x3
    if-lt v0, v1, :cond_no          # level < 3 → 不是 VIP
    invoke-virtual {p0}, Lcom/example/App;->checkLicense()Z
    move-result v0
    if-eqz v0, :cond_no             # license 無效 → 不是 VIP
    const/4 v0, 0x1
    return v0
    :cond_no
    const/4 v0, 0x0
    return v0
.end method
```

**patch：把整個方法體換成「直接回 1」**

```smali
.method public isVip()Z
    .locals 1

    const/4 v0, 0x1                 # v0 = true
    return v0                       # 直接回 true，方法內部那堆判斷全繞過
.end method
```

把原本一大串邏輯**整段刪掉**，只留 `const/4 v0, 0x1` + `return v0`。方法簽名 `()Z`（回 boolean）配 `return v0`（回 int/boolean）型別正確。`.locals` 從 `2` 改成 `1`（現在只用 v0）——**縮小是安全的，放大才要小心**。這是最省事的破法：不管內部怎麼算，出口強制 true。

> **回 false 就對調**：如果你要破的是「反作弊檢查」`isCheating()`，要它永遠回 **false**，就 `const/4 v0, 0x0` + `return v0`。Ch 32 的完整性校驗、Ch 30/31 的反調試/root 檢測，很多都是「一個 boolean 方法回報有沒有被動手腳」，patch 手法就是這招——強制回「沒問題」的那個值。

## 範例 3：插 log 追執行期的值

patch 之前，你常需要先看清楚「執行期這個變數到底是什麼」。在 smali 任意點插一句 `Log.d`：

假設你想知道 `checkPassword` 收到的密碼參數（`p1`，型別 String）：

```smali
.method public login(Ljava/lang/String;)V
    .locals 2                       # ← 從 1 加到 2（要用 v1 裝 tag）

    const-string v1, "REPatch"      # v1 = log tag
    invoke-static {v1, p1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I
    # ↑ Log.d("REPatch", p1) —— 把傳進來的密碼印到 logcat

    invoke-virtual {p0, p1}, Lcom/example/Login;->checkPassword(Ljava/lang/String;)Z
    move-result v0
    ...
```

要點：
- `Log.d(String tag, String msg)` 是 static 方法，用 `invoke-static`，簽名 `(Ljava/lang/String;Ljava/lang/String;)I`。
- 我多用了一個暫存器 `v1` 裝 tag，所以 `.locals` 從 `1` 改成 **`2`**（範例 1 的原方法是 `.locals 1`）。**這是插 log 最常忘的地雷**——加了暫存器沒加 `.locals`，組譯直接報錯。
- 印非 String 的值（如 int）要先 `Integer.toString` 或用 `Log.d` 的其他 overload，不能直接把 int 塞進 String 參數位置（型別不符）。

回編譯裝上後：

```bash
adb logcat -s REPatch          # 只看我們的 tag
#   D REPatch: hunter2          ← 執行期真的把密碼印出來了
```

> **插 log 是 patch 的偵察**：改邏輯前先插 log 確認「我以為的那個變數真的是我想的值嗎」。很多 patch 失敗是因為改錯了地方——log 讓你先確認執行流真的走到這、值真的是這個，再動刀。這跟 Ch 1 的「靜動印證」同精神，只是這裡用的是 smali 插樁而非 Frida。

## 範例 4：改比較邏輯——把試用天數限制拉大

假設試用檢查：

```java
if (daysUsed > 7) { lockApp(); }    // 用超過 7 天就鎖
```

smali：

```smali
    const/16 v1, 0x7                # v1 = 7  （注意用 const/16，7 雖小但原碼常這樣出）
    if-le v0, v1, :cond_ok          # if (daysUsed <= 7) goto ok
    invoke-virtual {p0}, Lcom/example/App;->lockApp()V   # 超過就鎖
    :cond_ok
```

**patch：把 7 改成一個很大的數**

```smali
    const v1, 0x7fffffff            # v1 = 2147483647 (int 最大值) ← 用 const 塞 32-bit
    if-le v0, v1, :cond_ok          # daysUsed 永遠 <= MAX_INT，永遠不鎖
```

把常數 `7` 換成 `0x7fffffff`（int 最大值），`daysUsed` 不可能超過它，鎖定分支永遠不走。注意：`0x7` 可以用 `const/4`，但 `0x7fffffff` 超出 4-bit 和 16-bit 範圍，**必須用 `const`（32-bit）**——這是範例開頭第三條地雷的實例。

## 對比與取捨：四種 patch 手法

| 手法 | 改動大小 | 適用 | 風險/注意 |
|---|---|---|---|
| 反轉跳轉（`if-eqz`↔`if-nez`） | 最小（1 opcode） | 要走另一條分支 | 會對調對/錯，正常輸入可能反而失敗 |
| 覆蓋結果（插 `const/4 v0,0x1`） | 小（1 行） | 讓校驗恆真/恆假 | 注意 `.locals` 夠不夠 |
| 換整個方法體（`const`+`return`） | 中 | 方法回值決定命運 | 型別要配、`.locals` 可縮小 |
| 改常數（`7`→`MAX_INT`） | 最小 | 數值限制（天數/次數） | 常數大小決定用 const/4 還 16 還 32 |

**patch vs Frida 動態**：

| 維度 | smali patch | Frida hook |
|---|---|---|
| 持久性 | 永久（改進 APK） | 每次跑 Frida |
| 需不需要 root/attach | 不需要 | 需要（frida-server） |
| 對抗完整性校驗 | 會被 App 的自校驗抓到（檔案變了） | 不改檔案，較難被檔案校驗抓 |
| 適合 | 離線 crackme、發布分析版 | 反調試兇、有自校驗、快速試 |

## 踩雷集錦

1. **加了暫存器忘了改 `.locals`**：插 log、多用一個暫存器，`.locals` 沒同步加大 → `register vN is not valid`。**改 smali 第一守則**：新增暫存器必加 `.locals`。
2. **`const/4` 塞超範圍的數**：`const/4` 只吃 -8~7。塞 `0x100` 組譯失敗。大數用 `const/16` 或 `const`。範例 4 的 `MAX_INT` 就必須用 `const`。
3. **反轉跳轉沒想清楚副作用**：`if-eqz`→`if-nez` 會把對/錯對調，正常輸入反而走進失敗分支。要「所有輸入都通過」用覆蓋返回值，別用反轉。
4. **返回型別配錯**：`()Z` 方法用 `return-void` 或 `()V` 方法用 `return v0` → 型別不符。boolean/int 用 `return vX`、void 用 `return-void`、long/double 用 `return-wide`。
5. **改錯 DEX 目錄的 smali**：multidex App 方法可能在 `smali_classes2/`。改了 `smali/` 裡的同名占位、真正跑的在別的 DEX → 白改。全目錄 grep 定位。
6. **忘了完整性校驗**：改了 smali 重打包，檔案 hash 變了。有自校驗的 App（Ch 32）會偵測到而閃退。patch 邏輯成功≠App 能跑，可能還要 patch 掉校驗本身，或改用 Frida。

## 進階：再往深一層

- **NOP 掉一段而非改邏輯**：有時你想「移除」某段（如一個上報、一個檢查）而非改它。可以把那幾條指令換成 `nop`（`0x00`，什麼都不做），或把 `invoke-xxx` 那行連同 `move-result` 一起刪。但要小心後續指令有沒有依賴被刪指令的暫存器結果——刪了會用到未初始化的暫存器。
- **改 `<clinit>`／靜態初始化**：feature flag 有時在 static 區塊 `sput` 進一個 static 欄位。要改它得動 `<clinit>`（類的靜態建構子）裡的 `const` + `sput`。這比改一般方法多一層——欄位在載入時就定值。
- **patch 完整性校驗常是「找到那個 boolean 方法」**：不管 App 怎麼校驗（比 hash、驗簽名、檢查 debuggable），最終幾乎都收斂到「一個方法回報 OK 不 OK」。找到那個出口方法，用範例 2 的手法強制回「OK」，往往一刀斃命。Ch 32 會展開多重校驗的情況（不只一個出口）。
- **smali 的 `:try_start`/`:catch`**：patch 在 try 區塊內插指令要小心 try/catch 的範圍標記。插在 `:try_start_0` 和 `:try_end_0` 之間的指令若丟出你沒預期的例外，會被那個 catch 吞掉，行為變得詭異。動 try 區塊前先看清楚 catch 抓什麼。

## 動手練習

1. 寫一個最小 App：`if (checkPassword(input)) grantAccess() else showError()`，`checkPassword` 硬編碼比對一個密碼。apktool 解出 smali，用**覆蓋返回值**法讓任何密碼都放行，回編譯裝上驗證。這是練習 A 的暖身。
2. 同一個 App，改用**反轉跳轉**法（`if-eqz`→`if-nez`），觀察「正確密碼反而進不去、錯密碼能進」的副作用——親手感受兩種手法的差別。
3. 在 `checkPassword` 開頭插 `Log.d` 印出傳入的密碼，回編譯裝上 `adb logcat -s REPatch`，看執行期把你輸入的密碼印出來。故意不改 `.locals`，看它怎麼組譯報錯，再改對。
4. 找一個有數值限制的邏輯（次數/天數），把常數改成 `MAX_INT`，注意 `const/4` vs `const/16` vs `const` 的選擇——塞一個超過 `const/4` 範圍的數，看組譯錯誤，理解常數大小和指令的關係。

## 本章重點整理

- smali patch 三種切法：**改判斷（反轉跳轉）／改返回值（`const`+`return`）／插 log（偵察）**，實戰常混用。
- 三大地雷：**加暫存器必加 `.locals`**、**`const/4` 只吃 -8~7（大數用 const/16 或 const）**、**返回型別要配**。
- 「繞過驗證」多半用**覆蓋返回值**（讓校驗恆真/恆假）比反轉跳轉更乾淨；反調試/root/完整性檢測常收斂到「一個 boolean 方法」，強制它回 OK。
- patch 是永久的、不需 root；但會改變檔案 hash，**有自校驗的 App 會抓到**，可能還要 patch 校驗本身或改用 Frida。

## 自我檢核

- [ ] 能寫出把 `if-eqz v0, :label` 反轉的 patch，並說出它的副作用
- [ ] 能把一個回傳複雜條件的 boolean 方法，patch 成永遠回 true（含正確的 `.locals` 與型別）
- [ ] 能在 smali 任意點插一句 `Log.d` 印出一個 String 暫存器，並知道要同步改 `.locals`
- [ ] 知道 `const/4`/`const/16`/`const` 各自的範圍，塞 `MAX_INT` 該用哪個
- [ ] 能說出 smali patch 相對 Frida 的優劣，以及為什麼有自校驗時 patch 可能不夠

## 延伸閱讀

### Smali / Dalvik 指令

- **[Dalvik bytecode 指令集](https://source.android.com/docs/core/runtime/dalvik-bytecode)** — AOSP
  - **讀哪裡**：`if-*`、`const-*`、`return-*`、`invoke-*` 那幾組；每個 opcode 的操作數格式與範圍
  - **和本章的關聯**：本章的 `const/4` 範圍、`if-eqz`/`if-nez`、`return` 型別配對，這裡是權威定義
- **[smali/baksmali Wiki](https://github.com/google/smali/wiki)** — google/smali
  - **這篇說什麼**：smali 語法、`.locals`/`.registers` 差別、型別描述符
  - **讀哪裡**：registers 與 types 那節
  - **注意**：`.locals` vs `.registers` 的計數差別（含不含參數暫存器）是新手常錯的，這裡講清楚

### 實戰方法論

- **[OWASP MASTG — Patching / Code Modification](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0037/)** — OWASP
  - **這篇說什麼**：標準化的 smali patch 流程與常見改動類型
  - **讀哪裡**：patch bytecode 與繞過檢查那段
  - **為什麼值得讀**：把本章的手法放進標準測試脈絡，並連到完整性校驗對抗（Ch 32 的前導）
- **[HackTricks — Smali changes / bypass](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)** — HackTricks
  - **讀哪裡**：smali patching 與 bypass root/debug detection 那幾段
  - **前提知識**：讀過本章手法，這頁給你更多現成的 patch 樣板可對照

理論講完了，該真的動手破一個。下一個檔案是練習 A：給你一個假想的 crackme（輸入密碼 → `checkPassword()` 回 boolean），你要用這章學的手法把它破掉，從解 APK 到 patch smali 到回編譯驗證，完整走一遍。參考解答藏在最後，先自己試。

→ [練習 A：手改 smali 破 crackme](./practice-a-smali-crackme.md)
