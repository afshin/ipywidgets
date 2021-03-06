"""Interact with functions using widgets."""

# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.

from __future__ import print_function
from __future__ import division

try:  # Python >= 3.3
    from inspect import signature, Parameter
except ImportError:
    from IPython.utils.signatures import signature, Parameter
from inspect import getcallargs

try:
    from inspect import getfullargspec as check_argspec
except ImportError:
    from inspect import getargspec as check_argspec # py2

from IPython.core.getipython import get_ipython
from . import (ValueWidget, Text,
    FloatSlider, IntSlider, Checkbox, Dropdown,
    Box, Button, DOMWidget, Output)
from IPython.display import display, clear_output
from ipython_genutils.py3compat import string_types, unicode_type
from traitlets import HasTraits, Any, Unicode, observe
from numbers import Real, Integral
from warnings import warn
from collections import Iterable, Mapping

empty = Parameter.empty


def _matches(o, pattern):
    """Match a pattern of types in a sequence."""
    if not len(o) == len(pattern):
        return False
    comps = zip(o,pattern)
    return all(isinstance(obj,kind) for obj,kind in comps)


def _get_min_max_value(min, max, value=None, step=None):
    """Return min, max, value given input values with possible None."""
    if value is None:
        if not max > min:
            raise ValueError('max must be greater than min: (min={0}, max={1})'.format(min, max))
        diff = max - min
        value = min + (diff / 2)
        # Ensure that value has the same type as diff
        if not isinstance(value, type(diff)):
            value = min + (diff // 2)
    elif min is None and max is None:
        if not isinstance(value, Real):
            raise TypeError('expected a real number, got: %r' % value)
        if value == 0:
            # This gives (0, 1) of the correct type
            min, max = (value, value + 1)
        elif value > 0:
            min, max = (-value, 3*value)
        else:
            min, max = (3*value, -value)
    else:
        raise ValueError('unable to infer range, value from: ({0}, {1}, {2})'.format(min, max, value))
    if step is not None:
        # ensure value is on a step
        tick = int((value - min) / step)
        value = min + tick * step
    return min, max, value

def _yield_abbreviations_for_parameter(param, kwargs):
    """Get an abbreviation for a function parameter."""
    name = param.name
    kind = param.kind
    ann = param.annotation
    default = param.default
    not_found = (name, empty, empty)
    if kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY):
        if name in kwargs:
            value = kwargs.pop(name)
        elif ann is not empty:
            value = ann
        elif default is not empty:
            value = default
        else:
            yield not_found
        yield (name, value, default)
    elif kind == Parameter.VAR_KEYWORD:
        # In this case name=kwargs and we yield the items in kwargs with their keys.
        for k, v in kwargs.copy().items():
            kwargs.pop(k)
            yield k, v, empty


class interactive(Box):
    """
    A Box container containing a group of interactive widgets tied to a
    function.

    Parameters
    ----------
    __interact_f : function
        The function to which the interactive widgets are tied. The `**kwargs`
        should match the function signature.
    **kwargs : various, optional
        An interactive widget is created for each keyword argument that is a
        valid widget abbreviation.
    """
    def __init__(self, __interact_f, **kwargs):
        Box.__init__(self, _dom_classes=['widget-interact'])
        self.result = None
        self.args = []
        self.kwargs = {}

        self.f = f = __interact_f
        self.clear_output = kwargs.pop('clear_output', True)
        self.manual = kwargs.pop('__manual', False)

        new_kwargs = self.find_abbreviations(kwargs)
        # Before we proceed, let's make sure that the user has passed a set of args+kwargs
        # that will lead to a valid call of the function. This protects against unspecified
        # and doubly-specified arguments.
        try:
            check_argspec(f)
        except TypeError:
            # if we can't inspect, we can't validate
            pass
        else:
            getcallargs(f, **{n:v for n,v,_ in new_kwargs})
        # Now build the widgets from the abbreviations.
        self.kwargs_widgets = self.widgets_from_abbreviations(new_kwargs)

        # This has to be done as an assignment, not using self.children.append,
        # so that traitlets notices the update. We skip any objects (such as fixed) that
        # are not DOMWidgets.
        c = [w for w in self.kwargs_widgets if isinstance(w, DOMWidget)]

        # If we are only to run the function on demand, add a button to request this.
        if self.manual:
            self.manual_button = Button(description="Run %s" % f.__name__)
            c.append(self.manual_button)

        self.out = Output()
        c.append(self.out)
        self.children = c

        # Wire up the widgets
        # If we are doing manual running, the callback is only triggered by the button
        # Otherwise, it is triggered for every trait change received
        # On-demand running also suppresses running the function with the initial parameters
        if self.manual:
            self.manual_button.on_click(self.call_f)

            # Also register input handlers on text areas, so the user can hit return to
            # invoke execution.
            for w in self.kwargs_widgets:
                if isinstance(w, Text):
                    w.on_submit(self.call_f)
        else:
            for widget in self.kwargs_widgets:
                widget.observe(self.call_f, names='value')

            self.on_displayed(lambda _: self.call_f(dict(name=None, old=None, new=None)))

    # Callback function
    def call_f(self, *args):
        self.kwargs = {}
        if self.manual:
            self.manual_button.disabled = True
        try:
            for widget in self.kwargs_widgets:
                value = widget.get_interact_value()
                self.kwargs[widget._kwarg] = value
            with self.out:
                if self.clear_output:
                    clear_output(wait=True)
                self.result = self.f(**self.kwargs)
                if self.result is not None:
                    display(self.result)
        except Exception as e:
            ip = get_ipython()
            if ip is None:
                self.log.warn("Exception in interact callback: %s", e, exc_info=True)
            else:
                ip.showtraceback()
        finally:
            if self.manual:
                self.manual_button.disabled = False

    # Find abbreviations
    def signature(self):
        return signature(self.f)

    def find_abbreviations(self, kwargs):
        """Find the abbreviations for the given function and kwargs.
        Return (name, abbrev, default) tuples.
        """
        new_kwargs = []
        try:
            sig = self.signature()
        except (ValueError, TypeError):
            # can't inspect, no info from function; only use kwargs
            return [ (key, value, value) for key, value in kwargs.items() ]

        for param in sig.parameters.values():
            for name, value, default in _yield_abbreviations_for_parameter(param, kwargs):
                if value is empty:
                    raise ValueError('cannot find widget or abbreviation for argument: {!r}'.format(name))
                new_kwargs.append((name, value, default))
        return new_kwargs

    # Abbreviations to widgets
    def widgets_from_abbreviations(self, seq):
        """Given a sequence of (name, abbrev, default) tuples, return a sequence of Widgets."""
        result = []
        for name, abbrev, default in seq:
            widget = self.widget_from_abbrev(abbrev, default)
            if not (isinstance(widget, ValueWidget) or isinstance(widget, fixed)):
                if widget is None:
                    raise ValueError("{!r} cannot be transformed to a widget".format(abbrev))
                else:
                    raise TypeError("{!r} is not a ValueWidget".format(widget))
            if not widget.description:
                widget.description = name
            widget._kwarg = name
            result.append(widget)
        return result

    def widget_from_abbrev(self, abbrev, default=empty):
        """Build a ValueWidget instance given an abbreviation or Widget."""
        if isinstance(abbrev, ValueWidget) or isinstance(abbrev, fixed):
            return abbrev

        if isinstance(abbrev, tuple):
            widget = self.widget_from_tuple(abbrev)
            if default is not empty:
                try:
                    widget.value = default
                except Exception:
                    # ignore failure to set default
                    pass
            return widget

        # Try single value
        widget = self.widget_from_single_value(abbrev)
        if widget is not None:
            return widget

        # Something iterable (list, dict, generator, ...). Note that str and
        # tuple should be handled before, that is why we check this case last.
        if isinstance(abbrev, Iterable):
            widget = self.widget_from_iterable(abbrev)
            if default is not empty:
                try:
                    widget.value = default
                except Exception:
                    # ignore failure to set default
                    pass
            return widget

        # No idea...
        return None

    @staticmethod
    def widget_from_single_value(o):
        """Make widgets from single values, which can be used as parameter defaults."""
        if isinstance(o, string_types):
            return Text(value=unicode_type(o))
        elif isinstance(o, bool):
            return Checkbox(value=o)
        elif isinstance(o, Integral):
            min, max, value = _get_min_max_value(None, None, o)
            return IntSlider(value=o, min=min, max=max)
        elif isinstance(o, Real):
            min, max, value = _get_min_max_value(None, None, o)
            return FloatSlider(value=o, min=min, max=max)
        else:
            return None

    @staticmethod
    def widget_from_tuple(o):
        """Make widgets from a tuple abbreviation."""
        if _matches(o, (Real, Real)):
            min, max, value = _get_min_max_value(o[0], o[1])
            if all(isinstance(_, Integral) for _ in o):
                cls = IntSlider
            else:
                cls = FloatSlider
            return cls(value=value, min=min, max=max)
        elif _matches(o, (Real, Real, Real)):
            step = o[2]
            if step <= 0:
                raise ValueError("step must be >= 0, not %r" % step)
            min, max, value = _get_min_max_value(o[0], o[1], step=step)
            if all(isinstance(_, Integral) for _ in o):
                cls = IntSlider
            else:
                cls = FloatSlider
            return cls(value=value, min=min, max=max, step=step)

    @staticmethod
    def widget_from_iterable(o):
        """Make widgets from an iterable. This should not be done for
        a string or tuple."""
        # Dropdown expects a dict or list, so we convert an arbitrary
        # iterable to either of those.
        if isinstance(o, (list, dict)):
            return Dropdown(options=o)
        elif isinstance(o, Mapping):
            return Dropdown(options=list(o.items()))
        else:
            return Dropdown(options=list(o))

    # User-facing constructors
    @classmethod
    def interact(cls, __interact_f=None, **kwargs):
        """
        Displays interactive widgets which are tied to a function.
        Expects the first argument to be a function. Parameters to this function are
        widget abbreviations passed in as keyword arguments (`**kwargs`). Can be used
        as a decorator (see examples).

        Returns
        -------
        f : __interact_f with interactive widget attached to it.

        Parameters
        ----------
        __interact_f : function
            The function to which the interactive widgets are tied. The `**kwargs`
            should match the function signature. Passed to :func:`interactive()`
        **kwargs : various, optional
            An interactive widget is created for each keyword argument that is a
            valid widget abbreviation. Passed to :func:`interactive()`

        Examples
        --------
        Render an interactive text field that shows the greeting with the passed in
        text::

           # 1. Using interact as a function
           def greeting(text="World"):
               print "Hello {}".format(text)
           interact(greeting, text="IPython Widgets")

           # 2. Using interact as a decorator
           @interact
           def greeting(text="World"):
               print "Hello {}".format(text)

           # 3. Using interact as a decorator with named parameters
           @interact(text="IPython Widgets")
           def greeting(text="World"):
               print "Hello {}".format(text)

        Render an interactive slider widget and prints square of number::

           # 1. Using interact as a function
           def square(num=1):
               print "{} squared is {}".format(num, num*num)
           interact(square, num=5)

           # 2. Using interact as a decorator
           @interact
           def square(num=2):
               print "{} squared is {}".format(num, num*num)

           # 3. Using interact as a decorator with named parameters
           @interact(num=5)
           def square(num=2):
               print "{} squared is {}".format(num, num*num)
        """
        # positional arg support in: https://gist.github.com/8851331
        if __interact_f is not None:
            # This branch handles the cases 1 and 2
            # 1. interact(f, **kwargs)
            # 2. @interact
            #    def f(*args, **kwargs):
            #        ...
            f = __interact_f
            w = cls(f, **kwargs)
            try:
                f.widget = w
            except AttributeError:
                # some things (instancemethods) can't have attributes attached,
                # so wrap in a lambda
                f = lambda *args, **kwargs: __interact_f(*args, **kwargs)
                f.widget = w
            display(w)
            return f
        else:
            # This branch handles the case 3
            # @interact(a=30, b=40)
            # def f(*args, **kwargs):
            #     ...
            def decorate(f):
                return cls.interact(f, **kwargs)
            return decorate

    @classmethod
    def interact_manual(cls, __interact_f=None, **kwargs):
        """interact_manual(f, **kwargs)

        As `interact()`, generates widgets for each argument, but rather than running
        the function after each widget change, adds a "Run" button and waits for it
        to be clicked. Useful if the function is long-running and has several
        parameters to change.
        """
        return cls.interact(__interact_f, __manual=True, **kwargs)

interact = interactive.interact
interact_manual = interactive.interact_manual


class fixed(HasTraits):
    """A pseudo-widget whose value is fixed and never synced to the client."""
    value = Any(help="Any Python object")
    description = Unicode('', help="Any Python object")
    def __init__(self, value, **kwargs):
        super(fixed, self).__init__(value=value, **kwargs)
