#!/usr/bin/env python3
#
# PyTorch documentation build configuration file, created by
# sphinx-quickstart on Fri Dec 23 13:31:47 2016.
#
# This file is execfile()d with the current directory set to its
# containing dir.
#
# Note that not all possible configuration values are present in this
# autogenerated file.
#
# All configuration values have a default; values that are commented out
# serve to show the default.

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))

import os

import pytorch_sphinx_theme
import torchvision


# -- General configuration ------------------------------------------------

# Required version of sphinx is set from docs/requirements.txt

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx.ext.mathjax",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.duration",
    "sphinx_gallery.gen_gallery",
    "sphinx_copybutton",
]

sphinx_gallery_conf = {
    "examples_dirs": "../../gallery/",  # path to your example scripts
    "gallery_dirs": "auto_examples",  # path to where to save gallery generated output
    "backreferences_dir": "gen_modules/backreferences",
    "doc_module": ("torchvision",),
}

napoleon_use_ivar = True
napoleon_numpy_docstring = False
napoleon_google_docstring = True


# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
#
source_suffix = {
    ".rst": "restructuredtext",
}

# The master toctree document.
master_doc = "index"

# General information about the project.
project = "Torchvision"
copyright = "2017-present, Torch Contributors"
author = "Torch Contributors"

# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.
#
# The short X.Y version.
version = "main (" + torchvision.__version__ + " )"
# The full version, including alpha/beta/rc tags.
release = "main"
VERSION = os.environ.get("VERSION", None)
if VERSION:
    # Turn 1.11.0aHASH into 1.11 (major.minor only)
    version = ".".join(version.split(".")[:2])
    html_title = " ".join((project, version, "documentation"))
    release = version


# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = None

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This patterns also effect to html_static_path and html_extra_path
exclude_patterns = []

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "sphinx"

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = True


# -- Options for HTML output ----------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "pytorch_sphinx_theme"
html_theme_path = [pytorch_sphinx_theme.get_html_theme_path()]

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
#
html_theme_options = {
    "collapse_navigation": False,
    "display_version": True,
    "logo_only": True,
    "pytorch_project": "docs",
    "navigation_with_keys": True,
    "analytics_id": "UA-117752657-2",
}

html_logo = "_static/img/pytorch-logo-dark.svg"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# TODO: remove this once https://github.com/pytorch/pytorch_sphinx_theme/issues/125 is fixed
html_css_files = [
    "css/custom_torchvision.css",
]

# -- Options for HTMLHelp output ------------------------------------------

# Output file base name for HTML help builder.
htmlhelp_basename = "PyTorchdoc"


autosummary_generate = True


# -- Options for LaTeX output ---------------------------------------------
latex_elements = {
    # The paper size ('letterpaper' or 'a4paper').
    #
    # 'papersize': 'letterpaper',
    # The font size ('10pt', '11pt' or '12pt').
    #
    # 'pointsize': '10pt',
    # Additional stuff for the LaTeX preamble.
    #
    # 'preamble': '',
    # Latex figure (float) alignment
    #
    # 'figure_align': 'htbp',
}


# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title,
#  author, documentclass [howto, manual, or own class]).
latex_documents = [
    (master_doc, "pytorch.tex", "torchvision Documentation", "Torch Contributors", "manual"),
]


# -- Options for manual page output ---------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [(master_doc, "torchvision", "torchvision Documentation", [author], 1)]


# -- Options for Texinfo output -------------------------------------------

# Grouping the document tree into Texinfo files. List of tuples
# (source start file, target name, title, author,
#  dir menu entry, description, category)
texinfo_documents = [
    (
        master_doc,
        "torchvision",
        "torchvision Documentation",
        author,
        "torchvision",
        "One line description of project.",
        "Miscellaneous",
    ),
]


# Example configuration for intersphinx: refer to the Python standard library.
intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "torch": ("https://pytorch.org/docs/stable/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "PIL": ("https://pillow.readthedocs.io/en/stable/", None),
    "matplotlib": ("https://matplotlib.org/stable/", None),
}

# -- A patch that prevents Sphinx from cross-referencing ivar tags -------
# See http://stackoverflow.com/a/41184353/3343043

from docutils import nodes
from sphinx import addnodes
from sphinx.util.docfields import TypedField


def patched_make_field(self, types, domain, items, **kw):
    # `kw` catches `env=None` needed for newer sphinx while maintaining
    #  backwards compatibility when passed along further down!

    # type: (list, unicode, tuple) -> nodes.field  # noqa: F821
    def handle_item(fieldarg, content):
        par = nodes.paragraph()
        par += addnodes.literal_strong("", fieldarg)  # Patch: this line added
        # par.extend(self.make_xrefs(self.rolename, domain, fieldarg,
        #                           addnodes.literal_strong))
        if fieldarg in types:
            par += nodes.Text(" (")
            # NOTE: using .pop() here to prevent a single type node to be
            # inserted twice into the doctree, which leads to
            # inconsistencies later when references are resolved
            fieldtype = types.pop(fieldarg)
            if len(fieldtype) == 1 and isinstance(fieldtype[0], nodes.Text):
                typename = "".join(n.astext() for n in fieldtype)
                typename = typename.replace("int", "python:int")
                typename = typename.replace("long", "python:long")
                typename = typename.replace("float", "python:float")
                typename = typename.replace("type", "python:type")
                par.extend(self.make_xrefs(self.typerolename, domain, typename, addnodes.literal_emphasis, **kw))
            else:
                par += fieldtype
            par += nodes.Text(")")
        par += nodes.Text(" -- ")
        par += content
        return par

    fieldname = nodes.field_name("", self.label)
    if len(items) == 1 and self.can_collapse:
        fieldarg, content = items[0]
        bodynode = handle_item(fieldarg, content)
    else:
        bodynode = self.list_type()
        for fieldarg, content in items:
            bodynode += nodes.list_item("", handle_item(fieldarg, content))
    fieldbody = nodes.field_body("", bodynode)
    return nodes.field("", fieldname, fieldbody)


TypedField.make_field = patched_make_field


def inject_minigalleries(app, what, name, obj, options, lines):
    """Inject a minigallery into a docstring.

    This avoids having to manually write the .. minigallery directive for every item we want a minigallery for,
    as it would be easy to miss some.

    This callback is called after the .. auto directives (like ..autoclass) have been processed,
    and modifies the lines parameter inplace to add the .. minigallery that will show which examples
    are using which object.

    It's a bit hacky, but not *that* hacky when you consider that the recommended way is to do pretty much the same,
    but instead with templates using autosummary (which we don't want to use):
    (https://sphinx-gallery.github.io/stable/configuration.html#auto-documenting-your-api-with-links-to-examples)

    For docs on autodoc-process-docstring, see the autodoc docs:
    https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html
    """

    if what in ("class", "function"):
        lines.append(f".. minigallery:: {name}")
        lines.append(f"    :add-heading: Examples using ``{name.split('.')[-1]}``:")
        # avoid heading entirely to avoid warning. As a bonud it actually renders better
        lines.append("    :heading-level: 9")
        lines.append("\n")


def generate_table():

    import torchvision.models as M
    from tabulate import tabulate
    import textwrap

    # TODO: this is ugly af and incorrect. We'll need an automatic way to
    # retrieve weight enums for each section, or manually list them.
    weight_enums = [getattr(M, name) for name in dir(M) if name.endswith("Weights")]
    weights = [w for weight_enum in weight_enums for w in weight_enum if "acc@1" in w.meta]

    column_names = ("**Weight**", "**Acc@1**", "**Acc@5**", "**Params**", "**Recipe**")
    content = [
        (str(w), w.meta["acc@1"], w.meta["acc@5"], f"{w.meta['num_params']:e}", f"`link <{w.meta['recipe']}>`__")
        for w in weights
    ]
    table = tabulate(content, headers=column_names, tablefmt="rst")
    print(table)

    with open("generated/classification_table.rst", "w") as table_file:
        table_file.write(".. table::\n")
        table_file.write("    :widths: 100 10 10 20 10\n\n")
        table_file.write(f"{textwrap.indent(table, ' ' * 4)}\n\n")



generate_table()

def setup(app):
    app.connect("autodoc-process-docstring", inject_minigalleries)
