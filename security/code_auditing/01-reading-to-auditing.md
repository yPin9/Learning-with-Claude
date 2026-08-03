# Ch 1 — 讀碼即逆向 → 審計即規模化

> **目標**：把 `reading_code` 教你的「手讀一條路徑找洞」的 SOP，接上這門課的核心——**規模化變體獵殺（variant analysis）**。你會搞懂：手讀為什麼追得深卻覆蓋不了整個 repo；一個 bug 修好之後，同型的變體為什麼還躲在別處；以及「從一個已知 bug 抽 pattern → 用 query 掃全 repo/生態找變體」這個動作，為什麼是現代 vuln research 真正的槓桿所在。

## 從 `reading_code` Part 5 接上來

在 [`reading_code`](../../soft_skills/reading_code/README.md) 的最後幾章，你已經練成一套「找漏洞式讀碼」的 SOP：從攻擊面切入、找 entry point、順著 tainted data 追一條路徑、在腦中維護 invariant、盯著「作者在防什麼、有沒有防漏」。這套方法很強——它讓你能追進 redis、curl、kernel 這種等級的真實 source，把一條 `read()` → parse → `memcpy()` 的路徑追到底，找出那個 off-by-one。

但它有一個**結構性的天花板**：人眼一次只能追一條路徑。

一個十萬行的 codebase，你手讀一天能追幾條路徑？三條？五條？就算你是頂尖的 auditor，注意力、記憶體、耐性都是有限資源。你追第 20 條路徑時，第 3 條路徑的 invariant 早就從腦子裡蒸發了。手讀的深度換來的代價是**覆蓋率趨近於零**：你精讀過的那 0.1% code 也許真的挖到寶，但剩下 99.9% 你根本沒看。

這不是能力問題，是規模問題。而規模問題，要用機器解。

```
   手讀（reading_code）              規模化 query（本課）
   ─────────────────                ──────────────────
   一次一條路徑，追得深              一次掃全 repo，覆蓋廣
   人腦維護 invariant，能理解語意    query engine 展開所有路徑
   0.1% 覆蓋率 × 高命中深度          100% 覆蓋率 × 需人工 triage
   適合：搞懂一個 bug 的全貌         適合：找出「同型 bug 還有幾個」
```

**這門課不是要取代手讀，而是把手讀的產物工業化。** 你手讀挖到的每一個 bug，都藏著一個可以抽象成 pattern 的「錯誤形狀」。手讀給你那個形狀；query 幫你在整個 repo、甚至整個生態裡，把所有長這個形狀的地方一次撈出來。

## 變體問題：一個 bug 修掉，同型的還在

先講一個讓 vuln research 圈子集體上頭的現象，叫**變體（variant）**。

假設某專案有這麼一個 bug：某個 handler 收到使用者控制的長度 `len`，直接拿去 `memcpy` 而沒檢查目標 buffer 夠不夠大。安全團隊收到報告、發了 CVE、commit 了一個 fix——在**那一處**加上 bound check。皆大歡喜。

問題是：同一個作者、同一套 mental model、同一個 code review 文化，寫出來的 code 裡，這種「收 user 長度直接 copy」的寫法，**幾乎不可能只有那一處**。它是一個 pattern，是這個團隊的集體盲點。你修掉了被人報上來的那一處，另外七處長得一模一樣的還安安靜靜躺在 repo 裡，等下一個研究者（或攻擊者）發現。

這些「和已知 bug 同型、但沒被一起修掉」的地方，就是**變體**。

變體問題為什麼手讀抓不完？因為手讀是「路徑導向」的——你追的是**一條**從 source 到 sink 的資料流。但變體是「pattern 導向」的——你要問的是「整個 repo 裡，有幾個地方符合『user 長度 → 未檢查 → memcpy』這個形狀」。這是兩種完全不同的搜尋：前者是深度優先追一條線，後者是在整個 code 空間裡做 pattern matching。人眼做不了後者，因為你沒辦法「同時看十萬行」。

> **變體分析（variant analysis）**：從一個已知的 bug 出發，抽取出它的抽象 pattern（哪種 source、哪種缺失的檢查、哪種 sink），寫成一條可執行的 query，掃遍整個 codebase 或整個生態，找出所有符合同一 pattern 的地方。這是 GitHub Security Lab 團隊反覆示範的核心方法：他們拿一個已公開的 CVE，把它的 root cause 寫成一條 CodeQL query，往往就在同一個專案、或同生態的其他專案裡，撈出一批從沒被人發現過的同型 bug。

謹慎起見我要標清楚：variant analysis 不是「保證找到所有變體」的魔法。你的 query 抽得準，就撈得全；抽得歪，就漏掉真變體、或淹沒在誤報裡。**query 的品質決定一切**，這也是為什麼這門課後面要花那麼多篇幅教你怎麼把 pattern 抽準（Part 2）、怎麼把 query 寫對（Part 3–5）、怎麼 triage 誤報（Part 7）。

## 手讀 vs 規模化 query：分工，不是取代

把兩者攤成一張表，你就知道它們各自的位置：

| 維度 | 手讀（reading_code） | 規模化 query（本課） |
|---|---|---|
| **覆蓋率** | 極低（一次一條路徑） | 全 repo / 全生態 |
| **深度** | 極深（理解語意、隱藏 invariant） | 淺～中（看 query 精度） |
| **誤報** | 幾乎零（你親眼確認過） | 高（工具給你一堆命中要 triage） |
| **前期成本** | 低（打開檔案就讀） | 高（要建 database / 寫 query / 學工具） |
| **產能天花板** | 一天幾條路徑 | 一條好 query 掃完一個生態 |
| **最適場景** | 搞懂一個 bug 的完整成因、驗證命中 | 從已知 bug 找變體、掃 CWE 大類 |

看懂這張表，你就知道正確的 workflow 是**兩者交替**，不是二選一：

1. **手讀開路**：先用 `reading_code` 的方法，在 target 上手讀，挖到第一個 bug、或找到一個值得懷疑的 pattern。這一步不可省——你得先「懂」，才能抽出對的 pattern。
2. **抽 pattern**：把這個 bug 的形狀抽象化——哪種 source？缺哪個 sanitizer？哪種 sink？
3. **query 掃全場**：把 pattern 寫成 query，掃整個 repo / 生態，撈出所有變體。
4. **手讀收尾**：query 給你一堆命中，回到手讀模式一個個確認——這是真 bug 還是誤報？能不能構造 PoC？

手讀是規模化的**輸入端**（提供 pattern）和**輸出端**（驗證命中）；query 負責中間那段人做不了的「掃全場」。**工具不關心語意對不對，只關心形狀符不符**；語意判斷還是得靠你這顆腦袋。

## 一個具體對比：缺 bound check 的 `memcpy`

抽象講完了，來看一個能真的動手的例子。假設你在手讀時，發現這麼一段：

```c
// handler_a.c
void handle_request_a(int fd) {
    uint32_t user_len;
    char buf[256];
    read(fd, &user_len, sizeof(user_len));   // user_len 完全由對方控制
    char *payload = read_payload(fd, user_len);
    memcpy(buf, payload, user_len);           // ← 沒檢查 user_len <= sizeof(buf)，classic OOB write
}
```

`buf` 是 256 bytes 的 stack buffer，`user_len` 由網路對端控制，`memcpy` 直接照著 `user_len` 拷——經典的 stack buffer overflow。你手讀找到了這一處，很好。

**現在問題來了：整個 repo 裡，還有幾個 handler 這樣寫？**

### 第一反應：grep

你會直覺地想 grep 一下所有 `memcpy`：

```bash
$ rg 'memcpy' --type c -n
handler_a.c:9:    memcpy(buf, payload, user_len);
handler_b.c:14:   memcpy(dst, src, sizeof(dst));
handler_c.c:22:   memcpy(out, in, MIN(in_len, sizeof(out)));
util.c:88:        memcpy(&hdr, pkt, sizeof(hdr));
proto.c:130:      memcpy(session->key, keybuf, key_len);
...（另外 340 筆）
```

grep 立刻暴露它的問題：**它只認字串，不認語意。** 這 345 筆命中裡：

- `handler_b.c` 的 `sizeof(dst)` 是安全的（拷貝量綁在目標大小上）。
- `handler_c.c` 用了 `MIN(in_len, sizeof(out))` clamp 過，也安全。
- `util.c` 拷的是固定大小 header，安全。
- `proto.c` 的 `key_len` 是不是 user 控制的？grep 看不出來。

grep 把安全的和危險的**混在一起**，而且它**看不出資料流**——它不知道 `user_len` 從 `read(fd, ...)` 來、是 tainted 的；也不知道 `handler_c` 那個 `MIN` 就是缺失的 sanitizer。你面對 345 筆命中，等於什麼線索都沒有，還是得一筆筆手讀回去確認。對一個十萬行的 repo，這跟沒掃一樣。

你可以試著用更聰明的 regex 縮面，比如「第三個參數是變數、不是 `sizeof(...)`」：

```bash
$ rg 'memcpy\([^,]+,[^,]+,\s*[a-z_]+\)' --type c -n
```

這確實濾掉了一批 `sizeof` 的命中，但你馬上撞到 regex 的天花板：

- 它濾不掉 `MIN(in_len, sizeof(out))`——因為那不是單純的變數名，regex 要窮舉所有 clamp 寫法根本寫不完。
- 它不知道 `user_len` 是 tainted、`key_len` 可能不是——**regex 沒有資料流概念**。
- 換一種寫法（`memcpy` 拆成多行、透過 macro 呼叫、`len` 先存進 struct 再拿出來用）它就瞎了——**regex 沒有語法結構概念**，它看的是字元序列，不是 AST。

這就是 grep/regex 的根本極限：**它在字元層面工作，而漏洞是語意層面的性質**。「user 控制的長度、沒被 clamp、流進了寫入原語」——這句話裡的每一個限定詞（user 控制、沒被 clamp、寫入原語）都是 grep 表達不出來的。

### 這就是四工具要解決的事

你需要的是一個能理解「語法結構 + 資料流」的工具，讓你能寫出接近自然語言的 query：

> 「找出所有 `memcpy` 呼叫，其 size 參數的資料流可以追溯到一個 network read，且中間沒有經過任何 bound check。」

這句話正是這門課四把刀在做的事，先給你一句話的鳥瞰（細節全留 [Ch 2](./02-static-analysis-landscape.md) 展開）：

- **CodeQL**——把整個 codebase 抽成關聯式 database，用 QL 語言下這種「跨函式追資料流」的深查詢。變體獵殺主力。
- **Semgrep**——用接近原始碼長相的 pattern 加輕量 taint 快篩，跨語言、上手快，適合先撈一輪。
- **Joern**——把 code 抽成 code property graph（CPG），用圖查詢做語意搜尋，且**不需要能 build** 就能跑，適合殘缺 / 陌生 target。
- **weggli**——C/C++ 專用的「半結構化 grep」，比 regex 懂語法結構、比 CodeQL 快得多，是縮小攻擊面的第一道漏斗。

四把刀的共通點：它們都在**語法結構之上**工作（起碼認得出「這是一個 `memcpy` 呼叫、這是它的第三個參數」），好一點的還能追資料流。這一步跨過去，你就從「grep 給我 345 筆噪音」進化到「query 給我 3 筆真正可疑的變體」。

## 踩雷集錦

**踩雷 1：以為工具能取代手讀。**
錯誤直覺：「有了 CodeQL，我就不用讀碼了，掃一掃就出 bug。」
正確認識：工具是**放大器**，不是**替代品**。它需要你先手讀懂一個 bug 才能抽出對的 pattern（輸入端），也需要你手讀確認每個命中是不是真的（輸出端）。不會手讀的人寫不出好 query，也 triage 不了誤報——他只會被一堆 false positive 淹死。這門課的前提，就是你已經會 `reading_code` 那套。

**踩雷 2：以為 grep 就夠了。**
錯誤直覺：「找 `memcpy` 漏洞不就 `rg memcpy` 嗎？」
正確認識：grep 在**字元層面**工作，漏洞是**語意層面**的性質。「user 控制」「沒被 clamp」「流進寫入原語」這些限定詞 grep 通通表達不出來，於是它把安全的和危險的混在一起丟給你，等於沒縮面。regex 稍微聰明一點但很快撞牆——它沒有語法結構、沒有資料流概念，換個寫法就瞎。grep 是好的第一道快篩（見 weggli），但它**不是**變體分析。

**踩雷 3：以為零誤報。**
錯誤直覺：「工具報的都是 bug，報越多越好，最好一個不漏。」
正確認識：靜態分析永遠在**漏報（false negative）**和**誤報（false positive）**之間取捨，不可能兩全（原理見 [Ch 2](./02-static-analysis-landscape.md) 的 sound / complete）。實務上你面對的是一大堆命中，其中大部分是誤報。**學會 triage 和排序命中，比學會寫 query 更難、也更值錢**（這是 Part 7 的重頭戲）。誰跟你保證「零誤報」，誰就是在賣你東西。

## 本章重點整理

- 手讀（`reading_code`）追得深但覆蓋率趨近於零；規模化 query 覆蓋全場但需人工 triage。兩者**分工互補，不是二選一**。
- **變體問題**：一個 bug 修掉後，同型的變體幾乎一定還躲在 repo/生態別處，因為它反映的是團隊的集體盲點。手讀是「路徑導向」的，抓不完「pattern 導向」的變體。
- **變體分析（variant analysis）**：從已知 bug 抽 pattern → 寫 query → 掃全場找變體。這是現代 vuln research 的槓桿，也是全課核心動作。GitHub Security Lab 反覆用它從一個 CVE 撈出一批新 bug。
- 正確 workflow：**手讀開路（抽 pattern）→ query 掃全場 → 手讀收尾（驗證）**。
- grep/regex 在字元層面工作，表達不出「user 控制 / 缺 sanitizer / 寫入原語」這類語意性質；四工具（CodeQL / Semgrep / Joern / weggli）在語法結構與資料流之上工作，才做得了變體分析。

## 自我檢核

- [ ] （主動回憶）不看筆記，說出「變體」是什麼、以及為什麼手讀抓不完變體。
- [ ] （主動回憶）畫出「手讀 → 抽 pattern → query → 手讀驗證」的 workflow，並說明手讀在頭尾各扮演什麼角色。
- [ ] （理解）給你那段缺 bound check 的 `memcpy`，說明為什麼 `rg memcpy` 撈出 345 筆等於沒縮面，而 regex 縮面又會在哪三個地方撞牆。
- [ ] （理解）為什麼「工具報越多越好、最好零漏報」是個危險的直覺？漏報和誤報的取捨跟你的 triage 成本有什麼關係？
- [ ] （應用）挑一個你手讀過的 bug，試著用一句自然語言把它的 pattern 描述成「哪種 source + 缺哪個 sanitizer + 哪種 sink」——這就是你之後要翻成 query 的東西。

## 延伸閱讀

- **[GitHub Security Lab — research 頁](https://securitylab.github.com/research/)**：讀任何一篇「用 CodeQL 找 CVE 變體」的 write-up（例如他們對 U-Boot、ffmpeg、各種 C 專案的分析）。看他們怎麼從**一個** root cause 出發、抽成 query、撈出**一批**新 bug——這就是本章講的變體分析的實戰範本。前提：略懂 CodeQL 概念，看不懂 query 語法沒關係，先看方法論。
- **[`reading_code` Part 5「找漏洞式讀碼」](../../soft_skills/reading_code/README.md)**：這門課的**輸入端**。回頭複習手讀怎麼從攻擊面切入、追一條 tainted 路徑、維護 invariant。本章講的「抽 pattern」就是建立在你能先手讀懂一個 bug 之上。
- **《The Art of Software Security Assessment》Ch 1–4**（Dowd, McDonald, Schuh）：手動 code audit 的聖經。讀它對「漏洞的形狀」的分類方式——這些分類正是你之後抽 pattern 時的詞彙庫。前提：C/C++ 基礎、有記憶體安全概念。
- **[Semgrep 官方 blog 的 variant analysis 類文章](https://semgrep.dev/blog/)**：另一個工具視角的變體分析，跟 CodeQL 的做法對照著看，體會「pattern 該抽多抽象」的取捨。前提：無，當入門讀物即可。

搞懂了「為什麼要規模化、變體分析是什麼」之後，下一步是建立靜態分析的世界觀——工具的能力邊界從哪來、四把刀各自站在座標的哪個位置。

→ [Ch 2 靜態分析全景](./02-static-analysis-landscape.md)
