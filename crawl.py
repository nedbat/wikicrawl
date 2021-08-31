import collections
import datetime
import itertools
import json
import os
import os.path
import re

import click
import requests.exceptions
from atlassian import Confluence

try:
    import keys
except:
    class keys:
        USER = os.environ["CRAWL_USER"]
        PASSWORD = os.environ["CRAWL_PASSWORD"]
        SITE = os.environ["CRAWL_SITE"]

from get_visits import get_visits
from htmlwriter import HtmlOutlineWriter, prep_html
from work import prog_bar, report_http_errors, work_in_threads, write_message


confluence = Confluence(username=keys.USER, password=keys.PASSWORD, url=keys.SITE)

def scrub_title(title):
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()

def user_name(user_info):
    for key in ['username', 'displayName', 'publicName', 'email', 'accountId']:
        if key in user_info:
            return user_info[key]
    return 'UNKNOWN'

def remove_end(text, end):
    """Remove `end` from text if it's there.

    Returns (text, was-removed)
    """
    if text.endswith(end):
        return text[:-len(end)], True
    else:
        return text, False

def html_for_name(name):
    name, deactivated = remove_end(name, " (Deactivated)")
    deadclass = " deactivated" if deactivated else ""
    deadmark = "<sup>&#x2020;</sup>" if deactivated else ""
    html = f"<span class='who{deadclass}'>{prep_html(text=name)}{deadmark}</span>"
    return html

def html_for_datetime(when):
    return format(when, "%Y-%m-%d")

class Edit:
    def __init__(self, who, when):
        self.who = who
        self.when = datetime.datetime.fromisoformat(when.rstrip("Z"))

    def __lt__(self, other):
        return self.when < other.when

    def to_html(self):
        html = f"<span class='edit' data='{self.when}'>"
        html += html_for_name(self.who)
        html += f"<span class='when'> ({html_for_datetime(self.when)})</span>"
        html += f"</span>"
        return html


class Page:
    def __init__(self, api_page):
        #write_message(json.dumps(api_page, indent=4))
        self.id = api_page.get('id')
        self.type = api_page['type']
        self.status = api_page['status']
        self.title = api_page.get('title', "<no title>")
        self.url = api_page['_links'].get('webui')
        if api_page.get('ancestors'):
            self.parent_id = api_page['ancestors'][-1]['id']
        else:
            self.parent_id = None
        self.parent = None
        self.children = []
        self.restrictions = None
        history = api_page.get('history', {})
        if 'createdBy' in history:
            self.created = Edit(
                who=history['createdBy']['displayName'],
                when=history['createdDate'],
            )
        else:
            self.created = None
        if 'lastUpdated' in history:
            self.lastedit = Edit(
                who=history['lastUpdated']['by']['displayName'],
                when=history['lastUpdated']['when'],
            )
        else:
            self.lastedit = None
        self.visits = None

        metadata = api_page.get("metadata", {})
        self.labels = [res["label"] for res in metadata.get("labels", {}).get("results", [])]
        self.likes = metadata.get("likes", {}).get("count", 0)

    def __repr__(self):
        return f"<Page {self.status} {self.type} {self.title!r} id:{self.id}>"

    def __str__(self):
        return repr(self.title)

    def __lt__(self, other):
        return scrub_title(self.title) < scrub_title(other.title)

    def fetch_page_information(self):
        if self.id is None:
            return

        with report_http_errors():
            error = None
            for retry in range(3):
                try:
                    restrictions = confluence.get_all_restrictions_for_content(self.id)
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

        if 'CLOUD_SESSION_COOKIE_TOKEN' in dir(keys):
            visits = get_visits(self.id, keys.CLOUD_SESSION_COOKIE_TOKEN)
            if visits:
                self.visits = sum(v["total"] for v in visits)

    def descendants(self):
        return 1 + sum(c.descendants() for c in self.children)

    def html_for_name(self):
        if self.url:
            href = keys.SITE + self.url
        else:
            href = None
        html = prep_html(text=self.title, href=href)
        return html


class Space:
    def __init__(self, api_space=None, key=None):
        self.key = key or api_space['key']
        self.pages = self.all_pages = self.blog_posts = None
        # A guess, will be filled in with sizes from the last run.
        self.size = 1
        if api_space:
            self.name = api_space.get('name')
            self.permissions = api_space.get('permissions')
        else:
            self.name = None
            self.permissions = None

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.key!r}>"

    def pages_with_status(self, status):
        return [page for page in self.all_pages if page.status == status]

    def fetch_pages(self):
        if self.all_pages is not None:
            return
        self.all_pages = list(map(Page, self.get_api_pages()))
        self.pages = self.pages_with_status("current")
        pages_by_id = {p.id: p for p in self.pages if p.id is not None}

        self.blog_posts = list(map(Page, self.get_api_pages(type="blogpost")))

        bar_label = f"Reading pages from {self.key}"
        workers = 12 if len(self.pages) > 1000 else 4
        results = work_in_threads(self.pages, lambda p: p.fetch_page_information(), max_workers=workers)
        with prog_bar(results, desc=bar_label, total=len(self.pages)) as bar:
            for page, _ in bar:
                if page.parent_id:
                    try:
                        page.parent = pages_by_id[page.parent_id]
                    except:
                        write_message(f"No parent for page in {self.key}: {page}")
                    else:
                        page.parent.children.append(page)

    def get_api_pages_chunk(self, start, type="page"):
        with report_http_errors():
            return confluence.get_all_pages_from_space(
                self.key,
                content_type=type,
                status="any",
                limit=100,
                start=start,
                expand="ancestors,history,history.lastUpdated,metadata.labels,metadata.likes",
            )

    def get_api_pages(self, type="page"):
        desc = f"Getting {type}s from {self.key}"
        chunk_size = 100
        with prog_bar(desc=desc) as bar:
            start = 0
            def handle_content(content):
                #write_message(json.dumps(content, indent=4))
                yield from content
                bar.update(len(content))

            if type == "page":
                starts = list(range(0, self.size, chunk_size))
                if starts:
                    for _, content in work_in_threads(starts, self.get_api_pages_chunk, max_workers=(self.size // 300)+1):
                        yield from handle_content(content)
                    start = starts[-1] + chunk_size
            while True:
                content = self.get_api_pages_chunk(start, type=type)
                if not content:
                    break
                yield from handle_content(content)
                start += len(content)

    def fetch_permissions(self):
        if self.permissions is not None:
            return
        with report_http_errors():
            space_info = confluence.get_space_information(self.key, expand='permissions')
        self.permissions = space_info['permissions']

    def root_pages(self):
        return (page for page in self.pages if page.parent is None)

    def total_page_count(self):
        return len(self.all_pages) + len(self.blog_posts)

    def total_visits(self):
        return sum(p.visits for p in self.pages if p.visits is not None)

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
        not_boring = (name for name in not_addon if name not in BORING_ADMINS)
        # Sort them, but always put "administrators" first.
        return sorted(not_boring, key=lambda n: "" if n == "administrators" else n)

    def likes(self):
        return sum(page.likes for page in self.pages)

    def latest_edit(self):
        return max((page.lastedit for page in self.pages), default=None)

BORING_ADMINS = {
    "Chat Notifications",
    "Copy Space for Confluence",
    "Jira Ops Confluence integration",
    "Lucidchart Diagrams Connector",
    "Microsoft Teams for Confluence Cloud",
}

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


def get_api_spaces_chunk(start):
    with report_http_errors():
        return confluence.get_all_spaces(limit=10, start=start, expand="permissions")

def get_api_spaces(num_guess):
    # Not sure why spaces are repeated, but let's eliminate duplicates ourselves.
    seen = set()    # of space ids
    start = 0
    desc = "Getting spaces"
    with prog_bar(desc=desc) as bar:
        chunk_size = 10

        def handle_spaces(spaces):
            for space in spaces['results']:
                if space['id'] not in seen:
                    yield space
                    seen.add(space['id'])
            bar.update(spaces['size'])

        starts = list(range(0, num_guess, chunk_size))
        if starts:
            for _, spaces in work_in_threads(starts, get_api_spaces_chunk, max_workers=8):
                yield from handle_spaces(spaces)
            start = starts[-1] + chunk_size
        while True:
            with report_http_errors():
                spaces = confluence.get_all_spaces(limit=10, start=start, expand="permissions")
            if spaces['size'] == 0:
                break
            yield from handle_spaces(spaces)
            start += spaces['size']


def write_page(writer, page, parent_restricted=False):
    """Recursively write out pages.

    Returns number of restricted pages.
    """
    num_restricted = 0
    html = page.html_for_name()
    if page.restrictions:
        html = prep_html(html=html, klass="restricted")
        limits = sorted(itertools.chain.from_iterable(page.restrictions))
        html_names = (prep_html(text=n) for n in limits)
        html += " (" + prep_html(html=", ".join(map(html_for_name, html_names))) + ")"
        this_restricted = True
    elif parent_restricted:
        html = prep_html(html=html, klass="parent_restricted")
        this_restricted = True
    else:
        this_restricted = False

    if page.visits:
        html += " " + html_for_visits(page.visits)

    if page.likes > 0:
        html += " " + html_for_likes(page.likes)

    if page.status != "current":
        html += f" <span class='status'>[{page.status}]</span>"

    ndescendants = page.descendants()
    if ndescendants > 1:
        html += f" <span class='count'>[{ndescendants}]</span>"
    if page.created is not None:
        html += "<span class='edits'>"
        html += page.created.to_html()
        if page.lastedit is not None:
            if page.lastedit.when.date() != page.created.when.date():
                html += " &rarr; "
                html += page.lastedit.to_html()
        html += "</span>"

    for label in page.labels:
        html += f"<span class='label'>{label}</span>"

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


OTHER_STATUSES = ["draft", "archived", "trashed"]

def generate_space_page(space, html_dir='html'):
    space.fetch_pages()
    with open_for_writing(f"{html_dir}/pages_{space.key}.html") as fout:
        writer = HtmlOutlineWriter(fout, stylefile="style.css", title=f"{space.key} space")
        writer.write(html="<h1>")
        writer.write(text=space.key)
        writer.write(html="</h1>")
        writer.write(html=f"<p>{len(space.pages)} pages</p>")
        writer.write_open_close_all()
        num_restricted = 0
        for page in sorted(space.root_pages()):
            num_restricted += write_page(writer, page)

        def write_section(title, pages):
            if pages:
                writer.start_section(f"{title} <span class='count'>[{len(pages)}]</span>")
                for page in pages:
                    write_page(writer, page)
                writer.end_section()

        for status in OTHER_STATUSES:
            write_section(status.title() + " pages", space.pages_with_status(status))
        write_section("Blog Posts", space.blog_posts)

    return num_restricted


def html_for_visits(visits):
    return f"<span class='visits'>{visits}</span>" if visits else ""

def html_for_likes(likes):
    return f"<span class='likes'>{likes}</span>" if likes else ""

def generate_all_space_pages(do_pages, html_dir='html', skip_largest=0, skip_smallest=0, visits=False):
    try:
        with open("space_sizes.json") as f:
            space_sizes = json.load(f)
    except:
        space_sizes = {}

    api_spaces = get_api_spaces(num_guess=len(space_sizes) or 200)
    spaces = [Space(sd) for sd in api_spaces]
    for space in spaces:
        space.size = space_sizes.get(space.key, 1)

    # Sort the spaces so that the largest spaces are first.
    spaces.sort(key=(lambda s: s.size), reverse=True)
    # Skip some, for dev purposes.
    spaces = spaces[skip_largest:(-skip_smallest if skip_smallest else None)]

    if do_pages:
        num_restricteds = {}
        work = work_in_threads(spaces, generate_space_page, max_workers=10)
        for space, num_restricted in prog_bar(work, desc="Reading spaces", total=len(spaces)):
            num_restricteds[space.key] = num_restricted

    with open("space_sizes.json", "w") as f:
        json.dump(space_sizes, f)

    with open("sort.js") as sortjs:
        JAVASCRIPT = sortjs.read()

    def tdl(html):
        writer.write("<td>")
        writer.write(str(html))
        writer.write("</td>")

    def tdr(html):
        writer.write("<td class='right'>")
        writer.write(str(html))
        writer.write("</td>")

    with open_for_writing(f"{html_dir}/spaces.html") as fout:
        total_pages = 0
        total_restricted = 0
        total_posts = 0
        writer = HtmlOutlineWriter(fout, stylefile="style.css", title="All spaces")
        if do_pages:
            writer.write(
                "<p>Other reports: &nbsp;" +
                "<a href='authors.html'>By author</a>, &nbsp;" +
                "<a href='all_spaces_pages.html'>All pages flat</a>." +
                "</p>"
            )

        writer.write("<table>")
        writer.write("<thead><tr><th>Space")
        if do_pages:
            writer.write("<th class='right'>Pages<th class='right'>Restricted<th class='right'>Blog Posts")
            for status in OTHER_STATUSES:
                writer.write(f"<th class='right'>{status.title()}")
            if visits:
                writer.write("<th class='right'>Visits")
            writer.write("<th class='right'>Likes<th class='right'>Last Edit")
        writer.write("<th>Anon<th>Logged-in<th>Summary<th>Admins")
        writer.write("</tr></thead>")
        writer.write("<tbody>")
        status_totals = dict.fromkeys(OTHER_STATUSES, 0)
        for order, space in enumerate(sorted(spaces, key=lambda s: s.key)):
            if do_pages:
                num_restricted = num_restricteds[space.key]

            writer.write("<tr>")
            title = space.key
            if space.name:
                title += ": " + space.name
            space_name = prep_html(text=title, href=f"pages_{space.key}.html" if do_pages else None)
            tdl(f"<span data='{order}'>{space_name}</span>")
            if do_pages:
                tdr(len(space.pages))
                tdr(num_restricted)
                tdr(len(space.blog_posts))
                for status in OTHER_STATUSES:
                    tdr(len(space.pages_with_status(status)))
                space_sizes[space.key] = space.total_page_count()
                if visits:
                    tdr(html_for_visits(space.total_visits()))
                tdr(html_for_likes(space.likes()))
                latest_edit = space.latest_edit()
                tdr(latest_edit.to_html() if latest_edit else "")
            anon = space.has_anonymous_read()
            logged = space.has_loggedin_read()
            tdl(anon)
            tdl(logged)
            tdl(PERM_SHORTHANDS[(anon, logged)])
            tdl(', '.join(map(html_for_name, space.admins())))
            writer.write("</tr>\n")
            if do_pages:
                total_pages += len(space.pages)
                total_restricted += num_restricted
                total_posts += len(space.blog_posts)
                for status in OTHER_STATUSES:
                    status_totals[status] += len(space.pages_with_status(status))
        writer.write("</tbody>")
        if do_pages:
            writer.write("<tfoot><tr>")
            tdl(f"TOTAL: {len(spaces)}")
            tdr(total_pages)
            tdr(total_restricted)
            tdr(total_posts)
            for status in OTHER_STATUSES:
                tdr(status_totals[status])
            writer.write("</tr></tfoot>")
        writer.write(html="</table>")
        writer.write(html=f"<script>{JAVASCRIPT}</script>")

    if do_pages:
        with open_for_writing(f"{html_dir}/all_spaces_pages.html") as fout:
            writer = HtmlOutlineWriter(fout, stylefile="style.css", title="All space pages")
            writer.write("<table><thead><tr>")
            writer.write("<th>Created</th><th>Last Edited</th><th>Last Edited by:</th>")
            if visits:
                writer.write("<th>Visits</th>")
            writer.write("<th>Likes</th><th>Space</th><th>Type</th><th>Page</th></tr></thead>")
            writer.write("<tbody>")
            for space in spaces:
                for page in itertools.chain(space.all_pages, space.blog_posts):
                    if page.created is None:
                        continue
                    writer.write("<tr>")
                    tdr(html_for_datetime(page.created.when))
                    tdr(html_for_datetime(page.lastedit.when))
                    tdl(html_for_name(page.lastedit.who))
                    if visits:
                        tdr(html_for_visits(page.visits))
                    tdr(html_for_likes(page.likes))
                    tdl(space.key)
                    tdl(page.type)
                    tdl(page.html_for_name())
                    writer.write("</tr>")
            writer.write(html="</tbody></table>")
            writer.write(html=f"<script>{JAVASCRIPT}</script>")

    if do_pages:
        with open_for_writing(f"{html_dir}/authors.html") as fout:
            writer = HtmlOutlineWriter(fout, stylefile="style.css", title="Authors")
            authors = collections.defaultdict(list)
            for space in spaces:
                for page in itertools.chain(space.all_pages, space.blog_posts):
                    if page.created:
                        authors[page.created.who].append(page)
            writer.write(html="<table><thead><tr><th>Who</th><th>Num created</th><th>First</th><th>When</th><th>Last</th><th>When</th></tr></thead>")
            writer.write("<tbody>")
            for author, pages in sorted(authors.items()):
                writer.write("<tr>")
                tdl(html_for_name(author))
                tdr(len(pages))
                first = min(pages, key=lambda p:p.created.when)
                tdl(first.html_for_name())
                tdr(html_for_datetime(first.created.when))
                if len(pages) > 1:
                    last = max(pages, key=lambda p:p.created.when)
                    tdl(last.html_for_name())
                    tdr(html_for_datetime(last.created.when))
                else:
                    tdl("&mdash;")
                    tdr("&mdash;")
                writer.write("</tr>")
            writer.write("</tbody></table>")
            writer.write(html=f"<script>{JAVASCRIPT}</script>")


PERM_SHORTHANDS = {
    (False, False): "Internal",
    (False, True): "Logged-in",
    (True, True): "Open",
    (True, False): "???",
}

@click.command(help="Examine a Confluence wiki and produce an HTML report on spaces and permissions")
@click.option('--all', 'all_spaces', is_flag=True, help="Examine all spaces")
@click.option('--pages/--no-pages', default=True, help="Examine the page trees")
@click.option('--visits/--no-visits', default=True, help="Collect visit stats")
@click.option('--htmldir', default='html', metavar="DIR", help="Directory to get the HTML results")
@click.option('--skip-largest', type=int, default=0, metavar="N", help="Skip N largest spaces")
@click.option('--skip-smallest', type=int, default=0, metavar="N", help="Skip N smallest spaces")
@click.argument('space_keys', nargs=-1)
def main(all_spaces, pages, visits, htmldir, space_keys, skip_largest, skip_smallest):
    if not visits:
        # Cheap way to prevent collecting visit information.
        try:
            del keys.CLOUD_SESSION_COOKIE_TOKEN
        except AttributeError:
            pass

    if all_spaces:
        if space_keys:
            click.echo("Can't specify space keys with --all")
            return
        generate_all_space_pages(
            do_pages=pages,
            html_dir=htmldir,
            skip_largest=skip_largest,
            skip_smallest=skip_smallest,
            visits=visits,
            )
    elif space_keys:
        for space_key in space_keys:
            generate_space_page(Space(key=space_key), html_dir=htmldir)
    else:
        click.echo("Nothing to do!")

if __name__ == "__main__":
    main()
