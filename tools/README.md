# 数据集批处理工具

这些脚本来自原本的 `E:/robocup/tools`，用于自动预标注、标注修补、数据集整理和离线质量检查。网页审核工具用于人工确认，批处理脚本用于其前后处理。它们不是比赛官方评测实现，不附带真实数据或模型权重。

## 安装与运行

在仓库根目录使用 Python 3.10 或以上版本：

```sh
python -m pip install -r tools/requirements.txt
python tools/annotate_screw_shim.py --help
```

工具依赖 NumPy、OpenCV 和 ONNX Runtime。部分检查脚本通过共享模块间接加载 ONNX Runtime，因此建议完整安装上述工具依赖。无需将这些依赖加入只使用网页审核工具的环境。脚本之间有直接导入关系，请保留同目录结构，并以 `python tools/脚本名.py` 运行。

## 工具清单

| 脚本 | 用途及写入行为 |
|---|---|
| `annotate_screw_shim.py` | 使用外部 ONNX 模型生成螺钉关键点、垫片轮廓；写入指定输出目录 |
| `unify_round_table_dataset.py` | 整理圆桌数据，统一为 96 点轮廓；生成新数据集与清单 |
| `generate_round_table_roi_640.py` | 从圆桌轮廓生成 640×640 JPG 裁剪图、空零件标注和变换记录；要求新输出目录 |
| `refine_circle_annotations.py` | 根据图像边缘优化圆桌轮廓；写入指定 JSON 输出目录与报告 |
| `refine_selected_circle_contours.py` | 优化指定图片的圆桌轮廓；写入指定输出目录 |
| `resegment_selected_circle.py` | 用外部模型重新分割指定图片；写入指定输出目录 |
| `resample_circle_json_96.py` | 将指定 JSON 的圆桌轮廓重采样为 96 点；直接修改该 JSON |
| `validate_circle_annotations.py` | 检查圆桌标注、图片尺寸、轮廓点等；终端报告 |
| `validate_screw_shim_dataset.py` | 检查螺钉、垫片标注；在数据集目录写入检查报告 |
| `qa_circle_annotations.py` | 生成圆桌标注浏览图、对比图 |
| `qa_screw_shim_annotations.py` | 生成螺钉、垫片标注浏览图 |
| `qa_incomplete_screw_keypoints.py` | 放大显示缺点或带说明标记的螺钉；输入为直接存放 JPG/JSON 的目录 |
| `qa_single_boundary_crops.py` | 为单张图片生成局部边界检查图 |
| `qa_unmatched_shims.py` | 可视化未配对的垫片轮廓 |
| `qa_unmatched_shim_pairs.py` | 生成垫片轮廓配对检查图 |
| `complete_screw_keypoints_geometry.py` | 几何补全螺钉关键点；带 `--apply` 时写回 |
| `repair_missing_screw_keypoints.py` | 模型补全缺失螺钉关键点；带 `--apply` 时写回 |
| `repair_missing_shim_inner.py` | 模型补全垫片内圈；带 `--apply` 时写回 |
| `propose_shim_inner_geometry.py` | 根据几何关系提出内圈候选；带 `--apply` 时写回 |
| `repair_shim_inner_crop_tta.py` | 局部裁剪、多次推理补内圈；带 `--apply` 时写回 |
| `repair_shim_outer_crop_tta.py` | 局部裁剪、多次推理补外圈；带 `--apply` 时写回 |
| `repair_shim_inner_scaled_ellipse.py` | 通过缩放椭圆提出内圈候选；带 `--apply` 时写回 |

修补、轮廓优化和几何推断属于历史实验工具，输出需要人工审核，不应当作真实标注直接用于评测。带 `--apply` 的脚本先不加该选项检查结果；其他写回脚本也应先使用数据副本。输出目录、报告路径不要与原数据重叠，除非你明确需要覆盖。查看每个脚本的 `--help` 以确认参数；各脚本对目录层级的要求不同。

## 常用示例

以下路径均为示例，需要替换成自己的数据和模型路径。对于期望数据集根目录的零件脚本，图片和 JSON 放在其 `images/` 子目录。

```sh
# 原图目录：JPG 和同名 JSON，包含 circle 多边形轮廓
python tools/generate_round_table_roi_640.py ./datasets/round/images ./datasets/round_roi
# 可选：要求来源图片数恰好为某个值
python tools/generate_round_table_roi_640.py ./datasets/round/images ./datasets/round_roi --expected-count 720

# 为裁剪后的桌面图生成零件预标注，模型需自行提供
python tools/annotate_screw_shim.py ./datasets/round_roi/images ./datasets/parts --pose-model ./models/screw.onnx --shim-model ./models/shim.onnx

# 生成离线检查图；这里传入直接包含图片和 JSON 的目录
python tools/qa_screw_shim_annotations.py ./datasets/parts/images ./outputs/qa_parts
python tools/qa_incomplete_screw_keypoints.py ./datasets/parts/images ./outputs/qa_incomplete

# 网页人工审核（回到仓库主启动器）
python run.py --tool parts --dataset ./datasets/parts
```

## 原版归档与完整性

原目录全部 23 个 Python 源文件均已保存：20 个保留在当前目录；两个移除固定路径限制的脚本另存原版在 `archive/`；单图片修补脚本也放在 `archive/`。当前目录共 22 个脚本，归档目录共 3 个。

`original_scripts_manifest.json` 记录每个原文件的 SHA-256 和原版保存位置，用于清理本机前核对。未保存 `__pycache__`，缓存可由 Python 自动重建。

模型权重、真实图片、标注、自动生成的检查图和报告不包含在本次源码归档中。保存代码不等于备份数据或模型。

## 本次归档验证

- 23 个原始源码逐字节及 SHA-256 核对通过；Git 对工具 Python 文件关闭换行转换，以保留原版哈希。
- 22 个当前脚本的 `--help` 均正常退出，所有 Python 文件可编译。
- 使用合成图片验证单图 ROI 生成、已有输出保护、可选图片数量检查、空输入拒绝。
- 使用合成零件验证缺失关键点预览输出，原 JSON 不变。
- 原仓库 6 项 Python 测试及 4 项 JavaScript 测试通过。
- 尚未使用真实数据集或实际 ONNX 权重验证模型推理效果。
