# 油菜表型拍照测量工具

一个面向油菜单株照片的 H5/PWA 测量工具，支持手机和电脑浏览器使用。

## 功能

- 上传图片或手机拍照
- 标尺校准
- 两点测量株高、主花序高、主花序长度
- 三点测量分支角
- 点击计数一级分枝
- 保存样品信息和测量结果
- 导出 Excel
- 导出带标注图片
- 支持 PWA 添加到手机主屏幕

## 本地运行

```bash
npm install
npm run dev
```

## 构建

```bash
npm run build
npm run preview
```

## GitHub Pages 部署

仓库推送到 GitHub 后，Actions 会自动构建并发布 `dist`。在仓库设置中启用 Pages，Source 选择 **GitHub Actions**。

## 使用建议

- 每张照片优先对应一株样品。
- 植株和标尺尽量在同一平面。
- 标尺真实长度要固定并清楚记录。
- 分支计数建议只数一级分枝，点在分枝与主茎连接位置。
