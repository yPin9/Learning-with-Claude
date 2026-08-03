# Ch 33 — Thrust：GPU 上的 STL

> **目標**：掌握 Thrust 的 `device_vector`、`transform`/`reduce`/`sort`/`scan`/`transform_reduce`；理解 functor、execution policy、fancy iterator（counting/zip/transform）的使用；知道何時選 Thrust、何時必須手寫 kernel（fusion 需求）；理解 Thrust 底層是 CUB。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。程式輸出均為「Colab 預期，未在本機實測」，附 Colab 執行步驟。

---

## 直覺：CPU STL 和 GPU Thrust 的對稱性

如果你寫過 C++ STL，Thrust 看起來非常熟悉：

```cpp
// CPU STL
std::vector<float> v(1024, 1.0f);
std::transform(v.begin(), v.end(), v.begin(), [](float x){ return x * 2; });
float s = std::reduce(v.begin(), v.end(), 0.0f);
std::sort(v.begin(), v.end());

// GPU Thrust（幾乎一比一對應）
thrust::device_vector<float> dv(1024, 1.0f);
thrust::transform(dv.begin(), dv.end(), dv.begin(), [] __device__ (float x){ return x * 2; });
float s = thrust::reduce(dv.begin(), dv.end(), 0.0f);
thrust::sort(dv.begin(), dv.end());
```

差異就是：
- `std::vector` → `thrust::device_vector`（記憶體在 GPU 上）
- lambda 前面加 `__device__`（或用 functor）
- 自動在 GPU 上平行執行

這就是 Thrust 的核心價值：**讓你用 STL 的語言在 GPU 上寫程式**，不需要自己設計 thread block、管 shared memory、寫 kernel。

---

## device_vector 和 host_vector

Thrust 提供兩種主要容器：

```cpp
#include <thrust/host_vector.h>
#include <thrust/device_vector.h>

thrust::host_vector<float>   h_vec(1024);   // 在 host（CPU）記憶體
thrust::device_vector<float> d_vec(1024);   // 在 device（GPU）記憶體
```

`device_vector` 自動管理 GPU 記憶體：建構時 `cudaMalloc`，解構時 `cudaFree`。就像 `std::vector` 管 heap 記憶體一樣，你不需要手動 free。

**Host ↔ Device 傳輸**：直接賦值就好，Thrust 在背後自動呼叫 `cudaMemcpy`：

```cpp
thrust::host_vector<float> h(1024, 3.14f);
thrust::device_vector<float> d = h;       // host → device（自動 cudaMemcpyH2D）
h = d;                                    // device → host（自動 cudaMemcpyD2H）
```

**初始化**：

```cpp
thrust::device_vector<int> v(1024, 0);          // 全填 0
thrust::fill(v.begin(), v.end(), 42);            // 全填 42
thrust::sequence(v.begin(), v.end());            // 填 0,1,2,...,1023
thrust::sequence(v.begin(), v.end(), 10, 2);     // 填 10,12,14,...
```

---

## 核心演算法

### transform：逐元素變換

```cpp
#include <thrust/transform.h>

thrust::device_vector<float> x(N), y(N), z(N);
// 填入 x, y 的資料（略）

// 一元 transform：z[i] = sqrt(x[i])
thrust::transform(x.begin(), x.end(), z.begin(),
    [] __device__ (float v) { return sqrtf(v); });

// 二元 transform：z[i] = x[i] + y[i]
thrust::transform(x.begin(), x.end(), y.begin(), z.begin(),
    [] __device__ (float a, float b) { return a + b; });
```

### reduce：並行規約

```cpp
#include <thrust/reduce.h>

thrust::device_vector<float> v(N, 1.0f);

float sum  = thrust::reduce(v.begin(), v.end(), 0.0f, thrust::plus<float>());
float prod = thrust::reduce(v.begin(), v.end(), 1.0f, thrust::multiplies<float>());
float mx   = thrust::reduce(v.begin(), v.end(), -1e30f, thrust::maximum<float>());
```

最後一個引數是 **binary reduction operator**，預設是 `thrust::plus<T>`（加法）。Thrust 的內建 functor（`plus`、`multiplies`、`maximum`、`minimum` 等）和 STL 完全對應。

### sort 和 sort_by_key

```cpp
#include <thrust/sort.h>

thrust::device_vector<int> v = {5, 2, 8, 1, 9, 3};
thrust::sort(v.begin(), v.end());                          // 升序
thrust::sort(v.begin(), v.end(), thrust::greater<int>());  // 降序

// sort_by_key：同時排序鍵和值
thrust::device_vector<int>   keys   = {3, 1, 4, 1, 5, 9};
thrust::device_vector<float> values = {3.3f, 1.1f, 4.4f, 1.0f, 5.5f, 9.9f};
thrust::sort_by_key(keys.begin(), keys.end(), values.begin());
// keys:   1 1 3 4 5 9
// values: 1.1 1.0 3.3 4.4 5.5 9.9（和 keys 同步移動）
```

Thrust `sort` 在 GPU 上使用 radix sort 或 merge sort（依資料型別和大小自動選擇），效能接近 CUB `DeviceRadixSort`（文獻預期；事實上 Thrust sort 底層就是 CUB）。

### scan（prefix sum）

```cpp
#include <thrust/scan.h>

thrust::device_vector<int> v  = {1, 2, 3, 4, 5};
thrust::device_vector<int> pf(5);

thrust::inclusive_scan(v.begin(), v.end(), pf.begin());
// pf: [1, 3, 6, 10, 15]

thrust::exclusive_scan(v.begin(), v.end(), pf.begin());
// pf: [0, 1, 3, 6, 10]  （第一個元素是 identity，其餘是之前所有元素的和）

// 自定義 operator
thrust::inclusive_scan(v.begin(), v.end(), pf.begin(), thrust::maximum<int>());
// 等於 running maximum：[1, 2, 3, 4, 5]（此例 max-scan 和 inclusive scan 結果一樣）
```

### transform_reduce：融合的 map-reduce

常見模式：先對每個元素做變換，再做規約。你可以串接 `transform` 和 `reduce`，但會有中間結果的記憶體讀寫。`transform_reduce` 把這兩個步驟融合：

```cpp
#include <thrust/transform_reduce.h>

thrust::device_vector<float> v = {-1.0f, 2.0f, -3.0f, 4.0f};

// 計算 L1 norm（絕對值之和）
float l1 = thrust::transform_reduce(
    v.begin(), v.end(),
    [] __device__ (float x) { return fabsf(x); },   // 變換：取絕對值
    0.0f,                                            // 初始值
    thrust::plus<float>()                            // 規約：求和
);
// 結果：1 + 2 + 3 + 4 = 10.0
// （Colab 預期，未在本機實測）

// 計算 L2 norm 的平方（不開根號）
float l2_sq = thrust::transform_reduce(
    v.begin(), v.end(),
    [] __device__ (float x) { return x * x; },
    0.0f,
    thrust::plus<float>()
);
// 結果：1 + 4 + 9 + 16 = 30.0
```

---

## Functor：當 lambda 不夠用

`__device__` lambda 在 CUDA 12 / Thrust 已廣泛支援，但有時 functor 更靈活——尤其是需要在構造時帶入 state（例如一個 scale factor）：

```cpp
// Lambda 版本（需要 capture，device lambda 對 capture 有限制）
float scale = 2.0f;
// CUDA 不保證所有情況下 device lambda 都能安全 capture host 變數
// 安全做法：用 functor

struct ScaleBy {
    float factor;
    ScaleBy(float f) : factor(f) {}
    __device__ float operator()(float x) const {
        return x * factor;
    }
};

thrust::device_vector<float> v = {1.0f, 2.0f, 3.0f, 4.0f};
thrust::transform(v.begin(), v.end(), v.begin(), ScaleBy(3.14f));
// v: [3.14, 6.28, 9.42, 12.56]
```

Functor 是 Thrust 傳統的寫法（Thrust 比 CUDA device lambda 早很多年），現在 device lambda 在大多數場景已夠用，但遇到複雜 state 或泛型 template 仍要回頭用 functor。

---

## Execution Policy：控制在哪執行、用哪條 stream

Thrust 的演算法第一個引數可以是 execution policy，控制執行環境：

```cpp
#include <thrust/execution_policy.h>
#include <thrust/device_vector.h>

thrust::device_vector<int> v(N, 1);

// 預設：在 GPU 上執行（推斷自資料位置）
thrust::reduce(v.begin(), v.end(), 0);

// 明確指定 device 執行
thrust::reduce(thrust::device, v.begin(), v.end(), 0);

// 在特定 stream 上執行（Thrust 的 CUDA-specific policy）
cudaStream_t stream;
cudaStreamCreate(&stream);
thrust::reduce(thrust::cuda::par.on(stream), v.begin(), v.end(), 0);

// 不同步版本（呼叫後不保證 kernel 完成；caller 要自己 sync）
thrust::reduce(thrust::cuda::par_nosync.on(stream), v.begin(), v.end(), 0);
cudaStreamSynchronize(stream);
```

`thrust::cuda::par.on(stream)` 讓 Thrust 把所有內部 kernel 提交到你指定的 stream，這樣就能和其他 CUDA 工作用 stream 組合做 overlap（回連 [Ch 23 streams](./23-streams-async.md)）。

---

## Fancy Iterator：不搬資料的間接操作

Fancy iterator 是 Thrust 最強大的工具之一——它們看起來像 iterator，但背後其實是「按需計算」，不需要實際分配中間記憶體。

### counting_iterator：產生序列索引

```cpp
#include <thrust/iterator/counting_iterator.h>

// 不需要真的建一個 device_vector 存 0,1,2,...,N-1
thrust::counting_iterator<int> first(0);
thrust::counting_iterator<int> last(N);

// 計算 0+1+2+...+(N-1) = N*(N-1)/2
long long sum = thrust::reduce(first, last, 0LL, thrust::plus<long long>());
```

### transform_iterator：把 functor 套在 iterator 上

```cpp
#include <thrust/iterator/transform_iterator.h>

thrust::device_vector<float> x = {1.0f, 4.0f, 9.0f, 16.0f};

// 建立一個「讀取時自動取 sqrt」的 iterator，不需要 temp vector
auto sqrt_iter = thrust::make_transform_iterator(x.begin(),
    [] __device__ (float v) { return sqrtf(v); });

// 對 sqrt 結果做 reduce，沒有中間記憶體
float sum = thrust::reduce(sqrt_iter, sqrt_iter + x.size(), 0.0f);
// 等價（但省掉 temp vector）於：
// thrust::device_vector<float> tmp(x.size());
// thrust::transform(x.begin(), x.end(), tmp.begin(), sqrt_functor);
// thrust::reduce(tmp.begin(), tmp.end(), 0.0f);
```

### zip_iterator：同時遍歷多個 vector

```cpp
#include <thrust/iterator/zip_iterator.h>
#include <thrust/tuple.h>

thrust::device_vector<float> x = {1.0f, 2.0f, 3.0f};
thrust::device_vector<float> y = {4.0f, 5.0f, 6.0f};

// 計算 x · y（點積）：sum(x[i] * y[i])
auto zipped = thrust::make_zip_iterator(
    thrust::make_tuple(x.begin(), y.begin()));

float dot = thrust::transform_reduce(
    zipped, zipped + x.size(),
    [] __device__ (thrust::tuple<float,float> t) {
        return thrust::get<0>(t) * thrust::get<1>(t);
    },
    0.0f,
    thrust::plus<float>()
);
// 結果：1*4 + 2*5 + 3*6 = 32.0
// （Colab 預期，未在本機實測）
```

---

## 完整範例：用 Thrust 實作 k-means 的一個步驟

```cpp
// Colab 執行步驟：
// !pip install nvcc4jupyter（或用 .cu + !nvcc）
// 需要 #include 路徑：thrust/ 在 CUDA toolkit 裡，不需要額外安裝

#include <thrust/device_vector.h>
#include <thrust/transform_reduce.h>
#include <thrust/iterator/zip_iterator.h>
#include <thrust/tuple.h>
#include <cstdio>

// 找每個點到 centroid 的距離平方，然後求和（total inertia）
float total_inertia(
    const thrust::device_vector<float>& px,
    const thrust::device_vector<float>& py,
    float cx, float cy)   // centroid 座標（純量）
{
    auto zipped = thrust::make_zip_iterator(
        thrust::make_tuple(px.begin(), py.begin()));

    return thrust::transform_reduce(
        zipped, zipped + px.size(),
        [cx, cy] __device__ (thrust::tuple<float,float> p) {
            float dx = thrust::get<0>(p) - cx;
            float dy = thrust::get<1>(p) - cy;
            return dx*dx + dy*dy;
        },
        0.0f,
        thrust::plus<float>()
    );
}

int main() {
    int N = 1024;
    thrust::device_vector<float> px(N), py(N);
    // 用 thrust::sequence 填假資料
    thrust::sequence(px.begin(), px.end(), 0.0f, 1.0f);
    thrust::sequence(py.begin(), py.end(), 0.0f, 0.5f);

    float inertia = total_inertia(px, py, 512.0f, 256.0f);
    printf("Total inertia = %.2f\n", inertia);
    // （Colab 預期，未在本機實測）
    return 0;
}
```

---

## 何時用 Thrust，何時手寫 kernel

Thrust 的選擇規則不複雜，但要講清楚：

| 情境 | 建議 |
|------|------|
| 標準 reduction / sort / scan，資料在 device_vector 裡 | 用 Thrust，夠快 |
| map-reduce 或 filter-reduce，可以用 fancy iterator 融合 | 用 Thrust transform_reduce + fancy iterator |
| 需要在一個 kernel 裡讀多個輸入、寫多個輸出，並在 shared memory 裡做精細的 tile 計算 | 必須手寫 kernel |
| 需要特定 shared memory 配置或 warp-level 操作（`__shfl_sync`）| 手寫 kernel |
| softmax / flash attention 這類需要「沿某個維度做多步操作」且要最大化 data reuse | 手寫 kernel（參考 Ch 40–41） |
| 原型開發、一次性任務、測試邏輯 | 先 Thrust，如果效能不夠再下手 |

**Fusion 是關鍵考量**：Thrust 的每個演算法是一個或幾個 kernel，資料在 global memory 進出。如果你的計算是「讀 A，做一些事，寫 B，讀 B，做另一些事，寫 C」，Thrust 會老老實實地讀寫 global memory 兩次。手寫 kernel 可以把「讀 A → 做所有事 → 寫 C」合成一個 kernel，消掉中間的 global memory 往返。

這就是 [Ch 38 GEMM 深挖](./38-gemm-deep-dive.md)、[Ch 40 softmax](./40-softmax-layernorm.md)、[Ch 41 flash attention](./41-flash-attention.md) 裡手寫 kernel 的核心理由。

---

## 底層是 CUB

Thrust 的 device-level 演算法在 CUDA backend 下幾乎都直接轉發給 [Ch 32](./32-libraries.md) 裡的 CUB：

- `thrust::reduce` → `cub::DeviceReduce::Reduce`
- `thrust::sort` → `cub::DeviceRadixSort`（整數）或 `cub::DeviceMergeSort`（其他）
- `thrust::inclusive_scan` → `cub::DeviceScan::InclusiveSum`

所以 Thrust 的效能和直接呼叫 CUB 幾乎一樣，差別只在 Thrust 多了一層 C++ 模板包裝。當你用 `thrust::cuda::par.on(stream)` 指定 stream 後，內部 CUB kernel 也會跑在那條 stream 上。

自 CUDA 12，Thrust 和 CUB 統一進了 **CCCL**（CUDA Core Compute Libraries），頭文件路徑 `<thrust/...>` 和 `<cub/cub.cuh>` 都繼續存在，只是維護在同一個 repo 下。

---

## 踩雷清單

**錯誤直覺 1：device_vector 的賦值（`d = h`）是非同步的，要手動 sync。**
正確：`thrust::device_vector` 的賦值（`=` 或複製建構）**是同步的**，完成後資料確實在 device 上。這和手寫 `cudaMemcpyAsync` 不同。如果你要非同步傳輸，要用 `cudaMemcpyAsync` + raw pointer。

**錯誤直覺 2：`__device__` lambda 可以 capture 任何 host 變數。**
正確：CUDA 的 device lambda 只能 capture **可平凡複製（trivially copyable）** 的純量（int、float 等）。不能 capture `std::vector`、`std::string`，也不能 capture `device_vector`（因為 device 端不能解引用 host 持有的 device_vector）。要傳複雜資料進 functor，用 raw device pointer。

**錯誤直覺 3：Thrust 的 sort 比 std::sort 總是更快。**
正確：對很小的資料（幾千個元素），Thrust sort 的 launch overhead 和資料傳輸 overhead 遠超排序本身的計算量，比 `std::sort` 慢。Thrust 的甜蜜點是 **100K 以上的元素**（理論預期，實際臨界值因 GPU 型號和資料型別而異）。

**錯誤直覺 4：zip_iterator 可以無限增加維度。**
正確：Thrust 的 tuple 最多支援 10 個元素（`thrust::tuple` 是固定 arity 的 template）。需要超過 10 個維度的 zip 要手動包成 struct 或分批處理。

**錯誤直覺 5：沒有指定 execution policy，Thrust 會自動選最快的後端。**
正確：Thrust 會根據 iterator 類型推斷：`device_vector::iterator` 推斷為 device 執行，`host_vector::iterator` 推斷為 host 執行。但它無法推斷「用哪條 stream」，不指定時用 default stream（可能導致和其他工作不必要的同步）。生產程式碼應該明確傳入 `thrust::cuda::par.on(stream)`。

---

## 進階：thrust::partition 和 gather/scatter

```cpp
// partition：把滿足條件的元素移到前面
thrust::device_vector<int> v = {3, 1, 4, 1, 5, 9, 2, 6};
auto mid = thrust::partition(v.begin(), v.end(),
    [] __device__ (int x) { return x % 2 == 0; });
// v: [4, 2, 6, 3, 1, 1, 5, 9]（偶數在前，奇數在後，各組內部順序不保證）
// mid 指向第一個奇數的位置

// gather：按索引收集
thrust::device_vector<int> src  = {10, 20, 30, 40, 50};
thrust::device_vector<int> idx  = {3, 1, 4, 0, 2};
thrust::device_vector<int> dst(5);
thrust::gather(idx.begin(), idx.end(), src.begin(), dst.begin());
// dst: [40, 20, 50, 10, 30]（src[idx[i]]）

// scatter：按索引寫入
thrust::device_vector<int> vals  = {10, 20, 30, 40, 50};
thrust::device_vector<int> locs  = {2, 0, 4, 1, 3};
thrust::device_vector<int> out(5, 0);
thrust::scatter(vals.begin(), vals.end(), locs.begin(), out.begin());
// out: [20, 40, 10, 50, 30]（out[locs[i]] = vals[i]）
```

---

## 動手練習

**Colab 執行步驟：**
1. Runtime → Change runtime type → GPU
2. 新建 `.cu` 檔，用 `!nvcc -o prog prog.cu && ./prog` 執行

練習 A：用 Thrust 計算 device_vector 的均值和標準差（不手寫任何 kernel）。提示：用兩次 `transform_reduce`——第一次算 sum，第二次算 sum of squares。

練習 B：用 `zip_iterator` 和 `transform_reduce` 計算兩個 device_vector 的 cosine similarity。

練習 C：用 `thrust::sort_by_key` 對（word_id, count）pair 按 count 降序排列，模擬詞頻統計的排名步驟。

---

## 本章重點

- `device_vector` 自動管理 GPU 記憶體，`=` 賦值觸發同步傳輸
- 核心演算法：`transform` / `reduce` / `sort` / `scan` / `transform_reduce`
- Execution policy：`thrust::cuda::par.on(stream)` 把工作綁到特定 stream
- Fancy iterator：`counting_iterator`（序列）、`transform_iterator`（按需映射）、`zip_iterator`（多 vector 並行讀取）——都是零額外記憶體
- Thrust 底層是 CUB，效能相當
- 需要 kernel fusion（在 shared memory 裡做多步操作）時，Thrust 不夠用，改手寫 kernel

## 自我檢核（主動回憶）

1. 如何讓 Thrust 的 `reduce` 跑在自訂的 CUDA stream 上？
2. `transform_reduce` 比 `transform` + `reduce` 的優勢是什麼？
3. `counting_iterator` 產生的序列存在哪裡？為什麼說它「零記憶體」？
4. device lambda 可以 capture `std::vector<float>` 嗎？為什麼？
5. 什麼情況下應該從 Thrust 切換到手寫 kernel？

## 延伸閱讀

1. **Thrust 官方文件** — [nvidia.github.io/cccl/thrust](https://nvidia.github.io/cccl/thrust/)：所有 API 的完整說明，特別看 `thrust::transform` 的 `UnaryFunction` 和 `BinaryFunction` 概念說明
2. **CCCL GitHub examples** — [github.com/NVIDIA/cccl](https://github.com/NVIDIA/cccl/tree/main/thrust/examples)：官方範例，包含 monte carlo、saxpy、histogram 等，有完整可跑的程式碼
3. **CUB 文件（Thrust 底層）** — [nvidia.github.io/cccl/cub](https://nvidia.github.io/cccl/cub/)：了解 DeviceReduce/DeviceSort 的底層機制
4. **「Thrust: A Parallel Algorithms Library」** — Bell & Hoberock (GPU Computing Gems, 2011)：Thrust 的設計理念和 fancy iterator 的數學基礎，雖然有點老但原理沒變
5. **PyTorch internals** — [pytorch.org/docs/stable/notes/cuda.html](https://pytorch.org/docs/stable/notes/cuda.html)：PyTorch 在哪些地方使用 Thrust/CUB，幫助你理解「高階框架用低階函式庫」的分層架構

---

Thrust 解決了單 GPU 的高階操作，但現實的 DL 訓練需要多卡協作。

→ [Ch 34 多 GPU](./34-multi-gpu.md)
