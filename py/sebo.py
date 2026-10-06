# -*- coding: utf-8 -*-
"""色播直播 - 实时版（直连上游接口取最新直播间与播放地址）

关键点：上游每次返回的都是带时效签名的直播地址，仓库里的静态快照只能当兜底。
所以这里优先实时拉取，且直播间列表不做长期缓存，每次打开平台都取新的，
地址失效能自动刷掉。
"""
import re
import sys
import json
import ssl
import time
import urllib.request
import urllib.parse
import urllib.error

sys.path.append("..")
from base.spider import Spider

UA = ("Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

# 中转地址：填了就所有取数走中转机（墙外源免梯子），留空 = 直连。
# 跑一次 set-relay.py 会自动填上，也可以手填，例如 "http://1.2.3.4:8899"。
RELAY = ""
RELAY_MEDIA = False          # 直播流是否也走中转（吃中转机流量，按需开）

# 实时接口（多路互备）
LIVE_BASES = [
    "http://api.maiyoux.com:81/mf/",
    "http://api.hclyz.com:81/mf/",
]
# 打包在仓库里的静态快照，只在实时接口全挂时兜底
SNAPSHOT_BASE = ("https://gh-proxy.com/https://raw.githubusercontent.com/"
                 "dql521/Tvbox18/main/sebo/")

IDX_TTL = 300      # 平台索引缓存 5 分钟
ROOM_TTL = 10      # 直播间列表只缓存 10 秒 —— 保证点开就是新的
BASE_TTL = 180     # 可用接口地址的记忆时间


class Spider(Spider):
    def __init__(self):
        self.ext_base = ''
        self._cache = {}          # (base,name) -> (ts, data)
        self._base = ''           # 当前可用接口
        self._base_ts = 0
        self._ctx = None

    # ---------- 网络 ----------
    def _ssl(self):
        if self._ctx is None:
            c = ssl.create_default_context()
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            self._ctx = c
        return self._ctx

    def _wrap(self, url):
        u = str(url or '')
        base = (RELAY or '').rstrip('/')
        if not base or not u.startswith('http') or u.startswith(base):
            return u
        try:
            s = urllib.parse.urlsplit(u)
        except Exception:
            return u
        if not s.netloc:
            return u
        out = '%s/%s/%s%s' % (base, s.scheme, s.netloc, s.path or '/')
        if s.query:
            out += '?' + s.query
        return out

    def _get(self, url, timeout=15):
        req = urllib.request.Request(self._wrap(url), headers={
            'User-Agent': UA,
            'Accept': '*/*',
            'Connection': 'close',
        })
        try:
            return urllib.request.urlopen(req, timeout=timeout,
                                          context=self._ssl()).read().decode('utf-8', 'replace')
        except Exception:
            if self._wrap(url) == url:
                raise
            # 中转不通就直连兜底
            req = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept': '*/*', 'Connection': 'close'})
            return urllib.request.urlopen(req, timeout=timeout,
                                          context=self._ssl()).read().decode('utf-8', 'replace')

    def init(self, extend=""):
        e = (extend or '').strip()
        if e.startswith('http'):
            self.ext_base = e.rstrip('/') + '/'
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

    # ---------- 接口选用 ----------
    def _candidates(self):
        out = []
        # 扩展里如果填的是接口地址，优先用它
        if self.ext_base and '/mf' in self.ext_base:
            out.append(self.ext_base)
        out.extend(LIVE_BASES)
        if self.ext_base and self.ext_base not in out:
            out.append(self.ext_base)      # 快照目录（兜底）
        if SNAPSHOT_BASE not in out:
            out.append(SNAPSHOT_BASE)
        return out

    def _probe(self, base):
        try:
            d = json.loads(self._fetch(base, 'json.txt', 12))
            if isinstance(d, dict) and d.get('pingtai'):
                return True
        except Exception:
            pass
        return False

    def _pick_base(self):
        now = time.time()
        if self._base and (now - self._base_ts) < BASE_TTL:
            return self._base
        for b in self._candidates():
            if self._probe(b):
                self._base, self._base_ts = b, now
                return b
        # 全都探测失败：沿用上次可用的，避免整块变空
        return self._base or (self.ext_base or LIVE_BASES[0])

    def _fetch(self, base, name, timeout=15):
        return self._get(base + name, timeout=timeout)

    def _json(self, name, ttl):
        base = self._pick_base()
        key = (base, name)
        hit = self._cache.get(key)
        now = time.time()
        if hit and (now - hit[0]) < ttl:
            return hit[1]
        data = {'pingtai': []} if name == 'json.txt' else {'zhubo': []}
        # 首选接口拿不到就顺序换其它接口
        for b in [base] + [x for x in self._candidates() if x != base]:
            try:
                j = json.loads(self._fetch(b, name))
                if isinstance(j, dict) and (j.get('pingtai') or j.get('zhubo')):
                    data = j
                    self._base, self._base_ts = b, now
                    break
            except Exception:
                continue
        self._cache[key] = (now, data)
        return data

    def _pindex(self):
        try:
            return self._json('json.txt', IDX_TTL).get('pingtai', []) or []
        except Exception:
            return []

    # ---------- 平台列表（平台 = 列表项） ----------
    def _plats(self):
        out = []
        for p in self._pindex():
            addr = p.get('address')
            if not addr:
                continue
            n = str(p.get('Number') or '').strip()
            try:
                cnt = int(n)
            except Exception:
                cnt = 0
            remark = ('%d 个直播间' % cnt) if cnt > 0 else '点击进入'
            out.append({
                'vod_id': 'sebo::' + str(addr),
                'vod_name': p.get('title') or str(addr),
                'vod_pic': p.get('xinimg') or '',
                'vod_remarks': remark,
            })
        return out

    def homeContent(self, filter):
        return {'class': [], 'filters': {}, 'list': self._plats()}

    def homeVideoContent(self):
        return {'list': self._plats()}

    def _title_of(self, addr):
        for p in self._pindex():
            if str(p.get('address')) == str(addr):
                return p.get('title') or str(addr)
        return str(addr)

    def categoryContent(self, tid, pg, filter, extend):
        lst = self._plats()
        return {'list': lst, 'page': 1, 'pagecount': 1, 'limit': 999, 'total': len(lst)}

    def searchContent(self, key, quick, pg="1"):
        return {'list': []}

    # ---------- 直播间（每个直播间 = 一集，实时取） ----------
    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) and ids else str(ids)
        room = str(vid).split('sebo::', 1)[-1]
        if not re.search(r'\.(?:txt|json)$', room, re.I):
            room = str(room).split('/')[-1]
            if not room:
                return {'list': []}
            room = room if re.search(r'\.(?:txt|json)$', room, re.I) else room + '.txt'
        try:
            items = self._json(room, ROOM_TTL).get('zhubo', []) or []
        except Exception:
            items = []
        segs = []
        seen = set()
        for it in items:
            u = str(it.get('address') or '').strip()
            if not u or u in seen:
                continue
            seen.add(u)
            nm = re.sub(r'[#$]', ' ', str(it.get('title') or '直播')).strip()[:40] or '直播'
            segs.append('%s$%s' % (nm, u))
        if not segs:
            return {'list': []}
        return {'list': [{
            'vod_id': vid,
            'vod_name': self._title_of(room),
            'vod_pic': '',
            'vod_content': '',
            'vod_play_from': '直播',
            'vod_play_url': '#'.join(segs),
        }]}

    def playerContent(self, flag, id, keys=None):
        u = str(id or '')
        if RELAY_MEDIA:
            u = self._wrap(u)
        return {
            'parse': 0,
            'url': u,
            'header': json.dumps({'User-Agent': UA, 'Referer': 'http://api.maiyoux.com:81/'}),
        }
