# Google 图片生成到 Adobe Stock CSV 使用说明

这个仓库用于生成图片、生成 Adobe Stock 元数据，并输出 Adobe 上传 CSV。

## 两种使用方式

### 1. 直接用提示词生成图片

使用 `main.py`：

```bash
python main.py "a studio portrait of a young woman recording a vlog at home" \
  --model gemini-3-pro-image-preview \
  --metadata-provider google \
  --metadata-model gemini-2.5-flash \
  --count 3 \
  --aspect-ratio 16:9 \
  --resolution 2K
```

说明：
- `google` 在 `main.py` 里表示通过 Vertex AI 调用 Google/Gemini 模型。
- 本仓库不应该使用 Google AI Studio / Gemini Developer API。
- Google/Gemini 模型必须通过 Vertex AI，也就是 `google-genai` 的 `vertexai=True`。

### 2. 从小红书下载文件夹反推壁纸提示词并自动生成

使用 `xhs_wallpaper_workflow.py`：

```bash
python xhs_wallpaper_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\some-folder" \
  --prompt-provider vertex \
  --metadata-provider vertex \
  --count 4 \
  --aspect-ratio 9:16 \
  --resolution 4K
```

输入文件夹格式：

```text
C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads\<target_folder>\
  metadata.json
  image-1.png
  image-2.jpg
```

`metadata.json` 可包含：

```json
{
  "title": "标题",
  "description": "描述",
  "hashtags": ["壁纸", "花朵"]
}
```

流程：
1. 读取输入图片和 `metadata.json`。
2. 反推出一个中文壁纸生成提示词。
3. 忽略手机、时间、通知、状态栏、app 图标、社交媒体 UI、手机边框等杂讯。
4. 根据中文提示词生成图片。
5. 为生成图生成 Adobe Stock 的 `Title`、`Keywords`、`Category Code`。
6. 小红书工作流不会执行去背。
7. 输出到 `output/<target_folder>/`。

## API 配置

在仓库根目录创建 `.env`。

推荐 Vertex AI ADC 配置：

```env
GOOGLE_AUTH_MODE=adc
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global

PROMPT_PROVIDER=vertex
METADATA_PROVIDER=vertex
IMAGE_MODEL=gemini-3-pro-image-preview
PROMPT_REVERSE_MODEL=gemini-2.5-flash
METADATA_MODEL=gemini-2.5-flash

IMAGE_COUNT=4
IMAGE_ASPECT_RATIO=9:16
IMAGE_RESOLUTION=4K
```

运行前需要：

```bash
gcloud init
gcloud auth application-default login
```

如果你在 Windows PowerShell 里看到 `gcloud` not recognized，说明当前 PowerShell 没有找到 Google Cloud CLI。可以打开 Windows 开始菜单里的 **Google Cloud SDK Shell**，然后运行：

```powershell
gcloud auth application-default login
```

也可以在 PowerShell 里直接调用 `gcloud.cmd`：

```powershell
& "C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" auth application-default login
```

如果这个路径不存在，再试：

```powershell
& "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd" auth application-default login
```

ADC 登录完成后，回到项目终端重新运行 Python 命令。

如果你的项目支持 Vertex API key：

```env
GOOGLE_AUTH_MODE=api_key
VERTEX_API_KEY=your-vertex-api-key
```

如果 `.env` 里已经有 `VERTEX_API_KEY`，并且你不想使用 ADC，只需要把认证模式改成：

```env
GOOGLE_AUTH_MODE=api_key
```

如果使用 OpenRouter：

```env
OPENROUTER_API_KEY=your-openrouter-api-key
PROMPT_PROVIDER=openrouter
METADATA_PROVIDER=openrouter
```

如果使用 NVIDIA NIM：

```env
NIM_API_KEY=your-nim-api-key
PROMPT_PROVIDER=nim
METADATA_PROVIDER=nim
```

Provider 可选值：
- `vertex`
- `openrouter`
- `nim`

注意：`vertex` 是 Vertex AI，不是 Google AI Studio API。

## 小红书工作流参数

```bash
python xhs_wallpaper_workflow.py "<target_folder>" \
  --prompt-provider vertex \
  --prompt-model gemini-2.5-flash \
  --metadata-provider vertex \
  --metadata-model gemini-2.5-flash \
  --model gemini-3-pro-image-preview \
  --count 4 \
  --aspect-ratio 9:16 \
  --resolution 4K \
  --temperature 1.0 \
  --top-p 0.95
```

参数优先级：

```text
CLI 参数 > .env > 内置默认值
```

常用参数：
- `--count`：生成图片数量，默认 `4`
- `--aspect-ratio`：画幅比例，默认 `9:16`
- `--resolution`：分辨率，默认 `4K`
- `--prompt-provider`：提示词反推 provider
- `--metadata-provider`：Adobe Stock 元数据 provider
- `--overwrite`：允许覆盖已有 `output/<target_folder>/`

## 输出目录

小红书工作流输出：

```text
output/<target_folder>/
  chinese_prompt.txt
  xhs_source_metadata.json
  xhs_source_images.json
  prompt.txt
  run_config.json
  session_log.txt
  session_log.json
  metadata.txt
  adobe_stock_metadata.csv
  img-<n>.png
```

文件说明：
- `chinese_prompt.txt`：反推出来的中文生成提示词
- `prompt.txt`：实际用于生成图片的提示词
- `run_config.json`：本次运行参数
- `metadata.txt`：每张生成图的原始 Adobe Stock 元数据输出
- `adobe_stock_metadata.csv`：Adobe Stock 上传 CSV
- `session_log.txt` / `session_log.json`：运行日志，适合 AI Agent 审计
- `img-<n>.png`：原始生成图

小红书工作流不会运行 background removal，`adobe_stock_metadata.csv` 会直接引用生成出来的 `img-<n>.png`。

## AI Agent / Codex 自动化用法

Codex 应该调用稳定 CLI，而不是临时拼流程：

```bash
python xhs_wallpaper_workflow.py "<absolute-xhs-target-folder>" \
  --prompt-provider vertex \
  --metadata-provider vertex \
  --count 4 \
  --aspect-ratio 9:16 \
  --resolution 4K \
  --overwrite
```

Agent 运行约束：
- 输入必须是绝对路径。
- 每次运行只处理一个 target folder。
- 如果允许覆盖输出，才传 `--overwrite`。
- Google/Gemini 模型只能走 Vertex AI。
- 不要使用 Google AI Studio / Gemini Developer API。
- 运行结束后检查 `session_log.json`、`adobe_stock_metadata.csv` 和生成图片是否存在。

## 测试

```bash
python -m pytest -v
```

## Batch XHS workflow + Adobe upload

Use `xhs_wallpaper_batch_workflow.py` when one parent folder contains multiple XHS note folders. The script processes only direct child folders that contain both `metadata.json` and at least one supported image file.

Input shape:

```text
<xhs-download-root>/
  note-folder-a/
    metadata.json
    image_1.jpeg
  note-folder-b/
    metadata.json
    image_1.jpeg
```

Each valid child folder is processed one by one with the same logic as `xhs_wallpaper_workflow.py`. After every child folder succeeds, the script creates one combined Adobe upload staging folder:

```text
output/_batch_<source-root-name>_<timestamp>/adobe_upload/
  adobe_stock_metadata.csv
  <folder-prefix>-img-1.png
  <folder-prefix>-img-2.png
  ...
```

The staging step rewrites duplicate filenames such as `img-1.png` into unique names, and rewrites the combined CSV so every `Filename` matches the copied upload image.

By default, successfully processed child folders are moved into:

```text
<xhs-download-root>/DONE_<timestamp>/
```

This lets you quickly tell which XHS folders are already done. Use `--no-move-done` if you want to keep the source folders in place.

The batch script supports parallel child-folder processing with `--max-workers`. Vertex AI can accept concurrent requests, but real throughput is still controlled by your project, region, model, and Vertex AI quota/throughput availability. Start with `--max-workers 2`. If you see `429 RESOURCE_EXHAUSTED`, reduce the worker count or request more quota / provisioned throughput.

Example with an already-running Edge CDP session:

```bash
python xhs_wallpaper_batch_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads" \
  --prompt-provider vertex \
  --metadata-provider vertex \
  --count 2 \
  --aspect-ratio 9:16 \
  --resolution 4K \
  --overwrite \
  --max-workers 2 \
  --adobe-cdp http://127.0.0.1:9222 \
  --adobe-file-type illustrations
```

Useful flags:

- `--skip-adobe-upload`: process all XHS folders and prepare the combined upload folder, but do not open Adobe.
- `--max-workers <n>`: process up to `<n>` child folders at the same time. Default is `1`.
- `--no-move-done`: do not move successfully processed child folders into `DONE_<timestamp>`.
- `--adobe-dry-run`: call `tools/adobe_upload_playwright.mjs` in dry-run mode after preparing the combined upload folder.
- `--adobe-cdp`: connect to Edge / Chrome started with `--remote-debugging-port=9222`.
- `--adobe-user-data-dir`: let Playwright launch a persistent browser profile instead of CDP.
- `--adobe-file-type photos|illustrations`: Adobe File type, default `illustrations`.
- `--no-adobe-mark-ai`: do not check Adobe's generative AI checkbox.
- `--adobe-mark-fictional`: check People and Property are fictional when visible.
- `--no-adobe-save-work`: do not click Adobe Save work.

The Adobe stage runs:

```bash
npm run adobe-upload -- --csv <combined-csv> --images <combined-upload-folder> ...
```

It uploads all generated images, uploads the combined CSV, verifies metadata appears, optionally sets file type / AI flags / draft save, and intentionally does not click Adobe's final `Submit <n> file(s)` button.

## XHS Playwright download automation

Use `tools/xhs_download_playwright.mjs` when Edge already has:

- XHS login state
- Tampermonkey installed
- `XHS-Downloader.js` installed and enabled
- site download permissions already configured

This script connects to the existing Edge session through CDP, opens the XHS board, triggers the Tampermonkey downloader, selects notes, waits for completion, and prints the downloaded folder paths.

### 复用 Edge automation profile

建议为 XHS + Adobe 自动化固定使用一个独立 Edge profile。这个 profile 会保存：

- 小红书登录状态
- Adobe Contributor 登录状态
- Tampermonkey 和 `XHS-Downloader.js`
- 小红书下载目录的文件系统授权

Windows 推荐 profile：

```text
%USERPROFILE%\.edge-xhs-adobe-automation
```

运行 Playwright 脚本前，先用这个 profile 启动 Edge，并打开 CDP 端口：

```powershell
cmd /c start "" msedge --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.edge-xhs-adobe-automation" --no-first-run --new-window "https://www.xiaohongshu.com/board/69da0e110000000017020f9b?source=web_user_page" "https://contributor.stock.adobe.com/en/uploads"
```

之后所有浏览器自动化命令都连接这个已经打开的 Edge：

```text
--cdp http://127.0.0.1:9222
```

不要随便换 `--user-data-dir`。换 profile 等于重新开始：小红书登录、Adobe 登录、Tampermonkey 脚本、下载目录授权都不会继承。

新的 profile 第一次跑小红书下载时，Edge 可能会弹两层权限：

1. `Select where this site can save changes`：选择小红书下载根目录，例如 `C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads`。
2. `Allow this site to edit files?`：点击 `Allow`。

这两个是浏览器/系统安全权限，不是普通网页 DOM，Playwright 不能直接稳定操作。Windows 首次初始化如果想完全自动化，可以传 `--auto-folder-permission`；它会启动 `tools/windows_edge_file_permission_helper.ps1`，用 Windows UI Automation 填入 folder picker，并点击 `Allow`。

授权一次后，只要继续使用同一个 Edge profile、同一个网站、同一个文件夹，通常之后会自动复用。`--auto-folder-permission` 可以保留在命令里；如果没有弹窗，helper 超时退出，XHS 脚本继续跑。helper 日志会写到 `final_runs/xhs_download/run_*/windows_folder_permission_helper.log`。

当 XHS 下载命令结束时，会打印 `XHS_DOWNLOAD_STAGE_COMPLETE`。这表示下载阶段结束；如果要继续生图和 Adobe 上传，下一步运行 `xhs_wallpaper_batch_workflow.py`。

当前最新版 `XHS-Downloader.js` 会复用之前已经授权的 directory handle，因此日常运行可以使用 `--background`，不会每次重新调用 `showDirectoryPicker()`。修改 `XHS-Downloader/static/XHS-Downloader.js` 后，需要把最新版文件内容同步更新到 Tampermonkey 已安装的 userscript。

首次设置或修改下载目录：

```powershell
cmd /c npm run xhs-download -- --board-url "<board-url>" --download-root "D:\new-xhs-download-root" --note-count 1 --cdp http://127.0.0.1:9222 --auto-folder-permission --reset-folder-permission
```

目录授权完成后的日常后台运行：

```powershell
cmd /c npm run xhs-download -- --board-url "<board-url>" --download-root "D:\new-xhs-download-root" --note-count 1 --cdp http://127.0.0.1:9222 --background
```

`--background` 只保证 Playwright 不主动调用 `bringToFront()`。当 XHS userscript 使用 `window.open()` 创建 helper tab 时，Edge 仍可能自行切换 tab；Playwright 无法可靠禁止浏览器控制的 popup/tab 激活。为了不影响日常操作，建议把 dedicated automation profile 放在独立 Edge 窗口或另一个 Windows 虚拟桌面。可使用以下后台执行参数启动：

```powershell
cmd /c start "" msedge --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.edge-xhs-adobe-automation" --no-first-run --new-window --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding "https://www.xiaohongshu.com/board/69da0e110000000017020f9b?source=web_user_page" "https://contributor.stock.adobe.com/en/uploads"
```

Adobe 上传适合后台运行，前提是 Contributor tab 已经打开。Adobe 脚本会复用已有 `contributor.stock.adobe.com` tab，使用 Playwright file chooser interception 而不是 Windows 原生文件选择器，不调用 `bringToFront()`，也不会关闭通过 CDP 连接的 Edge。

如果 `--cdp http://127.0.0.1:9222` 报 `ECONNREFUSED`，说明当前没有带 `--remote-debugging-port=9222` 启动的 Edge。重新运行上面的 Edge 启动命令。

Then run:

```powershell
cmd /c npm run xhs-download -- --board-url "https://www.xiaohongshu.com/board/69da0e110000000017020f9b?source=web_user_page" --download-root "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads" --note-count 1 --cdp http://127.0.0.1:9222 --auto-folder-permission --background
```

下载脚本现在默认使用保守安全模式：复用已经打开的相同专辑页面而不重复刷新；每个页面操作后使用固定冷却时间；单次最多处理三个笔记；检测到操作频繁、账号风险或验证提示时立即停止。这些措施用于减少不必要的平台负载，不用于绕过小红书的检测或限制。出现警告后，应停止自动化并人工检查页面和账号状态，再决定是否重试。

Useful flags:

- `--note-count <n>`：单次选择的笔记数量。默认 `1`，每次最多 `3`。
- `--action-delay <ms>`：页面操作后的固定冷却时间。默认和最小值均为 `5000`。
- `--navigation-cooldown <ms>`：确实需要导航到专辑页面时的冷却时间。默认和最小值均为 `10000`。
- `--cdp <url>`: connect to an existing Edge / Chrome session.
- `--user-data-dir <path>`: launch a persistent Edge profile when CDP is not used.
- `--background`: 普通 Playwright 网页操作时不主动切换到 XHS tab。首次 Windows 文件夹权限弹窗仍可能短暂抢占焦点。
- `--auto-folder-permission`: 仅 Windows。启动 PowerShell UI Automation helper，处理 Edge 首次文件夹选择和 `Allow` 权限弹窗。
- `--reset-folder-permission`: 清除 XHS-Downloader 已保存的 directory handle，并重新选择 `--download-root`。需要同时传 `--auto-folder-permission`，重设时会短暂把 XHS 切到前台。
- `--folder-permission-timeout <seconds>`: helper 超时时间，默认跟随 `--timeout`。
- `--dry-run`: validate Playwright and the download folder without opening XHS.

After XHS download succeeds, run the batch workflow:

```powershell
python xhs_wallpaper_batch_workflow.py "C:\Users\tanke\OneDrive\Desktop\xhs-files-downloads" --count 2 --aspect-ratio 9:16 --resolution 4K --overwrite --max-workers 2 --adobe-cdp http://127.0.0.1:9222 --adobe-file-type illustrations
```
