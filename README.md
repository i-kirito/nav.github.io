---
title: "webstack"
date: 2023-03-30T18:50:00
draft: true
web: https://nav.xazz.top/
---

## 这是 Hugo 版 WebStack 主题

本项目是基于**纯静态**的网址导航网站 [webstack.cc](https://github.com/WebStackPage/WebStackPage.github.io) 制作的 [Hugo](https://gohugo.io/) 主题，其中部分代码参考了以下几个开源项目：<br/><br/>


>[https://github.com/liutongxu/liutongxu.github.io](https://github.com/liutongxu/liutongxu.github.io)
>
>[https://github.com/iplaycode/webstack-hugo](https://github.com/iplaycode/webstack-hugo)
>
>[https://www.yuque.com/shenweiyan](https://github.com/shenweiyan/WebStack-Hugo)


## 项目成立于 2023 年 3 月 30日
- 这是本人第一次真正意义上使用 *vercel* 搭建网页，减少自购的阿里云学生云服务器本身的负载。
- 域名托管利用 *Cloudflare* 加速，以保证访问速度和避免备案的麻烦。

## 资源维护

```bash
# 从 Chrome 导出的书签中筛选公开、未重复的网址
python3 scripts/import_chrome_bookmarks.py ~/Downloads/bookmarks.html \
  --output /tmp/nav-bookmark-candidates.yml

# 更新网址在线状态，再生成静态站点
python3 scripts/check_links.py --timeout 10 --workers 24
hugo --minify
```

Chrome 导入脚本会过滤 `localhost`、局域网 IP、`.local` 等本地地址，移除常见跟踪参数，并排除导航站中已经存在的网址。失效但需要留档的网址存放在 `data/archived_links.yml`，不会显示在首页。

## 主题演示地址

- [小 A 猪崽](https://nav.xazz.top/)
  
| 完成度      | 自定义修改 | 
| :---       |   :----:  | 
- [x] 导航栏样式（个人感觉更好看）

### 爱发电支持我 *！*
[微信支付](https://raw.githubusercontent.com/i-kirito/i-kirito/main/img/wechat.png) | [支付宝](https://raw.githubusercontent.com/i-kirito/i-kirito/main/img/alipay.png) | [PayPal](https://www.paypal.com/paypalme/ikirito9)
