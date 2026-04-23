# 練習 A — 讀懂一個 LLVM pass

> 目標：挑一個真實的 LLVM pass，從頭到尾讀懂它 — 做什麼、怎麼做、為什麼這樣設計。讀 source 是 backend 工程師最基本的技能，本練習讓你練熟。

## 為什麼做這個

新手的陷阱：「讀 LLVM 像考古」。太多 class、template、macro，不知從哪下手。

這個練習給你一個**可管理的 scope**：一個 pass、幾百到一千行、完整讀透。做完一次你有讀 LLVM 的信心。

## 選擇哪個 pass

難度排序，從易到難：

### 初級（300-500 行）

- **`RISCVRedundantCopyElimination.cpp`**：消除 redundant COPY 指令
- **`RISCVSExtWRemoval.cpp`**：移除不必要的 sign extend
- **`RISCVMergeBaseOffset.cpp`**：合併 address 計算

### 中級（500-1500 行）

- **`RISCVExpandPseudoInsts.cpp`**：展開 pseudo instruction
- **`RISCVMakeCompressible.cpp`**：relaxation-related
- **`MachineCSE.cpp`**：Machine-level CSE（generic）
- **`BranchFolding.cpp`**：branch 折疊（generic）

### 進階（2000-5000 行）

- **`RISCVInsertVSETVLI.cpp`**：VSETVL insertion（RVV 靈魂）
- **`RegAllocGreedy.cpp`**：greedy register allocator
- **`InstCombine*.cpp`**：IR-level peephole

**建議第一次選初級**。完成後再挑戰中級。

## 範例選擇：`RISCVRedundantCopyElimination.cpp`

我用這個當 walkthrough 範例。你可以跟著讀或選別的。

### Step 1：找 file 跟 understand context

```bash
find llvm -name "RISCVRedundantCopyElimination.cpp"
# llvm/lib/Target/RISCV/RISCVRedundantCopyElimination.cpp
```

先**不讀**實作。讀 file 開頭的 comment：

```cpp
//===-- RISCVRedundantCopyElimination.cpp - ...
// This pass removes unnecessary copy instructions after comparison
// instructions (e.g., BEQ, BNE, etc.).
//...
```

知道它處理**比較指令後的多餘 copy**。

### Step 2：找例子 input / output

搜尋 test：

```bash
grep -r "redundant-copy-elimination" llvm/test/
# llvm/test/CodeGen/RISCV/redundant-copy-elim.ll
```

讀 test 檔，看 "before pass" 跟 "after pass" MIR 對比：

```
Before:
  %1 = COPY $x0              ; copy zero to %1
  BEQ %1, %2, bb.else        ; 之後比較 ... 多餘

After:
  BEQ $x0, %2, bb.else       ; 直接用 $x0
```

**pass 的 job 清晰**：把 `%1 = COPY $x0; BEQ %1, ...` 合併成 `BEQ $x0, ...`。

### Step 3：讀 pass 的 main 入口

```cpp
bool RISCVRedundantCopyElimination::runOnMachineFunction(MachineFunction &MF) {
    ...
    bool Changed = false;
    for (MachineBasicBlock &MBB : MF) {
        Changed |= optimizeBlock(MBB);
    }
    return Changed;
}
```

**入口都是這個樣**：iterate 所有 MBB、對每個 MBB 呼叫 optimize function。

### Step 4：讀 optimizeBlock

```cpp
bool RISCVRedundantCopyElimination::optimizeBlock(MachineBasicBlock &MBB) {
    // Find the last instruction of MBB
    auto TermIt = MBB.getFirstTerminator();
    ...
    // 檢查是否是 BEQ/BNE
    unsigned Opcode = TermIt->getOpcode();
    if (Opcode != RISCV::BEQ && Opcode != RISCV::BNE)
        return false;
    ...
```

**pattern**：先檢查 MBB 結尾是不是我們關心的指令。不是就早 return。

### Step 5：找實際 optimization

```cpp
    // 找 COPY 指令往前
    MachineBasicBlock::iterator CopyIt = ...;
    for (auto It = std::prev(TermIt); It != MBB.begin(); --It) {
        if (It->getOpcode() == TargetOpcode::COPY &&
            It->getOperand(1).getReg() == RISCV::X0) {
            CopyIt = It;
            break;
        }
    }

    // 檢查 BEQ 是不是用這個 COPY 的 output
    if (!isCopyUsedByTerm(CopyIt, TermIt))
        return false;

    // 替換
    TermIt->getOperand(0).setReg(RISCV::X0);
    CopyIt->eraseFromParent();
    return true;
}
```

**三步**：

1. 找候選 COPY（source = X0）
2. 驗證關聯
3. 替換 + 刪

### Step 6：理解 corner case

pass 在 comment / code 處理的 corner：

- COPY 跟 BEQ 中間有沒有其他 MI 改過 register？
- COPY 的 dest 有其他 use 嗎？
- X0 在 RV32 跟 RV64 都 behavior 相同嗎？

**每個 corner 對應 code 裡的 `if (...) return false`**。讀這些 check 最花時間但最長見識。

### Step 7：驗證你的理解

自己講解一次：

- Pass 做什麼？
- 運作條件？
- 為什麼這個 optimization 有用？
- 什麼情境不該做？

能清楚講 = 你懂了。

## 完整 walkthrough 練習

做這個 pass 的過程：

```
Day 1: 找 file, 讀 comment + test, 寫一頁筆記「pass 做什麼」
Day 2: 讀 main function 跟主 optimize function, 寫筆記「演算法」
Day 3: 讀 corner case handling, 寫筆記「哪些情境跳過」
Day 4: 寫 pseudocode / flowchart 表達整個 pass
Day 5: 寫 10 題自問自答驗證理解
```

5 天一個 pass。完成 3 個後你有感覺。

## 進階：寫 blog / gist

真正學會的 test：**能教別人**。寫一篇 blog 或 gist 解釋這個 pass。

好處：

- 強迫自己 explanation 精準
- 教別人 = 學兩次
- blog 放履歷：SiFive recruiter 看到「深度理解 LLVM pass」大加分

## 用 debugger 追 pass

Debug build + gdb：

```bash
gdb ./bin/llc
(gdb) b RISCVRedundantCopyElimination::runOnMachineFunction
(gdb) r -march=riscv64 hello.ll
# 進 break, 開始 step
```

看 runtime 實際執行什麼 branch、什麼 MI 被 match。比乾讀 source 高 10 倍 insight。

## 用 print statement

不想 gdb 可以加 print：

```cpp
bool RISCVRedundantCopyElimination::optimizeBlock(MachineBasicBlock &MBB) {
    errs() << "[MYPASS] Enter block: " << MBB.getName() << "\n";
    ...
    errs() << "[MYPASS] Found copy: "; CopyIt->dump();
    ...
}
```

rebuild + run → 看 output。比 debugger 簡單但也 noisy。

## 讀法的反 pattern

不要做：

1. **逐字逐句讀**：太慢。scope skim 後找 interesting 部分細讀。
2. **試圖懂每個 class**：LLVM 有幾千 class、多數你不用 worry。focus 在這個 pass 用到的。
3. **不寫筆記**：讀完忘光。
4. **只讀不跑**：沒看實際 input/output 很難 grok。

## 讀完一個 pass 該有的 artifact

1. **flowchart / pseudocode**：表達 pass 的演算法
2. **3-5 個 before/after MIR example**：體現 pass 做什麼
3. **corner case 列表**：pass 為什麼跳過這些 case
4. **"如果我要 improve 這個 pass, 我會..."**：verify 你懂夠深

## 寫自己的 mini pass

讀一個 pass 後試著寫一個 similar 但 trivial 的：

```cpp
class MyPass : public MachineFunctionPass {
public:
    static char ID;
    MyPass() : MachineFunctionPass(ID) {}

    bool runOnMachineFunction(MachineFunction &MF) override {
        bool Changed = false;
        for (auto &MBB : MF) {
            for (auto &MI : MBB) {
                if (...) {
                    // do something trivial
                    Changed = true;
                }
            }
        }
        return Changed;
    }
};
```

plug 進 pipeline、run、看有沒有改到東西。**能寫出來 = 真的懂 backend pass 寫法**。

## 推薦讀的 pass 清單

我挑的 5 個最值得讀的 pass：

1. **RISCVRedundantCopyElimination.cpp** - 最簡單 start
2. **RISCVExpandPseudoInsts.cpp** - 看 pseudo expansion
3. **MachineCSE.cpp** - 看 CSE 實作（generic）
4. **RISCVInsertVSETVLI.cpp** - 最重要的 RVV pass
5. **RegAllocGreedy.cpp** - register allocation 核心

做完這 5 個你是 backend 的半個 expert。

## 自我檢核

- [ ] 我選了一個 pass 並讀完
- [ ] 我寫了 pseudocode / flowchart 表達它的演算法
- [ ] 我能用自己的話解釋它處理的 corner case
- [ ] 我能在 gdb / 加 print 追蹤它的 runtime 行為
- [ ] 我寫了一篇 blog / gist 解釋這個 pass（optional 但值得）

## 下一步

→ [練習 B：加一條 pseudo-instruction](./practice-b-add-pseudo-instruction.md)
→ [Final Project：加一個 custom extension 端到端](./final-project-add-extension.md)
