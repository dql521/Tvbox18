# coding: utf-8
# GGJAV 专版插件（重写版）
#
# 为什么要单独写插件，而不是用规则：
#   GGJAV 的播放地址不在 HTML 里，页面用一段混淆脚本在浏览器端现算：
#       var l = "<base64>";  abl = atob(l);
#       links = JSON.parse(每个字符码 - 0x58 拼起来);
#   结果里 "ggjav" 这一项是 /main/embed?u=<base64(直链)>，
#   直链再拼 /index.m3u8 就是真正的 HLS 播放地址。
#   规则引擎不会跑 JS、也不会解 base64，所以只能用插件按同样的算法解出来。
#
# 适配：FongMi / 影视TV（ESM 用 3 参数调用）与原版 TVBox（2 参数）都兼容
import sys
import re
import json
import base64
import html as html_lib

import requests
import urllib3
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

urllib3.disable_warnings()
sys.path.append('..')
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):

    hosts = [
        "https://ggjav.com",
    ]

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

    # 分类：站内实测首页/第 2 页都能出 20+ 条的
    CATES = [
        ("有碼AV", "censored"),
        ("無碼AV", "uncensored"),
        ("素人AV", "amateur"),
        ("中文字幕", "chinese"),
        ("歐美", "europe"),
        ("動漫", "cartoon"),
    ]

    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.host = self.hosts[0]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    # ---------------- 基础 ----------------
    def init(self, extend=""):
        ext = (extend or "").strip()
        if ext.startswith("http"):
            self.host = ext.rstrip("/")
        elif ext.startswith("{"):
            try:
                cfg = json.loads(ext)
                if cfg.get("host"):
                    self.host = str(cfg["host"]).rstrip("/")
            except Exception:
                pass
        return self

    def getName(self):
        return "GGJAV"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(?:m3u8|mp4|flv)(?:$|[?#])", str(url or ""), re.I))

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return

    def destroy(self):
        return

    def localProxy(self, param):
        return None

    # ---------------- 抓取 ----------------
    def _get(self, path, timeout=20):
        url = path if str(path).startswith("http") else self.host + (
            path if str(path).startswith("/") else "/" + str(path))
        for base in [self.host] + [h for h in self.hosts if h != self.host]:
            u = url
            if not str(path).startswith("http") and base != self.host:
                u = base + (path if str(path).startswith("/") else "/" + str(path))
            try:
                rsp = self.session.get(u, timeout=timeout, verify=False,
                                      headers={"Referer": base + "/"})
                if rsp.status_code == 200 and rsp.text and len(rsp.text) > 500:
                    if not str(path).startswith("http"):
                        self.host = base
                    return rsp.text
            except Exception:
                continue
        return ""

    # ---------------- 解析列表 ----------------
    def _items(self, doc):
        out = []
        for box in doc.select("div.item"):
            a = box.select_one("div.item_title a") or box.select_one("a")
            if not a:
                continue
            href = a.get("href") or ""
            if "/main/video" not in href:
                continue
            title = html_lib.unescape((a.get_text() or "").strip())
            if not title:
                continue
            img = box.select_one("img.item_image") or box.select_one("img")
            pic = ""
            if img:
                pic = img.get("src") or img.get("data-src") or ""
            views = ""
            v = box.select_one("div.item_views")
            if v:
                views = re.sub(r"\s+", " ", v.get_text(" ", strip=True))
            out.append({
                "vod_id": href,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": views[:40],
            })
        return out

    def _pagecount(self, doc, pg):
        try:
            cur = int(pg)
        except Exception:
            cur = 1
        nums = []
        for a in doc.select("a"):
            m = re.search(r"[?&]page=(\d+)", str(a.get("href") or ""))
            if m:
                nums.append(int(m.group(1)))
        if nums:
            return max(max(nums), cur + 1)
        return cur + 1

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        classes = [{"type_name": n, "type_id": c} for n, c in self.CATES]
        return {"class": classes, "filters": {}, "list": self.homeVideoContent().get("list", [])}

    def homeVideoContent(self):
        h = self._get("/")
        if not h:
            return {"list": []}
        return {"list": self._items(BeautifulSoup(h, "lxml"))}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        if page < 1:
            page = 1
        tid = str(tid or "").strip() or "censored"
        h = self._get("/main/%s?order=recommended&page=%d" % (tid, page))
        if not h:
            return {"list": [], "page": page, "pagecount": page, "limit": 0, "total": 0}
        doc = BeautifulSoup(h, "lxml")
        lst = self._items(doc)
        pc = self._pagecount(doc, page)
        if not lst and page > 1:
            pc = page
        return {"list": lst, "page": page, "pagecount": pc,
                "limit": len(lst) or 20, "total": 999999}

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg="1"):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        kw = quote(str(key or "").strip(), safe="")
        h = self._get("/main/search?string=%s&page=%d" % (kw, page))
        if not h:
            return {"list": [], "page": page, "pagecount": 0, "limit": 0, "total": 0}
        doc = BeautifulSoup(h, "lxml")
        lst = self._items(doc)
        return {"list": lst, "page": page, "pagecount": self._pagecount(doc, page),
                "limit": len(lst) or 20, "total": 999999}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = str(ids[0] if not isinstance(ids, list) else ids[0]).strip()
        if not vid.startswith("http") and not vid.startswith("/"):
            vid = "/main/video?id=%s" % vid
        h = self._get(vid)
        if not h:
            return {"list": []}
        doc = BeautifulSoup(h, "lxml")

        name = ""
        m = re.search(r"<title>([^<]*)</title>", h, re.S)
        if m:
            name = html_lib.unescape(m.group(1))
            name = re.split(r"\s*[-|]\s*GGJAV", name)[0].strip()
        if not name:
            t = doc.select_one("div.item_title a")
            name = html_lib.unescape(t.get_text().strip()) if t else "GGJAV"

        pic = ""
        m = re.search(r'property="og:image"\s+content="([^"]+)"', h)
        if m:
            pic = m.group(1)

        direct, sources = self._play_of(h)
        lines_from, lines_url = [], []
        if direct:
            lines_from.append("🎬 直連")
            lines_url.append("正片$" + vid)
        backup = []
        for key, arr in (sources or {}).items():
            if key == "ggjav":
                continue
            for u in (arr or [])[:1]:
                if isinstance(u, str) and u.startswith("http"):
                    backup.append("%s$%s" % (key[:14], u))
        if backup:
            lines_from.append("☁️ 備用源")
            lines_url.append("#".join(backup))
        if not lines_from:
            lines_from.append("🎬 直連")
            lines_url.append("正片$" + vid)

        return {"list": [{
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_content": name,
            "vod_play_from": "$$$".join(lines_from),
            "vod_play_url": "$$$".join(lines_url),
            "vod_remarks": "GGJAV",
        }]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags=None):
        val = str(id or "").strip()
        if "$" in val:
            val = val.split("$")[-1].strip()
        headers = {"User-Agent": self.UA, "Referer": self.host + "/"}

        # 站内详情页：现解一次，拿到直链
        if val.startswith("/main/video") or "/main/video" in val:
            h = self._get(val)
            direct = self._play_of(h)[0] if h else ""
            if direct:
                return {"parse": 0, "url": direct, "header": headers}
            return {"parse": 1, "url": urljoin(self.host + "/", val), "header": headers}

        # 第三方外链：交给壳自己的解析
        if val.startswith("http"):
            if self.isVideoFormat(val):
                return {"parse": 0, "url": val.replace(" ", "%20"), "header": headers}
            return {"parse": 1, "url": val, "header": headers}

        return {"parse": 0, "url": "", "msg": "invalid id"}

    # ---------------- 解码核心 ----------------
    def _decode_sources(self, page):
        """还原页面里那段混淆脚本，拿回所有线路（含 ggjav 的 base64 直链）"""
        if not page:
            return {}
        m = re.search(r'var\s+l\s*=\s*"([^"]+)"', page)
        if not m:
            return {}
        b64 = m.group(1)
        try:
            raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
            s = "".join(chr(c - 0x58) for c in raw)
            data = json.loads(s)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _play_of(self, page):
        """详情页 -> 真正可播的 HLS 地址（两级兜底）"""
        sources = self._decode_sources(page)
        url = self._direct_url(sources)
        if not url:
            url = self._plyr_url(sources)
        return url, sources

    def _plyr_url(self, sources):
        """部分片子没有站内直链，只有 plyr 线路；这一条同样是真 HLS"""
        if not isinstance(sources, dict):
            return ""
        for entry in (sources.get("plyr") or []):
            if not isinstance(entry, str) or not entry.startswith("http"):
                continue
            try:
                rsp = self.session.get(entry, timeout=20, verify=False,
                                       headers={"Referer": self.host + "/"})
                m = re.search(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', rsp.text or "")
                if m:
                    return m.group(0).replace(" ", "%20")
            except Exception:
                continue
        return ""

    def _direct_url(self, sources):
        """从 ggjav 线路里取出真正的 HLS 地址"""
        if not isinstance(sources, dict):
            return ""
        for entry in (sources.get("ggjav") or []):
            if not isinstance(entry, str):
                continue
            m = re.search(r"[?&]u=([^&]+)", entry)
            if not m:
                continue
            b = m.group(1)
            try:
                u = base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore").strip()
            except Exception:
                continue
            if not u.startswith("http"):
                continue
            if not u.lower().endswith(".m3u8"):
                u = u.rstrip("/") + "/index.m3u8"
            return u.replace(" ", "%20")
        return ""
