/*!
 * 2KAD Chat Widget — лёгкий клиент для встраивания на сайт 2kad.ru
 * Загружается как <script src=".../widget/widget.js" defer></script>
 *
 * Конфигурация через window.KAD_CHATBOT_CONFIG:
 *   - apiBase: URL бэкенда (должен заканчиваться на /api)
 *   - title: заголовок окна
 *   - subtitle: подзаголовок
 *   - primaryColor: hex цвета
 *   - placeholder: плейсхолдер инпута
 *   - sessionId: уникальный ID сессии (если null — генерируется)
 *   - position: "left" | "right" (по умолчанию "left")
 *   - welcomeMessage: приветствие (по умолчанию содержит disclaimer "в стадии разработки")
 *
 * Стили изолированы через .kad-chatbot-*
 */
(function () {
  "use strict";

  if (window.__kadChatbotLoaded) return;
  window.__kadChatbotLoaded = true;

  var POS = "left";  // позиция кнопки и панели (фиксировано по требованию заказчика)

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
    // Робот 28x28 в кругу
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

  // Анимация «размышления» — контуры участка (прямоугольник со сторонами света)
  // + поэтажный план (две линии, имитирующие комнаты)
  var THINKING_SVG =
    '<div class="kad-thinking" aria-label="Ассистент думает">' +
    '<svg viewBox="0 0 120 48" width="120" height="48" ' +
    'preserveAspectRatio="xMidYMid meet">' +
    // Участок — динамический полигон
    '<g class="plot">' +
    '<polygon class="plot-poly" points="10,8 50,5 56,38 14,42" />' +
    '<polygon class="plot-fill" points="10,8 50,5 56,38 14,42" />' +
    '</g>' +
    // North-стрелка
    '<g class="compass">' +
    '<line x1="32" y1="6" x2="32" y2="14" />' +
    '<polygon points="29,8 32,4 35,8" />' +
    '<text x="32" y="3" font-size="6" text-anchor="middle">N</text>' +
    '</g>' +
    // Поэтажный план — две комнаты с дверным проёмом
    '<g class="floor" transform="translate(64,4)">' +
    '<rect class="floor-outer" x="0" y="0" width="48" height="40" rx="2" />' +
    '<line class="floor-wall" x1="22" y1="0" x2="22" y2="40" />' +
    // дверной проём (разрыв в стене)
    '<rect class="floor-door" x="18" y="14" width="8" height="6" fill="#fff"/>' +
    '<path class="floor-door-swing" d="M 22 14 A 8 8 0 0 1 22 26" ' +
    'fill="none" stroke-dasharray="2 2"/>' +
    // окна
    '<line x1="6" y1="0" x2="14" y2="0" />' +
    '<line x1="30" y1="40" x2="40" y2="40" />' +
    '<text x="24" y="22" font-size="5" text-anchor="middle">12.4</text>' +
    '<text x="35" y="22" font-size="5" text-anchor="middle">8.7</text>' +
    '</g>' +
    '</svg>' +
    '<span class="kad-thinking-text">Анализирую запрос…</span>' +
    '</div>';

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
    // ---- Кнопка-робот (левый нижний угол) ----
    ".kad-chatbot-btn{position:fixed;bottom:24px;left:24px;z-index:999999;",
    "width:72px;height:72px;border-radius:50%;border:none;cursor:pointer;",
    "background:" + CFG.primaryColor + ";color:#fff;",
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

    // ---- Панель чата ----
    ".kad-chatbot-panel{position:fixed;bottom:116px;left:24px;z-index:999999;",
    "width:400px;max-width:calc(100vw - 32px);height:560px;max-height:80vh;",
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

    // ---- Thinking-анимация (контур участка + план этажа) ----
    ".kad-thinking{padding:14px 8px;display:flex;flex-direction:column;align-items:center;gap:6px;min-width:140px}",
    ".kad-thinking svg{overflow:visible}",
    ".kad-thinking-text{font-size:11px;color:#6b7280;letter-spacing:.2px}",
    // Участок: рисуем контур
    ".plot-poly{fill:none;stroke:" + CFG.primaryColor + ";stroke-width:1.5;",
    "stroke-dasharray:200;stroke-dashoffset:200;animation:kadPlot 2.4s ease-out infinite}",
    ".plot-fill{fill:" + CFG.primaryColor + ";fill-opacity:.06;animation:kadPlotFill 2.4s ease-out infinite}",
    "@keyframes kadPlot{0%{stroke-dashoffset:200}40%,100%{stroke-dashoffset:0}}",
    "@keyframes kadPlotFill{0%,40%{fill-opacity:0}60%,100%{fill-opacity:.06}}",
    // North-стрелка медленно покачивается
    ".compass{transform-origin:32px 10px;animation:kadCompass 3s ease-in-out infinite}",
    "@keyframes kadCompass{0%,100%{transform:rotate(-5deg)}50%{transform:rotate(5deg)}}",
    // Поэтажный план: стены рисуются по очереди
    ".floor-outer{fill:none;stroke:#18181b;stroke-width:1.5;",
    "stroke-dasharray:200;stroke-dashoffset:200;animation:kadWall 2.4s ease-out infinite .6s}",
    ".floor-wall{stroke:#18181b;stroke-width:1.5;",
    "stroke-dasharray:40;stroke-dashoffset:40;animation:kadWall 2.4s ease-out infinite 1.2s}",
    ".floor-door-swing{stroke:#a51d1d;stroke-width:1;",
    "stroke-dasharray:25;stroke-dashoffset:25;animation:kadWall 2.4s ease-out infinite 1.8s}",
    "@keyframes kadWall{0%{stroke-dashoffset:200}30%,100%{stroke-dashoffset:0}}",
    // Бегущая «блестящая» искра по периметру
    ".plot-poly{filter:drop-shadow(0 0 2px " + CFG.primaryColor + "33)}",

    // ---- Быстрые кнопки ----
    ".kad-chatbot-quick{padding:6px 14px 0}",
    ".kad-chatbot-quick button{display:block;width:100%;text-align:left;",
    "background:#fff;border:1px solid #e5e7eb;border-radius:10px;",
    "padding:8px 12px;margin-bottom:6px;font-size:12px;cursor:pointer;",
    "color:#18181b;font-family:inherit}",
    ".kad-chatbot-quick button:hover{border-color:" + CFG.primaryColor + ";",
    "color:" + CFG.primaryColor + "}",
    ".kad-chatbot-disclaimer{font-size:10.5px;color:#92400e;background:#fef3c7;",
    "border-left:3px solid #f59e0b;padding:6px 10px;margin:0 14px 8px;border-radius:4px;",

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
    "<div><h3>" + ROBOT_SVG + "<span></span>" +
    "<span class='kad-chatbot-badge-dev'>в разработке</span></h3>" +
    "<p></p></div>" +
    "<button class='kad-chatbot-close' aria-label='Закрыть'>×</button>" +
    "</div>" +
    "<div class='kad-chatbot-body'></div>" +
    "<div class='kad-chatbot-disclaimer'>⚠️ Виртуальный ассистент 2КАД находится в стадии разработки. " +
    "Ответы могут содержать неточности. По конкретным задачам — " +
    "<a href='tel:+74822415768'>+7 (4822) 41-57-68</a>.</div>" +
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

  // Текстовое сообщение (проходит через md() и escape)
  function addMsg(role, text, meta) {
    var wrap = document.createElement("div");
    wrap.className = "kad-chatbot-msg " + role;
    var html = "<div class='bubble'>" + md(text) + "</div>";
    if (meta) {
      html += "<em>" + escape(meta) + "</em>";
    }
    wrap.innerHTML = html;
    body.appendChild(wrap);
    body.scrollTop = body.scrollHeight;
    return wrap;
  }

  // «Размышляющий» индикатор: контур участка + план этажа (HTML, без escape)
  function showTyping() {
    var wrap = document.createElement("div");
    wrap.className = "kad-chatbot-msg assistant kad-typing";
    wrap.innerHTML = "<div class='bubble'>" + THINKING_SVG + "</div>";
    body.appendChild(wrap);
    body.scrollTop = body.scrollHeight;
    return {
      remove: function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); }
    };
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

  async function send() {
    var text = (input.value || "").trim();
    if (!text) return;
    input.value = "";
    sendBtn.disabled = true;
    quick.innerHTML = "";
    addMsg("user", text);
    var typing = showTyping();
    var assistantMsg = null;
    var pageMeta = "";

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
            pageMeta = ev.page_title
              ? "→ " + ev.page_title + " · " + ev.page_url
              : (ev.mode === "pricing" ? "Калькулятор стоимости" : "");
          } else if (ev.type === "content") {
            fullText += ev.delta;
            if (!assistantMsg) {
              typing.remove();
              assistantMsg = addMsg("assistant", "");
            }
            assistantMsg.querySelector(".bubble").innerHTML = md(fullText);
            body.scrollTop = body.scrollHeight;
          } else if (ev.type === "error") {
            typing.remove();
            addMsg("assistant", "⚠️ Ошибка: " + ev.message);
          }
        }
      }
      if (pageMeta && assistantMsg) {
        var em = document.createElement("em");
        em.textContent = pageMeta;
        assistantMsg.appendChild(em);
        body.scrollTop = body.scrollHeight;
      }
    } catch (e) {
      typing.remove();
      addMsg("assistant", "⚠️ Не удалось связаться с сервером.");
    } finally {
      sendBtn.disabled = false;
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
