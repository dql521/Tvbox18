# -*- coding: utf-8 -*-
# JavDB 插件（重写版）
#
# 本轮实测到的两个变化：
#   1) 老域名 javdb563.com 已经失效（502），可用的是 javdb.com / javdb573.com 等
#      —— 这里改成多域名自动切换，坏一个自动换下一个。
#   2) 站点给详情页（/v/xxxx）加了登录要求：未登录会直接 302 到 /login，
#      磁链也就拿不到。列表页和搜索页不需要登录。
#      —— 所以支持在订阅里给这个源填自己的账号 cookie（见 setCookie / init 的说明），
#         填了就正常出磁链；没填就只出列表，并在详情里给出提示而不是假装成功。
import re
import sys
import json
import requests
import urllib3
from pyquery import PyQuery as pq

urllib3.disable_warnings()
sys.path.append('..')
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):

    hosts = [
        "https://javdb.com",
        "https://javdb573.com",
        "https://javdb580.com",
    ]

    headers = {
        # 实测：这个站的 Cloudflare 对手机版 UA 直接拦（403 挑战页），桌面 UA 才能过
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
        'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,'
                   'image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'),
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Cookie': 'over18=1; locale=zh',
    }

    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.host = self.hosts[0]
        self.session = requests.Session()

    def init(self, extend=""):
        """extend 可以是纯 cookie 串，也可以是 {"cookie": "...", "host": "..."}"""
        ext = (extend or "").strip()
        if ext.startswith('{'):
            try:
                cfg = json.loads(ext)
                if cfg.get('cookie'):
                    self.setCookie(cfg['cookie'])
                if cfg.get('host'):
                    self.host = str(cfg['host']).rstrip('/')
                    if self.host not in self.hosts:
                        self.hosts.insert(0, self.host)
            except Exception:
                pass
        elif ext:
            self.setCookie(ext)
        return self

    def setCookie(self, cookie):
        ck = str(cookie or '').strip()
        if not ck:
            return
        # 允许直接粘浏览器里复制的一整串，也允许只给 _jdb_session=xxx
        if 'over18' not in ck:
            ck = ck + '; over18=1'
        self.headers['Cookie'] = ck
        return ck

    def getName(self):
        return "JavDB"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return

    def destroy(self):
        return

    def localProxy(self, param):
        return None

    # ---------------- 抓取（多域名 + 年龄确认 + 登录判定） ----------------
    def _blocked(self, text):
        """Cloudflare 的挑战页/拦截页不是内容，别当成正常页面收下"""
        t = str(text or '')
        if len(t) < 2000:
            return True
        for k in ('Just a moment', 'challenge-platform', 'cf-chl', 'Attention Required',
                  'Enable JavaScript and cookies to continue'):
            if k in t:
                return True
        return False

    def _fetch(self, path, host=None):
        """返回 (文本, 最终URL)；任一域名失败自动换下一个"""
        order = [host] if host else [self.host] + [h for h in self.hosts if h != self.host]
        last = ('', '')
        for h in order:
            url = path if str(path).startswith('http') else h + path
            try:
                rsp = self.session.get(url, headers=self.headers, timeout=20, verify=False,
                                       allow_redirects=True)
                # 年龄确认弹窗：跟一次就行（同一会话内不再出现）
                m = re.search(r'href="(/over18\?respond=1[^"]*)"', rsp.text or '')
                if m and '/login' not in rsp.url:
                    self.session.get(h + m.group(1).replace('&amp;', '&'),
                                     headers=self.headers, timeout=20, verify=False)
                    rsp = self.session.get(url, headers=self.headers, timeout=20, verify=False)
                if rsp.status_code == 200 and not self._blocked(rsp.text):
                    if not str(path).startswith('http'):
                        self.host = h
                    return rsp.text, rsp.url
                last = (rsp.text or '', rsp.url)
            except Exception:
                continue
        return last

    def _need_login(self, url):
        return str(url or '').rstrip('/').endswith('/login')

    # ---------------- 列表 ----------------
    def _getlist(self, data):
        videos = []
        items = data('.movie-list .item')
        if not items:
            items = data('.movie-list div.item')
        if not items:
            items = data('div.item')
        for item in items.items():
            link = item('a.box')
            if not link:
                link = item('a').eq(0)
            href = link.attr('href')
            if not href or '/v/' not in str(href):
                continue
            title = self._fix_mojibake((link.attr('title') or link.text() or '').strip())
            img = item('img').attr('src')
            meta = self._fix_mojibake((item('.meta').text() or '').strip())
            score = self._fix_mojibake((item('.score').text() or '').strip())
            remark = score or meta
            videos.append({
                'vod_id': href,
                'vod_name': title,
                'vod_pic': img,
                'vod_remarks': remark[:30],
                'vod_year': meta,
            })
        return videos

    def _fix_mojibake(self, s):
        if not s:
            return s
        try:
            if re.search(r'[ÃÂäåæçèéìíòóùúÄÅÆÇÈÉÌÍÒÓÙÚ]', s):
                return s.encode('latin1', 'ignore').decode('utf-8', 'ignore')
        except Exception:
            pass
        return s

    def homeContent(self, filter):
        html, _ = self._fetch('/')
        data = pq(html or '')
        classes = [
            {'type_name': '最新', 'type_id': ''},
            {'type_name': '有碼', 'type_id': 'censored'},
            {'type_name': '無碼', 'type_id': 'uncensored'},
            {'type_name': '歐美', 'type_id': 'western'},
            {'type_name': 'FC2', 'type_id': 'fc2'},
            {'type_name': '動漫', 'type_id': 'anime'},
        ]
        return {'class': classes, 'filters': {}, 'list': self._getlist(data)}

    def homeVideoContent(self):
        html, _ = self._fetch('/')
        return {'list': self._getlist(pq(html or ''))}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        tid = str(tid or '').strip().strip('/')
        if not tid:
            path = '/' if page <= 1 else '/?page=%d' % page
        elif '?' in tid:
            path = '/%s&page=%d' % (tid, page)
        else:
            path = '/%s' % tid if page <= 1 else '/%s?page=%d' % (tid, page)
        html, _ = self._fetch(path)
        lst = self._getlist(pq(html or ''))
        return {'list': lst, 'page': page, 'pagecount': page + 1,
                'limit': len(lst) or 20, 'total': 999999}

    def searchContent(self, key, quick, pg="1"):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        path = '/search?q=%s' % requests.utils.quote(str(key or '').strip())
        if page > 1:
            path += '&page=%d' % page
        html, _ = self._fetch(path)
        lst = self._getlist(pq(html or ''))
        return {'list': lst, 'page': page, 'pagecount': page + 1,
                'limit': len(lst) or 20, 'total': 999999}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        if not ids:
            return {'list': []}
        vid = str(ids[0] if not isinstance(ids, list) else ids[0]).strip()
        if not vid.startswith('/') and not vid.startswith('http'):
            vid = '/v/' + vid
        html, final = self._fetch(vid)

        if self._need_login(final) or 'id="magnets-content"' not in (html or ''):
            # 站点现在要登录才给磁链。如实说明，不假装成功。
            basic = pq(html or '')
            name = self._fix_mojibake((basic('.video-title strong').text() or '').strip())
            return {'list': [{
                'vod_id': vid,
                'vod_name': name or 'JavDB',
                'vod_pic': '',
                'vod_content': ('JavDB 现在需要登录才能显示磁力链接。\n'
                                '在订阅里把这个源的 ext 填成你的账号 cookie 就能恢复：\n'
                                '{"cookie":"_jdb_session=你的值"}'),
                'vod_play_from': '提示',
                'vod_play_url': '说明$' + vid,
                'vod_remarks': '需登录',
            }]}

        data = pq(html)
        title = self._fix_mojibake((data('.video-title strong').text() or data('h1').text() or '').strip())
        vod = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': data('.cover img').attr('src') or '',
            'vod_year': self._fix_mojibake((data('.meta').text() or '').strip()),
            'vod_remarks': self._fix_mojibake((data('.score .value').text() or '').strip()),
            'vod_content': self._fix_mojibake((data('.video-title').text() or '').strip()),
        }
        magnets = []
        for item in data('#magnets-content .item').items():
            a = item('.magnet-name a')
            href = a.attr('href')
            name = self._fix_mojibake((a.text() or '').strip())
            if href and str(href).startswith('magnet:'):
                magnets.append('%s$%s' % (name or ('磁链%d' % (len(magnets) + 1)), href))
        vod['vod_play_from'] = '磁力链接'
        vod['vod_play_url'] = '#'.join(magnets) if magnets else ''
        return {'list': [vod]}

    def playerContent(self, flag, id, vipFlags=None):
        val = str(id or '').strip()
        if '$' in val:
            val = val.split('$')[-1].strip()
        if val.startswith('magnet:'):
            return {'parse': 0, 'url': val, 'header': self.headers}
        return {'parse': 0, 'url': val, 'header': self.headers}
