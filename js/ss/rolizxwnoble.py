# -*- coding: utf-8 -*-
"""
aissdj 站群（原 rolizxwnoble）适配

站点特点：
  1) 老域名 pnd27y1jh1.rolizxwnoble.buzz 已废，只剩一个 JS 跳转页；
     真正域名每天按固定算法生成：https://{t|d|l}{月份词}{日期词}.aissdj.cc
  2) 苹果CMS 内核：列表 /detail/{id}/，播放页 /play/{id}-1-1/
  3) 真播放地址不走播放页，要调接口 /huangguo.php?ac=play&id=&ep=

所以这里做两件事：自动算出当天域名 + 走接口取真 m3u8。
"""
import re
import json
import html as htmlmod
import datetime
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

try:
    from lxml import etree
except Exception:  # 壳里没 lxml 时退化为正则解析
    etree = None

# 跳转页里的月份/日期词表
STR_ARR = ['ask', 'act', 'and', 'bar', 'cap', 'day', 'hit', 'eye', 'for', 'ice']
# 旧跳转页，兜底用（如果还能访问）
PORTAL = "https://tools.aissdj.buzz/ssdj/"

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class Spider:
    def __init__(self):
        self.host = ""
        self.headers = {
            "User-Agent": UA_PC,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.categories = []
        self.max_pages = 1

    # ---------------- TVBox 必需接口 ----------------
    def getDependence(self):
        return []

    def getName(self):
        return "aissdj"

    def init(self, extend=""):
        ext = (extend or "").strip()
        if ext.startswith("http"):
            # 允许在 ext 里直接写死当天域名
            self.host = ext.rstrip("/")
        else:
            self.host = self._resolve_host()
        if not self.host:
            self.host = self._portal_host() or ""
        if self.host:
            self.headers["Referer"] = self.host + "/home/"
            self.session.headers.update(self.headers)
            self.categories = self._build_cates()

    def isVideoFormat(self, url):
        return bool(url) and bool(
            re.search(r"\.(?:m3u8|mp4|flv|ts)(?:$|[?#])", str(url), re.I))

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return {}

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass

    def localProxy(self, param):
        return [404, "text/plain", b"Not Found", {}]

    # ---------------- 域名解析 ----------------
    @staticmethod
    def _word(n):
        """把数字转成跳转页用的词（两位数按位拆）"""
        try:
            n = int(n)
        except Exception:
            return ""
        if n < 0:
            return ""
        if n < 10:
            return STR_ARR[n]
        return STR_ARR[n // 10] + STR_ARR[n % 10]

    @classmethod
    def _hosts_for(cls, dt):
        """按跳转页算法算出某一天的全部候选域名"""
        d = dt - datetime.timedelta(hours=5)      # 页面里 getHours()-5
        month = cls._word(d.month - 1)            # JS 的 getMonth() 从 0 开始
        day = cls._word(d.day)
        if not month or not day:
            return []
        return ["https://%s%s%s.aissdj.cc" % (p, month, day) for p in ("t", "d", "l")]

    def _candidates(self):
        now = datetime.datetime.now()
        out = []
        for delta in (0, -1, 1, -2, 2):
            for u in self._hosts_for(now + datetime.timedelta(days=delta)):
                if u not in out:
                    out.append(u)
        return out

    def _probe(self, base):
        try:
            r = self.session.get(base + "/home/", timeout=12, verify=False)
            if r.status_code == 200 and len(r.text or "") > 20000:
                return base
        except Exception:
            pass
        return ""

    def _resolve_host(self):
        for base in self._candidates():
            hit = self._probe(base)
            if hit:
                return hit
        return ""

    def _portal_host(self):
        """兜底：从旧跳转页的 JS 里把当天域名抠出来（页面还能开时可用）"""
        try:
            r = self.session.get(PORTAL, timeout=12, verify=False)
            t = r.text or ""
        except Exception:
            return ""
        m = re.search(r'str\s*=\s*"https://"\s*\+\s*randomString\(\)\s*\+\s*randDateString\(\)'
                      r'\s*\+\s*"\.([^"/]+?)/', t)
        dom = m.group(1) if m else "aissdj.cc"
        for base in self._candidates():
            if base.endswith(dom) and self._probe(base):
                return base
        return ""

    # ---------------- 基础请求 ----------------
    def _fetch(self, url, referer=None, timeout=20):
        if not url:
            return ""
        h = dict(self.headers)
        if referer:
            h["Referer"] = referer
        try:
            r = self.session.get(url, headers=h, timeout=timeout, verify=False)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text or ""
        except Exception:
            return ""
        return ""

    def _fix(self, u):
        u = str(u or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http"):
            return u
        return urljoin(self.host + "/", u.lstrip("/"))

    @staticmethod
    def _clean(s):
        s = htmlmod.unescape(re.sub(r"<[^>]+>", "", str(s or "")))
        return re.sub(r"\s+", " ", s).strip()

    # ---------------- 分类 ----------------
    def _build_cates(self):
        cates = [{"type_id": "---time----------", "type_name": "最近更新"},
                 {"type_id": "---hits----------", "type_name": "最多播放"}]
        page = self._fetch(self.host + "/home/")
        seen = {c["type_name"] for c in cates}
        for href, name in re.findall(
                r'href="/search/([^"]+?)"[^>]*>\s*([^<]{1,12})\s*<', page):
            name = self._clean(name)
            if not name or name in seen:
                continue
            if "---" not in href:
                continue
            seen.add(name)
            cates.append({"type_id": href, "type_name": name})
            if len(cates) >= 220:
                break
        return cates

    # ---------------- 列表解析 ----------------
    def _parse_cards(self, page_text):
        items = []
        if not page_text:
            return items
        if etree is not None:
            try:
                doc = etree.HTML(page_text)
            except Exception:
                doc = None
            if doc is not None:
                for box in doc.xpath(
                        '//div[contains(concat(" ",normalize-space(@class)," "),'
                        ' " video-element ")]'):
                    hrefs = box.xpath('.//a[contains(@href,"/detail/")]/@href')
                    if not hrefs:
                        continue
                    m = re.search(r"/detail/(\d+)", hrefs[0])
                    if not m:
                        continue
                    vid = m.group(1)
                    titles = box.xpath('.//h4[contains(@class,"video-title")]//text()')
                    name = self._clean("".join(titles)) or vid
                    pics = box.xpath('.//img[contains(@class,"video-cover-img")]/@src')
                    if not pics:
                        pics = box.xpath('.//img/@src')
                    pic = self._fix(pics[0]) if pics else ""
                    note = ""
                    nv = box.xpath('.//div[contains(@class,"video-view")]//text()')
                    if nv:
                        note = self._clean("".join(nv))
                    items.append({"vod_id": vid, "vod_name": name,
                                  "vod_pic": pic, "vod_remarks": note})
                if items:
                    return items
        # 正则兜底
        for blk in re.split(r'class="[^"]*video-element[^"]*"', page_text)[1:]:
            blk = blk[:4000]
            m = re.search(r'href="/detail/(\d+)/?"', blk)
            if not m:
                continue
            vid = m.group(1)
            t = re.search(r'class="video-title"[^>]*>[\s\S]{0,300}?<a[^>]*>([\s\S]{0,120}?)</a>', blk)
            p = re.search(r'class="video-cover-img"[^>]*src="([^"]+)"', blk) or \
                re.search(r'<img[^>]+src="([^"]+)"', blk)
            items.append({
                "vod_id": vid,
                "vod_name": self._clean(t.group(1)) if t else vid,
                "vod_pic": self._fix(p.group(1)) if p else "",
                "vod_remarks": "",
            })
        return items

    def homeContent(self, filter=None):
        if not self.host:
            self.host = self._resolve_host() or self._portal_host() or ""
        if self.host and not self.categories:
            self.categories = self._build_cates()
        return {"class": list(self.categories), "filters": {}}

    def homeVideoContent(self):
        if not self.host:
            return {"list": []}
        return {"list": self._parse_cards(self._fetch(self.host + "/home/"))[:60]}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        tid = str(tid or "").strip().strip("/")
        if not tid:
            return {"list": [], "page": pg, "pagecount": 1, "limit": 0, "total": 0}
        if "---" in tid:
            base = self.host + "/search/" + tid + "/"
        else:
            base = self.host + "/search/" + tid + "/"
        url = base
        if pg > 1:
            url = base + "page/" + str(pg) + "/"
        items = self._parse_cards(self._fetch(url, referer=self.host + "/home/"))
        if pg > 1 and not items:
            # 站点分页格式不固定，退回 ?page=
            items = self._parse_cards(
                self._fetch(base + "?page=" + str(pg), referer=self.host + "/home/"))
        return {"list": items, "page": pg,
                "pagecount": pg + 1 if len(items) >= 10 else pg,
                "limit": len(items), "total": 999999}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pg = int(pg) if pg else 1
        except Exception:
            pg = 1
        if not self.host:
            return {"list": []}
        kw = quote(str(key))
        for url in (self.host + "/search/" + kw + "-------------/",
                    self.host + "/search/" + kw + "/"):
            items = self._parse_cards(self._fetch(url, referer=self.host + "/home/"))
            if items:
                return {"list": items, "page": pg, "pagecount": pg,
                        "limit": len(items), "total": len(items)}
        return {"list": [], "page": pg, "pagecount": pg, "limit": 0, "total": 0}

    # ---------------- 播放地址 ----------------
    def _api_play(self, vid, ep):
        """调站内接口拿真 m3u8"""
        api = "%s/huangguo.php?ac=play&id=%s&ep=%s" % (self.host, vid, ep)
        h = dict(self.headers)
        h["Referer"] = "%s/static/player/huangguo.html" % self.host
        h["X-Requested-With"] = "XMLHttpRequest"
        try:
            r = self.session.get(api, headers=h, timeout=20, verify=False)
            j = r.json()
            if str(j.get("code")) == "1":
                return str(j.get("url") or "")
        except Exception:
            return ""
        return ""

    def _player_data(self, play_page):
        m = re.search(r"var\s+player_data\s*=\s*(\{.*?\})\s*</script>", play_page, re.S)
        if not m:
            m = re.search(r"player_data\s*=\s*(\{.*?\})\s*;", play_page, re.S)
        if not m:
            return {}
        try:
            return json.loads(m.group(1))
        except Exception:
            return {}

    def _eps(self, vid, first_page):
        """顺 link_next 把后续集数也抓出来（最多 60 集）"""
        out = []
        seen = set()
        page = first_page
        pd = self._player_data(page)
        guard = 0
        while pd and guard < 60:
            guard += 1
            raw = str(pd.get("url") or "")            # 形如 "2293|1"
            if "|" not in raw:
                break
            try:
                rid, ep = raw.split("|", 1)
                rid, ep = str(int(rid)), str(int(ep))
            except Exception:
                break
            if (rid, ep) in seen:
                break
            seen.add((rid, ep))
            url = self._api_play(rid, ep)
            if url:
                out.append((ep, url))
            nxt = str(pd.get("link_next") or "").strip()
            if not nxt:
                break
            page = self._fetch(urljoin(self.host + "/", nxt.lstrip("/")),
                               referer=self.host + "/home/")
            pd = self._player_data(page)
        return out

    def detailContent(self, ids):
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ""
        else:
            vid = str(ids or "")
        vid = vid.strip().strip("/")
        m = re.search(r"(\d+)", vid)
        if not m:
            return {"list": []}
        vid = m.group(1)

        detail_url = "%s/detail/%s/" % (self.host, vid)
        page = self._fetch(detail_url, referer=self.host + "/home/")

        name = ""
        mm = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', page or "")
        if mm:
            name = htmlmod.unescape(mm.group(1))
        if not name:
            mm = re.search(r"<h1[^>]*>([\s\S]{0,200}?)</h1>", page or "")
            name = self._clean(mm.group(1)) if mm else vid
        name = re.sub(r"在线观看\s*$", "", name).strip() or vid

        pic = ""
        mm = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page or "")
        if mm:
            pic = self._fix(mm.group(1))

        content = ""
        mm = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', page or "")
        if mm:
            content = self._clean(mm.group(1))

        play_url = "%s/play/%s-1-1/" % (self.host, vid)
        play_page = self._fetch(play_url, referer=detail_url)
        eps = self._eps(vid, play_page)

        if not eps:
            # 兜底：接口挂了就让壳自己去解析播放页
            return {"list": [{
                "vod_id": vid, "vod_name": name, "vod_pic": pic,
                "vod_content": content, "vod_year": "", "vod_area": "",
                "vod_actor": "", "vod_director": "", "vod_remarks": "",
                "vod_play_from": "aissdj", "vod_play_url": "播放$" + play_url,
            }]}

        eps = sorted(eps, key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)
        parts = []
        for ep, url in eps:
            parts.append(("第%s集" % ep) + "$" + url)
        return {"list": [{
            "vod_id": vid, "vod_name": name, "vod_pic": pic,
            "vod_content": content, "vod_year": "", "vod_area": "",
            "vod_actor": "", "vod_director": "", "vod_remarks": "",
            "vod_play_from": "aissdj", "vod_play_url": "#".join(parts),
        }]}

    def playerContent(self, flag, id, vipFlags=None):
        if isinstance(id, (list, tuple)):
            id = str(id[0]) if id else ""
        else:
            id = str(id or "")
        if self.isVideoFormat(id):
            p = urlparse(id)
            ref = "%s://%s/" % (p.scheme, p.netloc) if p.netloc else self.host + "/"
            return {"parse": 0, "jx": 0, "playUrl": "", "url": id,
                    "header": {"Referer": ref, "User-Agent": self.headers["User-Agent"]}}
        if id.startswith("http"):
            page = self._fetch(id, referer=self.host + "/home/")
            pd = self._player_data(page)
            raw = str(pd.get("url") or "")
            if "|" in raw:
                rid, ep = raw.split("|", 1)
                real = self._api_play(rid.strip(), ep.strip())
                if real:
                    return {"parse": 0, "jx": 0, "playUrl": "", "url": real,
                            "header": {"Referer": self.host + "/",
                                       "User-Agent": self.headers["User-Agent"]}}
            return {"parse": 1, "jx": 0, "playUrl": "", "url": id,
                    "header": dict(self.headers)}
        return {"parse": 0, "jx": 0, "playUrl": "", "url": id,
                "header": dict(self.headers)}
