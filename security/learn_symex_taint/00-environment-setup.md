# Ch 0 — 環境搭建

> 目標：把 Z3、angr、KLEE、Triton 全裝齊，各跑一個 hello 例子。確認整條鏈路沒有卡住你寫後面章節的問題。

## 這一章要裝什麼，為什麼

你接下來會用到三類工具：

```
              你的 target (C source / binary)
                          │
     ┌────────────────────┼────────────────────┐
     │                    │                    │
  Symbolic Exec        Taint Analysis        Dynamic Binary
     │                    │                  Instrumentation
  ┌──┴──┬─────┐        ┌──┴────┐             │
  Z3   KLEE  angr    libdft  Triton        Pin / DynamoRIO / Frida
  (solver)              (binary DTA)
```

- **Symbolic 一邊**：Z3 是底層 solver，KLEE 在 LLVM IR 上做 symex，angr 在 binary (VEX IR) 上做 symex。三個不互相取代 — 你之後會看到為什麼。
- **Taint 一邊**：Triton 是 Python 綁定的 binary-level taint + symex 混合庫，libdft 是 Pin-based 的純 taint 框架。我們主推 Triton，libdft 讀架構即可。
- **DBI 底層**：Pin 是 Intel 的閉源 DBI，DynamoRIO 開源，Frida 偏應用層 hook。後面 Ch 22 會比較。

所有東西在 Linux 上生活才順。原生 Windows 上 angr 能跑，但 KLEE 與 Pin 幾乎只在 Linux 運作正常。**走 WSL2**。

## Step 1 — WSL2 + Ubuntu 22.04

PowerShell 管理員模式：

```powershell
wsl --install -d Ubuntu-22.04
```

重開機，進 Ubuntu 設好帳號。之後**所有指令都在 WSL bash 裡跑**。

確認：

```bash
uname -a
# Linux ... x86_64 GNU/Linux
cat /etc/os-release | grep VERSION
# VERSION="22.04.x LTS (Jammy Jellyfish)"
```

選 22.04 不選 24.04 的理由：KLEE 官方 Docker 目前對 22.04 glibc 最穩。24.04 你會在某些 KLEE POSIX 模式上撞到 glibc symbol 不相容。

## Step 2 — 基本 toolchain

```bash
sudo apt update
sudo apt install -y build-essential git curl wget unzip \
    python3 python3-pip python3-venv python3-dev \
    cmake ninja-build pkg-config \
    gcc-multilib g++-multilib \
    libc6-dev-i386 \
    gdb
```

`gcc-multilib` 與 `libc6-dev-i386` 是為了之後某些 32-bit target 做練習。

LLVM / Clang（KLEE 要對上特定版本，我們先裝 14，KLEE 3.0 對 LLVM 14 支援最好）：

```bash
sudo apt install -y clang-14 llvm-14 llvm-14-dev llvm-14-tools
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-14 100
sudo update-alternatives --install /usr/bin/llvm-config llvm-config /usr/bin/llvm-config-14 100
clang --version   # 14.x
llvm-config --version  # 14.x
```

## Step 3 — Python 環境

angr 跟 Triton 都是 Python。**用 venv，不要汙染系統 Python**：

```bash
mkdir -p ~/symex && cd ~/symex
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
```

以後每個 shell 進來先 `source ~/symex/.venv/bin/activate`。加進 `~/.bashrc` 最省事：

```bash
echo 'source ~/symex/.venv/bin/activate' >> ~/.bashrc
```

## Step 4 — Z3

最底層的 solver。裝 Python binding 就同時有 CLI 跟 API：

```bash
pip install z3-solver
```

驗證：

```bash
python3 -c "import z3; s=z3.Solver(); x=z3.Int('x'); s.add(x*x == 16); print(s.check(), s.model())"
# sat [x = -4]  或  sat [x = 4]
```

`z3` CLI 一起裝進來了：

```bash
which z3
z3 --version
```

## Step 5 — angr

直接 pip：

```bash
pip install angr
```

這會順便把 claripy（angr 的 SMT wrapper）、pyvex（VEX IR binding）、archinfo、cle（loader）一起拖下來。大概要兩三分鐘。

hello 驗證：寫一個最小的 crackme。

```bash
mkdir -p ~/symex/hello-angr && cd ~/symex/hello-angr
cat > crackme.c <<'EOF'
#include <stdio.h>
#include <string.h>

int main() {
    char buf[32];
    if (!fgets(buf, sizeof(buf), stdin)) return 1;
    buf[strcspn(buf, "\n")] = 0;

    if (buf[0] == 'h' && buf[1] == 'a' && buf[2] == 'x' && buf[3] == '0' && buf[4] == 'r') {
        printf("win\n");
        return 0;
    }
    printf("lose\n");
    return 1;
}
EOF
gcc -O0 -no-pie -o crackme crackme.c
```

拿 angr 自動找出 `win` 的輸入：

```python
# solve.py
import angr

proj = angr.Project('./crackme', auto_load_libs=False)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=lambda s: b'win' in s.posix.dumps(1),
              avoid=lambda s: b'lose' in s.posix.dumps(1))

if simgr.found:
    print(repr(simgr.found[0].posix.dumps(0)))
else:
    print('no solution')
```

```bash
python3 solve.py
# b'hax0r\n...'  或類似
```

如果你看到 `b'hax0r'` 開頭的 stdin，angr 就通了。第一次跑會有幾秒熱身（載 libc 之類），正常。

## Step 6 — KLEE

KLEE 裝起來最麻煩 — 要 build 自己的 LLVM、uclibc、POSIX runtime。**用官方 Docker** 幾乎是唯一 sane 的選項：

```bash
docker --version || { echo "先裝 docker"; exit 1; }
docker pull klee/klee:3.0
```

如果沒裝 docker：

```bash
sudo apt install -y docker.io
sudo usermod -aG docker $USER
# 登出再登入讓 group 生效
```

hello 驗證：

```bash
mkdir -p ~/symex/hello-klee && cd ~/symex/hello-klee
cat > get_sign.c <<'EOF'
#include <klee/klee.h>

int get_sign(int x) {
    if (x == 0) return 0;
    if (x < 0)  return -1;
    return 1;
}

int main() {
    int a;
    klee_make_symbolic(&a, sizeof(a), "a");
    return get_sign(a);
}
EOF

docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 \
    /bin/bash -c "clang -I /usr/local/include -emit-llvm -c -g -O0 -Xclang -disable-O0-optnone get_sign.c -o get_sign.bc && klee get_sign.bc"
```

你會看到 KLEE 輸出類似：

```
KLEE: output directory is "/work/klee-out-0"
KLEE: done: total instructions = ...
KLEE: done: completed paths = 3
KLEE: done: generated tests = 3
```

三條路徑 — `x == 0`、`x < 0`、`x > 0` — 這就是 symex 的全覆蓋。後面 Part 3 會深入。

## Step 7 — Triton

Triton 是 C++ 庫 + Python binding。pip 裝 pre-built wheel：

```bash
pip install triton-library
```

（如果 pip 裝不起來 — 某些舊 distro glibc 太老 — 就從 source build：<https://github.com/JonathanSalwan/Triton/blob/master/doc/INSTALL.md>。正常情況 pip 就行。）

hello 驗證：

```python
# hello-triton.py
from triton import TritonContext, ARCH, Instruction

ctx = TritonContext(ARCH.X86_64)

# 標記 rax 為 tainted
ctx.taintRegister(ctx.registers.rax)

# 執行 mov rbx, rax — 預期 taint 傳過去
inst = Instruction(b"\x48\x89\xc3")  # mov rbx, rax
ctx.processing(inst)

print("rax tainted:", ctx.isRegisterTainted(ctx.registers.rax))
print("rbx tainted:", ctx.isRegisterTainted(ctx.registers.rbx))
```

```bash
python3 hello-triton.py
# rax tainted: True
# rbx tainted: True
```

兩個都 True — taint 從 rax 傳到了 rbx。你剛剛見證了 taint propagation 的最小單元。

## Step 8 — Pin（DBI）

Intel Pin 要從官網下載（不是 apt，不是 pip）：

```bash
cd ~
wget https://software.intel.com/sites/landingpage/pintool/downloads/pin-3.30-98830-g1d7b601b3-gcc-linux.tar.gz
tar xf pin-3.30-*-gcc-linux.tar.gz
mv pin-3.30-*-gcc-linux pin
echo 'export PIN_ROOT=$HOME/pin' >> ~/.bashrc
echo 'export PATH=$PIN_ROOT:$PATH' >> ~/.bashrc
source ~/.bashrc
```

驗證：

```bash
pin -t $PIN_ROOT/source/tools/ManualExamples/obj-intel64/inscount0.so -- /bin/ls
cat inscount.out
# Count 12345  (某個數字)
```

如果 `obj-intel64` 裡沒有 `.so`，先 build：

```bash
cd $PIN_ROOT/source/tools/ManualExamples
make TARGET=intel64
```

Pin 我們後面 Ch 22 才大量用，現在裝起來備著。

## Step 9 — QEMU（user-mode）

做 taint / symex 的 binary rewriting 時，QEMU TCG 是常見底層：

```bash
sudo apt install -y qemu-user qemu-user-static
qemu-x86_64 --version
```

Ch 22 會看它。

## 一次檢查腳本

把上面所有工具的存在性驗完：

```bash
cat > ~/symex/check-env.sh <<'EOF'
#!/usr/bin/env bash
set -e
echo "=== Python $(python3 --version) ==="
python3 -c "import z3; print('z3', z3.get_version())"
python3 -c "import angr; print('angr', angr.__version__)"
python3 -c "import triton; print('triton', triton.__version__)"
echo "=== CLI ==="
clang --version | head -1
llvm-config --version
docker --version
pin -version 2>/dev/null | head -1 || echo "pin NOT ok"
qemu-x86_64 --version | head -1
echo "=== KLEE (docker) ==="
docker run --rm klee/klee:3.0 klee --version 2>&1 | head -5
EOF
chmod +x ~/symex/check-env.sh
~/symex/check-env.sh
```

每一項都要有正常輸出，看到 error 就回上一步修。

## 編輯器

跟 `learn_sat_smt` / `learn_bpf` 一樣 — VS Code + Remote-WSL 最順。Python 端記得裝 Pylance，對 angr 這種重度 type 的庫幫很大。

## 常見問題

**angr 裝完 import 慢到懷疑人生** — 正常，第一次載大概 3–5 秒。之後就快。

**KLEE docker 跑起來 `permission denied` on volume mount** — WSL 下常見，通常是 `$(pwd)` 被解析成 Windows 路徑。進 WSL 的 `~` 下操作，不要在 `/mnt/c/...`。

**Triton `import` 報 `GLIBC_2.34 not found`** — 你 Ubuntu 版本太舊。22.04 正常不會。如果是 20.04，要嘛升級，要嘛 source build。

**Pin 跑起來 `Unable to find instrumentation tool`** — 你沒 build 它的 example tool。`cd $PIN_ROOT/source/tools/ManualExamples && make TARGET=intel64`。

**Z3 Python 跟 z3 CLI 版本對不上** — 不用管。我們 99% 用 Python binding，CLI 只是偶爾手測。

## 為什麼不裝 Manticore、S2E、SymCC？

- **Manticore**：Trail of Bits 的 symex 工具，已經 unmaintained（2023 底最後 commit 降頻）。功能 angr 都有，不裝。
- **S2E**：full-system symex，要改 QEMU，setup 半天。Ch 26 會提到，但不實作。
- **SymCC**：LLVM pass 注入 symbolic tracing，hybrid fuzzing 用。Ch 25 才會裝，這裡省下來。

## 自我檢核

- [ ] WSL2 Ubuntu 22.04 能進到 bash
- [ ] `~/symex/.venv` 起得來，`python3 -c "import angr, z3, triton"` 不報錯
- [ ] `clang-14` 與 `llvm-config-14` 都在
- [ ] KLEE docker hello 跑出 3 條路徑
- [ ] angr hello 跑出 `hax0r` 輸入
- [ ] Triton hello 印出兩個 True
- [ ] Pin `inscount0` 跑 `/bin/ls` 印得出 count
- [ ] `check-env.sh` 全綠

環境齊了，下一章拉高視角 — 先說清楚 symex 到底在 static analysis / fuzzing / dynamic analysis 的光譜裡站哪裡，不然你用它時會不斷犯「拿 symex 做 fuzzing 該做的事」這個最常見的病。

→ [Ch 1 — 為什麼要 symex：static / fuzzing / symbolic 三條路](./01-why-symex.md)
