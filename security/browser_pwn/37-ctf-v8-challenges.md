# Ch 37 — CTF V8 題型全解：從拿到題目到打通的固定套路

> **目標**：把「拿到一題 CTF V8 pwn」到「打通拿 flag」這條路變成**可複製的流程**。你會認清 CTF V8 題只有兩大出題模式（challenge patch 型、真 bug / n-day 型）、學會 30 秒內判斷你面對的是哪一種、知道每一種的固定起手式，並復盤幾道公開經典題（\*CTF 2019 `oob`、Google CTF、以及 CTFtime 上可查的公開 writeup），把前 36 章的原語真正接到「一場比賽」的語境裡。這章是 Part 7 的第一課，把散落的技術收攏成「比賽時你腦裡跑的那套 checklist」。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`（sandbox on）、`out/x64.release.nosbx/d8`（off）。本章多為方法論；可驗證的（d8 版本、d8 shell helper、題目 harness 結構）真跑，完整某題端到端 exploit 標「未實測，理論預期」。

## 為什麼需要這個？

你前面 36 章學的每一塊——值的表示、Map、elements kind、TurboFan type confusion、addrof/fakeobj、任意讀寫、code exec——單看都是「一個技術點」。但真正坐在一場 4 小時的 CTF 前面，你面對的不是技術點，是**一個 tar 包**：裡面有一顆改過的 d8、一個 `Dockerfile`、一段給你連的服務、一個要你交出 shellcode 或讀 flag 的 harness。你要在幾分鐘內回答：這題的 bug 在哪？它給我什麼原語？我從我背熟的 template 裡抽哪一份？

**這章要教的不是新技術，是「調度」**。資深 V8 pwner 和新手的差距，一大半不在會不會做 addrof，而在拿到題目後**多快能定位「這題唯一要換的那個空格」**（[Ch 18](./18-oob-to-arbitrary-rw.md) 講的那條流水線，左邊那格）。CTF 出題者為了讓題目「可解且有區分度」，其實只有很有限的幾種植洞方式——認得它們，你就把一題陌生的題目瞬間 map 到一個你練過的骨架上。

## 先建立直覺：CTF V8 題的「兩種出身」

天下 V8 pwn 題，出身只有兩類：

```
   ┌─────────────────────────────────────────────────────────────┐
   │  你拿到一個 tar：d8 (或 patched chrome) + Dockerfile + harness │
   └─────────────────────────────────────────────────────────────┘
             │                                    │
   有沒有附 .patch / .diff ?          d8 版本很舊、且乾淨（沒 patch）?
             │ 有                                  │ 是
             ▼                                     ▼
   ┌───────────────────┐               ┌───────────────────────┐
   │ (A) challenge patch│               │ (B) 真 bug / n-day 型  │
   │ 出題者「植入」一個  │               │ 出題者鎖一個舊版 V8，  │
   │ 人造 primitive     │               │ 要你打它「本來就有」的 │
   │ （常是直接 OOB）    │               │ 一個已公開 CVE / bug   │
   └───────────────────┘               └───────────────────────┘
        地基原語直接送你                    你要先「找到」那個洞
        重點：把送的原語接上 template        重點：patch-diff / 認 CVE
```

**判斷只要看兩樣東西**：`Dockerfile` 或 build script 裡有沒有 `git apply xxx.patch`（→ A 型），以及 `d8` 的版本 / commit（→ 若很舊且乾淨，多半 B 型）。這一步花不到一分鐘，卻決定你接下來整場的方向。下面把兩型各拆開。

## (A) Challenge patch 型：出題者送你一個 primitive

這是**入門 CTF V8 題的絕對主流**，也是 [Ch 14](./14-first-oob.md) 開篇假設的那種。出題者拿一顆正常 d8，打一個小 patch，故意加一個「本來不存在、明顯不安全」的能力。最經典的植入是給 `Array.prototype` 加一個無邊界檢查的 OOB 讀寫：

```diff
--- a/src/builtins/builtins-array.cc
+++ b/src/builtins/builtins-array.cc
@@ ... 出題者手加的 builtin ...
+BUILTIN(ArrayOob) {
+  // 故意：不做 bounds check，直接讀/寫 elements[index]
+  // 給你一個 relative OOB read/write on a JSArray
+}
```

你在 JS 端就會有類似 `arr.oob(idx)` 或 `arr.oob(idx, value)` 這種現實中不存在的方法。**這就是 [Ch 18](./18-oob-to-arbitrary-rw.md) 流水線最左邊「每題唯一要換的空格」**——出題者已經把最難的「怎麼拿到 OOB」直接送你了，你只要把它接到你背熟的 addrof/fakeobj/read64/write64 骨架上。

辨認 patch 型的固定動作：

1. **讀 `.patch`**。這是全題資訊密度最高的檔。它精確告訴你「多了什麼能力、簽名長怎樣、有沒有 bounds check、OOB 的粒度是 element 還是 byte」。花五分鐘讀懂 patch，勝過盲試一小時。
2. **在 JS 端把新原語試出來**。開你的 patched d8，`arr.oob` 到底吃幾個參數、回傳什麼、越界讀到的是 tagged value 還是 raw double——直接 `%DebugPrint` 對照 [Ch 7](./07-jsarray-elements-kind.md)/[Ch 8](./08-arraybuffer-typedarray.md) 的佈局知識確認。
3. **map 到 template**。OOB read/write on double array → 直接走 [Ch 15](./15-addrof-fakeobj.md) 的「double 陣列與 object 陣列別名」路；OOB 給的是 TypedArray 相對讀寫 → 走 [Ch 17](./17-typedarray-attack.md) 劫持 backing store。

patch 型的難度差異，幾乎只在「送的原語有多弱」：送滿地圖任意 OOB read/write 是簡單題；只送 relative OOB read（沒寫）、或 OOB 只有 4 byte 粒度、或加了部分檢查逼你先繞——就是中難題。難度不在原語本身，在你要**用弱原語湊出強原語**。

## (B) 真 bug / n-day 型：出題者鎖一個舊版逼你找洞

進階賽（Google CTF、\*CTF hard、hxp、real-world CTF）常這樣出：給你一顆**版本很舊、但完全沒 patch** 的 d8，附一句「這是 Chrome M×× 的 V8」。沒有 `arr.oob` 這種天上掉的原語——因為這題的 bug 是這個版本 V8「本來就有、後來被修掉」的一個真實漏洞。你要做的事，本質是 [Ch 27](./27-patch-diffing.md) 的 patch-diff，只是方向反過來：

1. **定版本**。`d8 -e 'print(version())'` 拿到版本號，或看 tar 裡的 commit。
2. **找出這版之後修了什麼**。去 V8 的 bug tracker / commit log 找這個版本區間的安全修補（[Ch 26](./26-reading-v8-source-commits.md) 教怎麼讀 commit）。CTF 出題者偏愛「有公開 writeup 的 CVE」，因為要保證可解——所以你認出 CVE 編號後，多半能對照到一篇現成 writeup。
3. **拿 PoC 觸發 → 接原語**。找到那個 bug 的 PoC（觸發 type confusion / OOB），確認在這顆 d8 上真的 crash，然後把它從「crash」養成 [Ch 30](./30-exploitability-triage.md) 講的可控原語，再接 template。

B 型的關鍵技能是**認 CVE**：看到「M89 的 d8」你要能聯想「這區間有沒有著名的 TurboFan bug」。這需要你平時就對 V8 安全史有肌肉記憶（[Ch 40](./40-p0-writeup-map-next-steps.md) 教你怎麼系統性累積這個記憶）。CTF 出題者幾乎不會拿一個「毫無公開資料的私有 0-day」出題——那樣沒人解得出來，賽制上就爛掉了。所以 B 型永遠是「n-day」，不是「0-day」。

## 經典題復盤（一）：\*CTF 2019 `oob` — patch 型的教科書

\*CTF 2019 的 `oob` 是幾乎每個 V8 pwn 教程都會拿來當第一題的題目，公開 writeup 多如牛毛（CTFtime 上可查）。它就是最純粹的 (A) patch 型，patch 大意是給 `Array.prototype` 加一對 `oob` getter/setter，越界一格讀寫 elements：

```js
// *CTF 2019 oob 的心智模型（非逐字，示意）
let a = [1.1, 2.2, 3.3];
a.oob();        // 讀 a.elements[a.length]（越界一格）
a.oob(value);   // 寫 a.elements[a.length]（越界一格）
```

它為什麼是最佳入門題：**越界那一格，剛好能踩到「下一個相鄰陣列的 map 或長度」**。標準解法（對照 [Ch 15](./15-addrof-fakeobj.md)）：

1. 並排放一個 double 陣列 `float_arr` 和一個 object 陣列 `obj_arr`，讓它們的 elements 在記憶體相鄰。
2. 用 `oob` 越界改寫，把 `obj_arr` 的 elements 讀成 double（拿到物件指標的數值 = **addrof**），或把一個 double 寫進去被當成物件指標（**fakeobj**）。
3. 有了 addrof/fakeobj → [Ch 16](./16-fake-object-rw.md) 造一個 fake ArrayBuffer/TypedArray → 任意讀寫 → [Ch 32](./32-arbitrary-rw-to-code-exec.md) 用 WASM RWX 頁寫 shellcode → 拿 shell。

這題的全部價值在於：**它把「拿到 OOB」這一最難的步驟直接送你**，讓你純練右邊三個工位。你如果 [Ch 14](./14-first-oob.md)–[Ch 18](./18-oob-to-arbitrary-rw.md) 練熟了，這題應該 30 分鐘內能寫完 exploit 骨架。把它當成你「template 有沒有背熟」的體檢。

> **時代註記**：\*CTF 2019 的 V8 沒有 pointer compression、沒有 V8 Sandbox，所以「拿到任意讀寫 → 直接找 WASM RWX 頁寫 shellcode」這條經典路是通的。你在 15.3 這顆現代 d8 上重做同型題，會撞到 sandbox（[Ch 34](./34-v8-sandbox.md)）——這正是本課刻意站在現代的原因。復盤老題學**結構**，但別照抄它的收尾（[Ch 38](./38-d8-vs-real-chrome.md) 會把「老 exploit 為何在新 V8 失效」講死）。

## 經典題復盤（二）：Google CTF 系列 — B 型與「弱原語」

Google CTF 的 V8 題長年是難度標杆，風格偏向兩種：

- **鎖真 bug 型（B 型）**：例如某些年給你一個對應真實 TurboFan CVE 的舊版 d8，要你自己找到並觸發那個 type confusion。這類題的解題起手式就是本章前面 (B) 那套：定版本 → 找修補 commit → 認 CVE → 抄公開 PoC 觸發 → 接 template。
- **弱原語 patch 型**：patch 只送你一個**很殘的** primitive（例如只有 OOB read、或只能寫受限的值、或 confusion 只發生在很窄的條件下），逼你用 [Ch 15](./15-addrof-fakeobj.md)–[Ch 17](./17-typedarray-attack.md) 的技巧把它「養」成完整任意讀寫。難度全在「弱→強」的湊。

Google CTF 這類題的官方或選手 writeup 品質通常很高（賽後多半公開在 GitHub / 個人 blog），是你練完本課後**最好的實戰對照組**。讀它們時，刻意用本課的座標系標註：「這步在造 addrof（Ch 15）」「這步在繞 CheckBounds（Ch 20）」「這步在做 code exec（Ch 32）」——把別人的 writeup 翻譯成你的章節語言，是把「讀懂」變成「會做」的關鍵一步。

## 怎麼快速上手一題 V8 pwn：實戰 checklist

把上面收攏成一張你比賽時照著跑的 checklist：

```
【0】解包，看清楚給了什麼
    - Dockerfile / start.sh：怎麼跑 d8？餵 stdin 還是給檔名？有沒有 flag 讀取方式？
    - 有沒有 .patch / .diff ?           → 有 → (A) patch 型
    - d8 版本：d8 -e 'print(version())'  → 舊且乾淨 → (B) n-day 型

【1】(A) patch 型
    - 逐行讀 patch：多了什麼原語？簽名？有無 bounds check？粒度？
    - 開 d8 把新原語試出來，%DebugPrint 對照物件佈局
    - map 到 template 左格（Ch 18），右邊三格照抄

【1】(B) n-day 型
    - 版本 → 找該區間安全修補 commit（Ch 26）
    - 認 CVE → 找公開 writeup / PoC
    - PoC 在這顆 d8 上真的 crash 嗎？→ 養成可控原語（Ch 30）→ 接 template

【2】收尾（兩型共用）
    - sandbox on 嗎？（Ch 34）→ 決定 code exec 路徑（Ch 32/33/35）
    - seccomp 開嗎？看 Dockerfile → 決定是 execve 還是 open+read flag（Ch 38）
    - 本地打通 → 對遠端服務重打，注意 ASLR/佈局差異

【3】穩定性
    - CTF 通常打一次就好，但遠端可能要重試：把 spray / 佈局做穩（Ch 13 GC 影響）
```

**最容易被新手忽略的是【0】和【2】的環境細節**。很多人本地 exploit 打通了，遠端卻拿不到 flag——不是 exploit 錯，是沒看清楚遠端 harness 是餵 stdin 還是給檔名、flag 在哪、seccomp 擋不擋 `execve`（[Ch 38](./38-d8-vs-real-chrome.md) 專講這些「非 V8 本身」的差異）。

## 動手：解包一題的最小自動化

CTF 開賽你要在幾秒內回答「A 型還是 B 型」。把它寫成一個腳本，拿到 tar 直接跑。下面在 d8 端先驗證幾個你**每題都會用到的 shell helper**（真跑）：

```js
// /d/bpwnP7_help.js — 確認 CTF harness 常用的 d8 shell 能力
print("read:"     + typeof read);       // 讀檔（本地讀 flag 常用）
print("readline:" + typeof readline);   // 逐行讀 stdin
print("quit:"     + typeof quit);       // 主動結束（打通後乾淨退出）
print("d8.file.read:" + typeof d8.file.read);
```

在本課的 d8 上真跑輸出：

```
read:function
readline:function
quit:function
d8.file.read:function
```

這四個 helper 是 d8 蜜糖：CTF 本地題常用 `read('/flag')` 直接讀 flag（如果 exploit 已拿到夠強的能力、或題目就是要你任意讀檔）；`readline` 對「服務餵你資料再要你回應」的互動題有用；`quit()` 讓你 exploit 成功後乾淨結束，不留一堆 GC/deopt 噪音。**注意**：真實 Chrome renderer 沒有這些 helper（它們是 d8 專屬），這是 [Ch 38](./38-d8-vs-real-chrome.md) 的伏筆——CTF 的 d8 是個「開了作弊選單」的簡化靶。

判斷 A/B 型的解包腳本骨架（MSYS/WSL 端，非 V8）：

```bash
# 拿到 chal.tar 後
tar xf chal.tar && cd chal
grep -rn "git apply\|\.patch\|\.diff" . && echo ">> 可能是 (A) patch 型"
# 找 d8 並問版本
find . -name d8 -type f | head -1 | xargs -I{} sh -c '{} -e "print(version())"'
```

## 對比：CTF 兩型 vs 真實漏洞研究

| 面向 | (A) challenge patch | (B) n-day 型 | 真實 0-day 研究 |
|---|---|---|---|
| bug 從哪來 | 出題者植入（送你） | 舊版真 bug（要你認/找） | 自己 fuzz/patch-diff 挖 |
| 起手核心技能 | 接 template | 認 CVE + 抄 PoC | Fuzzilli + triage（Part 5） |
| 有無公開答案 | 無（但套路固定） | 幾乎都有 writeup | 無 |
| sandbox/seccomp | 常關或簡化 | 視版本 | 全開，要 full chain |
| 對應本課 | Part 3 原語 | Part 5 找洞 + Part 3 | Part 5 全部 + Ch 39 |

這張表點出一件事：**CTF 是真實研究的「切片」**。patch 型切出「利用原語」這一段讓你純練；n-day 型切出「認洞 + 接原語」；只有真實 0-day 才要你從零 fuzz。你打完足夠多的 CTF 題，等於把真實研究流程的每一段分開練熟了——這正是 Part 5 + Part 7 的設計意圖。

## 踩雷集錦

1. **不讀 patch 就開始盲試**：patch 型題的 `.patch` 是資訊密度最高的檔，五分鐘讀懂勝過盲試一小時。新手常直接開 d8 亂打，卻不知道出題者到底改了什麼、送了什麼原語——這是最浪費時間的錯。
2. **照抄老 writeup 的收尾**：\*CTF 2019 那種「任意讀寫 → 找 WASM RWX 頁 → 寫 shellcode」在**沒 sandbox 的舊 V8** 上成立。你在現代 d8（sandbox on）照抄，`ArrayBuffer` backing store 指標被關進 external pointer table（[Ch 34](./34-v8-sandbox.md)），這條路直接斷。學結構，不要學過時的收尾。
3. **本地打通就以為結束**：遠端服務的 harness 可能餵 stdin（不是給檔名）、flag 路徑不同、seccomp 開著擋 `execve`。本地和遠端的「非 V8 環境」差異（[Ch 38](./38-d8-vs-real-chrome.md)）害死一堆本地能跑的 exploit。開賽先把 `Dockerfile` 讀透。
4. **B 型題不定版本就開始找洞**：n-day 型的第一步永遠是 `print(version())` + 找該版區間的修補。跳過這步去盲 fuzz 一顆舊 d8，等於在比賽現場重做別人早做完的事，時間根本不夠。
5. **把 d8 shell helper 當成真實能力**：`read`/`readline`/`os.system` 是 d8 專屬蜜糖，CTF 能用是因為靶被簡化。別把「我 exploit 裡用了 `read('/flag')`」當成你的 exploit 有多強——真實 renderer 裡它們根本不存在。
6. **spray/佈局不做穩就打遠端**：本地 GC 狀態乾淨，一次就中；遠端可能因 GC 佈局（[Ch 13](./13-garbage-collection.md)）偶爾失敗。互動題要能重試、要把並排 spray 做穩，別賭一次。

## 進階：再往深一層

- **patched Chrome（不是 d8）的 CTF 題**：少數硬核賽（real-world CTF、部分 Google CTF）給的是**改過的完整 Chrome renderer**，不是 d8。這時你的 exploit 要跑在真的 renderer 裡（有 DOM、可能有簡化的 sandbox），起手式不同——[Ch 38](./38-d8-vs-real-chrome.md) 專門講「從 d8 exploit 移植到 renderer 要補什麼」。認出「這題是 Chrome 不是 d8」很重要，因為連 `print`/`read` 這些 d8 helper 都沒有，你得靠 DOM/`fetch` 這類真實 API 做 I/O。
- **「弱原語湊強原語」的招式庫**：中難題的核心。常見招：只有 OOB read → 先 leak 一個相鄰 TypedArray 的 backing store 指標再 partial overwrite；只能寫受限值 → 用多次寫拼出你要的指標；confusion 條件很窄 → 用 `%OptimizeFunctionOnNextCall`（CTF d8 常開 `--allow-natives-syntax`）精準控制優化時機。把這些招式各自練一遍，中難題就不再是牆。
- **`--allow-natives-syntax` 開不開**：CTF d8 常開這個 flag，讓你能用 `%OptimizeFunctionOnNextCall`/`%DebugPrint` 這些 natives。開著是巨大方便（精準控制 tiering，見 [Ch 12](./12-speculation-deopt.md)）。真實 Chrome 不開——所以依賴 `%OptimizeFunctionOnNextCall` 的觸發碼在真實環境要改成「跑熱迴圈自然觸發優化」（[Ch 38](./38-d8-vs-real-chrome.md) 談移植）。
- **賽後把每題歸檔**：資深選手都有自己的「題型 → 骨架」對照庫。每解一題，把「這是哪型、bug 在哪層、我複用了哪份 template、收尾走哪條」記下來。累積十題，你拿到新題的定位速度會質變。

## 動手練習

1. **A 型體檢**：在本課的 d8 上手動打一個「Array.prototype.oob」風格的小 patch（或直接找 \*CTF 2019 oob 的 d8 build），把 [Ch 18](./18-oob-to-arbitrary-rw.md) 的 template 左格填上，計時看你多久能跑到 addrof/fakeobj。目標：30 分鐘內。跑不到就回頭補 Part 3。
2. **A/B 判別演練**：下載三份公開 CTF V8 題的 tar（CTFtime 上很多附檔連結），對每一份只做本章【0】步——判斷 A 型還 B 型、找出 patch（或版本）、說出「這題唯一要換的空格」是什麼。不用解，只練定位。
3. **翻譯 writeup**：找一篇 \*CTF 2019 oob 或 Google CTF V8 題的公開 writeup，逐段用本課章節號標註（「這步 = Ch 15 addrof」「這步 = Ch 32 code exec」）。翻譯完你會發現：別人的 exploit 90% 是你已經學過的 template。
4. **shell helper 摸底**：在你的 d8 上跑本章那段 `/d/bpwnP7_help.js`，確認 `read`/`readline`/`quit`/`d8.file.read` 都在。再試 `os.system`（可能要 `--enable-os-system`），體會 CTF d8 給你多少「作弊」能力——並記住真實 renderer 一個都沒有。

## 本章重點整理

- CTF V8 題出身只有兩類：**(A) challenge patch 型**（出題者植入 primitive，多半直接送 OOB）和 **(B) n-day 型**（鎖舊版逼你認/找真 bug）。開賽一分鐘內用「有無 .patch」+「版本」判別。
- **A 型的重點是接 template**：出題者已送你流水線最左格的 OOB，你只要把 addrof/fakeobj/read64/write64（Part 3）照抄上去。難度差異只在「送的原語有多弱」。
- **B 型的重點是認 CVE**：定版本 → 找修補 commit → 認 CVE → 抄公開 PoC → 觸發 → 接 template。CTF 幾乎都是 n-day（有公開資料），不是私有 0-day。
- \*CTF 2019 `oob` 是 A 型教科書、最佳 template 體檢題；但它的**收尾（WASM RWX 寫 shellcode）在現代 sandbox d8 上已失效**，學結構別學過時收尾。
- 最容易翻車的不是 V8 技術，是**環境細節**（harness 餵法、flag 路徑、seccomp、本地 vs 遠端佈局）——開賽先把 Dockerfile 讀透。

## 自我檢核

- [ ] 拿到一份 CTF V8 tar，能在一分鐘內判斷是 A 型還 B 型，並說出判斷依據
- [ ] 能解釋「challenge patch 型送你的到底是什麼」，以及它對應 [Ch 18](./18-oob-to-arbitrary-rw.md) 流水線的哪一格
- [ ] 能講清楚 B 型的完整起手式，以及為什麼 CTF 幾乎都是 n-day 而非 0-day
- [ ] 能說出 \*CTF 2019 oob 的標準解法輪廓，以及它的收尾為何在現代 d8 上失效
- [ ] 知道 d8 shell helper（`read`/`os.system`…）是簡化靶的蜜糖，真實 renderer 沒有
- [ ] 面試/賽後被問「你怎麼上手一題陌生的 V8 pwn」，能講出本章那張 checklist

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

- **[\*CTF 2019 oob 的公開 writeup（CTFtime 該題頁面聚合多篇）— ctftime.org](https://ctftime.org/)**
  - **這篇說什麼**：最經典的 V8 入門 patch 型題的完整解法，addrof/fakeobj → 任意讀寫 → shellcode 全流程。
  - **讀哪裡**：挑一篇附完整 exploit js 的，逐段對照本課 Part 3。**注意**它的收尾（WASM RWX）屬於前 sandbox 時代。
  - **和本章的關聯**：本章「經典題復盤（一）」的一手來源，是你的 template 體檢題。

- **[Google CTF 歷屆題目與官方 repo — github.com/google/google-ctf](https://github.com/google/google-ctf)**
  - **這篇說什麼**：Google CTF 各年題目（含 V8 pwn）的原始檔與部分官方解。
  - **讀哪裡**：找標題含 v8 / js 的 pwn 題，先看它給的是 patch 型還 n-day 型，對照本章兩型分類。
  - **為什麼值得讀**：難度標杆，「弱原語湊強原語」和「認真 bug」兩種進階能力的最佳練兵場。

- **[doar-e：Jeremy Fetiveau 的 V8 exploit 系列 — doar-e.github.io](https://doar-e.github.io/)**
  - **這篇說什麼**：把一個 TurboFan bug 從觸發到完整 exploit 寫得極清楚的系列，多篇直接對應 CTF/真實 CVE。
  - **和本章的關聯**：教你 B 型「認洞 → 接原語」那一段的思路怎麼落地成 code，是本課 Part 4 的直接延伸。

- **[saelo 的 exploit writeup 與「Attacking JavaScript Engines」— saelo.github.io / Phrack 70](http://phrack.org/issues/70/9.html)**
  - **這篇說什麼**：JS 引擎利用的奠基性文章，addrof/fakeobj 的思想源頭（雖以 JSC 為例，思路完全平移 V8）。
  - **前提**：讀完本課 Part 3 再看，會發現本課的骨架就是它的現代 V8 版。

CTF 是簡化的靶。你在 d8 裡用 `read('/flag')`、`os.system('/bin/sh')` 打通一題，很爽——但這顆 d8 和真實 Chrome renderer 差了整整一個世界。下一章把這條界線劃清楚：d8 到底簡化了什麼、真實 renderer 多了什麼、你的 CTF exploit 要移植到真 Chrome 得補上哪些功課。

→ [Ch 38 — d8 與真實 Chrome renderer 的差異](./38-d8-vs-real-chrome.md)
