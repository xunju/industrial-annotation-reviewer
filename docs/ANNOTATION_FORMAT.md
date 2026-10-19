# LabelMe 格式约定

最小结构：

```json
{
  "version": "5.0.0",
  "flags": {},
  "shapes": [],
  "imagePath": "sample.png",
  "imageData": null,
  "imageWidth": 640,
  "imageHeight": 640
}
```

每个 shape 包含 `label`、`points`、`shape_type`、`group_id`、`flags`。坐标单位为图片像素。矩形为两个对角点或四点表示；点为一个 `[x,y]`；轮廓多边形至少三个点；LabelMe 圆为中心点和半径上的一点。

| 类型 | 标签和顺序 |
| --- | --- |
| 圆桌 | `circle`（支持别名，见 README） |
| 方桌 | `desk`，`lt → rt → rb → lb` |
| 螺栓 | `screw`，`top_left → top_right → bot_right → bot_left` |
| 垫片轮廓 | `wai` 外轮廓，`nei` 内孔 |
| 垫片六点 | `shim` 框，`wai_1/wai_2/wai_3`，`nei_1/nei_2/nei_3` |

同一实体使用相同的 `group_id`，实体之间使用不同 ID。方桌四角要求图像坐标中的顺时针，`lt` 取 `x+y` 最小的角；这是一种可修改的项目约定。螺栓按标签名连线，不按当前屏幕位置重命名。

完成记录分别保存在 `round_table_review`、`square_table_review`、`quality_review`。编辑器保留无关 JSON 字段，但它不是所有 LabelMe 插件或自定义扩展的通用编辑器。只打开目录不会自动验证所有标注的视觉正确性。
