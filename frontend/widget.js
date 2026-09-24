/*!
 * 2KAD Chat Widget — лёгкий клиент для встраивания на сайт 2kad.ru
 * Загружается как <script src=".../widget/widget.js" defer></script>
 *
 * Конфигурация через window.KAD_CHATBOT_CONFIG:
 *   - apiBase: URL бэкенда (по умолчанию '/api')
 *   - title: заголовок окна
 *   - subtitle: подзаголовок
 *   - primaryColor: hex цвета кнопки
 *   - placeholder: плейсхолдер инпута
 *   - sessionId: уникальный ID сессии (если null — генерируется)
 *
 * Стили изолированы через .kad-chatbot-*
 */
(function () {
  "use strict";

  if (window.__kadChatbotLoaded) return;
  window.__kadChatbotLoaded = true;

  var CFG = Object.assign({
    apiBase: "/api",
    title: "Ассистент 2КАД",
    subtitle: "Отвечаем по кадастру и услугам",
    primaryColor: "#cc2c2c",
    placeholder: "Спросите про межевание, цены, документы…",
    sessionId: null,
    welcomeMessage:
      "Здравствуйте! Я ассистент сайта 2kad.ru. Помогу найти информацию " +
      "об услугах компании. Что вас интересует?",
  }, window.KAD_CHATBOT_CONFIG || {});

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
    ".kad-chatbot-btn{position:fixed;bottom:24px;right:24px;z-index:999999;",
    "width:60px;height:60px;border-radius:50%;border:none;cursor:pointer;",
    "background:" + CFG.primaryColor + ";color:#fff;font-size:28px;",
    "box-shadow:0 6px 20px rgba(0,0,0,.18);transition:transform .15s;",
    "display:flex;align-items:center;justify-content:center}",
    ".kad-chatbot-btn:hover{transform:scale(1.05)}",
    ".kad-chatbot-btn .badge{position:absolute;top:-4px;right:-4px;",
    "background:#fff;color:" + CFG.primaryColor + ";font-size:10px;font-weight:700;",
    "border-radius:10px;padding:2px 6px;display:none}",
    ".kad-chatbot-panel{position:fixed;bottom:100px;right:24px;z-index:999999;",
    "width:380px;max-width:calc(100vw - 32px);height:560px;max-height:80vh;",
    "background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.22);",
    "display:none;flex-direction:column;overflow:hidden;font-family:Manrope,system-ui,sans-serif}",
    ".kad-chatbot-panel.open{display:flex}",
    ".kad-chatbot-header{background:" + CFG.primaryColor + ";color:#fff;",
    "padding:14px 18px;display:flex;justify-content:space-between;align-items:center}",
    ".kad-chatbot-header h3{margin:0;font-size:15px;font-weight:700}",
    ".kad-chatbot-header p{margin:2px 0 0;font-size:11px;opacity:.85}",
    ".kad-chatbot-close{background:transparent;border:none;color:#fff;",
    "font-size:22px;cursor:pointer;line-height:1}",
    ".kad-chatbot-body{flex:1;overflow-y:auto;padding:14px;background:#fafafa;",
    "scroll-behavior:smooth}",
    ".kad-chatbot-msg{margin-bottom:10px;display:flex}",
    ".kad-chatbot-msg.user{justify-content:flex-end}",
    ".kad-chatbot-msg .bubble{max-width:80%;padding:9px 12px;border-radius:14px;",
    "font-size:13px;line-height:1.4;word-wrap:break-word;white-space:pre-wrap}",
    ".kad-chatbot-msg.user .bubble{background:" + CFG.primaryColor + ";color:#fff;",
    "border-bottom-right-radius:4px}",
    ".kad-chatbot-msg.assistant .bubble{background:#fff;color:#18181b;",
    "border:1px solid #e5e7eb;border-bottom-left-radius:4px}",
    ".kad-chatbot-msg .bubble a{color:" + CFG.primaryColor + ";text-decoration:underline}",
    ".kad-chatbot-msg .bubble strong{font-weight:700}",
    ".kad-chatbot-msg .bubble em{font-style:italic;color:#6b7280;font-size:11px;",
    "display:block;margin-top:4px}",
    ".kad-chatbot-msg .typing-dots{display:inline-block;width:30px;text-align:left}",
    ".kad-chatbot-msg .typing-dots span{display:inline-block;width:6px;height:6px;",
    "border-radius:50%;background:#999;margin:0 1px;animation:kadDot 1.2s infinite}",
    ".kad-chatbot-msg .typing-dots span:nth-child(2){animation-delay:.2s}",
    ".kad-chatbot-msg .typing-dots span:nth-child(3){animation-delay:.4s}",
    "@keyframes kadDot{0%,80%,100%{opacity:.3}40%{opacity:1}}",
    ".kad-chatbot-footer{padding:10px;border-top:1px solid #e5e7eb;background:#fff;",
    "display:flex;gap:8px}",
    ".kad-chatbot-footer input{flex:1;border:1px solid #e5e7eb;border-radius:20px;",
    "padding:9px 14px;font-size:13px;outline:none;font-family:inherit}",
    ".kad-chatbot-footer input:focus{border-color:" + CFG.primaryColor + "}",
    ".kad-chatbot-footer button{background:" + CFG.primaryColor + ";color:#fff;",
    "border:none;border-radius:50%;width:36px;height:36px;cursor:pointer;",
    "display:flex;align-items:center;justify-content:center;font-size:16px}",
    ".kad-chatbot-footer button:disabled{opacity:.4;cursor:not-allowed}",
    ".kad-chatbot-quick{padding:6px 14px 0}",
    ".kad-chatbot-quick button{display:block;width:100%;text-align:left;",
    "background:#fff;border:1px solid #e5e7eb;border-radius:10px;",
    "padding:8px 12px;margin-bottom:6px;font-size:12px;cursor:pointer;",
    "color:#18181b;font-family:inherit}",
    ".kad-chatbot-quick button:hover{border-color:" + CFG.primaryColor + ";",
    "color:" + CFG.primaryColor + "}",
    "@media(max-width:480px){.kad-chatbot-panel{right:8px;left:8px;width:auto;",
    "max-width:none}.kad-chatbot-btn{right:14px;bottom:14px}}"
  ].join("");
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // ---- DOM ----
  var btn = document.createElement("button");
  btn.className = "kad-chatbot-btn";
  btn.title = "Чат с 2КАД";
  btn.setAttribute("aria-label", "Открыть чат с ассистентом 2КАД");
  btn.innerHTML = "💬<span class='badge'>1</span>";
  document.body.appendChild(btn);

  var panel = document.createElement("div");
  panel.className = "kad-chatbot-panel";
  panel.innerHTML =
    "<div class='kad-chatbot-header'>" +
    "<div><h3></h3><p></p></div>" +
    "<button class='kad-chatbot-close' aria-label='Закрыть'>×</button>" +
    "</div>" +
    "<div class='kad-chatbot-body'></div>" +
    "<div class='kad-chatbot-quick'></div>" +
    "<div class='kad-chatbot-footer'>" +
    "<input type='text' placeholder='' autocomplete='off' />" +
    "<button aria-label='Отправить'>→</button>" +
    "</div>";
  document.body.appendChild(panel);

  panel.querySelector("h3").textContent = CFG.title;
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
    // минимальный markdown: **bold**, [text](url)
    var html = escape(s);
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
      "<a href=\"$2\" target=\"_blank\" rel=\"noopener\">$1</a>");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

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

  function showTyping() {
    return addMsg("assistant",
      "<span class='typing-dots'><span></span><span></span><span></span></span>");
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

  function close() {
    panel.classList.remove("open");
  }

  btn.addEventListener("click", function () {
    if (panel.classList.contains("open")) close();
    else open();
  });
  panel.querySelector(".kad-chatbot-close").addEventListener("click", close);

  // ---- send ----
  async function send() {
    var text = input.value.trim();
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
          message: text,
          session_id: SESSION_ID,
          stream: true,
        }),
      });
      if (!resp.ok) {
        typing.remove();
        addMsg("assistant", "⚠️ Ошибка соединения. Попробуйте позже или позвоните +7 (4822) 41-57-68.");
        sendBtn.disabled = false;
        return;
      }
      var reader = resp.body.getReader();
      var dec = new TextDecoder();
      var buf = "";
      var fullText = "";

      while (true) {
        var r = await reader.read();
        if (r.done) break;
        buf += dec.decode(r.value, {stream: true});
        var lines = buf.split("\n");
        buf = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line) continue;
          var ev;
          try { ev = JSON.parse(line); } catch (e) { continue; }
          if (ev.type === "meta") {
            if (ev.page_title) {
              pageMeta = ev.page_title + " · " + ev.page_url;
            }
            if (ev.mode === "pricing") pageMeta = "Калькулятор: " + ev.page_url;
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
        em.textContent = "→ " + pageMeta;
        assistantMsg.appendChild(em);
        body.scrollTop = body.scrollHeight;
      }
    } catch (e) {
      typing.remove();
      addMsg("assistant", "⚠️ Не удалось связаться с сервером.");
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  // badge
  setTimeout(function () {
    if (!panel.classList.contains("open")) {
      badge.style.display = "block";
    }
  }, 3000);
})();