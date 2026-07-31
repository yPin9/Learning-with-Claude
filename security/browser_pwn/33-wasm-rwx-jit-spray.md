# Ch 33 — WebAssembly RWX / JIT spray 與消亡史

> **目標**：徹底理解曾經的 code-exec 首選——**WebAssembly RWX 頁**與更古老的 **JIT spray**，它們怎麼運作、為什麼一步就能拿 shell、以及 **W^X 與 code 間接化怎麼把它們送進墳墓**。這是一堂「反面教材課」：看懂一個好用的招怎麼被殺死，你就懂了現代 code-exec 難在哪。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`。WASM RWX 手法在現代 V8 已大幅失效，觸發碼為歷史分析；W^X 現況可側面驗證。

## 為什麼需要這個？

上一章說「WASM RWX 這招死了」。但你不能只知道它死了——你要知道**它為什麼曾經無敵、又為什麼被殺**，因為：(1) 舊 writeup 滿地都是這招，你得看懂它們在幹嘛、以及為什麼不能照抄；(2) 理解它的死法，就是理解 W^X、code pointer sandboxing 這些現代防禦的精髓；(3) WASM 作為原語來源在 sandbox 時代仍有殘餘價值，你得知道哪部分還活著。

## 先建立直覺：一塊「能寫又能跑」的記憶體

CPU 執行的是機器碼。要執行你的 shellcode，你需要一塊記憶體**同時**滿足兩個條件：你能**寫**進去（放 shellcode）、CPU 能從那裡**執行**。這種「可寫 + 可執行」的記憶體叫 **RWX**。

現代作業系統的鐵律是 **W^X（Write XOR eXecute）**：一塊記憶體要嘛可寫、要嘛可執行，**不能兩者兼具**。這是為了擋掉「寫入 shellcode 然後執行」這種最直接的攻擊。

但 JIT 編譯器有個尷尬的需求：它**執行期產生機器碼**——需要先「寫」進一塊記憶體（產生 code），再「執行」它。早期的 JIT 圖方便，直接把 code 放在 RWX 頁——**這就是攻擊者的後門**。WebAssembly 的 JIT 曾經就是這樣。

## WASM RWX：一步登天的黃金招

> 歷史手法，針對當年 vulnerable/無 sandbox 的 V8。

WebAssembly 是瀏覽器裡的「近原生」執行環境，它的 code 由 V8 JIT 編成機器碼。早期 V8 把 WASM 的 JIT code 放在一塊 **RWX** 頁。利用鏈短得離譜：

```
1. 建一個最小的 WASM module 並實例化
   → V8 配置一塊 RWX 頁，放進這個 module 編好的機器碼
2. 用任意讀（Part 3）掃到那塊 RWX 頁的位址
   （通常從 WASM instance 物件裡的某個欄位找到）
3. 用任意寫把你的 shellcode 覆蓋到 RWX 頁上
   （反正它可寫）
4. 呼叫該 WASM module 匯出的函式
   → CPU 跳進 RWX 頁執行 = 執行你的 shellcode = shell
```

為什麼這麼香：**RWX 頁同時可寫（步驟 3）又可執行（步驟 4）**，完美繞過 W^X——因為 V8 自己違反了 W^X。你甚至不用煩惱 ROP、不用找 gadget，直接放原始 shellcode。

## JIT spray：更古老的表親

WASM RWX 之前還有更古老的 **JIT spray**（源自 Flash/JS JIT 時代）。概念：你沒有現成的 RWX 頁位址，但你能**誘導 JIT 產生內含你想要的位元組的 code**。

經典手法：寫一堆 JS 常數運算，例如 `var x = 0x3c909090 ^ 0x3c909090 ^ ...`。JIT 會把這些常數**原封不動編進機器碼**。你精心挑選常數，讓它們在機器碼裡「剛好」也是一段有效的 shellcode（利用 x86 變長指令，從常數中間跳進去解讀成不同指令）。然後想辦法跳進這塊 JIT code 的中間。

JIT spray 的精髓：**把 shellcode 藏在「合法的 JIT 產物」裡**，繞過「你不能自己配置可執行記憶體」的限制——因為 JIT code 本來就是可執行的。

## 消亡史：W^X 與 code 間接化怎麼殺死它們

這些招現在大多死了，死因值得逐條記：

### 死因一：W^X 強制化（RWX → RW then RX）

現代 V8（和作業系統）不再給 RWX 頁。JIT 產生 code 的流程改成：先在一塊 **RW** 頁寫 code，寫完後**把頁權限改成 RX（唯讀可執行）**，再執行。於是：

- 步驟 3（你覆寫 shellcode）：這時頁是 RX，**不可寫**——你的任意寫被頁權限擋下。
- 就算你在它還是 RW 時搶著寫，V8 有一致性檢查、且時間窗極小。

RWX 頁沒了，「一塊能寫又能跑的記憶體」這個前提消失，WASM RWX 直接崩。

### 死因二：常數 blinding（殺 JIT spray）

針對 JIT spray，JIT 加了 **constant blinding（常數盲化）**：把你寫的常數 `0x3c909090` 先 XOR 一個隨機 cookie 存，用的時候再 XOR 回來。於是你精心設計的「常數即 shellcode」在 code 裡變成被 XOR 過的亂碼，跳進去不再是你的指令。

### 死因三：W^X 硬體化與 code pointer sandboxing

V8 Sandbox 時代進一步：code pointer 移出 cage（[Ch 32](./32-arbitrary-rw-to-code-exec.md)），你 cage 內的任意寫連「指向 code 頁的指標」都改不到。加上 CET（[Ch 36](./36-cfi-cet-data-only.md)）的間接分支保護，「跳進一塊 JIT 頁的中間」也被 endbranch 檢查擋。

## WASM 在 sandbox 時代還剩什麼

WASM 沒有完全失去價值，只是「一步登天」沒了：

- **WASM instance/memory 仍是可控的原語來源**：WASM 的線性記憶體、函式表（table）在某些構造下仍能幫你做 leak、控制資料佈局。
- **WASM 相關的 type confusion / 記憶體 bug**：WASM 引擎本身（編譯器、bounds check、type check）是獨立攻擊面，出過真實 bug。
- **作為 code-exec 的一環而非全部**：現代 exploit 可能仍用 WASM 當其中一塊拼圖，但要配合 sandbox escape 或 data-only，不再是終點。

## 對比：三個時代的 code-exec 招

| 時代 | 招 | 為什麼有效 | 死因 |
|---|---|---|---|
| Flash/早期 JS | JIT spray | 常數被編進可執行 JIT code | constant blinding |
| 2016–2019 | WASM RWX 頁 | JIT code 放在 RWX 頁 | W^X（RW→RX） |
| sandbox 時代 | （都不行）→ 走 data-only / sandbox escape | — | code 間接化 + CET |

這張表就是一部「code-exec 防禦演進史」，和 [Ch 1](./01-why-renderer-attack-surface.md) 的 NX→ASLR→CET 是同一個劇本的瀏覽器版。

## 踩雷集錦

1. **照抄舊 exploit 的 WASM RWX 段**：現代 V8 沒有 RWX 頁，這段會直接失敗。看到 `rwx` / `wasm` 一步 code exec 的碼，先確認它的年代和目標 V8 版本。
2. **以為 W^X 只是 OS 的頁權限**：V8 自己也管理 code space 的權限轉換（RW→RX），並有一致性檢查。不是單純靠 `mprotect`。
3. **以為 JIT spray 還能用**：constant blinding 幾乎讓它絕跡。除非目標關掉 blinding（罕見），別指望。
4. **以為 WASM 完全沒用了**：它作為原語來源和獨立攻擊面仍活著，只是「RWX 一步 shell」死了。別把「一招死了」誤解成「WASM 無關了」。
5. **忽略「搶 RW 窗口」的誘惑其實極難**：有人想在 code 頁「還是 RW」的瞬間搶寫。時間窗極小、有一致性檢查、且併發下不可靠。別把它當可行主線。

## 進階：再往深一層

- **V8 的 code space 權限管理**：追 `src/heap/` 與 code space 相關的權限轉換（`SetPermissions`、write scope），看 RW→RX 怎麼實作、有沒有可乘的時間窗。
- **WASM 的 fast-path 記憶體**：WASM 線性記憶體的邊界檢查（guard pages、bounds check）是獨立主題，也是攻擊面。
- **Apple 的 APRR / JIT hardening**：其他引擎（JSC on Apple Silicon）用更硬體化的 JIT 記憶體保護（per-thread 權限），是「W^X 硬體化」的極端案例，值得對照理解 V8 的取捨。
- **constant blinding 的漏網**：不是所有常數都會被 blind（小常數、某些場景可能不 blind），研究「哪些常數逃過 blinding」是理解 JIT spray 殘餘可能性的角度。

## 動手練習

1. 讀一篇 2018–2019 的 V8 WASM RWX exploit（延伸閱讀），逐步標出它的四步（建 WASM → 找 RWX 頁 → 覆寫 shellcode → 呼叫），然後對每一步寫下「現代被什麼防禦擋住」。
2. 在現行 d8 建一個最小 WASM module 並實例化（`new WebAssembly.Instance(new WebAssembly.Module(bytes))`），用 `%DebugPrint` 看 instance 物件——思考當年攻擊者從哪個欄位找 code 頁位址（現在該指標可能已間接化）。
3. 思考題：constant blinding 為什麼能同時擋掉「常數即 shellcode」卻不影響正常程式效能太多？（提示：一個 XOR 的成本 vs 一次記憶體存取。）

## 本章重點整理

- code exec 需要「可寫又可執行」的記憶體；**W^X** 鐵律禁止 RWX，但早期 JIT 為方便違反了它。
- **WASM RWX 頁**：早期 WASM JIT code 放 RWX 頁，任意寫覆蓋 shellcode + 呼叫 = 一步 code exec。
- **JIT spray**：把 shellcode 藏進 JIT 會原封編入的常數裡，繞過「不能自配可執行記憶體」。
- 死因：**W^X 強制化**（RW→RX，殺 WASM RWX）、**constant blinding**（殺 JIT spray）、**code 間接化 + CET**（sandbox 時代封死）。
- WASM 仍是原語來源與獨立攻擊面，只是「一步登天」的年代結束。

## 自我檢核

- [ ] 能解釋 W^X 是什麼、為什麼 JIT 的需求和它衝突
- [ ] 能複述 WASM RWX 的四步，以及每步現在被什麼擋
- [ ] 能解釋 JIT spray 的核心詭計，以及 constant blinding 怎麼破它
- [ ] 知道 WASM 在 sandbox 時代還剩哪些價值
- [ ] 面試被問「為什麼現代瀏覽器不能直接寫 shellcode 執行」，能用 W^X + code 間接化回答

## 延伸閱讀

- **[各家 V8 WASM RWX exploit writeup（doar-e / saelo / 2018–2019 CTF writeup）](https://doar-e.github.io/)**
  - **這篇說什麼**：黃金時代「任意寫 → WASM RWX → shell」的完整示範。
  - **讀哪裡**：code-exec 段落。對照本章的死因，理解為什麼不能照抄。

- **[“Attacking Client-Side JIT Compilers” — Chris Rohlf & Yan Ivnitskiy（JIT spray 經典）](https://www.matasano.com/research/AttackingClientSideJITCompilers_Paper.pdf)**
  - **這篇說什麼**：JIT spray 與 constant blinding 的原始論述。
  - **和本章的關聯**：本章 JIT spray 段落的一手來源，含防禦設計動機。

- **[V8 code space / W^X 相關 v8.dev 貼文與 `src/heap/` 原始碼](https://v8.dev/blog)**
  - **讀哪裡**：JIT code 記憶體管理與權限轉換的說明。
  - **和本章的關聯**：看 RW→RX 在現代 V8 怎麼落實，理解 WASM RWX 的死因一。

經典 code-exec 招的興亡看完，該正面解剖那個殺死它們、也定義了現代 V8 利用難度的東西了。下一章專章拆 **V8 Sandbox**。

→ [Ch 34 — V8 Sandbox：ubercage / external pointer table](./34-v8-sandbox.md)
