import itertools
import os
import os.path
import pprint
import re
import sys

import click
from PythonConfluenceAPI import ConfluenceAPI

import keys
from htmlwriter import HtmlOutlineWriter, prep_html


api = ConfluenceAPI(keys.USER, keys.PASSWORD, keys.SITE)


def scrub_title(title):
    return re.sub(r"[^a-z ]", "", title.lower()).strip()

class Page:
    def __init__(self, api_page):
        self.id = api_page['id']
        self.title = api_page['title']
        self.url = api_page['_links']['webui']
        if api_page['ancestors']:
            self.parent_id = api_page['ancestors'][-1]['id']
        else:
            self.parent_id = None
        self.parent = None
        self.children = []
        self.restrictions = None

    def __repr__(self):
        return f"<Page {self.title!r}>"

    def __str__(self):
        return repr(self.title)

    def __lt__(self, other):
        return scrub_title(self.title) < scrub_title(other.title)

    def fetch_restrictions(self):
        restrictions = api.get_op_restrictions_for_content_id(self.id)
        read_res = restrictions['read']['restrictions']
        groups = [gr['name'] for gr in read_res['group']['results']]
        users = [ur['username'] for ur in read_res['user']['results']]
        if groups or users:
            self.restrictions = (tuple(groups), tuple(users))

    def breadcrumbs(self):
        if self.parent:
            bc = self.parent.breadcrumbs() + " / "
        else:
            bc = ""
        bc += self.title
        return bc

    def depth(self):
        if self.parent:
            return 1 + self.parent.depth()
        else:
            return 0


class Space:
    def __init__(self, api_space=None, key=None):
        self.key = key or api_space['key']
        self.pages = None
        self.pages_by_id = None
        self.blog_posts = None
        if api_space:
            self.name = api_space.get('name')
            self.permissions = api_space.get('permissions')
        else:
            self.name = None
            self.permissions = None

    def fetch_pages(self):
        if self.pages is not None:
            return
        self.pages = list(map(Page, get_api_pages(self.key)))
        self.pages_by_id = {p.id: p for p in self.pages}

        self.blog_posts = list(map(Page, get_api_pages(self.key, type='blogpost')))

        for page in count_off(self.pages):
            page.fetch_restrictions()
            if page.parent_id:
                try:
                    page.parent = self.pages_by_id[page.parent_id]
                except:
                    print(f"No parent for {page}")
                else:
                    page.parent.children.append(page)

    def fetch_permissions(self):
        if self.permissions is not None:
            return
        space_info = api.get_space_information(self.key, expand='permissions')
        self.permissions = space_info['permissions']

    def root_pages(self):
        return (page for page in self.pages if page.parent is None)

    def has_anonymous_read(self):
        self.fetch_permissions()
        return any(p for p in self.permissions if permission_is_anonymous_read(p))

    def has_loggedin_read(self):
        self.fetch_permissions()
        return any(p for p in self.permissions if permission_is_loggedin_read(p))


def permission_is_read_space(p):
    return (
        'operation' in p and
        p['operation']['operation'] == 'read' and
        p['operation']['targetType'] == 'space'
    )

def permission_is_anonymous_read(p):
    return (
        permission_is_read_space(p) and
        p.get('anonymousAccess', False)
    )

def permission_is_loggedin_read(p):
    return (
        permission_is_read_space(p) and
        'group' in p.get('subjects', {}) and
        # Not sure why group.results is a list? Only ever seems to have one thing in it.
        any(res['name'] == 'confluence-users' for res in p['subjects']['group']['results'])
    )


def get_api_pages(space_key, type="page"):
    start = 0
    while True:
        print(f"Getting {type}s from {space_key} at {start}")
        content = api.get_space_content(space_key, limit=100, start=start, expand="ancestors")
        results = content[type]
        if results['size'] == 0:
            break
        yield from results['results']
        start = results['start'] + results['size']


def get_api_spaces():
    start = 0
    while True:
        print(f"Getting spaces at {start}")
        spaces = api.get_spaces(limit=25, start=start, expand="permissions")
        if spaces['size'] == 0:
            break
        yield from spaces['results']
        start = spaces['start'] + spaces['size']


def count_off(seq):
    i = -1
    for i, thing in enumerate(seq):
        print(".", end="")
        sys.stdout.flush()
        if i % 100 == 99:
            print(i+1)
        yield thing
    if i > -1:
        print(i+1)


def write_page(writer, page, parent_restricted=False):
    """Recursively write out pages.

    Returns number of restricted pages.
    """
    num_restricted = 0
    if page.url:
        href = keys.SITE + page.url
    else:
        href = None
    html = prep_html(text=page.title, href=href)
    if page.restrictions:
        html = prep_html(html=html, klass="restricted")
        limits = sorted(itertools.chain.from_iterable(page.restrictions))
        html += " (" + prep_html(text=", ".join(limits)) + ")"
        this_restricted = True
    elif parent_restricted:
        html = prep_html(html=html, klass="parent_restricted")
        this_restricted = True
    else:
        this_restricted = False

    if this_restricted:
        num_restricted = 1

    if page.children:
        writer.start_section(html)
        for child in sorted(page.children):
            num_restricted += write_page(writer, child, this_restricted)
        writer.end_section()
    else:
        writer.write_leaf(html)

    return num_restricted


def open_for_writing(path):
    """Open a file for writing, including creating dirs as needed."""
    dirname, filename = os.path.split(path)
    if dirname and not os.path.isdir(dirname):
        os.makedirs(dirname)
    return open(path, "w")


STYLE = """
.restricted { background: #ffcccc; }
.parent_restricted { background: #ffff44; }
"""

def generate_space_page(space):
    space.fetch_pages()
    with open_for_writing(f"html/pages_{space.key}.html") as fout:
        writer = HtmlOutlineWriter(fout, style=STYLE)
        writer.write(html="<h1>")
        writer.write(text=space.key)
        writer.write(html="</h1>")
        writer.write(html=f"<p>{len(space.pages)} pages</p>")
        writer.write_open_close_all()
        num_restricted = 0
        for page in sorted(space.root_pages()):
            num_restricted += write_page(writer, page)
        if space.blog_posts:
            writer.start_section(prep_html(text="Blog Posts"))
            for post in space.blog_posts:
                write_page(writer, post)
            writer.end_section()
    return num_restricted

def generate_all_space_pages():
    DO_PAGES = True
    api_spaces = get_api_spaces()
    spaces = [Space(s) for s in api_spaces]
    spaces.sort(key=lambda s: s.key)
    with open_for_writing("html/spaces.html") as fout:
        total_pages = 0
        total_restricted = 0
        total_posts = 0
        writer = HtmlOutlineWriter(fout, style="""
            td, th {
                padding: .25em .5em;
                text-align: left;
            }
            td.right, th.right {
                text-align: right;
            }
            """
        )
        writer.write(html="<table>")
        writer.write(html="<tr><th>Space<th class='right'>Pages<th class='right'>Restricted<th class='right'>Blog Posts<th>Anon<th>Logged-in<th>Summary</tr>")
        for space in spaces:
            if DO_PAGES:
                num_restricted = generate_space_page(space)

            writer.write("<tr>")
            title = space.key
            if space.name:
                title += ": " + space.name
            title = prep_html(text=title, href=f"pages_{space.key}.html")
            writer.write(html=f"<td>{title}")
            if DO_PAGES:
                writer.write(html=f"<td class='right'>{len(space.pages)}")
                writer.write(html=f"<td class='right'>{num_restricted}")
                writer.write(html=f"<td class='right'>{len(space.blog_posts)}")
            else:
                writer.write(html=f"<td class='right'>0")
                writer.write(html=f"<td class='right'>0")
                writer.write(html=f"<td class='right'>0")
            anon = space.has_anonymous_read()
            logged = space.has_loggedin_read()
            writer.write(html=f"<td>{anon}")
            writer.write(html=f"<td>{logged}")
            writer.write(html=f"<td>{PERM_SHORTHANDS[(anon, logged)]}")
            writer.write("</tr>\n")
            if DO_PAGES:
                total_pages += len(space.pages)
                total_restricted += num_restricted
                total_posts += len(space.blog_posts)
        writer.write(html=f"<tr><td>TOTAL <td class='right'>{total_pages}<td class='right'>{total_restricted}<td class='right'>{total_posts}</tr>")
        writer.write(html="</table>")

PERM_SHORTHANDS = {
    (False, False): "Internal",
    (False, True): "Logged-in",
    (True, True): "Open",
    (True, False): "???",
}

@click.command()
@click.option('--all', 'all_spaces', is_flag=True)
@click.argument('space_keys', nargs=-1)
def main(all_spaces, space_keys):
    if all_spaces:
        if space_keys:
            click.echo("Can't specify space keys with --all")
            return
        generate_all_space_pages()
    elif space_keys:
        for space_key in space_keys:
            generate_space_page(Space(key=space_key))
    else:
        click.echo("Nothing to do!")

if __name__ == "__main__":
    main()
