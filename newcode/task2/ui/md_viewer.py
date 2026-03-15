import os
import markdown
from tkinterweb import HtmlFrame

KATEX_CSS = """
.katex { font-size: 1.1em; }
.katex-display { margin: 1em 0; overflow-x: auto; }
"""

KATEX_JS = """
function renderMathInElement(element, options) {
    var delimiters = options.delimiters || [
        {left: '$$', right: '$$', display: true},
        {left: '$', right: '$', display: false}
    ];
    
    function findAndReplace(text) {
        var result = [];
        var i = 0;
        while (i < text.length) {
            var found = false;
            for (var d = 0; d < delimiters.length; d++) {
                var delim = delimiters[d];
                var leftIndex = text.indexOf(delim.left, i);
                if (leftIndex !== -1) {
                    var rightIndex = text.indexOf(delim.right, leftIndex + delim.left.length);
                    if (rightIndex !== -1) {
                        var formula = text.substring(leftIndex + delim.left.length, rightIndex);
                        result.push({
                            type: delim.display ? 'display' : 'inline',
                            formula: formula,
                            start: leftIndex,
                            end: rightIndex + delim.right.length
                        });
                        i = rightIndex + delim.right.length;
                        found = true;
                        break;
                    }
                }
            }
            if (!found) {
                i++;
            }
        }
        return result;
    }
    
    function renderFormula(formula, display) {
        return '<span class="math-' + (display ? 'display' : 'inline') + '" style="font-family: Times New Roman, serif; font-style: italic; color: #0066cc; background: #f0f8ff; padding: 2px 6px; border-radius: 3px;">' + formula + '</span>';
    }
    
    function processNode(node) {
        if (node.nodeType === 3) {
            var text = node.textContent;
            var formulas = findAndReplace(text);
            if (formulas.length > 0) {
                var newHtml = '';
                var lastEnd = 0;
                for (var f = 0; f < formulas.length; f++) {
                    newHtml += text.substring(lastEnd, formulas[f].start);
                    newHtml += renderFormula(formulas[f].formula, formulas[f].type);
                    lastEnd = formulas[f].end;
                }
                newHtml += text.substring(lastEnd);
                var temp = document.createElement('div');
                temp.innerHTML = newHtml;
                node.parentNode.replaceChild(temp.firstChild, node);
            }
        } else if (node.nodeType === 1) {
            for (var i = 0; i < node.childNodes.length; i++) {
                processNode(node.childNodes[i]);
            }
        }
    }
    
    processNode(element);
}
"""

class MDViewer(HtmlFrame):
    def __init__(self, master=None, width=800, height=600, **kwargs):
        super().__init__(master, javascript_enabled=True, **kwargs)
        self._base_font_size = 25
        self._current_file_path = None
        self._current_md_text = ""
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Button-4>", self._on_mousewheel)
        self.bind("<Button-5>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        if hasattr(event, 'state') and (event.state & 0x4):
            if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                self._base_font_size = min(28, self._base_font_size + 2)
            elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                self._base_font_size = max(8, self._base_font_size - 2)
            if self._current_file_path:
                self.display_md(self._current_file_path)

    def _generate_html(self, md_text):
        html_body = markdown.markdown(md_text, extensions=['extra', 'tables', 'fenced_code'])
        
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * {{
            box-sizing: border-box;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            text-rendering: optimizeLegibility;
        }}
        html, body {{
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: "Microsoft YaHei", "微软雅黑", "Segoe UI", sans-serif;
            font-size: {self._base_font_size}px;
            padding: 20px;
            line-height: 1.8;
            color: #333;
            background-color: #fff;
        }}
        strong, b {{
            font-weight: bold !important;
            color: #000;
        }}
        em, i {{
            font-style: italic;
        }}
        h1, h2, h3, h4, h5, h6 {{
            font-family: "Microsoft YaHei", "微软雅黑", sans-serif;
            font-weight: bold;
            color: #2c3e50;
            margin-top: 20px;
            margin-bottom: 10px;
            line-height: 1.3;
        }}
        h1 {{ font-size: 1.71em; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        h2 {{ font-size: 1.43em; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
        h3 {{ font-size: 1.29em; }}
        h4 {{ font-size: 1.14em; }}
        p {{ margin: 10px 0; }}
        code {{
            font-family: "Consolas", "Courier New", monospace;
            background-color: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.93em;
        }}
        pre {{
            background-color: #f8f8f8;
            padding: 15px;
            border-left: 4px solid #4a90d9;
            border-radius: 0 4px 4px 0;
            overflow-x: auto;
            margin: 15px 0;
        }}
        pre code {{
            background: none;
            padding: 0;
            font-size: 0.93em;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 15px 0;
            font-size: 1em;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 10px 12px;
            text-align: left;
        }}
        th {{
            background-color: #f5f5f5;
            font-weight: bold;
        }}
        tr:nth-child(even) {{
            background-color: #fafafa;
        }}
        blockquote {{
            border-left: 4px solid #ddd;
            margin: 15px 0;
            padding: 10px 20px;
            background-color: #f9f9f9;
            color: #666;
        }}
        ul, ol {{
            padding-left: 25px;
            margin: 10px 0;
        }}
        li {{ margin: 5px 0; }}
        a {{
            color: #4a90d9;
            text-decoration: none;
        }}
        a:hover {{ text-decoration: underline; }}
        hr {{
            border: none;
            border-top: 1px solid #eee;
            margin: 20px 0;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        .math-inline {{
            font-family: "Times New Roman", serif;
            font-style: italic;
            color: #0066cc;
            background: #f0f8ff;
            padding: 2px 6px;
            border-radius: 3px;
        }}
        .math-display {{
            display: block;
            text-align: center;
            font-family: "Times New Roman", serif;
            font-style: italic;
            color: #0066cc;
            background: #f8f9fa;
            padding: 10px;
            margin: 15px 0;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    {html_body}
    <script defer>
        {KATEX_JS}
        renderMathInElement(document.body, {{
            delimiters: [
                {{left: '$$', right: '$$', display: true}},
                {{left: '$', right: '$', display: false}}
            ]
        }});
    </script>
</body>
</html>"""
        return full_html

    def display_md(self, file_path):
        self._current_file_path = file_path
        if not os.path.exists(file_path):
            self.load_html("<h2 style='color:red;'>未找到题面文件</h2>")
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            self._current_md_text = f.read()

        html_content = self._generate_html(self._current_md_text)
        self.load_html(html_content)
