#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键把整包切到"走中转" / 一键还原

用法：

  # 切换（把中转地址换成你自己的）
  python3 set-relay.py --base http://1.2.3.4:8899

  # 只看会改哪些文件，不动手
  python3 set-relay.py --base http://1.2.3.4:8899 --dry-run

  # 只中转列表/接口，播放地址不走中转（省中转机流量）
  python3 set-relay.py --base http://1.2.3.4:8899 --no-media

  # 直播列表也不动（默认会一起转）
  python3 set-relay.py --base http://1.2.3.4:8899 --no-live

  # 还原回直连（原始文件在 *.relaybak）
  python3 set-relay.py --restore

  # 看当前是什么状态
  python3 set-relay.py
"""
import argparse
import json
import os
import re
import shutil
import sys

BAK = '.relaybak'
SHIM_BEGIN = '# === Zaka relay shim BEGIN ==='
SHIM_END = '# === Zaka relay shim END ==='

# 这些域名本来就是国内可直连的（或中转机自己），不套中转
SKIP_HOSTS = (
    'gh-proxy.com', 'ghproxy.net', 'gh.llkk.cc', 'ghfast.top',
    'raw.githubusercontent.com', 'githubusercontent.com', 'github.com',
    'jsdelivr.net', 'jsdmirror.com', 'jsd.onmicrosoft.cn',
    'gitee.com', 'btstu.cn', '127.0.0.1', 'localhost',
)

URL_RE = re.compile(r'^https?://', re.I)


def log(*a):
    print(' '.join(str(x) for x in a))


def skip_url(u):
    try:
        host = re.sub(r'^https?://', '', str(u)).split('/')[0].split(':')[0].lower()
    except Exception:
        return True
    if not host:
        return True
    if '.' not in host:
        return True
    return any(host == h or host.endswith('.' + h) for h in SKIP_HOSTS)


def relay_form(base, url):
    """https://a.com/x?y=1 -> base/https/a.com/x?y=1"""
    u = str(url).strip()
    if not URL_RE.match(u) or skip_url(u) or u.startswith(base):
        return u
    m = re.match(r'^(https?)://([^/\s]+)(.*)$', u, re.I)
    if not m:
        return u
    rest = m.group(3) or '/'
    if not rest.startswith('/'):
        rest = '/' + rest
    return '%s/%s/%s%s' % (base, m.group(1).lower(), m.group(2), rest)


def backup(path):
    b = path + BAK
    if not os.path.exists(b):
        shutil.copy2(path, b)


def write_text(path, text):
    backup(path)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def write_json(path, obj, indent=None):
    if indent is None:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            head = f.read(400)
        indent = 1 if re.match(r'\s*\{\s*\n\s+"', head) else None
    txt = json.dumps(obj, ensure_ascii=False, indent=indent)
    if indent is None:
        txt = txt.replace(', ', ',').replace(': ', ':')
    write_text(path, txt)


# ------------------------------------------------------------------ 规则文件
def walk_urls(obj, base, stats):
    if isinstance(obj, dict):
        return {k: walk_urls(v, base, stats) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk_urls(v, base, stats) for v in obj]
    if isinstance(obj, str) and URL_RE.match(obj.strip()):
        new = relay_form(base, obj)
        if new != obj:
            stats['url'] += 1
        return new
    return obj


def do_rule_file(path, base, media, stats, dry):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            obj = json.load(f)
    except Exception as e:
        stats['bad'].append('%s (%s)' % (path, e))
        return
    if not isinstance(obj, dict):
        return
    if '主页url' in obj or '分类url' in obj:
        # py 引擎规则：只写开关，URL 交给引擎转（这样相对链接拼接不会错）
        if obj.get('中转') == base and bool(obj.get('中转视频')) == bool(media):
            return
        obj['中转'] = base
        obj['中转视频'] = bool(media)
        stats['rule'] += 1
        if not dry:
            write_json(path, obj)
        return
    # jar / XYQHiker 规则：把规则里写死的网址换成中转地址
    before = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    new = walk_urls(obj, base, stats)
    if json.dumps(new, ensure_ascii=False, sort_keys=True) != before:
        stats['rule'] += 1
        if not dry:
            write_json(path, new)


# ------------------------------------------------------------------ 插件注入
def shim_text(base, media):
    return SHIM_BEGIN + '''
try:
    _ZR_BASE = %r
    _ZR_MEDIA = %r
    if _ZR_BASE:
        try:
            import requests as _zr_req
            _zr_orig = _zr_req.sessions.Session.request

            def _zr_patch(self, method, url, **kw):
                if not kw.get('proxies'):
                    kw['proxies'] = {'http': _ZR_BASE, 'https': _ZR_BASE}
                return _zr_orig(self, method, url, **kw)

            _zr_req.sessions.Session.request = _zr_patch
        except Exception:
            pass
        try:
            import urllib.request as _zr_url
            _zr_url.install_opener(_zr_url.build_opener(
                _zr_url.ProxyHandler({'http': _ZR_BASE, 'https': _ZR_BASE})))
        except Exception:
            pass
except Exception:
    pass
''' % (base, media) + SHIM_END + '\n'


def strip_shim(text):
    while True:
        i = text.find(SHIM_BEGIN)
        if i < 0:
            return text
        j = text.find(SHIM_END, i)
        if j < 0:
            return text[:i]
        j = text.find('\n', j)
        text = text[:i] + (text[j + 1:] if j >= 0 else '')


def inject_shim(path, base, media, stats, dry):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        src = f.read()
    clean = strip_shim(src)
    lines = clean.split('\n')
    pos = 0
    for i, ln in enumerate(lines[:3]):
        if ln.startswith('#') and ('coding' in ln or 'coding' in ln):
            pos = i + 1
            break
    out = '\n'.join(lines[:pos]) + '\n' + shim_text(base, media) + '\n'.join(lines[pos:])
    if out == src:
        return
    stats['shim'] += 1
    if not dry:
        write_text(path, out)


def set_const(path, key, value, js=False, stats=None, dry=False):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        src = f.read()
    raw = str(value) in ('True', 'False', 'true', 'false')   # 布尔/数字不加引号
    val = str(value) if raw else '"%s"' % value
    if js:
        pat = re.compile(r'(var\s+%s\s*=\s*)([^;]*)(;)' % re.escape(key))
        rep = r'\g<1>%s\g<3>' % val
    else:
        pat = re.compile(r'(?m)^([ \t]*%s[ \t]*=[ \t]*)([^\n#]*?)([ \t]*(?:#.*)?)$' % re.escape(key))
        rep = r'\g<1>%s\g<3>' % val
    new, n = pat.subn(rep, src, count=1)
    if n and new != src:
        if stats is not None:
            stats['const'] += 1
        if not dry:
            write_text(path, new)


# ------------------------------------------------------------------ 直播列表
def do_live_file(path, base, stats, dry):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        src = f.read()
    n = [0]

    def rep(m):
        new = relay_form(base, m.group(0))
        if new != m.group(0):
            n[0] += 1
        return new

    out = re.sub(r'https?://[^\s"\'<>\\,\]]+', rep, src)
    if out != src:
        stats['live'] += n[0]
        if not dry:
            write_text(path, out)


# ------------------------------------------------------------------ 还原
def restore(root):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ('.git',)]
        for fn in filenames:
            if fn.endswith(BAK):
                src = os.path.join(dirpath, fn)
                dst = src[:-len(BAK)]
                shutil.move(src, dst)
                n += 1
    log('已还原 %d 个文件（中转开关全部撤掉，回到直连）' % n)


def status(root):
    bases = set()
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.endswith('.json'):
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                        o = json.load(f)
                except Exception:
                    continue
                if isinstance(o, dict) and isinstance(o.get('中转'), str) and o['中转']:
                    bases.add(o['中转'])
    p = os.path.join(root, 'py', 'sebo.py')
    if os.path.exists(p):
        with open(p, encoding='utf-8', errors='ignore') as f:
            m = re.search(r'(?m)^RELAY\s*=\s*"([^"]*)"', f.read())
            if m and m.group(1):
                bases.add(m.group(1))
    if bases:
        log('当前已切到中转：', '、'.join(sorted(bases)))
    else:
        log('当前是直连状态（没有配置中转）。')


def main():
    ap = argparse.ArgumentParser(description='一键切换整包走中转 / 还原')
    ap.add_argument('--base', default='', help='中转地址，例如 http://1.2.3.4:8899')
    ap.add_argument('--media', dest='media', action='store_true', default=True,
                    help='播放/直播流也走中转（默认开）')
    ap.add_argument('--no-media', dest='media', action='store_false',
                    help='播放地址不走中转，只中转列表与接口')
    ap.add_argument('--live', dest='live', action='store_true', default=True,
                    help='直播 m3u 列表里的地址也走中转（默认开）')
    ap.add_argument('--no-live', dest='live', action='store_false')
    ap.add_argument('--token', default='', help='中转口令（中转机上 --token 设的那个）')
    ap.add_argument('--root', default='.', help='包根目录')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--restore', action='store_true')
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    if a.restore:
        restore(root)
        return
    if not a.base:
        status(root)
        log('提示：加 --base http://你的中转机:8899 就开始切换。')
        return

    base = a.base.strip().rstrip('/')
    if not URL_RE.match(base):
        base = 'http://' + base
    token = (a.token or '').strip()
    if token:
        base = base + '/' + token
        hostport = re.sub(r'^https?://', '', a.base.strip().rstrip('/'))
        proxy_base = 'http://zaka:%s@%s' % (token, hostport)
    else:
        proxy_base = base
    stats = {'rule': 0, 'url': 0, 'shim': 0, 'const': 0, 'live': 0, 'api': 0, 'bad': []}
    dry = a.dry_run

    # 1) 采集接口（源站列表直接由壳请求的那批）
    idx = os.path.join(root, 'index.json')
    if os.path.exists(idx):
        with open(idx, 'r', encoding='utf-8', errors='ignore') as f:
            cfg = json.load(f)
        for s in cfg.get('sites', []):
            api = s.get('api', '')
            if not isinstance(api, str) or not URL_RE.match(api):
                continue          # py/js 源走的是引擎和插件，不在这里改
            new = relay_form(base, api)
            if new != api:
                s['api'] = new
                stats['api'] += 1
        if stats['api'] and not dry:
            write_json(idx, cfg, indent=None)
        log('采集接口改写：%d 条' % stats['api'])

    # 2) 规则文件
    for sub in ('rules', 'XYQHike', 'json', 'lib'):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.json'):
                do_rule_file(os.path.join(d, fn), base, a.media, stats, dry)
    log('规则文件改写/注入：%d 个（其中网址替换 %d 处）' % (stats['rule'], stats['url']))

    # 3) 插件（用代理方式，不用改它们的网址）
    for sub in (('js', 'ss'), ('plugin',), ('plugin', 'adult')):
        d = os.path.join(root, *sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.py'):
                inject_shim(os.path.join(d, fn), proxy_base, a.media, stats, dry)
    log('py 插件接入中转：%d 个' % stats['shim'])

    # 4) 自己写的脚本：填中转开关
    p = os.path.join(root, 'py', 'sebo.py')
    if os.path.exists(p):
        set_const(p, 'RELAY', base, stats=stats, dry=dry)
        set_const(p, 'RELAY_MEDIA', 'True' if a.media else 'False', stats=stats, dry=dry)
    p = os.path.join(root, 'js', 'live2vod.js')
    if os.path.exists(p):
        set_const(p, 'RELAY', base, js=True, stats=stats, dry=dry)
        set_const(p, 'RELAY_MEDIA', 'true' if a.media else 'false', js=True, stats=stats, dry=dry)
    log('脚本开关：%d 处' % stats['const'])

    # 5) 直播列表
    if a.live:
        for rel in ('live.m3u', 'live-p2p.m3u', 'live/live.txt', 'tv/live.txt'):
            p = os.path.join(root, rel)
            if os.path.exists(p):
                do_live_file(p, base, stats, dry)
        log('直播列表改写：%d 处' % stats['live'])
    else:
        log('直播列表：跳过（--no-live）')

    if stats['bad']:
        log('跳过（JSON 有问题）：')
        for b in stats['bad']:
            log('   ', b)

    log('')
    if dry:
        log('以上是预演，没有改动任何文件。去掉 --dry-run 就动手。')
    else:
        log('搞定。中转地址 = %s，播放%s走中转。' % (base, '也' if a.media else '不'))
        log('原文件都留在同目录的 *.relaybak，跑 python3 set-relay.py --restore 可整体还原。')


if __name__ == '__main__':
    main()
