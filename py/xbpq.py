# -*- coding: utf-8 -*-
"""
通用规则源引擎 (中文短键规则格式)
规则文件放服务器上，站点 ext 指向规则地址即可。
"""
import re
import sys
import json
import ssl
import html as html_mod
import urllib.parse
import urllib.request

sys.path.append("..")
from base.spider import Spider

MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

# 各站广告位/播放器示例片特征：命中即丢弃，避免"进详情页只播几秒广告"
AD_VIDEO_PAT = re.compile(
    r'artplayer\.org|/assets/sample/|/sample/test|/test\d*\.mp4|'
    r'/media/ads/|/ads?/[^/]*?\.(?:mp4|m3u8)|madouui\.com|18link\.vip|'
    r'/advert|promo\.mp4|prevideo|preview\.mp4',
    re.I)


def pick(text, rule):
    """单条截取: 'A&&B' / 'A&&' / '&&B'，支持 || 备选 与 '前缀+规则'"""
    if not text or not rule:
        return ''
    rule = str(rule)
    prefix = ''
    if '+' in rule:
        head, tail = rule.split('+', 1)
        if '&&' in tail and '&&' not in head:
            prefix, rule = head, tail
    for alt in rule.split('||'):
        alt = alt.strip()
        if '&&' not in alt:
            continue
        a, b = alt.split('&&', 1)
        i = text.find(a)
        if i < 0:
            continue
        i += len(a)
        if b == '':
            return prefix + text[i:]
        j = text.find(b, i)
        if j < 0:
            continue
        return prefix + text[i:j]
    return ''


def blocks(html, rule):
    if not html or not rule:
        return []
    rule = str(rule).split('||')[0]
    if '&&' not in rule:
        return []
    a, b = rule.split('&&', 1)
    out, i = [], 0
    while True:
        i = html.find(a, i)
        if i < 0:
            break
        i += len(a)
        if b == '':
            j = len(html)
        else:
            j = html.find(b, i)
            if j < 0:
                break
        out.append(html[i:j])
        i = j + len(b) if b else j
    return out


def pick_all(text, rule):
    """取所有匹配（支持 * 通配），用于多线路/多集"""
    if not text or not rule or '&&' not in str(rule):
        return []
    a, b = str(rule).split('&&', 1)
    if not a:
        return []
    pat = re.escape(a).replace(r'\*', '.*?')
    out = []
    for m in re.finditer(pat, text):
        st = m.end()
        if b == '':
            out.append(text[st:])
            continue
        j = text.find(b, st)
        if j < 0:
            continue
        out.append(text[st:j])
        if len(out) > 200:
            break
    return out


class Spider(Spider):
    def __init__(self):
        self.cfg = {}
        self.host = ''
        self.headers = {'User-Agent': MOBILE_UA}
        self._ctx = None

    # ---------- 基础设施 ----------
    def _ssl(self):
        if self._ctx is None:
            c = ssl.create_default_context()
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            self._ctx = c
        return self._ctx

    def _get(self, url, referer=None, timeout=20):
        h = dict(self.headers)
        if referer:
            h['Referer'] = referer
        req = urllib.request.Request(url, headers=h)
        r = urllib.request.urlopen(req, timeout=timeout, context=self._ssl())
        raw = r.read()
        enc = self.cfg.get('编码') or 'UTF-8'
        enc = 'utf-8' if str(enc).upper() in ('UTF-8', 'UTF8') else str(enc)
        try:
            return raw.decode(enc, 'replace')
        except Exception:
            return raw.decode('utf-8', 'replace')

    def _abs(self, u):
        u = str(u or '').strip()
        if not u:
            return ''
        if u.startswith('http'):
            return u
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('/'):
            m = re.match(r'(https?://[^/]+)', self.host)
            return (m.group(1) if m else self.host) + u
        return self.host.rstrip('/') + '/' + u

    def _headers(self, s):
        h = {'User-Agent': MOBILE_UA}
        if not s:
            return h
        for item in str(s).split('#'):
            if '$' in item:
                k, v = item.split('$', 1)
                k, v = k.strip(), v.strip()
                if v.upper() in ('MOBILE_UA', 'MOBILEUA'):
                    v = MOBILE_UA
                if k:
                    h[k] = v
        return h

    # ---------- 生命周期 ----------
    def init(self, extend=""):
        ext = (extend or '').strip()
        if ext.startswith('{'):
            self.cfg = json.loads(ext)
        elif ext.startswith('http'):
            self.cfg = json.loads(self._get(ext))
        else:
            self.cfg = {}
        self.host = str(self.cfg.get('主页url') or '').rstrip('/')
        self.headers = self._headers(self.cfg.get('请求头'))
        self.cates = self._cates(self.cfg.get('分类') or '')
        return

    @staticmethod
    def _cates(s):
        out = []
        for part in str(s).split('#'):
            part = part.strip()
            if not part:
                continue
            if '$' in part:
                n, i = part.rsplit('$', 1)
            else:
                n, i = part, part
            n, i = n.strip(), i.strip()
            if n and i:
                out.append({'type_name': n, 'type_id': i})
        return out

    def getName(self):
        return self.cfg.get('站名') or self.cfg.get('作者') or '规则源'

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(?:m3u8|mp4|flv|avi|mkv)(?:$|[?#])', str(url or ''), re.I))

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return

    def destroy(self):
        return

    # ---------- 抗广告位 ----------
    A_RE = re.compile(r'<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\']', re.I)

    def _hostname(self):
        m = re.match(r'https?://([^/]+)', self.host or '')
        return m.group(1).lower() if m else ''

    def _is_internal(self, u):
        """判断链接是否指向本站(相对路径/同域)"""
        u = str(u or '').strip()
        if not u:
            return False
        if u.startswith(('/', '?', '#', './', '../')):
            return True
        if u.startswith('//'):
            return True
        h = self._hostname()
        if h and h in u.lower():
            return True
        return False

    def _anti_ad(self, block):
        """列表块常被插广告位(外链)。
        块首链接指向站外时：后移到块内第一个站内链接处继续解析；
        整块都是广告位则返回 None 表示丢弃。站内站点完全不受影响。"""
        linkrule = self.cfg.get('链接') or ''
        if not linkrule:
            return block
        pos = 0
        for _ in range(16):
            l = pick(block[pos:], linkrule).strip()
            if not l:
                return block if pos == 0 else None
            if self._is_internal(l):
                return block[pos:] if pos else block
            j = block.find(l, pos)
            if j < 0:
                return block
            pos = j + len(l)
        return None

    def _detail_name(self, html):
        """详情页标题优先取结构化来源，避免拿到 logo/导航等公共元素的文案"""
        for pat in (r'<meta[^>]+property=["\']og:title["\'][^>]*?content=["\']([^"\']+)["\']',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*?property=["\']og:title["\']',
                    r'<h1[^>]*>(.*?)</h1>',
                    r'<h2[^>]*class=["\'][^"\']*title[^"\']*["\'][^>]*>(.*?)</h2>'):
            m = re.search(pat, html, re.S | re.I)
            if m:
                v = html_mod.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()
                if v and len(v) > 1:
                    return v
        return ''

    def _one(self, b):
        t = pick(b, self.cfg.get('标题') or '').strip()
        l = pick(b, self.cfg.get('链接') or '').strip()
        p = pick(b, self.cfg.get('图片') or '').strip()
        note = pick(b, self.cfg.get('副标题') or '').strip()
        if not l:
            return None
        t = html_mod.unescape(re.sub(r'<[^>]+>', '', t)).strip()
        note = html_mod.unescape(re.sub(r'<[^>]+>', '', note)).strip()
        if not t:
            t = note or l
        return {
            'vod_id': self._abs(l),
            'vod_name': t[:120],
            'vod_pic': self._abs(p),
            'vod_remarks': note[:40],
        }

    # ---------- 列表解析 ----------
    def _list(self, html, tid=None):
        scope = html
        sub2 = self.cfg.get('二次截取') or ''
        if sub2:
            s = pick(scope, sub2)
            if s:
                scope = s
        arr = self.cfg.get('数组') or ''
        if not arr:
            return []
        out, dropped = [], []
        for b in blocks(scope, arr):
            l0 = pick(b, self.cfg.get('链接') or '').strip()
            if l0 and not self._is_internal(l0):
                nb = self._anti_ad(b)
                if nb is None:
                    dropped.append(b)       # 纯广告位，整块丢掉
                    continue
                b = nb
            it = self._one(b)
            if it:
                out.append(it)
        if not out and dropped:
            # 兜底：整页都被判成广告时回退原逻辑，绝不把站点解析成空
            for b in dropped:
                it = self._one(b)
                if it:
                    out.append(it)
        return out

    # ---------- 接口 ----------
    def homeContent(self, filter):
        res = {'class': list(self.cates), 'filters': {}}
        try:
            html = self._get(self.host)
            res['list'] = self._list(html)[:60]
        except Exception:
            res['list'] = []
        return res

    def homeVideoContent(self):
        try:
            return {'list': self._list(self._get(self.host))[:60]}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        tpl = str(self.cfg.get('分类url') or '')
        by = ''
        if ';;' in tpl:
            tpl, sort = tpl.split(';;', 1)
            by = re.sub(r'\d+$', '', sort.strip())
        url = (tpl.replace('{cateId}', str(tid))
                  .replace('{catePg}', str(pg))
                  .replace('{pg}', str(pg))
                  .replace('{by}', by))
        url = self._abs(url)
        # 分类 ID 含中文时自动百分号编码，避免构造出非法 URL
        try:
            url = urllib.parse.quote(url, safe=':/?&=#%+;,[]@!$()*\'~')
        except Exception:
            pass
        try:
            html = self._get(url, referer=self.host)
            lst = self._list(html, tid)
        except Exception:
            lst = []
        return {'list': lst, 'page': int(pg), 'pagecount': 9999,
                'limit': 90, 'total': 999999}

    def searchContent(self, key, quick, pg="1"):
        tpl = str(self.cfg.get('搜索url') or '')
        if not tpl:
            return {'list': []}
        url = (tpl.replace('{wd}', urllib.parse.quote(str(key)))
                  .replace('{key}', urllib.parse.quote(str(key)))
                  .replace('{pg}', str(pg))
                  .replace('{catePg}', str(pg)))
        try:
            html = self._get(self._abs(url), referer=self.host)
            return {'list': self._list(html)}
        except Exception:
            return {'list': []}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) and ids else str(ids)
        try:
            html = self._get(vid, referer=self.host)
        except Exception:
            return {'list': []}
        name = self._detail_name(html) or pick(html, self.cfg.get('标题') or '') or ''
        name = html_mod.unescape(re.sub(r'<[^>]+>', '', name)).strip()
        pic = self._abs(pick(html, self.cfg.get('图片') or '') or '')
        desc = pick(html, self.cfg.get('简介') or '') or ''
        # 1) 优先取播放器里真实使用的地址（页面注释里的往往带防盗链参数，会 403）
        urls = []
        # 主播放器变量最可靠：命中就只用它，避免把页面里"相关视频"的预览地址也当成剧集
        main = re.findall(
            r"""(?:const|let|var)\s+(?:source|src|videoUrl|hlsUrl|playUrl|video_url)\s*=\s*['"](https?://[^'"]+?\.(?:m3u8|mp4)[^'"]*)['"]""",
            html)
        if main:
            urls = main
        else:
            for pat in (r"""(?:source|src|videoUrl|hlsUrl|playUrl|video_url|url)\s*[=:]\s*['"](https?://[^'"]+?\.(?:m3u8|mp4)[^'"]*)['"]""",
                        r"""url\s*:\s*['"](https?://[^'"]+?\.(?:m3u8|mp4)[^'"]*)['"]""",
                        r"""<source[^>]+src=['"](https?://[^'"]+?\.(?:m3u8|mp4)[^'"]*)['"]""",
                        r"""src\s*:\s*['"](https?://[^'"]+?\.(?:m3u8|mp4)[^'"]*)['"]"""):
                urls += re.findall(pat, html)
        # 2) 规则指定的跳转播放链接
        jr = self.cfg.get('跳转播放链接') or ''
        if not urls and jr:
            scope = html
            sub = self.cfg.get('线路二次截取') or ''
            if sub:
                s = pick(scope, sub)
                if s:
                    scope = s
            urls = pick_all(scope, jr)
        if not urls:
            for k in ('播放数组', '线路数组'):
                r = self.cfg.get(k)
                if not r:
                    continue
                seg = pick_all(html, r) or blocks(html, r)
                for s0 in seg:
                    u = pick(s0, self.cfg.get('播放链接') or '')
                    if u:
                        urls.append(u)
                if urls:
                    break
        # 3) 通用兜底
        if not urls:
            for m in re.finditer(r'(https?://[^\s"\'<>\\]+?\.(?:m3u8|mp4)[^\s"\'<>\\]*)', html):
                urls.append(m.group(1))
        # 4) 过滤广告位/播放器示例片（不清掉就会播成几秒的广告）
        urls = [u for u in dict.fromkeys(urls) if not AD_VIDEO_PAT.search(u)]
        urls = [self._abs(u) for u in urls]
        # 去掉防盗链参数（带 line= 的那种会被拒，同域不带参数的才通）
        clean = [re.sub(r'([?&])line=[^&]*&?', r'\1', u).rstrip('?&') for u in urls]
        if clean:
            urls = clean
        if not urls:
            return {'list': []}
        play = '#'.join('第%d集$%s' % (i + 1, u) for i, u in enumerate(urls))
        return {'list': [{
            'vod_id': vid, 'vod_name': name or '视频', 'vod_pic': pic,
            'vod_content': desc, 'vod_play_from': '默认', 'vod_play_url': play,
        }]}

    def playerContent(self, flag, id, keys=None):
        url = str(id or '')
        if url.startswith('http'):
            return {'parse': 0, 'url': url, 'header': json.dumps(self.headers, ensure_ascii=False)}
        return {'parse': 0, 'url': self._abs(url), 'header': json.dumps(self.headers, ensure_ascii=False)}
