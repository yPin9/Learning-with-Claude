# Ch 0 — 環境搭建

> 目標：把 C++20 toolchain、三個 SAT solver、兩個 SMT solver、一個 DRAT 驗證器裝好。用 CLI 讓 MiniSat 解一題 3-SAT、讓 Z3 解一題 QF_LRA，確認整條工具鏈能用。

## 這一章要裝什麼，為什麼

你接下來會用到的工具分三類：

```
            你的 C++20 code
                  │
   ┌──────────────┼──────────────┐
   │              │              │
 編譯建置       參照比對         驗證
   │              │              │
 g++ / clang   MiniSat        drat-trim
 CMake         CaDiCaL        （驗 UNSAT 證明）
 Ninja         Glucose
               Z3 / cvc5
               （SMT）
```

- **編譯建置**：寫 C++20 就靠它們。
- **參照比對**：每寫完一版 solver，拿 MiniSat / CaDiCaL 跑同樣 benchmark，看差幾倍。這是整套教材的核心練法。
- **驗證**：你的 solver 說 UNSAT 時，用 drat-trim 檢查證明對不對。Ch 20 會用。

這些工具 **都是 Linux 生態**。原生 Windows 能裝，但你會花 80% 時間跟 Visual Studio 的編譯錯誤搏鬥，SAT competition 的 benchmark script 也全是 bash。我們走 WSL2。

## Step 1：WSL2 + Ubuntu

PowerShell 用管理員權限打開：

```powershell
wsl --install -d Ubuntu
```

重開機後它會自動進 Ubuntu 設帳號。之後**所有指令都在 WSL 的 bash 裡跑**，不是 Windows PowerShell。

驗證：

```bash
uname -a
# Linux ... x86_64 GNU/Linux
cat /etc/os-release | grep VERSION
# VERSION="22.04.x LTS (Jammy Jellyfish)"  或 24.04
```

本章以 Ubuntu 22.04 為主。24.04 指令幾乎一樣。

## Step 2：C++20 toolchain

Ubuntu 22.04 預設 gcc-11，C++20 支援不完整（`std::format` 要 gcc-13+）。升級：

```bash
sudo apt update
sudo apt install -y build-essential
sudo add-apt-repository -y ppa:ubuntu-toolchain-r/test
sudo apt update
sudo apt install -y gcc-13 g++-13
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-13 100
sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-13 100
```

驗證：

```bash
g++ --version
# g++ (Ubuntu 13.x.x-...) 13.x.x
```

跑一個 C++20 feature test：

```cpp
// test-cpp20.cpp
#include <format>
#include <iostream>
int main() {
    std::cout << std::format("C++20 OK, answer = {}\n", 42);
}
```

```bash
g++ -std=c++20 test-cpp20.cpp -o test-cpp20 && ./test-cpp20
# C++20 OK, answer = 42
```

印出來就通了。

## Step 3：CMake 與 Ninja

```bash
sudo apt install -y cmake ninja-build
cmake --version   # 3.22+
ninja --version   # 1.10+
```

本系列所有 C++ 專案用 **CMake + Ninja**。`make` 也可以，但 Ninja 快 3–5 倍、錯誤訊息乾淨、你會謝謝自己。

## Step 4：SAT solvers

裝三個。MiniSat 有 deb：

```bash
sudo apt install -y minisat
```

驗證：

```bash
echo "p cnf 3 2
1 -2 3 0
-1 2 0" > test.cnf
minisat test.cnf
# 最後一行：SATISFIABLE
```

CaDiCaL、Glucose 沒 deb，從 source build：

```bash
# CaDiCaL
cd ~ && git clone https://github.com/arminbiere/cadical.git
cd cadical && ./configure && make -j
sudo cp build/cadical /usr/local/bin/

# Glucose
cd ~ && git clone https://github.com/audemard/glucose.git
cd glucose/simp && make -j
sudo cp glucose /usr/local/bin/
```

驗證兩個都能吃剛剛的 `test.cnf`：

```bash
cadical test.cnf | grep "^s "
glucose test.cnf | grep "^s "
# 都要看到 s SATISFIABLE
```

## Step 5：SMT solvers

Z3 跟 cvc5 直接抓官方 release binary，不要從 source build（Z3 從 source 要 10 分鐘）。去 Releases 頁抓最新版的 Linux x64 zip：

```bash
# Z3，版本號對不上就去 https://github.com/Z3Prover/z3/releases 找新的
cd /tmp
Z3_VER=4.13.0
wget https://github.com/Z3Prover/z3/releases/download/z3-${Z3_VER}/z3-${Z3_VER}-x64-glibc-2.35.zip
unzip z3-${Z3_VER}-x64-glibc-2.35.zip
sudo cp z3-${Z3_VER}-x64-glibc-2.35/bin/z3 /usr/local/bin/

# cvc5，類似邏輯
CVC5_VER=1.1.2
wget https://github.com/cvc5/cvc5/releases/download/cvc5-${CVC5_VER}/cvc5-Linux-static.zip
unzip cvc5-Linux-static.zip
sudo cp cvc5-Linux-static/bin/cvc5 /usr/local/bin/
```

驗證：

```bash
z3 --version    # Z3 version 4.13.x
cvc5 --version  # cvc5 version 1.1.x
```

## Step 6：drat-trim

```bash
cd ~ && git clone https://github.com/marijnheule/drat-trim.git
cd drat-trim && make
sudo cp drat-trim /usr/local/bin/
```

Ch 20 才真正會用，現在先裝起來。

## Hello SAT：3-SAT 小問題

開 work 目錄：

```bash
mkdir -p ~/sat-smt/hello && cd ~/sat-smt/hello
```

手寫一個 CNF：`(x1 ∨ x2 ∨ x3) ∧ (¬x1 ∨ x2) ∧ (¬x2 ∨ x3) ∧ (¬x3)`：

```bash
cat > hello.cnf <<'EOF'
p cnf 3 4
1 2 3 0
-1 2 0
-2 3 0
-3 0
EOF
```

DIMACS 格式解讀：

- `p cnf 3 4` — header：3 個變數、4 條 clause
- 每行一條 clause，`0` 結尾
- 正數 `k` 代表 `xk`，負數 `-k` 代表 `¬xk`

跑三個 solver：

```bash
minisat hello.cnf   | tail -1
cadical hello.cnf   | grep "^s "
glucose hello.cnf   | grep "^s "
```

三個都回 **UNSAT**。你腦袋推一次：

1. `¬x3` 逼 `x3 = 0`
2. `¬x2 ∨ x3` 加 `x3 = 0`，逼 `x2 = 0`
3. `¬x1 ∨ x2` 加 `x2 = 0`，逼 `x1 = 0`
4. `x1 ∨ x2 ∨ x3` 要求至少一個為 1 — 矛盾

剛剛這個推論過程叫 **unit propagation + conflict**。Ch 10 的主戲，你已經用腦袋跑了一次。

## Hello SMT：QF_LRA 小問題

```bash
cat > hello.smt2 <<'EOF'
(set-logic QF_LRA)
(declare-const x Real)
(declare-const y Real)
(assert (> x 0))
(assert (< y 0))
(assert (= (+ x y) 1))
(check-sat)
(get-model)
EOF

z3 hello.smt2
cvc5 hello.smt2
```

兩個 solver 都會吐：

```
sat
(
  (define-fun x () Real ...)
  (define-fun y () Real ...)
)
```

某個正 `x`、某個負 `y`、相加等於 1。SMT 比 SAT 強的地方就在這：它**推理實數**，不只是 true/false。

## 故意做錯：把 SMT 改到無解

上面那題改一下 — `x > 0`、`y > 0`，但要求 `x + y < 0`：

```bash
cat > impossible.smt2 <<'EOF'
(set-logic QF_LRA)
(declare-const x Real)
(declare-const y Real)
(assert (> x 0))
(assert (> y 0))
(assert (< (+ x y) 0))
(check-sat)
EOF

z3 impossible.smt2
# unsat
```

兩正數相加不可能小於 0，solver 秒回 `unsat`。

**把這流程內化**：寫 SMT 時，先寫你覺得「應該有解」的 constraints 跑一次、再寫你覺得「應該無解」的跑一次。兩次都跟你直覺對上，才往下走。這是後面所有章節裡會做幾百次的事 — SMT solver 是你驗證邏輯直覺的工具，別只拿它跑最後答案。

## 編輯器

用什麼都行，建議：

- **VS Code + Remote-WSL**：Windows 下最順，直接開 WSL 裡的專案
- **CLion**：商業，CMake 整合最好
- **Neovim + clangd**：硬派選擇

重點是 **clangd** 要接起來。後面幾千行的 solver 沒 language server 會痛苦。

## 常見問題

**`wsl --install` 卡住或失敗** — 確認 Windows 版本 ≥ 10 build 19041。公司電腦可能被 group policy 鎖 Hyper-V，要 IT 放行。

**apt 裝 gcc-13 找不到 package** — 漏了 `add-apt-repository ppa:ubuntu-toolchain-r/test` 那步，或 `apt update` 沒跑。

**MiniSat segfault** — MiniSat 2.2 在 empty clause 上有老 bug，忽略即可。後面我們只用它的 CLI，不 link 它的 library。

**`unzip` 找不到** — `sudo apt install -y unzip`。

## 自我檢核

- [ ] `g++ --version` 顯示 13.x
- [ ] `minisat`、`cadical`、`glucose` 都能跑 DIMACS 並回 SATISFIABLE / UNSATISFIABLE
- [ ] `z3` 和 `cvc5` 都能解 SMT-LIB v2
- [ ] 讀得懂基本 DIMACS（`p cnf n m`、正負 literal、`0` 結尾）
- [ ] 讀得懂基本 SMT-LIB v2（`declare-const`、`assert`、`check-sat`、`get-model`）

環境齊了，下一章拉高視角 — 把 SAT/SMT 放到整個 CS 地圖上看它在哪裡、憑什麼值得學一整本書。

→ [Ch 1 — 為什麼學 SAT/SMT：全景圖](./01-overview.md)
