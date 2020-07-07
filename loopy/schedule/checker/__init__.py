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


# {{{ create PairwiseScheduleBuilder for statement pair

def get_schedule_for_statement_pair(
        knl,
        linearization_items,
        insn_id_before,
        insn_id_after,
        ):
    """Create a :class:`loopy.schedule.checker.schedule.PairwiseScheduleBuilder`
    representing the order of two statements as a mapping from
    :class:`loopy.schedule.checker.StatementInstanceSet`
    to lexicographic time.

    :arg knl: A :class:`loopy.kernel.LoopKernel` containing the
        linearization items that will be used to create a schedule.

    :arg linearization_items: A list of :class:`loopy.schedule.ScheduleItem`
        (to be renamed to `loopy.schedule.LinearizationItem`) containing
        the two linearization items for which a schedule will be
        created. This list may be a *partial* linearization for a
        kernel since this function may be used during the linearization
        process.

    :arg insn_id_before: An instruction identifier that is unique within
        a :class:`loopy.kernel.LoopKernel`.

    :arg insn_id_after: An instruction identifier that is unique within
        a :class:`loopy.kernel.LoopKernel`.

    :returns: A :class:`loopy.schedule.checker.schedule.PairwiseScheduleBuilder`
        representing the order of two statements as a mapping from
        :class:`loopy.schedule.checker.StatementInstanceSet`
        to lexicographic time.

    Example usage::

        # Make kernel ------------------------------------------------------------
        knl = lp.make_kernel(
            "{[i,j,k]: 0<=i<pi and 0<=j<pj and 0<=k<pk}",
            [
                "a[i,j] = j  {id=insn_a}",
                "b[i,k] = k+a[i,0]  {id=insn_b,dep=insn_a}",
            ])
        knl = lp.add_and_infer_dtypes(knl, {"a": np.float32, "b": np.float32})
        knl = lp.prioritize_loops(knl, "i,j")
        knl = lp.prioritize_loops(knl, "i,k")

        # Get a linearization
        knl = lp.get_one_linearized_kernel(lp.preprocess_kernel(knl))

        # Get a pairwise schedule ------------------------------------------------

        from loopy.schedule.checker import (
            get_schedule_for_statement_pair,
        )
        sched_builder_ab = get_schedule_for_statement_pair(
            knl,
            knl.linearization,
            "insn_a",
            "insn_b",
            )

        # Get two maps from the PairwiseScheduleBuilder --------------------------

        sched_a, sched_b = sched_builder_ab.build_maps(knl)

        print(sched_a)
        print(sched_b)

    Example Output::

        [pi, pj, pk] -> {
        [_lp_linchk_statement = 0, i, j, k] ->
        [_lp_linchk_l0 = 0, _lp_linchk_l1 = i, _lp_linchk_l2 = 0,
        _lp_linchk_l3 = j, _lp_linchk_l4 = 0] :
        0 <= i < pi and 0 <= j < pj and 0 <= k < pk }
        [pi, pj, pk] -> {
        [_lp_linchk_statement = 1, i, j, k] ->
        [_lp_linchk_l0 = 0, _lp_linchk_l1 = i, _lp_linchk_l2 = 1,
        _lp_linchk_l3 = k, _lp_linchk_l4 = 0] :
        0 <= i < pi and 0 <= j < pj and 0 <= k < pk }

    """

    # {{{ preprocess if not already preprocessed

    from loopy import preprocess_kernel
    preproc_knl = preprocess_kernel(knl)

    # }}}

    # {{{ find any EnterLoop inames that are tagged as concurrent

    # so that PairwiseScheduleBuilder knows to ignore them
    # (In the future, this shouldn't be necessary because there
    #  won't be any inames with ConcurrentTags in EnterLoop linearization items.
    #  Test which exercises this: test_linearization_checker_with_stroud_bernstein())
    from loopy.schedule.checker.utils import (
        get_concurrent_inames,
        get_EnterLoop_inames,
    )
    conc_inames, _ = get_concurrent_inames(preproc_knl)
    enterloop_inames = get_EnterLoop_inames(linearization_items, preproc_knl)
    conc_loop_inames = conc_inames & enterloop_inames
    if conc_loop_inames:
        from warnings import warn
        warn(
            "get_schedule_for_statement_pair encountered EnterLoop for inames %s "
            "with ConcurrentTag(s) in linearization for kernel %s. "
            "Ignoring these loops." % (conc_loop_inames, preproc_knl.name))

    # }}}

    # {{{ Create PairwiseScheduleBuilder: mapping of {statement instance: lex point}

    # include only instructions involved in this dependency
    from loopy.schedule.checker.schedule import PairwiseScheduleBuilder
    return PairwiseScheduleBuilder(
        linearization_items,
        insn_id_before,
        insn_id_after,
        loops_to_ignore=conc_loop_inames,
        )

    # }}}

# }}}


def statement_pair_dep_sets_from_legacy_knl(knl):
    """Return a list of
    :class:`loopy.schedule.checker.dependency.StatementPairDependencySet`
    instances created for a :class:`loopy.LoopKernel` containing legacy
    depencencies.

    Create the new dependencies according to the following rules:

    (1) If a dependency exists between ``insn0`` and ``insn1``, create the
    dependnecy ``SAME(SNC)`` where ``SNC`` is the set of non-concurrent inames
    used by both ``insn0`` and ``insn1``, and ``SAME`` is the relationship
    specified by the ``SAME`` attribute of
    :class:`loopy.schedule.checker.dependency.DependencyType`.

    (2) For each subset of non-concurrent inames used by any instruction,

        (a), find the set of all instructions using those inames,

        (b), create a directed graph with these instructions as nodes and
        edges representing a 'happens before' relationship specfied by
        each dependency,

        (c), find the sources and sinks within this graph, and

        (d), connect each sink to each source (sink happens before source)
        with a ``PRIOR(SNC)`` dependency, where ``PRIOR`` is the
        relationship specified by the ``PRIOR`` attribute of
        :class:`loopy.schedule.checker.dependency.DependencyType`.

    """
    # TODO maybe just eliminate this function since it doesn't do much

    # Preprocess if not already preprocessed
    # note: kernels must always be preprocessed before scheduling
    from loopy import preprocess_kernel
    preprocessed_knl = preprocess_kernel(knl)

    # Create StatementPairDependencySet(s) from kernel dependencies
    from loopy.schedule.checker.dependency import (
        _create_dependencies_from_legacy_knl_old,
    )
    return _create_dependencies_from_legacy_knl_old(preprocessed_knl)


def check_linearization_validity(
        knl,
        statement_pair_dep_sets,
        linearization_items,
        ):
    # TODO document

    from loopy.schedule.checker.dependency import (
        create_dependency_constraint,
    )
    from loopy.schedule.checker.lexicographic_order_map import (
        get_statement_ordering_map,
    )
    from loopy.schedule.checker.utils import (
        prettier_map_string,
    )

    # Preprocess if not already preprocessed
    # note: kernels must always be preprocessed before scheduling
    from loopy import preprocess_kernel
    preprocessed_knl = preprocess_kernel(knl)

    # For each dependency, create+test linearization containing pair of insns------
    linearization_is_valid = True
    for statement_pair_dep_set in statement_pair_dep_sets:
        s_before = statement_pair_dep_set.statement_before
        s_after = statement_pair_dep_set.statement_after
        # TODO, since we now get the doms inside
        # build_maps()
        # reconsider the content of statement_pair_dep_set, which
        # currently contains doms(do we still want them there?)

        # Create PairwiseScheduleBuilder: mapping of {statement instance: lex point}
        # include only instructions involved in this dependency
        sched_builder = get_schedule_for_statement_pair(
            preprocessed_knl,
            linearization_items,
            s_before.insn_id,
            s_after.insn_id,
            )

        # Get two isl maps from the PairwiseScheduleBuilder,
        # one for each linearization item involved in the dependency;
        isl_sched_map_before, isl_sched_map_after = sched_builder.build_maps(
            preprocessed_knl)

        # get map representing lexicographic ordering
        sched_lex_order_map = sched_builder.get_lex_order_map_for_sched_space()

        # create statement instance ordering,
        # maps each statement instance to all statement instances occuring later
        sio = get_statement_ordering_map(
            isl_sched_map_before,
            isl_sched_map_after,
            sched_lex_order_map,
            )

        # create a map representing constraints from the dependency,
        # which maps statement instance to all stmt instances that must occur later
        # and is acquired from the non-preprocessed kernel
        constraint_map = create_dependency_constraint(
            statement_pair_dep_set,
            knl.loop_priority,
            )

        # reorder variables/params in constraint map space to match SIO so we can
        # check to see whether the constraint map is a subset of the SIO
        # (spaces must be aligned so that the variables in the constraint map
        # correspond to the same variables in the SIO)
        from loopy.schedule.checker.utils import (
            ensure_dim_names_match_and_align,
        )

        aligned_constraint_map = ensure_dim_names_match_and_align(
            constraint_map, sio)

        import islpy as isl
        assert aligned_constraint_map.space == sio.space
        assert (
            aligned_constraint_map.space.get_var_names(isl.dim_type.in_)
            == sio.space.get_var_names(isl.dim_type.in_))
        assert (
            aligned_constraint_map.space.get_var_names(isl.dim_type.out)
            == sio.space.get_var_names(isl.dim_type.out))
        assert (
            aligned_constraint_map.space.get_var_names(isl.dim_type.param)
            == sio.space.get_var_names(isl.dim_type.param))

        if not aligned_constraint_map.is_subset(sio):

            linearization_is_valid = False

            print("================ constraint check failure =================")
            print("Constraint map not subset of SIO")
            print("Dependencies:")
            print(statement_pair_dep_set)
            print("Statement instance ordering:")
            print(prettier_map_string(sio))
            print("constraint_map.gist(sio):")
            print(prettier_map_string(aligned_constraint_map.gist(sio)))
            print("sio.gist(constraint_map)")
            print(prettier_map_string(sio.gist(aligned_constraint_map)))
            print("Loop priority known:")
            print(preprocessed_knl.loop_priority)
            print("===========================================================")

    return linearization_is_valid
