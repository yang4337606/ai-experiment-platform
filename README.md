# AI 自动化实验平台

基于 AI 的自动化实验平台，用于自动完成虚拟机环境准备、软件安装测试、日志分析、问题修复和测试报告生成的全流程自动化。

## 功能特性

- **虚拟机管理**: VM 创建/配置/快照自动化
- **自动化安装**: 远程执行安装脚本，多层级验证
- **AI 日志分析**: 根因分析 + 自动修复策略
- **Git 分支管理**: 独立修复分支，[AI-FIX] 规范化 commit
- **自动回滚重试**: 回滚 VM 至干净快照，最多重试 10 次
- **测试报告**: 自动生成包含概况/执行记录/问题修复/验证结果的完整报告
- **微信通知**: 企业微信 Webhook 通知

## 快速启动

```bash
pip install -r requirements.txt
python app.py
```

访问 http://localhost:3000

## 技术栈

- **后端**: Python Flask + SQLAlchemy + SQLite
- **前端**: 单页应用 (HTML/CSS/JS)
- **工作流**: 状态机驱动的后台线程引擎

## 项目结构

```
├── app.py              # Flask 应用主入口 + API 路由
├── models.py           # SQLAlchemy 数据模型
├── workflow.py         # 状态机工作流引擎
├── templates/
│   └── index.html      # 前端单页应用
├── requirements.txt
└── README.md
```
