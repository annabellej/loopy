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

import islpy as isl


def prettier_map_string(map_obj):
    return str(map_obj
               ).replace("{ ", "{\n").replace(" }", "\n}").replace("; ", ";\n")


def get_islvars_from_space(space):
    param_names = space.get_var_names(isl.dim_type.param)
    in_names = space.get_var_names(isl.dim_type.in_)
    out_names = space.get_var_names(isl.dim_type.out)
    return isl.make_zero_and_vars(in_names+out_names, param_names)


def add_dims_to_isl_set(isl_set, dim_type, names, new_idx_start):
    new_set = isl_set.insert_dims(
        dim_type, new_idx_start, len(names)
        ).set_dim_name(dim_type, new_idx_start, names[0])
    for i, name in enumerate(names[1:]):
        new_set = new_set.set_dim_name(dim_type, new_idx_start+1+i, name)
    return new_set


def map_names_match_check(
        obj_map,
        desired_names,
        dim_type,
        assert_subset=True,
        assert_permutation=True,
        ):

    obj_map_names = obj_map.space.get_var_names(dim_type)
    if assert_permutation:
        if not set(obj_map_names) == set(desired_names):
            raise ValueError(
                "Set of map names %s for dim %s does not match target set %s"
                % (obj_map_names, dim_type, desired_names))
    elif assert_subset:
        if not set(obj_map_names).issubset(desired_names):
            raise ValueError(
                "Map names %s for dim %s are not a subset of target names %s"
                % (obj_map_names, dim_type, desired_names))


def insert_missing_dims_and_reorder_by_name(
        isl_set, dim_type, desired_dims_ordered):
    """Return an isl_set with the dimensions in the specified order.

    :arg isl_set: A :class:`islpy.Set` whose dimensions are
        to be reordered and, if necessary, augmented with missing dimensions.

    :arg dim_type: A :class:`islpy.dim_type`, i.e., an :class:`int`,
        specifying the dimension to be reordered.

    :arg desired_dims_ordered: A :class:`list` of :class:`str` elements
        representing the desired dimensions in order by dimension name.

    :returns: An :class:`islpy.Set` matching `isl_set` with the
        dimension order matching `desired_dims_ordered`,
        including additional dimensions present in `desred_dims_ordered`
        that are not present in `isl_set`.

    """

    map_names_match_check(
        isl_set, desired_dims_ordered, dim_type,
        assert_subset=True, assert_permutation=False)

    assert dim_type != isl.dim_type.param

    other_dim_type = isl.dim_type.param
    other_dim_len = len(isl_set.get_var_names(other_dim_type))

    new_set = isl_set.copy()
    for desired_idx, name in enumerate(desired_dims_ordered):
        # if iname doesn't exist in set, add dim:
        if name not in new_set.get_var_names(dim_type):
            # insert missing dim in correct location
            new_set = new_set.insert_dims(
                dim_type, desired_idx, 1
                ).set_dim_name(
                dim_type, desired_idx, name)
        else:  # iname exists in set
            current_idx = new_set.find_dim_by_name(dim_type, name)
            if current_idx != desired_idx:
                # move_dims(dst_type, dst_idx, src_type, src_idx, n)

                # first move to other dim because isl is stupid
                new_set = new_set.move_dims(
                    other_dim_type, other_dim_len, dim_type, current_idx, 1)

                # now move it where we actually want it
                new_set = new_set.move_dims(
                    dim_type, desired_idx, other_dim_type, other_dim_len, 1)

    return new_set


def ensure_dim_names_match_and_align(obj_map, tgt_map):

    # first make sure names match
    for dt in [isl.dim_type.in_, isl.dim_type.out, isl.dim_type.param]:
        map_names_match_check(
            obj_map, tgt_map.get_var_names(dt), dt,
            assert_permutation=True)

    aligned_obj_map = isl.align_spaces(obj_map, tgt_map)

    return aligned_obj_map


def create_new_isl_set_with_primes(old_isl_set, marker="'"):
    """Return an isl_set with apostrophes appended to
        dim_type.set dimension names.

    :arg old_isl_set: A :class:`islpy.Set`.

    :returns: A :class:`islpy.Set` matching `old_isl_set` with
        apostrophes appended to dim_type.set dimension names.

    """
    # TODO this is just a special case of append_marker_to_isl_map_var_names

    new_set = old_isl_set.copy()
    for i in range(old_isl_set.n_dim()):
        new_set = new_set.set_dim_name(
            isl.dim_type.set, i, old_isl_set.get_dim_name(
                isl.dim_type.set, i)+marker)
    return new_set


def append_marker_to_isl_map_var_names(old_isl_map, dim_type, marker="'"):
    """Return an isl_map with marker appended to
        dim_type dimension names.

    :arg old_isl_map: A :class:`islpy.Map`.

    :arg dim_type: A :class:`islpy.dim_type`, i.e., an :class:`int`,
        specifying the dimension to be marked.

    :returns: A :class:`islpy.Map` matching `old_isl_map` with
        apostrophes appended to dim_type dimension names.

    """

    new_map = old_isl_map.copy()
    for i in range(len(old_isl_map.get_var_names(dim_type))):
        new_map = new_map.set_dim_name(dim_type, i, old_isl_map.get_dim_name(
            dim_type, i)+marker)
    return new_map


def make_islvars_with_marker(
        var_names_needing_marker, other_var_names, param_names=[], marker="'"):
    """Return a dictionary from variable and parameter names
        to :class:`islpy.PwAff` instances that represent each of
        the variables and parameters, appending marker to
        var_names_needing_marker.

    :arg var_names_needing_marker: A :class:`list` of :class:`str`
        elements representing variable names to have markers appended.

    :arg other_var_names: A :class:`list` of :class:`str`
        elements representing variable names to be included as-is.

    :arg param_names:  A :class:`list` of :class:`str` elements
        representing parameter names.

    :returns: A dictionary from variable names to :class:`islpy.PwAff`
        instances that represent each of the variables
        (islvars may be produced by `islpy.make_zero_and_vars`). The key
        '0' is also include and represents a :class:`islpy.PwAff` zero constant.

    """

    def append_marker(items, mark):
        new_items = []
        for item in items:
            new_items.append(item+mark)
        return new_items

    return isl.make_zero_and_vars(
            append_marker(var_names_needing_marker, marker)
            + other_var_names, param_names)


def append_marker_to_strings(strings, marker="'"):
    if not isinstance(strings, list):
        raise ValueError("append_marker_to_strings did not receive a list")
    else:
        return [s+marker for s in strings]


def append_apostrophes(strings):
    return append_marker_to_strings(strings, marker="'")


def _get_union(list_items):
    union = list_items[0]
    for s in list_items[1:]:
        union = union.union(s)
    return union


def list_var_names_in_isl_sets(
        isl_sets,
        set_dim=isl.dim_type.set):
    inames = set()
    for isl_set in isl_sets:
        inames.update(isl_set.get_var_names(set_dim))

    # sorting is not necessary, but keeps results consistent between runs
    return sorted(list(inames))


def create_symbolic_map_from_tuples(
        tuple_pairs_with_domains,
        space,
        ):
    """Return an :class:`islpy.Map` constructed using the provided space,
        mapping input->output tuples provided in `tuple_pairs_with_domains`,
        with each set of tuple variables constrained by the domains provided.

    :arg tuple_pairs_with_domains: A :class:`list` with each element being
        a tuple of the form `((tup_in, tup_out), domain)`.
        `tup_in` and `tup_out` are tuples containing elements of type
        :class:`int` and :class:`str` representing values for the
        input and output dimensions in `space`, and `domain` is a
        :class:`islpy.Set` constraining variable bounds.

    :arg space: A :class:`islpy.Space` to be used to create the map.

    :returns: A :class:`islpy.Map` constructed using the provided space
        as follows. For each `((tup_in, tup_out), domain)` in
        `tuple_pairs_with_domains`, map
        `(tup_in)->(tup_out) : domain`, where `tup_in` and `tup_out` are
        numeric or symbolic values assigned to the input and output
        dimension variables in `space`, and `domain` specifies constraints
        on these values.

    """
    # TODO clarify this with more comments
    # TODO allow None for domains

    dim_type = isl.dim_type

    space_out_names = space.get_var_names(dim_type.out)
    space_in_names = space.get_var_names(isl.dim_type.in_)

    islvars = get_islvars_from_space(space)

    # loop through pairs and create a set that will later be converted to a map

    all_maps = []
    for (tup_in, tup_out), dom in tuple_pairs_with_domains:

        # initialize constraint with true
        constraint = islvars[0].eq_set(islvars[0])

        # set values for 'in' dimension using tuple vals
        assert len(tup_in) == len(space_in_names)
        for dim_name, val_in in zip(space_in_names, tup_in):
            if isinstance(val_in, int):
                constraint = constraint \
                    & islvars[dim_name].eq_set(islvars[0]+val_in)
            else:
                constraint = constraint \
                    & islvars[dim_name].eq_set(islvars[val_in])

        # set values for 'out' dimension using tuple vals
        assert len(tup_out) == len(space_out_names)
        for dim_name, val_out in zip(space_out_names, tup_out):
            if isinstance(val_out, int):
                constraint = constraint \
                    & islvars[dim_name].eq_set(islvars[0]+val_out)
            else:
                constraint = constraint \
                    & islvars[dim_name].eq_set(islvars[val_out])

        # convert set to map by moving dimensions around
        map_from_set = isl.Map.from_domain(constraint)
        map_from_set = map_from_set.move_dims(
            dim_type.out, 0, dim_type.in_,
            len(space_in_names), len(space_out_names))

        assert space_in_names == map_from_set.get_var_names(
            isl.dim_type.in_)

        # if there are any dimensions in dom that are missing from
        # map_from_set, we have a problem I think?
        # (assertion checks this in add_missing...
        dom_with_all_inames = insert_missing_dims_and_reorder_by_name(
            dom, isl.dim_type.set,
            space_in_names,
            )

        # intersect domain with this map
        all_maps.append(
            map_from_set.intersect_domain(dom_with_all_inames))

    return _get_union(all_maps)


def set_all_isl_space_names(
        isl_space, param_names=None, in_names=None, out_names=None):
    """Return a copy of `isl_space` with the specified dimension names.
        If no names are provided, use `p0, p1, ...` for parameters,
        `i0, i1, ...`, for in_ dimensions, and `o0, o1, ...` for out
        dimensions.

    """

    new_space = isl_space.copy()
    dim_type = isl.dim_type
    if param_names:
        for i, p in enumerate(param_names):
            new_space = new_space.set_dim_name(dim_type.param, i, p)
    else:
        for i in range(len(isl_space.get_var_names(dim_type.param))):
            new_space = new_space.set_dim_name(dim_type.param, i, "p%d" % (i))
    if in_names:
        for i, p in enumerate(in_names):
            new_space = new_space.set_dim_name(dim_type.in_, i, p)
    else:
        for i in range(len(isl_space.get_var_names(dim_type.in_))):
            new_space = new_space.set_dim_name(dim_type.in_, i, "i%d" % (i))
    if out_names:
        for i, p in enumerate(out_names):
            new_space = new_space.set_dim_name(dim_type.out, i, p)
    else:
        for i in range(len(isl_space.get_var_names(dim_type.out))):
            new_space = new_space.set_dim_name(dim_type.out, i, "o%d" % (i))
    return new_space


def get_isl_space(param_names, in_names, out_names):
    """Return an :class:`islpy.Space` with the specified dimension names.
    """

    space = isl.Space.alloc(
        isl.DEFAULT_CONTEXT, len(param_names), len(in_names), len(out_names))
    return set_all_isl_space_names(
        space, param_names=param_names, in_names=in_names, out_names=out_names)


def get_concurrent_inames(knl):
    from loopy.kernel.data import ConcurrentTag
    conc_inames = set()
    non_conc_inames = set()

    all_inames = knl.all_inames()
    for iname in all_inames:
        if knl.iname_tags_of_type(iname, ConcurrentTag):
            conc_inames.add(iname)
        else:
            non_conc_inames.add(iname)

    return conc_inames, all_inames-conc_inames


def get_insn_id_from_linearization_item(linearization_item):
    from loopy.schedule import Barrier
    if isinstance(linearization_item, Barrier):
        return linearization_item.originating_insn_id
    else:
        return linearization_item.insn_id


# TODO for better performance, could combine these funcs so we don't
# loop over linearization more than once
def get_all_nonconcurrent_insn_iname_subsets(
        knl, exclude_empty=False, non_conc_inames=None):
    """Return a :class:`set` of every unique subset of non-concurrent
        inames used in an instruction in a :class:`loopy.LoopKernel`.

    :arg knl: A :class:`loopy.LoopKernel`.

    :arg exclude_empty: A :class:`bool` specifying whether to
        exclude the empty set.

    :arg non_conc_inames: A :class:`set` of non-concurrent inames
        which may be provided if already known.

    :returns: A :class:`set` of every unique subset of non-concurrent
        inames used in any instruction in a :class:`loopy.LoopKernel`.

    """

    if non_conc_inames is None:
        _, non_conc_inames = get_concurrent_inames(knl)

    iname_subsets = set()
    for insn in knl.instructions:
        iname_subsets.add(insn.within_inames & non_conc_inames)

    if exclude_empty:
        iname_subsets.discard(frozenset())

    return iname_subsets


def get_linearization_item_ids_within_inames(knl, inames):
    linearization_item_ids = set()
    for insn in knl.instructions:
        if inames.issubset(insn.within_inames):
            linearization_item_ids.add(insn.id)
    return linearization_item_ids


# TODO use yield to clean this up
# TODO use topological sort from loopy, then find longest path in dag
def _generate_orderings_starting_w_prefix(
        allowed_after_dict, orderings, required_length=None,
        start_prefix=(), return_first_found=False):
    # alowed_after_dict = {str: set(str)}
    # start prefix = tuple(str)
    # orderings = set
    if start_prefix:
        next_items = allowed_after_dict[start_prefix[-1]]-set(start_prefix)
    else:
        next_items = allowed_after_dict.keys()

    if required_length:
        if len(start_prefix) == required_length:
            orderings.add(start_prefix)
            if return_first_found:
                return
    else:
        orderings.add(start_prefix)
        if return_first_found:
            return

    # return if no more items left
    if not next_items:
        return

    for next_item in next_items:
        new_prefix = start_prefix + (next_item,)
        _generate_orderings_starting_w_prefix(
                allowed_after_dict,
                orderings,
                required_length=required_length,
                start_prefix=new_prefix,
                return_first_found=return_first_found,
                )
        if return_first_found and orderings:
            return
    return


def get_orderings_of_length_n(
        allowed_after_dict, required_length, return_first_found=False):
    """Return all orderings found in tree represented by `allowed_after_dict`.

    :arg allowed_after_dict: A :class:`dict` mapping each :class:`string`
        names to a :class:`set` of names that are allowed to come after
        that name.

    :arg required_length: A :class:`int` representing the length required
        for all orderings. Orderings not matching the required length will
        not be returned.

    :arg return_first_found: A :class:`bool` specifying whether to return
        the first valid ordering found.

    :returns: A :class:`set` of all orderings that are *explicitly* allowed
        by the tree represented by `allowed_after_dict`. I.e., if we know
        a->b and c->b, we don't know enough to return a->c->b. Note that
        if the set for a dict key is empty, nothing is allowed to come after.

    """

    orderings = set()
    _generate_orderings_starting_w_prefix(
        allowed_after_dict,
        orderings,
        required_length=required_length,
        start_prefix=(),
        return_first_found=return_first_found,
        )
    return orderings


def create_graph_from_pairs(before_after_pairs):
    # create key for every before
    graph = dict([(before, set()) for before, _ in before_after_pairs])
    for before, after in before_after_pairs:
        graph[before] = graph[before] | set([after, ])
    return graph


# only used for example purposes:


def create_explicit_map_from_tuples(tuple_pairs, space):
    """Return a :class:`islpy.Map` in :class:`islpy.Space` space
        mapping tup_in->tup_out for each `(tup_in, tup_out)` pair
        in `tuple_pairs`, where `tup_in` and `tup_out` are
        tuples of :class:`int` values to be assigned to the
        corresponding dimension variables in `space`.

    """

    dim_type = isl.dim_type
    individual_maps = []

    for tup_in, tup_out in tuple_pairs:
        constraints = []
        for i, val_in in enumerate(tup_in):
            constraints.append(
                isl.Constraint.equality_alloc(space)
                .set_coefficient_val(dim_type.in_, i, 1)
                .set_constant_val(-1*val_in))
        for i, val_out in enumerate(tup_out):
            constraints.append(
                isl.Constraint.equality_alloc(space)
                .set_coefficient_val(dim_type.out, i, 1)
                .set_constant_val(-1*val_out))
        individual_maps.append(
            isl.Map.universe(space).add_constraints(constraints))

    union_map = individual_maps[0]
    for m in individual_maps[1:]:
        union_map = union_map.union(m)

    return union_map


def get_EnterLoop_inames(linearization_items, knl):
    from loopy.schedule import EnterLoop
    loop_inames = set()
    for linearization_item in linearization_items:
        if isinstance(linearization_item, EnterLoop):
            loop_inames.add(linearization_item.iname)
    return loop_inames
