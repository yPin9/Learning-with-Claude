# Ch 15 — llama.cpp 實戰：編譯、轉換、跑

> 目標：在 CPU 上用 llama.cpp 跑起一個 7B 語言模型，並理解各個參數的意義。

## llama.cpp 是什麼

llama.cpp 是 Georgi Gerganov 用純 C/C++ 寫的 LLM 推論引擎，不依賴 PyTorch 或 CUDA：

- **純 CPU 可跑**：充分利用 AVX2/AVX-512 SIMD 指令
- **極低記憶體**：INT4 量化的 7B 模型在 8GB RAM 上就能跑
- **跨平台**：Windows、macOS、Linux 都支援
- **GGUF 格式**：所有主流 LLM 都有 llama.cpp 可用的 GGUF 版本

## 安裝

### Windows（CMake + MinGW 或 MSVC）

```powershell
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp

# 用 CMake 編譯（需要先安裝 CMake 和 C++ 編譯器）
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j

# 執行檔在 build/bin/ 或 build/Release/
```

### macOS

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make -j  # macOS 預設有 Clang，直接 make 即可
```

### Linux

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make -j$(nproc)
```

## 下載 GGUF 模型

從 Hugging Face 下載量化好的模型：

```bash
# 安裝 huggingface-cli
pip install huggingface_hub

# 下載 Llama 3.2 3B Q4_K_M（約 1.9 GB，CPU 可跑）
huggingface-cli download \
    bartowski/Llama-3.2-3B-Instruct-GGUF \
    Llama-3.2-3B-Instruct-Q4_K_M.gguf \
    --local-dir ./models

# 或者直接用 wget（如果你知道直接連結）
```

**推薦給 CPU 使用者的模型大小**：
- RAM 8GB → 3B 或 7B Q2_K
- RAM 16GB → 7B Q4_K_M（推薦）
- RAM 32GB → 13B Q4_K_M 或 7B Q8_0

## 跑起來

```bash
# 基本生成
./build/bin/llama-cli \
    -m ./models/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
    -p "以下是一道 Python 面試題，請給出解答：" \
    -n 200

# 互動式對話模式
./build/bin/llama-cli \
    -m ./models/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
    --interactive \
    --chat-template llama3 \
    -n 512

# 重要參數說明
# -m     模型路徑
# -p     提示詞（prompt）
# -n     最多生成幾個 token
# -t     執行緒數（預設用所有 CPU core）
# -c     context 長度（預設 512，可以加大，但更慢）
# --temp 溫度（預設 0.8）
# --top-k top-k 採樣（預設 40）
```

## 效能調整

```bash
# 查看你的 CPU 有幾個 core
nproc  # Linux/macOS
# Windows: 查工作管理員

# 設定執行緒數（通常等於物理核心數，不是超執行緒數）
./build/bin/llama-cli -m model.gguf -p "Hello" -t 8

# 查看 token 速度（tokens/second）
# 輸出最後會顯示：
# llama_perf_context_print: eval time = 1234.56 ms /  200 tokens (6.17 ms per token, 162.05 tokens per second)
```

典型 CPU 速度（Llama 3.2 3B Q4_K_M）：
- 現代 8 核桌機：~30–50 tokens/s（流暢對話）
- 老款筆電 4 核：~5–15 tokens/s（可用但稍慢）

## 把 Hugging Face 格式轉成 GGUF

如果你下載的是 Hugging Face 格式（safetensors），需要先轉換：

```bash
# 安裝 Python 依賴
pip install -r requirements.txt

# 轉換（以 Llama 3 為例）
python convert_hf_to_gguf.py \
    /path/to/Meta-Llama-3-8B \
    --outtype f16 \
    --outfile llama-3-8b-f16.gguf

# 量化
./build/bin/llama-quantize \
    llama-3-8b-f16.gguf \
    llama-3-8b-q4km.gguf \
    Q4_K_M
```

## llama-server：本地 OpenAI 相容 API

llama.cpp 附帶一個 HTTP 伺服器，提供 OpenAI 相容的 API：

```bash
./build/bin/llama-server \
    -m ./models/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
    --host 0.0.0.0 \
    --port 8080

# 用 curl 測試
curl http://localhost:8080/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
        "model": "llama",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 100
    }'
```

## 動手練習

下載一個 3B 或 7B 的 GGUF 模型，用 llama-cli 生成以下內容，並記錄 tokens/second：

1. 一首短詩（30 tokens）
2. 一個簡單的 Python 函數（100 tokens）
3. 解釋「什麼是梯度下降」（200 tokens）

觀察：生成速度和長度是否線性關係？

## 自我檢核

- [ ] 成功編譯 llama.cpp
- [ ] 下載並跑起一個 GGUF 模型
- [ ] 知道 `-t`、`-n`、`-c` 參數的意義
- [ ] 記錄了自己機器的 tokens/second 速度

→ [Ch 16 Ollama 實戰：Modelfile / API / 換模型](./16-ollama.md)
