# HealthMate 骨骼多标签模型训练指南

## 1. 当前状态

项目已经具备完整的可训练链路，但仓库不包含第三方原始数据、训练权重或正式准确率：

- 统一12关节骨骼协议和人体尺度归一化。
- ST-GCN-lite时空图网络。
- 动作模式与观察区域两个多标签任务头。
- 按受试者和动作实例防泄漏划分。
- 验证集逐标签阈值校准。
- TorchScript导出、SHA-256和模型卡。
- Worker本地模型加载、协议校验、哈希校验和规则回退。

必须先取得数据使用权限并冻结测试集，才能训练和报告指标。

## 2. 标签协议

动作模式：

- `knee_dominant`
- `bilateral_lower_body`
- `unilateral_lower_body`
- `elbow_flexion_extension`
- `horizontal_upper_body`
- `static_or_low_amplitude`

观察区域：

- `lower_body`
- `upper_body`
- `trunk_stability`

这些是视频可观察语义，不等同于动作名称、目标肌肉或实际训练效果。目标部位仍由后端知识图谱提供。

## 3. 安装训练环境

推荐在纯英文路径创建独立Python 3.12环境。根据训练机器的CPU/CUDA版本选择官方PyTorch构建，然后安装：

```powershell
cd ai-worker
python -m venv .venv-training
.\.venv-training\Scripts\python.exe -m pip install -r requirements-training.txt
```

正式实验必须保存Python、PyTorch、CUDA、显卡驱动和依赖锁定信息。

## 4. 准备清单

先生成受试者级划分：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_semantic_dataset.py `
  D:\controlled-data\annotations.jsonl `
  D:\controlled-data\manifest-split-v1.jsonl `
  --seed healthmate-competition-v1 `
  --unseen-family unseen_family
```

清单只能使用数据根目录内的相对路径。程序会拒绝绝对路径、`..`目录逃逸、重复样本和同一受试者/实例跨集合。

## 5. 提取骨骼序列

```powershell
.\.venv\Scripts\python.exe scripts\extract_pose_dataset.py `
  D:\controlled-data\manifest-split-v1.jsonl `
  D:\controlled-data `
  D:\controlled-data\manifest-pose-v1.jsonl
```

每个样本会生成压缩NPZ：

```text
skeleton: [帧数, 12, 3]
最后一维: x, y, visibility
```

转换器不会复制或上传原视频。生成文件仍继承原数据集的许可限制。

## 6. 训练与导出

```powershell
.\.venv-training\Scripts\python.exe scripts\train_skeleton_semantics.py `
  D:\controlled-data\manifest-pose-v1.jsonl `
  D:\controlled-data `
  models\skeleton-semantic-v1.pt `
  --epochs 30 `
  --batch-size 16 `
  --seed 20260921
```

输出：

- `skeleton-semantic-v1.pt`：TorchScript推理文件。
- `skeleton-semantic-v1.pt.json`：标签顺序、阈值、清单哈希、验证指标、训练参数和权重哈希。

训练脚本只使用训练集更新权重，只使用验证集选择阈值。测试集必须由单独评测命令读取，不能用于调参。

## 7. Worker启用

在`.env`中配置：

```dotenv
SEMANTIC_MODEL_PATH=D:\HealthMate\models\skeleton-semantic-v1.pt
SEMANTIC_MODEL_SHA256=模型卡中的artifact_sha256
SEMANTIC_MODEL_MIN_CONFIDENCE=0.45
```

然后检查：

```powershell
.\.venv\Scripts\python.exe doctor.py --offline
```

只有以下条件全部满足才会启用训练模型：

- PyTorch可导入。
- 权重文件和模型卡存在。
- 配置哈希与文件一致。
- 模型卡标签顺序与`motion-semantic-v1`完全一致。
- 本次预测达到最低接受置信度。

任一条件失败，Worker自动使用`pose_compositional_rules_v1`，并在结果中记录回退原因。

## 8. 正式验收门禁

模型进入“validated”前至少满足：

- 测试集按受试者隔离。
- 包含未见动作族、不同机位、光照、体型和速度。
- 公布每个标签支持样本数、Macro-F1、Micro-F1和完全匹配率。
- 公布未知动作拒识召回率及覆盖率。
- 模型卡关联清单哈希、权重哈希和代码版本。
- 对失败样本进行人工复核。
- 不将观察区域表述为肌肉激活，不将置信度表述为医学风险。

当前`model_registry`中的`skeleton-stgcn-multilabel/pipeline-v1`状态是`not_trained`，仅表示工程管线已经存在。
