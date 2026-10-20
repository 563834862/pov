/* POV 看板前端逻辑 */
(function () {
  "use strict";

  var VERDICT_CLASS = {
    "踩中热点": "v-green", "追热偏早": "v-amber", "追热偏晚": "v-amber",
    "选题滞后": "v-red", "独苗选题": "v-blue", "数据不足": "v-gray"
  };
  var BADGE_CLASS = {
    "踩中热点": "vb-green", "追热偏早": "vb-amber", "追热偏晚": "vb-amber",
    "选题滞后": "vb-red", "独苗选题": "vb-blue", "数据不足": "vb-gray"
  };
  var PHASE_CLASS = {
    "发酵期": "ph-ferment", "活跃期": "ph-active", "苗头期": "ph-sprout",
    "回落期": "ph-fall", "消退期": "ph-gone"
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtTime(iso) {
    if (!iso) return "-";
    var d = new Date(iso);
    if (isNaN(d)) return String(iso).slice(0, 16);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function fmtNum(n) {
    n = Number(n) || 0;
    return n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, "") + "w" : n.toLocaleString();
  }

  /* ---------- 渲染 ---------- */
  function renderChips(sys) {
    $("sysVer").textContent = "POV " + (sys.version || "");
    $("kwChips").innerHTML = (sys.keywords || []).map(function (k) {
      return '<span class="chip">#' + esc(k) + "</span>";
    }).join("");
  }

  function renderKpis(state) {
    var b = state.baseline || {};
    var posts = state.posts || [];
    var riding = 0, judged = 0;
    posts.forEach(function (p) {
      var v = (p.verification || {}).verdict;
      if (v && v !== "数据不足") {
        judged++;
        if (v === "踩中热点" || v === "追热稳健" || v === "追热偏早" || v === "追热偏晚") riding++;
      }
    });
    var rate = judged ? Math.round(riding / judged * 100) : null;
    var kpis = [
      { label: "舆情健康分", value: b.health_score == null ? "-" : b.health_score,
        sub: b.ok ? "基线已就绪" : "未采集", color: b.health_score >= 80 ? "var(--green)" : (b.health_score >= 60 ? "var(--amber)" : "var(--red)") },
      { label: "活跃舆情事件", value: b.event_count || 0, sub: b.item_count ? b.item_count + " 条采集样本" : "-" },
      { label: "监控博主", value: (state.system.bloggers || []).length, sub: (state.system.bloggers || []).slice(0, 2).join(" / ") },
      { label: "已验证内容", value: posts.length, sub: "含示例演示数据" },
      { label: "追热命中率", value: rate == null ? "-" : rate + "<small>%</small>",
        sub: riding + "/" + judged + " 条命中当前热点" }
    ];
    $("kpis").innerHTML = kpis.map(function (k) {
      return '<div class="kpi"><div class="label">' + k.label + '</div>' +
        '<div class="value" style="' + (k.color ? "color:" + k.color : "") + '">' + k.value + "</div>" +
        '<div class="sub">' + esc(k.sub) + "</div></div>";
    }).join("");
  }

  function renderPosts(posts) {
    if (!posts.length) {
      $("postList").innerHTML = '<div class="empty">暂无验证内容。点击右上角「② 运行博主验证」开始。</div>';
      return;
    }
    $("postList").innerHTML = posts.map(function (p) {
      var v = p.verification || {};
      var cls = VERDICT_CLASS[v.verdict] || "v-gray";
      var badge = BADGE_CLASS[v.verdict] || "vb-gray";
      var score = v.total == null ? "" : '<span class="score">' + v.total + " 分</span>";
      var topics = (v.post_topics || p.topics || []);
      var hits = (v.matched_event && v.matched_event.overlap_topics) || [];
      var topicHtml = topics.length
        ? '<div class="topic-chips">' + topics.map(function (t) {
            return '<span class="topic-chip' + (hits.indexOf(t) >= 0 ? " hit" : "") + '">' + esc(t) + "</span>";
          }).join("") + "</div>"
        : "";
      var matchHtml = v.matched_event
        ? '<div class="match-line">📊 命中事件「' + esc(v.matched_event.title) + '」· <span class="phase ' +
          (PHASE_CLASS[v.matched_event.phase] || "ph-gone") + '">' + esc(v.matched_event.phase) + "</span>" +
          " · 匹配度 " + (v.match_score == null ? "-" : v.match_score.toFixed(2)) + "</div>"
        : "";
      var barsHtml = "";
      if (v.scores) {
        var labels = { heat: "热度", timing: "时效", volume: "声量", diff: "差异" };
        var maxes = { heat: 45, timing: 25, volume: 10, diff: 20 };
        barsHtml = '<div class="score-bars">' + Object.keys(v.scores).map(function (k) {
          if (v.scores[k] == null) {
            return '<span class="sb">' + labels[k] + " 无数据</span>";
          }
          var pct = Math.round(v.scores[k] / maxes[k] * 100);
          return '<span class="sb">' + labels[k] + " " + v.scores[k] +
            '<span class="bar"><i style="width:' + pct + '%"></i></span></span>';
        }).join("") + "</div>";
      }
      return '<div class="post-card ' + cls + '">' +
        '<div class="post-top">' +
        '<span class="verdict-badge ' + badge + '">' + (v.emoji || "") + " " + esc(v.verdict || "-") + "</span>" +
        score +
        '<span class="post-meta">' + esc(p.blogger) + " · " + esc(p.platform) + " · " + fmtTime(p.published_at) + "</span>" +
        (p.pinned ? '<span class="pin-tag">置顶</span>' : "") +
        (p.simulated ? '<span class="sim-tag">示例数据</span>' : "") +
        "</div>" +
        '<div class="post-title">' + esc(p.title) + "</div>" +
        topicHtml + matchHtml +
        '<div class="advice-line"><b>建议：</b>' + esc(v.advice || v.reason || "") + "</div>" +
        barsHtml +
        "</div>";
    }).join("");
  }

  function renderFocus(focus) {
    if (!focus.length) {
      $("focusList").innerHTML = '<div class="empty">暂无基线数据</div>';
      return;
    }
    $("focusList").innerHTML = focus.map(function (f, i) {
      return '<div class="focus-item">' +
        '<div class="focus-rank">' + (i + 1) + "</div>" +
        "<div><div class=\"f-title\">" +
        (f.topic ? '<span class="topic-chip">' + esc(f.topic) + "</span> " : "") +
        esc(f.title) +
        '<span class="phase ' + (PHASE_CLASS[f.phase] || "ph-gone") + '">' + esc(f.phase) + "</span></div>" +
        '<div class="f-meta">' + f.count + " 条报道 · 均互动 " + fmtNum(f.avg_eng) +
        (f.platforms && f.platforms.length ? " · " + f.platforms.map(esc).join("/") : "") + "</div></div></div>";
    }).join("");
  }

  function renderSignals(rows) {
    if (!rows || !rows.length) {
      $("signalList").innerHTML = '<div class="empty">暂无前沿信号</div>';
      return;
    }
    $("signalList").innerHTML = rows.map(function (s, i) {
      var cls = (s.classes || []).join("/") || "-";
      return '<div class="focus-item">' +
        '<div class="focus-rank">' + (i + 1) + "</div>" +
        "<div><div class=\"f-title\">" +
        '<span class="topic-chip">' + esc(cls) + "</span> " +
        esc(s.title) + "</div>" +
        '<div class="f-meta">' +
        ((s.topics && s.topics.length) ? esc(s.topics.slice(0, 3).join("/")) + " · " : "") +
        "互动 " + fmtNum(s.max_eng) +
        (s.first_seen ? " · " + fmtTime(s.first_seen) : "") + "</div></div></div>";
    }).join("");
  }

  function renderHotTopics(rows) {
    if (!rows || !rows.length) {
      $("hotTopicList").innerHTML = '<div class="empty">暂无博主内容数据</div>';
      return;
    }
    $("hotTopicList").innerHTML = rows.map(function (h, i) {
      return '<div class="focus-item">' +
        '<div class="focus-rank">' + (i + 1) + "</div>" +
        "<div><div class=\"f-title\"><span class=\"topic-chip\">" + esc(h.topic) + "</span>" +
        '<span class="phase ph-hot">' + h.posts + " 帖 / " + h.bloggers + " 人</span></div>" +
        '<div class="f-meta">追热命中率 ' + Math.round(h.riding_ratio * 100) + "%" +
        (h.avg_score != null ? " · 均分 " + h.avg_score : "") +
        (h.sample ? " · 代表：" + esc(h.sample) : "") + "</div></div></div>";
    }).join("");
  }

  /* ---------- 舆情事件库（主视图：日期查询 + 分页 + 点击看跟进） ---------- */
  var evPage = { page: 1, size: 20, start: "", end: "", q: "" };

  function evDateStr(d) {
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  function evQuick(days) {
    var today = new Date();
    if (days === "all") {
      $("evStart").value = ""; $("evEnd").value = "";
    } else if (days === 0) {
      $("evStart").value = evDateStr(today); $("evEnd").value = evDateStr(today);
    } else {
      var from = new Date(today.getTime() - days * 86400000);
      $("evStart").value = evDateStr(from); $("evEnd").value = evDateStr(today);
    }
    evPage.page = 1;
    loadEvents();
  }

  function loadEvents() {
    evPage.start = $("evStart").value;
    evPage.end = $("evEnd").value;
    evPage.q = $("evQ").value.trim();
    evPage.size = parseInt($("evSize").value, 10) || 20;
    var url = "/api/events?page=" + evPage.page + "&size=" + evPage.size +
      "&start=" + encodeURIComponent(evPage.start) + "&end=" + encodeURIComponent(evPage.end) +
      "&q=" + encodeURIComponent(evPage.q);
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      renderEventList(d);
    }).catch(function () {
      $("eventList").innerHTML = '<div class="empty">加载失败</div>';
    });
  }

  function renderEventList(d) {
    var box = $("eventList");
    if (!d.events || !d.events.length) {
      box.innerHTML = '<div class="empty">该日期范围内没有舆情事件</div>';
      $("evPageInfo").textContent = "共 0 条";
      $("evPrev").disabled = true; $("evNext").disabled = true;
      return;
    }
    box.innerHTML = d.events.map(function (e) {
      var fs = (e.first_seen || "").replace("T", " ").slice(5, 16);
      var plats = (e.platforms || []).slice(0, 3).map(esc).join(" / ") +
        ((e.platforms || []).length > 3 ? " 等" : "");
      return '<div class="ev-row" data-id="' + esc(e.id) + '">' +
        '<div class="ev-main">' +
        '<div class="ev-title">' + esc(e.title) + "</div>" +
        '<div class="ev-meta">' +
        '<span class="ev-time">🕐 ' + esc(fs) + "</span>" +
        '<span class="phase ' + (PHASE_CLASS[e.phase] || "ph-gone") + '">' + esc(e.phase) + "</span>" +
        (e.topic ? '<span class="topic-chip">' + esc(e.topic) + "</span>" : "") +
        "<span>📄 " + e.count + " 条报道</span>" +
        (plats ? "<span>📡 " + plats + "</span>" : "") +
        '<span class="ev-follow">👥 ' + e.follower_count + " 位博主跟进</span>" +
        "</div></div>" +
        '<div class="ev-go">查看跟进 ›</div>' +
        "</div>";
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll(".ev-row"), function (row) {
      row.onclick = function () { openEventDetail(row.getAttribute("data-id")); };
    });
    $("evPageInfo").textContent = "第 " + d.page + " / " + d.pages + " 页 · 共 " + d.total + " 条";
    $("evPrev").disabled = d.page <= 1;
    $("evNext").disabled = d.page >= d.pages;
  }

  function openEventDetail(id) {
    fetch("/api/event?id=" + encodeURIComponent(id)).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { $("eventDetail").innerHTML = '<div class="empty">' + esc(d.error) + "</div>"; return; }
        renderEventDetail(d);
      });
  }

  function renderEventDetail(d) {
    var e = d.event, box = $("eventDetail");
    var fs = (e.first_seen || "").replace("T", " ").slice(0, 16);
    var ls = (e.last_seen || "").replace("T", " ").slice(0, 16);
    var reports = (d.reports || []).map(function (r) {
      var link = r.url ? ' <a class="ev-link" href="' + esc(r.url) + '" target="_blank" rel="noopener">原文 ↗</a>' : "";
      var t = (r.published_at || "").replace("T", " ").slice(5, 16);
      return '<li><span class="ev-rep-t">' + esc(r.title) + "</span>" +
        '<span class="ev-rep-m">' + esc(t) + " · " + esc(r.platform || "") + link + "</span></li>";
    }).join("");
    var followers = (d.followers || []).map(function (f) {
      var link = f.url ? ' <a class="ev-link" href="' + esc(f.url) + '" target="_blank" rel="noopener">作品 ↗</a>' : "";
      var pub = (f.published_at || "").replace("T", " ").slice(5, 16);
      return '<li><span class="ev-f-b">' + esc(f.blogger) + "</span>" +
        '<span class="ev-f-t">' + esc(f.title) + link + "</span>" +
        '<span class="ev-f-m">' + esc(pub) + " · " +
        '<span style="color:' + f.color + '">' + esc(f.emoji + " " + f.verdict) + "</span> · " +
        esc(f.lag_text) + (f.lag_basis === "news" ? "（跟进自：" + esc(f.ref_title).slice(0, 30) + "）" : "") +
        "</span></li>";
    }).join("");
    box.innerHTML =
      '<div class="ev-detail-head">' +
      '<button class="btn" id="evDetailClose">‹ 返回列表</button>' +
      "<div><b>" + esc(e.title) + "</b>" +
      '<span class="phase ' + (PHASE_CLASS[e.phase] || "ph-gone") + '">' + esc(e.phase) + "</span>" +
      '<span class="ev-meta">🕐 首发 ' + esc(fs) + (ls && ls !== fs ? " → 末次 " + esc(ls) : "") +
      " · 📄 " + e.count + " 条报道 · 👥 " + d.followers.length + " 位博主涉及（舆情发布后跟进 " +
      d.after_count + " 位）</span></div></div>" +
      '<div class="ev-detail-cols">' +
      '<div class="ev-detail-col"><h3>📰 舆情报道（' + (d.reports || []).length + "）</h3>" +
      '<ul class="ev-rep-list">' + (reports || '<li class="empty">无报道明细</li>') + "</ul></div>" +
      '<div class="ev-detail-col"><h3>👥 跟进博主及作品（' + (d.followers || []).length + "）</h3>" +
      '<ul class="ev-f-list">' + (followers || '<li class="empty">暂无博主跟进</li>') + "</ul></div>" +
      "</div>";
    box.classList.remove("hidden");
    $("evDetailClose").onclick = function () { box.classList.add("hidden"); };
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function bindEventLib() {
    $("evSearch").onclick = function () { evPage.page = 1; loadEvents(); };
    $("evPrev").onclick = function () { if (evPage.page > 1) { evPage.page--; loadEvents(); } };
    $("evNext").onclick = function () { evPage.page++; loadEvents(); };
    $("evSize").onchange = function () { evPage.page = 1; loadEvents(); };
    $("evQ").onkeydown = function (ev) { if (ev.key === "Enter") { evPage.page = 1; loadEvents(); } };
    Array.prototype.forEach.call(document.querySelectorAll(".ev-quick a"), function (a) {
      a.onclick = function () { evQuick(a.getAttribute("data-days")); };
    });
    evQuick(30);   // 默认近30天
  }

  function renderSources(baseline) {
    var st = baseline.source_status || {};
    var keys = Object.keys(st);
    if (!keys.length) {
      $("sourceStatus").innerHTML = '<div class="empty">尚未采集</div>';
      return;
    }
    $("sourceStatus").innerHTML = keys.map(function (k) {
      var ok = st[k] === "ok";
      return '<div class="src-row"><span>' + esc(k) + '</span><span class="' +
        (ok ? "st-ok" : "st-bad") + '">' + (ok ? "✓ 已覆盖" : "✗ " + esc(st[k])) + "</span></div>";
    }).join("") + '<div class="f-meta" style="font-size:11px;color:var(--text-3);margin-top:6px">采集时间：' +
      fmtTime(baseline.collected_at) + "</div>";
  }

  function renderScheduler(sc) {
    var el = $("schedulerStatus");
    if (!sc) { el.innerHTML = ""; return; }
    if (!sc.enabled) {
      el.innerHTML = '<div class="src-row"><span>自动调度</span><span class="st-bad">已关闭 · 手动触发</span></div>';
      return;
    }
    var rows = [
      '<div class="src-row"><span>状态</span><span class="' +
        (sc.running ? "st-bad" : "st-ok") + '">' +
        (sc.running ? "⚙ " + esc(sc.running) + " 执行中" : "✓ 运行中") + "</span></div>",
      '<div class="src-row"><span>采集周期</span><span>每 ' + esc(sc.collect_interval_hours) + " 小时</span></div>",
      '<div class="src-row"><span>验证周期</span><span>每 ' + esc(sc.verify_interval_hours) + " 小时</span></div>",
      '<div class="src-row"><span>下次采集</span><span>' + fmtTime(sc.next_collect_at) + "</span></div>",
      '<div class="src-row"><span>下次验证</span><span>' + fmtTime(sc.next_verify_at) + "</span></div>",
      '<div class="src-row"><span>上次采集</span><span>' + fmtTime(sc.last_collect_at) + "</span></div>",
      '<div class="src-row"><span>上次验证</span><span>' + fmtTime(sc.last_verify_at) + "</span></div>"
    ];
    if (sc.last_error) {
      rows.push('<div class="src-row"><span>最近异常</span><span class="st-bad">' + esc(sc.last_error) + "</span></div>");
    }
    el.innerHTML = rows.join("");
  }

  function renderProfiles(profiles) {
    var tb = document.querySelector("#profileTable tbody");
    if (!profiles.length) {
      tb.innerHTML = '<tr><td colspan="9" class="empty">暂无数据</td></tr>';
      return;
    }
    tb.innerHTML = profiles.map(function (p) {
        var c = p.counts || {};
        var stCls = p.style === "追热型" ? "st-hot" : (p.style === "独苗型" ? "st-uniq" : "st-stable");
        return "<tr><td>" + esc(p.blogger) + "</td><td>" + esc(p.platform) + "</td>" +
          '<td><span class="style-tag ' + stCls + '">' + esc(p.style) + "</span></td>" +
          "<td>" + (p.avg_score == null ? "-" : p.avg_score) + "</td>" +
          "<td>" + (c["踩中热点"] || 0) + "</td>" +
          "<td>" + (c["追热稳健"] || 0) + "</td>" +
          "<td>" + ((c["追热偏早"] || 0) + (c["追热偏晚"] || 0)) + "</td>" +
          "<td>" + (c["选题滞后"] || 0) + "</td>" +
          "<td>" + (c["独苗选题"] || 0) + "</td></tr>";
      }).join("");
  }

  /* ---------- 响应速度榜 ---------- */
  function renderSpeed(rows) {
    var tb = document.querySelector("#speedTable tbody");
    if (!rows || !rows.length) {
      tb.innerHTML = '<tr><td colspan="6" class="empty">暂无足够样本（需命中 ≥2 个舆情事件）</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(function (r) {
      return "<tr><td><b>" + esc(r.blogger) + "</b></td><td>" + r.events + "</td>" +
        "<td><b>" + esc(r.avg_lag_text) + "</b></td><td>" + esc(r.fastest_lag_text) + "</td>" +
        "<td>" + r.early_count + "</td><td>" + r.within24h + "</td></tr>";
    }).join("");
  }

  var evBound = false;

  function render(state) {
    renderChips(state.system || {});
    renderKpis(state);
    renderPosts(state.posts || []);
    if (!evBound) { bindEventLib(); evBound = true; }   // 只绑一次，避免 5s 刷新重置过滤条件
    renderFocus((state.baseline || {}).focus || []);
    renderSignals((state.baseline || {}).signals || []);
    renderHotTopics(state.hot_topics || []);
    renderSources(state.baseline || {});
    renderScheduler(state.scheduler);
    renderProfiles(state.profiles || []);
    renderSpeed(state.speed || []);
    var job = state.job || {};
    var el = $("jobStatus");
    el.textContent = job.running ? job.message : (job.finished_at ? job.message : "");
    el.className = "job" + (job.running ? " busy" : "");
    $("btnCollect").disabled = !!job.running;
    $("btnVerify").disabled = !!job.running;
  }

  /* ---------- 交互 ---------- */
  function post(url, cb) {
    fetch(url, { method: "POST" }).then(function (r) { return r.json(); })
      .then(function (d) { cb && cb(d); }).catch(function (e) { alert("请求失败: " + e); });
  }
  $("btnCollect").onclick = function () {
    post("/api/collect", function (d) { if (!d.ok) alert(d.message); });
    setTimeout(refresh, 300);
  };
  $("btnVerify").onclick = function () {
    post("/api/verify", function (d) { if (!d.ok) alert(d.message); });
    setTimeout(refresh, 300);
  };

  function refresh() {
    fetch("/api/state").then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  }
  refresh();
  setInterval(refresh, 5000);

  function tick() {
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    $("clock").textContent = p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }
  tick(); setInterval(tick, 1000);
})();
