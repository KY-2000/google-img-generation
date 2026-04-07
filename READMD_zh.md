# Google 图片生成到 Adobe Stock CSV 使用说明

这个仓库会先用 Gemini 生成图片，再用第二次 Gemini 调用生成 Adobe Stock 元数据，最后输出 Adobe 风格的 CSV 文件。

## 工作流程

每次运行会执行以下步骤：

1. 用 `gemini-3-pro-image-preview` 根据你的提示词生成图片
2. 使用 `ffmpeg` 去除图片背景
3. 把生成后的图片、原始提示词、分类列表一起发送给 `gemini-2.5-flash`
4. 解析返回的 `Title`、`Keywords` 和 `Category Code`
5. 输出 Adobe Stock CSV，字段包括：
   - `Filename`
   - `Title`
   - `Keywords`
   - `Category`
   - `Releases`

## 仓库文件

- `main.py`：主命令行脚本
- `category-code.txt`：传给元数据模型的 Adobe Stock 分类列表
- `prompts/adobe_stock_metadata_system_prompt.txt`：元数据生成的 system prompt
- `Sample_Adobe_Stock_CSV_upload.csv`：Adobe 官方示例 CSV
- `csv-format.txt`：CSV 格式说明

## 环境要求

- Python 3.10+
- `ffmpeg`
- `google-genai`
- `numpy`
- `Pillow`
- Google Cloud CLI（`gcloud`）

安装 Python 依赖：

```bash
pip install google-genai numpy pillow
```

如果你在 macOS 上使用 Homebrew，可以安装 `ffmpeg`：

```bash
brew install ffmpeg
```

## API Key 设置

在仓库根目录创建 `.env` 文件：

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_AUTH_MODE=adc
OPENROUTER_API_KEY=your_openrouter_key_here
IMAGE_MODEL=nanobanana2
METADATA_PROVIDER=google
METADATA_MODEL=gemini-2.5-flash
```

对于 Google 模型，这个仓库现在使用 Vertex AI，并支持两种认证模式：
- `adc`
- `api_key`

脚本会从以下位置读取 Google 配置：
- 仓库根目录的 `.env`
- 或当前 shell 环境变量

必填：
- `GOOGLE_AUTH_MODE`

可选：
- `GOOGLE_CLOUD_PROJECT`
  当 `GOOGLE_AUTH_MODE=adc` 时需要
- `GOOGLE_CLOUD_LOCATION`
  默认值是 `global`
- `GOOGLE_API_KEY`
  当 `GOOGLE_AUTH_MODE=api_key` 时需要

如果你把元数据提供方切到 OpenRouter，脚本也会读取 `OPENROUTER_API_KEY`。

## Vertex AI 认证设置

### 方案 1：ADC

如果你想用 `gcloud` 登录，使用这个方案。

示例：

```env
GOOGLE_AUTH_MODE=adc
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
```

运行脚本前先完成：

1. 创建或选择一个 Google Cloud 项目
2. 为该项目开启 Billing
3. 启用 Vertex AI API
4. 安装并初始化 `gcloud`
5. 创建本地 ADC 凭证：

```bash
gcloud init
gcloud auth application-default login
```

### 方案 2：API key

如果你的项目允许 Vertex AI API key，并且你不想用 `gcloud auth application-default login`，可以使用这个方案。

示例：

```env
GOOGLE_AUTH_MODE=api_key
GOOGLE_API_KEY=your-google-api-key
```

在当前仓库实现里，如果你使用 `api_key` 模式，则不强制要求设置 `GOOGLE_CLOUD_PROJECT`。

如果 `.env` 里设置了 `IMAGE_MODEL`，脚本会把它当作默认图片模型；如果没有设置，则默认使用 `gemini-3-pro-image-preview`。

支持的图片模型别名：
- `nanobanana2` -> `gemini-3.1-flash-image-preview`
- `nanobananapro` -> `gemini-3-pro-image-preview`

支持的元数据提供方：
- `google`
- `openrouter`

支持的元数据模型别名：
- `qwenfree` -> `qwen/qwen3.6-plus:free`
- `minimaxfree` -> `minimax/minimax-m2.5:free`
- `nemotronsuperfree` -> `nvidia/nemotron-3-super-120b-a12b:free`

## 基本用法

在仓库根目录运行：

```bash
python3 main.py "a studio portrait of a young woman recording a vlog at home"
```

## 命令行参数

```bash
python3 main.py "你的提示词" \
  --model gemini-3-pro-image-preview \
  --metadata-provider google \
  --metadata-model gemini-2.5-flash \
  --temperature 1.0 \
  --top-p 0.95 \
  --count 3 \
  --aspect-ratio 16:9 \
  --resolution 2K
```

### 参数说明

- `prompt`：图片生成提示词
- `--model`：图片生成模型
  如果不传，会优先使用 `.env` 里的 `IMAGE_MODEL`，否则使用 `gemini-3-pro-image-preview`
  也可以直接使用别名：`nanobanana2`、`nanobananapro`
- `--metadata-provider`：元数据提供方，可选 `google` 或 `openrouter`
- `--metadata-model`：元数据模型
  也可以直接使用别名：`qwenfree`、`minimaxfree`、`nemotronsuperfree`
- `--temperature`：两个模型调用共用的采样温度
- `--top-p`：两个模型调用共用的 top-p
- `--count`：顺序生成的图片数量
- `--aspect-ratio`：可选值为 `1:1`、`1:4`、`1:8`、`2:3`、`3:2`、`3:4`、`4:1`、`4:3`、`4:5`、`5:4`、`8:1`、`9:16`、`16:9`、`21:9`
- `--resolution`：可选值为 `512`、`1K`、`2K`、`4K`

## 示例

```bash
python3 main.py "cute bakery themed sticker sheet on pure blue background" \
  --metadata-provider openrouter \
  --metadata-model qwenfree \
  --count 2 \
  --aspect-ratio 1:1 \
  --resolution 1K \
  --temperature 1.1 \
  --top-p 0.95
```

## 输出目录结构

每次运行都会创建一个目录：

```text
output/<timestamp>/
```

如果使用 `--count 2`，目录内容示例：

```text
output/20260407-153000/
  prompt.txt
  run_config.json
  metadata.txt
  adobe_stock_metadata.csv
  img-1.png
  img-2.png
  img-rembg-20260407-153000-1.png
  img-rembg-20260407-153000-2.png
```

### 输出文件说明

- `prompt.txt`：原始图片提示词
- `run_config.json`：本次运行保存的参数
- `metadata.txt`：所有生成图片的原始元数据返回内容，多个结果之间用空行分隔
- `adobe_stock_metadata.csv`：Adobe 风格 CSV，每张图一行
- `img-<n>.png`：原始生成图片
- `img-rembg-<timestamp>-<n>.png`：去背后的图片

## CSV 行为说明

CSV 表头为：

```text
Filename,Title,Keywords,Category,Releases
```

说明：
- `Filename` 写入的是去背后的图片文件名
- `Category` 写入的是数字形式的 `Category Code`
- `Releases` 当前固定留空

## 可自定义内容

如果你想调整行为，可以编辑：

- `category-code.txt`：修改传给元数据模型的分类列表
- `prompts/adobe_stock_metadata_system_prompt.txt`：修改元数据生成规则

如果只是想换图片提示词，不需要改文件，直接在命令行里传新的 prompt 即可。

## 运行测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 常见问题

### 提示 `GOOGLE_CLOUD_API_KEY is missing`

这个仓库已经不再使用 `GOOGLE_CLOUD_API_KEY` 调用 Google 模型。

请使用以下其中一种模式：

ADC 模式：
- `GOOGLE_AUTH_MODE=adc`
- `GOOGLE_CLOUD_PROJECT`
- 可选 `GOOGLE_CLOUD_LOCATION`

并执行：

```bash
gcloud auth application-default login
```

API key 模式：
- `GOOGLE_AUTH_MODE=api_key`
- `GOOGLE_API_KEY`

### 提示找不到 `ffmpeg`

请先安装 `ffmpeg`，然后检查：

```bash
ffmpeg -version
```

### API 没有返回图片

请检查：
- ADC 是否已登录
- Google Cloud 项目是否正确
- 模型名称是否正确
- 当前图片模型是否支持你选择的参数

### OpenRouter 元数据请求失败

请检查：
- `OPENROUTER_API_KEY` 是否已设置
- 是否选择了 `--metadata-provider openrouter`
- 你选择的 OpenRouter 模型是否支持图片输入

### 元数据缺少字段

如果 Gemini 没有返回以下全部字段：
- `Title`
- `Keywords`
- `Category Code`
- `Category Name`

脚本会直接报错，而不会写入不完整的 CSV。
