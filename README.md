# JNU ePortal 自动登录

暨南大学(JNU)校园网锐捷 ePortal 认证的自动登录与保活脚本。

掉线自动重登、WiFi 重连自动恢复、定时保活防踢下线。**只需提供校园网账号和密码即可使用**,无需从浏览器提取任何 Cookie 密文。

## 功能

- 循环探测网络,被门户拦截时自动重新登录
- 登录成功后定时发送 keepalive,防止空闲被踢下线
- WiFi 完全断开时持续重试,重连后自动恢复
- 自动从门户登录页获取 RSA 公钥并完成密码加密(完全复刻官方页面 JS 行为)
- 仅使用 Python 标准库,**零第三方依赖**;单实例锁防止重复运行
- 提供自检(--selftest)、单次测试(--once)、主动下线(--logout)等模式

## 快速开始

要求:Python 3.8+ (Windows / macOS / Linux)。

~~~bash
# 1. 下载本仓库,进入目录
git clone https://github.com/ZehChou/jnu-autologin.git
cd jnu-autologin

# 2. 初始化配置:按提示输入校园网账号和密码
python jnu_autologin.py --configure

# 3. 启动守护模式(一直运行)
python jnu_autologin.py
~~~

也可以把 config.example.json 复制为 config.json 后编辑填写。

> 由 AI 助手代为配置时,只需告诉助手你的校园网账号和密码,让它帮你把 config.json 写好即可。

## 密码加密说明

门户登录页的密码加密逻辑(官方 js/security.js + js/admin/login.js)是:密码反转 → 按 16 位小端分块(1024 位密钥为 126 字节/块)→ **无 PKCS#1 填充**的裸 RSA 模幂 → 十六进制输出。本脚本用纯 Python 精确复刻该算法,并在每次登录时实时获取门户公钥,因此**与浏览器行为完全一致**。

- password_mode: "auto"(默认):优先 RSA 加密;若获取公钥失败或密码含非 ASCII 字符,自动回退明文提交
- password_mode: "plain":始终以明文提交(门户支持 passwordEncrypt=false)
- password_mode: "cookie":直接使用浏览器 Cookie 中的 EPORTAL_COOKIE_PASSWORD 密文(无需明文密码)

## 配置项(config.json)

| 字段 | 说明 |
|---|---|
| username | 校园网账号/学号 |
| password | 校园网密码(仅保存在本机) |
| password_mode | auto(默认)/ plain / cookie |
| password_encrypt | cookie 模式必填:浏览器 Cookie 的 EPORTAL_COOKIE_PASSWORD |
| portal | 门户地址,默认暨南大学 ePortal,其他学校可改 |
| check_interval | 在线时探测间隔(秒,默认 5) |
| retry_interval | 登录失败重试间隔(秒,默认 3) |
| keepalive_interval | 保活间隔(秒,默认 60) |

## 命令行

| 命令 | 说明 |
|---|---|
| python jnu_autologin.py --configure | 交互式初始化配置(生成 config.json) |
| python jnu_autologin.py | 守护模式(推荐,一直运行) |
| python jnu_autologin.py --selftest | 端到端自检:下线 → 自动重登 |
| python jnu_autologin.py --once | 只跑一次探测 + 登录 |
| python jnu_autologin.py --logout | 主动下线 |

## Windows 开机自启

1. 双击 jnu_autologin.vbs 可静默后台启动(与脚本同目录,自动定位 Python)
2. 要开机自启:把 jnu_autologin.vbs 复制到启动文件夹
   Win + R 输入 shell:startup,粘贴进去即可

脚本有单实例锁,重复启动会自动退出,不会出现多个进程。

## 常见问题

**登录返回“设备未注册,请在ePortal上添加认证设备”**
说明该账号还未在 ePortal 系统注册当前设备。请用浏览器打开任意 http 网页跳到认证页,按提示添加设备后,脚本即可正常登录。

**提示 RSA 加密不可用并回退明文**
多为门户页面结构变化导致公钥解析失败,或密码含非 ASCII 字符。可检查 portal 配置;必要时改用 password_mode: "cookie"。

**如何获取 Cookie 密文**
浏览器登录认证页后,F12 → Application → Cookies,复制 EPORTAL_COOKIE_PASSWORD 的值填入 password_encrypt(仅在 cookie 模式需要)。

## 安全与隐私

- 账号密码仅保存在本机 config.json 中,该文件已被 .gitignore 排除,**请勿提交或分享**
- 本脚本不向任何第三方发送数据,仅与你的校园网认证服务器通信
- 请遵守学校网络使用规定,仅限本人设备使用

## 免责声明

本项目仅供学习与个人网络管理使用。使用自动登录产生的任何后果(包括但不限于违反校园网使用规定、账号被限制)由使用者自行承担。本项目与暨南大学、锐捷网络无任何隶属关系。

## License

[MIT](LICENSE) © [ZehChou](https://github.com/ZehChou)
