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
        try:
            d = self._json(self.base + 'json.txt')
            plats = d.get('pingtai', [])
        except Exception:
            plats = []
        return {'class': [{'type_name': p.get('title') or p.get('address'), 'type_id': p.get('address')}
                          for p in plats if p.get('address')], 'filters': {}}

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            d = self._json(self.base + str(tid))
            items = d.get('zhubo', [])
        except Exception:
            items = []
        out = []
        for it in items:
            u = it.get('address') or ''
            if not u:
                continue
            out.append({
                'vod_id': u,
                'vod_name': (it.get('title') or '直播')[:80],
                'vod_pic': it.get('img') or '',
                'vod_remarks': '直播',
            })
        return {'list': out, 'page': int(pg), 'pagecount': 1, 'limit': 999, 'total': len(out)}

    def searchContent(self, key, quick, pg="1"):
        return {'list': []}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) and ids else str(ids)
        return {'list': [{
            'vod_id': vid, 'vod_name': '直播', 'vod_pic': '',
            'vod_play_from': '直播', 'vod_play_url': '直播$' + vid,
        }]}

    def playerContent(self, flag, id, keys=None):
        return {'parse': 0, 'url': str(id or ''), 'header': json.dumps({'User-Agent': UA})}
