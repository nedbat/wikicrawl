wikicrawl
#########

This is a crawler to report on pages in a Confluence wiki.

Create a Python 3.8 virtualenv, then install the requirements::

    python -m pip install -r requirements.txt

To run it, create a file called ``keys.py`` like this::

    USER = 'myemail@company.com'
    PASSWORD = 'VouKOgWgS1xBiVMHtsGQD349'
    SITE = 'https://openedx.atlassian.net/wiki'

The PASSWORD is an API token you can get from https://id.atlassian.com/manage-profile/security/api-tokens

Then run::

    python crawl.py --all --pages

An ``html`` directory will be created here and populated with the report.  Open
html/spaces.html to see the list of wiki spaces, each linked to a page about
the pages in the space.
