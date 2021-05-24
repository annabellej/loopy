"""Plain C target and base for other C-family languages."""


__copyright__ = "Copyright (C) 2015 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import numpy as np  # noqa
from loopy.target import TargetBase, ASTBuilderBase, DummyHostASTBuilder
from loopy.diagnostic import LoopyError, LoopyTypeError
from cgen import Pointer, NestedDeclarator, Block
from cgen.mapper import IdentityMapper as CASTIdentityMapperBase
from pymbolic.mapper.stringifier import PREC_NONE
from loopy.symbolic import IdentityMapper
from loopy.types import NumpyType
from loopy.kernel.function_interface import ScalarCallable
import pymbolic.primitives as p

from loopy.tools import remove_common_indentation
import re

from pytools import memoize_method

__doc__ = """
.. currentmodule loopy.target.c

.. autoclass:: POD

.. autoclass:: ScopingBlock

.. automodule:: loopy.target.c.codegen.expression
"""


# {{{ dtype registry wrapper

class DTypeRegistryWrapper:
    def __init__(self, wrapped_registry):
        self.wrapped_registry = wrapped_registry

    def get_or_register_dtype(self, names, dtype=None):
        if dtype is not None:
            from loopy.types import LoopyType, NumpyType
            assert isinstance(dtype, LoopyType)

            if isinstance(dtype, NumpyType):
                return self.wrapped_registry.get_or_register_dtype(
                        names, dtype.dtype)
            else:
                raise LoopyError(
                        "unable to get or register type '%s'"
                        % dtype)
        else:
            return self.wrapped_registry.get_or_register_dtype(names, dtype)

    def dtype_to_ctype(self, dtype):
        from loopy.types import LoopyType, NumpyType, OpaqueType
        assert isinstance(dtype, LoopyType)

        if isinstance(dtype, NumpyType):
            return self.wrapped_registry.dtype_to_ctype(dtype)
        elif isinstance(dtype, OpaqueType):
            return dtype.name
        else:
            raise LoopyError(
                    "unable to convert type '%s' to C"
                    % dtype)

# }}}


# {{{ preamble generator

class InfOrNanInExpressionRecorder(IdentityMapper):
    def __init__(self):
        self.saw_inf_or_nan = False

    def map_constant(self, expr):
        if (np.isinf(expr) or np.isnan(expr) or np.isnan(expr)):
            self.saw_inf_or_nan = True
        return super().map_constant(expr)


def c99_preamble_generator(preamble_info):
    if any(dtype.is_integral() for dtype in preamble_info.seen_dtypes):
        yield("10_stdint", "#include <stdint.h>")
    if any(dtype.numpy_dtype == np.dtype("bool")
           for dtype in preamble_info.seen_dtypes
           if isinstance(dtype, NumpyType)):
        yield("10_stdbool", "#include <stdbool.h>")
    if any(dtype.is_complex() for dtype in preamble_info.seen_dtypes):
        yield("10_complex", "#include <complex.h>")

    # {{{ emit math.h

    inf_or_nan_recorder = InfOrNanInExpressionRecorder()

    for insn in preamble_info.codegen_state.kernel.instructions:
        insn.with_transformed_expressions(inf_or_nan_recorder)

    if inf_or_nan_recorder.saw_inf_or_nan:
        yield("10_math", "#include <math.h>")

    # }}}


def _preamble_generator(preamble_info, func_qualifier="inline"):
    integer_type_names = ["int8", "int16", "int32", "int64"]

    def_integer_types_macro = ("03_def_integer_types", r"""
            #define LOOPY_CALL_WITH_INTEGER_TYPES(MACRO_NAME) \
                MACRO_NAME(int8, char) \
                MACRO_NAME(int16, short) \
                MACRO_NAME(int32, int) \
                MACRO_NAME(int64, long)
            """)

    undef_integer_types_macro = ("05_undef_integer_types", """
            #undef LOOPY_CALL_WITH_INTEGER_TYPES
            """)

    function_defs = {
            "loopy_floor_div": r"""
            #define LOOPY_DEFINE_FLOOR_DIV(SUFFIX, TYPE) \
                {} TYPE loopy_floor_div_##SUFFIX(TYPE a, TYPE b) \
                {{ \
                    if ((a<0) != (b<0)) \
                        a = a - (b + (b<0) - (b>=0)); \
                    return a/b; \
                }}
            LOOPY_CALL_WITH_INTEGER_TYPES(LOOPY_DEFINE_FLOOR_DIV)
            #undef LOOPY_DEFINE_FLOOR_DIV
            """.format(func_qualifier),

            "loopy_floor_div_pos_b": r"""
            #define LOOPY_DEFINE_FLOOR_DIV_POS_B(SUFFIX, TYPE) \
                {} TYPE loopy_floor_div_pos_b_##SUFFIX(TYPE a, TYPE b) \
                {{ \
                    if (a<0) \
                        a = a - (b-1); \
                    return a/b; \
                }}
            LOOPY_CALL_WITH_INTEGER_TYPES(LOOPY_DEFINE_FLOOR_DIV_POS_B)
            #undef LOOPY_DEFINE_FLOOR_DIV_POS_B
            """.format(func_qualifier),

            "loopy_mod": r"""
            #define LOOPY_DEFINE_MOD(SUFFIX, TYPE) \
                {} TYPE loopy_mod_##SUFFIX(TYPE a, TYPE b) \
                {{ \
                    TYPE result = a%b; \
                    if (result < 0 && b > 0) \
                        result += b; \
                    if (result > 0 && b < 0) \
                        result = result + b; \
                    return result; \
                }}
            LOOPY_CALL_WITH_INTEGER_TYPES(LOOPY_DEFINE_MOD)
            #undef LOOPY_DEFINE_MOD
            """.format(func_qualifier),

            "loopy_mod_pos_b": r"""
            #define LOOPY_DEFINE_MOD_POS_B(SUFFIX, TYPE) \
                {} TYPE loopy_mod_pos_b_##SUFFIX(TYPE a, TYPE b) \
                {{ \
                    TYPE result = a%b; \
                    if (result < 0) \
                        result += b; \
                    return result; \
                }}
            LOOPY_CALL_WITH_INTEGER_TYPES(LOOPY_DEFINE_MOD_POS_B)
            #undef LOOPY_DEFINE_MOD_POS_B
            """.format(func_qualifier),
            }

    c_funcs = {func.c_name for func in preamble_info.seen_functions}

    for func_name, func_body in function_defs.items():
        if any((func_name + "_" + tpname) in c_funcs
                for tpname in integer_type_names):
            yield def_integer_types_macro
            yield ("04_%s" % func_name, func_body)
            yield undef_integer_types_macro

    for func in preamble_info.seen_functions:
        if func.name == "int_pow":
            base_ctype = preamble_info.kernel.target.dtype_to_typename(
                    func.arg_dtypes[0])
            exp_ctype = preamble_info.kernel.target.dtype_to_typename(
                    func.arg_dtypes[1])
            res_ctype = preamble_info.kernel.target.dtype_to_typename(
                    func.result_dtypes[0])

            if func.arg_dtypes[1].numpy_dtype.kind == "u":
                signed_exponent_preamble = ""
            else:
                signed_exponent_preamble = "\n" + remove_common_indentation(
                        """
                        if (n < 0) {
                          x = 1.0/x;
                          n =  -n;
                        }""")

            yield(f"07_{func.c_name}", f"""
            inline {res_ctype} {func.c_name}({base_ctype} x, {exp_ctype} n) {{
              if (n == 0)
                return 1;
              {re.sub("^", 14*" ", signed_exponent_preamble, flags=re.M)}

              {res_ctype} y = 1;

              while (n > 1) {{
                if (n % 2) {{
                  y = x * y;
                  x = x * x;
                }}
                else
                  x = x * x;
                n = n / 2;
              }}

              return x*y;
            }}""")

# }}}


# {{{ cgen overrides

from cgen import Declarator


class POD(Declarator):
    """A simple declarator: The type is given as a :class:`numpy.dtype`
    and the *name* is given as a string.
    """

    def __init__(self, ast_builder, dtype, name):
        from loopy.types import LoopyType
        assert isinstance(dtype, LoopyType)

        self.ast_builder = ast_builder
        self.ctype = ast_builder.target.dtype_to_typename(dtype)
        self.dtype = dtype
        self.name = name

    def get_decl_pair(self):
        return [self.ctype], self.name

    def struct_maker_code(self, name):
        return name

    def struct_format(self):
        return self.dtype.char

    def alignment_requirement(self):
        return self.ast_builder.target.alignment_requirement(self)

    def default_value(self):
        return 0

    mapper_method = "map_loopy_pod"


class ScopingBlock(Block):
    """A block that is mandatory for scoping and may not be simplified away
    by :func:`loopy.codegen.result.merge_codegen_results`.
    """


class FunctionDeclarationWrapper(NestedDeclarator):
    mapper_method = "map_function_decl_wrapper"

# }}}


# {{{ array literals

def generate_linearized_array(array, value):
    from pytools import product
    size = product(shape_ax for shape_ax in array.shape)

    if not isinstance(size, int):
        raise LoopyError("cannot produce literal for array '%s': "
                "shape is not a compile-time constant"
                % array.name)

    strides = []

    data = np.zeros(size, array.dtype.numpy_dtype)

    from loopy.kernel.array import FixedStrideArrayDimTag
    for i, dim_tag in enumerate(array.dim_tags):
        if isinstance(dim_tag, FixedStrideArrayDimTag):

            if not isinstance(dim_tag.stride, int):
                raise LoopyError("cannot produce literal for array '%s': "
                        "stride along axis %d (1-based) is not a "
                        "compile-time constant"
                        % (array.name, i+1))

            strides.append(dim_tag.stride)

        else:
            raise LoopyError("cannot produce literal for array '%s': "
                    "dim_tag type '%s' not supported"
                    % (array.name, type(dim_tag).__name__))

    assert array.offset == 0

    for ituple in np.ndindex(value.shape):
        i = sum(i_ax * strd_ax for i_ax, strd_ax in zip(ituple, strides))
        data[i] = value[ituple]

    return data


def generate_array_literal(kernel, ecm, ast_builder, array, value):
    data = generate_linearized_array(array, value)

    from loopy.expression import dtype_to_type_context
    from loopy.symbolic import ArrayLiteral

    type_context = dtype_to_type_context(kernel.target, array.dtype)
    return CExpression(
            ast_builder.get_c_expression_to_code_mapper(),
            ArrayLiteral(
                tuple(
                    ecm.map_constant(d_i, type_context)
                    for d_i in data)))

# }}}


# {{{ subscript CSE

class CASTIdentityMapper(CASTIdentityMapperBase):
    def map_loopy_pod(self, node, *args, **kwargs):
        return type(node)(node.ast_builder, node.dtype, node.name)

    def map_function_decl_wrapper(self, node, *args, **kwargs):
        return FunctionDeclarationWrapper(
                self.rec(node.subdecl, *args, **kwargs))


class SubscriptSubsetCounter(IdentityMapper):
    def __init__(self, subset_counters):
        self.subset_counters = subset_counters


class ASTSubscriptCollector(CASTIdentityMapper):
    def __init__(self):
        self.subset_counters = {}

    def map_expression(self, expr):
        from pymbolic.primitives import is_constant
        if isinstance(expr, CExpression) or is_constant(expr):
            return expr
        elif isinstance(expr, str):
            return expr
        else:
            raise LoopyError(
                    "Unexpected expression type: %s" % type(expr).__name__)

# }}}


# {{{ lazy expression generation

class CExpression:
    def __init__(self, to_code_mapper, expr):
        self.to_code_mapper = to_code_mapper
        self.expr = expr

    def __str__(self):
        return self.to_code_mapper(self.expr, PREC_NONE)

# }}}


class CFamilyTarget(TargetBase):
    """A target for "least-common denominator C", without any parallel
    extensions, and without use of any C99 specifics. Intended to be
    usable as a common base for C99, C++, OpenCL, CUDA, and the like.
    """

    hash_fields = TargetBase.hash_fields + ("fortran_abi",)
    comparison_fields = TargetBase.comparison_fields + ("fortran_abi",)

    def __init__(self, fortran_abi=False):
        self.fortran_abi = fortran_abi
        super().__init__()

    def split_kernel_at_global_barriers(self):
        return False

    def get_host_ast_builder(self):
        return DummyHostASTBuilder(self)

    def get_device_ast_builder(self):
        return CFamilyASTBuilder(self)

    # {{{ types

    @memoize_method
    def get_dtype_registry(self):
        from loopy.target.c.compyte.dtypes import (
                DTypeRegistry, fill_registry_with_c_types)
        result = DTypeRegistry()
        fill_registry_with_c_types(result, respect_windows=False,
                include_bool=True)
        return DTypeRegistryWrapper(result)

    def is_vector_dtype(self, dtype):
        return False

    def get_vector_dtype(self, base, count):
        raise KeyError()

    def get_or_register_dtype(self, names, dtype=None):
        # These kind of shouldn't be here.
        return self.get_dtype_registry().get_or_register_dtype(names, dtype)

    def dtype_to_typename(self, dtype):
        # These kind of shouldn't be here.
        return self.get_dtype_registry().dtype_to_ctype(dtype)

    def get_kernel_executor_cache_key(self, *args, **kwargs):
        raise NotImplementedError

    def get_kernel_executor(self, knl, *args, **kwargs):
        raise NotImplementedError

    # }}}


class _ConstRestrictPointer(Pointer):
    def get_decl_pair(self):
        sub_tp, sub_decl = self.subdecl.get_decl_pair()
        return sub_tp, ("*const __restrict__ %s" % sub_decl)


class _ConstPointer(Pointer):
    def get_decl_pair(self):
        sub_tp, sub_decl = self.subdecl.get_decl_pair()
        return sub_tp, ("*const %s" % sub_decl)


# {{{ symbol mangler

def c_symbol_mangler(kernel, name):
    # float NAN as defined in C99 standard
    if name == "NAN":
        return NumpyType(np.dtype(np.float32)), name

    if name in ["INT_MAX", "INT_MIN"]:
        return NumpyType(np.dtype(np.int32)), name

    return None

# }}}


# {{{ function scoping

class CMathCallable(ScalarCallable):
    """
    An umbrella callable for all the math functions which can be seen in a
    C-Target.
    """

    def with_types(self, arg_id_to_dtype, callables_table):
        name = self.name

        # {{{ (abs|max|min) -> (fabs|fmax|fmin)

        if name in ["abs", "min", "max"]:
            dtype = np.find_common_type(
                [], [dtype.numpy_dtype for dtype in arg_id_to_dtype.values()])
            if dtype.kind == "f":
                name = "f" + name

        # }}}

        # unary functions
        if name in ["fabs", "acos", "asin", "atan", "cos", "cosh", "sin", "sinh",
                    "tan", "tanh", "exp", "log", "log10", "sqrt", "ceil", "floor",
                    "erf", "erfc", "abs", "real", "imag", "conj"]:

            for id in arg_id_to_dtype:
                if not -1 <= id <= 0:
                    raise LoopyError(f"'{name}' can take only one argument.")

            if 0 not in arg_id_to_dtype or arg_id_to_dtype[0] is None:
                # the types provided aren't mature enough to specialize the
                # callable
                return (
                        self.copy(arg_id_to_dtype=arg_id_to_dtype),
                        callables_table)

            dtype = arg_id_to_dtype[0].numpy_dtype
            real_dtype = np.empty(0, dtype=dtype).real.dtype

            if dtype.kind in ("u", "i"):
                # ints and unsigned casted to float32
                dtype = np.float32

            # for CUDA, C Targets the name must be modified
            if real_dtype == np.float64:
                pass  # fabs
            elif real_dtype == np.float32:
                name = name + "f"  # fabsf
            elif (hasattr(np, "float128")
                    and real_dtype == np.float128):  # pylint:disable=no-member
                name = name + "l"  # fabsl
            else:
                raise LoopyTypeError("{} does not support type {}".format(name,
                    dtype))

            if name in ["abs", "real", "imag"]:
                dtype = real_dtype

            if dtype.kind == "c" or name in ["real", "imag", "abs"]:
                if name != "conj":
                    name = "c" + name

            return (
                    self.copy(name_in_target=name,
                        arg_id_to_dtype={0: NumpyType(dtype), -1:
                            NumpyType(dtype)}),
                    callables_table)

        # binary functions
        elif name in ["fmax", "fmin", "pow", "atan2", "copysign"]:

            for id in arg_id_to_dtype:
                if not -1 <= id <= 1:
                    raise LoopyError("%s can take only two arguments." % name)

            if 0 not in arg_id_to_dtype or 1 not in arg_id_to_dtype or (
                    arg_id_to_dtype[0] is None or arg_id_to_dtype[1] is None):
                # the types provided aren't mature enough to specialize the
                # callable
                return (
                        self.copy(arg_id_to_dtype=arg_id_to_dtype),
                        callables_table)

            dtype = np.find_common_type(
                [], [dtype.numpy_dtype for id, dtype in arg_id_to_dtype.items()
                     if id >= 0])
            real_dtype = np.empty(0, dtype=dtype).real.dtype

            if name in ["fmax", "fmin", "copysign"] and dtype.kind == "c":
                raise LoopyTypeError(f"{name} does not support complex numbers")

            elif real_dtype.kind in "fc":
                if real_dtype == np.float64:
                    pass  # fmin
                elif real_dtype == np.float32:
                    name = name + "f"  # fminf
                elif (hasattr(np, "float128")
                        and real_dtype == np.float128):  # pylint:disable=no-member
                    name = name + "l"  # fminl
                else:
                    raise LoopyTypeError("%s does not support type %s"
                                         % (name, dtype))
            if dtype.kind == "c":
                name = "c" + name  # cpow
            dtype = NumpyType(dtype)
            return (
                    self.copy(name_in_target=name,
                        arg_id_to_dtype={-1: dtype, 0: dtype, 1: dtype}),
                    callables_table)
        elif name in ["max", "min"]:

            for id in arg_id_to_dtype:
                if not -1 <= id <= 1:
                    raise LoopyError("%s can take only two arguments." % name)

            if 0 not in arg_id_to_dtype or 1 not in arg_id_to_dtype or (
                    arg_id_to_dtype[0] is None or arg_id_to_dtype[1] is None):
                # the types provided aren't resolved enough to specialize the
                # callable
                return (
                        self.copy(arg_id_to_dtype=arg_id_to_dtype),
                        callables_table)

            dtype = np.find_common_type(
                [], [dtype.numpy_dtype for id, dtype in arg_id_to_dtype.items()
                     if id >= 0])
            if dtype.kind not in "iu":
                # only support integers for now to avoid having to deal with NaNs
                raise LoopyError(f"{name} does not support '{dtype}' arguments.")

            return (
                    self.copy(name_in_target=f"lpy_{name}_{dtype.name}",
                              arg_id_to_dtype={-1: NumpyType(dtype),
                                               0: NumpyType(dtype),
                                               1: NumpyType(dtype)}),
                    callables_table)
        elif name == "isnan":
            for id in arg_id_to_dtype:
                if not -1 <= id <= 0:
                    raise LoopyError(f"'{name}' can take only one argument.")

            if 0 not in arg_id_to_dtype or arg_id_to_dtype[0] is None:
                # the types provided aren't mature enough to specialize the
                # callable
                return (
                        self.copy(arg_id_to_dtype=arg_id_to_dtype),
                        callables_table)

            dtype = arg_id_to_dtype[0].numpy_dtype
            return (
                    self.copy(
                        name_in_target=name,
                        arg_id_to_dtype={
                            0: NumpyType(dtype),
                            -1: NumpyType(np.int32)}),
                    callables_table)

    def generate_preambles(self, target):
        if self.name_in_target.startswith("lpy_max"):
            dtype = self.arg_id_to_dtype[-1]
            ctype = target.dtype_to_typename(dtype)

            yield ("40_lpy_max", f"""
            static inline {ctype} {self.name_in_target}({ctype} a, {ctype} b) {{
              return (a > b ? a : b);
            }}""")

        if self.name_in_target.startswith("lpy_min"):
            dtype = self.arg_id_to_dtype[-1]
            ctype = target.dtype_to_typename(dtype)
            yield ("40_lpy_min", f"""
            static inline {ctype} {self.name_in_target}({ctype} a, {ctype} b) {{
              return (a < b ? a : b);
            }}""")


def get_c_callables():
    """
    Returns an instance of :class:`InKernelCallable` if the function
    represented by :arg:`identifier` is known in C, otherwise returns *None*.
    """
    cmath_ids = ["abs", "acos", "asin", "atan", "cos", "cosh", "sin",
                 "sinh", "pow", "atan2", "tanh", "exp", "log", "log10",
                 "sqrt", "ceil", "floor", "max", "min", "fmax", "fmin",
                 "fabs", "tan", "erf", "erfc", "isnan", "real", "imag",
                 "conj"]

    return {id_: CMathCallable(id_) for id_ in cmath_ids}

# }}}


class CFamilyASTBuilder(ASTBuilderBase):

    preamble_function_qualifier = "inline"

    # {{{ library

    def symbol_manglers(self):
        return (
                super().symbol_manglers() + [
                    c_symbol_mangler
                    ])

    def preamble_generators(self):
        return (
                super().preamble_generators() + [
                    lambda preamble_info: _preamble_generator(preamble_info,
                        self.preamble_function_qualifier),
                    ])

    @property
    def known_callables(self):
        callables = super().known_callables
        callables.update(get_c_callables())
        return callables

    # }}}

    # {{{ code generation

    def get_function_definition(self, kernel, name, implemented_data_info,
                                function_decl, function_body):
        from cgen import FunctionBody
        fbody = FunctionBody(function_decl, function_body)
        return fbody

    def idi_to_cgen_declarator(self, kernel, idi):
        from loopy.kernel.data import InameArg
        if (idi.offset_for_name is not None
                or idi.stride_for_name_and_axis is not None):
            assert not idi.is_written
            from cgen import Const
            return Const(POD(self, idi.dtype, idi.name))
        elif issubclass(idi.arg_class, InameArg):
            return InameArg(idi.name, idi.dtype).get_arg_decl(self)
        else:
            name = idi.base_name or idi.name
            var_descr = kernel.get_var_descriptor(name)
            from loopy.kernel.data import ArrayBase
            if isinstance(var_descr, ArrayBase):
                return var_descr.get_arg_decl(
                        self,
                        idi.name[len(name):], idi.shape, idi.dtype,
                        idi.is_written)
            else:
                return var_descr.get_arg_decl(self)

    def get_function_declaration(self, kernel, callables_table, name,
                                 implemented_data_info,
                                 is_generating_device_code, is_entrypoint):
        from cgen import FunctionDeclaration, Value

        if self.target.fortran_abi:
            name += "_"

        if is_entrypoint:
            name = Value("void", name)
        else:
            name = Value("static void", name)
        return FunctionDeclarationWrapper(
                FunctionDeclaration(
                    name,
                    [self.idi_to_cgen_declarator(kernel, idi)
                     for idi in implemented_data_info]))

    def get_kernel_call(self, kernel, name, implemented_data_info, extra_args):
        return None

    def get_temporary_decls(self, kernel, subkernel_name):
        from loopy.kernel.data import AddressSpace

        ecm = self.get_expression_to_code_mapper(kernel, var_subst_map={},
                                                 vectorization_info=None)
        base_storage_decls = []
        temp_decls = []

        # {{{ declare temporaries

        base_storage_sizes = {}
        base_storage_to_scope = {}
        base_storage_to_align_bytes = {}

        from cgen import ArrayOf, Initializer, AlignedAttribute, Value, Line
        # Getting the temporary variables that are needed for the current
        # sub-kernel.
        from loopy.schedule.tools import (
                temporaries_read_in_subkernel,
                temporaries_written_in_subkernel)
        sub_knl_temps = (
                temporaries_read_in_subkernel(kernel, subkernel_name)
                | temporaries_written_in_subkernel(kernel, subkernel_name))

        for tv in sorted(
                kernel.temporary_variables.values(),
                key=lambda tv: tv.name):
            decl_info = tv.decl_info(self.target, index_dtype=kernel.index_dtype)

            if not tv.base_storage:
                for idi in decl_info:
                    # global temp vars are mapped to arguments or global declarations
                    if tv.address_space != AddressSpace.GLOBAL and (
                            tv.name in sub_knl_temps):
                        decl = self.wrap_temporary_decl(
                                self.get_temporary_decl(
                                    kernel, tv, idi),
                                tv.address_space)

                        if tv.initializer is not None:
                            assert tv.read_only
                            decl = Initializer(decl, generate_array_literal(
                                kernel, ecm, self, tv, tv.initializer))

                        temp_decls.append(decl)

            else:
                assert tv.initializer is None
                if (tv.address_space == AddressSpace.GLOBAL
                        and codegen_state.is_generating_device_code):
                    # global temps trigger no codegen in the device code
                    continue

                offset = 0
                base_storage_sizes.setdefault(tv.base_storage, []).append(
                        tv.nbytes)
                base_storage_to_scope.setdefault(tv.base_storage, []).append(
                        tv.address_space)

                align_size = tv.dtype.itemsize

                from loopy.kernel.array import VectorArrayDimTag
                for dim_tag, axis_len in zip(tv.dim_tags, tv.shape):
                    if isinstance(dim_tag, VectorArrayDimTag):
                        align_size *= axis_len

                base_storage_to_align_bytes.setdefault(tv.base_storage, []).append(
                        align_size)

                for idi in decl_info:
                    cast_decl = POD(self, idi.dtype, "")
                    temp_var_decl = POD(self, idi.dtype, idi.name)

                    cast_decl = self.wrap_temporary_decl(cast_decl, tv.address_space)
                    temp_var_decl = self.wrap_temporary_decl(
                            temp_var_decl, tv.address_space)

                    if tv._base_storage_access_may_be_aliasing:
                        ptrtype = _ConstPointer
                    else:
                        # The 'restrict' part of this is a complete lie--of course
                        # all these temporaries are aliased. But we're promising to
                        # not use them to shovel data from one representation to the
                        # other. That counts, right?
                        ptrtype = _ConstRestrictPointer

                    cast_decl = ptrtype(cast_decl)
                    temp_var_decl = ptrtype(temp_var_decl)

                    cast_tp, cast_d = cast_decl.get_decl_pair()
                    temp_var_decl = Initializer(
                            temp_var_decl,
                            "({} {}) ({} + {})".format(
                                " ".join(cast_tp), cast_d,
                                tv.base_storage,
                                offset))

                    temp_decls.append(temp_var_decl)

                    from pytools import product
                    offset += (
                            idi.dtype.itemsize
                            * product(si for si in idi.shape))

        for bs_name, bs_sizes in sorted(base_storage_sizes.items()):
            bs_var_decl = Value("char", bs_name)
            from pytools import single_valued
            bs_var_decl = self.wrap_temporary_decl(
                    bs_var_decl, single_valued(base_storage_to_scope[bs_name]))

            # FIXME: Could try to use isl knowledge to simplify max.
            if all(isinstance(bs, int) for bs in bs_sizes):
                bs_size_max = max(bs_sizes)
            else:
                bs_size_max = p.Max(tuple(bs_sizes))

            bs_var_decl = ArrayOf(bs_var_decl, ecm(bs_size_max))

            alignment = max(base_storage_to_align_bytes[bs_name])
            bs_var_decl = AlignedAttribute(alignment, bs_var_decl)

            base_storage_decls.append(bs_var_decl)

        # }}}

        result = base_storage_decls + temp_decls

        if result:
            result.append(Line())

        return result

    @property
    def ast_base_class(self):
        from cgen import Generable
        return Generable

    @property
    def ast_block_class(self):
        from cgen import Block
        return Block

    @property
    def ast_for_class(self):
        from cgen import For
        return For

    @property
    def ast_if_class(self):
        from cgen import If
        return If

    @property
    def ast_block_scope_class(self):
        return ScopingBlock

    # }}}

    # {{{ code generation guts

    @property
    def ast_module(self):
        import cgen
        return cgen

    def get_expression_to_code_mapper(self, kernel, callables_table,
                                      var_subst_map, vectorization_info):
        return self.get_expression_to_c_expression_mapper(kernel,
                                                          callables_table,
                                                          var_subst_map,
                                                          vectorization_info)

    def get_expression_to_c_expression_mapper(self, kernel, callables_table,
                                              var_subst_map,
                                              vectorization_info):
        from loopy.target.c.codegen.expression import ExpressionToCExpressionMapper
        return ExpressionToCExpressionMapper(kernel, callables_table, self,
                                             var_subst_map, vectorization_info,
                                             fortran_abi=self.target.fortran_abi)

    def get_c_expression_to_code_mapper(self):
        from loopy.target.c.codegen.expression import CExpressionToCodeMapper
        return CExpressionToCodeMapper()

    def get_temporary_decl(self, kernel, temp_var, decl_info):
        temp_var_decl = POD(self, decl_info.dtype, decl_info.name)

        if temp_var.read_only:
            from cgen import Const
            temp_var_decl = Const(temp_var_decl)

        if decl_info.shape:
            from cgen import ArrayOf
            ecm = self.get_expression_to_code_mapper(kernel, var_subst_map={},
                                                     vectorization_info=None)
            temp_var_decl = ArrayOf(temp_var_decl,
                    ecm(p.flattened_product(decl_info.shape),
                        prec=PREC_NONE, type_context="i"))

        if temp_var.alignment:
            from cgen import AlignedAttribute
            temp_var_decl = AlignedAttribute(temp_var.alignment, temp_var_decl)

        return temp_var_decl

    def wrap_temporary_decl(self, decl, scope):
        return decl

    def wrap_global_constant(self, decl):
        from cgen import Static
        return Static(decl)

    def get_value_arg_decl(self, name, shape, dtype, is_written):
        assert shape == ()

        result = POD(self, dtype, name)

        if not is_written:
            from cgen import Const
            result = Const(result)

        if self.target.fortran_abi:
            from cgen import Pointer
            result = Pointer(result)

        return result

    def get_array_arg_decl(self, name, mem_address_space, shape, dtype, is_written):
        from cgen import RestrictPointer, Const

        arg_decl = RestrictPointer(POD(self, dtype, name))

        if not is_written:
            arg_decl = Const(arg_decl)

        return arg_decl

    def get_global_arg_decl(self, name, shape, dtype, is_written):
        from warnings import warn
        warn("get_global_arg_decl is deprecated use get_array_arg_decl "
                "instead.", DeprecationWarning, stacklevel=2)
        from loopy.kernel.data import AddressSpace
        return self.get_array_arg_decl(name, AddressSpace.GLOBAL, shape,
                dtype, is_written)

    def get_constant_arg_decl(self, name, shape, dtype, is_written):
        from loopy.target.c import POD  # uses the correct complex type
        from cgen import RestrictPointer, Const

        arg_decl = RestrictPointer(POD(self, dtype, name))

        if not is_written:
            arg_decl = Const(arg_decl)

        return arg_decl

    def emit_assignment(self, kernel, insn, var_subst_map, vectorization_info):

        ecm = self.get_expression_to_code_mapper(kernel, var_subst_map,
                                                 vectorization_info)

        assignee_var_name, = insn.assignee_var_names()

        lhs_var = kernel.get_var_descriptor(assignee_var_name)
        lhs_dtype = lhs_var.dtype

        if insn.atomicity is not None:
            lhs_atomicity = [
                    a for a in insn.atomicity if a.var_name == assignee_var_name]
            assert len(lhs_atomicity) <= 1
            if lhs_atomicity:
                lhs_atomicity, = lhs_atomicity
            else:
                lhs_atomicity = None
        else:
            lhs_atomicity = None

        from loopy.kernel.data import AtomicInit, AtomicUpdate
        from loopy.expression import dtype_to_type_context

        lhs_code = ecm(insn.assignee, prec=PREC_NONE, type_context=None)
        rhs_type_context = dtype_to_type_context(kernel.target, lhs_dtype)
        if lhs_atomicity is None:
            from cgen import Assign
            return Assign(
                    lhs_code,
                    ecm(insn.expression, prec=PREC_NONE,
                        type_context=rhs_type_context,
                        needed_dtype=lhs_dtype))

        elif isinstance(lhs_atomicity, AtomicInit):
            self.seen_atomic_dtypes.add(lhs_dtype)
            return self.emit_atomic_init(
                    codegen_state, lhs_atomicity, lhs_var,
                    insn.assignee, insn.expression,
                    lhs_dtype, rhs_type_context)

        elif isinstance(lhs_atomicity, AtomicUpdate):
            self.seen_atomic_dtypes.add(lhs_dtype)
            return self.emit_atomic_update(
                    codegen_state, lhs_atomicity, lhs_var,
                    insn.assignee, insn.expression,
                    lhs_dtype, rhs_type_context)

        else:
            raise ValueError("unexpected lhs atomicity type: %s"
                    % type(lhs_atomicity).__name__)

    def emit_atomic_update(self, codegen_state, lhs_atomicity, lhs_var,
            lhs_expr, rhs_expr, lhs_dtype):
        raise NotImplementedError("atomic updates in %s" % type(self).__name__)

    def emit_tuple_assignment(self, kernel, callables_table, insn,
                              var_subst_map, vectorization_info):
        ecm = self.get_expression_to_code_mapper(kernel, callables_table,
                                                 var_subst_map,
                                                 vectorization_info)

        from cgen import Assign, block_if_necessary
        assignments = []

        for i, (assignee, parameter) in enumerate(
                zip(insn.assignees, insn.expression.parameters)):
            lhs_code = ecm(assignee, prec=PREC_NONE, type_context=None)
            assignee_var_name = insn.assignee_var_names()[i]
            lhs_var = kernel.get_var_descriptor(assignee_var_name)
            lhs_dtype = lhs_var.dtype

            from loopy.expression import dtype_to_type_context
            rhs_type_context = dtype_to_type_context(
                    kernel.target, lhs_dtype)
            rhs_code = ecm(parameter, prec=PREC_NONE,
                    type_context=rhs_type_context, needed_dtype=lhs_dtype)

            assignments.append(Assign(lhs_code, rhs_code))

        return block_if_necessary(assignments)

    def emit_multiple_assignment(self, kernel, callables_table, insn,
                                 var_subst_map, vectorization_info):
        ecm = self.get_expression_to_code_mapper(kernel, var_subst_map,
                                                 vectorization_info)

        func_id = insn.expression.function.name
        in_knl_callable = callables_table[func_id]

        if isinstance(in_knl_callable, ScalarCallable) and (
                in_knl_callable.name_in_target == "loopy_make_tuple"):
            return self.emit_tuple_assignment(kernel, callables_table, insn,
                                              var_subst_map, vectorization_info)

        # takes "is_returned" to infer whether insn.assignees[0] is a part of
        # LHS.
        in_knl_callable_as_call, is_returned = in_knl_callable.emit_call_insn(
                insn=insn,
                target=self.target,
                expression_to_code_mapper=ecm)

        if is_returned:
            from cgen import Assign
            lhs_code = ecm(insn.assignees[0], prec=PREC_NONE, type_context=None)
            return Assign(lhs_code,
                    CExpression(self.get_c_expression_to_code_mapper(),
                    in_knl_callable_as_call))
        else:
            from cgen import ExpressionStatement
            return ExpressionStatement(
                    CExpression(self.get_c_expression_to_code_mapper(),
                                in_knl_callable_as_call))

    def emit_sequential_loop(self, kernel, iname, iname_dtype, lbound, ubound,
                             inner, var_subst_map):
        ecm = self.get_expression_to_code_mapper(kernel, var_subst_map,
                                                 vectorization_info=None)

        from pymbolic import var
        from pymbolic.primitives import Comparison
        from pymbolic.mapper.stringifier import PREC_NONE
        from cgen import For, InlineInitializer

        return For(
                InlineInitializer(
                    POD(self, iname_dtype, iname),
                    ecm(lbound, PREC_NONE, "i")),
                ecm(
                    Comparison(
                        var(iname),
                        "<=",
                        ubound),
                    PREC_NONE, "i"),
                "++%s" % iname,
                inner)

    def emit_initializer(self, codegen_state, dtype, name, val_str, is_const):
        decl = POD(self, dtype, name)

        from cgen import Initializer, Const

        if is_const:
            decl = Const(decl)

        return Initializer(decl, val_str)

    def emit_blank_line(self):
        from cgen import Line
        return Line()

    def emit_comment(self, s):
        from cgen import Comment
        return Comment(s)

    @property
    def can_implement_conditionals(self):
        return True

    def emit_if(self, kernel, condition, ast, var_subst_map, vectorization_info):
        assert vectorization_info is None, "cannot be vectorizable if we see an if"
        from cgen import If
        ecm = self.get_expression_to_code_mapper(kernel, var_subst_map,
                                                 vectorization_info=None)
        return If(ecm(condition), ast)

    # }}}

    def process_ast(self, node):
        sc = ASTSubscriptCollector()
        sc(node)
        return node


# {{{ header generation

class CFunctionDeclExtractor(CASTIdentityMapper):
    def __init__(self):
        self.decls = []

    def map_expression(self, expr):
        return expr

    def map_function_decl_wrapper(self, node):
        self.decls.append(node.subdecl)
        return super()\
                .map_function_decl_wrapper(node)


def generate_header(kernel, codegen_result=None):
    """
    :arg kernel: a :class:`loopy.LoopKernel`
    :arg codegen_result: an instance of :class:`loopy.CodeGenerationResult`
    :returns: a list of AST nodes (which may have :class:`str`
        called on them to produce a string) representing
        function declarations for the generated device
        functions.
    """

    if not isinstance(kernel.target, CFamilyTarget):
        raise LoopyError(
                "Header generation for non C-based languages are not implemented")

    if codegen_result is None:
        from loopy.codegen import generate_code_v2
        codegen_result = generate_code_v2(kernel)

    fde = CFunctionDeclExtractor()
    for dev_prg in codegen_result.device_programs:
        fde(dev_prg.ast)

    return fde.decls

# }}}


# {{{ C99 target

class CTarget(CFamilyTarget):
    """This target may emit code using all features of C99.
    For a target base supporting "least-common-denominator" C,
    see :class:`CFamilyTarget`.
    """

    def get_device_ast_builder(self):
        return CASTBuilder(self)

    @memoize_method
    def get_dtype_registry(self):
        from loopy.target.c.compyte.dtypes import (
                DTypeRegistry, fill_registry_with_c99_stdint_types,
                fill_registry_with_c99_complex_types)
        result = DTypeRegistry()
        fill_registry_with_c99_stdint_types(result)
        fill_registry_with_c99_complex_types(result)
        return DTypeRegistryWrapper(result)


class CASTBuilder(CFamilyASTBuilder):
    def preamble_generators(self):
        return (
                super().preamble_generators() + [
                    c99_preamble_generator,
                    ])

# }}}


# {{{ executable c target

class ExecutableCTarget(CTarget):
    """
    An executable CFamilyTarget that uses (by default) JIT compilation of C-code
    """
    def __init__(self, compiler=None, fortran_abi=False):
        super().__init__(fortran_abi=fortran_abi)
        from loopy.target.c.c_execution import CCompiler
        self.compiler = compiler or CCompiler()

    def get_kernel_executor_cache_key(self, *args, **kwargs):
        # This is for things like the context in OpenCL. There is no such
        # thing that CPU JIT is specific to.
        return None

    def get_kernel_executor(self, t_unit, *args, **kwargs):
        from loopy.target.c.c_execution import CKernelExecutor
        return CKernelExecutor(t_unit, entrypoint=kwargs.pop("entrypoint"),
                compiler=self.compiler)

    def get_host_ast_builder(self):
        # enable host code generation
        return CFamilyASTBuilder(self)

# }}}

# vim: foldmethod=marker
