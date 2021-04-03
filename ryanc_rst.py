import re
import json
import subprocess

from hashlib import sha256

from pathlib import Path

from urllib.parse import quote

from base64 import b64encode as b64e

from docutils import nodes
from docutils.core import publish_parts
from docutils.parsers.rst import Directive, directives, roles

from pelican import signals

def html_node(html):
    return nodes.raw('', html, format='html')

def a_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    m = re.search(r"(.+?)\s*<([^>]+)>\s*(.*)", text)
    if not m:
        raise ValueError("Invalid a role text: " + text)

    classes = ["reference", "external"]
#    if re.match(r"(\w+:|//)", m.group(2)):
#        classes.append("external")

    extra = ''
    # stick extra attributes into the tag
    if m.group(3):
        for x in m.group(3).split():
            attr, _, data = x.partition('=')
            if data:
                if attr == 'class':
                    for y in data.split(','):
                        classes.append(y)
                else:
                    extra += ' {}="{}"'.format(attr, ' '.join(data.split(',')))

    html = '<a class="{c}" href="{u}"{r}>{t}</a>'.format(
        c=' '.join(classes),
        u=m.group(2),
        r=extra,
        t=m.group(1)
    )

    return [html_node(html)], []

def strike_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    return [html_node(f'<s>{text}</s>')], []

def html_role(name, rawtext, text, lineno, inliner, options={}, content=[]):
    return [html_node(text)], []

def register_directives(instance):
    OUTPUT_DIR = Path(instance.settings['OUTPUT_PATH'])

    class _Directive(Directive):
        has_content = True

        def text_content(self):
            self.assert_has_content()
            return '\n'.join(self.content).encode()

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

    class Details(_Directive):
        required_arguments = 1
        final_argument_whitespace = True

        def run(self):
            text = self.text_content()

            # a generated node is basically plain
            node = nodes.generated(text, **self.options)
            self.state.nested_parse(self.content, self.content_offset, node)
            summary = self.arguments[0]
            return [
                html_node(f'<details><summary>{summary}</summary>'),
                node,
                html_node('</details>'),
            ]

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
                    for sub in ['window','document','location','navigator']:
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

    directives.register_directive('details', Details)
    directives.register_directive('script', Script)
    directives.register_directive('style', Style)
    directives.register_directive('schema', Schema)

def register():
    roles.register_local_role('a', a_role)
    roles.register_local_role('html', html_role)
    roles.register_local_role('strike', strike_role)
    signals.initialized.connect(register_directives)
