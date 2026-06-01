# 泵零部件报价合理性分析系统

基于实时期货价格的耐酸泵零部件报价分析工具。

## 功能
- 8种零部件独立价格估算（含底座）
- 实时上期所期货数据（铜/铝/镍/不锈钢/橡胶）
- 供应商报价录入与偏差分析
- 电机/密封子类型选择（防爆、变频、双端面等）
- 历史成交记录参考

## 本地运行

```bash
pip install -r requirements.txt
python app.py
# 浏览器打开 http://localhost:5000
# 用 @danaipumps.cn 邮箱登录
```

## 部署到 Railway

1. 把代码推送到 GitHub
2. 在 [Railway](https://railway.app) 用 GitHub 账号注册
3. New Project → Deploy from GitHub repo → 选择本仓库
4. Railway 自动检测 Python + Procfile，一键部署
5. 获得 `https://xxx.railway.app` 公开地址

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask 密钥 | `pump-secret-key` |
| `DATABASE_URL` | 数据库连接 | `sqlite:///pump_quote_analysis.db` |
