(function () {
    "use strict";

    var toolbarButtons = [
        ["bold", "B", "ضخیم"],
        ["italic", "I", "کج"],
        ["underline", "U", "زیرخط"],
        ["strikeThrough", "S", "خط‌خورده"],
        ["insertUnorderedList", "•", "فهرست نشانه‌دار"],
        ["insertOrderedList", "1.", "فهرست شماره‌دار"],
        ["justifyRight", "↦", "راست‌چین"],
        ["justifyCenter", "↔", "وسط‌چین"],
        ["justifyLeft", "↤", "چپ‌چین"],
        ["removeFormat", "Tx", "حذف قالب‌بندی"],
    ];

    function escapeHtml(value) {
        return value
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function initialHtml(value) {
        if (!value || /<\s*\/?\s*[a-z][^>]*>/i.test(value)) {
            return value || "";
        }
        return value
            .split(/\n\s*\n+/)
            .map(function (paragraph) {
                return "<p>" + escapeHtml(paragraph.trim()).replace(/\n/g, "<br>") + "</p>";
            })
            .filter(function (paragraph) {
                return paragraph !== "<p></p>";
            })
            .join("");
    }

    function sync(textarea, editor) {
        textarea.value = editor.innerHTML.trim();
    }

    function executeCommand(editor, command, value) {
        editor.focus();
        document.execCommand(command, false, value || null);
        editor.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function addLink(editor) {
        var url = window.prompt("آدرس لینک را وارد کنید:");
        if (!url) {
            return;
        }
        executeCommand(editor, "createLink", url.trim());
    }

    function insertHtml(editor, html) {
        editor.focus();
        document.execCommand("insertHTML", false, html);
        editor.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function applyFontSize(editor, size) {
        if (!size) {
            return;
        }
        editor.focus();
        var selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
            return;
        }
        var range = selection.getRangeAt(0);
        var span = document.createElement("span");
        span.style.fontSize = size;
        try {
            range.surroundContents(span);
        } catch (error) {
            span.appendChild(range.extractContents());
            range.insertNode(span);
        }
        selection.removeAllRanges();
        var newRange = document.createRange();
        newRange.selectNodeContents(span);
        selection.addRange(newRange);
        editor.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function csrfToken(form) {
        var input = form ? form.querySelector('input[name="csrfmiddlewaretoken"]') : null;
        return input ? input.value : "";
    }

    function uploadImage(editor, textarea, file) {
        var uploadUrl = textarea.dataset.imageUploadUrl;
        if (!uploadUrl) {
            window.alert("آپلود تصویر در این صفحه فعال نیست.");
            return;
        }

        var formData = new FormData();
        formData.append("file", file);

        var request = new XMLHttpRequest();
        request.open("POST", uploadUrl, true);
        request.setRequestHeader("X-CSRFToken", csrfToken(textarea.form));
        request.onload = function () {
            var payload = {};
            try {
                payload = JSON.parse(request.responseText);
            } catch (error) {
                payload = {};
            }
            if (request.status === 200 && payload.url) {
                var alt = window.prompt("متن جایگزین تصویر (اختیاری):") || "";
                insertHtml(
                    editor,
                    '<img src="' + escapeHtml(payload.url) + '" alt="' + escapeHtml(alt) +
                        '" class="article-inline-image" loading="lazy">'
                );
            } else {
                window.alert(payload.error || "بارگذاری تصویر ناموفق بود.");
            }
        };
        request.onerror = function () {
            window.alert("بارگذاری تصویر ناموفق بود.");
        };
        request.send(formData);
    }

    function createEditor(textarea) {
        if (textarea.dataset.richEditorInitialized === "true") {
            return;
        }
        textarea.dataset.richEditorInitialized = "true";

        var wrapper = document.createElement("div");
        wrapper.className = "article-rich-editor";
        var toolbar = document.createElement("div");
        toolbar.className = "article-rich-editor__toolbar";
        toolbar.setAttribute("role", "toolbar");
        toolbar.setAttribute("aria-label", "ابزارهای قالب‌بندی متن");

        var editor = document.createElement("div");
        editor.className = "article-rich-editor__surface";
        editor.contentEditable = "true";
        editor.setAttribute("role", "textbox");
        editor.setAttribute("aria-multiline", "true");
        editor.innerHTML = initialHtml(textarea.value);

        toolbarButtons.forEach(function (buttonConfig) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "article-rich-editor__button";
            button.textContent = buttonConfig[1];
            button.title = buttonConfig[2];
            button.setAttribute("aria-label", buttonConfig[2]);
            button.addEventListener("mousedown", function (event) {
                event.preventDefault();
            });
            button.addEventListener("click", function () {
                executeCommand(editor, buttonConfig[0]);
            });
            toolbar.appendChild(button);
        });

        var formatSelect = document.createElement("select");
        formatSelect.className = "article-rich-editor__select";
        formatSelect.setAttribute("aria-label", "نوع تیتر");
        [
            ["p", "پاراگراف"],
            ["h2", "تیتر ۲"],
            ["h3", "تیتر ۳"],
            ["h4", "تیتر ۴"],
            ["blockquote", "نقل‌قول"],
        ].forEach(function (optionConfig) {
            var option = document.createElement("option");
            option.value = optionConfig[0];
            option.textContent = optionConfig[1];
            formatSelect.appendChild(option);
        });
        formatSelect.addEventListener("change", function () {
            executeCommand(editor, "formatBlock", "<" + formatSelect.value + ">");
            formatSelect.value = "p";
        });
        toolbar.appendChild(formatSelect);

        var linkButton = document.createElement("button");
        linkButton.type = "button";
        linkButton.className = "article-rich-editor__button";
        linkButton.textContent = "↗";
        linkButton.title = "افزودن لینک";
        linkButton.setAttribute("aria-label", "افزودن لینک");
        linkButton.addEventListener("mousedown", function (event) {
            event.preventDefault();
        });
        linkButton.addEventListener("click", function () {
            addLink(editor);
        });
        toolbar.appendChild(linkButton);

        var imageButton = document.createElement("button");
        imageButton.type = "button";
        imageButton.className = "article-rich-editor__button";
        imageButton.textContent = "🖼";
        imageButton.title = "افزودن تصویر";
        imageButton.setAttribute("aria-label", "افزودن تصویر");
        imageButton.addEventListener("mousedown", function (event) {
            event.preventDefault();
        });

        var imageInput = document.createElement("input");
        imageInput.type = "file";
        imageInput.accept = "image/*";
        imageInput.hidden = true;
        imageInput.addEventListener("change", function () {
            if (imageInput.files && imageInput.files[0]) {
                uploadImage(editor, textarea, imageInput.files[0]);
            }
            imageInput.value = "";
        });
        imageButton.addEventListener("click", function () {
            imageInput.click();
        });
        toolbar.appendChild(imageButton);

        var sizeSelect = document.createElement("select");
        sizeSelect.className = "article-rich-editor__select";
        sizeSelect.setAttribute("aria-label", "اندازه فونت");
        var sizePlaceholder = document.createElement("option");
        sizePlaceholder.value = "";
        sizePlaceholder.textContent = "اندازه فونت";
        sizeSelect.appendChild(sizePlaceholder);
        [
            ["0.9rem", "کوچک"],
            ["1rem", "معمولی"],
            ["1.1rem", "متوسط"],
            ["1.25rem", "بزرگ"],
            ["1.5rem", "خیلی بزرگ"],
            ["2rem", "تیتر"],
        ].forEach(function (sizeConfig) {
            var option = document.createElement("option");
            option.value = sizeConfig[0];
            option.textContent = sizeConfig[1];
            sizeSelect.appendChild(option);
        });
        sizeSelect.addEventListener("change", function () {
            applyFontSize(editor, sizeSelect.value);
            sizeSelect.value = "";
        });
        toolbar.appendChild(sizeSelect);

        editor.addEventListener("input", function () {
            sync(textarea, editor);
        });
        editor.addEventListener("blur", function () {
            sync(textarea, editor);
        });

        textarea.parentNode.insertBefore(wrapper, textarea);
        wrapper.appendChild(toolbar);
        wrapper.appendChild(editor);
        wrapper.appendChild(imageInput);
        textarea.style.display = "none";

        var form = textarea.form;
        if (form) {
            form.addEventListener("submit", function () {
                sync(textarea, editor);
            });
        }
        sync(textarea, editor);
    }

    function initializeEditors(root) {
        (root || document).querySelectorAll("textarea.rich-text-source").forEach(createEditor);
    }

    document.addEventListener("DOMContentLoaded", function () {
        initializeEditors(document);
    });

    document.addEventListener("formset:added", function (event) {
        initializeEditors(event.target);
    });
})();
