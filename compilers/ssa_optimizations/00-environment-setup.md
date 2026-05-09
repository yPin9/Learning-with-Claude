# Ch 0 — 環境設置

> 目標：能編譯 LLVM、執行 `opt` 對 IR 跑 pass、以及載入自己寫的 out-of-tree pass。

## 需要什麼

```
LLVM 17+（建議用發行版套件，不要從源碼編譯，除非你有特定需求）
Clang（同版本）
cmake 3.20+
Python 3（用來跑 lit 測試）
Alive2（可選，Ch 32 才會用到）
```

## 安裝 LLVM

### Ubuntu / Debian

```bash
# 加 LLVM 官方 apt repo（以 LLVM 17 為例）
wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
sudo ./llvm.sh 17

# 安裝開發用套件
sudo apt install llvm-17 llvm-17-dev clang-17 libclang-17-dev

# 讓工具指向 17
sudo update-alternatives --install /usr/bin/llvm-config llvm-config /usr/bin/llvm-config-17 100
sudo update-alternatives --install /usr/bin/opt opt /usr/bin/opt-17 100
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-17 100
```

### Arch / Manjaro

```bash
sudo pacman -S llvm clang
```

### macOS

```bash
brew install llvm
echo 'export PATH="/opt/homebrew/opt/llvm/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### 確認安裝

```bash
llvm-config --version    # 應該輸出 17.x.x
opt --version
clang --version
```

## 第一個 `opt` 指令

`opt` 是 LLVM 的 pass runner，這門課你會一直用它。

```bash
# 寫一個簡單的 C 程式
cat > /tmp/test.c << 'EOF'
int add(int a, int b) {
    return a + b;
}
EOF

# 編譯成 LLVM IR（human-readable .ll 格式）
clang -O0 -S -emit-llvm /tmp/test.c -o /tmp/test.ll

# 看一下 IR 長什麼樣
cat /tmp/test.ll

# 跑 mem2reg pass（構造 SSA）
opt -S -passes=mem2reg /tmp/test.ll -o /tmp/test_opt.ll
diff /tmp/test.ll /tmp/test_opt.ll
```

`-O0` 生成的 IR 會用 `alloca` 代替暫存器（所有變數都是記憶體存取），`mem2reg` pass 會把它轉成 SSA 形式——這正是 Ch 1–5 要講的事情。

## 建立 Out-of-Tree Pass 專案

這門課的每個實作章節都會寫一個 LLVM pass。用這個模板開始：

```bash
mkdir -p ~/ssa_passes && cd ~/ssa_passes
```

目錄結構：

```
~/ssa_passes/
├── CMakeLists.txt
├── HelloPass/
│   ├── CMakeLists.txt
│   └── HelloPass.cpp
└── build/
```

**CMakeLists.txt（根目錄）**

```cmake
cmake_minimum_required(VERSION 3.20)
project(SSAPasses)

find_package(LLVM REQUIRED CONFIG)
message(STATUS "Found LLVM ${LLVM_PACKAGE_VERSION}")

list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
include(AddLLVM)

add_definitions(${LLVM_DEFINITIONS})
include_directories(${LLVM_INCLUDE_DIRS})

add_subdirectory(HelloPass)
```

**HelloPass/CMakeLists.txt**

```cmake
add_llvm_pass_plugin(HelloPass HelloPass.cpp)
```

**HelloPass/HelloPass.cpp**

```cpp
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

struct HelloPass : PassInfoMixin<HelloPass> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &) {
        errs() << "Hello from function: " << F.getName() << "\n";
        return PreservedAnalyses::all();
    }
};

llvm::PassPluginLibraryInfo getHelloPassPluginInfo() {
    return {LLVM_PLUGIN_API_VERSION, "HelloPass", LLVM_VERSION_STRING,
            [](PassBuilder &PB) {
                PB.registerPipelineParsingCallback(
                    [](StringRef Name, FunctionPassManager &FPM,
                       ArrayRef<PassBuilder::PipelineElement>) {
                        if (Name == "hello") {
                            FPM.addPass(HelloPass());
                            return true;
                        }
                        return false;
                    });
            }};
}

extern "C" LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo
llvmGetPassPluginInfo() {
    return getHelloPassPluginInfo();
}
```

**編譯並執行**

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)

opt -load-pass-plugin ./build/HelloPass/HelloPass.so \
    -passes="hello" -S /tmp/test.ll -o /dev/null
# 輸出：Hello from function: add
```

## 常用的 `opt` 指令備忘

```bash
# 列出所有可用的 pass
opt --print-passes | less

# 跑多個 pass
opt -S -passes="mem2reg,dce" input.ll -o output.ll

# 每個 pass 執行後印出 IR（除錯用）
opt -S -passes="mem2reg,dce" --print-after-all input.ll -o /dev/null

# 查看支配樹分析結果
opt -passes="print<domtree>" input.ll -o /dev/null 2>&1

# 查看 loop 分析結果
opt -passes="print<loops>" input.ll -o /dev/null 2>&1
```

## 自我檢核

- [ ] `opt --version` 輸出 LLVM 17+
- [ ] 能把 C 程式編譯成 `.ll` 並用 `opt -passes=mem2reg` 轉換
- [ ] 能編譯 HelloPass 並用 `-load-pass-plugin` 載入執行

→ [Ch 1 為什麼需要 SSA](./01-why-ssa.md)
