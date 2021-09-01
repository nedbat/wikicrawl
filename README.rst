wikicrawl
#########

This is a crawler to report on pages in a Confluence wiki.

Create a Python 3.8 virtualenv, then install the requirements::

    python -m pip install -r requirements.txt

To run it, create a file called ``keys.py`` like this::

    USER = 'myemail@company.com'
    PASSWORD = 'VouKOgWgS1xBiVMHtsGQD349'
    SITE = 'https://openedx.atlassian.net/wiki'

or define environment variables::

    CRAWL_USER = 'myemail@company.com'
    CRAWL_PASSWORD = 'VouKOgWgS1xBiVMHtsGQD349'
    CRAWL_SITE = 'https://openedx.atlassian.net/wiki'

The PASSWORD is an API token you can get from https://id.atlassian.com/manage-profile/security/api-tokens

If you wish to get visited data on your pages, you can add CLOUD_SESSION_COOKIE_TOKEN to ``keys.py`` like this::

    CLOUD_SESSION_COOKIE_TOKEN = "sdljfslajdflashdflasjdflkajsldfjalsndamvosjdmiweryoweiurasnasdvosdueursasdkhasohdfasuioyfasjfioehsanfsflksajfioe"

You will need to copy the value from a cookie in your browser called ``cloud.session.token`` that is scoped to something similar to ``.atlassian.net``.
Your actual value will be much longer than this (~900 characters).

Then run::

    python crawl.py --all --pages

An ``html`` directory will be created here and populated with the report.  Open
html/index.html to see the list of wiki spaces, each linked to a page about
the pages in the space.
