# 正式公网部署

本项目可以运行在本地服务器上，但“本地运行”不等于“互联网可访问”。正式公网访问必须具备以下任一条件：

1. 服务器有可入站的公网 IPv4，并在防火墙/路由器放行 443（必要时将 443 转发到本机）；
2. 使用 Cloudflare Named Tunnel（固定账号、固定 tunnel、固定域名）；
3. 将应用部署到一台有公网 IP 的 VPS/云服务器。

不要使用 `trycloudflare.com` Quick Tunnel 或 LocalTunnel 作为生产入口，它们没有固定连接和可用性保证，连接进程退出就会出现 1033/503。

## VPS/公网主机

将项目放到 `/opt/work-management-tree`，创建 `worktree` 用户，把 `worktree.service` 安装到 `/etc/systemd/system/`，再执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now worktree
sudo systemctl status worktree
```

用 Caddy/Nginx 终止 HTTPS，并将域名反代到 `127.0.0.1:8080`。公网暴露前必须在 `/etc/worktree/worktree.env` 设置 `WMT_PUBLIC_USER` 与 `WMT_PUBLIC_PASSWORD`，并使用强随机密码。

## Cloudflare Named Tunnel

在 Cloudflare 账号中创建固定 tunnel 和 DNS hostname，将 `cloudflared-config.yml` 中的 UUID、凭据路径、域名替换后安装为 systemd 服务。Named Tunnel 的 connector 由 systemd 自动重启；不要再次生成 Quick Tunnel 链接。
