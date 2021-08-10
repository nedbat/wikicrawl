import itertools
import textwrap
from xml.sax.saxutils import escape

class HtmlOutlineWriter(object):
    HEAD = textwrap.dedent(r"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8" />
        <style>
        html {
            font-family: sans-serif;
        }

        div {
            padding-left: 1em;
            margin-top: .5em;
        }

        div.collapsible {
            margin-left: -1em;
        }

        .toggle-box {
            display: none;
        }

        .toggle-box + label {
            cursor: pointer;
            display: block;
        }

        .toggle-box + label + div {
            display: none;
        }

        .toggle-box:checked + label + div {
            display: block;
        }

        .toggle-box + label:before {
            color: #888;
            content: "\25B8";
            display: block;
            float: left;
            height: 20px;
            line-height: 20px;
            width: 1em;
        }

        .toggle-box:checked + label:before {
            content: "\25BE";
        }

        .button {
            cursor: pointer;
        }
    """)
    STYLE_END = textwrap.dedent(r"""
        </style>

        <script language="JavaScript">
        function toggle_all(checked) {
            var checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (var i = 0; i < checkboxes.length; i++) {
                checkboxes[i].checked = checked;
            }
        }
        </script>
    """)

    HEAD_END = textwrap.dedent(r"""
        </head>
        <body>
    """)

    SECTION_START = textwrap.dedent(u"""\
        <div class="collapsible {klass}">
        <input class="toggle-box {klass}" id="sect_{id:05d}" type="checkbox">
        <label for="sect_{id:05d}">{html}</label>
        <div class='container'>
    """)

    SECTION_END = "</div></div>"

    def __init__(self, fout, title="", style=""):
        self.fout = fout
        self.section_ids = itertools.count()
        self.write(html=self.HEAD)
        self.write(html=style)
        self.write(html=self.STYLE_END)
        if title:
            self.write(html=f"<title>{title}</title>")
        self.write(html=self.HEAD_END)

    def write_open_close_all(self):
        self.write(html="""
            <p>
                <button onClick="toggle_all(true)">Open All</button>
                <button onClick="toggle_all(false)">Close All</button>
            </p>
        """)

    def start_section(self, html, klass=""):
        self.write(html=self.SECTION_START.format(
            id=next(self.section_ids), html=html, klass=klass or "",
        ))

    def end_section(self):
        self.write(html=self.SECTION_END)

    def write_leaf(self, html, klass=""):
        self.write(html="<div class='leaf'>")
        self.write(html=html)
        self.write(html="</div>")

    def write(self, html="", text=""):
        self.fout.write(prep_html(html=html, text=text))


def prep_html(html="", text="", href="", klass=""):
    if not html:
        html = escape(text)
    if href:
        html = f"<a href='{href}'>{html}</a>"
    if klass:
        html = f"<span class='{klass}'>{html}</span>"
    return html
