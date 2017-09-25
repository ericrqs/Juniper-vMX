from collections import defaultdict

import re


def constraint_sorted(to_run, constraints, logger=None):
    """

    :param to_run: list[(str, str, str, str, str)]: list of function specs (kind, family, model, targetname, funcname) - all uncalled hooks that currently exist
    :param constraints: list[(str, str)]: list of (before-expr, after-expr) - list of constraints specifying that one function should be run before or after another, based on resource or service family, model, or name, or hook function name
    :param logger: Logger
    :return: list[list[(str, str, str, str, str)]]: list of serial execution chains to execute in parallel, where a chain is a list of function specs (kind, family, model, targetname, funcname)

    CONSTRAINT_EXPR ::= TARGET_SPEC TIME TARGET_SPEC
    TIME ::= before | after
    TARGET_SPEC ::= TS | TS,TARGET_SPEC
    TS ::= family(FAMILY_REGEX) | model(MODEL_REGEX) | name(NAME_REGEX) | funcname(FUNC_REGEX) | CATCHALL_REGEX
    (all case insensitive)

    Examples:
    family(server) after model(cisco),name(fabric)  # execute hooks on resource of family Server after hooks on resource of model containing Cisco with Fabric in the name
    family(power),funcname(prep) before .*  # if this is a "preparation" hook on a Power resource, execute before all other hooks
    funcname(y) after funcname(x)  # execute hooks with "y" in the name after hooks with "x"

    """
    class Node(object):
        def __init__(self, kind, family, model, target, funcname):
            self.outgoing = []
            self.incoming = []
            self.kind = kind
            self.family = family
            self.model = model
            self.target = target
            self.funcname = funcname

        def __repr__(self):
            return '%s %s' % (self.target, self.funcname)

    nodes = []
    for kind, family, model, target, funcname in to_run:
        nodes.append(Node(kind, family, model, target, funcname))

    family2target = defaultdict(set)
    model2target = defaultdict(set)
    name2target = defaultdict(set)
    catchall2target = defaultdict(set)
    funcname2target = defaultdict(set)

    for node in nodes:
        family2target[node.family].add(node)
        model2target[node.model].add(node)
        name2target[node.target].add(node)
        funcname2target[node.funcname].add(node)
        catchall2target[node.family].add(node)
        catchall2target[node.model].add(node)
        catchall2target[node.target].add(node)
        catchall2target[node.funcname].add(node)

    for constraintab in constraints:
        sides = []
        for constraint in constraintab:
            matchsets = []
            for expr in constraint.split(','):
                regex2index = {
                    r'family\(([^)]*)\)': family2target,
                    r'model\(([^)]*)\)': model2target,
                    r'name\(([^)]*)\)': name2target,
                    r'funcname\(([^)]*)\)': funcname2target,
                }
                for regex in regex2index:
                    m = re.match(regex, expr.strip(), re.IGNORECASE)
                    if m:
                        index = regex2index[regex]
                        patt = m.groups()[0]
                        matches = set()
                        for value in index:
                            if re.search(patt, value, re.IGNORECASE):
                                matches.update(index[value])
                        matchsets.append(matches)
                        break
                else:
                    matches = set()
                    for value in catchall2target:
                        if re.search(expr.strip(), value, re.IGNORECASE):
                            matches.update(catchall2target[value])
                        matchsets.append(matches)
            andmatches = set()
            for i, matchset in enumerate(matchsets):
                if i == 0:
                    andmatches = matchset
                else:
                    andmatches.intersection_update(matchset)
            if len(andmatches) == 0:
                if logger:
                    logger.warn('No matches for constraint %s; it will have no effect' % (constraint))
            sides.append(andmatches)
        aa, bb = sides

        for a in aa:
            for b in bb:
                if a != b:
                    a.outgoing.append(b)
                    b.incoming.append(a)

    starters = set(nodes)
    for node in nodes:
        if node.incoming:
            starters.remove(node)

    rv = []
    nodealready = set()

    def rtrav(ans, n):
        if n in nodealready:
            return
        nodealready.add(n)
        ans.append((n.kind, n.family, n.model, n.target, n.funcname))
        for n2 in n.outgoing:
            rtrav(ans, n2)

    for starter in starters:
        ans = []
        rtrav(ans, starter)
        rv.append(ans)

    totalans = sum([len(a) for a in rv])
    if totalans != len(to_run):
        raise Exception('Number of tasks that could be scheduled according to the constraints (%d) did not match the total number of tasks (%d). Check the constraints for cycles. Constraints: %s' % (totalans, len(to_run), constraints))

    return rv

