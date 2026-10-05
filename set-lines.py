#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zaka 成人订阅 · 一键换线路 / 自托管工具
=======================================
把 index.json 里指向 Zaka 服务器的绝对地址，一次性换成你自己 GitHub 仓库
（或多条国内镜像线路）的地址，生成 index-<线路>.json 一套文件。

用法（在本包根目录下跑）
------------------------
  python3 set-lines.py --check                      # 实测每条线路通不通（在你本地跑=你所在地真实视角）
  python3 set-lines.py --mirror                     # 用推荐线路生成全套（主力镜像）
  python3 set-lines.py --mirror all                 # 生成全部线路
  python3 set-lines.py --mirror ghproxy ghfast      # 只生成指定线路
  python3 set-lines.py --list                       # 列出所有线路代号
  python3 set-lines.py 用户名 仓库名 [分支]            # 官方 raw 直连版（默认分支 main）
  python3 set-lines.py --base https://your.site/    # 换成你自己的服务器/任何静态托管
  python3 set-lines.py --at <commit号>               # 用 commit 号代替分支（jsDelivr 更稳）

生成的文件：index-<线路>.json —— 全部 push 上去，壳里填能通的那条即可。
不跑脚本也能用：包里边有 check-lines.html，手机浏览器打开，点一下就知道
哪条线路通，直接把地址复制进壳。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 原地址前缀（包内所有依赖都指向这里）
SRC_PREFIX = 'https://r18sub.web.zaka.live/'

# ── 线路表：代号 -> (说明, 前缀模板) ─────────────────────────────
LINES = [
    ('raw',       '官方 raw 直连（无镜像）     ', 'https://raw.githubusercontent.com/{u}/{r}/{b}/'),
    ('jsd',       'jsDelivr CDN · 主入口       ', 'https://cdn.jsdelivr.net/gh/{u}/{r}@{b}/'),
    ('fastly',    'jsDelivr · Fastly 节点      ', 'https://fastly.jsdelivr.net/gh/{u}/{r}@{b}/'),
    ('gcore',     'jsDelivr · Gcore 节点       ', 'https://gcore.jsdelivr.net/gh/{u}/{r}@{b}/'),
    ('testingcf', 'jsDelivr · Cloudflare 节点  ', 'https://testingcf.jsdelivr.net/gh/{u}/{r}@{b}/'),
    ('ghproxy',   'gh-proxy.com 代理           ', 'https://gh-proxy.com/https://raw.githubusercontent.com/{u}/{r}/{b}/'),
    ('ghpnet',    'ghproxy.net 代理            ', 'https://ghproxy.net/https://raw.githubusercontent.com/{u}/{r}/{b}/'),
    ('ghfast',    'ghfast.top 代理             ', 'https://ghfast.top/https://raw.githubusercontent.com/{u}/{r}/{b}/'),
    ('llkk',      'gh.llkk.cc 代理             ', 'https://gh.llkk.cc/https://raw.githubusercontent.com/{u}/{r}/{b}/'),
    ('bgh',       'bgithub 镜像                ', 'https://raw.bgithub.xyz/{u}/{r}/{b}/'),
    ('gitproxy',  'gitproxy.click 代理         ', 'https://gitproxy.click/https://raw.githubusercontent.com/{u}/{r}/{b}/'),
    ('jsdmirror', 'jsdmirror CDN               ', 'https://cdn.jsdmirror.com/gh/{u}/{r}@{b}/'),
]
LINE_MAP = {k: (desc, tpl) for k, desc, tpl in LINES}
DEFAULT_LINES = ['raw', 'jsd', 'fastly', 'gcore', 'ghproxy', 'ghfast']
SRC_JSON = 'index.json'


def guess_remote():
    """从 git remote 里猜 用户名/仓库名/分支"""
    try:
        out = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None, None, None
    for line in out.splitlines():
        m = re.search(r'github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?\s', line)
        if m:
            u, r = m.group(1), m.group(2)
            b = 'main'
            try:
                b = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'],
                                   capture_output=True, text=True, timeout=10).stdout.strip()
                if not b:
                    b = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                       capture_output=True, text=True, timeout=10).stdout.strip()
            except Exception:
                pass
            if not b or b == 'HEAD':
                b = 'main'
            return u, r, b
    return None, None, None


def swap(obj, pref):
    """递归把所有字符串里的原前缀换成新前缀"""
    if isinstance(obj, dict):
        return {k: swap(v, pref) for k, v in obj.items()}
    if isinstance(obj, list):
        return [swap(v, pref) for v in obj]
    if isinstance(obj, str):
        return obj.replace(SRC_PREFIX, pref)
    return obj


def collect_refs(obj, acc):
    """收集所有指向原前缀的引用"""
    if isinstance(obj, dict):
        for v in obj.values():
            collect_refs(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            collect_refs(v, acc)
    elif isinstance(obj, str):
        if SRC_PREFIX in v:
            acc.append(v)
    return acc


def verify(path, pref):
    """自检：返回 (引用数, 残留原前缀数, 缺失文件列表)"""
    d = json.load(open(path, encoding='utf-8'))
    n = [0]
    left = []
    miss = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            if SRC_PREFIX in o:
                left.append(o)
            if pref in o:
                p = o.split(pref, 1)[1].split('?')[0].split('#')[0].split('$$$')[0].split(';')[0]
                if not os.path.exists(p):
                    miss.append(p)
                n[0] += 1

    walk(d)
    return n[0], len(left), sorted(set(miss))


def gen(code, pref, outdir='.'):
    d = json.load(open(SRC_JSON, encoding='utf-8'))
    out = os.path.join(outdir, f'index-{code}.json')
    json.dump(swap(d, pref), open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    return out


def check(u, r, b, timeout=12):
    """在本机实测每条线路的 index.json 是否可达（=你所在地的真实视角）"""
    def probe(item):
        code, desc, tpl = item
        url = tpl.format(u=u, r=r, b=b) + SRC_JSON
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(8192)
            ms = int((time.time() - t0) * 1000)
            ok = data.lstrip().startswith(b'{') and b'sites' in data
            return code, desc, url, ('OK' if ok else '内容不对(可能是错误页)'), ms
        except Exception as e:
            name = type(e).__name__
            if 'HTTPError' in name:
                st = 'HTTP ' + str(getattr(e, 'code', ''))
            elif 'URLError' in name:
                st = '连不上'
            else:
                st = name
            return code, desc, url, st, int((time.time() - t0) * 1000)

    print(f'实测 {len(LINES)} 条线路（目标 {u}/{r}@{b}/{SRC_JSON}）...\n')
    with ThreadPoolExecutor(max_workers=len(LINES)) as ex:
        res = list(ex.map(probe, LINES))
    res.sort(key=lambda x: (x[3] != 'OK', x[4]))
    ok_list = []
    for code, desc, url, st, ms in res:
        flag = 'OK  ' if st == 'OK' else 'FAIL'
        print(f'  [{flag}] {code:<10} {ms:>6}ms  {st:<22} {desc.strip()}')
        if st == 'OK':
            ok_list.append(code)
    print()
    if ok_list:
        print('推荐（按本机实测速度排序）：', ' '.join(ok_list[:4]))
        print(f'生成命令：  python3 set-lines.py --mirror {" ".join(ok_list[:4])}')
    else:
        print('全不通。先确认：仓库是 Public / 文件已 push / 分支名和地址一致。')
        print('仓库还没建好时，先用包里的 check-lines.html 或直接填现成地址。')
    return ok_list


def main():
    args = list(sys.argv[1:])
    lines, at, do_check, do_list, outdir = None, None, False, False, '.'
    base = None

    for flag, key in (('--check', 'c'), ('--list', 'l')):
        if flag in args:
            args.remove(flag)
            do_check = do_check or key == 'c'
            do_list = do_list or key == 'l'
    if '--out' in args:
        i = args.index('--out')
        outdir = args[i + 1]
        del args[i:i + 2]
    if '--at' in args:
        i = args.index('--at')
        at = args[i + 1]
        del args[i:i + 2]
    if '--base' in args:
        i = args.index('--base')
        base = args[i + 1]
        if not base.endswith('/'):
            base += '/'
        del args[i:i + 2]
    if '--mirror' in args:
        i = args.index('--mirror')
        lines = []
        j = i + 1
        while j < len(args) and not args[j].startswith('--'):
            lines.append(args[j])
            j += 1
        del args[i:j]
        if not lines or lines == ['all']:
            lines = DEFAULT_LINES if not lines else [k for k, _, _ in LINES]
        bad = [k for k in lines if k not in LINE_MAP]
        if bad:
            print('!! 不认识的线路代号：', ' '.join(bad), '（用 --list 看全部）')
            sys.exit(1)

    if do_list:
        print('可用线路代号：\n')
        for k, desc, tpl in LINES:
            print(f'  {k:<11}{desc}  {tpl}')
        return

    if not os.path.exists(SRC_JSON):
        print(f'!! 当前目录没有 {SRC_JSON}，请在本包根目录下运行。')
        sys.exit(1)

    # ── 自定义地址模式（自己的服务器 / 其他静态托管）
    if base:
        out = gen('custom', base, outdir)
        n, left, miss = verify(out, base)
        print(f'已生成： {out}')
        print(f'  替换引用 {n} 处，残留原地址 {left} 处，缺失文件 {len(miss)} 个')
        if miss:
            print('  !! 缺失：', miss[:10])
        return

    # 仓库信息
    if len(args) >= 2:
        u, r = args[0], args[1]
        b = args[2] if len(args) > 2 else 'main'
    else:
        u, r, b = guess_remote()
    if not u or not r:
        print('!! 没拿到仓库信息。两种用法：')
        print('   1) 在仓库目录里跑（已配好 git remote）：  python3 set-lines.py --mirror')
        print('   2) 手动指定：                          python3 set-lines.py 用户名 仓库名 [分支]')
        print('   3) 换成自己的服务器：                   python3 set-lines.py --base https://你的域名/')
        sys.exit(1)
    if at:
        b = at

    if do_check:
        check(u, r, b)
        print()

    if lines is None:
        if do_check:
            return
        lines = ['raw']

    print(f'仓库：{u}/{r}   分支/commit：{b}')
    os.makedirs(outdir, exist_ok=True)
    made = []
    for code in lines:
        desc, tpl = LINE_MAP[code]
        pref = tpl.format(u=u, r=r, b=b)
        out = gen(code, pref, outdir)
        n, left, miss = verify(out, pref)
        print(f'  [{code:<10}] 引用 {n:>3} 处  残留 {left}  缺失 {len(miss)}  ->  {out}')
        if miss:
            print('      !! 包内缺这些文件：', miss[:8])
        made.append(out)
    print()
    print(f'生成完成，共 {len(made)} 个订阅文件。')
    print(f'把它们全部 push 到 {u}/{r} 的 {b} 分支根目录（保持目录结构不变），')
    print('然后在壳里填你能通的那一条地址即可。')


if __name__ == '__main__':
    main()
