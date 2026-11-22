# 国内每日大小羊毛看板（零成本自托管版）

一套 **0 服务器费、0 大模型 API 消耗、手机端类原生体验** 的国内羊毛监控与分级看板。基于 GitHub Actions 定时抓取 + 纯规则评审打分 + Cloudflare Pages / GitHub Pages 静态托管。

## 核心防风控与评审设计
1. **每日 3 次黄金窗口调度（避免高频触发风控）**：仅在北京时间 **09:15、13:45、20:15** 自动运行（避开深夜无效抓取），并内置启动随机抖动（15–75 秒）、单源间隔延迟、HTTP `ETag / Last-Modified` 304 缓存与连续失败 3 次自动冷却 24 小时熔断机制。
2. **信源分级与防软文**：自动区分 `T1 官方直出`、`T2 社区实测`、`T3 单源线报`。**硬规则：T3 单源活动绝不评 S 级**，疑似含返利/推广口令的活动自动挂警示标记。
3. **透明 0–100 纯规则评审**：无需大模型 API，按收益力度、折扣率、信源可信度与个人卡包/城市匹配度综合打分，自动排除 AI 免费额度、VPS/云服务器、拉人头助力砍价及低性价比任务。
4. **兼顾“每天看”与“隔几天补看”**：前端自动记住你上次打开看板的时间，隔几天点开时自动为新增 S/A 级活动挂上 `NEW` 角标；过期或标黄活动自动沉入历史归档。

## 5 分钟零成本部署步骤

### 1. 上传到你的 GitHub 仓库
在 GitHub 新建一个仓库（公开或私有均可），把本压缩包里的全部文件上传到仓库根目录。

### 2. 开启 GitHub Actions 写入权限
进入仓库 **Settings → Actions → General → Workflow permissions**，勾选 **Read and write permissions** 并保存。

### 3. 开启静态页面托管（推荐二选一）
- **推荐 Cloudflare Pages（国内与海外访问都快）**：登录 Cloudflare Dashboard → Workers & Pages → 创建并关联你的 GitHub 仓库 → Build output directory 填 `docs`（构建命令留空）→ 部署完成。
- **或直接用 GitHub Pages**：仓库 **Settings → Pages** → Source 选 `Deploy from a branch`，分支选 `main`，目录选 `/docs` → 保存。

### 4. 自定义你的银行卡、城市与推送（选填）
- 编辑 `config/rules.json` 中的 `user_profile.my_banks` 和 `my_cities`，命中的活动会自动加权并打上徽章。
- 如需 S 级大毛自动推手机，在仓库 **Settings → Secrets and variables → Actions** 中按需添加任意一项即可：
  - iPhone Bark：`BARK_URL`（如 `https://api.day.app/你的Key/`）
  - Telegram：`TG_BOT_TOKEN` 和 `TG_CHAT_ID`
  - 飞书/钉钉/企业微信：`WEBHOOK_URL`
  - 看板网址（推送里附直达链接）：`DASHBOARD_URL`
  - 自建 RSSHub（可选，更稳）：`RSSHUB_BASE_URL`

### 5. 添加到手机主屏幕
用手机浏览器打开部署好的网址，点击分享菜单里的 **「添加到主屏幕」**，之后像原生 App 一样随手点开即看。
