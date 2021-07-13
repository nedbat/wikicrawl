import collections
import contextlib
import itertools
import os
import os.path
import pprint
import re

import click
import requests.exceptions
from PythonConfluenceAPI import ConfluenceAPI

import keys
from htmlwriter import HtmlOutlineWriter, prep_html


api = ConfluenceAPI(keys.USER, keys.PASSWORD, keys.SITE)


def scrub_title(title):
    return re.sub(r"[^a-z ]", "", title.lower()).strip()

def user_name(user_info):
    for key in ['username', 'displayName', 'publicName', 'email', 'accountId']:
        if key in user_info:
            return user_info[key]
    return 'UNKNOWN'

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
        with report_http_errors():
            error = None
            for retry in range(3):
                try:
                    restrictions = api.get_op_restrictions_for_content_id(self.id)
                except requests.exceptions.HTTPError as err:
                    error = err
                    continue
                else:
                    break
            else:
                raise error
        read_res = restrictions['read']['restrictions']
        groups = [gr['name'] for gr in read_res['group']['results']]
        users = [user_name(ur) for ur in read_res['user']['results']]
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

    def descendants(self):
        return 1 + sum(c.descendants() for c in self.children)


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

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.key!r}>"

    def fetch_pages(self):
        if self.pages is not None:
            return
        self.pages = list(map(Page, get_api_pages(self.key)))
        self.pages_by_id = {p.id: p for p in self.pages}

        self.blog_posts = list(map(Page, get_api_pages(self.key, type='blogpost')))

        bar_label = "Reading pages from {}".format(self.key)
        with click.progressbar(self.pages, label=bar_label, show_pos=True) as bar:
            for page in bar:
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
        with report_http_errors():
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

    def admins(self):
        """Get a sorted list of administrators names."""
        self.fetch_permissions()
        space_admin_perm = {'operation': 'administer', 'targetType': 'space'}
        has_perm = (name_for_permission(p) for p in self.permissions if p.get('operation') == space_admin_perm)
        not_addon = (name for name in has_perm if not name.startswith('addon_'))
        # Sort them, but always put "administrators" first.
        return sorted(not_addon, key=lambda n: "" if n == "administrators" else n)


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

def name_for_permission(p):
    """Get the name of the thing being given this permission."""
    # Confluence doesn't make it convenient...
    if p['anonymousAccess']:
        name = 'anonymous'
    else:
        subjects = p['subjects']
        if 'group' in subjects:
            name = subjects['group']['results'][0]['name']
        else:
            name = user_name(subjects['user']['results'][0])
    return name


@contextlib.contextmanager
def report_http_errors():
    """Wrap around api calls, so that HTTP errors will be reported usefully."""
    try:
        yield
    except requests.exceptions.HTTPError as err:
        resp = err.response
        print(f"Request for {resp.url!r} failed: status {resp.status_code}")
        print(f"Text response:\n{resp.text}")
        print()
        raise


def get_api_pages(space_key, type="page"):
    start = 0
    while True:
        print(f"Getting {type}s from {space_key} at {start}")
        with report_http_errors():
            content = api.get_space_content(space_key, limit=100, start=start, expand="ancestors")
        results = content[type]
        if results['size'] == 0:
            break
        yield from results['results']
        start = results['start'] + results['size']


def get_api_spaces():
    # Not sure why spaces are repeated, but let's eliminated duplicates ourselves.
    seen = set()    # of space ids
    start = 0
    while True:
        print(f"Getting spaces at {start}")
        with report_http_errors():
            spaces = api.get_spaces(limit=25, start=start, expand="permissions")
        if spaces['size'] == 0:
            break
        for space in spaces['results']:
            if space['id'] not in seen:
                yield space
                seen.add(space['id'])
        start = spaces['start'] + spaces['size']


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
    ndescendants = page.descendants()
    if ndescendants > 1:
        html += f" <span class='count'>[{ndescendants}]</span>"

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
.restricted { background: #ffcccc; padding: 2px; margin-left: -2px; }
.parent_restricted { background: #ffff44; padding: 2px; margin-left: -2px; }
.count { display: inline-block; margin-left: 1em; font-size: 85%; color: #666; }
"""

def generate_space_page(space, html_dir='html'):
    space.fetch_pages()
    with open_for_writing(f"{html_dir}/pages_{space.key}.html") as fout:
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

def generate_all_space_pages(do_pages, html_dir='html'):
    api_spaces = get_api_spaces()
    spaces = [Space(s) for s in api_spaces]
    spaces.sort(key=lambda s: s.key)
    with open_for_writing(f"{html_dir}/spaces.html") as fout:
        total_pages = 0
        total_restricted = 0
        total_posts = 0
        writer = HtmlOutlineWriter(fout, style="""
            td, th {
                padding: .25em .5em;
                text-align: left;
                vertical-align: top;
            }
            td.right, th.right {
                text-align: right;
            }
            """
        )
        writer.write(html="<table>")
        writer.write(html="<tr><th>Space")
        if do_pages:
            writer.write(html="<th class='right'>Pages<th class='right'>Restricted<th class='right'>Blog Posts")
        writer.write(html="<th>Anon<th>Logged-in<th>Summary<th>Admins</tr>")
        for space in spaces:
            if do_pages:
                num_restricted = generate_space_page(space)

            writer.write("<tr>")
            title = space.key
            if space.name:
                title += ": " + space.name
            title = prep_html(text=title, href=f"pages_{space.key}.html" if do_pages else None)
            writer.write(html=f"<td>{title}")
            if do_pages:
                writer.write(html=f"<td class='right'>{len(space.pages)}")
                writer.write(html=f"<td class='right'>{num_restricted}")
                writer.write(html=f"<td class='right'>{len(space.blog_posts)}")
            anon = space.has_anonymous_read()
            logged = space.has_loggedin_read()
            writer.write(html=f"<td>{anon}")
            writer.write(html=f"<td>{logged}")
            writer.write(html=f"<td>{PERM_SHORTHANDS[(anon, logged)]}")
            writer.write(html=f"<td>{', '.join(space.admins())}")
            writer.write("</tr>\n")
            if do_pages:
                total_pages += len(space.pages)
                total_restricted += num_restricted
                total_posts += len(space.blog_posts)
        if do_pages:
            writer.write(html=f"<tr><td>TOTAL: {len(spaces)}<td class='right'>{total_pages}<td class='right'>{total_restricted}<td class='right'>{total_posts}</tr>")
        writer.write(html="</table>")

PERM_SHORTHANDS = {
    (False, False): "Internal",
    (False, True): "Logged-in",
    (True, True): "Open",
    (True, False): "???",
}

@click.command(help="Examine a Confluence wiki and produce an HTML report on spaces and permissions")
@click.option('--all', 'all_spaces', is_flag=True, help="Examine all spaces")
@click.option('--pages/--no-pages', default=True, help="Examine the page trees")
@click.option('--htmldir', default='html', metavar="DIR", help="Directory to get the HTML results")
@click.argument('space_keys', nargs=-1)
def main(all_spaces, pages, htmldir, space_keys):
    if all_spaces:
        if space_keys:
            click.echo("Can't specify space keys with --all")
            return
        generate_all_space_pages(do_pages=pages, html_dir=htmldir)
    elif space_keys:
        for space_key in space_keys:
            generate_space_page(Space(key=space_key), html_dir=htmldir)
    else:
        click.echo("Nothing to do!")

if __name__ == "__main__":
    main()
