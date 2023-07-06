import re
import sys
import json
import subprocess

from hashlib import sha256

from pathlib import Path

from urllib.parse import quote

from base64 import b64encode as b64e

from collections.abc import Iterable

from itertools import chain

from docutils import nodes
from docutils.core import publish_parts
from docutils.parsers.rst import Directive, directives, roles
from docutils.parsers.rst.roles import set_classes
from docutils.writers.html4css1 import Writer, HTMLTranslator

from pelican import signals

from .abbr_state import AbbrState

# based on https://stackoverflow.com/a/49047197
class HTMLFragmentTranslator(HTMLTranslator):
    # prevent single paragraphs from being wrapped in <p> tags
    def visit_paragraph(self, node):
        if len(node.parent.children) == 1:
            self.context.append('')
        else:
            super().visit_paragraph(node)

    @classmethod
    def get_writer(cls, *a, **kw):
        w = Writer(*a, **kw)
        w.translator_class = cls
        return w

    @classmethod
    def rst_to_html(cls, s):
        w = cls.get_writer()
        return core.publish_parts(s, writer = w)['body']

def esc(s, attr=None):
    # https://mina86.com/2021/no-you-dont-need-to-escape-that/
    if attr is not None:
        # inside an attribute, escape `&` if followed by alphanumerics then a `;`
        s = re.sub(r'&(#|[A-Za-z0-9]+;)', r'&amp;\1', s)
        if attr == '"':
            s = s.replace('"', '&#34;')
        elif attr == "'":
            s = s.replace("'", '&#39;')
        elif attr == '':
            # don't use this
            s = s.replace(' ', '&#32;')
            s = s.replace('"', '&#34;')
            s = s.replace("'", '&#39;')
            s = s.replace('>', '&gt;')
            s = s.replace('=', '&#61;')
            s = s.replace('`', '&#96;')
        else:
            raise ValueError('Invalid attribue quoting style specified!')
    else:
        # outside of an attribute, `&` only needs to be escaped if followed by a named
        # character reference, but doing that precisely would require a list, but it's
        # a good enough heuristic to check if the next chracter is a `#` or a letter
        # followed by an alphanumeric character
        s = re.sub(r'&(#|[A-Za-z][0-9A-Za-z])', r'&amp;\1', s)

    s = s.replace('<', '&lt;')

    return s

def esc_sq(s):
    return esc(s, "'")

def esc_dq(s):
    return esc(s, '"')

def to_string(value):
    if isinstance(value, str):     return value
    elif isinstance(value, bytes): return value.decode()
    else:                          return str(value)

def html_element(tag_name, content=None, /, **kw):
    element = '<' + tag_name
    for attr, value in kw.items():
        # allow e.g. class and id as class_ and id_
        if attr[-1] == '_': attr = attr[:-1]
        attr = attr.replace('_', '-')

        if value not in (None, False):
            if value is True:
                element += ' ' + attr
            else:
                s = None
                if isinstance(value, str):
                    s = value
                elif isinstance(value, bytes):
                    s = value.decode()
                elif isinstance(value, Iterable):
                    l = list(value)
                    if len(l): s = ' '.join(map(to_string, l))
                else:
                    s = str(value)

                if s is not None: element += f' {attr}="{esc_dq(s)}"'

    if content is not None:
        return f'{element}>{esc(content)}</{tag_name}>'
    else:
        return element + '>'

def html_node(html):
    return nodes.raw('', html, format='html')

def html_raw(html):
    return [html_node(html)], []

def html_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    return html_raw(text)

# superscript suffix for e.g. 1st, 2nd, 3rd, 4th, nth
def ord_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    if text[-2:] in ('st', 'nd', 'rd', 'th'):
        # if the suffix is already there, just use it
        text, suffix = text[:-2], text[-2:]
    else:
        # otherwise, assume this is a number and do the right thing
        end = int(text) % 100
        if 11 >= end >= 13:
            suffix = 'th'
        else:
            end %= 10
            if   end == 1: suffix = 'st'
            elif end == 2: suffix = 'nd'
            elif end == 3: suffix = 'rd'
            else:          suffix = 'th'

    return html_raw(f'{esc(text)}<sup>{suffix}</sup>')

def ed_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    delim, text = text[0], text[1:]
    text = text.strip(delim)

    del_text, ins_text = map(esc, text.split(delim))
    html  = '<span class="subst">'
    html += f'<del>{del_text}</del>'
    html += f'<span class="invis"> </span>'
    html += f'<ins>{ins_text}</ins>'
    html += '</span>'

    return html_raw(html)

def a_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    m = re.search(r"id\s*=\s*([A-Za-z0-9-]+)", text)
    if m is not None:
        return html_raw(html_element('a', id=m.group(1)) + '</a>')

    # (.+?)     capture (group 1 - link text) non-greedy match of anything
    # \s*       zero or more whitespace characters
    # <([^>]+)> capture (group 2 - target url) everything between angle brackets
    # \s*       zero or more whitespace characters
    # (.*)      capture (group 3 - attributes) the rest of the string
    m = re.search(r"(.+?)\s*<([^>]+)>\s*(.*)", text)
    if not m:
        raise ValueError("Invalid a role text: " + text)

    classes = ["reference", "external"]
#    if re.match(r"(\w+:|//)", m.group(2)):
#        classes.append("external")

    kw = { 'href': m.group(2), 'class_': classes }
    # stick extra attributes into the tag
    if m.group(3):
        # split on whitespace
        for x in m.group(3).split():
            # key/value split on `=`
            attr, _, data = x.partition('=')
            if data:
                # class is handled special
                if attr == 'class':
                    for y in data.split(','):
                        classes.append(y)
                else:
                    # treat `,` as a list seperator
                    kw[attr] = data.split(',')

    html = html_element('a', **kw) + esc(m.group(1)) + '</a>'

    return html_raw(html)

_tag_stack = []
def push_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    html = ''
    for tag in text.split(','):
        html += f'<{tag}>'
        _tag_stack.append(tag)

    return html_raw(html)

def pop_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    html = ''
    n = len(_tag_stack) if text == '*' else int(text)
    for _ in range(n):
        html += f'</{_tag_stack.pop()}>'

    return html_raw(html)

def wiki_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    url_base = 'https://en.wikipedia.org/wiki/'
    parts = text.split('|', 1)
    text = parts[0]
    page = text if len(parts) == 1 else parts[1]
    m = re.search(r'(.*\S)#\S+$', text)
    if m is not None: text = m.group(1)
    # maybe need https://stackoverflow.com/a/32232764
    page = page[0].upper() + page[1:]
    page = page.replace(' ', '_')
    page = page.replace('’', "'")
    url = url_base + quote(page, safe='()#')

    html = html_element('a', text, href=url, class_=['reference', 'external'])
    return html_raw(html)

def abbr_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    options = options if options is not None else {}
    content = content if content is not None else []

    src = inliner.document.attributes['source']

    m = re.search(r'^(.+)\s+\((.+)\)$', text)
    if not m or not m.group(2):
        abbr = AbbrState.get(src, text)
        if abbr is None:
            raise ValueError("Invalid abbr role text: " + text)
    else:
        (text, title) = m.group(1, 2)
        if len(text) > len(title):
            (text, title) = (title, text)
        abbr = AbbrState.get(src, text, title)

    if abbr['count'] == 0:
        # we need to wrap the abbr to create an unstyled title with ::after
        html = ('<span class="abbr" data-title="{title}">'+\
                '<abbr title="{title}">{abbr}</abbr>'+\
                '</span>').format(**abbr)
    else:
        html = '<abbr title="{title}">{abbr}</abbr>'.format(**abbr)

    return html_raw(html)

def register_roles():
    module = sys.modules[__name__]
    for name in filter(lambda x: x.endswith('_role'), dir(module)):
        func = getattr(module, name)
        if callable(func):
            roles.register_local_role(name[:-5], func)

    tag_roles = {
        'bold':   'b',
        'italic': 'i',
        'strike': 's',
        'ul':     'u',
        'mark':   'mark',
        'var':    'var',
        'ins':    'ins',
        'del':    'del',
        'kbd':    'kbd',
        'samp':   'samp',
    }

    for name, tag in tag_roles.items():
        # the tag name needs to be passed into a closure...
        func = (lambda t: lambda *a: html_raw(f'<{t}>{esc(a[2])}</{t}>'))(tag)
        roles.register_local_role(name, func)

def register_directives(instance):
    OUTPUT_DIR = Path(instance.settings['OUTPUT_PATH'])

    class _Directive(Directive):
        has_content = True

        def text_content(self):
            self.assert_has_content()
            return '\n'.join(self.content).encode()

        def html_wrap(self, head, tail):
            text = self.text_content()

            # a generated node is basically plain
            node = nodes.generated(text, **self.options)
            self.add_name(node)
            node.line = self.content_offset + 1
            self.state.nested_parse(self.content, self.content_offset, node)
            return [html_node(head+'\n'), node, html_node(tail)]

        def write_file(self, suffix, data):
            src = Path(self.state.document.attributes['source'])
            d = src.stem + '_'
            name = sha256(data).hexdigest()[0:20] + suffix
            web = '/' + d + '/' + name
            aux = Path.joinpath(OUTPUT_DIR, src.stem + '_')
            aux.mkdir(exist_ok=True)
            with aux.joinpath(name).open('wb') as f:
                f.write(data)

            return web

    class Section(_Directive):
        has_content = True
        required_arguments = 1
        final_argument_whitespace = True
        option_spec = {
            'class': directives.class_option,
        }

        def run(self):
            classes = self.options.get("class", [])
            classes.insert(0, "section")
            id_ = nodes.make_id(self.arguments[0])

            head = html_element('div', class_=classes, id_=id_)
            tail = '</div>'

            return self.html_wrap(head, tail)

    class Details(_Directive):
        has_content = True
        required_arguments = 1
        final_argument_whitespace = True
        option_spec = {
            'class': directives.class_option,
            'section': directives.flag,
            'name': directives.unchanged,
        }

        def run(self):
            classes = self.options.get("class", [])
            summary = self.arguments[0]

            head = f'<details><summary>{summary}</summary>'
            tail = '</details>'

            if 'section' in self.options:
                id_ = re.sub(r'<.*?>', ' ', summary)
                id_ = re.sub(r'\s+', ' ', id_)
                id_ = nodes.make_id(id_)
                classes.insert(0, "section")
                head = html_element('div', class_=classes, id_=id_) + head
                tail += '</div>'

            return self.html_wrap(head, tail)

    # script with minification
    class Script(_Directive):
        option_spec = {
            'inline': directives.flag,
            'define': directives.unchanged_required,
            'enclose': directives.unchanged_required,
        }

        def run(self):
            text = self.text_content()

            args_common = ['terser', '--safari10', '--ecma', '5']
            args_pass1 = args_common + [
                '--compress', 'passes=2', '--mangle', 'reserved=_',
                '--mangle-props', 'regex=/^_.+/'
            ]
            # the second pass is a bit of a hack to allow unwanted constant
            # expression evaluation to be avoided
            args_pass2 = args_common + ['--define', '_=""']

            # add `define` arguments
            define = self.options.get('define', None)
            if define:
                for sub in define.split(','):
                    k, _, v = sub.partition('=')
                    args_pass1.append('--define')
                    args_pass1.append(k if not v else k + '=' + v)

            # add `enclose` arguments
            enclose = self.options.get('enclose', 'auto')
            if enclose not in ('no', 'off', 'false', 'disable'):
                eargs = {}
                if enclose not in ('yes', 'on', 'true', 'enable', 'auto'):
                    for sub in filter(lambda x: len(x), enclose.split(',')):
                        k, _, v = sub.partition('=')
                        eargs[k] = v or k
                else:
                    for sub in ('window', 'document', 'location', 'navigator'):
                        if text.count(sub.encode()) > 1:
                            eargs[sub] = sub

                if len(eargs) or enclose != 'auto':
                    args_pass1.append('--enclose')
                    keys, values = [], []
                    for k, v in eargs.items():
                        keys.append(k)
                        values.append(v)
                    args_pass1.append(','.join(keys)+':'+','.join(values))

            ret = subprocess.run(args_pass1, input=text, capture_output=True, check=True)
            ret = subprocess.run(args_pass2, input=ret.stdout, capture_output=True, check=True)

            if 'inline' in self.options:
                html = f'<script>{ret.stdout.decode()}</script>'
            else:
                link = self.write_file('.min.js', ret.stdout)
                html = f'<script src="{link}" async></script>'

            return [html_node(html)]

    # inline style with minification, as data uri
    class Style(_Directive):
        def run(self):
            text = self.text_content()

            ret = subprocess.run(['csso'], input=text, capture_output=True, check=True)

            quoted = ','+quote(ret.stdout.strip().decode())

            html = f'<link rel="stylesheet" href="data:text/css{quoted}">'

            return [html_node(html)]

    class Schema(_Directive):
        def run(self):
            obj = json.loads(self.text_content())
            content = json.dumps(obj, separators=(',', ':'))
            html = f'<script type="application/ld+json">{content}</script>'

            return [html_node(html)]

    directives.register_directive('section', Section)
    directives.register_directive('details', Details)
    directives.register_directive('script', Script)
    directives.register_directive('style', Style)
    directives.register_directive('schema', Schema)

def register():
    register_roles()
    signals.initialized.connect(register_directives)
