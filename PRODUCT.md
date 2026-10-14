# Promo Scanner

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
FastAPI + 原生 HTML/CSS/JavaScript，用户确认。本机单用户运行，Python 使用 uv 管理。

## Product Purpose
为现有 ChatGPT Business 促销码扫描脚本提供简易前端。用户可以在页面配置所有连接和扫描设置，控制任务，并查看执行数据和成果。

## Operating Context
用户确认在本机使用。实际请求需要用户自己的 ChatGPT Token；自动切换节点需要运行中的 Clash 兼容控制器。离线候选预览不依赖这些连接。

## Capabilities and Constraints
任务配置、候选预览、已知码扫描、候选发现、矩阵发现、暂停/继续/停止、日志、历史任务、结果筛选和导出。运行设置独立于旧 CLI 配置文件，用户不需要手动编辑文件。

## Brand Commitments
中文界面。前端设计遵循用户指定的 https://github.com/pbakaus/impeccable 指导。

## Evidence on Hand
`known_codes.json` 是仓库中已有历史数据，必须明确标记其更新日期和未重新验证的状态。实际任务结果与历史参考库分开显示。

## Product Principles
- 配置和操作在页面完成。
- 显示真实运行状态，保留失败、跳过和中断结果。
- 凭据不出现在响应、日志或导出中。
- 本机启动不需要额外前端构建工具。
