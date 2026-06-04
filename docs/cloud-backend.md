# 云端后端部署

这个后端用于：

- 生成籽粒候选点；
- 使用已经训练好的 YOLO 权重辅助识别；
- 从 Supabase 拉取当前用户的云端记录和籽粒照片训练新模型。

前端 GitHub Pages 仍然使用：

```text
https://sirius0509.github.io/rapeseed-pheno-tool/
```

云端后端部署成功后，只需要在前端的“识别服务地址”填写云端 HTTPS 地址。

## 推荐第一阶段：Hugging Face Docker Space

适合原因：

- 地址固定，手机和电脑都能访问；
- 比普通静态网站更适合 Python、OpenCV、YOLO 后端；
- 不依赖你电脑的局域网 IP；
- 数据主体仍然存在 Supabase，后端只负责识别和训练。

限制：

- 免费 CPU 训练会慢；
- Space 休眠后第一次访问会有冷启动；
- 长期大量训练建议后续换 GPU Space 或独立云服务器。

## 创建步骤

1. 打开 Hugging Face，创建一个新的 Space。
2. SDK 选择 `Docker`。
3. Visibility 可以先选 `Private`。
4. 把本仓库的 `Dockerfile`、`backend/app.py`、`backend/requirements.txt`、`backend/yolo11n.pt` 和当前 `best.pt` 按仓库结构上传，或者直接把整个 GitHub 仓库接到 Space。
5. Space 构建完成后，访问：

```text
https://你的-space名称.hf.space/api/health
```

如果返回：

```json
{"ok": true}
```

说明后端部署成功。

## 前端怎么填

在网页的“识别服务地址”填写：

```text
https://你的-space名称.hf.space
```

不要加 `/api/health`。

之后：

- 手机拍照保存数据到 Supabase；
- 点击“生成候选点”时，调用云端后端；
- 点击“用 Supabase 云端数据训练”时，云端后端会拉取当前账号的云端数据训练。

## 重要说明

云端训练能否成功，取决于 Supabase 里是否有：

- 记录数据；
- 籽粒照片 URL；
- 校正后的籽粒点位。

如果只有角果长度，或者只有籽粒总数但没有点位，不能训练 YOLO 检测模型。
