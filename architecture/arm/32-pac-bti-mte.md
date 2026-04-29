# Ch 32 — ARM 硬體安全：PAC、BTI、MTE

> 目標：搞懂 ARMv8.3 起加的三大安全 ISA 擴展 — PAC（Pointer Authentication）、BTI（Branch Target Identification）、MTE（Memory Tagging Extension）。它們各打哪類攻擊、實作原理、為什麼 Apple Silicon 與 Pixel 8 都在用。

## 攻擊面 vs 防禦演進

```
攻擊年代   攻擊技術             防禦
──────────────────────────────────────────────
1990s      stack smashing       stack canary, NX bit, ASLR
2000s      ROP (return-oriented)  CFI 概念
2010s      JOP / heap overflow  CFI、stack cookies
2020s+     speculative attacks  PAC, BTI, MTE,
                                 Spectre mitigation
```

ARM 從 ARMv8.3 起每代加 ISA-level 防禦，**讓硬體幫忙抓**而非全靠軟體。

## PAC：Pointer Authentication

ARMv8.3-A 加的。**核心想法**：return address / function pointer 加上一個密碼學簽章，**改寫位址但簽章不變就能偵測**。

```
普通 64-bit pointer:
 ┌─ 16 bit unused ─┬─────── 48 bit address ────────┐
 │  (預設都 0)     │                               │
 └─────────────────┴────────────────────────────────┘

加 PAC 後:
 ┌──── 16 bit PAC ─┬─────── 48 bit address ────────┐
 │  簽章 (signature) │                              │
 └─────────────────┴────────────────────────────────┘
```

PAC 計算：`PAC = QARMA64(address, modifier, key)`，QARMA 是 ARM 設計的 lightweight cipher。

**寫 / 讀指令**：

```asm
pacia   x0, x1     ; x0 = sign(x0, modifier=x1, key=IA)
autia   x0, x1     ; x0 = verify+strip(x0, modifier=x1, key=IA)
                   ; 簽章不對 → 把 PAC bit 變成「invalid」
                   ; 後續 BR/RET 用會 fault
```

5 個 key：IA（instruction-A）、IB、DA（data-A）、DB、GA（generic）。各 OS 用不同 key set。

## PAC 怎麼防 ROP

正常 function：

```asm
foo:
    paciasp                ; sign LR using SP as modifier
    stp  x29, x30, [sp, #-16]!
    ; ... function body ...
    ldp  x29, x30, [sp], #16
    autiasp                ; verify+strip
    ret
```

ROP 攻擊覆蓋 LR：

1. 攻擊者把 `*(sp + 8)` 改成 gadget 位址
2. `LDP` 讀回 LR，但 PAC bit 是攻擊者隨便填的
3. `AUTIASP` 驗證 → 失敗 → LR 變成 invalid pattern
4. `RET` 跳到 invalid 位址 → 立刻 fault

**ROP 需要正確簽章才能 chain，但攻擊者拿不到 key**，整個攻擊鏈斷掉。

## PAC 的限制

- **key 在 secure register（APIAKey 等）**：要是 leak（透過 spectre 或硬體 attack）就掛
- **modifier 是猜得到的**（SP / 0），同一個 LR 在同 SP 下簽章一樣 — 限制了 randomness
- **48-bit VA + 16-bit PAC**：48-bit VA 已經是 256 TB，多數 process 用不到那麼多。如果 OS 啟用 52-bit VA（ARMv8.2 的 LVA），PAC bit 縮減到 7-bit，安全性下降

iOS 用 PAC 自 iPhone XS（A12，2018）起，後續 Apple Silicon 都標配。Linux ARM64 用 PAC 自 5.0 kernel。

## BTI：Branch Target Identification

ARMv8.5-A 加。**防 JOP（Jump-Oriented Programming）**。

JOP 是 ROP 的變種，攻擊者用 indirect branch（`br x0`）跳到 gadget。BTI 規定：

> **任何 `br`/`blr` 跳到的位址必須是「合法 branch target」**，否則 fault。

合法 branch target 用 `BTI` instruction 標記：

```asm
function_entry:
    bti  jc       ; 接 BR 與 BLR 都 OK
    ; ... function body ...
```

`BTI` 有 4 種變體：

| 變體 | 接受的 branch |
|---|---|
| `BTI` | 都不接（reset） |
| `BTI c` | BLR / 從 BR with X16/X17 |
| `BTI j` | BR |
| `BTI jc` | BR / BLR |

**未標記的位址被 indirect branch 跳到 → fault**。

實作：CPU 在 indirect branch 跳到目標時 check 第一條指令是不是 BTI（或 PAC return / page non-BTI 之類）。

## BTI 的代價

- 編譯器要在每個 function entry 加 BTI（增加 4 byte / function）
- ASLR + BTI 加上 PAC 是個好組合，但每次都要 retrofit 既有 code

GCC `-mbranch-protection=standard` 開 PAC + BTI，編譯器自動加 BTI 與 PACIA / AUTIA。Linux kernel 5.10+ 支援。

## MTE：Memory Tagging Extension

ARMv8.5-A。**抓 use-after-free、buffer overflow**。

原理：**每個 16-byte memory granule 與每個 pointer 各帶 4-bit tag**，存取時硬體比對：

```
pointer:    [4-bit tag][4 unused][48-bit addr]
memory:     [16-byte data] + [4-bit tag at side-array]

dereference *p:
  pointer.tag == memory.tag → OK
  不等                       → fault (or async report)
```

`MTE 4-bit = 16 種 tag`，碰撞率 1/16（夠用）。

## MTE 怎麼用

allocator 端（malloc、kalloc）：

```c
void *malloc(size_t n) {
    void *p = ...;                    // 分一塊
    uint8_t tag = random() & 0xF;     // 隨機 tag
    set_memory_tag(p, n, tag);         // STG 指令
    return p | (tag << 56);            // pointer 帶 tag
}

void free(void *p) {
    void *raw = p & 0x00FFFFFFFFFFFFFF;
    uint8_t old_tag = (p >> 56) & 0xF;
    uint8_t new_tag = (old_tag + 1) & 0xF;   // 改 tag
    set_memory_tag(raw, ?, new_tag);          // 寫新 tag
    // 之後若有人用 stale pointer (tag = old_tag) 存取 → fault
}
```

stale pointer 的舊 tag 對不上記憶體新 tag → fault。**use-after-free 立刻被抓**。

## MTE 的執行模式

- **Sync mode**：fault 立刻發生（精準但慢）
- **Async mode**：寫日誌，每段時間檢查（快但事後檢測）
- **Asymmetric**：load 用 sync、store 用 async

Pixel 8（首批 production user-mode MTE）用 Async；安全敏感應用切 Sync。

## ARM-only？x86 沒類似東西嗎？

x86 有 **Intel CET (Control Enforcement Technology)**：

- IBT (Indirect Branch Tracking) ≈ BTI
- Shadow Stack ≈ PAC (return 保護)

但 **PAC 比 Shadow Stack 設計優雅**：不用額外 stack、不用同步、純 ISA-level 簽章。MTE 在 x86 沒對應（Intel MPK 是 page key 不是 byte tag）。

ARM 在 hardware security 這幾年領先 x86。Apple、Google、Microsoft 都在大量採用。

## Pixel 8 與 MTE：第一個 production 廣泛採用

Pixel 8（2023）首批 default-on MTE 的消費級設備。Google 對 MTE 的故事：

- 內部 fuzzing infra 用 MTE catch 上千個 heap bug
- Hardened malloc 配 MTE，性能損失 < 5%
- production 開 Async mode，bug 出現時記日誌不影響功能

**MTE 是現代 memory safety 的硬體基石**。Apple、Microsoft 都在跟進。

## 我的程式要怎麼用？

GCC / Clang flags：

```bash
# PAC + BTI
-mbranch-protection=standard

# MTE (build with stack & heap tagging)
-fsanitize=memtag-stack -fsanitize=memtag-heap
```

但要 chip 與 OS 都支援 — 多數 production 還沒全鋪開。**寫新嵌入式韌體現在開啟 PAC + BTI 是無痛的**，幾乎零性能損失。

## 一個常見誤解

「PAC / BTI / MTE 是不是讓程式變超安全？」

**只是 raise the bar**。PAC 防 ROP / BTI 防 JOP / MTE 防 UAF，但攻擊者仍能：

- 用 data-only attack（不改 code flow）
- 用 spectre 推測 leak key
- 用 type confusion 走合法 path

**這些 ISA 防禦是 defense-in-depth 的一層**，不是萬能解。但每一層都讓 exploit 寫起來更難 → 實務攻擊成本提升。

## 自我檢核

- [ ] 我能解釋 PAC 怎麼防 ROP attack
- [ ] 我能說出 PACIASP / AUTIASP 在 function prologue/epilogue 的位置
- [ ] 我能解釋 BTI 與 PAC 的差異（前者防 JOP）
- [ ] 我能說出 MTE 如何抓 use-after-free
- [ ] 我能比較 MTE Sync 與 Async mode
- [ ] 我能說出 ARM 這套 vs x86 CET 的對應

下一章看 ARM 的 CPU bug 與 errata 史 — Cortex-A53 errata、Spectre 對 ARM 的影響、Apple A 系列 patch 故事。

→ [Ch 33 ARM 的 CPU bug 與 errata 史](./33-errata-and-cpu-bugs.md)
