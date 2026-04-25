# Ch 20 — DRAT proof 與 UNSAT 驗證

> 目標：讓你的 SAT solver 在回 UNSAT 時**附上可機器驗證的 proof**。UNSAT 很容易「假回報」（bug 會讓 SAT solver 把 SAT instance 說成 UNSAT），DRAT format 是 SAT 圈的標準解決方案，從 2013 SAT Competition 強制要求起就是業界標配。

## UNSAT 的信任問題

你的 solver 回 SAT，**你可以驗證** — 把 model 代回 CNF 跑 eval，全部 ⊤ 就對。

你的 solver 回 UNSAT？**沒法直接驗證**。你憑什麼相信？一個有 bug 的 solver 可能把 SAT instance 漏掉 assignment 就回 UNSAT。

**Kissat、CaDiCaL 都曾經在 SAT competition 出過這種 bug** — 演算法對但實作錯、漏掉某些 assignment。如果答案只是「UNSAT」沒附佐證，誰也不知道。

**解法：讓 solver 產一份 proof，外部驗證器檢查**。

## Resolution Proof：太貴

Ch 6 告訴你 UNSAT 的 proof = 從原 CNF 出發、一路 resolve 到空 clause。存這個 DAG 是「傳統」做法。

**問題**：resolution proof **很大**，實務上 clause 數可能是 CNF 的數倍、數十倍。SAT competition 的某些 UNSAT instance resolution proof 達到 TB 級。光寫檔就拖垮 solver。

## DRUP：只記 learned clause

**DRUP (Delete Reverse Unit Propagation)**, Heule, Hunt, Wetzler 2013。觀察：

> CDCL 的每條 learned clause 都能 **用 unit propagation 從現有 CNF 推出**。

所以不用記完整 resolution chain，只記「我學了這條 clause」。Verifier 拿 learned clause，**自己跑 unit propagation 驗證它確實能推出**。

DRUP format 極簡：**就是一連串 learned clause，每條一行，結尾 `0`**。跟 DIMACS clause 一模一樣，只差沒 `p cnf` header：

```
-1 2 0
-3 -4 0
-2 -3 0
0          ← 空 clause，proof 結束（表 UNSAT）
```

**跟 CDCL 天生適配** — 每學一條 clause 就輸出到 proof file。效能 overhead 幾乎只有 I/O。

## DRAT：比 DRUP 強的變體

**DRAT (Deletion and Resolution Asymmetric Tautology)**, Wetzler, Heule, Hunt 2014。加兩個東西：

### 1. Deletion line

Solver 做 clause deletion（Ch 15 LBD）時，通知 proof：

```
d 1 2 3 0
```

`d` 開頭代表 delete。讓 verifier 知道這條 clause 已從 CNF 移除、後續 clause 的 RUP 驗證不要用它。

### 2. Asymmetric Tautology

DRUP 只驗 RUP（Reverse Unit Propagation）性質。某些合理的 learned clause **不符合 RUP**，但符合 RAT（Resolution Asymmetric Tautology）。DRAT 擴充驗證器的接受範圍，**允許 preprocessing/inprocessing 的 clause**（BVE、BCE、vivify）也能在 proof 裡表達。

**現代 solver 都輸出 DRAT**，因為它處理 preprocess 產生的 clause。SAT competition 也要求 DRAT，不要 DRUP。

## 怎麼產生 DRAT

改 Ch 16 的 v2 solver，每次 `add_clause(learned, true)` 時輸出：

```cpp
void Solver::emit_drat_add(const std::vector<Lit>& lits) {
    if (!drat_out) return;
    for (Lit l : lits) fprintf(drat_out, "%d ", l);
    fprintf(drat_out, "0\n");
}

void Solver::emit_drat_delete(const Clause& c) {
    if (!drat_out) return;
    fputs("d ", drat_out);
    for (Lit l : c.lits) fprintf(drat_out, "%d ", l);
    fprintf(drat_out, "0\n");
}
```

在 `analyze()` 結尾、`reduce_db()` 裡 delete clause、回 UNSAT 時加上呼叫。

UNSAT 時記得**輸出空 clause `0\n`** 標結束。

## drat-trim：官方驗證器

Heule 的驗證器，Ch 0 已安裝：

```bash
./sat-v2 --drat=proof.drat input.cnf
drat-trim input.cnf proof.drat
# output:
# c proof verified successfully
# s VERIFIED
```

drat-trim 做的事：

1. 讀原 CNF + proof file
2. 按 proof 順序，對每條 addition 驗證 RUP/RAT
3. 按 deletion 維護當前 CNF 集合
4. 最後確認有空 clause

**verifier 比 solver 簡單千倍**，可以被 formally verified。現在有 **verified DRAT checker**（由 Coq/Isabelle 形式化驗證）— 真正做到 solver bug-free。

## DRAT 的 binary format

文字 DRAT 占空間大（competition 的 proof 可達幾十 GB）。Binary format 壓縮：

- Literal 用 variable-length encoding（VLE）
- `0` terminator 變 single byte
- Delete line 前綴變 `d` byte

Binary DRAT 省 3–5× 空間。drat-trim 支援兩種。

## LRAT：帶 hint 的 proof format

**LRAT (Linear RAT)** - Cruz-Filipe 等人 2017。每條 proof line 加上「**哪些 clause 被用來 RUP/RAT**」的 hint：

```
5: -1 2 0 3 4 0      ← learned -1 2，用 clause 3 和 4 導
```

**驗證 LRAT 比 DRAT 快 100 倍**（不用搜索、直接 check）。工業化驗證（ACL2、Coq 認證的 verifier）用 LRAT。

Chain of tools:

```
solver → DRAT → drat-trim (生 LRAT hint) → verified-lrat-checker
```

這整個流程能產出 **機器驗證的 UNSAT 證明**。2016 的 **Pythagorean Triples problem** (Schur Number Five 類) 就是這樣確認的 — 200 TB proof、驗證器跑幾百 CPU-year。

## Clause Minimization 對 Proof 的影響

Ch 14 的 clause minimization 對 proof 友好：

- Minimization 用的 reason clauses 都在當前 CNF 裡
- 產 shorter clause → proof line shorter
- drat-trim 驗證更快

**不要為了 proof 關掉 minimization** — 它對 proof 大小是淨收益。

## 寫 Proof 的實務考量

1. **大小控制**：SAT competition 限制 proof 1 GB（binary），solver 學太多 clause 要權衡
2. **I/O 不要同步**：用 buffered writer、flush 頻率合理
3. **Early abort**：UNSAT 確定後立刻關 proof file
4. **Binary format**：production 用 binary，debug 用 text

## 動手練習

1. **v2 加 DRAT 輸出**：對 Ch 16 的 v2 加 `--drat=proof.drat` flag，每條 learned clause 輸出到檔。跑幾個 UNSAT instance、用 drat-trim 驗證應該 VERIFIED。
2. **故意做錯驗證**：改 solver 讓它跳過某條 learned clause 不加到 CNF、但 proof 輸出照舊。drat-trim 會抓到（因為 proof 對應的 clause 不能從目前 CNF RUP 推出）。這是最好的 solver debug 工具。
3. **對照 MiniSat 的 DRAT**：MiniSat 有 `-drup-file=...` flag（DRUP 是 DRAT 的子集），比較你 v2 的 proof size 和 MiniSat 的。差太多表示有 learning 策略問題。
4. **Pigeonhole 的 proof 爆炸**：對 `PHP_n^{n+1}` (n+1 鴿子 n 洞) 讓 solver 跑、觀察 proof size 跟 n 的關係。n = 10 的 proof 會 GB 級 — **這就是 resolution 指數下界的實物**。

## 常見誤解

- **「Proof 輸出會讓 solver 慢很多」** — 現代 binary DRAT 只讓 solver 慢 2–5%。**一定要開**，correctness > 微小效能損失。
- **「DRAT 只對 CDCL 有效」** — 錯。Preprocess 的 BVE、BCE、vivify 都能寫 DRAT（RAT rule 支援）。
- **「LRAT 會取代 DRAT」** — 短期不會。LRAT 需要 solver 或 trim 產 hint，DRAT 更靈活。現在普遍 solver 寫 DRAT、需要 formal verif 再 trim 成 LRAT。

## 自我檢核

- [ ] 懂為什麼 UNSAT 需要 proof
- [ ] 分得清 resolution proof、DRUP、DRAT、LRAT 的關係
- [ ] 會用 drat-trim 驗證 proof
- [ ] 知道怎麼在 CDCL 裡加 DRAT 輸出（add + delete）
- [ ] 理解 binary DRAT vs text DRAT 的差異
- [ ] 聽過 formally verified DRAT checker 的存在

Part 1 還剩兩章：並行 SAT（Ch 21）和兩個練習（B、C）。下一章看 **怎麼把 SAT solver 跑多核** — portfolio、clause sharing、cube-and-conquer。工業級 SAT 在超級電腦上跑百核不稀奇。

→ [Ch 21 — 並行 SAT：portfolio、clause sharing](./21-parallel-sat.md)
