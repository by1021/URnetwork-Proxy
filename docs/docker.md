# Docker 部署

项目自带 Dockerfile 与 Compose 配置，无需在宿主机安装 Python。需要 Docker Engine / Docker Desktop（Linux 容器）和 Compose v2+。

默认管理账号为 `admin / admin`，首次登录后请务必修改密码并重启。管理页和代理端口默认发布到宿主机全接口（`0.0.0.0` / `::`），支持外网与局域网直接访问。建议为网关设置代理账号密码，生产暴露建议配合防火墙或 HTTPS 反向代理。

## 首次启动

在项目根目录执行：

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 urnetwork-proxy
```

访问 <http://127.0.0.1:8777>。登录后在配置面板填写上游凭据、修改 `web_username` / `web_password`，保存后立即生效，无需重启容器。

首次启动不需要上游凭据；使用真实代理时再配置。容器配置独立于宿主机根目录的 `config.json`，已有配置迁移见下文。

## 管理与代理端口

| 项目 | 默认值 | 说明 |
| --- | --- | --- |
| 管理入口 | `0.0.0.0:8777` / `[::]:8777` | 默认发布至全接口，支持外网与局域网直连 |
| 代理端口 | `0.0.0.0:31000–31010` / `[::]:31000–31010` | 默认发布至全接口，HTTP、HTTPS、SOCKS5 共用每个网关 TCP 端口 |
| 网关监听地址 | 在控制台选择 `0.0.0.0` / `::` | 容器内回环监听不接受 Docker 转发的连接 |
| 网关端口选择 | `31000–31010` | 端口映射不会自动创建网关，仍需在控制台新建并启动 |

管理页面中的 `web_host` / `web_port` 被镜像启动参数覆盖。要更换宿主机端口，请在根目录创建 `.env`，而不是修改配置面板：

```dotenv
# 如需限制仅宿主机本机访问，可取消注释并指定绑定地址：
# URNETWORK_WEB_BIND=127.0.0.1
# URNETWORK_GATEWAY_BIND=127.0.0.1
URNETWORK_WEB_PORT=8999
URNETWORK_GATEWAY_PORTS=31000-31020
```

```bash
docker compose up -d
```

网关端口支持一个端口或一个连续范围，宿主机和容器保持同号；建议使用大于 1024 的未占用端口，避开容器管理端口 8777。更改映射需要 `up -d` 重建，`restart` 不更新映射。

默认即支持外网与其他设备直连（绑定全网卡）。如需限制访问源，可将对应 `*_BIND` 设置为特定内网 IP 或 `127.0.0.1`。开放外网访问时请务必修改初始管理密码并为网关配置认证。页面内显示的容器 IP 是诊断信息，客户端应使用宿主机的公网/内网 IP 及映射端口。

HTTPS **代理**的自签名证书由镜像内 OpenSSL 自动生成，默认只包含 localhost / 回环地址；它不为管理页面提供 HTTPS。远程 HTTPS 代理需挂载匹配域名/IP 的证书到 `/data`，在配置面板中成对填写容器内 `tls_cert_file` / `tls_key_file` 路径，重启后按[网关指南](gateway-guide.md)配置客户端信任。

## 持久化与权限

| 容器路径 | 内容 |
| --- | --- |
| `/data/config.json` | 上游凭据、管理账号与其他配置 |
| `/data/state/gateways.json` | 网关配置及上游资源归属记录 |
| `/data/state/gateway_auth.json` | 网关代理认证 |
| `/data/state/gateway_tls.crt`、`gateway_tls.key` | 自动生成的 HTTPS 代理证书和私钥 |
| `/data/state/credentials.json` | 从旧部署迁移的凭据（如有） |

Compose 默认将持久化数据挂载至宿主机本地 `./data` 目录（并在本地自动生成 `./data/config.json`），容器重建后数据保留；也可在 `.env` 中通过 `URNETWORK_DATA_DIR=urnetwork-data` 切回命名卷。`restart`、重建和普通 `docker compose down` 保留数据；`down --volumes` 会删除卷及其中的凭据和网关，日常更新不要使用。

镜像以 UID/GID `10001:10001` 运行；Compose 开启只读根文件系统，仅 `/data` 与临时目录可写。首次创建命名卷时从镜像复制无凭据模板，已有卷保持原内容。更换项目目录名或 Compose 项目名会使用另一个卷，运维时保持项目名一致。

若改用宿主机目录挂载，挂载**整个目录**到 `/data`，并为 UID/GID 10001 准备写权限。空目录会使用镜像中的配置模板回退，第一次从配置面板保存后生成 `/data/config.json`。不要只绑定挂载 `config.json`：配置保存使用临时文件加原子替换，单文件挂载会导致保存失败。

新增环境变量 `URNETWORK_CONFIG_FILE` 可指定配置路径；容器默认 `/data/config.json`，普通脚本未设置或设为空时仍使用项目根目录 `config.json`。`--state-dir` 控制状态目录，两者相互独立。

## 迁移现有配置

以下为 Bash 示例。先停止旧 Python/Systemd 实例与当前 Compose 服务，避免两个实例同时管理同一批上游资源；先备份已有 Docker 卷再导入。配置导入会覆盖当前 Docker 配置：

```bash
docker compose stop
docker compose run --rm --no-deps -T --entrypoint python urnetwork-proxy -c \
  'import sys; from core.config import parse_config, validate_config, save_config; save_config(validate_config(parse_config(sys.stdin.read())))' \
  < config.json
```

如需迁移旧 `.local` 中的网关、认证及证书，再执行（目录须存在）：

```bash
tar -C .local --exclude=manager.lock --exclude=docker-backups -cf - . | \
  docker compose run --rm --no-deps -T --entrypoint tar urnetwork-proxy \
  -C /data/state --no-same-owner -xf -
docker compose up -d
```

旧网关若监听 `127.0.0.1`，需在控制台改为全接口监听；超出映射范围的端口需同步调整 `.env`。自定义证书路径也要改为容器内路径。

## 备份、恢复与更新

备份含明文凭据和私钥，请限制访问。以下 Bash 命令停止服务取得一致快照，只读取数据卷：

```bash
umask 077
mkdir -p .local/docker-backups
docker compose stop
docker compose run --rm --no-deps -T --entrypoint tar urnetwork-proxy \
  -C /data -czf - . > .local/docker-backups/data.tar.gz
docker compose up -d
```

恢复前先备份目标卷，并停止服务；解压会覆盖同名文件，不会删除额外文件，推荐恢复到新部署的数据卷：

```bash
docker compose stop
docker compose run --rm --no-deps -T --entrypoint tar urnetwork-proxy \
  -C /data --no-same-owner -xzf - < .local/docker-backups/data.tar.gz
docker compose up -d
```

更新代码后重新构建并启动，保留原数据卷：

```bash
docker compose build --pull
docker compose up -d
docker compose ps
```

## 单独使用 Docker 或 CLI

不使用 Compose 时（这里的 `urnetwork-data` 是独立卷，不是 Compose 带前缀的卷）：

```bash
docker build -t urnetwork-proxy:local .
docker run -d --name urnetwork-proxy --init --restart unless-stopped --stop-timeout 150 \
  -p 8777:8777 -p 31000-31010:31000-31010 \
  -v urnetwork-data:/data urnetwork-proxy:local
```

镜像入口支持原有 CLI 参数。一次性命令不发布管理和网关端口：

```bash
docker compose run --rm --no-deps urnetwork-proxy --help
docker compose run --rm --no-deps urnetwork-proxy --status
```

运行会修改配置或管理上游资源的 CLI 命令前，先停止 Web 服务，完成后再启动。无参数镜像默认启动 Web；需要交互菜单时显式传入原 CLI 参数或覆盖命令。

## 检查与排障

```bash
python3 -m unittest discover -s tests -v
docker compose config --quiet
python3 tests/docker_smoke.py
```

集成测试构建镜像、创建随机项目名和临时卷，检查登录、配置原子保存、代理三协议入口、TLS、非 root 权限、正常停止与重建持久化；最后仅删除本次测试资源。测试不申请真实上游代理。

| 现象 | 检查 |
| --- | --- |
| `unhealthy` | 查看 `docker compose logs`，执行 `docker compose exec urnetwork-proxy python -m docker.healthcheck`；修改 `allowed_hosts` 后及时重启 |
| 网关不可连接 | 网关已启动、监听全接口，端口在映射范围内，客户端使用宿主机地址 |
| 配置保存报错 | 挂载整个数据目录；目录可被 UID 10001 写入，不使用单文件绑定挂载 |
| 重建后显示初始配置 | 检查 Compose 项目名和卷名是否改变，原卷可能仍存在 |
| 修改端口后仍是原端口 | 更改 `.env` 的宿主机映射，再执行 `docker compose up -d` |

健康检查只代表管理 HTTP 服务存活，不代表上游账号有效或代理出口可用；Docker 的重启策略不会因单纯 `unhealthy` 自动重启容器。镜像用 SIGINT 触发应用正常关闭，Compose 最多等待 150 秒后结束容器。

部署参数依据：[Dockerfile](https://docs.docker.com/reference/dockerfile/)、[Compose 服务](https://docs.docker.com/reference/compose-file/services/)、[Docker 数据卷](https://docs.docker.com/engine/storage/volumes/)。
