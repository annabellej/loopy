from __future__ import division, print_function

__copyright__ = "Copyright (C) 2019 James Stevens"

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

import six  # noqa: F401
import sys
import numpy as np
import loopy as lp
from pyopencl.tools import (  # noqa
    pytest_generate_tests_for_pyopencl
    as pytest_generate_tests)
from loopy.version import LOOPY_USE_LANGUAGE_VERSION_2018_2  # noqa
import logging
from loopy import (
    preprocess_kernel,
    get_one_linearized_kernel,
)
from loopy.schedule.checker.schedule import (
    LEX_VAR_PREFIX,
    STATEMENT_VAR_NAME,
)

logger = logging.getLogger(__name__)

try:
    import faulthandler
except ImportError:
    pass
else:
    faulthandler.enable()


# {{{ test PairwiseScheduleBuilder and map creation

def test_pairwise_schedule_and_map_creation():
    import islpy as isl
    from loopy.schedule.checker import (
        get_schedule_for_statement_pair,
    )
    from loopy.schedule.checker.utils import (
        ensure_dim_names_match_and_align,
    )

    # example kernel
    knl = lp.make_kernel(
        [
            "{[i]: 0<=i<pi}",
            "{[k]: 0<=k<pk}",
            "{[j]: 0<=j<pj}",
            "{[t]: 0<=t<pt}",
        ],
        """
        for i
            for k
                <>temp = b[i,k]  {id=insn_a}
            end
            for j
                a[i,j] = temp + 1  {id=insn_b,dep=insn_a}
                c[i,j] = d[i,j]  {id=insn_c}
            end
        end
        for t
            e[t] = f[t]  {id=insn_d}
        end
        """,
        name="example",
        assumptions="pi,pj,pk,pt >= 1",
        lang_version=(2018, 2)
        )
    knl = lp.add_and_infer_dtypes(
            knl,
            {"b": np.float32, "d": np.float32, "f": np.float32})
    knl = lp.prioritize_loops(knl, "i,k")
    knl = lp.prioritize_loops(knl, "i,j")

    # get a linearization
    knl = preprocess_kernel(knl)
    knl = get_one_linearized_kernel(knl)
    linearization_items = knl.linearization

    # Create PairwiseScheduleBuilder: mapping of {statement instance: lex point}
    sched_ab = get_schedule_for_statement_pair(
        knl,
        linearization_items,
        "insn_a",
        "insn_b",
        )
    sched_ac = get_schedule_for_statement_pair(
        knl,
        linearization_items,
        "insn_a",
        "insn_c",
        )
    sched_ad = get_schedule_for_statement_pair(
        knl,
        linearization_items,
        "insn_a",
        "insn_d",
        )
    sched_bc = get_schedule_for_statement_pair(
        knl,
        linearization_items,
        "insn_b",
        "insn_c",
        )
    sched_bd = get_schedule_for_statement_pair(
        knl,
        linearization_items,
        "insn_b",
        "insn_d",
        )
    sched_cd = get_schedule_for_statement_pair(
        knl,
        linearization_items,
        "insn_c",
        "insn_d",
        )

    # There are multiple potential linearization orders for this kernel, so when
    # performing our comparisons for schedule correctness, we need to know which
    # order loopy chose.
    from loopy.schedule import RunInstruction
    linearized_insn_ord = []
    for item in linearization_items:
        if isinstance(item, RunInstruction):
            linearized_insn_ord.append(item.insn_id)

    def _lex_space_string(dim_vals):
        # Return a string describing lex space dimension assignments
        # (used to create maps below)
        return ", ".join(
            ["%s%d=%s" % (LEX_VAR_PREFIX, idx, str(val))
            for idx, val in enumerate(dim_vals)])

    # Relationship between insn_a and insn_b ---------------------------------------

    assert sched_ab.stmt_instance_before.lex_points == [0, 'i', 0, 'k', 0]
    assert sched_ab.stmt_instance_after.lex_points == [0, 'i', 1, 'j', 0]

    # Get two maps from the PairwiseScheduleBuilder

    sched_map_before, sched_map_after = sched_ab.build_maps(knl)

    # Create expected maps, align, compare

    sched_map_before_expected = isl.Map(
        "[pi, pk] -> { [%s=0, i, k] -> [%s] : 0 <= i < pi and 0 <= k < pk }"
        % (
            STATEMENT_VAR_NAME,
            _lex_space_string(["0", "i", "0", "k", "0"]),
            )
        )
    sched_map_before_expected = ensure_dim_names_match_and_align(
        sched_map_before_expected, sched_map_before)

    sched_map_after_expected = isl.Map(
        "[pi, pj] -> { [%s=1, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_space_string(["0", "i", "1", "j", "0"]),
            )
        )
    sched_map_after_expected = ensure_dim_names_match_and_align(
        sched_map_after_expected, sched_map_after)

    assert sched_map_before == sched_map_before_expected
    assert sched_map_after == sched_map_after_expected

    # ------------------------------------------------------------------------------
    # Relationship between insn_a and insn_c ---------------------------------------

    assert sched_ac.stmt_instance_before.lex_points == [0, 'i', 0, 'k', 0]
    assert sched_ac.stmt_instance_after.lex_points == [0, 'i', 1, 'j', 0]

    # Get two maps from the PairwiseScheduleBuilder

    sched_map_before, sched_map_after = sched_ac.build_maps(knl)

    # Create expected maps, align, compare

    sched_map_before_expected = isl.Map(
        "[pi, pk] -> { [%s=0, i, k] -> [%s] : 0 <= i < pi and 0 <= k < pk }"
        % (
            STATEMENT_VAR_NAME,
            _lex_space_string(["0", "i", "0", "k", "0"]),
            )
        )
    sched_map_before_expected = ensure_dim_names_match_and_align(
        sched_map_before_expected, sched_map_before)

    sched_map_after_expected = isl.Map(
        "[pi, pj] -> { [%s=1, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
        % (
            STATEMENT_VAR_NAME,
            _lex_space_string(["0", "i", "1", "j", "0"]),
            )
        )
    sched_map_after_expected = ensure_dim_names_match_and_align(
        sched_map_after_expected, sched_map_after)

    assert sched_map_before == sched_map_before_expected
    assert sched_map_after == sched_map_after_expected

    # ------------------------------------------------------------------------------
    # Relationship between insn_a and insn_d ---------------------------------------

    # insn_a and insn_d could have been linearized in either order
    # (i loop could be before or after t loop)
    def perform_insn_ad_checks_with(a_lex_idx, d_lex_idx):
        assert sched_ad.stmt_instance_before.lex_points == [
            a_lex_idx, 'i', 0, 'k', 0]
        assert sched_ad.stmt_instance_after.lex_points == [d_lex_idx, 't', 0, 0, 0]

        # Get two maps from the PairwiseScheduleBuilder

        sched_map_before, sched_map_after = sched_ad.build_maps(knl)

        # Create expected maps, align, compare

        sched_map_before_expected = isl.Map(
            "[pi, pk] -> { [%s=0, i, k] -> [%s] : 0 <= i < pi and 0 <= k < pk }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string([a_lex_idx, "i", "0", "k", "0"]),
                )
            )
        sched_map_before_expected = ensure_dim_names_match_and_align(
            sched_map_before_expected, sched_map_before)

        sched_map_after_expected = isl.Map(
            "[pt] -> { [%s=1, t] -> [%s] : 0 <= t < pt }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string([d_lex_idx, "t", "0", "0", "0"]),
                )
            )
        sched_map_after_expected = ensure_dim_names_match_and_align(
            sched_map_after_expected, sched_map_after)

        assert sched_map_before == sched_map_before_expected
        assert sched_map_after == sched_map_after_expected

    if linearized_insn_ord.index("insn_a") < linearized_insn_ord.index("insn_d"):
        # insn_a was linearized first, check schedule accordingly
        perform_insn_ad_checks_with(0, 1)
    else:
        # insn_d was linearized first, check schedule accordingly
        perform_insn_ad_checks_with(1, 0)

    # ------------------------------------------------------------------------------
    # Relationship between insn_b and insn_c ---------------------------------------

    # insn_b and insn_c could have been linearized in either order
    # (i loop could be before or after t loop)
    def perform_insn_bc_checks_with(b_lex_idx, c_lex_idx):
        assert sched_bc.stmt_instance_before.lex_points == [
            0, 'i', 0, 'j', b_lex_idx]
        assert sched_bc.stmt_instance_after.lex_points == [0, 'i', 0, 'j', c_lex_idx]

        # Get two maps from the PairwiseScheduleBuilder

        sched_map_before, sched_map_after = sched_bc.build_maps(knl)

        # Create expected maps, align, compare

        sched_map_before_expected = isl.Map(
            "[pi, pj] -> { [%s=0, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string(["0", "i", "0", "j", b_lex_idx]),
                )
            )
        sched_map_before_expected = ensure_dim_names_match_and_align(
            sched_map_before_expected, sched_map_before)

        sched_map_after_expected = isl.Map(
            "[pi, pj] -> { [%s=1, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string(["0", "i", "0", "j", c_lex_idx]),
                )
            )
        sched_map_after_expected = ensure_dim_names_match_and_align(
            sched_map_after_expected, sched_map_after)

        assert sched_map_before == sched_map_before_expected
        assert sched_map_after == sched_map_after_expected

    if linearized_insn_ord.index("insn_b") < linearized_insn_ord.index("insn_c"):
        # insn_b was linearized first, check schedule accordingly
        perform_insn_bc_checks_with(0, 1)
    else:
        # insn_c was linearized first, check schedule accordingly
        perform_insn_bc_checks_with(1, 0)

    # ------------------------------------------------------------------------------
    # Relationship between insn_b and insn_d ---------------------------------------

    # insn_b and insn_d could have been linearized in either order
    # (i loop could be before or after t loop)
    def perform_insn_bd_checks_with(b_lex_idx, d_lex_idx):
        assert sched_bd.stmt_instance_before.lex_points == [
            b_lex_idx, 'i', 0, 'j', 0]
        assert sched_bd.stmt_instance_after.lex_points == [d_lex_idx, 't', 0, 0, 0]

        # Get two maps from the PairwiseScheduleBuilder

        sched_map_before, sched_map_after = sched_bd.build_maps(knl)

        # Create expected maps, align, compare

        sched_map_before_expected = isl.Map(
            "[pi, pj] -> { [%s=0, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string([b_lex_idx, "i", "0", "j", "0"]),
                )
            )
        sched_map_before_expected = ensure_dim_names_match_and_align(
            sched_map_before_expected, sched_map_before)

        sched_map_after_expected = isl.Map(
            "[pt] -> { [%s=1, t] -> [%s] : 0 <= t < pt }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string([d_lex_idx, "t", "0", "0", "0"]),
                )
            )
        sched_map_after_expected = ensure_dim_names_match_and_align(
            sched_map_after_expected, sched_map_after)

        assert sched_map_before == sched_map_before_expected
        assert sched_map_after == sched_map_after_expected

    if linearized_insn_ord.index("insn_b") < linearized_insn_ord.index("insn_d"):
        # insn_b was linearized first, check schedule accordingly
        perform_insn_bd_checks_with(0, 1)
    else:
        # insn_d was linearized first, check schedule accordingly
        perform_insn_bd_checks_with(1, 0)

    # ------------------------------------------------------------------------------
    # Relationship between insn_c and insn_d ---------------------------------------

    # insn_c and insn_d could have been linearized in either order
    # (i loop could be before or after t loop)
    def perform_insn_cd_checks_with(c_lex_idx, d_lex_idx):
        assert sched_cd.stmt_instance_before.lex_points == [
            c_lex_idx, 'i', 0, 'j', 0]
        assert sched_cd.stmt_instance_after.lex_points == [d_lex_idx, 't', 0, 0, 0]

        # Get two maps from the PairwiseScheduleBuilder

        sched_map_before, sched_map_after = sched_cd.build_maps(knl)

        # Create expected maps, align, compare

        sched_map_before_expected = isl.Map(
            "[pi, pj] -> { [%s=0, i, j] -> [%s] : 0 <= i < pi and 0 <= j < pj }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string([c_lex_idx, "i", "0", "j", "0"]),
                )
            )
        sched_map_before_expected = ensure_dim_names_match_and_align(
            sched_map_before_expected, sched_map_before)

        sched_map_after_expected = isl.Map(
            "[pt] -> { [%s=1, t] -> [%s] : 0 <= t < pt }"
            % (
                STATEMENT_VAR_NAME,
                _lex_space_string([d_lex_idx, "t", "0", "0", "0"]),
                )
            )
        sched_map_after_expected = ensure_dim_names_match_and_align(
            sched_map_after_expected, sched_map_after)

        assert sched_map_before == sched_map_before_expected
        assert sched_map_after == sched_map_after_expected

    if linearized_insn_ord.index("insn_c") < linearized_insn_ord.index("insn_d"):
        # insn_c was linearized first, check schedule accordingly
        perform_insn_cd_checks_with(0, 1)
    else:
        # insn_d was linearized first, check schedule accordingly
        perform_insn_cd_checks_with(1, 0)

# }}}


# {{{ test statement instance ordering creation

def test_statement_instance_ordering_creation():
    import islpy as isl
    from loopy.schedule.checker import (
        get_schedule_for_statement_pair,
    )
    from loopy.schedule.checker.utils import (
        ensure_dim_names_match_and_align,
        append_marker_to_isl_map_var_names,
    )
    from loopy.schedule.checker.lexicographic_order_map import (
        get_statement_ordering_map,
    )

    # example kernel (add deps to fix loop order)
    knl = lp.make_kernel(
        [
            "{[i]: 0<=i<pi}",
            "{[k]: 0<=k<pk}",
            "{[j]: 0<=j<pj}",
            "{[t]: 0<=t<pt}",
        ],
        """
        for i
            for k
                <>temp = b[i,k]  {id=insn_a}
            end
            for j
                a[i,j] = temp + 1  {id=insn_b,dep=insn_a}
                c[i,j] = d[i,j]  {id=insn_c,dep=insn_b}
            end
        end
        for t
            e[t] = f[t]  {id=insn_d, dep=insn_c}
        end
        """,
        name="example",
        assumptions="pi,pj,pk,pt >= 1",
        lang_version=(2018, 2)
        )
    knl = lp.add_and_infer_dtypes(
            knl,
            {"b": np.float32, "d": np.float32, "f": np.float32})
    knl = lp.prioritize_loops(knl, "i,k")
    knl = lp.prioritize_loops(knl, "i,j")

    # get a linearization
    knl = preprocess_kernel(knl)
    knl = get_one_linearized_kernel(knl)
    linearization_items = knl.linearization

    def check_sio_for_insn_pair(
            insn_id_before,
            insn_id_after,
            expected_lex_order_map,
            expected_sio,
            ):

        sched_builder = get_schedule_for_statement_pair(
            knl,
            linearization_items,
            insn_id_before,
            insn_id_after,
            )

        # Get two isl maps from the PairwiseScheduleBuilder
        sched_map_before, sched_map_after = sched_builder.build_maps(knl)

        # get map representing lexicographic ordering
        sched_lex_order_map = sched_builder.get_lex_order_map_for_sched_space()

        assert sched_lex_order_map == expected_lex_order_map

        # create statement instance ordering,
        # maps each statement instance to all statement instances occuring later
        sio = get_statement_ordering_map(
            sched_map_before,
            sched_map_after,
            sched_lex_order_map,
            )

        sio_aligned = ensure_dim_names_match_and_align(sio, expected_sio)

        assert sio_aligned == expected_sio

    expected_lex_order_map = isl.Map("{ "
        "[_lp_linchk_l0', _lp_linchk_l1', _lp_linchk_l2', _lp_linchk_l3', "
        "_lp_linchk_l4']"
        " -> "
        "[_lp_linchk_l0, _lp_linchk_l1, _lp_linchk_l2, _lp_linchk_l3, "
        "_lp_linchk_l4]"
        ":"
        "("
        "_lp_linchk_l0' < _lp_linchk_l0 "
        ") or ("
        "_lp_linchk_l0'= _lp_linchk_l0 and "
        "_lp_linchk_l1' < _lp_linchk_l1 "
        ") or ("
        "_lp_linchk_l0'= _lp_linchk_l0 and "
        "_lp_linchk_l1'= _lp_linchk_l1 and "
        "_lp_linchk_l2' < _lp_linchk_l2 "
        ") or ("
        "_lp_linchk_l0'= _lp_linchk_l0 and "
        "_lp_linchk_l1'= _lp_linchk_l1 and "
        "_lp_linchk_l2'= _lp_linchk_l2 and "
        "_lp_linchk_l3' < _lp_linchk_l3 "
        ") or ("
        "_lp_linchk_l0'= _lp_linchk_l0 and "
        "_lp_linchk_l1'= _lp_linchk_l1 and "
        "_lp_linchk_l2'= _lp_linchk_l2 and "
        "_lp_linchk_l3'= _lp_linchk_l3 and "
        "_lp_linchk_l4' < _lp_linchk_l4"
        ")"
        "}")

    # Isl ignores these apostrophes, but test would still pass since it ignores
    # variable names when checking for equality. Even so, explicitly add apostrophes
    # for sanity.
    expected_lex_order_map = append_marker_to_isl_map_var_names(
        expected_lex_order_map, isl.dim_type.in_, "'")

    # Relationship between insn_a and insn_b ---------------------------------------

    expected_sio = isl.Map(
        "[pi, pj, pk] -> { "
        "[_lp_linchk_statement'=0, i', k'] -> [_lp_linchk_statement=1, i, j]:"
        "0 <= i' < pi and 0 <= k' < pk and 0 <= j < pj and 0 <= i < pi and i > i'; "
        "[_lp_linchk_statement'=0, i', k'] -> [_lp_linchk_statement=1, i=i', j]:"
        "0 <= i' < pi and 0 <= k' < pk and 0 <= j < pj "
        "}"
        )
    # isl ignores these apostrophes, so explicitly add them
    expected_sio = append_marker_to_isl_map_var_names(
        expected_sio, isl.dim_type.in_, "'")

    check_sio_for_insn_pair(
        "insn_a", "insn_b", expected_lex_order_map, expected_sio)

    # Relationship between insn_a and insn_c ---------------------------------------

    expected_sio = isl.Map(
        "[pi, pj, pk] -> { "
        "[_lp_linchk_statement'=0, i', k'] -> [_lp_linchk_statement=1, i, j]:"
        "0 <= i' < pi and 0 <= k' < pk and 0 <= j < pj and 0 <= i < pi and i > i'; "
        "[_lp_linchk_statement'=0, i', k'] -> [_lp_linchk_statement=1, i=i', j]:"
        "0 <= i' < pi and 0 <= k' < pk and 0 <= j < pj "
        "}"
        )
    # isl ignores these apostrophes, so explicitly add them
    expected_sio = append_marker_to_isl_map_var_names(
        expected_sio, isl.dim_type.in_, "'")

    check_sio_for_insn_pair(
        "insn_a", "insn_c", expected_lex_order_map, expected_sio)

    # Relationship between insn_a and insn_d ---------------------------------------

    expected_sio = isl.Map(
        "[pt, pi, pk] -> { "
        "[_lp_linchk_statement'=0, i', k'] -> [_lp_linchk_statement=1, t]:"
        "0 <= i' < pi and 0 <= k' < pk and 0 <= t < pt "
        "}"
        )
    # isl ignores these apostrophes, so explicitly add them
    expected_sio = append_marker_to_isl_map_var_names(
        expected_sio, isl.dim_type.in_, "'")

    check_sio_for_insn_pair(
        "insn_a", "insn_d", expected_lex_order_map, expected_sio)

    # Relationship between insn_b and insn_c ---------------------------------------

    expected_sio = isl.Map(
        "[pi, pj] -> { "
        "[_lp_linchk_statement'=0, i', j'] -> [_lp_linchk_statement=1, i, j]:"
        "0 <= i' < pi and 0 <= j' < pj and i > i' and 0 <= i < pi and 0 <= j < pj; "
        "[_lp_linchk_statement'=0, i', j'] -> [_lp_linchk_statement=1, i=i', j]:"
        "0 <= i' < pi and 0 <= j' < pj and j > j' and 0 <= j < pj; "
        "[_lp_linchk_statement'=0, i', j'] -> [_lp_linchk_statement=1, i=i', j=j']:"
        "0 <= i' < pi and 0 <= j' < pj "
        "}"
        )
    # isl ignores these apostrophes, so explicitly add them
    expected_sio = append_marker_to_isl_map_var_names(
        expected_sio, isl.dim_type.in_, "'")

    check_sio_for_insn_pair(
        "insn_b", "insn_c", expected_lex_order_map, expected_sio)

    # Relationship between insn_b and insn_d ---------------------------------------

    expected_sio = isl.Map(
        "[pt, pi, pj] -> { "
        "[_lp_linchk_statement'=0, i', j'] -> [_lp_linchk_statement=1, t]:"
        "0 <= i' < pi and 0 <= j' < pj and 0 <= t < pt "
        "}"
        )
    # isl ignores these apostrophes, so explicitly add them
    expected_sio = append_marker_to_isl_map_var_names(
        expected_sio, isl.dim_type.in_, "'")

    check_sio_for_insn_pair(
        "insn_b", "insn_d", expected_lex_order_map, expected_sio)

    # Relationship between insn_c and insn_d ---------------------------------------

    expected_sio = isl.Map(
        "[pt, pi, pj] -> { "
        "[_lp_linchk_statement'=0, i', j'] -> [_lp_linchk_statement=1, t]:"
        "0 <= i' < pi and 0 <= j' < pj and 0 <= t < pt "
        "}"
        )
    # isl ignores these apostrophes, so explicitly add them
    expected_sio = append_marker_to_isl_map_var_names(
        expected_sio, isl.dim_type.in_, "'")

    check_sio_for_insn_pair(
        "insn_c", "insn_d", expected_lex_order_map, expected_sio)

# }}}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from pytest import main
        main([__file__])

# vim: foldmethod=marker
