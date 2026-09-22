# 三连阴缩量选股

`index.html` 为 `three_down.py` 的静态页面。页面通过 GitHub API 触发 `.github/workflows/three-down.yml`，扫描任务由 GitHub Actions 托管运行，完成后会提交 CSV 与 JSON 结果至 `three-down/results/`。

## 一次性配置

1. 在仓库 **Settings > Actions > General** 将 Workflow permissions 设置为 **Read and write permissions**。
2. 在 GitHub Pages 中访问 `/three-down/`。
3. 创建仅限此仓库的短期 fine-grained token，并授予 **Actions: Read and write** 与 **Contents: Read** 权限。

Token 只保留在当前页面内存，不会写入仓库或浏览器存储。

选股范围使用仓库中 `stock-similarity/a-share-universe.txt` 的版本化 A 股清单，避免 GitHub Actions 在运行时依赖 AkShare 的交易所列表接口。

## 参数校验

- `recent` 不要求日期。
- `hist` 必须填写起始交易日；结束交易日可选。
- `sim` 必须填写起始交易日和结束交易日。
- 其余脚本参数均为可选；空值不会传给脚本，因此沿用 `three_down.py` 默认值。
