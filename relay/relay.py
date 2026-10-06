#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zaka 中转服务 (relay)  —— 单文件、纯标准库、可跑在任意一台能访问上游的机器上

它同时提供三种用法:

1) 反向中转(给规则源/采集接口用)
   http://中转机:8899/https/目标域名/路径?查询
       -> 实际请求 https://目标域名/路径?查询
   http://中转机:8899/http/目标域名/路径
       -> 实际请求 http://目标域名/路径

2) 正向代理(给 py 插件用)
   proxies = {'http': 'http://中转机:8899', 'https': 'http://中转机:8899'}
   HTTP 走绝对地址转发，HTTPS 走 CONNECT 隧道。

3) 内容改写
   HTML / m3u8 / JSON 里的绝对地址自动改写成走中转，避免"列表能出、点开又断"。

用法:
   python3 relay.py                      # 监听 0.0.0.0:8899
   python3 relay.py -p 9000 --token abc  # 换端口 + 访问口令
   python3 relay.py --rewrite all        # 连其它域名的地址也改写成走中转
"""
import argparse
import http.client
import http.server
import ipaddress
import json
import os
import re
import socket
import socketserver
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit, unquote

VERSION = "1.0"

HOP = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade',
    'content-length', 'host',
}

# ---------------------------------------------------------------- 全局配置
CFG = {
    'rewrite': 'same',      # off | same | all
    'token': '',
    'allow_private': False,
    'timeout': 25,
    'max_redirect': 5,
    'debug': False,
}

JAR = {}                    # host -> {cookie: value}
JAR_LOCK = threading.Lock()
HOST_OK = {}                # host -> (ts, ok)
HOST_LOCK = threading.Lock()

UA_FALLBACK = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def log(*a):
    sys.stdout.write("%s %s\n" % (time.strftime('%H:%M:%S'), ' '.join(str(x) for x in a)))
    sys.stdout.flush()


# ---------------------------------------------------------------- 工具
def relay_url(relay_base, url):
    """把上游绝对地址改写成走中转的地址"""
    if not url or url.startswith(relay_base):
        return url
    try:
        s = urlsplit(url)
    except Exception:
        return url
    if s.scheme not in ('http', 'https') or not s.netloc:
        return url
    out = '%s/%s/%s%s' % (relay_base, s.scheme, s.netloc, s.path or '/')
    if s.query:
        out += '?' + s.query
    return out


def host_allowed(host):
    """拒绝明显的内网地址,避免中转被当成内网跳板"""
    if CFG['allow_private']:
        return True
    now = time.time()
    with HOST_LOCK:
        hit = HOST_OK.get(host)
        if hit and now - hit[0] < 600:
            return hit[1]
    ok = True
    try:
        ips = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
        for ip in ips:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if (a.is_private or a.is_loopback or a.is_link_local
                    or a.is_reserved or a.is_multicast or a.is_unspecified):
                ok = False
                break
    except Exception:
        ok = True          # 解析失败交给真正的请求去报错
    with HOST_LOCK:
        HOST_OK[host] = (now, ok)
    return ok


def cookie_header(host, client_cookie):
    with JAR_LOCK:
        jar = dict(JAR.get(host) or {})
    for item in str(client_cookie or '').split(';'):
        if '=' in item:
            k, v = item.split('=', 1)
            jar[k.strip()] = v.strip()
    return '; '.join('%s=%s' % (k, v) for k, v in jar.items()) if jar else ''


def jar_store(host, set_cookies):
    if not set_cookies:
        return
    with JAR_LOCK:
        box = JAR.setdefault(host, {})
        for raw in set_cookies:
            first = str(raw).split(';')[0].strip()
            if '=' in first:
                k, v = first.split('=', 1)
                box[k.strip()] = v.strip()


# ---------------------------------------------------------------- 内容改写
def rewrite_text(text, base_url, relay_base, kind):
    """kind: m3u8 / json / text"""
    if kind == 'm3u8':
        def sub(m):
            return relay_url(relay_base, m.group(0))
        text = re.sub(r'URI="([^"]+)"', lambda m: 'URI="%s"' % relay_url(relay_base, m.group(1)), text)
        text = re.sub(r'^(?!#)(\s*)(https?://\S+)\s*$', lambda m: m.group(1) + sub(m), text, flags=re.M)
        return text
    # html / json：只动真正的绝对地址
    base_host = urlsplit(base_url).hostname or ''
    hosts = {base_host}
    if base_host.startswith('www.'):
        hosts.add(base_host[4:])
    else:
        hosts.add('www.' + base_host)
    mode = CFG['rewrite']

    def rep(m):
        u = m.group(0)
        try:
            h = urlsplit(u).hostname or ''
        except Exception:
            return u
        if mode == 'same' and h not in hosts:
            return u
        return relay_url(relay_base, u)

    return re.sub(r'https?://[^\s"\'<>\\{}|]+', rep, text)


# ---------------------------------------------------------------- 请求处理
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ZakaRelay/' + VERSION
    relay_base = ''

    def log_message(self, fmt, *a):
        if CFG['debug']:
            log('*', self.address_string(), fmt % a)

    # ---------- 口令 ----------
    def _auth(self, query=None, path_ok=False):
        tok = CFG['token']
        if not tok:
            return True
        if path_ok:
            return True
        if self.headers.get('X-Relay-Token') == tok:
            return True
        pa = self.headers.get('Proxy-Authorization') or ''
        if pa.lower().startswith('basic '):
            import base64
            try:
                raw = base64.b64decode(pa.split(None, 1)[1]).decode('utf-8', 'replace')
                if raw.split(':')[-1] == tok:
                    return True
            except Exception:
                pass
        if query and query.get('__t') == tok:
            return True
        self.send_response(407)
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        return False

    # ---------- 入口 ----------
    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')

    def do_HEAD(self):
        self._handle('HEAD')

    def do_PUT(self):
        self._handle('PUT')

    def do_DELETE(self):
        self._handle('DELETE')

    def do_OPTIONS(self):
        self._handle('OPTIONS')

    def do_CONNECT(self):
        if not self._auth():
            return
        host, _, port = self.path.partition(':')
        try:
            port = int(port or 443)
        except ValueError:
            port = 443
        if not host_allowed(host):
            self.send_error(403, 'host not allowed')
            return
        try:
            upstream = socket.create_connection((host, port), timeout=CFG['timeout'])
        except Exception as e:
            log('CONNECT FAIL', host, port, e)
            self.send_error(502, 'upstream unreachable')
            return
        self.send_response(200, 'Connection Established')
        self.end_headers()
        self.close_connection = True
        t = threading.Thread(target=self._pump_oneway, args=(self.connection, upstream))
        t.daemon = True
        t.start()
        try:
            self._pump_oneway(upstream, self.connection)
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    def _pump_oneway(self, src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    # ---------- 主流程 ----------
    def _handle(self, method):
        raw = self.path
        path, _, query = raw.partition('?')
        qs = {}
        for item in query.split('&'):
            if '=' in item:
                k, v = item.split('=', 1)
                qs[unquote(k)] = unquote(v)
        path_ok = False
        if CFG['token']:
            seg = path.lstrip('/').split('/', 1)
            if seg and seg[0] == CFG['token']:
                path_ok = True
                path = '/' + (seg[1] if len(seg) > 1 else '')
        if not self._auth(qs, path_ok):
            return
        if path in ('/', '/_ping', '/ping'):
            body = json.dumps({'ok': True, 'relay': VERSION, 'rewrite': CFG['rewrite']}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if method != 'HEAD':
                self.wfile.write(body)
            return
        if query:
            query = '&'.join(x for x in query.split('&') if not x.startswith('__t='))
        url = self._resolve(path, query)
        if not url:
            self.send_error(404, 'bad relay path')
            self.close_connection = True
            return
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n > 0 else None
        t0 = time.time()
        try:
            self._forward(method, url, body)
        except Exception as e:
            if CFG['debug']:
                log('FAIL', method, url, repr(e))
            try:
                self.send_error(502, 'upstream error')
            except Exception:
                pass
            self.close_connection = True
            return
        if CFG['debug']:
            log('%s %s %.0fms' % (method, url[:120], (time.time() - t0) * 1000))

    def _resolve(self, path, query):
        if path.startswith('http://') or path.startswith('https://'):
            return path if not query else path + '?' + query    # 正向代理
        seg = path.lstrip('/').split('/', 2)
        if len(seg) >= 2 and seg[0] in ('http', 'https') and '.' in seg[1] and ' ' not in seg[1]:
            rest = ('/' + seg[2]) if len(seg) > 2 else ''
            url = '%s://%s%s' % (seg[0], seg[1], rest)
            return url + ('?' + query if query else '')
        return None

    # ---------- 真正的转发 ----------
    def _forward(self, method, url, body):
        seen = 0
        while True:
            s = urlsplit(url)
            host = s.hostname or ''
            if not host or not host_allowed(host):
                self.send_error(403, 'host not allowed')
                self.close_connection = True
                return
            port = s.port or (443 if s.scheme == 'https' else 80)
            path = s.path or '/'
            if s.query:
                path += '?' + s.query

            hdrs = {}
            for k, v in self.headers.items():
                if k.lower() in HOP:
                    continue
                hdrs[k] = v
            hdrs.setdefault('User-Agent', UA_FALLBACK)
            hdrs['Host'] = s.netloc
            ck = cookie_header(host, self.headers.get('Cookie'))
            if ck:
                hdrs['Cookie'] = ck
            else:
                hdrs.pop('Cookie', None)
            if CFG['rewrite'] != 'off':
                hdrs['Accept-Encoding'] = 'identity'
            if host_allowed(host) is False:
                self.send_error(403, 'host not allowed')
                return

            if s.scheme == 'https':
                conn = http.client.HTTPSConnection(host, port, timeout=CFG['timeout'], context=CTX)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=CFG['timeout'])
            try:
                conn.request(method, path, body=body, headers=hdrs)
                resp = conn.getresponse()
            except Exception:
                conn.close()
                raise

            # 跟随跳转（服务端完成，客户端只管拿最终结果）
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader('Location') or ''
                jar_store(host, resp.msg.get_all('Set-Cookie'))
                resp.read()
                conn.close()
                seen += 1
                if loc and seen <= CFG['max_redirect']:
                    url = loc if loc.startswith('http') else (
                        '%s://%s%s' % (s.scheme, s.netloc, loc if loc.startswith('/') else '/' + loc))
                    if resp.status == 303:
                        method, body = 'GET', None
                    continue
                self.send_response(resp.status)
                self.send_header('Location', relay_url(self.relay_base, loc) if loc else '')
                self.send_header('Content-Length', '0')
                self.end_headers()
                self.close_connection = True
                return

            jar_store(host, resp.msg.get_all('Set-Cookie'))
            self._relay_response(resp, conn, url)
            return

    def _relay_response(self, resp, conn, url):
        ctype = (resp.getheader('Content-Type') or '')
        clen = resp.getheader('Content-Length')
        try:
            clen_i = int(clen) if clen is not None else None
        except ValueError:
            clen_i = None

        low = ctype.lower()
        is_m3u8 = ('mpegurl' in low or url.split('?')[0].lower().endswith(('.m3u8', '.m3u')))
        is_text = low.startswith('text/') or 'json' in low or 'xml' in low or is_m3u8

        do_rewrite = False
        if is_m3u8 and clen_i is not None and clen_i < 8 * 1024 * 1024:
            do_rewrite = True
        elif is_text and ctype and 'html' in low and (clen_i is None or clen_i < 8 * 1024 * 1024):
            do_rewrite = True

        heads = []
        for k, v in resp.getheaders():
            if k.lower() in HOP or k.lower() in ('content-length', 'location', 'set-cookie'):
                continue
            heads.append((k, v))
        if resp.getheader('Location'):
            heads.append(('Location', relay_url(self.relay_base, resp.getheader('Location'))))

        if do_rewrite:
            data = resp.read()
            if 'gzip' in (resp.getheader('Content-Encoding') or '').lower():
                import gzip
                try:
                    data = gzip.decompress(data)
                except Exception:
                    pass
                heads = [(k, v) for k, v in heads if k.lower() != 'content-encoding']
            txt = data.decode('utf-8', 'replace')
            kind = 'm3u8' if is_m3u8 else 'text'
            new = rewrite_text(txt, url, self.relay_base, kind)
            data = new.encode('utf-8')
            self.send_response(resp.status)
            for k, v in heads:
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
            conn.close()
            return

        # 原样透传（含 Range / 分段视频）
        self.send_response(resp.status)
        for k, v in heads:
            self.send_header(k, v)
        if clen_i is not None:
            self.send_header('Content-Length', str(clen_i))
            self.end_headers()
            if self.command != 'HEAD':
                left = clen_i
                while left > 0:
                    chunk = resp.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        else:
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            if self.command != 'HEAD':
                while True:
                    try:
                        chunk = resp.read(262144)
                    except Exception:
                        break
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        conn.close()


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description='Zaka 中转服务')
    ap.add_argument('-p', '--port', type=int, default=int(os.environ.get('RELAY_PORT', 8899)))
    ap.add_argument('-H', '--host', default='0.0.0.0')
    ap.add_argument('--public-base', default='', help='对外地址,例如 http://1.2.3.4:8899 或 https://r.你的域名')
    ap.add_argument('--token', default=os.environ.get('RELAY_TOKEN', ''), help='访问口令,留空则不校验')
    ap.add_argument('--rewrite', choices=['off', 'same', 'all'], default='same',
                    help='内容改写范围:off 不改 / same 只改本站地址(默认) / all 连其它域名一起改')
    ap.add_argument('--allow-private', action='store_true', help='允许中转指向内网地址')
    ap.add_argument('--timeout', type=int, default=25)
    ap.add_argument('--debug', action='store_true')
    a = ap.parse_args()

    CFG['rewrite'] = a.rewrite
    CFG['token'] = a.token
    CFG['allow_private'] = a.allow_private
    CFG['timeout'] = a.timeout
    CFG['debug'] = a.debug
    Handler.relay_base = (a.public_base or 'http://%s:%d' % (a.host if a.host != '0.0.0.0' else '127.0.0.1', a.port)).rstrip('/')

    srv = Server((a.host, a.port), Handler)
    log('中转服务已启动 监听 %s:%d  对外地址 %s  改写=%s  口令=%s'
        % (a.host, a.port, Handler.relay_base, a.rewrite, '有' if a.token else '无'))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('已停止')


if __name__ == '__main__':
    main()
