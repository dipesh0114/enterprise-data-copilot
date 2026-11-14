"""minigraph.py - a tiny stand-in for LangGraph's StateGraph.

Why it exists: LangGraph depends on compiled Windows files (for example ormsgpack). Some computers block such
files ("DLL load failed ... An Application Control policy has blocked this file"). agents.py then falls back to
this ~40-line runner so the project still works. It supports exactly what agents.py uses:
add_node, add_edge, add_conditional_edges, compile, invoke.
Analogy: LangGraph is a full railway network; this is a single toy train track with the same stations.
"""
START, END = "__start__", "__end__"                         # special names for the entry and exit of the flowchart
MAX_STEPS = 60                                              # safety net: stop if a loop never ends


class StateGraph:
    def __init__(self, state_type=None):
        self.nodes, self.edges, self.conditional = {}, {}, {}   # node functions, fixed arrows, decision arrows

    def add_node(self, name, fn):
        self.nodes[name] = fn                               # register an agent function under a name

    def add_edge(self, source, target):
        self.edges[source] = target                         # fixed arrow: after `source` always go to `target`

    def add_conditional_edges(self, source, router, mapping):
        self.conditional[source] = (router, mapping)        # decision arrow: router(state) names the next node via mapping

    def compile(self):
        return _Compiled(self)


class _Compiled:
    def __init__(self, graph):
        self.g = graph

    def invoke(self, state: dict) -> dict:
        state = dict(state)                                 # work on a copy of the input
        current = self.g.edges[START]                       # first node
        for _ in range(MAX_STEPS):
            if current == END:                              # reached the exit
                return state
            state.update(self.g.nodes[current](state) or {})    # run the agent, merge the keys it returned
            if current in self.g.conditional:               # choose the next node by rule...
                router, mapping = self.g.conditional[current]
                current = mapping[router(state)]
            else:                                           # ...or follow the fixed arrow
                current = self.g.edges[current]
        raise RuntimeError("Graph did not finish within MAX_STEPS (possible endless loop).")