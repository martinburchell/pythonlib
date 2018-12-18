#!/usr/bin/env python
# cardinal_pythonlib/sphinxtools.py

"""
===============================================================================

    Original code copyright (C) 2009-2018 Rudolf Cardinal (rudolf@pobox.com).

    This file is part of cardinal_pythonlib.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

===============================================================================

**Functions to help with Sphinx, in particular the generation of autodoc
files.**

Rationale: if you want Sphinx ``autodoc`` code to appear as "one module per
Sphinx page" (which I normally do), you need one ``.rst`` file per module.

"""

from enum import Enum
from fnmatch import fnmatch
import glob
import logging
from os.path import (
    abspath, basename, dirname, exists, expanduser, isdir, isfile, join,
    relpath, sep, splitext
)
from typing import Dict, Iterable, List, Union

from cardinal_pythonlib.fileops import mkdir_p, relative_filename_within_dir
from cardinal_pythonlib.logs import BraceStyleAdapter
from cardinal_pythonlib.reprfunc import auto_repr
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

log = BraceStyleAdapter(logging.getLogger(__name__))


# =============================================================================
# Constants
# =============================================================================

AUTOGENERATED_COMMENT = ".. THIS FILE IS AUTOMATICALLY GENERATED. DO NOT EDIT."
DEFAULT_INDEX_TITLE = "Automatic documentation of source code"
DEFAULT_SKIP_GLOBS = ["__init__.py"]
EXT_PYTHON = ".py"
EXT_RST = ".rst"
CODE_TYPE_NONE = "none"


class AutodocMethod(Enum):
    """
    Enum to specify the method of autodocumenting a file.
    """
    BEST = 0
    CONTENTS = 1
    AUTOMODULE = 2


# =============================================================================
# Helper functions
# =============================================================================

def rst_underline(heading: str, underline_char: str) -> str:
    """
    Underlines a heading for RST files.

    Args:
        heading: text to underline
        underline_char: character to use

    Returns:
        underlined heading, over two lines (without a final terminating
        newline)
    """
    assert "\n" not in heading
    assert len(underline_char) == 1
    return heading + "\n" + (underline_char * len(heading))


def fail(msg: str) -> None:
    log.critical(msg)
    raise RuntimeError(msg)


def write_if_allowed(filename: str,
                     content: str,
                     overwrite: bool = False,
                     mock: bool = False) -> None:
    """
    Writes the contents to a file, if permitted.

    Args:
        filename: filename to write
        content: contents to write
        overwrite: permit overwrites?
        mock: pretend to write, but don't

    Raises:
        RuntimeError: if file exists but overwriting not permitted
    """
    # Check we're allowed
    if not overwrite and exists(filename):
        fail("File exists, not overwriting: {!r}".format(filename))

    # Make the directory, if necessary
    directory = dirname(filename)
    if not mock:
        mkdir_p(directory)

    # Write the file
    log.info("Writing to {!r}", filename)
    if mock:
        log.warning("Skipping writes as in mock mode")
    else:
        with open(filename, "wt") as outfile:
            outfile.write(content)


# =============================================================================
# FileToAutodocument
# =============================================================================

class FileToAutodocument(object):
    """
    Class representing a file to document automatically via Sphinx autodoc.

    Example:
        
    .. code-block:: python

        import logging
        from cardinal_pythonlib.logs import *
        from cardinal_pythonlib.sphinxtools import *
        main_only_quicksetup_rootlogger(level=logging.DEBUG)
        
        f = FileToAutodocument(
            source_filename="~/Documents/code/cardinal_pythonlib/cardinal_pythonlib/sphinxtools.py",
            project_root_dir="~/Documents/code/cardinal_pythonlib",
            target_rst_filename="~/Documents/code/cardinal_pythonlib/docs/source/autodoc/sphinxtools.rst",
        )
        print(f)
        f.source_extension
        f.is_python
        f.source_filename_rel_project_root
        f.rst_dir
        f.source_filename_rel_rst_file
        f.rst_filename_rel_project_root
        f.rst_filename_rel_autodoc_index(
            "~/Documents/code/cardinal_pythonlib/docs/source/autodoc/_index.rst")
        f.python_module_name
        f.pygments_code_type
        print(f.rst_content(prefix=".. Hello!"))
        print(f.rst_content(prefix=".. Hello!", method=AutodocMethod.CONTENTS))
        f.write_rst(prefix=".. Hello!")

    """  # noqa

    def __init__(self,
                 source_filename: str,
                 project_root_dir: str,
                 target_rst_filename: str,
                 method: AutodocMethod = AutodocMethod.BEST,
                 python_package_root_dir: str = None,
                 source_rst_title_style_python: bool = True,
                 pygments_language_override: Dict[str, str] = None) -> None:
        """
        Args:
            source_filename: source file (e.g. Python, C++, XML file) to
                document
            project_root_dir: root directory of the whole project
            target_rst_filename: filenamd of an RST file to write that will
                document the source file
            method: instance of :class:`AutodocMethod`; for example, should we
                ask Sphinx's ``autodoc`` to read docstrings and build us a
                pretty page, or just include the contents with syntax
                highlighting?
            python_package_root_dir: if your Python modules live in a directory
                other than ``project_root_dir``, specify it here
            source_rst_title_style_python: if ``True`` and the file is a Python
                file and ``method == AutodocMethod.AUTOMODULE``, the heading
                used will be in the style of a Python module, ``x.y.z``.
                Otherwise, it will be a path (``x/y/z``).
            pygments_language_override: if specified, a dictionary mapping
                file extensions to Pygments languages (for example: a ``.pro``
                file will be autodetected as Prolog, but you might want to
                map that to ``none`` for Qt project files).
        """
        self.source_filename = abspath(expanduser(source_filename))
        self.project_root_dir = abspath(expanduser(project_root_dir))
        self.target_rst_filename = abspath(expanduser(target_rst_filename))
        self.method = method
        self.source_rst_title_style_python = source_rst_title_style_python
        self.python_package_root_dir = (
            abspath(expanduser(python_package_root_dir))
            if python_package_root_dir else self.project_root_dir
        )
        self.pygments_language_override = pygments_language_override or {}  # type: Dict[str, str]  # noqa
        assert isfile(self.source_filename), (
            "Not a file: source_filename={!r}".format(self.source_filename))
        assert isdir(self.project_root_dir), (
            "Not a directory: project_root_dir={!r}".format(
                self.project_root_dir))
        assert relative_filename_within_dir(
            filename=self.source_filename,
            directory=self.project_root_dir
        ), (
            "Source file {!r} is not within project directory {!r}".format(
                self.source_filename, self.project_root_dir)
        )
        assert relative_filename_within_dir(
            filename=self.python_package_root_dir,
            directory=self.project_root_dir
        ), (
            "Python root {!r} is not within project directory {!r}".format(
                self.python_package_root_dir, self.project_root_dir)
        )
        assert isinstance(method, AutodocMethod)

    def __repr__(self) -> str:
        return auto_repr(self)

    @property
    def source_extension(self) -> str:
        """
        Returns the extension of the source filename.
        """
        return splitext(self.source_filename)[1]

    @property
    def is_python(self) -> bool:
        """
        Is the source file a Python file?
        """
        return self.source_extension == EXT_PYTHON

    @property
    def source_filename_rel_project_root(self) -> str:
        """
        Returns the name of the source filename, relative to the project root.
        Used to calculate file titles.
        """
        return relpath(self.source_filename, start=self.project_root_dir)

    @property
    def source_filename_rel_python_root(self) -> str:
        """
        Returns the name of the source filename, relative to the Python package
        root. Used to calculate the name of Python modules.
        """
        return relpath(self.source_filename,
                       start=self.python_package_root_dir)

    @property
    def rst_dir(self) -> str:
        """
        Returns the directory of the target RST file.
        """
        return dirname(self.target_rst_filename)

    @property
    def source_filename_rel_rst_file(self) -> str:
        """
        Returns the source filename as seen from the RST filename that we
        will generate. Used for ``.. include::`` commands.
        """
        return relpath(self.source_filename, start=self.rst_dir)

    @property
    def rst_filename_rel_project_root(self) -> str:
        """
        Returns the filename of the target RST file, relative to the project
        root directory. Used for labelling the RST file itself.
        """
        return relpath(self.target_rst_filename, start=self.project_root_dir)

    def rst_filename_rel_autodoc_index(self, index_filename: str) -> str:
        """
        Returns the filename of the target RST file, relative to a specified
        index file. Used to make the index refer to the RST.
        """
        index_dir = dirname(abspath(expanduser(index_filename)))
        return relpath(self.target_rst_filename, start=index_dir)

    @property
    def python_module_name(self) -> str:
        """
        Returns the name of the Python module that this instance refers to,
        in dotted Python module notation, or a blank string if it doesn't.
        """
        if not self.is_python:
            return ""
        filepath = self.source_filename_rel_python_root
        dirs_and_base = splitext(filepath)[0]
        dir_and_file_parts = dirs_and_base.split(sep)
        return ".".join(dir_and_file_parts)

    @property
    def pygments_language(self) -> str:
        """
        Returns the code type annotation for Pygments; e.g. ``python`` for
        Python, ``cpp`` for C++, etc.
        """
        extension = splitext(self.source_filename)[1]
        if extension in self.pygments_language_override:
            return self.pygments_language_override[extension]
        try:
            lexer = get_lexer_for_filename(self.source_filename)  # type: Lexer
            return lexer.name
        except ClassNotFound:
            log.warning("Don't know Pygments code type for extension {!r}",
                        self.source_extension)
            return CODE_TYPE_NONE

    def rst_content(self,
                    prefix: str = "",
                    suffix: str = "",
                    heading_underline_char: str = "=",
                    method: AutodocMethod = None) -> str:
        """
        Returns the text contents of an RST file that will automatically
        document our source file.

        Args:
            prefix: prefix, e.g. RST copyright comment
            suffix: suffix, after the part we're creating
            heading_underline_char: RST character to use to underline the
                heading
            method: optional method to override ``self.method``; see
                constructor

        Returns:
            the RST contents
        """
        spacer = "    "
        # Choose our final method
        if method is None:
            method = self.method
        is_python = self.is_python
        if method == AutodocMethod.BEST:
            if is_python:
                method = AutodocMethod.AUTOMODULE
            else:
                method = AutodocMethod.CONTENTS
        elif method == AutodocMethod.AUTOMODULE:
            if not is_python:
                method = AutodocMethod.CONTENTS

        # Write the instruction
        if method == AutodocMethod.AUTOMODULE:
            if self.source_rst_title_style_python:
                title = self.python_module_name
            else:
                title = self.source_filename_rel_project_root
            instruction = ".. automodule:: {modulename}\n    :members:".format(
                modulename=self.python_module_name
            )
        elif method == AutodocMethod.CONTENTS:
            title = self.source_filename_rel_project_root
            # Using ".. include::" with options like ":code: python" doesn't
            # work properly; everything comes out as Python.
            # Instead, see http://www.sphinx-doc.org/en/1.4.9/markup/code.html;
            # we need ".. literalinclude::" with ":language: LANGUAGE".

            instruction = (
                ".. literalinclude:: {filename}\n"
                "{spacer}:language: {language}".format(
                    filename=self.source_filename_rel_rst_file,
                    spacer=spacer,
                    language=self.pygments_language
                )
            )
        else:
            raise ValueError("Bad method!")

        # Create the whole file
        content = """
.. {filename}
        
{AUTOGENERATED_COMMENT}

{prefix}

{underlined_title}

{instruction}

{suffix}
        """.format(
            filename=self.rst_filename_rel_project_root,
            AUTOGENERATED_COMMENT=AUTOGENERATED_COMMENT,
            prefix=prefix,
            underlined_title=rst_underline(
                title, underline_char=heading_underline_char),
            instruction=instruction,
            suffix=suffix,
        ).strip() + "\n"
        return content

    def write_rst(self,
                  prefix: str = "",
                  suffix: str = "",
                  heading_underline_char: str = "=",
                  method: AutodocMethod = None,
                  overwrite: bool = False,
                  mock: bool = False) -> None:
        """
        Writes the RST file to our destination RST filename, making any
        necessary directories.

        Args:
            prefix: as for :func:`rst_content`
            suffix: as for :func:`rst_content`
            heading_underline_char: as for :func:`rst_content`
            method: as for :func:`rst_content`
            overwrite: overwrite the file if it exists already?
            mock: pretend to write, but don't
        """
        content = self.rst_content(
            prefix=prefix,
            suffix=suffix,
            heading_underline_char=heading_underline_char,
            method=method
        )
        write_if_allowed(self.target_rst_filename, content,
                         overwrite=overwrite, mock=mock)


# =============================================================================
# AutodocIndex
# =============================================================================

class AutodocIndex(object):
    """
    Class to make an RST file that indexes others.

    Example:

    .. code-block:: python

        import logging
        from cardinal_pythonlib.logs import *
        from cardinal_pythonlib.sphinxtools import *
        main_only_quicksetup_rootlogger(level=logging.INFO)
        
        # Example where one index contains another:
        
        subidx = AutodocIndex(
            index_filename="~/Documents/code/cardinal_pythonlib/docs/source/autodoc/_index2.rst",
            highest_code_dir="~/Documents/code/cardinal_pythonlib",
            project_root_dir="~/Documents/code/cardinal_pythonlib",
            autodoc_rst_root_dir="~/Documents/code/cardinal_pythonlib/docs/source/autodoc",
            source_filenames_or_globs="~/Documents/code/cardinal_pythonlib/docs/*.py",
        )
        idx = AutodocIndex(
            index_filename="~/Documents/code/cardinal_pythonlib/docs/source/autodoc/_index.rst",
            highest_code_dir="~/Documents/code/cardinal_pythonlib",
            project_root_dir="~/Documents/code/cardinal_pythonlib",
            autodoc_rst_root_dir="~/Documents/code/cardinal_pythonlib/docs/source/autodoc",
            source_filenames_or_globs="~/Documents/code/cardinal_pythonlib/cardinal_pythonlib/*.py",
        )
        idx.add_index(subidx)
        print(idx.index_content())
        idx.write_index_and_rst_files(overwrite=True, mock=True)
        
        # Example with a flat index:
        
        flatidx = AutodocIndex(
            index_filename="~/Documents/code/cardinal_pythonlib/docs/source/autodoc/_index.rst",
            highest_code_dir="~/Documents/code/cardinal_pythonlib/cardinal_pythonlib",
            project_root_dir="~/Documents/code/cardinal_pythonlib",
            autodoc_rst_root_dir="~/Documents/code/cardinal_pythonlib/docs/source/autodoc",
            source_filenames_or_globs="~/Documents/code/cardinal_pythonlib/cardinal_pythonlib/*.py",
        )
        print(flatidx.index_content())
        flatidx.write_index_and_rst_files(overwrite=True, mock=True)

    """  # noqa
    def __init__(self,
                 index_filename: str,
                 project_root_dir: str,
                 autodoc_rst_root_dir: str,
                 highest_code_dir: str,
                 python_package_root_dir: str = None,
                 source_filenames_or_globs: Union[str, Iterable[str]] = None,
                 index_heading_underline_char: str = "-",
                 source_rst_heading_underline_char: str = "~",
                 title: str = DEFAULT_INDEX_TITLE,
                 recursive: bool = True,
                 skip_globs: List[str] = None,
                 toctree_maxdepth: int = 1,
                 method: AutodocMethod = AutodocMethod.BEST,
                 rst_prefix: str = "",
                 rst_suffix: str = "",
                 source_rst_title_style_python: bool = True,
                 pygments_language_override: Dict[str, str] = None) -> None:
        """
        Args:
            index_filename: filename of the index ``.RST`` (ReStructured Text)
                file to create
            project_root_dir: top-level directory for the whole project
            autodoc_rst_root_dir: directory within which all automatically
                generated ``.RST`` files (each to document a specific source
                file) will be placed. A directory hierarchy within this
                directory will be created, reflecting the structure of the
                code relative to ``highest_code_dir`` (q.v.).
            highest_code_dir: the "lowest" directory such that all code is
                found within it; the directory structure within
                ``autodoc_rst_root_dir`` is to ``.RST`` files what the
                directory structure is of the source files, relative to
                ``highest_code_dir``.
            python_package_root_dir: if your Python modules live in a directory
                other than ``project_root_dir``, specify it here
            source_filenames_or_globs: optional string, or list of strings,
                each describing a file or glob-style file specification; these
                are the source filenames to create automatic RST` for. If you
                don't specify them here, you can use :func:`add_source_files`.
                To add sub-indexes, use :func:`add_index` and
                :func:`add_indexes`.
            index_heading_underline_char: the character used to underline the
                title in the index file
            source_rst_heading_underline_char: the character used to underline
                the heading in each of the source files
            title: title for the index
            recursive: use :func:`glob.glob` in recursive mode?
            skip_globs: list of file names or file specifications to skip; e.g.
                ``['__init__.py']``
            toctree_maxdepth: ``maxdepth`` parameter for the ``toctree``
                command generated in the index file
            method: see :class:`FileToAutodocument`
            rst_prefix: optional RST content (e.g. copyright comment) to put
                early on in each of the RST files
            rst_suffix: optional RST content to put late on in each of the RST
                files
            source_rst_title_style_python: make the individual RST files use
                titles in the style of Python modules, ``x.y.z``, rather than
                path style (``x/y/z``); path style will be used for non-Python
                files in any case.
            pygments_language_override: if specified, a dictionary mapping
                file extensions to Pygments languages (for example: a ``.pro``
                file will be autodetected as Prolog, but you might want to
                map that to ``none`` for Qt project files).

        """
        assert index_filename
        assert project_root_dir
        assert autodoc_rst_root_dir
        assert isinstance(toctree_maxdepth, int)
        assert isinstance(method, AutodocMethod)

        self.index_filename = abspath(expanduser(index_filename))
        self.title = title
        self.project_root_dir = abspath(expanduser(project_root_dir))
        self.autodoc_rst_root_dir = abspath(expanduser(autodoc_rst_root_dir))
        self.highest_code_dir = abspath(expanduser(highest_code_dir))
        self.python_package_root_dir = (
            abspath(expanduser(python_package_root_dir))
            if python_package_root_dir else self.project_root_dir
        )
        self.index_heading_underline_char = index_heading_underline_char
        self.source_rst_heading_underline_char = source_rst_heading_underline_char  # noqa
        self.recursive = recursive
        self.skip_globs = skip_globs if skip_globs is not None else DEFAULT_SKIP_GLOBS  # noqa
        self.toctree_maxdepth = toctree_maxdepth
        self.method = method
        self.rst_prefix = rst_prefix
        self.rst_suffix = rst_suffix
        self.source_rst_title_style_python = source_rst_title_style_python
        self.pygments_language_override = pygments_language_override or {}  # type: Dict[str, str]  # noqa

        assert isdir(self.project_root_dir), (
            "Not a directory: project_root_dir={!r}".format(
                self.project_root_dir))
        assert relative_filename_within_dir(
            filename=self.index_filename,
            directory=self.project_root_dir
        ), (
            "Index file {!r} is not within project directory {!r}".format(
                self.index_filename, self.project_root_dir)
        )
        assert relative_filename_within_dir(
            filename=self.highest_code_dir,
            directory=self.project_root_dir
        ), (
            "Highest code directory {!r} is not within project directory "
            "{!r}".format(self.highest_code_dir, self.project_root_dir)
        )
        assert relative_filename_within_dir(
            filename=self.autodoc_rst_root_dir,
            directory=self.project_root_dir
        ), (
            "Autodoc RST root directory {!r} is not within project "
            "directory {!r}".format(
                self.autodoc_rst_root_dir, self.project_root_dir)
        )
        assert isinstance(method, AutodocMethod)
        assert isinstance(recursive, bool)

        self.files_to_index = []  # type: List[Union[FileToAutodocument, AutodocIndex]]  # noqa
        if source_filenames_or_globs:
            self.add_source_files(source_filenames_or_globs)

    def __repr__(self) -> str:
        return auto_repr(self)

    def add_source_files(
            self,
            source_filenames_or_globs: Union[str, List[str]],
            method: AutodocMethod = None,
            recursive: bool = None,
            source_rst_title_style_python: bool = None,
            pygments_language_override: Dict[str, str] = None) -> None:
        """
        Adds source files to the index.

        Args:
            source_filenames_or_globs: string containing a filename or a
                glob, describing the file(s) to be added, or a list of such
                strings
            method: optional method to override ``self.method``
            recursive: use :func:`glob.glob` in recursive mode? (If ``None``,
                the default, uses the version from the constructor.)
            source_rst_title_style_python: optional to override
                ``self.source_rst_title_style_python``
            pygments_language_override: optional to override
                ``self.pygments_language_override``
        """
        if not source_filenames_or_globs:
            return

        if method is None:
            # Use the default
            method = self.method
        if recursive is None:
            recursive = self.recursive
        if source_rst_title_style_python is None:
            source_rst_title_style_python = self.source_rst_title_style_python
        if pygments_language_override is None:
            pygments_language_override = self.pygments_language_override

        # Get a sorted list of filenames
        final_filenames = self.get_sorted_source_files(
            source_filenames_or_globs,
            recursive=recursive
        )

        # Process that sorted list
        for source_filename in final_filenames:
            self.files_to_index.append(FileToAutodocument(
                source_filename=source_filename,
                project_root_dir=self.project_root_dir,
                python_package_root_dir=self.python_package_root_dir,
                target_rst_filename=self.specific_file_rst_filename(
                    source_filename
                ),
                method=method,
                source_rst_title_style_python=source_rst_title_style_python,
                pygments_language_override=pygments_language_override,
            ))

    def get_sorted_source_files(
            self,
            source_filenames_or_globs: Union[str, List[str]],
            recursive: bool = True) -> List[str]:
        """
        Returns a sorted list of filenames to process, from a filename,
        a glob string, or a list of filenames/globs.

        Args:
            source_filenames_or_globs: filename/glob, or list of them
            recursive: use :func:`glob.glob` in recursive mode?

        Returns:
            sorted list of files to process
        """
        if isinstance(source_filenames_or_globs, str):
            source_filenames_or_globs = [source_filenames_or_globs]
        final_filenames = []  # type: List[str]
        for sfg in source_filenames_or_globs:
            sfg_expanded = expanduser(sfg)
            log.debug("Looking for: {!r}", sfg_expanded)
            for filename in glob.glob(sfg_expanded, recursive=recursive):
                log.debug("Trying: {!r}", filename)
                if self.should_exclude(filename):
                    log.info("Skipping file {!r}", filename)
                    continue
                final_filenames.append(filename)
        final_filenames.sort()
        return final_filenames

    @staticmethod
    def filename_matches_glob(filename: str, globtext: str) -> bool:
        """
        The ``glob.glob`` function doesn't do exclusion very well. We don't
        want to have to specify root directories for exclusion patterns. We
        don't want to have to trawl a massive set of files to find exclusion
        files. So let's implement a glob match.

        Args:
            filename: filename
            globtext: glob

        Returns:
            does the filename match the glob?

        See also:

        - https://stackoverflow.com/questions/20638040/glob-exclude-pattern

        """
        # Quick check on basename-only matching
        if fnmatch(filename, globtext):
            log.debug("{!r} matches {!r}", filename, globtext)
            return True
        bname = basename(filename)
        if fnmatch(bname, globtext):
            log.debug("{!r} matches {!r}", bname, globtext)
            return True
        # Directory matching: is actually accomplished by the code above!
        # Otherwise:
        return False

    def should_exclude(self, filename) -> bool:
        """
        Should we exclude this file from consideration?
        """
        for skip_glob in self.skip_globs:
            if self.filename_matches_glob(filename, skip_glob):
                return True
        return False

    def add_index(self, index: "AutodocIndex") -> None:
        """
        Add a sub-index file to this index.

        Args:
            index: index file to add, as an instance of :class:`AutodocIndex`
        """
        self.files_to_index.append(index)

    def add_indexes(self, indexes: List["AutodocIndex"]) -> None:
        """
        Adds multiple sub-indexes to this index.

        Args:
            indexes: list of sub-indexes
        """
        for index in indexes:
            self.add_index(index)

    def specific_file_rst_filename(self, source_filename: str) -> str:
        """
        Gets the RST filename corresponding to a source filename.
        See the help for the constructor for more details.

        Args:
            source_filename: source filename within current project

        Returns:
            RST filename

        Note in particular: the way we structure the directories means that we
        won't get clashes between files with idential names in two different
        directories. However, we must also incorporate the original source
        filename, in particular for C++ where ``thing.h`` and ``thing.cpp``
        must not generate the same RST filename. So we just add ``.rst``.
        """
        highest_code_to_target = relative_filename_within_dir(
            source_filename, self.highest_code_dir)
        bname = basename(source_filename)
        result = join(self.autodoc_rst_root_dir,
                      dirname(highest_code_to_target),
                      bname + EXT_RST)
        log.debug("Source {!r} -> RST {!r}", source_filename, result)
        return result

    def write_index_and_rst_files(self, overwrite: bool = False,
                                  mock: bool = False) -> None:
        """
        Writes both the individual RST files and the index.

        Args:
            overwrite: allow existing files to be overwritten?
            mock: pretend to write, but don't
        """
        for f in self.files_to_index:
            if isinstance(f, FileToAutodocument):
                f.write_rst(
                    prefix=self.rst_prefix,
                    suffix=self.rst_suffix,
                    heading_underline_char=self.source_rst_heading_underline_char,  # noqa
                    overwrite=overwrite,
                    mock=mock,
                )
            elif isinstance(f, AutodocIndex):
                f.write_index_and_rst_files(overwrite=overwrite, mock=mock)
            else:
                fail("Unknown thing in files_to_index: {!r}".format(f))
        self.write_index(overwrite=overwrite, mock=mock)

    @property
    def index_filename_rel_project_root(self) -> str:
        """
        Returns the name of the index filename, relative to the project root.
        Used for labelling the index file.
        """
        return relpath(self.index_filename, start=self.project_root_dir)

    def index_filename_rel_other_index(self, other: str) -> str:
        """
        Returns the filename of this index, relative to the director of another
        index. (For inserting a reference to this index into ``other``.)

        Args:
            other: the other index

        Returns:
            relative filename of our index
        """
        return relpath(self.index_filename, start=dirname(other))

    def index_content(self) -> str:
        """
        Returns the contents of the index RST file.
        """
        # Build the toctree command
        index_filename = self.index_filename
        spacer = "    "
        instruction_lines = [
            "..  toctree::",
            spacer + ":maxdepth: {}".format(self.toctree_maxdepth),
            ""
        ]
        for f in self.files_to_index:
            if isinstance(f, FileToAutodocument):
                rst_filename = spacer + f.rst_filename_rel_autodoc_index(
                    index_filename)
            elif isinstance(f, AutodocIndex):
                rst_filename = (
                    spacer + f.index_filename_rel_other_index(index_filename)
                )
            else:
                fail("Unknown thing in files_to_index: {!r}".format(f))
                rst_filename = ""  # won't get here; for the type checker
            instruction_lines.append(rst_filename)
        instruction = "\n".join(instruction_lines)

        # Create the whole file
        content = """
.. {filename}

{AUTOGENERATED_COMMENT}

{prefix}

{underlined_title}

{instruction}

{suffix}
                """.format(
            filename=self.index_filename_rel_project_root,
            AUTOGENERATED_COMMENT=AUTOGENERATED_COMMENT,
            prefix=self.rst_prefix,
            underlined_title=rst_underline(
                self.title, underline_char=self.index_heading_underline_char),
            instruction=instruction,
            suffix=self.rst_suffix,
        ).strip() + "\n"
        return content

    def write_index(self, overwrite: bool = False, mock: bool = False) -> None:
        """
        Writes the index file, if permitted.

        Args:
            overwrite: allow existing files to be overwritten?
            mock: pretend to write, but don't
        """
        write_if_allowed(self.index_filename, self.index_content(),
                         overwrite=overwrite, mock=mock)
