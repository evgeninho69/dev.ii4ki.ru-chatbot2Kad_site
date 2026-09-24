/*!
 * 2KAD Chat Widget — клиент для встраивания на сайт 2kad.ru
 * Загружается как <script src=".../widget/widget.js" defer></script>
 *
 * Конфигурация через window.KAD_CHATBOT_CONFIG:
 *   - apiBase: URL бэкенда (должен заканчиваться на /api)
 *   - title: заголовок окна
 *   - subtitle: подзаголовок
 *   - primaryColor: hex цвета
 *   - placeholder: плейсхолдер инпута
 *   - sessionId: уникальный ID сессии (если null — генерируется)
 *
 * Стили изолированы через .kad-chatbot-*
 */
(function () {
  "use strict";

  if (window.__kadChatbotLoaded) return;
  window.__kadChatbotLoaded = true;

  var POS = "left";

  var CFG = Object.assign({
    apiBase: "/api",
    title: "Ассистент 2КАД",
    subtitle: "Отвечаем по кадастру и услугам",
    primaryColor: "#cc2c2c",
    placeholder: "Спросите про межевание, цены, документы…",
    sessionId: null,
    welcomeMessage:
      "Здравствуйте! Я виртуальный ассистент сайта 2kad.ru.\n\n" +
      "⚠️ Бот находится в стадии разработки — часть ответов может быть неточной. " +
      "По конкретным задачам звоните +7 (4822) 41-57-68 или пишите info@2kad.ru.\n\n" +
      "Чем могу помочь?",
  }, window.KAD_CHATBOT_CONFIG || {});

  // ---- SVG-иконки ----
  var ROBOT_SVG =
    '<svg viewBox="0 0 32 32" width="28" height="28" ' +
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="7" y="11" width="18" height="14" rx="3" fill="currentColor" ' +
    'fill-opacity="0.18"/>' +
    '<circle cx="13" cy="18" r="1.5" fill="currentColor"/>' +
    '<circle cx="19" cy="18" r="1.5" fill="currentColor"/>' +
    '<path d="M11 25 L9 28 M21 25 L23 28"/>' +
    '<path d="M16 11 L16 7 M13 7 L19 7" />' +
    '<circle cx="16" cy="5" r="1" fill="currentColor"/>' +
    '</svg>';

  // Анимация «размышления» — контур участка + поэтажный план
  var THINKING_SVG =
    '<div class="kad-thinking" aria-label="Ассистент думает">' +
    '<svg viewBox="0 0 120 48" width="120" height="48" ' +
    'preserveAspectRatio="xMidYMid meet">' +
    '<g class="plot">' +
    '<polygon class="plot-poly" points="10,8 50,5 56,38 14,42" />' +
    '<polygon class="plot-fill" points="10,8 50,5 56,38 14,42" />' +
    '</g>' +
    '<g class="compass">' +
    '<line x1="32" y1="6" x2="32" y2="14" />' +
    '<polygon points="29,8 32,4 35,8" />' +
    '<text x="32" y="3" font-size="6" text-anchor="middle">N</text>' +
    '</g>' +
    '<g class="floor" transform="translate(64,4)">' +
    '<rect class="floor-outer" x="0" y="0" width="48" height="40" rx="2" />' +
    '<line class="floor-wall" x1="22" y1="0" x2="22" y2="40" />' +
    '<rect class="floor-door" x="18" y="14" width="8" height="6" fill="#fff"/>' +
    '<path class="floor-door-swing" d="M 22 14 A 8 8 0 0 1 22 26" ' +
    'fill="none" stroke-dasharray="2 2"/>' +
    '<line x1="6" y1="0" x2="14" y2="0" />' +
    '<line x1="30" y1="40" x2="40" y2="40" />' +
    '<text x="24" y="22" font-size="5" text-anchor="middle">12.4</text>' +
    '<text x="35" y="22" font-size="5" text-anchor="middle">8.7</text>' +
    '</g>' +
    '</svg>' +
    '<span class="kad-thinking-text">Анализирую запрос…</span>' +
    '</div>';

  // ---- Иконка по типу найденной страницы (для mind-map карточки) ----
  function iconFor(mode, title) {
    var t = (title || "").toLowerCase();
    if (mode === "pricing")
      return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
             'fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round" stroke-linejoin="round">' +
             '<rect x="3" y="4" width="18" height="16" rx="2"/>' +
             '<line x1="7" y1="9" x2="17" y2="9"/>' +
             '<line x1="7" y1="13" x2="13" y2="13"/></svg>';
    if (t.indexOf("пзз") >= 0 || t.indexOf("зонирован") >= 0 || t.indexOf("генплан") >= 0)
      return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
             'fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round"><polygon points="4,6 12,3 20,6 21,18 12,21 3,18"/>' +
             '<line x1="12" y1="3" x2="12" y2="21"/><line x1="3" y1="6" x2="21" y2="6"/>' +
             '<line x1="3" y1="18" x2="21" y2="18"/></svg>';
    if (t.indexOf("план") >= 0 || t.indexOf("технич") >= 0 || t.indexOf("этаж") >= 0)
      return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
             'fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="1"/>' +
             '<line x1="3" y1="12" x2="21" y2="12"/><line x1="12" y1="12" x2="12" y2="21"/>' +
             '<path d="M5 12 Q 12 6 18 12" fill="none"/></svg>';
    if (t.indexOf("межеван") >= 0 || t.indexOf("границ") >= 0)
      return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
             'fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round" stroke-linejoin="round">' +
             '<polygon points="4,7 14,4 20,9 19,18 9,20 3,16"/>' +
             '<line x1="14" y1="4" x2="14" y2="14"/><line x1="14" y1="14" x2="20" y2="14"/></svg>';
    if (t.indexOf("контакт") >= 0 || t.indexOf("адрес") >= 0 || t.indexOf("телефон") >= 0)
      return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
             'fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
    if (t.indexOf("гпзу") >= 0 || t.indexOf("разрешен") >= 0 || t.indexOf("строительств") >= 0)
      return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
             'fill="none" stroke="currentColor" stroke-width="2" ' +
             'stroke-linecap="round" stroke-linejoin="round">' +
             '<path d="M3 21h18M5 21V8l7-5 7 5v13"/><path d="M9 21V12h6v9"/></svg>';
    // default
    return '<svg viewBox="0 0 24 24" width="18" height="18" ' +
           'fill="none" stroke="currentColor" stroke-width="2" ' +
           'stroke-linecap="round" stroke-linejoin="round">' +
           '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>' +
           '<polyline points="14 2 14 8 20 8"/></svg>';
  }

  var SESSION_ID = CFG.sessionId || (function () {
    var k = "kad_chat_session";
    var v = localStorage.getItem(k);
    if (!v) {
      v = (Date.now().toString(36) + Math.random().toString(36).slice(2, 8));
      localStorage.setItem(k, v);
    }
    return v;
  })();

  // ---- inject styles ----
  var css = [
    // ---- Кнопка (левый нижний угол) ----
    ".kad-chatbot-btn{position:fixed;bottom:24px;left:24px;z-index:999999;",
    "width:72px;height:72px;border-radius:50%;border:none;cursor:pointer;",
    "background:linear-gradient(135deg," + CFG.primaryColor + " 0%, #a51d1d 100%);",
    "color:#fff;",
    "box-shadow:0 8px 24px rgba(204,44,44,.35);transition:transform .2s ease;",
    "display:flex;align-items:center;justify-content:center;flex-direction:column;",
    "font-family:Manrope,system-ui,sans-serif}",
    ".kad-chatbot-btn:hover{transform:scale(1.06)}",
    ".kad-chatbot-btn .ai-label{margin-top:-2px;font-size:9px;font-weight:800;",
    "letter-spacing:.5px;line-height:1;color:#fff;background:rgba(0,0,0,.18);",
    "padding:1px 5px;border-radius:6px}",
    ".kad-chatbot-btn .badge{position:absolute;top:-4px;left:-4px;",
    "background:#fff;color:" + CFG.primaryColor + ";font-size:10px;font-weight:700;",
    "border-radius:10px;padding:2px 6px;display:none}",
    ".kad-chatbot-btn .pulse{position:absolute;inset:0;border-radius:50%;",
    "border:2px solid " + CFG.primaryColor + ";opacity:.5;animation:kadPulse 2s infinite}",
    "@keyframes kadPulse{0%{transform:scale(.9);opacity:.6}70%{transform:scale(1.25);opacity:0}100%{opacity:0}}",

    // ---- Панель ----
    ".kad-chatbot-panel{position:fixed;bottom:116px;left:24px;z-index:999999;",
    "width:400px;max-width:calc(100vw - 32px);height:580px;max-height:82vh;",
    "background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.22);",
    "display:none;flex-direction:column;overflow:hidden;font-family:Manrope,system-ui,sans-serif}",
    ".kad-chatbot-panel.open{display:flex;animation:kadSlide .25s ease}",
    "@keyframes kadSlide{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}",

    // ---- Шапка ----
    ".kad-chatbot-header{background:linear-gradient(135deg," + CFG.primaryColor + " 0%, #a51d1d 100%);color:#fff;",
    "padding:14px 18px;display:flex;justify-content:space-between;align-items:center}",
    ".kad-chatbot-header h3{margin:0;font-size:15px;font-weight:700;display:flex;align-items:center;gap:8px}",
    ".kad-chatbot-header h3 svg{flex-shrink:0}",
    ".kad-chatbot-header p{margin:3px 0 0;font-size:11px;opacity:.85}",
    ".kad-chatbot-close{background:transparent;border:none;color:#fff;",
    "font-size:22px;cursor:pointer;line-height:1}",
    ".kad-chatbot-badge-dev{font-size:9px;font-weight:600;background:rgba(255,255,255,.22);",
    "padding:2px 6px;border-radius:4px;margin-left:4px;letter-spacing:.3px}",
    ".kad-chatbot-session{font-size:10px;opacity:.7;margin-top:2px;display:flex;",
    "align-items:center;gap:4px}",
    ".kad-chatbot-session .dot{width:6px;height:6px;border-radius:50%;background:#10b981;",
    "animation:kadBlink 2s infinite}",
    "@keyframes kadBlink{0%,100%{opacity:1}50%{opacity:.4}}",

    // ---- Сообщения ----
    ".kad-chatbot-body{flex:1;overflow-y:auto;padding:14px;background:#fafafa;",
    "scroll-behavior:smooth}",
    ".kad-chatbot-msg{margin-bottom:10px;display:flex}",
    ".kad-chatbot-msg.user{justify-content:flex-end}",
    ".kad-chatbot-msg .bubble{max-width:85%;padding:10px 13px;border-radius:14px;",
    "font-size:13px;line-height:1.45;word-wrap:break-word;white-space:pre-wrap}",
    ".kad-chatbot-msg.user .bubble{background:" + CFG.primaryColor + ";color:#fff;",
    "border-bottom-right-radius:4px}",
    ".kad-chatbot-msg.assistant .bubble{background:#fff;color:#18181b;",
    "border:1px solid #e5e7eb;border-bottom-left-radius:4px}",
    ".kad-chatbot-msg .bubble a{color:" + CFG.primaryColor + ";text-decoration:underline}",
    ".kad-chatbot-msg .bubble strong{font-weight:700}",
    ".kad-chatbot-msg .bubble em{font-style:italic;color:#6b7280;font-size:11px;",
    "display:block;margin-top:4px}",

    // ---- Mind-map карточка источника ----
    ".kad-source-card{margin-top:8px;display:flex;align-items:stretch;",
    "background:linear-gradient(135deg,#fef7f7 0%, #fff 100%);",
    "border:1px solid #e5e7eb;border-left:3px solid " + CFG.primaryColor + ";",
    "border-radius:8px;overflow:hidden;transition:transform .15s ease,box-shadow .15s ease}",
    ".kad-source-card:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.06)}",
    ".kad-source-card .icon{flex-shrink:0;width:42px;display:flex;align-items:center;",
    "justify-content:center;color:" + CFG.primaryColor + ";background:#fff7f7;",
    "border-right:1px solid #f5e1e1}",
    ".kad-source-card .body{flex:1;padding:9px 12px;min-width:0}",
    ".kad-source-card .label{font-size:9px;font-weight:700;text-transform:uppercase;",
    "letter-spacing:.6px;color:" + CFG.primaryColor + ";margin-bottom:3px}",
    ".kad-source-card .title{display:block;font-size:13px;font-weight:600;color:#18181b;",
    "text-decoration:none;line-height:1.3;word-break:break-word}",
    ".kad-source-card .title:hover{color:" + CFG.primaryColor + ";text-decoration:underline}",
    ".kad-source-card .url{display:block;font-size:10.5px;color:#6b7280;font-family:ui-monospace,monospace;",
    "margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".kad-source-card .arrow{flex-shrink:0;align-self:center;padding:0 10px 0 0;color:#cbd5e1}",
    ".kad-source-card .arrow svg{width:14px;height:14px}",

    // ---- Thinking-анимация ----
    ".kad-thinking{padding:14px 8px;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:140px}",
    ".kad-thinking svg{overflow:visible}",
    ".kad-thinking-text{font-size:11px;color:#6b7280;letter-spacing:.2px}",
    ".plot-poly{fill:none;stroke:" + CFG.primaryColor + ";stroke-width:1.5;",
    "stroke-dasharray:200;stroke-dashoffset:200;animation:kadPlot 2.4s ease-out infinite}",
    ".plot-fill{fill:" + CFG.primaryColor + ";fill-opacity:.06;animation:kadPlotFill 2.4s ease-out infinite}",
    "@keyframes kadPlot{0%{stroke-dashoffset:200}40%,100%{stroke-dashoffset:0}}",
    "@keyframes kadPlotFill{0%,40%{fill-opacity:0}60%,100%{fill-opacity:.06}}",
    ".compass{transform-origin:32px 10px;animation:kadCompass 3s ease-in-out infinite}",
    "@keyframes kadCompass{0%,100%{transform:rotate(-5deg)}50%{transform:rotate(5deg)}}",
    ".floor-outer{fill:none;stroke:#18181b;stroke-width:1.5;",
    "stroke-dasharray:200;stroke-dashoffset:200;animation:kadWall 2.4s ease-out infinite .6s}",
    ".floor-wall{stroke:#18181b;stroke-width:1.5;",
    "stroke-dasharray:40;stroke-dashoffset:40;animation:kadWall 2.4s ease-out infinite 1.2s}",
    ".floor-door-swing{stroke:#a51d1d;stroke-width:1;",
    "stroke-dasharray:25;stroke-dashoffset:25;animation:kadWall 2.4s ease-out infinite 1.8s}",
    "@keyframes kadWall{0%{stroke-dashoffset:200}30%,100%{stroke-dashoffset:0}}",
    ".plot-poly{filter:drop-shadow(0 0 2px " + CFG.primaryColor + "33)}",

    // ---- Быстрые кнопки ----
    ".kad-chatbot-quick{padding:6px 14px 10px}",
    ".kad-chatbot-quick button{display:block;width:100%;text-align:left;",
    "background:#fff;border:1px solid #e5e7eb;border-radius:10px;",
    "padding:8px 12px;margin-bottom:6px;font-size:12px;cursor:pointer;",
    "color:#18181b;font-family:inherit}",
    ".kad-chatbot-quick button:hover{border-color:" + CFG.primaryColor + ";",
    "color:" + CFG.primaryColor + "}",

    // ---- Инпут ----
    ".kad-chatbot-footer{padding:10px;border-top:1px solid #e5e7eb;background:#fff;",
    "display:flex;gap:8px}",
    ".kad-chatbot-footer input{flex:1;border:1px solid #e5e7eb;border-radius:20px;",
    "padding:9px 14px;font-size:13px;outline:none;font-family:inherit}",
    ".kad-chatbot-footer input:focus{border-color:" + CFG.primaryColor + "}",
    ".kad-chatbot-footer button{background:" + CFG.primaryColor + ";color:#fff;",
    "border:none;border-radius:50%;width:36px;height:36px;cursor:pointer;",
    "display:flex;align-items:center;justify-content:center;font-size:16px}",
    ".kad-chatbot-footer button:disabled{opacity:.4;cursor:not-allowed}",

    "@media(max-width:480px){.kad-chatbot-panel{left:8px;right:8px;width:auto;",
    "max-width:none}.kad-chatbot-btn{left:14px;bottom:14px}}",
  ].join("");
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // ---- DOM ----
  var btn = document.createElement("button");
  btn.className = "kad-chatbot-btn";
  btn.title = "Открыть чат с AI-ассистентом 2КАД";
  btn.setAttribute("aria-label", "Открыть чат с AI-ассистентом 2КАД");
  btn.innerHTML =
    "<span class='pulse'></span>" +
    ROBOT_SVG +
    "<span class='ai-label'>AI</span>" +
    "<span class='badge'>1</span>";
  document.body.appendChild(btn);

  var panel = document.createElement("div");
  panel.className = "kad-chatbot-panel";
  panel.innerHTML =
    "<div class='kad-chatbot-header'>" +
      "<div>" +
        "<h3>" + ROBOT_SVG + "<span></span>" +
        "<span class='kad-chatbot-badge-dev'>в разработке</span></h3>" +
        "<p></p>" +
        "<div class='kad-chatbot-session'>" +
          "<span class='dot'></span>" +
          "<span>контекст сессии активен</span>" +
        "</div>" +
      "</div>" +
      "<button class='kad-chatbot-close' aria-label='Закрыть'>×</button>" +
    "</div>" +
    "<div class='kad-chatbot-body'></div>" +
    "<div class='kad-chatbot-quick'></div>" +
    "<div class='kad-chatbot-footer'>" +
      "<input type='text' placeholder='' autocomplete='off' />" +
      "<button aria-label='Отправить'>→</button>" +
    "</div>";
  document.body.appendChild(panel);

  panel.querySelector("h3 span").textContent = CFG.title;
  panel.querySelector("p").textContent = CFG.subtitle;
  panel.querySelector("input").placeholder = CFG.placeholder;

  var body = panel.querySelector(".kad-chatbot-body");
  var input = panel.querySelector("input");
  var sendBtn = panel.querySelector(".kad-chatbot-footer button");
  var quick = panel.querySelector(".kad-chatbot-quick");
  var badge = btn.querySelector(".badge");

  // ---- quick replies ----
  var QUICK = [
    "Сколько стоит межевание участка?",
    "Какие документы нужны для межевания?",
    "Что такое технический план?",
    "Как получить ГПЗУ?",
    "Контакты 2КАД",
  ];
  QUICK.forEach(function (q) {
    var b = document.createElement("button");
    b.textContent = q;
    b.addEventListener("click", function () {
      quick.innerHTML = "";
      input.value = q;
      send();
    });
    quick.appendChild(b);
  });

  // ---- helpers ----
  function escape(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c];
    });
  }

  function md(s) {
    var html = escape(s);
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      "<a href=\"$2\" target=\"_blank\" rel=\"noopener\">$1</a>");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function addMsg(role, text) {
    var wrap = document.createElement("div");
    wrap.className = "kad-chatbot-msg " + role;
    var html = "<div class='bubble'>" + md(text) + "</div>";
    wrap.innerHTML = html;
    body.appendChild(wrap);
    body.scrollTop = body.scrollHeight;
    return wrap;
  }

  // Mind-map источник: тип-специфичная иконка, заголовок, URL
  function renderSourceCard(meta) {
    var label = meta.mode === "pricing" ? "Калькулятор стоимости" : "Источник";
    var ic = iconFor(meta.mode, meta.page_title);
    var host = "";
    try { host = new URL(meta.page_url).hostname.replace(/^www\./, ""); }
    catch (e) {}
    var card = document.createElement("a");
    card.className = "kad-source-card";
    card.href = meta.page_url;
    card.target = "_blank";
    card.rel = "noopener";
    card.innerHTML =
      "<div class='icon'>" + ic + "</div>" +
      "<div class='body'>" +
        "<div class='label'>" + escape(label) + "</div>" +
        "<span class='title'>" + escape(meta.page_title) + "</span>" +
        "<span class='url'>" + escape(host + new URL(meta.page_url).pathname) + "</span>" +
      "</div>" +
      "<div class='arrow'>" +
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' " +
        "stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>" +
        "<path d='M5 12h14M12 5l7 7-7 7'/></svg>" +
      "</div>";
    return card;
  }

  function showTyping() {
    var wrap = document.createElement("div");
    wrap.className = "kad-chatbot-msg assistant kad-typing";
    wrap.innerHTML = "<div class='bubble'>" + THINKING_SVG + "</div>";
    body.appendChild(wrap);
    body.scrollTop = body.scrollHeight;
    var removed = false;
    return {
      remove: function () {
        if (removed) return;
        removed = true;
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
      }
    };
  }

  // Гарантирует, что SVG-анимация «размышления» видна минимум 600 мс.
  // Если LLM ответил мгновенно — ждём оставшееся время, иначе убираем сразу.
  function finishTyping() {
    if (!typing) return;
    var elapsed = performance.now() - typingShownAt;
    var wait = Math.max(0, 600 - elapsed);
    if (wait === 0) {
      typing.remove();
    } else {
      setTimeout(function () { typing.remove(); }, wait);
    }
  }

  function open() {
    panel.classList.add("open");
    badge.style.display = "none";
    if (!body.dataset.welcomed) {
      addMsg("assistant", CFG.welcomeMessage);
      body.dataset.welcomed = "1";
    }
    setTimeout(function () { input.focus(); }, 100);
  }
  function close() { panel.classList.remove("open"); }

  btn.addEventListener("click", function () {
    if (panel.classList.contains("open")) close(); else open();
  });
  panel.querySelector(".kad-chatbot-close").addEventListener("click", close);

  // Состояние текущего ответа ассистента (модульный scope — доступно из finishTyping)
  var currentAssistant = null;  // { wrap, bubble, meta }
  var typingShownAt = 0;         // момент показа «размышления» (ms)
  var typing = null;             // { remove() } — ссылка на thinking-bubble (модульный)

  var sending = false;  // single-flight guard (модульный)

  async function send() {
    if (sending) return;  // защита от двойных кликов / повторов Enter
    var text = (input.value || "").trim();
    if (!text) return;
    sending = true;
    input.value = "";
    sendBtn.disabled = true;
    quick.innerHTML = "";
    addMsg("user", text);
    typing = showTyping();
    currentAssistant = null;
    // Фиксируем момент показа — finishTyping() прочитает эту переменную
    typingShownAt = performance.now();

    try {
      var resp = await fetch(CFG.apiBase + "/chat", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          session_id: SESSION_ID,
          message: text,
        }),
      });
      if (!resp.ok || !resp.body) {
        typing.remove();
        addMsg("assistant",
          "⚠️ Не удалось получить ответ от сервера. Попробуйте позже или позвоните " +
          "+7 (4822) 41-57-68.");
        sendBtn.disabled = false;
        return;
      }
      var reader = resp.body.getReader();
      var dec = new TextDecoder("utf-8");
      var buf = "";
      var fullText = "";
      while (true) {
        var r = await reader.read();
        if (r.done) break;
        buf += dec.decode(r.value, {stream: true});
        var lines = buf.split("\n");
        buf = lines.pop() || "";
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line) continue;
          var ev;
          try { ev = JSON.parse(line); } catch (e) { continue; }
          if (ev.type === "meta") {
            if (!currentAssistant && ev.page_url && ev.page_title) {
              finishTyping();
              currentAssistant = addMsg("assistant", "");
              // сохраняем метаданные для карточки
              currentAssistant.__meta = {
                mode: ev.mode,
                page_url: ev.page_url,
                page_title: ev.page_title,
              };
            }
          } else if (ev.type === "content") {
            fullText += ev.delta;
            if (!currentAssistant) {
              finishTyping();
              currentAssistant = addMsg("assistant", "");
            }
            var bubble = currentAssistant.querySelector(".bubble");
            // Если есть сохранённая мета — рисуем текст + mind-map карточку
            if (currentAssistant.__meta) {
              if (!bubble.querySelector(".answer")) {
                bubble.innerHTML =
                  "<div class='answer'>" + md(fullText) + "</div>";
              } else {
                bubble.querySelector(".answer").innerHTML = md(fullText);
              }
              if (!bubble.querySelector(".kad-source-card")) {
                bubble.appendChild(renderSourceCard(currentAssistant.__meta));
              }
            } else {
              bubble.innerHTML = md(fullText);
            }
            body.scrollTop = body.scrollHeight;
          } else if (ev.type === "error") {
            finishTyping();
            addMsg("assistant", "⚠️ Ошибка: " + ev.message);
          }
        }
      }
    } catch (e) {
      finishTyping();
      addMsg("assistant", "⚠️ Не удалось связаться с сервером.");
    } finally {
      sendBtn.disabled = false;
      sending = false;
    }
  }

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  // ---- API ----
  window.KadChatbot = {
    open: open,
    close: close,
    send: send,
    sessionId: function () { return SESSION_ID; },
  };
})();
