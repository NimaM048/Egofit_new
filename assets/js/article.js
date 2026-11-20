/*
 * EgoFit article page: reading progress, table of contents with scroll-spy,
 * text-size switch, share buttons and progressive structuring of plain-text
 * articles ("- item" lines -> lists, "---" -> divider).
 * DOM-only (no innerHTML), so it cannot introduce markup from content.
 */
(function () {
    "use strict";

    var page = document.querySelector(".ef-article-page");
    var prose = document.querySelector("[data-prose]");
    if (!page || !prose) return;

    var faNum = function (n) {
        try { return Number(n).toLocaleString("fa-IR"); } catch (e) { return String(n); }
    };
    var store = {
        get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
        set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
    };

    /* ------------------------------------------------------------------ 1. structure plain text */
    var MARK = /^\s*(?:[-–—•▪●*]|\d{1,2}[.)\-]|[۰-۹]{1,2}[.)\-])\s+/;
    var NUMBERED = /^\s*(?:\d{1,2}|[۰-۹]{1,2})[.)\-]\s+/;
    var RULE = /^\s*(?:-{3,}|\*{3,}|_{3,}|—{2,})\s*$/;

    var BLOCK = /^(P|DIV|UL|OL|LI|H[1-6]|BLOCKQUOTE|FIGURE|TABLE|SECTION|HR|PRE|DETAILS|ASIDE)$/;

    function lineText(nodes) {
        return nodes.map(function (n) { return n.textContent; }).join("");
    }

    function stripMarker(nodes) {
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            if (n.nodeType === 3 && n.textContent.trim() === "") continue;
            var target = n.nodeType === 3 ? n : document.createTreeWalker(n, NodeFilter.SHOW_TEXT).nextNode();
            if (target) target.textContent = target.textContent.replace(MARK, "");
            return;
        }
    }

    /* Turn a run of inline nodes separated by <br> into paragraphs / lists / dividers.
       Returns a fragment, or null when the run has no structure worth changing. */
    function buildFrom(nodes) {
        var lines = [[]], brs = [];
        nodes.forEach(function (node) {
            if (node.nodeName === "BR") { brs.push(node); lines.push([]); } else { lines[lines.length - 1].push(node); }
        });
        if (!brs.length) {
            // a lone divider line such as "------"
            if (RULE.test(lineText(nodes))) {
                var single = document.createDocumentFragment();
                single.appendChild(document.createElement("hr"));
                nodes.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
                return single;
            }
            return null;
        }
        var kinds = lines.map(function (ln) {
            var t = lineText(ln);
            if (RULE.test(t)) return "rule";
            if (MARK.test(t)) return NUMBERED.test(t) ? "ol" : "ul";
            return t.trim() === "" ? "blank" : "text";
        });
        var listLines = kinds.filter(function (k) { return k === "ul" || k === "ol"; }).length;
        if (listLines < 2 && kinds.indexOf("rule") === -1 && kinds.indexOf("blank") === -1) return null;

        var frag = document.createDocumentFragment();
        var para = null, list = null, listKind = null;
        var flushPara = function () { if (para && para.textContent.trim()) frag.appendChild(para); para = null; };
        var flushList = function () { if (list) frag.appendChild(list); list = null; listKind = null; };

        lines.forEach(function (ln, i) {
            var kind = kinds[i];
            if (kind === "rule") { flushPara(); flushList(); frag.appendChild(document.createElement("hr")); return; }
            if (kind === "ul" || kind === "ol") {
                flushPara();
                if (!list || listKind !== kind) { flushList(); list = document.createElement(kind); listKind = kind; }
                var li = document.createElement("li");
                stripMarker(ln);
                ln.forEach(function (n) { li.appendChild(n); });
                list.appendChild(li);
                return;
            }
            flushList();
            if (kind === "blank") { flushPara(); ln.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); }); return; }
            if (!para) { para = document.createElement("p"); } else { para.appendChild(document.createElement("br")); }
            ln.forEach(function (n) { para.appendChild(n); });
        });
        flushPara();
        flushList();
        brs.forEach(function (br) { if (br.parentNode) br.parentNode.removeChild(br); });
        return frag;
    }

    // 1a. paragraphs that hold several "lines"
    Array.prototype.slice.call(prose.querySelectorAll("p")).forEach(function (p) {
        if (!p.querySelector("br") || p.closest("li, blockquote, figure, td")) return;
        var frag = buildFrom(Array.prototype.slice.call(p.childNodes));
        if (frag) p.parentNode.replaceChild(frag, p);
    });

    // 1b. inline runs that sit directly inside containers (sections, plain divs)
    Array.prototype.slice.call(prose.querySelectorAll(".ef-prose__section, .ef-takeaway__body, div:not([class])")).forEach(function (box) {
        var run = [];
        var flush = function (before) {
            if (run.length) {
                var nodes = run; run = [];
                var anchor = nodes[0];
                var marker = document.createComment("");
                anchor.parentNode.insertBefore(marker, anchor);
                var frag = buildFrom(nodes);
                if (frag) box.insertBefore(frag, marker);
                box.removeChild(marker);
            }
        };
        Array.prototype.slice.call(box.childNodes).forEach(function (node) {
            if (node.nodeType === 1 && BLOCK.test(node.nodeName)) { flush(); } else { run.push(node); }
        });
        flush();
    });

    // 1c. no two dividers in a row, no divider at the very start/end of a section
    Array.prototype.slice.call(prose.querySelectorAll("hr")).forEach(function (hr) {
        var prev = hr.previousElementSibling, next = hr.nextElementSibling;
        if ((prev && prev.tagName === "HR") || !prev || !next) hr.parentNode.removeChild(hr);
    });

    // 1d. mostly-Latin paragraphs (citations, English quotes) read left-to-right
    var LATIN = /[A-Za-z]/g, PERSIAN = /[؀-ۿ]/g;
    Array.prototype.slice.call(prose.querySelectorAll("p, li")).forEach(function (el) {
        var t = el.textContent, lat = (t.match(LATIN) || []).length, fa = (t.match(PERSIAN) || []).length;
        if (lat > 12 && lat > fa * 3) { el.setAttribute("dir", "ltr"); el.style.textAlign = "left"; }
    });

    /* ------------------------------------------------------------------ 2. table of contents */
    var tocBox = document.querySelector("[data-toc]");
    var tocList = document.querySelector("[data-toc-list]");
    // skip headings inside the takeaway body and "headings" that are really whole paragraphs
    var headings = Array.prototype.slice.call(prose.querySelectorAll("h2, h3")).filter(function (h) {
        var len = h.textContent.replace(/\s+/g, " ").trim().length;
        return len > 1 && len <= 110 && !h.closest(".ef-takeaway__body");
    });
    var links = [];

    if (tocBox && tocList) {
        if (headings.length < 2) {
            tocBox.hidden = true;
        } else {
            headings.forEach(function (h, i) {
                if (!h.id) h.id = "section-" + (i + 1);
                var li = document.createElement("li");
                if (h.tagName === "H3") li.className = "is-sub";
                var a = document.createElement("a");
                a.href = "#" + h.id;
                var text = h.textContent.replace(/\s+/g, " ").trim();
                a.textContent = text.length > 90 ? text.slice(0, 88) + "…" : text;
                a.addEventListener("click", function (ev) {
                    ev.preventDefault();
                    h.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
                    if (history.replaceState) history.replaceState(null, "", "#" + h.id);
                    if (window.innerWidth < 1100) tocBox.open = false;
                });
                li.appendChild(a);
                tocList.appendChild(li);
                links.push(a);
            });
            if (window.innerWidth < 1100) tocBox.open = false;
        }
    }

    /* ------------------------------------------------------------------ 3. progress + scroll-spy */
    var bar = document.querySelector(".ef-read-progress");
    var remainingEl = document.querySelector("[data-remaining]");
    var minutes = parseInt(page.getAttribute("data-reading-time"), 10) || 0;
    var ticking = false;

    function update() {
        ticking = false;
        var rect = prose.getBoundingClientRect();
        var vh = window.innerHeight;
        var total = rect.height - vh * 0.55;
        var p = total > 0 ? Math.min(1, Math.max(0, (vh * 0.3 - rect.top) / total)) : 0;
        if (bar) bar.style.setProperty("--ef-progress", p.toFixed(4));

        if (remainingEl && minutes > 0) {
            remainingEl.hidden = false;
            var left = Math.ceil(minutes * (1 - p));
            remainingEl.textContent = p >= 0.98 ? "پایان مطالعه" : (p <= 0.01 ? faNum(minutes) + " دقیقه" : faNum(Math.max(left, 1)) + " دقیقه مانده");
        }

        if (links.length) {
            var active = -1;
            for (var i = 0; i < headings.length; i++) {
                if (headings[i].getBoundingClientRect().top < 150) active = i; else break;
            }
            links.forEach(function (a, j) { a.classList.toggle("is-active", j === active); });
            if (active > -1 && window.innerWidth >= 1100) {
                var al = links[active], list = tocList;
                if (al.offsetTop < list.scrollTop || al.offsetTop > list.scrollTop + list.clientHeight - 40) {
                    list.scrollTop = al.offsetTop - 60;
                }
            }
        }
    }

    function onScroll() {
        if (ticking) return;
        ticking = true;
        // rAF is paused in background tabs; fall back to a timer there
        if (document.hidden || !window.requestAnimationFrame) { window.setTimeout(update, 16); }
        else { window.requestAnimationFrame(update); }
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    update();

    /* ------------------------------------------------------------------ 4. text size */
    var sizeButtons = Array.prototype.slice.call(document.querySelectorAll(".ef-size-switch [data-size]"));
    function setSize(size) {
        prose.setAttribute("data-size", size);
        sizeButtons.forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-size") === size ? "true" : "false"); });
    }
    sizeButtons.forEach(function (b) {
        b.addEventListener("click", function () {
            var s = b.getAttribute("data-size");
            setSize(s);
            store.set("ef-article-size", s);
            onScroll();
        });
    });
    var saved = store.get("ef-article-size");
    setSize(saved === "sm" || saved === "lg" ? saved : "md");

    /* ------------------------------------------------------------------ 5. share */
    var share = document.querySelector(".ef-share");
    if (share) {
        var url = window.location.href.split("#")[0];
        var title = share.getAttribute("data-share-title") || document.title;
        var tg = share.querySelector('[data-share="telegram"]');
        var wa = share.querySelector('[data-share="whatsapp"]');
        if (tg) tg.href = "https://t.me/share/url?url=" + encodeURIComponent(url) + "&text=" + encodeURIComponent(title);
        if (wa) wa.href = "https://wa.me/?text=" + encodeURIComponent(title + "\n" + url);

        var copyBtn = share.querySelector("[data-copy-link]");
        if (copyBtn) {
            copyBtn.addEventListener("click", function () {
                var done = function () {
                    copyBtn.classList.add("is-copied");
                    window.setTimeout(function () { copyBtn.classList.remove("is-copied"); }, 1600);
                };
                if (navigator.clipboard && window.isSecureContext) {
                    navigator.clipboard.writeText(url).then(done, function () {});
                } else {
                    var ta = document.createElement("textarea");
                    ta.value = url;
                    ta.setAttribute("readonly", "");
                    ta.style.position = "fixed";
                    ta.style.opacity = "0";
                    document.body.appendChild(ta);
                    ta.select();
                    try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
                    document.body.removeChild(ta);
                }
            });
        }
    }
})();
