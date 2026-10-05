#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zaka 成人订阅 · 一键换成你自己的地址
════════════════════════════════════════════════════

默认这份 index.json 用的是**相对路径**（依赖写成 "./jar/xxx.jar"），
好处是：不管你用哪家镜像、哪种订阅地址，壳都会自动按你填的地址去拼依赖，
一条订阅地址通吃所有镜像，不用改任何东西。

只有在「你的壳不认相对路径」的时候（症状：大部分源正常，但那一批要
加载 jar / 脚本的源全白），才需要跑本脚本把它变成绝对地址。

用法
────
  1) 先看看要换多少处（不改文件）：
       python3 set-base.py

  2) 一键换成你 GitHub 仓库的镜像绝对地址：
       python3 set-base.py --repo 用户名/仓库名

     默认用 gh-proxy.com。想换别的镜像：
       python3 set-base.py --repo 用户名/仓库名 --mirror ghproxy
       python3 set-base.py --repo 用户名/仓库名 --mirror ghfast
       python3 set-base.py --repo 用户名/仓库名 --mirror llkk
       python3 set-base.py --repo 用户名/仓库名 --mirror jsdelivr

  3) 用自己的域名 / 服务器：
       python3 set-base.py --base https://你的域名/订阅目录

跑完自带自检：换了多少处、有没有残留、引用的文件在不在。
"""
import argparse
import os
import re
import sys

MIRRORS = {
    'gh-proxy':  'https://gh-proxy.com/https://raw.githubusercontent.com/{repo}/main',
    'ghproxy':   'https://ghproxy.net/https://raw.githubusercontent.com/{repo}/main',
    'ghfast':    'https://ghfast.top/https://raw.githubusercontent.com/{repo}/main',
    'llkk':      'https://gh.llkk.cc/https://raw.githubusercontent.com/{repo}/main',
    'jsdelivr':  'https://fastly.jsdelivr.net/gh/{repo}@main',
}
SELF_HOST = 'https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main'
SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ttf', '.woff', '.zip',
            '.apk', '.jar', '.dex', '.mp4', '.flv'}
TEXT_EXT = {'.json', '.txt', '.m3u', '.py', '.js', '.html', '.md', '.cfg', '.xml', '.css'}


def walk_text(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith('.')]
        for f in fn:
            ext = os.path.splitext(f)[1].lower()
            if ext in SKIP_EXT:
                continue
            if ext in TEXT_EXT:
                yield os.path.join(dp, f)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--repo', default='', help='GitHub 用户名/仓库名')
    ap.add_argument('--mirror', default='gh-proxy', choices=sorted(MIRRORS),
                    help='用哪家镜像做前缀（默认 gh-proxy）')
    ap.add_argument('--base', default='', help='自定义前缀地址，给了就优先用它')
    ap.add_argument('--root', default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()

    prefix = ''
    if a.base:
        prefix = a.base.rstrip('/')
    elif a.repo:
        repo = a.repo.strip().strip('/')
        if '/' not in repo:
            print('✗ --repo 要写成 用户名/仓库名 的格式')
            return 1
        prefix = MIRRORS[a.mirror].format(repo=repo)
    if prefix.endswith('/main'):
        pass
    prefix = prefix.rstrip('/')

    # ── 检查模式 ──────────────────────────────────────────────
    if not prefix:
        hit_rel = hit_old = 0
        files = []
        for p in walk_text(a.root):
            try:
                t = open(p, encoding='utf-8').read()
            except (UnicodeDecodeError, PermissionError):
                continue
            n1 = t.count('": "https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main/')
            n2 = t.count(SELF_HOST)
            if n1 or n2:
                hit_rel += n1
                hit_old += n2
                files.append((os.path.relpath(p, a.root), n1, n2))
        print('检查模式（不改任何文件）：')
        print('  相对路径引用  ": "https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main/     %d 处' % hit_rel)
        print('  指向原站的绝对地址      %d 处' % hit_old)
        for f, n1, n2 in files[:15]:
            print('    %-40s 相对 %-4d 绝对 %d' % (f, n1, n2))
        print()
        print('  这份包默认用相对路径，能直接被任何镜像地址带着走，不用换。')
        print('  要换成绝对地址就加 --repo 用户名/仓库名 再跑一次。')
        return 0

    # ── 替换模式 ──────────────────────────────────────────────
    n_rel = n_old = 0
    touched = 0
    for p in walk_text(a.root):
        try:
            t = open(p, encoding='utf-8').read()
        except (UnicodeDecodeError, PermissionError):
            continue
        t2 = t.replace('": "https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main/', '": "%s/' % prefix)
        t2 = t2.replace(SELF_HOST, prefix)
        if t2 != t:
            n_rel += t.count('": "https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main/')
            n_old += t.count(SELF_HOST)
            open(p, 'w', encoding='utf-8').write(t2)
            touched += 1

    print('替换完成：')
    print('  前缀    %s' % prefix)
    print('  改动文件 %d 个' % touched)
    print('  相对引用 %d 处 → 绝对；原站引用 %d 处 → 你的地址' % (n_rel, n_old))

    # ── 自检：index.json 里引用的文件在不在 ────────────────────
    idx = os.path.join(a.root, 'index.json')
    if os.path.exists(idx):
        raw = open(idx, encoding='utf-8').read()
        urls = set(re.findall(r'"%s/([^"]+)"' % re.escape(prefix), raw))
        missing = [u for u in urls if not os.path.exists(os.path.join(a.root, u))]
        left = raw.count('": "https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main/')
        print('  自检：引用 %d 个文件，缺失 %d 个，剩余相对引用 %d 处'
              % (len(urls), len(missing), left))
        for m in missing[:10]:
            print('    ✗ 缺失：', m)
        if not missing and not left:
            print('  零缺失、零残留 ✓')
    print()
    print('现在把 index.json 的订阅地址填进壳里，记得删掉旧订阅重新添加一次。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
