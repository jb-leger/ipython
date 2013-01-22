"""Base classes for the notebook conversion pipeline.

This module defines Converter, from which all objects designed to implement
a conversion of IPython notebooks to some other format should inherit.
"""
#-----------------------------------------------------------------------------
# Copyright (c) 2012, the IPython Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

from __future__ import print_function, absolute_import

# Stdlib imports
import io
import os
import re
from IPython.utils import path

from jinja2 import Environment, FileSystemLoader
env = Environment(
        loader=FileSystemLoader([
            './templates/',
            './templates/skeleton/',
            ]),
        extensions=['jinja2.ext.loopcontrols']
        )

texenv = Environment(
        loader=FileSystemLoader([
            './templates/tex/',
            './templates/skeleton/tex/',
            ]),
        extensions=['jinja2.ext.loopcontrols']
        )

# IPython imports
from IPython.nbformat import current as nbformat
from IPython.config.configurable import Configurable
from IPython.utils.traitlets import ( Unicode, Any, List, Bool)

# Our own imports
from IPython.utils.text import indent
from .utils import remove_ansi
from markdown import markdown
from .utils import highlight, ansi2html
from .utils import markdown2latex
#-----------------------------------------------------------------------------
# Class declarations
#-----------------------------------------------------------------------------
def rm_fake(strng):
    return strng.replace('/files/', '')

class ConversionException(Exception):
    pass


def python_comment(string):
    return '# '+'\n# '.join(string.split('\n'))



def header_body():
    """Return the body of the header as a list of strings."""

    from pygments.formatters import HtmlFormatter

    header = []
    static = os.path.join(path.get_ipython_package_dir(),
    'frontend', 'html', 'notebook', 'static',
    )
    here = os.path.split(os.path.realpath(__file__))[0]
    css = os.path.join(static, 'css')
    for sheet in [
        # do we need jquery and prettify?
        # os.path.join(static, 'jquery', 'css', 'themes', 'base',
        # 'jquery-ui.min.css'),
        # os.path.join(static, 'prettify', 'prettify.css'),
        os.path.join(css, 'boilerplate.css'),
        os.path.join(css, 'fbm.css'),
        os.path.join(css, 'notebook.css'),
        os.path.join(css, 'renderedhtml.css'),
        # our overrides:
        os.path.join(here, '..', 'css', 'static_html.css'),
    ]:

        with io.open(sheet, encoding='utf-8') as f:
            s = f.read()
            header.append(s)

    pygments_css = HtmlFormatter().get_style_defs('.highlight')
    header.append(pygments_css)
    return header

# todo, make the key part configurable.
def _new_figure(data, fmt, count):
    """Create a new figure file in the given format.

    Returns a path relative to the input file.
    """
    figname = '_fig_%02i.%s' % (count, fmt)

    # Binary files are base64-encoded, SVG is already XML
    if fmt in ('png', 'jpg', 'pdf'):
        data = data.decode('base64')

    return figname,data




inlining = {}
inlining['css'] = header_body()

LATEX_SUBS = (
    (re.compile(r'\\'), r'\\textbackslash'),
    (re.compile(r'([{}_#%&$])'), r'\\\1'),
    (re.compile(r'~'), r'\~{}'),
    (re.compile(r'\^'), r'\^{}'),
    (re.compile(r'"'), r"''"),
    (re.compile(r'\.\.\.+'), r'\\ldots'),
)

def escape_tex(value):
    newval = value
    for pattern, replacement in LATEX_SUBS:
        newval = pattern.sub(replacement, newval)
    return newval

texenv.block_start_string = '((*'
texenv.block_end_string = '*))'
texenv.variable_start_string = '((('
texenv.variable_end_string = ')))'
texenv.comment_start_string = '((='
texenv.comment_end_string = '=))'
texenv.filters['escape_tex'] = escape_tex

def cell_preprocessor(function):
    """ wrap a function to be executed on all cells of a notebook

    wrapped function  parameters :
    cell  : the cell
    other : external resources
    index : index of the cell
    """
    def wrappedfunc(nb,other):
        for worksheet in nb.worksheets :
            for index, cell in enumerate(worksheet.cells):
                worksheet.cells[index],other= function(cell,other,index)
        return nb,other
    return wrappedfunc



@cell_preprocessor
def haspyout_transformer(cell, other, count):
    """
    Add a haspyout flag to cell that have it
    
    Easier for templating, where you can't know in advance
    wether to write the out prompt

    """
    cell.type = cell.cell_type
    cell.haspyout = False
    for out in cell.get('outputs', []):
        if out.output_type == 'pyout':
            cell.haspyout = True
            break
    return cell,other


@cell_preprocessor
def extract_figure_transformer(cell,other,count):
    for i,out in enumerate(cell.get('outputs', [])):
        for type in ['html', 'pdf', 'svg', 'latex', 'png', 'jpg', 'jpeg']:
            if out.hasattr(type):
                figname,data = _new_figure(out[type], type,count)
                cell.outputs[i][type] = figname
                out['key_'+type] = figname
                other[figname] = data
                count = count+1
    return cell,other


class ConverterTemplate(Configurable):
    """ A Jinja2 base converter templates"""

    display_data_priority = List(['html', 'pdf', 'svg', 'latex', 'png', 'jpg', 'jpeg' , 'text'],
            config=True,
              help= """
                    A list of ast.NodeTransformer subclass instances, which will be applied
                    to user input before code is run.
                    """
            )

    extract_figures = Bool(False,
            config=True,
              help= """
                    wether to remove figure data from ipynb and store them in auxiliary
                    dictionnary
                    """
            )

    tex_environement = Bool(False,
            config=True,
            help=""" is this a tex environment or not """)

    template_file = Unicode('',
            config=True,
            help=""" whetever """ )
    #-------------------------------------------------------------------------
    # Instance-level attributes that are set in the constructor for this
    # class.
    #-------------------------------------------------------------------------
    infile = Any()


    infile_dir = Unicode()

    def filter_data_type(self,output):
        for fmt in self.display_data_priority:
            if fmt in output:
                return [fmt]

    def __init__(self, preprocessors=[], config=None, **kw):
        """
        tplfile : jinja template file to process.

        config: the Configurable confg object to pass around

        preprocessors: list of function to run on ipynb json data before conversion
        to extract/inline file,

        """
        super(ConverterTemplate, self).__init__(config=config, **kw)
        self.env = texenv  if self.tex_environement else env
        self.ext = '.tplx' if self.tex_environement else '.tpl'
        self.nb = None
        self.preprocessors = preprocessors
        self.preprocessors.append(haspyout_transformer)
        if self.extract_figures:
            self.preprocessors.append(extract_figure_transformer)

        self.env.filters['filter_data_type'] = self.filter_data_type
        self.env.filters['pycomment'] = python_comment
        self.env.filters['indent'] = indent
        self.env.filters['rm_fake'] = rm_fake
        self.env.filters['rm_ansi'] = remove_ansi
        self.env.filters['markdown'] = markdown
        self.env.filters['highlight'] = highlight
        self.env.filters['ansi2html'] = ansi2html
        self.env.filters['markdown2latex'] = markdown2latex

        self.template = self.env.get_template(self.template_file+self.ext)



    def process(self):
        """
        preprocess the notebook json for easier use with the templates.
        will call all the `preprocessor`s in order before returning it.
        """
        nb = self.nb

        # dict of 'resources' that could be made by the preprocessors
        # like key/value data to extract files from ipynb like in latex conversion
        resources = {}

        for preprocessor in self.preprocessors:
            nb,resources = preprocessor(nb,resources)

        return nb, resources

    def convert(self):
        """ convert the ipynb file

        return both the converted ipynb file and a dict containing potential
        other resources
        """
        nb,resources = self.process()
        return self.template.render(nb=nb, inlining=inlining), resources


    def read(self, filename):
        "read and parse notebook into NotebookNode called self.nb"
        with io.open(filename) as f:
            self.nb = nbformat.read(f, 'json')

