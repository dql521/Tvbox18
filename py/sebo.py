# -*- coding: utf-8 -*-
"""色播直播 - 全量镜像版（数据托管在自己服务器，直连可播）"""
import re
import sys
import json
import ssl
import urllib.request

sys.path.append("..")
from base.spider import Spider

UA = ("Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
DEFAULT_BASE = "https://gh-proxy.com/https://raw.githubusercontent.com/dql521/Tvbox18/main/sebo/"


class Spider(Spider):
    def __init__(self):
        self.base = DEFAULT_BASE
        self._cache = {}
        self._ctx = None

    def _ssl(self):
        if self._ctx is None:
            c = ssl.create_default_context()
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            self._ctx = c
        return self._ctx

    def _get(self, url, timeout=20):
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        return urllib.request.urlopen(req, timeout=timeout, context=self._ssl()).read().decode('utf-8', 'replace')

    def _json(self, url):
        if url in self._cache:
            return self._cache[url]
        d = json.loads(self._get(url))
        self._cache[url] = d
        return d

    def init(self, extend=""):
        e = (extend or '').strip()
        if e.startswith('http'):
            self.base = e.rstrip('/') + '/'
        return

    def getName(self):
        return "色播直播"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(?:m3u8|flv|mp4)(?:$|[?#])', str(url or ''), re.I))

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return

    def destroy(self):
        return

    def homeContent(self, filter):
        # 平台作为列表项直接展示，不再铺成上百个分类 tab
        return {'class': [], 'filters': {}, 'list': self._plats()}

    def homeVideoContent(self):
        return {'list': self._plats()}

    def _pindex(self):
        try:
            return self._json(self.base + 'json.txt').get('pingtai', []) or []
        except Exception:
            return []

    def _plats(self):
        out = []
        for p in self._pindex():
            addr = p.get('address')
            if not addr:
                continue
            n = str(p.get('Number') or '').strip()
            remark = ('%s 个直播间' % n) if n and n not in ('0', 'None') else '直播平台'
            out.append({
                'vod_id': 'sebo::' + str(addr),
                'vod_name': p.get('title') or str(addr),
                'vod_pic': p.get('xinimg') or '',
                'vod_remarks': remark,
            })
        return out

    def _title_of(self, addr):
        for p in self._pindex():
            if str(p.get('address')) == str(addr):
                return p.get('title') or str(addr)
        return str(addr)

    def categoryContent(self, tid, pg, filter, extend):
        # 兜底：壳若仍按分类调用，同样给出平台列表
        lst = self._plats()
        return {'list': lst, 'page': 1, 'pagecount': 1, 'limit': 999, 'total': len(lst)}

    def searchContent(self, key, quick, pg="1"):
        return {'list': []}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) and ids else str(ids)
        room = str(vid).split('sebo::', 1)[-1]
        try:
            items = self._json(self.base + room).get('zhubo', []) or []
        except Exception:
            items = []
        # 该平台的每个直播间 = 一集
        segs = []
        for it in items:
            u = str(it.get('address') or '').strip()
            if not u:
                continue
            nm = re.sub(r'[#$]', ' ', str(it.get('title') or '直播')).strip()[:40] or '直播'
            segs.append('%s$%s' % (nm, u))
        if not segs:
            return {'list': []}
        return {'list': [{
            'vod_id': vid, 'vod_name': self._title_of(room), 'vod_pic': '',
            'vod_content': '',
            'vod_play_from': '直播', 'vod_play_url': '#'.join(segs),
        }]}

    def playerContent(self, flag, id, keys=None):
        return {'parse': 0, 'url': str(id or ''), 'header': json.dumps({'User-Agent': UA})}
