/*
 * 直播转点播（原 js/live2vod.js 重写版）
 * 数据源：一个 JSON 数组文件（[{name, url}]），每条 = 一个分类
 * 列表：分类内每个频道各成一条，可点即播（不再把整个分类塞成一条合集）
 */
var headers = { "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36" };
// 中转地址：填了就所有取数走中转机（墙外源免梯子），留空 = 直连。
// 跑一次 set-relay.py 会自动填上。
var RELAY = "";
var RELAY_MEDIA = false;      // 直播流是否也走中转（吃中转机流量，按需开）
var classes = [];
var cates = {};       // tid -> 频道数组
var picUrl = "";
var pics = {};        // tid -> 该分类的图片模板
var webPaths = {};    // tid -> 源文件所在目录
var PAGE_SIZE = 100;

// ---------- 基础工具 ----------
function _content(res) {
  if (res == null) return "";
  if (typeof res === "string") return res;
  if (res.content != null) return String(res.content);
  return String(res);
}

function relayWrap(u) {
  u = String(u == null ? "" : u);
  var base = String(RELAY || "").replace(/\/+$/, "");
  if (!base || !/^https?:\/\//i.test(u) || u.indexOf(base) === 0) return u;
  var m = u.match(/^(https?):\/\/([^\/\s]+)(\/.*)?$/i);
  if (!m) return u;
  return base + "/" + m[1].toLowerCase() + "/" + m[2] + (m[3] || "/");
}

function get(url, hd) {
  return _content(req(relayWrap(url), { method: "GET", headers: hd || headers }));
}

function absUrl(u, base) {
  if (!u) return "";
  u = String(u).replace(/^\s+|\s+$/g, "");
  if (u.indexOf("://") >= 0) return u;
  return (base || "") + u;
}

function cleanName(s) {
  s = String(s == null ? "" : s).replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "");
  s = s.replace(/^groupName\s*=\s*/i, "");
  s = s.replace(/^\[|\]$/g, "");
  return s;
}

function sanitizeUrl(u) {
  return String(u == null ? "" : u).replace(/[\s#"']/g, "").replace(/&amp;/g, "&");
}

function isMediaUrl(u) {
  return /^(https?|rtmp|rtsp|rtp|mms):\/\//i.test(String(u || ""));
}

function decodeEntities(s) {
  s = String(s == null ? "" : s);
  s = s.replace(/&#x([0-9a-fA-F]+);/g, function (m, h) { return String.fromCharCode(parseInt(h, 16)); });
  s = s.replace(/&#(\d+);/g, function (m, d) { return String.fromCharCode(parseInt(d, 10)); });
  s = s.replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
  return s;
}

// ---------- init：读分类索引 ----------
function init(ext) {
  var base = "";
  var e = String(ext == null ? "" : ext);

  // ext 里可带 &&& 分隔的图片模板：源地址&&&图片模板
  if (e.indexOf("&&&") >= 0) {
    var seg = e.split("&&&");
    e = String(seg[0]).replace(/^\s+|\s+$/g, "");
    picUrl = String(seg[1] || "").replace(/^\s+|\s+$/g, "");
    if (picUrl && picUrl.indexOf("://") < 0) picUrl = e + picUrl;
  }
  if (!e) return;

  var body = "";
  if (e.indexOf("://") >= 0) {
    body = get(e);
  } else {
    body = e;   // ext 直接就是内容
  }
  if (e.indexOf("://") >= 0) {
    var i = e.lastIndexOf("/");
    base = i > 0 ? e.substring(0, i + 1) : "";
  }

  var list = null;
  try { list = JSON.parse(body); } catch (err) { list = null; }

  // 兼容 # 分隔的「名称$地址」写法
  if (!list) {
    var parts = body.split("#");
    for (var p = 0; p < parts.length; p++) {
      var one = parts[p].replace(/^\s+|\s+$/g, "");
      if (!one) continue;
      var sp = one.split("$");
      if (sp.length >= 2) pushClass(sp[0], sp[1], base);
      else if (isMediaUrl(one)) pushClass(one, one, base);
    }
    return;
  }

  if (list && list.length != null) {
    for (var k = 0; k < list.length; k++) {
      var it = list[k];
      if (!it) continue;
      var nm = it.name != null ? it.name : it.title;
      var u = it.url != null ? it.url : it.address;
      if (!nm || !u) continue;
      pushClass(nm, u, base);
    }
  }
}

function pushClass(name, url, base) {
  name = String(name).replace(/^\s+|\s+$/g, "");
  var u = String(url == null ? "" : url);
  var pic = "";
  // 单条源可自带图片模板：地址&&&图片
  if (u.indexOf("&&&") >= 0) {
    var sp = u.split("&&&");
    u = sp[0];
    pic = String(sp[1] || "").replace(/^\s+|\s+$/g, "");
  }
  u = absUrl(u, base);
  if (!u) return;
  var tid = name + "$" + u;
  if (webPaths[tid]) return;
  webPaths[tid] = base;
  if (pic) {
    if (pic.indexOf("://") < 0) {
      pic = pic.replace(/^\/+/, "");
      if (pic.indexOf("://") < 0) pic = base + pic;
    }
    pics[tid] = pic;
  } else if (picUrl) {
    pics[tid] = picUrl;
  }
  classes.push({ type_id: tid, type_name: name.replace(/!!/g, "") });
}

function home() {
  return JSON.stringify({ class: classes, filters: {} });
}

// ---------- 解析各种源格式 ----------
function parseM3u(text, defGroup) {
  var out = [];
  var lines = String(text).replace(/\r/g, "").split("\n");
  var cur = "";
  var group = defGroup;
  for (var i = 0; i < lines.length; i++) {
    var ln = lines[i].replace(/^\s+|\s+$/g, "");
    if (!ln) continue;
    if (ln.indexOf("#EXTINF") === 0) {
      var g = /group-title="(.*?)"/i.exec(ln);
      group = (g && g[1]) ? decodeEntities(g[1]) : defGroup;
      cur = decodeEntities(ln.split(",").length > 1 ? ln.substring(ln.indexOf(",") + 1) : "");
      continue;
    }
    if (ln.charAt(0) === "#") continue;
    if (!isMediaUrl(ln)) continue;
    var nm = cur || ln.split("/").pop() || "直播";
    out.push({ name: cleanName(nm) || "直播", url: sanitizeUrl(ln), group: group || defGroup });
    cur = "";
  }
  return out;
}

function parsePairs(text, defGroup) {
  var out = [];
  var lines = String(text).replace(/\r/g, "").split("\n");
  var group = defGroup;
  for (var i = 0; i < lines.length; i++) {
    var ln = lines[i].replace(/\s+/g, "");
    if (!ln) continue;
    var ci = ln.indexOf(",");
    var hi = ln.indexOf("http");
    if (ci < 0 || hi < 0) {
      // 分组/标题行：[Group] / groupName=xxx / 单独的分类名
      var g = cleanName(lines[i]);
      if (g && ln.indexOf("://") < 0 && ln.indexOf("Exportfrompan") < 0 && ln.length < 40) group = g;
      continue;
    }
    var nm = decodeEntities(lines[i].substring(0, lines[i].indexOf(",")));
    var url = ln.substring(hi);
    if (!isMediaUrl(url)) continue;
    out.push({ name: cleanName(nm) || "直播", url: sanitizeUrl(url), group: group || defGroup });
  }
  return out;
}

function parseJsonList(text, defGroup) {
  var out = [];
  var d = null;
  try { d = JSON.parse(text); } catch (e) { return out; }
  var i, j, k, ch, us;
  if (d && d.channel && d.urls) {
    for (i = 0; i < d.channel.length; i++) {
      ch = d.channel[i];
      var grp = ch.name || defGroup;
      if (ch.urls && ch.urls.length) {
        for (j = 0; j < ch.urls.length; j++) {
          var it = ch.urls[j];
          if (it && it.url) out.push({ name: cleanName(it.name || ("频道" + (j + 1))), url: sanitizeUrl(it.url), group: grp });
        }
      }
    }
  } else if (d && d.datalist) {
    for (i = 0; i < d.datalist.length; i++) {
      var dt = d.datalist[i];
      var gname = dt.name || defGroup;
      var cs = dt.channel || dt.url || [];
      for (j = 0; j < cs.length; j++) {
        var c = cs[j];
        var us2 = c.urls || [];
        if (us2.length) {
          for (k = 0; k < us2.length; k++) {
            if (us2[k].url) out.push({ name: cleanName(us2[k].name || c.name || ("频道" + (k + 1))), url: sanitizeUrl(us2[k].url), group: gname });
          }
        } else if (c.url || c.address) {
          out.push({ name: cleanName(c.name || c.title || ("频道" + (j + 1))), url: sanitizeUrl(c.url || c.address), group: gname });
        }
      }
    }
  } else if (d && (d.zhubo || d.data || d.list)) {
    var arr = d.zhubo || d.data || d.list;
    for (i = 0; i < arr.length; i++) {
      var r = arr[i];
      if (r && (r.address || r.url)) out.push({ name: cleanName(r.title || r.name), url: sanitizeUrl(r.address || r.url), group: defGroup });
    }
  }
  return out;
}

function parseLive(text, defGroup) {
  var t = String(text || "");
  if (t.indexOf("#EXTINF") >= 0 || t.indexOf("#EXTM3U") >= 0) {
    var m = parseM3u(t, defGroup);
    if (m.length) return m;
  }
  if (t.replace(/^\s+/, "").charAt(0) === "{") {
    var jd = {};
    try { jd = JSON.parse(t); } catch (e) { jd = {}; }
    if (jd.channel || jd.datalist || jd.zhubo || jd.data || jd.list) return parseJsonList(t, defGroup);
  }
  var p = parsePairs(t, defGroup);
  if (p.length) return p;
  return parseM3u(t, defGroup);
}

function dedupe(arr) {
  var seen = {};
  var out = [];
  for (var i = 0; i < arr.length; i++) {
    var k = arr[i].url;
    if (seen[k]) continue;
    seen[k] = 1;
    out.push(arr[i]);
  }
  return out;
}

// ---------- 取分类数据（带缓存） ----------
function getCateData(tid) {
  if (cates[tid]) return cates[tid];
  var i = String(tid).indexOf("$");
  if (i < 0) return [];
  var name = String(tid).substring(0, i);
  var url = String(tid).substring(i + 1);
  if (!isMediaUrl(url)) url = absUrl(url, webPaths[tid] || "");
  var body = "";
  try { body = get(url); } catch (e) { body = ""; }
  var list = [];
  if (body) list = dedupe(parseLive(body, name));
  cates[tid] = list;
  return list;
}

function pic4(ch, tid) {
  var tpl = pics[tid] || picUrl;
  if (!tpl) return "";
  return tpl.replace(/\{name\}/g, encodeURIComponent(ch.name)).replace(/\{cate\}/g, encodeURIComponent(ch.group || ""));
}

function toVod(tid, ch, idx) {
  return {
    vod_id: tid + "$$$" + idx,
    vod_name: ch.name,
    vod_pic: pic4(ch, tid),
    vod_remarks: (ch.group && ch.group !== (String(tid).split("$")[0])) ? ch.group : "",
    type_name: String(tid).split("$")[0].replace(/!!/g, ""),
    vod_year: "", vod_area: "", vod_actor: "", vod_director: "", vod_content: ""
  };
}

function homeVod() {
  if (!classes.length) return JSON.stringify({ list: [] });
  var tid = classes[0].type_id;
  var all = getCateData(tid);
  var out = [];
  for (var i = 0; i < all.length && i < PAGE_SIZE; i++) out.push(toVod(tid, all[i], i));
  return JSON.stringify({ list: out });
}

function category(tid, pg, filter, ext) {
  var all = getCateData(tid);
  var page = parseInt(pg, 10);
  if (!page || page < 1) page = 1;
  var start = (page - 1) * PAGE_SIZE;
  var out = [];
  for (var i = start; i < all.length && i < start + PAGE_SIZE; i++) out.push(toVod(tid, all[i], i));
  var pagecount = Math.max(1, Math.ceil(all.length / PAGE_SIZE));
  return JSON.stringify({ list: out, page: page, pagecount: pagecount, limit: PAGE_SIZE, total: all.length });
}

function detail(id) {
  var vid = String(id);
  var pos = vid.indexOf("$$$");
  if (pos < 0) return JSON.stringify({ list: [] });
  var tid = vid.substring(0, pos);
  var idx = parseInt(vid.substring(pos + 3), 10) || 0;
  var all = getCateData(tid);
  var ch = all[idx];
  if (!ch) {
    // 缓存失效时按名字兜底匹配
    for (var i = 0; i < all.length; i++) { if (all[i].name) { ch = all[i]; break; } }
  }
  if (!ch) return JSON.stringify({ list: [] });
  var from = String(tid).split("$")[0].replace(/!!/g, "") || "直播";
  return JSON.stringify({
    list: [{
      vod_id: vid,
      vod_name: ch.name,
      vod_pic: pic4(ch, tid),
      vod_content: "",
      vod_play_from: from,
      vod_play_url: ch.name.replace(/\$/g, " ").replace(/#/g, " ") + "$" + ch.url
    }]
  });
}

function play(flag, id, flags) {
  var u = String(id);
  if (RELAY_MEDIA) u = relayWrap(u);
  return JSON.stringify({ parse: 0, url: u, header: JSON.stringify(headers) });
}

function search(key, quick) {
  return JSON.stringify({ list: [] });
}

var SPIDER_API = {
  init: init,
  home: home,
  homeVod: homeVod,
  category: category,
  detail: detail,
  play: play,
  search: search
};

__JS_SPIDER__ = SPIDER_API;
var __jsEvalReturn = function () { return SPIDER_API; };
